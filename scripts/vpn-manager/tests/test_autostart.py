"""Autostart: enable/disable/status of vpnm profile units via systemctl --user.

systemctl is never executed — runners.run_cmd is monkeypatched with recording
stubs and canned is-enabled output; bins.install_all is stubbed so
'vpn install' does not touch the network.
"""

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import bins, cli, profiles, runners  # noqa: E402
from vpnm.links import parse_link  # noqa: E402

FIXTURE_VLESS = (
    "vless://11111111-2222-3333-4444-555555555555@203.0.113.10:443"
    "?security=reality&sni=example-test.invalid&pbk=FIXTURE_PBK&sid=ab"
    "#fixture-reality"
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(runners, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(profiles, "STORE_PATH", tmp_path / "profiles.json")

    def add(name):
        profiles.add(parse_link(FIXTURE_VLESS), raw_link=FIXTURE_VLESS,
                     name=name)

    return {"add": add}


@pytest.fixture
def sysctl(monkeypatch):
    """Record systemctl calls; serve canned is-enabled / list-unit-files."""
    calls = []

    def run(cmd):
        calls.append(list(cmd))
        if cmd[:2] == ["systemctl", "--user"]:
            if cmd[2] == "is-enabled":
                return 0, "enabled\n", ""
            if cmd[2] == "list-unit-files":
                return 0, ("vpnm-office.service enabled enabled\n"
                           "vpnm-mob.service disabled disabled\n"), ""
        return 0, "", ""

    monkeypatch.setattr(runners, "run_cmd", run)
    monkeypatch.setattr(bins, "install_all", lambda: [])
    return calls


# --- runners parsing helpers ----------------------------------------------------

def test_is_enabled_parses_output(env, sysctl, monkeypatch):
    assert runners.is_enabled("office") is True

    def disabled(cmd):
        if "is-enabled" in cmd:
            return 1, "disabled\n", ""
        return 0, "", ""

    monkeypatch.setattr(runners, "run_cmd", disabled)
    assert runners.is_enabled("office") is False


def test_enabled_vpnm_units_parses_list(env, sysctl):
    assert runners.enabled_vpnm_units() == ["office"]


def test_enable_autostart_runs_systemctl_enable(env, sysctl):
    runners.enable_autostart("office")
    assert ["systemctl", "--user", "enable", "vpnm-office.service"] in sysctl


def test_enable_autostart_failure_raises(env, monkeypatch):
    def fail(cmd):
        return 1, "", "boom"

    monkeypatch.setattr(runners, "run_cmd", fail)
    with pytest.raises(runners.RunnerError, match="boom"):
        runners.enable_autostart("office")


# --- CLI: install --autostart ---------------------------------------------------

def test_install_autostart_with_profile_name(env, sysctl):
    env["add"]("office")
    result = CliRunner().invoke(cli.app, ["install", "--autostart", "office"])
    assert result.exit_code == 0
    assert ["systemctl", "--user", "enable", "vpnm-office.service"] in sysctl
    assert "autostart enabled: vpnm-office.service" in result.output


def test_install_autostart_bare_uses_active(env, sysctl):
    env["add"]("office")
    runners.set_active("office")
    result = CliRunner().invoke(
        cli.app, ["install", f"--autostart={cli.AUTOSTART_ACTIVE}"])
    assert result.exit_code == 0
    assert ["systemctl", "--user", "enable", "vpnm-office.service"] in sysctl


def test_rewrite_bare_autostart():
    argv = cli._rewrite_bare_autostart(["install", "--autostart"])
    assert argv == ["install", f"--autostart={cli.AUTOSTART_ACTIVE}"]
    argv = cli._rewrite_bare_autostart(["install", "--autostart", "mob"])
    assert argv == ["install", "--autostart", "mob"]
    argv = cli._rewrite_bare_autostart(["install", "--linger"])
    assert argv == ["install", "--linger"]


def test_install_autostart_unknown_profile_fails(env, sysctl):
    result = CliRunner().invoke(cli.app, ["install", "--autostart", "ghost"])
    assert result.exit_code == 2
    assert "not found" in result.output
    assert not [c for c in sysctl if c[2] == "enable"]


# --- CLI: autostart on/off/status ------------------------------------------------

def test_autostart_on_enables_active(env, sysctl):
    env["add"]("office")
    runners.set_active("office")
    result = CliRunner().invoke(cli.app, ["autostart", "on"])
    assert result.exit_code == 0
    assert ["systemctl", "--user", "enable", "vpnm-office.service"] in sysctl
    assert "autostart enabled: vpnm-office.service" in result.output


def test_autostart_on_without_active_fails(env, sysctl):
    result = CliRunner().invoke(cli.app, ["autostart", "on"])
    assert result.exit_code == 1
    assert "no active profile" in result.output


def test_autostart_off_disables_active(env, sysctl):
    env["add"]("office")
    runners.set_active("office")
    result = CliRunner().invoke(cli.app, ["autostart", "off"])
    assert result.exit_code == 0
    assert ["systemctl", "--user", "disable", "vpnm-office.service"] in sysctl


def test_autostart_off_without_active_disables_enabled_units(env, sysctl):
    result = CliRunner().invoke(cli.app, ["autostart", "off"])
    assert result.exit_code == 0
    assert ["systemctl", "--user", "disable", "vpnm-office.service"] in sysctl


def test_autostart_status_shows_units(env, sysctl):
    env["add"]("office")
    runners.set_active("office")
    result = CliRunner().invoke(cli.app, ["autostart", "status"])
    assert result.exit_code == 0
    assert "active profile 'office': autostart enabled" in result.output
    assert "enabled units: vpnm-office.service" in result.output


def test_autostart_bad_action_fails(env, sysctl):
    result = CliRunner().invoke(cli.app, ["autostart", "maybe"])
    assert result.exit_code == 2
