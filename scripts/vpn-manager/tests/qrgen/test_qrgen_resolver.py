"""WP-QR1: резолвер ``vpn qr`` на изолированных фикстурах.

Фикстуры воспроизводят живую топологию (SPEC §7): store из трёх профилей
(``asv2-reality`` домен / ``relay-reality`` IP / ``asv2-hy2``), ``relay.conf``
с доменом релея, ``mode.json`` ``{"mode":"direct","iface":"laptop"}`` без
``target``, релейные AWG-конфиги в ``~/.awg`` и direct-пир ``laptop.conf``
только в системном каталоге (читается замоканным ``sudo -n cat``).

Живая машина не трогается ни одним тестом: пути подменяются monkeypatch'ем,
``subprocess`` — заглушкой (tests/conftest.py autouse-guard страхует сверху).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm import amneziawg, mode, profiles  # noqa: E402
from vpnm.qrgen import models, redact, resolver  # noqa: E402

# --- фикстуры данных (зарезервированные IP/UUID, префиксы FIXTURE_*) ---------

REALITY_DIRECT = (
    "vless://11111111-2222-3333-4444-000000000001@203.0.113.10:443"
    "?security=reality&sni=www.google.com&pbk=FIXTURE_PBK_DIRECT&sid=01"
    "&flow=xtls-rprx-vision&fp=chrome#asv2-reality")
REALITY_RELAY = (
    "vless://11111111-2222-3333-4444-000000000002@185.80.91.135:443"
    "?security=reality&sni=ya.ru&pbk=FIXTURE_PBK_RELAY&sid=02"
    "&flow=xtls-rprx-vision&fp=chrome#relay-reality")
REALITY_DIRECT_SECOND = (
    "vless://11111111-2222-3333-4444-000000000003@203.0.113.11:443"
    "?security=reality&sni=www.google.com&pbk=FIXTURE_PBK_DIRECT2&sid=03"
    "&flow=xtls-rprx-vision&fp=chrome#asv2-reality-2")
HY2 = ("hysteria2://FIXTURE_PW_HY2@203.0.113.20:443/?sni=asv2.example.invalid"
       "#asv2-hy2")

RELAY_DOMAIN = "relay.asvpersonal.ru"


def _awg_text(endpoint: str, *, address: str = "10.10.0.2/32", dns: bool = True,
              keepalive: bool = True, table_off: bool = False) -> str:
    lines = ["[Interface]", f"Address = {address}"]
    if dns:
        lines.append("DNS = 1.1.1.1")
    if table_off:
        lines.append("Table = off")
    lines += [
        "PrivateKey = FIXTURE_PW_CLIENT_AWG",
        "MTU = 1280",
        "Jc = 4",
        "S1 = 85",
        "H1 = 2093687166",
        "",
        "[Peer]",
        "PublicKey = FIXTURE_PW_SERVER_AWG",
        "PresharedKey = FIXTURE_PW_PSK_AWG",
        f"Endpoint = {endpoint}",
        "AllowedIPs = 0.0.0.0/0",
    ]
    if keepalive:
        lines.append("PersistentKeepalive = 25")
    return "\n".join(lines) + "\n"


class Env:
    """Изолированное «локальное состояние»: store, ~/.awg, /etc, mode.json."""

    def __init__(self, tmp_path: Path) -> None:
        self.store = tmp_path / "profiles.json"
        self.awg_dir = tmp_path / "awg"
        self.awg_dir.mkdir()
        self.etc_dir = tmp_path / "etc"
        self.etc_dir.mkdir()
        self.mode_payload: Dict[str, object] = {
            "schema": "vpnm.mode/1", "mode": "direct", "proto": "amneziawg",
            "iface": "laptop", "unit": "vpn-awg-split@laptop.service",
        }
        self.records: List[Dict[str, str]] = []

    # store ------------------------------------------------------------------
    def add_profile(self, name: str, link: str, protocol: str) -> None:
        self.records.append({"name": name, "protocol": protocol, "link": link})
        self.store.write_text(
            json.dumps({"profiles": self.records}, indent=2) + "\n",
            encoding="utf-8")

    def live_store(self) -> None:
        self.add_profile("asv2-reality", REALITY_DIRECT, "xray-reality")
        self.add_profile("relay-reality", REALITY_RELAY, "xray-reality")
        self.add_profile("asv2-hy2", HY2, "hysteria2")

    # awg --------------------------------------------------------------------
    def add_awg(self, stem: str, text: str) -> Path:
        path = self.awg_dir / f"{stem}.conf"
        path.write_text(text, encoding="utf-8")
        return path

    def add_system_awg(self, stem: str, text: str) -> Path:
        path = self.etc_dir / f"{stem}.conf"
        path.write_text(text, encoding="utf-8")
        return path

    def live_awg(self) -> None:
        relay = f"{RELAY_DOMAIN}:443"
        self.add_awg("android", _awg_text(relay, address="10.10.0.4/32"))
        self.add_awg("windows", _awg_text(relay, address="10.10.0.5/32"))
        # relay.conf — как в жизни: без DNS и без PersistentKeepalive
        self.add_awg("relay", _awg_text(
            relay, address="10.10.0.2/32", dns=False, keepalive=False))
        # мусор: должен игнорироваться
        (self.awg_dir / "laptop.conf.bak.1").write_text(
            _awg_text("198.51.100.9:51825"), encoding="utf-8")
        # активный direct-пир доступен только через sudo -n cat
        self.add_system_awg("laptop", _awg_text(
            "213.108.23.119:51825", address="10.9.0.2/32",
            dns=False, keepalive=False, table_off=True))
        self.add_system_awg("awg0-laptop", _awg_text(
            "103.45.247.172:51825", address="10.9.0.2/32", table_off=True))


@pytest.fixture()
def env(tmp_path, monkeypatch) -> Env:
    state = Env(tmp_path)

    monkeypatch.setattr(profiles, "STORE_PATH", state.store)
    monkeypatch.setattr(amneziawg, "LOCAL_CONF_DIR", state.awg_dir)
    monkeypatch.setattr(amneziawg, "AWG_SYSTEM_CONF_DIR", str(state.etc_dir))
    monkeypatch.setattr(mode, "read_mode", lambda path=None: dict(state.mode_payload))
    return state


@pytest.fixture()
def sudo(monkeypatch):
    """Заглушка ``sudo -n cat``: отдаёт содержимое ``AWG_SYSTEM_CONF_DIR``."""
    calls: List[List[str]] = []

    def install(etc_dir: Path) -> List[List[str]]:
        def fake_run(cmd, **kwargs):  # noqa: ANN001 - повторяет subprocess.run
            calls.append(list(cmd))
            target = Path(cmd[-1])
            if not str(target).startswith(str(etc_dir)) or not target.is_file():
                return SimpleNamespace(returncode=1, stdout="", stderr="No such file")
            return SimpleNamespace(returncode=0, stdout=target.read_text(encoding="utf-8"),
                                   stderr="")

        monkeypatch.setattr(resolver, "subprocess", SimpleNamespace(run=fake_run))
        return calls

    return install


def req(route: str, proto: str, os_: str, **kwargs) -> models.QrRequest:
    return models.QrRequest(
        route=models.Route(route), proto=models.Proto(proto),
        target_os=models.TargetOS(os_), **kwargs)


# --- P3: валидация --profile --------------------------------------------------

@pytest.mark.parametrize("bad", ["", "..", "../etc/passwd", ".hidden",
                                 "laptop.conf", "-bad", "a/b", "a\\b"])
def test_profile_name_rejects_path_like_values(bad):
    with pytest.raises(models.QrUsageError) as excinfo:
        models.validate_profile_name(bad)
    assert excinfo.value.exit_code == models.EXIT_USAGE


@pytest.mark.parametrize("good", ["laptop", "asv2-reality", "relay-reality",
                                  "awg0-laptop", "A_1"])
def test_profile_name_accepts_plain_stems(good):
    assert models.validate_profile_name(good) == good


# --- §3.3: матрица --client × протокол ---------------------------------------

def test_client_defaults_match_matrix():
    assert models.resolve_client(models.Proto.AWG, models.TargetOS.ANDROID,
                                 "auto") == "amneziawg"
    assert models.resolve_client(models.Proto.REALITY, models.TargetOS.LINUX,
                                 "auto") == "nekobox"
    assert models.resolve_client(models.Proto.XHTTP, models.TargetOS.LINUX,
                                 "auto") == "hiddify"
    assert models.resolve_client(models.Proto.HY, models.TargetOS.IOS,
                                 "auto") == "streisand"
    assert models.resolve_client(models.Proto.REALITY, models.TargetOS.WINDOWS,
                                 "hiddify") == "hiddify"


@pytest.mark.parametrize("proto", list(models.Proto))
def test_amneziavpn_is_unsupported_in_v1(proto):
    with pytest.raises(models.QrUnsupportedComboError) as excinfo:
        models.resolve_client(proto, models.TargetOS.ANDROID, "amneziavpn")
    assert excinfo.value.exit_code == models.EXIT_UNSUPPORTED_COMBO


def test_client_proto_mismatch_is_unsupported():
    with pytest.raises(models.QrUnsupportedComboError):
        models.resolve_client(models.Proto.AWG, models.TargetOS.ANDROID, "v2rayng")
    with pytest.raises(models.QrUnsupportedComboError):
        models.resolve_client(models.Proto.REALITY, models.TargetOS.ANDROID,
                              "amneziawg")


def test_unknown_client_is_usage_error():
    with pytest.raises(models.QrUsageError):
        models.resolve_client(models.Proto.REALITY, models.TargetOS.ANDROID, "warp")


# --- B2: неподдерживаемые маршруты -------------------------------------------

@pytest.mark.parametrize("proto", ["xhttp", "hy"])
def test_relay_unsupported_protocols_fail_before_reading_anything(
        env, sudo, proto):
    calls = sudo(env.etc_dir)
    with pytest.raises(models.QrUnsupportedComboError) as excinfo:
        resolver.resolve(req("relay", proto, "android"))
    assert excinfo.value.exit_code == models.EXIT_UNSUPPORTED_COMBO
    assert calls == []  # ни конфигов, ни subprocess


# --- R1: живой store, direct vs relay ----------------------------------------

def test_live_store_relay_reality_is_classified_by_name(env):
    env.live_store()
    env.add_awg("relay", _awg_text(f"{RELAY_DOMAIN}:443", dns=False,
                                   keepalive=False))

    result = resolver.resolve(req("relay", "reality", "windows"))

    assert result.profile_name == "relay-reality"
    assert result.source == "profile"
    assert result.endpoint_host == "185.80.91.135"
    assert result.client == "v2rayn"
    assert any("классифицирован как relay по имени" in w
               for w in result.warnings)
    assert any("185.80.91.135" in w for w in result.warnings)


def test_live_store_direct_reality_ignores_relay_profile(env):
    env.live_store()
    env.add_awg("relay", _awg_text(f"{RELAY_DOMAIN}:443", dns=False,
                                   keepalive=False))

    result = resolver.resolve(req("direct", "reality", "android"))

    assert result.profile_name == "asv2-reality"
    assert result.endpoint_host == "203.0.113.10"
    assert result.warnings == []


def test_ambiguous_direct_profiles_require_profile_flag(env):
    env.live_store()
    env.add_profile("asv2-reality-2", REALITY_DIRECT_SECOND, "xray-reality")

    with pytest.raises(models.QrConfigError) as excinfo:
        resolver.resolve(req("direct", "reality", "android"))
    assert excinfo.value.exit_code == models.EXIT_CONFIG_ERROR
    assert "asv2-reality" in str(excinfo.value)

    chosen = resolver.resolve(req("direct", "reality", "android",
                                  profile="asv2-reality-2"))
    assert chosen.profile_name == "asv2-reality-2"


def test_profile_route_mismatch_is_config_error(env):
    env.live_store()
    with pytest.raises(models.QrConfigError):
        resolver.resolve(req("direct", "reality", "android",
                             profile="relay-reality"))


def test_hy2_direct_resolves(env):
    env.live_store()
    result = resolver.resolve(req("direct", "hy", "linux"))
    assert result.profile_name == "asv2-hy2"
    assert result.endpoint_port == 443
    assert result.client == "nekobox"


# --- R3 + R4b + P2 + Мелочь 4: AWG -------------------------------------------

def test_relay_awg_picks_os_specific_config_without_dns_warning(env):
    env.live_awg()

    android = resolver.resolve(req("relay", "awg", "android"))
    assert android.profile_name == "android"
    assert android.source == "awg"
    assert android.endpoint_host == RELAY_DOMAIN
    assert not any("PersistentKeepalive" in w for w in android.warnings)

    windows = resolver.resolve(req("relay", "awg", "windows"))
    assert windows.profile_name == "windows"
    assert windows.client == "amneziawg"


def test_relay_awg_linux_uses_relay_conf_and_warns_about_missing_keys(env):
    env.live_awg()

    result = resolver.resolve(req("relay", "awg", "linux"))

    assert result.profile_name == "relay"
    assert any("отсутствует DNS" in w and "PersistentKeepalive" in w
               for w in result.warnings)


def test_direct_awg_uses_active_peer_via_sudo(env, sudo):
    env.live_awg()
    calls = sudo(env.etc_dir)

    result = resolver.resolve(req("direct", "awg", "android"))

    assert result.profile_name == "laptop"
    assert result.endpoint_host == "213.108.23.119"
    assert result.endpoint_port == 51825
    assert any("активного пира 'laptop'" in w for w in result.warnings)
    assert any("10.9.0.2/32" in w for w in result.warnings)
    # мусор и второй системный direct-конфиг не читаются
    assert [Path(c[-1]).name for c in calls] == ["laptop.conf"]


def test_direct_awg_rejects_relay_iface_from_mode(env, sudo):
    env.live_awg()
    sudo(env.etc_dir)
    env.mode_payload = {"schema": "vpnm.mode/1", "mode": "relay",
                        "proto": "amneziawg", "iface": "relay",
                        "unit": "vpn-awg-split@relay.service"}

    with pytest.raises(models.QrConfigError) as excinfo:
        resolver.resolve(req("direct", "awg", "android"))
    assert excinfo.value.exit_code == models.EXIT_CONFIG_ERROR


def test_direct_awg_rejects_active_peer_with_relay_endpoint(env, sudo):
    env.live_awg()
    # активный пир есть, но его Endpoint — релейный (P2: не выбирать)
    env.add_system_awg("laptop", _awg_text(f"{RELAY_DOMAIN}:443",
                                           address="10.9.0.2/32"))
    sudo(env.etc_dir)

    with pytest.raises(models.QrConfigError):
        resolver.resolve(req("direct", "awg", "android"))


def test_awg_profile_flag_checks_route(env):
    env.live_awg()
    with pytest.raises(models.QrConfigError):
        resolver.resolve(req("direct", "awg", "android", profile="android"))
    chosen = resolver.resolve(req("relay", "awg", "android", profile="relay"))
    assert chosen.profile_name == "relay"


def test_local_direct_awg_wins_over_mode_iface(env, sudo):
    env.live_awg()
    env.add_awg("android", _awg_text("198.51.100.77:51825"))
    calls = sudo(env.etc_dir)

    result = resolver.resolve(req("direct", "awg", "android"))

    assert result.profile_name == "android"
    assert result.endpoint_host == "198.51.100.77"
    assert calls == []  # системный каталог не понадобился


def test_broken_local_awg_file_does_not_break_resolution(env):
    env.live_awg()
    (env.awg_dir / "broken.conf").write_text("не INI вовсе\n", encoding="utf-8")

    result = resolver.resolve(req("relay", "awg", "android"))

    assert result.profile_name == "android"


def test_profile_proto_mismatch_is_reported_explicitly(env):
    env.live_store()
    with pytest.raises(models.QrConfigError) as excinfo:
        resolver.resolve(req("direct", "hy", "android",
                             profile="asv2-reality"))
    message = str(excinfo.value)
    assert "asv2-reality" in message
    assert "reality" in message and "hy" in message


# --- секреты не утекают -------------------------------------------------------

def test_redact_hides_ini_and_uri_secrets():
    ini = _awg_text("203.0.113.9:51825")
    cleaned = redact.redact_text(ini)
    assert "FIXTURE_PW_CLIENT_AWG" not in cleaned
    assert "FIXTURE_PW_PSK_AWG" not in cleaned
    assert "<redacted>" in cleaned

    uri = redact.redact_text(REALITY_DIRECT)
    assert "11111111-2222-3333-4444-000000000001" not in uri
    assert "FIXTURE_PBK_DIRECT" not in uri


def test_resolver_errors_do_not_leak_secrets(env):
    env.live_store()
    with pytest.raises(models.QrConfigError) as excinfo:
        resolver.resolve(req("relay", "reality", "android",
                             profile="missing-profile"))
    message = redact.redact_text(str(excinfo.value))
    for secret in ("11111111-2222-3333-4444-000000000001",
                   "11111111-2222-3333-4444-000000000002",
                   "FIXTURE_PBK_DIRECT", "FIXTURE_PBK_RELAY", "FIXTURE_PW_HY2"):
        assert secret not in message


def test_split_endpoint_handles_ipv6_and_rejects_garbage():
    assert resolver.split_endpoint("[2001:db8::1]:443") == ("2001:db8::1", 443)
    assert resolver.split_endpoint("relay.example.invalid:443") == (
        "relay.example.invalid", 443)
    for bad in ("", "no-port", "[2001:db8::1]", "host:notaport"):
        with pytest.raises(models.QrConfigError):
            resolver.split_endpoint(bad)
