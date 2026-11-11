"""Tests for vpn doh on|off|status (mocks: no root, no network, tmp drop-ins).

The module's RESOLVED_DIR/DROPIN paths are monkeypatched into tmp_path so the
"sudo mv" stub can actually move files; systemctl actions are verified by
recorded commands.
"""

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import doh  # noqa: E402
from vpnm.cli import app  # noqa: E402

runner = CliRunner()


@pytest.fixture
def doh_paths(tmp_path, monkeypatch):
    """Redirect drop-in paths into tmp, provide a disabled drop-in, and make
    run_cmd a root-free stub (real mv, no sudo) so CLI tests never call sudo."""
    conf_dir = tmp_path / "resolved.conf.d"
    conf_dir.mkdir()
    disabled = conf_dir / "doh-local.conf.disabled-manualvpn"
    active = conf_dir / "doh-local.conf"
    monkeypatch.setattr(doh, "RESOLVED_DIR", conf_dir)
    monkeypatch.setattr(doh, "DROPIN", active)
    monkeypatch.setattr(doh, "DROPIN_DISABLED", disabled)
    disabled.write_text("[Resolve]\nDNS=127.0.0.1\nDomains=~.\n")

    def stub_run_cmd(cmd):
        if cmd[:3] == ["sudo", "-n", "mv"] and len(cmd) == 5:
            src, dst = Path(cmd[3]), Path(cmd[4])
            dst.write_bytes(src.read_bytes())
            src.unlink()
        return 0, "", ""

    monkeypatch.setattr(doh, "run_cmd", stub_run_cmd)
    return {"dir": conf_dir, "active": active, "disabled": disabled}


def make_run(record, fail_on=()):
    """run stub: records commands, performs real mv, fails on matched patterns.

    The stub actually executes 'sudo -n mv SRC DST' so the drop-in state
    transitions are real; other commands just return success.
    """
    def run(cmd):
        joined = " ".join(cmd)
        record.append(cmd)
        for pattern in fail_on:
            if pattern in joined:
                return 1, "", f"mock failure: {pattern}"
        if cmd[:3] == ["sudo", "-n", "mv"] and len(cmd) == 5:
            src, dst = Path(cmd[3]), Path(cmd[4])
            dst.write_bytes(src.read_bytes())
            src.unlink()
        return 0, "", ""
    return run


def make_is_active(active_services):
    """run stub that answers systemctl is-active from a dict, passes the rest."""
    def run(cmd):
        if cmd[:2] == ["systemctl", "is-active"]:
            return 0, active_services.get(cmd[2], "inactive"), ""
        return 0, "", ""
    return run


# --- dropin_state ----------------------------------------------------------------

def test_dropin_state_missing(doh_paths):
    doh_paths["disabled"].unlink()
    assert doh.dropin_state() == "missing"


def test_dropin_state_off(doh_paths):
    assert doh.dropin_state() == "off"


def test_dropin_state_on(doh_paths):
    doh_paths["disabled"].rename(doh_paths["active"])
    assert doh.dropin_state() == "on"


# --- enable ----------------------------------------------------------------------

def test_enable_moves_dropin_and_starts_services(doh_paths):
    record: list = []
    ok, msg = doh.enable(run=make_run(record))
    assert ok
    assert doh_paths["active"].exists() and not doh_paths["disabled"].exists()
    joined = " ".join(" ".join(c) for c in record)
    assert "systemctl start dnsmasq cloudflared-proxy-dns" in joined
    assert "systemctl restart systemd-resolved" in joined
    assert "DoH-режим включён" in msg


def test_enable_is_idempotent_when_already_on(doh_paths):
    doh_paths["disabled"].rename(doh_paths["active"])
    record: list = []
    ok, msg = doh.enable(run=make_run(record))
    assert ok
    # mv должен отсутствовать — drop-in уже на месте
    assert not any("mv" in " ".join(c) for c in record)


def test_enable_reports_failure_when_services_fail(doh_paths):
    ok, msg = doh.enable(run=make_run([], fail_on=["systemctl start"]))
    assert not ok
    assert "вручную" in msg
    # mv не должен был выполниться
    assert doh_paths["disabled"].exists()


def test_enable_missing_dropin_fails_cleanly(doh_paths):
    doh_paths["disabled"].unlink()
    ok, msg = doh.enable(run=make_run([]))
    assert not ok
    assert "drop-in не найден" in msg


# --- disable ---------------------------------------------------------------------

def test_disable_reverts_dropin_and_stops_services(doh_paths):
    doh_paths["disabled"].rename(doh_paths["active"])
    record: list = []
    active = dict.fromkeys(doh.SERVICES, "active")
    ok, msg = doh.disable(run=make_run(record))
    # services_state использует run stub без is-active ответов — допустимо:
    # stop вызовется только для активных, здесь все inactive по умолчанию
    assert ok
    assert doh_paths["disabled"].exists() and not doh_paths["active"].exists()
    joined = " ".join(" ".join(c) for c in record)
    assert "systemctl restart systemd-resolved" in joined


