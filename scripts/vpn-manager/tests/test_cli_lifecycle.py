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


@pytest.fixture(autouse=True)
def _hermetic_system(monkeypatch):
    """Не дёргать живой systemctl/ip/nft из CLI-тестов.

    Инцидент 2026-11-18: ``vpn reset``/``vpn start`` в тестах реально вызывали
    ``xsplit.stop()`` (``sudo -n bash 17_xray-split.sh --off``) и
    ``amneziawg.instance_control`` (``sudo -n systemctl stop
    vpn-awg-split@relay.service``) — то есть снесли живой туннель. Здесь
    изолируем только read-only пробы и снос xsplit; мутирующие операции
    (``tunnel_control``/``domain_split_apply``) тесты мокают сами, чтобы
    проверять порядок вызовов.
    """
    from vpnm import amneziawg as awg
    from vpnm import xsplit as xsplit_mod
    monkeypatch.setattr(awg, "instance_active", lambda name, run=None: False)
    monkeypatch.setattr(awg, "active_instances", lambda run=None: [])
    monkeypatch.setattr(awg, "local_instance", lambda run=None: "laptop")
    monkeypatch.setattr(xsplit_mod, "active_profile", lambda run=None: None)
    monkeypatch.setattr(xsplit_mod, "stop",
                        lambda run=None: (True, "xray-split демонтирован"))
    yield


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


def test_cli_start_awg_order_tunnel_then_split(monkeypatch):
    """vpn start awg: туннель ДО split (nft нужен iface), идемпотентен."""
    from vpnm import amneziawg as awg
    from vpnm.cli import app as cli_app
    from typer.testing import CliRunner
    calls: list = []
    monkeypatch.setattr(awg, "tunnel_control",
                        lambda a, run=None: calls.append(("tun", a)) or (True, "ok"))
    monkeypatch.setattr(awg, "domain_split_apply",
                        lambda a, run=None: calls.append(("split", a)) or (True, "ok"))
    result = CliRunner().invoke(cli_app, ["start", "awg"])
    assert result.exit_code == 0, result.output
    assert calls == [("tun", "on"), ("split", "on")]
    assert "основная схема активна" in result.output
    result2 = CliRunner().invoke(cli_app, ["start", "awg"])
    assert result2.exit_code == 0, result2.output


def test_cli_start_tunnel_fail_no_split_attempt(monkeypatch):
    from vpnm import amneziawg as awg
    from vpnm.cli import app as cli_app
    from typer.testing import CliRunner
    calls: list = []
    monkeypatch.setattr(awg, "tunnel_control",
                        lambda a, run=None: calls.append(("tun", a)) or (False, "no tunnel"))
    monkeypatch.setattr(awg, "domain_split_apply",
                        lambda a, run=None: calls.append(("split", a)) or (True, "ok"))
    result = CliRunner().invoke(cli_app, ["start", "awg"])
    assert result.exit_code == 1
    assert calls == [("tun", "on")]  # split не пытаемся без iface


def test_cli_scheme_awg_calls_tunnel_then_split(monkeypatch):
    from vpnm import amneziawg as awg
    from vpnm.cli import app as cli_app
    from typer.testing import CliRunner
    calls: list = []
    monkeypatch.setattr(awg, "tunnel_control",
                        lambda a, run=None: calls.append(("tun", a)) or (True, "ok"))
    monkeypatch.setattr(awg, "domain_split_apply",
                        lambda a, run=None: calls.append(("split", a)) or (True, "ok"))
    result = CliRunner().invoke(cli_app, ["scheme", "awg"])
    assert result.exit_code == 0, result.output
    assert calls == [("tun", "on"), ("split", "on")]


def test_cli_scheme_private_no_sysproxy(monkeypatch):
    """private: профиль поднят, gsettings НЕ тронут, напечатан socks-адрес."""
    from vpnm import profiles as profiles_mod
    from vpnm import runners as runners_mod
    from vpnm import sysproxy as sysproxy_mod
    from vpnm.cli import app as cli_app
    from typer.testing import CliRunner
    monkeypatch.setattr(runners_mod, "up", lambda name: "/tmp/x.json")
    monkeypatch.setattr(profiles_mod, "get",
                        lambda name: {"socks_port": 2080, "protocol": "x"})
    touched = []
    monkeypatch.setattr(sysproxy_mod, "on", lambda port: touched.append(port))
    result = CliRunner().invoke(cli_app, ["scheme", "private", "phone"])
    assert result.exit_code == 0, result.output
    assert touched == []  # системный прокси не тронут
    assert "127.0.0.1:2080" in result.output
    assert "vpncli on 2080" in result.output


