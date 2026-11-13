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
    HYSTERIA2_OBFS_SALAMANDER,
    PROTOCOL_HYSTERIA2,
    PROTOCOL_XRAY_REALITY,
    PROTOCOL_XRAY_XHTTP,
    PROTOCOL_XRAY_WS,
    ProfileLink,
)

# Интервал смены порта при хоппинге (Hysteria2: минимум 5s, дефолт 30s).
HY2_HOP_INTERVAL = "30s"

# Mux (multiplexing) применяется только к WS-профилю (CDN через Cloudflare):
# Vision несовместим с Mux, XHTTP мультиплексирует сам (SPEC-20261113).
MUX_CONCURRENCY = 8


def _hysteria2_server(link: ProfileLink) -> str:
    """Адрес сервера: обычный `host:port` либо multi-port `host:443,20000-20500`.

    Hysteria2 клиент принимает список портов и диапазонов в поле `server` и
    периодически переключается между ними; базовый порт ссылки остаётся в
    наборе (сервер слушает именно его, диапазон редиректится nft/iptables).
    """
    mport = (link.params.get("mport") or "").strip()
    if not mport:
        return f"{link.host}:{link.port}"
    ports = [p for p in mport.split(",") if p]
    base = str(link.port)
    if base not in ports:
        ports.insert(0, base)
    return f"{link.host}:{','.join(ports)}"


def xray_config(link: ProfileLink, socks_port: int, mux: bool = False) -> str:
    """Generate a JSON config string for an xray client (reality/xhttp/ws).

    ``mux=True`` добавляет блок ``mux`` в outbound только для WS-профиля
    (PROTOCOL_XRAY_WS); для Reality/XHTTP блок не появляется ни при каких
    флагах.
    """
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
    elif link.protocol == PROTOCOL_XRAY_WS:
        sni = link.params.get("sni", link.host)
        stream = {
            "network": "ws",
            "security": "tls",
            "tlsSettings": {
                "serverName": sni,
                "fingerprint": link.params.get("fp", "chrome"),
                "allowInsecure": False,
            },
            "wsSettings": {
                "path": link.params.get("path", "/"),
                "headers": {"Host": link.params.get("host", sni)},
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
    if mux and link.protocol == PROTOCOL_XRAY_WS:
        outbound["mux"] = {"enabled": True, "concurrency": MUX_CONCURRENCY}
    return json.dumps({"inbounds": [inbound], "outbounds": [outbound]},
                      indent=2, ensure_ascii=False)


def hysteria2_config(link: ProfileLink, socks_port: int,
                     up_mbps: int = 20, down_mbps: int = 50) -> str:
    """Generate a YAML config string for a hysteria2 client (fixed template).

    Ссылка может нести port hopping (`mport='20000-20500'`) и obfs salamander
    (`obfs=salamander&obfs-password=...`) — серверные инстансы из vds
    (SPEC-20261112 WP-1). Хоппинг выражается multi-port адресом сервера
    (`host:443,20000-20500`, см. Hysteria2 docs «Port Hopping»), интервал —
    `transport.udp.hopInterval`; obfs — блоком `obfs: salamander`.
    """
    insecure = link.params.get("insecure") == "1"
    sni = link.params.get("sni", link.host)
    server = _hysteria2_server(link)
    lines = [
        f"server: {server}",
        "",
        f"auth: {link.credential}",
        "",
        "tls:",
        f"  sni: {sni}",
    ]
    if insecure:
        lines.append("  insecure: true")
    obfs = link.params.get("obfs", "")
    obfs_password = link.params.get("obfs-password", "")
    if obfs == HYSTERIA2_OBFS_SALAMANDER and obfs_password:
        lines += [
            "",
            "obfs:",
            f"  type: {HYSTERIA2_OBFS_SALAMANDER}",
            "  salamander:",
            f"    password: {json.dumps(obfs_password)}",
        ]
    lines += [
        "",
        "socks5:",
        f"  listen: 127.0.0.1:{int(socks_port)}",
        "",
        "bandwidth:",
        f"  up: {up_mbps} mbps",
        f"  down: {down_mbps} mbps",
    ]
    if link.params.get("mport"):
        lines += [
            "",
            "transport:",
            "  udp:",
            f"    hopInterval: {HY2_HOP_INTERVAL}",
        ]
    return "\n".join(lines) + "\n"


def generate(link: ProfileLink, socks_port: int, mux: bool = False) -> str:
    """Dispatch to the right generator by protocol."""
    if link.protocol in (PROTOCOL_XRAY_REALITY, PROTOCOL_XRAY_XHTTP,
                         PROTOCOL_XRAY_WS):
        return xray_config(link, socks_port, mux=mux)
    if link.protocol == PROTOCOL_HYSTERIA2:
        return hysteria2_config(link, socks_port)
    raise ValueError(f"generate: unknown protocol '{link.protocol}'")
