import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm.links import ProfileLink  # noqa: E402
from vpnm.xclient import (  # noqa: E402
    hysteria2_config,
    xray_config,
    generate,
)

FIXTURE_PW_HY2 = "FIXTURE_PW_hy2_demo_only"
UUID_FIXTURE = "11111111-2222-3333-4444-555555555555"
PBK_FIXTURE = "FIXTURE_PBK_base64url_demo_x9y8z7"


def reality_link():
    return ProfileLink(
        protocol="xray-reality", host="203.0.113.10", port=443,
        credential=UUID_FIXTURE, name="Reality",
        params={"security": "reality", "sni": "example-test.invalid",
                "fp": "chrome", "pbk": PBK_FIXTURE, "sid": "a1b2c3d4",
                "flow": "xtls-rprx-vision"},
    )


def xhttp_link():
    return ProfileLink(
        protocol="xray-xhttp", host="203.0.113.20", port=8443,
        credential=UUID_FIXTURE, name="XHTTP",
        params={"security": "tls", "network": "xhttp",
                "sni": "example-test.invalid", "path": "/xhttp/route",
                "fp": "chrome"},
    )


def hy2_link(insecure="0"):
    return ProfileLink(
        protocol="hysteria2", host="203.0.113.30", port=443,
        credential=FIXTURE_PW_HY2, name="Hy2",
        params={"sni": "example-test.invalid", "insecure": insecure},
    )


def test_reality_json_valid_and_complete():
    cfg = json.loads(xray_config(reality_link(), 2080))
    inbound = cfg["inbounds"][0]
    assert inbound["listen"] == "127.0.0.1"
    assert inbound["port"] == 2080
    assert inbound["protocol"] == "socks"
    out = cfg["outbounds"][0]
    assert out["protocol"] == "vless"
    user = out["settings"]["vnext"][0]["users"][0]
    assert user["id"] == UUID_FIXTURE
    assert user["flow"] == "xtls-rprx-vision"
    ss = out["streamSettings"]
    assert ss["network"] == "tcp"
    assert ss["security"] == "reality"
    rs = ss["realitySettings"]
    assert rs["publicKey"] == PBK_FIXTURE
    assert rs["serverName"] == "example-test.invalid"
    assert rs["shortId"] == "a1b2c3d4"
    assert rs["fingerprint"] == "chrome"


def test_xhttp_json_contains_path():
    cfg = json.loads(xray_config(xhttp_link(), 2084))
    ss = cfg["outbounds"][0]["streamSettings"]
    assert ss["network"] == "xhttp"
    assert ss["security"] == "tls"
    assert ss["tlsSettings"]["serverName"] == "example-test.invalid"
    assert ss["xhttpSettings"]["path"] == "/xhttp/route"
    assert cfg["inbounds"][0]["port"] == 2084


def test_hy2_yaml_contains_required_fields():
    yaml_text = hysteria2_config(hy2_link(), 2083)
    assert "server: 203.0.113.30:443" in yaml_text
    assert f"auth: {FIXTURE_PW_HY2}" in yaml_text
    assert "sni: example-test.invalid" in yaml_text
    assert "listen: 127.0.0.1:2083" in yaml_text
    assert "up: 20 mbps" in yaml_text
    assert "down: 50 mbps" in yaml_text


def test_hy2_insecure_flag_passthrough():
    secure = hysteria2_config(hy2_link(insecure="0"), 2083)
    assert "insecure" not in secure
    insecure_cfg = hysteria2_config(hy2_link(insecure="1"), 2083)
    assert "insecure: true" in insecure_cfg


def hy2_hop_link(mport="20000-20500", obfs=None):
    params = {"sni": "example-test.invalid", "insecure": "1", "mport": mport}
    if obfs:
        params.update({"obfs": "salamander", "obfs-password": obfs})
    return ProfileLink(
        protocol="hysteria2", host="203.0.113.30", port=443,
        credential=FIXTURE_PW_HY2, name="Hy2 hop", params=params,
    )


def test_hy2_mport_renders_multi_port_server_and_hop_interval():
    cfg = hysteria2_config(hy2_hop_link("20000-20500"), 2083)
    assert "server: 203.0.113.30:443,20000-20500" in cfg
    assert "hopInterval: 30s" in cfg


def test_hy2_mport_list_rendered_verbatim():
    cfg = hysteria2_config(hy2_hop_link("20000-20500,30000"), 2083)
    assert "server: 203.0.113.30:443,20000-20500,30000" in cfg


def test_hy2_without_mport_has_no_transport_block():
    cfg = hysteria2_config(hy2_link(insecure="1"), 2083)
    assert "server: 203.0.113.30:443" in cfg
    assert "hopInterval" not in cfg
    assert "transport:" not in cfg


def test_hy2_obfs_block_rendered():
    cfg = hysteria2_config(hy2_hop_link(obfs="FIXTURE_PW_obfs_only"), 2083)
    assert "obfs:" in cfg
    assert "  type: salamander" in cfg
    assert "  salamander:" in cfg
    assert '    password: "FIXTURE_PW_obfs_only"' in cfg


def test_generate_dispatch(tmp_path):
    assert json.loads(generate(reality_link(), 2080))["outbounds"][0]["protocol"] == "vless"
    assert "server:" in generate(hy2_link(), 2083)
