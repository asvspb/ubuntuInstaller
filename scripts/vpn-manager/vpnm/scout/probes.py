"""WP-S2: bounded, read-only active probes.

Three primitives, all unprivileged and all on a **finite budget**:

- :func:`probe_tcp_tls` — TCP connect, then a TLS 1.3 handshake with the real
  SNI of the profile.  ``refused`` (RST on connect), ``timeout`` (no SYN-ACK)
  and ``TSPU_TLS_FILTER`` (RST or a hanging ClientHello *after* the connection
  was established) are different observations and stay different;
- :func:`probe_quic_udp` — a QUIC Initial-shaped datagram of ≥ 1200 bytes
  with a random DCID, then a wait for a QUIC answer.  ICMP port unreachable
  is a hard negative, complete silence is an *unknown* (reported as
  ``silence``), never "blocked";
- :func:`probe_awg_reachability` — AmneziaWG never answers unauthenticated
  packets, so the probe measures UDP reachability (is there an ICMP
  unreachable?) and adds the G-04 history of the last 30 minutes
  (``RECENT_UDP_BLACKHOLE``).

If the server verdict is fresh and ``lockdown.active`` is set, a **direct
xray on TCP 443** is reported as ``SERVER_LOCKDOWN`` **without opening a
socket**: probing the very port the server just locked would manufacture
load and confuse the observation.

Honest limitation of the QUIC probe: without the server's AEAD keys a
datagram can be *shaped* like an Initial but cannot be decrypted, so a real
server will not answer it.  The probe therefore measures UDP reachability
(ICMP unreachable vs silence), which is exactly what the WP asks for; it
does not claim "Hysteria2 works".

Concurrency: :func:`probe_all` uses ``ThreadPoolExecutor(max_workers=8)``,
a per-socket timeout of 1.5 s and one shared budget of 2500 ms; the timeout
handed to each socket is clamped to what is left of the budget, so four
hanging sockets still finish the whole run in ~1.5 s.
"""

from __future__ import annotations

import errno
import os
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Dict, Optional, Sequence, Tuple

from .models import (
    PROTO_AWG,
    PROTO_HY2,
    ROUTE_DIRECT,
    TLS_PROTOS,
    Candidate,
    ProbeResult,
    STATUS_BUDGET,
    STATUS_DNS_ERROR,
    STATUS_ERROR,
    STATUS_NO_ROUTE,
    STATUS_OK,
    STATUS_REACHABLE,
    STATUS_RECENT_UDP_BLACKHOLE,
    STATUS_REFUSED,
    STATUS_SERVER_LOCKDOWN,
    STATUS_SILENCE,
    STATUS_TIMEOUT,
    STATUS_TLS_ERROR,
    STATUS_TSPU_TLS_FILTER,
    STATUS_UDP_REFUSED,
)

#: Per-socket timeout (WP-S2).
DEFAULT_TIMEOUT_S = 1.5
#: Whole-run budget (WP-S2); 2500 ms including every probe.
DEFAULT_BUDGET_MS = 2500
#: Worker count (WP-S2).
MAX_WORKERS = 8
#: A QUIC Initial must be at least 1200 bytes (RFC 9000 §14.1).
QUIC_MIN_INITIAL_BYTES = 1200
#: QUIC version 1.
_QUIC_VERSION = b"\x00\x00\x00\x01"
#: UDP datagram size for the AWG reachability probe (padding, not a packet).
_AWG_DATAGRAM_BYTES = 32


def _remaining(deadline: float) -> float:
    """Seconds left until ``deadline`` (never zero, so settimeout is valid)."""
    return max(0.05, deadline - time.monotonic())


def _oserror_result(exc: OSError) -> ProbeResult:
    """Map a socket-level error onto a probe status (no interpretation)."""
    code = getattr(exc, "errno", None)
    if code == errno.ECONNREFUSED:
        return ProbeResult(STATUS_REFUSED, detail="connection refused")
    if code in (errno.ECONNRESET, errno.EPIPE):
        return ProbeResult(STATUS_TSPU_TLS_FILTER,
                           detail="RST/сброс во время соединения")
    if code == errno.ETIMEDOUT:
        return ProbeResult(STATUS_TIMEOUT, detail="таймаут сокета")
    if code in (errno.ENETUNREACH, errno.EHOSTUNREACH):
        return ProbeResult(STATUS_NO_ROUTE, detail=f"нет маршрута: {exc}")
    return ProbeResult(STATUS_ERROR, detail=f"{type(exc).__name__}: {exc}")


