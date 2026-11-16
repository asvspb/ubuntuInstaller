"""WP-G4 tests: the run loop, S3 contexts and D-06/D-07 invariants.

No journald, no sysfs, no network, no root: readers/watcher/poller are
injected, the sysfs tree is a temporary directory and the XDG paths point at
``tmp_path``.  Fixtures use RFC 5737 addresses only (AGENTS §6).
"""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import counters, events, parse, reader, rules, schema, store  # noqa: E402
from vpnm.guard.runner import GuardRunner, build_runner  # noqa: E402
from vpnm.guard.window import MONO_US_PER_S, Clock  # noqa: E402

US = MONO_US_PER_S
BASE = 1_000_000_000
UNIT = "vpnm-asv2.service"
EP = {"host": "203.0.113.10", "port": 443}
NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPN_MANAGER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VPN_MANAGER_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setenv("VPN_MANAGER_CONFIG_DIR", str(tmp_path / "config"))


def mono(seconds: float) -> int:
    return BASE + int(seconds * US)


def journal(message: str, seconds: float, *, unit: str = UNIT,
            **extra: object) -> dict:
    record = {
        "MESSAGE": message,
        "PRIORITY": "6",
        "__REALTIME_TIMESTAMP": str(1_794_830_400_000_000 + int(seconds * US)),
        "__MONOTONIC_TIMESTAMP": str(mono(seconds)),
        "_SYSTEMD_USER_UNIT": unit,
        "_SYSTEMD_INVOCATION_ID": "inv-1",
    }
    record.update(extra)
    return record


class FakeReader:
    """Minimal reader protocol (start/records/stop/status/note)."""

    def __init__(self, records: list[dict], source: str = "journal:user",
                 status: str = "off") -> None:
        self.records_list = list(records)
        self.source = source
        self.status = status
        self.note: str | None = None

    def start(self) -> None:
        self.status = "ok"

    def stop(self) -> None:
        self.status = "off"

    def records(self):
        for record in self.records_list:
            yield record


