"""WP-G6 field checkpoint (2026-11-17): the four defects seen on live data.

Everything is synthetic: explicit ``mono_us`` timestamps, no root, no
journald, no live units, no network.  Fixtures use RFC 5737 addresses only
(AGENTS §6).  One regression scenario per defect plus the "neighbouring rule
still works" pairs.

Defects:
- G-09 attributed an auxiliary unit's resets (``cloudflared-proxy-dns``) to a
  silent VPN endpoint;
- the dedup key used the *triggering event's* endpoint instead of the endpoint
  the finding reports, so one physical fault could emit several findings;
- ``vpn guard findings`` returned file order, not chronology;
- G-12 called a deliberately stopped AWG a "lost source" (and resume/skew
  artifacts as well).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import events, rules, schema, store  # noqa: E402
from vpnm.guard.rules import RuleEngine, RuleOutcome  # noqa: E402
from vpnm.guard.window import MONO_US_PER_S, Clock  # noqa: E402

US = MONO_US_PER_S
BASE = 1_000_000_000
UNIT = "vpnm-asv2.service"
XRAY_SPLIT = "vpn-xray-split@asv.service"
AWG_UNIT = "vpn-awg-split@laptop.service"
EP = {"host": "203.0.113.10", "port": 443}
EP2 = {"host": "203.0.113.11", "port": 443}


def mono(seconds: float) -> int:
    return BASE + int(seconds * US)


def ts(seconds: float) -> str:
    total = int(round(seconds))
    return f"2026-11-17T12:{total // 60 % 60:02d}:{total % 60:02d}.000Z"


def ev(signal: str, seconds: float, *, unit: str = UNIT,
       endpoint: dict | None = None, count: int = 1,
       proto: str = "xray", **extra: object) -> dict:
    payload: dict = {
        "schema": "guard.event/1",
        "ts": ts(seconds),
        "mono_us": mono(seconds),
        "source": "journal:user",
        "unit": unit,
        "profile": "asv2",
        "proto": proto,
        "level": "info",
        "signal": signal,
        "template": "3f9a1c02be77",
        "count": count,
        "clock_fallback": False,
        "invocation_id": "inv-1",
    }
    if endpoint is not None:
        payload["endpoint"] = endpoint
    payload.update(extra)
    return payload


def engine() -> RuleEngine:
    return RuleEngine(clock=Clock(BASE))


def run(target: RuleEngine, *events_in: dict) -> list[RuleOutcome]:
    outcomes: list[RuleOutcome] = []
    for item in events_in:
        outcomes.extend(target.feed(item))
    return outcomes


def findings(outcomes: list[RuleOutcome], rule: str | None = None) -> list[dict]:
    found = [item.finding for item in outcomes
             if item.is_finding and item.finding is not None]
    if rule is not None:
        found = [item for item in found if item["rule"] == rule]
    return found


def outcome(outcomes: list[RuleOutcome], rule: str,
            status: str) -> dict | None:
    for item in outcomes:
        if item.rule == rule and item.status == status:
            return item.as_dict()
    return None


def assert_valid(finding: dict) -> None:
    assert schema.validate(finding, events.FINDING_SCHEMA) == [], finding
    assert len(finding["id"]) == 12


def startup(target: RuleEngine) -> None:
    run(target, ev("unit_start", 0.0), ev("app_banner_ok", 1.0))


def silent(target: RuleEngine, unit: str, *, count: int = 5,
           signal: str = "dial_timeout", endpoint: dict = EP,
           start: float = 10.0) -> list[RuleOutcome]:
    outcomes: list[RuleOutcome] = []
    for index in range(count):
        outcomes.extend(run(target, ev(signal, start + index, unit=unit,
                                       endpoint=endpoint)))
    return outcomes


# --- G-09: transport-only -------------------------------------------------------


def test_g09_ignores_auxiliary_service_resets() -> None:
    """cloudflared/dnsmasq/zapret resets are local context, not a VPN endpoint."""
    for unit in ("cloudflared-proxy-dns.service", "dnsmasq.service",
                 "zapret.service", "NetworkManager.service"):
        target = engine()
        startup(target)
        outcomes = silent(target, unit, signal="conn_reset")
        assert findings(outcomes, "G-09") == [], unit


def test_g09_still_fires_for_a_silent_vpn_transport() -> None:
    """Regression: the transport filter must not mute the real case."""
    for unit in (UNIT, XRAY_SPLIT, "vpn-awg-split@laptop.service",
                 "awg-quick@laptop.service"):
        target = engine()
        startup(target)
        found = findings(silent(target, unit), "G-09")
        assert len(found) == 1, unit
        assert_valid(found[0])
        assert found[0]["endpoint"] == EP
        assert found[0]["category"] == "endpoint_unreachable"


def test_is_vpn_transport_unit_classification() -> None:
    assert rules.is_vpn_transport_unit(UNIT)
    assert rules.is_vpn_transport_unit(XRAY_SPLIT)
    assert rules.is_vpn_transport_unit("vpn-relay-reality.service")
    assert not rules.is_vpn_transport_unit("cloudflared-proxy-dns.service")
    assert not rules.is_vpn_transport_unit("dnsmasq.service")
    assert not rules.is_vpn_transport_unit(None)
    assert not rules.is_vpn_transport_unit("")


# --- G-09: dedup inside a batch -------------------------------------------------


def test_g09_one_fault_one_finding_across_upstream_endpoints() -> None:
    """The dedup key follows the reported endpoint, not the trigger event.

    Four failures on EP and one on EP2 are fed one by one; when the window
    resolves to EP, an event carrying EP2 must reload the *same* cooldown key
    instead of minting a second finding for the same silent endpoint.
    """
    target = engine()
    startup(target)
    outcomes = silent(target, UNIT, count=4, endpoint=EP)
    outcomes += run(target, ev("dial_timeout", 14.0, endpoint=EP))
    assert len(findings(outcomes, "G-09")) == 1
    # one stray attempt on another upstream IP of the same unit: the reported
    # endpoint (EP) is unchanged, so no second finding may appear
    outcomes = run(target, ev("dial_timeout", 15.0, endpoint=EP2))
    assert findings(outcomes, "G-09") == []


def test_g09_same_batch_does_not_repeat_the_same_key() -> None:
    """Five pre-handshake failures in one batch → exactly one finding."""
    target = engine()
    startup(target)
    batch = [ev("conn_reset", 10.0 + index * 0.1) for index in range(5)]
    for item in batch:
        item["endpoint"] = EP
    outcomes: list[RuleOutcome] = []
    for item in batch:
        outcomes.extend(target.feed(item))
    found = findings(outcomes, "G-09")
    assert len(found) == 1
    assert found[0]["count"] == 5
    # a sixth failure on the same key stays silent (hysteresis/cooldown)
    assert findings(run(target, ev("dial_timeout", 14.0)), "G-09") == []


# --- findings chronology --------------------------------------------------------


def test_findings_read_is_strict_chronology(tmp_path: Path) -> None:
    target = store.FindingsStore(tmp_path / "findings.jsonl", segment_bytes=4096)
    # written out of order on purpose (the guard may fire rules in any order)
    target.append({"id": "b", "window": {"last": "2026-11-17T12:00:05Z"}})
    target.append({"id": "a", "window": {"last": "2026-11-17T12:00:01Z"}})
    target.append({"id": "c", "window": {"last": "2026-11-17T12:00:09Z"}})
    assert [item["id"] for item in target.read(limit=0)] == ["c", "b", "a"]


def test_findings_read_breaks_ties_by_insertion_order(tmp_path: Path) -> None:
    target = store.FindingsStore(tmp_path / "findings.jsonl", segment_bytes=4096)
    same = "2026-11-17T12:00:05Z"
    target.append({"id": "first", "window": {"last": same}})
    target.append({"id": "second", "window": {"last": same}})
    # newest-first: the later appended record comes first, stably
    assert [item["id"] for item in target.read(limit=0)] == ["second", "first"]


def test_findings_read_orders_across_rotated_segments(tmp_path: Path) -> None:
    path = tmp_path / "findings.jsonl"
    target = store.FindingsStore(path, segment_bytes=120, max_segments=8)
    for index in range(6):
        target.append({"id": f"id{index}",
                       "window": {"last": f"2026-11-17T12:00:0{index}Z"}})
    assert len(target.segment_paths()) > 1  # rotation actually happened
    assert [item["id"] for item in target.read(limit=0)] == [
        "id5", "id4", "id3", "id2", "id1", "id0"]


# --- G-06 vs G-07 ---------------------------------------------------------------


def test_g06_suppressed_by_g07_in_any_batch_order() -> None:
    """G-07 has priority inside one drain regardless of order.

    A drain is the unit of "the whole batch is known": events that the
    reorder buffer releases together cannot blame DNS when the same drain
    also carried "no route", whatever their in-drain order.
    """
    batch = [ev("dns_lookup_fail", 6.0), ev("dns_timeout", 6.01),
             ev("network_unreachable", 6.02)]
    target = engine()
    startup(target)
    target.seed(batch)  # same window, then one tick
    outcomes = target.tick(mono(7))
    assert findings(outcomes, "G-06") == []
    assert findings(outcomes, "G-07")
    assert outcome(outcomes, "G-06", "suppressed")["reason"] == "local_net_down"


def test_g06_then_g07_incident_root_is_local_net() -> None:
    """G-06 may fire before the route is lost; the incident is still G-07.

    Nothing is retracted: the DNS finding was true when it fired.  The
    correlation priority (§9.2) guarantees the incident is attributed to the
    local network, not to DNS.
    """
    from vpnm.guard.correlate import Correlator

    target = engine()
    startup(target)
    outcomes = run(target, ev("dns_lookup_fail", 6.0),
                   ev("dns_timeout", 7.0), ev("network_unreachable", 8.0))
    found = findings(outcomes)
    assert {item["rule"] for item in found} == {"G-06", "G-07"}
    correlator = Correlator()
    incident: dict = {}
    for item in found:
        incident = correlator.note(item, mono(9))
    assert incident["root_cause"] == "local_net"


def test_g06_suppressed_by_g07_in_backfill_window() -> None:
    """``run --once``: seed the window, then one tick sees both causes."""
    target = engine()
    startup(target)
    target.seed([ev("dns_lookup_fail", 6.0), ev("dns_timeout", 7.0),
                 ev("network_unreachable", 8.0)])
    outcomes = target.tick(mono(9))
    assert findings(outcomes, "G-06") == []
    assert findings(outcomes, "G-07")
    assert outcome(outcomes, "G-06", "suppressed")["reason"] == "local_net_down"


def test_g06_still_fires_for_real_dns_breakage_without_g07() -> None:
    """Regression: no local-network events → DNS finding stays."""
    target = engine()
    startup(target)
    found = findings(run(target, ev("dns_lookup_fail", 5.0),
                         ev("dns_timeout", 6.0)), "G-06")
    assert len(found) == 1
    assert_valid(found[0])
    assert found[0]["category"] == "dns"


def test_g06_without_names_is_honest() -> None:
    """distinct_names=0 must read "not extracted", not "0 names broken"."""
    target = engine()
    startup(target)
    found = findings(run(target, ev("dns_lookup_fail", 5.0, template=None),
                         ev("dns_timeout", 6.0, template=None)), "G-06")
    assert len(found) == 1
    evidence = {item["metric"]: item["value"] for item in found[0]["evidence"]
                if item.get("kind") == "ratio"}
    assert evidence["distinct_names"] == 0
    assert any("not extracted" in check for check in found[0]["fp_checks"])


# --- G-12: AWG off is not a lost source ----------------------------------------


def test_g12_awg_lost_is_ignored_when_awg_is_not_expected() -> None:
    target = engine()
    target.note_awg_expected(False)
    target.note_source_status("sysfs:awg", "lost", mono(0), ts(0))
    assert findings(target.tick(mono(61)), "G-12") == []


def test_g12_awg_lost_still_reported_when_awg_is_expected() -> None:
    target = engine()
    target.note_awg_expected(True)
    target.note_source_status("sysfs:awg", "lost", mono(0), ts(0))
    found = findings(target.tick(mono(61)), "G-12")
    assert len(found) == 1
    assert any(item.get("metric") == "source_lost"
               for item in found[0]["evidence"])


def test_g12_awg_off_by_lifecycle_is_not_a_lost_source() -> None:
    """Without mode.json the last AWG lifecycle decides: unit_stop = off."""
    stopped = engine()
    run(stopped, ev("unit_start", 0.0, unit=AWG_UNIT, proto="amneziawg"))
    run(stopped, ev("unit_stop", 5.0, unit=AWG_UNIT, proto="amneziawg"))
    stopped.note_source_status("sysfs:awg", "lost", mono(6), ts(6))
    assert findings(stopped.tick(mono(80)), "G-12") == []

    running = engine()
    run(running, ev("unit_start", 0.0, unit=AWG_UNIT, proto="amneziawg"))
    running.note_source_status("sysfs:awg", "lost", mono(6), ts(6))
    assert len(findings(running.tick(mono(80)), "G-12")) == 1


def test_g12_other_source_lost_is_not_muted_by_the_awg_filter() -> None:
    target = engine()
    target.note_awg_expected(False)
    target.note_source_status("journal:user", "lost", mono(0), ts(0))
    found = findings(target.tick(mono(61)), "G-12")
    assert len(found) == 1
    assert any(item.get("metric") == "source_lost"
               for item in found[0]["evidence"])


def test_g12_source_lost_after_suspend_is_not_a_degradation() -> None:
    target = engine()
    target.note_source_status("journal:user", "lost", mono(0), ts(0))
    target.note_suspend(mono(50))
    assert findings(target.tick(mono(61)), "G-12") == []


def test_g12_source_lost_with_clock_skew_reports_the_skew_only() -> None:
    target = engine()
    target.note_source_status("journal:user", "lost", mono(0), ts(0))
    target.note_clock_skew(120.0, mono(50))
    found = findings(target.tick(mono(61)), "G-12")
    assert len(found) == 1
    metrics = {item.get("metric") for item in found[0]["evidence"]}
    assert "clock_skew" in metrics
    assert "source_lost" not in metrics


# --- runner: expected AWG from mode.json ---------------------------------------


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPN_MANAGER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VPN_MANAGER_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setenv("VPN_MANAGER_CONFIG_DIR", str(tmp_path / "config"))


def _runner() -> object:
    from vpnm.guard.runner import GuardRunner

    return GuardRunner(engine=engine(), restore_speed_ring=False)


def test_runner_reads_expected_awg_from_mode_json(isolated_env: None) -> None:
    from vpnm import mode

    runner = _runner()
    assert runner._awg_expected() is None  # no mode.json at all

    assert mode.write_mode("relay", proto="amneziawg", iface="relay")
    assert runner._awg_expected() is True

    assert mode.write_mode("direct", proto="xray", iface="xray0")
    assert runner._awg_expected() is False

    assert mode.write_mode("none")
    assert runner._awg_expected() is False


# --- IPv6 ENETUNREACH: не G-07 и не маскировка настоящего G-06 (2026-11-17) ---------


def test_ipv6_unreachable_burst_neither_fires_g07_nor_hides_g06() -> None:
    """IPv6-«network is unreachable» — «нет v6», а не падение локальной сети.

    Полевой случай: cloudflared на IPv4-only ноутбуке долбится в
    [2606:4700:4700::1111] каждые ~5 с. Раньше это давало G-07 high, а через
    batch-приоритет ещё и глушило настоящий G-06 (IPv4 DNS).
    """
    from vpnm.guard.parse import JournalParser

    parser = JournalParser()
    cf = "cloudflared-proxy-dns.service"
    v6_line = ('failed to connect to an HTTPS backend "https://[2606:4700:4700'
               '::1111]/dns-query": Post "https://[2606:4700:4700::1111]'
               '/dns-query": dial tcp [2606:4700:4700::1111]:443: connect: '
               'network is unreachable')

    def journal(message: str) -> dict:
        return {
            "MESSAGE": message,
            "PRIORITY": "3",
            "__REALTIME_TIMESTAMP": "1794830403412345",
            "__MONOTONIC_TIMESTAMP": "812345678",
            "_SYSTEMD_UNIT": cf,
            "_SYSTEMD_INVOCATION_ID": "fixture-invocation",
        }

    parsed = [parser.parse(journal(v6_line)) for _ in range(8)]
    assert parsed == [None] * 8          # парсер отбросил их до правил
    assert parser.stats.unparsed == 0

    target = engine()
    startup(target)
    outcomes = run(
        target,
        ev("dns_timeout", 10.0, unit=cf, proto="dns"),
        ev("dns_timeout", 11.0, unit=cf, proto="dns"),
        ev("dns_timeout", 12.0, unit=cf, proto="dns"),
    )
    assert findings(outcomes, "G-07") == []
    assert findings(outcomes, "G-06"), "настоящий IPv4-DNS сбой должен выжить"
