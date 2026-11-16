"""Server-verdict exchange file and the single explicit network command (WP-G4).

The daemon **never** opens a socket (R-4/AC-7): it only reads
``~/.local/share/vpn-manager/server-verdict.json`` and hands the already
validated, already freshness-checked :class:`~vpnm.guard.rules.ServerContext`
to the rule engine (D-06).  The single networking command of the whole guard
is :func:`sync` — one ``ssh`` invocation with an argument list (never
``shell=True``) running::

    vds gateway probe-watch watch --json

**without** ``--record``: the server command is read-only by construction
(``src/gateway/probing.py: cmd_watch`` writes only under ``if record:``) and
the client only replaces its own ``server-verdict.json``.

Two clocks, strictly separated (D-06):

- ``fetched_at`` (wall-clock) is used **only** to compute ``age_s``/``fresh``
  (≤ 65 min — the server timer ticks hourly);
- every S3 event is stamped by the caller in the monotonic domain; nothing
  here converts wall-clock into ``mono_us``.

Allowlist of SPEC §13.2 is enforced on write: ``top``/``ru_top``/``journal``
and every other third-party IP list are dropped, ``raw_sha256`` proves what
was merged without storing it.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import events, schema
from .reader import config_dir, data_dir
from .rules import ServerContext
from .store import atomic_write_0600

#: SPEC §9.3: freshness window is 65 min (hourly server tick + tolerance).
FRESH_MAX_S = 65 * 60

#: The one remote command, fixed by SPEC §13.1 (no ``--record``).
REMOTE_COMMAND = "vds gateway probe-watch watch --json"

#: Values copied from the server JSON; everything else is dropped (§13.2).
ALLOWED_FIELDS: Tuple[str, ...] = (
    "verdict",
    "threat_level",
    "threat_reason",
    "window",
    "stage",
    "reasons",
    "lockdown",
    "now",
)


def verdict_path() -> Path:
    """``~/.local/share/vpn-manager/server-verdict.json`` (SPEC §6.3)."""
    return data_dir() / "server-verdict.json"


def settings_path() -> Path:
    """``~/.config/vpn-manager/settings.json`` (default ssh host)."""
    return config_dir() / "settings.json"


# --- timestamps ------------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(moment: datetime) -> str:
    """RFC3339 UTC, second precision (contract example of §13.2)."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(text: Any) -> Optional[datetime]:
    """RFC3339 (``Z`` or offset) → aware datetime; ``None`` on garbage."""
    if not isinstance(text, str) or not text.strip():
        return None
    raw = text.strip()
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def age_seconds(fetched_at: Any, now: Optional[datetime] = None) -> Optional[int]:
    """Age of ``fetched_at`` in whole seconds (``None`` when unparseable)."""
    moment = parse_timestamp(fetched_at)
    if moment is None:
        return None
    reference = now if now is not None else utc_now()
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return max(0, int((reference - moment).total_seconds()))


# --- reading / validation --------------------------------------------------------


def validate_verdict(payload: Any) -> List[str]:
    """Contract errors of a ``guard.server_verdict/1`` payload."""
    if not isinstance(payload, Mapping):
        return ["$: expected object"]
    return schema.validate(dict(payload), events.SERVER_VERDICT_SCHEMA)


