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

import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Tuple

from . import store
from .redact import redact_raw

NOTIFY_BIN = "notify-send"
DEFAULT_MAX_PER_HOUR = 12
RATE_WINDOW_S = 3600
BODY_MAX_CHARS = 200
TITLE_MAX_CHARS = 120

#: WP-FAILOVER §8.2: Telegram POST budget and message ceiling.
TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_TEXT_MAX = 4000
TELEGRAM_DEFAULT_TIMEOUT = 4.0

#: Injected HTTP transport: ``(url, form_bytes, timeout) -> (status, body)``.
HttpPost = Callable[[str, bytes, float], Tuple[int, str]]

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


def _http_post(url: str, form: bytes, timeout: float) -> Tuple[int, str]:
    """Default Telegram transport (stdlib, no third-party dependency)."""
    request = urllib.request.Request(
        url, data=form, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(getattr(response, "status", 0)), response.read(
                4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # HTTP status is still a response
        return int(exc.code), exc.read(2048).decode("utf-8", "replace")


def _scrub_token(text: str, token: Optional[str]) -> str:
    """Remove the bot token from any text that may be logged (SPEC §8.2).

    ``URLError`` carries the full request URL, which contains the token in
    the path — this is the one place it could leak into a journal line.
    """
    safe, _hits = redact_raw(text)
    if token:
        safe = safe.replace(token, "<redacted:tg_token>")
    return safe


def _token_file_mode_ok(path: Path) -> bool:
    """True when the token file is not group/world readable (0600 contract)."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return (stat.S_IMODE(mode) & (stat.S_IRWXG | stat.S_IRWXO)) == 0


class Notifier:
    """Severity/rate-limited ``notify-send`` wrapper (never raises)."""

    def __init__(self, *, config_root: Optional[Any] = None,
                 run: Optional[RunCmd] = None,
                 now_fn: Optional[Callable[[], float]] = None,
                 available: Optional[Callable[[], Tuple[bool, str]]] = None,
                 max_per_hour: int = DEFAULT_MAX_PER_HOUR,
                 http_post: Optional[HttpPost] = None,
                 token_reader: Optional[Callable[[Path], Optional[str]]] = None
                 ) -> None:
        self.config_root = config_root
        self._run = run
        self._now_fn = now_fn
        self._available = available
        self._http_post = http_post
        self._token_reader = token_reader
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

    # -- WP-FAILOVER: desktop opt-in (SPEC-20261118 §8.1) --------------------

    def failover_enabled(self) -> bool:
        """``notify.failover_desktop`` — independent of ``notify.desktop``."""
        return store.failover_desktop_enabled(self.config_root)

    def send_failover(self, title: str, body: str, *,
                      urgency: str = "normal") -> bool:
        """Deliver one failover banner (opt-in, argument list, redacted).

        Not rate-limited: failover events are rare and each one is the whole
        point of the agent.  ``urgency`` is ``notify-send``'s own urgency —
        ``critical`` is legal here and **only** here (it is not a severity).
        """
        if not self.failover_enabled():
            self.last_reason = "failover_desktop_off"
            return False
        available, reason = self.available()
        if not available:
            self.suppressed += 1
            self.last_reason = reason
            return False
        safe_title, _ = redact_raw(title)
        safe_body, _ = redact_raw(body)
        runner = self._run if self._run is not None else _run_cmd
        try:
            rc, _out, _err = runner([
                NOTIFY_BIN,
                "--app-name=vpn-failover",
                f"--urgency={urgency}",
                "--category=network",
                safe_title[:TITLE_MAX_CHARS],
                safe_body[:BODY_MAX_CHARS],
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

    # -- WP-FAILOVER: Telegram (SPEC-20261118 §8.2) --------------------------

    def telegram_settings(self) -> Dict[str, Any]:
        return store.telegram_settings(self.config_root)

    def _read_token(self, path: Path) -> Optional[str]:
        if self._token_reader is not None:
            return self._token_reader(path)
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return text or None

    def send_telegram(self, text: str) -> Tuple[bool, str]:
        """POST ``sendMessage``; ``(sent, reason)`` — never raises.

        The token is read only from ``notify.telegram.bot_token_file``; a file
        wider than 0600 yields a warning reason but is still used (SPEC §8.2).
        A transport failure returns ``("failed:<class>")`` **with the token
        scrubbed** so it can be logged or queued.
        """
        settings = self.telegram_settings()
        if not settings.get("enabled"):
            return False, "telegram_off"
        chat_id = str(settings.get("chat_id") or "")
        if not chat_id:
            return False, "telegram_no_chat_id"
        token_path = Path(str(settings.get("bot_token_file") or "")).expanduser()
        token = self._read_token(token_path)
        if not token:
            return False, "telegram_no_token"
        warning = "" if _token_file_mode_ok(token_path) else "token_perms_wide"
        timeout = float(settings.get("timeout_sec")
                        or TELEGRAM_DEFAULT_TIMEOUT)
        safe_text, _hits = redact_raw(text)
        form = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": safe_text[:TELEGRAM_TEXT_MAX],
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        url = f"{TELEGRAM_API}/bot{token}/sendMessage"
        poster = self._http_post if self._http_post is not None else _http_post
        try:
            status, body = poster(url, form, timeout)
        except Exception as exc:  # URLError carries the URL → must scrub
            detail = _scrub_token(f"{type(exc).__name__}: {exc}", token)
            self.last_reason = f"telegram_failed:{detail}"[:200]
            return False, self.last_reason
        if int(status) != 200 or '"ok":true' not in body.replace(" ", ""):
            detail = _scrub_token(f"http_{status}", token)
            self.last_reason = f"telegram_failed:{detail}"
            return False, f"{self.last_reason}:{warning}" if warning else self.last_reason
        self.last_reason = warning or None
        return True, warning


def flush_pending_alerts(notifier: Optional[Notifier] = None, *,
                         path: Optional[Path] = None) -> Dict[str, Any]:
    """Re-send queued Telegram alerts; drop the ones that went through (§8.3).

    Called after a successful ``post_flight``, by ``vpn guard sync`` and by
    ``vpn guard failover status``.  The queue is atomically rewritten, so a
    crash mid-flush can duplicate a message at worst — never lose it.
    """
    instance = notifier if notifier is not None else Notifier()
    queue = store.PendingAlerts(path)
    records = queue.records()
    if not records:
        return {"sent": 0, "remaining": 0}
    sent = 0
    remaining: List[Dict[str, Any]] = []
    for record in records:
        stamp = str(record.get("ts") or "")
        text = str(record.get("text") or "")
        message = f"[Отложенное уведомление от {stamp}]\n{text}"
        ok, _reason = instance.send_telegram(message)
        if ok:
            sent += 1
        else:
            remaining.append(record)
    queue.replace(remaining)
    return {"sent": sent, "remaining": len(remaining)}


__all__ = [
    "DEFAULT_MAX_PER_HOUR",
    "Notifier",
    "NOTIFY_BIN",
    "RATE_WINDOW_S",
    "SEVERITY_RANK",
    "TELEGRAM_API",
    "TELEGRAM_DEFAULT_TIMEOUT",
    "TELEGRAM_TEXT_MAX",
    "flush_pending_alerts",
]
