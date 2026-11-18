"""``vpn guard install/remove/status`` — user unit management (WP-G1).

The unit is named **``vpn-guard.service``** (never ``vpnm-guard.service``):
``runners.enabled_vpnm_units()`` treats every ``vpnm-*.service`` as a
profile, so a ``vpnm-`` prefixed guard would be switched off by
``vpn autostart off`` / swept by profile sweeps (SPEC §10.4, RSK-11).

Two unit profiles (SPEC §10.4, AC-17):

- ``hardened`` (default) — full sandbox;
- ``compat`` — no ``ProtectSystem``/``SystemCallFilter``/
  ``SystemCallArchitectures``, used **only** when the hardened unit is
  observed failing (``226/NAMESPACE``-style start failure); the choice is
  recorded in ``install-state.json`` — a silent downgrade never happens.

Install is **opt-in and dry-run by default**: nothing is written and no
systemctl command runs until ``--apply``.  ``--linger`` and the hourly
``vpn-guard-sync.timer`` are separate opt-ins.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

UNIT_NAME = "vpn-guard.service"
SYNC_SERVICE = "vpn-guard-sync.service"
SYNC_TIMER = "vpn-guard-sync.timer"
INSTALL_STATE = "install-state.json"

#: HMAC key for ``redact.hash_ip`` — 32 random bytes, hex-encoded text,
#: 0600, created by install (SPEC §7.3; without it hashing degrades).
HMAC_KEY_NAME = "hmac.key"
HMAC_KEY_BYTES = 32

RunCmd = Callable[[List[str]], Tuple[int, str, str]]

#: S2 suggestion: install reports membership but never flips the flag
#: silently (SPEC §6.2 / OQ-4).
SYSTEM_JOURNAL_GROUP = "systemd-journal"

#: Markers of a user-namespace sandbox failure (SPEC §10.4, AC-17):
#: 226/NAMESPACE, 203/EXEC (broken interpreter), 218/CAPABILITIES.
START_FAILURE_MARKERS: Tuple[str, ...] = (
    "226/namespace",
    "226/",
    "status=226",
    "203/exec",
    "status=203",
    "218/capabilities",
    "status=218",
)

NOTIFY_BIN = "notify-send"

_UNIT_HEADER = """\
[Unit]
Description=VPN guard — real-time client log analysis

[Service]
Type=simple
"""

_UNIT_TRAILER = """\
Restart=on-failure
RestartSec=10
Nice=10
MemoryMax=128M
CPUQuota=10%
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ReadWritePaths=%h/.local/share/vpn-manager/guard
ProtectControlGroups=yes
RestrictAddressFamilies=AF_UNIX{af_netlink}
StandardOutput=journal
StandardError=journal
SyslogIdentifier=vpn-guard

[Install]
WantedBy=default.target
"""

_HARDENED_BLOCK = """\
ProtectSystem=strict
SystemCallFilter=@system-service
SystemCallArchitectures=native
"""


def _run_cmd(cmd: List[str]) -> Tuple[int, str, str]:
    import subprocess  # local: keep module import light
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def unit_dir() -> Path:
    env = os.environ.get("VPN_MANAGER_UNIT_DIR")
    if env:
        return Path(env)
    return Path.home() / ".config" / "systemd" / "user"


def guard_data_dir() -> Path:
    from .reader import guard_dir
    return guard_dir()


def exec_start(python_exe: Optional[str] = None) -> str:
    """``ExecStart`` line; interpreter resolved at install time (SPEC §10.4)."""
    exe = python_exe or sys.executable
    return f"{exe} -m vpnm guard run --foreground --quiet"


def render_unit(profile: str = "hardened", python_exe: Optional[str] = None,
                netlink: bool = False) -> str:
    """Full unit text; ``compat`` drops the namespace-sandbox trio (AC-17)."""
    if profile not in ("hardened", "compat"):
        raise ValueError(f"unknown unit profile: {profile!r}")
    body = _UNIT_HEADER
    body += f"ExecStart={exec_start(python_exe)}\n"
    if profile == "hardened":
        body += _HARDENED_BLOCK
    body += _UNIT_TRAILER.format(
        af_netlink=" AF_NETLINK" if netlink else "")
    return body


def render_sync_service(python_exe: Optional[str] = None) -> str:
    """One-shot server-verdict fetch (the only networked guard command)."""
    exe = python_exe or sys.executable
    return "\n".join([
        "[Unit]",
        "Description=VPN guard — fetch server verdict (read-only ssh)",
        "After=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        f"ExecStart={exe} -m vpnm guard sync",
        "",
    ]) + "\n"


def render_sync_timer() -> str:
    """Hourly sync timer (server tick is hourly; SPEC §13.1 / OQ-7)."""
    return "\n".join([
        "[Unit]",
        "Description=VPN guard — periodic server-verdict sync",
        "",
        "[Timer]",
        "OnBootSec=10min",
        "OnUnitActiveSec=1h",
        "RandomizedDelaySec=5min",
        "Persistent=false",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ]) + "\n"


def in_systemd_journal_group() -> bool:
    """True when the user can read the system journal (S2 suggestion)."""
    try:
        import grp
        gid = grp.getgrnam(SYSTEM_JOURNAL_GROUP).gr_gid
    except (KeyError, OSError):
        return False
    return gid in os.getgroups()


# --- notify-send / D-Bus (SPEC §10.4 item 3) ----------------------------------

def session_bus_socket() -> Optional[Path]:
    """Session D-Bus socket path (``AF_UNIX`` — allowed by the sandbox)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        candidate = Path(runtime) / "bus"
        if candidate.exists():
            return candidate
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    for part in address.split(";"):
        if part.startswith("unix:path="):
            candidate = Path(part[len("unix:path="):])
            if candidate.exists():
                return candidate
    return None