def test_disable_stops_running_services(doh_paths):
    doh_paths["disabled"].rename(doh_paths["active"])
    active = dict.fromkeys(doh.SERVICES, "active")
    service_run = make_is_active(active)
    record: list = []

    def run(cmd):
        record.append(cmd)
        if "stop" in cmd:
            return 0, "", ""
        return service_run(cmd)

    ok, msg = doh.disable(run=run)
    assert ok
    joined = " ".join(" ".join(c) for c in record)
    assert "systemctl stop dnsmasq cloudflared-proxy-dns" in joined


def test_disable_is_idempotent_without_dropin(doh_paths):
    doh_paths["disabled"].unlink()  # state = missing
    ok, msg = doh.disable(run=make_run([]))
    assert ok
    assert "уже выключен" in msg


# --- status ----------------------------------------------------------------------

def test_status_lines_reports_states(doh_paths):
    lines = doh.status_lines(run=make_is_active(
        dict.fromkeys(doh.SERVICES, "active")))
    text = "\n".join(lines)
    assert "drop-in: отключён" in text
    for svc in doh.SERVICES:
        assert f"{svc}: active" in text


def test_status_lines_on_state(doh_paths):
    doh_paths["disabled"].rename(doh_paths["active"])
    lines = doh.status_lines(run=make_is_active({}))
    assert any("drop-in: активен" in line for line in lines)


# --- CLI -------------------------------------------------------------------------

def test_cli_doh_on(doh_paths):
    result = runner.invoke(app, ["doh", "on"])
    assert result.exit_code == 0, result.output
    assert "DoH-режим включён" in result.output


def test_cli_doh_off(doh_paths):
    doh_paths["disabled"].rename(doh_paths["active"])
    result = runner.invoke(app, ["doh", "off"])
    assert result.exit_code == 0, result.output
    assert "выключен" in result.output


def test_cli_doh_status(doh_paths):
    result = runner.invoke(app, ["doh", "status"])
    assert result.exit_code == 0, result.output
    assert "drop-in" in result.output


def test_cli_doh_bad_action(doh_paths):
    result = runner.invoke(app, ["doh", "restart"])
    assert result.exit_code == 2


def test_cli_help_lists_doh():
    result = runner.invoke(app, ["--help"])
    assert "doh" in result.output


# --- boot-guard -------------------------------------------------------------------

@pytest.fixture
def guard_paths(tmp_path, monkeypatch):
    unit = tmp_path / "vpn-doh-boot-guard.service"
    monkeypatch.setattr(doh, "GUARD_UNIT", unit)
    return unit


def test_guard_state_missing(guard_paths):
    assert doh.guard_state(make_run([])) == "missing"


def test_guard_state_off_when_not_enabled(guard_paths):
    guard_paths.write_text("[Unit]\n")
    assert doh.guard_state(make_run([])) == "off"


def test_guard_state_on(guard_paths):
    guard_paths.write_text("[Unit]\n")
    def run(cmd):
        if cmd[:2] == ["systemctl", "is-enabled"]:
            return 0, "enabled\n", ""
        return 0, "", ""
    assert doh.guard_state(run) == "on"


def test_guard_install_writes_and_enables(guard_paths):
    record = []
    run = make_run(record)
    ok, msg = doh.guard_install(run)
    assert ok and "включён" in msg
    joined = [" ".join(c) for c in record]
    assert any(str(guard_paths) in j and "cp" in j for j in joined)
    assert any(f"systemctl enable {guard_paths.name}" in j for j in joined)
    # tmp cleaned up
    assert not Path("/tmp/vpn-doh-boot-guard.service.tmp").exists()


def test_guard_install_sudo_fail_gives_manual_hint(guard_paths):
    run = make_run([], fail_on=["cp"])
    ok, msg = doh.guard_install(run)
    assert not ok and "вручную" in msg and "sudo tee" in msg


def test_guard_remove_disabled_unit(guard_paths):
    guard_paths.write_text("[Unit]\n")
    record = []
    ok, msg = doh.guard_remove(make_run(record))
    assert ok and "удалён" in msg
    joined = [" ".join(c) for c in record]
    assert any(f"disable {guard_paths.name}" in j for j in joined)
    assert any(str(guard_paths) in j and " rm" in j for j in joined)


def test_guard_remove_missing_is_noop(guard_paths):
    ok, msg = doh.guard_remove(make_run([]))
    assert ok and "нечего" in msg


def test_guard_unit_content_paths_follow_constants(guard_paths, monkeypatch):
    monkeypatch.setattr(doh, "DROPIN", Path("/tmp/x/doh-local.conf"))
    monkeypatch.setattr(doh, "DROPIN_DISABLED", Path("/tmp/x/disabled"))
    content = doh._guard_unit_content()
    assert "/tmp/x/doh-local.conf" in content
    assert "ConditionPathExists" in content
