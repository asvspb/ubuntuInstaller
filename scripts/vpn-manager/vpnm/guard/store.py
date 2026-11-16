"""Bounded storage for guard findings + command activity markers (WP-G1).

Artefacts (SPEC §10.3), all under ``~/.local/share/vpn-manager/guard/``
(env-overridable via ``VPN_MANAGER_GUARD_DIR`` for tests):

- ``findings.jsonl`` — 0600, rotation 14 days / 20 MB total in 5 × 4 MB
  segments (oldest segment dropped);
- ``activity.json`` — best-effort FP-guard marker written by the probe
  commands (``monitor``/``probe``/``speed``): ``{cmd, started, until}``.
  The write must never break the probing command and never change its
  output; every failure is swallowed.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from .reader import config_dir, guard_dir

SEGMENT_BYTES = 4 * 1024 * 1024      # 5 × 4 MB = 20 MB budget (SPEC §11.5)
MAX_SEGMENTS = 5
MAX_AGE_DAYS = 14

#: Contract limits from ``guard.coverage/1`` / ``guard.status/1`` (SPEC §10.2).
COVERAGE_MAX_SOURCES = 16
COVERAGE_MAX_NOTES = 32
NOTE_MAX_LEN = 200


def guard_config(config_root: Optional[Path] = None) -> Dict[str, Any]:
    """Read ``guard.json`` (thresholds/flags only, no user regex; SPEC §10.3)."""
    root = config_root if config_root is not None else config_dir()
    try:
        payload = json.loads((root / "guard.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def redact_snippets(config_root: Optional[Path] = None) -> bool:
    """``redact.snippets`` from the user config — **off** by default (OQ-9)."""
    redact_cfg = guard_config(config_root).get("redact")
    return isinstance(redact_cfg, dict) and bool(redact_cfg.get("snippets"))


def sanitize(payload: Mapping[str, Any], *,
             snippets: Optional[bool] = None,
             config_root: Optional[Path] = None) -> Tuple[Dict[str, Any], int]:
    """Write-path hook: ``redact.sanitize_payload`` with the configured mode.

    Returns ``(clean_copy, hits)``; the caller writes only ``clean_copy``
    (SPEC §7.3).  Snippets stay off unless ``redact.snippets=true``.
    """
    from .redact import sanitize_payload
    mode = redact_snippets(config_root) if snippets is None else bool(snippets)
    return sanitize_payload(payload, snippets=mode)

#: How long a crashed probe command may keep the FP-guard marker active
#: (a clean exit rewrites ``until`` to the actual end time).  Sized to the
#: slowest realistic run of each command.
ACTIVITY_GRACE_S: dict = {
    "probe": 15 * 60,
    "speed": 30 * 60,
    "monitor": 10 * 60,
}
DEFAULT_ACTIVITY_GRACE_S = 10 * 60


def atomic_write_0600(path: Path, text: str) -> None:
    """Atomic durable write with 0600 perms (dir 0700)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def findings_path() -> Path:
    return guard_dir() / "findings.jsonl"


