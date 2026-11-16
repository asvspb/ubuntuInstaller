"""WP-G1 source tests: journal health, S3 file watcher, coverage assembly.

No journald, no root, no network: journal probes are injected
``(rc, out, err)`` stubs, S3 files live in ``tmp_path``.
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from typing import List, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import cli, counters, install, reader, store  # noqa: E402


class FakeRun:
    def __init__(self, responses: List[Tuple[int, str, str]]) -> None:
        self.responses = list(responses)
        self.calls: List[List[str]] = []

    def __call__(self, cmd: List[str]) -> Tuple[int, str, str]:
        self.calls.append(list(cmd))
        return self.responses.pop(0)


# --- unit prefilter ----------------------------------------------------------

def test_system_units_match_full_journal_names() -> None:
    for unit in ("dnsmasq.service", "systemd-resolved.service",
                 "NetworkManager.service", "zapret.service",
                 "cloudflared-proxy-dns.service"):
        assert reader.interesting_unit(unit, user=False), unit
    assert reader.interesting_unit("awg-quick@laptop.service", user=False)
    assert reader.interesting_unit("vpn-awg-split@laptop.service", user=False)
    assert not reader.interesting_unit("sshd.service", user=False)


# --- journal health ----------------------------------------------------------

def test_journal_readable_ok() -> None:
    health = reader.journal_readable(user=True, run=FakeRun([(0, "", "")]))
    assert health.status == "ok"
    assert health.as_dict() == {"name": "journal:user", "status": "ok"}


def test_journal_readable_denied_system_notes_awg() -> None:
    run = FakeRun([(1, "", "Hint: You are currently not seeing messages from "
                           "other users.\nPermission denied")])
    health = reader.journal_readable(user=False, run=run)
    assert health.status == "denied"
    assert health.note is not None
    assert "awg_lifecycle_unobservable" in health.note
    # zero-record read only, never sudo
    assert run.calls[0][0] == "journalctl"
    assert "-n" in run.calls[0]


def test_journal_readable_user_denied_plain_note() -> None:
    health = reader.journal_readable(
        user=True, run=FakeRun([(1, "", "Permission denied")]))
    assert health.status == "denied"
    assert health.note == "нет прав на журнал"


def test_journal_readable_lost_on_other_error() -> None:
    health = reader.journal_readable(
        user=True, run=FakeRun([(1, "", "boom")]))
    assert health.status == "lost"
    assert health.note is not None and "1" in health.note


def test_journal_missing_binary_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(reader, "journalctl_available", lambda: False)
    assert reader.journal_readable(user=True).status == "unavailable"


# --- cursor persistence (AC-11: restart without duplicates) ------------------

def jline(cursor: str, unit: str = "vpnm-asg.service") -> str:
    return json.dumps({
        "__CURSOR": cursor,
        "_SYSTEMD_USER_UNIT": unit,
        "MESSAGE": "hello",
        "__REALTIME_TIMESTAMP": "1760000000123456",
    })


class _Lines:
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

    def poll(self):
        return self.returncode if self.stdout.done else None

    def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        pass


class FakePopen:
    def __init__(self, procs: List[FakeProc]) -> None:
        self.procs = list(procs)
        self.commands: List[List[str]] = []

    def __call__(self, cmd: List[str], **kwargs: object) -> FakeProc:
        self.commands.append(list(cmd))
        return self.procs.pop(0)


def _read(store_: reader.CursorStore, line_sets: List[List[str]],
          cursor: str, since: str = "-2m") -> Tuple[List[dict], FakePopen]:
    popen = FakePopen([FakeProc(lines) for lines in line_sets])
    rdr = reader.JournalReader(source="journal:user", cursor=cursor,
                               since=since, cursors=store_, popen=popen,
                               cursor_grace_s=0.0)
    return list(rdr.records()), popen


def test_restart_resumes_after_persisted_cursor(tmp_path: Path) -> None:
    cursors = reader.CursorStore(tmp_path / "cursor.json")
    records, _ = _read(cursors, [[jline("c1"), jline("c2")]], cursor=None)
    assert [r["__CURSOR"] for r in records] == ["c1", "c2"]
    assert cursors.get("journal:user") == "c2"

    # next start resumes with --after-cursor, not --since
    records2, popen2 = _read(cursors, [[jline("c3")]],
                             cursor=cursors.get("journal:user"))
    assert [r["__CURSOR"] for r in records2] == ["c3"]
    assert "--after-cursor=c2" in popen2.commands[0]
    assert not any(arg.startswith("--since=") for arg in popen2.commands[0])


def test_stale_cursor_restart_does_not_duplicate(tmp_path: Path) -> None:
    cursors = reader.CursorStore(tmp_path / "cursor.json")
    first, _ = _read(cursors, [[jline("c1"), jline("c2")]], cursor=None)
    assert len(first) == 2

    # kill -9 style restart with a stale cursor: journalctl dies immediately,
    # the reader falls back to --since and must not re-emit c1/c2
    dead = FakeProc([], rc=1, stderr="Failed to seek to cursor: Invalid")
    live = FakeProc([jline("c1"), jline("c2"), jline("c3")])
    popen = FakePopen([dead, live])
    rdr = reader.JournalReader(source="journal:user", cursor="very-old",
                               since="-2m", cursors=cursors, popen=popen,
                               cursor_grace_s=0.0)
    records = list(rdr.records())
    assert [r["__CURSOR"] for r in records] == ["c3"]
    assert "--since=-2m" in popen.commands[1]
    assert cursors.get("journal:user") == "c3"


def test_cursor_advances_even_for_filtered_records(tmp_path: Path) -> None:
    """Noise-only journal still moves the cursor: no replay of the window."""
    cursors = reader.CursorStore(tmp_path / "cursor.json")
    noise = [json.dumps({"__CURSOR": "n1", "_SYSTEMD_USER_UNIT": "gdm.service"}),
             json.dumps({"__CURSOR": "n2", "_SYSTEMD_USER_UNIT": "gdm.service"})]
    records, _ = _read(cursors, [noise], cursor=None)
    assert records == []
    assert cursors.get("journal:user") == "n2"


def test_recent_cursor_list_is_bounded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(reader, "PERSIST_RECENT_MAX", 4)
    cursors = reader.CursorStore(tmp_path / "cursor.json")
    cursors.save("journal:user", "c9",
                 recent=[f"c{index}" for index in range(10)])
    stored = json.loads((tmp_path / "cursor.json").read_text(encoding="utf-8"))
    assert len(stored["journal:user:recent"]) == 4
    assert stored["journal:user:recent"][-1] == "c9"
    assert len(cursors.recent("journal:user")) == 4


def test_cursor_write_failure_degrades_not_crashes(tmp_path: Path) -> None:
    class BrokenStore:
        """Duck-typed cursor store whose save always fails (read-only fs)."""

        def __init__(self, path: Path) -> None:
            self.path = path

        def load(self) -> dict:
            return {}

        def get(self, source: str):
            return None

        def recent(self, source: str, limit: int = 0):
            return []

        def save(self, *args: object, **kwargs: object) -> None:
            raise OSError("read-only fs")

    rdr = reader.JournalReader(source="journal:user",
                               cursors=BrokenStore(tmp_path / "c.json"),
                               popen=FakePopen([FakeProc([jline("c1")])]),
                               cursor_grace_s=0.0)
    records = list(rdr.records())
    assert len(records) == 1  # stream continues
    assert rdr.status == "degraded"
    assert rdr.note == "cursor_write_failed"


# --- S3 file watcher ---------------------------------------------------------

def test_file_watcher_reports_only_changes(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"active": "asg"}), encoding="utf-8")
    watcher = reader.FileWatcher({state: "state"})
    changes = watcher.poll()
    assert len(changes) == 1
    assert changes[0].source == "state"
    assert changes[0].payload == {"active": "asg"}
    assert watcher.poll() == []

    state.write_text(json.dumps({"active": None}), encoding="utf-8")
    changed = watcher.poll()
    assert len(changed) == 1 and changed[0].payload == {"active": None}


def test_file_watcher_broken_json_and_missing(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text("{broken", encoding="utf-8")
    watcher = reader.FileWatcher({state: "state"})
    changes = watcher.poll()
    assert changes[0].note == "state_parse_error"

    state.unlink()
    gone = watcher.poll()
    assert gone[0].note == "file_gone"


def test_file_watcher_repair_log_only_action_lines(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "repair-1.log"
    log.parent.mkdir(parents=True)
    log.write_text("noise\nDNS: switched to 1.1.1.1\nmore noise\n"
                   "итог: ok\n", encoding="utf-8")
    watcher = reader.FileWatcher({}, repair_globs=(tmp_path / "logs" / "repair-*.log",))
    changes = watcher.poll()
    assert len(changes) == 1
    assert changes[0].lines == ("DNS: switched to 1.1.1.1", "итог: ok")


def test_state_and_verdict_health(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    assert reader.state_source_health(state).status == "degraded"
    state.write_text("{}", encoding="utf-8")
    assert reader.state_source_health(state).status == "ok"

    verdict = tmp_path / "server-verdict.json"
    assert reader.verdict_source_health(verdict).status == "off"
    verdict.write_text("{broken", encoding="utf-8")
    assert reader.verdict_source_health(verdict).status == "degraded"
    verdict.write_text(json.dumps({"schema": "guard.server_verdict/1"}),
                       encoding="utf-8")
    assert reader.verdict_source_health(verdict).status == "ok"


# --- coverage assembly -------------------------------------------------------

def _coverage(tmp_path: Path, **kwargs) -> dict:
    run = FakeRun([(0, "", "")])  # user journal readable
    kwargs.setdefault("run", run)
    kwargs.setdefault("config_root", tmp_path / "config")
    kwargs.setdefault("state_path", tmp_path / "state.json")
    kwargs.setdefault("verdict_path", tmp_path / "server-verdict.json")
    kwargs.setdefault("files", {})
    kwargs.setdefault("sysfs_root", tmp_path / "sysfs")
    return cli.collect_coverage(**kwargs)


def test_coverage_reports_honest_blind_sources(tmp_path: Path) -> None:
    sysfs = tmp_path / "sysfs"
    sysfs.mkdir()
    payload = _coverage(tmp_path)
    names = [entry["name"] for entry in payload["sources"]]
    assert names == ["journal:user", "journal:system", "state", "file",
                     "verdict", "sysfs:awg", "netlink"]
    statuses = {entry["name"]: entry["status"] for entry in payload["sources"]}
    assert statuses["journal:user"] == "ok"
    assert statuses["journal:system"] == "off"
    assert statuses["sysfs:awg"] == "off"
    assert payload["signals"]["amneziawg"] == "missing"
    assert "user_journal_unreadable" not in payload["blind"]


def test_coverage_awg_iface_is_counters(tmp_path: Path) -> None:
    sysfs = tmp_path / "sysfs"
    iface = sysfs / "laptop"
    (iface / "statistics").mkdir(parents=True)
    (iface / "type").write_text("65534\n", encoding="ascii")
    for name in ("tx_bytes", "rx_bytes", "tx_packets", "rx_packets"):
        (iface / "statistics" / name).write_text("0\n", encoding="ascii")
    payload = _coverage(tmp_path)
    sysfs_entry = next(entry for entry in payload["sources"]
                       if entry["name"] == "sysfs:awg")
    assert sysfs_entry["status"] == "ok"
    assert sysfs_entry["note"] == "iface=laptop"
    assert payload["signals"]["amneziawg"] == "counters"


def test_coverage_flags_awg_ambiguity(tmp_path: Path) -> None:
    sysfs = tmp_path / "sysfs"
    for name in ("awg-home", "awg-work"):
        base = sysfs / name / "statistics"
        base.mkdir(parents=True)
        (sysfs / name / "type").write_text("65534\n", encoding="ascii")
        for counter in ("tx_bytes", "rx_bytes", "tx_packets", "rx_packets"):
            (base / counter).write_text("0\n", encoding="ascii")
    payload = _coverage(tmp_path)
    assert "awg_iface_ambiguous" in payload["notes"]
    assert payload["signals"]["amneziawg"] == "counters"


def test_coverage_flagged_degraded_when_journal_denied(tmp_path: Path) -> None:
    run = FakeRun([(1, "", "Permission denied")])
    payload = _coverage(tmp_path, run=run)
    assert payload["sources"][0]["status"] == "denied"
    assert store.coverage_degraded(payload) is True
    assert "user_journal_unreadable" in payload["blind"]


def test_coverage_reports_unit_profile_note(tmp_path: Path) -> None:
    payload = _coverage(tmp_path, install_state={
        "profile": "compat", "fallback_reason": "hardened start failed: 226"})
    assert any(note.startswith("unit_profile=compat")
               for note in payload["notes"])


def test_coverage_system_journal_opt_in(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "guard.json").write_text(json.dumps({
        "schema": "guard.config/1",
        "sources": {"system_journal": True},
    }), encoding="utf-8")
    run = FakeRun([(0, "", ""), (1, "", "Permission denied")])
    payload = _coverage(tmp_path, run=run, config_root=config)
    system = payload["sources"][1]
    assert system["name"] == "journal:system"
    assert system["status"] == "denied"
    assert "awg_lifecycle_unobservable" in payload["notes"]


# --- AC-7: no network sockets in the WP-G1 surface ---------------------------

def test_reader_no_network_sockets(tmp_path: Path, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("guard opened a network socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "socketpair", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)

    # journal parsing / cursor store
    reader.parse_journal_line(jline("c1"))
    reader.realtime_iso(json.loads(jline("c1")))
    cursors = reader.CursorStore(tmp_path / "cursor.json")
    cursors.save("journal:user", "c1")

    # S3 watcher + health probes
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    list(reader.FileWatcher({state: "state"}).poll())
    reader.state_source_health(state)
    reader.file_source_health({state: "state"})
    reader.verdict_source_health(tmp_path / "verdict.json")

    # AWG sysfs + activity marker + unit rendering
    counters.AwgCounterPoller(sysfs_root=tmp_path).resolve_iface()
    store.mark_activity("probe", path=tmp_path / "activity.json")
    install.render_unit("hardened")

    # coverage assembly (journal probe is an injected runner, not a socket)
    cli.collect_coverage(run=FakeRun([(0, "", "")]),
                         config_root=tmp_path / "config",
                         state_path=state,
                         verdict_path=tmp_path / "verdict.json",
                         files={state: "state"},
                         sysfs_root=tmp_path / "sysfs")
