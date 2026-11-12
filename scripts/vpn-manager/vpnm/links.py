"""Share-link parsing for Xray Reality / XHTTP and Hysteria2 links.

Each parser returns a :class:`ProfileLink` with protocol, host, port, the
credential (uuid for vless, password for hysteria2) and the transport params
needed later by the config generator. All values are percent-decoded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict
from urllib.parse import unquote, parse_qs

PROTOCOL_XRAY_REALITY = "xray-reality"
PROTOCOL_XRAY_XHTTP = "xray-xhttp"
PROTOCOL_XRAY_WS = "xray-cdn-ws"
PROTOCOL_HYSTERIA2 = "hysteria2"

# Hysteria2 port hopping (SPEC-20261112 WP-1): `mport` в ссылке — диапазон
# портов, по которым клиент прыгает ('20000-20500'), либо их список
# ('20000-20500,30000'). Клиентский конфиг получает multi-port адрес
# `host:443,20000-20500` (см. xclient.hysteria2_config).
HYSTERIA2_OBFS_SALAMANDER = "salamander"
_MPORT_RE = re.compile(r"^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$")


class LinkError(ValueError):
    """Raised on malformed or unsupported share links."""


@dataclass
class ProfileLink:
    """Parsed representation of a share link."""

    protocol: str
    host: str
    port: int
    credential: str  # uuid (vless) or password (hysteria2)
    name: str  # percent-decoded fragment
    params: Dict[str, str] = field(default_factory=dict)


def _parse_qs_single(query: str) -> Dict[str, str]:
    qs = parse_qs(query, keep_blank_values=True)
    out: Dict[str, str] = {}
    for key, values in qs.items():
        # keep the first occurrence of each key, already URL-decoded by parse_qs
        out[key] = values[0]
    return out


def _require(mapping: Dict[str, str], key: str, ctx: str) -> str:
    value = mapping.get(key, "")
    if not value:
        raise LinkError(f"{ctx}: missing required parameter '{key}'")
    return value


def _parse_vless(link: str) -> ProfileLink:
    rest = link[len("vless://"):]
    # fragment = display name (percent-encoded)
    if "#" in rest:
        rest, fragment = rest.split("#", 1)
        name = unquote(fragment)
    else:
        name = ""
    if "?" in rest:
        userinfo_host, query = rest.split("?", 1)
        params = _parse_qs_single(query)
    else:
        userinfo_host, params = rest, {}

    if "@" not in userinfo_host:
        raise LinkError("vless link: missing 'uuid@host:port' part")
    userinfo, hostport = userinfo_host.rsplit("@", 1)
    uuid = unquote(userinfo)
    if not uuid:
        raise LinkError("vless link: empty user id")
    try:
        host, port_s = hostport.rsplit(":", 1)
    except ValueError as exc:
        raise LinkError(f"vless link: bad host:port '{hostport}'") from exc
    if not port_s.isdigit():
        raise LinkError(f"vless link: bad port '{port_s}'")
    host = unquote(host)

    security = params.get("security", "")
    if security == "reality":
        for req in ("sni", "pbk", "sid"):
            _require(params, req, "vless reality link")
        flow = params.get("flow", "")
        if flow and flow != "xtls-rprx-vision":
            raise LinkError(
                f"vless reality link: unsupported flow '{flow}' "
                "(only 'xtls-rprx-vision' is supported)"
            )
        proto = PROTOCOL_XRAY_REALITY
    elif security == "tls":
        network = params.get("network", params.get("type", ""))
        if network == "xhttp":
            _require(params, "path", "vless xhttp link")
            proto = PROTOCOL_XRAY_XHTTP
        elif network == "ws":
            # CDN-transport (ADR-006): vless+ws+tls за Cloudflare; path/host
            # обязательны (server рендерит path /cdnws, Host = cdn-домен)
            _require(params, "path", "vless ws link")
            proto = PROTOCOL_XRAY_WS
        else:
            raise LinkError(
                f"vless tls link: unsupported network '{network}' "
                "(supported: 'xhttp', 'ws')"
            )
    else:
        raise LinkError(
            f"vless link: unsupported security '{security}' "
            "(supported: reality, tls+network=xhttp|ws)"
        )

    return ProfileLink(protocol=proto, host=host, port=int(port_s),
                       credential=uuid, name=name, params=params)


def _validate_mport(mport: str) -> str:
    """Validate a Hysteria2 hop range ('20000-20500' / 'a-b,c') and return it.

    The client renders the range into a multi-port server address, so a
    malformed value would produce an unrunnable config — fail at import time
    with a readable error instead.
    """
    value = mport.strip()
    if not _MPORT_RE.match(value):
        raise LinkError(
            f"hysteria2 link: bad mport '{mport}' "
            "(expected '20000-20500' or '20000-20500,30000')")
    for part in value.split(","):
        lo_s, _, hi_s = part.partition("-")
        lo = int(lo_s)
        hi = int(hi_s) if hi_s else lo
        if not (1 <= lo <= 65535 and 1 <= hi <= 65535 and lo <= hi):
            raise LinkError(f"hysteria2 link: mport '{part}' out of range "
                            "(1-65535, start <= end)")
    return value


def _parse_hysteria2(link: str) -> ProfileLink:
    rest = link[len("hysteria2://"):]
    if "#" in rest:
        rest, fragment = rest.split("#", 1)
        name = unquote(fragment)
    else:
        name = ""
    if "?" in rest:
        userinfo_host, query = rest.split("?", 1)
        params = _parse_qs_single(query)
    else:
        userinfo_host, params = rest, {}

    if "@" not in userinfo_host:
        raise LinkError("hysteria2 link: missing 'password@host:port' part")
    userinfo_host = userinfo_host.rstrip("/")
    userinfo, hostport = userinfo_host.rsplit("@", 1)
    password = unquote(userinfo)
    if not password:
        raise LinkError("hysteria2 link: empty password")
    try:
        host, port_s = hostport.rsplit(":", 1)
    except ValueError as exc:
        raise LinkError(f"hysteria2 link: bad host:port '{hostport}'") from exc
    if not port_s.isdigit():
        raise LinkError(f"hysteria2 link: bad port '{port_s}'")
    host = unquote(host).rstrip("/")

    insecure = params.get("insecure", "") in ("1", "true")

    # obfs salamander (SPEC-20261112 WP-1): неизвестный тип обфускации не
    # умеем рендерить — лучше явная ошибка, чем «тихо без обфускации».
    obfs = params.get("obfs", "").strip()
    if obfs and obfs != HYSTERIA2_OBFS_SALAMANDER:
        raise LinkError(
            f"hysteria2 link: unsupported obfs '{obfs}' "
            f"(supported: {HYSTERIA2_OBFS_SALAMANDER})")
    if obfs and not params.get("obfs-password"):
        raise LinkError("hysteria2 link: 'obfs' given without 'obfs-password'")

    mport = params.get("mport", "").strip()
    if mport:
        mport = _validate_mport(mport)

    merged = {**params, "insecure": "1" if insecure else "0"}
    if mport:
        merged["mport"] = mport

    return ProfileLink(protocol=PROTOCOL_HYSTERIA2, host=host, port=int(port_s),
                       credential=password, name=name, params=merged)


def parse_link(link: str) -> ProfileLink:
    """Parse a share link, returning a :class:`ProfileLink`.

    Raises :class:`LinkError` (a ValueError subclass) on malformed or
    unsupported input.
    """
    link = link.strip()
    if not link:
        raise LinkError("empty share link")
    if link.startswith("vless://"):
        return _parse_vless(link)
    if link.startswith("hysteria2://"):
        return _parse_hysteria2(link)
    scheme = link.split("://", 1)[0] if "://" in link else link
    raise LinkError(f"unsupported share link scheme '{scheme}' "
                    "(supported: vless://, hysteria2://)")