class FindingsStore:
    """Append-only JSONL with size- and age-based rotation.

    Every finding goes through :func:`sanitize` **before** it can reach the
    file (SPEC §7.3); the number of redaction hits is accumulated in
    :attr:`redaction_hits` for ``status``/``coverage``.
    """

    def __init__(self, path: Optional[Path] = None, *,
                 segment_bytes: int = SEGMENT_BYTES,
                 max_segments: int = MAX_SEGMENTS,
                 max_age_days: int = MAX_AGE_DAYS,
                 now_fn: float = time.time,
                 snippets: Optional[bool] = None,
                 config_root: Optional[Path] = None) -> None:
        self.path = path if path is not None else findings_path()
        self.segment_bytes = segment_bytes
        self.max_segments = max_segments
        self.max_age_days = max_age_days
        self._now_fn = now_fn
        self.snippets = snippets
        self.config_root = config_root
        #: Total redaction hits applied by this store (feeds status/coverage).
        self.redaction_hits = 0

    # --- append/rotate -------------------------------------------------------

    def append(self, finding: Mapping[str, Any]) -> Dict[str, Any]:
        """Sanitize, then write one finding as a JSON line (rotates)."""
        from .redact import sanitize_finding

        mode = (redact_snippets(self.config_root)
                if self.snippets is None else bool(self.snippets))
        clean, hits = sanitize_finding(finding, snippets=mode)
        self.redaction_hits += hits
        line = json.dumps(clean, ensure_ascii=False,
                          sort_keys=True, separators=(",", ":")) + "\n"
        self._rotate_if_needed(len(line.encode("utf-8")))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        os.chmod(self.path, 0o600)
        return clean

    def segment_paths(self) -> List[Path]:
        """Existing segments, newest first (``.jsonl`` then ``.1`` … ``.N``)."""
        if not self.path.exists():
            return []
        paths = [self.path]
        for index in range(1, self.max_segments + 1):
            candidate = self.path.with_name(f"{self.path.name}.{index}")
            if candidate.exists():
                paths.append(candidate)
            else:
                break
        return paths

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if not self.path.exists():
            return
        if self.path.stat().st_size + incoming_bytes <= self.segment_bytes:
            return
        segments = self.segment_paths()
        # drop the oldest beyond the budget
        while len(segments) > self.max_segments - 1:
            segments.pop().unlink()
        # shift .N → .N+1, newest last
        for source in reversed(segments[1:]):
            index = int(source.name.rsplit(".", 1)[-1])
            source.replace(self.path.with_name(f"{self.path.name}.{index + 1}"))
        self.path.replace(self.path.with_name(f"{self.path.name}.1"))
        self.trim_by_age()

    def trim_by_age(self) -> None:
        """Drop whole segments whose newest record is older than the TTL."""
        cutoff = self._now_fn() - self.max_age_days * 86400
        for segment in self.segment_paths():
            newest = self._segment_newest_ts(segment)
            if newest is not None and newest < cutoff:
                segment.unlink()

    def _segment_newest_ts(self, segment: Path) -> Optional[float]:
        newest: Optional[float] = None
        try:
            lines = segment.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in lines[-64:]:
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            ts = payload.get("window", {}).get("last") if isinstance(
                payload.get("window"), dict) else payload.get("ts")
            if isinstance(ts, str):
                try:
                    stamp = datetime.fromisoformat(
                        ts.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
                newest = stamp if newest is None else max(newest, stamp)
        return newest

    # --- read ----------------------------------------------------------------

    def read(self, since_ts: Optional[float] = None,
             limit: int = 50) -> List[dict]:
        """Findings newest-first, filtered by ``window.last``/``ts``."""
        found: List[dict] = []
        for segment in self.segment_paths():
            try:
                lines = segment.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    payload = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if since_ts is not None:
                    ts = None
                    window = payload.get("window")
                    if isinstance(window, dict):
                        ts = window.get("last")
                    ts = ts or payload.get("ts")
                    if isinstance(ts, str):
                        try:
                            stamp = datetime.fromisoformat(
                                ts.replace("Z", "+00:00")).timestamp()
                        except ValueError:
                            stamp = None
                        if stamp is not None and stamp < since_ts:
                            continue
                found.append(payload)
        found.reverse()  # newest first
        return found[:limit] if limit > 0 else found


# --- activity markers (FP-guard, SPEC §8.4) -----------------------------------

def activity_path() -> Path:
    return guard_dir() / "activity.json"


def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, tz=timezone.utc).isoformat()


def mark_activity(cmd: str, path: Optional[Path] = None,
                  grace_s: Optional[int] = None,
                  now: Optional[float] = None) -> bool:
    """Best-effort FP-guard marker write; never raises.

    ``{cmd, started, until}`` — ``until`` covers the estimated worst-case
    run so events during a crash do not un-suppress themselves early; a
    clean exit rewrites ``until`` with the real end time via
    :func:`mark_activity_end`.

    **No guard directory — no marker** (SPEC §8.4: "ошибка/нет каталога —
    молча пропускается").  This keeps the probing commands byte-identical
    when ``vpn guard`` was never installed and keeps the test suite from
    touching the developer's real XDG state.
    """
    target = path if path is not None else activity_path()
    if not target.parent.is_dir():
        return False
    moment = now if now is not None else time.time()
    grace = grace_s if grace_s is not None else ACTIVITY_GRACE_S.get(
        cmd, DEFAULT_ACTIVITY_GRACE_S)
    payload = {"cmd": cmd, "started": _iso(moment),
               "until": _iso(moment + grace)}
    try:
        atomic_write_0600(target, json.dumps(payload) + "\n")
    except OSError:
        return False  # best-effort by contract (SPEC §8.4)
    return True


def mark_activity_end(cmd: str, path: Optional[Path] = None,
                      now: Optional[float] = None) -> bool:
    """Close the marker: ``until = now`` so guard stops suppressing."""
    target = path if path is not None else activity_path()
    moment = now if now is not None else time.time()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    if not isinstance(payload, dict) or payload.get("cmd") != cmd:
        return False
    payload["until"] = _iso(moment)
    try:
        atomic_write_0600(target, json.dumps(payload) + "\n")
    except OSError:
        return False
    return True


