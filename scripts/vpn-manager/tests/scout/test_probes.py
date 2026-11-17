"""WP-S2 tests: socket-level probes with fake sockets (no root, no network).

Every case pins one observation: a refused connect, a connect timeout, a
completed TLS 1.3 handshake, a hanging ClientHello (TSPU TLS filter), an
ICMP unreachable and plain UDP silence.  The budget test proves that four
hanging sockets still finish the whole run well under 2500 ms.
"""

from __future__ import annotations

import socket
import ssl
import sys
import time
from pathlib import Path
from typing import List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.scout import probes  # noqa: E402
from vpnm.scout.models import (  # noqa: E402
    STATUS_OK,
    STATUS_RECENT_UDP_BLACKHOLE,
    STATUS_REACHABLE,
    STATUS_SERVER_LOCKDOWN,
    STATUS_SILENCE,
    STATUS_TIMEOUT,
    STATUS_TSPU_TLS_FILTER,
    STATUS_UDP_REFUSED,
    Candidate,
)


# --- fake socket layer ----------------------------------------------------------


class FakeIO:
    """Scripted behaviour of every socket opened during one probe."""

    def __init__(self, *, connect_error: Optional[BaseException] = None,
                 handshake_error: Optional[BaseException] = None,
                 recv_result: Optional[bytes] = None,
                 recv_error: Optional[BaseException] = None,
                 connect_delay: float = 0.0) -> None:
        self.connect_error = connect_error
        self.handshake_error = handshake_error
        self.recv_result = recv_result
        self.recv_error = recv_error
        self.connect_delay = connect_delay
        self.getaddrinfo_calls = 0
        self.connected = 0
        self.sent: List[bytes] = []
        self.sni: List[Optional[str]] = []
        self.closed = 0


class FakeSock:
    def __init__(self, io: FakeIO) -> None:
        self.io = io

    def settimeout(self, _value: float) -> None:
        pass

    def connect(self, _addr) -> None:
        if self.io.connect_delay:
            time.sleep(self.io.connect_delay)
        if self.io.connect_error is not None:
            raise self.io.connect_error
        self.io.connected += 1

    def send(self, payload: bytes) -> int:
        self.io.sent.append(payload)
        return len(payload)

    def recv(self, _size: int) -> bytes:
        if self.io.recv_error is not None:
            raise self.io.recv_error
        return self.io.recv_result if self.io.recv_result is not None else b""

    def close(self) -> None:
        self.io.closed += 1


class FakeTLS:
    def __init__(self, io: FakeIO) -> None:
        self.io = io

    def close(self) -> None:
        self.io.closed += 1


class FakeTLSContext:
    def __init__(self, io: FakeIO) -> None:
        self.io = io

    def wrap_socket(self, _sock, server_hostname: Optional[str] = None):
        self.io.sni.append(server_hostname)
        if self.io.handshake_error is not None:
            raise self.io.handshake_error
        return FakeTLS(self.io)


class FakeSocketModule:
    """Only the surface ``probes`` uses; everything else is the real module."""

    AF_INET = socket.AF_INET
    SOCK_STREAM = socket.SOCK_STREAM
    SOCK_DGRAM = socket.SOCK_DGRAM
    timeout = socket.timeout
    gaierror = socket.gaierror
    error = socket.error

    def __init__(self, io: FakeIO) -> None:
        self.io = io

    def getaddrinfo(self, host, port, type=0, **kwargs):  # noqa: A002
        self.io.getaddrinfo_calls += 1
        return [(socket.AF_INET, type or socket.SOCK_STREAM,
                 socket.IPPROTO_TCP, "", (host, port))]

    def socket(self, family, socktype, proto=0):
        return FakeSock(self.io)


def install_fake(monkeypatch: pytest.MonkeyPatch, io: FakeIO) -> FakeSocketModule:
    fake = FakeSocketModule(io)
    monkeypatch.setattr(probes, "socket", fake)
    monkeypatch.setattr(probes, "_tls_context", lambda: FakeTLSContext(io))
    return fake


# --- TCP / TLS --------------------------------------------------------------------