def test_cli_scheme_proxy_sets_sysproxy(monkeypatch):
    from vpnm import profiles as profiles_mod
    from vpnm import runners as runners_mod
    from vpnm import sysproxy as sysproxy_mod
    from vpnm.cli import app as cli_app
    from typer.testing import CliRunner
    monkeypatch.setattr(runners_mod, "up", lambda name: "/tmp/x.json")
    monkeypatch.setattr(profiles_mod, "get",
                        lambda name: {"socks_port": 2083, "protocol": "x"})
    touched = []
    monkeypatch.setattr(sysproxy_mod, "on", lambda port: touched.append(port))
    from vpnm import runners as r2
    monkeypatch.setattr(r2, "set_sysproxy", lambda v: touched.append(v))
    result = CliRunner().invoke(cli_app, ["scheme", "proxy", "mob"])
    assert result.exit_code == 0, result.output
    assert touched == [2083, True]


def test_cli_scheme_cdn_and_unknown(monkeypatch):
    from vpnm import runners as runners_mod
    from vpnm.cli import app as cli_app
    from typer.testing import CliRunner
    ups = []
    monkeypatch.setattr(runners_mod, "up", lambda name: ups.append(name) or "/c")
    monkeypatch.setattr(runners_mod, "get_active", lambda *a, **k: "cdn-home")
    monkeypatch.setattr(runners_mod, "set_sysproxy", lambda v: None)
    # isolate from the live state.json: the sysproxy flag must not leak in
    monkeypatch.setattr(runners_mod, "get_sysproxy", lambda *a, **k: False)
    # CDN-профиль резолвится из store (не хардкод): имя задаёт тест
    monkeypatch.setattr(profiles, "resolve_cdn", lambda *a, **k: "cdn-home")
    monkeypatch.setattr(profiles, "get",
                        lambda name, *a, **k: {"socks_port": 2081,
                                               "protocol": "xray-cdn-ws"})
    from vpnm import sysproxy as sysproxy_mod
    monkeypatch.setattr(sysproxy_mod, "on", lambda port: None)
    r1 = CliRunner().invoke(cli_app, ["scheme", "cdn"])
    assert r1.exit_code == 0, r1.output
    assert ups == ["cdn-home"]
    r2 = CliRunner().invoke(cli_app, ["scheme", "wat"])
    assert r2.exit_code == 2


def test_cli_scheme_cdn_without_profile_fails(monkeypatch):
    from vpnm.cli import app as cli_app
    from typer.testing import CliRunner
    called = []
    monkeypatch.setattr(profiles, "resolve_cdn", lambda *a, **k: None)
    from vpnm import runners as runners_mod
    monkeypatch.setattr(runners_mod, "up", lambda name: called.append(name))
    result = CliRunner().invoke(cli_app, ["scheme", "cdn"])
    assert result.exit_code == 2
    assert "CDN-профиля нет" in result.output
    assert called == []


def test_cli_start_default_is_awg(monkeypatch):
    from vpnm import cli as cli_mod
    from typer.testing import CliRunner
    calls: list = []
    monkeypatch.setattr(
        cli_mod, "_apply_awg_scheme",
        lambda: calls.append("awg") or (True, "AWG + доменный split"))
    result = CliRunner().invoke(cli_mod.app, ["start"])
    assert result.exit_code == 0, result.output
    assert calls == ["awg"]
    assert "AWG" in result.output


def test_cli_start_xhttp(monkeypatch):
    from vpnm import xsplit
    from vpnm.cli import app as cli_app
    from typer.testing import CliRunner
    calls: list = []

    def fake_apply(profile=None, run=None, default_protocol=None):
        calls.append((profile, default_protocol))
        return True, "xhttp ok"

    monkeypatch.setattr(xsplit, "apply", fake_apply)
    result = CliRunner().invoke(cli_app, ["start", "xhttp"])
    assert result.exit_code == 0, result.output
    assert calls == [(None, "xray-xhttp")]
    assert "XHTTP-split" in result.output


def test_cli_start_reality_failure_exits(monkeypatch):
    from vpnm import xsplit
    from vpnm.cli import app as cli_app
    from typer.testing import CliRunner
    monkeypatch.setattr(xsplit, "apply",
                        lambda **kw: (False, "нет профиля"))
    result = CliRunner().invoke(cli_app, ["start", "reality"])
    assert result.exit_code == 1


def test_cli_start_unknown_scheme():
    from vpnm.cli import app as cli_app
    from typer.testing import CliRunner
    result = CliRunner().invoke(cli_app, ["start", "wireguard"])
    assert result.exit_code == 2
    assert "expected 'awg'" in result.output


