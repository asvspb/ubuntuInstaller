"""Tests for channel 0 (zapret): channels / doctor / CLI / TUI on mocks.

No root, no network: nfqws paths are monkeypatched into tmp dirs and all
subprocess interaction goes through run stubs.
"""

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import channels, doctor, zapret  # noqa: E402
from vpnm.cli import app  # noqa: E402

runner = CliRunner()


@pytest.fixture
def nfqws_dir(tmp_path, monkeypatch) -> Path:
    """A tmp 'binary dir' containing a fake nfqws (absent when requested)."""
    bindir = tmp_path / "bindir"
    bindir.mkdir()
    monkeypatch.setattr(zapret, "BIN_DIRS", (bindir,))
    return bindir


def with_nfqws(nfqws_dir: Path) -> None:
    (nfqws_dir / "nfqws").write_text("#!/bin/sh\nexit 0\n")


def make_run(**behaviors):
    def run(cmd):
        joined = " ".join(cmd)
        for key, value in behaviors.items():
            if key in joined:
                if callable(value):
                    return value(cmd)
                return value
        return 0, "", ""

    return run


# --- channels.check_zapret --------------------------------------------------------

def test_check_zapret_not_installed(nfqws_dir):
    result = channels.check_zapret(run=make_run())
    assert result["status"] == channels.STATUS_NOT_INSTALLED
    assert "не установлен" in result["detail"]


def test_check_zapret_ok_direct_canary(nfqws_dir):
    with_nfqws(nfqws_dir)
    result = channels.check_zapret(run=make_run(
        **{"systemctl is-active zapret": (0, "active", ""),
           "generate_204": (0, "204", "")}))
    assert result["status"] == channels.STATUS_OK
    assert "direct DPI-bypass жив" in result["detail"]
    assert "без socks" in result["detail"]


def test_check_zapret_fail_when_canary_blocked(nfqws_dir):
    with_nfqws(nfqws_dir)
    result = channels.check_zapret(run=make_run(
        **{"systemctl is-active zapret": (0, "active", ""),
           "generate_204": (0, "000", "")}))
    assert result["status"] == channels.STATUS_FAIL
    assert "blockcheck" in result["detail"]


def test_check_zapret_installed_but_stopped(nfqws_dir):
    with_nfqws(nfqws_dir)
    result = channels.check_zapret(run=make_run(
        **{"systemctl is-active zapret": (3, "inactive", "")}))
    assert result["status"] == channels.STATUS_WARN
    assert "не запущен" in result["detail"]


def test_check_zapret_binary_but_no_unit(nfqws_dir):
    with_nfqws(nfqws_dir)
    result = channels.check_zapret(run=make_run(
        **{"systemctl is-active zapret": (4, "not-found", "")}))
    assert result["status"] == channels.STATUS_WARN
    assert "не определён" in result["detail"]