def activity_active(now: Optional[float] = None,
                    path: Optional[Path] = None) -> bool:
    """True when a probe marker is currently in force (``until`` in future)."""
    target = path if path is not None else activity_path()
    moment = now if now is not None else time.time()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    until = payload.get("until")
    if not isinstance(until, str):
        return False
    try:
        horizon = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except ValueError:
        return False
    return horizon.timestamp() >= moment


@contextlib.contextmanager
def activity_scope(cmd: str, path: Optional[Path] = None) -> Iterator[None]:
    """Wrap a probe command body: marker on enter, closed on any exit."""
    mark_activity(cmd, path=path)
    try:
        yield
    finally:
        mark_activity_end(cmd, path=path)


def activity_window(cmd: Optional[str] = None,
                    path: Optional[Path] = None) -> Optional[timedelta]:
    """Remaining marker validity (diagnostics for ``guard sources``)."""
    target = path if path is not None else activity_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if cmd is not None and payload.get("cmd") != cmd:
        return None
    until = payload.get("until")
    if not isinstance(until, str):
        return None
    try:
        horizon = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except ValueError:
        return None
    return horizon - datetime.now(timezone.utc)


# --- coverage / status payloads (SPEC §6.6 / §10.2) ---------------------------

def _clip(text: str, limit: int = NOTE_MAX_LEN) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _source_dict(entry: Any) -> Dict[str, Any]:
    """Normalise a ``SourceHealth``/dict into one coverage source entry."""
    if isinstance(entry, dict):
        payload = dict(entry)
    elif hasattr(entry, "as_dict"):
        payload = dict(entry.as_dict())
    else:  # pragma: no cover - programming error, surfaced loudly
        raise TypeError(f"unsupported coverage source: {entry!r}")
    note = payload.get("note")
    if isinstance(note, str):
        payload["note"] = _clip(note)
    return payload


def _dedupe(items: Iterable[str]) -> List[str]:
    seen: set = set()
    ordered: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def build_coverage(sources: Sequence[Any], *,
                   signals: Mapping[str, str],
                   unparsed_ratio: float = 0.0,
                   dropped: int = 0,
                   blind: Sequence[str] = (),
                   notes: Sequence[str] = (),
                   disk_full: bool = False,
                   redaction_hits: int = 0,
                   snippets: Optional[bool] = None,
                   config_root: Optional[Path] = None) -> Dict[str, Any]:
    """Assemble, sanitize and validate a ``guard.coverage/1`` payload.

    Truncation limits are the schema's own (``maxItems``/``maxLength``):
    violating them here raises instead of writing a broken contract file.
    The payload is passed through :func:`sanitize` so ``sources --json``
    cannot print a secret that the written file would have masked (§7.3).
    """
    from . import events, schema

    payload: Dict[str, Any] = {
        "schema": events.COVERAGE_SCHEMA,
        "sources": [_source_dict(entry)
                    for entry in list(sources)[:COVERAGE_MAX_SOURCES]],
        "signals": dict(signals),
        "unparsed_ratio": max(0.0, min(1.0, float(unparsed_ratio))),
        "dropped": max(0, int(dropped)),
        "blind": [_clip(item) for item in _dedupe(blind)][:COVERAGE_MAX_NOTES],
        "notes": [_clip(item)
                  for item in _dedupe(notes)][:COVERAGE_MAX_NOTES],
        "disk_full": bool(disk_full),
        # additive field inside major /1 (ADR-014 §2): redaction accounting
        "redaction_hits": max(0, int(redaction_hits)),
    }
    clean, hits = sanitize(payload, snippets=snippets,
                           config_root=config_root)
    if hits:
        clean["redaction_hits"] = int(clean["redaction_hits"]) + hits
    errors = schema.validate(clean, events.COVERAGE_SCHEMA)
    if errors:
        raise ValueError("invalid guard.coverage/1: " + "; ".join(errors))
    return clean


def coverage_path() -> Path:
    return guard_dir() / "coverage.json"


def write_coverage(payload: Mapping[str, Any],
                   path: Optional[Path] = None, *,
                   snippets: Optional[bool] = None,
                   config_root: Optional[Path] = None) -> Path:
    """Persist ``coverage.json`` atomically (0600, sanitized; SPEC §7.3)."""
    clean, _hits = sanitize(payload, snippets=snippets,
                            config_root=config_root)
    target = path if path is not None else coverage_path()
    atomic_write_0600(target, json.dumps(clean, ensure_ascii=False,
                                         sort_keys=True) + "\n")
    return target


