"""Time windows, reorder buffer, coalescing, cooldown and hysteresis (WP-G3).

Streaming contract (SPEC-20261116-vpn-guard §5.3, §7.1, §8.1):

- **reorder buffer** — 100 ms: journal (S1/S2) and sysfs (S3) sources arrive
  slightly out of order, so events are released only once the newest
  ``mono_us`` is ``delay_us`` ahead (a watermark), then strictly by
  ``(mono_us, arrival)``;
- **coalescing** — the same ``(unit, signal, endpoint)`` within 100 ms becomes
  one event with ``count > 1`` (§7.1), so a burst cannot flood the rules;
- **event windows** — bounded per-unit rings (≤ 256 events, 1 h age) queried by
  monotonic time, so a wall-clock jump cannot break a window (RSK-9);
- **cooldown** — dedup key ``rule|unit|endpoint|cause``: a repeat inside
  ``cooldown_s`` (300 s) bumps ``count``/``last_ts``; it is **not** a new
  finding and never notifies (§8.1);
- **hysteresis** — fire after ``confirm_windows`` (2) consecutive satisfied
  evaluations, resolve after ``quiet_windows`` (3) quiet ones.

Nothing here reads a wall clock: every window is driven by the caller's
``mono_us`` values, so a corpus replay is bit-for-bit reproducible (AC-9).
Memory is bounded by the ``MAX_*`` constants below and never by the backfill
depth (§6.5): an old unit is garbage-collected after one hour (§12).
"""

from __future__ import annotations

import heapq
import time
from collections import OrderedDict, deque
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Tuple

MONO_NS_PER_US = 1_000
MONO_US_PER_S = 1_000_000

#: SPEC §5.3: sources are glued together inside a 100 ms reorder buffer.
REORDER_DELAY_US = 100_000
#: SPEC §7.1: one ``(unit, signal, endpoint)`` within 100 ms collapses.
COALESCE_WINDOW_US = 100_000
#: Bound of the reorder heap (drop-oldest, never raise; §7.1 limit family).
MAX_REORDER_EVENTS = 4096
MAX_COALESCE_KEYS = 512
#: SPEC §7.1: at most 256 events per rule window.
MAX_WINDOW_EVENTS = 256
MAX_WINDOW_AGE_US = 3600 * MONO_US_PER_S
MAX_UNITS = 64
#: SPEC §12: a removed unit's state is collected after one hour.
UNIT_TTL_US = 3600 * MONO_US_PER_S
MAX_TRACKED_KEYS = 512

#: Hysteresis verdicts (``Hysteresis.observe``).
IDLE = "idle"
PENDING = "pending"
FIRE = "fire"
ACTIVE = "active"
RESOLVED = "resolved"


def mono_us() -> int:
    """Now in µs — same domain as journald ``__MONOTONIC_TIMESTAMP``."""
    return time.monotonic_ns() // MONO_NS_PER_US


