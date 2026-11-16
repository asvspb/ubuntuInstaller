"""WP-G5 tests: opt-in desktop notifications (SPEC §8.1/§10.4).

``notify-send`` never runs: :class:`FakeRun` records the argument list and
serves a scripted exit code.  No root, no network, no session bus required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import notify, rules, store  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPN_MANAGER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VPN_MANAGER_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setenv("VPN_MANAGER_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path


class FakeRun:
    def __init__(self, rc: int = 0) -> None:
        self.rc = rc
        self.calls: List[List[str]] = []

    def __call__(self, cmd: List[str]) -> Tuple[int, str, str]:
        self.calls.append(list(cmd))
        return self.rc, "", ""


def write_config(root: Path, **notify_cfg) -> None:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "guard.json").write_text(
        json.dumps({"schema": "guard.config/1",
                    "notify": notify_cfg}), encoding="utf-8")


def finding(severity: str = "high", confidence: str = "medium",
            rule: str = "G-03", hypothesis: str = "обрыв после handshake") -> dict:
    return rules.build_finding(
        rule=rule, unit="vpnm-asv2.service", profile="asv2",
        endpoint={"host": "203.0.113.10", "port": 443},
        window_first="2026-11-16T12:00:00.000Z",
        window_last="2026-11-16T12:01:00.000Z", count=3,
        evidence=[{"kind": "signal_count", "signal": "conn_reset", "value": 3}],
        fp_checks=["no auth_fail"], severity=severity,
        confidence=confidence, category="censorship_suspect",
        stage_hint="data_phase", hypothesis=hypothesis,
        action="не рестартить; vpn probe", server=None, cross_vantage=False)


def notifier(root: Path, run: FakeRun, **kwargs) -> notify.Notifier:
    kwargs.setdefault("available", lambda: (True, "ok"))
    kwargs.setdefault("now_fn", lambda: 1_800_000_000.0)
    return notify.Notifier(config_root=root / "config", run=run, **kwargs)


def test_notifications_are_off_by_default(isolated_xdg: Path) -> None:
    run = FakeRun()
    instance = notifier(isolated_xdg, run)
    assert instance.enabled() is False
    assert instance.notify(finding()) is False
    assert run.calls == []


def test_high_finding_is_sent_with_an_argument_list(isolated_xdg: Path) -> None:
    write_config(isolated_xdg, desktop=True, min_severity="high")
    run = FakeRun()
    instance = notifier(isolated_xdg, run)
    assert instance.notify(finding()) is True
    assert len(run.calls) == 1
    cmd = run.calls[0]
    assert cmd[0] == notify.NOTIFY_BIN
    assert "--app-name=vpn-guard" in cmd
    assert "--urgency=critical" in cmd
    assert isinstance(cmd, list)  # never a shell string
    assert instance.sent == 1


def test_medium_finding_is_suppressed_by_the_floor(isolated_xdg: Path) -> None:
    write_config(isolated_xdg, desktop=True, min_severity="high")
    run = FakeRun()
    instance = notifier(isolated_xdg, run)
    assert instance.notify(finding(severity="medium")) is False
    assert instance.suppressed == 1
    assert run.calls == []


def test_high_with_low_confidence_is_not_notified(isolated_xdg: Path) -> None:
    write_config(isolated_xdg, desktop=True, min_severity="high")
    run = FakeRun()
    instance = notifier(isolated_xdg, run)
    assert instance.notify(finding(confidence="low")) is False
    assert run.calls == []


def test_g12_never_notifies(isolated_xdg: Path) -> None:
    write_config(isolated_xdg, desktop=True, min_severity="low")
    run = FakeRun()
    instance = notifier(isolated_xdg, run)
    g12 = finding(severity="low", confidence="low", rule="G-12")
    assert instance.notify(g12) is False
    assert run.calls == []


def test_rate_limit_caps_notifications_per_hour(isolated_xdg: Path) -> None:
    write_config(isolated_xdg, desktop=True, min_severity="high")
    run = FakeRun()
    instance = notifier(isolated_xdg, run, max_per_hour=2)
    assert instance.notify(finding()) is True
    assert instance.notify(finding()) is True
    assert instance.notify(finding()) is False
    assert instance.suppressed == 1
    assert instance.last_reason == "rate_limited"
    assert len(run.calls) == 2


def test_missing_desktop_degrades_to_no_notification(isolated_xdg: Path) -> None:
    write_config(isolated_xdg, desktop=True, min_severity="high")
    run = FakeRun()
    instance = notify.Notifier(config_root=isolated_xdg / "config", run=run,
                               available=lambda: (False, "нет D-Bus"))
    assert instance.notify(finding()) is False
    assert instance.last_reason == "нет D-Bus"
    assert run.calls == []


def test_failed_notify_send_is_not_counted(isolated_xdg: Path) -> None:
    write_config(isolated_xdg, desktop=True, min_severity="high")
    run = FakeRun(rc=1)
    instance = notifier(isolated_xdg, run)
    assert instance.notify(finding()) is False
    assert instance.sent == 0
    assert instance.last_reason == "notify_rc=1"


def test_body_is_redacted_before_leaving_the_process(isolated_xdg: Path) -> None:
    write_config(isolated_xdg, desktop=True, min_severity="high")
    run = FakeRun()
    instance = notifier(isolated_xdg, run)
    leaked = finding(hypothesis="password=FIXTURE_PW_NOTIFY мост")
    assert instance.notify(leaked) is True
    joined = " ".join(run.calls[0])
    assert "FIXTURE_PW_NOTIFY" not in joined
    assert "guard" in joined


def test_notify_settings_come_from_guard_json(isolated_xdg: Path) -> None:
    assert store.notify_settings() == (False, "high")
    write_config(isolated_xdg, desktop=True, min_severity="medium")
    assert store.notify_settings() == (True, "medium")
    write_config(isolated_xdg, desktop=True, min_severity="nonsense")
    assert store.notify_settings() == (True, "high")
