"""Rules G-01…G-08 of the client guard (WP-G3, SPEC §8.2/§8.3).

The engine is a pure function of the event stream: it reads no wall clock, no
files and no network, so a corpus replay yields a bit-for-bit identical list
of findings (AC-9).  Callers feed already normalized ``guard.event/1`` events
(``parse`` → ``feed``), the 5 s ticker (``tick``), and the derived contexts the
daemon owns:

- ``note_state(active)`` — ``state.json.active`` (command markers, FP-guards);
- ``note_activity(active, now_us)`` — ``activity.json`` probe marker (§8.4);
- ``note_route_change``/``note_suspend``/``note_roaming`` — netlink/suspend
  stop factors (S4 is opt-in v1.1, the hooks are here for G-04/G-07);
- ``note_awg_window`` — one closed sysfs counter window (G-04 AWG branch).

What the rules deliberately **do not** do:

- no signals outside :mod:`vpnm.guard.events` are invented — a rule that has
  no observable evidence returns ``insufficient_data`` instead of a finding;
- G-01 fires only when coverage is proven (``app_banner_ok`` ≤ 15 s or
  ``dial_start``/``dial_timeout``/``conn_refused`` ≤ 25 s after the *same*
  ``unit_start``); silence alone is ``insufficient_data:no_app_banner``;
- the AWG branch of G-04 lives on counters only: ``awg`` has no app logs by
  design, so ``app_signal=blind`` does not block it (§4.3);
- server-side correlation (``verdict.py``, incidents, G-09…G-12) is WP-G4.
  This module only *consumes* an already validated, already freshness-checked
  :class:`ServerContext` — never the file itself.
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
                     endpoint_key, mono_of, mono_us, sum_counts)

VPNM_PREFIX = "vpnm-"
#: AmneziaWG template units: no app logs at all, own observability (§4.3).
AWG_PREFIXES: Tuple[str, ...] = ("vpn-awg-split@", "awg-quick@")

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
#: Route-change kinds that mean "the default route is gone" (G-07).
ROUTE_GONE_KINDS: frozenset = frozenset(
    ("del_default", "delete_default", "route_del", "default_route_removed"))

RULES_IN_ORDER: Tuple[str, ...] = ("G-01", "G-02", "G-03", "G-04",
                                   "G-05", "G-06", "G-07", "G-08")

#: Rules whose SPEC condition is a single decisive observation (a count of
#: resets/cycles/idle timeouts/auth fails/DNS errors, one unreachable route):
#: their own evidence **is** the confirmation, so hysteresis uses
#: ``confirm_windows=1``.  Otherwise the SPEC threshold would silently double
#: (3 resets → 4–5) and AC-2/AC-14 would drift.  Absence-style rules
#: (G-01, G-08) still need the configured ``confirm_windows`` evaluations.
SINGLE_EVIDENCE_RULES: frozenset = frozenset(
    ("G-02", "G-03", "G-04", "G-05", "G-06", "G-07"))

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
)
FLOAT_THRESHOLDS: Tuple[str, ...] = (
    "post_handshake_drop_gap_s", "awg_rx_ratio")


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

    def note_awg_gone(self, iface: Optional[str] = None,
                      now_us: Optional[int] = None) -> List[RuleOutcome]:
        """``awg_iface_gone``: disable the G-04 branch (G-10/G-07 own it)."""
        names = [iface] if isinstance(iface, str) and iface else list(
            self._awg_windows)
        for name in names:
            self._awg_gone[name] = True
            self._awg_windows.pop(name, None)
        return []

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
        outcomes: List[RuleOutcome] = []
        for item in survivors:
            outcomes.extend(self.feed(item))
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
            outcome = self._apply(rule, unit, verdict,
                                  self._key(rule, unit, endpoint,
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
                verdict = self._evaluate(rule, unit, stamp)
                if verdict is None:
                    continue
                endpoint = self._last_endpoint.get((rule, unit))
                outcome = self._apply(rule, unit, verdict,
                                      self._key(rule, unit, endpoint,
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
        }.get(rule, "unknown")

    # -- shared context helpers ---------------------------------------------

    def _now_us(self) -> int:
        if self._clock is not None:
            return self._clock.now_us()
        return mono_us()

    def _planned_stop(self, unit: str) -> bool:
        return self._causes.get(unit) == "vpn_down"

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
        cross = self._cross()

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
                severity="high", confidence=confidence,
                category="censorship_suspect", stage_hint="post_handshake",
                hypothesis="серия reset/EOF после успешного handshake — "
                           "цензура на пути или серверный сбой",
                action="не рестартить; vpn probe; свежие ссылки; резервный "
                       "канал",
                alternatives=["свои пробы или параллельные клиенты",
                              "рестарт сервера",
                              "локальная сеть/маршрут"],
                server=self._server_block(), cross_vantage=cross)

        return _Verdict(True, finding=build)

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

        return _Verdict(True, finding=build)

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
        if self._fp(unit, now_us, LOCAL_NET_SIGNALS, t.dns_fail_window_s):
            # no route/unreachable: G-07 owns the cause, DNS is a symptom
            return _Verdict(False, status="suppressed", reason="local_network")
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
        profile = self._profile(unit)

        def build() -> Dict[str, Any]:
            return build_finding(
                rule="G-06", unit=unit, profile=profile, endpoint=None,
                window_first=first_ts, window_last=last_ts, count=max(1, count),
                evidence=evidence,
                fp_checks=[
                    "no network_unreachable/no_route in 60s",
                    "resolver errors within 60s",
                    f"{len(names)} distinct names affected",
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

        return _Verdict(True, finding=build)


__all__ = [
    "AWG_PREFIXES",
    "SINGLE_EVIDENCE_RULES",
    "COUNTER_SIGNALS",
    "DIAL_SIGNALS",
    "DNS_SIGNALS",
    "DROP_SIGNALS",
    "FLOAT_THRESHOLDS",
    "HANDBOOK_SIGNALS",
    "INT_THRESHOLDS",
    "LOCAL_NET_SIGNALS",
    "RESET_SIGNALS",
    "RULES_IN_ORDER",
    "RULE_TRIGGERS",
    "RuleEngine",
    "RuleOutcome",
    "ServerContext",
    "Thresholds",
    "build_finding",
    "finding_schema_hash",
]

#: Public alias of the trigger map (tests assert the catalogue is complete).
RULE_TRIGGERS: Mapping[str, frozenset] = _RULE_TRIGGERS
