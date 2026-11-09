"""Client config generators built from stored profiles.

- xray (reality / xhttp): a JSON dict matching the real Xray client layout
  (socks inbound on 127.0.0.1:<socks_port>, one vless outbound).
- hysteria2: hand-rolled YAML string from a fixed template (no external
  dependency). Credentials appear only in the returned config text, never in
  logs.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from .links import (
    PROTOCOL_HYSTERIA2,
    PROTOCOL_XRAY_REALITY,
    PROTOCOL_XRAY_XHTTP,
    ProfileLink,
)


def xray_config(link: ProfileLink, socks_port: int) -> str:
    """Generate a JSON config string for an xray client (reality or xhttp)."""
    inbound: Dict[str, Any] = {
        "listen": "127.0.0.1",
        "port": int(socks_port),
        "protocol": "socks",
        "settings": {"auth": "noauth", "udp": False},
    }
    users: Dict[str, Any] = {
        "id": link.credential,
        "encryption": "none",
        "flow": link.params.get("flow", ""),
    }
    if link.protocol == PROTOCOL_XRAY_REALITY:
        stream: Dict[str, Any] = {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "serverName": link.params.get("sni", ""),
                "fingerprint": link.params.get("fp", "chrome"),
                "publicKey": link.params.get("pbk", ""),
                "shortId": link.params.get("sid", ""),
                "spiderX": link.params.get("spath", ""),
            },
        }
    elif link.protocol == PROTOCOL_XRAY_XHTTP:
        sni = link.params.get("sni", link.host)
        stream = {
            "network": "xhttp",
            "security": "tls",
            "tlsSettings": {
                "serverName": sni,
                "fingerprint": link.params.get("fp", "chrome"),
                "allowInsecure": False,
            },
            "xhttpSettings": {
                "path": link.params.get("path", "/"),
                "host": link.params.get("host", sni),
            },
        }
    else:
        raise ValueError(f"xray_config: unexpected protocol '{link.protocol}'")
    outbound: Dict[str, Any] = {
        "protocol": "vless",
        "settings": {
            "vnext": [{
                "address": link.host,
                "port": link.port,
                "users": [users],
            }],
        },
        "streamSettings": stream,
    }
    return json.dumps({"inbounds": [inbound], "outbounds": [outbound]},
                      indent=2, ensure_ascii=False)


def hysteria2_config(link: ProfileLink, socks_port: int,
                     up_mbps: int = 20, down_mbps: int = 50) -> str:
    """Generate a YAML config string for a hysteria2 client (fixed template)."""
    insecure = link.params.get("insecure") == "1"
    sni = link.params.get("sni", link.host)
    lines = [
        f"server: {link.host}:{link.port}",
        "",
        f"auth: {link.credential}",
        "",
        "tls:",
        f"  sni: {sni}",
    ]
    if insecure:
        lines.append("  insecure: true")
    lines += [
        "",
        "socks5:",
        f"  listen: 127.0.0.1:{int(socks_port)}",
        "",
        "bandwidth:",
        f"  up: {up_mbps} mbps",
        f"  down: {down_mbps} mbps",
    ]
    return "\n".join(lines) + "\n"


def generate(link: ProfileLink, socks_port: int) -> str:
    """Dispatch to the right generator by protocol."""
    if link.protocol in (PROTOCOL_XRAY_REALITY, PROTOCOL_XRAY_XHTTP):
        return xray_config(link, socks_port)
    if link.protocol == PROTOCOL_HYSTERIA2:
        return hysteria2_config(link, socks_port)
    raise ValueError(f"generate: unknown protocol '{link.protocol}'")