def load_verdict(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Read and validate the exchange file; ``None`` on any problem.

    A broken or absent file is never an exception: the caller degrades to
    ``fresh=false`` (R-9, «деградировать явно»).
    """
    target = path if path is not None else verdict_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or validate_verdict(payload):
        return None
    return payload


def context_from_payload(payload: Optional[Mapping[str, Any]],
                         now: Optional[datetime] = None) -> ServerContext:
    """Build the rules' :class:`ServerContext` (D-06 freshness only)."""
    if not isinstance(payload, Mapping):
        return ServerContext(fresh=False, present=False)
    age = age_seconds(payload.get("fetched_at"), now)
    fresh = age is not None and age <= FRESH_MAX_S
    lockdown_cfg = payload.get("lockdown")
    lockdown = (isinstance(lockdown_cfg, Mapping)
                and bool(lockdown_cfg.get("active")))
    threat = payload.get("threat_level")
    threat_level = threat if threat in events.THREAT_LEVELS else None
    stage = payload.get("stage")
    verdict = payload.get("verdict")
    return ServerContext(
        threat_level=threat_level,
        stage=stage if isinstance(stage, str) and stage else None,
        age_s=age if age is not None else 0,
        fresh=fresh,
        lockdown=lockdown,
        verdict=verdict if isinstance(verdict, str) and verdict else None,
        present=True,
    )


def read_server_context(path: Optional[Path] = None,
                        now: Optional[datetime] = None) -> ServerContext:
    """Convenience: read the file, always return a context (never raises)."""
    return context_from_payload(load_verdict(path), now=now)


# --- sync (the only network command) --------------------------------------------


@dataclass(frozen=True)
class SyncResult:
    """Outcome of ``vpn guard sync`` (exit 0/2, §10.1)."""

    ok: bool
    host: Optional[str] = None
    path: Optional[str] = None
    error: Optional[str] = None
    note: Optional[str] = None
    verdict: Optional[str] = None
    threat_level: Optional[str] = None
    raw_sha256: Optional[str] = None
    context: ServerContext = field(default_factory=ServerContext)

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ok": self.ok,
            "host": self.host,
            "path": self.path,
            "note": self.note,
            "verdict": self.verdict,
            "threat_level": self.threat_level,
            "raw_sha256": self.raw_sha256,
            "fresh": self.context.fresh,
            "age_s": self.context.age_s,
            "lockdown": self.context.lockdown,
        }
        if self.error:
            payload["error"] = self.error
        return payload


Runner = Any  # Callable[[List[str], float], Tuple[int, str, str]]


def ssh_command(host: str, timeout: float) -> List[str]:
    """``ssh`` argument list — one call, never ``shell=True`` (§13.1)."""
    return [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={max(1, int(timeout))}",
        host,
        REMOTE_COMMAND,
    ]


