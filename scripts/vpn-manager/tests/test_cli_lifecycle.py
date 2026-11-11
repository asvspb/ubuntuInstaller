"""WP-E acceptance fixes.

D-a: 'vpn profile remove <name>' must not crash when the removed profile is
not active (it used to call the nonexistent profiles.get_active()); when it
IS active, the running client must be stopped.

D-b: 'vpn status' probe: external_ip() never wired its None default run to
run_cmd, so every live call raised TypeError, which the blanket except
swallowed and the probe always reported failure. The probe must invoke curl
through the profile's SOCKS port with --socks5-hostname and --max-time.
"""

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import cli, profiles, runners  # noqa: E402
from vpnm.links import parse_link  # noqa: E402

FIXTURE_LINK = (
    "vless://11111111-2222-3333-4444-555555555555@203.0.113.10:443"
    "?type=tcp&security=reality&sni=example-test.invalid"
    "&pbk=FIXTURE_PBK_x&sid=01&fp=chrome#fixture-reality"
)
HY2_LINK = "hysteria2://FIXTURE_PW_TestPassword@203.0.113.10:8443#fixture-hy2"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "STORE_PATH", tmp_path / "profiles.json")
    monkeypatch.setattr(runners, "STATE_PATH", tmp_path / "state.json")
    return tmp_path


def _add(name: str, link: str) -> None:
    profiles.add(parse_link(link), raw_link=link, name=name)


# --- D-a ----------------------------------------------------------------------


def test_remove_inactive_profile_does_not_crash(env, monkeypatch):
    _add("fixture-reality", FIXTURE_LINK)
    runners.set_active(None)
    called = []
    monkeypatch.setattr(runners, "down",
                        lambda name=None: called.append(name))
    result = CliRunner().invoke(cli.app,
                                ["profile", "remove", "fixture-reality",
                                 "--force"])
    assert result.exception is None
    assert result.exit_code == 0
    assert "removed profile 'fixture-reality'" in result.output
    assert called == []  # not active: no stop attempt


def test_remove_active_profile_stops_client(env, monkeypatch):
    _add("fixture-reality", FIXTURE_LINK)
    runners.set_active("fixture-reality")
    called = []
    monkeypatch.setattr(runners, "down",
                        lambda name=None: called.append(name))
    result = CliRunner().invoke(cli.app,
                                ["profile", "remove", "fixture-reality",
                                 "--force"])
    assert result.exception is None
    assert result.exit_code == 0
    assert called == [None]  # runners.down() targets the active profile
    assert "stopped running client 'fixture-reality'" in result.output


# --- D-b ----------------------------------------------------------------------


def test_status_probe_uses_socks_hostname_with_max_time(env, monkeypatch):
    _add("fixture-hy2", HY2_LINK)
    runners.set_active("fixture-hy2")
    monkeypatch.setattr(runners, "is_active", lambda name: True)
    captured = {}

    def fake_run(cmd):
        captured["cmd"] = cmd
        return 0, "203.0.113.99", ""

    monkeypatch.setattr(runners, "run_cmd", fake_run)
    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exception is None
    cmd = captured["cmd"]
    assert cmd[0] == "curl"
    assert "--socks5-hostname" in cmd
    assert "127.0.0.1:2083" in cmd  # hysteria2 default SOCKS port
    assert "--max-time" in cmd
    assert cmd[cmd.index("--max-time") + 1] == str(runners.CURL_TIMEOUT)
    assert "external IP:    203.0.113.99" in result.output
    assert "timed out or failed" not in result.output


def test_cli_reset_idempotent_full_cycle(monkeypatch):
    """vpn reset: гасит профиль+split+туннель+DoH, повтор — без ошибок."""
    from vpnm import amneziawg as awg
    from vpnm import runners as runners_mod
    from vpnm.cli import app as cli_app
    from typer.testing import CliRunner
    calls: list = []
    monkeypatch.setattr(runners_mod, "get_active", lambda *a, **k: None)
    monkeypatch.setattr(awg, "domain_split_apply",
                        lambda a, run=None: calls.append(("split", a)) or (True, "ok"))
    monkeypatch.setattr(awg, "tunnel_control",
                        lambda a, run=None: calls.append(("tun", a)) or (True, "ok"))
    from vpnm import doh as doh_mod
    monkeypatch.setattr(doh_mod, "disable", lambda run=None: (True, "off"))
    result = CliRunner().invoke(cli_app, ["reset"])
    assert result.exit_code == 0, result.output
    assert ("split", "off") in calls and ("tun", "off") in calls
    assert "чистого старта" in result.output
    # повтор (уже чисто) — тоже ok
    result2 = CliRunner().invoke(cli_app, ["reset"])
    assert result2.exit_code == 0, result2.output


def test_cli_reset_reports_partial_failure(monkeypatch):
    from vpnm import amneziawg as awg
    from vpnm import runners as runners_mod
    from vpnm.cli import app as cli_app
    from typer.testing import CliRunner
    monkeypatch.setattr(runners_mod, "get_active", lambda *a, **k: None)
    monkeypatch.setattr(awg, "domain_split_apply",
                        lambda a, run=None: (False, "mock fail"))
    monkeypatch.setattr(awg, "tunnel_control",
                        lambda a, run=None: (True, "ok"))
    from vpnm import doh as doh_mod
    monkeypatch.setattr(doh_mod, "disable", lambda run=None: (True, "off"))
    result = CliRunner().invoke(cli_app, ["reset"])
    assert result.exit_code == 1
    assert "split" in result.output
