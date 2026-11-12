import json
import sys
from pathlib import Path

import pytest

# make the vpnm package importable when tests run from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm.links import LinkError, parse_link  # noqa: E402

# Fixture secrets per AGENTS-style invariant: FIXTURE_PW_* prefixes,
# RFC 5737 IPs and reserved UUIDs. Not real credentials.
FIXTURE_PW_HY2 = "FIXTURE_PW_hy2_demo_only"
UUID_FIXTURE = "11111111-2222-3333-4444-555555555555"
PBK_FIXTURE = "FIXTURE_PBK_base64url_demo_x9y8z7"
SID_FIXTURE = "a1b2c3d4"


REALITY_LINK = (
    f"vless://{UUID_FIXTURE}@203.0.113.10:443"
    f"?security=reality&sni=example-test.invalid&fp=chrome"
    f"&pbk={PBK_FIXTURE}&sid={SID_FIXTURE}&flow=xtls-rprx-vision"
    "#Reality%20%D0%A2%D0%B5%D1%81%D1%82"
)

XHTTP_LINK = (
    f"vless://{UUID_FIXTURE}@203.0.113.20:8443"
    f"?security=tls&network=xhttp&sni=example-test.invalid"
    f"&path=%2Fxhttp%2Froute&type=xhttp#XHTTP"
)

HY2_LINK = (
    f"hysteria2://{FIXTURE_PW_HY2}@203.0.113.30:443/"
    f"?sni=example-test.invalid&insecure=1#Hy2"
)


class TestReality:
    def test_parse_reality(self):
        p = parse_link(REALITY_LINK)
        assert p.protocol == "xray-reality"
        assert p.host == "203.0.113.10"
        assert p.port == 443
        assert p.credential == UUID_FIXTURE
        assert p.name == "Reality Тест"
        assert p.params["sni"] == "example-test.invalid"
        assert p.params["pbk"] == PBK_FIXTURE
        assert p.params["sid"] == SID_FIXTURE
        assert p.params["flow"] == "xtls-rprx-vision"

    def test_percent_encoded_name_decoded(self):
        p = parse_link(REALITY_LINK)
        assert p.name == "Reality Тест"
        # name with plain space encoded as %20 or literal space must decode too
        p2 = parse_link(REALITY_LINK.replace("%20", " "))
        assert p2.name == "Reality Тест"


class TestXhttp:
    def test_parse_xhttp(self):
        p = parse_link(XHTTP_LINK)
        assert p.protocol == "xray-xhttp"
        assert p.host == "203.0.113.20"
        assert p.port == 8443
        assert p.credential == UUID_FIXTURE
        assert p.name == "XHTTP"
        assert p.params["network"] == "xhttp"
        assert p.params["path"] == "/xhttp/route"
        assert p.params["sni"] == "example-test.invalid"


