"""Typer-обвязка ``vpn qr`` и ``vpn conf`` (SPEC-20261118-vpn-qr §2, v1.1 §9.1).

Валидация трёх групп флагов и комбинаций ``--format``/``--out`` (код 2),
затем резолвер → билдер → рендер. Любая :class:`~vpnm.qrgen.models.QrError`
превращается в ``typer.Exit(code=exc.exit_code)`` с санитизированным текстом
в stderr (секреты не печатаются никогда, §1.3).

v1.1 (D1/D2, §9.1–§9.2):

- ``vpn conf`` — основной сценарий оператора: гарантирует локальный файл
  ``.conf``/``.uri`` (0600) в каноническом каталоге артефактов, печатает
  путь + masked-текст; ``--qr`` дополнительно рисует QR и сохраняет PNG;
  ``--reveal`` печатает сырой payload с WARNING в stderr.
- ``vpn qr`` по умолчанию авто-сохраняет PNG (0600) и печатает путь в шапке;
  ``--no-save`` возвращает поведение v1 (только вывод, без файлов);
  ``--reveal`` — алиас ``--show-uri``; ``--sync`` — opt-in read-only
  подтяжка через ``vpnm.sync`` (единственная сетевая точка).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import typer

from . import render
from .builders import build_payload
from .models import (
    OutputFormat,
    Proto,
    QrConfigError,
    QrError,
    QrRequest,
    QrUsageError,
    ResolvedSource,
    Route,
    TargetOS,
)
from .redact import redact_text
from .resolver import resolve

_FORMATS = frozenset({fmt.value for fmt in OutputFormat})

_NS_SUBDIRS = ("direct", "relay")


def _pick(flags: List[Tuple[str, bool, object]], group: str):
    """Ровно один флаг группы (§2.1); иначе ``QrUsageError`` (код 2)."""
    chosen = [value for _name, enabled, value in flags if enabled]
    if len(chosen) != 1:
        names = ", ".join(name for name, _enabled, _value in flags)
        raise QrUsageError(
            f"выберите ровно один {group}: {names} "
            f"(выбрано {len(chosen)})")
    return chosen[0]


def _pick_axes(*, direct: bool, relay: bool, awg: bool, reality: bool,
               xhttp: bool, hy: bool, android: bool, ios: bool,
               windows: bool, macos: bool, linux: bool,
               laptop: bool) -> Tuple[Route, Proto, TargetOS]:
    """Три группы осей (§9.1): маршрут × транспорт × устройство."""
    route = _pick([("--direct", direct, Route.DIRECT),
                   ("--relay", relay, Route.RELAY)], "маршрут")
    proto = _pick([("--awg", awg, Proto.AWG),
                   ("--reality", reality, Proto.REALITY),
                   ("--xhttp", xhttp, Proto.XHTTP),
                   ("--hy/--hy2", hy, Proto.HY)], "протокол")
    # --laptop — синоним --linux (v1.1, D1: снимает трение имён exit-пиров)
    target_os = _pick([("--android", android, TargetOS.ANDROID),
                       ("--ios", ios, TargetOS.IOS),
                       ("--windows", windows, TargetOS.WINDOWS),
                       ("--macos", macos, TargetOS.MACOS),
                       ("--linux", linux, TargetOS.LINUX),
                       ("--laptop", laptop, TargetOS.LINUX)], "целевую ОС")
    return route, proto, target_os


def _build_request(*, direct: bool, relay: bool, awg: bool, reality: bool,
                   xhttp: bool, hy: bool, android: bool, ios: bool,
                   windows: bool, macos: bool, linux: bool,
                   laptop: bool, profile: Optional[str], client: str,
                   output_format: str, out: Optional[Path], force: bool,
                   show_uri: bool, ascii_mode: bool, invert: bool,
                   no_tty_check: bool) -> QrRequest:
    """Проверить грамматику флагов (§2.1, §6) и собрать :class:`QrRequest`."""
    route, proto, target_os = _pick_axes(
        direct=direct, relay=relay, awg=awg, reality=reality, xhttp=xhttp,
        hy=hy, android=android, ios=ios, windows=windows, macos=macos,
        linux=linux, laptop=laptop)

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


# --- --sync и метаданные источника (v1.1, D1/D2) --------------------------------

def _run_sync(route: Route) -> None:
    """``--sync``: read-only подтяжка авторитетных файлов (§9.1).

    Единственная сетевая точка стека; сбой ssh/scp (SyncError/PullError)
    не убивает офлайн-ветку — предупреждение в stderr, дальше работает
    локальный кэш (коды 3/4 отдаёт резолвер по его собственным правилам).
    """
    from .. import pull as vpnm_pull
    from .. import sync as vpnm_sync
    try:
        report = vpnm_sync.run_sync(routes=(route.value,))
    except (vpnm_sync.SyncError, vpnm_pull.PullError) as exc:
        typer.secho(
            f"[vpn sync] {route.value}: {redact_text(str(exc))}",
            fg=typer.colors.YELLOW, err=True)
        return
    for section_route, section in report.items():
        if "error" in section:
            typer.secho(
                f"[vpn sync] {section_route}: "
                f"{redact_text(str(section['error']))}",
                fg=typer.colors.YELLOW, err=True)
            continue
        confs = section.get("confs") or {}
        for category in ("imported", "updated", "skipped", "stale"):
            for stem in confs.get(category) or []:
                typer.echo(f"[vpn sync] {section_route} conf {category}: {stem}")
        for name, action in section.get("links") or []:
            typer.echo(f"[vpn sync] {section_route} link {action}: {name}")
        for name in section.get("links_stale") or []:
            typer.echo(
                f"[vpn sync] {section_route}: STALE (absent on server): "
                f"{name} — подсказка: vpn profile archive {name} "
                "--reason \"...\" --apply")


def _awg_source_path(resolved: ResolvedSource) -> Optional[Path]:
    """Исходный AWG-конфиг: direct//relay//legacy-корень или None (system)."""
    from .. import amneziawg
    base = Path(amneziawg.LOCAL_CONF_DIR)
    for ns in _NS_SUBDIRS:
        path = base / ns / f"{resolved.profile_name}.conf"
        if path.is_file():
            return path
    path = base / f"{resolved.profile_name}.conf"
    return path if path.is_file() else None


