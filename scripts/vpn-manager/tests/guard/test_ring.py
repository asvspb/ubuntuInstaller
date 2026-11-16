"""WP-G5 tests: G-11 speed ring persistence (DECISIONS D-09).

The ring (``ts``, ``profile``, ``proto``, ``median_mbps``) lives in
``guard/state.json`` so a daemon restart / ``kill -9`` no longer resets the
24 h speed history.  No root, no network, no real XDG state.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import rules, store  # noqa: E402
from vpnm.guard.runner import GuardRunner  # noqa: E402

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)
BASE = 1_000_000_000
UNIT_SEC = 1_000_000


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPN_MANAGER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VPN_MANAGER_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setenv("VPN_MANAGER_CONFIG_DIR", str(tmp_path / "config"))


def speed_report(finished: str, speed: float, name: str = "asv2",
                 protocol: str = "xray") -> dict:
    return {"finished": finished,
            "channels": [{"name": name, "protocol": protocol,
                          "speed_mbps": speed}]}


def runner(**kwargs) -> GuardRunner:
    defaults = dict(
        engine=kwargs.pop("engine", rules.RuleEngine()),
        readers=[], sink=store.FindingsStore(
            path=store.guard_dir() / "findings.jsonl"),
        restore_speed_ring=kwargs.pop("restore_speed_ring", True),
        now_us_fn=lambda: BASE,
        wall_clock=lambda: NOW,
    )
    defaults.update(kwargs)
    return GuardRunner(**defaults)


def test_engine_ring_has_the_persisted_fields() -> None:
    engine = rules.RuleEngine()
    engine.note_speed_report(speed_report("2026-11-16T11:50:00Z", 1.5),
                             now_us=BASE)
    engine.note_speed_report(speed_report("2026-11-16T11:55:00Z", 1.2),
                             now_us=BASE + 300 * UNIT_SEC)
    ring = engine.speed_ring()
    assert len(ring) == 2
    assert set(ring[0]) == {"ts", "profile", "proto", "median_mbps"}
    assert ring[0]["ts"] == "2026-11-16T11:50:00Z"
    assert ring[0]["profile"] == "asv2"
    assert ring[0]["proto"] == "xray"
    assert ring[1]["median_mbps"] == 1.2


def test_ring_is_capped_at_five_records() -> None:
    engine = rules.RuleEngine()
    for index in range(8):
        engine.note_speed_report(
            speed_report(f"2026-11-16T11:5{index}:00Z", 1.0),
            now_us=BASE + index * UNIT_SEC)
    ring = engine.speed_ring()
    assert len(ring) == rules.SPEED_RING_PERSISTED == 5
    assert ring[-1]["ts"] == "2026-11-16T11:57:00Z"


def test_persist_writes_the_ring_without_dropping_existing_fields() -> None:
    guard = runner()
    guard.engine.note_speed_report(
        speed_report("2026-11-16T11:50:00Z", 1.5), now_us=BASE)
    guard.engine.note_speed_report(
        speed_report("2026-11-16T11:55:00Z", 1.2), now_us=BASE + 300 * UNIT_SEC)
    guard.counters.findings = 7
    guard.persist()
    state = store.read_state()
    assert state is not None
    assert state["schema"] == "guard.status/1"
    assert state["counters"]["findings"] == 7
    assert "redaction_hits" in state
    assert len(state["speed_ring"]) == 2
    assert state["speed_ring"][1]["median_mbps"] == 1.2
    # the ring survives the JSON round-trip as plain data
    json.dumps(state)


def test_restart_restores_the_ring_and_g11_fires() -> None:
    first = runner()
    first.engine.note_speed_report(
        speed_report("2026-11-16T11:45:00Z", 1.5), now_us=BASE)
    first.engine.note_speed_report(
        speed_report("2026-11-16T11:55:00Z", 1.2), now_us=BASE + 600 * UNIT_SEC)
    first.persist()

    # "restart": a fresh engine/runner in the same guard directory
    emitted = []
    second = runner(engine=rules.RuleEngine(),
                    on_finding=lambda finding: emitted.append(finding))
    second.tick()
    assert second.notes and any(note.startswith("speed_ring_restored:")
                                for note in second.notes)
    assert any(item.get("rule") == "G-11" for item in emitted), emitted
    g11 = next(item for item in emitted if item.get("rule") == "G-11")
    assert g11["category"] == "degraded"
    assert g11["count"] == 2


def test_restored_records_do_not_duplicate_on_re_read() -> None:
    engine = rules.RuleEngine()
    payload = speed_report("2026-11-16T11:55:00Z", 1.2)
    engine.note_speed_report(payload, now_us=BASE)
    stored = engine.speed_ring()
    again = rules.RuleEngine()
    assert again.restore_speed_ring(stored, now_us=BASE,
                                    now_wall=NOW) == 1
    again.note_speed_report(payload, now_us=BASE)
    assert len(again.speed_ring()) == 1


def test_corrupt_ring_records_are_skipped() -> None:
    store.write_state({
        "schema": "guard.status/1", "running": True, "since": "x",
        "counters": {"events": 0, "unparsed": 0, "dropped": 0,
                     "findings": 0},
        "speed_ring": [
            {"ts": "not-a-date", "profile": "asv2", "proto": "xray",
             "median_mbps": 1.0},
            {"ts": "2026-11-16T11:55:00Z", "profile": "", "proto": "xray",
             "median_mbps": 1.0},
            {"ts": "2026-11-16T11:55:00Z", "profile": "asv2",
             "proto": "xray", "median_mbps": True},
            {"ts": "2026-11-16T11:55:00Z", "profile": "asv2",
             "proto": "xray", "median_mbps": 1.0},
        ],
    })
    engine = rules.RuleEngine()
    restored = engine.restore_speed_ring(store.read_speed_ring(),
                                         now_us=BASE, now_wall=NOW)
    assert restored == 1
    assert engine.speed_ring() == [{"ts": "2026-11-16T11:55:00Z",
                                    "profile": "asv2", "proto": "xray",
                                    "median_mbps": 1.0}]


def test_restore_synthesises_monotonic_time_from_wall_clock() -> None:
    engine = rules.RuleEngine()
    records = [{"ts": "2026-11-16T11:55:00Z", "profile": "asv2",
                "proto": "xray", "median_mbps": 1.0}]
    assert engine.restore_speed_ring(records, now_us=BASE,
                                     now_wall=NOW) == 1
    entry = list(engine._speed_reports)[0]
    # 5 minutes before "now" on the monotonic timeline (§7.1/D-06)
    assert entry["mono_us"] == BASE - 300 * UNIT_SEC


def test_new_speed_report_is_persisted_immediately() -> None:
    from types import SimpleNamespace

    guard = runner()
    change = SimpleNamespace(path=Path("last-speed.json"), source="file",
                             note=None,
                             payload=speed_report("2026-11-16T11:55:00Z", 1.2))
    guard._note_change(change)
    state = store.read_state()
    assert state is not None
    assert state["speed_ring"] == [{"ts": "2026-11-16T11:55:00Z",
                                    "profile": "asv2", "proto": "xray",
                                    "median_mbps": 1.2}]


def test_state_without_ring_keeps_working() -> None:
    engine = rules.RuleEngine()
    assert engine.restore_speed_ring([], now_us=BASE) == 0
    empty = runner(engine=engine)
    empty.tick()
    assert store.read_speed_ring() == []
