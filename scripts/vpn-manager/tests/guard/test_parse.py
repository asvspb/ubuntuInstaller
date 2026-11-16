"""WP-G2 tests: journal parser (structured-first, app-regex, unparsed).

Fixture lines are synthetic; secrets use the reserved ``FIXTURE_*`` prefixes
and RFC 5737 addresses only (AGENTS §6).  No root, no network, no journald.
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import events, schema  # noqa: E402
from vpnm.guard.parse import (  # noqa: E402
    GUARD_IDENTIFIER,
    GUARD_UNIT,
    MAX_MESSAGE_CHARS,
    JournalParser,
    ParseStats,
    normalize_line,
    template_hash,
)

RT_US = "1794830403412345"      # 2026-11-16T12:00:03.412Z
MONO_US = "812345678"
FIXTURE_UUID = "11111111-2222-3333-4444-555555555555"  # reserved (AGENTS §6)


def entry(message: object = None, unit: str = "vpnm-asv2.service", **extra: object) -> dict:
    base: dict = {
        "MESSAGE": message,
        "PRIORITY": "6",
        "__REALTIME_TIMESTAMP": RT_US,
        "__MONOTONIC_TIMESTAMP": MONO_US,
        "_SYSTEMD_USER_UNIT": unit,
        "_SYSTEMD_INVOCATION_ID": "fixture-invocation",
    }
    base.update(extra)
    return base


# --- happy paths -------------------------------------------------------------------


def test_xray_json_line_dial_refused() -> None:
    parser = JournalParser(proto_hints={"vpnm-asv2.service": "xray"})
    line = ('{"level":"warning","msg":"proxy/vless/outbound: failed to dial '
            'to 203.0.113.10:443: dial tcp: connection refused"}')
    event = parser.parse(entry(line))
    assert event is not None
    assert event["signal"] == "conn_refused"
    assert event["proto"] == "xray"
    assert event["source"] == "journal:user"
    assert event["endpoint"] == {"host": "203.0.113.10", "port": 443}
    assert event["template"] == template_hash(line)
    assert schema.validate(event, events.EVENT_SCHEMA) == []


def test_sing_box_json_reset_is_parsed() -> None:
    parser = JournalParser()
    line = ('{"level":"warn","timestamp":"2026-11-16T12:00:03Z",'
            '"message":"outbound/tls: connection reset by peer"}')
    event = parser.parse(entry(line))
    assert event is not None
    assert event["signal"] == "conn_reset"


def test_hysteria2_auth_fail() -> None:
    parser = JournalParser(proto_hints={"vpnm-asv2.service": "hysteria2"})
    event = parser.parse(entry("2026/11/16 12:00:03 auth error: invalid password"))
    assert event is not None
    assert event["signal"] == "auth_fail"
    assert event["proto"] == "hysteria2"
    assert event["profile"] == "asv2"


def test_app_banner_ok_xray() -> None:
    parser = JournalParser(proto_hints={"vpnm-asv2.service": "xray"})
    event = parser.parse(entry("Xray 1.8.6 started"))
    assert event is not None
    assert event["signal"] == "app_banner_ok"
    assert event["signal"] in events.BOOT_SIGNALS


def test_app_banner_ok_hysteria2() -> None:
    parser = JournalParser(proto_hints={"vpnm-asv2.service": "hysteria2"})
    event = parser.parse(entry("Hysteria2 client mode, socks5://127.0.0.1:1080"))
    assert event is not None
    assert event["signal"] == "app_banner_ok"


def test_quic_idle_timeout_and_no_activity() -> None:
    parser = JournalParser(proto_hints={"vpnm-asv2.service": "hysteria2"})
    idle = parser.parse(entry("timeout: no recent network activity"))
    assert idle is not None and idle["signal"] == "quic_no_recent_activity"
    plain = parser.parse(entry("connection closed due to idle timeout"))
    assert plain is not None and plain["signal"] == "quic_idle_timeout"


def test_dial_timeout_not_shadowed_by_generic_words() -> None:
    parser = JournalParser()
    event = parser.parse(entry("read timeout while streaming"))
    assert event is not None
    assert event["signal"] == "read_timeout"


@pytest.mark.parametrize("line,expected", [
    ("failed to dial to 203.0.113.10:443: i/o timeout", "dial_timeout"),
    ("dial tcp 203.0.113.10:443: connect: connection refused", "conn_refused"),
    ("failed to handshake with server: authentication error", "auth_fail"),
    ("tls: failed to verify certificate: x509: certificate signed by unknown"
     " authority", "tls_cert_error"),
    ("dns lookup falcon.example.net: no such host", "dns_nxdomain"),
    ("resolver returned SERVFAIL for falcon.example.net", "dns_servfail"),
    ("listen tcp :443: address already in use", "address_in_use"),
    ("route: network is unreachable", "network_unreachable"),
    ("proxy/vless/outbound: tunnel established", "conn_established"),
])
def test_signal_families(line: str, expected: str) -> None:
    parser = JournalParser()
    event = parser.parse(entry(line))
    assert event is not None, line
    assert event["signal"] == expected, line


# --- structured-first (systemd) ------------------------------------------------------


def test_structured_job_start_beats_message_text() -> None:
    parser = JournalParser()
    event = parser.parse(entry(
        "Some localized text that would otherwise be unparsed",
        JOB_TYPE="start", JOB_RESULT="done",
    ))
    assert event is not None
    assert event["signal"] == "unit_start"
    assert event["proto"] == "systemd"


def test_structured_unit_exit_fields() -> None:
    parser = JournalParser()
    event = parser.parse(entry(
        unit="vpnm-cdn.service",
        message="Process exited",
        EXIT_CODE="exited", EXIT_STATUS="137",
    ))
    assert event is not None
    assert event["signal"] == "unit_exit"
    assert event["fields"]["exit_status"] == 137
    assert event["fields"]["exit_abnormal"] is True
    assert event["profile"] == "cdn"


def test_structured_unit_exit_zero_is_not_abnormal() -> None:
    parser = JournalParser()
    event = parser.parse(entry(EXIT_CODE="exited", EXIT_STATUS="0"))
    assert event is not None
    assert event["signal"] == "unit_exit"
    assert "exit_abnormal" not in event.get("fields", {})


def test_awg_lifecycle_keeps_amneziawg_proto() -> None:
    parser = JournalParser()
    event = parser.parse(entry(
        unit="awg-quick@laptop.service", message=None,
        JOB_TYPE="stop", JOB_RESULT="done",
    ))
    assert event is not None
    assert event["signal"] == "unit_stop"
    assert event["proto"] == "amneziawg"


def test_job_failed_on_failed_result() -> None:
    parser = JournalParser()
    event = parser.parse(entry(JOB_TYPE="start", JOB_RESULT="failed"))
    assert event is not None
    assert event["signal"] == "job_failed"
    assert event["fields"]["job_result"] == "failed"


# --- filtering and degradation --------------------------------------------------------


def test_awg_quick_lifecycle_lines_and_infra_noise() -> None:
    """awg-quick: add/delete → lifecycle; other shell lines → ignored_infra."""
    parser = JournalParser()
    started = parser.parse(entry(
        "[#] ip link add laptop type amneziawg",
        unit="awg-quick@laptop.service",
    ))
    stopped = parser.parse(entry(
        "[#] ip link delete dev laptop",
        unit="awg-quick@laptop.service",
    ))
    noise = parser.parse(entry(
        "[#] ip -4 address add 10.9.0.2/32 dev laptop",
        unit="awg-quick@laptop.service",
    ))
    assert started is not None and started["signal"] == "unit_start"
    assert stopped is not None and stopped["signal"] == "unit_stop"
    assert started["proto"] == "amneziawg"
    assert noise is None
    assert parser.stats.ignored_infra == 1
    assert parser.stats.app_lines == 0
    assert parser.stats.unparsed == 0


def test_unparsed_ratio_ignores_infra_lines() -> None:
    """AC-5: denominator — app lines only; awg-quick noise never dilutes it."""
    parser = JournalParser()
    parser.parse(entry("garbage app line"))
    parser.parse(entry(
        "[#] ip link set mtu 1420 up dev laptop",
        unit="awg-quick@laptop.service",
    ))
    assert parser.stats.app_lines == 1
    assert parser.stats.unparsed == 1
    assert parser.stats.ignored_infra == 1
    assert parser.stats.unparsed_ratio == pytest.approx(1.0)


def test_foreign_unit_is_ignored() -> None:
    parser = JournalParser()
    assert parser.parse(entry(unit="gnome-shell.service", message="random noise")) is None


def test_guard_unit_is_excluded() -> None:
    parser = JournalParser()
    assert parser.parse(entry(unit=GUARD_UNIT, message="whatever")) is None


def test_garbage_app_line_becomes_unparsed_not_crash() -> None:
    parser = JournalParser()
    event = parser.parse(entry("flurb \x01 ??? zzz"))
    assert event is not None
    assert event["signal"] == "unparsed"
    assert parser.stats.unparsed == 1
    assert parser.stats.app_lines == 1


def test_empty_message_without_structured_fields_is_dropped() -> None:
    parser = JournalParser()
    assert parser.parse(entry(message="")) is None
    assert parser.parse(entry(message=None)) is None


def test_malformed_entry_increments_errors_without_crash() -> None:
    parser = JournalParser()
    assert parser.parse("not-a-dict") is None  # type: ignore[arg-type]
    assert parser.parse({42: "bad", "MESSAGE": None}) is None
    assert parser.parse(entry(PRIORITY="bogus", message="connection refused"))
    assert parser.stats.errors == 1


def test_no_monotonic_timestamp_sets_clock_fallback() -> None:
    parser = JournalParser()
    raw = entry("connection reset by peer")
    del raw["__MONOTONIC_TIMESTAMP"]
    event = parser.parse(raw)
    assert event is not None
    assert event["clock_fallback"] is True
    assert event["mono_us"] == int(RT_US)
    assert event["ts"].startswith("2026-11-16T12:00:03.412")
    assert schema.validate(event, events.EVENT_SCHEMA) == []


def test_message_as_byte_array_is_decoded() -> None:
    parser = JournalParser()
    event = parser.parse(entry(message=list(b"connection refused")))
    assert event is not None
    assert event["signal"] == "conn_refused"


# --- AWG / DNS units ------------------------------------------------------------------


def test_awg_unit_gets_amneziawg_proto() -> None:
    parser = JournalParser()
    event = parser.parse(entry(
        unit="vpn-awg-split@laptop.service", message="interface laptop created",
    ))
    assert event is not None
    assert event["proto"] == "amneziawg"
    assert event["profile"] == "laptop"


def test_dnsmasq_identifier_maps_to_dns_proto() -> None:
    parser = JournalParser()
    raw = entry(unit="dnsmasq.service", message="query falcon.example.net SERVFAIL")
    del raw["_SYSTEMD_USER_UNIT"]
    raw["UNIT"] = "dnsmasq.service"
    raw["SYSLOG_IDENTIFIER"] = "dnsmasq"
    event = parser.parse(raw, source="journal:system")
    assert event is not None
    assert event["proto"] == "dns"
    assert event["signal"] == "dns_servfail"
    assert event["source"] == "journal:system"


# --- templates and stats ----------------------------------------------------------------


def test_template_normalization_and_determinism() -> None:
    left = "failed to dial to 203.0.113.10:443 after 5s"
    right = "failed to dial to 203.0.113.99:8443 after 9s"
    assert template_hash(left) == template_hash(right)
    assert "203.0.113.10" not in normalize_line(left)
    assert "<ip>" in normalize_line(left)
    assert "<n>" in normalize_line(left)


def test_template_hash_hides_uuid() -> None:
    line_a = f"client {FIXTURE_UUID} connected"
    line_b = "client <uuid> connected"
    assert template_hash(line_a) == template_hash(line_b)


def test_parse_stats_ratio() -> None:
    stats = ParseStats(lines=10, app_lines=4, unparsed=1)
    assert stats.unparsed_ratio == pytest.approx(0.25)
    assert ParseStats().unparsed_ratio == 0.0


def test_level_from_priority() -> None:
    parser = JournalParser()
    event = parser.parse(entry("connection refused", PRIORITY="3"))
    assert event is not None and event["level"] == "error"
    event = parser.parse(entry("connection refused", PRIORITY="4"))
    assert event is not None and event["level"] == "warn"


def test_scheduled_restart_is_detected() -> None:
    parser = JournalParser()
    event = parser.parse(entry(
        "vpnm-asv2.service: Scheduled restart job, restart counter is at 3",
        PRIORITY="4",
    ))
    assert event is not None
    assert event["signal"] == "unit_restart_scheduled"


# --- system journal (S2) and self-exclusion ------------------------------------------


def test_system_journal_unit_field_is_honoured() -> None:
    """S2 entries carry ``_SYSTEMD_UNIT`` and no ``_SYSTEMD_USER_UNIT``/``UNIT``."""
    parser = JournalParser()
    raw = {
        "MESSAGE": "Stopped vpn-awg-split@laptop.",
        "_SYSTEMD_UNIT": "vpn-awg-split@laptop.service",
        "JOB_TYPE": "stop",
        "JOB_RESULT": "done",
        "PRIORITY": "6",
        "__REALTIME_TIMESTAMP": RT_US,
        "__MONOTONIC_TIMESTAMP": MONO_US,
    }
    event = parser.parse(raw, source="journal:system")
    assert event is not None
    assert event["signal"] == "unit_stop"
    assert event["proto"] == "amneziawg"
    assert event["profile"] == "laptop"
    assert event["source"] == "journal:system"


def test_dnsmasq_unit_name_without_identifier_is_dns() -> None:
    parser = JournalParser()
    raw = {
        "MESSAGE": "query A falcon.example.net",
        "_SYSTEMD_UNIT": "dnsmasq.service",
        "PRIORITY": "6",
        "__REALTIME_TIMESTAMP": RT_US,
        "__MONOTONIC_TIMESTAMP": MONO_US,
    }
    event = parser.parse(raw, source="journal:system")
    assert event is not None
    assert event["proto"] == "dns"


def test_guard_own_identifier_is_excluded() -> None:
    """No self-feeding: guard's journal lines are never analysed (§6.1)."""
    parser = JournalParser()
    raw = entry("G-03 high/medium vpnm-asv2 handshake->drop")
    raw["SYSLOG_IDENTIFIER"] = GUARD_IDENTIFIER
    del raw["_SYSTEMD_USER_UNIT"]
    raw["_SYSTEMD_UNIT"] = GUARD_UNIT
    assert parser.parse(raw) is None


