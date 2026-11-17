"""``vpn guard notify-test`` — the delivery check for desktop banners.

The command exists so "уведомления не приходят" stops being a guess: it
delivers one finding-shaped banner through the same opt-in / severity /
availability path the daemon uses, **bypasses the 12/hour cooldown on
purpose** (it is a manual check, not a finding) and prints the failure
reason with a hint.  Nothing is written to ``findings.jsonl`` and
``notify-send`` is never executed for real.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Tuple

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import cli as guard_cli, notify  # noqa: E402

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VPN_MANAGER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VPN_MANAGER_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setenv("VPN_MANAGER_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path


def write_config(root: Path, **notify_cfg) -> None:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "guard.json").write_text(
        json.dumps({"schema": "guard.config/1", "notify": notify_cfg}),
        encoding="utf-8")


class FakeRun:
    def __init__(self, rc: int = 0) -> None:
        self.rc = rc
        self.calls: List[List[str]] = []

    def __call__(self, cmd: List[str]) -> Tuple[int, str, str]:
        self.calls.append(list(cmd))
        return self.rc, "", ""


def test_banner_notifier_bypasses_the_hourly_cooldown(
        isolated_xdg: Path) -> None:
    instance = guard_cli._banner_notifier()
    # fill the window with the documented maximum (12/hour)
    instance._sent_at.extend([1.0] * notify.DEFAULT_MAX_PER_HOUR)
    assert instance._rate_ok() is True


def test_banner_finding_is_finding_shaped_and_not_g12() -> None:
    finding = guard_cli._banner_finding("high")
    assert finding["rule"] == guard_cli.NOTIFY_TEST_RULE
    assert finding["rule"] != "G-12"  # G-12 never notifies
    assert finding["severity"] == "high"
    assert finding["confidence"] in ("low", "medium", "high")


def test_notify_test_delivers_and_reports_ok(
        isolated_xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(isolated_xdg, desktop=True, min_severity="high")
    run = FakeRun()
    monkeypatch.setattr(notify, "_run_cmd", run)
    monkeypatch.setattr(notify.Notifier, "available",
                        lambda self: (True, "ok"))
    result = runner.invoke(guard_cli.guard_app, ["notify-test"])
    assert result.exit_code == 0
    assert "баннер отправлен" in result.output
    assert "desktop=True" in result.output
    assert run.calls and run.calls[0][0] == notify.NOTIFY_BIN


def test_notify_test_reports_the_disabled_reason(
        isolated_xdg: Path) -> None:
    # no guard.json at all: notifications are opt-in and therefore off
    result = runner.invoke(guard_cli.guard_app, ["notify-test"])
    assert result.exit_code == 1
    assert "desktop_notifications_off" in result.output
    assert "desktop" in result.output  # the hint names the config key


def test_notify_test_reports_a_missing_channel(
        isolated_xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(isolated_xdg, desktop=True, min_severity="high")
    monkeypatch.setattr(notify.Notifier, "available",
                        lambda self: (False, "нет D-Bus session bus"))
    result = runner.invoke(guard_cli.guard_app, ["notify-test"])
    assert result.exit_code == 1
    assert "нет D-Bus session bus" in result.output
    assert "D-Bus" in result.output


def test_notify_test_rejects_an_unknown_severity(
        isolated_xdg: Path) -> None:
    result = runner.invoke(guard_cli.guard_app,
                           ["notify-test", "--severity", "crit"])
    assert result.exit_code == 2
    assert "crit" not in result.output.split("\n")[0]
    assert "high" in result.output  # the allowed values are printed


def test_notify_test_does_not_write_findings(
        isolated_xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(isolated_xdg, desktop=True, min_severity="high")
    monkeypatch.setattr(notify, "_run_cmd", FakeRun())
    monkeypatch.setattr(notify.Notifier, "available",
                        lambda self: (True, "ok"))
    result = runner.invoke(guard_cli.guard_app, ["notify-test"])
    assert result.exit_code == 0
    findings = isolated_xdg / "guard" / "findings.jsonl"
    assert not findings.exists()
