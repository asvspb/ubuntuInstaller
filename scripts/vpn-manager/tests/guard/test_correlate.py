"""WP-G4 tests: incidents, root cause and the suppression matrix (SPEC §9.2).

Pure module: findings are built through ``rules.build_finding`` (so they are
contract-valid), monotonic and wall timestamps are explicit.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import events, schema  # noqa: E402
from vpnm.guard.correlate import (  # noqa: E402
    ROOT_CAUSE_PRIORITY,
    Correlator,
    incident_id,
)
from vpnm.guard.rules import ServerContext, build_finding  # noqa: E402
from vpnm.guard.window import MONO_US_PER_S  # noqa: E402

US = MONO_US_PER_S
BASE = 1_000_000_000
UNIT = "vpnm-asv2.service"
EP = {"host": "203.0.113.10", "port": 443}


def mono(seconds: float) -> int:
    return BASE + int(seconds * US)


def ts(seconds: float) -> str:
    total = int(round(seconds))
    return f"2026-11-16T12:{total // 60 % 60:02d}:{total % 60:02d}.000Z"


def finding(rule: str, category: str, *, seconds: float = 10.0,
            unit: str | None = UNIT, profile: str | None = "asv2",
            severity: str = "high", confidence: str = "medium") -> dict:
    return build_finding(
        rule=rule, unit=unit, profile=profile, endpoint=EP,
        window_first=ts(seconds), window_last=ts(seconds + 2), count=2,
        evidence=[{"kind": "signal_count", "signal": "conn_reset", "value": 2}],
        fp_checks=["no auth_fail"], severity=severity,
        confidence=confidence, category=category,
        stage_hint="post_handshake", hypothesis="h", action="a")


def assert_valid_incident(payload: dict) -> None:
    assert schema.validate(payload, events.INCIDENT_SCHEMA) == [], payload
    assert payload["id"].startswith("i-") and len(payload["id"]) == 8
    assert payload["schema"] == "guard.incident/1"


# --- grouping --------------------------------------------------------------------

def test_findings_within_180s_merge_into_one_incident() -> None:
    correlator = Correlator()
    first = finding("G-09", "endpoint_unreachable", seconds=10)
    second = finding("G-10", "local_fault", seconds=100, severity="high")
    incident = correlator.note(first, mono(10))
    again = correlator.note(second, mono(100))
    assert incident["id"] == again["id"]
    assert first["incident_id"] == incident["id"]
    assert second["incident_id"] == incident["id"]
    assert set(again["findings"]) == {first["id"], second["id"]}
    assert again["first"] == ts(10)
    assert again["last"] == ts(102)
    assert_valid_incident(again)


def test_gap_over_180s_starts_a_new_incident() -> None:
    correlator = Correlator()
    first = finding("G-09", "endpoint_unreachable", seconds=10)
    second = finding("G-09", "endpoint_unreachable", seconds=500)
    old = correlator.note(first, mono(10))
    new = correlator.note(second, mono(500))
    assert old["id"] != new["id"]
    assert new["first"] == ts(500)
    assert correlator.open_incidents() == [new]
    history = correlator.history()
    assert history and history[0]["id"] == old["id"]
    assert history[0]["resolved_at"] == ts(500)


def test_global_findings_group_by_profile() -> None:
    correlator = Correlator()
    first = finding("G-11", "degraded", unit=None, profile="asv2",
                    seconds=10, severity="medium")
    other = finding("G-11", "degraded", unit=None, profile="awg",
                    seconds=12, severity="medium")
    one = correlator.note(first, mono(10))
    two = correlator.note(other, mono(12))
    assert one["id"] != two["id"]
    assert len(correlator.open_incidents()) == 2


def test_incident_id_is_stable_and_schema_shaped() -> None:
    assert incident_id(UNIT, ts(10)) == incident_id(UNIT, ts(10))
    assert incident_id(UNIT, ts(10)) != incident_id(UNIT, ts(11))
    assert incident_id(UNIT, ts(10)).startswith("i-")
    assert len(incident_id(UNIT, ts(10))) == 8


# --- root cause ------------------------------------------------------------------

def test_root_cause_follows_the_wp_g4_matrix() -> None:
    correlator = Correlator()
    for rule, category in (("G-05", "config"), ("G-06", "dns")):
        correlator.note(finding(rule, category, seconds=10), mono(10))
    # G-06 (dns) outranks G-05 (config) in the WP-G4 matrix
    assert correlator.open_incidents()[0]["root_cause"] == "dns"


def test_local_net_dominates_everything() -> None:
    correlator = Correlator()
    correlator.note(finding("G-09", "endpoint_unreachable", seconds=10),
                    mono(10))
    correlator.note(finding("G-07", "local_net", seconds=20), mono(20))
    incident = correlator.open_incidents()[0]
    assert incident["root_cause"] == "local_net"
    assert ROOT_CAUSE_PRIORITY[0] == "local_net"


def test_local_fault_outranks_config_and_censorship() -> None:
    correlator = Correlator()
    correlator.note(finding("G-02", "censorship_suspect", seconds=10),
                    mono(10))
    correlator.note(finding("G-05", "config", seconds=12), mono(12))
    correlator.note(finding("G-10", "local_fault", seconds=14), mono(14))
    assert correlator.open_incidents()[0]["root_cause"] == "local_fault"


def test_root_cause_priority_list_is_complete() -> None:
    assert set(ROOT_CAUSE_PRIORITY) == set(events.CATEGORIES)


# --- server merge (D-06 / AC-10) --------------------------------------------------

def test_fresh_high_server_raises_censorship_confidence() -> None:
    fresh = ServerContext(threat_level="high", stage="data_phase", age_s=60,
                          fresh=True)
    correlator = Correlator(server=fresh)
    incident = correlator.note(finding("G-03", "censorship_suspect"),
                               mono(10))
    assert incident["confidence"] == "high"
    assert incident["cross_vantage"] is True
    assert incident["server"]["fresh"] is True


def test_stale_server_is_ignored() -> None:
    stale = ServerContext(threat_level="high", age_s=7200, fresh=False)
    correlator = Correlator(server=stale)
    incident = correlator.note(finding("G-03", "censorship_suspect"),
                               mono(10))
    assert incident["confidence"] == "medium"
    assert "cross_vantage" not in incident


def test_server_does_not_reclassify_local_net() -> None:
    fresh = ServerContext(threat_level="high", age_s=10, fresh=True)
    correlator = Correlator(server=fresh)
    incident = correlator.note(finding("G-07", "local_net"), mono(10))
    assert incident["root_cause"] == "local_net"
    assert incident["confidence"] == "medium"  # finding's own confidence


def test_lockdown_marks_expected_and_lowers_severity() -> None:
    fresh = ServerContext(threat_level="high", stage="data_phase", age_s=30,
                          fresh=True, lockdown=True)
    correlator = Correlator(server=fresh)
    incident = correlator.note(finding("G-09", "endpoint_unreachable"),
                               mono(10))
    assert incident["expected_lockdown"] is True
    assert incident["severity"] == "info"


def test_selfdegraded_never_raises_confidence() -> None:
    fresh = ServerContext(threat_level="high", age_s=10, fresh=True)
    correlator = Correlator(server=fresh)
    incident = correlator.note(
        finding("G-12", "observability", severity="low", confidence="low"),
        mono(10))
    assert incident["confidence"] == "low"
    assert incident["severity"] == "low"


# --- lifecycle -------------------------------------------------------------------

def test_incident_closes_after_ten_quiet_minutes() -> None:
    correlator = Correlator()
    correlator.note(finding("G-09", "endpoint_unreachable"), mono(100))
    assert correlator.tick(mono(100 + 599), ts(800)) == []
    resolved = correlator.tick(mono(100 + 601), ts(800))
    assert len(resolved) == 1
    assert resolved[0]["resolved_at"] == ts(800)
    assert correlator.open_incidents() == []
    assert any(item["id"] == resolved[0]["id"]
               for item in correlator.history())


def test_high_open_detects_a_high_incident() -> None:
    correlator = Correlator()
    assert correlator.high_open() is False
    correlator.note(finding("G-01", "tunnel_down", severity="high"), mono(10))
    assert correlator.high_open() is True


def test_self_only_incident_is_not_a_high_incident() -> None:
    correlator = Correlator()
    correlator.note(finding("G-12", "observability", severity="low",
                            confidence="low"), mono(10))
    assert correlator.high_open() is False
