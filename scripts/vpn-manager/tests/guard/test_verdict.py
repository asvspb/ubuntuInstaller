"""WP-G4 tests: server verdict file, freshness and the explicit `sync` (D-06).

No network and no root: ``sync`` is driven by an injected runner, the file
lives in ``tmp_path``.  Fixtures use RFC 5737 addresses only (AGENTS §6).
"""

from __future__ import annotations

import json
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import events, schema, verdict  # noqa: E402

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)
HOST = "root@203.0.113.10"


def fetched(seconds_ago: int = 0) -> str:
    return verdict.iso_utc(NOW - timedelta(seconds=seconds_ago))


def verdict_payload(seconds_ago: int = 0, **overrides: object) -> dict:
    payload = {
        "schema": "guard.server_verdict/1",
        "fetched_at": fetched(seconds_ago),
        "host": HOST,
        "source": verdict.REMOTE_COMMAND,
        "verdict": "RU-PROBE-SUSPECT",
        "threat_level": "high",
        "threat_reason": "2 окна RU-PROBE-SUSPECT",
        "window": "2026-11-16T11:00:00 .. 2026-11-16T12:00:00",
        "stage": None,
        "reasons": ["syn_bare growth"],
        "lockdown": {"active": False, "ttl_until": ""},
        "raw_sha256": "sha256:" + "a" * 64,
    }
    payload.update(overrides)
    return payload


def write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


def server_json(**overrides: object) -> dict:
    payload = {
        "now": "2026-11-16T12:00:00",
        "window": "2026-11-16T11:00:00 .. 2026-11-16T12:00:00",
        "verdict": "RU-PROBE-SUSPECT",
        "reasons": ["syn_bare growth", "syn_valid growth"],
        "threshold": 5,
        "syn_bare": 12,
        "syn_valid": 3,
        "udp_443": 0,
        "top": [{"ip": "203.0.113.5", "packets": 9, "geo": "??"}],
        "new_ru_ips": ["203.0.113.6"],
        "ru_top": [{"ip": "203.0.113.7", "packets": 4, "delta": 4}],
        "journal": [{"source": "x", "pattern": "p", "count": 1,
                     "ips": ["203.0.113.8"]}],
        "threat_level": "high",
        "threat_reason": "2 окна RU-PROBE-SUSPECT",
        "ru_syn_new": ["203.0.113.9"],
        "own_ips_sources": ["awg"],
        "lockdown": {"active": False, "since": "",
                     "ttl_until": "", "allow_from": ["203.0.113.11"]},
    }
    payload.update(overrides)
    return payload


# --- reading / freshness ---------------------------------------------------------

def test_valid_file_is_fresh_with_expected_fields(tmp_path: Path) -> None:
    path = write(tmp_path / "server-verdict.json", verdict_payload(120))
    payload = verdict.load_verdict(path)
    assert payload is not None
    context = verdict.read_server_context(path, now=NOW)
    assert context.present is True
    assert context.fresh is True
    assert context.age_s == 120
    assert context.threat_level == "high"
    assert context.verdict == "RU-PROBE-SUSPECT"
    assert context.stage is None              # optional field is tolerated
    assert context.lockdown is False


def test_freshness_boundary_is_65_minutes(tmp_path: Path) -> None:
    fresh_path = write(tmp_path / "fresh.json", verdict_payload(3900))
    stale_path = write(tmp_path / "stale.json", verdict_payload(3901))
    assert verdict.read_server_context(fresh_path, now=NOW).fresh is True
    stale = verdict.read_server_context(stale_path, now=NOW)
    assert stale.fresh is False               # stale is kept, never boosts
    assert stale.age_s == 3901
    assert stale.present is True


def test_missing_broken_and_invalid_files_degrade_to_not_fresh(
        tmp_path: Path) -> None:
    missing = verdict.read_server_context(tmp_path / "absent.json", now=NOW)
    assert missing.present is False and missing.fresh is False

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert verdict.load_verdict(broken) is None
    assert verdict.read_server_context(broken, now=NOW).fresh is False

    invalid = write(tmp_path / "invalid.json",
                    verdict_payload(schema="guard.event/1"))
    assert verdict.load_verdict(invalid) is None
    assert verdict.read_server_context(invalid, now=NOW).present is False


def test_stage_and_lockdown_are_read_when_present(tmp_path: Path) -> None:
    path = write(tmp_path / "v.json", verdict_payload(
        stage="data_phase",
        lockdown={"active": True, "ttl_until": "2026-11-16T13:00:00Z"}))
    context = verdict.read_server_context(path, now=NOW)
    assert context.stage == "data_phase"
    assert context.lockdown is True
    assert context.lockdown_expected is True


