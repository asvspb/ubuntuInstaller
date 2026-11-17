"""WP-S2 + Stealth Cascade: bounded, read-only active probes.

Three primitives, all unprivileged and all on a **finite budget**:

- :func:`probe_tcp_tls` — TCP connect, a TLS 1.3 handshake with the real SNI
  of the profile, then **one RFC-shaped HTTP request** (``HEAD /`` with the
  candidate's SNI as ``Host``) and a read of the first 64 response bytes.  A
  full HTTP answer (``HTTP/1.`` / ``HTTP/2``) is ``ok``; a completed handshake
  without an HTTP answer is ``tls_only`` (proven handshake, unproven
  transport), never a hard fail.  ``refused`` (RST on connect), ``timeout``
  (no SYN-ACK) and ``TSPU_TLS_FILTER`` (RST or a hanging ClientHello *after*
  the connection was established) stay different observations;
- :func:`probe_quic_udp` — Hysteria2 **local routability only**: no datagram is
  ever built or sent (no QUIC Initial, no ``os.urandom`` payload).  A UDP
  ``connect()`` performs the kernel's route lookup and sends nothing, so the
  probe is ``local_route`` with **0 bytes on the wire**; a fresh G-04 on the
  endpoint keeps the old ``RECENT_UDP_BLACKHOLE`` and a recent G-01/G-03 for
  the profile demotes the result to ``recent_incident``;
- :func:`probe_awg_reachability` — the same local-route check: AmneziaWG never
  answers unauthenticated packets, so silence proved nothing anyway.  G-04
  history (``RECENT_UDP_BLACKHOLE``) and G-01/G-03 (``recent_incident``) are
  applied without touching the network.

Why no packets: an unsolicited QUIC Initial / 32-byte UDP blip towards a
filtering middlebox is exactly the kind of traffic that can *trigger* the
probe-watch on the other side.  A connected UDP socket with no ``send``
answers the only question this probe can honestly answer — "does the local
routing table have a path to this endpoint?" — and stays invisible on the
wire.

If the server verdict is fresh and ``lockdown.active`` is set, a **direct
xray on TCP 443** is reported as ``SERVER_LOCKDOWN`` **without opening a
socket**: probing the very port the server just locked would manufacture
load and confuse the observation.

Address family order: every probe walks **every** address ``getaddrinfo``
returned, IPv4 first and IPv6 last, instead of taking ``infos[0]``.  On an
IPv4-only host a dual-stack name (A+AAAA) resolves with the AAAA record
first, and the old code only ever tried it — so a working IPv4 path was
reported as ``no_route``/``error``.  ENETUNREACH/EHOSTUNREACH on one address
is *this address has no route*, not a verdict about the peer (guard fix
904212f): the next address is tried and the last error is kept only when
every address has been exhausted.

Two run modes:

- :func:`probe_cascade` — **the default**.  Candidates arrive already sorted by
  the local, network-free :func:`vpnm.scout.rank.pre_score`; they are probed
  **strictly one at a time**, stopping at the first good status.  A random
  150–400 ms jitter separates a failed attempt from the next one.  The whole
  cascade shares :data:`CASCADE_BUDGET_MS`; a node that cannot be started is
  reported as ``budget``, never silently skipped.  Nodes after the winner are
  never contacted at all;
- :func:`probe_all` — the explicit ``vpn scout --probe-all`` path: the old
  parallel ``ThreadPoolExecutor(max_workers=8)`` with a shared 2500 ms budget.
"""

from __future__ import annotations

import errno
import random
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .models import (
    GOOD_STATUSES,
    PROTO_AWG,
    PROTO_HY2,
    ROUTE_DIRECT,
    TLS_PROTOS,
    Candidate,
    ProbeResult,
    STATUS_BUDGET,
    STATUS_DNS_ERROR,
    STATUS_ERROR,
    STATUS_LOCAL_ROUTE,
    STATUS_NO_ROUTE,
    STATUS_OK,
    STATUS_RECENT_INCIDENT,
    STATUS_RECENT_UDP_BLACKHOLE,
    STATUS_REFUSED,
    STATUS_SERVER_LOCKDOWN,
    STATUS_TIMEOUT,
    STATUS_TLS_ERROR,
    STATUS_TLS_ONLY,
    STATUS_TSPU_TLS_FILTER,
)