def notify_available() -> Tuple[bool, str]:
    """``(available, reason)`` — binary present **and** session bus reachable."""
    if shutil.which(NOTIFY_BIN) is None:
        return False, "notify-send не найден"
    if session_bus_socket() is None:
        return False, "нет D-Bus session bus ($XDG_RUNTIME_DIR/bus)"
    return True, "ok"


def notify_probe(run: Optional[RunCmd] = None) -> str:
    """Attempt one real notification; ``ok``/``failed``/``unavailable``.

    Only called when ``notify.desktop=true`` is configured (opt-in): the
    check proves the sandbox permits ``AF_UNIX`` to the session bus.  The
    guard never fails an install because a desktop notification could not
    be shown — findings degrade to journal lines.
    """
    available, _reason = notify_available()
    if not available:
        return "unavailable"
    runner = run if run is not None else _run_cmd
    rc, _out, _err = runner([NOTIFY_BIN, "--app-name=vpn-guard",
                             "--urgency=low", "vpn guard",
                             "уведомления guard включены"])
    return "ok" if rc == 0 else "failed"


def _guard_config(config_root: Optional[Path] = None) -> Dict[str, Any]:
    """Read ``guard.json`` (thresholds/flags only; SPEC §10.3)."""
    from .store import guard_config
    return guard_config(config_root)


def _config_dir() -> Path:
    from .reader import config_dir
    return config_dir()


def desktop_notify_enabled(config_root: Optional[Path] = None) -> bool:
    """``notify.desktop`` from the user config (default ``false``, OQ-5)."""
    notify = _guard_config(config_root).get("notify")
    return isinstance(notify, dict) and bool(notify.get("desktop"))


# --- HMAC key for IP pseudonyms (SPEC §7.3 R-3) -------------------------------

def hmac_key_path(data_dir: Optional[Path] = None) -> Path:
    """``~/.local/share/vpn-manager/guard/hmac.key`` (or the test data dir)."""
    if data_dir is not None:
        return data_dir / HMAC_KEY_NAME
    from . import redact
    return redact.HMAC_KEY_FILE


def ensure_hmac_key(data_dir: Optional[Path] = None,
                    key_text: Optional[str] = None) -> Tuple[Path, bool]:
    """Create the 0600 HMAC key once; ``(path, created)``.

    32 random bytes, hex-encoded so :func:`redact.load_hmac_key` can read it
    as UTF-8 text.  An existing key is never overwritten — pseudonyms must
    stay stable across reinstalls; only its mode is tightened to 0600.
    """
    from .store import atomic_write_0600

    path = hmac_key_path(data_dir)
    if path.exists():
        # an existing key must never be rewritten (stable pseudonyms); only
        # tighten its mode when the filesystem lets us
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        return path, False
    text = key_text if key_text is not None else secrets.token_hex(HMAC_KEY_BYTES)
    atomic_write_0600(path, text.strip() + "\n")
    return path, True


def _write_state(state: Dict[str, Any],
                 target_dir: Optional[Path] = None,
                 config_root: Optional[Path] = None) -> None:
    """Write ``install-state.json`` (0600) through the redaction hook."""
    from .redact import sanitize_payload
    from .store import atomic_write_0600, redact_snippets

    clean, hits = sanitize_payload(
        state, snippets=redact_snippets(config_root))
    if hits:
        clean["redaction_hits"] = int(clean.get("redaction_hits", 0)) + hits
    root = target_dir if target_dir is not None else guard_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_0600(root / INSTALL_STATE,
                      json.dumps(clean, indent=2) + "\n")


