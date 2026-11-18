"""Полевые фиксы G6 (2026-11-19): D1 (exit-код демона) и D3 (баннер Hy2 + AC-5).

Контекст — отчёт T+33 (`vds/docs/team/reports/TEST-20261119-vpn-guard-t33.md`):

* **D1.** Демон, штатно остановленный SIGTERM, выходил ``2``, если за прогон
  были high-findings → systemd писал ``Failed with result 'exit-code'`` и
  портил AC-5 («0 падений»). Теперь ``high → 2`` только для ``--once``.
* **D3.** Hysteria2 2.x печатает баннер как ``INFO\\tclient mode`` (без имени
  протокола) — строка уходила в ``unparsed``, а ``app_banner_ok`` (доказательство
  покрытия для G-01) не выставлялся. Плюс доля ``unparsed/app_lines`` на
  выборке из одной строки не отдаётся как метрика AC-5.

Секретов нет: только счётчики, сигналы и фикстурные строки.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import cli as guard_cli  # noqa: E402
from vpnm.guard import parse, store  # noqa: E402
from vpnm.guard.runner import RunCounters, build_runner  # noqa: E402

UNIT = "vpnm-asv2-hy2.service"


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VPN_MANAGER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VPN_MANAGER_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setenv("VPN_MANAGER_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path


def entry(message: str, unit: str = UNIT) -> Dict[str, Any]:
    return {"MESSAGE": message, "_SYSTEMD_USER_UNIT": unit,
            "__REALTIME_TIMESTAMP": "1700000000000000"}


class FakeReader:
    """Reader protocol без journald (start/records/stop/status/note)."""

    def __init__(self, records: List[Dict[str, Any]]) -> None:
        self.records_list = list(records)
        self.status = "off"
        self.note = None

    def start(self) -> None:
        self.status = "ok"

    def stop(self) -> None:
        self.status = "off"

    def records(self):
        return iter(self.records_list)


# --- D3: Hysteria2-баннер -----------------------------------------------------

def test_hysteria2_bare_client_mode_banner_is_recognised() -> None:
    """Поле: `INFO\\tclient mode` (ANSI-цвета) — это баннер, а не unparsed."""
    parser = parse.JournalParser(proto_hints={UNIT: "hysteria2"})
    event = parser.parse(entry(
        "2026-11-18T13:32:29+03:00\t\x1b[34mINFO\x1b[0m\tclient mode"))

    assert event is not None
    assert event["signal"] == "app_banner_ok"
    assert parser.stats.unparsed == 0
    assert parser.stats.app_lines == 1
    assert parser.stats.unparsed_ratio == pytest.approx(0.0)


def test_hysteria2_client_mode_with_socks_port_is_still_a_banner() -> None:
    parser = parse.JournalParser(proto_hints={UNIT: "hysteria2"})
    event = parser.parse(entry("INFO\tclient mode, socks5://127.0.0.1:1083"))
    assert event is not None and event["signal"] == "app_banner_ok"


def test_client_mode_not_at_line_end_is_not_a_banner() -> None:
    """«client mode disabled» — не баннер: иначе G-01 получит ложное покрытие."""
    parser = parse.JournalParser(proto_hints={UNIT: "hysteria2"})
    event = parser.parse(entry("INFO\tclient mode disabled by policy"))
    assert event is not None
    assert event["signal"] == "unparsed"


# --- D3: AC-5 не судит по выборке из одной строки ------------------------------

def _runner_with_stats(app_lines: int, unparsed: int):
    runner = build_runner(
        readers=[FakeReader([])], watcher=None, poller=None,
        sink=store.FindingsStore(path=Path("/tmp") / "unused-findings.jsonl"),
        state_writer=lambda _payload: None,
        coverage_writer=lambda _payload: None)
    runner.parser.stats = parse.ParseStats(
        lines=app_lines, app_lines=app_lines, parsed=app_lines - unparsed,
        unparsed=unparsed)
    return runner


def test_coverage_does_not_report_ratio_on_tiny_sample() -> None:
    coverage = _runner_with_stats(app_lines=1, unparsed=1).coverage_payload()

    assert coverage["unparsed_ratio"] == pytest.approx(0.0)
    assert any(note.startswith("app_lines_low:") for note in coverage["notes"])


def test_coverage_reports_ratio_when_sample_is_real() -> None:
    coverage = _runner_with_stats(app_lines=100, unparsed=2).coverage_payload()

    assert coverage["unparsed_ratio"] == pytest.approx(0.02)
    assert not any(note.startswith("app_lines_low:") for note in coverage["notes"])


# --- D1: exit-код демона vs --once --------------------------------------------

class _FakeRunner:
    def __init__(self, counters: RunCounters) -> None:
        self.counters = counters

    def run_once(self) -> RunCounters:
        return self.counters

    def run_forever(self, stop=None) -> RunCounters:  # noqa: ANN001
        return self.counters


def _invoke_run(monkeypatch, counters: RunCounters, *args: str):
    # `guard_cli.run` импортирует runner лениво внутри команды → патчим модуль.
    from vpnm.guard import runner as runner_mod

    monkeypatch.setattr(runner_mod, "build_runner",
                        lambda **kwargs: _FakeRunner(counters))
    monkeypatch.setattr(guard_cli, "_install_stop_handlers", lambda stop: None)
    return CliRunner().invoke(guard_cli.guard_app, ["run", "--quiet", *args])


def test_daemon_graceful_shutdown_exits_zero_even_with_high_findings(monkeypatch):
    counters = RunCounters(events=10, findings=2, high=2)
    result = _invoke_run(monkeypatch, counters)
    assert result.exit_code == 0


def test_once_mode_keeps_high_as_exit_code_2(monkeypatch):
    counters = RunCounters(events=10, findings=2, high=2)
    result = _invoke_run(monkeypatch, counters, "--once")
    assert result.exit_code == 2


def test_once_mode_without_high_exits_zero(monkeypatch):
    result = _invoke_run(monkeypatch, RunCounters(events=3), "--once")
    assert result.exit_code == 0
