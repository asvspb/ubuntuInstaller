"""WP-QR2: сборка payload — AWG (R2), Reality/XHTTP/Hysteria2 + round-trip.

Все ссылки/конфиги — фикстуры (зарезервированные IP, префиксы ``FIXTURE_``);
ничего не читается с диска, subprocess не вызывается.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm import links  # noqa: E402
from vpnm.qrgen import builders, models  # noqa: E402
from vpnm.xclient import XHTTP_PADDING_BYTES, XHTTP_XMUX  # noqa: E402

REALITY_LINK = (
    "vless://11111111-2222-3333-4444-000000000001@203.0.113.10:443"
    "?security=reality&sni=www.google.com&pbk=FIXTURE_PBK_DIRECT&sid="
    "&flow=xtls-rprx-vision&fp=chrome#asv2-reality")
REALITY_SPX = (
    "vless://11111111-2222-3333-4444-000000000001@203.0.113.10:443"
    "?security=reality&sni=www.google.com&pbk=FIXTURE_PBK_DIRECT&sid="
    "&flow=xtls-rprx-vision&fp=chrome&spx=%2Fcustom%2Fpath%3Fa%3D1#asv2-reality")
XHTTP_LINK = (
    "vless://11111111-2222-3333-4444-000000000004@203.0.113.30:8443"
    "?security=tls&type=xhttp&path=%2Fxhttp&host=cdn.example.invalid"
    "&sni=cdn.example.invalid&fp=chrome&mode=auto&flow=xtls-rprx-vision#xhttp")
HY2_LINK = (
    "hysteria2://FIXTURE_PW_HY2@203.0.113.20:443/?sni=asv2.example.invalid#asv2-hy2")


def _awg_raw(*, table_off: bool = False, extra_system: bool = False,
             dns: bool = True, keepalive: bool = True,
             allowed_ips: str = "0.0.0.0/0") -> str:
    lines = ["[Interface]", "Address = 10.9.0.2/32"]
    if dns:
        lines.append("DNS = 1.1.1.1")
    if table_off:
        lines.append("Table = off")
    if extra_system:
        lines.append("SaveConfig = true")
        lines.append("PostUp = iptables -A FORWARD -i wg0 -j ACCEPT")
        lines.append("PreUp = echo up")
    lines += [
        "PrivateKey = FIXTURE_PW_CLIENT_AWG",
        "MTU = 1280",
        "Jc = 4",
        "Jmin = 50",
        "Jmax = 1000",
        "S1 = 85",
        "S2 = 3",
        "H1 = 2093687166",
        "H2 = 1234",
        "H3 = 5678",
        "H4 = 9012",
        "I1 = <b 0x01>",
        "I2 = <b 0x02>",
        "I3 = <b 0x03>",
        "I4 = <b 0x04>",
        "I5 = <b 0x05>",
        "",
        "[Peer]",
        "PublicKey = FIXTURE_PW_SERVER_AWG",
        "PresharedKey = FIXTURE_PW_PSK_AWG",
        "Endpoint = 213.108.23.119:51825",
        f"AllowedIPs = {allowed_ips}",
    ]
    if keepalive:
        lines.append("PersistentKeepalive = 25")
    return "\n".join(lines) + "\n"


def _resolved(profile_name: str, link: str, proto: models.Proto, *,
              os_name: str = "android",
              raw_awg_ini: str | None = None) -> models.ResolvedSource:
    if link:
        parsed = links.parse_link(link)
        host, port = parsed.host, parsed.port
    else:
        host, port = "213.108.23.119", 51825
    return models.ResolvedSource(
        profile_name=profile_name, route=models.Route.DIRECT, proto=proto,
        target_os=models.TargetOS(os_name), client="auto",
        endpoint_host=host, endpoint_port=port,
        source="awg" if raw_awg_ini else "profile",
        raw_profile={"name": profile_name, "link": link} if link else None,
        raw_awg_ini=raw_awg_ini)


# --- AWG (AC-QR-4, R2) --------------------------------------------------------

def test_awg_strips_wg_quick_only_and_keeps_client_values():
    result = builders.build_payload(_resolved(
        "laptop", "", models.Proto.AWG, raw_awg_ini=_awg_raw(
            table_off=True, extra_system=True)))

    payload = result.payload
    for directive in ("Table", "SaveConfig", "PostUp", "PreUp"):
        assert directive not in payload
    for expected in (
            "PrivateKey = FIXTURE_PW_CLIENT_AWG",
            "Address = 10.9.0.2/32",
            "DNS = 1.1.1.1",
            "MTU = 1280",
            "Jc = 4", "S1 = 85", "H1 = 2093687166", "H4 = 9012",
            "PublicKey = FIXTURE_PW_SERVER_AWG",
            "PresharedKey = FIXTURE_PW_PSK_AWG",
            "Endpoint = 213.108.23.119:51825",
            "AllowedIPs = 0.0.0.0/0",
            "PersistentKeepalive = 25"):
        assert expected in payload
    assert payload.startswith("[Interface]\n")
    assert payload.endswith("\n") and not payload.endswith("\n\n")
    assert result.payload_format == "conf"


def test_awg_does_not_invent_dns_or_keepalive():
    # relay.conf-подобный конфиг: без DNS/keepalive, v4+v6 AllowedIPs
    result = builders.build_payload(_resolved(
        "relay", "", models.Proto.AWG,
        raw_awg_ini=_awg_raw(dns=False, keepalive=False,
                             allowed_ips="0.0.0.0/0,::/0")))
    payload = result.payload
    assert "DNS" not in payload
    assert "PersistentKeepalive" not in payload
    assert "AllowedIPs = 0.0.0.0/0,::/0" in payload


def test_awg_keeps_single_dns_and_keepalive_lines():
    payload = builders.build_payload(_resolved(
        "android", "", models.Proto.AWG, raw_awg_ini=_awg_raw())).payload
    assert payload.count("DNS = 1.1.1.1") == 1
    assert payload.count("PersistentKeepalive = 25") == 1


def test_awg_missing_required_fields_is_config_error_without_secrets():
    broken = _awg_raw().replace("PrivateKey = FIXTURE_PW_CLIENT_AWG", "MTU = 1280")
    with pytest.raises(models.QrConfigError) as excinfo:
        builders.build_payload(_resolved(
            "laptop", "", models.Proto.AWG, raw_awg_ini=broken))
    message = str(excinfo.value)
    assert excinfo.value.exit_code == models.EXIT_CONFIG_ERROR
    assert "privatekey" in message
    assert "FIXTURE_PW_PSK_AWG" not in message


# --- ECC (§5.1) ---------------------------------------------------------------

def test_ecc_is_m_for_short_and_l_for_long_payload():
    short = builders.build_payload(_resolved(
        "asv2-reality", REALITY_LINK, models.Proto.REALITY))
    assert short.payload_bytes <= builders.ECC_SMALL_MAX_BYTES
    assert short.qr_ecc == "M"

    long = builders.build_payload(_resolved(
        "laptop", "", models.Proto.AWG, raw_awg_ini=_awg_raw()))
    assert long.payload_bytes > builders.ECC_SMALL_MAX_BYTES
    assert long.qr_ecc == "L"


# --- Reality (AC-QR-5) --------------------------------------------------------

def test_reality_uri_has_required_params_and_roundtrips():
    result = builders.build_payload(_resolved(
        "asv2-reality", REALITY_LINK, models.Proto.REALITY, os_name="windows"))
    uri = result.payload

    assert uri.startswith("vless://11111111-2222-3333-4444-000000000001@203.0.113.10:443?")
    assert uri.endswith("#asv2-reality-windows")
    assert "security=reality" in uri and "type=tcp" in uri
    assert "flow=xtls-rprx-vision" in uri

    parsed = links.parse_link(uri)
    assert parsed.protocol == links.PROTOCOL_XRAY_REALITY
    assert parsed.credential == "11111111-2222-3333-4444-000000000001"
    assert (parsed.host, parsed.port) == ("203.0.113.10", 443)
    assert parsed.params["sni"] == "www.google.com"
    assert parsed.params["pbk"] == "FIXTURE_PBK_DIRECT"
    assert parsed.params["fp"] == "chrome"
    assert parsed.params["sid"] == ""
    assert "spx" not in parsed.params and "spath" not in parsed.params


def test_reality_spx_roundtrips_into_spath():
    result = builders.build_payload(_resolved(
        "asv2-reality", REALITY_SPX, models.Proto.REALITY))
    assert "spx=%2Fcustom%2Fpath%3Fa%3D1" in result.payload

    parsed = links.parse_link(result.payload)
    assert parsed.params["spath"] == "/custom/path?a=1"


def test_reality_ipv6_host_is_bracketed():
    link = ("vless://11111111-2222-3333-4444-000000000005@[2001:db8::1]:443"
            "?security=reality&sni=www.google.com&pbk=FIXTURE_PBK_V6&sid="
            "&flow=xtls-rprx-vision&fp=chrome#v6")
    result = builders.build_payload(_resolved(
        "v6", link, models.Proto.REALITY))
    assert "@[2001:db8::1]:443?" in result.payload
    assert result.endpoint == "[2001:db8::1]:443"


def test_reality_without_pbk_fails_without_leaking_secrets():
    link = ("vless://11111111-2222-3333-4444-000000000006@203.0.113.10:443"
            "?security=reality&sni=www.google.com&sid=&fp=chrome#broken")
    # профиль с битой ссылкой: разбор упадёт при сборке, не в helper'е
    resolved = models.ResolvedSource(
        profile_name="broken", route=models.Route.DIRECT,
        proto=models.Proto.REALITY, target_os=models.TargetOS.ANDROID,
        client="v2rayng", endpoint_host="203.0.113.10", endpoint_port=443,
        source="profile", raw_profile={"name": "broken", "link": link})
    with pytest.raises(models.QrConfigError) as excinfo:
        builders.build_payload(resolved)
    message = str(excinfo.value)
    assert excinfo.value.exit_code == models.EXIT_CONFIG_ERROR
    assert "11111111-2222-3333-4444-000000000006" not in message


# --- XHTTP + GR-25 (AC-QR-5) --------------------------------------------------

def test_xhttp_carries_gr25_extra_and_drops_flow():
    result = builders.build_payload(_resolved(
        "xhttp", XHTTP_LINK, models.Proto.XHTTP, os_name="linux"))
    uri = result.payload

    assert "type=xhttp" in uri
    assert "security=tls" in uri
    assert "flow=" not in uri  # инвариант §4.3

    query = parse_qs(urlsplit(uri).query, keep_blank_values=True)
    extra = json.loads(query["extra"][0])
    assert extra["xPaddingBytes"] == XHTTP_PADDING_BYTES
    assert extra["xmux"] == XHTTP_XMUX
    assert query["path"] == ["/xhttp"]
    assert query["host"] == ["cdn.example.invalid"]
    assert query["mode"] == ["auto"]


def test_xhttp_roundtrips_through_links():
    uri = builders.build_payload(_resolved(
        "xhttp", XHTTP_LINK, models.Proto.XHTTP)).payload
    parsed = links.parse_link(uri)
    assert parsed.protocol == links.PROTOCOL_XRAY_XHTTP
    assert parsed.params["path"] == "/xhttp"
    assert parsed.params["security"] == "tls"


def test_xhttp_extra_override_merges_over_gr25():
    link = XHTTP_LINK.replace(
        "&mode=auto",
        "&mode=auto&extra=%7B%22xPaddingBytes%22%3A%22100-200%22%7D")
    uri = builders.build_payload(_resolved(
        "xhttp", link, models.Proto.XHTTP)).payload
    extra = json.loads(parse_qs(urlsplit(uri).query)["extra"][0])
    assert extra["xPaddingBytes"] == "100-200"
    assert extra["xmux"] == XHTTP_XMUX


# --- Hysteria2 (AC-QR-5) ------------------------------------------------------

def test_hy2_uri_uses_hysteria2_scheme_and_roundtrips():
    result = builders.build_payload(_resolved(
        "asv2-hy2", HY2_LINK, models.Proto.HY, os_name="linux"))
    uri = result.payload

    assert uri.startswith("hysteria2://FIXTURE_PW_HY2@203.0.113.20:443/?")
    assert uri.endswith("#asv2-hy2-linux")

    parsed = links.parse_link(uri)
    assert parsed.protocol == links.PROTOCOL_HYSTERIA2
    assert parsed.credential == "FIXTURE_PW_HY2"
    assert parsed.params["sni"] == "asv2.example.invalid"
    assert parsed.params["insecure"] == "0"


def test_hy2_special_chars_obfs_mport_insecure_roundtrip():
    password = "p@ss:w/rd#1?"
    obfs_password = "obfs:pa$$/word"
    from urllib.parse import quote
    link = (f"hysteria2://{quote(password, safe='')}@203.0.113.20:443/"
            f"?sni=asv2.example.invalid&obfs=salamander"
            f"&obfs-password={quote(obfs_password, safe='')}"
            f"&mport=20000-20500&insecure=1#hy2")
    uri = builders.build_payload(_resolved(
        "hy2", link, models.Proto.HY)).payload

    parsed = links.parse_link(uri)
    assert parsed.credential == password
    assert parsed.params["obfs"] == "salamander"
    assert parsed.params["obfs-password"] == obfs_password
    assert parsed.params["mport"] == "20000-20500"
    assert parsed.params["insecure"] == "1"


def test_mport_separator_is_normalized_to_dash():
    assert builders._normalize_mport("20000:20500") == "20000-20500"
    assert builders._normalize_mport(" 20000-20500 ") == "20000-20500"
    assert builders._normalize_mport("") == ""


# --- диспетчер ----------------------------------------------------------------

@pytest.mark.parametrize("proto,link_text", [
    (models.Proto.REALITY, REALITY_LINK),
    (models.Proto.XHTTP, XHTTP_LINK),
    (models.Proto.HY, HY2_LINK),
])
def test_builders_are_uri_format(proto, link_text):
    result = builders.build_payload(_resolved("p", link_text, proto))
    assert result.payload_format == "uri"
    assert result.payload_sha256.startswith("sha256:")
    assert len(result.payload_sha256) == len("sha256:") + 64
