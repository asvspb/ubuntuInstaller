"""Typer-обвязка ``vpn qr`` (SPEC-20261118-vpn-qr §2).

Валидация трёх групп флагов и комбинаций ``--format``/``--out`` (код 2),
затем резолвер → билдер → рендер. Любая :class:`~vpnm.qrgen.models.QrError`
превращается в ``typer.Exit(code=exc.exit_code)`` с санитизированным текстом
в stderr (секреты не печатаются никогда, §1.3).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import typer

from . import render
from .builders import build_payload
from .models import (
    OutputFormat,
    Proto,
    QrError,
    QrRequest,
    QrUsageError,
    Route,
    TargetOS,
)
from .redact import redact_text
from .resolver import resolve

_FORMATS = frozenset({fmt.value for fmt in OutputFormat})


def _pick(flags: List[Tuple[str, bool, object]], group: str):
    """Ровно один флаг группы (§2.1); иначе ``QrUsageError`` (код 2)."""
    chosen = [value for _name, enabled, value in flags if enabled]
    if len(chosen) != 1:
        names = ", ".join(name for name, _enabled, _value in flags)
        raise QrUsageError(
            f"выберите ровно один {group}: {names} "
            f"(выбрано {len(chosen)})")
    return chosen[0]


def _build_request(*, direct: bool, relay: bool, awg: bool, reality: bool,
                   xhttp: bool, hy: bool, android: bool, ios: bool,
                   windows: bool, macos: bool, linux: bool,
                   profile: Optional[str], client: str, output_format: str,
                   out: Optional[Path], force: bool, show_uri: bool,
                   ascii_mode: bool, invert: bool,
                   no_tty_check: bool) -> QrRequest:
    """Проверить грамматику флагов (§2.1, §6) и собрать :class:`QrRequest`."""
    route = _pick([("--direct", direct, Route.DIRECT),
                   ("--relay", relay, Route.RELAY)], "маршрут")
    proto = _pick([("--awg", awg, Proto.AWG),
                   ("--reality", reality, Proto.REALITY),
                   ("--xhttp", xhttp, Proto.XHTTP),
                   ("--hy/--hy2", hy, Proto.HY)], "протокол")
    target_os = _pick([("--android", android, TargetOS.ANDROID),
                       ("--ios", ios, TargetOS.IOS),
                       ("--windows", windows, TargetOS.WINDOWS),
                       ("--macos", macos, TargetOS.MACOS),
                       ("--linux", linux, TargetOS.LINUX)], "целевую ОС")

    fmt = (output_format or "").strip().lower()
    if fmt not in _FORMATS:
        allowed = ", ".join(sorted(_FORMATS))
        raise QrUsageError(
            f"--format '{output_format}': неизвестное значение (допустимо: {allowed})")
    if fmt in (OutputFormat.PNG.value, OutputFormat.SVG.value) and out is None:
        raise QrUsageError(f"--format {fmt} требует --out <path>")
    if fmt == OutputFormat.QR.value and out is not None:
        raise QrUsageError("--out несовместим с --format qr (используйте --format uri)")

    return QrRequest(
        route=route, proto=proto, target_os=target_os, client=client,
        profile=profile, output_format=OutputFormat(fmt), out_path=out,
        force=force, show_uri=show_uri, ascii_mode=ascii_mode, invert=invert,
        no_tty_check=no_tty_check)


def qr_command(
        direct: bool = typer.Option(False, "--direct", help="Прямой маршрут (exit-узел)"),
        relay: bool = typer.Option(False, "--relay", help="Через RU-релей (ADR-007)"),
        awg: bool = typer.Option(False, "--awg", help="AmneziaWG (.conf)"),
        reality: bool = typer.Option(False, "--reality", help="VLESS Reality"),
        xhttp: bool = typer.Option(False, "--xhttp", help="VLESS XHTTP"),
        hy: bool = typer.Option(False, "--hy", "--hy2", help="Hysteria2"),
        android: bool = typer.Option(False, "--android"),
        ios: bool = typer.Option(False, "--ios"),
        windows: bool = typer.Option(False, "--windows"),
        macos: bool = typer.Option(False, "--macos"),
        linux: bool = typer.Option(False, "--linux"),
        profile: Optional[str] = typer.Option(
            None, "--profile",
            help="Имя профиля или stem AWG-конфига (снимает неоднозначность)"),
        client: str = typer.Option(
            "auto", "--client",
            help="auto|amneziawg|v2rayng|v2rayn|hiddify|nekobox|"
                 "shadowrocket|streisand"),
        output_format: str = typer.Option(
            "qr", "--format", help="qr|uri|png|svg|json"),
        out: Optional[Path] = typer.Option(
            None, "--out", help="Файл для png/svg (обязателен) или uri/json"),
        force: bool = typer.Option(False, "--force", help="Перезаписать --out"),
        show_uri: bool = typer.Option(
            False, "--show-uri",
            help="Показать сырой URI/INI под QR (и payload в JSON)"),
        ascii_mode: bool = typer.Option(
            False, "--ascii", help="ASCII-символы вместо UTF-8 полублоков"),
        invert: bool = typer.Option(
            False, "--invert", help="Инверсия цветов QR"),
        no_tty_check: bool = typer.Option(
            False, "--no-tty-check", help="Не проверять ширину терминала"),
) -> None:
    """QR/URI клиентского профиля: маршрут × протокол × целевая ОС.

    Читает только локальное состояние vpnm (~/.config/vpn-manager, ~/.awg,
    системный AWG-каталог через read-only `sudo -n cat`), ничего не меняет и
    не ходит в сеть. По умолчанию печатает шапку и QR без секретов; сырой
    URI/INI — только по --show-uri. Коды возврата: 0 ok, 2 usage, 3
    неподдерживаемая комбинация, 4 нет валидного профиля, 5 ошибка рендера.
    """
    try:
        request = _build_request(
            direct=direct, relay=relay, awg=awg, reality=reality, xhttp=xhttp,
            hy=hy, android=android, ios=ios, windows=windows, macos=macos,
            linux=linux, profile=profile, client=client,
            output_format=output_format, out=out, force=force,
            show_uri=show_uri, ascii_mode=ascii_mode, invert=invert,
            no_tty_check=no_tty_check)
        resolved = resolve(request)
        built = build_payload(resolved)
        text = render.emit(resolved, built, request)
    except QrError as exc:
        typer.echo(redact_text(str(exc)), err=True)
        raise typer.Exit(code=exc.exit_code) from exc
    if text:
        typer.echo(text, nl=False)
