"""Readers for ``vpn guard`` (WP-G1, SPEC-20261116-vpn-guard §6).

One long-lived ``journalctl -f -o json`` subprocess per journal source
(§5.3): the user journal (S1, primary) and — opt-in, default off — the
system journal (S2) for ``awg-quick@*`` / ``vpn-awg-split@*`` / ``dnsmasq``
lifecycle.  Structured state files (S3: ``state.json``, ``last-speed.json``,
``server-verdict.json``, ``repair-*.log``, ``activity.json``) are watched by
``mtime+size`` with :class:`FileWatcher`; sysfs counters live in
:mod:`vpnm.guard.counters`.

Everything runs without root and without network sockets: the reader only
spawns ``journalctl`` with an argument list (never ``shell=True``), parses
JSON lines, deduplicates by ``__CURSOR`` and persists the cursor so a
restart continues without duplicates (§6.5, AC-11).

When the system journal is unavailable (no membership in
``systemd-journal``/``adm``) the reader reports ``denied`` and, if AWG
units were requested, an honest ``awg_lifecycle_unobservable`` note —
never a sudo suggestion (R-4).  :func:`journal_readable` and the other
``*_source_health`` probes feed ``guard.coverage/1`` for ``vpn guard
sources`` (§6.6).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Deque, Dict, IO, Iterator, List, Mapping, Optional, Tuple

#: The guard unit itself.  Deliberately NOT ``vpnm-guard.service``: that name
#: would collide with profile units (``runners.enabled_vpnm_units`` treats
#: every ``vpnm-*.service`` as a profile; SPEC §10.4).
GUARD_UNIT = "vpn-guard.service"

#: journald fields the reader asks for (smaller pipe, smaller privacy
#: surface).  Old systemd without ``--output-fields`` support retries without
#: the flag and filters in Python (SPEC §6.1).
JOURNAL_FIELDS: Tuple[str, ...] = (
    "MESSAGE",
    "PRIORITY",
    "__REALTIME_TIMESTAMP",
    "__MONOTONIC_TIMESTAMP",
    "__CURSOR",
    "_SYSTEMD_USER_UNIT",
    "_SYSTEMD_UNIT",
    "SYSLOG_IDENTIFIER",
    "_PID",
    "_COMM",
    "UNIT",
    "JOB_TYPE",
    "JOB_RESULT",
    "EXIT_CODE",
    "EXIT_STATUS",
    "MESSAGE_ID",
    "_SYSTEMD_INVOCATION_ID",
    "INVOCATION_ID",
)

#: System units S2 watches (SPEC §6.2: resolvers + AWG lifecycle).  Globs go
#: to ``-u`` verbatim (systemd expands them; one ``-u`` per pattern, no shell).
SYSTEM_UNITS: Tuple[str, ...] = (
    "cloudflared-proxy-dns",
    "zapret",
    "dnsmasq",
    "systemd-resolved",
    "NetworkManager",
    "awg-quick@*",
    "vpn-awg-split@*",
)

#: Glob prefixes that mark an AmneziaWG-related system unit record.
AWG_UNIT_PREFIXES: Tuple[str, ...] = ("awg-quick@", "vpn-awg-split@")

DEFAULT_SINCE = "-2m"
MAX_BACKFILL_S = 24 * 3600  # SPEC §6.5: --since capped at 24h

#: Bounded in-memory cursor dedup (SPEC §7.1 limit family).
DEDUP_MAX = 4096

#: Cursors persisted for cross-restart dedup — smaller than the in-memory
#: ring so ``cursor.json`` stays well inside the state budget (§10.3).
PERSIST_RECENT_MAX = 512

_PERMISSION_MARKERS: Tuple[str, ...] = (
    "insufficient permission",   # journalctl wording
    "permission denied",
    "eacces",
    "eperm",
    "access denied",
)


class BackfillError(ValueError):
    """Raised when the requested backfill window exceeds 24 hours."""


def guard_dir() -> Path:
    """Guard data directory (SPEC §10.3; env-overridable for tests)."""
    env = os.environ.get("VPN_MANAGER_GUARD_DIR")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "vpn-manager" / "guard"


def config_dir() -> Path:
    """``~/.config/vpn-manager`` (env-overridable; mirrors ``runners``)."""
    env = os.environ.get("VPN_MANAGER_CONFIG_DIR")
    if env:
        return Path(env)
    return Path.home() / ".config" / "vpn-manager"


def data_dir() -> Path:
    """``~/.local/share/vpn-manager`` (env-overridable)."""
    env = os.environ.get("VPN_MANAGER_DATA_DIR")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "vpn-manager"


def cursor_path() -> Path:
    return guard_dir() / "cursor.json"


# --- --since parsing / backfill cap ------------------------------------------

_SINCE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_since(text: str) -> int:
    """Duration of a ``--since`` argument like ``-2m`` / ``24h`` / ``90s``.

    Sign is ignored (backfill is always positive); plain integers count as
    seconds.  Raises ``ValueError`` on garbage.
    """
    raw = text.strip().lstrip("+-")
    if not raw:
        raise ValueError(f"empty --since value: {text!r}")
    unit = raw[-1].lower()
    number = raw[:-1] if unit in _SINCE_UNITS else raw
    if not number.isdigit():
        raise ValueError(f"cannot parse --since value: {text!r}")
    value = int(number) * (_SINCE_UNITS.get(unit, 1) if unit in _SINCE_UNITS
                           else 1)
    return value


def validate_backfill(since: str) -> int:
    """Backfill seconds; ``BackfillError`` when the window exceeds 24 h."""
    seconds = parse_since(since)
    if seconds > MAX_BACKFILL_S:
        raise BackfillError(
            f"backfill window {since} exceeds the 24h cap (SPEC §6.5); "
            "use a shorter --since")
    return seconds


# --- command building ---------------------------------------------------------

def journalctl_cmd(*, user: bool = True,
                   cursor: Optional[str] = None,
                   since: Optional[str] = None,
                   units: Tuple[str, ...] = (),
                   output_fields: bool = True,
                   follow: bool = True) -> List[str]:
    """``journalctl`` argument list (never ``shell=True``)."""
    cmd = ["journalctl", "-o", "json"]
    if user:
        cmd.append("--user")
    if follow:
        cmd.append("-f")
    if cursor:
        cmd.append(f"--after-cursor={cursor}")
    if since:
        cmd.append(f"--since={since}")
    for unit in units:
        cmd.extend(["-u", unit])
    if user and output_fields:
        cmd.append("--output-fields=" + ",".join(JOURNAL_FIELDS))
    return cmd


# --- record helpers -----------------------------------------------------------

def record_unit(record: Dict[str, Any]) -> Optional[str]:
    """Systemd unit of a journal record (``_SYSTEMD_USER_UNIT`` or ``UNIT``)."""
    for key in ("_SYSTEMD_USER_UNIT", "UNIT", "_SYSTEMD_UNIT"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def unit_base(unit: str) -> str:
    """``dnsmasq.service`` → ``dnsmasq`` (journald ``UNIT`` keeps the suffix)."""
    return unit[:-len(".service")] if unit.endswith(".service") else unit


def interesting_unit(unit: Optional[str], *, user: bool = True) -> bool:
    """Prefilter done in-process (SPEC §6.1).

    User journal: only ``vpnm-*`` profile units; the guard's own unit is
    excluded by name so the daemon never eats its own log lines.  System
    journal: only the S2 watchlist — the ``-u`` filters already narrow it,
    but the same guard runs when ``--output-fields`` had to be dropped.
    ``UNIT`` carries the ``.service`` suffix, so the watchlist is compared
    against the base name (and AWG instances are matched by prefix).
    """
    if unit is None:
        return False
    if user:
        return unit.startswith("vpnm-") and unit != GUARD_UNIT
    base = unit_base(unit)
    if base.startswith(AWG_UNIT_PREFIXES):
        return True
    return base in SYSTEM_UNITS


def parse_journal_line(line: str) -> Optional[Dict[str, Any]]:
    """One ``-o json`` line → record dict; ``None`` on garbage (no raise)."""
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def realtime_iso(record: Dict[str, Any]) -> Optional[str]:
    """``__REALTIME_TIMESTAMP`` (µs) → RFC3339 UTC with milliseconds."""
    raw = record.get("__REALTIME_TIMESTAMP")
    if not isinstance(raw, str) or not raw.isdigit():
        return None
    seconds, micros = divmod(int(raw), 1_000_000)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds))
    return f"{stamp}.{micros // 1000:03d}Z"


# --- cursor persistence -------------------------------------------------------

def _atomic_write_0600(path: Path, text: str) -> None:
    """Атомарная запись 0600 с УНИКАЛЬНЫМ tmp-именем.

    Два reader-потока (user/system) пишут cursor.json одновременно; общий
    ``cursor.json.tmp`` приводил к гонке: один делает os.replace, второй
    получает ENOENT → ложный ``cursor_write_failed`` (живой WP-G6).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class CursorStore:
    """Persistent per-source cursors (``cursor.json``, 0600, atomic).

    Besides ``<source> -> __CURSOR`` it keeps a bounded list of recently
    emitted cursors under ``<source>:recent`` so a restart that had to fall
    back to ``--since`` (stale cursor after journal rotation) does not
    re-emit events that were already processed (AC-11).
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path if path is not None else cursor_path()

    def load(self) -> Dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        clean: Dict[str, Any] = {}
        for key, value in payload.items():
            if not isinstance(key, str):
                continue
            if isinstance(value, str) and value:
                clean[key] = value
            elif isinstance(value, list):
                recent = [item for item in value
                          if isinstance(item, str) and item]
                if recent:
                    clean[key] = recent
        return clean

    def get(self, source: str) -> Optional[str]:
        cursor = self.load().get(source)
        return cursor if isinstance(cursor, str) else None

    def recent(self, source: str, limit: int = PERSIST_RECENT_MAX) -> List[str]:
        """Recently emitted cursors of *source* (oldest first)."""
        stored = self.load().get(f"{source}:recent")
        if not isinstance(stored, list):
            return []
        items = [item for item in stored if isinstance(item, str) and item]
        return items[-limit:]

    def save(self, source: str, cursor: str,
             recent: Optional[List[str]] = None) -> None:
        if not cursor and not recent:
            return
        state = self.load()
        if cursor:
            state[source] = cursor
        if recent is not None:
            state[f"{source}:recent"] = recent[-PERSIST_RECENT_MAX:]
        _atomic_write_0600(self.path, json.dumps(state, sort_keys=True) + "\n")


# --- reader process -----------------------------------------------------------

#: Reader restart backoff 1→2→4→…→30 s (SPEC §12).
BACKOFF_MAX_S = 30.0


def backoff_delays(count: int) -> List[float]:
    """First *count* backoff delays, capped at 30 s."""
    delays: List[float] = []
    value = 1.0
    for _ in range(count):
        delays.append(min(value, BACKOFF_MAX_S))
        value *= 2
    return delays


def _stderr_text(proc: Any) -> str:
    """Текст stderr из Popen/FakeProc.

    Реальный ``Popen(stderr=PIPE, text=True)`` отдаёт TextIOWrapper, а не str;
    прежний ``stderr.lower()`` падал с AttributeError и убивал reader-поток
    (живой дефект WP-G6).  Читаем файл-объект после завершения процесса.
    """
    stderr = getattr(proc, "stderr", None)
    if stderr is None:
        return ""
    if isinstance(stderr, str):
        return stderr
    if isinstance(stderr, (bytes, bytearray)):
        return bytes(stderr).decode("utf-8", errors="replace")
    try:
        data = stderr.read()
    except (OSError, ValueError):
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data if isinstance(data, str) else str(data)


class _FakeablePopen:
    """Minimal protocol of ``subprocess.Popen`` the reader relies on."""

    stdout: Optional[IO[str]]
    stderr: str
    returncode: int

    def poll(self) -> Optional[int]:  # pragma: no cover - overridden in fakes
        raise NotImplementedError

    def terminate(self) -> None:  # pragma: no cover - overridden in fakes
        raise NotImplementedError


PopenFn = Callable[..., _FakeablePopen]


class JournalReader:
    """One streaming journal source with cursor resume and dedup.

    ``start()`` launches ``journalctl`` (argument list).  When resuming from
    a persisted cursor the process can die immediately (stale cursor after
    journal rotation) — the reader then falls back to ``--since -2m`` with
    cursor dedup so no duplicates are emitted (SPEC §6.5).

    With a :class:`CursorStore` attached the reader persists the last cursor
    (throttled to ``persist_interval_s``, SPEC §6.5/§11.4) plus the recent
    cursors: a restart continues after the last record and a fallback still
    does not re-emit already processed events (AC-11).
    """

    def __init__(self, *, source: str = "journal:user", user: bool = True,
                 cursor: Optional[str] = None, since: Optional[str] = None,
                 units: Tuple[str, ...] = (),
                 popen: Optional[PopenFn] = None,
                 cursors: Optional[CursorStore] = None,
                 persist_interval_s: float = 5.0,
                 cursor_grace_s: float = 0.3,
                 follow: bool = True) -> None:
        self.source = source
        self.user = user
        self.cursor = cursor
        self.since = since
        self.units = units
        self.follow = bool(follow)
        self.cursors = cursors
        self.persist_interval_s = persist_interval_s
        self.cursor_grace_s = cursor_grace_s
        self.status = "off"
        self.note: Optional[str] = None
        self._popen_fn: PopenFn = popen or _real_popen
        self._proc: Optional[_FakeablePopen] = None
        self._seen: set = set()
        self._seen_order: Deque[str] = deque()
        self._persist_at = 0.0
        self.last_cursor: Optional[str] = None
        if self.cursors is not None:
            for known in self.cursors.recent(self.source):
                self._remember(known)

    # -- lifecycle ----------------------------------------------------------

    def _spawn(self, cursor: Optional[str], since: Optional[str]) -> _FakeablePopen:
        cmd = journalctl_cmd(user=self.user, cursor=cursor, since=since,
                             units=self.units, follow=self.follow)
        return self._popen_fn(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)

    def start(self) -> None:
        """Launch the reader; fall back to ``--since -2m`` on a dead cursor."""
        self.status = "ok"
        # SPEC §6.1/§6.5: ``--since`` is the start context, a valid cursor
        # resumes with ``--after-cursor`` only (both together would clip the
        # unprocessed window after a long downtime)
        proc = self._spawn(self.cursor, None if self.cursor else self.since)
        if self.cursor is not None:
            rc = self._await_grace(proc)
            if rc is not None and rc != 0:
                # stale/invalid cursor: restart from a short window instead
                self.cursor = None
                self.since = self.since or DEFAULT_SINCE
                self.note = "cursor_stale_fallback_since"
                proc = self._spawn(None, self.since)
        self._proc = proc

    def _await_grace(self, proc: _FakeablePopen) -> Optional[int]:
        """Let the process prove it is alive; ``None`` means still running."""
        deadline = time.monotonic() + max(self.cursor_grace_s, 0.0)
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        return proc.poll()

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()

    # -- streaming ----------------------------------------------------------

    def _classify_failure(self, stderr: str) -> None:
        lowered = stderr.lower()
        if any(marker in lowered for marker in _PERMISSION_MARKERS):
            self.status = "denied"
            self.note = "нет прав на журнал"
            if (not self.user
                    and any(u.startswith(AWG_UNIT_PREFIXES)
                            for u in self.units)):
                self.note = "awg_lifecycle_unobservable (нет прав на " \
                            "системный журнал; счётчики S3 продолжают работать)"
        else:
            self.status = "lost"
            self.note = f"journalctl exit {self._proc.returncode if self._proc else '?'}"

    def records(self) -> Iterator[Dict[str, Any]]:
        """Yield parsed, deduplicated, unit-filtered journal records."""
        if self._proc is None:
            self.start()
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        emitted_any = False
        for line in proc.stdout:
            record = parse_journal_line(line)
            if record is None:
                continue
            cursor = record.get("__CURSOR")
            cursor_str = cursor if isinstance(cursor, str) and cursor else None
            if not interesting_unit(record_unit(record), user=self.user):
                if cursor_str is not None:
                    # progress cursor even for filtered lines: resuming must
                    # not replay the whole backfill window
                    self.last_cursor = cursor_str
                    self._persist_progress()
                continue
            if cursor_str is not None:
                if cursor_str in self._seen:
                    continue  # duplicate after a cursor fallback / restart
                self._remember(cursor_str)
                self.last_cursor = cursor_str
            emitted_any = True
            self._persist_progress()
            yield record
        rc = proc.poll()
        if rc is None:
            rc = proc.wait()
        self._persist_progress(force=True)
        if rc != 0 and not emitted_any:
            self._classify_failure(_stderr_text(proc))
        elif rc != 0:
            self.status = "lost"

    def _persist_progress(self, force: bool = False) -> None:
        """Save ``last_cursor`` + recent cursors (throttled, SPEC §6.5)."""
        if self.cursors is None or self.last_cursor is None:
            return
        now = time.monotonic()
        if not force and now < self._persist_at:
            return
        self._persist_at = now + self.persist_interval_s
        try:
            self.cursors.save(self.source, self.last_cursor,
                              recent=list(self._seen_order))
        except OSError:
            # read-only home / full disk: the stream continues (§12)
            self.status = "degraded"
            self.note = "cursor_write_failed"

    def _remember(self, cursor: str) -> None:
        if cursor in self._seen:
            return
        self._seen.add(cursor)
        self._seen_order.append(cursor)
        while len(self._seen_order) > DEDUP_MAX:
            old = self._seen_order.popleft()
            self._seen.discard(old)


def _real_popen(cmd: List[str], **kwargs: Any) -> subprocess.Popen:
    """Real ``subprocess.Popen`` (lists only, never ``shell=True``)."""
    return subprocess.Popen(cmd, **kwargs)


def open_user_reader(cursors: Optional[CursorStore] = None,
                     since: Optional[str] = None,
                     popen: Optional[PopenFn] = None,
                     follow: bool = True) -> JournalReader:
    """S1 reader: whole user journal, ``vpnm-*`` filtered in-process."""
    store = cursors if cursors is not None else CursorStore()
    return JournalReader(source="journal:user", user=True,
                         cursor=store.get("journal:user"),
                         since=since or DEFAULT_SINCE,
                         popen=popen, cursors=store, follow=follow)


def open_system_reader(system_journal: bool = True,
                       cursors: Optional[CursorStore] = None,
                       since: Optional[str] = None,
                       popen: Optional[PopenFn] = None,
                       follow: bool = True) -> JournalReader:
    """S2 reader (opt-in): AWG lifecycle + dnsmasq, honest ``denied``."""
    store = cursors if cursors is not None else CursorStore()
    return JournalReader(source="journal:system", user=False,
                         cursor=store.get("journal:system"),
                         since=since or DEFAULT_SINCE,
                         units=SYSTEM_UNITS, popen=popen, cursors=store,
                         follow=follow)


# --- S3: structured state files (read-only, mtime+size) -----------------------

#: ``repair-*.log`` is context only (SPEC §6.3): only action lines are read,
#: never the whole file, and nothing is persisted by the guard.
REPAIR_LINE_PREFIXES: Tuple[str, ...] = ("DNS:", "итог", "Итог")


@dataclass(frozen=True)
class FileChange:
    """One observed change of an S3 file (mtime+size moved on)."""
    source: str                     # state | file | verdict
    path: Path
    payload: Optional[Dict[str, Any]] = None
    lines: Tuple[str, ...] = ()
    note: Optional[str] = None


@dataclass
class _FileMark:
    mtime_ns: int
    size: int


class FileWatcher:
    """Poll S3 files by ``mtime+size`` and emit changes (SPEC §6.3).

    All files are written atomically by the existing code, so a partial read
    is not expected; a broken JSON nonetheless yields ``note=*_parse_error``
    instead of raising.  Text logs (``repair-*.log``) contribute only action
    lines and never become findings by themselves.
    """

    def __init__(self, files: Mapping[Path, str],
                 repair_globs: Tuple[Path, ...] = (),
                 max_repair_lines: int = 20) -> None:
        self.files = dict(files)
        self.repair_globs = tuple(repair_globs)
        self.max_repair_lines = max_repair_lines
        self._marks: Dict[Path, _FileMark] = {}
        self._initialized = False

    def _watch_paths(self) -> List[Path]:
        found: List[Path] = list(self.files)
        for pattern in self.repair_globs:
            try:
                found.extend(sorted(pattern.parent.glob(pattern.name)))
            except OSError:
                continue
        return found

    def poll(self) -> List[FileChange]:
        """Changes since the previous call (first call reports every file)."""
        changes: List[FileChange] = []
        for path in self._watch_paths():
            source = self.files.get(path, "file")
            try:
                stat = path.stat()
            except OSError:
                mark = self._marks.pop(path, None)
                if mark is not None and self._initialized:
                    changes.append(FileChange(source=source, path=path,
                                              note="file_gone"))
                continue
            current = _FileMark(stat.st_mtime_ns, stat.st_size)
            if self._marks.get(path) == current:
                continue
            self._marks[path] = current
            changes.append(self._read_change(source, path))
        self._initialized = True
        return changes

    def _read_change(self, source: str, path: Path) -> FileChange:
        if path.name.endswith(".log"):
            return FileChange(source=source, path=path,
                              lines=self._repair_lines(path))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return FileChange(source=source, path=path,
                              note=f"{source}_parse_error")
        if not isinstance(payload, dict):
            return FileChange(source=source, path=path,
                              note=f"{source}_parse_error")
        return FileChange(source=source, path=path, payload=payload)

    def _repair_lines(self, path: Path) -> Tuple[str, ...]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ()
        interesting = [line.strip() for line in text.splitlines()
                       if line.strip().startswith(REPAIR_LINE_PREFIXES)]
        return tuple(interesting[-self.max_repair_lines:])


def default_file_sources() -> Dict[Path, str]:
    """S3 watch list with the SPEC §6.3 source names."""
    return {
        config_dir() / "state.json": "state",
        data_dir() / "last-speed.json": "file",
        data_dir() / "server-verdict.json": "verdict",
        guard_dir() / "activity.json": "file",
    }


def open_file_watcher() -> FileWatcher:
    """S3 watcher for this user's XDG paths (no root, no network)."""
    return FileWatcher(default_file_sources(),
                       repair_globs=(data_dir() / "logs" / "repair-*.log",))


