"""VPN channel availability monitoring.

Each channel check returns ``{channel, status, detail}`` with status
``ok | warn | fail | not_installed``:

- **vpnm**: the active profile + one egress probe (single curl measurement)
  through its SOCKS port (reuses ``probe.measure_latency``);
- **ZeroTier**: service state, ``zerotier-cli status`` ONLINE, joined
  networks, optional ping of a 10.x address.  ZeroTier is archived (owner
  decision 2026-11-10): its absence is ``not_installed`` (warn-colour), not
  a fail.  When zerotier-cli / the service exist but the daemon is down,
  the status is ``warn`` ("installed but not running").  A missing
  passwordless sudo is a ``warn`` too, never a fail;
- **AmneziaWG**: a placeholder slot — looks for binaries / ``*.conf``
  artifacts / an ``amz*|wg*`` interface; when nothing exists the status is
  ``not_installed`` with "транспорт в разработке (SPEC-20261110)".

Summary: exit code 0 when at least one channel is ``ok``, 1 when every
channel is fail/not_installed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from . import probe, profiles, runners
from .runners import RunCmd, run_cmd

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_NOT_INSTALLED = "not_installed"

# status -> colour class for renderers
STATUS_WEIGHT = {
    STATUS_OK: 0,
    STATUS_WARN: 1,
    STATUS_NOT_INSTALLED: 1,
    STATUS_FAIL: 2,
}

Channel = Dict[str, str]

AWG_BIN_NAMES = ("awg", "awg-quick", "amnezia-wg")
AWG_BIN_DIRS = (Path("/usr/local/bin"), Path("/usr/bin"), Path("/usr/sbin"))
AWG_CONF_DIRS = (Path("/etc/amnezia/amneziawg"), Path("/etc/wireguard"))
ZT_CLI_FALLBACK_DIRS = (Path("/usr/bin"), Path("/usr/local/bin"),
                        Path("/usr/sbin"))
AWG_SPEC_NOTE = "транспорт в разработке (SPEC-20261110)"

ZT_BAD_NETWORK_MARKERS = ("ACCESS_DENIED", "NOT_FOUND")


def _result(channel: str, status: str, detail: str) -> Channel:
    return {"channel": channel, "status": status, "detail": detail}


def healthy_count(channels: List[Channel]) -> int:
    return sum(1 for c in channels if c["status"] == STATUS_OK)


# --- vpnm -----------------------------------------------------------------------

def check_vpnm(run: Optional[RunCmd] = None) -> Channel:
    """Active profile + a single egress probe through its SOCKS port."""
    active = runners.get_active()
    if not active:
        return _result("vpnm", STATUS_NOT_INSTALLED,
                       "нет активного профиля (vpn up <name>)")
    try:
        record = profiles.get(active)
    except profiles.ProfileStoreError as exc:
        return _result("vpnm", STATUS_FAIL,
                       f"запись активного профиля '{active}' сломана: {exc}")
    port = int(str(record["socks_port"]))
    latency = probe.measure_latency(port, run=run)
    if latency is None:
        return _result("vpnm", STATUS_FAIL,
                       f"профиль '{active}': запрос через "
                       f"127.0.0.1:{port} не прошёл — туннель не отвечает")
    return _result("vpnm", STATUS_OK,
                   f"профиль '{active}' ({record['protocol']}) через "
                   f"127.0.0.1:{port}, egress {round(latency * 1000.0, 1)} ms")


# --- ZeroTier -------------------------------------------------------------------

def _zt_cli_available(run: RunCmd) -> bool:
    rc, _out, _err = run(["which", "zerotier-cli"])
    if rc == 0:
        return True
    return any((d / "zerotier-cli").exists() for d in ZT_CLI_FALLBACK_DIRS)


def _zt_joined_networks(status_out: str) -> List[str]:
    """Network ids of listnetworks rows without ACCESS_DENIED/NOT_FOUND."""
    joined: List[str] = []
    for line in status_out.splitlines()[1:]:  # skip the header line
        line = line.strip()
        if not line or any(marker in line for marker in ZT_BAD_NETWORK_MARKERS):
            continue
        cols = line.split()
        if cols:
            joined.append(cols[0])
    return joined


def _zt_ping_first_ip(run: RunCmd) -> str:
    """Optional ping of a 10.x assigned address; '' when skipped/failed."""
    rc, out, _err = run(["sudo", "-n", "zerotier-cli", "listnetworks"])
    if rc != 0:
        return ""
    for line in out.splitlines():
        cols = line.split()
        if len(cols) < 7:
            continue
        assigned = cols[-1]
        for ip in assigned.split(","):
            ip = ip.strip()
            if ip.startswith("10."):
                addr = ip.split("/")[0]
                ping_rc, _pout, _perr = run(
                    ["ping", "-c", "1", "-W", "2", addr])
                if ping_rc == 0:
                    return f", ping {addr} ok"
                return f", ping {addr} не отвечает"
    return ""


def check_zerotier(run: Optional[RunCmd] = None) -> Channel:
    """ZeroTier channel: archived transport; absence is not a fail."""
    runner: RunCmd = run or run_cmd
    if not _zt_cli_available(runner):
        return _result("zerotier", STATUS_NOT_INSTALLED,
                       "zerotier-cli не найден (ZeroTier в архиве)")
    # service state distinguishes "installed" from "installed but stopped"
    rc, state_out, _err = runner(
        ["systemctl", "is-active", "zerotier-one"])
    state = state_out.strip()
    if rc != 0 and state in ("inactive", "failed"):
        return _result("zerotier", STATUS_WARN,
                       f"установлен, но не запущен (zerotier-one: {state})")
    if state == "not-found":
        return _result("zerotier", STATUS_NOT_INSTALLED,
                       "сервис zerotier-one не найден")
    rc, out, err = runner(["sudo", "-n", "zerotier-cli", "status"])
    if rc != 0:
        return _result("zerotier", STATUS_WARN,
                       "нет прав на zerotier-cli (sudo -n недоступен)")
    if "ONLINE" not in out:
        state_word = "OFFLINE" if "OFFLINE" in out else "не ONLINE"
        return _result("zerotier", STATUS_FAIL,
                       f"zerotier-cli status: {state_word}")
    rc, nets_out, _err = runner(
        ["sudo", "-n", "zerotier-cli", "listnetworks"])
    joined = _zt_joined_networks(nets_out) if rc == 0 else []
    if not joined:
        detail = "ONLINE, joined-сетей нет"
    else:
        detail = f"ONLINE, joined-сети: {', '.join(joined)}"
        detail += _zt_ping_first_ip(runner)
    return _result("zerotier", STATUS_OK, detail)


# --- AmneziaWG (slot) -----------------------------------------------------------

def _awg_binaries() -> List[str]:
    found: List[str] = []
    for name in AWG_BIN_NAMES:
        for d in AWG_BIN_DIRS:
            if (d / name).exists():
                found.append(str(d / name))
                break
    return found


def _awg_confs() -> List[Path]:
    confs: List[Path] = []
    for d in AWG_CONF_DIRS:
        if d.is_dir():
            confs.extend(sorted(d.glob("*.conf")))
    return confs


def _awg_interfaces(run: RunCmd) -> List[str]:
    rc, out, _err = run(["ip", "-o", "link", "show"])
    if rc != 0:
        return []
    ifaces: List[str] = []
    for line in out.splitlines():
        match = re.match(r"\d+:\s+(\S+?@\S+|\S+?):", line)
        if not match:
            continue
        name = match.group(1).split("@")[0]
        if re.match(r"^(amz|wg)", name) and "UP" in line:
            ifaces.append(name)
    return ifaces


def check_awg(run: Optional[RunCmd] = None) -> Channel:
    """AmneziaWG slot: transport not implemented yet, artifacts only."""
    runner: RunCmd = run or run_cmd
    bins_found = _awg_binaries()
    confs = _awg_confs()
    if not bins_found and not confs:
        return _result("amneziawg", STATUS_NOT_INSTALLED,
                       f"артефактов нет — {AWG_SPEC_NOTE}")
    ifaces = _awg_interfaces(runner)
    if ifaces:
        return _result("amneziawg", STATUS_OK,
                       f"интерфейс поднят: {', '.join(ifaces)}")
    parts = []
    if bins_found:
        parts.append(f"бинарь: {', '.join(Path(b).name for b in bins_found)}")
    if confs:
        parts.append(f"конфигов: {len(confs)}")
    return _result("amneziawg", STATUS_WARN,
                   "артефакты найдены (" + "; ".join(parts)
                   + "), интерфейс не поднят")


# --- top level -------------------------------------------------------------------

def check_all(run: Optional[RunCmd] = None) -> List[Channel]:
    return [check_vpnm(run=run), check_zerotier(run=run),
            check_awg(run=run)]


def run_monitor(run: Optional[RunCmd] = None) -> Dict[str, object]:
    """Run all channel checks; return channels + healthy counters."""
    channels = check_all(run=run)
    return {
        "channels": channels,
        "healthy": healthy_count(channels),
        "total": len(channels),
    }


def exit_code(outcome: Dict[str, object]) -> int:
    """0 when at least one channel is ok, 1 otherwise."""
    return 0 if int(str(outcome["healthy"])) > 0 else 1
