"""Interactive TUI menu (questionary).

``vpn`` with no arguments opens the menu. Each action delegates to the same
CLI-layer functions used by the commands, then returns to the menu in a loop;
Ctrl+C or 'Exit' leaves the loop.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

import typer

try:
    import questionary  # type: ignore[import-not-found]
except ImportError:  # tests inject a fake; the menu errors at runtime
    questionary = None  # type: ignore[assignment]

from . import doctor, profiles, runners, settings, sysproxy
from .links import LinkError


class MenuExit(Exception):
    """Raised by the 'Exit' action to leave the menu loop."""


def _echo(msg: str) -> None:
    typer.echo(msg)


def _ok(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.GREEN)


def _err(msg: str) -> None:
    typer.secho(f"error: {msg}", fg=typer.colors.RED)


def _warn(msg: str) -> None:
    typer.secho(f"warning: {msg}", fg=typer.colors.YELLOW)


# --- actions (shared with the CLI command layer) -------------------------------

def _sync_sysproxy_after(name: str, action: str) -> None:
    """Auto-apply GNOME sysproxy to the new profile when the flag is on."""
    try:
        port = runners._sync_sysproxy()
    except sysproxy.SysproxyError as exc:
        _warn(f"could not update the system proxy after {action}: {exc}")
        return
    if port is not None:
        _ok(f"system proxy updated → 127.0.0.1:{port} (profile '{name}')")


def _sync_sysproxy_off_after() -> None:
    try:
        if runners._sync_sysproxy_off():
            _ok("system proxy off")
    except sysproxy.SysproxyError as exc:
        _warn(f"could not turn the system proxy off: {exc}")


def action_connect() -> None:
    rows = profiles.list_profiles()
    if not rows:
        _warn("no profiles; use 'vpn profile import' or 'vpn profile pull'")
        return
    names = [str(r["name"]) for r in rows]
    name = questionary.select("Connect to:", choices=names).ask()
    if name:
        try:
            runners.up(name)
        except (profiles.ProfileStoreError, runners.RunnerError, LinkError) as exc:
            _err(str(exc))
        else:
            _ok(f"started '{name}'")
            _sync_sysproxy_after(str(name), "connect")


def action_disconnect() -> None:
    try:
        stopped = runners.down()
    except runners.RunnerError as exc:
        _err(str(exc))
    else:
        _ok(f"stopped '{stopped}'")
        _sync_sysproxy_off_after()


def action_switch() -> None:
    rows = profiles.list_profiles()
    if not rows:
        _warn("no profiles to switch to")
        return
    names = [str(r["name"]) for r in rows]
    name = questionary.select("Switch to:", choices=names).ask()
    if not name:
        return
    try:
        runners.switch(name)
    except (profiles.ProfileStoreError, runners.RunnerError, LinkError) as exc:
        _err(str(exc))
    else:
        _ok(f"switched to '{name}'")
        _sync_sysproxy_after(str(name), "switch")


def action_status() -> None:
    _run_status()


def _run_status() -> None:
    active = runners.get_active()
    if not active:
        _echo("no active profile")
        return
    try:
        record = profiles.get(active)
    except profiles.ProfileStoreError as exc:
        _err(f"active profile record is broken: {exc}")
        return
    unit_active = runners.is_active(active)
    _echo(f"active profile: {active}")
    _echo(f"protocol:       {record['protocol']}")
    _echo(f"socks port:     127.0.0.1:{record['socks_port']}")
    _echo(f"unit state:     {'active' if unit_active else 'inactive/failed'}")
    ip = runners.external_ip(int(record["socks_port"]))
    if ip:
        _echo(f"external IP:    {ip}")
    else:
        _warn("external IP probe failed")


def action_profile_list() -> None:
    rows = profiles.list_profiles()
    if not rows:
        _echo("no profiles")
        return
    for row in rows:
        _echo(f"{row['name']}\t{row['protocol']}\tsocks:{row['socks_port']}")


def action_profile_import() -> None:
    link = questionary.text("Share link:").ask()
    if not link:
        return
    from .links import parse_link
    try:
        parsed = parse_link(link)
    except LinkError as exc:
        _err(str(exc))
        return
    try:
        record = profiles.add(parsed, raw_link=link)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        return
    _ok(f"added profile '{record['name']}' "
        f"(protocol={record['protocol']}, socks_port={record['socks_port']})")


def action_profile_remove() -> None:
    rows = profiles.list_profiles()
    if not rows:
        _echo("no profiles")
        return
    name = questionary.select(
        "Remove profile:", choices=[str(r["name"]) for r in rows]).ask()
    if not name:
        return
    try:
        profiles.remove(name)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        return
    if runners.get_active() == name:
        try:
            runners.down(name)
        except runners.RunnerError as exc:
            _warn(f"could not stop the running client: {exc}")
        else:
            _ok(f"stopped running client '{name}'")
    _ok(f"removed profile '{name}'")


def action_profile_pull() -> None:
    host = settings.get_host() or questionary.text(
        "ssh host (user@server):").ask()
    if not host:
        return
    _run_pull(host)


def _run_pull(host: str) -> None:
    from . import pull as pull_mod
    try:
        actions = pull_mod.pull(host)
    except pull_mod.PullError as exc:
        _err(str(exc))
        return
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        return
    for name, action in actions:
        if action == "imported":
            _ok(f"imported '{name}'")
        elif action == "updated":
            _warn(f"updated '{name}' (link changed on the server)")
        else:
            _echo(f"skipped '{name}' (unchanged)")
    try:
        settings.set_host(host)
        _ok(f"saved host '{host}' to settings")
    except OSError as exc:
        _warn(f"could not save host to settings: {exc}")


def _run_sysproxy(on_off: str) -> None:
    if on_off == "on":
        active = runners.get_active()
        if not active:
            _err("no active profile; connect first to pick a SOCKS port")
            return
        try:
            record = profiles.get(active)
        except profiles.ProfileStoreError as exc:
            _err(str(exc))
            return
        try:
            sysproxy.on(int(record["socks_port"]))
            runners.set_sysproxy(True)
        except sysproxy.SysproxyError as exc:
            _err(str(exc))
        else:
            _ok(f"system proxy on: socks {sysproxy.SOCKS_HOST}:"
                f"{record['socks_port']}")
    elif on_off == "off":
        try:
            sysproxy.off()
            runners.set_sysproxy(False)
        except sysproxy.SysproxyError as exc:
            _err(str(exc))
        else:
            _ok("system proxy off")
    else:
        try:
            mode, host, port = sysproxy.status()
        except sysproxy.SysproxyError as exc:
            _err(str(exc))
            return
        _echo(f"mode: {mode}, socks: {host}:{port}")


def action_sysproxy() -> None:
    choice = questionary.select(
        "System proxy:",
        choices=["on", "off", "status"]).ask()
    if choice:
        _run_sysproxy(choice)


def action_doctor() -> None:
    host = settings.get_host()
    checks, verdict = doctor.run_doctor(host)
    for status, subject, detail in checks:
        _echo(f"[{status}] {subject}: {detail}")
    typer.secho(f"verdict: {verdict}",
                fg=typer.colors.GREEN if verdict == "ok" else (
                    typer.colors.YELLOW if verdict == "warn"
                    else typer.colors.RED))


def action_probe() -> None:
    """Probe all profiles and offer to switch to the fastest."""
    import time as time_mod

    from .cli import _print_probe_table
    from . import probe as probe_mod
    try:
        outcome = probe_mod.run_probe(switch_best=False,
                                      sleep_fn=time_mod.sleep)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        return
    _print_probe_table(outcome)
    fastest = outcome.get("fastest")
    if not fastest:
        _err("all profiles failed the probe")
        return
    _echo(f"fastest: {fastest} ({_probe_latency_of(outcome)} ms)")
    choice = questionary.select(
        f"Switch to {fastest}?", choices=["yes", "no"]).ask()
    if choice == "yes":
        try:
            runners.switch(str(fastest))
        except (profiles.ProfileStoreError, runners.RunnerError,
                LinkError) as exc:
            _err(str(exc))
        else:
            _ok(f"switched to '{fastest}'")
            _sync_sysproxy_after(str(fastest), "probe")
    else:
        _echo("staying on the initially active profile "
              f"({outcome.get('initial_active')})")


def _probe_latency_of(outcome: dict) -> object:
    for row in outcome.get("rows", []):
        if row.get("name") == outcome.get("fastest"):
            return row.get("latency_ms")
    return "?"


def action_channels() -> None:
    """Show the VPN channel availability matrix."""
    from .cli import _print_channels_table
    from . import channels as channels_mod
    outcome = channels_mod.run_monitor()
    _print_channels_table(outcome)
    healthy = int(str(outcome["healthy"]))
    total = int(str(outcome["total"]))
    typer.secho(f"healthy channels: {healthy}/{total}",
                fg=typer.colors.GREEN if healthy else typer.colors.RED)


def action_zapret() -> None:
    """Channel 0: zapret on/off/status from the menu (same CLI-layer logic)."""
    from . import zapret as zapret_mod
    choice = questionary.select(
        "Канал 0 (zapret):",
        choices=["status", "on", "off"]).ask()
    if not choice:
        return
    if choice == "status":
        for line in zapret_mod.status_lines():
            _echo(line)
        return
    ok, message = zapret_mod.control(choice)
    (_ok if ok else _err)(message)


def action_exit() -> None:
    raise MenuExit()


# --- menu wiring ----------------------------------------------------------------

ActionMap = Dict[str, Optional[Callable[[], None]]]

MENU_ACTIONS: ActionMap = {
    "Connect": action_connect,
    "Disconnect": action_disconnect,
    "Switch": action_switch,
    "Status": action_status,
    "Мониторинг каналов": action_channels,
    "Канал 0 (zapret)": action_zapret,
    "Опрос протоколов / выбрать быстрейший": action_probe,
    "Profiles": None,  # submenu, handled in run_menu
    "System proxy": action_sysproxy,
    "Doctor": action_doctor,
    "Exit": action_exit,
}

PROFILE_ACTIONS: ActionMap = {
    "List profiles": action_profile_list,
    "Import link": action_profile_import,
    "Remove profile": action_profile_remove,
    "Pull from server": action_profile_pull,
    "Back": None,
}


def run_menu() -> None:
    """The main loop: pick an action, run it, return to the menu."""
    if questionary is None:
        _err("questionary is required for the interactive menu: "
             "pip install questionary")
        return
    while True:
        try:
            choice = questionary.select(
                "vpn-manager:", choices=list(MENU_ACTIONS)).ask()
        except questionary.errors.Abort:  # Ctrl+C
            return
        if choice is None:  # Esc / aborted prompt
            return
        if choice == "Profiles":
            while True:
                try:
                    sub = questionary.select(
                        "Profiles:", choices=list(PROFILE_ACTIONS)).ask()
                except questionary.errors.Abort:
                    break
                if sub is None or sub == "Back":
                    break
                action = PROFILE_ACTIONS[sub]
                if callable(action):
                    action()
        else:
            action = MENU_ACTIONS[choice]
            if callable(action):
                action()


def main_no_args() -> None:
    """Entry point used by cli.main when no command is given."""
    run_menu()