# --- structured-first level ------------------------------------------------------------


def test_json_payload_level_beats_priority() -> None:
    """An app JSON ``level`` is structured evidence, journald PRIORITY is not."""
    parser = JournalParser()
    event = parser.parse(entry('{"level":"error","msg":"connection refused"}',
                               PRIORITY="6"))
    assert event is not None
    assert event["level"] == "error"


def test_unparsable_json_level_falls_back_to_priority() -> None:
    parser = JournalParser()
    event = parser.parse(entry('{"level":"megafatal","msg":"connection refused"}',
                               PRIORITY="4"))
    assert event is not None
    assert event["level"] == "warn"


# --- endpoint allowlist ------------------------------------------------------------------


def test_loopback_endpoint_is_not_recorded() -> None:
    parser = JournalParser(proto_hints={"vpnm-asv2.service": "hysteria2"})
    event = parser.parse(entry("Hysteria2 client mode, socks5://127.0.0.1:1080"))
    assert event is not None
    assert event["signal"] == "app_banner_ok"
    assert "endpoint" not in event  # our own listener, not a server endpoint


def test_remote_endpoint_is_recorded() -> None:
    parser = JournalParser()
    event = parser.parse(entry("dialing to asv2.example.net:49222"))
    assert event is not None
    assert event["endpoint"] == {"host": "asv2.example.net", "port": 49222}


