"""Bounded storage for guard findings + command activity markers (WP-G1).

Artefacts (SPEC §10.3), all under ``~/.local/share/vpn-manager/guard/``
(env-overridable via ``VPN_MANAGER_GUARD_DIR`` for tests):

- ``findings.jsonl`` — 0600, rotation 14 days / 20 MB total in 5 × 4 MB
  segments (oldest segment dropped);
- ``trace.ndjson`` — opt-in ``--trace`` ring: 1 h / ≤ 10 MB, redacted before
  the write, one ``.1`` generation (SPEC §10.3);
- ``export`` — anonymised bundle (``manifest.json`` + ``findings.ndjson`` +
  ``coverage.json``, ≤ 5 MB) built by :func:`build_export`;
- ``activity.json`` — best-effort FP-guard marker written by the probe
  commands (``monitor``/``probe``/``speed``): ``{cmd, started, until}``.
  The write must never break the probing command and never change its
  output; every failure is swallowed.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import (Any, Callable, Dict, Iterable, Iterator, List, Mapping,
                    Optional, Sequence, Tuple)

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


#: Documented defaults of ``guard.config/1`` (SPEC §10.3).  ``vpn guard rules``
#: and ``config_hash`` work off the **effective** config = defaults + guard.json,
#: so a fresh install and an explicit file with the same values hash equally.
DEFAULT_CONFIG: Dict[str, Any] = {
    "schema": "guard.config/1",
    "sources": {
        "user_journal": True,
        "system_journal": False,
        "files": True,
        "awg_counters": True,
        "netlink": False,
        "server_verdict": True,
    },
    "notify": {
        "desktop": False,
        "min_severity": "high",
        # WP-FAILOVER §8.1: own opt-in, does NOT inherit notify.desktop
        "failover_desktop": False,
        "telegram": {
            "enabled": False,
            "bot_token_file": "~/.config/vpn-manager/telegram.token",
            "chat_id": "",
            "timeout_sec": 4.0,
        },
    },
    "redact": {"hosts_in_export": True, "snippets": False},
    "retention_days": 14,
    # WP-FAILOVER §7: off by default (invariant I-6, AC-F8)
    "failover": {
        "enabled": False,
        "trigger_profiles": ["asv2-reality"],
        "ladder": ["relay", "asv2-hy2", "asv2-awg", "asv2-cdn"],
        "cooldown_sec": 900,
        "max_switches_per_hour": 3,
        "candidate_cooldown_sec": 600,
        "backoff_sec": [120, 300, 900],
        "pre_flight_timeout_sec": 2.0,
        "post_flight_timeout_sec": 4.0,
        "confirm_lockdown_via_sync": True,
        "kill_switch": False,
    },
}

_CONFIG_BOOL_FLAGS: Mapping[str, Tuple[str, ...]] = {
    "sources": ("system_journal", "files", "awg_counters", "netlink",
                "server_verdict"),
    "redact": ("hosts_in_export", "snippets"),
}

#: Known ladder ids (SPEC §4.1).  An entry outside this set is a typo, not an
#: extensibility point.
FAILOVER_LADDER_IDS: Tuple[str, ...] = (
    "relay", "asv2-hy2", "asv2-awg", "asv2-cdn")


def _clamp_int(value: Any, low: int, high: int) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if low <= value <= high else None


def _clamp_float(value: Any, low: float, high: float) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if low <= float(value) <= high else None


def _valid_ladder(value: Any) -> Optional[List[str]]:
    """``ladder`` = subset of known ids, non-empty, order preserved."""
    if not isinstance(value, (list, tuple)):
        return None
    seen: List[str] = []
    for item in value:
        if not isinstance(item, str) or item not in FAILOVER_LADDER_IDS:
            return None  # unknown id = typo: reject the whole block
        if item not in seen:
            seen.append(item)
    return seen or None


def _valid_backoff(value: Any) -> Optional[List[int]]:
    """Increasing sequence, ≤3 elements, each 30…3600 (SPEC §7)."""
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 3:
        return None
    steps: List[int] = []
    for item in value:
        step = _clamp_int(item, 30, 3600)
        if step is None:
            return None
        steps.append(step)
    if any(later <= earlier for earlier, later in zip(steps, steps[1:])):
        return None
    return steps


def _failover_block(raw: Any) -> Dict[str, Any]:
    """Validated ``failover`` section over the documented defaults.

    Only known keys are accepted; a value outside its range keeps the default
    for that key (a typo must not silently change behaviour, SPEC §10.3).
    ``trigger_profiles`` is **not** validated against the profile store on
    purpose: a profile may legitimately be imported later (SPEC §7).
    """
    block = copy.deepcopy(DEFAULT_CONFIG["failover"])
    if not isinstance(raw, Mapping):
        return block
    for flag in ("enabled", "confirm_lockdown_via_sync", "kill_switch"):
        if isinstance(raw.get(flag), bool):
            block[flag] = bool(raw[flag])
    cooldown = _clamp_int(raw.get("cooldown_sec"), 60, 7200)
    if cooldown is not None:
        block["cooldown_sec"] = cooldown
    max_switches = _clamp_int(raw.get("max_switches_per_hour"), 1, 20)
    if max_switches is not None:
        block["max_switches_per_hour"] = max_switches
    candidate = _clamp_int(raw.get("candidate_cooldown_sec"), 60, 3600)
    if candidate is not None:
        block["candidate_cooldown_sec"] = candidate
    backoff = _valid_backoff(raw.get("backoff_sec"))
    if backoff is not None:
        block["backoff_sec"] = backoff
    ladder = _valid_ladder(raw.get("ladder"))
    if ladder is not None:
        block["ladder"] = ladder
    for timeout_key in ("pre_flight_timeout_sec", "post_flight_timeout_sec"):
        timeout = _clamp_float(raw.get(timeout_key), 0.5, 10.0)
        if timeout is not None:
            block[timeout_key] = timeout
    profiles = raw.get("trigger_profiles")
    if isinstance(profiles, (list, tuple)):
        names = [str(item) for item in profiles
                 if isinstance(item, str) and item.strip()]
        if names:
            block["trigger_profiles"] = names
    return block


def _notify_block(raw: Any) -> Dict[str, Any]:
    """Validated ``notify`` section over the documented defaults.

    ``failover_desktop`` is an independent opt-in (SPEC §8.1): it is never
    derived from ``desktop``.  ``telegram`` keeps the token **path**, never
    the token itself.
    """
    from . import events

    block = copy.deepcopy(DEFAULT_CONFIG["notify"])
    if not isinstance(raw, Mapping):
        return block
    if isinstance(raw.get("desktop"), bool):
        block["desktop"] = bool(raw["desktop"])
    if raw.get("min_severity") in events.SEVERITIES:
        block["min_severity"] = str(raw["min_severity"])
    if isinstance(raw.get("failover_desktop"), bool):
        block["failover_desktop"] = bool(raw["failover_desktop"])
    telegram = raw.get("telegram")
    if isinstance(telegram, Mapping):
        if isinstance(telegram.get("enabled"), bool):
            block["telegram"]["enabled"] = bool(telegram["enabled"])
        token_file = telegram.get("bot_token_file")
        if isinstance(token_file, str) and token_file.strip():
            block["telegram"]["bot_token_file"] = token_file.strip()
        chat_id = telegram.get("chat_id")
        if isinstance(chat_id, str):
            block["telegram"]["chat_id"] = chat_id.strip()[:64]
        timeout = _clamp_float(telegram.get("timeout_sec"), 0.5, 10.0)
        if timeout is not None:
            block["telegram"]["timeout_sec"] = timeout
    return block


def effective_config(config_root: Optional[Path] = None) -> Dict[str, Any]:
    """Defaults + ``guard.json``, validated field by field (no user regex).

    Unknown keys and garbage values are ignored: the guard config surface is
    fixed by SPEC §10.3 (OQ-13) and a typo must not silently change behaviour.
    Only known booleans/enums/retention are accepted; thresholds are parsed
    through :meth:`vpnm.guard.rules.Thresholds.from_mapping`.
    ``notify`` and ``failover`` blocks go through their own validators
    (:func:`_notify_block` / :func:`_failover_block`).
    """
    from .rules import Thresholds

    raw = guard_config(config_root)
    payload: Dict[str, Any] = {
        "schema": DEFAULT_CONFIG["schema"],
        "sources": dict(DEFAULT_CONFIG["sources"]),
        "notify": _notify_block(raw.get("notify")),
        "redact": dict(DEFAULT_CONFIG["redact"]),
        "retention_days": DEFAULT_CONFIG["retention_days"],
        "failover": _failover_block(raw.get("failover")),
    }
    for block, flags in _CONFIG_BOOL_FLAGS.items():
        given = raw.get(block)
        if not isinstance(given, Mapping):
            continue
        for flag in flags:
            if isinstance(given.get(flag), bool):
                payload[block][flag] = bool(given[flag])
    retention = raw.get("retention_days")
    if (isinstance(retention, int) and not isinstance(retention, bool)
            and 1 <= retention <= 90):
        payload["retention_days"] = retention
    payload["thresholds"] = Thresholds.from_mapping(
        raw.get("thresholds")).as_dict()
    return payload


def config_hash(config_root: Optional[Path] = None) -> str:
    """``sha256:<64 hex>`` of the effective ``guard.config/1`` payload.

    Deterministic: same config → same hash; the config holds thresholds and
    flags only, so the hash can never encode a secret (SPEC §10.2, AC-8).
    """
    from .events import payload_hash
    return payload_hash(effective_config(config_root))


def effective_thresholds(config_root: Optional[Path] = None) -> Any:
    """Typed thresholds of the effective config (``guard.json`` over defaults)."""
    from .rules import Thresholds
    return Thresholds.from_mapping(guard_config(config_root).get("thresholds"))


def rules_hash(config_root: Optional[Path] = None) -> str:
    """Effective ``rules_hash`` (catalogue + effective thresholds, §10.2)."""
    from .rules import rules_hash as _rules_hash
    return _rules_hash(effective_thresholds(config_root))


def notify_settings(config_root: Optional[Path] = None) -> Tuple[bool, str]:
    """``(desktop, min_severity)`` of the effective config (opt-in, OQ-5)."""
    config = effective_config(config_root)
    notify = config.get("notify")
    if not isinstance(notify, Mapping):
        return False, "high"
    return bool(notify.get("desktop")), str(notify.get("min_severity", "high"))


def redact_snippets(config_root: Optional[Path] = None) -> bool:
    """``redact.snippets`` from the user config — **off** by default (OQ-9)."""
    redact_cfg = guard_config(config_root).get("redact")
    return isinstance(redact_cfg, dict) and bool(redact_cfg.get("snippets"))


# --- failover / notification settings (SPEC-20261118 §7/§8) --------------------

def failover_settings(config_root: Optional[Path] = None) -> Dict[str, Any]:
    """Effective, validated ``failover`` block (defaults + ``guard.json``)."""
    config = effective_config(config_root)
    block = config.get("failover")
    return dict(block) if isinstance(block, Mapping) else _failover_block(None)


def failover_enabled(config_root: Optional[Path] = None) -> bool:
    """``failover.enabled`` — **false** unless explicitly opted in (I-6)."""
    return bool(failover_settings(config_root).get("enabled"))


def failover_desktop_enabled(config_root: Optional[Path] = None) -> bool:
    """``notify.failover_desktop`` — independent opt-in (SPEC §8.1)."""
    config = effective_config(config_root)
    notify = config.get("notify")
    return (isinstance(notify, Mapping)
            and bool(notify.get("failover_desktop")))


def telegram_settings(config_root: Optional[Path] = None) -> Dict[str, Any]:
    """``notify.telegram`` block; only the **token path**, never the token."""
    config = effective_config(config_root)
    notify = config.get("notify")
    if not isinstance(notify, Mapping):
        return copy.deepcopy(DEFAULT_CONFIG["notify"]["telegram"])
    block = notify.get("telegram")
    return (copy.deepcopy(block) if isinstance(block, Mapping)
            else copy.deepcopy(DEFAULT_CONFIG["notify"]["telegram"]))


def set_config_value(section: str, key: str, value: Any,
                     config_root: Optional[Path] = None) -> Path:
    """Merge one value into ``guard.json`` (0600, atomic) and return the path.

    Only used by the ``vpn guard failover enable|disable`` surface: the rest
    of ``guard.json`` is preserved verbatim.  The file stays a plain mapping;
    :func:`effective_config` continues to ignore unknown keys.
    """
    root = config_root if config_root is not None else config_dir()
    target = root / "guard.json"
    payload: Dict[str, Any] = {}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(loaded, Mapping):
            payload = dict(loaded)
    except (OSError, json.JSONDecodeError, ValueError):
        payload = {}
    block = payload.get(section)
    merged = dict(block) if isinstance(block, Mapping) else {}
    merged[key] = value
    payload[section] = merged
    atomic_write_0600(target, json.dumps(payload, ensure_ascii=False,
                                         indent=2, sort_keys=True) + "\n")
    return target


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
    # SPEC-20261118 §3.2: a manual stop is a deliberate choice, not a fault —
    # the failover agent must stay quiet for 5 minutes after it.
    "down": 300,
    "reset": 300,
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
        os.chmod(self.path.parent, 0o700)
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

    @staticmethod
    def _record_ts(payload: Mapping[str, Any]) -> str:
        """Chronology key of one finding: ``window.last``, else ``ts``.

        ISO-8601 UTC strings of the same shape compare lexicographically, so a
        plain string sort is enough; a record without any timestamp sorts
        first (oldest) instead of inventing a position.
        """
        window = payload.get("window")
        if isinstance(window, Mapping):
            last = window.get("last")
            if isinstance(last, str) and last:
                return last
        ts = payload.get("ts")
        return ts if isinstance(ts, str) else ""

    def read(self, since_ts: Optional[float] = None,
             limit: int = 50) -> List[dict]:
        """Findings newest-first, filtered by ``window.last``/``ts``.

        Ordering is **strict chronology**, not file order: a batch is written
        in whatever order the rules fired (WP-G6 field checkpoint:
        ``vpn guard findings`` showed a triple G-09 out of time order) and
        rotation spreads one window across segments.  Ties on the timestamp
        keep insertion order (the later-appended record comes first), which
        makes the output stable and reproducible.
        """
        found: List[Tuple[str, int, dict]] = []
        # Insertion sequence in append order: oldest segment first, then lines
        # as written — this is the tie-breaker of the sort below.
        sequence = 0
        for segment in reversed(self.segment_paths()):
            try:
                lines = segment.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    payload = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                sequence += 1
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
                found.append((self._record_ts(payload), sequence, payload))
        found.sort(key=lambda item: (item[0], item[1]))  # oldest first, stable
        ordered = [item[2] for item in found]
        ordered.reverse()  # newest first; ties → later insertion first
        return ordered[:limit] if limit > 0 else ordered


# --- failover artefacts (SPEC-20261118 §9) -------------------------------------
#
# Three bounded files under the 0700 guard directory, written only through
# this module (atomic 0600 + schema validation), so ``selftest`` and
# ``schema_hashes()`` stay meaningful:
#
#   failover_state.json      — pin/cooldown/backoff, ≤ 16 KB
#   failover_history.jsonl   — attempts/outcomes, 5 × 4 MB / 14 days
#   pending_alerts.jsonl     — undelivered Telegram, FIFO ≤ 50 / 1 MB

FAILOVER_STATE_MAX_BYTES = 16 * 1024
PENDING_ALERTS_MAX_RECORDS = 50
PENDING_ALERTS_MAX_BYTES = 1024 * 1024


def failover_state_path() -> Path:
    return guard_dir() / "failover_state.json"


def failover_history_path() -> Path:
    return guard_dir() / "failover_history.jsonl"


def pending_alerts_path() -> Path:
    return guard_dir() / "pending_alerts.jsonl"


def empty_failover_state() -> Dict[str, Any]:
    """The documented neutral state (no pin, no cooldown, no backoff)."""
    from . import events

    return {
        "schema": events.FAILOVER_STATE_SCHEMA,
        "updated": "",
        "pin": {"profile": None, "since": None, "reason": None,
                "lockdown_active": False},
        "last_switch_ts": None,
        "last_switch_mono_ns": None,
        "switches": [],
        "candidate_cooldown": {},
        "backoff": {"count": 0, "until": None},
    }


def read_failover_state(path: Optional[Path] = None
                        ) -> Dict[str, Any]:
    """Read ``failover_state.json``; a missing/broken file is neutral state."""
    target = path if path is not None else failover_state_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return empty_failover_state()
    if not isinstance(payload, Mapping):
        return empty_failover_state()
    return dict(payload)


def write_failover_state(payload: Mapping[str, Any],
                         path: Optional[Path] = None, *,
                         config_root: Optional[Path] = None) -> Path:
    """Validate, sanitize and persist the state (0600, ≤ 16 KB)."""
    from . import events, schema

    clean, _hits = sanitize(payload, snippets=False,
                            config_root=config_root)
    errors = schema.validate(clean, events.FAILOVER_STATE_SCHEMA)
    if errors:
        raise ValueError("invalid guard.failover_state/1: "
                         + "; ".join(errors))
    text = json.dumps(clean, ensure_ascii=False, sort_keys=True) + "\n"
    encoded = text.encode("utf-8")
    if len(encoded) > FAILOVER_STATE_MAX_BYTES:
        raise ValueError(
            f"guard.failover_state/1 exceeds {FAILOVER_STATE_MAX_BYTES} bytes")
    target = path if path is not None else failover_state_path()
    atomic_write_0600(target, text)
    return target


class FailoverHistory(FindingsStore):
    """``failover_history.jsonl``: same rotation budget as findings.

    Reuses :class:`FindingsStore` rotation/segment logic (5 × 4 MB / 14 days)
    but validates each record against ``guard.failover_history/1`` and does
    not add a ``redaction_hits`` counter — a history line is an outcome, not
    a finding.
    """

    def __init__(self, path: Optional[Path] = None, *,
                 segment_bytes: int = SEGMENT_BYTES,
                 max_segments: int = MAX_SEGMENTS,
                 max_age_days: int = MAX_AGE_DAYS,
                 now_fn: float = time.time) -> None:
        super().__init__(path if path is not None else failover_history_path(),
                         segment_bytes=segment_bytes,
                         max_segments=max_segments,
                         max_age_days=max_age_days, now_fn=now_fn)

    def append(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        from . import events, schema

        clean, _hits = sanitize(record, snippets=False)
        errors = schema.validate(clean, events.FAILOVER_HISTORY_SCHEMA)
        if errors:
            raise ValueError("invalid guard.failover_history/1: "
                             + "; ".join(errors))
        line = json.dumps(clean, ensure_ascii=False,
                          sort_keys=True, separators=(",", ":")) + "\n"
        self._rotate_if_needed(len(line.encode("utf-8")))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        os.chmod(self.path, 0o600)
        return clean


class PendingAlerts:
    """FIFO queue of undelivered Telegram notifications (§8.2/§8.3).

    Bounded by **records and bytes**: the oldest line is dropped first, so a
    long offline window never grows the file without limit.
    """

    def __init__(self, path: Optional[Path] = None, *,
                 max_records: int = PENDING_ALERTS_MAX_RECORDS,
                 max_bytes: int = PENDING_ALERTS_MAX_BYTES) -> None:
        self.path = path if path is not None else pending_alerts_path()
        self.max_records = max(1, int(max_records))
        self.max_bytes = max(1024, int(max_bytes))

    def records(self) -> List[Dict[str, Any]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        items: List[Dict[str, Any]] = []
        for line in lines:
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(payload, Mapping):
                items.append(dict(payload))
        return items

    def enqueue(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        """Append one record, then enforce the FIFO record/byte budget."""
        from . import events, schema

        clean, _hits = sanitize(dict(record), snippets=False)
        errors = schema.validate(clean, events.PENDING_ALERTS_SCHEMA)
        if errors:
            raise ValueError("invalid guard.pending_alerts/1: "
                             + "; ".join(errors))
        items = self.records()
        items.append(clean)
        self._rewrite(items)
        return clean

    def replace(self, items: Sequence[Mapping[str, Any]]) -> None:
        """Atomically rewrite the queue (used by flush)."""
        self._rewrite([dict(item) for item in items])

    def _rewrite(self, items: List[Dict[str, Any]]) -> None:
        trimmed = items[-self.max_records:]
        text = ""
        while trimmed:
            text = "".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")) + "\n"
                for item in trimmed)
            if len(text.encode("utf-8")) <= self.max_bytes:
                break
            trimmed.pop(0)  # oldest first
        atomic_write_0600(self.path, text)


def enqueue_pending_alert(text: str, *, event: str = "notify",
                          path: Optional[Path] = None,
                          now: Optional[str] = None) -> Dict[str, Any]:
    """Queue one redacted notification text (returns the written record)."""
    from . import events
    from .redact import redact_raw

    safe, _hits = redact_raw(text)
    stamp = now or datetime.now(timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    return PendingAlerts(path).enqueue({
        "schema": events.PENDING_ALERTS_SCHEMA,
        "ts": stamp,
        "event": event[:40],
        "text": safe[:4000],
    })


def read_pending_alerts(path: Optional[Path] = None
                        ) -> List[Dict[str, Any]]:
    return PendingAlerts(path).records()


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


def activity_active_cmd(cmd: str, now: Optional[float] = None,
                        path: Optional[Path] = None) -> bool:
    """True when the marker is in force **and** belongs to *cmd*.

    :func:`activity_active` answers "is any probe running"; a manual
    ``vpn down``/``vpn reset`` needs the command-scoped answer so a running
    ``vpn speed`` cannot be mistaken for a deliberate stop (SPEC §3.2).
    """
    target = path if path is not None else activity_path()
    moment = now if now is not None else time.time()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    if not isinstance(payload, dict) or payload.get("cmd") != cmd:
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


# --- G-11 ring persistence (DECISIONS D-09) -----------------------------------

def read_speed_ring(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """``speed_ring`` mini-records persisted in ``state.json`` (or ``[]``).

    Shape (D-09): ``ts``, ``profile``, ``proto``, ``median_mbps`` — no
    endpoints/URLs/secrets, so the ring is safe to keep across restarts.
    """
    payload = read_state(path)
    if not payload:
        return []
    ring = payload.get("speed_ring")
    if not isinstance(ring, list):
        return []
    return [dict(item) for item in ring if isinstance(item, Mapping)]


# --- trace ring (SPEC §10.3: ``trace.ndjson``, 1 h / ≤ 10 MB) -------------------

TRACE_MAX_BYTES = 10 * 1024 * 1024
TRACE_TTL_S = 3600

#: Event fields kept in a trace line (the contract is add-only: a superset is
#: rejected by nothing, but tracing only contract keys keeps it small).
TRACE_EVENT_KEYS: Tuple[str, ...] = (
    "schema", "ts", "mono_us", "source", "unit", "profile", "proto", "level",
    "signal", "template", "endpoint", "fields", "count",
)


def trace_path() -> Path:
    return guard_dir() / "trace.ndjson"


class TraceWriter:
    """Append-only NDJSON trace with size + age rotation and redaction.

    Every event is passed through :func:`vpnm.guard.redact.sanitize_payload`
    **before** serialisation (SPEC §7.3: "запись только после redact"), so a
    secret that slipped into a free-text field is masked in the trace too.
    The file is 0600 inside the 0700 guard directory; ``--trace`` is opt-in.
    """

    def __init__(self, path: Optional[Path] = None, *,
                 max_bytes: int = TRACE_MAX_BYTES,
                 ttl_s: int = TRACE_TTL_S,
                 now_fn: Optional[Callable[[], float]] = None) -> None:
        self.path = path if path is not None else trace_path()
        self.max_bytes = max(1024, int(max_bytes))
        self.ttl_s = max(0, int(ttl_s))
        self._now_fn = now_fn

    def now(self) -> float:
        return self._now_fn() if self._now_fn is not None else time.time()

    def append(self, event: Mapping[str, Any]) -> bool:
        """Sanitize + append one event; ``False`` on an I/O failure."""
        from .redact import sanitize_payload

        clean, _hits = sanitize_payload(
            {key: event[key] for key in TRACE_EVENT_KEYS if key in event})
        line = json.dumps(clean, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")) + "\n"
        try:
            self._rotate_if_needed(len(line.encode("utf-8")))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            os.chmod(self.path, 0o600)
        except OSError:
            return False
        return True

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if not self.path.exists():
            return
        stat = self.path.stat()
        too_big = stat.st_size + incoming_bytes > self.max_bytes
        too_old = (self.ttl_s > 0
                   and self.now() - stat.st_mtime > self.ttl_s)
        if not too_big and not too_old:
            return
        rotated = self.path.with_name(self.path.name + ".1")
        if rotated.exists():
            rotated.unlink()
        os.replace(self.path, rotated)


# --- export bundle (SPEC §10.1/§11.6, AC-8/AC-13) ------------------------------

EXPORT_MAX_BYTES = 5 * 1024 * 1024
BUNDLE_SCHEMA = "guard.bundle/1"


def build_export(findings: Sequence[Mapping[str, Any]],
                 coverage: Optional[Mapping[str, Any]] = None, *,
                 keep_hosts: bool = False,
                 since: Optional[str] = None,
                 config_root: Optional[Path] = None,
                 created: Optional[str] = None,
                 max_bytes: int = EXPORT_MAX_BYTES) -> Dict[str, Any]:
    """Build the anonymised bundle dict ``guard.bundle/1`` (no raw text).

    - hosts → ``<host>`` unless ``keep_hosts`` (explicit opt-in, §11.6);
    - every finding goes through :func:`redact.export_view` (sanitize + hosts);
    - findings are trimmed oldest-first until the bundle fits ``max_bytes``;
    - the manifest carries hashes (``*_sha256`` keys) and counts only.

    Returns the bundle dict; :func:`export_hits` is the gate the CLI enforces
    **before** anything is written (exit 2, paths only).
    """
    from . import events, schema
    from .redact import export_view, sanitize_payload

    views: List[Dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, Mapping):
            continue
        view, _hits = export_view(finding, keep_hosts=keep_hosts)
        views.append(view)
    # deterministic "newest first" regardless of the caller's order, so the
    # trimming below always drops the oldest findings
    views.sort(key=_export_order, reverse=True)
    clean_coverage: Optional[Dict[str, Any]] = None
    if isinstance(coverage, Mapping):
        clean_coverage, _hits = sanitize_payload(coverage)
    dropped = 0
    while views and _bundle_size(views, clean_coverage, keep_hosts, since,
                                 created) > max_bytes:
        views.pop()  # findings are newest-first: drop the oldest
        dropped += 1
    manifest: Dict[str, Any] = {
        "schema": BUNDLE_SCHEMA,
        "created": created or datetime.now(timezone.utc).replace(
            microsecond=0).isoformat().replace("+00:00", "Z"),
        "since": since,
        "keep_hosts": bool(keep_hosts),
        "findings": len(views),
        "findings_dropped": dropped,
        "coverage_included": clean_coverage is not None,
        "rules_hash": _lazy_rules_hash(config_root),
        "config_hash": config_hash(config_root),
        "findings_sha256": events.payload_hash(views),
        "coverage_sha256": events.payload_hash(clean_coverage),
        "schemas": schema.schema_hashes(),
    }
    return {"manifest": manifest, "findings": views,
            "coverage": clean_coverage}


def _export_order(finding: Mapping[str, Any]) -> str:
    """Sort key: ``window.last`` (newest first), falling back to ``ts``."""
    window = finding.get("window")
    if isinstance(window, Mapping):
        last = window.get("last")
        if isinstance(last, str):
            return last
    ts = finding.get("ts")
    return ts if isinstance(ts, str) else ""


def _lazy_rules_hash(config_root: Optional[Path] = None) -> str:
    """Effective rules hash (rules + effective thresholds)."""
    return rules_hash(config_root)


def _bundle_size(findings: Sequence[Mapping[str, Any]],
                 coverage: Optional[Mapping[str, Any]], keep_hosts: bool,
                 since: Optional[str],
                 created: Optional[str]) -> int:
    """Serialized size estimate of the findings+coverage part of a bundle."""
    body = {"findings": list(findings), "coverage": coverage,
            "keep_hosts": bool(keep_hosts), "since": since,
            "created": created}
    return len(json.dumps(body, ensure_ascii=False, sort_keys=True,
                          default=str).encode("utf-8"))


def export_hits(bundle: Mapping[str, Any]) -> List[str]:
    """Post-scan the built bundle; returns hit paths (never values).

    ``redact.export_gate`` is applied to the very payload that would be
    written, so the allowlist (``*_hash``/``*_sha256``/``sha256:<64 hex>``)
    is exercised end-to-end (AC-13).
    """
    from .redact import export_gate

    findings = bundle.get("findings")
    coverage = bundle.get("coverage")
    code, hits = export_gate(findings if isinstance(findings, list) else [],
                             coverage if isinstance(coverage, Mapping) else None)
    return hits if code == 2 else []


def input_export_hits(findings: Sequence[Mapping[str, Any]],
                      coverage: Optional[Mapping[str, Any]]) -> List[str]:
    """Gate over the **source** findings/coverage, before anonymisation.

    Sanitisation normally removes a secret earlier, but AC-13 is explicit: a
    real ``FIXTURE_PW_*`` in a written artefact must *block* the export with
    exit 2 instead of being silently masked — a leak already on disk is an
    incident, not something to prettify.  Returns paths only.
    """
    from .redact import export_gate

    code, hits = export_gate(findings, coverage)
    return hits if code == 2 else []


def write_export(bundle: Mapping[str, Any],
                 target: Optional[Path] = None) -> Tuple[Path, int]:
    """Write the bundle: ``*.json`` → one file, otherwise a directory.

    Directory layout follows SPEC §10.1: ``manifest.json``,
    ``findings.ndjson`` and ``coverage.json`` (all 0600).  Returns the path
    touched and the byte size actually written.
    """
    destination = target if target is not None else (
        guard_dir() / "export")
    manifest = bundle.get("manifest") or {}
    findings = bundle.get("findings") or []
    coverage = bundle.get("coverage")
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2,
                               sort_keys=True, default=str) + "\n"
    findings_text = "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        for item in findings)
    coverage_text = ""
    if coverage is not None:
        coverage_text = json.dumps(coverage, ensure_ascii=False, indent=2,
                                   sort_keys=True, default=str) + "\n"
    if destination.suffix.lower() == ".json":
        payload = json.dumps(bundle, ensure_ascii=False, indent=2,
                             sort_keys=True, default=str) + "\n"
        atomic_write_0600(destination, payload)
        return destination, len(payload.encode("utf-8"))
    destination.mkdir(parents=True, exist_ok=True)
    os.chmod(destination, 0o700)
    atomic_write_0600(destination / "manifest.json", manifest_text)
    atomic_write_0600(destination / "findings.ndjson", findings_text)
    if coverage_text:
        atomic_write_0600(destination / "coverage.json", coverage_text)
    total = sum(len(text.encode("utf-8")) for text in
                (manifest_text, findings_text, coverage_text))
    return destination, total
