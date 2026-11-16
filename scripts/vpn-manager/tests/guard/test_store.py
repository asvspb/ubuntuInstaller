"""WP-G1 store tests: coverage/status payloads, activity markers, findings.

No daemon, no root, no network: only temp directories.  The activity marker
must be a *no-op* when the guard directory does not exist — the probing
commands (``monitor``/``probe``/``speed``) never gain a new failure mode or
output change (SPEC §8.4).
"""

from __future__ import annotations

import json
import stat
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import events, schema, store  # noqa: E402


# --- activity markers (FP-guard) ---------------------------------------------

def test_marker_without_guard_dir_is_noop(tmp_path: Path) -> None:
    target = tmp_path / "absent" / "activity.json"
    assert store.mark_activity("probe", path=target) is False
    assert not target.exists()
    assert not target.parent.exists()


def test_marker_roundtrip_and_close(tmp_path: Path) -> None:
    path = tmp_path / "activity.json"
    assert store.mark_activity("speed", path=path) is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["cmd"] == "speed"
    assert set(payload) == {"cmd", "started", "until"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.activity_active(path=path) is True
    assert store.mark_activity_end("speed", path=path) is True
    assert store.activity_active(path=path) is False


def test_marker_end_of_other_command_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "activity.json"
    store.mark_activity("monitor", path=path)
    assert store.mark_activity_end("probe", path=path) is False
    assert store.activity_active(path=path) is True


def test_activity_scope_closes_on_exception(tmp_path: Path) -> None:
    path = tmp_path / "activity.json"
    with pytest.raises(RuntimeError):
        with store.activity_scope("probe", path=path):
            assert store.activity_active(path=path) is True
            raise RuntimeError("boom")
    assert store.activity_active(path=path) is False


def test_activity_window_diagnostics(tmp_path: Path) -> None:
    path = tmp_path / "activity.json"
    assert store.activity_window(path=path) is None
    store.mark_activity("probe", path=path, grace_s=60)
    window = store.activity_window("probe", path=path)
    assert window is not None and window.total_seconds() > 0
    assert store.activity_window("speed", path=path) is None


# --- coverage payload --------------------------------------------------------

def _source_ok(name: str = "journal:user") -> dict:
    return {"name": name, "status": "ok"}


def test_build_coverage_is_schema_valid() -> None:
    payload = store.build_coverage(
        [_source_ok(), {"name": "journal:system", "status": "denied",
                        "note": "нет прав"},
         {"name": "netlink", "status": "off"}],
        signals={"xray": "ok", "amneziawg": "counters"},
        unparsed_ratio=0.02, dropped=1, blind=("whitelist_mode_unobservable",),
        notes=("unit_profile=hardened",))
    assert payload["schema"] == events.COVERAGE_SCHEMA
    assert schema.validate(payload, events.COVERAGE_SCHEMA) == []
    assert payload["blind"] == ["whitelist_mode_unobservable"]


def test_build_coverage_clips_notes_and_dedupes() -> None:
    long_note = "x" * 500
    payload = store.build_coverage(
        [_source_ok()], signals={"xray": "ok"},
        notes=(long_note, long_note, "short"))
    assert len(payload["notes"]) == 2  # duplicates collapsed
    assert all(len(note) <= store.NOTE_MAX_LEN for note in payload["notes"])
    assert payload["notes"][1] == "short"


def test_build_coverage_rejects_unknown_status() -> None:
    with pytest.raises(ValueError):
        store.build_coverage([{"name": "journal:user", "status": "fine"}],
                             signals={})


def test_coverage_degraded_flags() -> None:
    ok = store.build_coverage([_source_ok(), {"name": "netlink",
                                              "status": "off"}],
                              signals={"xray": "ok"})
    assert store.coverage_degraded(ok) is False
    denied = store.build_coverage(
        [_source_ok(), {"name": "journal:system", "status": "denied"}],
        signals={"xray": "ok"})
    assert store.coverage_degraded(denied) is True


def test_coverage_roundtrip_0600(tmp_path: Path) -> None:
    payload = store.build_coverage([_source_ok()], signals={"xray": "ok"})
    target = tmp_path / "coverage.json"
    store.write_coverage(payload, target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert store.read_coverage(target) == payload


def test_read_coverage_tolerates_garbage(tmp_path: Path) -> None:
    target = tmp_path / "coverage.json"
    target.write_text("{broken", encoding="utf-8")
    assert store.read_coverage(target) is None


# --- status payload ----------------------------------------------------------

def test_build_status_is_schema_valid() -> None:
    payload = store.build_status(
        running=True, state="armed", unit_profile="hardened",
        counters={"events": 10, "unparsed": 0, "dropped": 0,
                  "findings": 1, "suppressed": 0})
    assert payload["schema"] == events.STATUS_SCHEMA
    assert schema.validate(payload, events.STATUS_SCHEMA) == []
    assert payload["counters"]["events"] == 10


def test_build_status_defaults_and_state_enum() -> None:
    payload = store.build_status(running=False, state="idle",
                                 since="2026-11-16T10:00:00Z")
    assert payload["running"] is False
    assert payload["since"] == "2026-11-16T10:00:00Z"
    with pytest.raises(ValueError):
        store.build_status(running=True, state="sleepy")


# --- findings store (rotation budget) ---------------------------------------

def test_findings_append_and_read(tmp_path: Path) -> None:
    path = tmp_path / "findings.jsonl"
    findings = store.FindingsStore(path, segment_bytes=2048, max_segments=3)
    for index in range(5):
        findings.append({"schema": "guard.finding/1", "id": f"id{index}",
                         "window": {"last": "2026-11-16T12:00:0%dZ" % index}})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(findings.segment_paths()) <= 3
    newest = findings.read(limit=2)
    assert [item["id"] for item in newest] == ["id4", "id3"]


def test_findings_trim_by_age(tmp_path: Path) -> None:
    path = tmp_path / "findings.jsonl"
    store_ = store.FindingsStore(path, max_age_days=1,
                                 now_fn=lambda: time.time() + 30 * 86400)
    store_.append({"schema": "guard.finding/1",
                   "window": {"last": "2020-01-01T00:00:00Z"}})
    store_.trim_by_age()
    assert not path.exists()


# --- write-path redaction hooks (§7.3) --------------------------------------

def test_findings_append_sanitizes_secrets(tmp_path: Path) -> None:
    from vpnm.guard import redact

    path = tmp_path / "findings.jsonl"
    findings = store.FindingsStore(path, snippets=False)
    clean = findings.append({
        "schema": "guard.finding/1",
        "hypothesis": "node rejected password=FIXTURE_PW_leak",
        "evidence": [{"kind": "signal_count", "signal": "conn_reset",
                      "value": 2,
                      "snippet": "reset by peer FIXTURE_PW_snippet"}],
    })
    written = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert "FIXTURE_PW" not in path.read_text(encoding="utf-8")
    assert written == clean
    assert written["redaction_hits"] >= 1
    assert findings.redaction_hits >= 1
    assert "snippet" not in written["evidence"][0]  # snippets off by default
    assert redact.scan_secrets(written) == []


def test_findings_store_reads_snippets_config(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "guard.json").write_text(json.dumps({
        "schema": "guard.config/1", "redact": {"snippets": True}}),
        encoding="utf-8")
    path = tmp_path / "findings.jsonl"
    findings = store.FindingsStore(path, config_root=config)
    findings.append({"schema": "guard.finding/1",
                     "evidence": [{"kind": "signal_count",
                                   "signal": "conn_reset", "value": 1,
                                   "snippet": "connection reset"}]})
    written = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert written["evidence"][0]["snippet"] == "connection reset"
    assert store.redact_snippets(config) is True
    assert store.redact_snippets() is False


def test_coverage_write_sanitizes_notes(tmp_path: Path) -> None:
    from vpnm.guard import redact

    target = tmp_path / "coverage.json"
    payload = store.build_coverage(
        [{"name": "journal:user", "status": "ok"}],
        signals={"xray": "blind"},
        notes=("repair used password=FIXTURE_PW_cov",))
    assert "FIXTURE_PW" not in json.dumps(payload)
    assert payload["redaction_hits"] >= 1
    store.write_coverage(payload, target)
    text = target.read_text(encoding="utf-8")
    assert "FIXTURE_PW" not in text
    assert redact.scan_secrets(json.loads(text)) == []


def test_state_write_sanitizes_payload(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    store.write_state({"schema": "guard.state/1",
                       "note": "auth password=FIXTURE_PW_state"}, target)
    text = target.read_text(encoding="utf-8")
    assert "FIXTURE_PW" not in text
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert store.read_state(target) is not None


def test_status_carries_redaction_hits() -> None:
    payload = store.build_status(running=True, redaction_hits=7)
    assert payload["counters"]["redaction_hits"] == 7
    assert schema.validate(payload, events.STATUS_SCHEMA) == []


# --- persistent redaction_hits (WP-G3) ---------------------------------------

def test_state_redaction_hits_are_cumulative_across_restarts(
        tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    assert store.read_redaction_hits(target) == 0
    store.write_state({"schema": "guard.state/1",
                       "note": "auth password=FIXTURE_PW_state"}, target)
    first = store.read_redaction_hits(target)
    assert first >= 1
    # a clean write (a restarted daemon with nothing to mask) must not reset it
    store.write_state({"schema": "guard.state/1", "state": "armed"}, target)
    assert store.read_redaction_hits(target) == first
    # a write that masks again adds to the same counter
    store.write_state({"schema": "guard.state/1",
                       "note": "obfs-password=FIXTURE_PW_again"}, target)
    assert store.read_redaction_hits(target) > first
    assert "FIXTURE_PW" not in target.read_text(encoding="utf-8")


def test_state_redaction_hits_take_the_payload_counter_as_a_floor(
        tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    store.write_state({"schema": "guard.state/1", "redaction_hits": 5}, target)
    assert store.read_redaction_hits(target) >= 5
    store.write_state({"schema": "guard.state/1", "redaction_hits": 3}, target)
    assert store.read_redaction_hits(target) == 5


def test_state_redaction_hits_can_be_disabled(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    store.write_state({"schema": "guard.state/1",
                       "note": "password=FIXTURE_PW_noacc"}, target,
                      accumulate_redaction_hits=False)
    assert store.read_redaction_hits(target) == 0


def test_redaction_hits_total_prefers_the_persisted_counter(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    assert store.redaction_hits_total(3, target) == 3
    store.write_state({"schema": "guard.state/1", "redaction_hits": 9}, target)
    assert store.redaction_hits_total(3, target) == 9
    assert store.redaction_hits_total(0, tmp_path / "absent.json") == 0
    assert store.redaction_hits_total(-4, tmp_path / "absent.json") == 0
