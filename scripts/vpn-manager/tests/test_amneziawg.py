"""Tests for vpn amneziawg: local tunnel + server clients over ssh.

All subprocess interaction goes through a fake ``run`` callable
(same pattern as test_pull.py / test_zapret.py); the local conf dir and
settings paths are redirected to tmp_path.
"""

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import amneziawg, cli, settings  # noqa: E402

HOST = "root@203.0.113.10"  # RFC 5737 test-net, fixture only
SERVER_STATUS_OUT = "\n".join([
    "Юнит vds-amneziawg.service: active",
    "Клиенты (1): laptop",
    "Конфиги в /opt/my-vds/gateway/clients/amneziawg: laptop.conf",
])


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(amneziawg, "LOCAL_CONF_DIR", tmp_path / "awg")
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    return tmp_path


class FakeRun:
    """Dispatch fake: records calls, answers per command prefix."""

    def __init__(self, handlers, default_rc=0):
        self.handlers = handlers
        self.calls = []
        self.default_rc = default_rc

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        joined = " ".join(cmd)
        for needle, answer in self.handlers.items():
            if needle in joined:
                rc, out, err = answer
                return rc, out, err
        return self.default_rc, "", ""


def unit_active(inst="laptop"):
    return (0, "active\n", "")


def list_units_out(inst="laptop"):
    return (0, f"vpn-awg-split@{inst}.service     loaded active exited "
               f"AWG split ({inst})\n", "")


AWG_SHOW_OUT = "\n".join([
    "interface: laptop",
    "  public key: FIXTURE_KEY",
    "  listening port: 52436",
    "peer: FIXTURE_PEER_KEY",
    "  latest handshake: 1 minute, 2 seconds ago",
    "  transfer: 1.2 MiB received, 3.4 MiB sent",
])


def local_handlers(**extra):
    handlers = {
        "systemctl list-units": list_units_out(),
        "systemctl is-active vpn-awg-split@laptop.service": unit_active(),
        "awg show": (0, AWG_SHOW_OUT, ""),
    }
    handlers.update(extra)
    return handlers


# --- local status ---------------------------------------------------------------

def test_local_status_lines_full(env, monkeypatch):
    fake = FakeRun(local_handlers())
    monkeypatch.setattr(amneziawg, "run_cmd", fake)
    lines = amneziawg.local_status_lines()
    joined = "\n".join(lines)
    assert "локальный инстанс: laptop (active)" in joined
    assert "интерфейс: up" in joined
    assert "peers: 1, handshakes: 1" in joined


def test_local_status_lines_no_sudo_warns(env, monkeypatch):
    handlers = local_handlers()
    handlers["awg show"] = (1, "", "sudo: a password is required")
    fake = FakeRun(handlers)
    monkeypatch.setattr(amneziawg, "run_cmd", fake)
    lines = amneziawg.local_status_lines()
    assert any(line.startswith("warning: awg show недоступен")
               for line in lines)
    assert "интерфейс" not in "\n".join(lines)


def test_local_status_lines_no_instance(env, monkeypatch):
    fake = FakeRun({"systemctl list-units": (0, "", "")})
    monkeypatch.setattr(amneziawg, "run_cmd", fake)
    lines = amneziawg.local_status_lines()
    assert any("не запущен" in line for line in lines)


# --- host resolution ------------------------------------------------------------

def test_resolve_host_saved(env):
    settings.set_host(HOST, env / "settings.json")
    assert amneziawg.resolve_host(path=env / "settings.json") == HOST


def test_resolve_host_prompt_and_persist(env):
    answers = []

    def prompt(question):
        answers.append(question)
        return "root@" + "203.0.113.99"

    got = amneziawg.resolve_host(prompt=prompt, path=env / "settings.json")
    assert got == "root@203.0.113.99"
    assert settings.get_host(env / "settings.json") == got
    assert answers and "ssh host" in answers[0]


def test_resolve_host_empty_prompt_raises(env):
    with pytest.raises(amneziawg.AwgError):
        amneziawg.resolve_host(prompt=lambda q: "  ",
                               path=env / "settings.json")


def test_resolve_host_no_prompt_raises(env):
    with pytest.raises(amneziawg.AwgError):
        amneziawg.resolve_host(path=env / "settings.json")


# --- server side ----------------------------------------------------------------

def test_server_status_lines_ok(env, monkeypatch):
    fake = FakeRun({"vds amneziawg status": (0, SERVER_STATUS_OUT, "")})
    monkeypatch.setattr(amneziawg, "run_cmd", fake)
    lines = amneziawg.server_status_lines(HOST)
    assert lines == SERVER_STATUS_OUT.splitlines()
    assert fake.calls == [["ssh", HOST, "vds amneziawg status"]]