#: Per-socket timeout (WP-S2).
DEFAULT_TIMEOUT_S = 1.5
#: Whole-run budget of the parallel ``--probe-all`` path (WP-S2).
DEFAULT_BUDGET_MS = 2500
#: Whole-run budget of the default cascade (Stealth Cascade).
CASCADE_BUDGET_MS = 6000
#: Random pause between two cascade attempts (seconds, inclusive bounds).
CASCADE_JITTER_MIN_S = 0.15
CASCADE_JITTER_MAX_S = 0.4
#: Worker count of the ``--probe-all`` path (WP-S2).
MAX_WORKERS = 8
#: Bytes read back from the HTTP answer (enough for ``HTTP/1.`` / ``HTTP/2``).
HTTP_READ_BYTES = 64
#: HTTP prefixes that count as a real answer to the HEAD request.
_HTTP_PREFIXES = (b"HTTP/1.", b"HTTP/2")

#: Address families in the order the probes try them: IPv4 first, IPv6 last,
#: anything else after.  ``sorted`` is stable, so the resolver's own order is
#: kept inside one family — the order never depends on the host's OS.
_FAMILY_ORDER = {socket.AF_INET: 0, socket.AF_INET6: 1}

#: errno values that mean "no route to *this* address", not "peer is blocked".
_NO_ROUTE_ERRNOS = (errno.ENETUNREACH, errno.EHOSTUNREACH)


def _remaining(deadline: float) -> float:
    """Seconds left until ``deadline`` (never zero, so settimeout is valid)."""
    return max(0.05, deadline - time.monotonic())


def _ordered_infos(infos: Sequence[Tuple]) -> Sequence[Tuple]:
    """Deterministic IPv4-first order of ``getaddrinfo`` records.

    A dual-stack name on an IPv4-only host resolves AAAA-first; trying that
    address first only produces ENETUNREACH and used to hide a reachable
    IPv4 path.  IPv4 therefore goes first, IPv6 last, and the relative order
    inside a family is left to the resolver.
    """
    return sorted(infos, key=lambda info: _FAMILY_ORDER.get(info[0], 2))


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


def build_http_request(sni: str) -> bytes:
    """The **exact** bytes sent after a successful handshake (Stealth Cascade).

    One RFC-shaped ``HEAD /`` with the candidate's real SNI as ``Host`` is the
    smallest request that looks like a browser cache validation instead of a
    scanner fingerprint.  Pinned byte-for-byte by the tests.
    """
    return (f"HEAD / HTTP/1.1\r\nHost: {sni}\r\n"
            "User-Agent: Mozilla/5.0\r\nConnection: close\r\n\r\n").encode()


def classify_http_answer(data: bytes) -> Optional[str]:
    """``'http'`` for a real answer, ``None`` when it is EOF/garbage."""
    if data.startswith(_HTTP_PREFIXES):
        return "http"
    return None


def _http_exchange(tls: Any, sni: str, deadline: float) -> ProbeResult:
    """Send one HEAD request, read ≤64 bytes, classify the answer.

    A completed handshake with no HTTP answer is ``tls_only``: the TLS leg is
    proven, the transport behind it is not.  Never a hard fail.
    """
    request = build_http_request(sni)
    try:
        tls.settimeout(_remaining(deadline))
    except (AttributeError, OSError):
        pass  # a fake socket in tests may not implement it
    try:
        tls.sendall(request)
        data = tls.recv(HTTP_READ_BYTES)
    except (socket.timeout, TimeoutError):
        return ProbeResult(STATUS_TLS_ONLY,
                           detail="TLS ok, HTTP-ответа нет (timeout)")
    except (ConnectionResetError, BrokenPipeError):
        return ProbeResult(STATUS_TLS_ONLY,
                           detail="TLS ok, HTTP-ответа нет (RST/сброс)")
    except ssl.SSLError as exc:
        return ProbeResult(STATUS_TLS_ONLY,
                           detail=f"TLS ok, HTTP-ответа нет (TLS: "
                                  f"{getattr(exc, 'reason', None) or exc})")
    except OSError as exc:
        return ProbeResult(STATUS_TLS_ONLY,
                           detail=f"TLS ok, HTTP-ответа нет "
                                  f"({type(exc).__name__})")
    if not data:
        return ProbeResult(STATUS_TLS_ONLY,
                           detail="TLS ok, HTTP-ответа нет (EOF)")
    if classify_http_answer(data) == "http":
        return ProbeResult(STATUS_OK, detail="TLS+HTTP")
    return ProbeResult(STATUS_TLS_ONLY,
                       detail="TLS ok, HTTP-ответа нет (мусор в ответе)")