def test_tcp_connect_timeout_is_reported_as_timeout(
        monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake(monkeypatch, FakeIO(connect_error=socket.timeout("timed out")))
    result = probes.probe_tcp_tls("203.0.113.10", 443, "example.com",
                                  timeout=1.5)
    assert result.status == STATUS_TIMEOUT
    assert result.hard_fail is True


def test_tcp_rst_is_the_tls_filter_signature(
        monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake(monkeypatch, FakeIO(
        connect_error=ConnectionResetError(104, "Connection reset by peer")))
    result = probes.probe_tcp_tls("203.0.113.10", 443, "example.com")
    assert result.status == STATUS_TSPU_TLS_FILTER


def test_tls_ok_measures_rtt_and_uses_the_profile_sni(
        monkeypatch: pytest.MonkeyPatch) -> None:
    io = FakeIO()
    install_fake(monkeypatch, io)
    result = probes.probe_tcp_tls("203.0.113.10", 443, "www.microsoft.com")
    assert result.status == STATUS_OK
    assert result.rtt_ms is not None and result.rtt_ms >= 0
    assert io.sni == ["www.microsoft.com"]
    assert io.connected == 1


def test_hanging_client_hello_is_tspu_filter_not_a_plain_timeout(
        monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake(monkeypatch, FakeIO(
        handshake_error=socket.timeout("handshake timed out")))
    result = probes.probe_tcp_tls("203.0.113.10", 443, "example.com")
    assert result.status == STATUS_TSPU_TLS_FILTER
    assert "ClientHello" in (result.detail or "")


def test_tls_eof_is_the_tls_filter_signature(
        monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake(monkeypatch, FakeIO(
        handshake_error=ssl.SSLEOFError(8, "EOF occurred in violation of "
                                           "protocol")))
    result = probes.probe_tcp_tls("203.0.113.10", 443, "example.com")
    assert result.status == STATUS_TSPU_TLS_FILTER


def test_lockdown_reports_without_opening_a_socket(
        monkeypatch: pytest.MonkeyPatch) -> None:
    io = FakeIO()
    install_fake(monkeypatch, io)
    candidate = Candidate(route="direct", proto="xray-reality", profile="phone",
                          host="203.0.113.10", port=443, sni="example.com")
    result = probes.probe_candidate(candidate, lockdown_443=True)
    assert result.status == STATUS_SERVER_LOCKDOWN
    assert io.getaddrinfo_calls == 0
    assert io.connected == 0


# --- QUIC / AWG -------------------------------------------------------------------


def test_quic_initial_is_1200_bytes_with_a_random_dcid() -> None:
    dcid = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    packet = probes.build_quic_initial(dcid)
    assert len(packet) == probes.QUIC_MIN_INITIAL_BYTES
    assert packet[0] == 0xC0
    assert packet[1:5] == b"\x00\x00\x00\x01"
    assert packet[5] == len(dcid)
    assert packet[6:14] == dcid
    assert probes.build_quic_initial() != probes.build_quic_initial()


def test_quic_icmp_unreachable_is_a_hard_negative(
        monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake(monkeypatch, FakeIO(
        recv_error=ConnectionRefusedError(111, "Connection refused")))
    result = probes.probe_quic_udp("203.0.113.10", 443)
    assert result.status == STATUS_UDP_REFUSED
    assert result.hard_fail is True


def test_quic_silence_is_unknown_not_blocked(
        monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake(monkeypatch, FakeIO(recv_error=socket.timeout("timed out")))
    result = probes.probe_quic_udp("203.0.113.10", 443)
    assert result.status == STATUS_SILENCE
    assert result.hard_fail is False


def test_quic_answer_is_ok_and_the_datagram_is_padded(
        monkeypatch: pytest.MonkeyPatch) -> None:
    io = FakeIO(recv_result=b"\x00" * 64)
    install_fake(monkeypatch, io)
    result = probes.probe_quic_udp("203.0.113.10", 443)
    assert result.status == STATUS_OK
    assert len(io.sent) == 1 and len(io.sent[0]) >= 1200


def test_awg_recent_g04_is_a_blackhole_without_network(
        monkeypatch: pytest.MonkeyPatch) -> None:
    io = FakeIO()
    install_fake(monkeypatch, io)
    result = probes.probe_awg_reachability("203.0.113.10", 51820,
                                           recent_g04=True)
    assert result.status == STATUS_RECENT_UDP_BLACKHOLE
    assert io.getaddrinfo_calls == 0


def test_awg_silence_is_normal_reachability(
        monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake(monkeypatch, FakeIO(recv_error=socket.timeout("timed out")))
    result = probes.probe_awg_reachability("203.0.113.10", 51820)
    assert result.status == STATUS_REACHABLE
    assert result.hard_fail is False
    assert "не отвечает" in (result.detail or "")


def test_awg_icmp_unreachable_is_refused(
        monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake(monkeypatch, FakeIO(
        recv_error=ConnectionRefusedError(111, "Connection refused")))
    result = probes.probe_awg_reachability("203.0.113.10", 51820)
    assert result.status == STATUS_UDP_REFUSED


# --- budget -----------------------------------------------------------------------


def test_four_hanging_sockets_finish_under_the_2500ms_budget(
        monkeypatch: pytest.MonkeyPatch) -> None:
    io = FakeIO(connect_delay=1.5,
                connect_error=socket.timeout("timed out"))
    install_fake(monkeypatch, io)
    candidates = [
        Candidate(route="direct", proto="xray-reality", profile=f"p{i}",
                  host=f"203.0.113.{10 + i}", port=443, sni="example.com")
        for i in range(4)
    ]
    started = time.monotonic()
    results = probes.probe_all(candidates)
    elapsed = time.monotonic() - started
    assert elapsed < 2.5, f"бюджет пробит: {elapsed:.2f}s"
    assert elapsed >= 1.4  # the probes really waited on their sockets
    assert set(results) == {candidate.key for candidate in candidates}
    assert all(result.status == STATUS_TIMEOUT for result in results.values())


def test_probe_all_keeps_candidate_order_and_masks_crashes(
        monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake(monkeypatch, FakeIO())

    def boom(candidate, **kwargs):
        if candidate.profile == "bad":
            raise RuntimeError("probe bug")
        return probes.ProbeResult(STATUS_OK, rtt_ms=5.0)

    monkeypatch.setattr(probes, "probe_candidate", boom)
    candidates = [
        Candidate(route="direct", proto="xray-reality", profile="good",
                  host="203.0.113.10", port=443, sni="example.com"),
        Candidate(route="direct", proto="hy2", profile="bad",
                  host="203.0.113.11", port=443),
    ]
    results = probes.probe_all(candidates)
    assert list(results) == [candidate.key for candidate in candidates]
    assert results[candidates[0].key].status == STATUS_OK
    assert results[candidates[1].key].status not in (STATUS_OK,)