def test_out_of_range_port_is_ignored() -> None:
    parser = JournalParser()
    event = parser.parse(entry("dialing to asv2.example.net:70000"))
    assert event is not None
    assert "endpoint" not in event


# --- banner / close / dns precision (false-positive guards) ------------------------------


@pytest.mark.parametrize("line", [
    "client started",
    "session started, reusing it",
    "[debug] started goroutine pool",
])
def test_generic_started_is_not_a_banner(line: str) -> None:
    """A false banner would let G-01 fire as a real `tunnel_down`."""
    parser = JournalParser()
    event = parser.parse(entry(line))
    assert event is not None
    assert event["signal"] == "unparsed"


@pytest.mark.parametrize("line,expected", [
    ("connection closed normally", "conn_closed"),
    ("disconnected from server", "conn_closed"),
    ("unexpected EOF", "conn_eof"),
    ("read: connection closed by peer", "conn_eof"),
    ("read tcp: connection reset by peer", "conn_reset"),
])
def test_close_vs_eof_vs_reset(line: str, expected: str) -> None:
    parser = JournalParser()
    event = parser.parse(entry(line))
    assert event is not None
    assert event["signal"] == expected, line


@pytest.mark.parametrize("line", [
    "template not found in registry",
    "configuration file not found",
])
def test_generic_not_found_is_not_dns(line: str) -> None:
    """A generic "not found" must not count towards G-06 dns_fail_count."""
    parser = JournalParser()
    event = parser.parse(entry(line))
    assert event is not None
    assert event["signal"] == "unparsed"


