"""QR и текстовый конфиг профиля (как wg: showconf + qrencode)."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import profiles, qr  # noqa: E402
from vpnm.links import parse_link  # noqa: E402

REALITY_RAW = (
    "vless://11111111-2222-3333-4444-777777777777@203.0.113.10:443"
    "?security=reality&sni=example-test.invalid&pbk=FIXTURE_PBK&sid=01"
    "&flow=xtls-rprx-vision&fp=chrome#phone")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "profiles.json"
    monkeypatch.setattr(profiles, "STORE_PATH", path)
    profiles.add(parse_link(REALITY_RAW), REALITY_RAW, name="phone", path=path)
    return path


def _runner():
    from typer.testing import CliRunner
    return CliRunner()


# --- qr.render ---------------------------------------------------------------

def test_render_feeds_secret_via_stdin_not_argv(monkeypatch):
    monkeypatch.setattr(qr.shutil, "which", lambda name: "/usr/bin/qrencode")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(cmd, 0, stdout="QR-ART", stderr="")

    monkeypatch.setattr(qr.subprocess, "run", fake_run)
    assert qr.render("vless://secret@host") == "QR-ART"
    assert seen["input"] == "vless://secret@host"
    assert "vless://secret@host" not in " ".join(seen["cmd"])
    assert "/dev/stdin" in seen["cmd"]


def test_render_without_qrencode(monkeypatch):
    monkeypatch.setattr(qr.shutil, "which", lambda name: None)
    with pytest.raises(qr.QrError, match="qrencode"):
        qr.render("x")


def test_render_reports_qrencode_failure(monkeypatch):
    monkeypatch.setattr(qr.shutil, "which", lambda name: "/usr/bin/qrencode")
    monkeypatch.setattr(
        qr.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="Input data is too large"))
    with pytest.raises(qr.QrError, match="too large"):
        qr.render("x")


# --- CLI ---------------------------------------------------------------------

def test_cli_config_share_link(store):
    from vpnm.cli import app as cli_app
    result = _runner().invoke(cli_app, ["profile", "config", "phone"])
    assert result.exit_code == 0, result.output
    assert REALITY_RAW in result.output


def test_cli_config_native_json(store):
    from vpnm.cli import app as cli_app
    result = _runner().invoke(
        cli_app, ["profile", "config", "phone", "--native"])
    assert result.exit_code == 0, result.output
    assert '"inbounds"' in result.output


def test_cli_qr_uses_render(store, monkeypatch):
    from vpnm.cli import app as cli_app
    captured = {}

    def fake_render(text):
        captured["text"] = text
        return "QR-ART"

    monkeypatch.setattr(qr, "render", fake_render)
    result = _runner().invoke(cli_app, ["profile", "qr", "phone"])
    assert result.exit_code == 0, result.output
    assert "QR-ART" in result.output
    assert captured["text"] == REALITY_RAW
    assert REALITY_RAW not in result.output  # в QR, а не текстом


def test_cli_qr_missing_qrencode(store, monkeypatch):
    from vpnm.cli import app as cli_app
    monkeypatch.setattr(qr.shutil, "which", lambda name: None)
    result = _runner().invoke(cli_app, ["profile", "qr", "phone"])
    assert result.exit_code == 2
    assert "qrencode" in result.output


def test_cli_config_unknown_profile(store):
    from vpnm.cli import app as cli_app
    result = _runner().invoke(cli_app, ["profile", "config", "ghost"])
    assert result.exit_code == 2