def test_monitor_matrix_has_six_channels(nfqws_dir, tmp_path, monkeypatch):
    from vpnm import profiles, runners
    monkeypatch.setattr(profiles, "STORE_PATH", tmp_path / "profiles.json")
    monkeypatch.setattr(runners, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(channels.probe, "measure_latency", lambda *a, **k: 0.1)
    monkeypatch.setattr(channels, "check_dnsdoh", lambda **kw: {
        "channel": "dns-doh", "status": channels.STATUS_NOT_INSTALLED,
        "detail": "mocked"})
    outcome = channels.run_monitor(run=make_run(
        **{"which zerotier-cli": (1, "", "")}))
    names = [c["channel"] for c in outcome["channels"]]
    assert names == ["vpnm", "cdn", "zerotier", "amneziawg", "xray-split",
                     "zapret", "dns-doh"]
    assert outcome["total"] == 7


# --- doctor.check_zapret ------------------------------------------------------------

def test_doctor_zapret_missing_is_warn(nfqws_dir):
    status, subject, detail = doctor.check_zapret(run=make_run())
    assert (status, subject) == ("warn", "zapret")
    assert "12_zapret.sh" in detail


def test_doctor_zapret_active_canary_ok(nfqws_dir):
    with_nfqws(nfqws_dir)
    status, subject, _ = doctor.check_zapret(run=make_run(
        **{"systemctl is-active zapret": (0, "active", ""),
           "generate_204": (0, "204", "")}))
    assert (status, subject) == ("ok", "zapret")


def test_doctor_zapret_active_canary_fail(nfqws_dir):
    with_nfqws(nfqws_dir)
    status, subject, detail = doctor.check_zapret(run=make_run(
        **{"systemctl is-active zapret": (0, "active", ""),
           "generate_204": (0, "000", "")}))
    assert (status, subject) == ("fail", "zapret")
    assert "blockcheck" in detail


def test_doctor_zapret_stopped_is_warn(nfqws_dir):
    with_nfqws(nfqws_dir)
    status, _subject, detail = doctor.check_zapret(run=make_run(
        **{"systemctl is-active zapret": (3, "inactive", "")}))
    assert status == "warn"
    assert "sudo systemctl start zapret" in detail


# --- zapret.control ------------------------------------------------------------------

def test_control_on_no_sudo_gives_hint(nfqws_dir):
    with_nfqws(nfqws_dir)
    ok, msg = zapret.control("on", run=make_run(
        **{"sudo -n systemctl": (1, "", "sudo: a password is required")}))
    assert ok is False
    assert "sudo systemctl start zapret" in msg


def test_control_on_success(nfqws_dir):
    with_nfqws(nfqws_dir)
    ok, msg = zapret.control("on", run=make_run(
        **{"sudo -n systemctl start zapret": (0, "", "")}))
    assert ok is True
    assert "запущен" in msg


def test_control_off_success(nfqws_dir):
    with_nfqws(nfqws_dir)
    ok, msg = zapret.control("off", run=make_run(
        **{"sudo -n systemctl stop zapret": (0, "", "")}))
    assert ok is True
    assert "остановлен" in msg


def test_control_not_installed(nfqws_dir):
    ok, msg = zapret.control("on", run=make_run())
    assert ok is False
    assert "12_zapret.sh" in msg


def test_control_bad_action(nfqws_dir):
    with_nfqws(nfqws_dir)
    ok, msg = zapret.control("reboot", run=make_run())
    assert ok is False


# --- status_lines / CLI / TUI ----------------------------------------------------------

def test_status_lines_not_installed(nfqws_dir):
    lines = zapret.status_lines(run=make_run())
    assert len(lines) == 1
    assert "не установлен" in lines[0]


def test_status_lines_active(nfqws_dir):
    with_nfqws(nfqws_dir)
    lines = zapret.status_lines(run=make_run(
        **{"systemctl is-active zapret": (0, "active", ""),
           "generate_204": (0, "204", "")}))
    assert any("service: active" in line for line in lines)
    assert any("DPI-bypass жив" in line for line in lines)


def test_cli_zapret_on(monkeypatch):
    monkeypatch.setattr(zapret, "control", lambda a: (True, "zapret запущен"))
    result = runner.invoke(app, ["zapret", "on"])
    assert result.exit_code == 0
    assert "zapret запущен" in result.output


def test_cli_zapret_off_no_sudo_exit_1(monkeypatch):
    monkeypatch.setattr(zapret, "control", lambda a: (False, "выполните вручную"))
    result = runner.invoke(app, ["zapret", "off"])
    assert result.exit_code == 1
    assert "выполните вручную" in result.output


def test_cli_zapret_status(monkeypatch):
    monkeypatch.setattr(zapret, "status_lines",
                        lambda: ["nfqws: /usr/local/bin/nfqws", "service: active"])
    result = runner.invoke(app, ["zapret", "status"])
    assert result.exit_code == 0
    assert "service: active" in result.output


def test_cli_zapret_bad_action():
    result = runner.invoke(app, ["zapret", "sideways"])
    assert result.exit_code == 2


def test_cli_help_lists_zapret():
    result = runner.invoke(app, ["--help"])
    assert "zapret" in result.output


def test_tui_menu_has_zapret_action():
    from vpnm import tui
    assert "Канал 0 (zapret)" in tui.MENU_ACTIONS
    assert callable(tui.MENU_ACTIONS["Канал 0 (zapret)"])


def test_tui_action_zapret_status(env_none=None):
    from vpnm import tui

    class FakeQ:
        class errors:
            class Abort(Exception):
                pass

        def select(self, *a, **k):
            return self

        def ask(self):
            return "status"

    saved = tui.questionary
    tui.questionary = FakeQ()
    try:
        tui.action_zapret()  # real status path: nfqws absent -> one warn line
    finally:
        tui.questionary = saved
