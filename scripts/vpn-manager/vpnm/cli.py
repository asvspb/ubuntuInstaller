"""vpn-manager CLI (typer).

Working: profile import/list/remove/show/pull, up/down/switch/status,
install-bins, install --linger, sysproxy on/off/status, doctor (WP-D),
interactive TUI when invoked without arguments.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Optional

import typer

from . import profiles
from .links import LinkError, parse_link

AUTOSTART_ACTIVE = "__active__"  # sentinel: --autostart without a value


def _rewrite_bare_autostart(argv: list) -> list:
    """Rewrite a valueless '--autostart' into '--autostart=<sentinel>'.

    click/typer cannot express an option whose value is optional, so the
    bare form ('vpn install --autostart') is normalized before parsing;
    the sentinel means "use the currently active profile".
    """
    out = []
    for i, tok in enumerate(argv):
        if (tok == "--autostart"
                and (i + 1 == len(argv) or str(argv[i + 1]).startswith("-"))):
            out.append(f"--autostart={AUTOSTART_ACTIVE}")
        else:
            out.append(tok)
    return out


class VpnTyper(typer.Typer):
    """Typer app that normalizes the valueless '--autostart' form on entry."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        import sys
        sys.argv[1:] = _rewrite_bare_autostart(sys.argv[1:])
        return super().__call__(*args, **kwargs)


app = VpnTyper(help="vpn-manager: manage client VPN profiles "
                    "(Xray Reality/XHTTP, Hysteria2)")
profile_app = typer.Typer(help="Manage stored profiles")
app.add_typer(profile_app, name="profile")
amneziawg_app = typer.Typer(help="AmneziaWG channel: local tunnel and "
                                 "server-side clients")
app.add_typer(amneziawg_app, name="amneziawg")


def _awg_resolve_host() -> str:
    """Saved ssh host, else interactive question; saved back on success."""
    from . import amneziawg as awg
    from . import settings
    host = settings.get_host()
    if host:
        _ok(f"using saved host '{host}'")
        return host
    # resolve_host prompts and persists the answer into settings.json
    host = awg.resolve_host(typer.prompt)
    _ok(f"saved host '{host}' to settings")
    return host


@amneziawg_app.command("status")
def amneziawg_status() -> None:
    """Two sections: LOCAL tunnel state and SERVER 'vds amneziawg status'."""
    from . import amneziawg as awg
    typer.secho("── ЛОКАЛЬНО ──", fg=typer.colors.CYAN)
    for line in awg.local_status_lines():
        typer.echo(line)
    typer.secho("── СЕРВЕР ──", fg=typer.colors.CYAN)
    try:
        host = _awg_resolve_host()
    except awg.AwgError as exc:
        _warn(f"сервер недоступен: {exc}")
        return
    lines = awg.server_status_lines(host)
    if lines is None:
        _warn(f"сервер недоступен (ssh {host} 'vds amneziawg status' "
              "failed)")
        return
    for line in lines:
        typer.echo(line)


@amneziawg_app.command("add-client")
def amneziawg_add_client(name: str) -> None:
    """Create a client on the server and scp its config to ~/.awg/NAME.conf."""
    from . import amneziawg as awg
    try:
        host = _awg_resolve_host()
        conf_path = awg.server_add_client(host, name)
    except awg.AwgError as exc:
        _err(str(exc))
        raise typer.Exit(code=1)
    _ok(f"config saved: {conf_path} (0600)")
    _ok("импортируйте файл в AmneziaVPN (AmneziaWG, из файла)")