# --- bounded messages (R-6: one line cannot stall the analyzer) --------------------------


def test_huge_line_is_clipped_and_cheap() -> None:
    parser = JournalParser()
    junk = ("11111111-2222-" * 20000)[:200_000]  # 200 kB adversarial run
    started = time.perf_counter()
    event = parser.parse(entry(junk))
    elapsed = time.perf_counter() - started
    assert event is not None
    assert schema.validate(event, events.EVENT_SCHEMA) == []
    # Generous bound: catches catastrophic backtracking, not CI jitter.
    assert elapsed < 2.0, f"200 kB line took {elapsed:.2f}s"
    # The stored fingerprint is computed over the clipped message only.
    assert event["template"] == template_hash(junk[:MAX_MESSAGE_CHARS])
    assert event["template"] == template_hash(junk)


def test_clipped_line_hash_is_deterministic() -> None:
    parser = JournalParser()
    junk = "a-" * 100_000
    first = parser.parse(entry(junk))
    second = JournalParser().parse(entry(junk))
    assert first is not None and second is not None
    assert first["template"] == second["template"]


# --- golden replay: fixed corpus → fixed events (R-2) -----------------------------------


GOLDEN_CORPUS = [
    ("Xray 1.8.24 started", "app_banner_ok"),
    ('{"level":"warning","msg":"failed to dial to 203.0.113.7:443: i/o timeout"}',
     "dial_timeout"),
    ("Hysteria2 client mode, socks5://127.0.0.1:1080", "app_banner_ok"),
    ("timeout: no recent network activity", "quic_no_recent_activity"),
    ("authentication failed for user", "auth_fail"),
    ("resolve falcon.example.net: no such host", "dns_nxdomain"),
    ("listen tcp :8443: address already in use", "address_in_use"),
    ("tunnel established", "conn_established"),
    ("флурб ??? zzz", "unparsed"),
]


