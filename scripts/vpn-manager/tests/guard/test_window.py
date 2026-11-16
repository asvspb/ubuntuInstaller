"""WP-G3 tests: reorder buffer, coalescing, windows, cooldown, hysteresis.

No root, no network, no daemon, no wall clock: every window is driven by the
injected ``mono_us`` values, so the assertions are deterministic (AC-9).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import window  # noqa: E402

US = window.MONO_US_PER_S
BASE_US = 1_000_000_000


def event(signal: str, seconds: float, *, unit: str = "vpnm-asv2.service",
          endpoint: dict | None = None, count: int = 1) -> dict:
    payload = {
        "schema": "guard.event/1",
        "ts": f"2026-11-16T12:00:{int(seconds) % 60:02d}.000Z",
        "mono_us": BASE_US + int(seconds * US),
        "source": "journal:user",
        "unit": unit,
        "signal": signal,
        "count": count,
    }
    if endpoint is not None:
        payload["endpoint"] = endpoint
    return payload


# --- clock ---------------------------------------------------------------------


def test_clock_is_injected_and_monotonic_in_tests() -> None:
    clock = window.Clock(0)
    assert clock.now_us() == 0
    assert clock.advance(1.5) == int(1.5 * US)
    assert clock.set(10) == 10
    assert window.mono_us() > 0  # real clock untouched by the test clock


# --- reorder buffer ------------------------------------------------------------


def test_reorder_holds_events_for_the_delay() -> None:
    buffer = window.ReorderBuffer(delay_us=100_000)
    assert buffer.push(event("conn_reset", 10.0)) == []
    assert buffer.pending == 1
    # 50 ms later: still inside the 100 ms watermark
    assert buffer.push(event("conn_reset", 10.05)) == []
    assert buffer.pending == 2
    # 200 ms later: everything older than 100 ms behind the newest is released
    released = buffer.push(event("conn_reset", 10.2))
    stamps = [item["mono_us"] for item in released]
    assert stamps == sorted(stamps)
    assert len(released) == 2
    assert buffer.pending == 1


def test_reorder_orders_out_of_order_input() -> None:
    buffer = window.ReorderBuffer(delay_us=100_000)
    buffer.push(event("a", 5.10))
    buffer.push(event("b", 5.02))   # late arrival, earlier timestamp
    buffer.push(event("c", 5.05))
    released = [item["signal"] for item in buffer.push(event("d", 5.30))]
    assert released == ["b", "c", "a"]


def test_reorder_flush_releases_everything() -> None:
    buffer = window.ReorderBuffer(delay_us=100_000)
    buffer.push(event("a", 1.0))
    buffer.push(event("b", 1.01))
    assert [item["signal"] for item in buffer.flush()] == ["a", "b"]
    assert buffer.pending == 0


def test_reorder_is_bounded_and_counts_drops() -> None:
    buffer = window.ReorderBuffer(delay_us=100_000, max_items=4)
    for index in range(10):
        buffer.push(event("conn_reset", 1.0 + index / 1000))
    assert buffer.pending <= 4
    assert buffer.dropped >= 6
    assert len(buffer.flush()) <= 4


# --- coalescing ----------------------------------------------------------------


def test_same_signal_within_100ms_is_folded() -> None:
    coalescer = window.Coalescer()
    endpoint = {"host": "203.0.113.10", "port": 443}
    first = event("conn_reset", 10.0, endpoint=endpoint)
    assert coalescer.fold(first) is False
    second = event("conn_reset", 10.05, endpoint=endpoint)
    assert coalescer.fold(second) is True
    assert first["count"] == 2


def test_coalescing_respects_window_and_endpoint() -> None:
    coalescer = window.Coalescer()
    endpoint = {"host": "203.0.113.10", "port": 443}
    other = {"host": "203.0.113.11", "port": 443}
    first = event("conn_reset", 10.0, endpoint=endpoint)
    coalescer.fold(first)
    late = event("conn_reset", 10.5, endpoint=endpoint)      # > 100 ms
    assert coalescer.fold(late) is False
    elsewhere = event("conn_reset", 10.55, endpoint=other)   # другой endpoint
    assert coalescer.fold(elsewhere) is False
    assert first["count"] == 1


def test_coalescer_is_bounded() -> None:
    coalescer = window.Coalescer(max_keys=2)
    for index in range(5):
        coalescer.fold(event("conn_reset", 1.0 + index,
                             unit=f"vpnm-p{index}.service"))
    assert len(coalescer._last) <= 2


# --- event windows -------------------------------------------------------------


def test_event_window_counts_and_prunes_by_age() -> None:
    samples = window.EventWindow(max_events=8, max_age_us=10 * US)
    for index in range(5):
        samples.add(event("conn_reset", float(index)))
    assert samples.count(BASE_US + 5 * US, 10, ("conn_reset",)) == 5
    assert samples.count(BASE_US + 5 * US, 2, ("conn_reset",)) == 2  # t=3,4
    assert samples.count(BASE_US + 5 * US, 1, ("conn_reset",)) == 1  # t=4
    samples.add(event("conn_reset", 40.0))             # prune everything older
    assert len(samples) == 1
    assert samples.last()["mono_us"] == BASE_US + 40 * US


def test_event_window_sums_coalesced_counts() -> None:
    samples = window.EventWindow()
    samples.add(event("conn_reset", 1.0, count=3))
    samples.add(event("handshake_ok", 1.0))
    assert samples.count(BASE_US + 5 * US, 60, ("conn_reset",)) == 3
    assert samples.count(BASE_US + 5 * US, 60,
                         ("conn_reset", "handshake_ok")) == 4


def test_event_window_is_bounded() -> None:
    samples = window.EventWindow(max_events=4)
    for index in range(10):
        samples.add(event("conn_reset", float(index)))
    assert len(samples) == 4
    assert samples.events[0]["mono_us"] > BASE_US  # oldest ones were dropped


def test_window_store_keys_by_unit_and_gc() -> None:
    store = window.WindowStore(unit_ttl_us=60 * US, max_units=2)
    store.add(event("conn_reset", 0.0, unit="vpnm-a.service"))
    store.add(event("conn_reset", 1.0, unit="vpnm-b.service"))
    assert store.units == ("vpnm-a.service", "vpnm-b.service")
    assert store.peek("vpnm-a.service") is not None
    assert store.peek(None) is None
    # an idle unit is collected after the TTL
    assert store.gc(BASE_US + 120 * US) == 2
    assert store.units == ()


def test_window_store_evicts_beyond_max_units() -> None:
    store = window.WindowStore(max_units=2, unit_ttl_us=10_000 * US)
    for index in range(4):
        store.add(event("conn_reset", float(index),
                        unit=f"vpnm-{index}.service"))
    assert len(store.units) == 2
    assert "vpnm-0.service" not in store.units


# --- cooldown ------------------------------------------------------------------


def test_cooldown_allows_then_suppresses_repeats() -> None:
    registry = window.CooldownRegistry(cooldown_s=300)
    key = "G-02|vpnm-a.service|203.0.113.10:443|reset_storm"
    first = registry.evaluate(key, BASE_US)
    assert first.allowed is True and first.repeats == 0
    repeat = registry.evaluate(key, BASE_US + 60 * US)
    assert repeat.allowed is False and repeat.repeats == 1
    again = registry.evaluate(key, BASE_US + 120 * US)
    assert again.allowed is False and again.repeats == 2
    # the repeat slid the window: 200 s after the last repeat is still inside
    assert registry.evaluate(key, BASE_US + 400 * US).allowed is False
    assert registry.evaluate(key, BASE_US + 800 * US).allowed is True


def test_cooldown_keys_are_independent_and_bounded() -> None:
    registry = window.CooldownRegistry(cooldown_s=300, max_keys=2)
    assert registry.evaluate("a", 0).allowed is True
    assert registry.evaluate("b", 0).allowed is True
    assert registry.evaluate("a", 10).allowed is False
    registry.clear("a")
    assert registry.evaluate("a", 20).allowed is True
    for key in ("c", "d", "e"):
        registry.evaluate(key, 30)
    assert len(registry._last) <= 2


# --- hysteresis ----------------------------------------------------------------


def test_hysteresis_needs_confirm_windows() -> None:
    gauge = window.Hysteresis(confirm_windows=2, quiet_windows=3)
    assert gauge.observe("k", False) == window.IDLE
    assert gauge.observe("k", True) == window.PENDING
    assert gauge.observe("k", True) == window.FIRE
    assert gauge.observe("k", True) == window.ACTIVE
    assert gauge.is_fired("k") is True


def test_hysteresis_resolves_after_quiet_windows() -> None:
    gauge = window.Hysteresis(confirm_windows=1, quiet_windows=3)
    assert gauge.observe("k", True) == window.FIRE
    assert gauge.observe("k", False) == window.ACTIVE
    assert gauge.observe("k", False) == window.ACTIVE
    assert gauge.observe("k", False) == window.RESOLVED
    assert gauge.is_fired("k") is False
    assert gauge.observe("k", True) == window.FIRE   # refires after resolve


def test_hysteresis_confirm_override_and_reset() -> None:
    gauge = window.Hysteresis(confirm_windows=2)
    assert gauge.observe("k", True, confirm_windows=1) == window.FIRE
    gauge.reset("k")
    assert gauge.is_fired("k") is False
    assert gauge.observe("k", True) == window.PENDING


def test_hysteresis_false_evaluation_resets_the_streak() -> None:
    gauge = window.Hysteresis(confirm_windows=2)
    assert gauge.observe("k", True) == window.PENDING
    assert gauge.observe("k", False) == window.IDLE
    assert gauge.observe("k", True) == window.PENDING


def test_hysteresis_is_bounded() -> None:
    gauge = window.Hysteresis(max_keys=3)
    for index in range(10):
        gauge.observe(f"k{index}", True)
    assert len(gauge._state) <= 3


def test_helpers_tolerate_garbage() -> None:
    assert window.mono_of({}) == 0
    assert window.mono_of({"mono_us": "x"}) == 0
    assert window.mono_of({"mono_us": True}) == 0
    assert window.event_count({}) == 1
    assert window.event_count({"count": 0}) == 1
    assert window.endpoint_key(None) is None
    assert window.endpoint_key({"host": "203.0.113.10"}) is None
    assert window.endpoint_key({"host": "203.0.113.10", "port": 443}) == \
        "203.0.113.10:443"
