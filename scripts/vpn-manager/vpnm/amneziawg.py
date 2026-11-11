"""AmneziaWG channel management: local tunnel + remote server clients.

Two planes are managed from the same CLI group ``vpn amneziawg``:

- **local**: the system systemd instance ``vpn-awg-split@<name>.service``
  (``awg-quick up /etc/amnezia/amneziawg/<name>.conf``).  Toggling needs
  root, so ``sudo -n`` is used and degrades to a printed manual hint when
  passwordless sudo is unavailable (same pattern as ``zapret.control``).
- **server**: the VDS gateway's AWG transport, driven over ssh with
  ``vds amneziawg <cmd>``; client configs are fetched back with ``scp``
  into ``~/.awg/<name>.conf`` (0600) for import into AmneziaVPN.

The ssh host is stored in settings.json (``ssh_host``) by ``vpn profile
pull``; when absent it is asked interactively and persisted.

All subprocess interaction goes through an injectable ``run`` callable
returning ``(rc, out, err)`` with argument lists (no shell=True).  Tests
monkeypatch ``run``, ``scp_fetch`` and the path constants.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import settings
from .runners import RunCmd, run_cmd

AWG_UNIT_PREFIX = "vpn-awg-split@"
DEFAULT_LOCAL_INSTANCE = "laptop"
SERVER_CLIENTS_DIR = "/opt/my-vds/gateway/clients/amneziawg"
LOCAL_CONF_DIR = Path(os.environ.get(
    "VPN_MANAGER_AWG_CONF_DIR", str(Path.home() / ".awg")))
AWG_SERVICE = "vds-amneziawg"  # server-side unit, for log hints only

PromptFn = Callable[[str], str]


class AwgError(Exception):
    """Raised when the ssh/scp roundtrip or the host resolution fails."""


# --- host ----------------------------------------------------------------------

def resolve_host(prompt: Optional[PromptFn] = None,
                 path: Optional[Path] = None) -> str:
    """Saved ssh host from settings, else interactive prompt; always persisted.

    ``prompt`` takes the question and returns the answer (typer.prompt in
    the CLI; a stub in tests).  Raises AwgError on an empty answer.
    """
    host = settings.get_host(path)
    if host:
        return host
    if prompt is None:
        raise AwgError("ssh host is not configured; run 'vpn profile pull' "
                       "or pass it explicitly")
    answer = (prompt("ssh host (user@server)") or "").strip()
    if not answer:
        raise AwgError("no host given")
    settings.set_host(answer, path)
    return answer


# --- ssh helpers ----------------------------------------------------------------

def ssh_run(host: str, command: str,
            run: Optional[RunCmd] = None) -> Tuple[int, str, str]:
    """Run ``ssh <host> '<command>'``; argument list, never shell=True."""
    runner: RunCmd = run or run_cmd
    return runner(["ssh", host, command])


def scp_fetch(host: str, remote_path: str, local_path: Path,
              scp: Optional[RunCmd] = None) -> Tuple[int, str, str]:
    """``scp <host>:<remote> <local>``; argument list, never shell=True."""
    runner: RunCmd = scp or run_cmd
    return runner(["scp", f"{host}:{remote_path}", str(local_path)])


def _chmod_0600(path: Path) -> None:
    os.chmod(path, 0o600)


# --- local status ----------------------------------------------------------------

def local_instance(run: Optional[RunCmd] = None) -> Optional[str]:
    """Name of the running vpn-awg-split@<name> instance, else None.

    Enumerates ``systemctl list-units 'vpn-awg-split@*'``; only units whose
    state is active/activating count (list-units already filters to loaded
    units, but a failed unit is also listed, so the state column is checked).
    """
    runner: RunCmd = run or run_cmd
    rc, out, _err = runner([
        "systemctl", "list-units", "--type=service", "--plain",
        "--no-legend", AWG_UNIT_PREFIX + "*",
    ])
    if rc != 0:
        return None
    for line in out.splitlines():
        cols = line.split()
        if not cols:
            continue
        match = re.match(
            re.escape(AWG_UNIT_PREFIX) + r"(.+)\.service$", cols[0])
        if match and len(cols) >= 3 and cols[2] in ("active", "activating"):
            return match.group(1)
    return None


def local_status_lines(run: Optional[RunCmd] = None,
                       conf_dir: Optional[Path] = None) -> List[str]:
    """Human-readable LOCAL section (instance, unit, awg show, iface, confs).

    sudo-protected parts (awg show) degrade to a warning line when
    passwordless sudo is unavailable; the report itself never fails.
    """
    runner: RunCmd = run or run_cmd
    lines: List[str] = []
    instance = local_instance(runner)
    if instance is None:
        lines.append("локальный инстанс: не запущен "
                     f"(нет активного юнита {AWG_UNIT_PREFIX}<name>.service)")
    else:
        rc, state_out, _err = runner(
            ["systemctl", "is-active", f"{AWG_UNIT_PREFIX}{instance}.service"])
        state = state_out.strip() or ("active" if rc == 0 else "unknown")
        lines.append(f"локальный инстанс: {instance} ({state})")
        rc, show_out, _err = runner(["sudo", "-n", "awg", "show"])
        if rc != 0:
            lines.append("warning: awg show недоступен "
                         "(нет прав sudo -n)")
        else:
            handshakes = sum(
                1 for line in show_out.splitlines()
                if line.strip().startswith("latest handshake"))
            transfer = sum(
                1 for line in show_out.splitlines()
                if line.strip().startswith("transfer:"))
            peers = sum(
                1 for line in show_out.splitlines()
                if line.strip().startswith("peer:"))
            iface_up = any(
                line.strip().startswith("interface:")
                for line in show_out.splitlines())
            lines.append(
                "интерфейс: " + ("up" if iface_up else "не обнаружен"))
            if peers:
                lines.append(f"peers: {peers}, handshakes: {handshakes}, "
                             f"transfer-строк: {transfer}")
    confs = sorted((conf_dir or LOCAL_CONF_DIR).glob("*.conf"))
    if confs:
        lines.append("локальные конфиги: "
                     + ", ".join(c.name for c in confs))
    return lines


# --- server side -------------------------------------------------------------------

def server_status_lines(host: str,
                        run: Optional[RunCmd] = None) -> Optional[List[str]]:
    """REMOTE section: verbatim ``vds amneziawg status`` output lines.

    Returns None when ssh fails (caller prints a warning); the server
    output already masks secrets.
    """
    rc, out, err = ssh_run(host, "vds amneziawg status", run)
    if rc != 0:
        return None
    lines = [line for line in out.splitlines()]
    return lines if lines else ["(сервер вернул пустой вывод)"]


def server_add_client(host: str, name: str,
                      run: Optional[RunCmd] = None,
                      scp: Optional[RunCmd] = None,
                      conf_dir: Optional[Path] = None,
                      ) -> Path:
    """Add a client on the server and scp its config into ``~/.awg``.

    Raises AwgError with a readable message on any ssh/scp failure.
    """
    rc, out, err = ssh_run(host, f"vds amneziawg add-client {name}", run)
    if rc != 0:
        raise AwgError(
            f"ssh {host} 'vds amneziawg add-client {name}' failed "
            f"(exit {rc}): {(err or out).strip() or 'no error output'}")
    target_dir = conf_dir or LOCAL_CONF_DIR
    target = target_dir / f"{name}.conf"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AwgError(f"cannot create {target_dir}: {exc}") from exc
    remote = f"{SERVER_CLIENTS_DIR}/{name}.conf"
    rc, _out, err = scp_fetch(host, remote, target, scp)
    if rc != 0:
        raise AwgError(
            f"scp {host}:{remote} failed (exit {rc}): "
            f"{(err or '').strip() or 'no error output'}")
    try:
        _chmod_0600(target)
    except OSError as exc:
        raise AwgError(f"cannot chmod 0600 {target}: {exc}") from exc
    return target


def server_remove_client(host: str, name: str, apply: bool,
                         run: Optional[RunCmd] = None) -> Tuple[int, str, str]:
    """``vds amneziawg remove-client NAME [--apply]`` over ssh.

    Destructive server-side op; the CLI gates it with a confirm and the
    server itself requires --apply for the actual removal.
    """
    command = f"vds amneziawg remove-client {name}"
    if apply:
        command += " --apply"
    return ssh_run(host, command, run)


# --- local tunnel control ------------------------------------------------------------

def _sudo_hint(action: str) -> str:
    return f"sudo systemctl {action} {AWG_UNIT_PREFIX}{DEFAULT_LOCAL_INSTANCE}"


# --- domain split -----------------------------------------------------------------
# 15_domain-split.sh: dnsmasq+nft+fwmark маршрутизирует зоны из
# /opt/awg-split/domains.txt (по умолчанию ходовые TLD: com/org/net/io/dev/ai +
# youtube/telegram/google) через AWG. tunnel on применяет его после подъёма
# туннеля, tunnel off демонтирует до остановки туннеля.

DOMAIN_SPLIT_ENV = "VPNM_DOMAIN_SPLIT_SCRIPT"


def domain_split_script() -> Optional[Path]:
    """Путь к 15_domain-split.sh (env или дефолт репозитория), None если нет."""
    env = os.environ.get(DOMAIN_SPLIT_ENV)
    if env:
        path = Path(env).expanduser()
        return path if path.exists() else None
    default = (Path.home() / "Dev" / "ubuntuInstaller" / "scripts"
               / "15_domain-split.sh")
    return default if default.exists() else None


def domain_split_apply(action: str, run: Optional[RunCmd] = None
                       ) -> Tuple[bool, str]:
    """Установить ('on') или демонтировать ('off') доменный split.

    Скрипт сам перекидывается в sudo и идемпотентен. Никогда не бросает:
    (ok, сообщение) с ручной подсказкой при неудаче.
    """
    runner: RunCmd = run or run_cmd
    if action not in ("on", "off"):
        return False, f"неизвестное действие '{action}' (ожидается on|off)"
    script = domain_split_script()
    if script is None:
        return False, (f"15_domain-split.sh не найден (задайте env "
                       f"{DOMAIN_SPLIT_ENV}); split не применён")
    args = ["sudo", "-n", "bash", str(script)]
    if action == "off":
        args.append("--off")
    rc, out, err = runner(args)
    if rc == 0:
        word = "применён" if action == "on" else "демонтирован"
        return True, f"доменный split {word} (15_domain-split.sh)"
    detail = ((err or "").strip().splitlines() or [""])[-1]
    word = "применён" if action == "on" else "демонтирован"
    hint = "sudo bash " + str(script) + (" --off" if action == "off" else "")
    return False, (f"доменный split не {word} "
                   f"({detail or f'exit {rc}'}); вручную: {hint}")


def tunnel_control(action: str, run: Optional[RunCmd] = None
                   ) -> Tuple[bool, str]:
    """on/off the local system instance via ``sudo -n systemctl``.

    Returns (ok, message); on sudo failure message carries the manual
    hint.  Never raises on systemctl failure.
    """
    runner: RunCmd = run or run_cmd
    if action not in ("on", "off"):
        return False, f"неизвестное действие '{action}' (ожидается on|off)"
    systemctl_action = "start" if action == "on" else "stop"
    rc, _out, err = runner(
        ["sudo", "-n", "systemctl", systemctl_action,
         f"{AWG_UNIT_PREFIX}{DEFAULT_LOCAL_INSTANCE}"])
    if rc == 0:
        word = "запущен" if action == "on" else "остановлен"
        return True, (f"туннель {AWG_UNIT_PREFIX}"
                      f"{DEFAULT_LOCAL_INSTANCE} {word}")
    detail_lines = (err or "").strip().splitlines()
    detail = detail_lines[-1] if detail_lines else f"exit {rc}"
    return False, (f"не удалось {systemctl_action} "
                   f"{AWG_UNIT_PREFIX}{DEFAULT_LOCAL_INSTANCE} "
                   f"({detail}); выполните вручную: {_sudo_hint(systemctl_action)}")


def tunnel_status_lines(run: Optional[RunCmd] = None,
                        conf_dir: Optional[Path] = None) -> List[str]:
    """Status of the local tunnel (same report as the LOCAL section)."""
    return local_status_lines(run, conf_dir=conf_dir)