class TestHysteria2:
    def test_parse_hy2(self):
        p = parse_link(HY2_LINK)
        assert p.protocol == "hysteria2"
        assert p.host == "203.0.113.30"
        assert p.port == 443
        assert p.credential == FIXTURE_PW_HY2
        assert p.name == "Hy2"
        assert p.params["sni"] == "example-test.invalid"
        assert p.params["insecure"] == "1"

    def test_hy2_without_insecure(self):
        link = f"hysteria2://{FIXTURE_PW_HY2}@203.0.113.30:443/?sni=example-test.invalid"
        p = parse_link(link)
        assert p.params["insecure"] == "0"

    def test_hy2_port_hopping_mport_kept(self):
        link = (f"hysteria2://{FIXTURE_PW_HY2}@203.0.113.30:443/"
                "?sni=example-test.invalid&insecure=1&mport=20000-20500#hop")
        p = parse_link(link)
        assert p.params["mport"] == "20000-20500"

    def test_hy2_mport_list_kept(self):
        link = (f"hysteria2://{FIXTURE_PW_HY2}@203.0.113.30:443/"
                "?mport=20000-20500,30000#hop")
        assert parse_link(link).params["mport"] == "20000-20500,30000"

    def test_hy2_bad_mport_raises(self):
        for bad in ("abc", "20000-", "30000-20000", "0-100", "70000"):
            link = (f"hysteria2://{FIXTURE_PW_HY2}@203.0.113.30:443/"
                    f"?mport={bad}")
            with pytest.raises(LinkError, match="mport"):
                parse_link(link)

    def test_hy2_obfs_salamander_kept(self):
        link = (f"hysteria2://{FIXTURE_PW_HY2}@203.0.113.30:443/"
                "?sni=example-test.invalid&insecure=1"
                "&obfs=salamander&obfs-password=FIXTURE_PW_obfs_only#obfs")
        p = parse_link(link)
        assert p.params["obfs"] == "salamander"
        assert p.params["obfs-password"] == "FIXTURE_PW_obfs_only"

    def test_hy2_unknown_obfs_raises(self):
        link = (f"hysteria2://{FIXTURE_PW_HY2}@203.0.113.30:443/"
                "?obfs=none&obfs-password=x")
        with pytest.raises(LinkError, match="unsupported obfs"):
            parse_link(link)

    def test_hy2_obfs_without_password_raises(self):
        link = (f"hysteria2://{FIXTURE_PW_HY2}@203.0.113.30:443/"
                "?obfs=salamander")
        with pytest.raises(LinkError, match="obfs-password"):
            parse_link(link)


class TestErrors:
    def test_empty_link(self):
        with pytest.raises(LinkError):
            parse_link("")

    def test_unknown_scheme(self):
        with pytest.raises(LinkError, match="unsupported share link scheme"):
            parse_link("vmess://whatever")

    def test_unknown_security(self):
        link = f"vless://{UUID_FIXTURE}@203.0.113.10:443?security=grpc&sni=x"
        with pytest.raises(LinkError, match="unsupported security"):
            parse_link(link)

    def test_unknown_transport_on_tls(self):
        # ws с 2026-11-11 поддержан (CDN/ADR-006); неизвестный = grpc
        link = (f"vless://{UUID_FIXTURE}@203.0.113.10:443"
                "?security=tls&network=grpc&path=/x")
        with pytest.raises(LinkError, match="unsupported network"):
            parse_link(link)


# --- vless+ws (CDN transport, ADR-006) ---------------------------------------------

WS_LINK = ("vless://11111111-2222-3333-4444-555555555555@asvpersonal.ru:2053"
           "?encryption=none&security=tls&sni=asvpersonal.ru&type=ws"
           "&host=asvpersonal.ru&path=%2Fcdnws#cdn-home")


def test_parse_vless_ws():
    from vpnm.links import parse_link, PROTOCOL_XRAY_WS
    link = parse_link(WS_LINK)
    assert link.protocol == PROTOCOL_XRAY_WS
    assert link.host == "asvpersonal.ru" and link.port == 2053
    assert link.params["path"] == "/cdnws"
    assert link.params["host"] == "asvpersonal.ru"


def test_parse_vless_ws_missing_path_raises():
    from vpnm.links import parse_link, LinkError
    import pytest
    bad = WS_LINK.replace("&path=%2Fcdnws", "")
    with pytest.raises(LinkError):
        parse_link(bad)


def test_xray_config_ws_stream():
    import json
    from vpnm.links import parse_link
    from vpnm.xclient import generate
    cfg = json.loads(generate(parse_link(WS_LINK), 2081))
    ss = cfg["outbounds"][0]["streamSettings"]
    assert ss["network"] == "ws"
    assert ss["security"] == "tls"
    assert ss["wsSettings"]["path"] == "/cdnws"
    assert ss["wsSettings"]["headers"]["Host"] == "asvpersonal.ru"
    assert cfg["inbounds"][0]["port"] == 2081


def test_ws_default_port_2081():
    from vpnm.links import parse_link, PROTOCOL_XRAY_WS
    from vpnm.profiles import DEFAULT_SOCKS_PORT
    assert DEFAULT_SOCKS_PORT[PROTOCOL_XRAY_WS] == 2081