def test_age_seconds_handles_garbage() -> None:
    assert verdict.age_seconds(None, now=NOW) is None
    assert verdict.age_seconds("not-a-date", now=NOW) is None
    assert verdict.age_seconds(fetched(5), now=NOW) == 5


# --- sync ------------------------------------------------------------------------

def test_sync_success_is_read_only_and_allowlisted(tmp_path: Path) -> None:
    target = tmp_path / "server-verdict.json"
    calls: list = []

    def runner(cmd: list, timeout: float):
        calls.append((list(cmd), timeout))
        return 0, json.dumps(server_json()), ""

    result = verdict.sync(host=HOST, timeout=10, path=target,
                          run=runner, now=NOW)
    assert result.ok is True
    assert result.note is None
    assert result.threat_level == "high"
    assert result.context.fresh is True
    assert len(calls) == 1                    # exactly one ssh invocation
    cmd, timeout = calls[0]
    assert cmd[0] == "ssh"
    assert cmd[-1] == verdict.REMOTE_COMMAND
    assert "--record" not in " ".join(cmd)
    assert "vds gateway probe-watch watch --json" in cmd[-1]
    assert timeout == 10.0

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert schema.validate(payload, events.SERVER_VERDICT_SCHEMA) == []
    assert payload["fetched_at"] == verdict.iso_utc(NOW)
    # third-party IP lists are dropped by construction (§13.2)
    assert "top" not in payload and "ru_top" not in payload
    assert "journal" not in payload and "new_ru_ips" not in payload
    assert "allow_from" not in payload["lockdown"]
    assert payload["raw_sha256"].startswith("sha256:")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_sync_failure_keeps_the_old_file(tmp_path: Path) -> None:
    target = write(tmp_path / "server-verdict.json", verdict_payload(60))
    before = target.read_bytes()

    def failing(cmd: list, timeout: float):
        return 255, "", "Permission denied (publickey)."

    result = verdict.sync(host=HOST, path=target, run=failing, now=NOW)
    assert result.ok is False
    assert result.note == "verdict_stale"
    assert "ssh exit 255" in (result.error or "")
    assert target.read_bytes() == before


def test_sync_timeout_and_garbage_json_are_stale(tmp_path: Path) -> None:
    target = tmp_path / "server-verdict.json"

    def timeout_runner(cmd: list, timeout: float):
        return 124, "", "ssh timeout"

    broken = verdict.sync(host=HOST, path=target, run=timeout_runner, now=NOW)
    assert broken.ok is False and broken.note == "verdict_stale"

    def garbage(cmd: list, timeout: float):
        return 0, "not json", ""

    bad = verdict.sync(host=HOST, path=target, run=garbage, now=NOW)
    assert bad.ok is False and bad.note == "verdict_stale"
    assert not target.exists()                # nothing written on failure


def test_sync_without_host_reports_stale(tmp_path: Path) -> None:
    target = tmp_path / "server-verdict.json"
    result = verdict.sync(host=None, path=target,
                          settings=tmp_path / "absent.json", now=NOW)
    assert result.ok is False
    assert result.note == "verdict_stale"
    assert "host" in (result.error or "").lower()


def test_sync_default_host_comes_from_settings(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"ssh_host": HOST}), encoding="utf-8")
    target = tmp_path / "server-verdict.json"
    seen: list = []

    def runner(cmd: list, timeout: float):
        seen.append(cmd)
        return 0, json.dumps(server_json()), ""

    result = verdict.sync(host=None, path=target, settings=settings,
                          run=runner, now=NOW)
    assert result.ok is True
    assert seen[0][-2] == HOST                # host precedes the command


def test_sync_rejects_payload_without_verdict_or_threat(tmp_path: Path) -> None:
    target = tmp_path / "server-verdict.json"

    def runner(cmd: list, timeout: float):
        return 0, json.dumps({"now": "x", "reasons": []}), ""

    result = verdict.sync(host=HOST, path=target, run=runner, now=NOW)
    assert result.ok is False and result.note == "verdict_stale"
    assert not target.exists()


def test_ssh_command_has_no_record_and_no_shell() -> None:
    cmd = verdict.ssh_command(HOST, 7)
    assert cmd == ["ssh", "-o", "BatchMode=yes",
                   "-o", "ConnectTimeout=7", HOST, verdict.REMOTE_COMMAND]
    assert "--record" not in " ".join(cmd)
    assert ";" not in " ".join(cmd)