def load_install_state(target_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    root = target_dir if target_dir is not None else guard_data_dir()
    try:
        payload = json.loads(
            (root / INSTALL_STATE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def start_failure(run: Optional[RunCmd] = None) -> Optional[str]:
    """Observed start failure of the installed unit, or ``None``.

    ``systemctl --user is-active`` alone does not carry the reason; the
    ``status --no-pager`` output does (``status=226/NAMESPACE``,
    ``203/EXEC``, ``218/CAPABILITIES``).  The probe is read-only.
    """
    runner = run if run is not None else _run_cmd
    rc, out, err = runner(["systemctl", "--user", "is-active", UNIT_NAME])
    active_text = f"{out} {err}".strip().lower()
    if rc == 0 and active_text.startswith("active"):
        return None
    _rc_status, status_out, status_err = runner(
        ["systemctl", "--user", "status", UNIT_NAME, "--no-pager",
         "--lines=20"])
    combined = f"{active_text}\n{status_out}\n{status_err}".lower()
    for marker in START_FAILURE_MARKERS:
        if marker in combined:
            return f"hardened start failed: {marker}"
    if rc != 0:
        first_line = (status_out or status_err or active_text).strip().splitlines()
        reason = first_line[0][:120] if first_line else f"rc={rc}"
        return f"hardened start failed: {reason}"
    return None


def unit_profile_note(state: Optional[Dict[str, Any]]) -> Optional[str]:
    """``unit_profile=compat`` line for ``sources`` (SPEC §10.4, AC-17)."""
    if not isinstance(state, dict):
        return None
    profile = state.get("profile")
    if not isinstance(profile, str) or not profile:
        return None
    if profile == "hardened":
        return "unit_profile=hardened"
    reason = state.get("fallback_reason")
    if isinstance(reason, str) and reason:
        return f"unit_profile={profile}: {reason}"[:200]
    return f"unit_profile={profile}"


def install(*, apply: bool = False, linger: bool = False,
            sync_timer: bool = False, netlink: bool = False,
            python_exe: Optional[str] = None,
            unit_root: Optional[Path] = None,
            data_dir: Optional[Path] = None,
            config_root: Optional[Path] = None,
            run: Optional[RunCmd] = None) -> Dict[str, Any]:
    """Write + enable the guard unit.

    Dry-run by default: returns the full plan (unit text, commands) without
    touching the filesystem.  With ``apply=True`` the unit is written
    (0644), daemon-reloaded, enabled and started; a hardened-profile start
    failure falls back to ``compat`` and records the reason (AC-17).
    """
    runner = run if run is not None else _run_cmd
    root = unit_root if unit_root is not None else unit_dir()
    target_data = data_dir if data_dir is not None else guard_data_dir()
    unit_file = root / UNIT_NAME
    unit_text = render_unit("hardened", python_exe, netlink)
    notify_requested = desktop_notify_enabled(config_root)

    commands: List[List[str]] = [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", UNIT_NAME],
        ["systemctl", "--user", "start", UNIT_NAME],
    ]
    if linger:
        commands.append(["loginctl", "enable-linger"])
    if sync_timer:
        commands.append(["systemctl", "--user", "enable", SYNC_TIMER])

    plan: Dict[str, Any] = {
        "apply": apply,
        "unit_path": str(unit_file),
        "unit_profile": "hardened",
        "linger": linger,
        "sync_timer": sync_timer,
        "desktop_notify": notify_requested,
        "hmac_key": str(hmac_key_path(target_data)),
        "hmac_key_exists": hmac_key_path(target_data).exists(),
        "commands": [" ".join(cmd) for cmd in commands],
        "journal_group": in_systemd_journal_group(),
    }
    if not apply:
        return plan

    # guard artefacts live under 0700 (SPEC §11.4); create it so the daemon
    # can write findings/state on its first tick
    target_data.mkdir(parents=True, exist_ok=True)
    os.chmod(target_data, 0o700)
    key_path, key_created = ensure_hmac_key(target_data)
    plan["hmac_key"] = str(key_path)
    plan["hmac_key_created"] = key_created
    root.mkdir(parents=True, exist_ok=True)
    unit_file.write_text(unit_text, encoding="utf-8")
    os.chmod(unit_file, 0o644)
    if sync_timer:
        (root / SYNC_SERVICE).write_text(
            render_sync_service(python_exe), encoding="utf-8")
        (root / SYNC_TIMER).write_text(render_sync_timer(), encoding="utf-8")

    profile_used = "hardened"
    fallback_reason: Optional[str] = None
    for cmd in commands:
        rc, _out, err = runner(cmd)
        if rc != 0:
            plan["error"] = f"{' '.join(cmd)}: {err.strip()}"
            break
        if cmd[1:] == ["--user", "start", UNIT_NAME]:
            failure = start_failure(runner)
            if failure is not None:
                # AC-17: observed hardened failure → compat, recorded
                profile_used = "compat"
                fallback_reason = failure
                unit_file.write_text(render_unit("compat", python_exe, netlink),
                                     encoding="utf-8")
                os.chmod(unit_file, 0o644)
                runner(["systemctl", "--user", "daemon-reload"])
                runner(["systemctl", "--user", "restart", UNIT_NAME])
                break

    notify_delivery = "not_requested"
    if notify_requested:
        notify_delivery = notify_probe(runner)

    _write_state({
        "version": 1,
        "profile": profile_used,
        "fallback_reason": fallback_reason,
        "linger": linger,
        "sync_timer": sync_timer,
        "netlink": netlink,
        "desktop_notify": notify_requested,
        "notify_delivery": notify_delivery,
        "created": datetime.now(timezone.utc).isoformat(),
        "unit": UNIT_NAME,
    }, target_data, config_root)
    plan["unit_profile"] = profile_used
    plan["fallback_reason"] = fallback_reason
    plan["notify_delivery"] = notify_delivery
    return plan


def remove(*, apply: bool = False, purge: bool = False,
           unit_root: Optional[Path] = None,
           data_dir: Optional[Path] = None,
           run: Optional[RunCmd] = None) -> Dict[str, object]:
    """Disable + remove the unit; keep findings unless ``purge``."""
    runner = run if run is not None else _run_cmd
    root = unit_root if unit_root is not None else unit_dir()
    target_data = data_dir if data_dir is not None else guard_data_dir()
    unit_file = root / UNIT_NAME

    commands: List[List[str]] = [
        ["systemctl", "--user", "disable", UNIT_NAME],
        ["systemctl", "--user", "stop", UNIT_NAME],
        ["systemctl", "--user", "disable", SYNC_TIMER],
        ["systemctl", "--user", "daemon-reload"],
    ]
    plan: Dict[str, object] = {
        "apply": apply,
        "unit_path": str(unit_file),
        "purge": purge,
        "commands": [" ".join(cmd) for cmd in commands],
    }
    if not apply:
        return plan

    warnings: List[str] = []
    plan["warnings"] = warnings
    for cmd in commands:
        rc, _out, _err = runner(cmd)
        if rc != 0:
            # disable/stop of a not-fully-installed unit is not an error:
            # uninstall must be idempotent
            warnings.append(f"{' '.join(cmd)}: rc={rc}")
    for name in (SYNC_TIMER, SYNC_SERVICE, UNIT_NAME):
        candidate = root / name
        if candidate.exists():
            candidate.unlink()
    state_file = target_data / INSTALL_STATE
    if state_file.exists():
        state_file.unlink()
    if purge and target_data.exists():
        import shutil
        shutil.rmtree(target_data)
    return plan


def status(*, run: Optional[RunCmd] = None,
           unit_root: Optional[Path] = None,
           data_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Installed/active/enabled + the recorded unit profile."""
    runner = run if run is not None else _run_cmd
    root = unit_root if unit_root is not None else unit_dir()
    target_data = data_dir if data_dir is not None else guard_data_dir()
    installed = (root / UNIT_NAME).exists()
    active = enabled = False
    if installed:
        rc, out, _err = runner(["systemctl", "--user", "is-active", UNIT_NAME])
        active = rc == 0 and out.strip() == "active"
        rc, out, _err = runner(["systemctl", "--user", "is-enabled", UNIT_NAME])
        enabled = rc == 0 and out.strip() == "enabled"
    state = load_install_state(target_data)
    return {
        "unit": UNIT_NAME,
        "installed": installed,
        "active": active,
        "enabled": enabled,
        "profile": state.get("profile") if state else None,
        "fallback_reason": state.get("fallback_reason") if state else None,
        "linger": bool(state.get("linger")) if state else False,
        "sync_timer": bool(state.get("sync_timer")) if state else False,
        "desktop_notify": bool(state.get("desktop_notify")) if state else False,
        "notify_delivery": state.get("notify_delivery") if state else None,
        "created": state.get("created") if state else None,
        "unit_profile_note": unit_profile_note(state),
    }