# --- TCP / TLS ------------------------------------------------------------------


def probe_tcp_tls(host: str, port: int, sni: Optional[str] = None, *,
                  timeout: float = DEFAULT_TIMEOUT_S) -> ProbeResult:
    """TCP connect → TLS 1.3 handshake → HEAD request (read-only, bounded).

    Every resolved address is tried, IPv4 first.  A route failure
    (ENETUNREACH/EHOSTUNREACH) or a connect timeout on one address moves on
    to the next while the shared deadline still has time; the last failure is
    returned only when every address is exhausted.
    """
    started = time.monotonic()
    deadline = started + max(0.05, float(timeout))
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return ProbeResult(STATUS_DNS_ERROR, detail=f"DNS: {exc}")
    if not infos:
        return ProbeResult(STATUS_DNS_ERROR, detail="DNS: пустой ответ")
    last = ProbeResult(STATUS_ERROR, detail="проба не выполнялась")
    for index, (family, socktype, proto, _canon, sockaddr) in enumerate(
            _ordered_infos(infos)):
        if index and _remaining(deadline) <= 0.05:
            break  # бюджет исчерпан первым адресом — дальше не пробуем
        last, retry = _tcp_tls_address(
            family, socktype, proto, sockaddr,
            host=host, sni=sni, started=started, deadline=deadline,
            timeout=float(timeout))
        if not retry:
            return last
    return last


def _tcp_tls_address(family: int, socktype: int, proto: int, sockaddr: Tuple,
                     *, host: str, sni: Optional[str], started: float,
                     deadline: float, timeout: float
                     ) -> Tuple[ProbeResult, bool]:
    """One TCP+TLS+HTTP attempt against one resolved address.

    Returns ``(result, retry)``; ``retry`` is true only when the failure is
    about *this address* (no route, or a connect that timed out before the
    shared deadline) — then the next address may still be reachable.  A
    completed handshake, an RST, a hanging ClientHello and a TLS error are all
    observations about the peer and end the probe.
    """
    try:
        sock = socket.socket(family, socktype, proto)
    except OSError as exc:
        return _oserror_result(exc), True
    phase = "connect"
    tls: Optional[object] = None
    try:
        sock.settimeout(_remaining(deadline))
        sock.connect(sockaddr)
        rtt_ms = round((time.monotonic() - started) * 1000.0, 1)
        phase = "handshake"
        sock.settimeout(_remaining(deadline))
        tls = _tls_context().wrap_socket(sock, server_hostname=sni or host)
        phase = "http"
        exchange = _http_exchange(tls, sni or host, deadline)
        return ProbeResult(exchange.status, rtt_ms=rtt_ms,
                           detail=exchange.detail), False
    except (socket.timeout, TimeoutError):
        if phase == "handshake":
            # connected, but the ClientHello never got an answer: the
            # signature of a TLS filter that swallows instead of RSTing
            return ProbeResult(STATUS_TSPU_TLS_FILTER,
                               detail="ClientHello без ответа (handshake "
                                      "timeout)"), False
        if phase == "http":  # pragma: no cover - _http_exchange catches it
            return ProbeResult(STATUS_TLS_ONLY,
                               detail="TLS ok, HTTP-ответа нет (timeout)"), False
        return ProbeResult(STATUS_TIMEOUT,
                           detail=f"TCP connect timeout {timeout:g}s"), True
    except ConnectionResetError:
        return ProbeResult(STATUS_TSPU_TLS_FILTER,
                           detail="RST после ClientHello/на connect"), False
    except ssl.SSLEOFError as exc:
        return ProbeResult(STATUS_TSPU_TLS_FILTER,
                           detail=f"EOF во время handshake: {exc}"), False
    except ssl.SSLError as exc:
        return ProbeResult(STATUS_TLS_ERROR,
                           detail=f"TLS: {getattr(exc, 'reason', None) or exc}"
                           ), False
    except OSError as exc:
        result = _oserror_result(exc)
        return result, result.status in (STATUS_NO_ROUTE, STATUS_TIMEOUT)
    finally:
        for sock_obj in (tls, sock):
            if sock_obj is None:
                continue
            try:
                sock_obj.close()
            except OSError:
                pass


