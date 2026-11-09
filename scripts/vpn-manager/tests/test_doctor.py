"""Tests for doctor logic on mocks: bins, ports (ours/foreign), v2rayA, ssh, DNS."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import doctor, profiles, runners  # noqa: E402
from vpnm.links import parse_link  # noqa: E402

FIXTURE_VLESS = (
    "vless://11111111-2222-3333-4444-555555555555@203.0.113.10:443"
    "?security=reality&sni=example-test.invalid&pbk=FIXTURE_PBK&sid=ab"
    "#fixture-reality"
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "STORE_PATH", tmp_path / "profiles.json")
    monkeypatch.setattr(runners, "STATE_PATH", tmp_path / "state.json")
    return tmp_path


def add_profile(name: str, port: int) -> None:
    link = parse_link(FIXTURE_VLESS)
    profiles.add(link, raw_link=FIXTURE_VLESS, name=name, socks_port=port)


def make_run(bin_exists=(), port_holder=None, ssh_ok=True, dns_204=True):
    """run stub emulating ss/ps/systemctl/curl/ssh/`binary version`."""
    calls = []

    def run(cmd):
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if joined.startswith("ss "):
            if port_holder:
                return 0, f"LISTEN pid={port_holder[0]} ", ""
            return 0, "", ""
        if joined.startswith("ps "):
            if port_holder:
                return 0, port_holder[1], ""
            return 0, "", ""
        if "systemctl --user status" in joined:
            if port_holder and port_holder[2]:
                return 0, (f"Main PID: {port_holder[0]} (vpnm-x)"), ""
            return 1, "inactive", ""
        if joined.startswith("ssh "):
            return (0, "", "") if ssh_ok else (255, "", "Connection refused")
        if joined.startswith("curl "):
            return ((0, "204", "") if dns_204 else (7, "000", "connect refused"))
        if joined.endswith("version"):
            if Path(cmd[0]).name in bin_exists:
                return 0, "Xray-core v25.1.1 (mock) / hysteria-app mock v2.13.0", ""
            return 126, "", "permission denied"
        return 0, "", ""

    run.calls = calls  # type: ignore[attr-defined]
    return run


def by_subject(checks):
    return {c[1]: c for c in checks}


# --- bins -----------------------------------------------------------------------

def test_bins_ok(env, tmp_path):
    bins_dir = tmp_path / "bins"
    bins_dir.mkdir()
    (bins_dir / "xray").write_text("#!/bin/sh\nexit 0\n")
    (bins_dir / "hysteria").write_text("#!/bin/sh\nexit 0\n")
    checks = doctor.check_bins(bin_dir=bins_dir, run=make_run(
        bin_exists=("xray", "hysteria")))
    statuses = [c[0] for c in checks]
    assert statuses == ["ok", "ok"]


def test_bins_missing_is_warn(env, tmp_path):
    checks = doctor.check_bins(bin_dir=tmp_path / "nope")
    assert all(c[0] == "warn" for c in checks)
    assert any("install-bins" in c[2] for c in checks)


# --- ports ------------------------------------------------------------------------

def test_port_free_is_ok(env):
    add_profile("p1", 22001)
    checks = doctor.check_ports(run=make_run())
    assert checks == [("ok", "ports", "p1: port 22001 is free")]


def test_port_held_by_foreign_is_warn_for_inactive(env):
    add_profile("p1", 22002)
    run = make_run(port_holder=(999, "someproxy", False))
    checks = doctor.check_ports(run=run)
    assert checks[0][0] == "warn"
    assert "someproxy" in checks[0][2]


def test_port_held_by_foreign_on_active_is_fail(env):
    add_profile("p1", 22003)
    runners.set_active("p1")
    run = make_run(port_holder=(999, "someproxy", False))
    checks = doctor.check_ports(run=run)
    assert checks[0][0] == "fail"


def test_port_held_by_our_unit_is_ok(env):
    add_profile("p1", 22004)
    runners.set_active("p1")
    run = make_run(port_holder=(4242, "xray", True))
    checks = doctor.check_ports(run=run)
    assert checks[0][0] == "ok"
    assert "our unit" in checks[0][2]


# --- v2rayA -------------------------------------------------------------------------

def test_v2raya_free_is_ok():
    check = doctor.check_v2raya(run=make_run())
    assert check[0] == "ok"


def test_v2raya_foreign_is_warn():
    run = make_run(port_holder=(777, "v2raya", False))
    check = doctor.check_v2raya(run=run)
    assert check[0] == "warn"
    assert "v2rayA" in check[2] or "v2raya" in check[2].lower()


# --- ssh / dns -----------------------------------------------------------------------

def test_ssh_ok_and_bad(env, tmp_path):
    assert doctor.check_ssh("root@asv1", run=make_run())[0] == "ok"
    check = doctor.check_ssh("root@asv1", run=make_run(ssh_ok=False))
    assert check[0] == "warn"
    assert "root@asv1" in check[2]
    assert doctor.check_ssh(None, run=make_run()) is None


def test_dns_ok_and_fail(env):
    assert doctor.check_dns(2080, run=make_run())[0] == "ok"
    check = doctor.check_dns(2080, run=make_run(dns_204=False))
    assert check[0] == "fail"
    assert "2080" in check[2]
    assert doctor.check_dns(None, run=make_run()) is None


# --- top level ------------------------------------------------------------------------

def test_run_doctor_no_active_verdict_is_ok_or_warn(env):
    add_profile("p1", 22005)
    checks, verdict = doctor.run_doctor(host=None, run=make_run(
        bin_exists=("xray", "hysteria")))
    assert all(c[0] in ("ok", "warn") for c in checks)
    assert verdict in ("ok", "warn")
    subjects = {c[1] for c in checks}
    assert {"bins", "ports", "v2rayA", "active"} <= subjects


def test_run_doctor_fail_sets_exit_verdict(env):
    add_profile("p1", 22006)
    runners.set_active("p1")
    run = make_run(bin_exists=("xray", "hysteria"),
                   port_holder=(999, "someproxy", False))
    checks, verdict = doctor.run_doctor(host=None, run=run)
    assert verdict == "fail"
