"""Channel 0: zapret (nfqws DPI-desync).

zapret works on direct connections (no VPN tunnel): nfqws fragments/blocks
DPI probes so youtube/discord stay reachable without changing the IP.
Installed system-wide by ``scripts/12_zapret.sh`` (root): binary in
``/usr/local/bin/nfqws``, config + hostlists in ``/opt/zapret``, a system
``zapret.service`` and a daily ``zapret-hostlist`` timer.

This module is shared by the channel monitor (``channels.check_zapret``),
``vpn doctor`` and ``vpn zapret on|off|status``.  All subprocess interaction
goes through an injectable ``run`` callable returning ``(rc, out, err)``;
systemctl actions need root, so they go through ``sudo -n`` and degrade to a
printed manual hint when passwordless sudo is not available.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from .runners import RunCmd, run_cmd

SERVICE = "zapret"
BIN_NAME = "nfqws"
CANARY_URL = "https://www.youtube.com/generate_204"
CANARY_TIMEOUT = 8  # seconds, generous for a throttled direct link

BIN_DIRS = (Path("/usr/local/bin"), Path("/usr/bin"), Path("/usr/sbin"))
UNIT_HINT = "bash scripts/12_zapret.sh"
SUDO_HINT = f"sudo systemctl {0} {SERVICE}"  # formatted in _manual_hint


def find_binary() -> Optional[Path]:
    """Path to nfqws when installed, else None."""
    for d in BIN_DIRS:
        candidate = d / BIN_NAME
        if candidate.exists():
            return candidate
    return None


def service_state(run: Optional[RunCmd] = None) -> str:
    """`systemctl is-active zapret` output (active/inactive/failed/...)."""
    runner: RunCmd = run or run_cmd
    _rc, out, _err = runner(["systemctl", "is-active", SERVICE])
    return out.strip() or "unknown"


def canary(run: Optional[RunCmd] = None) -> Tuple[bool, str]:
    """Direct (no SOCKS) canary to youtube generate_204."""
    runner: RunCmd = run or run_cmd
    cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
           "--max-time", str(CANARY_TIMEOUT), CANARY_URL]
    rc, out, _err = runner(cmd)
    code = out.strip()
    if rc == 0 and code == "204":
        return True, code
    return False, code or f"exit {rc}"


def _manual_hint(action: str) -> str:
    return f"sudo systemctl {action} {SERVICE}"


def control(action: str, run: Optional[RunCmd] = None) -> Tuple[bool, str]:
    """on/off the system zapret service.

    Tries passwordless sudo first; on failure returns (False, hint) so the
    caller can print the manual command.  Never raises on systemctl failure.
    """
    runner: RunCmd = run or run_cmd
    if action not in ("on", "off"):
        return False, "неизвестное действие"
    if find_binary() is None:
        return False, f"nfqws не найден — канал 0 не установлен ({UNIT_HINT})"
    systemctl_action = "start" if action == "on" else "stop"
    rc, _out, err = runner(
        ["sudo", "-n", "systemctl", systemctl_action, SERVICE])
    if rc == 0:
        return True, f"{SERVICE} {'запущен' if action == 'on' else 'остановлен'}"
    reason = (err or "").strip().splitlines()
    detail = reason[-1] if reason else f"exit {rc}"
    return False, (f"не удалось {systemctl_action} {SERVICE} "
                   f"({detail}); выполните вручную: "
                   f"{_manual_hint(systemctl_action)}")


def status_lines(run: Optional[RunCmd] = None) -> List[str]:
    """Human-readable status used by both CLI and TUI."""
    lines: List[str] = []
    binary = find_binary()
    if binary is None:
        lines.append(f"nfqws: не установлен ({UNIT_HINT})")
        return lines
    lines.append(f"nfqws: {binary}")
    state = service_state(run)
    lines.append(f"service: {state}")
    if state == "active":
        ok, code = canary(run)
        if ok:
            lines.append("canary: direct DPI-bypass жив "
                         f"(youtube generate_204 → {code})")
        else:
            lines.append(f"canary: fail (http={code}) — стратегия не "
                         "пропускает; перезапустите blockcheck и обновите "
                         "/opt/zapret/config")
    elif state in ("inactive", "failed"):
        lines.append(f"hint: {_manual_hint('start')}")
    return lines