# --- UDP local routability (Stealth Cascade: 0 bytes on the wire) -----------------


def _udp_local_route(host: str, port: int, *,
                     timeout: float = DEFAULT_TIMEOUT_S) -> ProbeResult:
    """Prove the kernel has a path to ``host:port`` **without sending anything**.

    ``socket.connect()`` on a UDP socket performs the route lookup and
    installs the peer, but a UDP ``connect`` emits no datagram.  Nothing is
    ever handed to ``send``/``sendto`` — this is the whole point of the
    Stealth Cascade: a filtering middlebox sees no traffic to react to.
    Every resolved address is tried, IPv4 first; ENETUNREACH/EHOSTUNREACH on
    one address moves on to the next, and ``no_route`` is returned only when
    every address is exhausted.
    """
    started = time.monotonic()
    deadline = started + max(0.05, float(timeout))
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    except socket.gaierror as exc:
        return ProbeResult(STATUS_DNS_ERROR, detail=f"DNS: {exc}")
    if not infos:
        return ProbeResult(STATUS_DNS_ERROR, detail="DNS: пустой ответ")
    last = ProbeResult(STATUS_ERROR, detail="проба не выполнялась")
    for index, (family, socktype, proto, _canon, sockaddr) in enumerate(
            _ordered_infos(infos)):
        if index and _remaining(deadline) <= 0.05:
            break  # бюджет исчерпан первым адресом — дальше не пробуем
        last, retry = _udp_local_address(family, socktype, proto, sockaddr,
                                         deadline=deadline)
        if not retry:
            return last
    return last


def _udp_local_address(family: int, socktype: int, proto: int,
                       sockaddr: Tuple, *, deadline: float
                       ) -> Tuple[ProbeResult, bool]:
    """One address: ``connect`` only (no ``send``), then classify.

    Returns ``(result, retry)``; ``retry`` is true only when this address has
    no route, so the next one may still be usable.
    """
    try:
        sock = socket.socket(family, socktype, proto)
    except OSError as exc:
        code = getattr(exc, "errno", None)
        if code in _NO_ROUTE_ERRNOS:
            return ProbeResult(STATUS_NO_ROUTE,
                               detail=f"нет маршрута: {exc}"), True
        return ProbeResult(STATUS_ERROR,
                           detail=f"{type(exc).__name__}: {exc}"), False
    try:
        sock.settimeout(_remaining(deadline))
        sock.connect(sockaddr)
        return ProbeResult(
            STATUS_LOCAL_ROUTE,
            detail="локальная маршрутизируемость (0 байт в эфир)"), False
    except OSError as exc:
        code = getattr(exc, "errno", None)
        if code in _NO_ROUTE_ERRNOS:
            return ProbeResult(STATUS_NO_ROUTE,
                               detail=f"нет маршрута: {exc}"), True
        return ProbeResult(STATUS_ERROR,
                           detail=f"{type(exc).__name__}: {exc}"), False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def probe_quic_udp(host: str, port: int, *,
                   recent_g04: bool = False,
                   recent_incident: bool = False,
                   incident_label: Optional[str] = None,
                   timeout: float = DEFAULT_TIMEOUT_S) -> ProbeResult:
    """Hysteria2: local routability, and **no datagram whatsoever**.

    History is applied before any socket work: a fresh G-04 for this endpoint
    keeps the old ``RECENT_UDP_BLACKHOLE``; a recent G-01/G-03 for the profile
    demotes the verdict to ``recent_incident`` (still no packet).  Otherwise
    the answer is ``local_route`` — the routing table has a path, and that is
    all this probe can honestly say without sending a QUIC Initial.
    """
    if recent_g04:
        return ProbeResult(
            STATUS_RECENT_UDP_BLACKHOLE,
            detail="G-04 за последние 30 мин: UDP-чёрная дыра на этом эндпоинте")
    if recent_incident:
        return ProbeResult(
            STATUS_RECENT_INCIDENT,
            detail=f"недавний G-01/G-03 по профилю "
                   f"'{incident_label or '?'}' — UDP-проба понижена, "
                   "пакетов не отправляли")
    return _udp_local_route(host, port, timeout=timeout)


