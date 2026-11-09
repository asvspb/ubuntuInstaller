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
import socket
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

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


def load_state(path: Optional[Path] = None) -> Dict[str, Optional[str]]:
    path = path if path is not None else STATE_PATH
    if not path.exists():
        return {"active": None}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"active": None}
    if not isinstance(state, dict):
        return {"active": None}
    active = state.get("active")
    return {"active": active if isinstance(active, str) else None}


def save_state(state: Dict[str, Optional[str]], path: Optional[Path] = None) -> None:
    _atomic_write(path if path is not None else STATE_PATH,
                  json.dumps(state, indent=2) + "\n")


def get_active(path: Optional[Path] = None) -> Optional[str]:
    return load_state(path)["active"]


def set_active(name: Optional[str], path: Optional[Path] = None) -> None:
    save_state({"active": name}, path)


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
        raise RunnerError(
            f"port {socks_port} is already in use; "
            "stop the conflicting client first (vpn down / vpn switch)")
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


def external_ip(socks_port: int, run: Optional[RunCmd] = None) -> Optional[str]:
    """Fetch the external IP through the local SOCKS port; None on failure."""
    cmd = ["curl", "--socks5-hostname", f"127.0.0.1:{socks_port}",
           "--max-time", str(CURL_TIMEOUT), "-s", "https://api.ipify.org"]
    try:
        rc, out, _err = run(cmd)
    except Exception:  # defensive: the probe must never crash `status`
        return None
    if rc == 0 and out.strip():
        return out.strip()
    return None