def test_server_status_lines_ssh_fail_returns_none(env, monkeypatch):
    fake = FakeRun({"vds amneziawg status": (255, "", "Connection refused")})
    monkeypatch.setattr(amneziawg, "run_cmd", fake)
    assert amneziawg.server_status_lines(HOST) is None


def test_server_add_client_happy_path(env, monkeypatch):
    conf_dir = env / "awg"

    def scp_run(host, remote_path, local_path, scp=None):
        assert host == HOST
        assert remote_path == f"{amneziawg.SERVER_CLIENTS_DIR}/phone.conf"
        # emulate scp actually creating the local file
        Path(local_path).write_text("[Interface]\n# FIXTURE conf\n",
                                    encoding="utf-8")
        return 0, "", ""

    fake = FakeRun({"vds amneziawg add-client": (0, "client added", "")})
    monkeypatch.setattr(amneziawg, "run_cmd", fake)
    monkeypatch.setattr(amneziawg, "scp_fetch", scp_run)
    target = amneziawg.server_add_client(HOST, "phone", conf_dir=conf_dir)
    assert target == conf_dir / "phone.conf"
    assert target.exists()
    assert (target.stat().st_mode & 0o777) == 0o600
    assert fake.calls == [["ssh", HOST, "vds amneziawg add-client phone"]]


def test_server_add_client_scp_fail_raises(env, monkeypatch):
    fake = FakeRun({"vds amneziawg add-client": (0, "", "")})
    monkeypatch.setattr(amneziawg, "run_cmd", fake)

    def scp_run(host, remote_path, local_path, scp=None):
        return 1, "", "scp: permission denied"

    monkeypatch.setattr(amneziawg, "scp_fetch", scp_run)
    with pytest.raises(amneziawg.AwgError, match="scp"):
        amneziawg.server_add_client(HOST, "phone", conf_dir=env / "awg")


def test_server_add_client_ssh_fail_raises(env, monkeypatch):
    fake = FakeRun({"vds amneziawg add-client": (1, "", "client exists")})
    monkeypatch.setattr(amneziawg, "run_cmd", fake)
    with pytest.raises(amneziawg.AwgError, match="client exists"):
        amneziawg.server_add_client(HOST, "phone", conf_dir=env / "awg")


def test_server_remove_client_apply_flag(env, monkeypatch):
    fake = FakeRun({"vds amneziawg remove-client": (0, "removed", "")})
    monkeypatch.setattr(amneziawg, "run_cmd", fake)
    amneziawg.server_remove_client(HOST, "phone", apply=True)
    assert fake.calls[0][2] == "vds amneziawg remove-client phone --apply"
    amneziawg.server_remove_client(HOST, "phone", apply=False)
    assert fake.calls[1][2] == "vds amneziawg remove-client phone"


# --- tunnel control -------------------------------------------------------------

def test_tunnel_on_off_sudo(env, monkeypatch):
    fake = FakeRun({"systemctl start": (0, "", "")})
    monkeypatch.setattr(amneziawg, "run_cmd", fake)
    ok, message = amneziawg.tunnel_control("on")
    assert ok and "запущен" in message
    assert fake.calls[0] == ["sudo", "-n", "systemctl", "start",
                             "vpn-awg-split@laptop"]
    fake2 = FakeRun({"systemctl stop": (0, "", "")})
    monkeypatch.setattr(amneziawg, "run_cmd", fake2)
    ok, message = amneziawg.tunnel_control("off")
    assert ok and "остановлен" in message
    assert fake2.calls[0][3] == "stop"


def test_tunnel_sudo_fail_gives_hint(env, monkeypatch):
    fake = FakeRun({"systemctl start": (1, "", "sudo: a password is required")})
    monkeypatch.setattr(amneziawg, "run_cmd", fake)
    ok, message = amneziawg.tunnel_control("on")
    assert not ok
    assert "sudo systemctl start vpn-awg-split@laptop" in message


def test_tunnel_invalid_action(env, monkeypatch):
    ok, message = amneziawg.tunnel_control("restart")
    assert not ok


# --- CLI wiring -----------------------------------------------------------------

def _cli_env(env, monkeypatch, handlers):
    fake = FakeRun(handlers)
    monkeypatch.setattr(amneziawg, "run_cmd", fake)
    settings.set_host(HOST, env / "settings.json")
    return fake


