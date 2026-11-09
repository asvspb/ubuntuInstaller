"""sysproxy auto-follow: flag in state.json, applied on up/switch, off on down.

gsettings and systemctl are never touched: sysproxy.on/off are monkeypatched
with recorders, runners run_cmd is a recording stub, paths go into tmp dirs.
"""

import socket
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import cli, profiles, runners, sysproxy  # noqa: E402
from vpnm.links import parse_link  # noqa: E402

FIXTURE_VLESS = (
    "vless://11111111-2222-3333-4444-555555555555@203.0.113.10:443"
    "?security=reality&sni=example-test.invalid&pbk=FIXTURE_PBK&sid=ab"
    "#fixture-reality"
)
FIXTURE_HY2 = (
    "hysteria2://FIXTURE_PW_hy2@203.0.113.11:8443?insecure=1#fixture-hy2"
)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(runners, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(runners, "GENERATED_DIR", tmp_path / "generated")
    monkeypatch.setattr(runners, "UNIT_DIR", tmp_path / "systemd" / "user")
    monkeypatch.setattr(runners, "BIN_DIR", tmp_path / "bins")
    monkeypatch.setattr(profiles, "STORE_PATH", tmp_path / "profiles.json")
    # fake binaries so runners.up does not fail on the missing-binary check
    for name in ("xray", "hysteria"):
        (tmp_path / "bins").mkdir(parents=True, exist_ok=True)
        (tmp_path / "bins" / name).write_text("#!/bin/sh\nexit 0\n")

    def ok_run(cmd):
        return 0, "active\n" if "is-active" in cmd else "", ""

    monkeypatch.setattr(runners, "run_cmd", ok_run)
    calls = {"on": [], "off": 0}

    def fake_on(port, run=None):
        calls["on"].append(port)

    def fake_off(run=None):
        calls["off"] += 1

    monkeypatch.setattr(sysproxy, "on", fake_on)
    monkeypatch.setattr(sysproxy, "off", fake_off)
    return {"calls": calls, "tmp": tmp_path}


def _add(name, link, port):
    profiles.add(parse_link(link), raw_link=link, name=name,
                 socks_port=port)


# --- state flag ---------------------------------------------------------------

def test_sysproxy_flag_default_false(env):
    assert runners.get_sysproxy() is False


def test_sysproxy_flag_roundtrip(env):
    runners.set_sysproxy(True)
    assert runners.get_sysproxy() is True
    runners.set_sysproxy(False)
    assert runners.get_sysproxy() is False


def test_set_active_preserves_sysproxy_flag(env):
    runners.set_sysproxy(True)
    runners.set_active("a")
    assert runners.get_sysproxy() is True
    assert runners.get_active() == "a"


def test_legacy_state_migrates(env):
    import json
    env["tmp"].joinpath("state.json").write_text(
        json.dumps({"active": "phone"}))
    assert runners.get_active() == "phone"
    assert runners.get_sysproxy() is False


def test_set_sysproxy_preserves_active(env):
    runners.set_active("a")
    runners.set_sysproxy(True)
    assert runners.get_active() == "a"


# --- runners._sync_sysproxy helpers ---------------------------------------------

def test_sync_sysproxy_noop_without_flag(env):
    runners.set_active("a")
    assert runners._sync_sysproxy() is None
    assert runners._sync_sysproxy_off() is False
    assert env["calls"]["on"] == []
    assert env["calls"]["off"] == 0


def test_sync_sysproxy_applies_active_port(env):
    port = free_port()
    _add("a", FIXTURE_VLESS, port)
    runners.set_active("a")
    runners.set_sysproxy(True)
    assert runners._sync_sysproxy() == port
    assert env["calls"]["on"] == [port]


def test_sync_sysproxy_off_applies_when_flag(env):
    runners.set_sysproxy(True)
    assert runners._sync_sysproxy_off() is True
    assert env["calls"]["off"] == 1


# --- CLI lifecycle integration ---------------------------------------------------

def test_cli_up_applies_sysproxy_when_flag_on(env):
    port = free_port()
    _add("a", FIXTURE_VLESS, port)
    runners.set_sysproxy(True)
    result = CliRunner().invoke(cli.app, ["up", "a"])
    assert result.exit_code == 0
    assert env["calls"]["on"] == [port]
    assert f"system proxy updated → 127.0.0.1:{port}" in result.output


def test_cli_up_skips_sysproxy_when_flag_off(env):
    _add("a", FIXTURE_VLESS, free_port())
    result = CliRunner().invoke(cli.app, ["up", "a"])
    assert result.exit_code == 0
    assert env["calls"]["on"] == []
    assert "system proxy updated" not in result.output


def test_cli_switch_applies_new_port(env):
    port_a, port_b = free_port(), free_port()
    _add("a", FIXTURE_VLESS, port_a)
    _add("b", FIXTURE_HY2, port_b)
    runners.set_sysproxy(True)
    result = CliRunner().invoke(cli.app, ["switch", "b"])
    assert result.exit_code == 0
    assert env["calls"]["on"] == [port_b]
    assert f"system proxy updated → 127.0.0.1:{port_b}" in result.output


def test_cli_down_disables_sysproxy_and_keeps_flag(env):
    _add("a", FIXTURE_VLESS, free_port())
    runners.set_sysproxy(True)
    result = CliRunner().invoke(cli.app, ["up", "a"])
    assert result.exit_code == 0
    env["calls"]["off"] = 0
    result = CliRunner().invoke(cli.app, ["down"])
    assert result.exit_code == 0
    assert env["calls"]["off"] == 1
    assert "system proxy off" in result.output
    # the flag stays on so the next `up` re-applies the proxy
    assert runners.get_sysproxy() is True


def test_cli_down_skips_sysproxy_when_flag_off(env):
    _add("a", FIXTURE_VLESS, free_port())
    CliRunner().invoke(cli.app, ["up", "a"])
    result = CliRunner().invoke(cli.app, ["down"])
    assert result.exit_code == 0
    assert env["calls"]["off"] == 0


def test_cli_sysproxy_on_sets_flag_off_clears(env):
    port = free_port()
    _add("a", FIXTURE_VLESS, port)
    runners.set_active("a")
    result = CliRunner().invoke(cli.app, ["sysproxy", "on"])
    assert result.exit_code == 0
    assert runners.get_sysproxy() is True
    assert env["calls"]["on"] == [port]
    result = CliRunner().invoke(cli.app, ["sysproxy", "off"])
    assert result.exit_code == 0
    assert runners.get_sysproxy() is False
    assert env["calls"]["off"] == 1
