"""``vpn guard`` CLI surface (WP-G1…WP-G5).

Heavy modules are imported lazily inside the commands so that CLI startup
stays light.  Commands:

- ``run`` — the streaming daemon (``--once`` processes the available backfill
  and exits); rules → incidents → ``findings.jsonl``; ``--trace`` writes the
  redacted ``trace.ndjson`` ring (SPEC §10.3);
- ``status`` — ``guard.status/1``: running/state/profile/counters/open
  incidents/coverage (exit 0 ok / 1 degraded / 2 high open);
- ``findings``/``explain`` — history and "why did it fire";
- ``rules`` — the G-01…G-12 catalogue, effective thresholds and the
  deterministic ``rules_hash``/``config_hash``/``schema_hash`` set;
- ``export`` — anonymised ``guard.bundle/1`` (hosts → ``<host>`` unless
  ``--keep-hosts``), blocked with exit 2 on a surviving secret (AC-13);
- ``selftest`` — schemas, parser, rules and the post-scanner **from the
  installed package** (AC-12), exit 0/1;
- ``sync`` — the **only** network command: one read-only ssh fetch of the
  server verdict (SPEC §13.1);
- ``sources`` — ``guard.coverage/1``: per-source health, honest
  ``denied``/``off``/``missing`` instead of "всё хорошо" (R-9);
- ``install``/``remove`` — the ``vpn-guard.service`` user unit with the
  ``hardened`` → ``compat`` fallback that is only taken on an *observed*
  start failure (AC-17);
- ``notify-test`` — one test banner through the same opt-in/severity/
  availability path (cooldown deliberately bypassed) plus the OK/failure
  reason, so "уведомления не приходят" stops being a guess.

Desktop notifications are opt-in (``notify.desktop=true``) and are wired by
the run loop, not by a command (SPEC §10.4).
"""

from __future__ import annotations

import json
import re
import signal
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import typer

guard_app = typer.Typer(
    help="Real-time анализ клиентских логов (SPEC-20261116-vpn-guard). "
         "v1 — read-only: демон ничего не рестартит, не переключает и не "
         "лезет в сеть; свои артефакты — ~/.local/share/vpn-manager/guard/.")

MappingLike = Dict[str, Any]


def _echo_json(payload: MappingLike) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


# --- coverage (SPEC §6.6 / §10.2) --------------------------------------------

def collect_coverage(*, run: Optional[Any] = None,
                     sysfs_root: Optional[Path] = None,
                     install_state: Optional[Dict[str, Any]] = None,
                     config_root: Optional[Path] = None,
                     state_path: Optional[Path] = None,
                     verdict_path: Optional[Path] = None,
                     files: Optional[Dict[Path, str]] = None,
                     redaction_hits: int = 0) -> MappingLike:
    """Probe sources and build a validated ``guard.coverage/1`` payload.

    No root, no network: journal probes are zero-record reads, AWG is a
    sysfs scan, everything else is ``stat``/``read_text``.
    """
    from . import counters, install, reader, store

    sysfs = sysfs_root if sysfs_root is not None else counters.DEFAULT_SYSFS_ROOT
    sources: List[Any] = [reader.journal_readable(user=True, run=run)]

    system_enabled = _system_journal_enabled(config_root)
    if system_enabled:
        sources.append(reader.journal_readable(user=False, run=run))
    else:
        sources.append(reader.SourceHealth(
            name="journal:system", status="off",
            note="opt-in: sources.system_journal=false"))

    sources.append(reader.state_source_health(state_path))
    watch_files = files if files is not None else reader.default_file_sources()
    sources.append(reader.file_source_health(watch_files))
    sources.append(reader.verdict_source_health(verdict_path))

    poller = counters.AwgCounterPoller(sysfs_root=sysfs)
    iface = poller.resolve_iface()
    if iface is None:
        sysfs_status = "off" if sysfs.is_dir() else "denied"
        sysfs_note = ("AWG-интерфейс не найден (ARPHRD_NONE)"
                      if sysfs.is_dir() else "нет доступа к /sys/class/net")
        sources.append(reader.SourceHealth(name="sysfs:awg",
                                           status=sysfs_status,
                                           note=sysfs_note))
    else:
        sources.append(reader.SourceHealth(
            name="sysfs:awg", status="ok", note=f"iface={iface}"))

    sources.append(reader.SourceHealth(name="netlink", status="off",
                                       note="v1.1, opt-in"))

    systemd_ok = sources[0].status == "ok"
    signals: Dict[str, str] = {
        "systemd": "ok" if systemd_ok else "missing",
        # WP-G1 has no parser yet (WP-G2) and no run loop (WP-G3), so app
        # signals are honestly "blind" — claiming "ok" would fabricate
        # observability that does not exist.
        "xray": "blind",
        "hysteria2": "blind",
        "amneziawg": "counters" if iface is not None else "missing",
        "dns": ("ok" if system_enabled and systemd_ok else "blind"),
    }

    notes: List[str] = []
    blind: List[str] = []
    if not systemd_ok:
        blind.append("user_journal_unreadable")
    if system_enabled and sources[1].status in ("denied", "lost"):
        notes.append("awg_lifecycle_unobservable")
    if poller.note == "awg_iface_ambiguous":
        notes.append("awg_iface_ambiguous")
    notes.append("app_signal=blind (app-сигналы разбирает WP-G2; "
                 "guard run — WP-G3)")
    note = install.unit_profile_note(install_state)
    if note:
        notes.append(note)
    if not (reader.guard_dir().is_dir()):
        notes.append("guard не установлен: vpn guard install")
    notes.append("unparsed_ratio не измеряется до WP-G2")

    return store.build_coverage(
        sources, signals=signals, unparsed_ratio=0.0, dropped=0,
        blind=blind, notes=notes, redaction_hits=redaction_hits,
        config_root=config_root)