def _replay(corpus: list) -> list:
    parser = JournalParser(proto_hints={"vpnm-asv2.service": "xray"},
                           now_us=1_794_830_403_412_345)
    out = []
    for message, _ in corpus:
        event = parser.parse(entry(message))
        assert event is not None, message
        out.append(event)
    return out


def test_golden_corpus_signals() -> None:
    events_out = _replay(GOLDEN_CORPUS)
    assert [event["signal"] for event in events_out] == [
        expected for _, expected in GOLDEN_CORPUS]
    for event in events_out:
        assert schema.validate(event, events.EVENT_SCHEMA) == []
        assert len(event["template"]) == 12
        assert event["count"] == 1


def test_golden_replay_is_byte_identical() -> None:
    first = json.dumps(_replay(GOLDEN_CORPUS), sort_keys=True, ensure_ascii=False)
    second = json.dumps(_replay(GOLDEN_CORPUS), sort_keys=True, ensure_ascii=False)
    assert first == second


def test_missing_realtime_is_pinned_by_now_us() -> None:
    """Determinism hook: no wall clock inside the parser when now_us is set."""
    parser = JournalParser(now_us=1_794_830_403_412_345)
    raw = entry("connection refused")
    del raw["__REALTIME_TIMESTAMP"]
    del raw["__MONOTONIC_TIMESTAMP"]
    event = parser.parse(raw)
    assert event is not None
    assert event["ts"] == "2026-11-16T12:00:03.412Z"
    assert event["clock_fallback"] is True