def probe_awg_reachability(host: str, port: int, *,
                           recent_g04: bool = False,
                           recent_incident: bool = False,
                           incident_label: Optional[str] = None,
                           timeout: float = DEFAULT_TIMEOUT_S) -> ProbeResult:
    """AWG probe: history first, then local routability (0 bytes sent).

    AmneziaWG stays silent for unauthenticated packets by design, so the old
    "silence" observation never proved anything about the tunnel — and the
    datagram itself was exactly the kind of unsolicited traffic that can
    trigger the other side's probe-watch.  The probe therefore only checks
    that the kernel has a route and emits nothing.
    """
    if recent_g04:
        return ProbeResult(
            STATUS_RECENT_UDP_BLACKHOLE,
            detail="G-04 за последние 30 мин: UDP-чёрная дыра на этом эндпоинте")
    if recent_incident:
        return ProbeResult(
            STATUS_RECENT_INCIDENT,
            detail=f"недавний G-01/G-03 по профилю "
                   f"'{incident_label or '?'}' — UDP-проба понижена, "
                   "пакетов не отправляли")
    return _udp_local_route(host, port, timeout=timeout)


# --- one candidate, then the pool -------------------------------------------------


def probe_candidate(candidate: Candidate, *,
                    lockdown_443: bool = False,
                    recent_g04: bool = False,
                    recent_incident: bool = False,
                    timeout: float = DEFAULT_TIMEOUT_S) -> ProbeResult:
    """Dispatch by protocol; the history/lock shortcuts never open a socket."""
    if candidate.proto == PROTO_AWG:
        return probe_awg_reachability(candidate.host, candidate.port,
                                      recent_g04=recent_g04,
                                      recent_incident=recent_incident,
                                      incident_label=candidate.profile,
                                      timeout=timeout)
    if candidate.proto == PROTO_HY2:
        return probe_quic_udp(candidate.host, candidate.port,
                              recent_g04=recent_g04,
                              recent_incident=recent_incident,
                              incident_label=candidate.profile,
                              timeout=timeout)
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
              recent_incident_profiles: Sequence[str] = (),
              ) -> Dict[str, ProbeResult]:
    """Probe every candidate in parallel within one shared budget.

    The explicit ``vpn scout --probe-all`` path — the old concurrency model
    (``ThreadPoolExecutor(max_workers=8)``), kept verbatim for the case when
    the owner really wants the full picture and accepts the traffic.

    Returns ``{candidate.key: ProbeResult}`` in candidate order.  A probe that
    never returned (budget exhausted) is reported as ``timeout``/``budget`` —
    never silently dropped.
    """
    results: Dict[str, ProbeResult] = {}
    if not candidates:
        return results
    deadline = time.monotonic() + max(0.05, budget_ms / 1000.0)
    g04 = set(recent_g04_endpoints)
    incidents = set(recent_incident_profiles)
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
                recent_incident=(candidate.profile in incidents),
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


# --- Stealth Cascade ---------------------------------------------------------------


