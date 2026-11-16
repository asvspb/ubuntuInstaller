"""WP-G5 tests: anonymised export bundle (AC-8/AC-13, SPEC §10.1/§11.6).

No root, no network, no real XDG state: every path is ``tmp_path`` and
fixtures use RFC 5737 addresses / ``FIXTURE_*`` markers only (AGENTS §6).
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import events, redact, rules, schema, store  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPN_MANAGER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VPN_MANAGER_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setenv("VPN_MANAGER_CONFIG_DIR", str(tmp_path / "config"))


def finding(seconds: int = 0, *, host: str = "203.0.113.10") -> dict:
    return rules.build_finding(
        rule="G-03", unit="vpnm-asv2.service", profile="asv2",
        endpoint={"host": host, "port": 443},
        window_first=f"2026-11-16T12:{seconds % 60:02d}:00.000Z",
        window_last=f"2026-11-16T12:{seconds % 60:02d}:30.000Z", count=3,
        evidence=[{"kind": "signal_count", "signal": "conn_reset", "value": 3}],
        fp_checks=["no auth_fail"], severity="high", confidence="medium",
        category="censorship_suspect", stage_hint="data_phase",
        hypothesis="после handshake данные обрываются",
        action="не рестартить; vpn probe",
        server=None, cross_vantage=False)


def coverage() -> dict:
    return store.build_coverage(
        [{"name": "journal:user", "status": "ok"},
         {"name": "sysfs:awg", "status": "ok", "note": "iface=laptop"}],
        signals={"systemd": "ok", "xray": "blind", "hysteria2": "blind",
                 "amneziawg": "counters", "dns": "blind"},
        unparsed_ratio=0.0, notes=["app_signal=blind"])


def test_export_masks_hosts_by_default() -> None:
    bundle = store.build_export([finding()], coverage())
    assert bundle["findings"][0]["endpoint"]["host"] == "<host>"
    assert bundle["manifest"]["keep_hosts"] is False
    assert bundle["manifest"]["schema"] == "guard.bundle/1"


def test_export_keep_hosts_is_explicit_opt_in() -> None:
    bundle = store.build_export([finding()], coverage(), keep_hosts=True)
    assert bundle["findings"][0]["endpoint"]["host"] == "203.0.113.10"
    assert bundle["manifest"]["keep_hosts"] is True


def test_reference_bundle_passes_post_scanner() -> None:
    """AC-13: schema/rules/config hashes are contract values, not secrets."""
    bundle = store.build_export([finding()], coverage())
    manifest = bundle["manifest"]
    for key in ("rules_hash", "config_hash", "findings_sha256",
                "coverage_sha256"):
        assert manifest[key].startswith("sha256:")
        assert len(manifest[key]) == len("sha256:") + 64
    assert manifest["schemas"] == schema.schema_hashes()
    assert redact.scan_secrets(bundle) == []
    assert store.export_hits(bundle) == []


def test_export_gate_blocks_a_survived_fixture_secret() -> None:
    """A secret that reaches the bundle (contract change regression) → exit 2."""
    leaked = {"findings": [{"hypothesis": "password=FIXTURE_PW_EXPORT",
                            "schema": "guard.finding/1"}],
              "coverage": None}
    hits = store.export_hits(leaked)
    assert hits
    assert all("FIXTURE" not in item for item in hits)  # paths only


def test_export_manifest_carries_no_values_from_scanned_fields() -> None:
    bundle = store.build_export([finding(host="203.0.113.77")], coverage())
    serialized = json.dumps(bundle["manifest"], ensure_ascii=False)
    assert "203.0.113.77" not in serialized
    assert "asv2.example" not in serialized


def test_export_trims_oldest_findings_to_fit_the_budget() -> None:
    items = [finding(seconds) for seconds in range(10)]
    bundle = store.build_export(items, coverage(), keep_hosts=False,
                                max_bytes=2500)
    assert bundle["manifest"]["findings"] < 10
    assert bundle["manifest"]["findings_dropped"] > 0
    # newest findings survive, oldest are dropped
    assert bundle["findings"][0]["window"]["first"].endswith("09:00.000Z")


def test_write_export_directory_layout_and_modes(tmp_path: Path) -> None:
    bundle = store.build_export([finding()], coverage())
    target = tmp_path / "bundle"
    path, size = store.write_export(bundle, target)
    assert path == target and size > 0
    for name in ("manifest.json", "findings.ndjson", "coverage.json"):
        item = target / name
        assert item.is_file()
        assert stat.S_IMODE(item.stat().st_mode) == 0o600
        json.loads(item.read_text(encoding="utf-8"))
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["schema"] == "guard.bundle/1"
    lines = (target / "findings.ndjson").read_text().splitlines()
    assert len(lines) == manifest["findings"]


def test_write_export_single_json_file(tmp_path: Path) -> None:
    bundle = store.build_export([finding()], coverage())
    target = tmp_path / "guard-bundle.json"
    path, _size = store.write_export(bundle, target)
    assert path == target
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["manifest"]["schema"] == "guard.bundle/1"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not (tmp_path / "guard-bundle.json").is_dir()


def test_export_since_filter_uses_findings_window(tmp_path: Path) -> None:
    sink = store.FindingsStore(path=tmp_path / "findings.jsonl")
    old = finding(1)
    old["window"] = {"first": "2020-01-01T00:00:00.000Z",
                     "last": "2020-01-01T00:01:00.000Z"}
    sink.append(old)
    sink.append(finding(10))
    import time
    fresh = sink.read(since_ts=time.time() - 7 * 86400, limit=0)
    assert [item["window"]["first"] for item in fresh] == [
        "2026-11-16T12:10:00.000Z"]


def test_exported_findings_still_validate_as_guard_finding() -> None:
    bundle = store.build_export([finding()], coverage())
    for item in bundle["findings"]:
        assert schema.validate(item, events.FINDING_SCHEMA) == []


def test_export_without_coverage_is_honest(tmp_path: Path) -> None:
    bundle = store.build_export([finding()], None)
    assert bundle["coverage"] is None
    assert bundle["manifest"]["coverage_included"] is False
    target = tmp_path / "bundle"
    store.write_export(bundle, target)
    assert (target / "manifest.json").is_file()
    assert not (target / "coverage.json").exists()


def test_cli_export_blocks_a_secret_already_on_disk(capsys) -> None:
    import typer

    from vpnm.guard import cli as guard_cli

    leaked = finding()
    leaked["hypothesis"] = "password=FIXTURE_PW_CLI"
    path = store.findings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(leaked) + "\n", encoding="utf-8")
    with pytest.raises(typer.Exit) as excinfo:
        guard_cli.export("7d", "-", False)
    assert excinfo.value.exit_code == 2
    captured = capsys.readouterr()
    assert "FIXTURE_PW_CLI" not in captured.out
    assert "FIXTURE_PW_CLI" not in captured.err
    assert "секрет в бандле" in captured.err


def test_cli_export_to_stdout(tmp_path: Path, capsys) -> None:
    import typer

    from vpnm.guard import cli as guard_cli

    sink = store.FindingsStore(path=store.findings_path())
    sink.append(finding())
    with pytest.raises(typer.Exit) as excinfo:
        guard_cli.export("7d", "-", False)
    assert excinfo.value.exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["manifest"]["schema"] == "guard.bundle/1"
    assert payload["manifest"]["findings"] == 1
    assert payload["findings"][0]["endpoint"]["host"] == "<host>"