class RecordingEngine(rules.RuleEngine):
    """RuleEngine that records every closed AWG window (D-07 assertion)."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.awg_windows: list[dict] = []

    def note_awg_window(self, window):
        self.awg_windows.append(dict(window))
        return super().note_awg_window(window)


def storm_records() -> list[dict]:
    script = [
        journal("", 0.0, JOB_TYPE="start", JOB_RESULT="done"),
        journal("Xray 1.8.6 started", 1.0),
    ]
    for index in range(3):
        moment = 10.0 + index * 10
        script.append(journal(
            '{"level":"info","msg":"proxy: handshake ok with '
            '203.0.113.10:443"}', moment))
        script.append(journal(
            '{"level":"warning","msg":"connection reset by peer '
            '203.0.113.10:443"}', moment + 1))
    return script


def make_runner(*, records: list[dict] | None = None,
                clock: Clock | None = None,
                watcher: reader.FileWatcher | None = None,
                poller: counters.AwgCounterPoller | None = None,
                engine: rules.RuleEngine | None = None,
                sink: store.FindingsStore | None = None,
                state: list | None = None,
                coverage: list | None = None) -> GuardRunner:
    stamp = clock if clock is not None else Clock(BASE)
    engine = engine if engine is not None else rules.RuleEngine(clock=stamp)
    parser = parse.JournalParser(proto_hints={UNIT: "xray"})
    return GuardRunner(
        engine=engine, parser=parser,
        readers=[FakeReader(records)] if records is not None else [],
        watcher=watcher, poller=poller, sink=sink, clock=stamp,
        wall_clock=lambda: NOW,
        state_writer=(state.append if state is not None else None),
        coverage_writer=(coverage.append if coverage is not None else None),
    )


# --- run --once: journal → finding → sink ---------------------------------------

def test_run_once_parses_journal_and_writes_a_finding(tmp_path: Path) -> None:
    sink = store.FindingsStore(path=tmp_path / "findings.jsonl")
    runner = make_runner(records=storm_records(), sink=sink)
    counts = runner.run_once()
    assert counts.events >= 8
    assert counts.findings >= 1
    assert counts.high >= 1
    written = [json.loads(line) for line in
               (tmp_path / "findings.jsonl").read_text(
                   encoding="utf-8").splitlines()]
    assert written and all(
        schema.validate(item, events.FINDING_SCHEMA) == [] for item in written)
    assert [item["rule"] for item in written] == ["G-03"]
    assert written[0]["incident_id"].startswith("i-")
    assert runner.emitted


def test_run_once_persists_state_and_coverage(tmp_path: Path) -> None:
    runner = make_runner(records=storm_records())
    runner.run_once()
    state = json.loads(store.state_path().read_text(encoding="utf-8"))
    coverage = json.loads(store.coverage_path().read_text(encoding="utf-8"))
    assert state["schema"] == "guard.status/1"
    assert state["counters"]["findings"] >= 1
    assert state["open_incidents"]
    assert coverage["schema"] == "guard.coverage/1"
    assert any(item["name"] == "journal:user"
               for item in coverage["sources"])


def test_status_and_coverage_payloads_are_contract_valid() -> None:
    runner = make_runner(records=storm_records())
    runner.run_once()
    status = runner.status_payload()
    assert schema.validate(status, events.STATUS_SCHEMA) == []
    assert status["counters"]["findings"] >= 1
    assert status["last_lifecycle"]["signal"] == "unit_start"
    assert isinstance(status["counters"]["redaction_hits"], int)
    coverage = runner.coverage_payload()
    assert schema.validate(coverage, events.COVERAGE_SCHEMA) == []
    assert coverage["signals"]["xray"] == "ok"
    assert coverage["sources"]


# --- S3: speed report → G-11 (D-06) ---------------------------------------------

def speed_payload(speed: float, finished: str) -> dict:
    return {
        "finished": finished,
        "channels": [{"name": "asv2", "protocol": "xray-vless-reality",
                      "speed_mbps": speed}],
    }


def test_last_speed_file_drives_g11_on_the_ticker(tmp_path: Path) -> None:
    path = tmp_path / "last-speed.json"
    path.write_text(json.dumps(speed_payload(
        1.1, "2026-11-16T11:00:00Z")), encoding="utf-8")
    watcher = reader.FileWatcher({path: "file"})
    emitted: list = []
    runner = make_runner(watcher=watcher)
    runner.on_finding = emitted.append
    runner.poll_files()
    path.write_text(json.dumps(speed_payload(
        0.9, "2026-11-16T12:00:00Z")), encoding="utf-8")
    os.utime(path, ns=(1_800_000_000_000_000_000,
                       1_800_000_000_000_000_000))
    runner.poll_files()
    runner.tick()
    found = [item for item in emitted if item["rule"] == "G-11"]
    assert len(found) == 1
    assert found[0]["profile"] == "asv2"
    assert found[0]["count"] == 2
    assert schema.validate(found[0], events.FINDING_SCHEMA) == []


def test_cdn_speed_channel_is_excluded(tmp_path: Path) -> None:
    engine = rules.RuleEngine(clock=Clock(BASE))
    for index, moment in enumerate((10, 20)):
        engine.note_speed_report({
            "finished": f"2026-11-16T1{index}:00:00Z",
            "channels": [{"name": "xray-cdn-ws", "protocol": "xray-cdn-ws",
                          "speed_mbps": 0.4}]}, mono(moment))
    assert [item for item in engine.tick(mono(30))
            if item.is_finding] == []


# --- S3: activity marker suppresses a probe's own resets ------------------------

def test_activity_marker_from_file_suppresses_the_storm(tmp_path: Path) -> None:
    activity = tmp_path / "activity.json"
    activity.write_text(json.dumps({
        "cmd": "probe",
        "started": "2026-11-16T11:59:30Z",
        "until": "2026-11-16T12:10:00Z",
    }), encoding="utf-8")
    watcher = reader.FileWatcher({activity: "file"})
    # the storm spans 31 s of monotonic time: the marker must be stamped at
    # the "now" of the detection, not at the first journal line
    runner = make_runner(records=storm_records(), watcher=watcher,
                         clock=Clock(mono(31)))
    counts = runner.run_once()
    assert counts.findings == 0
    assert counts.suppressed >= 1


# --- D-07: closed AWG windows are noted exactly once ----------------------------

def make_iface(root: Path, name: str, tx: int = 0, rx: int = 0) -> Path:
    base = root / name
    stats = base / "statistics"
    stats.mkdir(parents=True, exist_ok=True)
    (base / "type").write_text(counters.ARPHRD_NONE + "\n", encoding="ascii")
    (stats / "tx_bytes").write_text(f"{tx}\n", encoding="ascii")
    (stats / "rx_bytes").write_text(f"{rx}\n", encoding="ascii")
    (stats / "tx_packets").write_text("1\n", encoding="ascii")
    (stats / "rx_packets").write_text("1\n", encoding="ascii")
    return base


def test_closed_awg_window_is_noted_once_per_window(tmp_path: Path) -> None:
    root = tmp_path / "sysfs"
    make_iface(root, "laptop", tx=0, rx=0)
    clock = Clock(BASE)
    poller = counters.AwgCounterPoller(sysfs_root=root, window_s=1)
    assert poller.resolve_iface() == "laptop"
    engine = RecordingEngine(clock=clock)
    runner = make_runner(clock=clock, poller=poller, engine=engine)
    runner.poll_sysfs()                                   # base sample at t=0
    assert engine.awg_windows == []
    (root / "laptop" / "statistics" / "tx_bytes").write_text(
        "300000\n", encoding="ascii")
    clock.advance(1.0)
    runner.poll_sysfs()                                   # window 1 closes
    assert len(engine.awg_windows) == 1
    runner.poll_sysfs()                                   # same tick: no repeat
    runner.poll_sysfs()
    assert len(engine.awg_windows) == 1
    (root / "laptop" / "statistics" / "tx_bytes").write_text(
        "700000\n", encoding="ascii")
    clock.advance(1.0)
    runner.poll_sysfs()                                   # window 2 closes
    assert len(engine.awg_windows) == 2
    for window in engine.awg_windows:
        assert window["mono_us"] == int(float(window["mono_s"]) * 1_000_000)


def test_awg_iface_gone_disables_the_branch(tmp_path: Path) -> None:
    root = tmp_path / "sysfs"
    base = make_iface(root, "laptop", tx=5, rx=5)
    clock = Clock(BASE)
    poller = counters.AwgCounterPoller(sysfs_root=root, window_s=1)
    assert poller.resolve_iface() == "laptop"
    runner = make_runner(clock=clock, poller=poller)
    runner.poll_sysfs()
    import shutil
    shutil.rmtree(base)
    clock.advance(1.0)
    runner.poll_sysfs()
    assert poller.evaluate()["iface_gone"] is True


# --- no network / no root (R-4, AC-7) -------------------------------------------

def test_run_loop_never_opens_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object):
        raise AssertionError("guard must not create sockets")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    runner = make_runner(records=storm_records())
    counts = runner.run_once()
    assert counts.events >= 1


# --- factory --------------------------------------------------------------------

def test_build_runner_reads_thresholds_and_injected_sources(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "guard.json").write_text(json.dumps({
        "thresholds": {"reset_storm_count": 5, "degraded_mbps": 3.0},
        "sources": {"user_journal": True, "files": False,
                    "awg_counters": False, "system_journal": False},
    }), encoding="utf-8")

    def fake_install_state():
        return {"profile": "compat"}

    monkeypatch.setattr("vpnm.guard.install.load_install_state",
                        fake_install_state)
    sink = store.FindingsStore(path=tmp_path / "findings.jsonl")
    runner = build_runner(config_root=config, readers=[FakeReader([])],
                          watcher=None, poller=None, sink=sink,
                          state_writer=lambda _payload: None,
                          coverage_writer=lambda _payload: None)
    assert runner.engine.thresholds.reset_storm_count == 5
    assert runner.engine.thresholds.degraded_mbps == 3.0
    assert runner.unit_profile == "compat"
    assert runner.watcher is None and runner.poller is None