def jitter_between_attempts(sleep: Callable[[float], None] = time.sleep) -> float:
    """Pause a random 150–400 ms before the next cascade attempt.

    The pause is not cosmetic: a fixed cadence of failing connects to
    different nodes is a machine signature.  Tests monkeypatch ``sleep`` (or
    pass their own) and assert both the call count and the range.
    """
    delay = random.uniform(CASCADE_JITTER_MIN_S, CASCADE_JITTER_MAX_S)
    sleep(delay)
    return delay


def probe_cascade(candidates: Sequence[Candidate], *,
                  budget_ms: int = CASCADE_BUDGET_MS,
                  timeout_s: float = DEFAULT_TIMEOUT_S,
                  lockdown_443: bool = False,
                  recent_g04_endpoints: Sequence[str] = (),
                  recent_incident_profiles: Sequence[str] = (),
                  sleep: Callable[[float], None] = time.sleep,
                  ) -> Tuple[Dict[str, ProbeResult], List[str]]:
    """Probe candidates **one at a time**, stopping at the first good one.

    ``candidates`` must already be ordered by the network-free
    :func:`vpnm.scout.rank.pre_score` (the caller does that).  The contract:

    - strictly sequential — never two probes at once;
    - the first ``ok`` / ``reachable`` / ``local_route`` ends the run: the
      remaining candidates are **not contacted at all** (no socket, no DNS);
    - between two attempts a random 150–400 ms jitter is applied (and only
      between attempts: nothing is slept before the first probe, nothing
      after the last);
    - the cascade shares :data:`CASCADE_BUDGET_MS`.  A candidate that cannot
      be started is reported as ``budget`` — honest, never silent;
    - a probe crash is ``error`` and does not kill the run.

    Returns ``(results, probed_keys)`` — ``probed_keys`` holds only the
    candidates whose probe actually started (attempt order); ``results`` also
    carries a ``budget`` marker for the candidate that could not be started,
    so the report can say so instead of dropping it.
    """
    results: Dict[str, ProbeResult] = {}
    probed: List[str] = []
    if not candidates:
        return results, probed
    deadline = time.monotonic() + max(0.05, budget_ms / 1000.0)
    g04 = set(recent_g04_endpoints)
    incidents = set(recent_incident_profiles)
    for index, candidate in enumerate(candidates):
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            results[candidate.key] = ProbeResult(
                STATUS_BUDGET,
                detail=f"бюджет каскада {budget_ms} мс исчерпан до старта "
                       "пробы")
            break
        if index:  # only between attempts, never before the first one
            jitter_between_attempts(sleep)
            if deadline - time.monotonic() <= 0.05:
                results[candidate.key] = ProbeResult(
                    STATUS_BUDGET,
                    detail=f"бюджет каскада {budget_ms} мс исчерпан "
                           "джиттером до старта пробы")
                break
        try:
            result = probe_candidate(
                candidate,
                lockdown_443=lockdown_443,
                recent_g04=f"{candidate.host}:{candidate.port}" in g04,
                recent_incident=(candidate.profile in incidents),
                timeout=max(0.05, min(float(timeout_s),
                                      deadline - time.monotonic())),
            )
        except Exception as exc:  # a probe bug must not kill the run
            result = ProbeResult(STATUS_ERROR,
                                 detail=f"probe crashed: {type(exc).__name__}")
        results[candidate.key] = result
        probed.append(candidate.key)
        if result.status in GOOD_STATUSES:
            break  # 0 packets to every node after this one
    return results, probed


__all__ = [
    "CASCADE_BUDGET_MS",
    "CASCADE_JITTER_MAX_S",
    "CASCADE_JITTER_MIN_S",
    "DEFAULT_BUDGET_MS",
    "DEFAULT_TIMEOUT_S",
    "HTTP_READ_BYTES",
    "MAX_WORKERS",
    "build_http_request",
    "classify_http_answer",
    "jitter_between_attempts",
    "probe_all",
    "probe_awg_reachability",
    "probe_candidate",
    "probe_cascade",
    "probe_quic_udp",
    "probe_tcp_tls",
]