# --- contract drift guards ---------------------------------------------------------------

#: Lifecycle signals come from structured systemd fields, not from regex.
STRUCTURED_SIGNALS = {"unit_start", "unit_stop", "unit_exit", "job_failed"}
#: Signals neither parsed nor derived: emitted by reader/rules layers.
LAYER_SIGNALS = {"unparsed", "source_lost", "clock_skew"}


def test_event_keys_are_contract_keys() -> None:
    parser = JournalParser()
    event = parser.parse(entry('{"level":"error","msg":"connection refused"}'))
    assert event is not None
    allowed = set(schema.load_schema(events.EVENT_SCHEMA)["properties"])
    assert set(event) <= allowed


def test_catalogue_is_fully_accounted_for() -> None:
    """Every frozen signal is parsed, derived or emitted by a documented layer."""
    from vpnm.guard import parse as parse_module

    families = {signal for signal, _ in parse_module._SIGNAL_FAMILIES}
    missing = (set(events.SIGNALS) - families - STRUCTURED_SIGNALS
               - set(events.DERIVED_SIGNALS) - LAYER_SIGNALS)
    assert missing == set()
    assert families <= set(events.SIGNALS)


# --- property: arbitrary junk must never crash (§14.1 property level) ----------------

_JUNK_ALPHABET = (
    "abcXYZ019 \t\n\"'{}[]:/.@%+-#_=<>,;|\\"
    "\x00\x01\x7f\xc3\x28"
    "ж漢🙂"
)


def _junk(rng: random.Random, max_len: int = 400) -> str:
    return "".join(rng.choice(_JUNK_ALPHABET)
                   for _ in range(rng.randint(0, max_len)))


def test_random_junk_never_crashes_and_stays_valid() -> None:
    rng = random.Random(20261116)  # fixed seed: reproducible property run
    parser = JournalParser()
    for _ in range(400):
        message = _junk(rng)
        event = parser.parse(entry(message))
        if event is None:
            continue
        assert schema.validate(event, events.EVENT_SCHEMA) == [], message
        assert len(event["template"]) == 12
        for value in event.get("fields", {}).values():
            assert isinstance(value, (str, int, float, bool))
            assert not isinstance(value, str) or len(value) <= 32


@pytest.mark.parametrize("junk", [
    "\x00\x01\x02 control bytes",
    "ж" * 5000,
    '{"broken json',
    "a" * 10000,
    f"uuid {FIXTURE_UUID} and password=FIXTURE_PW_x in line",
    "half-uuid 11111111-2222-",
    '{"level": 42, "msg": null}',
    "{}",
])
def test_fuzz_lines_never_crash(junk: str) -> None:
    parser = JournalParser()
    event = parser.parse(entry(junk))
    if event is not None:
        assert schema.validate(event, events.EVENT_SCHEMA) == []
        # The line itself is never stored: only signal/template/endpoint.
        assert junk not in json.dumps(event, ensure_ascii=False)


def test_stats_account_for_every_line() -> None:
    parser = JournalParser()
    for message in ("Xray 1.8.24 started", "flurb zzz", ""):
        parser.parse(entry(message))
    parser.parse(entry(unit="gnome-shell.service", message="noise"))
    parser.parse("not-a-dict")  # type: ignore[arg-type]  # deliberate bad input
    assert parser.stats.lines == 5
    assert parser.stats.app_lines == 2  # empty message is not an app line
    assert parser.stats.parsed == 1
    assert parser.stats.unparsed == 1
    assert parser.stats.errors == 1