# --- source health (SPEC §6.6 / R-4) ------------------------------------------

JOURNALCTL = "journalctl"


@dataclass(frozen=True)
class SourceHealth:
    """One ``guard.coverage/1`` source entry (schema-shaped)."""
    name: str
    status: str
    note: Optional[str] = None
    last_event_ts: Optional[str] = None
    lag_ms: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"name": self.name, "status": self.status}
        if self.note is not None:
            payload["note"] = self.note
        if self.last_event_ts is not None:
            payload["last_event_ts"] = self.last_event_ts
        if self.lag_ms is not None:
            payload["lag_ms"] = self.lag_ms
        return payload


def journalctl_available() -> bool:
    """``journalctl`` on PATH (no execution, no network)."""
    return shutil.which(JOURNALCTL) is not None


def journal_readable(*, user: bool, run: Optional["RunCmdLike"] = None) -> SourceHealth:
    """Probe one journal source with a zero-record read (read-only).

    No sudo, no retries, no network: a permission failure is reported as
    ``denied`` with the exact reason and the guard keeps running (R-4).
    """
    name = "journal:user" if user else "journal:system"
    if not journalctl_available():
        return SourceHealth(name=name, status="unavailable",
                            note="journalctl не найден")
    runner = run if run is not None else _run_readonly
    cmd = [JOURNALCTL] + (["--user"] if user else []) + [
        "-n", "0", "--no-pager"]
    rc, _out, err = runner(cmd)
    if rc == 0:
        return SourceHealth(name=name, status="ok")
    lowered = (err or "").lower()
    if any(marker in lowered for marker in _PERMISSION_MARKERS):
        note = "нет прав на журнал"
        if not user:
            note = ("awg_lifecycle_unobservable (нет прав на системный "
                    "журнал; счётчики S3 продолжают работать)")
        return SourceHealth(name=name, status="denied", note=note)
    return SourceHealth(name=name, status="lost",
                        note=f"journalctl exit {rc}"[:200])


