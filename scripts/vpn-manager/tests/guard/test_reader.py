"""WP-G1 reader tests: journal JSON parsing, cursors, dedup, backfill cap.

No journald, no root, no network: the reader is driven by a fake Popen
that yields canned ``-o json`` lines (the same protocol journalctl uses).
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import reader  # noqa: E402


# --- fakes ----------------------------------------------------------------------

class _Lines:
    """Iterator that knows when it is exhausted (for poll())."""

    def __init__(self, items: List[str]) -> None:
        self._it = iter(items)
        self.done = not items

    def __iter__(self) -> "_Lines":
        return self

    def __next__(self) -> str:
        try:
            return next(self._it)
        except StopIteration:
            self.done = True
            raise


class FakeProc:
    def __init__(self, lines: List[str], rc: int = 0, stderr: str = "") -> None:
        self.stdout = _Lines(lines)
        self.stderr = stderr
        self.returncode = rc
        self.terminated = False

    def poll(self) -> Optional[int]:
        return self.returncode if self.stdout.done else None

    def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True


class FakePopen:
    """Records commands; returns pre-scripted processes in order."""

    def __init__(self, procs: List[FakeProc]) -> None:
        self.procs = list(procs)
        self.commands: List[List[str]] = []

    def __call__(self, cmd: List[str], **kwargs: object) -> FakeProc:
        self.commands.append(list(cmd))
        if not self.procs:
            raise AssertionError(f"unexpected journalctl call: {cmd}")
        return self.procs.pop(0)


def jline(unit: str = "vpnm-phone.service", cursor: str = "c1",
          msg: str = "hello", ts_us: str = "1760000000123456") -> str:
    return json.dumps({
        "__CURSOR": cursor,
        "_SYSTEMD_USER_UNIT": unit,
        "MESSAGE": msg,
        "PRIORITY": "6",
        "__REALTIME_TIMESTAMP": ts_us,
    })


# --- parsing ----------------------------------------------------------------------

def test_parse_journal_line_valid_and_garbage() -> None:
    record = reader.parse_journal_line(jline())
    assert record is not None and record["MESSAGE"] == "hello"
    assert reader.parse_journal_line("not json") is None
    assert reader.parse_journal_line("[1,2,3]") is None
    assert reader.parse_journal_line("") is None


def test_realtime_iso_format() -> None:
    record = json.loads(jline(ts_us="1760000000123456"))
    # 1760000000 s = 2025-10-09T08:53:20Z (verified with ``date -u -d``);
    # journald stores UTC microseconds since the epoch (SPEC §7.1).
    assert reader.realtime_iso(record) == "2025-10-09T08:53:20.123Z"
    assert reader.realtime_iso({}) is None


def test_record_unit_fallbacks() -> None:
    assert reader.record_unit({"_SYSTEMD_USER_UNIT": "vpnm-a.service"}) == "vpnm-a.service"
    assert reader.record_unit({"UNIT": "awg-quick@x.service"}) == "awg-quick@x.service"
    assert reader.record_unit({"_SYSTEMD_UNIT": "dnsmasq.service"}) == "dnsmasq.service"
    assert reader.record_unit({}) is None


# --- unit prefilter -----------------------------------------------------------

def test_interesting_unit_user_journal() -> None:
    assert reader.interesting_unit("vpnm-phone.service", user=True)
    assert not reader.interesting_unit(reader.GUARD_UNIT, user=True)
    assert not reader.interesting_unit("gdm.service", user=True)
    assert not reader.interesting_unit(None, user=True)


def test_interesting_unit_system_journal() -> None:
    assert reader.interesting_unit("awg-quick@laptop.service", user=False)
    assert reader.interesting_unit("vpn-awg-split@laptop.service", user=False)
    assert reader.interesting_unit("dnsmasq.service", user=False)
    assert not reader.interesting_unit("vpnm-phone.service", user=False)
    assert not reader.interesting_unit("sshd.service", user=False)


# --- command building ------------------------------------------------------------

def test_journalctl_cmd_shape() -> None:
    cmd = reader.journalctl_cmd(user=True, cursor="abc", since="-2m")
    assert cmd[:4] == ["journalctl", "-o", "json", "--user"]
    assert "--user" in cmd
    assert "-f" in cmd
    assert "--after-cursor=abc" in cmd
    assert "--since=-2m" in cmd
    assert any(arg.startswith("--output-fields=") for arg in cmd)
    fields = next(arg for arg in cmd if arg.startswith("--output-fields="))
    assert "__CURSOR" in fields and "MESSAGE" in fields


def test_journalctl_cmd_system_units_without_output_fields() -> None:
    cmd = reader.journalctl_cmd(user=False, since=None, cursor=None,
                                units=reader.SYSTEM_UNITS,
                                output_fields=False)
    assert "--user" not in cmd
    assert not any(a.startswith("--output-fields=") for a in cmd)
    flat = " ".join(cmd)
    assert "-u awg-quick@*" in flat and "-u dnsmasq" in flat


# --- backfill cap ------------------------------------------------------------------

@pytest.mark.parametrize("since,seconds", [("-2m", 120), ("24h", 86400),
                                          ("90s", 90), ("10", 10)])
def test_parse_since(since: str, seconds: int) -> None:
    assert reader.parse_since(since) == seconds


def test_parse_since_garbage() -> None:
    with pytest.raises(ValueError):
        reader.parse_since("soon")


def test_backfill_over_24h_refused() -> None:
    with pytest.raises(reader.BackfillError):
        reader.validate_backfill("25h")
    assert reader.validate_backfill("-2m") == 120


# --- cursor persistence ------------------------------------------------------------

def test_cursor_store_roundtrip_and_perms(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    store = reader.CursorStore(path)
    assert store.load() == {}
    store.save("journal:user", "cursor-1")
    assert store.get("journal:user") == "cursor-1"
    store.save("journal:system", "cursor-2")
    assert store.load() == {"journal:user": "cursor-1",
                            "journal:system": "cursor-2"}
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    # empty cursor is a no-op
    store.save("journal:user", "")
    assert store.get("journal:user") == "cursor-1"


def test_cursor_store_survives_garbage(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    path.write_text("{broken", encoding="utf-8")
    assert reader.CursorStore(path).load() == {}


# --- streaming: dedup + filter ----------------------------------------------------

def _records(popen: FakePopen, **kwargs: object) -> List[dict]:
    rdr = reader.JournalReader(popen=popen, cursor_grace_s=0.0,
                               **kwargs)  # type: ignore[arg-type]
    return list(rdr.records())


def test_records_dedup_by_cursor() -> None:
    lines = [jline(cursor="c1"), jline(cursor="c1"),
             jline(cursor="c2"), jline(cursor="c1")]
    popen = FakePopen([FakeProc(lines)])
    records = _records(popen)
    assert [r["__CURSOR"] for r in records] == ["c1", "c2"]
    assert popen.commands[0][0] == "journalctl"


def test_records_filter_units() -> None:
    lines = [
        jline(unit="vpnm-phone.service", cursor="a"),
        jline(unit=reader.GUARD_UNIT, cursor="b"),
        jline(unit="gnome-shell.service", cursor="c"),
        jline(unit="vpnm-cdn-home.service", cursor="d"),
    ]
    records = _records(FakePopen([FakeProc(lines)]))
    assert [r["_SYSTEMD_USER_UNIT"] for r in records] == [
        "vpnm-phone.service", "vpnm-cdn-home.service"]


def test_records_bad_lines_skipped() -> None:
    lines = ["garbage", jline(cursor="a"), ""]
    records = _records(FakePopen([FakeProc(lines)]))
    assert len(records) == 1


# --- cursor fallback ------------------------------------------------------------

def test_stale_cursor_falls_back_to_since() -> None:
    dead = FakeProc([], rc=1, stderr="Failed to seek to cursor: Invalid argument")
    fresh = FakeProc([jline(cursor="z1")])
    popen = FakePopen([dead, fresh])
    records = _records(popen, cursor="very-old-cursor")
    assert len(records) == 1
    first, second = popen.commands
    assert "--after-cursor=very-old-cursor" in first
    assert "--since=-2m" in second
    assert not any(a.startswith("--after-cursor=") for a in second)


def test_live_cursor_process_kept() -> None:
    proc = FakeProc([jline(cursor="k1")])
    popen = FakePopen([proc])
    records = _records(popen, cursor="fresh")
    assert len(records) == 1
    assert len(popen.commands) == 1


# --- S2: honest denied ----------------------------------------------------------

def test_system_journal_denied_reports_awg_unobservable() -> None:
    denied = FakeProc([], rc=1, stderr="journalctl: Permission denied")
    rdr = reader.JournalReader(source="journal:system", user=False,
                               units=reader.SYSTEM_UNITS,
                               popen=FakePopen([denied]))
    assert list(rdr.records()) == []
    assert rdr.status == "denied"
    assert rdr.note is not None
    assert "awg_lifecycle_unobservable" in rdr.note


def test_system_journal_denied_without_awg_units_plain_note() -> None:
    denied = FakeProc([], rc=1, stderr="journalctl: Permission denied")
    rdr = reader.JournalReader(source="journal:system", user=False,
                               units=("dnsmasq",),
                               popen=FakePopen([denied]))
    assert list(rdr.records()) == []
    assert rdr.status == "denied"
    assert rdr.note is not None and "awg" not in rdr.note


def test_system_journal_lost_on_other_error() -> None:
    broken = FakeProc([], rc=1, stderr="something else")
    rdr = reader.JournalReader(source="journal:system", user=False,
                               units=("dnsmasq",),
                               popen=FakePopen([broken]))
    assert list(rdr.records()) == []
    assert rdr.status == "lost"


# --- helpers -----------------------------------------------------------------------

def test_open_user_reader_uses_persisted_cursor(tmp_path: Path) -> None:
    store = reader.CursorStore(tmp_path / "cursor.json")
    store.save("journal:user", "persisted")
    proc = FakeProc([jline(cursor="n1")])
    popen = FakePopen([proc])
    rdr = reader.open_user_reader(cursors=store, popen=popen)
    list(rdr.records())
    assert "--after-cursor=persisted" in popen.commands[0]


def test_backoff_delays_capped_at_30s() -> None:
    assert reader.backoff_delays(7) == [1, 2, 4, 8, 16, 30, 30]


def test_guard_unit_name_not_a_profile_prefix() -> None:
    # SPEC §10.4 / RSK-11: vpnm- prefix would collide with profiles.
    assert reader.GUARD_UNIT == "vpn-guard.service"
    assert not reader.GUARD_UNIT.startswith("vpnm-")