def _tls_context() -> ssl.SSLContext:
    """Client context: TLS 1.3 only, no certificate verification.

    The probe measures *reachability of the handshake*, not the identity of
    the peer: Reality serves the real destination certificate, and a
    certificate mismatch is not a censorship signal.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    return context


# --- TCP / TLS ------------------------------------------------------------------


def probe_tcp_tls(host: str, port: int, sni: Optional[str] = None, *,
                  timeout: float = DEFAULT_TIMEOUT_S) -> ProbeResult:
    """TCP connect → TLS 1.3 handshake with ``sni`` (read-only, bounded)."""
    started = time.monotonic()
    deadline = started + max(0.05, float(timeout))
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return ProbeResult(STATUS_DNS_ERROR, detail=f"DNS: {exc}")
    if not infos:
        return ProbeResult(STATUS_DNS_ERROR, detail="DNS: пустой ответ")
    family, socktype, proto, _canon, sockaddr = infos[0]
    try:
        sock = socket.socket(family, socktype, proto)
    except OSError as exc:
        return _oserror_result(exc)
    phase = "connect"
    tls: Optional[object] = None
    try:
        sock.settimeout(_remaining(deadline))
        sock.connect(sockaddr)
        rtt_ms = round((time.monotonic() - started) * 1000.0, 1)
        phase = "handshake"
        sock.settimeout(_remaining(deadline))
        tls = _tls_context().wrap_socket(sock, server_hostname=sni or host)
        return ProbeResult(STATUS_OK, rtt_ms=rtt_ms,
                           detail="TCP+TLS handshake ok")
    except (socket.timeout, TimeoutError):
        if phase == "handshake":
            # connected, but the ClientHello never got an answer: the
            # signature of a TLS filter that swallows instead of RSTing
            return ProbeResult(STATUS_TSPU_TLS_FILTER,
                               detail="ClientHello без ответа (handshake timeout)")
        return ProbeResult(STATUS_TIMEOUT,
                           detail=f"TCP connect timeout {timeout:g}s")
    except ConnectionResetError:
        return ProbeResult(STATUS_TSPU_TLS_FILTER,
                           detail="RST после ClientHello/на connect")
    except ssl.SSLEOFError as exc:
        return ProbeResult(STATUS_TSPU_TLS_FILTER,
                           detail=f"EOF во время handshake: {exc}")
    except ssl.SSLError as exc:
        return ProbeResult(STATUS_TLS_ERROR,
                           detail=f"TLS: {getattr(exc, 'reason', None) or exc}")
    except OSError as exc:
        return _oserror_result(exc)
    finally:
        for sock_obj in (tls, sock):
            if sock_obj is None:
                continue
            try:
                sock_obj.close()
            except OSError:
                pass


# --- UDP helpers ----------------------------------------------------------------


def _udp_roundtrip(host: str, port: int, payload: bytes, *,
                   timeout: float) -> Tuple[str, Optional[float], str]:
    """Send one datagram and wait for an answer (connected UDP socket).

    Returns ``(kind, rtt_ms, detail)`` with ``kind`` in ``answered`` (any UDP
    response), ``silence`` (nothing at all) or ``icmp`` (the kernel surfaced
    an ICMP port/host unreachable).  ``dns_error``/``no_route``/``error`` are
    deterministic failures, not observations about the peer's filter.
    """
    started = time.monotonic()
    deadline = started + max(0.05, float(timeout))
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    except socket.gaierror as exc:
        return "dns_error", None, f"DNS: {exc}"
    if not infos:
        return "dns_error", None, "DNS: пустой ответ"
    family, socktype, proto, _canon, sockaddr = infos[0]
    try:
        sock = socket.socket(family, socktype, proto)
    except OSError as exc:
        return "error", None, f"{type(exc).__name__}: {exc}"
    try:
        sock.settimeout(_remaining(deadline))
        sock.connect(sockaddr)
        sock.send(payload)
        try:
            data = sock.recv(1500)
        except (socket.timeout, TimeoutError):
            return "silence", None, f"нет ответа за {timeout:g}s"
        except ConnectionRefusedError:
            return "icmp", None, "ICMP port unreachable"
        except OSError as exc:
            code = getattr(exc, "errno", None)
            if code in (errno.ECONNREFUSED, errno.EHOSTUNREACH,
                        errno.ECONNRESET):
                return "icmp", None, "ICMP unreachable"
            if code == errno.ENETUNREACH:
                return "no_route", None, f"нет маршрута: {exc}"
            return "error", None, f"{type(exc).__name__}: {exc}"
        return ("answered", round((time.monotonic() - started) * 1000.0, 1),
                f"{len(data)} байт в ответ")
    except OSError as exc:
        return "error", None, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _varint2(value: int) -> bytes:
    """QUIC two-byte varint (RFC 9000 §16); values below 16384 only."""
    if not 0 <= value < 16384:
        raise ValueError("value out of the 2-byte varint range")
    return (0x4000 | value).to_bytes(2, "big")


def build_quic_initial(dcid: Optional[bytes] = None, *,
                       scid: Optional[bytes] = None,
                       min_bytes: int = QUIC_MIN_INITIAL_BYTES) -> bytes:
    """A QUIC v1 Initial-*shaped* datagram of exactly ``min_bytes`` bytes.

    Header: long-header byte ``0xC0``, version 1, DCID (random by default),
    SCID, empty token, a 2-byte length varint that covers packet number +
    payload, then random bytes.  The payload is **not** AEAD-protected (the
    stdlib has no AEAD and the client does not have the server's keys), so a
    real server will not answer this datagram — see the module docstring.
    The shape and the ≥ 1200-byte padding are what the probe needs to send
    something a QUIC endpoint will at least parse before dropping it.
    """
    if min_bytes < 64:
        raise ValueError("a QUIC Initial needs at least 64 bytes")
    dcid_bytes = dcid if dcid is not None else os.urandom(8)
    scid_bytes = scid if scid is not None else os.urandom(8)
    if not 1 <= len(dcid_bytes) <= 20 or not 1 <= len(scid_bytes) <= 20:
        raise ValueError("QUIC connection ids are 1..20 bytes")
    header = (b"\xc0" + _QUIC_VERSION
              + bytes([len(dcid_bytes)]) + dcid_bytes
              + bytes([len(scid_bytes)]) + scid_bytes
              + b"\x00")  # token length 0
    room = min_bytes - len(header) - 2  # 2-byte length varint
    if room < 2:  # pragma: no cover - guarded by the min_bytes check
        raise ValueError("min_bytes too small for the header")
    # length covers packet number (1 byte) + payload
    packet = header + _varint2(room) + os.urandom(room)
    return packet


def probe_quic_udp(host: str, port: int, *,
                   timeout: float = DEFAULT_TIMEOUT_S) -> ProbeResult:
    """Hysteria2 reachability: QUIC Initial → wait for a QUIC answer.

    ``udp_refused`` (ICMP unreachable) is a hard negative; ``silence`` is an
    unknown (a server may drop a datagram it cannot decrypt); ``ok`` means
    *something* on that UDP port answered.
    """
    packet = build_quic_initial()
    kind, rtt, detail = _udp_roundtrip(host, port, packet, timeout=timeout)
    if kind == "answered":
        return ProbeResult(STATUS_OK, rtt_ms=rtt, detail=f"QUIC-ответ: {detail}")
    if kind == "silence":
        return ProbeResult(STATUS_SILENCE, detail=detail)
    if kind == "icmp":
        return ProbeResult(STATUS_UDP_REFUSED, detail=detail)
    if kind == "dns_error":
        return ProbeResult(STATUS_DNS_ERROR, detail=detail)
    if kind == "no_route":
        return ProbeResult(STATUS_NO_ROUTE, detail=detail)
    return ProbeResult(STATUS_ERROR, detail=detail)


def probe_awg_reachability(host: str, port: int, *,
                           recent_g04: bool = False,
                           timeout: float = DEFAULT_TIMEOUT_S) -> ProbeResult:
    """AWG probe: G-04 history first, then UDP reachability.

    AmneziaWG stays silent for unauthenticated packets by design, so "no
    answer" is the *normal* result and must never be read as a block.  What
    is actionable: the kernel saw an ICMP unreachable (nothing listening) or
    G-04 already fired for this endpoint in the last 30 minutes.
    """
    if recent_g04:
        return ProbeResult(
            STATUS_RECENT_UDP_BLACKHOLE,
            detail="G-04 за последние 30 мин: UDP-чёрная дыра на этом эндпоинте")
    payload = os.urandom(_AWG_DATAGRAM_BYTES)
    kind, rtt, detail = _udp_roundtrip(host, port, payload, timeout=timeout)
    if kind == "icmp":
        return ProbeResult(STATUS_UDP_REFUSED, detail=detail)
    if kind == "answered":
        return ProbeResult(STATUS_OK, rtt_ms=rtt,
                           detail=f"неожиданный UDP-ответ: {detail}")
    if kind == "silence":
        return ProbeResult(
            STATUS_REACHABLE,
            detail="ICMP не пришёл; AWG не отвечает на неаутентифицированные "
                   "пакеты — это норма, а не доказательство блокировки")
    if kind == "dns_error":
        return ProbeResult(STATUS_DNS_ERROR, detail=detail)
    if kind == "no_route":
        return ProbeResult(STATUS_NO_ROUTE, detail=detail)
    return ProbeResult(STATUS_ERROR, detail=detail)


# --- one candidate, then the pool -------------------------------------------------


def probe_candidate(candidate: Candidate, *,
                    lockdown_443: bool = False,
                    recent_g04: bool = False,
                    timeout: float = DEFAULT_TIMEOUT_S) -> ProbeResult:
    """Dispatch by protocol; the lock-443 shortcut does not touch the network."""
    if candidate.proto == PROTO_AWG:
        return probe_awg_reachability(candidate.host, candidate.port,
                                      recent_g04=recent_g04, timeout=timeout)
    if candidate.proto == PROTO_HY2:
        return probe_quic_udp(candidate.host, candidate.port, timeout=timeout)
    if (lockdown_443 and candidate.route == ROUTE_DIRECT
            and candidate.proto in TLS_PROTOS and candidate.port == 443):
        return ProbeResult(
            STATUS_SERVER_LOCKDOWN,
            detail="lock-443 активен (server-verdict ≤10 мин) — TCP 443 "
                   "не трогаем")
    return probe_tcp_tls(candidate.host, candidate.port, candidate.sni,
                         timeout=timeout)


def probe_all(candidates: Sequence[Candidate], *,
              budget_ms: int = DEFAULT_BUDGET_MS,
              timeout_s: float = DEFAULT_TIMEOUT_S,
              max_workers: int = MAX_WORKERS,
              lockdown_443: bool = False,
              recent_g04_endpoints: Sequence[str] = (),
              ) -> Dict[str, ProbeResult]:
    """Probe every candidate in parallel within one shared budget.

    Returns ``{candidate.key: ProbeResult}`` in candidate order.  A probe that
    never returned (budget exhausted) is reported as ``timeout``/``budget`` —
    never silently dropped.
    """
    results: Dict[str, ProbeResult] = {}
    if not candidates:
        return results
    deadline = time.monotonic() + max(0.05, budget_ms / 1000.0)
    g04 = set(recent_g04_endpoints)
    workers = max(1, min(int(max_workers), len(candidates)))
    executor = ThreadPoolExecutor(max_workers=workers,
                                  thread_name_prefix="vpn-scout")
    futures = {}
    try:
        for candidate in candidates:
            remaining = deadline - time.monotonic()
            if remaining <= 0.01:
                results[candidate.key] = ProbeResult(
                    STATUS_BUDGET,
                    detail=f"бюджет {budget_ms} мс исчерпан до старта пробы")
                continue
            futures[executor.submit(
                probe_candidate, candidate,
                lockdown_443=lockdown_443,
                recent_g04=f"{candidate.host}:{candidate.port}" in g04,
                timeout=max(0.05, min(float(timeout_s), remaining)),
            )] = candidate
        if futures:
            done, pending = wait(list(futures),
                                 timeout=max(0.0, deadline - time.monotonic()))
        else:
            done, pending = set(), set()
        for future in done:
            candidate = futures[future]
            try:
                results[candidate.key] = future.result()
            except Exception as exc:  # a probe bug must not kill the run
                results[candidate.key] = ProbeResult(
                    STATUS_ERROR, detail=f"probe crashed: {type(exc).__name__}")
        for future in pending:
            candidate = futures[future]
            future.cancel()
            results[candidate.key] = ProbeResult(
                STATUS_TIMEOUT, detail="не уложился в общий бюджет")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return {candidate.key: results.get(
        candidate.key, ProbeResult(STATUS_ERROR, detail="проба не запускалась"))
        for candidate in candidates}


__all__ = [
    "DEFAULT_BUDGET_MS",
    "DEFAULT_TIMEOUT_S",
    "MAX_WORKERS",
    "QUIC_MIN_INITIAL_BYTES",
    "build_quic_initial",
    "probe_all",
    "probe_awg_reachability",
    "probe_candidate",
    "probe_quic_udp",
    "probe_tcp_tls",
]