@amneziawg_app.command("remove-client")
def amneziawg_remove_client(name: str, apply: bool = typer.Option(
        False, "--apply", help="Actually remove on the server "
        "(server-side gate; without it the server only previews)")) -> None:
    """Remove a client on the server (destructive: needs --apply)."""
    from . import amneziawg as awg
    try:
        host = _awg_resolve_host()
    except awg.AwgError as exc:
        _err(str(exc))
        raise typer.Exit(code=1)
    if apply and not typer.confirm(
            f"Remove client '{name}' on the server?", default=False):
        typer.echo("aborted")
        raise typer.Exit(code=1)
    rc, out, err = awg.server_remove_client(host, name, apply)
    if rc != 0:
        _err(f"ssh {host} remove-client failed (exit {rc}): "
             f"{(err or out).strip() or 'no error output'}")
        raise typer.Exit(code=1)
    for line in out.splitlines():
        typer.echo(line)
    _ok(f"server-side remove-client '{name}' done (apply={apply})")
    if awg.local_instance() == name:
        _warn(f"'{name}' — активный локальный инстанс; перезапустите "
              "туннель, чтобы он перечитал конфиг: sudo systemctl "
              f"restart {awg.AWG_UNIT_PREFIX}{name}")


@amneziawg_app.command("tunnel")
def amneziawg_tunnel(action: str = typer.Argument(
        ..., help="'on', 'off' or 'status'")) -> None:
    """Control the LOCAL systemd tunnel vpn-awg-split@laptop (sudo -n)."""
    from . import amneziawg as awg
    action = action.strip().lower()
    if action not in ("on", "off", "status"):
        _err("expected 'on', 'off' or 'status'")
        raise typer.Exit(code=2)
    if action == "status":
        for line in awg.tunnel_status_lines():
            typer.echo(line)
        return
    ok, message = awg.tunnel_control(action)
    if ok:
        _ok(message)
    else:
        _err(message)
        raise typer.Exit(code=1)


def _err(msg: str) -> None:
    typer.secho(f"error: {msg}", fg=typer.colors.RED, err=True)


def _ok(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.GREEN)


def _warn(msg: str) -> None:
    typer.secho(f"warning: {msg}", fg=typer.colors.YELLOW)


@profile_app.command("import")
def profile_import(link: str, name: Optional[str] = typer.Option(
        None, "--name", "-n", help="Override profile name"),
        replace: bool = typer.Option(
            False, "--replace", help="Overwrite an existing profile name")) -> None:
    """Parse a share link and store it as a profile."""
    try:
        parsed = parse_link(link)
    except LinkError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    try:
        if replace:
            try:
                profiles.remove(name or parsed.name)
            except profiles.ProfileStoreError:
                pass  # nothing to replace
        record = profiles.add(parsed, raw_link=link, name=name)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    _ok(f"added profile '{record['name']}' "
        f"(protocol={record['protocol']}, socks_port={record['socks_port']})")


@profile_app.command("list")
def profile_list() -> None:
    """List stored profiles (no credentials shown)."""
    rows = profiles.list_profiles()
    if not rows:
        typer.echo("no profiles")
        return
    for row in rows:
        typer.echo(f"{row['name']}\t{row['protocol']}\tsocks:{row['socks_port']}")


@profile_app.command("remove")
def profile_remove(name: str, force: bool = typer.Option(
        False, "--force", "-f", help="Skip confirmation")) -> None:
    """Remove a stored profile by name."""
    if not force:
        confirm = typer.confirm(f"Remove profile '{name}'?", default=False)
        if not confirm:
            typer.echo("aborted")
            raise typer.Exit(code=1)
    try:
        record = profiles.remove(name)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    from . import runners
    if runners.get_active() == name:
        try:
            runners.down()
        except runners.RunnerError as exc:
            _warn(f"could not stop the running client: {exc}")
        else:
            _ok(f"stopped running client '{name}'")
    _ok(f"removed profile '{record['name']}'")


@profile_app.command("show")
def profile_show(name: str) -> None:
    """Show profile details and generated client config (prints credentials)."""
    try:
        record = profiles.get(name)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    try:
        parsed = parse_link(record["link"])
    except LinkError as exc:
        _err(f"stored link no longer parses: {exc}")
        raise typer.Exit(code=2)
    from .xclient import generate
    config_text = generate(parsed, int(record["socks_port"]))
    typer.echo(json.dumps(
        {k: v for k, v in record.items() if k != "link"}, indent=2))
    typer.echo("---")
    typer.echo(config_text)