def _run_ssh(cmd: List[str], timeout: float) -> Tuple[int, str, str]:
    """Run the ssh call with a hard timeout; errors become ``(rc, out, err)``."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, "", f"ssh timeout after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except OSError as exc:
        return 126, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def default_host(settings: Optional[Path] = None) -> Optional[str]:
    """``ssh_host`` from ``settings.json`` (same source as ``vpn profile pull``)."""
    target = settings if settings is not None else settings_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    value = payload.get("ssh_host") if isinstance(payload, dict) else None
    return value if isinstance(value, str) and value else None


def _clip(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def build_verdict_payload(server_payload: Mapping[str, Any], *,
                          host: Optional[str],
                          now: Optional[datetime] = None) -> Dict[str, Any]:
    """Allowlisted ``guard.server_verdict/1`` from the raw server JSON (§13.2).

    Only contract fields survive: ``top``/``ru_top``/``journal``/``new_ru_ips``
    (third-party IPs) are dropped by construction, not by a filter that could
    be forgotten.
    """
    moment = now if now is not None else utc_now()
    threat = server_payload.get("threat_level")
    threat_level = threat if threat in events.THREAT_LEVELS else None
    lockdown_raw = server_payload.get("lockdown")
    lockdown = lockdown_raw if isinstance(lockdown_raw, Mapping) else {}
    reasons_raw = server_payload.get("reasons")
    reasons: List[str] = []
    if isinstance(reasons_raw, list):
        reasons = [text for text in
                   (_clip(item, 120) for item in reasons_raw[:16])
                   if text]
    window = _clip(server_payload.get("window"), 64)
    payload: Dict[str, Any] = {
        "schema": events.SERVER_VERDICT_SCHEMA,
        "fetched_at": iso_utc(moment),
        "host": _clip(host, 128),
        "source": _clip(REMOTE_COMMAND, 128) or REMOTE_COMMAND,
        "verdict": _clip(server_payload.get("verdict"), 64),
        "threat_level": threat_level,
        "threat_reason": _clip(server_payload.get("threat_reason"), 240),
        "window": window,
        "stage": _clip(server_payload.get("stage"), 32),
        "reasons": reasons,
        "lockdown": {
            "active": bool(lockdown.get("active")),
            "ttl_until": _clip(lockdown.get("ttl_until"), 32) or "",
        },
        "raw_sha256": events.payload_hash(dict(server_payload)),
    }
    return payload


def sync(*, host: Optional[str] = None, timeout: float = 10.0,
         path: Optional[Path] = None,
         settings: Optional[Path] = None,
         run: Optional[Runner] = None,
         now: Optional[datetime] = None) -> SyncResult:
    """One read-only ssh fetch → atomic ``server-verdict.json`` replacement.

    Any failure (no host, non-zero rc, timeout, garbage JSON, contract error)
    keeps the **old** file untouched and reports ``note=verdict_stale``; the
    daemon then simply keeps a stale/absent server context.
    """
    target = path if path is not None else verdict_path()
    resolved = host or default_host(settings)
    if not resolved:
        return SyncResult(ok=False, path=str(target),
                          error="ssh host is not configured",
                          note="verdict_stale")
    cmd = ssh_command(resolved, timeout)
    runner = run if run is not None else _run_ssh
    try:
        rc, out, err = runner(cmd, float(timeout))
    except (OSError, ValueError) as exc:
        return SyncResult(ok=False, host=resolved, path=str(target),
                          error=str(exc), note="verdict_stale")
    if rc != 0:
        detail = (err or out or "").strip()
        return SyncResult(ok=False, host=resolved, path=str(target),
                          error=f"ssh exit {rc}: {detail}"[:240],
                          note="verdict_stale")
    try:
        server_payload = json.loads(out)
    except (json.JSONDecodeError, ValueError) as exc:
        return SyncResult(ok=False, host=resolved, path=str(target),
                          error=f"invalid server JSON: {exc}"[:240],
                          note="verdict_stale")
    if not isinstance(server_payload, dict):
        return SyncResult(ok=False, host=resolved, path=str(target),
                          error="server JSON is not an object",
                          note="verdict_stale")
    if not server_payload.get("threat_level") and not server_payload.get(
            "verdict"):
        return SyncResult(ok=False, host=resolved, path=str(target),
                          error="server JSON has neither threat_level nor "
                                "verdict",
                          note="verdict_stale")
    payload = build_verdict_payload(server_payload, host=resolved, now=now)
    errors = validate_verdict(payload)
    if errors:
        return SyncResult(ok=False, host=resolved, path=str(target),
                          error="invalid guard.server_verdict/1: "
                                + "; ".join(errors)[:240],
                          note="verdict_stale")
    try:
        atomic_write_0600(target, json.dumps(
            payload, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        return SyncResult(ok=False, host=resolved, path=str(target),
                          error=f"cannot write {target}: {exc}"[:240],
                          note="verdict_stale")
    context = context_from_payload(payload, now=now)
    return SyncResult(
        ok=True, host=resolved, path=str(target),
        note=None, verdict=payload.get("verdict"),
        threat_level=payload.get("threat_level"),
        raw_sha256=payload.get("raw_sha256"), context=context)


__all__ = [
    "ALLOWED_FIELDS",
    "FRESH_MAX_S",
    "REMOTE_COMMAND",
    "SyncResult",
    "age_seconds",
    "build_verdict_payload",
    "context_from_payload",
    "default_host",
    "iso_utc",
    "load_verdict",
    "parse_timestamp",
    "read_server_context",
    "settings_path",
    "ssh_command",
    "sync",
    "utc_now",
    "validate_verdict",
    "verdict_path",
]
