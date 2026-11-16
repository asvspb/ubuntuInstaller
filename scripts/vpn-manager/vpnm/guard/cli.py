"""``vpn guard`` CLI surface (WP-G1: install/remove/status/sources).

Heavy modules are imported lazily inside the commands so that CLI startup
stays light.  The full command set (``run``/``findings``/``explain``/…)
arrives in WP-G3+; this module wires what WP-G1 owns:

- ``install``/``remove`` — the ``vpn-guard.service`` user unit with the
  ``hardened`` → ``compat`` fallback that is only taken on an *observed*
  start failure (AC-17);
- ``status`` — ``guard.status/1``: running/state/profile/counters (exit
  0 ok / 1 degraded / 2 high open);
- ``sources`` — ``guard.coverage/1``: per-source health, honest
  ``denied``/``off``/``missing`` instead of "всё хорошо" (R-9).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    """``state.json.active`` — the channel the user last brought up."""
    from . import reader
    path = reader.config_dir() / "state.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    active = payload.get("active") if isinstance(payload, dict) else None
    return active if isinstance(active, str) and active else None


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
def status(as_json: bool = typer.Option(
        False, "--json", help="Machine-readable guard.status/1")) -> None:
    """Состояние guard: running/state/profile/coverage (exit 0/1/2)."""
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
    payload = store.build_status(
        running=bool(info["active"]),
        since=info.get("created"),
        state=guard_state,
        unit_profile=info.get("profile"),
        counters={"events": 0, "unparsed": 0, "dropped": 0,
                  "findings": 0, "suppressed": 0},
        redaction_hits=redaction_hits,
        open_incidents=(),
        last_event_ts=None,
        )
    degraded = not info["active"]
    if as_json:
        _echo_json(payload)
        raise typer.Exit(code=1 if degraded else 0)
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
    if cached and cached.get("notify_delivery"):
        typer.secho(f"[info] notify-send: {cached['notify_delivery']}",
                    fg=typer.colors.CYAN)
    if degraded:
        typer.secho("[warning] guard не активен: наблюдаемость деградирована",
                    fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)


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
