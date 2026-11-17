"""Rules G-01…G-12 of the client guard (WP-G3/WP-G4, SPEC §8.2/§8.3).

The engine is a pure function of the event stream: it reads no wall clock, no
files and no network, so a corpus replay yields a bit-for-bit identical list
of findings (AC-9).  Callers feed already normalized ``guard.event/1`` events
(``parse`` → ``feed``), the 5 s ticker (``tick``), and the derived contexts the
daemon owns:

- ``note_state(active)`` — ``state.json.active`` (command markers, FP-guards);
- ``note_activity(active, now_us)`` — ``activity.json`` probe marker (§8.4);
- ``note_route_change``/``note_suspend``/``note_roaming`` — netlink/suspend
  stop factors (S4 is opt-in v1.1, the hooks are here for G-04/G-07);
- ``note_awg_window`` — one closed sysfs counter window (G-04 AWG branch);
- ``note_speed_report`` — a finished ``last-speed.json`` report (G-11);
- ``note_source_status``/``note_queue_drops``/``note_parse_stats``/
  ``note_clock_skew`` — the guard's own health (G-12, never a notification).

What the rules deliberately **do not** do:

- no signals outside :mod:`vpnm.guard.events` are invented — a rule that has
  no observable evidence returns ``insufficient_data`` instead of a finding;
- G-01 fires only when coverage is proven (``app_banner_ok`` ≤ 15 s or
  ``dial_start``/``dial_timeout``/``conn_refused`` ≤ 25 s after the *same*
  ``unit_start``); silence alone is ``insufficient_data:no_app_banner``;
- the AWG branch of G-04 lives on counters only: ``awg`` has no app logs by
  design, so ``app_signal=blind`` does not block it (§4.3);
- G-09 never claims censorship: without a second vantage it stays
  ``endpoint_unreachable`` with confidence ≤ medium; ``conn_refused`` is an
  ``endpoint_down`` (local/server side), never a censor;
- G-12 is ``low`` and never boosts another rule's confidence (§8.4);
- the engine only *consumes* an already validated, already freshness-checked
  :class:`ServerContext` — never the file itself (``verdict.py`` owns it).
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import (Any, Callable, Deque, Dict, Iterable, List, Mapping,
                    Optional, Sequence, Set, Tuple)

from . import events, schema
from .window import (FIRE, MONO_US_PER_S, PENDING, Clock, Coalescer,
                     CooldownRegistry, Hysteresis, ReorderBuffer, WindowStore,
                     endpoint_key, event_count, mono_of, mono_us, sum_counts)

VPNM_PREFIX = "vpnm-"
#: AmneziaWG template units: no app logs at all, own observability (§4.3).
AWG_PREFIXES: Tuple[str, ...] = ("vpn-awg-split@", "awg-quick@")
#: Client **VPN transports** — only their silence means "the endpoint is
#: unreachable".  Everything else the guard reads is context: resolver units
#: (``cloudflared-proxy-dns``, ``dnsmasq``, ``systemd-resolved``), ``zapret``
#: and system/network units report resets and timeouts of the *local* layer,
#: so endpoint rules must not attribute them to a remote host (WP-G6 field
#: checkpoint: ``conn_reset`` from cloudflared produced a false G-09).
VPN_TRANSPORT_PREFIXES: Tuple[str, ...] = (
    VPNM_PREFIX,          # vpnm-<profile>.service — xray/hysteria профили
    "vpn-xray-split@",    # tun xray0 (Reality/XHTTP)
    "vpn-awg-split@",     # AWG split: direct/relay
    "awg-quick@",
    "vpn-relay-",         # релейный контур на клиенте (vpn-relay-*.service)
)


def is_vpn_transport_unit(name: Optional[str]) -> bool:
    """``True`` only for a client VPN transport unit (not DNS/zapret/system).

    Endpoint-scoped rules (G-09) use this so an auxiliary service that resets
    its own upstream sockets never looks like a censored VPN endpoint.  The
    predicate is **prefix-based and explicit**: an unknown unit is *not* a
    transport, which fails closed (no finding) instead of inventing one.
    """
    if not isinstance(name, str) or not name:
        return False
    return name.startswith(VPN_TRANSPORT_PREFIXES)


HANDBOOK_SIGNALS: frozenset = frozenset(events.HANDBOOK_SIGNALS)
BOOT_SIGNALS: frozenset = frozenset(events.BOOT_SIGNALS)
DIAL_SIGNALS: frozenset = frozenset(events.DIAL_SIGNALS)
RESET_SIGNALS: frozenset = frozenset(("conn_reset", "conn_eof"))
#: What ends a post-handshake cycle (G-03): reset, EOF, QUIC idle.
DROP_SIGNALS: frozenset = frozenset((
    "conn_reset", "conn_eof", "quic_idle_timeout", "quic_no_recent_activity"))
IDLE_SIGNALS: frozenset = frozenset(
    ("quic_idle_timeout", "quic_no_recent_activity"))
DNS_SIGNALS: frozenset = frozenset((
    "dns_lookup_fail", "dns_timeout", "dns_servfail", "dns_nxdomain"))
#: G-06 trigger list of SPEC §8.2 (``dns_nxdomain`` is context, not a trigger).
DNS_STORM_SIGNALS: frozenset = frozenset(
    ("dns_lookup_fail", "dns_timeout", "dns_servfail"))
LOCAL_NET_SIGNALS: frozenset = frozenset(("network_unreachable", "no_route"))
FLAP_SIGNALS: frozenset = frozenset(
    ("unit_exit", "unit_restart_scheduled"))
LIFECYCLE_SIGNALS: frozenset = frozenset((
    "unit_start", "unit_stop", "unit_exit", "unit_restart_scheduled"))
#: "The client was waiting for traffic" proof of G-04 (§8.3).
TRAFFIC_WAIT_SIGNALS: frozenset = frozenset(
    ("dial_start", "read_timeout", "slow_consumer"))
COUNTER_SIGNALS: Tuple[str, ...] = ("awg_tx_growth", "awg_rx_flat")
#: Pre-handshake failures of G-09 (§8.2): refused/timeout/stall/reset before
#: any successful handshake is an endpoint problem, not a reset storm.
SILENT_SIGNALS: frozenset = frozenset(
    ("dial_timeout", "tls_stall", "conn_refused", "conn_reset"))
#: Perf signals of the G-11 second branch (`1 report + 3 timeouts`).
PERF_TIMEOUT_SIGNALS: frozenset = frozenset(("read_timeout", "write_timeout"))
#: How many mini-records of the G-11 ring are persisted in ``state.json``
#: (DECISIONS D-09: ``ts``, ``profile``, ``proto``, ``median_mbps``).
SPEED_RING_PERSISTED = 5
#: G-12 inputs (the guard's own degradations, §8.2).
SELF_SIGNALS: frozenset = frozenset(
    ("unparsed", "source_lost", "clock_skew", "no_signal_window"))
#: CDN channels never count for G-11 (Cloudflare gives no bulk baseline).
CDN_PROTOCOLS: frozenset = frozenset(("xray-cdn-ws",))
#: Route-change kinds that mean "the default route is gone" (G-07).
ROUTE_GONE_KINDS: frozenset = frozenset(
    ("del_default", "delete_default", "route_del", "default_route_removed"))

RULES_IN_ORDER: Tuple[str, ...] = (
    "G-01", "G-02", "G-03", "G-04", "G-05", "G-06", "G-07", "G-08",
    "G-09", "G-10", "G-11", "G-12")

#: Rules evaluated once per ticker, not per unit (they describe the guard or
#: the whole measurement history, not one service window).
GLOBAL_RULES: Tuple[str, ...] = ("G-11", "G-12")

#: Rules whose SPEC condition is a single decisive observation (a count of
#: resets/cycles/idle timeouts/auth fails/DNS errors, 5 silent dials, 3 unit
#: flaps, 2 low speed reports, one lost source): their own evidence **is** the
#: confirmation, so hysteresis uses ``confirm_windows=1``.  Otherwise the SPEC
#: threshold would silently double (3 resets → 4–5) and AC-2/AC-14 would
#: drift.  Absence-style rules (G-01, G-08) still need the configured
#: ``confirm_windows`` evaluations.
SINGLE_EVIDENCE_RULES: frozenset = frozenset(
    ("G-02", "G-03", "G-04", "G-05", "G-06", "G-07",
     "G-09", "G-10", "G-11", "G-12"))

#: Static catalogue of SPEC §8.2 for ``vpn guard rules`` (WP-G5).  Metadata
#: only — the logic lives in the ``_gNN`` methods and the defaults in
#: :class:`Thresholds`; a test asserts the ids match :data:`RULES_IN_ORDER`.
#: ``notify=False`` is the "never notifies" property of G-12 (§8.4).
RULE_CATALOG: Mapping[str, Mapping[str, Any]] = {
    "G-01": {"name": "no_handshake_after_start", "category": "tunnel_down",
             "severity": "high", "stage": "pre_handshake",
             "summary": "app жив (banner/dial), но за 25 с нет handshake"},
    "G-02": {"name": "reset_storm", "category": "censorship_suspect",
             "severity": "high", "stage": "post_handshake",
             "summary": "≥3 conn_reset/conn_eof за 60 с после handshake"},
    "G-03": {"name": "post_handshake_drop", "category": "censorship_suspect",
             "severity": "high", "stage": "data_phase",
             "summary": "≥3 циклов handshake→обрыв (≤5 с) за 120 с"},
    "G-04": {"name": "udp_response_blackhole", "category": "censorship_suspect",
             "severity": "high", "stage": "udp_response",
             "summary": "QUIC handshake ok, ответы не приходят; AWG tx↑/rx≈0"},
    "G-05": {"name": "auth_failure", "category": "config",
             "severity": "high", "stage": "pre_handshake",
             "summary": "auth_fail без auth_ok 5 мин (или ≥3 за 5 мин)"},
    "G-06": {"name": "dns_broken", "category": "dns",
             "severity": "high", "stage": "local",
             "summary": "≥2 DNS-ошибок за 60 с или SERVFAIL на ≥2 именах"},
    "G-07": {"name": "local_network_down", "category": "local_net",
             "severity": "high", "stage": "local",
             "summary": "network_unreachable/no_route, потеря default route"},
    "G-08": {"name": "tls_cert_error", "category": "config",
             "severity": "high", "stage": "tls_stall",
             "summary": "tls_cert_error без успешного TLS 5 мин"},
    "G-09": {"name": "endpoint_silent", "category": "endpoint_unreachable",
             "severity": "medium", "stage": "pre_handshake",
             "summary": "≥5 pre-handshake сбоев на host:port за 10 мин"},
    "G-10": {"name": "unit_flap", "category": "local_fault",
             "severity": "high", "stage": "local",
             "summary": "≥3 unit_exit (non-zero)/unit_start за 10 мин"},
    "G-11": {"name": "degraded_throughput", "category": "degraded",
             "severity": "medium", "stage": "data_phase",
             "summary": "≥2 отчётов speed < 2.0 Мбит/с подряд за 24 ч"},
    "G-12": {"name": "guard_selfdegraded", "category": "observability",
             "severity": "low", "stage": "local",
             "summary": "источник lost/очереди/unparsed/часы — деградация guard",
             "notify": False},
}


def rule_catalog() -> List[Dict[str, Any]]:
    """Catalogue entries in fixed rule order (``vpn guard rules``)."""
    return [dict(RULE_CATALOG[rule], id=rule) for rule in RULES_IN_ORDER]


def rules_hash(thresholds: Optional["Thresholds"] = None) -> str:
    """Deterministic ``sha256:<64 hex>`` of the rule catalogue + thresholds.

    The same value is printed by ``vpn guard rules`` and stored in
    ``guard.status/1``; it changes only when a rule/metadata/threshold
    changes, never because of local time or state (SPEC §10.2).
    """
    payload = {
        "rules": rule_catalog(),
        "thresholds": (thresholds if thresholds is not None
                       else Thresholds()).as_dict(),
    }
    return events.payload_hash(payload)

#: Signals that make each rule worth re-evaluating (event-driven path).
_RULE_TRIGGERS: Mapping[str, frozenset] = {
    "G-01": frozenset(BOOT_SIGNALS | DIAL_SIGNALS | HANDBOOK_SIGNALS),
    "G-02": frozenset(RESET_SIGNALS),
    "G-03": frozenset(HANDBOOK_SIGNALS | DROP_SIGNALS),
    "G-04": frozenset(IDLE_SIGNALS | HANDBOOK_SIGNALS | TRAFFIC_WAIT_SIGNALS),
    "G-05": frozenset(("auth_fail", "auth_ok")),
    "G-06": frozenset(DNS_SIGNALS),
    "G-07": frozenset(LOCAL_NET_SIGNALS),
    "G-08": frozenset(("tls_cert_error", "tls_handshake_ok")),
    "G-09": frozenset(SILENT_SIGNALS | HANDBOOK_SIGNALS
                      | frozenset(("tls_cert_error",))),
    "G-10": frozenset(("unit_start", "unit_stop", "unit_exit",
                       "unit_restart_scheduled")),
    "G-11": frozenset(PERF_TIMEOUT_SIGNALS | frozenset(("slow_consumer",))),
    "G-12": frozenset(SELF_SIGNALS),
}

#: Prose limits of ``guard.finding/1`` (clipped here, not by the writer).
MAX_PROSE_CHARS = 240
MAX_ITEM_CHARS = 120
MAX_EVIDENCE = 64
MAX_FP_CHECKS = 32
MAX_ALTERNATIVES = 8


def _iso_now() -> str:
    """RFC3339 UTC with milliseconds — only for hook-driven findings."""
    moment = datetime.now(timezone.utc)
    return (moment.strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{moment.microsecond // 1000:03d}Z")


@lru_cache(maxsize=1)
def finding_schema_hash() -> str:
    """``sha256:<64 hex>`` of the canonical ``guard.finding/1`` schema."""
    return schema.schema_hash(events.FINDING_SCHEMA)


# --- thresholds (SPEC §8.1 table; guard.json may override) ----------------------

INT_THRESHOLDS: Tuple[str, ...] = (
    "handshake_timeout_s", "startup_banner_s", "reset_storm_count",
    "reset_storm_window_s", "reset_storm_handshake_s",
    "post_handshake_drop_count", "post_handshake_drop_window_s",
    "udp_blackhole_count", "udp_blackhole_window_s", "awg_window_s",
    "awg_tx_min_bytes", "awg_consecutive_windows", "auth_fail_count",
    "auth_fail_window_s", "dns_fail_count", "dns_fail_window_s",
    "tls_ok_window_s", "route_change_grace_s", "suspend_grace_s",
    "cooldown_s", "confirm_windows", "quiet_windows", "activity_grace_s",
    "endpoint_silent_count", "endpoint_silent_window_s",
    "unit_flap_count", "unit_flap_window_s",
    "degraded_reports", "degraded_window_s",
    "degraded_timeout_count", "degraded_timeout_window_s",
    "self_lost_s", "self_unparsed_min_lines", "self_skew_s",
    "self_drop_window_s",
)
FLOAT_THRESHOLDS: Tuple[str, ...] = (
    "post_handshake_drop_gap_s", "awg_rx_ratio", "degraded_mbps",
    "self_unparsed_ratio")


@dataclass(frozen=True)
class Thresholds:
    """Default thresholds; **only** from ``guard.json`` at runtime (§10.3).

    User regexes are forbidden by contract (OQ-13) — this dataclass is the
    whole tuning surface of WP-G3.  ``from_mapping`` ignores unknown keys and
    rejects garbage values instead of silently breaking a rule.
    """

    handshake_timeout_s: int = 25
    startup_banner_s: int = 15
    reset_storm_count: int = 3
    reset_storm_window_s: int = 60
    reset_storm_handshake_s: int = 600
    post_handshake_drop_count: int = 3
    post_handshake_drop_window_s: int = 120
    post_handshake_drop_gap_s: float = 5.0
    udp_blackhole_count: int = 2
    udp_blackhole_window_s: int = 600
    awg_window_s: int = 120
    awg_tx_min_bytes: int = 262144
    awg_rx_ratio: float = 0.05
    awg_consecutive_windows: int = 2
    auth_fail_count: int = 3
    auth_fail_window_s: int = 300
    dns_fail_count: int = 2
    dns_fail_window_s: int = 60
    tls_ok_window_s: int = 300
    route_change_grace_s: int = 90
    #: Suspend/roaming stop factor of G-04 (§8.3): a resumed laptop must not
    #: look like a UDP blackhole for a few minutes.
    suspend_grace_s: int = 300
    cooldown_s: int = 300
    confirm_windows: int = 2
    quiet_windows: int = 3
    #: ``activity.json`` grace: the probe's own sockets outlive the marker
    #: by a few seconds (§8.4 "±30 с").
    activity_grace_s: int = 30
    #: G-09 `endpoint_silent` (§8.2): 5 failures / 10 min on one host:port.
    endpoint_silent_count: int = 5
    endpoint_silent_window_s: int = 600
    #: G-10 `unit_flap` (§8.2): 3 hard exits or starts / 10 min per unit.
    unit_flap_count: int = 3
    unit_flap_window_s: int = 600
    #: G-11 `degraded_throughput` (§8.2): 2 consecutive reports < 2.0 Mbps
    #: within 24 h, or 1 report + 3 read/write timeouts / 5 min.
    degraded_mbps: float = 2.0
    degraded_reports: int = 2
    degraded_window_s: int = 86400
    degraded_timeout_count: int = 3
    degraded_timeout_window_s: int = 300
    #: G-12 `guard_selfdegraded` (§8.2): a lost source > 60 s, unparsed > 30%
    #: of ≥ 200 app lines, queue drops, clock skew > 2 s.
    self_lost_s: int = 60
    self_unparsed_ratio: float = 0.3
    self_unparsed_min_lines: int = 200
    self_skew_s: int = 2
    self_drop_window_s: int = 60

    @classmethod
    def from_mapping(cls, payload: Optional[Mapping[str, Any]]) -> "Thresholds":
        """Build from the ``thresholds`` block of ``guard.json``."""
        base = cls()
        if payload is None:
            return base
        values: Dict[str, Any] = {}
        for name in INT_THRESHOLDS:
            raw = payload.get(name)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                continue
            values[name] = max(1, int(raw))
        for name in FLOAT_THRESHOLDS:
            raw = payload.get(name)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                continue
            values[name] = max(0.0, float(raw))
        return cls(**values) if values else base

    def as_dict(self) -> Dict[str, Any]:
        """JSON-friendly view for ``vpn guard rules`` (WP-G5)."""
        payload: Dict[str, Any] = {}
        for name in INT_THRESHOLDS:
            payload[name] = int(getattr(self, name))
        for name in FLOAT_THRESHOLDS:
            payload[name] = float(getattr(self, name))
        return payload


# --- server verdict (freshness already decided by WP-G4) ------------------------


@dataclass(frozen=True)
class ServerContext:
    """Result of the cross-vantage merge (``verdict.py`` is WP-G4).

    The rules never read ``server-verdict.json``: the merge layer validates
    the file, computes ``fresh`` (``fetched_at`` ≤ 65 min) and hands this
    object in.  A stale context is kept for evidence but never boosts.
    """

    threat_level: Optional[str] = None
    stage: Optional[str] = None
    age_s: int = 0
    fresh: bool = False
    lockdown: bool = False
    #: Verdict name (``RU-PROBE-SUSPECT``…) — informational, never an enum
    #: the rules act on.  ``present`` distinguishes "file absent" from a
    #: quiet server verdict.
    verdict: Optional[str] = None
    present: bool = False

    def __post_init__(self) -> None:
        if (self.threat_level is not None
                and self.threat_level not in events.THREAT_LEVELS):
            raise ValueError(
                f"unknown threat_level {self.threat_level!r}; "
                f"expected one of {events.THREAT_LEVELS}")

    @property
    def boosts(self) -> bool:
        """A fresh ``high`` verdict is the second independent signal (§2.3)."""
        return bool(self.fresh and self.threat_level == "high")

    @property
    def lockdown_expected(self) -> bool:
        """Active server lock-443 explains client-side resets (§9.3)."""
        return bool(self.fresh and self.lockdown)

    def as_finding(self) -> Dict[str, Any]:
        return {
            "threat_level": self.threat_level,
            "stage": self.stage,
            "age_s": max(0, int(self.age_s)),
            "fresh": bool(self.fresh),
            "lockdown": bool(self.lockdown),
        }


# --- outcomes -------------------------------------------------------------------


@dataclass
class RuleOutcome:
    """One rule verdict: a finding, a suppression, or an honest blind spot."""

    rule: str
    status: str
    unit: Optional[str] = None
    reason: Optional[str] = None
    finding: Optional[Dict[str, Any]] = None
    count: int = 0
    repeats: int = 0

    @property
    def is_finding(self) -> bool:
        return self.status == "finding" and self.finding is not None

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"rule": self.rule, "status": self.status}
        if self.unit is not None:
            payload["unit"] = self.unit
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.count:
            payload["count"] = self.count
        if self.repeats:
            payload["repeats"] = self.repeats
        if self.finding is not None:
            payload["finding"] = self.finding
        return payload


@dataclass
class _Verdict:
    """Internal rule output before hysteresis/cooldown/AC-counting."""

    satisfied: bool
    finding: Optional[Callable[[], Dict[str, Any]]] = None
    status: Optional[str] = None
    reason: Optional[str] = None
    #: Endpoint the finding actually reports (``rule|unit|endpoint|cause``
    #: dedup key of §8.1).  Rules that pick a host:port out of the window must
    #: set it, otherwise the key falls back to the *triggering event's*
    #: endpoint and one physical fault emits several findings in one batch
    #: (WP-G6 field checkpoint: triple G-09 with distinct upstream IPs and the
    #: same timestamp).
    dedup_endpoint: Optional[str] = None


# --- finding builder ------------------------------------------------------------


def build_finding(*, rule: str, unit: Optional[str],
                  profile: Optional[str],
                  endpoint: Optional[Mapping[str, Any]],
                  window_first: str, window_last: str, count: int,
                  evidence: Sequence[Mapping[str, Any]],
                  fp_checks: Sequence[str],
                  severity: str, confidence: str, category: str,
                  stage_hint: Optional[str],
                  hypothesis: str, action: str,
                  alternatives: Sequence[str] = (),
                  server: Optional[Mapping[str, Any]] = None,
                  expected_lockdown: bool = False,
                  cross_vantage: bool = False) -> Dict[str, Any]:
    """Assemble a ``guard.finding/1`` payload and validate it.

    ``id = sha256(rule|unit|endpoint|window_start)[:12]`` (§8.1) — stable
    across replays, so a golden corpus compares byte for byte (AC-9).  The
    payload carries counters, hashes and prose only: no log text, no secrets.
    """
    end_key = endpoint_key(endpoint)
    identity = "|".join((rule, unit or "-", end_key or "-", window_first))
    finding: Dict[str, Any] = {
        "schema": events.FINDING_SCHEMA,
        "id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12],
        "rule": rule,
        "severity": severity,
        "confidence": confidence,
        "category": category,
        "stage_hint": stage_hint,
        "unit": unit,
        "profile": profile,
        "endpoint": dict(endpoint) if isinstance(endpoint, Mapping) else None,
        "window": {"first": window_first, "last": window_last},
        "count": max(1, int(count)),
        "evidence": [dict(item) for item in list(evidence)[:MAX_EVIDENCE]],
        "fp_checks": [str(item)[:MAX_ITEM_CHARS]
                      for item in list(fp_checks)[:MAX_FP_CHECKS]],
        "hypothesis": str(hypothesis)[:MAX_PROSE_CHARS],
        "action": str(action)[:MAX_PROSE_CHARS],
        "cross_vantage": bool(cross_vantage),
        "expected_lockdown": bool(expected_lockdown),
        "schema_hash": finding_schema_hash(),
    }
    if alternatives:
        finding["alternatives"] = [str(item)[:MAX_ITEM_CHARS]
                                   for item in list(alternatives)[:MAX_ALTERNATIVES]]
    if server is not None:
        finding["server"] = dict(server)
    errors = schema.validate(finding, events.FINDING_SCHEMA)
    if errors:
        raise ValueError("invalid guard.finding/1: " + "; ".join(errors))
    return finding


# --- engine ---------------------------------------------------------------------


class RuleEngine:
    """Fixed-order rule evaluation over a bounded, monotonic event history."""

    def __init__(self, *, thresholds: Optional[Thresholds] = None,
                 server: Optional[ServerContext] = None,
                 clock: Optional[Clock] = None) -> None:
        self.thresholds = thresholds if thresholds is not None else Thresholds()
        self.windows = WindowStore()
        self.cooldown = CooldownRegistry(self.thresholds.cooldown_s)
        self.hysteresis = Hysteresis(self.thresholds.confirm_windows,
                                     self.thresholds.quiet_windows)
        #: Streaming path (``push``): 100 ms reorder buffer + coalescing.
        #: Replay path (``feed``) analyses immediately and stays bit-for-bit
        #: reproducible (AC-9).
        self.reorder = ReorderBuffer()
        self.coalescer = Coalescer()
        self._clock = clock
        self._server = server
        self._state_known = False
        self._active_profile: Optional[str] = None
        self._causes: Dict[str, str] = {}
        self._last_lifecycle: Optional[Dict[str, Any]] = None
        self._starts: Dict[str, int] = {}
        self._startups: Dict[str, Dict[str, Any]] = {}
        self._reported_insufficient: Set[Tuple[str, str, str]] = set()
        self._last_endpoint: Dict[Tuple[str, str], Optional[str]] = {}
        self._insufficient: Deque[Dict[str, Any]] = deque(maxlen=32)
        self._activity_until_us: Optional[int] = None
        self._route_changes: Deque[Tuple[int, str]] = deque(maxlen=64)
        self._suspend_us: Optional[int] = None
        self._roaming_us: Optional[int] = None
        self._awg_windows: Dict[str, Deque[Dict[str, Any]]] = {}
        self._awg_gone: Dict[str, bool] = {}
        self._awg_units: Dict[str, str] = {}
        #: Does the user expect AmneziaWG to be up?  ``None`` = no opinion from
        #: the caller; the engine then falls back to the last AWG lifecycle
        #: (``vpn_down`` means "not expected").  G-12 must not call a stopped
        #: AWG a "lost source" (WP-G6 field checkpoint: 61 s of ``sysfs:awg``
        #: loss while AWG was off).
        self._awg_expected: Optional[bool] = None
        #: The batch currently being folded contains G-07 evidence.  Endpoint
        #: rules consult the window anyway; G-06 also honours this so a drain
        #: carrying "no route" and DNS errors in one go never reports DNS as
        #: the cause (G-07 priority, §8.4).
        self._batch_local_net = False
        # G-11: ring of observed (finished) speed reports, no URLs/secrets.
        self._speed_reports: Deque[Dict[str, Any]] = deque(maxlen=8)
        # G-12: the guard's own health, filled by the run loop.
        self._source_status: Dict[str, Dict[str, Any]] = {}
        self._queue_drops = 0
        self._queue_drop_us: Optional[int] = None
        self._parse_ratio = 0.0
        self._parse_lines = 0
        self._parse_us: Optional[int] = None
        self._clock_skew_s = 0.0
        self._skew_us: Optional[int] = None
        self._self_ts: Optional[str] = None

    # -- contexts (filled by the daemon; WP-G4 wires them) -------------------

    def set_server(self, server: Optional[ServerContext]) -> None:
        self._server = server

    def note_state(self, active_profile: Optional[str]) -> None:
        """``state.json.active``: the channel the user brought up."""
        self._state_known = True
        self._active_profile = active_profile or None

    def note_activity(self, active: bool, now_us: Optional[int] = None) -> None:
        """Probe marker (``monitor``/``probe``/``speed``) with ±grace (§8.4)."""
        if not active:
            return
        stamp = int(now_us) if now_us is not None else self._now_us()
        self._activity_until_us = stamp + (
            self.thresholds.activity_grace_s * MONO_US_PER_S)

    def note_suspend(self, now_us: Optional[int] = None) -> None:
        self._suspend_us = int(now_us) if now_us is not None else self._now_us()

    def note_roaming(self, now_us: Optional[int] = None) -> None:
        self._roaming_us = int(now_us) if now_us is not None else self._now_us()

    def note_route_change(self, now_us: Optional[int] = None, *,
                          kind: str = "change",
                          ts: Optional[str] = None) -> List[RuleOutcome]:
        """Netlink route change (S4); a removed default route is G-07."""
        stamp = int(now_us) if now_us is not None else self._now_us()
        self._route_changes.append((stamp, str(kind)))
        if str(kind) not in ROUTE_GONE_KINDS:
            return []
        verdict = self._g07_route(stamp, ts)
        outcome = self._apply("G-07", None, verdict,
                              self._key("G-07", None, None,
                                        "default_route_removed"), stamp)
        return [outcome] if outcome is not None else []

    def note_awg_expected(self, expected: Optional[bool]) -> None:
        """AWG is supposed to be up (`mode.json`/контекст), or it is not.

        Only ``sysfs:awg`` uses this: a stopped AWG has no interface on
        purpose, so a missing sysfs source is "off", not a degraded guard.
        """
        self._awg_expected = None if expected is None else bool(expected)

    def _awg_expected_active(self) -> bool:
        """Whether the AWG source is meaningful right now (G-12 FP-guard)."""
        if self._awg_expected is not None:
            return self._awg_expected
        if self._awg_units:
            return True  # a unit_start was seen and no stop yet
        for unit, cause in self._causes.items():
            if unit.startswith(AWG_PREFIXES):
                return cause != "vpn_down"
        return False  # no evidence AWG was ever up → its sysfs is not "lost"

    def note_awg_gone(self, iface: Optional[str] = None,
                      now_us: Optional[int] = None) -> List[RuleOutcome]:
        """``awg_iface_gone``: disable the G-04 branch (G-10/G-07 own it)."""
        names = [iface] if isinstance(iface, str) and iface else list(
            self._awg_windows)
        for name in names:
            self._awg_gone[name] = True
            self._awg_windows.pop(name, None)
        return []

    # -- G-11 / G-12 contexts (S3 files and self-health) ----------------------

    def note_speed_report(self, payload: Optional[Mapping[str, Any]],
                          now_us: Optional[int] = None) -> None:
        """One ``last-speed.json`` snapshot (G-11, §8.3).

        Only **finished** reports count, CDN channels are excluded (Cloudflare
        never gives a bulk baseline) and only measured ``speed_mbps`` values
        are kept — no endpoints, URLs or profile secrets.
        """
        if not isinstance(payload, Mapping):
            return
        finished = payload.get("finished")
        if not isinstance(finished, str) or not finished.strip():
            return  # an interrupted report is not an observation
        stamp = int(now_us) if now_us is not None else self._now_us()
        channels = payload.get("channels")
        if not isinstance(channels, list):
            return
        for channel in channels:
            if not isinstance(channel, Mapping):
                continue
            name = channel.get("name")
            protocol = channel.get("protocol")
            if not isinstance(name, str) or not name:
                continue
            if (protocol in CDN_PROTOCOLS
                    or "cdn" in name.lower()):
                continue  # SPEC §8.2: CDN-профиль исключён
            speed = channel.get("speed_mbps")
            if isinstance(speed, bool) or not isinstance(speed, (int, float)):
                continue  # "no data" is not "0 Mbps"
            if any(existing.get("ts") == finished
                   and existing.get("channel") == name
                   for existing in self._speed_reports):
                continue  # the same report re-read (restart / mtime change)
            self._speed_reports.append({
                "mono_us": stamp,
                "ts": finished,
                "channel": name,
                "proto": protocol if isinstance(protocol, str) else None,
                "speed": float(speed),
            })

    # -- G-11 ring persistence (D-09) ----------------------------------------

    def speed_ring(self) -> List[Dict[str, Any]]:
        """Last :data:`SPEED_RING_PERSISTED` mini-records for ``state.json``.

        ``ts``, ``profile``, ``proto``, ``median_mbps`` — no endpoints, URLs
        or secrets (D-09).  Oldest first.
        """
        records = list(self._speed_reports)[-SPEED_RING_PERSISTED:]
        return [{"ts": str(record["ts"]),
                 "profile": str(record["channel"]),
                 "proto": record.get("proto"),
                 "median_mbps": round(float(record["speed"]), 3)}
                for record in records]

    def restore_speed_ring(self, records: Optional[Iterable[Mapping[str, Any]]],
                           *, now_us: int,
                           now_wall: Optional[datetime] = None) -> int:
        """Load the persisted ring; returns how many records were accepted.

        Monotonic time is **synthesised from the wall timestamp** exactly like
        any other ``state``-sourced context (§7.1, D-06): ``mono_us = now_us -
        (now_wall - ts)``.  A malformed record is skipped instead of breaking
        the daemon; a restart therefore no longer resets the G-11 history
        (D-09).
        """
        if not records:
            return 0
        moment = now_wall if now_wall is not None else datetime.now(timezone.utc)
        horizon = moment.timestamp()
        accepted = 0
        for record in records:
            if not isinstance(record, Mapping):
                continue
            ts_value = record.get("ts")
            profile = record.get("profile")
            speed = record.get("median_mbps")
            if (not isinstance(ts_value, str) or not isinstance(profile, str)
                    or not profile
                    or isinstance(speed, bool)
                    or not isinstance(speed, (int, float))):
                continue
            try:
                stamp = datetime.fromisoformat(
                    ts_value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            proto = record.get("proto")
            if any(existing.get("ts") == ts_value
                   and existing.get("channel") == profile
                   for existing in self._speed_reports):
                continue
            age_us = max(0, int((horizon - stamp) * MONO_US_PER_S))
            self._speed_reports.append({
                "mono_us": int(now_us) - age_us,
                "ts": ts_value,
                "channel": profile,
                "proto": proto if isinstance(proto, str) else None,
                "speed": float(speed),
            })
            accepted += 1
        return accepted

    def note_source_status(self, name: str, status: str,
                           now_us: Optional[int] = None,
                           ts: Optional[str] = None) -> None:
        """Source health of one reader/S3 file (G-12 ``source_lost``)."""
        if not isinstance(name, str) or not name:
            return
        if status in ("ok", "off"):
            self._source_status.pop(name, None)
            return
        stamp = int(now_us) if now_us is not None else self._now_us()
        record = self._source_status.get(name)
        if record is None or record.get("status") != status:
            self._source_status[name] = {
                "status": str(status),
                "since_us": stamp,
                "ts": ts if isinstance(ts, str) and ts else _iso_now(),
            }
            self._self_ts = self._source_status[name]["ts"]

    def note_queue_drops(self, count: int = 1,
                         now_us: Optional[int] = None) -> None:
        """Bounded-queue drops are a G-12 signal, never a finding of a rule."""
        amount = int(count) if isinstance(count, int) else 1
        if amount <= 0:
            return
        self._queue_drops += amount
        self._queue_drop_us = (int(now_us) if now_us is not None
                               else self._now_us())

    def note_parse_stats(self, unparsed_ratio: float, lines: int,
                         now_us: Optional[int] = None) -> None:
        """App-only unparsed ratio of the parser (D-10, G-12)."""
        try:
            self._parse_ratio = max(0.0, min(1.0, float(unparsed_ratio)))
        except (TypeError, ValueError):
            return
        self._parse_lines = max(0, int(lines))
        self._parse_us = (int(now_us) if now_us is not None
                          else self._now_us())

    def note_clock_skew(self, skew_s: float,
                        now_us: Optional[int] = None) -> None:
        """``realtime - monotonic`` drift estimate (G-12, RSK-9)."""
        try:
            value = abs(float(skew_s))
        except (TypeError, ValueError):
            return
        self._clock_skew_s = value
        if value > 0:
            self._skew_us = (int(now_us) if now_us is not None
                             else self._now_us())

    # -- streaming ----------------------------------------------------------

    def push(self, event: Dict[str, Any]) -> List[RuleOutcome]:
        """Reorder + coalesce + analyse one raw event (daemon path)."""
        return self._drain(self.reorder.push(event))

    def flush(self) -> List[RuleOutcome]:
        """Release the reorder buffer (tick/shutdown)."""
        return self._drain(self.reorder.flush())

    def seed(self, events: Iterable[Mapping[str, Any]]) -> None:
        """Backfill: fill the history **without** firing event-driven rules.

        ``vpn guard run --once`` and a restart replay an already collected
        window (SPEC §10.1); the next :meth:`tick` decides.  Seeding is also
        what makes the "pattern repeated in ≥ 2 windows" confidence of G-03
        observable: six cycles of a two-minute storm are all in the history
        before the first evaluation.
        """
        for event in events:
            payload = event if isinstance(event, dict) else dict(event)
            unit = payload.get("unit")
            if isinstance(unit, str) and unit:
                self.windows.add(payload)
            self._note_lifecycle(payload)
            self._note_startup_signal(payload)

    def _drain(self, ready: Sequence[Dict[str, Any]]) -> List[RuleOutcome]:
        """Fold a released batch *before* analysing it, then feed survivors.

        Coalescing must happen before the first event of the group reaches a
        rule, otherwise the rule sees ``count=1`` and the later folds are lost
        (a 20 ms burst of ``conn_reset`` is one G-02 event with ``count=3``).
        """
        survivors = [item for item in ready if not self.coalescer.fold(item)]
        # Batch-scoped G-07 priority: the whole drain is known before the first
        # rule sees it, so DNS cannot be blamed when the same batch also
        # carried "no route" (order inside the drain is not meaningful).
        self._batch_local_net = any(
            item.get("signal") in LOCAL_NET_SIGNALS for item in survivors)
        outcomes: List[RuleOutcome] = []
        try:
            for item in survivors:
                outcomes.extend(self.feed(item))
        finally:
            self._batch_local_net = False
        return self._sorted(outcomes)

    def feed(self, event: Mapping[str, Any]) -> List[RuleOutcome]:
        """Analyse one normalized event that is already in time order."""
        payload = event if isinstance(event, dict) else dict(event)
        signal = payload.get("signal")
        unit = payload.get("unit")
        now_us = mono_of(payload)
        if isinstance(unit, str) and unit:
            self.windows.add(payload)
        self._note_lifecycle(payload)
        self._note_startup_signal(payload)
        endpoint = endpoint_key(payload.get("endpoint"))
        results: List[RuleOutcome] = []
        for rule in RULES_IN_ORDER:
            if rule in GLOBAL_RULES:
                continue  # evaluated once per ticker (G-11/G-12)
            if signal not in _RULE_TRIGGERS[rule]:
                continue
            if isinstance(unit, str) and unit:
                # remember the endpoint so the 5 s ticker dedups with the same
                # `rule|unit|endpoint|cause` key (otherwise a tick would fire
                # a second time under a different key)
                self._last_endpoint[(rule, unit)] = endpoint
            verdict = self._evaluate(rule, unit, now_us)
            if verdict is None:
                continue
            # The dedup key must name what the finding *reports*, not the event
            # that happened to trigger the evaluation: a rule that selects a
            # host:port out of the window sets ``dedup_endpoint`` (§8.1).
            key_endpoint = (verdict.dedup_endpoint
                            if verdict.dedup_endpoint is not None
                            else endpoint)
            outcome = self._apply(rule, unit, verdict,
                                  self._key(rule, unit, key_endpoint,
                                            self._cause_key(rule)), now_us)
            if outcome is not None:
                results.append(outcome)
        return self._sorted(results)

    def tick(self, now_us: Optional[int] = None) -> List[RuleOutcome]:
        """5 s ticker: absence rules, hysteresis confirmations, unit GC."""
        stamp = int(now_us) if now_us is not None else self._now_us()
        self.windows.gc(stamp)
        self._gc_contexts(stamp)
        results: List[RuleOutcome] = []
        for unit in self.windows.units:
            for rule in RULES_IN_ORDER:
                if rule in GLOBAL_RULES:
                    continue
                verdict = self._evaluate(rule, unit, stamp)
                if verdict is None:
                    continue
                endpoint = self._last_endpoint.get((rule, unit))
                key_endpoint = (verdict.dedup_endpoint
                                if verdict.dedup_endpoint is not None
                                else endpoint)
                outcome = self._apply(rule, unit, verdict,
                                      self._key(rule, unit, key_endpoint,
                                                self._cause_key(rule)), stamp)
                if outcome is not None:
                    results.append(outcome)
        for rule in GLOBAL_RULES:
            verdict = self._evaluate(rule, None, stamp)
            if verdict is None:
                continue
            outcome = self._apply(rule, None, verdict,
                                  self._key(rule, None, None,
                                            self._cause_key(rule)), stamp)
            if outcome is not None:
                results.append(outcome)
        return self._sorted(results)

    # -- AWG counter windows (G-04 branch) -----------------------------------

    def note_awg_window(self, window: Mapping[str, Any]) -> List[RuleOutcome]:
        """One closed sysfs window from :class:`counters.AwgCounterPoller`."""
        iface = window.get("iface")
        if not isinstance(iface, str) or not iface:
            return []
        mono_s = window.get("mono_s")
        if isinstance(mono_s, (int, float)) and not isinstance(mono_s, bool):
            now_us = int(float(mono_s) * MONO_US_PER_S)
        else:
            now_us = self._now_us()
        raw_signals = window.get("signals")
        signals = {item for item in raw_signals if isinstance(item, str)} \
            if isinstance(raw_signals, (list, tuple)) else set()
        ts = window.get("ts")
        record = {
            "mono_us": now_us,
            "signals": signals,
            "tx_growth": window.get("tx_growth"),
            "rx_growth": window.get("rx_growth"),
            "ts": ts if isinstance(ts, str) else _iso_now(),
        }
        self._awg_windows.setdefault(iface, deque(maxlen=8)).append(record)
        self._awg_gone[iface] = False
        unit = self._awg_units.get(iface) or f"vpn-awg-split@{iface}.service"
        verdict = self._g04_awg(iface, unit, now_us)
        if verdict is None:
            return []
        outcome = self._apply("G-04", unit, verdict,
                              self._key("G-04", unit, None, "awg_tx_no_rx"),
                              now_us)
        return [outcome] if outcome is not None else []

    # -- introspection -------------------------------------------------------

    @property
    def lifecycle(self) -> Optional[Dict[str, Any]]:
        """``{signal, ts, cause}`` of the last lifecycle event (§8.1/AC-1)."""
        return dict(self._last_lifecycle) if self._last_lifecycle else None

    @property
    def insufficient(self) -> List[Dict[str, Any]]:
        """Honest blind spots for coverage/G-12 (never findings, §8.3)."""
        return [dict(item) for item in self._insufficient]

    def channel_state(self) -> str:
        """``armed`` unless no channel is known at all (SPEC §8.1, R-11)."""
        if self.windows.units or self._awg_windows:
            return "armed"
        return "idle"

    def window_snapshot(self, unit: str) -> List[Dict[str, Any]]:
        """Events of one unit (tests/``explain``; bounded by the window)."""
        window = self.windows.peek(unit)
        return window.events if window is not None else []

    # -- lifecycle / startup -------------------------------------------------

    def _note_lifecycle(self, event: Mapping[str, Any]) -> None:
        signal = event.get("signal")
        if signal not in LIFECYCLE_SIGNALS:
            return
        unit = event.get("unit")
        if not isinstance(unit, str) or not unit:
            return
        stamp = mono_of(event)
        ts = event.get("ts") if isinstance(event.get("ts"), str) else None
        fields = event.get("fields")
        cause = "unknown"
        if signal == "unit_start":
            previous = self._starts.get(unit)
            switched = any(
                other != unit and abs(stamp - when) <= 60 * MONO_US_PER_S
                for other, when in self._starts.items())
            cause = "vpn_switch" if switched else "vpn_up"
            self._starts[unit] = stamp
            self._startup_begin(unit, event)
            if previous is not None:
                self._clear_insufficient(unit)
        elif signal == "unit_stop":
            cause = "vpn_down"
            self._startups.pop(unit, None)
        elif signal == "unit_exit":
            status = fields.get("exit_status") if isinstance(fields, Mapping) else None
            # SPEC §8.1: exit 0 with `state.active=null` is a planned stop;
            # any non-zero code (or an unknown state with a code) is an
            # incident and belongs to G-10 (WP-G4), never masked as planned.
            planned = (status in (0, None)
                       and (not self._state_known
                            or self._active_profile is None))
            cause = "vpn_down" if planned else "unknown"
            self._startups.pop(unit, None)
        self._causes[unit] = cause
        self._last_lifecycle = {"signal": signal, "ts": ts, "cause": cause}
        if unit.startswith(AWG_PREFIXES):
            self._note_awg_lifecycle(unit, signal)

    def _note_awg_lifecycle(self, unit: str, signal: str) -> None:
        iface = unit.split("@", 1)[1]
        if iface.endswith(".service"):
            iface = iface[:-len(".service")]
        iface = iface.split(":", 1)[0]
        if signal == "unit_start":
            self._awg_units[iface] = unit
        elif signal in ("unit_stop", "unit_exit"):
            self._awg_units.pop(iface, None)

    def _startup_begin(self, unit: str, event: Mapping[str, Any]) -> None:
        if unit.startswith(AWG_PREFIXES):
            return  # AmneziaWG has no app logs; G-01 does not apply (§4.3)
        invocation = event.get("invocation_id")
        self._startups[unit] = {
            "started_us": mono_of(event),
            "ts": event.get("ts") if isinstance(event.get("ts"), str)
            else _iso_now(),
            "invocation": invocation if isinstance(invocation, str) else None,
            "banner_us": None,
            "dial_us": None,
            "dial_signal": None,
            "handshake_us": None,
        }

    def _note_startup_signal(self, event: Mapping[str, Any]) -> None:
        unit = event.get("unit")
        if not isinstance(unit, str):
            return
        check = self._startups.get(unit)
        if check is None:
            return
        invocation = event.get("invocation_id")
        if (check["invocation"] and isinstance(invocation, str)
                and invocation != check["invocation"]):
            return  # another instance of the unit — G-01 stays per INVOCATION_ID
        signal = event.get("signal")
        stamp = mono_of(event)
        if signal in BOOT_SIGNALS:
            if check["banner_us"] is None:
                check["banner_us"] = stamp
        elif signal in DIAL_SIGNALS:
            if check["dial_us"] is None:
                check["dial_us"] = stamp
                check["dial_signal"] = signal
        elif signal in HANDBOOK_SIGNALS:
            check["handshake_us"] = stamp

    # -- rule plumbing -------------------------------------------------------

    def _evaluate(self, rule: str, unit: Optional[str],
                  now_us: int) -> Optional[_Verdict]:
        handler = getattr(self, "_" + rule.lower().replace("-", ""), None)
        if handler is None:  # pragma: no cover - registry mismatch
            return None
        return handler(unit, now_us)

    def _apply(self, rule: str, unit: Optional[str], verdict: _Verdict,
               key: str, now_us: int) -> Optional[RuleOutcome]:
        confirm = 1 if rule in SINGLE_EVIDENCE_RULES \
            else self.thresholds.confirm_windows
        if verdict.status == "suppressed":
            self.hysteresis.observe(key, False, confirm)
            return RuleOutcome(rule, "suppressed", unit=unit,
                               reason=verdict.reason)
        if verdict.status == "insufficient_data":
            self.hysteresis.observe(key, False, confirm)
            mark = (rule, unit or "", verdict.reason or "")
            if mark in self._reported_insufficient:
                return None
            self._reported_insufficient.add(mark)
            self._insufficient.append(
                {"rule": rule, "unit": unit, "reason": verdict.reason})
            return RuleOutcome(rule, "insufficient_data", unit=unit,
                               reason=verdict.reason)
        step = self.hysteresis.observe(key, verdict.satisfied, confirm)
        if step == PENDING:
            return RuleOutcome(rule, "pending", unit=unit)
        if step != FIRE:
            return None
        emission = self.cooldown.evaluate(key, now_us)
        if not emission.allowed:
            return RuleOutcome(rule, "suppressed", unit=unit,
                               reason="cooldown", repeats=emission.repeats)
        finding = verdict.finding() if verdict.finding is not None else None
        if finding is None:
            return None
        return RuleOutcome(rule, "finding", unit=unit, finding=finding,
                           count=int(finding.get("count", 1)))

    def _key(self, rule: str, unit: Optional[str],
             endpoint: Optional[str], cause: Optional[str]) -> str:
        """``rule|unit|endpoint|cause`` — the dedup key of SPEC §8.1."""
        return "|".join((rule, unit or "-", endpoint or "-", cause or "-"))

    @staticmethod
    def _cause_key(rule: str) -> str:
        return {
            "G-01": "no_handshake_after_start",
            "G-02": "reset_storm",
            "G-03": "post_handshake_drop",
            "G-04": "udp_response_blackhole",
            "G-05": "auth_failure",
            "G-06": "dns_broken",
            "G-07": "local_network_down",
            "G-08": "tls_cert_error",
            "G-09": "endpoint_silent",
            "G-10": "unit_flap",
            "G-11": "degraded_throughput",
            "G-12": "guard_selfdegraded",
        }.get(rule, "unknown")

    # -- shared context helpers ---------------------------------------------

    def _now_us(self) -> int:
        if self._clock is not None:
            return self._clock.now_us()
        return mono_us()

    def _planned_stop(self, unit: str) -> bool:
        return self._causes.get(unit) == "vpn_down"

    def _unit_exit_is_fault(self, event: Mapping[str, Any],
                            unit: str) -> bool:
        """Non-zero exit (or an exit while the profile stays active) = fault.

        SPEC §8.1: ``exit 0`` with ``state.active=null`` is a planned stop;
        any non-zero code — or an unknown code while a profile is still
        active — is an incident and belongs to G-10.
        """
        fields = event.get("fields")
        status = fields.get("exit_status") if isinstance(fields, Mapping) else None
        if isinstance(status, int) and not isinstance(status, bool):
            return status != 0
        return bool(self._state_known and self._active_profile is not None)

    def _local_net_active(self, unit: str, now_us: int) -> bool:
        """G-07 owns the cause: tunnel rules are lowered, not notified."""
        return bool(self._fp(unit, now_us, LOCAL_NET_SIGNALS,
                             self.thresholds.reset_storm_window_s))

    def _other_channel_alive(self, unit: Optional[str], now_us: int) -> bool:
        """Any *other* channel proved a handshake recently (G-09 vantage)."""
        horizon = int(now_us) - 10 * 60 * MONO_US_PER_S
        for other in self.windows.units:
            if other == unit:
                continue
            window = self.windows.peek(other)
            if window is None:
                continue
            if any(mono_of(event) >= horizon
                   and event.get("signal") in HANDBOOK_SIGNALS
                   for event in window.events):
                return True
        for records in self._awg_windows.values():
            if any(int(record.get("mono_us", 0)) >= horizon
                   and "awg_tx_growth" in record.get("signals", set())
                   for record in records):
                return True
        return False

    def _state_off(self, unit: Optional[str]) -> bool:
        """``state.json.active=null`` for a ``vpnm-*`` unit (G-07 FP-guard)."""
        return bool(self._state_known and self._active_profile is None
                    and (unit is None or unit.startswith(VPNM_PREFIX)))

    def _state_off_any(self) -> bool:
        """``state.json.active=null`` regardless of the unit (G-04 AWG)."""
        return bool(self._state_known and self._active_profile is None)

    def _activity(self, now_us: int) -> bool:
        until = self._activity_until_us
        return until is not None and int(now_us) <= until

    def _fp(self, unit: str, now_us: int, signals: Iterable[str],
            seconds: int) -> List[Dict[str, Any]]:
        window = self.windows.peek(unit)
        if window is None:
            return []
        wanted = set(signals)
        return [event for event in window.since(now_us, seconds)
                if event.get("signal") in wanted]

    def _last_ts(self, unit: Optional[str],
                 fallback: Optional[str] = None) -> str:
        window = self.windows.peek(unit) if unit else None
        if window is not None:
            for event in reversed(window.events):
                ts = event.get("ts")
                if isinstance(ts, str) and ts:
                    return ts
        return fallback or _iso_now()

    def _latest_ts(self, fallback: Optional[str] = None) -> str:
        """Newest wall timestamp across all units (G-11/G-12 windows)."""
        best: Optional[str] = None
        for name in self.windows.units:
            window = self.windows.peek(name)
            if window is None:
                continue
            for event in window.events:
                ts = event.get("ts")
                if isinstance(ts, str) and (best is None or ts > best):
                    best = ts
        return best or fallback or _iso_now()

    def _profile(self, unit: Optional[str]) -> Optional[str]:
        if not isinstance(unit, str):
            return None
        for prefix in (VPNM_PREFIX,) + AWG_PREFIXES:
            if unit.startswith(prefix):
                instance = unit[len(prefix):]
                if instance.endswith(".service"):
                    instance = instance[:-len(".service")]
                return instance or None
        return None

    def _server_block(self) -> Optional[Dict[str, Any]]:
        if self._server is None:
            return None
        return self._server.as_finding()

    def _boost(self, base: str = "medium") -> str:
        return "high" if (self._server is not None and self._server.boosts) \
            else base

    def _cross(self) -> bool:
        return bool(self._server is not None and self._server.boosts)

    def _gc_contexts(self, now_us: int) -> None:
        """Drop per-unit context of units whose window was collected (§12)."""
        known = set(self.windows.units)
        for store in (self._causes, self._startups, self._starts):
            for unit in list(store):
                if unit not in known:
                    store.pop(unit, None)
        for key in list(self._last_endpoint):
            if key[1] not in known:
                self._last_endpoint.pop(key, None)
        for mark in list(self._reported_insufficient):
            if mark[1] and mark[1] not in known:
                self._reported_insufficient.discard(mark)

    def _clear_insufficient(self, unit: str) -> None:
        for mark in list(self._reported_insufficient):
            if mark[1] == unit:
                self._reported_insufficient.discard(mark)

    def _planned_switch(self, now_us: int) -> bool:
        """Two route changes inside 2 min = a planned network switch (G-07)."""
        horizon = int(now_us) - 120 * MONO_US_PER_S
        recent = [item for item in self._route_changes if item[0] >= horizon]
        return len(recent) >= 2

    def _recent_switch(self, unit: str) -> bool:
        """Another profile started within 60 s: ``vpn switch`` (G-10 stop)."""
        own = self._starts.get(unit)
        if own is None:
            return False
        return any(other != unit and abs(when - own) <= 60 * MONO_US_PER_S
                   for other, when in self._starts.items())

    def _suspend_blocked(self, now_us: int) -> bool:
        grace = self.thresholds.suspend_grace_s * MONO_US_PER_S
        for stamp in (self._suspend_us, self._roaming_us):
            if stamp is not None and 0 <= int(now_us) - stamp <= grace:
                return True
        return False

    def _route_blocked(self, now_us: int) -> bool:
        grace = self.thresholds.route_change_grace_s * MONO_US_PER_S
        return any(0 <= int(now_us) - stamp <= grace
                   for stamp, _kind in self._route_changes)

    @staticmethod
    def _sorted(outcomes: List[RuleOutcome]) -> List[RuleOutcome]:
        def order(outcome: RuleOutcome) -> Tuple[str, str, str]:
            finding = outcome.finding or {}
            window = finding.get("window") if isinstance(
                finding.get("window"), dict) else {}
            return (str(window.get("first", "")), outcome.rule,
                    str(finding.get("id", "")))

        return sorted(outcomes, key=order)

    # -- G-01 no_handshake_after_start ---------------------------------------

    def _g01(self, unit: Optional[str], now_us: int) -> Optional[_Verdict]:
        if not isinstance(unit, str) or not unit.startswith(VPNM_PREFIX):
            return None
        check = self._startups.get(unit)
        if check is None or check["handshake_us"] is not None:
            return None
        t = self.thresholds
        started = int(check["started_us"])
        deadline = started + t.handshake_timeout_s * MONO_US_PER_S
        if now_us < deadline:
            return None
        if self._planned_stop(unit):
            return _Verdict(False, status="suppressed", reason="planned_stop")
        if self._fp(unit, now_us, FLAP_SIGNALS, t.reset_storm_handshake_s):
            # G-10 suppression matrix §8.4: restarts are the cause, not the
            # consequence; the startup check of a new invocation is fresh.
            return _Verdict(False, status="suppressed", reason="unit_flap")
        if self._fp(unit, now_us, LOCAL_NET_SIGNALS, t.handshake_timeout_s):
            return _Verdict(False, status="insufficient_data",
                            reason="network_unreachable")
        banner = (check["banner_us"] is not None
                  and 0 <= int(check["banner_us"]) - started
                  <= t.startup_banner_s * MONO_US_PER_S)
        dial = (check["dial_us"] is not None
                and 0 <= int(check["dial_us"]) - started
                <= t.handshake_timeout_s * MONO_US_PER_S)
        if not (banner or dial):
            # No banner and no dial attempt: the app may simply not log at the
            # default loglevel.  Honest `insufficient_data`, never a false
            # `tunnel_down` (§8.3, AC-14).
            return _Verdict(False, status="insufficient_data",
                            reason="no_app_banner")
        evidence: List[Dict[str, Any]] = [
            {"kind": "absence", "metric": "handshake_after_start_s",
             "value": t.handshake_timeout_s}]
        proof: List[str] = []
        if banner:
            evidence.append({"kind": "signal_count",
                             "signal": "app_banner_ok", "value": 1})
            proof.append("app_banner_ok")
        if dial:
            evidence.append({"kind": "signal_count",
                             "signal": check["dial_signal"] or "dial_start",
                             "value": 1})
            proof.append(check["dial_signal"] or "dial_start")
        first_ts = check["ts"]
        last_ts = self._last_ts(unit, first_ts)
        profile = self._profile(unit)

        def build() -> Dict[str, Any]:
            return build_finding(
                rule="G-01", unit=unit, profile=profile, endpoint=None,
                window_first=first_ts, window_last=last_ts, count=1,
                evidence=evidence,
                fp_checks=[
                    "no handshake_ok/conn_established/quic_handshake_ok "
                    "after unit_start",
                    "coverage proven by " + "+".join(proof),
                    "no network_unreachable in the startup window",
                    "planned stop not observed",
                ],
                severity="high", confidence=self._boost("medium"),
                category="tunnel_down", stage_hint="pre_handshake",
                hypothesis="приложение живо (баннер/dial), но handshake не "
                           "установился за "
                           f"{t.handshake_timeout_s} с",
                action="не рестартить; vpn probe по профилю; проверить "
                       "сервер/порт и свежие ссылки",
                alternatives=["сервер не запущен или другой порт",
                              "блокировка на пути до сервера"],
                server=self._server_block(),
                cross_vantage=self._cross())

        return _Verdict(True, finding=build)

    # -- G-02 reset_storm ----------------------------------------------------

    def _g02(self, unit: Optional[str], now_us: int) -> Optional[_Verdict]:
        if not isinstance(unit, str):
            return None
        window = self.windows.peek(unit)
        if window is None:
            return None
        t = self.thresholds
        resets = [event for event in window.since(now_us, t.reset_storm_window_s)
                  if event.get("signal") in RESET_SIGNALS]
        if not resets:
            return _Verdict(False)
        groups: Dict[Optional[str], List[Dict[str, Any]]] = {}
        for event in resets:
            groups.setdefault(endpoint_key(event.get("endpoint")),
                              []).append(event)
        endpoint, group = max(
            groups.items(),
            key=lambda item: (sum_counts(item[1]), str(item[0])))
        count = sum_counts(group)
        if count < t.reset_storm_count:
            return _Verdict(False)
        handshakes = [event for event in
                      window.since(now_us, t.reset_storm_handshake_s)
                      if event.get("signal") in HANDBOOK_SIGNALS]
        if not handshakes:
            # Resets before any handshake are G-09 (pre_handshake), not G-02.
            return _Verdict(False)
        if self._planned_stop(unit):
            return _Verdict(False, status="suppressed", reason="planned_stop")
        if self._activity(now_us):
            return _Verdict(False, status="suppressed", reason="active_probe")
        if self._fp(unit, now_us, ("auth_fail",), t.auth_fail_window_s):
            return _Verdict(False, status="suppressed", reason="auth_fail")
        if self._fp(unit, now_us, FLAP_SIGNALS, t.reset_storm_handshake_s):
            return _Verdict(False, status="suppressed", reason="unit_flap")
        if self._fp(unit, now_us, LOCAL_NET_SIGNALS, t.reset_storm_window_s):
            return _Verdict(False, status="suppressed", reason="local_network")
        if len(self._cycles(unit, now_us,
                            t.post_handshake_drop_window_s)) \
                >= t.post_handshake_drop_count:
            # SPEC §8.3: resets inside the post-handshake gap are a G-03
            # fingerprint ("усиливает G-03, а не просто сеть шумит") — one
            # storm must not produce two high findings (AC-2).
            return _Verdict(False, status="suppressed",
                            reason="post_handshake_drop")
        first_ts = min((event.get("ts") for event in group
                        if isinstance(event.get("ts"), str)), default=None)
        last_ts = max((event.get("ts") for event in group
                       if isinstance(event.get("ts"), str)), default=None)
        if first_ts is None:
            first_ts = self._last_ts(unit)
        if last_ts is None:
            last_ts = first_ts
        endpoint_payload = None
        if endpoint:
            host, _, port = endpoint.rpartition(":")
            if host and port.isdigit():
                endpoint_payload = {"host": host, "port": int(port)}
        profile = self._profile(unit)
        handshake_count = sum_counts(handshakes)
        evidence: List[Dict[str, Any]] = [
            {"kind": "signal_count", "signal": "conn_reset", "value": count},
            {"kind": "signal_count", "signal": "handshake_ok",
             "value": handshake_count},
            {"kind": "ratio", "metric": "resets_per_handshake",
             "value": round(count / max(handshake_count, 1), 2)},
        ]
        confidence = self._boost("medium")
        lockdown = bool(self._server is not None
                        and self._server.lockdown_expected)
        severity = "info" if lockdown else "high"
        hypothesis = ("серия reset/EOF после успешного handshake — "
                      "цензура на пути или серверный сбой")
        action = "не рестартить; vpn probe; свежие ссылки; резервный канал"
        if lockdown:
            hypothesis = ("ожидаемо: активен серверный lock-443 (lockdown), "
                          "клиентские обрывы не цензура")
            action = ("снять серверный lock-443 или дождаться TTL; "
                      "проверить vpn probe")
        cross = bool(self._cross() or lockdown)

        def build() -> Dict[str, Any]:
            return build_finding(
                rule="G-02", unit=unit, profile=profile,
                endpoint=endpoint_payload,
                window_first=first_ts, window_last=last_ts, count=count,
                evidence=evidence,
                fp_checks=[
                    f"handshake seen within {t.reset_storm_handshake_s}s",
                    f"no auth_fail in {t.auth_fail_window_s}s",
                    f"no unit_exit/restart in {t.reset_storm_handshake_s}s",
                    f"no network_unreachable in {t.reset_storm_window_s}s",
                    "no active probe marker",
                    "planned stop not observed",
                ],
                severity=severity, confidence=confidence,
                category="censorship_suspect", stage_hint="post_handshake",
                hypothesis=hypothesis, action=action,
                alternatives=["свои пробы или параллельные клиенты",
                              "рестарт сервера",
                              "локальная сеть/маршрут"],
                server=self._server_block(),
                expected_lockdown=lockdown, cross_vantage=cross)

        return _Verdict(True, finding=build, dedup_endpoint=endpoint)

    # -- G-03 post_handshake_drop -------------------------------------------

    def _cycles(self, unit: str, now_us: int,
                horizon_s: int) -> List[Tuple[Dict[str, Any], Dict[str, Any],
                                              int]]:
        """Pair every handshake with the next drop ≤ gap (SPEC §8.3)."""
        window = self.windows.peek(unit)
        if window is None:
            return []
        t = self.thresholds
        gap_us = int(t.post_handshake_drop_gap_s * MONO_US_PER_S)
        events_in = window.since(now_us, horizon_s + gap_us / MONO_US_PER_S)
        handshakes = [event for event in events_in
                      if event.get("signal") in HANDBOOK_SIGNALS]
        drops = [event for event in events_in
                 if event.get("signal") in DROP_SIGNALS]
        used: Set[int] = set()
        cycles: List[Tuple[Dict[str, Any], Dict[str, Any], int]] = []
        for handshake in handshakes:
            start = mono_of(handshake)
            start_ep = endpoint_key(handshake.get("endpoint"))
            best: Optional[Tuple[int, int, Dict[str, Any]]] = None
            for index, drop in enumerate(drops):
                if index in used:
                    continue
                gap = mono_of(drop) - start
                if gap < 0 or gap > gap_us:
                    continue
                drop_ep = endpoint_key(drop.get("endpoint"))
                if start_ep and drop_ep and start_ep != drop_ep:
                    continue
                if best is None or gap < best[0]:
                    best = (gap, index, drop)
            if best is not None:
                used.add(best[1])
                cycles.append((handshake, best[2], best[0]))
        return cycles

    def _g03(self, unit: Optional[str], now_us: int) -> Optional[_Verdict]:
        if not isinstance(unit, str):
            return None
        t = self.thresholds
        window_s = t.post_handshake_drop_window_s
        wide = self._cycles(unit, now_us, 2 * window_s)
        if not wide:
            return _Verdict(False)
        narrow = [cycle for cycle in wide
                  if now_us - mono_of(cycle[0]) <= window_s * MONO_US_PER_S]
        if len(narrow) < t.post_handshake_drop_count:
            return _Verdict(False)
        if self._planned_stop(unit):
            return _Verdict(False, status="suppressed", reason="planned_stop")
        if self._activity(now_us):
            return _Verdict(False, status="suppressed", reason="active_probe")
        if self._fp(unit, now_us, ("auth_fail",), t.auth_fail_window_s):
            return _Verdict(False, status="suppressed", reason="auth_fail")
        if self._fp(unit, now_us, ("tls_cert_error",), t.auth_fail_window_s):
            return _Verdict(False, status="suppressed", reason="tls_cert_error")
        if self._fp(unit, now_us, FLAP_SIGNALS, window_s):
            return _Verdict(False, status="suppressed", reason="unit_flap")
        if self._fp(unit, now_us, ("slow_consumer",), window_s):
            return _Verdict(False, status="suppressed", reason="slow_consumer")
        if self._local_net_active(unit, now_us):
            return _Verdict(False, status="suppressed", reason="local_network")
        lockdown = bool(self._server is not None
                        and self._server.lockdown_expected)
        severity = "info" if lockdown else "high"
        if lockdown:
            confidence = "high"
        elif self._server is not None and self._server.boosts:
            confidence = "high"
        elif len(wide) >= 2 * t.post_handshake_drop_count:
            confidence = "high"  # the pattern repeated in ≥ 2 windows (§8.3)
        else:
            confidence = "medium"
        gaps = sorted(cycle[2] for cycle in narrow)
        median = gaps[len(gaps) // 2] / MONO_US_PER_S
        endpoints = [endpoint_key(cycle[0].get("endpoint"))
                     for cycle in narrow]
        endpoint = next((item for item in endpoints if item), None)
        endpoint_payload = None
        if endpoint:
            host, _, port = endpoint.rpartition(":")
            if host and port.isdigit():
                endpoint_payload = {"host": host, "port": int(port)}
        stamps = [cycle[0].get("ts") for cycle in narrow
                  if isinstance(cycle[0].get("ts"), str)]
        first_ts = min(stamps) if stamps else self._last_ts(unit)
        last_ts = max([cycle[1].get("ts") for cycle in narrow
                       if isinstance(cycle[1].get("ts"), str)] or stamps
                      or [first_ts])
        evidence: List[Dict[str, Any]] = [
            {"kind": "signal_count", "signal": "handshake_ok",
             "value": len(narrow)},
            {"kind": "signal_count", "signal": "conn_reset",
             "value": sum(1 for cycle in narrow
                          if cycle[1].get("signal") == "conn_reset")},
            {"kind": "cycle", "metric": "handshake_to_drop_s",
             "median": round(median, 3)},
        ]
        profile = self._profile(unit)
        hypothesis = ("handshake проходит, данные/reset — следом: "
                      "data-phase воздействие на пути")
        if lockdown:
            hypothesis = ("ожидаемо: активен серверный lock-443 "
                          "(lockdown), клиентские обрывы не цензура")
        action = ("не рестартить; vpn probe; свежие ссылки; резервный канал")
        if lockdown:
            action = "снять серверный lock-443 или дождаться TTL; проверить vpn probe"
        alternatives = ["рестарт сервера", "локальный краш клиента"]
        if not lockdown:
            alternatives.append("потери/высокий RTT на пути")
        cross = bool(self._cross() or lockdown)

        def build() -> Dict[str, Any]:
            return build_finding(
                rule="G-03", unit=unit, profile=profile,
                endpoint=endpoint_payload,
                window_first=first_ts, window_last=last_ts,
                count=len(narrow), evidence=evidence,
                fp_checks=[
                    f"{len(narrow)} cycles handshake→drop in {window_s}s",
                    f"every drop within {t.post_handshake_drop_gap_s}s "
                    "after handshake",
                    "no unit_exit/unit_restart_scheduled in window",
                    f"no auth_fail in {t.auth_fail_window_s}s",
                    f"no tls_cert_error in {t.auth_fail_window_s}s",
                    "no slow_consumer in window",
                    "no active probe marker",
                    "server verdict fresh" if (self._server is not None
                                               and self._server.fresh)
                    else "server verdict absent/stale",
                ],
                severity=severity, confidence=confidence,
                category="censorship_suspect", stage_hint="data_phase",
                hypothesis=hypothesis, action=action,
                alternatives=alternatives,
                server=self._server_block(),
                expected_lockdown=lockdown, cross_vantage=cross)

        return _Verdict(True, finding=build, dedup_endpoint=endpoint)

    # -- G-04 udp_response_blackhole (Hysteria2 branch) ----------------------

    def _g04(self, unit: Optional[str], now_us: int) -> Optional[_Verdict]:
        if not isinstance(unit, str):
            return None
        window = self.windows.peek(unit)
        if window is None:
            return None
        t = self.thresholds
        horizon = t.udp_blackhole_window_s
        idles = [event for event in window.since(now_us, horizon)
                 if event.get("signal") in IDLE_SIGNALS]
        count = sum_counts(idles)
        if count < t.udp_blackhole_count:
            return _Verdict(False)
        first_idle = min(idles, key=mono_of)
        handshakes_before = [
            event for event in window.since(now_us, horizon)
            if event.get("signal") in HANDBOOK_SIGNALS
            and mono_of(event) < mono_of(first_idle)]
        if not handshakes_before:
            return _Verdict(False)
        retries = [
            event for event in window.since(now_us, horizon)
            if mono_of(event) > mono_of(first_idle)
            and (event.get("signal") in HANDBOOK_SIGNALS
                 or event.get("signal") in TRAFFIC_WAIT_SIGNALS)]
        if not retries:
            # No repeat handshake / timeout after the idle: the client may just
            # have been silent, which is not evidence of a UDP blackhole.
            return _Verdict(False)
        if self._planned_stop(unit):
            return _Verdict(False, status="suppressed", reason="planned_stop")
        if self._activity(now_us):
            return _Verdict(False, status="suppressed", reason="active_probe")
        if self._fp(unit, now_us, ("auth_fail",), t.auth_fail_window_s):
            return _Verdict(False, status="suppressed", reason="auth_fail")
        if self._suspend_blocked(now_us):
            return _Verdict(False, status="suppressed", reason="suspend/roaming")
        if self._route_blocked(now_us):
            return _Verdict(False, status="suppressed", reason="route_change")
        if self._local_net_active(unit, now_us):
            return _Verdict(False, status="suppressed", reason="local_network")
        endpoint = endpoint_key(idles[-1].get("endpoint")) or endpoint_key(
            handshakes_before[-1].get("endpoint"))
        endpoint_payload = None
        if endpoint:
            host, _, port = endpoint.rpartition(":")
            if host and port.isdigit():
                endpoint_payload = {"host": host, "port": int(port)}
        stamps = [event.get("ts") for event in idles + handshakes_before
                  if isinstance(event.get("ts"), str)]
        first_ts = min(stamps) if stamps else self._last_ts(unit)
        last_ts = max(stamps) if stamps else first_ts
        evidence: List[Dict[str, Any]] = [
            {"kind": "signal_count", "signal": "quic_idle_timeout",
             "value": count},
            {"kind": "signal_count", "signal": "quic_handshake_ok",
             "value": sum_counts(handshakes_before)},
            {"kind": "ratio", "metric": "idle_timeouts_per_window",
             "value": round(count / max(horizon / 60, 1), 3)},
        ]
        profile = self._profile(unit)

        def build() -> Dict[str, Any]:
            return build_finding(
                rule="G-04", unit=unit, profile=profile,
                endpoint=endpoint_payload,
                window_first=first_ts, window_last=last_ts, count=count,
                evidence=evidence,
                fp_checks=[
                    "quic handshake succeeded before the idle timeouts",
                    "client retried after idle (dial/handshake/timeout)",
                    f"no auth_fail in {t.auth_fail_window_s}s",
                    f"no suspend/roaming in {t.suspend_grace_s}s",
                    f"no route change in {t.route_change_grace_s}s",
                    "no active probe marker",
                    "planned stop not observed",
                ],
                severity="high", confidence=self._boost("medium"),
                category="censorship_suspect", stage_hint="udp_response",
                hypothesis="QUIC handshake проходит, ответные данные не "
                           "приходят — UDP response blackhole",
                action="не рестартить; vpn probe; проверить UDP 443/obfs; "
                       "резервный канал",
                alternatives=["сервер не отвечает/лежит",
                              "битый конфиг или порт Hysteria2",
                              "роуминг и потери на пути"],
                server=self._server_block(), cross_vantage=self._cross())

        return _Verdict(True, finding=build)

    # -- G-04 branch awg_tx_no_rx (counters only) ---------------------------

    def _g04_awg(self, iface: str, unit: str,
                 now_us: int) -> Optional[_Verdict]:
        t = self.thresholds
        if self._awg_gone.get(iface):
            return _Verdict(False, status="suppressed", reason="awg_iface_gone")
        windows = list(self._awg_windows.get(iface) or ())
        needed = max(1, t.awg_consecutive_windows)
        recent = windows[-needed:]
        if len(recent) < needed:
            return _Verdict(False)
        counter_signals = set(COUNTER_SIGNALS)
        if not all(counter_signals <= set(item["signals"]) for item in recent):
            return _Verdict(False)
        if self._state_off_any() or self._causes.get(unit) == "vpn_down":
            return _Verdict(False, status="suppressed", reason="planned_stop")
        if self._suspend_blocked(now_us):
            return _Verdict(False, status="suppressed", reason="suspend/roaming")
        if self._route_blocked(now_us):
            return _Verdict(False, status="suppressed", reason="route_change")
        if self._activity(now_us):
            return _Verdict(False, status="suppressed", reason="active_probe")
        tx_total = sum(int(item["tx_growth"] or 0) for item in recent)
        rx_total = sum(int(item["rx_growth"] or 0) for item in recent)
        ratio = round(rx_total / max(tx_total, 1), 4)
        first_ts = recent[0]["ts"]
        last_ts = recent[-1]["ts"]
        evidence: List[Dict[str, Any]] = [
            {"kind": "counter", "signal": "awg_tx_growth", "value": tx_total,
             "unit": "bytes"},
            {"kind": "counter", "signal": "awg_rx_flat", "value": rx_total,
             "unit": "bytes"},
            {"kind": "ratio", "metric": "rx_tx_ratio", "value": ratio},
        ]
        profile = iface

        def build() -> Dict[str, Any]:
            return build_finding(
                rule="G-04", unit=unit, profile=profile, endpoint=None,
                window_first=first_ts, window_last=last_ts,
                count=len(recent), evidence=evidence,
                fp_checks=[
                    f"{len(recent)} consecutive {t.awg_window_s}s windows",
                    f"tx grew ≥ {t.awg_tx_min_bytes} bytes per window",
                    f"rx growth below {t.awg_rx_ratio} of tx growth",
                    "iface present for the whole window",
                    f"no suspend/roaming in {t.suspend_grace_s}s",
                    f"no route change in {t.route_change_grace_s}s",
                    "state.active != null",
                ],
                severity="high", confidence=self._boost("medium"),
                category="censorship_suspect", stage_hint="udp_response",
                hypothesis="AWG: клиент устойчиво шлёт, ответы почти не "
                           "приходят — UDP-блэкаут на пути",
                action="не рестартить; vpn probe по AWG; проверить резервный "
                       "канал",
                alternatives=["сервер не отвечает/лежит",
                              "битый конфиг AWG"],
                server=self._server_block(), cross_vantage=self._cross())

        return _Verdict(True, finding=build)

    # -- G-05 auth_failure ---------------------------------------------------

    def _g05(self, unit: Optional[str], now_us: int) -> Optional[_Verdict]:
        if not isinstance(unit, str):
            return None
        window = self.windows.peek(unit)
        if window is None:
            return None
        t = self.thresholds
        auth_events = [event for event in
                       window.since(now_us, t.auth_fail_window_s)
                       if event.get("signal") in ("auth_fail", "auth_ok")]
        if auth_events and auth_events[-1].get("signal") == "auth_ok":
            # a later successful auth proves the credentials work: the rule is
            # about the *current* failure, not about a historical count (§8.2)
            return _Verdict(False)
        fails = [event for event in auth_events
                 if event.get("signal") == "auth_fail"]
        count = sum_counts(fails)
        if count < 1:
            return _Verdict(False)
        oks = [event for event in auth_events
               if event.get("signal") == "auth_ok"]
        branch_a = not oks  # auth_fail and no auth_ok within 5 minutes
        branch_b = count >= t.auth_fail_count
        if not (branch_a or branch_b):
            return _Verdict(False)
        stamps = [event.get("ts") for event in fails
                  if isinstance(event.get("ts"), str)]
        first_ts = min(stamps) if stamps else self._last_ts(unit)
        last_ts = max(stamps) if stamps else first_ts
        evidence: List[Dict[str, Any]] = [
            {"kind": "signal_count", "signal": "auth_fail", "value": count}]
        if branch_a:
            evidence.append({"kind": "absence", "metric": "auth_ok_s",
                             "value": t.auth_fail_window_s})
        confidence = "high" if branch_b else self._boost("medium")
        profile = self._profile(unit)

        def build() -> Dict[str, Any]:
            return build_finding(
                rule="G-05", unit=unit, profile=profile, endpoint=None,
                window_first=first_ts, window_last=last_ts, count=count,
                evidence=evidence,
                fp_checks=[
                    f"no auth_ok in {t.auth_fail_window_s}s" if branch_a
                    else f"auth_fail count ≥ {t.auth_fail_count} in "
                         f"{t.auth_fail_window_s}s",
                    "no unit_exit/unit_restart_scheduled",
                    "server lock-443 is not an auth failure",
                ],
                severity="high", confidence=confidence,
                category="config", stage_hint="pre_handshake",
                hypothesis="сервер отвергает креды профиля — конфиг, а не "
                           "цензура",
                action="проверить логин/пароль профиля и ссылки: vpn links, "
                       "vpn profile pull; lock-443 — не auth",
                alternatives=["профиль устарел после ротации ключей",
                              "серверный lock-443 (не auth)"],
                server=self._server_block(), cross_vantage=False)

        return _Verdict(True, finding=build)

    # -- G-06 dns_broken -----------------------------------------------------

    def _g06(self, unit: Optional[str], now_us: int) -> Optional[_Verdict]:
        if not isinstance(unit, str):
            return None
        window = self.windows.peek(unit)
        if window is None:
            return None
        t = self.thresholds
        failures = [event for event in window.since(now_us, t.dns_fail_window_s)
                    if event.get("signal") in DNS_STORM_SIGNALS]
        count = sum_counts(failures)
        servfails = [event for event in failures
                     if event.get("signal") == "dns_servfail"]
        names = {endpoint_key(event.get("endpoint")) or event.get("template")
                 for event in servfails}
        names.discard(None)
        if count < t.dns_fail_count and len(names) < 2:
            return _Verdict(False)
        if self._fp(unit, now_us, LOCAL_NET_SIGNALS, t.dns_fail_window_s) \
                or self._batch_local_net:
            # G-07 owns the cause, DNS is a symptom: one fault must not produce
            # two high findings (SPEC §8.4 suppression matrix).  Two sources of
            # proof: the window already holds "no route", or the drain being
            # folded carries it alongside the DNS errors (``_batch_local_net``).
            return _Verdict(False, status="suppressed",
                            reason="local_net_down")
        kinds = {event.get("signal") for event in failures}
        confidence = "high" if (len(names) >= 2 or len(kinds) >= 2) \
            else self._boost("medium")
        stamps = [event.get("ts") for event in failures
                  if isinstance(event.get("ts"), str)]
        first_ts = min(stamps) if stamps else self._last_ts(unit)
        last_ts = max(stamps) if stamps else first_ts
        evidence: List[Dict[str, Any]] = [
            {"kind": "signal_count",
             "signal": failures[-1].get("signal") or "dns_lookup_fail",
             "value": count},
            {"kind": "ratio", "metric": "distinct_names", "value": len(names)},
        ]
        # Honest wording: without a resolvable endpoint/template the parser
        # gives no name at all — that is "not extracted", not "0 names broken".
        if names:
            names_check = f"{len(names)} distinct names affected"
        else:
            names_check = "distinct names not extracted (no endpoint/template)"
        profile = self._profile(unit)

        def build() -> Dict[str, Any]:
            return build_finding(
                rule="G-06", unit=unit, profile=profile, endpoint=None,
                window_first=first_ts, window_last=last_ts, count=max(1, count),
                evidence=evidence,
                fp_checks=[
                    "no network_unreachable/no_route in 60s",
                    "resolver errors within 60s",
                    names_check,
                ],
                severity="high", confidence=confidence,
                category="dns", stage_hint="local",
                hypothesis="резолвер не отвечает или подменяет ответы — "
                           "локальный слой DNS",
                action="vpn repair; проверить /etc/resolv.conf, DoH и dnsmasq "
                       "сплита AWG",
                alternatives=["DNS-подмена/отравление провайдером",
                              "локальный резолвер остановлен"],
                server=self._server_block(), cross_vantage=False)

        return _Verdict(True, finding=build)

    # -- G-07 local_network_down ---------------------------------------------

    def _g07(self, unit: Optional[str], now_us: int) -> Optional[_Verdict]:
        if not isinstance(unit, str):
            return None
        window = self.windows.peek(unit)
        if window is None:
            return None
        t = self.thresholds
        events_in = [event for event in
                     window.since(now_us, t.reset_storm_window_s)
                     if event.get("signal") in LOCAL_NET_SIGNALS]
        if not events_in:
            return _Verdict(False)
        if self._state_off(unit):
            return _Verdict(False, status="suppressed", reason="vpn_off")
        if self._planned_switch(now_us):
            return _Verdict(False, status="suppressed", reason="network_switch")
        count = sum_counts(events_in)
        stamps = [event.get("ts") for event in events_in
                  if isinstance(event.get("ts"), str)]
        first_ts = min(stamps) if stamps else self._last_ts(unit)
        last_ts = max(stamps) if stamps else first_ts
        signal = events_in[-1].get("signal")
        profile = self._profile(unit)
        evidence: List[Dict[str, Any]] = [
            {"kind": "signal_count", "signal": signal, "value": count}]

        def build() -> Dict[str, Any]:
            return build_finding(
                rule="G-07", unit=unit, profile=profile, endpoint=None,
                window_first=first_ts, window_last=last_ts, count=count,
                evidence=evidence,
                fp_checks=[
                    "no planned network switch in 120s",
                    "state.active != null",
                    "route events within 60s",
                ],
                severity="high", confidence="high",
                category="local_net", stage_hint="local",
                hypothesis="нет маршрута/сети на клиенте — локальная сеть, "
                           "не цензура",
                action="проверить Wi-Fi/маршрут по умолчанию; VPN тут ни "
                       "при чём",
                alternatives=["Wi-Fi отключён или сменил сеть",
                              "провайдер сбросил маршрут"],
                server=self._server_block(), cross_vantage=False)

        return _Verdict(True, finding=build)

    def _g07_route(self, now_us: int,
                   ts: Optional[str]) -> _Verdict:
        """Default route removed (netlink hook, S4 opt-in)."""
        if self._state_known and self._active_profile is None:
            return _Verdict(False, status="suppressed", reason="vpn_off")
        if len([item for item in self._route_changes
                if item[0] >= int(now_us) - 120 * MONO_US_PER_S]) >= 2:
            return _Verdict(False, status="suppressed", reason="network_switch")
        stamp = ts if isinstance(ts, str) and ts else _iso_now()
        evidence: List[Dict[str, Any]] = [
            {"kind": "counter", "metric": "default_route_removed", "value": 1}]

        def build() -> Dict[str, Any]:
            return build_finding(
                rule="G-07", unit=None, profile=None, endpoint=None,
                window_first=stamp, window_last=stamp, count=1,
                evidence=evidence,
                fp_checks=["state.active != null",
                           "no planned network switch in 120s"],
                severity="high", confidence="high",
                category="local_net", stage_hint="local",
                hypothesis="удалён маршрут по умолчанию — локальная сеть, "
                           "не цензура",
                action="проверить Wi-Fi/маршрут по умолчанию; VPN тут ни "
                       "при чём",
                alternatives=["Wi-Fi отключён или сменил сеть",
                              "провайдер сбросил маршрут"],
                server=self._server_block(), cross_vantage=False)

        return _Verdict(True, finding=build)

    # -- G-08 tls_cert_error -------------------------------------------------

    def _g08(self, unit: Optional[str], now_us: int) -> Optional[_Verdict]:
        if not isinstance(unit, str):
            return None
        window = self.windows.peek(unit)
        if window is None:
            return None
        t = self.thresholds
        errors = [event for event in window.since(now_us, t.tls_ok_window_s)
                  if event.get("signal") == "tls_cert_error"]
        if not errors:
            return _Verdict(False)
        oks = [event for event in window.since(now_us, t.tls_ok_window_s)
               if event.get("signal") == "tls_handshake_ok"]
        if oks:
            return _Verdict(False)
        if self._activity(now_us):
            return _Verdict(False, status="suppressed", reason="active_probe")
        count = sum_counts(errors)
        clock_fallback = any(bool(event.get("clock_fallback")) for event in errors)
        stamps = [event.get("ts") for event in errors
                  if isinstance(event.get("ts"), str)]
        first_ts = min(stamps) if stamps else self._last_ts(unit)
        last_ts = max(stamps) if stamps else first_ts
        endpoint = endpoint_key(errors[-1].get("endpoint"))
        endpoint_payload = None
        if endpoint:
            host, _, port = endpoint.rpartition(":")
            if host and port.isdigit():
                endpoint_payload = {"host": host, "port": int(port)}
        evidence: List[Dict[str, Any]] = [
            {"kind": "signal_count", "signal": "tls_cert_error", "value": count},
            {"kind": "absence", "metric": "tls_handshake_ok_s",
             "value": t.tls_ok_window_s},
        ]
        confidence = "low" if clock_fallback else self._boost("medium")
        profile = self._profile(unit)
        checks = [
            f"no tls_handshake_ok in {t.tls_ok_window_s}s",
            "no active probe marker",
        ]
        if clock_fallback:
            checks.append("clock_fallback on the event: system clock suspect")
        else:
            checks.append("no clock fallback on the event")

        def build() -> Dict[str, Any]:
            return build_finding(
                rule="G-08", unit=unit, profile=profile,
                endpoint=endpoint_payload,
                window_first=first_ts, window_last=last_ts, count=count,
                evidence=evidence, fp_checks=checks,
                severity="high", confidence=confidence,
                category="config", stage_hint="tls_stall",
                hypothesis="сертификат не проверяется — конфиг/время, а не "
                           "цензура",
                action="проверить SNI/сертификат и системное время; для "
                       "Reality — dest-сертификат; перегенерировать профиль",
                alternatives=["сертификат назначения в Reality",
                              "сбитые системные часы",
                              "MITM/подмена сертификата"],
                server=self._server_block(), cross_vantage=False)

        return _Verdict(True, finding=build, dedup_endpoint=endpoint)

    # -- G-09 endpoint_silent -------------------------------------------------

    def _g09(self, unit: Optional[str], now_us: int) -> Optional[_Verdict]:
        if not isinstance(unit, str):
            return None
        if not is_vpn_transport_unit(unit):
            # DNS/zapret/system units also reset their upstream sockets, but
            # their silence is local context, never a silent VPN endpoint
            # (G-09 is transport-only; §8.2).
            return _Verdict(False)
        window = self.windows.peek(unit)
        if window is None:
            return None
        t = self.thresholds
        horizon = t.endpoint_silent_window_s
        candidates = [event for event in window.since(now_us, horizon)
                      if event.get("signal") in SILENT_SIGNALS]
        if not candidates:
            return _Verdict(False)
        groups: Dict[Optional[str], List[Dict[str, Any]]] = {}
        for event in candidates:
            key = endpoint_key(event.get("endpoint"))
            if key is None:
                continue  # no attribution → not an endpoint-silent finding
            groups.setdefault(key, []).append(event)
        if not groups:
            return _Verdict(False)
        endpoint, group = max(
            groups.items(),
            key=lambda item: (sum_counts(item[1]), str(item[0])))
        count = sum_counts(group)
        if count < t.endpoint_silent_count:
            return _Verdict(False)
        handshakes = [event for event in window.since(now_us, horizon)
                      if event.get("signal") in HANDBOOK_SIGNALS
                      and endpoint_key(event.get("endpoint")) == endpoint]
        if handshakes:
            # A handshake on the same endpoint: G-02/G-03 own the pattern, an
            # endpoint is not silent.
            return _Verdict(False)
        if self._planned_stop(unit):
            return _Verdict(False, status="suppressed", reason="planned_stop")
        if self._activity(now_us):
            return _Verdict(False, status="suppressed", reason="active_probe")
        if self._fp(unit, now_us, ("tls_cert_error",), t.tls_ok_window_s):
            # G-08 owns the certificate failure: G-09 is not escalated (§8.4).
            return _Verdict(False, status="suppressed",
                            reason="tls_cert_error")
        if self._local_net_active(unit, now_us):
            return _Verdict(False, status="suppressed", reason="local_network")
        if self._planned_switch(now_us):
            return _Verdict(False, status="suppressed", reason="network_switch")
        if self._fp(unit, now_us, FLAP_SIGNALS, horizon):
            return _Verdict(False, status="suppressed", reason="unit_flap")
        refused = [event for event in group
                   if event.get("signal") == "conn_refused"]
        stalls = [event for event in group
                  if event.get("signal") == "tls_stall"]
        kinds: Dict[str, int] = {}
        for event in group:
            name = str(event.get("signal"))
            kinds[name] = kinds.get(name, 0) + event_count(event)
        stamps = [event.get("ts") for event in group
                  if isinstance(event.get("ts"), str)]
        first_ts = min(stamps) if stamps else self._last_ts(unit)
        last_ts = max(stamps) if stamps else first_ts
        host, _, port = endpoint.rpartition(":")
        endpoint_payload = ({"host": host, "port": int(port)}
                            if host and port.isdigit() else None)
        profile = self._profile(unit)
        lockdown = bool(self._server is not None
                        and self._server.lockdown_expected)
        alive = self._other_channel_alive(unit, now_us)
        cross = bool(alive and self._cross())
        alternatives = ["сервер не отвечает/лежит",
                        "L3-ACL или молчаливый drop на пути (цензура)"]
        if refused:
            severity = "high"
            confidence = "high"
            stage = "pre_handshake"
            hypothesis = ("эндпоинт отвечает refused: IP достижим, порт не "
                          "слушает — сервис/порт, а не цензура")
            action = ("проверить unit и порт на сервере; профиль не менять; "
                      "vpn probe")
            alternatives = ["сервер не запущен или упал",
                            "порт изменён после обновления/перегенерации",
                            "локальный файрвол закрыл порт"]
        elif stalls:
            severity = "medium"
            confidence = "high" if cross else "medium"
            stage = "tls_stall"
            hypothesis = ("TCP/QUIC connect проходит, handshake молчит — "
                          "кандидат на silent ClientHello drop")
            action = ("не рестартить; vpn probe; проверить резервный канал и "
                      "свежие ссылки")
        else:
            severity = "medium"
            confidence = "high" if cross else "medium"
            stage = "pre_handshake"
            hypothesis = ("эндпоинт молчит: ни handshake, ни ответа на "
                          "попытки подключения")
            action = ("не рестартить; vpn probe; свежие ссылки; резервный "
                      "канал")
        if lockdown:
            severity = "info"
            hypothesis = ("ожидаемо: активен серверный lock-443 (lockdown); "
                          "молчание эндпоинта не цензура")
            action = ("снять серверный lock-443 или дождаться TTL; "
                      "проверить vpn probe")
        evidence: List[Dict[str, Any]] = [
            {"kind": "signal_count", "signal": name, "value": value}
            for name, value in sorted(kinds.items())]
        evidence.append({"kind": "absence", "metric": "handshake_ok_s",
                         "value": horizon})
        checks = [
            f"{count} silent attempts on one endpoint in {horizon}s",
            "no handshake_ok on that endpoint",
            f"no tls_cert_error in {t.tls_ok_window_s}s",
            "no network_unreachable/no_route in window",
            "no unit_exit/unit_restart_scheduled in window",
            "no active probe marker",
            "alternative channel alive" if alive
            else "no alternative channel evidence",
            "server verdict fresh" if (self._server is not None
                                       and self._server.fresh)
            else "server verdict absent/stale",
        ]

        def build() -> Dict[str, Any]:
            return build_finding(
                rule="G-09", unit=unit, profile=profile,
                endpoint=endpoint_payload,
                window_first=first_ts, window_last=last_ts, count=count,
                evidence=evidence, fp_checks=checks,
                severity=severity, confidence=confidence,
                category="endpoint_unreachable", stage_hint=stage,
                hypothesis=hypothesis, action=action,
                alternatives=alternatives,
                server=self._server_block(),
                expected_lockdown=lockdown, cross_vantage=cross)

        return _Verdict(True, finding=build, dedup_endpoint=endpoint)

    # -- G-10 unit_flap -------------------------------------------------------

    def _g10(self, unit: Optional[str], now_us: int) -> Optional[_Verdict]:
        if not isinstance(unit, str):
            return None
        window = self.windows.peek(unit)
        if window is None:
            return None
        t = self.thresholds
        events_in = window.since(now_us, t.unit_flap_window_s)
        starts = [event for event in events_in
                  if event.get("signal") == "unit_start"]
        hard_exits = [event for event in events_in
                      if event.get("signal") == "unit_exit"
                      and self._unit_exit_is_fault(event, unit)]
        scheduled = [event for event in events_in
                     if event.get("signal") == "unit_restart_scheduled"]
        hard = hard_exits + scheduled
        if len(hard) < t.unit_flap_count and len(starts) < t.unit_flap_count:
            return _Verdict(False)
        if self._planned_stop(unit):
            return _Verdict(False, status="suppressed", reason="planned_stop")
        if self._causes.get(unit) == "vpn_switch":
            return _Verdict(False, status="suppressed", reason="vpn_switch")
        if self._recent_switch(unit):
            return _Verdict(False, status="suppressed", reason="vpn_switch")
        if self._state_off_any() and not hard_exits:
            return _Verdict(False, status="suppressed", reason="vpn_off")
        if self._activity(now_us):
            return _Verdict(False, status="suppressed", reason="active_probe")
        stamps = [event.get("ts") for event in events_in
                  if isinstance(event.get("ts"), str)]
        first_ts = min(stamps) if stamps else self._last_ts(unit)
        last_ts = max(stamps) if stamps else first_ts
        count = max(len(hard), len(starts))
        evidence: List[Dict[str, Any]] = [
            {"kind": "signal_count", "signal": "unit_start",
             "value": len(starts)},
            {"kind": "signal_count", "signal": "unit_exit",
             "value": len(hard_exits)},
            {"kind": "signal_count", "signal": "unit_restart_scheduled",
             "value": len(scheduled)},
        ]
        profile = self._profile(unit)

        def build() -> Dict[str, Any]:
            return build_finding(
                rule="G-10", unit=unit, profile=profile, endpoint=None,
                window_first=first_ts, window_last=last_ts, count=count,
                evidence=evidence,
                fp_checks=[
                    f"hard exits/starts ≥ {t.unit_flap_count} in "
                    f"{t.unit_flap_window_s}s",
                    "planned stop not observed",
                    "state.active unchanged during the flap window",
                    "no active probe marker",
                    "no planned restart evidence (vpn down/switch)",
                ],
                severity="high", confidence="high",
                category="local_fault", stage_hint="local",
                hypothesis="юнит перезапускается/flapping — локальный сбой "
                           "клиента, а не сеть или цензура",
                action="journalctl --user -u <unit>; проверить конфиг/бинарь; "
                       "не рестартить вслепую",
                alternatives=["OOM/убийство юнита супервизором",
                              "обновление или переустановка бинаря",
                              "битый конфиг профиля"],
                server=self._server_block(), cross_vantage=False)

        return _Verdict(True, finding=build)

    # -- G-11 degraded_throughput (global) ------------------------------------

    def _speed_streak(self, now_us: int) -> Optional[Dict[str, Any]]:
        """Best channel with ≥ ``degraded_reports`` consecutive low reports."""
        t = self.thresholds
        horizon = int(now_us) - t.degraded_window_s * MONO_US_PER_S
        by_channel: Dict[str, List[Dict[str, Any]]] = {}
        for report in self._speed_reports:
            if int(report["mono_us"]) < horizon:
                continue
            by_channel.setdefault(str(report["channel"]), []).append(report)
        best: Optional[Dict[str, Any]] = None
        for channel in sorted(by_channel):
            items = sorted(by_channel[channel], key=lambda item: item["mono_us"])
            streak = 0
            for report in reversed(items):
                if float(report["speed"]) < t.degraded_mbps:
                    streak += 1
                else:
                    break
            if streak < t.degraded_reports:
                continue
            if best is None or streak > best["streak"]:
                best = {
                    "channel": channel,
                    "streak": streak,
                    "reports": items[-streak:],
                }
        return best

    def _g11(self, unit: Optional[str], now_us: int) -> Optional[_Verdict]:
        if unit is not None:
            return None  # global rule: evaluated once per ticker
        t = self.thresholds
        streak = self._speed_streak(now_us)
        if not self._speed_reports:
            return _Verdict(False, status="insufficient_data",
                            reason="no_speed_history")
        if self._activity(now_us):
            return _Verdict(False, status="suppressed", reason="active_probe")
        timeouts = 0
        for name in self.windows.units:
            window = self.windows.peek(name)
            if window is None:
                continue
            timeouts += window.count(now_us, t.degraded_timeout_window_s,
                                     PERF_TIMEOUT_SIGNALS)
        branch_b = (
            streak is None and timeouts >= t.degraded_timeout_count
            and any(float(report["speed"]) < t.degraded_mbps
                    and int(report["mono_us"]) >= int(now_us)
                    - t.degraded_window_s * MONO_US_PER_S
                    for report in self._speed_reports))
        if streak is None and not branch_b:
            return _Verdict(False)
        evidence: List[Dict[str, Any]] = []
        if streak is not None:
            last = streak["reports"][-1]
            first = streak["reports"][0]
            count = streak["streak"]
            profile = streak["channel"]
            first_ts = str(first["ts"])
            last_ts = str(last["ts"])
            evidence = [
                {"kind": "counter", "metric": "speed_mbps",
                 "value": round(float(last["speed"]), 2), "unit": "mbps"},
                {"kind": "counter", "metric": "reports_below_threshold",
                 "value": count},
                {"kind": "counter", "metric": "degraded_mbps",
                 "value": round(float(t.degraded_mbps), 2)},
            ]
            hypothesis = ("скорость канала устойчиво ниже "
                          f"{t.degraded_mbps} Мбит/с в "
                          f"{count} отчётах подряд")
        else:
            count = timeouts
            profile = None
            first_ts = self._latest_ts()
            last_ts = first_ts
            evidence = [
                {"kind": "signal_count", "signal": "read_timeout",
                 "value": timeouts},
                {"kind": "counter", "metric": "degraded_mbps",
                 "value": round(float(t.degraded_mbps), 2)},
                {"kind": "absence", "metric": "speed_history_s",
                 "value": t.degraded_window_s},
            ]
            hypothesis = ("данные идут с обрывами: read/write timeouts без "
                          "падения handshake — деградация канала")

        def build() -> Dict[str, Any]:
            return build_finding(
                rule="G-11", unit=None, profile=profile, endpoint=None,
                window_first=first_ts, window_last=last_ts, count=count,
                evidence=evidence,
                fp_checks=[
                    "finished speed reports only",
                    "CDN profile excluded",
                    "no active probe marker",
                    f"threshold {t.degraded_mbps} Mbps",
                ],
                severity="medium", confidence="medium",
                category="degraded", stage_hint="data_phase",
                hypothesis=hypothesis,
                action="vpn speed по каналам; сменить канал/сервер; проверить "
                       "шейпинг провайдера; VPN тут не рестартить",
                alternatives=["провайдер шейпит bulk-эндпоинт",
                              "сервер/канал перегружен",
                              "замер во время инцидента"],
                server=self._server_block(), cross_vantage=False)

        return _Verdict(True, finding=build)

    # -- G-12 guard_selfdegraded (global) -------------------------------------

    def _g12(self, unit: Optional[str], now_us: int) -> Optional[_Verdict]:
        if unit is not None:
            return None  # global rule: evaluated once per ticker
        t = self.thresholds
        # (metric, detail for fp_checks, numeric evidence value)
        breaches: List[Tuple[str, str, float]] = []
        stamps: List[str] = []
        lost_us = t.self_lost_s * MONO_US_PER_S
        # A lost source is only a guard degradation when the source was
        # expected: a suspend/resume makes monotonic-based age jump (the
        # realtime↔monotonic skew is its own G-12 breach) and a stopped AWG has
        # no sysfs interface by design (WP-G6 field checkpoint).
        suspend_recent = self._suspend_blocked(now_us)
        skew_recent = bool(
            self._skew_us is not None
            and self._clock_skew_s > t.self_skew_s
            and 0 <= int(now_us) - self._skew_us
            <= t.self_drop_window_s * MONO_US_PER_S)
        for name, record in sorted(self._source_status.items()):
            if record.get("status") != "lost":
                continue  # §8.2: only `lost` > 60 s is a G-12 breach
            if suspend_recent or skew_recent:
                continue  # resume/skew artifact, not a lost source
            if name == "sysfs:awg" and not self._awg_expected_active():
                continue  # AWG is off on purpose: no interface is normal
            age_s = (int(now_us) - int(record["since_us"])) / MONO_US_PER_S
            if age_s * MONO_US_PER_S >= lost_us:
                breaches.append(("source_lost", name, round(age_s, 1)))
                ts = record.get("ts")
                if isinstance(ts, str):
                    stamps.append(ts)
        if (self._queue_drop_us is not None
                and 0 <= int(now_us) - self._queue_drop_us
                <= t.self_drop_window_s * MONO_US_PER_S):
            breaches.append(("queue_drops", str(self._queue_drops),
                             float(self._queue_drops)))
        if (self._parse_us is not None
                and self._parse_lines >= t.self_unparsed_min_lines
                and self._parse_ratio > t.self_unparsed_ratio
                and 0 <= int(now_us) - self._parse_us
                <= t.self_drop_window_s * MONO_US_PER_S):
            breaches.append(("unparsed_ratio", f"{self._parse_ratio:.2f}",
                             round(self._parse_ratio, 3)))
        if (self._skew_us is not None
                and self._clock_skew_s > t.self_skew_s
                and 0 <= int(now_us) - self._skew_us
                <= t.self_drop_window_s * MONO_US_PER_S):
            breaches.append(("clock_skew", f"{self._clock_skew_s:.1f}",
                             round(self._clock_skew_s, 2)))
        if not breaches:
            return _Verdict(False)
        first_ts = min(stamps) if stamps else (self._self_ts or
                                               self._latest_ts())
        last_ts = max(stamps) if stamps else first_ts
        evidence: List[Dict[str, Any]] = [
            {"kind": "counter", "metric": kind, "value": value}
            for kind, _detail, value in breaches[:8]]
        checks = [f"{kind}: {detail}"
                  for kind, detail, _value in breaches[:8]]

        def build() -> Dict[str, Any]:
            return build_finding(
                rule="G-12", unit=None, profile=None, endpoint=None,
                window_first=first_ts, window_last=last_ts,
                count=len(breaches), evidence=evidence,
                fp_checks=checks,
                severity="low", confidence="low",
                category="observability", stage_hint="local",
                hypothesis="деградация самого guard: источник/парсер/часы — "
                           "наблюдаемость ниже, это не отказ VPN",
                action="проверить права на журнал, очереди и системные часы; "
                       "guard v1 ничего не рестартит",
                alternatives=["журнал/права изменились",
                              "высокая нагрузка или скачок часов",
                              "версия клиента не совпала с каталогом сигналов"],
                server=self._server_block(), cross_vantage=False)

        return _Verdict(True, finding=build)


__all__ = [
    "AWG_PREFIXES",
    "SINGLE_EVIDENCE_RULES",
    "COUNTER_SIGNALS",
    "DIAL_SIGNALS",
    "DNS_SIGNALS",
    "DROP_SIGNALS",
    "FLOAT_THRESHOLDS",
    "GLOBAL_RULES",
    "HANDBOOK_SIGNALS",
    "INT_THRESHOLDS",
    "LOCAL_NET_SIGNALS",
    "RESET_SIGNALS",
    "RULES_IN_ORDER",
    "RULE_CATALOG",
    "RULE_TRIGGERS",
    "SILENT_SIGNALS",
    "SPEED_RING_PERSISTED",
    "RuleEngine",
    "RuleOutcome",
    "ServerContext",
    "Thresholds",
    "build_finding",
    "finding_schema_hash",
    "rule_catalog",
    "rules_hash",
]

#: Public alias of the trigger map (tests assert the catalogue is complete).
RULE_TRIGGERS: Mapping[str, frozenset] = _RULE_TRIGGERS
