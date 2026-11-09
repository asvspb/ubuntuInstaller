"""Tests for runners: unit generation, state tracking, lifecycle, port probe.

systemctl / network are never touched — run_cmd is monkeypatched with a
recording stub, paths are redirected into tmp dirs.
"""

import os
import socket
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import profiles, runners  # noqa: E402
from vpnm.links import ProfileLink, parse_link  # noqa: E402

FIXTURE_VLESS = (
    "vless://11111111-2222-3333-4444-555555555555@203.0.113.10:443"
    "?security=reality&sni=example-test.invalid&pbk=FIXTURE_PBK&sid=ab"
    "#fixture-reality"
)
FIXTURE_HY2 = (
    "hysteria2://FIXTURE_PW_hy2@203.0.113.11:8443?insecure=1"
    "#fixture-hy2"
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Redirect every runner path into tmp dirs; empty profile store."""
    paths = {
        "STATE_PATH": tmp_path / "state.json",
        "GENERATED_DIR": tmp_path / "generated",
        "UNIT_DIR": tmp_path / "systemd" / "user",
        "BIN_DIR": tmp_path / "bins",
    }
    for attr, value in paths.items():
        monkeypatch.setattr(runners, attr, value)
    monkeypatch.setattr(profiles, "STORE_PATH", tmp_path / "profiles.json")
    return paths


def make_link(protocol="xray-reality", name="a"):
    if protocol == "hysteria2":
        return parse_link(FIXTURE_HY2)
    link = parse_link(FIXTURE_VLESS)
    if name != "a" or protocol != "xray-reality":
        return ProfileLink(protocol=protocol, host=link.host, port=link.port,
                           credential=link.credential, name=name,
                           params=link.params)
    return link


def make_fake_bin(paths):
    bins_dir = paths["BIN_DIR"]
    bins_dir.mkdir(parents=True, exist_ok=True)
    (bins_dir / "xray").write_text("#!/bin/sh\nexit 0\n")
    (bins_dir / "hysteria").write_text("#!/bin/sh\nexit 0\n")


# --- unit text snapshot -------------------------------------------------------

def test_unit_text_xray_snapshot():
    text = runners.unit_text("office", "xray-reality",
                             Path("/home/u/.local/lib/vpn/xray"),
                             Path("/cfg/office.json"))
    assert text == "\n".join([
        "[Unit]",
        "Description=vpn-manager client 'office' (xray-reality)",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "ExecStart=/home/u/.local/lib/vpn/xray -c /cfg/office.json",
        "Restart=on-failure",
        "RestartSec=3",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])


def test_unit_text_hysteria_uses_client_subcommand():
    text = runners.unit_text("hy", "hysteria2",
                             Path("/b/hysteria"), Path("/cfg/hy.yaml"))
    assert "ExecStart=/b/hysteria client -c /cfg/hy.yaml" in text
    assert "After=network-online.target" in text
    assert "Restart=on-failure" in text


def test_write_unit_file(env):
    make_fake_bin(env)
    out = runners.write_unit("a", "xray-reality",
                             env["BIN_DIR"] / "xray",
                             env["GENERATED_DIR"] / "a.json")
    assert out.name == "vpnm-a.service"
    assert out.exists()
    assert "ExecStart=" in out.read_text()


# --- generated configs --------------------------------------------------------

def test_write_config_xray_json(env):
    path = runners.write_config("a", make_link(), 2080)
    assert path == env["GENERATED_DIR"] / "a.json"
    text = path.read_text()
    assert '"port": 2080' in text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_config_hysteria_yaml(env):
    path = runners.write_config("hy", make_link("hysteria2"), 2083)
    assert path == env["GENERATED_DIR"] / "hy.yaml"
    assert "socks5:" in path.read_text()


# --- state tracking -----------------------------------------------------------

def test_state_active_tracking(env):
    assert runners.get_active() is None
    runners.set_active("a")
    assert runners.get_active() == "a"
    runners.set_active(None)
    assert runners.get_active() is None


def test_state_missing_file_is_none(env):
    env["STATE_PATH"].write_text("not json at all")
    assert runners.get_active() is None


# --- port probe ---------------------------------------------------------------

def test_port_in_use_false_when_free():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert runners.port_in_use(port) is False


def test_port_in_use_true_when_listening():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.listen(1)
        assert runners.port_in_use(port) is True


# --- lifecycle: up / down / switch ---------------------------------------------

def recording_run(calls):
    def run(cmd):
        calls.append(list(cmd))
        return 0, "active\n" if "is-active" in cmd else "", ""
    return run


def seed_profiles(names):
    for name in names:
        profiles.add(make_link(name=name), raw_link=FIXTURE_VLESS,
                     name=name)


def test_up_generates_and_starts(env, monkeypatch):
    seed_profiles(["a"])
    make_fake_bin(env)
    calls = []
    config_path = runners.up("a", run=recording_run(calls))
    assert config_path == env["GENERATED_DIR"] / "a.json"
    unit = env["UNIT_DIR"] / "vpnm-a.service"
    assert unit.exists()
    assert ["systemctl", "--user", "daemon-reload"] in calls
    assert ["systemctl", "--user", "start", "vpnm-a.service"] in calls
    assert runners.get_active() == "a"


def test_up_missing_binary_fails(env):
    seed_profiles(["a"])
    with pytest.raises(runners.RunnerError, match="install-bins"):
        runners.up("a", run=recording_run([]))


def test_up_port_conflict(env):
    seed_profiles(["a"])
    make_fake_bin(env)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 2080))
        sock.listen(1)
        with pytest.raises(runners.RunnerError, match="already in use"):
            runners.up("a", run=recording_run([]))
    assert runners.get_active() is None


def test_down_stops_and_clears_state(env):
    seed_profiles(["a"])
    runners.set_active("a")
    calls = []
    stopped = runners.down(run=recording_run(calls))
    assert stopped == "a"
    assert ["systemctl", "--user", "stop", "vpnm-a.service"] in calls
    assert runners.get_active() is None


def test_down_without_active_fails(env):
    with pytest.raises(runners.RunnerError, match="no active profile"):
        runners.down(run=recording_run([]))


def test_switch_sequence_stops_old_then_starts_new(env):
    seed_profiles(["a", "b"])
    make_fake_bin(env)
    runners.set_active("a")
    calls = []
    runners.switch("b", run=recording_run(calls))
    order = [(cmd[2], cmd[3] if len(cmd) > 3 else "") for cmd in calls
             if cmd[:3] == ["systemctl", "--user", "start"]]
    stops = [cmd for cmd in calls if cmd[:3] == ["systemctl", "--user", "stop"]]
    starts = [cmd for cmd in calls
              if cmd[:3] == ["systemctl", "--user", "start"]]
    assert stops == [["systemctl", "--user", "stop", "vpnm-a.service"]]
    assert starts == [["systemctl", "--user", "start", "vpnm-b.service"]]
    assert order or starts  # start of new profile present
    # stop must come before the new start
    assert calls.index(stops[0]) < calls.index(starts[0])
    assert runners.get_active() == "b"


def test_switch_same_name_restarts(env):
    seed_profiles(["a"])
    make_fake_bin(env)
    runners.set_active("a")
    calls = []
    runners.switch("a", run=recording_run(calls))
    stops = [c for c in calls if c[:3] == ["systemctl", "--user", "stop"]]
    starts = [c for c in calls if c[:3] == ["systemctl", "--user", "start"]]
    assert stops and starts
    assert runners.get_active() == "a"


def test_systemctl_failure_propagates(env):
    seed_profiles(["a"])
    make_fake_bin(env)

    def failing_run(cmd):
        return 1, "", "boom"

    with pytest.raises(runners.RunnerError, match="boom"):
        runners.up("a", run=failing_run)
    # failed start must not mark the profile active
    assert runners.get_active() is None


def test_remove_unit(env):
    out = runners.write_unit("a", "xray-reality",
                             Path("/b/xray"), Path("/cfg/a.json"))
    assert runners.remove_unit("a") is True
    assert not out.exists()
    assert runners.remove_unit("a") is False


def test_is_active_parses_output(env, monkeypatch):
    monkeypatch.setattr(runners, "run_cmd",
                        lambda cmd: (0, "active\n", ""))
    assert runners.is_active("a") is True
    monkeypatch.setattr(runners, "run_cmd",
                        lambda cmd: (3, "inactive\n", ""))
    assert runners.is_active("a") is False


def test_external_ip_none_on_failure():
    def timeout_run(cmd):
        raise TimeoutError("curl")

    assert runners.external_ip(2080, run=timeout_run) is None


def test_external_ip_success():
    def ok_run(cmd):
        assert "--socks5-hostname" in cmd
        assert "127.0.0.1:2080" in cmd
        return 0, "203.0.113.99\n", ""

    assert runners.external_ip(2080, run=ok_run) == "203.0.113.99"