RunCmdLike = Callable[[List[str]], Tuple[int, str, str]]


def _run_readonly(cmd: List[str]) -> Tuple[int, str, str]:
    """Run a read-only probe with an argument list (never ``shell=True``)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def state_source_health(path: Optional[Path] = None) -> SourceHealth:
    """``state.json`` presence/readability (grouping key for FP-guards)."""
    target = path if path is not None else config_dir() / "state.json"
    if not target.exists():
        return SourceHealth(name="state", status="degraded",
                            note="state.json отсутствует")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return SourceHealth(name="state", status="degraded",
                            note="state_parse_error")
    return SourceHealth(
        name="state", status="ok" if isinstance(payload, dict) else "degraded")


def file_source_health(paths: Dict[Path, str]) -> SourceHealth:
    """Aggregate S3 health: any readable file → ``ok``, none → ``degraded``."""
    readable = 0
    for path in paths:
        try:
            path.stat()
        except OSError:
            continue
        readable += 1
    if readable:
        return SourceHealth(name="file", status="ok")
    return SourceHealth(name="file", status="degraded",
                        note="нет файлов состояния/отчётов")


def verdict_source_health(path: Optional[Path] = None) -> SourceHealth:
    """``server-verdict.json`` presence; freshness is a WP-G4 merge concern."""
    target = path if path is not None else data_dir() / "server-verdict.json"
    if not target.exists():
        return SourceHealth(name="verdict", status="off",
                            note="server-verdict.json нет (vpn guard sync)")
    try:
        json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return SourceHealth(name="verdict", status="degraded",
                            note="verdict_parse_error")
    return SourceHealth(name="verdict", status="ok")

