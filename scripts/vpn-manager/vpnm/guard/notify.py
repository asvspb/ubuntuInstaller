"""Desktop notifications for ``vpn guard`` (SPEC §8.1/§10.4, WP-G5).

Rules of the contract:

- **opt-in only:** ``notify.desktop=true`` in ``guard.json`` (OQ-5); a fresh
  install never notifies;
- **severity floor:** ``notify.min_severity`` (default ``high``); a finding
  with ``severity=high`` and ``confidence=low`` is **not** notified (§8.1);
- **rate limit:** at most :data:`DEFAULT_MAX_PER_HOUR` delivered notifications
  per hour; the rest only increments the ``suppressed`` counter (§8.1);
- **argument list, never a shell:** ``notify-send`` is invoked through
  ``subprocess.run([...])`` with ``shell=False``; the body is masked with
  :func:`vpnm.guard.redact.redact_raw` and clipped before it leaves the process;
- **no network:** D-Bus is a local ``AF_UNIX`` socket; the availability check
  is the one ``vpn guard install`` already performs (§10.4 item 3).  If the
  binary/bus is missing the notification degrades to the journal line that the
  run loop already prints — a finding is never lost.

Nothing here raises: a missing desktop must not break the daemon.
"""

from __future__ import annotations

import subprocess
import time
from collections import deque
from typing import Any, Callable, Deque, List, Mapping, Optional, Tuple

from . import store
from .redact import redact_raw

NOTIFY_BIN = "notify-send"
DEFAULT_MAX_PER_HOUR = 12
RATE_WINDOW_S = 3600
BODY_MAX_CHARS = 200
TITLE_MAX_CHARS = 120

SEVERITY_RANK: Mapping[str, int] = {
    "info": 0, "low": 1, "medium": 2, "high": 3}
URGENCY: Mapping[str, str] = {
    "info": "low", "low": "low", "medium": "normal", "high": "critical"}

RunCmd = Callable[[List[str]], Tuple[int, str, str]]


def _run_cmd(cmd: List[str]) -> Tuple[int, str, str]:
    """``subprocess.run`` with an argument list (no shell, no network)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except (FileNotFoundError, OSError) as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def _available() -> Tuple[bool, str]:
    """Binary **and** session bus (the install-time check, §10.4)."""
    from .install import notify_available
    return notify_available()


class Notifier:
    """Severity/rate-limited ``notify-send`` wrapper (never raises)."""

    def __init__(self, *, config_root: Optional[Any] = None,
                 run: Optional[RunCmd] = None,
                 now_fn: Optional[Callable[[], float]] = None,
                 available: Optional[Callable[[], Tuple[bool, str]]] = None,
                 max_per_hour: int = DEFAULT_MAX_PER_HOUR) -> None:
        self.config_root = config_root
        self._run = run
        self._now_fn = now_fn
        self._available = available
        self.max_per_hour = max(1, int(max_per_hour))
        self._sent_at: Deque[float] = deque()
        #: Delivered notifications (diagnostics in ``status``).
        self.sent = 0
        #: Notifications dropped by severity/rate/availability.
        self.suppressed = 0
        #: Last reason a notification did not happen (never a secret).
        self.last_reason: Optional[str] = None

    # -- settings ------------------------------------------------------------

    def enabled(self) -> bool:
        desktop, _severity = store.notify_settings(self.config_root)
        return desktop

    def min_severity(self) -> str:
        _desktop, severity = store.notify_settings(self.config_root)
        return severity if severity in SEVERITY_RANK else "high"

    def now(self) -> float:
        return self._now_fn() if self._now_fn is not None else time.time()

    def available(self) -> Tuple[bool, str]:
        checker = self._available if self._available is not None else _available
        try:
            return checker()
        except (OSError, ValueError, RuntimeError) as exc:
            return False, f"notify_check_failed:{type(exc).__name__}"

    # -- decision ------------------------------------------------------------

    def accepts(self, finding: Mapping[str, Any]) -> bool:
        """Severity/confidence gate of §8.1 (config and availability aside)."""
        if not isinstance(finding, Mapping):
            return False
        if finding.get("rule") == "G-12":
            return False  # §8.4: never notifies
        severity = str(finding.get("severity", ""))
        confidence = str(finding.get("confidence", ""))
        if severity == "high" and confidence == "low":
            return False  # §8.1: high/low is not a notification
        floor = SEVERITY_RANK.get(self.min_severity(), 3)
        return SEVERITY_RANK.get(severity, -1) >= floor

    def _rate_ok(self) -> bool:
        moment = self.now()
        while self._sent_at and moment - self._sent_at[0] > RATE_WINDOW_S:
            self._sent_at.popleft()
        return len(self._sent_at) < self.max_per_hour

    def _message(self, finding: Mapping[str, Any]) -> Tuple[str, str, str]:
        rule = str(finding.get("rule", "guard"))
        severity = str(finding.get("severity", "high"))
        confidence = str(finding.get("confidence", ""))
        target = finding.get("unit") or finding.get("profile") or "-"
        title, _ = redact_raw(f"VPN guard: {rule} {severity}")
        parts: List[str] = [f"{target} ({confidence})"]
        hypothesis = finding.get("hypothesis")
        if isinstance(hypothesis, str) and hypothesis:
            parts.append(hypothesis)
        action = finding.get("action")
        if isinstance(action, str) and action:
            parts.append(f"→ {action}")
        body, _ = redact_raw("; ".join(parts))
        return (title[:TITLE_MAX_CHARS], body[:BODY_MAX_CHARS],
                URGENCY.get(severity, "normal"))

    # -- delivery ------------------------------------------------------------

    def notify(self, finding: Mapping[str, Any]) -> bool:
        """Offer one finding; ``True`` when a notification was delivered."""
        if not self.enabled():
            self.last_reason = "desktop_notifications_off"
            return False
        if not self.accepts(finding):
            self.suppressed += 1
            self.last_reason = "below_severity_or_confidence"
            return False
        if not self._rate_ok():
            self.suppressed += 1
            self.last_reason = "rate_limited"
            return False
        available, reason = self.available()
        if not available:
            self.suppressed += 1
            self.last_reason = reason
            return False
        title, body, urgency = self._message(finding)
        runner = self._run if self._run is not None else _run_cmd
        try:
            rc, _out, _err = runner([
                NOTIFY_BIN,
                "--app-name=vpn-guard",
                f"--urgency={urgency}",
                "--category=network",
                title,
                body,
            ])
        except (OSError, ValueError, RuntimeError) as exc:
            self.last_reason = f"notify_failed:{type(exc).__name__}"
            return False
        if rc != 0:
            self.last_reason = f"notify_rc={rc}"
            return False
        self.sent += 1
        self._sent_at.append(self.now())
        self.last_reason = None
        return True

    def as_dict(self) -> dict:
        return {"desktop": self.enabled(), "min_severity": self.min_severity(),
                "sent": self.sent, "suppressed": self.suppressed,
                "last_reason": self.last_reason}


__all__ = [
    "DEFAULT_MAX_PER_HOUR",
    "Notifier",
    "NOTIFY_BIN",
    "RATE_WINDOW_S",
    "SEVERITY_RANK",
]