def test_cli_status_two_sections(env, monkeypatch):
    fake = _cli_env(env, monkeypatch, local_handlers(
        **{"vds amneziawg status": (0, SERVER_STATUS_OUT, "")}))
    result = CliRunner().invoke(cli.app, ["amneziawg", "status"])
    assert result.exit_code == 0, result.output
    assert "── ЛОКАЛЬНО ──" in result.output
    assert "── СЕРВЕР ──" in result.output
    assert "локальный инстанс: laptop" in result.output
    assert "Клиенты (1): laptop" in result.output


def test_cli_status_server_down_warns(env, monkeypatch):
    _cli_env(env, monkeypatch, local_handlers(
        **{"vds amneziawg status": (255, "", "Connection refused")}))
    result = CliRunner().invoke(cli.app, ["amneziawg", "status"])
    assert result.exit_code == 0, result.output
    assert "── СЕРВЕР ──" in result.output
    assert "warning: сервер недоступен" in result.output
    assert "Клиенты" not in result.output


def test_cli_add_client_saves_file(env, monkeypatch):
    fake = _cli_env(env, monkeypatch,
                    {"vds amneziawg add-client": (0, "added", "")})

    def scp_run(host, remote_path, local_path, scp=None):
        Path(local_path).write_text("[Interface]\n", encoding="utf-8")
        return 0, "", ""

    monkeypatch.setattr(amneziawg, "scp_fetch", scp_run)
    result = CliRunner().invoke(cli.app, ["amneziawg", "add-client", "phone"])
    assert result.exit_code == 0, result.output
    conf = env / "awg" / "phone.conf"
    assert conf.exists()
    assert (conf.stat().st_mode & 0o777) == 0o600
    assert str(conf) in result.output
    assert "AmneziaVPN" in result.output
    assert fake.calls == [["ssh", HOST, "vds amneziawg add-client phone"]]


def test_cli_add_client_ssh_fail_errors(env, monkeypatch):
    _cli_env(env, monkeypatch,
             {"vds amneziawg add-client": (1, "", "name taken")})
    result = CliRunner().invoke(cli.app, ["amneziawg", "add-client", "phone"])
    assert result.exit_code == 1
    assert "name taken" in result.output


def test_cli_remove_client_warns_on_active_local_instance(env, monkeypatch):
    _cli_env(env, monkeypatch, local_handlers(
        **{"vds amneziawg remove-client": (0, "removed (preview)", "")}))
    result = CliRunner().invoke(
        cli.app, ["amneziawg", "remove-client", "laptop", "--apply"],
        input="y\n")
    assert result.exit_code == 0, result.output
    assert "активный локальный инстанс" in result.output
    assert "sudo systemctl restart vpn-awg-split@laptop" in result.output


def test_cli_remove_client_no_warning_other_name(env, monkeypatch):
    _cli_env(env, monkeypatch, local_handlers(
        **{"vds amneziawg remove-client": (0, "removed", "")}))
    result = CliRunner().invoke(
        cli.app, ["amneziawg", "remove-client", "phone", "--apply"],
        input="y\n")
    assert result.exit_code == 0, result.output
    assert "активный локальный инстанс" not in result.output


def test_cli_tunnel_on_off(env, monkeypatch):
    fake = _cli_env(env, monkeypatch, {"systemctl start": (0, "", "")})
    result = CliRunner().invoke(cli.app, ["amneziawg", "tunnel", "on"])
    assert result.exit_code == 0, result.output
    assert "запущен" in result.output
    # off path
    fake.handlers = {"systemctl stop": (0, "", "")}
    result = CliRunner().invoke(cli.app, ["amneziawg", "tunnel", "off"])
    assert result.exit_code == 0, result.output
    assert "остановлен" in result.output


def test_cli_tunnel_sudo_fail_hint(env, monkeypatch):
    _cli_env(env, monkeypatch, {"systemctl stop": (1, "", "no tty")})
    result = CliRunner().invoke(cli.app, ["amneziawg", "tunnel", "off"])
    assert result.exit_code == 1
    assert "sudo systemctl stop vpn-awg-split@laptop" in result.output


def test_cli_tunnel_status(env, monkeypatch):
    _cli_env(env, monkeypatch, local_handlers())
    result = CliRunner().invoke(cli.app, ["amneziawg", "tunnel", "status"])
    assert result.exit_code == 0, result.output
    assert "локальный инстанс: laptop (active)" in result.output


def test_cli_help_lists_subcommands(env):
    result = CliRunner().invoke(cli.app, ["amneziawg", "--help"])
    assert result.exit_code == 0, result.output
    for cmd in ("status", "add-client", "remove-client", "tunnel"):
        assert cmd in result.output
