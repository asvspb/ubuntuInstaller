"""vpn-manager CLI (typer).

Working: profile import/list/remove/show/pull, up/down/switch/status,
install-bins, install --linger, sysproxy on/off/status, doctor (WP-D),
interactive TUI when invoked without arguments.
"""

from __future__ import annotations

import json
import subprocess
from typing import Optional

import typer

from . import profiles
from .links import LinkError, parse_link

app = typer.Typer(help="vpn-manager: manage client VPN profiles "
                       "(Xray Reality/XHTTP, Hysteria2)")
profile_app = typer.Typer(help="Manage stored profiles")
app.add_typer(profile_app, name="profile")


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
        "survive logout")) -> None:
    """Install binaries (same as install-bins) and optionally enable linger."""
    from . import runners
    ctx = typer.get_current_context()
    ctx.invoke(install_bins)
    if linger:
        proc = subprocess.run(["loginctl", "enable-linger"],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            _err(f"loginctl enable-linger failed: {proc.stderr.strip()}")
            raise typer.Exit(code=2)
        _ok("linger enabled for user units")


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
            _ok(f"system proxy on: socks 127.0.0.1:{record['socks_port']} "
                f"(profile '{active}')")
        elif on_off == "off":
            sysproxy.off()
            _ok("system proxy off")
        else:
            mode, sock_host, sock_port = sysproxy.status()
            typer.echo(f"mode: {mode}")
            typer.echo(f"socks host: {sock_host}")
            typer.echo(f"socks port: {sock_port}")
    except sysproxy.SysproxyError as exc:
        _err(str(exc))
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