# --- lifecycle (WP-C) --------------------------------------------------------

def _sync_sysproxy_after(name: str, action: str) -> None:
    """Auto-apply GNOME sysproxy to the new profile when the flag is on."""
    from . import runners, sysproxy
    try:
        port = runners._sync_sysproxy()
    except sysproxy.SysproxyError as exc:
        _warn(f"could not update the system proxy after {action}: {exc}")
        return
    if port is not None:
        _ok(f"system proxy updated → 127.0.0.1:{port} (profile '{name}')")


def _sync_sysproxy_off_after() -> None:
    """Auto-disable GNOME sysproxy after 'down' when the flag is on."""
    from . import runners, sysproxy
    try:
        turned_off = runners._sync_sysproxy_off()
    except sysproxy.SysproxyError as exc:
        _warn(f"could not turn the system proxy off: {exc}")
        return
    if turned_off:
        _ok("system proxy off")


@app.command()
def up(name: str) -> None:
    """Generate configs and start the profile via a systemd user unit."""
    from . import runners
    try:
        config_path = runners.up(name)
    except (profiles.ProfileStoreError, runners.RunnerError, LinkError) as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    _ok(f"started '{name}' (config: {config_path})")
    _sync_sysproxy_after(name, "up")


@app.command()
def down(name: Optional[str] = typer.Argument(None)) -> None:
    """Stop the running VPN client (default: the active one)."""
    from . import runners
    try:
        stopped = runners.down(name)
    except (profiles.ProfileStoreError, runners.RunnerError) as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    _ok(f"stopped '{stopped}'")
    _sync_sysproxy_off_after()


@app.command()
def switch(name: str) -> None:
    """Stop the active client and start another profile."""
    from . import runners
    try:
        config_path = runners.switch(name)
    except (profiles.ProfileStoreError, runners.RunnerError, LinkError) as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    _ok(f"switched to '{name}' (config: {config_path})")
    _sync_sysproxy_after(name, "switch")


@app.command()
def status() -> None:
    """Show the active profile, unit state and external IP (via SOCKS)."""
    from . import runners
    active = runners.get_active()
    if not active:
        typer.echo("no active profile")
        return
    try:
        record = profiles.get(active)
    except profiles.ProfileStoreError as exc:
        _err(f"active profile record is broken: {exc}")
        raise typer.Exit(code=2)
    unit_active = runners.is_active(active)
    typer.echo(f"active profile: {active}")
    typer.echo(f"protocol:       {record['protocol']}")
    typer.echo(f"socks port:     127.0.0.1:{record['socks_port']}")
    typer.echo(f"unit state:     {'active' if unit_active else 'inactive/failed'}")
    ip = runners.external_ip(int(record["socks_port"]))
    if ip:
        typer.echo(f"external IP:    {ip}")
    else:
        _warn("external IP probe timed out or failed "
              "(curl through the SOCKS port did not answer within "
              f"{runners.CURL_TIMEOUT}s)")


@app.command("install-bins")
def install_bins() -> None:
    """Download xray-core and hysteria binaries into ~/.local/lib/vpn."""
    from . import bins
    try:
        results = bins.install_all()
    except bins.BinError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    for bin_name, outcome in results:
        _ok(f"{bin_name}: {outcome}")


