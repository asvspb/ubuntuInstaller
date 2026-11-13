"""Runner lifecycle: generated client configs, systemd user units, up/down/switch.

Layout:
- generated configs: ~/.local/share/vpn-manager/generated/<name>.json|.yaml (0600)
- user units:        ~/.config/systemd/user/vpnm-<name>.service (generated in code)
- state:             ~/.config/vpn-manager/state.json {"active": name|null}

All systemctl/loginctl/curl interactions go through subprocess with argument
lists (no shell=True). Tests monkeypatch ``run_cmd`` and the path constants.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .links import PROTOCOL_HYSTERIA2, ProfileLink, parse_link
from .xclient import generate

CONFIG_DIR = Path(os.environ.get(
    "VPN_MANAGER_CONFIG_DIR", str(Path.home() / ".config" / "vpn-manager")))
STATE_PATH = Path(os.environ.get(
    "VPN_MANAGER_STATE_FILE", str(CONFIG_DIR / "state.json")))
GENERATED_DIR = Path(os.environ.get(
    "VPN_MANAGER_GENERATED_DIR",
    str(Path.home() / ".local" / "share" / "vpn-manager" / "generated")))
UNIT_DIR = Path(os.environ.get(
    "VPN_MANAGER_UNIT_DIR", str(Path.home() / ".config" / "systemd" / "user")))
BIN_DIR = Path(os.environ.get(
    "VPN_MANAGER_BIN_DIR", str(Path.home() / ".local" / "lib" / "vpn")))

CURL_TIMEOUT = 10  # seconds, for the status external-IP probe

RunCmd = Callable[[List[str]], Tuple[int, str, str]]


class RunnerError(Exception):
    """Raised on runner lifecycle failures (port busy, systemctl error)."""


def run_cmd(cmd: List[str]) -> Tuple[int, str, str]:
    """Run a command with an argument list; never shell=True."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError as exc:
        return 127, "", str(exc)