def _origin(resolved: ResolvedSource) -> str:
    """``origin`` для шапки ``vpn conf`` (§9.2): local/synced/system + mtime."""
    if resolved.source == "profile":
        synced = (resolved.raw_profile or {}).get("synced_at")
        return f"synced {synced}" if synced else "local"
    path = _awg_source_path(resolved)
    if path is None:
        return "system"
    try:
        stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return "local"
    return f"local {stamp.isoformat(timespec='seconds')}"


def _warn_world_readable(out: Path) -> None:
    """WARNING в stderr для ``--out`` в world-readable каталоге (§9.2)."""
    if render.parent_world_readable(out):
        typer.secho(
            render.WORLD_READABLE_WARNING.format(path=out),
            fg=typer.colors.YELLOW, err=True)


# --- vpn qr ----------------------------------------------------------------------

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
        laptop: bool = typer.Option(
            False, "--laptop", help="Синоним --linux (имена exit-пиров)"),
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
        reveal: bool = typer.Option(
            False, "--reveal", help="Алиас --show-uri (v1.1, §9.1)"),
        no_save: bool = typer.Option(
            False, "--no-save",
            help="Не сохранять PNG автоматически (поведение v1)"),
        sync: bool = typer.Option(
            False, "--sync",
            help="Перед выводом подтянуть авторитетные файлы (read-only "
                 "ssh/scp; по умолчанию команда офлайновая)"),
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
    не ходит в сеть (сеть — только `--sync`, read-only). В v1.1 по умолчанию
    авто-сохраняет PNG (0600) в ~/.local/share/vpn-manager/artifacts и печатает
    путь в шапке; `--no-save` отключает запись. По умолчанию печатает шапку и
    QR без секретов; сырой URI/INI — только по --show-uri/--reveal. Коды
    возврата: 0 ok, 2 usage, 3 неподдерживаемая комбинация, 4 нет валидного
    профиля, 5 ошибка рендера.
    """
    try:
        request = _build_request(
            direct=direct, relay=relay, awg=awg, reality=reality, xhttp=xhttp,
            hy=hy, android=android, ios=ios, windows=windows, macos=macos,
            linux=linux, laptop=laptop, profile=profile, client=client,
            output_format=output_format, out=out, force=force,
            show_uri=show_uri or reveal, ascii_mode=ascii_mode, invert=invert,
            no_tty_check=no_tty_check)
        if sync:
            _run_sync(request.route)
        resolved = resolve(request)
        built = build_payload(resolved)
        if request.show_uri:
            typer.secho(render.REVEAL_WARNING, fg=typer.colors.YELLOW, err=True)
        source_conf = (_awg_source_path(resolved)
                       if resolved.source == "awg" else None)
        save_cb: Optional[Callable[[], Path]] = None
        if request.output_format is OutputFormat.QR and not no_save:
            def _save_png() -> Path:
                return render.save_png_auto(
                    built, render.canonical_path(resolved, "png"))
            save_cb = _save_png
        text = render.emit(resolved, built, request, source_conf=source_conf,
                           save_png_cb=save_cb)
    except QrError as exc:
        typer.echo(redact_text(str(exc)), err=True)
        raise typer.Exit(code=exc.exit_code) from exc
    if text:
        typer.echo(text, nl=False)


# --- vpn conf --------------------------------------------------------------------

def conf_command(
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
        laptop: bool = typer.Option(
            False, "--laptop", help="Синоним --linux (имена exit-пиров)"),
        profile: Optional[str] = typer.Option(
            None, "--profile", help="Имя профиля или stem AWG-конфига"),
        client: str = typer.Option(
            "auto", "--client",
            help="auto|amneziawg|v2rayng|v2rayn|hiddify|nekobox|"
                 "shadowrocket|streisand"),
        qr: bool = typer.Option(
            False, "--qr", help="Дополнительно нарисовать QR и сохранить PNG (0600)"),
        reveal: bool = typer.Option(
            False, "--reveal",
            help="Печатать сырой payload (WARNING в stderr) вместо masked"),
        sync: bool = typer.Option(
            False, "--sync",
            help="Перед выводом подтянуть авторитетные файлы (read-only "
                 "ssh/scp; по умолчанию команда офлайновая)"),
        no_save: bool = typer.Option(
            False, "--no-save", help="Только вывод, без записи файлов"),
        out: Optional[Path] = typer.Option(
            None, "--out", help="Писать payload в указанный файл (0600) вместо "
                                "канонического имени в artifacts"),
        force: bool = typer.Option(False, "--force", help="Перезаписать --out"),
) -> None:
    """Локальный файл конфига/URI: маршрут × протокол × целевая ОС (v1.1).

    Гарантирует локальный артефакт (санитизированный .conf для AWG, .uri для
    URI-протоколов; 0600) в каноническом каталоге
    ~/.local/share/vpn-manager/artifacts, печатает путь и masked-текст без
    секретов. С --qr дополнительно рисует QR и сохраняет PNG. Секреты —
    только по --reveal (WARNING в stderr). Офлайн; сеть — только --sync.
    """
    try:
        if no_save and out is not None:
            raise QrUsageError("--out несовместим с --no-save")
        route, proto, target_os = _pick_axes(
            direct=direct, relay=relay, awg=awg, reality=reality, xhttp=xhttp,
            hy=hy, android=android, ios=ios, windows=windows, macos=macos,
            linux=linux, laptop=laptop)
        if sync:
            _run_sync(route)
        request = QrRequest(route=route, proto=proto, target_os=target_os,
                            client=client, profile=profile)
        resolved = resolve(request)
        built = build_payload(resolved)
        if reveal:
            typer.secho(render.REVEAL_WARNING, fg=typer.colors.YELLOW, err=True)

        conf_path: Optional[Path] = None
        png_path: Optional[Path] = None
        if out is not None:
            _warn_world_readable(out)
            conf_path = render.write_text(out, built.payload, force=force)
        elif not no_save:
            conf_path = render.save_payload_auto(resolved, built)

        preview = render.conf_preview(
            resolved, built, conf_path=conf_path, png_path=png_path,
            origin=_origin(resolved), reveal=reveal)
        if qr:
            qr_text = render.render_qr_text(built)
            render.ensure_terminal_width(qr_text, no_tty_check=False)
            if not no_save:
                png_path = render.save_png_auto(
                    built, render.canonical_path(resolved, "png"))
                preview = render.conf_preview(
                    resolved, built, conf_path=conf_path, png_path=png_path,
                    origin=_origin(resolved), reveal=reveal)
            text = f"{preview}\n{qr_text}"
        else:
            text = preview
    except QrError as exc:
        typer.echo(redact_text(str(exc)), err=True)
        raise typer.Exit(code=exc.exit_code) from exc
    typer.echo(text, nl=False)
