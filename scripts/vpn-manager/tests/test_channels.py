"""Tests for the channel monitor: vpnm / ZeroTier / AmneziaWG / zapret.

All subprocess interaction is mocked (run stubs + tmp-path store fixtures);
no root, no network, no docker.
"""

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import channels, profiles, runners  # noqa: E402
from vpnm.cli import app  # noqa: E402
from vpnm.links import parse_link  # noqa: E402

FIXTURE_VLESS = (
    "vless://11111111-2222-3333-4444-555555555555@203.0.113.10:443"
    "?security=reality&sni=example-test.invalid&pbk=FIXTURE_PBK&sid=ab"
    "#fixture-reality"
)

runner = CliRunner()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "STORE_PATH", tmp_path / "profiles.json")
    monkeypatch.setattr(runners, "STATE_PATH", tmp_path / "state.json")
    return tmp_path


def add_profile(name: str, port: int) -> None:
    profiles.add(parse_link(FIXTURE_VLESS), raw_link=FIXTURE_VLESS,
                 name=name, socks_port=port)


def make_run(**behaviors):
    """Build a run stub; behaviors map substring -> (rc, out, err) or callable.

    Unmatched commands return (0, "", "").
    """
    def run(cmd):
        joined = " ".join(cmd)
        for key, value in behaviors.items():
            if key in joined:
                if callable(value):
                    return value(cmd)
                return value
        return 0, "", ""

    return run


# --- vpnm channel ---------------------------------------------------------------

def test_vpnm_no_active_profile(env, monkeypatch):
    monkeypatch.setattr(channels.probe, "measure_latency", lambda *a, **k: 0.1)
    result = channels.check_vpnm(run=make_run())
    assert result["status"] == channels.STATUS_NOT_INSTALLED


def test_vpnm_ok(env, monkeypatch):
    add_profile("p1", 2080)
    runners.set_active("p1")
    monkeypatch.setattr(channels.probe, "measure_latency",
                        lambda *a, **k: 0.123)
    result = channels.check_vpnm(run=make_run())
    assert result["channel"] == "vpnm"
    assert result["status"] == "ok"
    assert result["detail"].startswith("профиль 'p1'")
    assert "egress 123.0 ms" in result["detail"]


def test_vpnm_fail_on_probe(env, monkeypatch):
    add_profile("p1", 2080)
    runners.set_active("p1")
    monkeypatch.setattr(channels.probe, "measure_latency",
                        lambda *a, **k: None)
    result = channels.check_vpnm(run=make_run())
    assert result["status"] == channels.STATUS_FAIL


# --- ZeroTier channel ------------------------------------------------------------

def test_zt_not_installed_no_cli(env, monkeypatch):
    monkeypatch.setattr(channels, "ZT_CLI_FALLBACK_DIRS", (env / "nopath",))
    result = channels.check_zerotier(
        run=make_run(**{"which zerotier-cli": (1, "", "")}))
    assert result["status"] == channels.STATUS_NOT_INSTALLED


def test_zt_installed_but_stopped(env):
    result = channels.check_zerotier(run=make_run(
        **{"is-active zerotier-one": (3, "inactive", "")}))
    assert result["status"] == channels.STATUS_WARN
    assert "установлен, но не запущен" in result["detail"]


def test_zt_service_not_found(env):
    result = channels.check_zerotier(run=make_run(
        **{"is-active zerotier-one": (4, "not-found", "")}))
    assert result["status"] == channels.STATUS_NOT_INSTALLED


def test_zt_no_sudo_rights(env):
    result = channels.check_zerotier(run=make_run(
        **{"sudo -n zerotier-cli status": (1, "", "sudo: a password is required")}))
    assert result["status"] == channels.STATUS_WARN
    assert "нет прав" in result["detail"]


def test_zt_offline_is_fail(env):
    result = channels.check_zerotier(run=make_run(
        **{"sudo -n zerotier-cli status": (0, "200 info OFFLINE ...", "")}))
    assert result["status"] == channels.STATUS_FAIL


