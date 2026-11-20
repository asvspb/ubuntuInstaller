"""`vpn --help` перечисляет протоколы, которые можно активировать (2026-11-19).

Typer (rich_utils) склеивает одиночные переводы строк внутри абзаца, поэтому
команды проверяем по исходному epilog, а не по отрендеренному тексту.
"""
from typer.testing import CliRunner

from vpnm.cli import app

runner = CliRunner()


def test_help_has_protocols_section():
    out = runner.invoke(app, ["--help"]).output
    assert "Протоколы, которые можно активировать" in out
    for name in ("AmneziaWG", "Reality", "XHTTP", "Hysteria2", "CDN", "RU-релей"):
        assert name in out, f"в --help нет протокола {name}"


def test_cdn_marked_cold_reserve():
    """CDN не должен выглядеть как живой канал (DECISION Q6, 2026-11-20)."""
    epilog = app.info.epilog or ""
    assert "ХОЛОДНЫЙ РЕЗЕРВ" in epilog
    cdn_line = next(l for l in epilog.splitlines() if l.startswith("CDN (ws)"))
    assert "не развёрнут" in cdn_line or "не развёрнут" in epilog


def test_epilog_lists_activation_commands():
    epilog = app.info.epilog or ""
    for cmd in ("vpn start reality", "vpn start xhttp", "vpn up asv2-hy2",
                "vpn scheme cdn", "vpn start relay", "--proto reality",
                "vpn amneziawg tunnel on"):
        assert cmd in epilog, f"в epilog нет команды {cmd}"
