"""Frozen constants for the ``vpn guard`` contract (``guard.*/1``).

This module is the **single registry** of enum values shared by the runtime,
the JSON schemas (``vpnm/guard/schemas/*.schema.json``) and the tests.  A
schema enum that drifts from this registry is a contract bug, not a detail:
``tests/guard/test_guard_contract.py`` compares both directions.

The catalogue is frozen for v1 (SPEC-20261116-vpn-guard §8.2).  Extending it
is an additive change inside major ``/1`` only when the new value is not a new
rule: new *signals* are additive, new *rules* (``G-13``+) need a new SPEC
round.  Thresholds live in ``guard.json`` and never here.

No I/O, no imports from the rest of ``vpnm``: this module must stay importable
by schema tooling, the daemon, and the tests without side effects.
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

# --- schema identifiers ---------------------------------------------------------

EVENT_SCHEMA = "guard.event/1"
FINDING_SCHEMA = "guard.finding/1"
COVERAGE_SCHEMA = "guard.coverage/1"
STATUS_SCHEMA = "guard.status/1"
SERVER_VERDICT_SCHEMA = "guard.server_verdict/1"
CONFIG_SCHEMA = "guard.config/1"
INCIDENT_SCHEMA = "guard.incident/1"
#: WP-FAILOVER artefacts (SPEC-20261118 §9): additive schemas with own files.
FAILOVER_STATE_SCHEMA = "guard.failover_state/1"
FAILOVER_HISTORY_SCHEMA = "guard.failover_history/1"
PENDING_ALERTS_SCHEMA = "guard.pending_alerts/1"

# schema id -> file name inside ``vpnm/guard/schemas/``
SCHEMA_FILES: Mapping[str, str] = {
    EVENT_SCHEMA: "guard.event.1.schema.json",
    FINDING_SCHEMA: "guard.finding.1.schema.json",
    COVERAGE_SCHEMA: "guard.coverage.1.schema.json",
    STATUS_SCHEMA: "guard.status.1.schema.json",
    SERVER_VERDICT_SCHEMA: "guard.server_verdict.1.schema.json",
    CONFIG_SCHEMA: "guard.config.1.schema.json",
    INCIDENT_SCHEMA: "guard.incident.1.schema.json",
    FAILOVER_STATE_SCHEMA: "guard.failover_state.1.schema.json",
    FAILOVER_HISTORY_SCHEMA: "guard.failover_history.1.schema.json",
    PENDING_ALERTS_SCHEMA: "guard.pending_alerts.1.schema.json",
}

# --- sources and protocols ------------------------------------------------------

SOURCES: Tuple[str, ...] = (
    "journal:user",
    "journal:system",
    "file",
    "state",
    "sysfs",
    "verdict",
)

PROTOS: Tuple[str, ...] = (
    "xray",
    "hysteria2",
    "amneziawg",
    "systemd",
    "dns",
    "zapret",
    "unknown",
)

LEVELS: Tuple[str, ...] = ("debug", "info", "warn", "error")

# --- signal catalogue (SPEC §7.2) -----------------------------------------------

SIGNAL_GROUPS: Mapping[str, Tuple[str, ...]] = {
    "lifecycle": (
        "unit_start",
        "unit_stop",
        "unit_exit",
        "unit_restart_scheduled",
        "job_failed",
    ),
    "app-boot": ("app_banner_ok",),
    "tcp": (
        "dial_start",
        "tcp_connected",
        "tls_handshake_ok",
        "handshake_ok",
        "conn_established",
        "conn_closed",
        "conn_reset",
        "conn_eof",
        "dial_timeout",
        "conn_refused",
        "address_in_use",
        "network_unreachable",
        "no_route",
    ),
    "quic": (
        "quic_handshake_ok",
        "quic_handshake_fail",
        "quic_idle_timeout",
        "quic_no_recent_activity",
    ),
    "auth": ("auth_ok", "auth_fail"),
    "dns": (
        "dns_lookup_fail",
        "dns_timeout",
        "dns_servfail",
        "dns_nxdomain",
    ),
    "tls": ("tls_cert_error", "tls_alert", "tls_stall"),
    "perf": ("read_timeout", "write_timeout", "slow_consumer"),
    "counters": ("awg_tx_growth", "awg_rx_flat", "awg_iface_gone"),
    "self": ("unparsed", "source_lost", "clock_skew", "no_signal_window"),
}

SIGNALS: Tuple[str, ...] = tuple(
    signal for group in SIGNAL_GROUPS.values() for signal in group
)

#: Signals the guard derives itself (no daemon ever prints them).
DERIVED_SIGNALS: Tuple[str, ...] = (
    "tls_stall",
    "no_signal_window",
    "awg_tx_growth",
    "awg_rx_flat",
    "awg_iface_gone",
)

HANDBOOK_SIGNALS: Tuple[str, ...] = (
    "handshake_ok",
    "conn_established",
    "quic_handshake_ok",
)

#: Proof that the app is alive after ``unit_start`` but **not** traffic proof.
BOOT_SIGNALS: Tuple[str, ...] = ("app_banner_ok",)

#: Proof that the app tried to connect (used by G-01 as coverage evidence).
DIAL_SIGNALS: Tuple[str, ...] = ("dial_start", "dial_timeout", "conn_refused")

# --- findings -------------------------------------------------------------------

RULES: Tuple[str, ...] = (
    "G-01",
    "G-02",
    "G-03",
    "G-04",
    "G-05",
    "G-06",
    "G-07",
    "G-08",
    "G-09",
    "G-10",
    "G-11",
    "G-12",
)

SEVERITIES: Tuple[str, ...] = ("info", "low", "medium", "high")
CONFIDENCES: Tuple[str, ...] = ("low", "medium", "high")

CATEGORIES: Tuple[str, ...] = (
    "tunnel_down",
    "censorship_suspect",
    "config",
    "dns",
    "local_net",
    "local_fault",
    "endpoint_unreachable",
    "degraded",
    "observability",
)

#: Root-cause priority for incident merging (SPEC §9.2), highest first.
CATEGORY_PRIORITY: Tuple[str, ...] = CATEGORIES

STAGES: Tuple[str, ...] = (
    "pre_handshake",
    "tls_stall",
    "post_handshake",
    "data_phase",
    "udp_response",
    "l3_silence",
    "local",
)

EVIDENCE_KINDS: Tuple[str, ...] = (
    "signal_count",
    "cycle",
    "ratio",
    "absence",
    "counter",
)

LIFECYCLE_CAUSES: Tuple[str, ...] = (
    "vpn_up",
    "vpn_down",
    "vpn_switch",
    "unknown",
)

GUARD_STATES: Tuple[str, ...] = ("armed", "idle")

SOURCE_STATUSES: Tuple[str, ...] = (
    "ok",
    "degraded",
    "denied",
    "off",
    "lost",
    "unavailable",
)

SIGNAL_COVERAGE: Tuple[str, ...] = ("ok", "blind", "counters", "missing")

THREAT_LEVELS: Tuple[str, ...] = ("quiet", "elevated", "high")

#: Keys that the secret post-scanner must never treat as secrets (SPEC §7.3).
HASH_EXEMPT_KEYS: Tuple[str, ...] = (
    "schema",
    "schema_hash",
    "rules_hash",
    "config_hash",
    "raw_sha256",
    "evidence_hash",
    "template",
    "id",
    "incident_id",
    "rule",
    "signal",
    "category",
    "stage_hint",
)

#: Canonical hash prefixes are contract values, not leaked secrets.
HASH_VALUE_PREFIXES: Tuple[str, ...] = ("sha256:",)


def _registry_duplicates(values: Iterable[str]) -> List[str]:
    """Return values that occur more than once, preserving first-seen order."""
    seen: Dict[str, int] = {}
    duplicates: List[str] = []
    for value in values:
        seen[value] = seen.get(value, 0) + 1
        if seen[value] == 2:
            duplicates.append(value)
    return duplicates


def _check_registry() -> None:
    """Fail fast on a duplicated signal/rule — the schema enum would drift."""
    problems = {
        "signals": _registry_duplicates(SIGNALS),
        "rules": _registry_duplicates(RULES),
        "categories": _registry_duplicates(CATEGORIES),
    }
    broken = {name: dups for name, dups in problems.items() if dups}
    if broken:
        raise RuntimeError(f"guard registry has duplicates: {broken}")
    unknown_derived = sorted(set(DERIVED_SIGNALS) - set(SIGNALS))
    if unknown_derived:
        raise RuntimeError(
            f"derived signals missing from catalogue: {unknown_derived}")
    unknown_boot = sorted(set(BOOT_SIGNALS) - set(SIGNALS))
    if unknown_boot:
        raise RuntimeError(f"boot signals missing from catalogue: {unknown_boot}")


_check_registry()


def canonical_json(payload: object) -> str:
    """Canonical JSON used for ``schema_hash`` (sorted, compact, UTF-8)."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def payload_hash(payload: object) -> str:
    """``sha256:<64 hex>`` of the canonical JSON of *payload*."""
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def signals_of(group: str) -> Sequence[str]:
    """Signals of one catalogue group (``KeyError`` on unknown group)."""
    return SIGNAL_GROUPS[group]
