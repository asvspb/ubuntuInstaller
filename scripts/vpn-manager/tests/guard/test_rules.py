"""WP-G3 tests: rules G-01…G-08 (SPEC §8.2/§8.3, AC-1…AC-4, AC-9/13/14/15).

Everything is synthetic: no root, no network, no journald, no wall clock.
Events carry explicit ``mono_us``; the only inputs are the event stream and
the context hooks (state/activity/route/suspend/AWG windows).  Fixtures use
RFC 5737 addresses only (AGENTS §6).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import events, redact, schema  # noqa: E402
from vpnm.guard.rules import (  # noqa: E402
    RULES_IN_ORDER,
    RULE_TRIGGERS,
    RuleEngine,
    RuleOutcome,
    ServerContext,
    Thresholds,
    build_finding,
    finding_schema_hash,
)
from vpnm.guard.window import Clock, MONO_US_PER_S  # noqa: E402

US = MONO_US_PER_S
BASE = 1_000_000_000
UNIT = "vpnm-asv2.service"
AWG_UNIT = "vpn-awg-split@laptop.service"
EP = {"host": "203.0.113.10", "port": 443}
EP2 = {"host": "203.0.113.11", "port": 443}


def mono(seconds: float) -> int:
    return BASE + int(seconds * US)


def ts(seconds: float) -> str:
    total = int(round(seconds))
    return f"2026-11-16T12:{total // 60 % 60:02d}:{total % 60:02d}.000Z"


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


def engine(*, thresholds: Thresholds | None = None,
           server: ServerContext | None = None) -> RuleEngine:
    return RuleEngine(thresholds=thresholds, server=server, clock=Clock(BASE))


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


def outcome(outcomes: list[RuleOutcome], rule: str, status: str) -> dict | None:
    for item in outcomes:
        if item.rule == rule and item.status == status:
            return item.as_dict()
    return None


def assert_valid(finding: dict) -> None:
    assert schema.validate(finding, events.FINDING_SCHEMA) == [], finding
    assert len(finding["id"]) == 12
    assert finding["schema_hash"] == finding_schema_hash()
    assert redact.scan_secrets(finding) == []


def startup(target: RuleEngine, banner: bool = True) -> None:
    run(target, ev("unit_start", 0.0))
    if banner:
        run(target, ev("app_banner_ok", 1.0))


# --- catalogue -----------------------------------------------------------------


def test_every_wp_g3_rule_has_a_trigger_and_a_handler() -> None:
    assert RULES_IN_ORDER == events.RULES[:8]
    for rule in RULES_IN_ORDER:
        assert rule in RULE_TRIGGERS
        assert RULE_TRIGGERS[rule]
    probe = engine()
    for rule in RULES_IN_ORDER:
        assert hasattr(probe, "_" + rule.lower().replace("-", ""))


def test_thresholds_from_mapping_overrides_and_ignores_garbage() -> None:
    tuned = Thresholds.from_mapping({
        "handshake_timeout_s": 30, "reset_storm_count": 4,
        "awg_rx_ratio": 0.1, "unknown_key": 1, "dns_fail_count": "x",
        "confirm_windows": True,
    })
    assert tuned.handshake_timeout_s == 30
    assert tuned.reset_storm_count == 4
    assert tuned.awg_rx_ratio == 0.1
    assert tuned.dns_fail_count == Thresholds().dns_fail_count
    assert tuned.confirm_windows == Thresholds().confirm_windows
    assert Thresholds.from_mapping(None) == Thresholds()
    assert Thresholds().as_dict()["udp_blackhole_count"] == 2


def test_server_context_rejects_unknown_threat_level() -> None:
    with pytest.raises(ValueError):
        ServerContext(threat_level="apocalyptic", fresh=True)
    assert ServerContext(threat_level="high", fresh=True).boosts is True
    assert ServerContext(threat_level="high", fresh=False).boosts is False
    assert ServerContext(fresh=True,
                         lockdown=True).lockdown_expected is True


def test_build_finding_id_is_stable_and_endpoint_sensitive() -> None:
    kwargs = dict(rule="G-03", unit=UNIT, profile="asv2", endpoint=EP,
                  window_first=ts(0), window_last=ts(10), count=3,
                  evidence=[{"kind": "signal_count", "signal": "conn_reset",
                             "value": 3}],
                  fp_checks=["no auth_fail"], severity="high",
                  confidence="medium", category="censorship_suspect",
                  stage_hint="data_phase", hypothesis="h", action="a")
    left = build_finding(**kwargs)
    right = build_finding(**kwargs)
    assert left["id"] == right["id"]
    other = build_finding(**{**kwargs, "endpoint": EP2})
    assert other["id"] != left["id"]
    assert_valid(left)


# --- G-01 no_handshake_after_start --------------------------------------------


def test_g01_fires_with_banner_coverage_after_timeout() -> None:
    target = engine()
    startup(target)
    assert findings(target.tick(mono(20))) == []           # 25 s not reached
    assert outcome(target.tick(mono(26)), "G-01", "pending")
    found = findings(target.tick(mono(31)), "G-01")
    assert len(found) == 1
    finding = found[0]
    assert_valid(finding)
    assert finding["severity"] == "high"
    assert finding["confidence"] == "medium"
    assert finding["category"] == "tunnel_down"
    assert finding["stage_hint"] == "pre_handshake"
    assert finding["window"]["first"] == ts(0.0)
    assert "app_banner_ok" in {item["signal"] for item in finding["evidence"]
                               if item["kind"] == "signal_count"}


def test_g01_fires_with_dial_coverage() -> None:
    target = engine()
    run(target, ev("unit_start", 0.0), ev("dial_start", 5.0))
    target.tick(mono(26))
    found = findings(target.tick(mono(31)), "G-01")
    assert len(found) == 1
    assert "dial_start" in {item["signal"] for item in found[0]["evidence"]
                            if item["kind"] == "signal_count"}
    assert any("coverage proven" in check for check in found[0]["fp_checks"])


def test_g01_without_banner_or_dial_is_insufficient_data() -> None:
    target = engine()
    run(target, ev("unit_start", 0.0))
    outcomes = target.tick(mono(26))
    assert findings(outcomes) == []
    report = outcome(outcomes, "G-01", "insufficient_data")
    assert report is not None and report["reason"] == "no_app_banner"
    assert target.insufficient[0]["reason"] == "no_app_banner"
    # reported once, not on every tick
    assert outcome(target.tick(mono(31)), "G-01", "insufficient_data") is None
    assert findings(target.tick(mono(36))) == []


def test_g01_without_banner_but_with_network_unreachable() -> None:
    target = engine()
    run(target, ev("unit_start", 0.0), ev("network_unreachable", 3.0))
    report = outcome(target.tick(mono(26)), "G-01", "insufficient_data")
    assert report is not None and report["reason"] == "network_unreachable"


def test_g01_does_not_fire_when_handshake_happened() -> None:
    target = engine()
    run(target, ev("unit_start", 0.0), ev("app_banner_ok", 1.0),
        ev("handshake_ok", 4.0, endpoint=EP))
    for moment in (26, 31, 36):
        assert findings(target.tick(mono(moment)), "G-01") == []


def test_g01_planned_stop_is_not_a_finding() -> None:
    target = engine()
    run(target, ev("unit_start", 0.0), ev("app_banner_ok", 1.0),
        ev("unit_stop", 10.0))
    assert findings(target.tick(mono(26)), "G-01") == []
    assert target.lifecycle == {"signal": "unit_stop", "ts": ts(10),
                                "cause": "vpn_down"}


def test_g01_ignores_another_invocation_instance() -> None:
    target = engine()
    run(target, ev("unit_start", 0.0))
    run(target, ev("app_banner_ok", 1.0, invocation_id="inv-2"))
    report = outcome(target.tick(mono(26)), "G-01", "insufficient_data")
    assert report is not None and report["reason"] == "no_app_banner"


def test_lifecycle_causes() -> None:
    target = engine()
    assert target.lifecycle is None
    run(target, ev("unit_start", 0.0))
    assert target.lifecycle == {"signal": "unit_start", "ts": ts(0),
                                "cause": "vpn_up"}
    run(target, ev("unit_start", 1.0, unit="vpnm-other.service"))
    assert target.lifecycle["cause"] == "vpn_switch"
    run(target, ev("unit_exit", 2.0,
                   fields={"exit_status": 1, "exit_abnormal": True}))
    assert target.lifecycle["cause"] == "unknown"  # G-10 owns this


# --- G-02 reset_storm ----------------------------------------------------------


def test_g02_reset_storm_fires_once_with_count() -> None:
    target = engine()
    startup(target)
    run(target, ev("handshake_ok", 2.0, endpoint=EP))
    found = findings(run(
        target,
        ev("conn_reset", 10.0, endpoint=EP),
        ev("conn_reset", 11.0, endpoint=EP),
        ev("conn_reset", 12.0, endpoint=EP)), "G-02")
    assert len(found) == 1
    finding = found[0]
    assert_valid(finding)
    assert finding["count"] == 3
    assert finding["severity"] == "high"
    assert finding["category"] == "censorship_suspect"
    assert finding["stage_hint"] == "post_handshake"
    assert finding["endpoint"] == EP
    assert finding["alternatives"]
    assert any(item["kind"] == "ratio" for item in finding["evidence"])


def test_g02_needs_a_handshake_in_the_last_10_minutes() -> None:
    target = engine()
    startup(target)
    found = findings(run(
        target,
        ev("conn_reset", 10.0, endpoint=EP),
        ev("conn_reset", 11.0, endpoint=EP),
        ev("conn_reset", 12.0, endpoint=EP)), "G-02")
    assert found == []  # pre-handshake resets are G-09
    assert findings(target.tick(mono(20)), "G-02") == []


def test_g02_counts_one_endpoint_only() -> None:
    target = engine()
    startup(target)
    run(target, ev("handshake_ok", 2.0, endpoint=EP))
    outcomes = run(
        target,
        ev("conn_reset", 10.0, endpoint=EP),
        ev("conn_reset", 11.0, endpoint=EP2),
        ev("conn_reset", 12.0, endpoint=EP2))
    # the biggest per-endpoint group is 2 < reset_storm_count: no finding
    assert findings(outcomes, "G-02") == []
    assert findings(target.tick(mono(20)), "G-02") == []


def test_g02_defers_to_g03_when_drops_follow_handshakes() -> None:
    target = engine()
    startup(target)
    outcomes: list[RuleOutcome] = []
    for index in range(3):
        moment = 10 + index * 10
        outcomes.extend(run(target, ev("handshake_ok", moment, endpoint=EP),
                            ev("conn_reset", moment + 1, endpoint=EP)))
    # one storm → exactly one finding (AC-2): G-03 owns the data-phase shape
    assert findings(outcomes, "G-02") == []
    assert outcome(outcomes, "G-02", "suppressed")["reason"] == \
        "post_handshake_drop"
    assert len(findings(outcomes, "G-03")) == 1


def test_g02_repeats_inside_cooldown_are_suppressed() -> None:
    target = engine()
    startup(target)
    run(target, ev("handshake_ok", 2.0, endpoint=EP))
    first = findings(run(target,
                         ev("conn_reset", 10.0, endpoint=EP),
                         ev("conn_reset", 11.0, endpoint=EP),
                         ev("conn_reset", 12.0, endpoint=EP)), "G-02")
    assert len(first) == 1
    # let the condition go quiet until the resets age out and hysteresis resolves
    for moment in (80, 85, 90):
        target.tick(mono(moment))
    again = run(target,
                ev("conn_reset", 100.0, endpoint=EP),
                ev("conn_reset", 101.0, endpoint=EP),
                ev("conn_reset", 102.0, endpoint=EP))
    assert findings(again, "G-02") == []
    suppressed = outcome(again, "G-02", "suppressed")
    assert suppressed is not None and suppressed["reason"] == "cooldown"
    assert suppressed["repeats"] >= 1


def test_g02_suppressed_by_auth_fail_and_probe_marker() -> None:
    target = engine()
    startup(target)
    run(target, ev("handshake_ok", 2.0, endpoint=EP), ev("auth_fail", 5.0))
    outcomes = run(target,
                   ev("conn_reset", 10.0, endpoint=EP),
                   ev("conn_reset", 11.0, endpoint=EP),
                   ev("conn_reset", 12.0, endpoint=EP))
    assert findings(outcomes, "G-02") == []
    assert outcome(outcomes, "G-02", "suppressed")["reason"] == "auth_fail"

    other = engine()
    startup(other)
    run(other, ev("handshake_ok", 2.0, endpoint=EP))
    other.note_activity(True, mono(9))
    outcomes = run(other,
                   ev("conn_reset", 10.0, endpoint=EP),
                   ev("conn_reset", 11.0, endpoint=EP),
                   ev("conn_reset", 12.0, endpoint=EP))
    assert findings(outcomes, "G-02") == []
    assert outcome(outcomes, "G-02", "suppressed")["reason"] == "active_probe"


def test_g02_merges_coalesced_events() -> None:
    target = engine()
    startup(target)
    run(target, ev("handshake_ok", 2.0, endpoint=EP))
    found = findings(run(target,
                         ev("conn_reset", 10.0, endpoint=EP, count=3)), "G-02")
    assert len(found) == 1
    assert found[0]["count"] == 3


# --- G-03 post_handshake_drop --------------------------------------------------


def _cycles(target: RuleEngine, start: float = 10.0,
            count: int = 3) -> list[RuleOutcome]:
    outcomes: list[RuleOutcome] = []
    for index in range(count):
        moment = start + index * 10
        outcomes.extend(run(target, ev("handshake_ok", moment, endpoint=EP),
                            ev("conn_reset", moment + 1, endpoint=EP)))
    return outcomes


def test_g03_post_handshake_drop_cycles() -> None:
    target = engine()
    startup(target)
    found = findings(_cycles(target), "G-03")
    assert len(found) == 1
    finding = found[0]
    assert_valid(finding)
    assert finding["count"] == 3
    assert finding["stage_hint"] == "data_phase"
    assert finding["category"] == "censorship_suspect"
    assert finding["confidence"] == "medium"
    cycle = [item for item in finding["evidence"] if item["kind"] == "cycle"]
    assert cycle and cycle[0]["metric"] == "handshake_to_drop_s"
    assert cycle[0]["median"] == pytest.approx(1.0)
    assert "рестарт сервера" in finding["alternatives"]
    assert "локальный краш клиента" in finding["alternatives"]


def test_g03_requires_the_drop_to_be_close_to_the_handshake() -> None:
    target = engine()
    startup(target)
    outcomes: list[RuleOutcome] = []
    for index in range(3):
        moment = 10 + index * 10
        outcomes.extend(run(target, ev("handshake_ok", moment, endpoint=EP),
                            ev("conn_reset", moment + 9, endpoint=EP)))
    assert findings(outcomes, "G-03") == []
    assert findings(target.tick(mono(41)), "G-03") == []


def test_g03_becomes_high_when_the_pattern_repeats() -> None:
    target = engine()
    target.seed([ev("unit_start", 0.0), ev("app_banner_ok", 1.0)])
    target.seed([item for index in range(6)
                 for item in (ev("handshake_ok", 10 + index * 10, endpoint=EP),
                              ev("conn_reset", 11 + index * 10, endpoint=EP))])
    found = findings(target.tick(mono(61)), "G-03")
    assert len(found) == 1
    assert found[0]["confidence"] == "high"
    assert found[0]["count"] == 6


def test_g03_lockdown_makes_it_expected() -> None:
    target = engine(server=ServerContext(threat_level="high",
                                         stage="data_phase", age_s=120,
                                         fresh=True, lockdown=True))
    startup(target)
    found = findings(_cycles(target), "G-03")
    assert len(found) == 1
    finding = found[0]
    assert_valid(finding)
    assert finding["expected_lockdown"] is True
    assert finding["severity"] == "info"
    assert finding["server"]["lockdown"] is True
    assert "lock-443" in finding["action"]


def test_g03_suppressed_by_auth_fail_and_unit_flap() -> None:
    target = engine()
    startup(target)
    run(target, ev("auth_fail", 15.0))
    assert findings(_cycles(target), "G-03") == []

    flapping = engine()
    startup(flapping)
    run(flapping, ev("unit_restart_scheduled", 15.0))
    assert findings(_cycles(flapping), "G-03") == []


def test_g03_server_boost_raises_confidence_and_marks_cross_vantage() -> None:
    target = engine(server=ServerContext(threat_level="high",
                                         stage="data_phase", age_s=60,
                                         fresh=True))
    startup(target)
    finding = findings(_cycles(target), "G-03")[0]
    assert finding["confidence"] == "high"
    assert finding["cross_vantage"] is True
    assert finding["expected_lockdown"] is False


def test_stale_server_verdict_never_boosts() -> None:
    target = engine(server=ServerContext(threat_level="high", age_s=7200,
                                         fresh=False, lockdown=True))
    startup(target)
    finding = findings(_cycles(target), "G-03")[0]
    assert finding["confidence"] == "medium"
    assert finding["expected_lockdown"] is False
    assert finding["server"]["fresh"] is False


# --- G-04 udp_response_blackhole (Hysteria2) -----------------------------------


def _hy2_base(target: RuleEngine) -> None:
    run(target, ev("unit_start", 0.0, proto="hysteria2"),
        ev("app_banner_ok", 1.0, proto="hysteria2"))


def test_g04_hy2_udp_blackhole() -> None:
    target = engine()
    _hy2_base(target)
    outcomes = run(
        target,
        ev("quic_handshake_ok", 10.0, endpoint=EP, proto="hysteria2"),
        ev("quic_idle_timeout", 40.0, endpoint=EP, proto="hysteria2"),
        ev("quic_no_recent_activity", 70.0, endpoint=EP, proto="hysteria2"))
    assert findings(outcomes, "G-04") == []          # no retry proof yet
    found = findings(
        run(target, ev("quic_handshake_ok", 100.0, endpoint=EP,
                       proto="hysteria2")), "G-04")
    assert len(found) == 1
    finding = found[0]
    assert_valid(finding)
    assert finding["stage_hint"] == "udp_response"
    assert finding["count"] == 2
    assert finding["endpoint"] == EP
    assert finding["alternatives"]
    assert any("сервер не отвечает" in item
               for item in finding["alternatives"])


def test_g04_hy2_silence_without_retry_is_not_a_finding() -> None:
    target = engine()
    _hy2_base(target)
    outcomes = run(
        target,
        ev("quic_handshake_ok", 10.0, endpoint=EP, proto="hysteria2"),
        ev("quic_idle_timeout", 40.0, endpoint=EP, proto="hysteria2"),
        ev("quic_idle_timeout", 70.0, endpoint=EP, proto="hysteria2"))
    assert findings(outcomes, "G-04") == []
    assert findings(target.tick(mono(80)), "G-04") == []


def test_g04_hy2_suspend_and_route_change_are_stop_factors() -> None:
    target = engine()
    _hy2_base(target)
    run(target,
        ev("quic_handshake_ok", 10.0, endpoint=EP, proto="hysteria2"),
        ev("quic_idle_timeout", 40.0, endpoint=EP, proto="hysteria2"),
        ev("quic_idle_timeout", 70.0, endpoint=EP, proto="hysteria2"))
    target.note_suspend(mono(90))
    outcomes = run(target, ev("quic_handshake_ok", 100.0, endpoint=EP,
                              proto="hysteria2"))
    assert findings(outcomes, "G-04") == []
    assert outcome(outcomes, "G-04", "suppressed")["reason"] == "suspend/roaming"

    other = engine()
    _hy2_base(other)
    run(other,
        ev("quic_handshake_ok", 10.0, endpoint=EP, proto="hysteria2"),
        ev("quic_idle_timeout", 40.0, endpoint=EP, proto="hysteria2"),
        ev("quic_idle_timeout", 70.0, endpoint=EP, proto="hysteria2"))
    other.note_route_change(mono(95))
    outcomes = run(other, ev("quic_handshake_ok", 100.0, endpoint=EP,
                             proto="hysteria2"))
    assert findings(outcomes, "G-04") == []
    assert outcome(outcomes, "G-04", "suppressed")["reason"] == "route_change"


# --- G-04 branch awg_tx_no_rx (AC-15) -----------------------------------------


def awg_window(seconds: float, *, iface: str = "laptop",
               tx: int = 300_000, rx: int = 100) -> dict:
    return {
        "iface": iface,
        # the poller reports `time.monotonic()` seconds: the same timeline as
        # `mono_us` of the events (BASE is an offset inside that timeline)
        "mono_s": BASE / US + seconds,
        "window_s": 120,
        "tx_growth": tx,
        "rx_growth": rx,
        "signals": ["awg_tx_growth", "awg_rx_flat"],
        "ts": ts(seconds),
    }


def test_g04_awg_needs_two_windows() -> None:
    target = engine()
    target.note_state("laptop")
    assert target.note_awg_window(awg_window(120.0)) == []
    found = findings(target.note_awg_window(awg_window(240.0)), "G-04")
    assert len(found) == 1
    finding = found[0]
    assert_valid(finding)
    assert finding["unit"] == AWG_UNIT
    assert finding["profile"] == "laptop"
    assert finding["stage_hint"] == "udp_response"
    assert finding["count"] == 2
    assert finding["confidence"] == "medium"
    counters = [item for item in finding["evidence"]
                if item["kind"] == "counter"]
    assert {item["signal"] for item in counters} == {
        "awg_tx_growth", "awg_rx_flat"}
    assert "битый конфиг AWG" in finding["alternatives"]


def test_g04_awg_one_window_is_not_enough() -> None:
    target = engine()
    target.note_state("laptop")
    assert target.note_awg_window(awg_window(120.0)) == []
    assert target.note_awg_window(
        {**awg_window(240.0), "signals": ["awg_tx_growth"]}) == []


def test_g04_awg_iface_gone_resets_history() -> None:
    target = engine()
    target.note_state("laptop")
    target.note_awg_window(awg_window(120.0))
    assert target.note_awg_gone("laptop") == []
    # the poller only reads existing sysfs entries, so a window means the
    # interface is back: the branch re-arms after `awg_consecutive_windows`
    assert target.note_awg_window(awg_window(240.0)) == []
    assert len(findings(target.note_awg_window(awg_window(360.0)), "G-04")) == 1


def test_g04_awg_suppressed_when_vpn_is_down_or_suspended() -> None:
    target = engine()
    target.note_state(None)
    target.note_awg_window(awg_window(120.0))
    outcomes = target.note_awg_window(awg_window(240.0))
    assert findings(outcomes, "G-04") == []
    assert outcomes[0].reason == "planned_stop"

    other = engine()
    other.note_state("laptop")
    other.note_suspend(mono(200))
    other.note_awg_window(awg_window(120.0))
    outcomes = other.note_awg_window(awg_window(240.0))
    assert findings(outcomes, "G-04") == []
    assert outcomes[0].reason == "suspend/roaming"


# --- G-05 auth_failure ---------------------------------------------------------


def test_g05_auth_failure_without_auth_ok() -> None:
    target = engine()
    startup(target)
    found = findings(run(target, ev("auth_fail", 5.0)), "G-05")
    assert len(found) == 1
    finding = found[0]
    assert_valid(finding)
    assert finding["category"] == "config"
    assert finding["stage_hint"] == "pre_handshake"
    assert finding["severity"] == "high"
    assert "lock-443" in finding["action"]
    assert finding["alternatives"]


def test_g05_auth_ok_clears_the_rule() -> None:
    target = engine()
    startup(target)
    outcomes = run(target, ev("auth_ok", 4.0), ev("auth_fail", 5.0))
    assert findings(outcomes, "G-05") == []


def test_g05_three_failures_with_an_old_auth_ok() -> None:
    target = engine()
    startup(target)
    outcomes = run(target, ev("auth_ok", 4.0),
                   ev("auth_fail", 5.0), ev("auth_fail", 6.0),
                   ev("auth_fail", 7.0))
    found = findings(outcomes, "G-05")
    assert len(found) == 1
    assert found[0]["count"] == 3
    assert found[0]["confidence"] == "high"


# --- G-06 dns_broken -----------------------------------------------------------


def test_g06_dns_errors_fire_with_repair_action() -> None:
    target = engine()
    startup(target)
    outcomes = run(target, ev("dns_lookup_fail", 5.0),
                   ev("dns_timeout", 10.0))
    found = findings(outcomes, "G-06")
    assert len(found) == 1
    finding = found[0]
    assert_valid(finding)
    assert finding["category"] == "dns"
    assert finding["stage_hint"] == "local"
    assert "vpn repair" in finding["action"]


def test_g06_servfail_on_two_names_is_high_confidence() -> None:
    target = engine()
    startup(target)
    outcomes = run(target,
                   ev("dns_servfail", 5.0, endpoint=EP),
                   ev("dns_servfail", 6.0, endpoint=EP2))
    finding = findings(outcomes, "G-06")[0]
    assert finding["confidence"] == "high"
    assert any("2 distinct names" in check for check in finding["fp_checks"])


def test_g06_downgraded_when_the_network_is_unreachable() -> None:
    target = engine()
    startup(target)
    outcomes = run(target, ev("dns_lookup_fail", 5.0),
                   ev("network_unreachable", 6.0),
                   ev("dns_lookup_fail", 10.0))
    assert findings(outcomes, "G-06") == []
    assert outcome(outcomes, "G-06", "suppressed")["reason"] == "local_network"


# --- G-07 local_network_down ---------------------------------------------------


def test_g07_local_network_down() -> None:
    target = engine()
    startup(target)
    outcomes = run(target, ev("network_unreachable", 5.0))
    found = findings(outcomes, "G-07")
    assert len(found) == 1
    finding = found[0]
    assert_valid(finding)
    assert finding["category"] == "local_net"
    assert finding["stage_hint"] == "local"
    assert finding["confidence"] == "high"
    assert finding["severity"] == "high"


def test_g07_suppressed_when_vpn_is_off() -> None:
    target = engine()
    target.note_state(None)
    startup(target)
    outcomes = run(target, ev("network_unreachable", 5.0))
    assert findings(outcomes, "G-07") == []
    assert outcome(outcomes, "G-07", "suppressed")["reason"] == "vpn_off"


def test_g07_default_route_removed_hook() -> None:
    target = engine()
    target.note_state("asv2")
    outcomes = target.note_route_change(mono(10), kind="del_default",
                                        ts=ts(10))
    found = findings(outcomes, "G-07")
    assert len(found) == 1
    assert found[0]["window"]["first"] == ts(10)
    assert found[0]["evidence"][0]["metric"] == "default_route_removed"


def test_g07_planned_network_switch_is_suppressed() -> None:
    target = engine()
    target.note_state("asv2")
    target.note_route_change(mono(5), kind="change")
    outcomes = target.note_route_change(mono(10), kind="del_default",
                                        ts=ts(10))
    assert findings(outcomes, "G-07") == []
    assert outcome(outcomes, "G-07", "suppressed")["reason"] == "network_switch"


# --- G-08 tls_cert_error -------------------------------------------------------


def test_g08_tls_cert_error_without_success() -> None:
    target = engine()
    startup(target)
    assert findings(run(target, ev("tls_cert_error", 5.0)), "G-08") == []
    found = findings(target.tick(mono(10)), "G-08")
    assert len(found) == 1
    finding = found[0]
    assert_valid(finding)
    assert finding["category"] == "config"
    assert finding["stage_hint"] == "tls_stall"


def test_g08_successful_tls_clears_the_rule() -> None:
    target = engine()
    startup(target)
    outcomes = run(target, ev("tls_cert_error", 5.0),
                   ev("tls_handshake_ok", 6.0))
    assert findings(outcomes, "G-08") == []
    assert findings(target.tick(mono(12)), "G-08") == []


def test_g08_clock_fallback_is_low_confidence_and_named() -> None:
    target = engine()
    startup(target)
    run(target, ev("tls_cert_error", 5.0, clock_fallback=True))
    finding = findings(target.tick(mono(10)), "G-08")[0]
    assert finding["confidence"] == "low"
    assert any("clock_fallback" in check for check in finding["fp_checks"])


# --- ordering / determinism / hygiene -----------------------------------------


def _corpus() -> list[dict]:
    return [
        ev("unit_start", 0.0),
        ev("app_banner_ok", 1.0),
        ev("handshake_ok", 2.0, endpoint=EP),
        ev("conn_reset", 3.0, endpoint=EP),
        ev("handshake_ok", 4.0, endpoint=EP),
        ev("conn_reset", 4.8, endpoint=EP),
        ev("handshake_ok", 6.0, endpoint=EP),
        ev("conn_reset", 6.7, endpoint=EP),
        ev("auth_fail", 30.0),
        ev("dns_lookup_fail", 40.0),
        ev("dns_servfail", 41.0),
        ev("tls_cert_error", 50.0),
        ev("network_unreachable", 60.0),
        ev("unit_stop", 90.0),
    ]


def test_replay_is_deterministic() -> None:
    def replay() -> str:
        target = engine()
        collected: list[dict] = []
        for item in _corpus():
            collected.extend(findings(target.feed(item)))
        collected.extend(findings(target.tick(mono(120))))
        return json.dumps(collected, sort_keys=True, ensure_ascii=False)

    assert replay() == replay()


def test_findings_are_ordered_by_window_then_rule() -> None:
    target = engine()
    target.seed(_corpus())
    collected = findings(target.tick(mono(120)))
    keys = [(item["window"]["first"], item["rule"]) for item in collected]
    assert keys == sorted(keys)
    assert {"G-05", "G-07"} <= {item["rule"] for item in collected}


def test_no_secret_or_raw_text_reaches_a_finding() -> None:
    target = engine()
    collected: list[dict] = []
    for item in _corpus():
        collected.extend(findings(target.feed(item)))
    collected.extend(findings(target.tick(mono(120))))
    assert collected
    for finding in collected:
        assert_valid(finding)
        assert "FIXTURE_PW" not in json.dumps(finding)


def test_push_holds_events_for_the_reorder_delay() -> None:
    target = engine()
    run(target, ev("unit_start", 0.0))     # feed bypasses the reorder buffer
    assert target.push(ev("handshake_ok", 2.02, endpoint=EP)) == []
    assert target.reorder.pending == 1
    # the next push 200 ms later releases the held event
    target.push(ev("app_banner_ok", 2.30, endpoint=EP))
    assert target.reorder.pending <= 1
    target.flush()
    assert target.reorder.pending == 0


def test_push_coalesces_a_burst_into_one_event() -> None:
    target = engine()
    run(target, ev("unit_start", 0.0), ev("app_banner_ok", 1.0))
    run(target, ev("handshake_ok", 2.0, endpoint=EP))
    for offset in (10.00, 10.02, 10.04):
        target.push(ev("conn_reset", offset, endpoint=EP))
    collected = findings(target.flush(), "G-02")
    assert len(collected) == 1
    assert collected[0]["count"] == 3
    resets = [item for item in target.window_snapshot(UNIT)
              if item["signal"] == "conn_reset"]
    assert len(resets) == 1


def test_channel_state_follows_the_windows() -> None:
    target = engine()
    assert target.channel_state() == "idle"
    run(target, ev("unit_start", 0.0))
    assert target.channel_state() == "armed"


def test_unit_state_is_collected_after_an_hour() -> None:
    target = engine()
    startup(target)
    assert target.channel_state() == "armed"
    target.tick(mono(2 * 3600))          # SPEC §12: GC after 1 h
    assert target.channel_state() == "idle"
    assert target.window_snapshot(UNIT) == []


# --- integration with the parser (WP-G2 → WP-G3) -------------------------------


def test_parse_to_rules_end_to_end() -> None:
    from vpnm.guard.parse import JournalParser

    parser = JournalParser(proto_hints={UNIT: "xray"})
    target = engine()

    def journal(message: str, seconds: float, **extra: object) -> dict:
        record = {
            "MESSAGE": message,
            "PRIORITY": "6",
            "__REALTIME_TIMESTAMP": str(
                1_794_830_400_000_000 + int(seconds * US)),
            "__MONOTONIC_TIMESTAMP": str(mono(seconds)),
            "_SYSTEMD_USER_UNIT": UNIT,
            "_SYSTEMD_INVOCATION_ID": "inv-1",
        }
        record.update(extra)
        return record

    script = [
        journal("", 0.0, JOB_TYPE="start", JOB_RESULT="done"),
        journal("Xray 1.8.6 started", 1.0),
    ]
    for lockstep in range(3):
        moment = 2.0 + lockstep
        script.append(journal(
            '{"level":"info","msg":"proxy: handshake ok with '
            '203.0.113.10:443"}', moment))
        script.append(journal(
            '{"level":"warning","msg":"connection reset by peer '
            '203.0.113.10:443"}', moment + 1))
    collected: list[dict] = []
    for record in script:
        event = parser.parse(record)
        assert event is not None
        collected.extend(findings(target.feed(event)))
    assert [item["rule"] for item in collected] == ["G-03"]
    finding = collected[0]
    assert_valid(finding)
    assert finding["endpoint"] == EP
    assert finding["count"] == 3