def test_zt_online_no_networks(env):
    result = channels.check_zerotier(run=make_run(
        **{"sudo -n zerotier-cli status": (0, "200 info ONLINE ...", "")}))
    assert result["status"] == channels.STATUS_OK
    assert "joined-сетей нет" in result["detail"]


def test_zt_online_joined_with_ping(env):
    nets = "200 listnetworks <nwid> <name> <mac> <status> <type> <dev> <ips>\n" \
           "111abcdef  mynet  -  OK  PRIVATE  mynet  10.147.20.5/24"
    result = channels.check_zerotier(run=make_run(
        **{"sudo -n zerotier-cli status": (0, "200 info ONLINE ...", ""),
           "sudo -n zerotier-cli listnetworks": (0, nets, "")}))
    assert result["status"] == channels.STATUS_OK
    assert "111abcdef" in result["detail"]
    assert "ping 10.147.20.5 ok" in result["detail"]


def test_zt_access_denied_network_excluded(env):
    nets = "200 listnetworks <nwid> <name> <mac> <status> <type> <dev> <ips>\n" \
           "999bad  x  -  ACCESS_DENIED  PRIVATE  -  -"
    result = channels.check_zerotier(run=make_run(
        **{"sudo -n zerotier-cli status": (0, "200 info ONLINE ...", ""),
           "sudo -n zerotier-cli listnetworks": (0, nets, "")}))
    assert result["status"] == channels.STATUS_OK
    assert "999bad" not in result["detail"]
    assert "joined-сетей нет" in result["detail"]


def test_zt_ping_fail_keeps_ok(env):
    nets = "200 listnetworks <nwid> <name> <mac> <status> <type> <dev> <ips>\n" \
           "111abcdef  mynet  -  OK  PRIVATE  mynet  10.147.20.5/24"

    def ping(cmd):
        return 1, "", ""

    result = channels.check_zerotier(run=make_run(
        **{"sudo -n zerotier-cli status": (0, "200 info ONLINE ...", ""),
           "sudo -n zerotier-cli listnetworks": (0, nets, ""),
           "ping -c 1": ping}))
    assert result["status"] == channels.STATUS_OK
    assert "не отвечает" in result["detail"]


# --- AmneziaWG slot ---------------------------------------------------------------

def test_awg_slot_not_installed(env, monkeypatch):
    monkeypatch.setattr(channels, "AWG_BIN_DIRS",
                        (env / "nobin",))
    monkeypatch.setattr(channels, "AWG_CONF_DIRS",
                        (env / "noconf",))
    result = channels.check_awg(run=make_run(**{"ip -o link": (1, "", "")}))
    assert result == {"channel": "amneziawg", "status": "not_installed",
                      "detail": "артефактов нет — транспорт в разработке "
                                "(SPEC-20261110)"}


def test_awg_artifacts_but_no_iface(env, monkeypatch):
    bindir = env / "bindir"
    bindir.mkdir()
    (bindir / "awg").write_text("#!/bin/sh\n")
    confdir = env / "awgconf"
    confdir.mkdir()
    (confdir / "tunnel.conf").write_text("[Interface]\n")
    monkeypatch.setattr(channels, "AWG_BIN_DIRS", (bindir,))
    monkeypatch.setattr(channels, "AWG_CONF_DIRS", (confdir,))
    result = channels.check_awg(run=make_run())
    assert result["status"] == channels.STATUS_WARN
    assert "интерфейс не поднят" in result["detail"]


def test_awg_iface_up(env, monkeypatch):
    bindir = env / "bindir2"
    bindir.mkdir()
    (bindir / "awg").write_text("#!/bin/sh\n")
    monkeypatch.setattr(channels, "AWG_BIN_DIRS", (bindir,))
    monkeypatch.setattr(channels, "AWG_CONF_DIRS", (env / "noconf2",))
    out = "1: lo: <LOOPBACK,UP> mtu\n2: amz0: <POINTOPOINT,UP> mtu\n"
    result = channels.check_awg(
        run=make_run(**{"ip -o link": (0, out, "")}))
    assert result["status"] == channels.STATUS_OK
    assert "amz0" in result["detail"]


