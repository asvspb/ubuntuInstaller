"""Data model of the scout: one candidate route and one probe outcome.

Everything here is a plain frozen dataclass: no I/O, no side effects, so the
scoring matrix (``rank.py``) and the tests can build candidates and results
without touching the filesystem or a socket.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# --- routes and protocols -----------------------------------------------------

ROUTE_DIRECT = "direct"
ROUTE_RELAY = "relay"
ROUTES = (ROUTE_DIRECT, ROUTE_RELAY)

#: Scout protocol ids (the ``proto`` field of :class:`Candidate`).  They are
#: deliberately **not** the profile-store protocol strings: the matrix of
#: WP-S3 speaks ``awg|hy2|xray-reality|xray-xhttp|xray-cdn``.
PROTO_AWG = "awg"
PROTO_HY2 = "hy2"
PROTO_XRAY_REALITY = "xray-reality"
PROTO_XRAY_XHTTP = "xray-xhttp"
PROTO_XRAY_CDN = "xray-cdn"
PROTOS = (PROTO_AWG, PROTO_HY2, PROTO_XRAY_REALITY, PROTO_XRAY_XHTTP,
          PROTO_XRAY_CDN)

#: Protocols that travel over TCP/TLS (the ones a TLS filter can break).
TLS_PROTOS = (PROTO_XRAY_REALITY, PROTO_XRAY_XHTTP, PROTO_XRAY_CDN)


@dataclass(frozen=True)
class Candidate:
    """One route the client could try.

    ``route``  — ``direct`` (client → VDS) or ``relay`` (client → RU-relay →
    exit node); ``proto`` — see :data:`PROTOS`; ``profile`` — the store profile
    name (``None`` only for synthetic candidates); ``host``/``port`` — the
    real endpoint; ``sni`` — the TLS SNI/Host used by the transport;
    ``port_hop_range`` — Hysteria2 ``mport`` range (``'20000-20500'``), if the
    link carries one.

    The profile name is kept as a **discovery hint** only; no secret from the
    share link (uuid/password/obfs-password) is ever copied into a
    :class:`Candidate`.
    """

    route: str
    proto: str
    profile: Optional[str]
    host: str
    port: int
    sni: Optional[str] = None
    port_hop_range: Optional[str] = None
    #: Extra non-secret context for the report (e.g. ``{"mport": "..."}``).
    notes: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable identity of a candidate inside one scout run."""
        return f"{self.route}:{self.proto}:{self.host}:{self.port}"

    @property
    def target(self) -> str:
        return f"{self.host}:{self.port}"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "route": self.route,
            "proto": self.proto,
            "profile": self.profile,
            "host": self.host,
            "port": self.port,
            "sni": self.sni,
            "port_hop_range": self.port_hop_range,
        }


# --- probe outcome statuses ---------------------------------------------------

STATUS_OK = "ok"
STATUS_REACHABLE = "reachable"      # AWG: no ICMP answer (it never speaks first)
STATUS_REFUSED = "refused"
STATUS_TIMEOUT = "timeout"
STATUS_SILENCE = "silence"          # UDP: no answer at all (ambiguous)
STATUS_UDP_REFUSED = "udp_refused"  # ICMP unreachable — a hard negative
STATUS_NO_ROUTE = "no_route"        # ENETUNREACH/EHOSTUNREACH, no IPv6 back
STATUS_DNS_ERROR = "dns_error"
STATUS_TLS_ERROR = "tls_error"
STATUS_ERROR = "error"
STATUS_BUDGET = "budget"            # whole-run deadline hit before start
STATUS_TSPU_TLS_FILTER = "TSPU_TLS_FILTER"
STATUS_SERVER_LOCKDOWN = "SERVER_LOCKDOWN"
STATUS_RECENT_UDP_BLACKHOLE = "RECENT_UDP_BLACKHOLE"

#: Statuses that prove the route is not usable right now (score → 0).
#: ``RECENT_UDP_BLACKHOLE`` is deliberately **not** here: it is a strong
#: negative from history, not a failed probe of the current attempt, and the
#: WP-S3 matrix applies its own −60 penalty to it.
HARD_FAIL_STATUSES = (
    STATUS_REFUSED,
    STATUS_TIMEOUT,
    STATUS_UDP_REFUSED,
    STATUS_NO_ROUTE,
    STATUS_DNS_ERROR,
    STATUS_TLS_ERROR,
    STATUS_ERROR,
    STATUS_BUDGET,
    STATUS_TSPU_TLS_FILTER,
    STATUS_SERVER_LOCKDOWN,
)


@dataclass(frozen=True)
class ProbeResult:
    """What one probe observed — never an interpretation of the whole route.

    ``rtt_ms`` is the TCP connect RTT for TLS probes and the UDP round-trip
    for the ones that got an answer; ``None`` when nothing was measured.
    ``detail`` is a short human-readable explanation and never contains a
    secret or a third-party IP.
    """

    status: str
    rtt_ms: Optional[float] = None
    detail: Optional[str] = None

    @property
    def hard_fail(self) -> bool:
        return self.status in HARD_FAIL_STATUSES

    def as_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "rtt_ms": self.rtt_ms,
                "detail": self.detail}
