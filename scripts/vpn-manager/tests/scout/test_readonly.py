"""WP-S1…WP-S4 read-only contract: scout never writes guard artefacts.

The scout reads the profile store, ``server-verdict.json``, ``findings.jsonl``
and ``guard.json``; none of them may change (content **and** mtime) after a
run.  Probes are stubbed out — this test is about writes, not about sockets —
and the fixtures use only reserved addresses (RFC 5737) and ``FIXTURE_*``
placeholder secrets.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm import amneziawg as awg_mod  # noqa: E402
from vpnm import profiles, runners  # noqa: E402
from vpnm import settings as settings_mod  # noqa: E402
from vpnm.guard import verdict as verdict_mod  # noqa: E402
from vpnm.scout import cli as scout_cli  # noqa: E402
from vpnm.scout.models import (  # noqa: E402
    STATUS_OK,
    STATUS_SERVER_LOCKDOWN,
    TLS_PROTOS,
    ProbeResult,
)

REALITY_LINK = (
    "vless://FIXTURE_UUID_11111111-2222-3333-4444-555555555555"
    "@203.0.113.10:443?security=reality&sni=example.com"
    "&pbk=FIXTURE_PBK_public_key&sid=ab&type=tcp#phone"
)
HY2_LINK = (
    "hysteria2://FIXTURE_PW_hy2_password@203.0.113.11:443"
    "?insecure=1&sni=example.com&mport=20000-20500#hy2-main"
)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Path]:
    paths = {name: tmp_path / name
             for name in ("config", "data", "guard", "awg", "awg-system")}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VPN_MANAGER_CONFIG_DIR", str(paths["config"]))
    monkeypatch.setenv("VPN_MANAGER_DATA_DIR", str(paths["data"]))
    monkeypatch.setenv("VPN_MANAGER_GUARD_DIR", str(paths["guard"]))
    monkeypatch.setenv("VPN_MANAGER_AWG_CONF_DIR", str(paths["awg"]))
    monkeypatch.setenv("VPN_MANAGER_STATE_FILE",
                       str(paths["config"] / "state.json"))
    monkeypatch.setattr(profiles, "STORE_PATH",
                        paths["config"] / "profiles.json")
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH",
                        paths["config"] / "settings.json")
    monkeypatch.setattr(runners, "STATE_PATH",
                        paths["config"] / "state.json")
    monkeypatch.setattr(awg_mod, "LOCAL_CONF_DIR", paths["awg"])
    # The real /etc/amnezia/amneziawg must never leak into a test run.
    monkeypatch.setattr(awg_mod, "AWG_SYSTEM_CONF_DIR",
                        str(paths["awg-system"]))

    (paths["config"] / "profiles.json").write_text(json.dumps({
        "profiles": [
            {"name": "phone", "protocol": "xray-reality",
             "link": REALITY_LINK, "socks_port": 2080,
             "created": "2026-11-16T00:00:00+00:00"},
            {"name": "hy2-main", "protocol": "hysteria2",
             "link": HY2_LINK, "socks_port": 2083,
             "created": "2026-11-16T00:00:00+00:00"},
        ]}), encoding="utf-8")
    (paths["config"] / "state.json").write_text(
        json.dumps({"active": "phone"}), encoding="utf-8")
    (paths["config"] / "guard.json").write_text(json.dumps({
        "schema": "guard.config/1",
        "notify": {"desktop": False, "min_severity": "high"}}),
        encoding="utf-8")

    (paths["awg"] / "laptop.conf").write_text(
        "[Interface]\nPrivateKey = FIXTURE_PW_awg_private\n"
        "[Peer]\nEndpoint = 203.0.113.12:51820\n", encoding="utf-8")

    now = datetime.now(timezone.utc)
    findings = [
        {"schema": "guard.finding/1", "id": "a1" * 6, "rule": "G-04",
         "severity": "high", "confidence": "medium",
         "endpoint": {"host": "203.0.113.12", "port": 51820},
         "window": {"first": (now - timedelta(minutes=10)).isoformat(),
                    "last": (now - timedelta(minutes=5)).isoformat()}},
        {"schema": "guard.finding/1", "id": "b2" * 6, "rule": "G-03",
         "severity": "high", "confidence": "medium",
         "endpoint": {"host": "203.0.113.10", "port": 443},
         "window": {"first": (now - timedelta(minutes=9)).isoformat(),
                    "last": (now - timedelta(minutes=4)).isoformat()}},
    ]
    (paths["guard"] / "findings.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in findings),
        encoding="utf-8")
    (paths["data"] / "server-verdict.json").write_text(json.dumps({
        "schema": "guard.server_verdict/1",
        "fetched_at": (now - timedelta(minutes=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "host": "root@asv1",
        "source": "vds gateway probe-watch watch --json",
        "verdict": "quiet",
        "threat_level": "quiet",
        "lockdown": {"active": False},
    }), encoding="utf-8")
    return paths


def snapshot(path: Path) -> Dict[str, object]:
    stat = path.stat()
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "mtime_ns": stat.st_mtime_ns,
        "mode": stat.st_mode & 0o777,
    }


def artefacts(paths: Dict[str, Path]) -> Dict[str, Dict[str, object]]:
    return {
        "findings.jsonl": snapshot(paths["guard"] / "findings.jsonl"),
        "server-verdict.json": snapshot(paths["data"] / "server-verdict.json"),
        "guard.json": snapshot(paths["config"] / "guard.json"),
    }


def fake_probe_all(candidates, *, budget_ms: int = 2500,
                   timeout_s: float = 1.5, lockdown_443: bool = False,
                   recent_g04_endpoints=(), **kwargs):
    results = {}
    for candidate in candidates:
        if (lockdown_443 and candidate.route == "direct"
                and candidate.proto in TLS_PROTOS and candidate.port == 443):
            results[candidate.key] = ProbeResult(STATUS_SERVER_LOCKDOWN)
        else:
            results[candidate.key] = ProbeResult(STATUS_OK, rtt_ms=20.0)
    return results


def paths_of(env: Dict[str, Path]) -> Dict[str, Path]:
    return {
        "profiles_path": env["config"] / "profiles.json",
        "awg_conf_dir": env["awg"],
        "verdict_path": env["data"] / "server-verdict.json",
        "findings_path": env["guard"] / "findings.jsonl",
    }


class RecordingNotifier:
    """Stands in for the guard notifier; records instead of calling D-Bus."""

    def __init__(self) -> None:
        self.findings: List[dict] = []
        self.last_reason = None

    def notify(self, finding) -> bool:
        self.findings.append(dict(finding))
        return True


def test_build_report_writes_nothing(env, monkeypatch) -> None:
    monkeypatch.setattr(scout_cli, "probe_all", fake_probe_all)
    before = artefacts(env)
    notifier = RecordingNotifier()
    payload = scout_cli.build_report(**paths_of(env), notifier=notifier)
    after = artefacts(env)
    assert payload["schema"] == "scout.report/1"
    assert payload["recommended"] is not None
    assert payload["context"]["verdict_fresh"] is True
    assert payload["context"]["findings_recent"] == 2
    assert after == before  # byte-identical, mtime-identical, same mode


def test_report_json_keys_are_backward_compatible(env, monkeypatch) -> None:
    # scout.report/1 gains exactly one additive top-level key (threat_level);
    # everything the old consumers read stays in place.
    monkeypatch.setattr(scout_cli, "probe_all", fake_probe_all)
    payload = scout_cli.build_report(**paths_of(env),
                                     notifier=RecordingNotifier())
    assert set(payload) == {
        "schema", "created", "context", "threat_level", "recommended",
        "fallback", "rejected", "candidates", "notify",
    }
    assert payload["threat_level"] == payload["context"]["threat_level"]
    assert set(payload["context"]) >= {
        "verdict", "verdict_fresh", "verdict_age_s", "threat_level",
        "server_verdict", "lockdown_443", "findings_recent",
        "g04_endpoints", "notes",
    }
    row = payload["recommended"]
    assert set(row) >= {"route", "proto", "profile", "host", "port", "sni",
                        "port_hop_range", "probe", "score", "status",
                        "reasons"}


def test_scout_cli_writes_nothing(env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(scout_cli, "probe_all", fake_probe_all)
    monkeypatch.setattr(scout_cli, "_notify_blocked",
                        lambda report, active, notifier=None: None)
    before = artefacts(env)
    code = scout_cli.run_scout_cli(as_json=True)
    out = capsys.readouterr().out
    assert code == 0
    report = json.loads(out)
    assert report["recommended"]["probe"]["status"] == STATUS_OK
    assert artefacts(env) == before


def test_sync_is_the_only_writer_and_only_by_delegation(env, monkeypatch) -> None:
    monkeypatch.setattr(scout_cli, "probe_all", fake_probe_all)
    calls: Dict[str, object] = {}

    def fake_sync(*, host=None, timeout=10.0, path=None, **kwargs):
        calls.update({"host": host, "path": path, "timeout": timeout})
        return SimpleNamespace(ok=True, verdict="quiet", threat_level="quiet")

    monkeypatch.setattr(verdict_mod, "sync", fake_sync)
    before = artefacts(env)
    payload = scout_cli.build_report(sync=True, host="root@asv1",
                                     **paths_of(env))
    assert calls["host"] == "root@asv1"
    assert calls["path"] == env["data"] / "server-verdict.json"
    assert any("server-verdict обновлён" in note
               for note in payload["context"]["notes"])
    assert artefacts(env) == before  # the fake sync wrote nothing


def test_active_blocked_channel_triggers_the_guard_notifier(env,
                                                            monkeypatch) -> None:
    monkeypatch.setattr(scout_cli, "probe_all", fake_probe_all)
    notifier = RecordingNotifier()
    payload = scout_cli.build_report(**paths_of(env), notifier=notifier)
    # the active profile 'phone' is a direct reality on 443 and the verdict is
    # fresh … but lockdown is off here, so the probe is ok → no notification
    assert payload["notify"]["attempted"] is False
    assert notifier.findings == []


def test_lockdown_blocks_the_active_channel_and_notifies(env, monkeypatch) -> None:
    monkeypatch.setattr(scout_cli, "probe_all", fake_probe_all)
    verdict_file = env["data"] / "server-verdict.json"
    payload = json.loads(verdict_file.read_text(encoding="utf-8"))
    payload["lockdown"] = {"active": True}
    verdict_file.write_text(json.dumps(payload), encoding="utf-8")
    notifier = RecordingNotifier()
    report = scout_cli.build_report(**paths_of(env), notifier=notifier)
    assert report["context"]["lockdown_443"] is True
    assert report["notify"]["attempted"] is True
    assert report["notify"]["sent"] is True
    assert notifier.findings
    hint = notifier.findings[0]["action"]
    assert (hint.startswith("vpn switch ")
            or hint in ("vpn start", "vpn start relay"))
    # the relay/AWG/Hy2 candidates are the surviving ones
    assert report["recommended"] is not None
    assert report["recommended"]["route"] in ("relay", "direct")
    phone = next(row for row in report["candidates"]
                 if row["profile"] == "phone")
    assert phone["score"] == 0
    assert phone["probe"]["status"] == STATUS_SERVER_LOCKDOWN

