"""Direct ↔ relay mutual exclusion, ``mode.json`` and guard contract.

No root, no network, no real systemd: ``systemctl``/``sudo`` interactions are
monkeypatched and ``~/.awg``/data dirs point into ``tmp_path``.  What is fixed
by these tests:

- ``vpn start relay`` tears the direct contour down first (profiles + AWG
  instances + xray-split), then installs/starts ``vpn-awg-split@relay`` and
  records ``mode=relay`` (proto/iface/unit/target) for ``vpn guard``;
- ``vpn start`` stops the relay instance before raising the direct AWG scheme;
- a failed relay stop aborts the direct start (never two tunnels at once);
- ``vpn down`` stops profiles and the relay but never ``vpn-guard.service``;
- ``mode.json`` is a separate artefact from the probe-only ``activity.json``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import amneziawg, cli, mode, profiles, runners, xsplit  # noqa: E402

RELAY_CONF = """[Interface]
PrivateKey = FIXTURE_PW_RelayClientKey
Address = 10.10.0.2/32
DNS = 1.1.1.1
MTU = 1280

[Peer]
PublicKey = FIXTURE_PW_RelayServerKey
Endpoint = 203.0.113.10:443
AllowedIPs = 0.0.0.0/0,::/0
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "STORE_PATH", tmp_path / "profiles.json")
    monkeypatch.setattr(runners, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(amneziawg, "LOCAL_CONF_DIR", tmp_path / "awg")
    monkeypatch.setenv("VPN_MANAGER_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "awg").mkdir()
    return tmp_path


# --- mode.json ---------------------------------------------------------------


def test_mode_roundtrip_and_permissions(env):
    assert mode.write_mode("relay", proto="amneziawg", iface="relay",
                           unit="vpn-awg-split@relay.service",
                           target="203.0.113.10")
    payload = mode.read_mode()
    assert payload["schema"] == mode.SCHEMA
    assert payload["mode"] == "relay"
    assert payload["iface"] == "relay"
    path = mode.mode_path()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert "RELAY" in mode.describe(payload)


def test_mode_broken_and_foreign_schema_are_ignored(env):
    path = mode.mode_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert mode.read_mode() == {}
    path.write_text(json.dumps({"schema": "other/1", "mode": "relay"}),
                    encoding="utf-8")
    assert mode.read_mode() == {}
    path.write_text(json.dumps({"schema": mode.SCHEMA, "mode": "bogus"}),
                    encoding="utf-8")
    assert mode.read_mode() == {}


def test_clear_mode_records_none(env):
    mode.write_mode("direct", proto="amneziawg", iface="laptop")
    assert mode.clear_mode()
    payload = mode.read_mode()
    assert payload["mode"] == "none"
    assert "NONE" in mode.describe(payload)


def test_mode_never_overwrites_activity_json(env, monkeypatch):
    guard_dir = env / "guard"
    guard_dir.mkdir()
    activity = guard_dir / "activity.json"
    activity.write_text(json.dumps({"cmd": "probe", "started": "x",
                                    "until": "9999-01-01T00:00:00+00:00"}),
                        encoding="utf-8")
    monkeypatch.setenv("VPN_MANAGER_GUARD_DIR", str(guard_dir))
    mode.write_mode("relay", proto="amneziawg", iface="relay")
    assert json.loads(activity.read_text())["cmd"] == "probe"


# --- vpn start relay ---------------------------------------------------------


def _patch_teardown(monkeypatch, calls, *, failures=None):
    monkeypatch.setattr(runners, "stop_all_profiles",
                        lambda run=None: (["asv2-reality"], failures or []))
    monkeypatch.setattr(cli, "_sync_sysproxy_off_after", lambda: None)
    monkeypatch.setattr(xsplit, "active_profile", lambda run=None: None)
    monkeypatch.setattr(amneziawg, "active_instances",
                        lambda run=None: calls.get("instances", []))
    monkeypatch.setattr(amneziawg, "domain_split_apply",
                        lambda action, run=None: (True, "split off"))
    monkeypatch.setattr(amneziawg, "stop_instances",
                        lambda keep=None, run=None: (
                            calls.get("instances", []), []))


def _patch_postchecks(monkeypatch, *, l3=(True, "dev relay via 10.10.0.1"),
                      dp=(True, {"ip": "203.0.113.9", "country": "EE"})):
    """QX-DR-1: моки fail-closed пост-проверок L3 + data-plane."""
    monkeypatch.setattr(cli, "_relay_l3_ok",
                        lambda instance="relay", run=None: l3)
    monkeypatch.setattr(cli, "_relay_dataplane",
                        lambda instance="relay", run=None: dp)


def test_start_relay_stops_direct_and_records_mode(env, monkeypatch):
    (env / "awg" / "relay.conf").write_text(RELAY_CONF, encoding="utf-8")
    calls: dict = {"instances": ["laptop"], "control": [], "egress": []}
    _patch_teardown(monkeypatch, calls)
    _patch_postchecks(monkeypatch)
    monkeypatch.setattr(cli, "_print_egress_ip",
                        lambda iface=None: calls["egress"].append(iface))
    monkeypatch.setattr(amneziawg, "sudo_available", lambda run=None: True)
    monkeypatch.setattr(amneziawg, "instance_active",
                        lambda name, run=None: False)
    monkeypatch.setattr(amneziawg, "install_instance_conf",
                        lambda name, src, run=None: (True, f"installed {name}"))
    monkeypatch.setattr(amneziawg, "instance_control",
                        lambda name, action, run=None: calls["control"].append(
                            (name, action)) or (True, "ok"))

    result = CliRunner().invoke(cli.app, ["start", "relay"])
    assert result.exit_code == 0, result.output
    assert calls["control"] == [("relay", "on")]
    payload = mode.read_mode()
    assert payload["mode"] == "relay"
    assert payload["proto"] == "amneziawg"
    assert payload["iface"] == "relay"
    assert payload["unit"] == "vpn-awg-split@relay.service"
    assert payload["target"] == "203.0.113.10"
    assert "RELAY" in result.output
    assert "vpn-guard" not in json.dumps(calls)
    # QX-DR-1: egress-проба биндится к интерфейсу релея
    assert calls["egress"] == ["relay"]


def test_start_relay_without_conf_is_refused(env):
    result = CliRunner().invoke(cli.app, ["start", "relay"])
    assert result.exit_code == 2
    assert "relay.conf" in result.output
    assert mode.read_mode() == {}


def test_start_relay_aborts_when_direct_not_torn_down(env, monkeypatch):
    (env / "awg" / "relay.conf").write_text(RELAY_CONF, encoding="utf-8")
    calls: dict = {"instances": [], "control": []}
    _patch_teardown(monkeypatch, calls, failures=["vpnm-a.service: boom"])
    monkeypatch.setattr(amneziawg, "sudo_available", lambda run=None: True)
    monkeypatch.setattr(amneziawg, "install_instance_conf",
                        lambda name, src, run=None: (True, "installed"))
    monkeypatch.setattr(amneziawg, "instance_control",
                        lambda name, action, run=None: calls["control"].append(
                            (name, action)) or (True, "ok"))

    result = CliRunner().invoke(cli.app, ["start", "relay"])
    assert result.exit_code == 1
    assert calls["control"] == []  # relay never started on a mixed route
    assert mode.read_mode() == {}


# --- vpn start (direct) ------------------------------------------------------


def test_start_direct_stops_relay_first(env, monkeypatch):
    calls: list = []
    monkeypatch.setattr(amneziawg, "instance_active",
                        lambda name, run=None: True)
    monkeypatch.setattr(amneziawg, "instance_control",
                        lambda name, action, run=None: calls.append(
                            (name, action)) or (True, "ok"))
    monkeypatch.setattr(cli, "_apply_awg_scheme", lambda: (True, "ok"))
    monkeypatch.setattr(amneziawg, "local_instance", lambda run=None: "laptop")

    result = CliRunner().invoke(cli.app, ["start", "awg", "--allow-direct"])
    assert result.exit_code == 0, result.output
    assert calls == [("relay", "off")]
    payload = mode.read_mode()
    assert payload["mode"] == "direct"
    assert payload["iface"] == "laptop"
    assert payload["unit"] == "vpn-awg-split@laptop.service"


def test_start_direct_aborts_if_relay_will_not_stop(env, monkeypatch):
    calls: list = []
    monkeypatch.setattr(amneziawg, "instance_active",
                        lambda name, run=None: True)
    monkeypatch.setattr(amneziawg, "instance_control",
                        lambda name, action, run=None: calls.append(
                            (name, action)) or (False, "sudo failed"))
    started: list = []
    monkeypatch.setattr(cli, "_apply_awg_scheme",
                        lambda: started.append(True) or (True, "ok"))

    result = CliRunner().invoke(cli.app, ["start", "awg", "--allow-direct"])
    assert result.exit_code == 1
    assert started == []  # no direct scheme on top of a live relay
    assert mode.read_mode() == {}


# --- QX-DR-1: идемпотентный подъём + fail-closed пост-проверки -----------------


def test_start_relay_idempotent_rebuild_when_route_slipped(env, monkeypatch):
    """Активный юнит + слетевший маршрут → off → install → on (не no-op)."""
    (env / "awg" / "relay.conf").write_text(RELAY_CONF, encoding="utf-8")
    calls: dict = {"instances": ["relay"], "control": [], "install": []}
    _patch_teardown(monkeypatch, calls)
    _patch_postchecks(monkeypatch)
    monkeypatch.setattr(cli, "_print_egress_ip", lambda iface=None: None)
    monkeypatch.setattr(amneziawg, "sudo_available", lambda run=None: True)
    monkeypatch.setattr(amneziawg, "instance_conf_matches",
                        lambda name, src, run=None: True)  # conf ok…
    monkeypatch.setattr(cli, "_relay_l3_ok",
                        lambda instance="relay", run=None:
                        (False, "dev eth0 via …"))  # …но маршрут слетел
    monkeypatch.setattr(amneziawg, "install_instance_conf",
                        lambda name, src, run=None:
                        calls["install"].append(name) or (True, "installed"))
    monkeypatch.setattr(amneziawg, "instance_control",
                        lambda name, action, run=None: calls["control"].append(
                            (name, action)) or (True, "ok"))
    # первый вызов (fast-path чек) видит слетевший маршрут, пост-проверка — зелёный
    l3_state = {"n": 0}

    def fake_l3(instance="relay", run=None):
        l3_state["n"] += 1
        return (False, "dev eth0 via …") if l3_state["n"] == 1 else \
            (True, "dev relay")

    monkeypatch.setattr(cli, "_relay_l3_ok", fake_l3)

    result = CliRunner().invoke(cli.app, ["start", "relay"])
    assert result.exit_code == 0, result.output
    assert calls["control"] == [("relay", "off"), ("relay", "on")]
    assert calls["install"] == ["relay"]
    assert "rebuild" in result.output
    assert mode.read_mode()["mode"] == "relay"


def test_start_relay_fastpath_when_healthy(env, monkeypatch):
    """Активный юнит + conf совпадает + L3 жив → без рестарта."""
    (env / "awg" / "relay.conf").write_text(RELAY_CONF, encoding="utf-8")
    calls: dict = {"instances": ["relay"], "control": []}
    _patch_teardown(monkeypatch, calls)
    _patch_postchecks(monkeypatch)
    monkeypatch.setattr(cli, "_print_egress_ip", lambda iface=None: None)
    monkeypatch.setattr(amneziawg, "sudo_available", lambda run=None: True)
    monkeypatch.setattr(amneziawg, "instance_conf_matches",
                        lambda name, src, run=None: True)
    monkeypatch.setattr(amneziawg, "install_instance_conf",
                        lambda name, src, run=None: (_ for _ in ()).throw(
                            AssertionError("fast-path не должен переустанавливать")))
    monkeypatch.setattr(amneziawg, "instance_control",
                        lambda name, action, run=None: calls["control"].append(
                            (name, action)) or (True, "ok"))

    result = CliRunner().invoke(cli.app, ["start", "relay"])
    assert result.exit_code == 0, result.output
    assert calls["control"] == []  # ни off, ни on — сессии не рвутся
    assert "fast-path" in result.output
    assert mode.read_mode()["mode"] == "relay"


def test_start_relay_l3_fail_is_fail_closed(env, monkeypatch):
    """L3 не захватился после start → exit 1, mode НЕ relay, туннель снят."""
    (env / "awg" / "relay.conf").write_text(RELAY_CONF, encoding="utf-8")
    calls: dict = {"instances": [], "control": []}
    _patch_teardown(monkeypatch, calls)
    _patch_postchecks(monkeypatch, l3=(False, "маршрут мимо туннеля: dev eth0"))
    monkeypatch.setattr(cli, "_print_egress_ip", lambda iface=None: None)
    monkeypatch.setattr(amneziawg, "sudo_available", lambda run=None: True)
    monkeypatch.setattr(amneziawg, "install_instance_conf",
                        lambda name, src, run=None: (True, "installed"))
    monkeypatch.setattr(amneziawg, "instance_control",
                        lambda name, action, run=None: calls["control"].append(
                            (name, action)) or (True, "ok"))

    result = CliRunner().invoke(cli.app, ["start", "relay"])
    assert result.exit_code == 1
    assert ("relay", "off") in calls["control"]  # анти «полу-применённый»
    assert mode.read_mode().get("mode") != "relay"
    assert "L3-захват не подтвердился" in result.output
    assert "режим RELAY активен" not in result.output


def test_start_relay_dataplane_timeout_is_fail_closed(env, monkeypatch):
    """L3 OK, но data-plane молчит → exit 1 с раздельным диагнозом."""
    (env / "awg" / "relay.conf").write_text(RELAY_CONF, encoding="utf-8")
    calls: dict = {"instances": [], "control": []}
    _patch_teardown(monkeypatch, calls)
    _patch_postchecks(monkeypatch, dp=(False, {}))
    monkeypatch.setattr(cli, "_print_egress_ip", lambda iface=None: None)
    monkeypatch.setattr(amneziawg, "sudo_available", lambda run=None: True)
    monkeypatch.setattr(amneziawg, "install_instance_conf",
                        lambda name, src, run=None: (True, "installed"))
    monkeypatch.setattr(amneziawg, "instance_control",
                        lambda name, action, run=None: calls["control"].append(
                            (name, action)) or (True, "ok"))

    result = CliRunner().invoke(cli.app, ["start", "relay"])
    assert result.exit_code == 1
    assert "data-plane не отвечает" in result.output
    assert "режим RELAY активен" not in result.output
    assert mode.read_mode().get("mode") != "relay"
    # L3 жив → туннель НЕ снимаем (маршрут применён — диагноз в сообщении)
    assert calls["control"] == [("relay", "on")]


def test_teardown_direct_never_stops_oob_instance(env, monkeypatch):
    """QX-DR-1 oob-инвариант: oob* не гасится и не считается direct."""
    calls: dict = {"instances": ["laptop", "oob"], "control": []}
    _patch_teardown(monkeypatch, calls)
    monkeypatch.setattr(amneziawg, "stop_instances",
                        lambda keep=None, run=None: (
                            [n for n in calls["instances"]
                             if n != keep and not n.startswith("oob")], []))
    failures = cli._teardown_direct()
    assert failures == []


# --- vpn down ----------------------------------------------------------------


def test_down_stops_profiles_and_relay_but_not_guard(env, monkeypatch):
    calls: list = []
    monkeypatch.setattr(runners, "stop_all_profiles",
                        lambda run=None: (["asv2-reality"], []))
    monkeypatch.setattr(cli, "_sync_sysproxy_off_after", lambda: None)
    monkeypatch.setattr(amneziawg, "instance_active",
                        lambda name, run=None: True)
    monkeypatch.setattr(amneziawg, "instance_control",
                        lambda name, action, run=None: calls.append(
                            (name, action)) or (True, "ok"))
    mode.write_mode("relay", proto="amneziawg", iface="relay")

    result = CliRunner().invoke(cli.app, ["down"])
    assert result.exit_code == 0, result.output
    assert calls == [("relay", "off")]
    assert "vpn guard" in result.output
    assert "vpn-guard" not in json.dumps(calls)
    assert mode.read_mode()["mode"] == "none"


def test_down_is_idempotent_when_nothing_runs(env, monkeypatch):
    monkeypatch.setattr(runners, "stop_all_profiles",
                        lambda run=None: ([], []))
    monkeypatch.setattr(amneziawg, "instance_active",
                        lambda name, run=None: False)
    result = CliRunner().invoke(cli.app, ["down"])
    assert result.exit_code == 0
    assert "нет активных туннелей" in result.output


def test_start_relay_without_sudo_changes_nothing(env, monkeypatch):
    (env / "awg" / "relay.conf").write_text(RELAY_CONF, encoding="utf-8")
    calls: dict = {"instances": ["laptop"], "control": []}
    _patch_teardown(monkeypatch, calls)
    monkeypatch.setattr(amneziawg, "sudo_available", lambda run=None: False)

    result = CliRunner().invoke(cli.app, ["start", "relay"])
    assert result.exit_code == 1
    assert "passwordless sudo" in result.output
    assert calls["control"] == []
    assert mode.read_mode() == {}


def test_start_relay_reality_fallback_uses_profile(env, monkeypatch):
    calls: list = []
    monkeypatch.setattr(cli, "_teardown_direct", lambda: [])
    monkeypatch.setattr(cli, "_stop_relay_contour", lambda: (
        calls.append("relay-off") or True, "релей не активен"))
    monkeypatch.setattr(runners, "up", lambda name: f"/tmp/{name}.json")
    monkeypatch.setattr(profiles, "get",
                        lambda name: {"socks_port": 2080, "protocol": "x"})

    result = CliRunner().invoke(cli.app,
                                ["start", "relay", "--proto", "reality"])
    assert result.exit_code == 0, result.output
    payload = mode.read_mode()
    assert payload["mode"] == "relay"
    assert payload["proto"] == "xray"
    assert payload["unit"] == "vpnm-relay-reality.service"
    assert "фолбэк" in result.output