# --- state ------------------------------------------------------------------

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def load_state(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load state.json; normalizes to {"active": name|None, "sysproxy": bool}.

    Older files that only carry "active" migrate cleanly (sysproxy=False).
    """
    path = path if path is not None else STATE_PATH
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = None
        if isinstance(state, dict):
            active = state.get("active")
            return {
                "active": active if isinstance(active, str) else None,
                "sysproxy": bool(state.get("sysproxy", False)),
            }
    return {"active": None, "sysproxy": False}


def save_state(state: Dict[str, Any], path: Optional[Path] = None) -> None:
    _atomic_write(path if path is not None else STATE_PATH,
                  json.dumps(state, indent=2) + "\n")


def get_active(path: Optional[Path] = None) -> Optional[str]:
    active = load_state(path)["active"]
    return active if isinstance(active, str) else None


def set_active(name: Optional[str], path: Optional[Path] = None) -> None:
    state = load_state(path)
    state["active"] = name
    save_state(state, path)


def get_sysproxy(path: Optional[Path] = None) -> bool:
    """True when the GNOME system proxy should follow the active profile."""
    sysproxy_flag = load_state(path)["sysproxy"]
    return bool(sysproxy_flag)


def set_sysproxy(on: bool, path: Optional[Path] = None) -> None:
    """Store the sysproxy flag without touching the active profile."""
    state = load_state(path)
    state["sysproxy"] = bool(on)
    save_state(state, path)


# --- configs and units ------------------------------------------------------

def is_hysteria(protocol: str) -> bool:
    return protocol == PROTOCOL_HYSTERIA2


def bin_name(protocol: str) -> str:
    return "hysteria" if is_hysteria(protocol) else "xray"


def config_filename(name: str, protocol: str) -> str:
    return f"{name}.yaml" if is_hysteria(protocol) else f"{name}.json"


def unit_name(name: str) -> str:
    return f"vpnm-{name}.service"


def write_config(name: str, link: ProfileLink, socks_port: int,
                 gen_dir: Optional[Path] = None) -> Path:
    """Generate and write the client config for a profile (mode 0600)."""
    gen_dir = gen_dir if gen_dir is not None else GENERATED_DIR
    text = generate(link, socks_port)
    out = gen_dir / config_filename(name, link.protocol)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    os.chmod(out, 0o600)
    return out


def unit_text(name: str, protocol: str, bin_path: Path,
              config_path: Path) -> str:
    """Render the systemd user-unit text (kept in code, not a data file)."""
    bin_path = Path(bin_path)
    config_path = Path(config_path)
    if is_hysteria(protocol):
        exec_start = f"{bin_path} client -c {config_path}"
    else:
        exec_start = f"{bin_path} -c {config_path}"
    return "\n".join([
        "[Unit]",
        f"Description=vpn-manager client '{name}' ({protocol})",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        f"ExecStart={exec_start}",
        "Restart=on-failure",
        "RestartSec=3",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])


def write_unit(name: str, protocol: str, bin_path: Path, config_path: Path,
               unit_dir: Optional[Path] = None) -> Path:
    unit_dir = unit_dir if unit_dir is not None else UNIT_DIR
    text = unit_text(name, protocol, bin_path, config_path)
    out = unit_dir / unit_name(name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    os.chmod(out, 0o644)
    return out


def remove_unit(name: str, unit_dir: Optional[Path] = None) -> bool:
    out = (unit_dir if unit_dir is not None else UNIT_DIR) / unit_name(name)
    if out.exists():
        out.unlink()
        return True
    return False


# --- systemctl helpers ------------------------------------------------------

def daemon_reload(run: Optional[RunCmd] = None) -> Tuple[int, str, str]:
    return (run or run_cmd)(["systemctl", "--user", "daemon-reload"])


def systemctl_start(name: str, run: Optional[RunCmd] = None) -> Tuple[int, str, str]:
    return (run or run_cmd)(["systemctl", "--user", "start", unit_name(name)])


def systemctl_stop(name: str, run: Optional[RunCmd] = None) -> Tuple[int, str, str]:
    return (run or run_cmd)(["systemctl", "--user", "stop", unit_name(name)])


def is_active(name: str, run: Optional[RunCmd] = None) -> bool:
    """True when the unit reports 'active' via systemctl --user is-active."""
    rc, out, _ = (run or run_cmd)(["systemctl", "--user", "is-active", unit_name(name)])
    return rc == 0 and out.strip() == "active"


def systemctl_enable(name: str, run: Optional[RunCmd] = None) -> Tuple[int, str, str]:
    """Enable the unit so the profile starts on login (systemd user unit)."""
    return (run or run_cmd)(["systemctl", "--user", "enable", unit_name(name)])


def systemctl_disable(name: str, run: Optional[RunCmd] = None) -> Tuple[int, str, str]:
    return (run or run_cmd)(["systemctl", "--user", "disable", unit_name(name)])


def is_enabled(name: str, run: Optional[RunCmd] = None) -> bool:
    """True when the unit reports 'enabled' via systemctl --user is-enabled."""
    rc, out, _ = (run or run_cmd)(["systemctl", "--user", "is-enabled",
                                   unit_name(name)])
    return rc == 0 and out.strip() == "enabled"


def enabled_vpnm_units(run: Optional[RunCmd] = None) -> List[str]:
    """Names of vpnm-*.service units currently enabled (autostart at login)."""
    rc, out, _err = (run or run_cmd)(["systemctl", "--user", "list-unit-files",
                                      "vpnm-*.service", "--no-legend"])
    if rc != 0:
        return []
    names: List[str] = []
    for line in out.splitlines():
        cols = line.split()
        if len(cols) >= 2 and cols[1] == "enabled":
            unit = cols[0]
            if unit.startswith("vpnm-") and unit.endswith(".service"):
                names.append(unit[len("vpnm-"):-len(".service")])
    return names


def enable_autostart(name: str, run: Optional[RunCmd] = None) -> None:
    """Enable the profile unit; raise RunnerError on systemctl failure."""
    rc, _out, err = systemctl_enable(name, run)
    if rc != 0:
        raise RunnerError(f"systemctl --user enable {unit_name(name)} "
                          f"failed: {err.strip()}")


def disable_autostart(name: str, run: Optional[RunCmd] = None) -> None:
    rc, _out, err = systemctl_disable(name, run)
    if rc != 0:
        raise RunnerError(f"systemctl --user disable {unit_name(name)} "
                          f"failed: {err.strip()}")


# --- port probe ---------------------------------------------------------------

def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Bind-probe a TCP port; True when something already listens there."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return True
    return False


# --- stale-client adoption ----------------------------------------------------

def _own_stale_pids(port: int, name: str,
                    run: Optional[RunCmd] = None) -> List[int]:
    """PIDs listening on ``port`` that are this profile's own orphaned client.

    Whitelist (per project safety rules): the executable must live in BIN_DIR
    and the command line must reference this profile's generated config.
    Anything else is not ours and is left untouched.
    """
    rc, out, _err = (run or run_cmd)(["ss", "-tlnp", "-H", f"sport = :{port}"])
    if rc != 0:
        return []
    wanted_config = str(GENERATED_DIR / f"{name}.json").encode()
    pids: List[int] = []
    for pid_str in re.findall(r"pid=(\d+)", out):
        pid = int(pid_str)
        try:
            exe = os.readlink(f"/proc/{pid}/exe")
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        if exe.startswith(str(BIN_DIR)) and wanted_config in cmdline:
            pids.append(pid)
    return pids


def _terminate_stale(pids: List[int], timeout: float = 2.0) -> None:
    """SIGTERM own stale clients, then SIGKILL after the grace period."""
    def alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and any(alive(p) for p in pids):
        time.sleep(0.1)
    for pid in pids:
        if alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


# --- lifecycle ----------------------------------------------------------------

def _require_profile(name: str) -> Tuple[Dict[str, object], ProfileLink]:
    from . import profiles  # local import to keep module import light
    record = profiles.get(name)
    link = parse_link(str(record["link"]))
    return record, link


def up(name: str, run: Optional[RunCmd] = None) -> Path:
    """Generate config + unit and start the profile via systemctl --user."""
    record, link = _require_profile(name)
    socks_port = int(str(record["socks_port"]))
    if port_in_use(socks_port):
        # Desync recovery: the client for this very profile is running but
        # state lost track of it (e.g. unit stopped out-of-band, state edited,
        # or an orphan after a crash). Adopt it instead of dead-ending.
        if is_active(name, run):
            rc, _out, err = systemctl_stop(name, run)
            if rc != 0:
                raise RunnerError(
                    f"systemctl --user stop {unit_name(name)} failed: "
                    f"{err.strip()}")
        else:
            stale = _own_stale_pids(socks_port, name, run)
            if not stale:
                raise RunnerError(
                    f"port {socks_port} is already in use by another "
                    "program; stop the conflicting client first "
                    "(vpn down / vpn switch)")
            _terminate_stale(stale)
    protocol = str(record["protocol"])
    config_path = write_config(name, link, socks_port)
    bin_path = BIN_DIR / bin_name(protocol)
    if not bin_path.exists():
        raise RunnerError(
            f"binary {bin_path} is missing; run 'vpn install-bins' first")
    write_unit(name, protocol, bin_path, config_path)
    rc, _out, err = daemon_reload(run)
    if rc != 0:
        raise RunnerError(f"systemctl --user daemon-reload failed: {err.strip()}")
    rc, _out, err = systemctl_start(name, run)
    if rc != 0:
        raise RunnerError(f"systemctl --user start {unit_name(name)} "
                          f"failed: {err.strip()}")
    set_active(name)
    return config_path


def down(name: Optional[str] = None, run: Optional[RunCmd] = None) -> str:
    """Stop the active (or named) profile; clears the active tracking."""
    target = name or get_active()
    if not target:
        raise RunnerError("no active profile; nothing to stop")
    rc, _out, err = systemctl_stop(target, run)
    if rc != 0:
        raise RunnerError(f"systemctl --user stop {unit_name(target)} "
                          f"failed: {err.strip()}")
    if get_active() == target:
        set_active(None)
    return target


def switch(name: str, run: Optional[RunCmd] = None) -> Path:
    """Stop the currently active profile (if any), then start ``name``."""
    current = get_active()
    if current and current != name:
        down(current, run)
    elif current == name and is_active(name, run):
        # already running the same profile; restart cleanly
        down(name, run)
    return up(name, run)


def _sync_sysproxy(run: Optional[RunCmd] = None) -> Optional[int]:
    """Re-point the GNOME system proxy at the active profile's SOCKS port.

    No-op unless the sysproxy flag is stored in state.json and a profile is
    active. Returns the applied port, or None when nothing was done.
    Raises SysproxyError (from the sysproxy module) when gsettings fails.
    """
    from . import sysproxy as sysproxy_mod  # lazy: sysproxy imports runners
    if not get_sysproxy():
        return None
    active = get_active()
    if not active:
        return None
    record = _require_profile(active)[0]
    socks_port = int(str(record["socks_port"]))
    sysproxy_mod.on(socks_port, run=run)
    return socks_port


def _sync_sysproxy_off(run: Optional[RunCmd] = None) -> bool:
    """Turn the GNOME system proxy off when the sysproxy flag is stored.

    Returns True when 'off' was applied; False when the flag is not set
    (nothing to do). Raises SysproxyError when gsettings fails.
    """
    from . import sysproxy as sysproxy_mod  # lazy: sysproxy imports runners
    if not get_sysproxy():
        return False
    sysproxy_mod.off(run=run)
    return True


def external_ip(socks_port: int, run: Optional[RunCmd] = None) -> Optional[str]:
    """Fetch the external IP through the local SOCKS port; None on failure."""
    run = run or run_cmd
    cmd = ["curl", "--socks5-hostname", f"127.0.0.1:{socks_port}",
           "--max-time", str(CURL_TIMEOUT), "-s", "https://api.ipify.org"]
    try:
        rc, out, _err = run(cmd)
    except Exception:  # defensive: the probe must never crash `status`
        return None
    if rc == 0 and out.strip():
        return out.strip()
    return None
