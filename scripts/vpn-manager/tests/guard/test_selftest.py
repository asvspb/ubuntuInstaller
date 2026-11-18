"""WP-G5 tests: ``vpn guard selftest`` (AC-12, SPEC §5.4/§10.1).

The checks run against the **installed package layout** (``vpnm/guard`` on
``sys.path``): no journald, no root, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import cli, parse, redact, schema  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPN_MANAGER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VPN_MANAGER_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setenv("VPN_MANAGER_CONFIG_DIR", str(tmp_path / "config"))


def _checks_by_name(checks):
    return {item["check"]: item for item in checks}


def test_selftest_is_green_on_the_package() -> None:
    ok, checks = cli.run_selftest()
    named = _checks_by_name(checks)
    assert set(named) == {"schemas", "parser", "rules", "post-scanner"}
    assert all(item["ok"] for item in checks), checks
    assert ok is True
    assert "7/7" in named["schemas"]["detail"]
    assert "rules_hash=sha256:" in named["rules"]["detail"]


def test_selftest_detects_missing_schema_package_data(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schema, "SCHEMA_DIR", tmp_path / "no-schemas")
    ok, checks = cli.run_selftest()
    named = _checks_by_name(checks)
    assert ok is False
    assert named["schemas"]["ok"] is False
    assert "нет:" in named["schemas"]["detail"]


def test_selftest_detects_a_broken_parser(
        monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenParser:
        def __init__(self, *args, **kwargs) -> None:
            raise ValueError("parser broken")

    monkeypatch.setattr(parse, "JournalParser", BrokenParser)
    ok, checks = cli.run_selftest()
    assert ok is False
    assert _checks_by_name(checks)["parser"]["ok"] is False


def test_selftest_detects_a_broken_post_scanner(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redact, "scan_secrets",
                        lambda payload: ["$.hypothesis:fixture_marker"])
    ok, checks = cli.run_selftest()
    assert ok is False
    assert _checks_by_name(checks)["post-scanner"]["ok"] is False


def test_selftest_detects_unknown_rule_enums(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from vpnm.guard import rules

    catalog = rules.rule_catalog()
    catalog[0]["category"] = "not_a_category"
    monkeypatch.setattr(rules, "rule_catalog", lambda: catalog)
    ok, checks = cli.run_selftest()
    assert ok is False
    assert _checks_by_name(checks)["rules"]["ok"] is False


def test_selftest_command_exits_zero_on_success() -> None:
    with pytest.raises(typer.Exit) as excinfo:
        cli.selftest()
    assert excinfo.value.exit_code == 0


def test_selftest_command_exits_one_on_failure(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redact, "scan_secrets",
                        lambda payload: ["$.x:fixture_marker"])
    with pytest.raises(typer.Exit) as excinfo:
        cli.selftest()
    assert excinfo.value.exit_code == 1
