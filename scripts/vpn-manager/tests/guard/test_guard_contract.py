"""Contract tests for the frozen ``guard.*/1`` schemas (WP-G0).

No network, no root, no daemon: only the registry in ``vpnm.guard.events``,
the JSON schemas and the small validator in ``vpnm.guard.schema``.  A failure
here means the client contract drifted from SPEC-20261116-vpn-guard §7.2/§8.2
or the schemas disagree with the code registry.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import events, schema  # noqa: E402

FIXTURE_HASH = "sha256:" + "0" * 64


def _shape(value: object) -> object:
    """Key-order-insensitive view of nested dicts (for canonical JSON tests)."""
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shape(item) for item in value]
    return value


def test_every_schema_file_exists_and_ids_match() -> None:
    for name, filename in events.SCHEMA_FILES.items():
        path = schema.SCHEMA_DIR / filename
        assert path.is_file(), f"missing schema file for {name}"
        payload = schema.load_schema(name)
        assert payload["$id"] == name
        assert payload["properties"]["schema"]["const"] == name


def test_event_signal_enum_matches_registry() -> None:
    node = schema.load_schema(events.EVENT_SCHEMA)["$defs"]["signal"]
    assert tuple(node["enum"]) == events.SIGNALS


def test_event_enums_match_registry() -> None:
    defs = schema.load_schema(events.EVENT_SCHEMA)["$defs"]
    assert tuple(defs["source"]["enum"]) == events.SOURCES
    assert tuple(defs["proto"]["enum"]) == events.PROTOS
    assert tuple(defs["level"]["enum"]) == events.LEVELS


def test_finding_enums_match_registry() -> None:
    defs = schema.load_schema(events.FINDING_SCHEMA)["$defs"]
    assert tuple(defs["rule"]["enum"]) == events.RULES
    assert tuple(defs["severity"]["enum"]) == events.SEVERITIES
    assert tuple(defs["confidence"]["enum"]) == events.CONFIDENCES
    assert tuple(defs["category"]["enum"]) == events.CATEGORIES
    assert tuple(defs["stage"]["enum"]) == events.STAGES


def test_registry_contains_g2_signals() -> None:
    for signal in ("app_banner_ok", "awg_tx_growth", "awg_rx_flat",
                   "awg_iface_gone", "no_signal_window"):
        assert signal in events.SIGNALS
    assert set(events.BOOT_SIGNALS) <= set(events.SIGNALS)
    assert set(events.DIAL_SIGNALS) <= set(events.SIGNALS)
    assert set(events.DERIVED_SIGNALS) <= set(events.SIGNALS)


def test_coverages_are_known_values() -> None:
    assert set(events.SIGNAL_COVERAGE) == {"ok", "blind", "counters", "missing"}
    assert "counters" in events.SIGNAL_COVERAGE
    assert set(events.GUARD_STATES) == {"armed", "idle"}


def test_hash_exempt_keys_cover_contract_hashes() -> None:
    for key in ("schema_hash", "rules_hash", "config_hash", "raw_sha256",
                "template", "id", "evidence_hash"):
        assert key in events.HASH_EXEMPT_KEYS


def test_schema_hash_is_stable_and_distinct() -> None:
    hashes = schema.schema_hashes()
    assert set(hashes) == set(events.SCHEMA_FILES)
    for name, digest in hashes.items():
        assert digest.startswith("sha256:"), name
        assert len(digest) == len("sha256:") + 64
        assert digest == schema.schema_hash(name)
    assert len(set(hashes.values())) == len(hashes)


def test_canonical_json_is_key_order_independent() -> None:
    left = {"b": 1, "a": [{"y": 2, "x": 1}]}
    right = {"a": [{"x": 1, "y": 2}], "b": 1}
    assert events.canonical_json(left) == events.canonical_json(right)
    assert " " not in events.canonical_json(left)
    assert events.payload_hash(left) == events.payload_hash(right)


def test_sibling_refs_are_declared() -> None:
    for name in events.SCHEMA_FILES:
        text = json.dumps(schema.load_schema(name))
        for ref in _refs(text):
            if ref.startswith("#/"):
                continue
            assert ref in events.SCHEMA_FILES, f"{name} references unknown {ref}"


def test_status_incident_copy_matches_standalone_schema() -> None:
    inline = schema.load_schema(events.STATUS_SCHEMA)["$defs"]["incident"]
    standalone = dict(schema.load_schema(events.INCIDENT_SCHEMA))
    for meta in ("$schema", "$id", "title", "description"):
        standalone.pop(meta, None)
    assert inline == standalone


def _refs(text: str) -> list:
    def walk(node: object) -> list:
        found = []
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                found.append(ref)
            for value in node.values():
                found.extend(walk(value))
        elif isinstance(node, list):
            for item in node:
                found.extend(walk(item))
        return found

    return walk(json.loads(text))


# --- valid payloads --------------------------------------------------------------

def fixture_event() -> dict:
    return {
        "schema": "guard.event/1",
        "ts": "2026-11-16T12:00:03.412Z",
        "mono_us": 812345678,
        "source": "journal:user",
        "unit": "vpnm-asv2.service",
        "profile": "asv2",
        "proto": "hysteria2",
        "level": "warn",
        "signal": "quic_idle_timeout",
        "template": "3f9a1c02be77",
        "endpoint": {"host": "asv2.example.net", "port": 443},
        "fields": {"timeout_s": 10},
        "count": 1,
        "invocation_id": "fixture-invocation",
    }


def fixture_finding() -> dict:
    return {
        "schema": "guard.finding/1",
        "id": "7c1d2f0a9b3e",
        "rule": "G-03",
        "severity": "high",
        "confidence": "high",
        "category": "censorship_suspect",
        "stage_hint": "data_phase",
        "unit": "vpnm-asv2.service",
        "profile": "asv2",
        "endpoint": {"host": "asv2.example.net", "port": 443},
        "window": {"first": "2026-11-16T12:00:03.412Z",
                   "last": "2026-11-16T12:01:58.901Z"},
        "count": 4,
        "evidence": [
            {"kind": "signal_count", "signal": "handshake_ok", "value": 4},
            {"kind": "cycle", "metric": "handshake_to_drop_s", "median": 1.2},
        ],
        "fp_checks": ["no unit_exit", "no auth_fail", "server verdict fresh"],
        "hypothesis": "data-phase impact on the path",
        "alternatives": ["server restart", "local client crash"],
        "action": "do not restart; run vpn probe; use a backup channel",
        "server": {"threat_level": "high", "stage": "data_phase",
                   "age_s": 120, "fresh": True, "lockdown": False},
        "cross_vantage": True,
        "expected_lockdown": False,
        "incident_id": "i-2f81c0",
        "redaction_hits": 0,
        "schema_hash": FIXTURE_HASH,
    }


def fixture_coverage() -> dict:
    return {
        "schema": "guard.coverage/1",
        "sources": [
            {"name": "journal:user", "status": "ok",
             "last_event_ts": "2026-11-16T12:00:03Z", "lag_ms": 120},
            {"name": "journal:system", "status": "denied",
             "note": "no access to the system journal"},
            {"name": "sysfs:awg", "status": "ok", "note": "iface=laptop"},
            {"name": "netlink", "status": "off"},
        ],
        "signals": {"xray": "ok", "hysteria2": "blind",
                    "amneziawg": "counters"},
        "unparsed_ratio": 0.02,
        "dropped": 0,
        "blind": ["whitelist_mode_unobservable"],
        "disk_full": False,
        "notes": [],
    }


def fixture_status() -> dict:
    return {
        "schema": "guard.status/1",
        "running": True,
        "state": "armed",
        "pid": 4242,
        "since": "2026-11-16T10:00:00Z",
        "unit_profile": "hardened",
        "rules_hash": FIXTURE_HASH,
        "config_hash": FIXTURE_HASH,
        "sources_ref": "coverage.json",
        "last_event_ts": "2026-11-16T12:01:58.901Z",
        "last_lifecycle": {"signal": "unit_stop",
                           "ts": "2026-11-16T12:02:00Z",
                           "cause": "vpn_down"},
        "counters": {"events": 1234, "unparsed": 2, "dropped": 0,
                     "findings": 3, "suppressed": 1},
        "open_incidents": [{
            "schema": "guard.incident/1",
            "id": "i-2f81c0",
            "headline": "reset storm after handshake",
            "root_cause": "censorship_suspect",
            "confidence": "medium",
            "first": "2026-11-16T12:00:03Z",
            "last": "2026-11-16T12:01:58Z",
            "resolved_at": None,
            "unit": "vpnm-asv2.service",
            "profile": "asv2",
            "findings": ["7c1d2f0a9b3e"],
            "server": {"threat_level": "high", "stage": "data_phase",
                       "age_s": 120, "fresh": True, "lockdown": False},
        }],
    }


def fixture_verdict() -> dict:
    return {
        "schema": "guard.server_verdict/1",
        "fetched_at": "2026-11-16T12:00:00Z",
        "host": "root@203.0.113.10",
        "source": "vds gateway probe-watch watch --json",
        "verdict": "RU-PROBE-SUSPECT",
        "threat_level": "high",
        "threat_reason": "2 windows RU-PROBE-SUSPECT",
        "window": "2026-11-16T11:00:00 .. 2026-11-16T12:00:00",
        "stage": None,
        "reasons": ["syn_bare growth"],
        "lockdown": {"active": False, "ttl_until": ""},
        "raw_sha256": FIXTURE_HASH,
    }


def fixture_config() -> dict:
    return {
        "schema": "guard.config/1",
        "sources": {"user_journal": True, "system_journal": False,
                    "files": True, "awg_counters": True,
                    "netlink": False, "server_verdict": True},
        "notify": {"desktop": False, "min_severity": "high"},
        "redact": {"hosts_in_export": True, "snippets": False},
        "thresholds": {"handshake_timeout_s": 25, "startup_banner_s": 15,
                       "reset_storm_count": 3, "udp_blackhole_count": 2,
                       "awg_window_s": 120, "awg_tx_min_bytes": 262144,
                       "awg_rx_ratio": 0.05, "cooldown_s": 300},
        "retention_days": 14,
    }


@pytest.mark.parametrize("builder,name", [
    (fixture_event, events.EVENT_SCHEMA),
    (fixture_finding, events.FINDING_SCHEMA),
    (fixture_coverage, events.COVERAGE_SCHEMA),
    (fixture_status, events.STATUS_SCHEMA),
    (fixture_verdict, events.SERVER_VERDICT_SCHEMA),
    (fixture_config, events.CONFIG_SCHEMA),
])
def test_registry_examples_validate(builder, name) -> None:
    assert schema.validate(builder(), name) == []


def test_unknown_signal_is_rejected() -> None:
    payload = fixture_event()
    payload["signal"] = "not_a_signal"
    assert schema.validate(payload, events.EVENT_SCHEMA)


def test_missing_required_is_rejected() -> None:
    payload = fixture_event()
    del payload["template"]
    errors = schema.validate(payload, events.EVENT_SCHEMA)
    assert any("template" in error for error in errors)


def test_wrong_schema_const_is_rejected() -> None:
    payload = fixture_finding()
    payload["schema"] = "guard.event/1"
    assert schema.validate(payload, events.FINDING_SCHEMA)


def test_malformed_schema_hash_is_rejected() -> None:
    payload = fixture_finding()
    payload["schema_hash"] = "sha256:not-hex"
    assert schema.validate(payload, events.FINDING_SCHEMA)


def test_unknown_rule_is_rejected() -> None:
    payload = fixture_finding()
    payload["rule"] = "G-13"
    assert schema.validate(payload, events.FINDING_SCHEMA)


def test_hysteria2_and_awg_protos_validate() -> None:
    for proto in ("hysteria2", "amneziawg"):
        payload = fixture_event()
        payload["proto"] = proto
        payload["signal"] = ("quic_idle_timeout" if proto == "hysteria2"
                             else "awg_rx_flat")
        payload["source"] = ("journal:user" if proto == "hysteria2"
                             else "sysfs")
        assert schema.validate(payload, events.EVENT_SCHEMA) == []


def test_shape_helper_is_order_insensitive() -> None:
    left = {"b": {"y": 1, "x": 2}, "a": [1, 2]}
    right = {"a": [1, 2], "b": {"x": 2, "y": 1}}
    assert _shape(left) == _shape(right)