def _system_journal_enabled(config_root: Optional[Path] = None) -> bool:
    """``sources.system_journal`` from ``guard.json`` (default ``false``)."""
    from . import install
    sources = install._guard_config(config_root).get("sources")
    return isinstance(sources, dict) and bool(sources.get("system_journal"))


def _active_profile() -> Optional[str]:
    """The channel the user last brought up: socks profile, else the tun scheme.

    ``state.json.active`` tracks socks profiles only — ``vpn start reality``
    (xray-split) never writes it.  Without the fallback both ``status`` and the
    agent would report "no active profile" while the direct tun channel is
    exactly what carries the traffic.
    """
    from . import reader
    path = reader.config_dir() / "state.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        payload = {}
    active = payload.get("active") if isinstance(payload, dict) else None
    if isinstance(active, str) and active:
        return active
    from . import failover as failover_mod
    return failover_mod.xsplit_active_profile()


# --- commands -----------------------------------------------------------------

@guard_app.command()
def install(apply: bool = typer.Option(
                False, "--apply",
                help="Реально записать юнит и включить его (по умолчанию "
                     "dry-run: только план)"),
            linger: bool = typer.Option(
                False, "--linger",
                help="Opt-in: loginctl enable-linger — guard работает и без "
                     "активной сессии"),
            sync_timer: bool = typer.Option(
                False, "--sync-timer",
                help="Opt-in: таймер vpn-guard-sync.timer (раз в час, "
                     "RandomizedDelaySec=5min) для vpn guard sync"),
            netlink: bool = typer.Option(
                False, "--netlink",
                help="Добавить AF_NETLINK в RestrictAddressFamilies "
                     "(источник S4, v1.1)")) -> None:
    """Установить user-юнит vpn-guard.service (hardened, fallback compat).

    Имя строго vpn-guard.service: префикс vpnm- конфликтовал бы с
    профилями (vpn autostart off выключил бы сам guard). Профиль
    compat выбирается только по факту наблюдаемого отказа старта и
    записывается в install-state.json — молчаливых послаблений нет.
    """
    from . import install as install_mod
    plan = install_mod.install(apply=apply, linger=linger,
                               sync_timer=sync_timer, netlink=netlink)
    _echo_json(plan)
    if not apply:
        typer.secho("[info] dry-run: ничего не изменено; повторите с --apply",
                    fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)
    if plan.get("journal_group"):
        typer.secho("[info] вы в группе systemd-journal: можно включить "
                    "sources.system_journal=true в guard.json для S2 "
                    "(awg-quick@*/vpn-awg-split@*/dnsmasq lifecycle)",
                    fg=typer.colors.CYAN)
    if plan.get("desktop_notify"):
        delivery = plan.get("notify_delivery")
        color = typer.colors.GREEN if delivery == "ok" else typer.colors.YELLOW
        typer.secho(f"[info] notify-send (D-Bus session bus): {delivery}",
                    fg=color)
    if plan.get("error"):
        typer.secho(f"[error] {plan['error']}", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    typer.secho(f"[ok] unit profile: {plan.get('unit_profile')}"
                + (f" ({plan.get('fallback_reason')})"
                   if plan.get("fallback_reason") else ""),
                fg=typer.colors.GREEN)


@guard_app.command()
def remove(apply: bool = typer.Option(
               False, "--apply",
               help="Реально удалить юниты (по умолчанию dry-run)"),
           purge: bool = typer.Option(
               False, "--purge",
               help="Удалить и данные guard (findings/state); без флага "
                    "история сохраняется")) -> None:
    """Выключить и удалить юнит guard (+ опционально данные)."""
    from . import install as install_mod
    plan = install_mod.remove(apply=apply, purge=purge)
    _echo_json(plan)
    if not apply:
        typer.secho("[info] dry-run: ничего не изменено; повторите с --apply",
                    fg=typer.colors.YELLOW)
    else:
        typer.secho("[ok] guard unit removed"
                    + (" (+ данные)" if purge else ""),
                    fg=typer.colors.GREEN)


@guard_app.command()
def run(once: bool = typer.Option(
            False, "--once",
            help="Обработать доступный backfill (--since) и выйти; absence-"
                 "правила могут вернуть insufficient_data"),
        since: str = typer.Option(
            "-2m", "--since",
            help="Окно backfill на старте (максимум 24h, SPEC §6.5)"),
        as_json: bool = typer.Option(
            False, "--json", help="Findings как NDJSON в stdout"),
        quiet: bool = typer.Option(
            False, "--quiet", help="Не печатать findings (только файл)"),
        foreground: bool = typer.Option(
            False, "--foreground",
            help="Явно foreground (демонизация — только systemd)"),
        trace: bool = typer.Option(
            False, "--trace",
            help="Opt-in: кольцевой trace.ndjson (≤10 МБ, 1 ч, только после "
                 "redact)")) -> None:
    """Потоковый анализ журналов; read-only, без сети и root.

    Сеть в guard отсутствует: серверный вердикт обновляется только явной
    командой `vpn guard sync`. Exit: 0 — нет high; 2 — есть high; 3 —
    ошибка конфига/источника.
    """
    from . import reader, report, runner

    try:
        reader.validate_backfill(since)
    except reader.BackfillError as exc:
        typer.secho(f"[error] {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=3)
    except ValueError as exc:
        typer.secho(f"[error] --since: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=3)

    def emit(finding: Dict[str, Any]) -> None:
        if as_json:
            typer.echo(json.dumps(finding, ensure_ascii=False,
                                  sort_keys=True))
        elif not quiet:
            for line in report.render_finding(finding):
                typer.echo(line)

    try:
        guard_runner = runner.build_runner(
            since=since, once=once, on_finding=emit,
            trace_enabled=trace,
            on_incident=(None if quiet or as_json else
                         lambda item: typer.echo(report.incident_line(item))))
        if once:
            counters = guard_runner.run_once()
        else:
            stop = threading.Event()
            _install_stop_handlers(stop)
            typer.secho("[info] guard run (foreground): Ctrl-C/SIGTERM — "
                        "graceful shutdown", fg=typer.colors.CYAN,
                        err=quiet or as_json)
            counters = guard_runner.run_forever(stop)
    except (OSError, ValueError) as exc:
        typer.secho(f"[error] guard run: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=3)
    if counters.high:
        raise typer.Exit(code=2)
    if as_json or quiet:
        raise typer.Exit(code=0)
    typer.secho(
        f"[info] findings={counters.findings} high={counters.high} "
        f"suppressed={counters.suppressed} events={counters.events} "
        f"dropped={counters.dropped}",
        fg=typer.colors.CYAN)
    raise typer.Exit(code=0)


def _install_stop_handlers(stop: threading.Event) -> None:
    """SIGTERM/SIGINT → graceful shutdown (main thread only)."""
    def handle(_signum: int, _frame: Any) -> None:
        stop.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(signum, handle)
        except (ValueError, OSError):
            continue  # non-main thread: caller passes its own event


@guard_app.command()
def findings(since: str = typer.Option(
                 "24h", "--since",
                 help="Окно истории: 30m, 24h, 7d (по window.last)"),
             limit: int = typer.Option(
                 50, "--limit", help="Максимум findings (0 — без лимита)"),
             as_json: bool = typer.Option(
                 False, "--json", help="JSON-массив (или NDJSON с --follow)"),
             follow: bool = typer.Option(
                 False, "--follow", help="Хвост findings.jsonl (Ctrl-C — стоп)")) -> None:
    """История findings (файл 0600, без секретов и сырого текста)."""
    from . import reader, store

    try:
        seconds = reader.parse_since(since)
    except ValueError as exc:
        typer.secho(f"[error] --since: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    findings_store = store.FindingsStore(path=store.findings_path())
    cutoff = time.time() - seconds
    if not follow:
        items = findings_store.read(since_ts=cutoff, limit=limit)
        if as_json:
            _echo_json_list(items)
        else:
            for item in items:
                typer.echo("\n".join(
                    _render_quiet(item, as_json=False)))
        raise typer.Exit(code=0)

    seen: set = set()
    try:
        while True:
            items = findings_store.read(since_ts=cutoff, limit=limit)
            for item in reversed(items):  # oldest first for a tail
                key = item.get("id") or id(item)
                if key in seen:
                    continue
                seen.add(key)
                if as_json:
                    typer.echo(json.dumps(item, ensure_ascii=False,
                                          sort_keys=True))
                else:
                    for line in _render_quiet(item, as_json=False):
                        typer.echo(line)
            time.sleep(1.0)
    except KeyboardInterrupt:
        raise typer.Exit(code=0)


def _render_quiet(finding: Dict[str, Any],
                  as_json: bool) -> List[str]:
    from . import report
    return report.render_finding(finding)


def _echo_json_list(items: List[Dict[str, Any]]) -> None:
    typer.echo(json.dumps(items, ensure_ascii=False, indent=2, default=str))


@guard_app.command()
def explain(target: str = typer.Argument(
        ..., help="ID finding (12 hex) или правило (например G-03)")) -> None:
    """Почему сработало: окно, сигналы, выполненные FP-проверки, действия."""
    from . import report, store

    findings_store = store.FindingsStore(path=store.findings_path())
    items = findings_store.read(limit=0)
    match: Optional[Dict[str, Any]] = None
    for item in items:  # newest first
        if item.get("id") == target or item.get("rule") == target:
            match = item
            break
    if match is None:
        typer.secho(f"[warning] finding не найден: {target}",
                    fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    opened = store.read_state()
    incident = None
    if opened:
        for item in opened.get("open_incidents") or []:
            if isinstance(item, dict) and match.get("id") in (
                    item.get("findings") or []):
                incident = item
                break
    typer.echo(report.explain(match, incident))
    raise typer.Exit(code=0)


@guard_app.command()
def sync(host: Optional[str] = typer.Option(
             None, "--host",
             help="ssh-хост; по умолчанию settings.json:ssh_host"),
         timeout: float = typer.Option(
             10.0, "--timeout", help="Таймаут одного ssh, секунды")) -> None:
    """Сеть: один ssh `vds gateway probe-watch watch --json` (без --record)."""
    from . import verdict

    if timeout <= 0:
        typer.secho("[error] --timeout должен быть > 0", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    result = verdict.sync(host=host, timeout=timeout)
    _echo_json(result.as_dict())
    if not result.ok:
        typer.secho(f"[warning] sync не выполнен: {result.error}; старый "
                    f"server-verdict.json сохранён ({result.note})",
                    fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=2)
    typer.secho(f"[ok] verdict={result.verdict or '-'} "
                f"threat={result.threat_level or '-'} "
                f"fresh={result.context.fresh}", fg=typer.colors.GREEN,
                err=True)
    raise typer.Exit(code=0)


@guard_app.command()
def status(as_json: bool = typer.Option(
        False, "--json", help="Machine-readable guard.status/1")) -> None:
    """Состояние guard: running/state/profile/incidents/coverage (exit 0/1/2)."""
    from . import counters, install as install_mod, store

    info = install_mod.status()
    state_active = _active_profile()
    poller = counters.AwgCounterPoller()
    iface = poller.resolve_iface()
    guard_state = counters.channel_state(
        vpnm_units=(state_active,) if state_active else (),
        awg_iface=iface)
    cached = install_mod.load_install_state()
    install_hits = 0
    if cached and isinstance(cached.get("redaction_hits"), int):
        install_hits = max(0, int(cached["redaction_hits"]))
    # the daemon keeps a cumulative counter in state.json: after a crash it
    # must not look like a restart reset it (WP-G3, store.read_redaction_hits)
    redaction_hits = store.redaction_hits_total(install_hits)
    daemon_state = store.read_state() or {}
    persisted = daemon_state.get("counters")
    counter_payload: Dict[str, int] = {
        "events": 0, "unparsed": 0, "dropped": 0,
        "findings": 0, "suppressed": 0,
    }
    if isinstance(persisted, dict):
        for key in counter_payload:
            value = persisted.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                counter_payload[key] = max(0, value)
    open_incidents = daemon_state.get("open_incidents")
    if not isinstance(open_incidents, list):
        open_incidents = []
    coverage = store.read_coverage()
    last_lifecycle = daemon_state.get("last_lifecycle")
    if isinstance(last_lifecycle, dict) and not isinstance(
            last_lifecycle.get("ts"), str):
        last_lifecycle = None
    since_value = info.get("created") or daemon_state.get("since")
    if not isinstance(since_value, str) or not since_value:
        since_value = None
    payload = store.build_status(
        running=bool(info["active"]),
        since=since_value,
        state=guard_state,
        unit_profile=info.get("profile"),
        counters=counter_payload,
        redaction_hits=redaction_hits,
        open_incidents=open_incidents,
        last_event_ts=(daemon_state.get("last_event_ts")
                       if isinstance(daemon_state.get("last_event_ts"), str)
                       else None),
        last_lifecycle=last_lifecycle,
        )
    # additive summary inside major /1: the CLI needs coverage without
    # re-probing sources on every status call (SPEC §10.2, R-9)
    if coverage is not None:
        payload["coverage"] = {
            "sources_ref": "coverage.json",
            "unparsed_ratio": coverage.get("unparsed_ratio", 0.0),
            "dropped": coverage.get("dropped", 0),
            "blind": list(coverage.get("blind") or []),
            "notes": list(coverage.get("notes") or []),
            "redaction_hits": coverage.get("redaction_hits", 0),
        }
    # contract hashes (SPEC §10.2): deterministic, so status can prove which
    # rules/config produced the findings it reports
    try:
        payload["rules_hash"] = store.rules_hash()
        payload["config_hash"] = store.config_hash()
    except (OSError, ValueError) as exc:
        typer.secho(f"[warning] hashes недоступны: "
                    f"{type(exc).__name__}", fg=typer.colors.YELLOW, err=True)
    high_open = any(
        isinstance(item, dict) and item.get("severity") == "high"
        for item in open_incidents)
    degraded = (not info["active"]
                or (coverage is not None
                    and store.coverage_degraded(coverage)))
    if as_json:
        _echo_json(payload)
        raise typer.Exit(code=2 if high_open else (1 if degraded else 0))
    if not info["installed"]:
        typer.secho("[info] guard не установлен (vpn guard install)",
                    fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    run_state = "running" if info["active"] else "stopped"
    enabled = "enabled" if info["enabled"] else "disabled"
    typer.secho(f"[info] {info['unit']}: {run_state}, {enabled}, "
                f"profile={info.get('profile')}", fg=typer.colors.GREEN)
    typer.secho(f"[info] state={guard_state}"
                + (f", awg iface={iface}" if iface else
                   ", awg не обнаружен"),
                fg=typer.colors.CYAN)
    try:
        from ..mode import describe, read_mode
        mode_payload = read_mode()
    except Exception:  # noqa: BLE001 - mode.json is best-effort telemetry
        mode_payload = {}
    if mode_payload:
        typer.secho(f"[info] mode={describe(mode_payload)}",
                    fg=typer.colors.CYAN)
    lifecycle = payload.get("last_lifecycle")
    if isinstance(lifecycle, dict):
        typer.secho(f"[info] last_lifecycle={lifecycle.get('signal')} "
                    f"cause={lifecycle.get('cause')}",
                    fg=typer.colors.CYAN)
    counters_info = payload.get("counters") or {}
    typer.secho("[info] counters: " + ", ".join(
        f"{key}={value}" for key, value in counters_info.items()),
        fg=typer.colors.CYAN)
    for incident in open_incidents:
        if isinstance(incident, dict):
            typer.secho(f"[warning] open incident {incident.get('id')} "
                        f"root_cause={incident.get('root_cause')} "
                        f"severity={incident.get('severity', '-')}",
                        fg=typer.colors.YELLOW)
    if cached and cached.get("notify_delivery"):
        typer.secho(f"[info] notify-send: {cached['notify_delivery']}",
                    fg=typer.colors.CYAN)
    if coverage is not None:
        for note in coverage.get("notes") or []:
            typer.secho(f"[info] coverage: {note}", fg=typer.colors.CYAN)
    if high_open:
        typer.secho("[warning] есть открытый high-инцидент",
                    fg=typer.colors.RED)
        raise typer.Exit(code=2)
    if degraded:
        typer.secho("[warning] guard не активен: наблюдаемость деградирована",
                    fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


@guard_app.command()
def sources(as_json: bool = typer.Option(
                False, "--json", help="Machine-readable guard.coverage/1"),
            recheck: bool = typer.Option(
                False, "--recheck",
                help="Перепроверить источники сейчас (по умолчанию — "
                     "сохранённый coverage.json, если он есть)")) -> None:
    """Покрытие: права на журналы, файлы, sysfs AWG, слепые зоны."""
    from . import install as install_mod, store

    payload = None if recheck else store.read_coverage()
    if payload is None:
        install_state = install_mod.load_install_state()
        install_hits = 0
        if install_state and isinstance(install_state.get("redaction_hits"), int):
            install_hits = max(0, int(install_state["redaction_hits"]))
        hits = store.redaction_hits_total(install_hits)
        payload = collect_coverage(install_state=install_state,
                                   redaction_hits=hits)
        try:
            store.write_coverage(payload)
        except OSError as exc:
            typer.secho(f"[warning] coverage.json не записан: "
                        f"{exc.strerror or exc}", fg=typer.colors.YELLOW)

    degraded = store.coverage_degraded(payload)
    if as_json:
        _echo_json(payload)
        raise typer.Exit(code=1 if degraded else 0)

    for entry in payload["sources"]:
        status_text = entry["status"]
        color = {"ok": typer.colors.GREEN,
                 "off": typer.colors.CYAN}.get(status_text,
                                               typer.colors.YELLOW)
        note = entry.get("note")
        line = f"[{status_text}] {entry['name']}"
        if note:
            line += f" — {note}"
        typer.secho(line, fg=color)
    typer.echo("signals: " + ", ".join(
        f"{name}={value}" for name, value in payload["signals"].items()))
    if payload.get("blind"):
        typer.secho("[warning] слепые зоны: " + ", ".join(payload["blind"]),
                    fg=typer.colors.YELLOW)
    for note in payload.get("notes", []):
        typer.secho(f"[info] {note}", fg=typer.colors.CYAN)
    raise typer.Exit(code=1 if degraded else 0)


# --- WP-G5: rules / export / selftest ----------------------------------------

@guard_app.command()
def rules(as_json: bool = typer.Option(
              False, "--json", help="Machine-readable каталог + пороги + хэши"),
          verbose: bool = typer.Option(
              False, "--verbose",
              help="Печатать эффективный конфиг целиком")) -> None:
    """Каталог G-01…G-12, текущие пороги, rules_hash/config_hash.

    Хэши детерминированы (каталог + эффективный конфиг) и не содержат
    секретов: конфиг guard — только пороги и флаги (SPEC §10.2/§10.3).
    """
    from . import report

    payload = report.rules_payload()
    if as_json:
        _echo_json(payload)
        raise typer.Exit(code=0)
    for line in report.render_rules(payload, verbose=verbose):
        typer.echo(line)
    raise typer.Exit(code=0)


@guard_app.command()
def export(since: str = typer.Option(
               "7d", "--since", help="Окно findings: 30m, 24h, 7d"),
           output: Optional[str] = typer.Option(
               None, "-o", "--output",
               help="Файл *.json или каталог (manifest/findings/coverage); "
                    "'-' — stdout"),
           keep_hosts: bool = typer.Option(
               False, "--keep-hosts",
               help="Осознанный opt-in: оставить хосты эндпоинтов "
                    "(по умолчанию <host>)")) -> None:
    """Обезличенный бандл findings+coverage ≤ 5 МБ (SPEC §11.6, AC-13).

    Хосты заменяются на <host> по умолчанию; пост-сканер секретов
    блокирует экспорт кодом 2 и печатает только пути полей.  Ключи
    собственного контракта (*_hash/*_sha256, sha256:<64 hex>) — не секреты.
    """
    from . import reader, store

    try:
        seconds = reader.parse_since(since)
    except ValueError as exc:
        typer.secho(f"[error] --since: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    findings_store = store.FindingsStore(path=store.findings_path())
    items = findings_store.read(since_ts=time.time() - seconds, limit=0)
    stored_coverage = store.read_coverage()
    bundle = store.build_export(
        items, stored_coverage, keep_hosts=keep_hosts, since=since)
    # gate twice: the bundle as it would be written **and** the source
    # artefacts — a secret already on disk blocks the export (AC-13)
    hits = sorted(set(store.export_hits(bundle))
                  | set(store.input_export_hits(items, stored_coverage)))
    if hits:
        for path in hits:
            typer.secho(f"[error] секрет в бандле: {path}",
                        fg=typer.colors.RED, err=True)
        typer.secho("[error] export заблокирован (exit 2): значения не "
                    "печатаются, только пути полей", fg=typer.colors.RED,
                    err=True)
        raise typer.Exit(code=2)
    manifest = bundle["manifest"]
    if output == "-":
        typer.echo(json.dumps(bundle, ensure_ascii=False, sort_keys=True,
                              indent=2, default=str))
        raise typer.Exit(code=0)
    target = Path(output).expanduser() if output else None
    try:
        path, size = store.write_export(bundle, target)
    except OSError as exc:
        typer.secho(f"[error] export не записан: {exc.strerror or exc}",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    if size > store.EXPORT_MAX_BYTES:
        typer.secho(f"[warning] бандл больше {store.EXPORT_MAX_BYTES} байт",
                    fg=typer.colors.YELLOW, err=True)
    typer.secho(f"[ok] export: {path} ({size} байт, "
                f"findings={manifest.get('findings')}"
                + (f", dropped={manifest.get('findings_dropped')}"
                   if manifest.get("findings_dropped") else "")
                + f", keep_hosts={bool(keep_hosts)})",
                fg=typer.colors.GREEN)
    raise typer.Exit(code=0)


def run_selftest(config_root: Optional[Path] = None) -> Tuple[
        bool, List[Dict[str, Any]]]:
    """Validate schemas/parser/rules/post-scanner **from the installed package**.

    Returns ``(ok, checks)``; each check is ``{check, ok, detail}``.  No live
    logs, no root, no network — this is the AC-12 probe that ``pip install``
    delivered ``vpnm.guard.schemas`` as package data (SPEC §5.4).
    """
    from . import events, parse, redact, schema, store

    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # 1. schemas: all seven files present, loadable, hashes canonical
    missing = [name for name in events.SCHEMA_FILES
               if not schema.schema_path(name).is_file()]
    if missing:
        add("schemas", False, f"нет: {', '.join(missing)}")
    else:
        try:
            hashes = schema.schema_hashes()
            bad = [name for name, value in hashes.items()
                   if not re.fullmatch(r"sha256:[0-9a-f]{64}", value)]
            add("schemas", not bad and len(hashes) == len(events.SCHEMA_FILES),
                f"{len(hashes)}/{len(events.SCHEMA_FILES)} схем"
                + (f", битый хэш: {', '.join(bad)}" if bad else ""))
        except (OSError, ValueError) as exc:
            add("schemas", False, f"{type(exc).__name__}: {exc}")

    # 2. parser: structured/app-regex corpus subset, events validate
    try:
        parser = parse.JournalParser(
            proto_hints={"vpnm-selftest.service": "xray"})
        base = {
            "PRIORITY": "6",
            "__REALTIME_TIMESTAMP": "1794830403412345",
            "__MONOTONIC_TIMESTAMP": "812345678",
            "_SYSTEMD_USER_UNIT": "vpnm-selftest.service",
            "_SYSTEMD_INVOCATION_ID": "selftest",
        }
        samples = [
            dict(base, MESSAGE="Xray 1.8.6 started"),
            dict(base, MESSAGE='{"level":"warning","msg":"connection reset '
                               'by peer"}'),
        ]
        signals: List[str] = []
        errors: List[str] = []
        for record in samples:
            event = parser.parse(record)
            if event is None:
                errors.append("unparsed sample")
                continue
            signals.append(str(event.get("signal")))
            errors.extend(schema.validate(event, events.EVENT_SCHEMA))
        add("parser", not errors and signals == ["app_banner_ok",
                                                 "conn_reset"],
            f"signals={signals}" + (f", errors={errors}" if errors else ""))
    except (OSError, ValueError, RuntimeError) as exc:
        add("parser", False, f"{type(exc).__name__}: {exc}")

    # 3. rules: catalogue complete, enums known, hash deterministic
    try:
        from . import rules as rules_mod
        catalog = rules_mod.rule_catalog()
        ids = [str(item.get("id")) for item in catalog]
        unknown = [str(item.get("id")) for item in catalog
                   if item.get("category") not in events.CATEGORIES
                   or item.get("severity") not in events.SEVERITIES
                   or item.get("stage") not in events.STAGES]
        first = store.rules_hash(config_root)
        second = store.rules_hash(config_root)
        add("rules", ids == list(events.RULES) and not unknown
            and first == second,
            f"{len(ids)} правил, rules_hash={first}"
            + (f", неизвестные enum: {unknown}" if unknown else ""))
    except (OSError, ValueError, RuntimeError) as exc:
        add("rules", False, f"{type(exc).__name__}: {exc}")

    # 4. post-scanner (AC-13): reference contract values pass, FIXTURE blocks
    try:
        from . import rules as rules_mod
        reference = rules_mod.build_finding(
            rule="G-03", unit="vpnm-selftest.service", profile="selftest",
            endpoint={"host": "203.0.113.10", "port": 443},
            window_first="2026-11-16T12:00:00.000Z",
            window_last="2026-11-16T12:01:00.000Z", count=3,
            evidence=[{"kind": "signal_count", "signal": "conn_reset",
                       "value": 3}],
            fp_checks=["no auth_fail"], severity="high", confidence="medium",
            category="censorship_suspect", stage_hint="data_phase",
            hypothesis="после handshake данные обрываются",
            action="не рестартить; vpn probe",
            server=None, cross_vantage=False)
        clean_hits = redact.scan_secrets(reference)
        view, _hits = redact.export_view(reference)
        code_clean, _paths = redact.export_gate([view], None)
        # the gate is the last line of defence: it scans the payload as
        # handed in (a contract regression that skipped sanitizing), so the
        # dirty copy is passed **without** export_view
        dirty = dict(reference)
        dirty["hypothesis"] = "password=FIXTURE_PW_SELFTEST leak"
        code_dirty, dirty_paths = redact.export_gate([dirty], None)
        add("post-scanner", not clean_hits and code_clean == 0
            and code_dirty == 2 and bool(dirty_paths),
            f"reference hits={clean_hits}, block_hits="
            f"{len(dirty_paths)}")
    except (OSError, ValueError, RuntimeError) as exc:
        add("post-scanner", False, f"{type(exc).__name__}: {exc}")

    ok = bool(checks) and all(item["ok"] for item in checks)
    return ok, checks


@guard_app.command()
def selftest() -> None:
    """Проверка схем/парсера/правил/пост-сканера из установленного пакета.

    Exit 0 — всё зелёное; 1 — любая проверка провалилась (AC-12).  Живых
    логов, root и сети не требуется.
    """
    ok, checks = run_selftest()
    for item in checks:
        color = typer.colors.GREEN if item["ok"] else typer.colors.RED
        tag = "ok" if item["ok"] else "error"
        detail = f" — {item['detail']}" if item.get("detail") else ""
        typer.secho(f"[{tag}] {item['check']}{detail}", fg=color)
    if not ok:
        typer.secho("[error] selftest провален: guard работать не будет "
                    "корректно (см. строки выше)", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.secho("[ok] selftest пройден", fg=typer.colors.GREEN)
    raise typer.Exit(code=0)


# --- notify-test (WP: проверка канала доставки) -------------------------------

#: The banner is shaped like a real finding so it travels the exact same
#: severity/redaction path — but it is **never written** to findings.jsonl.
NOTIFY_TEST_RULE = "G-03"


def _banner_finding(severity: str) -> Dict[str, Any]:
    """Finding-shaped payload of the test banner (no secret, no endpoint)."""
    return {
        "rule": NOTIFY_TEST_RULE,
        "severity": severity,
        "confidence": "medium",
        "unit": "vpn-guard-notify-test",
        "profile": None,
        "hypothesis": "тестовое уведомление guard (notify-test)",
        "action": "если вы это видите — канал доставки работает",
    }


def _banner_notifier() -> Any:
    """A :class:`Notifier` whose hourly cooldown is disabled on purpose.

    ``notify-test`` checks **delivery**, not a finding: the 12/hour rate limit
    exists to keep a storm of findings from spamming the desktop, so a manual
    test must not be swallowed by it.  Everything else (opt-in, severity
    floor, availability, redaction) stays exactly as in the daemon.
    """
    from . import notify as notify_mod

    instance = notify_mod.Notifier()
    instance.max_per_hour = 10 ** 9
    return instance


def send_test_banner(severity: str = "high",
                     notifier: Optional[Any] = None) -> Tuple[
                         bool, Optional[str]]:
    """Deliver one test banner; returns ``(sent, reason)`` (never raises)."""
    instance = notifier if notifier is not None else _banner_notifier()
    sent = bool(instance.notify(_banner_finding(severity)))
    return sent, getattr(instance, "last_reason", None)


def _notify_reason_hint(reason: Optional[str]) -> str:
    if reason == "desktop_notifications_off":
        return ("уведомления выключены: включите \"notify\": {\"desktop\": "
                "true} в guard.json (путь покажет 'vpn guard install')")
    if reason == "below_severity_or_confidence":
        return ("severity ниже notify.min_severity — понизьте порог в "
                "guard.json или запустите с --severity high")
    if reason and ("notify-send" in reason or "D-Bus" in reason):
        return ("нет канала: установите libnotify-bin и запускайте из "
                "графической сессии с D-Bus ($XDG_RUNTIME_DIR/bus)")
    return ("проверьте: notify-send в PATH, D-Bus session bus, "
            "notify.desktop=true в guard.json")


@guard_app.command("notify-test")
def notify_test(severity: str = typer.Option(
        "high", "--severity",
        help="Severity тестового баннера (info|low|medium|high): ниже "
             "notify.min_severity — будет честно отказано")):
    """Отправить тестовый баннер (в обход cooldown) и напечатать статус.

    Проверяет фактическую доставку: opt-in (notify.desktop), порог
    min_severity, наличие notify-send и D-Bus session bus — ровно то, что
    решает судьбу настоящего уведомления.  Cooldown (12/час) намеренно
    обойдён: это ручная проверка канала, а не находка guard.  В
    findings.jsonl ничего не пишется.  Exit: 0 — баннер доставлен,
    1 — не доставлен (причина и что делать — в выводе), 2 — плохой
    --severity.
    """
    from . import events, store

    severity = severity.strip().lower()
    if severity not in events.SEVERITIES:
        typer.secho(f"[error] --severity: ожидается одно из "
                    f"{', '.join(events.SEVERITIES)}", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    desktop, min_severity = store.notify_settings()
    typer.echo(f"[info] notify: desktop={desktop} "
               f"min_severity={min_severity}")
    sent, reason = send_test_banner(severity)
    if sent:
        typer.secho("[ok] notify-test: баннер отправлен (cooldown обойдён)",
                    fg=typer.colors.GREEN)
        raise typer.Exit(code=0)
    typer.secho(f"[error] notify-test: баннер не отправлен — "
                f"{reason or 'причина не сообщена'}", fg=typer.colors.RED)
    typer.secho(f"[info] {_notify_reason_hint(reason)}", fg=typer.colors.CYAN)
    raise typer.Exit(code=1)


# --- WP-FAILOVER: ``vpn guard failover …`` (SPEC-20261118 §12 WP-F6) ---------

failover_app = typer.Typer(
    help="Авто-переключение каналов при отказе прямого (SPEC-20261118). "
         "Исполняющий агент: vpn-failover.service/path; по умолчанию выключен.")


def _failover_notifier() -> Any:
    from . import notify as notify_mod
    return notify_mod.Notifier()


@failover_app.command("status")
def failover_status(as_json: bool = typer.Option(
        False, "--json", help="Machine-readable статус")) -> None:
    """Состояние: enabled, pin, cooldown, backoff, свежесть лока.

    Read-only: очередь отложенных уведомлений досылается (SPEC §8.3),
    состояние агента не меняется.
    """
    from . import failover as failover_mod
    from . import notify as notify_mod, store

    payload = failover_mod.status()
    flushed = {}
    try:
        flushed = notify_mod.flush_pending_alerts(_failover_notifier())
    except (OSError, ValueError, RuntimeError) as exc:
        flushed = {"error": type(exc).__name__}
    pending = len(store.read_pending_alerts())
    if as_json:
        payload["pending_alerts"] = pending
        payload["flushed"] = flushed
        _echo_json(payload)
        raise typer.Exit(code=0)
    state = "ON" if payload["enabled"] else "OFF (по умолчанию)"
    typer.secho(f"[info] failover: {state}; профили триггера: "
                f"{', '.join(payload['trigger_profiles']) or '-'}",
                fg=typer.colors.CYAN)
    configured = payload.get("ladder") or []
    effective = payload.get("effective_ladder") or configured
    typer.secho("[info] Configured ladder: " + " -> ".join(configured),
                fg=typer.colors.CYAN)
    skipped = payload.get("skipped_profiles") or []
    tail = ""
    if skipped:
        tail = " (skipped: " + ", ".join(
            f"{entry['candidate']} [{entry['reason']}]" for entry in skipped) + ")"
    typer.secho("[info] Effective ladder:  " + " -> ".join(effective) + tail,
                fg=typer.colors.CYAN)
    pin = payload.get("pin") or {}
    if pin.get("profile"):
        typer.secho(f"[info] pin: {pin['profile']} с {pin.get('since')} "
                    f"(lockdown_active={bool(pin.get('lockdown_active'))})",
                    fg=typer.colors.CYAN)
    else:
        typer.secho("[info] pin: нет", fg=typer.colors.CYAN)
    backoff = payload.get("backoff") or {}
    if backoff.get("remaining_sec"):
        typer.secho(f"[warning] backoff: ещё {backoff['remaining_sec']} с "
                    f"(попытка {backoff.get('count')})", fg=typer.colors.YELLOW)
    typer.secho(f"[info] переключений за час: "
                f"{payload['switches_last_hour']} "
                f"(лимит по конфигу); отложенных уведомлений: {pending}",
                fg=typer.colors.CYAN)
    live = payload.get("liveness") or {}
    if live:
        typer.secho(f"[info] liveness (Detect-1): "
                    f"{'on' if live.get('enabled') else 'off'}, "
                    f"провалов {live.get('failures', 0)}/"
                    f"{live.get('threshold', 2)}, "
                    f"тик {live.get('interval_sec', 60)} с",
                    fg=typer.colors.CYAN)
    lock = payload.get("lockdown_now")
    lock_text = "нет данных/устарел" if lock is None else (
        "активен" if lock else "не активен")
    typer.secho(f"[info] server-verdict lockdown: {lock_text}",
                fg=typer.colors.CYAN)
    raise typer.Exit(code=0)


@failover_app.command("run")
def failover_run(once: bool = typer.Option(
        False, "--once", help="Один проход (обязательно; демон-режима в v1 нет)"),
        as_json: bool = typer.Option(False, "--json")) -> None:
    """Один проход движка: триггер → local_net → лестница → pin/blackout."""
    from . import failover as failover_mod
    from . import notify as notify_mod

    if not once:
        typer.secho("[error] поддержан только `failover run --once` "
                    "(юнит vpn-failover.service вызывает именно его)",
                    fg=typer.colors.RED)
        raise typer.Exit(code=2)
    notifier = _failover_notifier()
    outcome = failover_mod.run_once(notifier=notifier)
    for text in _render_failover_outcome(outcome):
        typer.echo(text)
    if outcome.status == "switched":
        try:
            notify_mod.flush_pending_alerts(notifier)  # §8.3 after success
        except (OSError, ValueError, RuntimeError):
            pass
    if as_json:
        _echo_json(outcome.as_dict())
    raise typer.Exit(code=0)


def _render_failover_outcome(outcome: Any) -> List[str]:
    lines = [f"[info] failover: {outcome.status} "
             f"(trigger={outcome.trigger.reason}"
             + (f"/{outcome.trigger.rule}" if outcome.trigger.rule else "")
             + ")"]
    for item in outcome.attempts:
        lines.append(f"  - {item.candidate}: {item.result}"
                     + (f" ({item.detail})" if item.detail else ""))
    if outcome.switched_to:
        lines.append(f"[success] канал: {outcome.switched_to}"
                     f" ({outcome.latency_ms} ms)")
    if outcome.backoff_sec:
        lines.append(f"[warning] total blackout: повтор через "
                     f"{outcome.backoff_sec} с")
    return lines


@failover_app.command("reset")
def failover_reset() -> None:
    """Снять pin/cooldown/backoff (SPEC §5.2)."""
    from . import failover as failover_mod

    state = failover_mod.reset()
    typer.secho(f"[ok] failover сброшен: pin=нет, backoff=0, "
                f"candidate_cooldown={len(state.get('candidate_cooldown') or {})}",
                fg=typer.colors.GREEN)
    raise typer.Exit(code=0)


def _set_failover(enabled: bool, apply: bool) -> None:
    from . import install as install_mod, store

    action = "включён" if enabled else "выключен"
    target = store.config_dir() / "guard.json"
    if not apply:
        typer.secho(f"[info] dry-run: failover будет {action}, "
                    f"guard.json: {target}; повторите с --apply",
                    fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)
    store.set_config_value("failover", "enabled", enabled)
    if enabled:
        interval = int(store.failover_settings().get("liveness_interval_sec") or 60)
        plan = install_mod.install_failover(apply=True,
                                            liveness_interval_sec=interval)
    else:
        plan = install_mod.remove_failover(apply=True)
    if plan.get("error"):
        typer.secho(f"[error] юниты: {plan['error']}", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    typer.secho(f"[ok] failover {action}; юниты "
                f"{install_mod.FAILOVER_SERVICE}/{install_mod.FAILOVER_PATH}/"
                f"{install_mod.FAILOVER_TIMER} обновлены",
                fg=typer.colors.GREEN)
    if enabled:
        typer.secho("[info] desktop-баннеры — отдельный opt-in "
                    "notify.failover_desktop=true; Telegram — "
                    "notify.telegram.enabled=true + bot_token_file 0600",
                    fg=typer.colors.CYAN)
    raise typer.Exit(code=0)


@failover_app.command("enable")
def failover_enable(apply: bool = typer.Option(
        False, "--apply", help="Записать конфиг и поставить юниты")) -> None:
    """Включить агент (config.failover.enabled=true) и юниты."""
    _set_failover(True, apply)


@failover_app.command("disable")
def failover_disable(apply: bool = typer.Option(
        False, "--apply", help="Записать конфиг и снять юниты")) -> None:
    """Выключить агент и снять юниты (guard продолжает работать)."""
    _set_failover(False, apply)


@failover_app.command("test-notify")
def failover_test_notify() -> None:
    """Проверить каналы уведомлений failover (desktop opt-in + Telegram)."""
    from . import notify as notify_mod

    notifier = notify_mod.Notifier()
    desktop = notifier.send_failover(
        "VPN failover: тест", "канал desktop работает", urgency="normal")
    tg_ok, tg_reason = notifier.send_telegram(
        "🔄 [client: test] тестовое уведомление failover")
    desktop_text = "sent" if desktop else (
        "не отправлен: " + str(notifier.last_reason or "opt-in off"))
    typer.echo(f"[info] desktop: {desktop_text}")
    typer.secho(f"[{'ok' if tg_ok else 'warning'}] telegram: "
                f"{'sent' if tg_ok else tg_reason}",
                fg=typer.colors.GREEN if tg_ok else typer.colors.YELLOW)
    raise typer.Exit(code=0 if (desktop or tg_ok) else 1)


guard_app.add_typer(failover_app, name="failover")
