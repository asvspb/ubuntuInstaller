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
        link = (f"vless://{UUID_FIXTURE}@203.0.113.10:443"
                "?security=tls&network=ws&path=/x")
        with pytest.raises(LinkError, match="unsupported network"):
            parse_link(link)
