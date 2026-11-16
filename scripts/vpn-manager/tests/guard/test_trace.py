"""WP-G5 tests: ``run --trace`` ring (SPEC §10.3, §7.3 privacy).

The trace must store only redacted ``guard.event/1`` lines, rotate at 10 MB
or 1 h and live in the 0700/0600 guard directory.  No real journal, no root.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import redact, rules, store  # noqa: E402
from vpnm.guard.runner import GuardRunner  # noqa: E402

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPN_MANAGER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VPN_MANAGER_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setenv("VPN_MANAGER_CONFIG_DIR", str(tmp_path / "config"))


def event(message: str = "connection reset by peer", **fields: object) -> dict:
    return {
        "schema": "guard.event/1",
        "ts": "2026-11-16T12:00:03.412Z",
        "mono_us": 812_345_678,
        "source": "journal:user",
        "unit": "vpnm-asv2.service",
        "profile": "asv2",
        "proto": "xray",
        "level": "warn",
        "signal": "conn_reset",
        "template": "3f9a1c02be77",
        "endpoint": {"host": "203.0.113.10", "port": 443},
        "fields": {"message": message, **fields},
        "count": 1,
    }


def test_trace_appends_a_redacted_event(tmp_path: Path) -> None:
    writer = store.TraceWriter(path=tmp_path / "trace.ndjson", now_fn=lambda: NOW)
    assert writer.append(event()) is True
    line = (tmp_path / "trace.ndjson").read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["signal"] == "conn_reset"
    assert redact.scan_secrets(payload) == []
    assert stat.S_IMODE((tmp_path / "trace.ndjson").stat().st_mode) == 0o600


def test_trace_masks_secrets_before_the_write(tmp_path: Path) -> None:
    writer = store.TraceWriter(path=tmp_path / "trace.ndjson",
                               now_fn=lambda: NOW)
    writer.append(event(message="password=FIXTURE_PW_TRACE boom"))
    raw = (tmp_path / "trace.ndjson").read_text(encoding="utf-8")
    assert "FIXTURE_PW_TRACE" not in raw
    payload = json.loads(raw.strip())
    assert payload["fields"]["message"].startswith(redact.REDACTED)
    assert redact.scan_secrets(payload) == []


def test_trace_rotates_on_size(tmp_path: Path) -> None:
    path = tmp_path / "trace.ndjson"
    writer = store.TraceWriter(path=path, max_bytes=800, now_fn=lambda: NOW)
    for _ in range(10):
        assert writer.append(event()) is True
    rotated = path.with_name(path.name + ".1")
    assert rotated.is_file()
    assert path.stat().st_size <= 800


def test_trace_rotates_when_older_than_an_hour(tmp_path: Path) -> None:
    path = tmp_path / "trace.ndjson"
    writer = store.TraceWriter(path=path, now_fn=lambda: NOW)
    writer.append(event())
    os.utime(path, (NOW - 7200, NOW - 7200))
    writer.append(event())
    rotated = path.with_name(path.name + ".1")
    assert rotated.is_file()
    assert len(path.read_text().splitlines()) == 1
    assert len(rotated.read_text().splitlines()) == 1


def test_trace_write_failure_returns_false(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    writer = store.TraceWriter(path=blocked / "trace.ndjson",
                               now_fn=lambda: NOW)
    assert writer.append(event()) is False


class OneEventReader:
    source = "journal:user"
    status = "ok"
    note = None

    def __init__(self, record: dict) -> None:
        self.record = record

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def records(self):
        yield self.record


def journal_record(message: str) -> dict:
    return {
        "MESSAGE": message,
        "PRIORITY": "6",
        "__REALTIME_TIMESTAMP": "1794830403412345",
        "__MONOTONIC_TIMESTAMP": "812345678",
        "_SYSTEMD_USER_UNIT": "vpnm-asv2.service",
        "_SYSTEMD_INVOCATION_ID": "inv-1",
    }


def test_run_once_writes_the_trace(tmp_path: Path) -> None:
    from vpnm.guard import parse
    writer = store.TraceWriter(path=tmp_path / "trace.ndjson",
                               now_fn=lambda: NOW)
    runner = GuardRunner(
        engine=rules.RuleEngine(),
        parser=parse.JournalParser(proto_hints={"vpnm-asv2.service": "xray"}),
        readers=[OneEventReader(journal_record(
            '{"level":"warn","message":"connection reset by peer"}'))],
        sink=store.FindingsStore(path=tmp_path / "findings.jsonl"),
        trace=writer,
        restore_speed_ring=False,
        state_writer=lambda payload: None,
        coverage_writer=lambda payload: None,
    )
    runner.run_once()
    lines = (tmp_path / "trace.ndjson").read_text().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["signal"] == "conn_reset"
    assert payload["schema"] == "guard.event/1"
    assert redact.scan_secrets(payload) == []


def test_trace_failure_is_noted_once(tmp_path: Path) -> None:
    from vpnm.guard import parse

    class FailingTrace:
        def append(self, payload):
            return False

    runner = GuardRunner(
        engine=rules.RuleEngine(),
        parser=parse.JournalParser(proto_hints={"vpnm-asv2.service": "xray"}),
        readers=[],
        sink=store.FindingsStore(path=tmp_path / "findings.jsonl"),
        trace=FailingTrace(),
        restore_speed_ring=False,
        state_writer=lambda payload: None,
        coverage_writer=lambda payload: None,
    )
    for record in (journal_record("Xray 1.8.6 started"),
                   journal_record("Xray 1.8.6 started")):
        runner._ingest_record(record, "journal:user", streaming=False)
    assert runner.notes.count("trace_write_failed") == 1
