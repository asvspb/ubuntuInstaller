"""Tests for vpn repair (mocks: no root, no network, tmp paths).

DNS-стек эмулируется run-стабом: dig по шаблону ответа, systemctl is-active
из словаря, sudo-команды только записываются. Пути drop-in и лога
перенаправляются в tmp_path через monkeypatch.
"""

import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import doh as doh_mod  # noqa: E402
from vpnm import repair  # noqa: E402
from vpnm.cli import app  # noqa: E402

runner_cli = CliRunner()


@pytest.fixture
def paths(tmp_path, monkeypatch):
    conf_dir = tmp_path / "resolved.conf.d"
    conf_dir.mkdir()
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(repair, "RESOLVED_DIR", conf_dir)
    monkeypatch.setattr(repair, "REPAIR_DROPIN",
                        conf_dir / "vpn-dns-fix.conf")
    monkeypatch.setattr(repair, "LOG_DIR", log_dir)
    monkeypatch.setattr(doh_mod, "DROPIN",
                        conf_dir / "doh-local.conf")  # не существует = off
    monkeypatch.setattr(
        doh_mod, "DROPIN_DISABLED",
        conf_dir / "doh-local.conf.disabled-manualvpn")
    return {"dir": conf_dir, "dropin": conf_dir / "vpn-dns-fix.conf",
            "log_dir": log_dir,
            "doh_active": conf_dir / "doh-local.conf",
            "doh_disabled": conf_dir / "doh-local.conf.disabled-manualvpn"}


class FakeRun:
    """run-стаб: dig/@<ip> и systemctl is-active из словаря, остальное — запись."""

    def __init__(self, dig_answers=None, active_services=None):
        self.recorded: list = []
        self.dig_answers = dig_answers or {}
        self.active = active_services or {}

    def __call__(self, cmd):
        self.recorded.append(cmd)
        joined = " ".join(cmd)
        # модель: после рестарта resolved stub начинает резолвить
        if joined == "sudo -n systemctl restart systemd-resolved":
            self.dig_answers["127.0.0.53"] = "5.255.255.242"
        if cmd[:1] == ["dig"]:
            ip = cmd[1]
            if ip.startswith("@"):
                ip = ip[1:]
            answer = self.dig_answers.get(ip)
            return (0, answer + "\n", "") if answer else (1, "", "timeout")
        if cmd[:2] == ["systemctl", "is-active"]:
            return 0, self.active.get(cmd[2], "inactive"), ""
        if cmd[:1] == ["curl"]:
            return 0, "302 0.2s", ""
        if cmd[:1] == ["resolvectl"]:
            return 0, "Current DNS Server: 77.88.8.8\n", ""
        return 0, "", ""


def enable_doh_dropin(paths):
    """Активный doh-local.conf в tmp (как при мёртвой цепочке)."""
    active = paths["doh_active"]
    active.write_text("[Resolve]\nDNS=127.0.0.1\nDomains=~.\n")
    return active


@pytest.fixture(autouse=True)
def restore_doh_paths(monkeypatch):
    monkeypatch.setattr(doh_mod, "DROPIN",
                        Path("/etc/systemd/resolved.conf.d/doh-local.conf"))
    monkeypatch.setattr(
        doh_mod, "DROPIN_DISABLED",
        Path("/etc/systemd/resolved.conf.d/doh-local.conf.disabled-manualvpn"))
    yield


# --- resolver health ---------------------------------------------------------------

def test_system_resolve_ok_true():
    run = FakeRun(dig_answers={"127.0.0.53": "77.88.8.88"})
    assert repair.system_resolve_ok(run) is True


def test_system_resolve_ok_false():
    run = FakeRun()
    assert repair.system_resolve_ok(run) is False


def test_find_working_resolver_first_alive():
    run = FakeRun(dig_answers={"77.88.8.8": "5.255.255.242"})
    assert repair.find_working_resolver(run) == "77.88.8.8"


def test_find_working_resolver_skips_dead():
    run = FakeRun(dig_answers={"8.8.8.8": "142.250.74.14"})
    assert repair.find_working_resolver(run) == "8.8.8.8"


def test_find_working_resolver_none_alive():
    run = FakeRun()
    assert repair.find_working_resolver(run) is None


# --- fix_dns -----------------------------------------------------------------------

def test_fix_dns_noop_when_stub_alive():
    run = FakeRun(dig_answers={"127.0.0.53": "1.2.3.4"})
    action, lines = repair.fix_dns(run)
    assert action == "ok"
    assert not any(c[:1] == ["sudo"] for c in run.recorded)


