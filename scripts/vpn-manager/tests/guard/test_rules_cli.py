"""WP-G5 tests: ``vpn guard rules`` catalogue + deterministic hashes (§10.2).

The payload must be stable across calls, cover every rule of the frozen
registry and contain no secrets (thresholds/flags only, OQ-13).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import events, redact, report, rules, schema, store  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPN_MANAGER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VPN_MANAGER_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setenv("VPN_MANAGER_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path


def test_catalogue_covers_the_registry_in_order() -> None:
    catalog = rules.rule_catalog()
    assert [item["id"] for item in catalog] == list(events.RULES)
    for item in catalog:
        assert item["category"] in events.CATEGORIES
        assert item["severity"] in events.SEVERITIES
        assert item["stage"] in events.STAGES
        assert item["name"]
        assert item["summary"]


def test_g12_never_notifies() -> None:
    catalog = {item["id"]: item for item in rules.rule_catalog()}
    assert catalog["G-12"]["notify"] is False
    assert catalog["G-03"].get("notify", True) is True


def test_rules_payload_has_hashes_and_thresholds() -> None:
    payload = report.rules_payload()
    assert len(payload["rules"]) == len(events.RULES)
    assert payload["rules_hash"] == store.rules_hash()
    assert payload["config_hash"] == store.config_hash()
    assert payload["schemas"] == schema.schema_hashes()
    assert payload["thresholds"]["degraded_mbps"] == 2.0
    assert payload["thresholds"]["reset_storm_count"] == 3
    assert payload["config"]["schema"] == "guard.config/1"


def test_hashing_is_deterministic_and_repeatable() -> None:
    first = report.rules_payload()
    second = report.rules_payload()
    assert json.dumps(first, sort_keys=True) == json.dumps(second,
                                                           sort_keys=True)
    for value in (first["rules_hash"], first["config_hash"]):
        assert value.startswith("sha256:") and len(value) == 71


def test_rules_hash_changes_with_thresholds(isolated_xdg: Path) -> None:
    baseline = rules.rules_hash()
    config_dir = isolated_xdg / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "guard.json").write_text(
        json.dumps({"schema": "guard.config/1",
                    "thresholds": {"degraded_mbps": 5.0}}),
        encoding="utf-8")
    effective = store.effective_thresholds()
    assert effective.degraded_mbps == 5.0
    assert rules.rules_hash(effective) != baseline
    assert store.rules_hash() == rules.rules_hash(effective)
    # config_hash moves too, and stays canonical
    assert store.config_hash() != store.config_hash(
        isolated_xdg / "missing-config-root")
    assert store.config_hash().startswith("sha256:")


def test_rules_payload_carries_no_secret(isolated_xdg: Path) -> None:
    config_dir = isolated_xdg / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "guard.json").write_text(
        json.dumps({"schema": "guard.config/1",
                    "sources": {"system_journal": True},
                    "notify": {"desktop": True, "min_severity": "medium"},
                    "retention_days": 30}),
        encoding="utf-8")
    payload = report.rules_payload()
    assert redact.scan_secrets(payload) == []
    assert payload["config"]["sources"]["system_journal"] is True
    assert payload["config"]["notify"]["min_severity"] == "medium"
    assert payload["config"]["retention_days"] == 30


def test_unknown_config_keys_are_ignored(isolated_xdg: Path) -> None:
    config_dir = isolated_xdg / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "guard.json").write_text(
        json.dumps({"schema": "guard.config/1",
                    "sources": {"system_journal": "yes"},
                    "notify": {"min_severity": "catastrophic"},
                    "retention_days": 999,
                    "regex": {"evil": ".*"}}),
        encoding="utf-8")
    config = store.effective_config()
    assert config["sources"]["system_journal"] is False  # not a bool → default
    assert config["notify"]["min_severity"] == "high"
    assert config["retention_days"] == 14
    assert "regex" not in config


def test_render_rules_contains_ids_hashes_and_notify_flag() -> None:
    lines = report.render_rules(report.rules_payload())
    text = "\n".join(lines)
    for rule_id in events.RULES:
        assert rule_id in text
    assert "rules_hash=sha256:" in text
    assert "config_hash=sha256:" in text
    assert "не уведомляет" in text  # G-12 line


def test_render_rules_verbose_prints_effective_config() -> None:
    lines = report.render_rules(report.rules_payload(), verbose=True)
    assert any(line.startswith("effective config: ")
               for line in lines)