def read_coverage(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    target = path if path is not None else coverage_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def state_path() -> Path:
    """Daemon state file (SPEC §10.3: ``guard/state.json``, ≤ 64 KB)."""
    return guard_dir() / "state.json"


def write_state(payload: Mapping[str, Any],
                path: Optional[Path] = None, *,
                snippets: Optional[bool] = None,
                config_root: Optional[Path] = None,
                accumulate_redaction_hits: bool = True) -> Path:
    """Persist daemon state atomically (0600, sanitized; SPEC §7.3/§11.4).

    ``redaction_hits`` is **cumulative** and survives ``kill -9`` (WP-G3):
    the counter already stored in ``state.json`` is read back, the payload's
    own counter is treated as a floor, and the hits applied by this very
    write are added on top.  A restart therefore never resets the number
    ``vpn guard status`` shows (SPEC §12 «guard перезапущен → state
    восстановлен»).
    """
    target = path if path is not None else state_path()
    prior = 0
    if accumulate_redaction_hits:
        prior = read_redaction_hits(target)
        own = payload.get("redaction_hits")
        if isinstance(own, int) and not isinstance(own, bool):
            prior = max(prior, max(0, own))
    clean, hits = sanitize(payload, snippets=snippets,
                           config_root=config_root)
    if accumulate_redaction_hits:
        clean["redaction_hits"] = prior + int(hits)
    atomic_write_0600(target, json.dumps(clean, ensure_ascii=False,
                                         sort_keys=True) + "\n")
    return target


def read_state(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    target = path if path is not None else state_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def read_redaction_hits(path: Optional[Path] = None) -> int:
    """Cumulative ``redaction_hits`` persisted in ``state.json`` (0 if none).

    Used by the daemon at start-up (it continues counting instead of zeroing
    the counter) and by ``vpn guard status`` after a crash.
    """
    payload = read_state(path)
    if not payload:
        return 0
    value = payload.get("redaction_hits")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def redaction_hits_total(install_hits: int = 0,
                         path: Optional[Path] = None) -> int:
    """Best known cumulative hits: daemon ``state.json`` or install baseline."""
    base = 0
    if isinstance(install_hits, int) and not isinstance(install_hits, bool):
        base = max(0, install_hits)
    return max(read_redaction_hits(path), base)


def coverage_degraded(payload: Mapping[str, Any]) -> bool:
    """``True`` when at least one source is not ``ok``/``off`` (exit code 1)."""
    for entry in payload.get("sources", []):
        if isinstance(entry, dict) and entry.get("status") in (
                "degraded", "denied", "lost", "unavailable"):
            return True
    return False


def build_status(*, running: bool, since: Optional[str] = None,
                 state: Optional[str] = None, pid: Optional[int] = None,
                 unit_profile: Optional[str] = None,
                 counters: Optional[Mapping[str, int]] = None,
                 redaction_hits: int = 0,
                 sources_ref: str = "coverage.json",
                 last_event_ts: Optional[str] = None,
                 last_lifecycle: Optional[Mapping[str, Any]] = None,
                 open_incidents: Sequence[Mapping[str, Any]] = (),
                 ) -> Dict[str, Any]:
    """Assemble and validate a ``guard.status/1`` payload (SPEC §10.2)."""
    from . import events, schema

    counter_payload: Dict[str, int] = {
        "events": 0, "unparsed": 0, "dropped": 0,
        "findings": 0, "suppressed": 0,
        # additive counter inside major /1 (ADR-014 §2)
        "redaction_hits": max(0, int(redaction_hits)),
    }
    if counters:
        counter_payload.update({key: int(value)
                                for key, value in counters.items()})
    payload: Dict[str, Any] = {
        "schema": events.STATUS_SCHEMA,
        "running": bool(running),
        "since": since or datetime.now(timezone.utc).replace(
            microsecond=0).isoformat().replace("+00:00", "Z"),
        "sources_ref": sources_ref,
        "counters": counter_payload,
        "open_incidents": [dict(item) for item in open_incidents],
    }
    if state is not None:
        payload["state"] = state
    if pid is not None:
        payload["pid"] = pid
    if unit_profile is not None:
        payload["unit_profile"] = unit_profile
    if last_event_ts is not None:
        payload["last_event_ts"] = last_event_ts
    if last_lifecycle is not None:
        payload["last_lifecycle"] = dict(last_lifecycle)
    errors = schema.validate(payload, events.STATUS_SCHEMA)
    if errors:
        raise ValueError("invalid guard.status/1: " + "; ".join(errors))
    return payload