@app.command()
def install(linger: bool = typer.Option(
        False, "--linger", help="Run 'loginctl enable-linger' so user units "
        "survive logout"),
        autostart: Optional[str] = typer.Option(
            None, "--autostart",
            help="Enable the profile unit so it starts at login; with no "
                 "value the currently active profile is used")) -> None:
    """Install binaries (+ optional linger) and optionally enable autostart."""
    from . import runners
    install_bins()
    if linger:
        proc = subprocess.run(["loginctl", "enable-linger"],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            _err(f"loginctl enable-linger failed: {proc.stderr.strip()}")
            raise typer.Exit(code=2)
        _ok("linger enabled for user units")
    if autostart is not None:
        if autostart == AUTOSTART_ACTIVE:
            active = runners.get_active()
            if not active:
                _err("no active profile; pass a profile name: "
                     "'vpn install --autostart <name>'")
                raise typer.Exit(code=1)
            autostart = active
        _autostart_enable(autostart)


def _autostart_enable(profile: str) -> None:
    """Enable the vpnm-<profile> unit (autostart at login)."""
    from . import runners
    try:
        profiles.get(profile)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    try:
        runners.enable_autostart(profile)
    except runners.RunnerError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    _ok(f"autostart enabled: {runners.unit_name(profile)}")


@app.command()
def autostart(action: str = typer.Argument(
        ..., help="'on', 'off' or 'status'")) -> None:
    """Toggle or inspect login autostart for vpnm profile units."""
    from . import runners
    action = action.strip().lower()
    if action not in ("on", "off", "status"):
        _err("expected 'on', 'off' or 'status'")
        raise typer.Exit(code=2)
    if action == "on":
        active = runners.get_active()
        if not active:
            _err("no active profile; run 'vpn up <name>' first")
            raise typer.Exit(code=1)
        _autostart_enable(active)
    elif action == "off":
        active = runners.get_active()
        targets = ([active] if active and runners.is_enabled(active)
                   else runners.enabled_vpnm_units())
        if not targets:
            _warn("no enabled vpnm units; nothing to disable")
            return
        for name in targets:
            try:
                runners.disable_autostart(str(name))
            except runners.RunnerError as exc:
                _err(str(exc))
                raise typer.Exit(code=2)
            _ok(f"autostart disabled: {runners.unit_name(str(name))}")
    else:
        active = runners.get_active()
        enabled = runners.enabled_vpnm_units()
        if active:
            state = ("enabled" if runners.is_enabled(active)
                     else "disabled")
            typer.echo(f"active profile '{active}': autostart {state}")
        else:
            typer.echo("no active profile")
        if enabled:
            typer.echo("enabled units: " + ", ".join(
                runners.unit_name(str(n)) for n in enabled))
        else:
            typer.echo("no enabled vpnm units")


# --- WP-D: pull / sysproxy / doctor / TUI --------------------------------------

@profile_app.command("pull")
def profile_pull(host: Optional[str] = typer.Option(
        None, "--host", "-H", help="ssh target like root@asv1; when omitted, "
        "the saved host is used, otherwise asked interactively")) -> None:
    """Pull share links from a VDS server ('vds gateway links' over ssh)."""
    from . import pull as pull_mod
    from . import settings
    if not host:
        host = settings.get_host()
        if not host:
            host = typer.prompt("ssh host (user@server)")
            if not host.strip():
                _err("no host given")
                raise typer.Exit(code=1)
        else:
            _ok(f"using saved host '{host}'")
    try:
        actions = pull_mod.pull(host)
    except pull_mod.PullError as exc:
        _err(str(exc))
        raise typer.Exit(code=1)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    for name, action in actions:
        if action == "imported":
            _ok(f"imported '{name}'")
        elif action == "updated":
            _warn(f"updated '{name}' (link changed on the server)")
        else:
            typer.echo(f"skipped '{name}' (unchanged)")
    try:
        settings.set_host(host)
        _ok(f"saved host '{host}' to settings")
    except OSError as exc:
        _warn(f"could not save host to settings: {exc}")


@app.command("sysproxy")
def sysproxy_cmd(on_off: str = typer.Argument(
        ..., help="'on', 'off' or 'status'")) -> None:
    """Toggle or inspect the GNOME system proxy (SOCKS to the active profile)."""
    from . import runners, settings, sysproxy
    on_off = on_off.strip().lower()
    if on_off not in ("on", "off", "status"):
        _err("expected 'on', 'off' or 'status'")
        raise typer.Exit(code=2)
    try:
        if on_off == "on":
            active = runners.get_active()
            if not active:
                _err("no active profile; run 'vpn up <name>' first to pick a "
                     "SOCKS port")
                raise typer.Exit(code=1)
            record = profiles.get(active)
            sysproxy.on(int(record["socks_port"]))
            runners.set_sysproxy(True)  # follow the active profile on up/switch
            _ok(f"system proxy on: socks 127.0.0.1:{record['socks_port']} "
                f"(profile '{active}')")
        elif on_off == "off":
            sysproxy.off()
            runners.set_sysproxy(False)  # no auto-follow until re-enabled
            _ok("system proxy off")
        else:
            mode, sock_host, sock_port = sysproxy.status()
            typer.echo(f"mode: {mode}")
            typer.echo(f"socks host: {sock_host}")
            typer.echo(f"socks port: {sock_port}")
    except sysproxy.SysproxyError as exc:
        _err(str(exc))
        raise typer.Exit(code=1)


@app.command("zapret")
def zapret_cmd(action: str = typer.Argument(
        ..., help="'on', 'off' or 'status'")) -> None:
    """Channel 0: control or inspect the zapret (nfqws DPI-desync) service."""
    from . import zapret as zapret_mod
    action = action.strip().lower()
    if action not in ("on", "off", "status"):
        _err("expected 'on', 'off' or 'status'")
        raise typer.Exit(code=2)
    if action == "status":
        for line in zapret_mod.status_lines():
            typer.echo(line)
        return
    ok, message = zapret_mod.control(action)
    if ok:
        _ok(message)
    else:
        _err(message)
        raise typer.Exit(code=1)


@app.command()
def doctor() -> None:
    """Diagnose environment: binaries, ports, v2rayA, ssh host, DNS."""
    from . import doctor as doctor_mod
    from . import settings
    checks, verdict = doctor_mod.run_doctor(settings.get_host())
    for status, subject, detail in checks:
        typer.echo(f"[{status}] {subject}: {detail}")
    colors: dict = {"ok": typer.colors.GREEN, "warn": typer.colors.YELLOW,
                    "fail": typer.colors.RED}
    typer.secho(f"verdict: {verdict}", fg=colors[verdict])
    if verdict == "fail":
        raise typer.Exit(code=1)


@app.command()
def monitor(as_json: bool = typer.Option(
        False, "--json", help="Machine-readable output (same matrix)"),
        zt_ping: Optional[str] = typer.Option(
            None, "--zt-ping",
            help="Ping an explicit ZeroTier peer IP and include the result "
                 "in the zerotier channel detail")) -> None:
    """Show the VPN channel availability matrix (vpnm, ZeroTier, AmneziaWG, zapret, dns-doh)."""
    from . import channels as channels_mod
    outcome = channels_mod.run_monitor(peer_ip=zt_ping)
    if as_json:
        typer.echo(json.dumps(outcome, ensure_ascii=False, indent=2))
    else:
        _print_channels_table(outcome)
        healthy = int(str(outcome["healthy"]))
        total = int(str(outcome["total"]))
        typer.secho(
            f"healthy channels: {healthy}/{total}",
            fg=typer.colors.GREEN if healthy else typer.colors.RED)
    raise typer.Exit(code=channels_mod.exit_code(outcome))


def _print_channels_table(outcome: dict,
                          title: Optional[str] = "vpn channels") -> None:
    """Render the channel matrix (rich table, plain-text fallback)."""
    rows = list(outcome["channels"])
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        for row in rows:
            typer.echo(f"{row['channel']}\t{row['status']}\t{row['detail']}")
        return
    status_colors = {"ok": "green", "warn": "yellow",
                     "not_installed": "yellow", "fail": "red"}
    table = Table(title=title)
    table.add_column("channel")
    table.add_column("status")
    table.add_column("detail")
    for row in rows:
        table.add_row(
            str(row["channel"]),
            f"[{status_colors.get(str(row['status']), 'white')}]"
            f"{row['status']}[/]",
            str(row["detail"]))
    Console().print(table)


CHANNELS_SECTION_HEADER = "── Каналы (vpn/zapret/zerotier/amneziawg) ──"


def _print_channels_section() -> None:
    """Append the channel matrix (same as `vpn monitor`) to the probe report.

    The AWG tun channel is outside the socks profile model: when its
    interface is up we additionally measure the YouTube generate_204
    latency through the tun iface (traffic follows AllowedIPs); otherwise
    an informational "не поднят" line is shown. This section is
    informational only — switch/fastest semantics stay socks-only.
    """
    from . import channels as channels_mod
    typer.secho(CHANNELS_SECTION_HEADER, fg=typer.colors.CYAN)
    rows = channels_mod.check_all()
    _print_channels_table({"channels": rows}, title=None)
    awg_row = next((r for r in rows if r["channel"] == "amneziawg"), None)
    if awg_row is not None and awg_row["status"] == channels_mod.STATUS_OK:
        latency = channels_mod.awg_tunnel_latency()
        if latency is not None:
            typer.secho(f"awg (tun): {latency} ms", fg=typer.colors.GREEN)
        else:
            typer.secho("awg (tun): интерфейс поднят, замер задержки "
                        "не удался", fg=typer.colors.YELLOW)
    else:
        typer.secho("awg (tun): не поднят", fg=typer.colors.YELLOW)


@app.command()
def probe(switch_best: bool = typer.Option(
        True, "--switch-best/--no-switch",
        help="Switch to the fastest ok profile when done (default); "
             "--no-switch restores the initially active profile")) -> None:
    """Probe all profiles' latency and report/switch to the fastest."""
    from . import probe as probe_mod
    import time as time_mod
    try:
        outcome = probe_mod.run_probe(switch_best=switch_best,
                                      sleep_fn=time_mod.sleep)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    _print_probe_table(outcome)
    _print_channels_section()
    fastest = outcome["fastest"]
    switched_to = outcome["switched_to"]
    if not fastest:
        _err("all profiles failed the probe; nothing to switch to")
        raise typer.Exit(code=1)
    if switched_to:
        _ok(f"switched to fastest: {fastest} ({_latency_of(outcome)} ms)")
        _sync_sysproxy_after(str(fastest), "probe")
    elif switch_best:
        _warn(f"fastest is {fastest} but the switch failed; "
              "the previously active profile was restored")


def _latency_of(outcome: dict) -> object:
    for row in outcome["rows"]:
        if row.get("name") == outcome.get("fastest"):
            return row.get("latency_ms")
    return "?"


def _print_probe_table(outcome: dict) -> None:
    """Render the probe results as a rich table (plain text fallback)."""
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        for row in outcome["rows"]:
            status = "ok" if row.get("ok") else "fail"
            latency = row.get("latency_ms")
            line = (f"{row.get('name')}\t{row.get('protocol')}\t"
                    f"socks:{row.get('port')}\t{status}\t"
                    f"{'' if latency is None else f'{latency} ms'}")
            if row.get("error"):
                line += f"\t{row.get('error')}"
            typer.echo(line)
        return
    table = Table(title="vpn probe")
    table.add_column("profile")
    table.add_column("protocol")
    table.add_column("socks port")
    table.add_column("status")
    table.add_column("latency (median of 3)")
    table.add_column("error")
    for row in outcome["rows"]:
        status = "ok" if row.get("ok") else "fail"
        latency = row.get("latency_ms")
        table.add_row(
            str(row.get("name")), str(row.get("protocol")),
            f"127.0.0.1:{row.get('port')}", status,
            "" if latency is None else f"{latency} ms",
            str(row.get("error") or ""))
    Console().print(table)
    fastest = outcome.get("fastest")
    if fastest:
        typer.secho(f"fastest: {fastest} ({_latency_of(outcome)} ms)",
                    fg=typer.colors.GREEN)


def _tui() -> None:
    from . import tui
    if tui.questionary is None:
        _err("questionary is required for the interactive menu: "
             "pip install questionary")
        raise SystemExit(1)
    try:
        tui.main_no_args()
    except KeyboardInterrupt:
        typer.echo("")


def main() -> None:
    import sys

    if len(sys.argv) <= 1:
        _tui()
    else:
        app()


if __name__ == "__main__":
    main()