def mono_of(event: Mapping[str, Any]) -> int:
    """``mono_us`` of an event; ``0`` when the field is missing or broken."""
    value = event.get("mono_us")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def event_count(event: Mapping[str, Any]) -> int:
    """Coalesced ``count`` of an event (≥ 1, tolerant to garbage)."""
    value = event.get("count", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 1
    return value


def sum_counts(events: Iterable[Mapping[str, Any]]) -> int:
    """Sum of the coalesced counts (a folded burst counts as its size)."""
    return sum(event_count(event) for event in events)


def endpoint_key(endpoint: Optional[Mapping[str, Any]]) -> Optional[str]:
    """``host:port`` key of an endpoint; ``None`` when absent/incomplete."""
    if not isinstance(endpoint, Mapping):
        return None
    host = endpoint.get("host")
    port = endpoint.get("port")
    if isinstance(host, str) and host and isinstance(port, int):
        return f"{host}:{port}"
    return None


class Clock:
    """Injectable monotonic clock: pinned in tests, real in the daemon.

    ``clock.advance(seconds)`` moves the time forward without sleeping, which
    is what makes absence-rule tests fast and deterministic.
    """

    def __init__(self, start_us: int = 0) -> None:
        self._now = int(start_us)

    def now_us(self) -> int:
        return self._now

    def set(self, value_us: int) -> int:
        self._now = int(value_us)
        return self._now

    def advance(self, seconds: float) -> int:
        self._now += int(seconds * MONO_US_PER_S)
        return self._now


class ReorderBuffer:
    """0.1 s watermark buffer ordering events by ``(mono_us, arrival)``.

    ``push`` returns the events that are now safe to analyse; the newest input
    is held until either ``delay_us`` of monotonic time has passed (the next
    push advances the watermark) or :meth:`flush` is called (5 s ticker,
    shutdown).  A full buffer drops the *oldest* pending event and counts it —
    a burst must never grow memory or raise (§12 burst budget).
    """

    def __init__(self, delay_us: int = REORDER_DELAY_US,
                 max_items: int = MAX_REORDER_EVENTS) -> None:
        self.delay_us = max(0, int(delay_us))
        self.max_items = max(1, int(max_items))
        self.dropped = 0
        self._heap: List[Tuple[int, int, Dict[str, Any]]] = []
        self._seq = 0
        self._watermark = 0

    @property
    def pending(self) -> int:
        return len(self._heap)

    def push(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Buffer one event; return the released ones in time order."""
        stamp = mono_of(event)
        self._seq += 1
        heapq.heappush(self._heap, (stamp, self._seq, event))
        if stamp - self.delay_us > self._watermark:
            self._watermark = stamp - self.delay_us
        released = self._release(self._watermark)
        while len(self._heap) > self.max_items:
            heapq.heappop(self._heap)
            self.dropped += 1
        return released

    def flush(self) -> List[Dict[str, Any]]:
        """Release everything still buffered (ticker/shutdown)."""
        return self._release(self._watermark, all_items=True)

    def _release(self, limit_us: int,
                 all_items: bool = False) -> List[Dict[str, Any]]:
        released: List[Dict[str, Any]] = []
        while self._heap and (all_items or self._heap[0][0] <= limit_us):
            released.append(heapq.heappop(self._heap)[2])
        return released


class Coalescer:
    """Fold identical events inside 100 ms into one with ``count > 1`` (§7.1).

    ``fold(event)`` returns ``True`` when *event* was merged into a previously
    emitted event (the caller then drops it): the previous dict is mutated in
    place (``count += event.count``), so the rule windows, which hold the same
    object, see the burst size.  Different endpoints never fold — a reset storm
    must stay attributable to one ``host:port``.
    """

    def __init__(self, window_us: int = COALESCE_WINDOW_US,
                 max_keys: int = MAX_COALESCE_KEYS) -> None:
        self.window_us = max(0, int(window_us))
        self.max_keys = max(1, int(max_keys))
        self._last: "OrderedDict[Tuple[Any, Any, Any], Tuple[int, Dict[str, Any]]]" = (
            OrderedDict())

    def key(self, event: Mapping[str, Any]) -> Tuple[Any, Any, Any]:
        return (event.get("unit"), event.get("signal"),
                endpoint_key(event.get("endpoint")))

    def fold(self, event: Dict[str, Any]) -> bool:
        key = self.key(event)
        stamp = mono_of(event)
        record = self._last.get(key)
        if record is not None and 0 <= stamp - record[0] <= self.window_us:
            previous = record[1]
            previous["count"] = event_count(previous) + event_count(event)
            self._last[key] = (stamp, previous)
            self._last.move_to_end(key)
            return True
        self._last[key] = (stamp, event)
        self._last.move_to_end(key)
        while len(self._last) > self.max_keys:
            self._last.popitem(last=False)
        return False


class EventWindow:
    """Bounded ring of events for one unit, queried by monotonic time."""

    def __init__(self, max_events: int = MAX_WINDOW_EVENTS,
                 max_age_us: int = MAX_WINDOW_AGE_US) -> None:
        self.max_events = max(1, int(max_events))
        self.max_age_us = max(0, int(max_age_us))
        self._events: Deque[Dict[str, Any]] = deque(maxlen=self.max_events)
        self.last_us = 0

    def __len__(self) -> int:
        return len(self._events)

    @property
    def events(self) -> List[Dict[str, Any]]:
        """Snapshot in insertion (time) order."""
        return list(self._events)

    def add(self, event: Dict[str, Any]) -> None:
        stamp = mono_of(event)
        if stamp > self.last_us:
            self.last_us = stamp
        self._events.append(event)
        self.prune(stamp)

    def prune(self, now_us: int) -> None:
        """Drop events older than ``max_age_us`` (never grows with backfill)."""
        horizon = int(now_us) - self.max_age_us
        while self._events and mono_of(self._events[0]) < horizon:
            self._events.popleft()

    def since(self, now_us: int, seconds: float) -> List[Dict[str, Any]]:
        """Events inside the last *seconds* (inclusive), oldest first."""
        horizon = int(now_us) - int(seconds * MONO_US_PER_S)
        return [event for event in self._events if mono_of(event) >= horizon]

    def count(self, now_us: int, seconds: float,
              signals: Iterable[str]) -> int:
        """Coalesced count of *signals* inside the last *seconds*."""
        wanted = set(signals)
        return sum_counts(event for event in self.since(now_us, seconds)
                          if event.get("signal") in wanted)

    def last(self, signal: Optional[str] = None) -> Optional[Dict[str, Any]]:
        for event in reversed(self._events):
            if signal is None or event.get("signal") == signal:
                return event
        return None


class WindowStore:
    """``unit`` → :class:`EventWindow`, with one-hour unit GC (SPEC §12)."""

    def __init__(self, unit_ttl_us: int = UNIT_TTL_US,
                 max_units: int = MAX_UNITS,
                 max_events: int = MAX_WINDOW_EVENTS,
                 max_age_us: int = MAX_WINDOW_AGE_US) -> None:
        self.unit_ttl_us = max(0, int(unit_ttl_us))
        self.max_units = max(1, int(max_units))
        self.max_events = max(1, int(max_events))
        self.max_age_us = max(0, int(max_age_us))
        self._windows: "OrderedDict[str, EventWindow]" = OrderedDict()

    @property
    def units(self) -> Tuple[str, ...]:
        return tuple(self._windows)

    def peek(self, unit: Optional[str]) -> Optional[EventWindow]:
        if not isinstance(unit, str) or not unit:
            return None
        return self._windows.get(unit)

    def get(self, unit: str) -> EventWindow:
        window = self._windows.get(unit)
        if window is None:
            window = EventWindow(max_events=self.max_events,
                                 max_age_us=self.max_age_us)
            self._windows[unit] = window
        else:
            self._windows.move_to_end(unit)
        return window

    def add(self, event: Dict[str, Any]) -> Optional[EventWindow]:
        unit = event.get("unit")
        if not isinstance(unit, str) or not unit:
            return None
        window = self.get(unit)
        window.add(event)
        self._evict(mono_of(event))
        return window

    def gc(self, now_us: int) -> int:
        """Drop units idle for longer than the TTL; returns how many."""
        removed = 0
        horizon = int(now_us) - self.unit_ttl_us
        for unit in list(self._windows):
            window = self._windows[unit]
            if window.last_us and window.last_us < horizon:
                del self._windows[unit]
                removed += 1
        return removed

    def _evict(self, now_us: int) -> None:
        while len(self._windows) > self.max_units:
            self._windows.popitem(last=False)
        self.gc(now_us)


class CooldownRegistry:
    """Dedup of findings by ``rule|unit|endpoint|cause`` (SPEC §8.1).

    ``evaluate`` is called once per fire attempt: the first one is allowed, a
    repeat inside ``cooldown_s`` is suppressed and only bumps the aggregation
    counter (the caller raises ``count``/``last_ts`` of the existing finding).
    """

    def __init__(self, cooldown_s: float = 300,
                 max_keys: int = MAX_TRACKED_KEYS) -> None:
        self.cooldown_us = int(max(0.0, float(cooldown_s)) * MONO_US_PER_S)
        self.max_keys = max(1, int(max_keys))
        self._last: "OrderedDict[str, Tuple[int, int]]" = OrderedDict()

    def evaluate(self, key: str, now_us: int) -> "Emission":
        record = self._last.get(key)
        if record is not None and 0 <= int(now_us) - record[0] < self.cooldown_us:
            repeats = record[1] + 1
            # a repeat extends the quiet period and aggregates into the
            # already-published finding (§8.1)
            self._last[key] = (int(now_us), repeats)
            self._last.move_to_end(key)
            return Emission(allowed=False, repeats=repeats)
        self._last[key] = (int(now_us), 0)
        self._last.move_to_end(key)
        while len(self._last) > self.max_keys:
            self._last.popitem(last=False)
        return Emission(allowed=True)

    def clear(self, key: str) -> None:
        self._last.pop(key, None)


class Emission:
    """Outcome of one cooldown check (``allowed`` or a suppressed repeat)."""

    __slots__ = ("allowed", "repeats")

    def __init__(self, *, allowed: bool, repeats: int = 0) -> None:
        self.allowed = bool(allowed)
        self.repeats = int(repeats)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"Emission(allowed={self.allowed}, repeats={self.repeats})"


class _Streak:
    __slots__ = ("confirm", "quiet", "fired")

    def __init__(self) -> None:
        self.confirm = 0
        self.quiet = 0
        self.fired = False


class Hysteresis:
    """``confirm_windows`` to fire, ``quiet_windows`` to resolve (§8.1).

    Every call is one evaluation; a satisfied evaluation counts towards
    ``confirm_windows``, an unsatisfied one towards ``quiet_windows`` after a
    fire.  The caller evaluates on every relevant event *and* on the 5 s
    ticker, so a count rule reached by the last event of a storm still fires
    at the next tick instead of waiting for another event.
    """

    def __init__(self, confirm_windows: int = 2, quiet_windows: int = 3,
                 max_keys: int = MAX_TRACKED_KEYS) -> None:
        self.confirm_windows = max(1, int(confirm_windows))
        self.quiet_windows = max(1, int(quiet_windows))
        self.max_keys = max(1, int(max_keys))
        self._state: "OrderedDict[str, _Streak]" = OrderedDict()

    def observe(self, key: str, satisfied: bool,
                confirm_windows: Optional[int] = None) -> str:
        """One evaluation; *confirm_windows* overrides the default per rule.

        A rule whose SPEC threshold is itself a repetition count (``3 resets
        per 60 s``) is already confirmed by its own count, so it passes
        ``confirm_windows=1`` — otherwise the effective threshold would
        silently double (3 → 4–5 events) and AC-2/AC-14 would drift.
        """
        peak = (self.confirm_windows if confirm_windows is None
                else max(1, int(confirm_windows)))
        state = self._state.get(key)
        if state is None:
            state = _Streak()
            self._state[key] = state
            self._evict()
        self._state.move_to_end(key)
        if state.fired:
            if satisfied:
                state.quiet = 0
                return ACTIVE
            state.quiet += 1
            if state.quiet >= self.quiet_windows:
                state.fired = False
                state.quiet = 0
                state.confirm = 0
                return RESOLVED
            return ACTIVE
        if not satisfied:
            state.confirm = 0
            return IDLE
        state.confirm += 1
        if state.confirm >= peak:
            state.fired = True
            state.quiet = 0
            state.confirm = 0
            return FIRE
        return PENDING

    def is_fired(self, key: str) -> bool:
        state = self._state.get(key)
        return bool(state and state.fired)

    def reset(self, key: str) -> None:
        self._state.pop(key, None)

    def _evict(self) -> None:
        while len(self._state) > self.max_keys:
            self._state.popitem(last=False)


__all__ = [
    "ACTIVE",
    "Clock",
    "Coalescer",
    "CooldownRegistry",
    "Emission",
    "EventWindow",
    "FIRE",
    "Hysteresis",
    "IDLE",
    "MAX_WINDOW_EVENTS",
    "MONO_US_PER_S",
    "PENDING",
    "RESOLVED",
    "REORDER_DELAY_US",
    "ReorderBuffer",
    "WindowStore",
    "endpoint_key",
    "event_count",
    "mono_of",
    "mono_us",
    "sum_counts",
]