def test_awg_iface_amn0_up(env, monkeypatch):
    """AmneziaVPN client creates interface amn0 (real-world verified)."""
    bindir = env / "bindir3"
    bindir.mkdir()
    (bindir / "awg").write_text("#!/bin/sh\n")
    monkeypatch.setattr(channels, "AWG_BIN_DIRS", (bindir,))
    monkeypatch.setattr(channels, "AWG_CONF_DIRS", (env / "noconf3",))
    out = ("1: lo: <LOOPBACK,UP> mtu\n"
           "2: amn0: <POINTOPOINT,UP,LOWER_UP> mtu\n")
    result = channels.check_awg(
        run=make_run(**{"ip -o link": (0, out, "")}))
    assert result["status"] == channels.STATUS_OK
    assert "amn0" in result["detail"]


def test_awg_iface_amnezia_and_awg_prefixes(env, monkeypatch):
    """amnezia* and awg* interface names are also recognized."""
    bindir = env / "bindir4"
    bindir.mkdir()
    (bindir / "awg").write_text("#!/bin/sh\n")
    monkeypatch.setattr(channels, "AWG_BIN_DIRS", (bindir,))
    monkeypatch.setattr(channels, "AWG_CONF_DIRS", (env / "noconf4",))
    out = ("1: awg0: <POINTOPOINT,UP> mtu\n"
           "2: amnezia0: <POINTOPOINT,UP> mtu\n")
    result = channels.check_awg(
        run=make_run(**{"ip -o link": (0, out, "")}))
    assert result["status"] == channels.STATUS_OK
    assert "awg0" in result["detail"]
    assert "amnezia0" in result["detail"]


# --- monitor aggregate / CLI ------------------------------------------------------

def test_monitor_exit_0_when_one_ok(env, monkeypatch):
    add_profile("p1", 2080)
    runners.set_active("p1")
    monkeypatch.setattr(channels.probe, "measure_latency",
                        lambda *a, **k: 0.1)
    outcome = channels.run_monitor(run=make_run())
    assert channels.exit_code(outcome) == 0
    assert outcome["healthy"] == 1
    assert outcome["total"] == 4


def test_monitor_exit_1_when_all_down(env, monkeypatch):
    monkeypatch.setattr(channels.probe, "measure_latency", lambda *a, **k: None)
    outcome = channels.run_monitor(
        run=make_run(**{"which zerotier-cli": (1, "", "")}))
    assert channels.exit_code(outcome) == 1
    assert outcome["healthy"] == 0


def test_cli_monitor_json_structure(env, monkeypatch):
    add_profile("p1", 2080)
    runners.set_active("p1")
    monkeypatch.setattr(channels.probe, "measure_latency", lambda *a, **k: 0.1)
    result = runner.invoke(app, ["monitor", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert set(data) == {"channels", "healthy", "total"}
    assert len(data["channels"]) == 4
    for row in data["channels"]:
        assert set(row) == {"channel", "status", "detail"}
    assert data["total"] == 4


def test_cli_monitor_exit_1(env, monkeypatch):
    monkeypatch.setattr(channels.probe, "measure_latency", lambda *a, **k: None)
    result = runner.invoke(app, ["monitor"])
    assert result.exit_code == 1
    assert "healthy channels: 0/4" in result.output


def test_cli_monitor_json_is_pure_json(env, monkeypatch):
    monkeypatch.setattr(channels.probe, "measure_latency", lambda *a, **k: None)
    result = runner.invoke(app, ["monitor", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.output)  # must not raise: summary stays inside


# --- TUI wiring --------------------------------------------------------------------

def test_tui_menu_has_channels_action(env, monkeypatch):
    monkeypatch.setattr(channels.probe, "measure_latency", lambda *a, **k: 0.1)
    from vpnm import tui
    assert "Мониторинг каналов" in tui.MENU_ACTIONS
    assert callable(tui.MENU_ACTIONS["Мониторинг каналов"])
    add_profile("p1", 2080)
    runners.set_active("p1")
    tui.action_channels()
