"""WP-1 (SPEC-20261113): `vpn mux on|off|status` и `vpn up --mux/--no-mux`."""

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import cli, profiles, runners, settings  # noqa: E402
from vpnm.links import parse_link  # noqa: E402

FIXTURE_CDN = (
    "vless://11111111-2222-3333-4444-555555555555@203.0.113.12:2053"
    "?type=ws&security=tls&sni=cdn.example.invalid&path=%2Fcdnws"
    "&host=cdn.example.invalid&fp=chrome#fixture-cdn"
)
FIXTURE_REALITY = (
    "vless://11111111-2222-3333-4444-555555555555@203.0.113.10:443"
    "?type=tcp&security=reality&sni=example-test.invalid"
    "&pbk=FIXTURE_PBK_x&sid=01&fp=chrome#fixture-reality"
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "STORE_PATH", tmp_path / "profiles.json")
    monkeypatch.setattr(runners, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(runners, "GENERATED_DIR", tmp_path / "generated")
    monkeypatch.setattr(runners, "UNIT_DIR", tmp_path / "systemd" / "user")
    monkeypatch.setattr(runners, "BIN_DIR", tmp_path / "bins")
    monkeypatch.setattr(settings, "SETTINGS_PATH",
                        tmp_path / "settings.json")
    return tmp_path


# --- settings roundtrip --------------------------------------------------------

def test_mux_settings_roundtrip(env):
    assert settings.get_mux() is False  # отсутствие ключа = false
    settings.set_mux(True)
    assert settings.get_mux() is True
    settings.set_mux(False)
    assert settings.get_mux() is False


def test_mux_settings_preserves_foreign_keys(env):
    settings.set_mux(True)
    data = json.loads(settings.SETTINGS_PATH.read_text())
    data["ssh_host"] = "example-host"
    settings.SETTINGS_PATH.write_text(json.dumps(data))
    settings.set_mux(False)
    data = json.loads(settings.SETTINGS_PATH.read_text())
    assert data["ssh_host"] == "example-host"
    assert data["mux"] is False


def test_mux_settings_0600(env):
    import stat
    settings.set_mux(True)
    assert stat.S_IMODE(settings.SETTINGS_PATH.stat().st_mode) == 0o600


# --- CLI: vpn mux --------------------------------------------------------------

def test_cli_mux_on_off_status(env):
    runner = CliRunner()
    result = runner.invoke(cli.app, ["mux", "status"])
    assert result.exit_code == 0, result.output
    assert "mux: off" in result.output
    assert "только к CDN-профилям" in result.output

    result = runner.invoke(cli.app, ["mux", "on"])
    assert result.exit_code == 0, result.output
    assert settings.get_mux() is True

    result = runner.invoke(cli.app, ["mux", "status"])
    assert "mux: on" in result.output

    result = runner.invoke(cli.app, ["mux", "off"])
    assert result.exit_code == 0, result.output
    assert settings.get_mux() is False


def test_cli_mux_rejects_unknown_action(env):
    result = CliRunner().invoke(cli.app, ["mux", "maybe"])
    assert result.exit_code == 2


# --- CLI: vpn up --mux ---------------------------------------------------------

def make_fake_bin(env):
    (env / "bins").mkdir(parents=True, exist_ok=True)
    (env / "bins" / "xray").write_text("#!/bin/sh\nexit 0\n")


def not_active_run(calls):
    def run(cmd):
        calls.append(list(cmd))
        if "is-active" in cmd:
            return 1, "", ""
        return 0, "", ""
    return run


def test_cli_up_no_mux_generates_config_without_mux(env, monkeypatch):
    make_fake_bin(env)
    profiles.add(parse_link(FIXTURE_CDN), raw_link=FIXTURE_CDN, name="cdn-home")
    monkeypatch.setattr(runners, "run_cmd", not_active_run([]))
    monkeypatch.setattr(cli, "_sync_sysproxy_after", lambda *a, **k: None)
    monkeypatch.setattr(runners, "get_sysproxy", lambda *a, **k: False)
    settings.set_mux(True)

    result = CliRunner().invoke(cli.app, ["up", "cdn-home", "--no-mux"])
    assert result.exit_code == 0, result.output
    cfg = json.loads((env / "generated" / "cdn-home.json").read_text())
    assert "mux" not in cfg["outbounds"][0]
    assert settings.get_mux() is True  # разовый флаг настройку не меняет


def test_cli_up_mux_generates_config_with_mux(env, monkeypatch):
    make_fake_bin(env)
    profiles.add(parse_link(FIXTURE_CDN), raw_link=FIXTURE_CDN, name="cdn-home")
    monkeypatch.setattr(runners, "run_cmd", not_active_run([]))
    monkeypatch.setattr(cli, "_sync_sysproxy_after", lambda *a, **k: None)
    monkeypatch.setattr(runners, "get_sysproxy", lambda *a, **k: False)

    result = CliRunner().invoke(cli.app, ["up", "cdn-home", "--mux"])
    assert result.exit_code == 0, result.output
    cfg = json.loads((env / "generated" / "cdn-home.json").read_text())
    assert cfg["outbounds"][0]["mux"] == {"enabled": True, "concurrency": 8}


def test_cli_up_mux_flag_ignored_for_reality(env, monkeypatch):
    """--mux на Reality-профиле не порождает блок mux (Vision несовместим)."""
    make_fake_bin(env)
    profiles.add(parse_link(FIXTURE_REALITY), raw_link=FIXTURE_REALITY,
                 name="phone")
    monkeypatch.setattr(runners, "run_cmd", not_active_run([]))
    monkeypatch.setattr(cli, "_sync_sysproxy_after", lambda *a, **k: None)
    monkeypatch.setattr(runners, "get_sysproxy", lambda *a, **k: False)

    result = CliRunner().invoke(cli.app, ["up", "phone", "--mux"])
    assert result.exit_code == 0, result.output
    cfg = json.loads((env / "generated" / "phone.json").read_text())
    assert "mux" not in cfg["outbounds"][0]
