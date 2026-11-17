"""Daemon run loop for ``vpn guard`` (WP-G4, SPEC §5.3/§10.1).

The loop wires the pieces the previous work packages built and nothing else:

- readers S1/S2 (``journalctl`` subprocesses) and S3 (``FileWatcher``) plus the
  1 Hz AmneziaWG sysfs poller;
- the parser (:mod:`vpnm.guard.parse`) → :class:`~vpnm.guard.rules.RuleEngine`;
- the correlator (:mod:`vpnm.guard.correlate`) → ``findings.jsonl`` sink;
- a 5 s ticker for absence rules/hysteresis/G-11/G-12 and a periodic
  ``state.json``/``coverage.json`` snapshot;
- opt-in ``trace.ndjson`` sink (``--trace``) that redacts each event before it
  touches the disk (SPEC §10.3);
- opt-in desktop notifications (``notify.desktop=true``), severity/rate gated
  in :mod:`vpnm.guard.notify` (SPEC §8.1/§10.4).

D-06/D-07/D-09 invariants:

- every S3-driven context is stamped with the single monotonic clock
  (``clock.now_us()``/``window.mono_us()``) — wall-clock is used only for
  freshness of ``fetched_at`` (``verdict.py``);
- a closed 120 s AWG counter window is popped **once** and handed to
  ``engine.note_awg_window`` exactly once per window;
- the G-11 speed ring is restored from ``state.json`` at start and written back
  on every persist, so a restart does not reset the 24 h history (D-09).

No network, no root, no ``systemctl``: the only network command of the guard
is the explicit ``vpn guard sync`` (:mod:`vpnm.guard.verdict`).
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Sequence

from . import counters, parse, reader, store, verdict
from . import notify as notify_mod
from .correlate import Correlator
from .rules import RuleEngine, RuleOutcome, ServerContext, Thresholds
from .window import MONO_US_PER_S, Clock, mono_us

DEFAULT_TICK_S = 5.0
DEFAULT_POLL_S = 1.0
DEFAULT_QUEUE_MAX = 10_000
#: How many queued records are folded before the loop checks its timers.
DRAIN_BATCH = 512


@dataclass
class RunCounters:
    """Counters of ``guard.status/1`` fed by the loop."""

    events: int = 0
    unparsed: int = 0
    dropped: int = 0
    findings: int = 0
    suppressed: int = 0
    high: int = 0
    redaction_hits: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "events": self.events,
            "unparsed": self.unparsed,
            "dropped": self.dropped,
            "findings": self.findings,
            "suppressed": self.suppressed,
            "redaction_hits": self.redaction_hits,
        }


class GuardRunner:
    """One guard run: sources → rules → findings/incidents → own artefacts."""

    def __init__(self, *,
                 engine: RuleEngine,
                 parser: Optional[parse.JournalParser] = None,
                 readers: Sequence[Any] = (),
                 watcher: Optional[reader.FileWatcher] = None,
                 poller: Optional[counters.AwgCounterPoller] = None,
                 correlator: Optional[Correlator] = None,
                 sink: Optional[store.FindingsStore] = None,
                 clock: Optional[Clock] = None,
                 now_us_fn: Optional[Callable[[], int]] = None,
                 wall_clock: Optional[Callable[[], datetime]] = None,
                 tick_interval_s: float = DEFAULT_TICK_S,
                 poll_interval_s: float = DEFAULT_POLL_S,
                 queue_max: int = DEFAULT_QUEUE_MAX,
                 on_finding: Optional[Callable[[Dict[str, Any]], None]] = None,
                 on_incident: Optional[Callable[[Dict[str, Any]], None]] = None,
                 state_writer: Optional[Callable[[Mapping[str, Any]], Any]] = None,
                 coverage_writer: Optional[Callable[[Mapping[str, Any]], Any]] = None,
                 trace: Optional[store.TraceWriter] = None,
                 notifier: Optional[Any] = None,
                 restore_speed_ring: bool = True,
                 log: Optional[Callable[[str], None]] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 pid: Optional[int] = None,
                 started_ts: Optional[str] = None,
                 unit_profile: Optional[str] = None) -> None:
        self.engine = engine
        self.parser = parser
        self.readers = list(readers)
        self.watcher = watcher
        self.poller = poller
        self.correlator = correlator if correlator is not None else Correlator()
        self.sink = sink
        self.clock = clock
        self._now_us_fn = now_us_fn
        self._wall_clock = wall_clock or (
            lambda: datetime.now(timezone.utc))
        self.tick_interval_s = max(0.05, float(tick_interval_s))
        self.poll_interval_s = max(0.05, float(poll_interval_s))
        self.queue_max = max(1, int(queue_max))
        self.on_finding = on_finding
        self.on_incident = on_incident
        self.state_writer = state_writer
        self.coverage_writer = coverage_writer
        self.trace = trace
        self.notifier = notifier
        self.log = log or (lambda _message: None)
        self.sleep = sleep
        self.pid = pid
        self.started_ts = started_ts or verdict.iso_utc(self._wall_clock())
        self.unit_profile = unit_profile
        self.counters = RunCounters()
        self.emitted: Deque[Dict[str, Any]] = deque(maxlen=256)
        self.resolved_incidents: List[Dict[str, Any]] = []
        self.last_event_ts: Optional[str] = None
        self.notes: List[str] = []
        self._app_proto: Dict[str, str] = {}
        self._last_dropped = 0
        self._server: Optional[ServerContext] = None
        self._initial_server()
        if restore_speed_ring:
            self._restore_speed_ring()

    # -- time ---------------------------------------------------------------

    def now_us(self) -> int:
        if self.clock is not None:
            return self.clock.now_us()
        if self._now_us_fn is not None:
            return int(self._now_us_fn())
        return mono_us()

    def now_s(self) -> float:
        return self.now_us() / MONO_US_PER_S

    def wall_now(self) -> datetime:
        return self._wall_clock()

    def wall_ts(self) -> str:
        return verdict.iso_utc(self.wall_now())

    # -- context bootstrap ---------------------------------------------------

    def _initial_server(self) -> None:
        context = verdict.read_server_context(now=self.wall_now())
        self._server = context if context.present else None
        self.engine.set_server(context if context.present else None)
        self.correlator.set_server(context if context.present else None)
        if context.present and not context.fresh:
            self.notes.append("verdict_stale")

    def _restore_speed_ring(self) -> None:
        """Reload the G-11 ring from ``state.json`` (D-09).

        A restart must not reset the 24 h speed history: ``vpn speed`` runs
        rarely, so losing the ring meant G-11 could never see two low reports
        in a row.  Monotonic time of the restored records is synthesised from
        their wall timestamps relative to now (D-06, §7.1).
        """
        records = store.read_speed_ring()
        if not records:
            return
        restored = self.engine.restore_speed_ring(
            records, now_us=self.now_us(), now_wall=self.wall_now())
        if restored:
            self.notes.append(f"speed_ring_restored:{restored}")

    # -- ingestion -----------------------------------------------------------

    def _ingest_record(self, record: Mapping[str, Any], source: str,
                       *, streaming: bool) -> None:
        if self.parser is None:
            return
        event = self.parser.parse(record, source=source)
        if event is None:
            return
        self._ingest_event(event, streaming=streaming)

    def _ingest_event(self, event: Dict[str, Any],
                      *, streaming: bool) -> None:
        self.counters.events += 1
        if self.trace is not None:
            # SPEC §10.3/§7.3: the trace stores an already redacted event.
            if not self.trace.append(event):
                if "trace_write_failed" not in self.notes:
                    self.notes.append("trace_write_failed")
        proto = event.get("proto")
        signal = event.get("signal")
        if isinstance(proto, str) and signal not in (None, "unparsed"):
            if proto in ("xray", "hysteria2", "amneziawg"):
                self._app_proto[proto] = "ok"
        ts = event.get("ts")
        if isinstance(ts, str):
            self.last_event_ts = ts
        unit = event.get("unit")
        if isinstance(unit, str):
            hint = counters.iface_from_unit(unit)
            if hint and self.poller is not None and self.poller.iface != hint:
                self.poller.resolve_iface(hint)
        outcomes = (self.engine.push(event) if streaming
                    else self.engine.feed(event))
        self._handle(outcomes)
        self._note_self_health()

    def _handle(self, outcomes: Sequence[RuleOutcome]) -> None:
        for outcome in outcomes:
            if outcome.is_finding and outcome.finding is not None:
                finding = outcome.finding
                incident = self.correlator.note(finding, self.now_us())
                self.counters.findings += 1
                if finding.get("severity") == "high":
                    self.counters.high += 1
                if self.sink is not None:
                    try:
                        self.sink.append(finding)
                    except OSError as exc:
                        # disk full: keep the stream alive (§12)
                        note = f"findings_write_failed:{exc.errno}"
                        if note not in self.notes:
                            self.notes.append(note)
                self.emitted.append(finding)
                if self.on_finding is not None:
                    self.on_finding(finding)
                if self.notifier is not None:
                    # opt-in desktop notification (§8.1/§10.4); never raises,
                    # a missing desktop degrades to the journal line above.
                    self.notifier.notify(finding)
                if self.on_incident is not None:
                    self.on_incident(incident)
            elif outcome.status == "suppressed":
                self.counters.suppressed += 1

    def _note_self_health(self) -> None:
        if self.parser is None:
            return
        stats = self.parser.stats
        self.counters.unparsed = stats.unparsed
        self.engine.note_parse_stats(stats.unparsed_ratio, stats.app_lines,
                                     self.now_us())
        new_drops = self.counters.dropped - self._last_dropped
        if new_drops > 0:
            self.engine.note_queue_drops(new_drops, self.now_us())
            self._last_dropped = self.counters.dropped
        for source in self.readers:
            status = getattr(source, "status", "off")
            note = getattr(source, "note", None)
            self.engine.note_source_status(
                str(getattr(source, "source", "reader")), str(status),
                self.now_us(), self.wall_ts())
            if note and note not in self.notes:
                self.notes.append(str(note))

    # -- S3 ------------------------------------------------------------------

    def poll_files(self) -> List[reader.FileChange]:
        if self.watcher is None:
            return []
        changes = self.watcher.poll()
        for change in changes:
            self._note_change(change)
        return changes

    def _note_change(self, change: reader.FileChange) -> None:
        payload = change.payload
        name = change.path.name
        if change.note and change.note.endswith("_parse_error"):
            if change.note not in self.notes:
                self.notes.append(change.note)
        if name == "activity.json":
            active = _activity_active(payload, self.wall_now())
            if active:
                self.engine.note_activity(True, self.now_us())
            return
        if name == "last-speed.json":
            before = len(self.engine.speed_ring())
            self.engine.note_speed_report(payload, self.now_us())
            if len(self.engine.speed_ring()) != before:
                # D-09: persist the refreshed G-11 ring with the report, so a
                # crash right after `vpn speed` does not lose the observation
                self.persist()
            return
        if name == "server-verdict.json":
            context = verdict.context_from_payload(payload, now=self.wall_now())
            self._server = context if context.present else None
            self.engine.set_server(context if context.present else None)
            self.correlator.set_server(context if context.present else None)
            if context.present and not context.fresh:
                if "verdict_stale" not in self.notes:
                    self.notes.append("verdict_stale")
            return
        if change.source == "state" and isinstance(payload, Mapping):
            active = payload.get("active")
            self.engine.note_state(active if isinstance(active, str) else None)
            return
        # repair-*.log lines are context only (SPEC §6.3), never findings.

    def poll_sysfs(self) -> None:
        if self.poller is None:
            return
        sample = self.poller.poll(self.now_s())
        if sample is None:
            gone = bool(self.poller.iface_gone)
            self.engine.note_source_status(
                "sysfs:awg", "lost" if gone else "off", self.now_us(),
                self.wall_ts())
            if gone:
                self.engine.note_awg_gone(self.poller.iface, self.now_us())
            return
        self.engine.note_source_status("sysfs:awg", "ok", self.now_us(),
                                       self.wall_ts())
        window = self.poller.pop_closed_window()
        if window is not None:
            # D-07: exactly one note per closed 120 s window.
            self._handle(self.engine.note_awg_window(window))

    # -- ticker --------------------------------------------------------------

    def tick(self) -> None:
        outcomes: List[RuleOutcome] = []
        outcomes.extend(self.engine.flush())
        outcomes.extend(self.engine.tick(self.now_us()))
        self._handle(outcomes)
        self._note_self_health()
        resolved = self.correlator.tick(self.now_us(), self.wall_ts())
        if resolved:
            self.resolved_incidents.extend(resolved)

    # -- once ---------------------------------------------------------------

    def run_once(self) -> RunCounters:
        """Process the available backfill window, tick once, write artefacts."""
        self.poll_files()
        self.poll_sysfs()
        for source in self.readers:
            source.start()
            for record in source.records():
                self._ingest_record(record, source.source, streaming=False)
        self._note_self_health()
        self.tick()
        self.persist()
        return self.counters

    # -- daemon -------------------------------------------------------------

    def run_forever(self,
                    stop: Optional[threading.Event] = None) -> RunCounters:
        """Streaming loop; returns when *stop* is set (SIGTERM/SIGINT)."""
        stop_event = stop if stop is not None else threading.Event()
        queue_: "queue.Queue[tuple]" = queue.Queue(maxsize=self.queue_max)
        threads = [threading.Thread(target=self._pump, args=(source, queue_,
                                                            stop_event),
                                    name=f"guard-{source.source}", daemon=True)
                   for source in self.readers]
        for thread in threads:
            thread.start()
        self.poll_files()
        self.poll_sysfs()
        next_poll = time.monotonic() + self.poll_interval_s
        next_tick = time.monotonic() + self.tick_interval_s
        next_state = time.monotonic()
        while not stop_event.is_set():
            moment = time.monotonic()
            wait = max(0.02, min(next_poll, next_tick) - moment)
            drained = 0
            try:
                source_name, record = queue_.get(timeout=wait)
            except queue.Empty:
                source_name = record = None
            while record is not None and drained < DRAIN_BATCH:
                self._ingest_record(record, str(source_name), streaming=True)
                drained += 1
                try:
                    source_name, record = queue_.get_nowait()
                except queue.Empty:
                    record = None
            moment = time.monotonic()
            if moment >= next_poll:
                self.poll_files()
                self.poll_sysfs()
                next_poll = moment + self.poll_interval_s
            if moment >= next_tick:
                self.tick()
                next_tick = moment + self.tick_interval_s
            if moment >= next_state:
                self.persist()
                next_state = moment + self.tick_interval_s
        for source in self.readers:
            try:
                source.stop()
            except OSError:
                continue
        for thread in threads:
            thread.join(timeout=2.0)
        self.tick()
        self.persist()
        return self.counters

    def _pump(self, source: Any, queue_: "queue.Queue[tuple]",
              stop: threading.Event) -> None:
        """Reader thread: blocking ``journalctl`` lines → bounded queue."""
        try:
            source.start()
            for record in source.records():
                if stop.is_set():
                    return
                while not stop.is_set():
                    try:
                        queue_.put((source.source, record), timeout=0.5)
                        break
                    except queue.Full:
                        # drop-oldest, then retry: memory stays bounded and the
                        # newest line is not lost behind a stuck consumer
                        try:
                            queue_.get_nowait()
                        except queue.Empty:
                            continue
                        self.counters.dropped += 1
        except (OSError, ValueError, RuntimeError) as exc:
            source.status = "lost"
            source.note = f"reader_error:{type(exc).__name__}"

    # -- artefacts -----------------------------------------------------------

    def state_label(self) -> str:
        if self.poller is not None and self.poller.iface:
            return "armed"
        return self.engine.channel_state()

    def status_payload(self) -> Dict[str, Any]:
        hits = self.counters.redaction_hits
        if self.sink is not None:
            hits += int(getattr(self.sink, "redaction_hits", 0))
        lifecycle = self.engine.lifecycle
        if lifecycle is not None and not isinstance(lifecycle.get("ts"), str):
            lifecycle = None
        return store.build_status(
            running=True,
            since=self.started_ts,
            state=self.state_label(),
            unit_profile=self.unit_profile,
            counters=self.counters.as_dict(),
            redaction_hits=hits,
            last_event_ts=self.last_event_ts,
            last_lifecycle=lifecycle,
            open_incidents=self.correlator.open_incidents(),
        )

    def coverage_payload(self) -> Dict[str, Any]:
        sources: List[Any] = []
        for source in self.readers:
            sources.append(reader.SourceHealth(
                name=str(getattr(source, "source", "reader")),
                status=str(getattr(source, "status", "off")),
                note=getattr(source, "note", None)))
        if not self.readers:
            sources.append(reader.SourceHealth(
                name="journal:user", status="off",
                note="reader не запущен (--once без источников)"))
        sources.append(reader.state_source_health())
        watched = self.watcher.files if self.watcher is not None else {}
        sources.append(reader.file_source_health(watched))
        sources.append(self._verdict_health())
        if self.poller is None:
            sources.append(reader.SourceHealth(
                name="sysfs:awg", status="off", note="awg_counters=false"))
        elif self.poller.iface:
            sources.append(reader.SourceHealth(
                name="sysfs:awg", status="ok",
                note=f"iface={self.poller.iface}"))
        else:
            sources.append(reader.SourceHealth(
                name="sysfs:awg", status="off",
                note="AWG-интерфейс не найден"))
        sources.append(reader.SourceHealth(name="netlink", status="off",
                                           note="v1.1, opt-in"))
        signals: Dict[str, str] = {
            "systemd": "ok" if any(
                getattr(source, "status", "off") == "ok"
                for source in self.readers) else "missing",
            "xray": self._app_proto.get("xray", "blind"),
            "hysteria2": self._app_proto.get("hysteria2", "blind"),
            "amneziawg": ("counters" if self.poller is not None
                          and self.poller.iface else "missing"),
            "dns": "blind",
        }
        notes = list(self.notes)
        stats = self.parser.stats if self.parser is not None else None
        unparsed_ratio = stats.unparsed_ratio if stats is not None else 0.0
        blind: List[str] = []
        if not any(getattr(source, "status", "off") == "ok"
                   for source in self.readers):
            blind.append("user_journal_unreadable")
        if "xray" not in self._app_proto and "hysteria2" not in self._app_proto:
            notes.append("app_signal=blind (нет app-событий за прогон)")
        if self.poller is not None and self.poller.note:
            notes.append(self.poller.note)
        hits = self.counters.redaction_hits
        if self.sink is not None:
            hits += int(getattr(self.sink, "redaction_hits", 0))
        return store.build_coverage(
            sources, signals=signals, unparsed_ratio=unparsed_ratio,
            dropped=self.counters.dropped, blind=blind, notes=notes,
            redaction_hits=hits)

    def _verdict_health(self) -> reader.SourceHealth:
        if self._server is None:
            return reader.SourceHealth(
                name="verdict", status="off",
                note="server-verdict.json нет (vpn guard sync)")
        if not self._server.fresh:
            return reader.SourceHealth(
                name="verdict", status="degraded", note="verdict_stale")
        return reader.SourceHealth(name="verdict", status="ok")

    def persist(self) -> None:
        payload: Dict[str, Any] = dict(self.status_payload())
        ring = self.engine.speed_ring()
        if ring:
            # additive key inside state.json (D-09): `guard.status/1` ignores
            # unknown fields and the schema allows them (ADR-014 §2)
            payload["speed_ring"] = ring
        try:
            if self.state_writer is not None:
                self.state_writer(payload)
            else:
                store.write_state(payload)
        except OSError as exc:
            self.log(f"state_write_failed:{exc}")
        try:
            if self.coverage_writer is not None:
                self.coverage_writer(self.coverage_payload())
            else:
                store.write_coverage(self.coverage_payload())
        except OSError as exc:
            self.log(f"coverage_write_failed:{exc}")


def _activity_active(payload: Optional[Mapping[str, Any]],
                     now: datetime) -> bool:
    if not isinstance(payload, Mapping):
        return False
    until = verdict.parse_timestamp(payload.get("until"))
    if until is None:
        return False
    return until >= now


def build_runner(*, config_root: Optional[Path] = None,
                 since: Optional[str] = None,
                 once: bool = False,
                 sink_path: Optional[Path] = None,
                 clock: Optional[Clock] = None,
                 now_us_fn: Optional[Callable[[], int]] = None,
                 wall_clock: Optional[Callable[[], datetime]] = None,
                 on_finding: Optional[Callable[[Dict[str, Any]], None]] = None,
                 on_incident: Optional[Callable[[Dict[str, Any]], None]] = None,
                 readers: Optional[Sequence[Any]] = None,
                 watcher: Optional[reader.FileWatcher] = None,
                 poller: Optional[counters.AwgCounterPoller] = None,
                 sink: Optional[store.FindingsStore] = None,
                 state_writer: Optional[Callable[[Mapping[str, Any]], Any]] = None,
                 coverage_writer: Optional[Callable[[Mapping[str, Any]], Any]] = None,
                 trace_enabled: bool = False,
                 notifications: bool = True,
                 notifier: Optional[Any] = None,
                 log: Optional[Callable[[str], None]] = None) -> GuardRunner:
    """Wire the production daemon from ``guard.json`` (lazy, no side effects).

    ``readers``/``watcher``/``poller``/``sink`` are injectable so the run loop
    is testable without journald, sysfs or the user's XDG state.
    """
    from .install import load_install_state

    config = store.guard_config(config_root)
    thresholds = Thresholds.from_mapping(config.get("thresholds"))
    sources_cfg = config.get("sources")
    enabled = sources_cfg if isinstance(sources_cfg, dict) else {}
    context = verdict.read_server_context()
    engine = RuleEngine(thresholds=thresholds,
                        server=context if context.present else None)
    correlator = Correlator(server=context if context.present else None)
    parser = parse.JournalParser()
    if readers is None:
        built: List[Any] = []
        if enabled.get("user_journal", True):
            built.append(reader.open_user_reader(since=since,
                                                 follow=not once))
        if enabled.get("system_journal", False):
            built.append(reader.open_system_reader(since=since,
                                                   follow=not once))
        readers = built
    if watcher is None and enabled.get("files", True):
        watcher = reader.open_file_watcher()
    if poller is None and enabled.get("awg_counters", True):
        poller = counters.AwgCounterPoller()
        hint = None
        try:
            from ..mode import read_mode
            stored_iface = read_mode().get("iface")
            hint = stored_iface if isinstance(stored_iface, str) else None
        except Exception:  # noqa: BLE001 - optional deployment artefact
            hint = None
        poller.resolve_iface(hint)
    if sink is None:
        sink = store.FindingsStore(
            path=sink_path, config_root=config_root)
    if notifier is None and notifications:
        notifier = notify_mod.Notifier(config_root=config_root)
    trace = store.TraceWriter() if trace_enabled else None
    install_state = load_install_state()
    return GuardRunner(
        engine=engine, parser=parser, readers=readers or (),
        watcher=watcher, poller=poller, correlator=correlator, sink=sink,
        clock=clock, now_us_fn=now_us_fn, wall_clock=wall_clock,
        on_finding=on_finding, on_incident=on_incident,
        state_writer=state_writer, coverage_writer=coverage_writer,
        trace=trace, notifier=notifier,
        log=log, unit_profile=_profile_of_install(install_state))


def _profile_of_install(state: Optional[Mapping[str, Any]]) -> Optional[str]:
    if isinstance(state, Mapping):
        profile = state.get("profile")
        if profile in ("hardened", "compat"):
            return str(profile)
    return None


__all__ = [
    "DEFAULT_POLL_S",
    "DEFAULT_QUEUE_MAX",
    "DEFAULT_TICK_S",
    "GuardRunner",
    "RunCounters",
    "build_runner",
]