def test_status_awg_scheme_does_not_report_missing_profile(env, monkeypatch):
    """AWG-схема ≠ socks-профиль: status не должен говорить «no active profile».

    Живой случай 2026-11-17: при рабочем `vpn-awg-split@laptop` вывод был
    `DIRECT (awg laptop)` + `no active profile` — читалось как «схема не
    работает».
    """
    from vpnm import amneziawg as awg
    from vpnm import mode as mode_mod

    monkeypatch.setattr(awg, "active_instances", lambda: ["laptop"])
    monkeypatch.setattr(awg, "instance_active", lambda name: False)
    monkeypatch.setattr(mode_mod, "read_mode",
                        lambda: {"mode": "direct", "iface": "laptop"})
    monkeypatch.setattr(runners, "get_active", lambda: None)

    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exception is None
    assert result.exit_code == 0
    assert "DIRECT (awg laptop)" in result.output
    assert "awg-схема активна" in result.output
    assert "vpn-awg-split@laptop.service" in result.output
    assert "no active profile" not in result.output


def test_status_without_any_scheme_still_says_no_profile(env, monkeypatch):
    """Ни туннелей, ни профиля — прежняя строка остаётся (не маскируем пустоту)."""
    from vpnm import amneziawg as awg
    from vpnm import mode as mode_mod

    monkeypatch.setattr(awg, "active_instances", lambda: [])
    monkeypatch.setattr(awg, "instance_active", lambda name: False)
    monkeypatch.setattr(mode_mod, "read_mode", lambda: {})
    monkeypatch.setattr(runners, "get_active", lambda: None)

    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exception is None
    assert "no active profile" in result.output


# --- status не врёт про мёртвый юнит (инцидент 2026-11-18) ---------------------


def test_status_relay_mode_but_unit_inactive_is_explicit(env, monkeypatch):
    """mode=relay + юнит неактивен: явная пометка, а не «RELAY (… → ip)»."""
    from vpnm import amneziawg as awg
    from vpnm import mode as mode_mod

    monkeypatch.setattr(awg, "active_instances", lambda: [])
    monkeypatch.setattr(awg, "instance_active", lambda name: False)
    monkeypatch.setattr(mode_mod, "read_mode", lambda: {
        "mode": "relay", "proto": "amneziawg", "iface": "relay",
        "unit": "vpn-awg-split@relay.service", "target": "203.0.113.10"})
    monkeypatch.setattr(runners, "get_active", lambda: None)

    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exception is None
    assert result.exit_code == 0
    assert ("RELAY (юнит vpn-awg-split@relay.service неактивен — "
            "туннель не поднят)" in result.output)
    assert "vpn start relay" in result.output
    # мёртвый target не выдаётся за живой маршрут
    assert "→ 203.0.113.10" not in result.output


def test_status_relay_mode_when_unit_active_is_unchanged(env, monkeypatch):
    from vpnm import amneziawg as awg
    from vpnm import mode as mode_mod

    monkeypatch.setattr(awg, "active_instances", lambda: [])
    monkeypatch.setattr(awg, "instance_active", lambda name: True)
    monkeypatch.setattr(mode_mod, "read_mode", lambda: {
        "mode": "relay", "proto": "amneziawg", "iface": "relay",
        "unit": "vpn-awg-split@relay.service", "target": "203.0.113.10"})
    monkeypatch.setattr(runners, "get_active", lambda: None)

    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "RELAY (awg relay → 203.0.113.10)" in result.output
    assert "неактивен" not in result.output


def test_status_direct_mode_but_unit_inactive_is_explicit(env, monkeypatch):
    from vpnm import amneziawg as awg
    from vpnm import mode as mode_mod

    monkeypatch.setattr(awg, "active_instances", lambda: [])
    monkeypatch.setattr(awg, "instance_active", lambda name: False)
    monkeypatch.setattr(mode_mod, "read_mode", lambda: {
        "mode": "direct", "proto": "amneziawg", "iface": "laptop",
        "unit": "vpn-awg-split@laptop.service"})
    monkeypatch.setattr(runners, "get_active", lambda: None)

    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert ("direct (юнит vpn-awg-split@laptop.service неактивен — "
            "туннель не поднят)" in result.output)
    assert "vpn start" in result.output


def test_status_direct_mode_when_unit_active_is_unchanged(env, monkeypatch):
    from vpnm import amneziawg as awg
    from vpnm import mode as mode_mod

    monkeypatch.setattr(awg, "active_instances", lambda: [])
    monkeypatch.setattr(awg, "instance_active",
                        lambda name, run=None: name == "laptop")
    monkeypatch.setattr(mode_mod, "read_mode", lambda: {
        "mode": "direct", "proto": "amneziawg", "iface": "laptop",
        "unit": "vpn-awg-split@laptop.service"})
    monkeypatch.setattr(runners, "get_active", lambda: None)

    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "DIRECT(amneziawg, laptop)" in result.output
    assert "неактивен" not in result.output