def test_fix_dns_dead_doh_chain_disabled_first(paths):
    active = enable_doh_dropin(paths)
    run = FakeRun(active_services={"dnsmasq": "inactive",
                                   "cloudflared-proxy-dns": "inactive"},
                  dig_answers={"77.88.8.8": "5.255.255.242",
                               "127.0.0.53": ""})
    action, lines = repair.fix_dns(run)
    assert action == "fixed"
    joined = [" ".join(c) for c in run.recorded]
    assert any(str(active) in j and "mv" in j for j in joined)
    assert any("systemctl restart systemd-resolved" in j for j in joined)


def test_fix_dns_writes_dropin_via_sudo(paths):
    run = FakeRun(dig_answers={"8.8.8.8": "142.250.74.14"})
    action, lines = repair.fix_dns(run)
    assert action == "fixed"
    joined = [" ".join(c) for c in run.recorded]
    assert any(str(repair.REPAIR_DROPIN) in j and "cp" in j
               and "sudo" in j for j in joined)
    assert any("chmod 0644" in j for j in joined)
    assert any("systemctl restart systemd-resolved" in j for j in joined)


def test_fix_dns_dry_run_changes_nothing(paths):
    run = FakeRun(dig_answers={"8.8.8.8": "1.2.3.4"})
    action, _lines = repair.fix_dns(run, dry_run=True)
    assert action == "dry-run"
    assert not any(c[:1] == ["sudo"] for c in run.recorded)


def test_fix_dns_no_resolver_alive_is_failed(paths):
    run = FakeRun()  # dig не отвечает нигде
    action, lines = repair.fix_dns(run)
    assert action == "failed"
    assert any("реально заблокирован канал" in l for l in lines)
    assert not any(c[:1] == ["sudo"] for c in run.recorded)


# --- remove_dns_fix ----------------------------------------------------------------

def test_remove_dns_fix_missing_is_noop(paths):
    ok, msg = repair.remove_dns_fix(FakeRun())
    assert ok and "отсутствует" in msg


def test_remove_dns_fix_removes_dropin(paths):
    paths["dropin"].write_text("[Resolve]\nDNS=77.88.8.8\n")
    run = FakeRun()
    ok, msg = repair.remove_dns_fix(run)
    assert ok and "удалён" in msg
    joined = [" ".join(c) for c in run.recorded]
    assert any(str(paths["dropin"]) in j and "rm" in j for j in joined)


# --- snapshot / log ----------------------------------------------------------------

def test_snapshot_lines_contains_sections():
    run = FakeRun(active_services={"dnsmasq": "inactive"},
                  dig_answers={"77.88.8.8": "5.255.255.242"})
    lines = repair.snapshot_lines(run)
    text = "\n".join(lines)
    assert "-- DNS-стек --" in text
    assert "-- сервисы --" in text
    assert "-- порты --" in text
    assert "-- канал провайдера --" in text
    assert "dnsmasq: inactive" in text
    assert "port 2083: closed" in text


def test_write_log_creates_0600_file(paths):
    path = repair.write_log(["line one", "line two"],
                            log_dir=paths["log_dir"])
    assert path is not None and path.exists()
    assert oct(os.stat(path).st_mode)[-3:] == "600"
    assert "line one\nline two" in path.read_text()


# --- CLI ---------------------------------------------------------------------------

def test_cli_repair_dry_run_wires_everything(paths, monkeypatch):
    import vpnm.cli as cli_mod
    from vpnm import runners as runners_mod
    run = FakeRun(active_services={"dnsmasq": "inactive"},
                  dig_answers={"8.8.8.8": "1.2.3.4"})
    monkeypatch.setattr(runners_mod, "run_cmd", run)
    monkeypatch.setattr(repair, "run_cmd", run)
    monkeypatch.setattr(doh_mod, "run_cmd", run)
    from vpnm import doctor as doctor_mod
    monkeypatch.setattr(doctor_mod, "run_doctor",
                        lambda host=None, run=None: ([("ok", "x", "y")], "ok"))
    monkeypatch.setattr(repair, "write_log",
                        lambda lines, log_dir=None: paths["log_dir"] / "x.log")
    result = runner_cli.invoke(app, ["repair", "--dry-run", "--no-log"])
    assert result.exit_code == 0, result.output
    assert "план:" in result.output
    assert not any(c[:1] == ["sudo"] for c in run.recorded)


def test_cli_repair_bad_dns_arg(paths):
    result = runner_cli.invoke(app, ["repair", "--dns", "wat"])
    assert result.exit_code == 2


def test_cli_repair_dns_off(paths):
    paths["dropin"].write_text("[Resolve]\nDNS=77.88.8.8\n")
    result = runner_cli.invoke(app, ["repair", "--dns", "off"])
    assert result.exit_code == 0, result.output
    assert "удалён" in result.output
