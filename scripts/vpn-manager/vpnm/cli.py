"""vpn-manager CLI (typer).

Working: profile import/list/remove/show/pull, up/down/switch/status,
install-bins, install --linger, sysproxy on/off/status, doctor (WP-D),
repair (diagnose-to-log + restore traffic/DNS), interactive TUI when
invoked without arguments.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional, Tuple

import typer

from . import profiles
from .guard.cli import guard_app
from .links import LinkError, parse_link

AUTOSTART_ACTIVE = "__active__"  # sentinel: --autostart without a value


def _rewrite_bare_autostart(argv: list) -> list:
    """Rewrite a valueless '--autostart' into '--autostart=<sentinel>'.

    click/typer cannot express an option whose value is optional, so the
    bare form ('vpn install --autostart') is normalized before parsing;
    the sentinel means "use the currently active profile".
    """
    out = []
    for i, tok in enumerate(argv):
        if (tok == "--autostart"
                and (i + 1 == len(argv) or str(argv[i + 1]).startswith("-"))):
            out.append(f"--autostart={AUTOSTART_ACTIVE}")
        else:
            out.append(tok)
    return out


class VpnTyper(typer.Typer):
    """Typer app that normalizes the valueless '--autostart' form on entry."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        import sys
        sys.argv[1:] = _rewrite_bare_autostart(sys.argv[1:])
        return super().__call__(*args, **kwargs)


app = VpnTyper(
    help="vpn-manager: клиентские VPN-профили (Xray Reality/XHTTP, "
         "Hysteria2) через локальный SOCKS + системные split-схемы "
         "(AWG, xray-split) и каналы (zapret, CDN, ZeroTier). "
         "Без аргументов — интерактивное TUI-меню.",
    epilog="""
Политика ноутбука (2026-11-11): при загрузке системы — только чистый
трафик провайдера. Любой VPN включается вручную и живёт до выключения
или ребута; автостарт всех VPN-юнитов отключён.

Протоколы, которые можно активировать:

AmneziaWG — UDP/443 (через релей) или UDP/51825 (exit). Активация: vpn start
(туннель + доменный split) либо vpn amneziawg tunnel on.

Reality — VLESS Reality, TCP/443, tun xray0. Активация: vpn start reality.

XHTTP — VLESS XHTTP, TCP/8443, tun xray0. Активация: vpn start xhttp.

Hysteria2 — QUIC UDP/443, socks 127.0.0.1:2083. Активация: vpn up asv2-hy2
(имя профиля — из vpn profile list).

CDN (ws) — VLESS+WS за Cloudflare, TCP/443. ХОЛОДНЫЙ РЕЗЕРВ (DECISION Q6,
2026-11-20): origin не развёрнут, канал не поднимается; команда vpn scheme cdn
работает только при живом origin и профиле CDN.

RU-релей — AWG до релея, дальше policy релея. Активация: vpn start relay;
socks-фолбэк Reality: vpn start relay --proto reality.

Профиль для 'vpn up' — из 'vpn profile list'; его транспорт (Reality/XHTTP/
Hysteria2/CDN) и есть протокол, порт socks смотрите там же.

Активация VPN — из вкладки терминала:

  vpn start                  # AWG + доменный split (по умолчанию)
  vpn start relay            # RU-релей: весь трафик → релей → exit (ADR-007)
  vpn start reality          # Reality-split: tun xray0, TCP 443
  vpn start xhttp            # XHTTP-split: tun xray0, TCP 8443
  vpn up <profile>           # один socks-профиль (Reality/XHTTP/Hy2/CDN)
  vpn sysproxy on            # socks-профиль → системный прокси браузера
  vpn status                 # что включено + внешний IP
  vpn reset                  # выключить всё (чистый старт)

  Вкладку можно закрыть сразу после запуска: VPN живёт в systemd-юните,
  действует на всю систему (не только на эту вкладку) и держится до
  'vpn reset' или перезагрузки ОС.

Типовая сессия — включить VPN:

  vpn probe                  # замерить задержки профилей и включить
                             # самый быстрый (+ системный прокси сам
                             # последует за активным профилем)

  # либо по шагам:
  vpn up <profile>           # запустить профиль (socks на 127.0.0.1:208x)
  vpn sysproxy on            # направить системный прокси браузера в socks
  vpn switch <profile>       # переключиться на другой профиль на лету
  vpn amneziawg tunnel on    # AWG-туннель + доменный split: зоны из
                             # /opt/awg-split/domains.txt (ходовые TLD
                             # com/org/net/io/dev/ai + youtube/telegram/google)
                             # через туннель, GLM-кластер и RU — напрямую.
                             # Сам поднимает DNS-цепочку (dnsmasq+DoH с
                             # fallback 77.88.8.8). --no-split — только туннель.
                             # tunnel off демонтирует split ДО остановки
                             # туннеля и возвращает DNS провайдеру

  # чистый DNS через DoH (опционально; скрывает резолвинг от провайдера):
  vpn doh on                 #resolved → dnsmasq → cloudflared → Cloudflare DoH
  vpn doh guard on           #при загрузке DNS гарантированно у провайдера
                             #(drop-in не переживает ребут — DNS не мёртв)

Типовая сессия — выключить VPN:

  vpn amneziawg tunnel off   # опустить AWG-туннель
  vpn down                   # остановить профили и релей (системный прокси
                             # выключится; vpn-guard.service остаётся жив)
  vpn doh off                # DNS мгновенно вернётся к провайдеру (DHCP),
                             # сервисы остановятся; статус: vpn doh status
  vpn reset                  # выключить ВСЁ: туннели, split, sysproxy, DoH —
                             # состояние «как после перезагрузки ОС»

Предустановленные конфигурации (зеркала 'vpn reset'):

  vpn scheme awg              # AWG-схема: туннель + доменный split
                              # (= 'vpn start'); снимает xray-split
  vpn scheme xray-split <profile>
                              # split через tun xray0: Reality (443) или
                              # XHTTP (8443). <profile> — имя из
                              # 'vpn profile list' (напр. 'phone',
                              # 'xhttp-web'); без имени — первый подходящий;
                              # домены через туннель, RU/GLM напрямую
  vpn scheme cdn              # канал выживания: CDN-профиль (ws) + системный
                              # прокси (профиль определяется автоматически)
  vpn scheme proxy <profile>  # vpnm как ОБЩИЙ системный прокси по
                              # заданному протоколу (весь браузер через него)
  vpn scheme private <profile># vpnm как ЧАСТНЫЙ прокси: sysproxy не трогается,
                              # печать socks-адреса для прокси-приложений
                              # браузера (расширения/профили/vpncli)

Диагностика:

  vpn monitor                # матрица каналов: vpnm / cdn / zerotier /
                             # amneziawg / xray-split / zapret / dns-doh
                             # (статусы: ✔ / ○ готов / — нет / ✖ ошибка)
  vpn status                 # активный профиль, unit, внешний IP (через socks)
  vpn doctor                 # бинарники, порты, ssh-хост, DNS — всё разом
  vpn repair                 # снимок состояния в лог + вернуть трафик
                             # (починить DNS без правки resolved.conf)
  vpn speed                  # живой замер скорости: все каналы, несколько
                             # эндпоинтов, real-time таблица (bulk, не latency)
  vpn probe --no-switch      # замерить задержки, ничего не переключать
  vpn scout                  # активная разведка маршрутов (read-only):
                             # TCP/TLS, QUIC, AWG + вердикт сервера →
                             # RECOMMENDED/FALLBACK и «отсеяны» с причинами;
                             # 'vpn up --scout [--dry-run]' — сразу поднять

Канал выживания (CDN за Cloudflare, переживает блок IP asv1):

  vpn up cdn-home            # socks 127.0.0.1:2081; sysproxy последует;
                             # браузеру нужен перезапуск после смены порта
  vpn monitor                # строка 'cdn': работает=через CF жив; не
                             # включён=резерв ('vpn up cdn-home'); amneziawg
                             # показывает split: N зон, GLM/RU напрямую

Куда смотрит браузер (одной строкой):

  gsettings get org.gnome.system.proxy mode && \
    gsettings get org.gnome.system.proxy.socks host && \
    gsettings get org.gnome.system.proxy.socks port
  # none -> напрямую (RU+TLD-зоны через AWG-split); 2081 -> CDN;
  # 2080 -> Reality/XHTTP; 2083 -> Hysteria2. Chrome применяет
  # системный прокси ТОЛЬКО при старте — после смены перезапустить.

VPN для одной вкладки/браузера (остальное — напрямую):

  vpn up <profile>           # socks 127.0.0.1:208x; sysproxy НЕ включается
  vpncli on                  # только текущая вкладка терминала (env)
  vpn scheme private <profile>  # socks + готовая команда для Chrome

  Вкладка браузера — только расширением (SwitchyOmega/FoxyProxy, SOCKS5
  127.0.0.1:2080). Отдельный Chrome целиком — 'vpn scheme private <profile>'
  печатает --user-data-dir/--proxy-server. 'vpn start'/'scheme proxy' —
  это VPN для всего трафика.

Профили и сервер:

  vpn profile pull -H root@asv1   # подтянуть share-линки с VDS (asv1)
  vpn profile list                # сохранённые профили (без секретов)
  vpn profile show <profile>      # детали + конфиг (ПЕЧАТАЕТ СЕКРЕТЫ)
  vpn profile show <profile> --link  # одна share-ссылка (секрет)
  vpn profile config <profile>    # конфиг текстом (--native — JSON/YAML)
  vpn profile qr <profile>        # QR в терминале для телефона
  vpn qr --direct --awg --android # QR под маршрут×протокол×ОС (--relay, --reality, --hy, --xhttp)
  vpn profile export -o b.json    # бандл всех профилей (0600, секреты)
  vpn profile restore b.json      # перенос на другой ноутбук (--dry-run)
  vpn amneziawg status            # локальный туннель + сервер
  vpn amneziawg add-client <name> # создать клиента на сервере и забрать конфиг

Автозапуск профилей на логин (по умолчанию ВЫКЛ — см. политику):

  vpn autostart status | on | off

Нюанс «ру-сайты видят впн»: если включён системный прокси (vpn sysproxy
on), браузер гоняет ВСЁ через socks и RU-сайты видят иностранный IP.
Исключения по доменам уже внесены в:

  gsettings get org.gnome.system.proxy ignore-hosts

Добавить сайт в исключения:

  gsettings set org.gnome.system.proxy ignore-hosts \\
    "['localhost', '127.0.0.0/8', '::1', 'yandex.ru', <новый домен>, ...]"
""")
profile_app = typer.Typer(help="Профили: import/list/remove/show/pull "
                                "(share-линки с сервера)")
app.add_typer(profile_app, name="profile")
amneziawg_app = typer.Typer(help="Канал AmneziaWG: локальный split-туннель "
                                 "и клиенты на сервере")
app.add_typer(amneziawg_app, name="amneziawg")
# guard sub-app: light (typer + frozen contract); heavy modules load
# lazily inside the commands (vpnm/guard/cli.py).
app.add_typer(guard_app, name="guard")
# `vpn qr` / `vpn conf` (SPEC-20261118-vpn-qr v1.1): резолвер и рендер живут
# в vpnm/qrgen/, модуль лёгкий (typer + stdlib), поэтому регистрируем на импорте.
from .qrgen.cli import conf_command, qr_command  # noqa: E402  (регистрация ниже)

app.command("qr", help="QR/URI клиентского профиля: маршрут × протокол × ОС "
                       "(офлайн, без секретов в выводе; v1.1 авто-сохраняет "
                       "PNG, --no-save отключает запись)")(qr_command)
app.command("conf", help="Локальный конфиг/URI профиля: маршрут × протокол × "
                         "ОС (0600 в artifacts, masked-вывод; --qr — плюс QR; "
                         "--reveal — сырой текст)")(conf_command)


@app.command("sync")
def sync_cmd(
        direct: bool = typer.Option(False, "--direct",
                                    help="Только прямой маршрут (exit-узел)"),
        relay: bool = typer.Option(False, "--relay",
                                   help="Только релейный маршрут"),
        all_routes: bool = typer.Option(False, "--all",
                                        help="Оба маршрута (по умолчанию)"),
        exit_host: Optional[str] = typer.Option(
            None, "--exit-host", help="ssh exit-узла (иначе — сохранённый "
                                      "ssh_host из settings.json)"),
        relay_host: Optional[str] = typer.Option(
            None, "--relay-host", help="ssh релея (иначе — relay_ssh_host из "
                                       "settings.json)"),
        include_obfs: bool = typer.Option(
            False, "--include-obfs",
            help="также забрать obfs-профили hysteria2"),
        dry_run: bool = typer.Option(
            False, "--dry-run",
            help="Читать сервера, но ничего не писать локально"),
) -> None:
    """Подтянуть авторитетные серверные файлы (read-only; v1.1, D1).

    Единственная сетевая точка стека qr/conf: ``vds gateway links`` по ssh и
    scp клиентских ``*.conf`` из каталогов клиентов exit/релея. Сервера не
    мутируются; локальные файлы не удаляются — профили, которых больше нет
    на сервере, помечаются ``STALE (absent on server)`` с подсказкой
    архивации.
    """
    from . import sync as sync_mod
    routes: Tuple[str, ...]
    if direct and relay:
        _err("--direct и --relay несовместимы")
        raise typer.Exit(code=2)
    if direct:
        routes = ("direct",)
    elif relay:
        routes = ("relay",)
    else:
        routes = ("direct", "relay")  # --all и пустой выбор означают оба
    try:
        report = sync_mod.run_sync(
            exit_host=exit_host, relay_host=relay_host, routes=routes,
            include_obfs=include_obfs, dry_run=dry_run)
    except sync_mod.SyncError as exc:
        _err(str(exc))
        raise typer.Exit(code=1)
    mode_label = "dry-run" if dry_run else "synced"
    for route, section in report.items():
        if "error" in section:
            _warn(f"{route}: {section['error']}")
            continue
        host = section.get("host") or "?"
        typer.secho(f"── {route} ({host}) [{mode_label}] ──",
                    fg=typer.colors.CYAN)
        confs = section.get("confs") or {}
        for category in ("imported", "updated", "skipped"):
            for stem in confs.get(category) or []:
                (_ok if category == "imported" else _warn)(
                    f"{category} conf '{stem}'")
        for stem in confs.get("stale") or []:
            typer.secho(
                f"STALE (absent on server): {stem} — подсказка: "
                f"vpn profile archive {stem} --reason \"...\" --apply",
                fg=typer.colors.YELLOW)
        for name, action in section.get("links") or []:
            (_ok if action in ("imported", "updated") else
             (lambda m: typer.echo(m)))(f"{action} link '{name}'")
        for name in section.get("links_stale") or []:
            typer.secho(
                f"STALE (absent on server): {name} — подсказка: "
                f"vpn profile archive {name} --reason \"...\" --apply",
                fg=typer.colors.YELLOW)


def _awg_resolve_host() -> str:
    """Saved ssh host, else interactive question; saved back on success."""
    from . import amneziawg as awg
    from . import settings
    host = settings.get_host()
    if host:
        _ok(f"using saved host '{host}'")
        return host
    # resolve_host prompts and persists the answer into settings.json
    host = awg.resolve_host(typer.prompt)
    _ok(f"saved host '{host}' to settings")
    return host


@amneziawg_app.command("status")
def amneziawg_status() -> None:
    """AWG: локальный туннель + сервер (по ssh)."""
    from . import amneziawg as awg
    typer.secho("── ЛОКАЛЬНО ──", fg=typer.colors.CYAN)
    for line in awg.local_status_lines():
        typer.echo(line)
    typer.secho("── СЕРВЕР ──", fg=typer.colors.CYAN)
    try:
        host = _awg_resolve_host()
    except awg.AwgError as exc:
        _warn(f"сервер недоступен: {exc}")
        return
    lines = awg.server_status_lines(host)
    if lines is None:
        _warn(f"сервер недоступен (ssh {host} 'vds amneziawg status' "
              "failed)")
        return
    for line in lines:
        typer.echo(line)


@amneziawg_app.command("qr")
def amneziawg_qr(name: Optional[str] = typer.Argument(
        None, help="Имя инстанции/пира (по умолчанию — активный "
        "vpn-awg-split@<name>); секрет не печатается, идёт в qrencode "
        "через stdin")) -> None:
    """QR AWG-конфига в терминал: сканировать AmneziaVPN на телефоне/планшете.

    По умолчанию берётся конфиг АКТИВНОГО основного профиля
    (/etc/amnezia/amneziawg/<name>.conf работающего туннеля); с аргументом
    NAME — конфиг этого пира (fallback: ~/.awg/NAME.conf от add-client).
    Системные атрибуты (Table = off — split-механика ноута) вырезаются:
    парсер AmneziaVPN их не понимает. Требуется `qrencode`
    (`sudo apt install qrencode`).
    """
    from . import amneziawg as awg
    from .qr import QrError, render
    try:
        resolved, text = awg.active_conf_text(name)
        art = render(text)
    except (awg.AwgError, QrError) as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    typer.echo(f"── AWG: {resolved} (конфиг содержит секреты — "
               "не фотографируйте в общий чат) ──")
    typer.echo(art)


@amneziawg_app.command("add-client")
def amneziawg_add_client(name: str) -> None:
    """Создать клиента на сервере, конфиг придёт в ~/.awg/NAME.conf (0600)."""
    from . import amneziawg as awg
    try:
        host = _awg_resolve_host()
        conf_path = awg.server_add_client(host, name)
    except awg.AwgError as exc:
        _err(str(exc))
        raise typer.Exit(code=1)
    _ok(f"config saved: {conf_path} (0600)")
    _ok("импортируйте файл в AmneziaVPN (AmneziaWG, из файла)")


@amneziawg_app.command("remove-client")
def amneziawg_remove_client(name: str, apply: bool = typer.Option(
        False, "--apply", help="Actually remove on the server "
        "(server-side gate; without it the server only previews)")) -> None:
    """Удалить клиента на сервере (деструктивно — только с --apply)."""
    from . import amneziawg as awg
    try:
        host = _awg_resolve_host()
    except awg.AwgError as exc:
        _err(str(exc))
        raise typer.Exit(code=1)
    if apply and not typer.confirm(
            f"Remove client '{name}' on the server?", default=False):
        typer.echo("aborted")
        raise typer.Exit(code=1)
    rc, out, err = awg.server_remove_client(host, name, apply)
    if rc != 0:
        _err(f"ssh {host} remove-client failed (exit {rc}): "
             f"{(err or out).strip() or 'no error output'}")
        raise typer.Exit(code=1)
    for line in out.splitlines():
        typer.echo(line)
    _ok(f"server-side remove-client '{name}' done (apply={apply})")
    if awg.local_instance() == name:
        _warn(f"'{name}' — активный локальный инстанс; перезапустите "
              "туннель, чтобы он перечитал конфиг: sudo systemctl "
              f"restart {awg.AWG_UNIT_PREFIX}{name}")


@amneziawg_app.command("tunnel")
def amneziawg_tunnel(action: str = typer.Argument(
        ..., help="'on', 'off' or 'status'"),
        no_split: bool = typer.Option(
            False, "--no-split",
            help="Не применять/не снимать доменный split (ходовые зоны .com/"
                 ".org/.net + youtube/telegram через AWG)")) -> None:
    """AWG-туннель: vpn amneziawg tunnel on|off|status.

    При on/off дополнительно применяется/демонтируется доменный split
    (15_domain-split.sh): зоны из /opt/awg-split/domains.txt (ходовые
    TLD .com/.org/.net/.io/.dev/.ai + youtube/telegram/google) идут
    через AWG, остальное — напрямую. Правка списка — domains.txt,
    повторный запуск скрипта.
    """
    from . import amneziawg as awg
    action = action.strip().lower()
    if action not in ("on", "off", "status"):
        _err("expected 'on', 'off' or 'status'")
        raise typer.Exit(code=2)
    if action == "status":
        for line in awg.tunnel_status_lines():
            typer.echo(line)
        return
    if action == "off":
        # split снимать ДО остановки туннеля (nft/fwmark смотрят на iface)
        if not no_split:
            ok_s, msg_s = awg.domain_split_apply("off")
            (_ok if ok_s else _warn)(msg_s)
        ok, message = awg.tunnel_control(action)
        if ok:
            _ok(message)
        else:
            _err(message)
            raise typer.Exit(code=1)
        return
    ok, message = awg.tunnel_control(action)
    if not ok:
        _err(message)
        raise typer.Exit(code=1)
    _ok(message)
    if not no_split:
        ok_s, msg_s = awg.domain_split_apply("on")
        (_ok if ok_s else _warn)(msg_s)


def _err(msg: str) -> None:
    typer.secho(f"error: {msg}", fg=typer.colors.RED, err=True)


def _ok(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.GREEN)


def _warn(msg: str) -> None:
    typer.secho(f"warning: {msg}", fg=typer.colors.YELLOW)


@profile_app.command("import")
def profile_import(link: str, name: Optional[str] = typer.Option(
        None, "--name", "-n", help="Override profile name"),
        replace: bool = typer.Option(
            False, "--replace", help="Overwrite an existing profile name")) -> None:
    """Импорт share-ссылки как профиля: vpn profile import <link> --name NAME."""
    try:
        parsed = parse_link(link)
    except LinkError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    try:
        if replace:
            try:
                profiles.remove(name or parsed.name)
            except profiles.ProfileStoreError:
                pass  # nothing to replace
        record = profiles.add(parsed, raw_link=link, name=name)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    _ok(f"added profile '{record['name']}' "
        f"(protocol={record['protocol']}, socks_port={record['socks_port']})")


@profile_app.command("list")
def profile_list() -> None:
    """Список профилей (имя, протокол, socks-порт — без секретов)."""
    rows = profiles.list_profiles()
    if not rows:
        typer.echo("no profiles")
        return
    for row in rows:
        typer.echo(f"{row['name']}\t{row['protocol']}\tsocks:{row['socks_port']}")


@profile_app.command("remove")
def profile_remove(name: str, force: bool = typer.Option(
        False, "--force", "-f", help="Skip confirmation")) -> None:
    """Удалить профиль (останавливающий клиент тоже остановится)."""
    if not force:
        confirm = typer.confirm(f"Remove profile '{name}'?", default=False)
        if not confirm:
            typer.echo("aborted")
            raise typer.Exit(code=1)
    try:
        record = profiles.remove(name)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    from . import runners
    if runners.get_active() == name:
        try:
            runners.down()
        except runners.RunnerError as exc:
            _warn(f"could not stop the running client: {exc}")
        else:
            _ok(f"stopped running client '{name}'")
    _ok(f"removed profile '{record['name']}'")


@profile_app.command("show")
def profile_show(name: str,
                 show_link: bool = typer.Option(
                     False, "--link",
                     help="Печать исходную share-ссылку (СЕКРЕТ)")) -> None:
    """Детали профиля + сгенерированный конфиг (ПЕЧАТАЕТ СЕКРЕТЫ)."""
    try:
        record = profiles.get(name)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    if show_link:
        typer.echo("warning: ссылка содержит секреты (UUID/пароль) — "
                   "не вставляйте в чаты/тикеты", err=True)
        typer.echo(str(record["link"]))
        return
    try:
        parsed = parse_link(record["link"])
    except LinkError as exc:
        _err(f"stored link no longer parses: {exc}")
        raise typer.Exit(code=2)
    from .xclient import generate
    config_text = generate(parsed, int(record["socks_port"]))
    typer.echo(json.dumps(
        {k: v for k, v in record.items() if k != "link"}, indent=2))
    typer.echo("---")
    typer.echo(config_text)


def _profile_config_text(name: str, native: bool) -> str:
    """Share-ссылка (по умолчанию) или сгенерированный конфиг профиля."""
    record = profiles.get(name)
    if not native:
        return str(record["link"])
    from .xclient import generate
    return generate(parse_link(str(record["link"])),
                    int(record["socks_port"]))


@profile_app.command("config")
def profile_config(name: str,
                   native: bool = typer.Option(
                       False, "--native",
                       help="Сгенерированный JSON/YAML вместо share-ссылки")) -> None:
    """Конфиг профиля текстом для копирования (содержит секреты).

    По умолчанию — share-ссылка (`vless://…` / `hysteria2://…`), её
    импортируют клиенты (v2rayNG, Hiddify, Streisand). `--native` —
    сгенерированный конфиг xray/hysteria для ручного копирования.
    """
    try:
        text = _profile_config_text(name, native)
    except (profiles.ProfileStoreError, LinkError, ValueError) as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    typer.echo("warning: конфиг содержит секреты (UUID/пароль) — "
               "не вставляйте в чаты/тикеты", err=True)
    typer.echo(text)


@profile_app.command("qr")
def profile_qr(name: str,
               native: bool = typer.Option(
                   False, "--native",
                   help="QR сгенерированного конфига (плотный) вместо ссылки")) -> None:
    """QR профиля в терминале: сканировать телефоном (v2rayNG/Hiddify).

    По умолчанию кодируется share-ссылка; требуется `qrencode`
    (`sudo apt install qrencode`), секрет идёт через stdin, не в argv.
    """
    try:
        text = _profile_config_text(name, native)
    except (profiles.ProfileStoreError, LinkError, ValueError) as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    if native:
        _warn("QR JSON-конфига плотный — сканеры могут не взять; "
              "для телефона обычно нужна share-ссылка")
    from .qr import QrError, render
    try:
        art = render(text)
    except QrError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    typer.echo(art)


@profile_app.command("export")
def profile_export(
        out: Optional[str] = typer.Option(
            None, "--out", "-o",
            help="Файл бандла (по умолчанию ~/vpn-profiles-<дата>.json)"),
        stdout: bool = typer.Option(
            False, "--stdout",
            help="JSON в stdout — для шифрования (gpg/age), не для терминала"),
        force: bool = typer.Option(
            False, "--force", "-f", help="Перезаписать существующий файл")) -> None:
    """Сохранить все профили в бандл (бэкап/перенос; содержит секреты).

    Бандл — JSON со share-ссылками: из него восстанавливаются и профили, и
    конфиги (они генерируются заново). Файл всегда 0600.
    """
    payload = profiles.export_bundle()
    if not payload["profiles"]:
        _err("профилей нет — нечего экспортировать")
        raise typer.Exit(code=1)
    if stdout:
        typer.echo("warning: в stdout share-ссылки с секретами — "
                   "направьте вывод в gpg/age, не в лог", err=True)
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    target = Path(out) if out else profiles.default_bundle_path()
    if target.exists() and not force:
        _err(f"файл уже существует: {target} (--force для перезаписи)")
        raise typer.Exit(code=2)
    try:
        profiles.save_bundle(payload, target)
    except OSError as exc:
        _err(f"не записал {target}: {exc}")
        raise typer.Exit(code=2)
    _ok(f"экспортировано профилей: {len(payload['profiles'])} → {target} "
        "(0600, содержит секреты; передавайте scp/age, не почтой)")


@profile_app.command("restore")
def profile_restore(
        file: str = typer.Argument(
            ..., metavar="FILE|-",
            help="JSON-бандл от 'vpn profile export'; '-' — stdin"),
        replace: bool = typer.Option(
            False, "--replace", help="Перезаписать совпадающие имена"),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Показать план, ничего не менять"),
        yes: bool = typer.Option(
            False, "--yes", "-y", help="Без подтверждения")) -> None:
    """Влить бандл: новые профили добавить, совпадающие — skip/--replace."""
    if file == "-":
        try:
            payload = json.loads(sys.stdin.read())
        except json.JSONDecodeError as exc:
            _err(f"stdin: некорректный JSON: {exc}")
            raise typer.Exit(code=2)
    else:
        path = Path(file)
        if not path.exists():
            _err(f"файл не найден: {path}")
            raise typer.Exit(code=2)
        try:
            mode = path.stat().st_mode & 0o777
            if mode & 0o077:
                _warn(f"{path} доступен другим (0o{mode:03o}) — "
                      "перенесите в 0600: chmod 600")
            payload = profiles.load_bundle(path)
        except (OSError, profiles.ProfileStoreError) as exc:
            _err(str(exc))
            raise typer.Exit(code=2)
    try:
        preview = profiles.import_bundle(payload, replace=replace,
                                         dry_run=True)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    added, replaced = preview["added"], preview["replaced"]
    if added:
        typer.echo("добавить: " + ", ".join(added))
    if replaced:
        typer.echo("заменить: " + ", ".join(replaced))
    if preview["skipped"]:
        typer.echo("пропустить (уже есть): " + ", ".join(preview["skipped"]))
    if dry_run:
        typer.echo("dry-run: store не изменён")
        return
    if not added and not replaced:
        _ok("нечего импортировать — все профили уже есть")
        return
    if not yes and not typer.confirm(
            f"Импортировать {len(added) + len(replaced)} профилей?",
            default=True):
        typer.echo("aborted")
        raise typer.Exit(code=1)
    try:
        result = profiles.import_bundle(payload, replace=replace)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    _ok(f"импорт завершён: добавлено {len(result['added'])}, "
        f"заменено {len(result['replaced'])}, "
        f"пропущено {len(result['skipped'])}; "
        "запуск — 'vpn up <profile>' (см. 'vpn profile list')")


# --- lifecycle (WP-C) --------------------------------------------------------

def _sync_sysproxy_after(name: str, action: str) -> None:
    """Auto-apply GNOME sysproxy to the new profile when the flag is on."""
    from . import runners, sysproxy
    try:
        port = runners._sync_sysproxy()
    except sysproxy.SysproxyError as exc:
        _warn(f"could not update the system proxy after {action}: {exc}")
        return
    if port is not None:
        _ok(f"system proxy updated → 127.0.0.1:{port} (profile '{name}')")


def _sync_sysproxy_off_after() -> None:
    """Auto-disable GNOME sysproxy after 'down' when the flag is on."""
    from . import runners, sysproxy
    try:
        turned_off = runners._sync_sysproxy_off()
    except sysproxy.SysproxyError as exc:
        _warn(f"could not turn the system proxy off: {exc}")
        return
    if turned_off:
        _ok("system proxy off")


@app.command()
def up(name: Optional[str] = typer.Argument(
        None, help="Профиль из 'vpn profile list'; с --scout можно не "
                   "указывать"),
        scout: bool = typer.Option(
            False, "--scout",
            help="Сначала активная разведка (vpn scout), затем поднять "
                 "рекомендованный маршрут"),
        dry_run: bool = typer.Option(
            False, "--dry-run",
            help="С --scout: только показать, что было бы запущено")) -> None:
    """Запустить профиль: vpn up <profile> (socks-порт см. в vpn profile list).

    Поднимает только локальный socks-клиент: маршруты и системный прокси не
    меняются (sysproxy включается отдельно, 'vpn sysproxy on'). Для VPN в
    одной вкладке терминала — 'vpncli on'; в браузере — расширение
    (SwitchyOmega/FoxyProxy) или отдельный Chrome с --proxy-server
    (см. 'vpn --help', блок про одну вкладку).

    ``vpn up --scout`` сначала выполняет read-only разведку маршрутов
    (probe TCP/TLS, QUIC, AWG) и поднимает рекомендованный; ``--dry-run``
    печатает только план. Если рекомендация — AWG, выполняется 'vpn start'
    (или 'vpn start relay' для релейного маршрута).
    """
    from . import runners
    if scout and name:
        _err("укажите либо профиль, либо --scout (не оба)")
        raise typer.Exit(code=2)
    if scout:
        from .scout.cli import build_report, plan_lines, up_target
        payload = build_report()
        target = up_target(payload)
        for line in plan_lines(payload):
            typer.echo(line)
        if dry_run:
            return
        if target["kind"] == "none":
            _err("scout: рекомендации нет — смотрите полный отчёт "
                 "'vpn scout'")
            raise typer.Exit(code=1)
        if target["kind"] == "awg":
            if target.get("route") == "relay":
                start("relay", proto="awg")
            else:
                start("awg")
            return
        name = target.get("profile")
    if not name:
        _err("укажите профиль: vpn up <profile> (или --scout)")
        raise typer.Exit(code=2)
    try:
        config_path = runners.up(name)
    except (profiles.ProfileStoreError, runners.RunnerError, LinkError) as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    _ok(f"started '{name}' (config: {config_path})")
    _sync_sysproxy_after(name, "up")
    # Auto-follow only applies when the sysproxy flag is on (clean-boot keeps
    # it off). A silently started client that no app routes through is the
    # most common "vpn up ran but VPN does not work" confusion — say it.
    if not runners.get_sysproxy():
        _warn("system proxy is OFF — traffic still goes direct; "
              "run 'vpn sysproxy on' to route apps through this client")


@app.command()
def down(name: Optional[str] = typer.Argument(None)) -> None:
    """Остановить туннели: профиль(и) и/или релей (guard не трогается).

    ``vpn down`` без аргумента — штатное выключение VPN: гасит все активные
    socks-профили и релейный AWG-инстанс (``vpn-awg-split@relay``), если он
    жив; ``vpn-guard.service`` продолжает работать.  ``vpn down <profile>`` —
    только указанный профиль. Идемпотентно.
    """
    from . import amneziawg as awg
    from . import mode as mode_mod
    from . import runners
    if name:
        try:
            stopped = runners.down(name)
        except (profiles.ProfileStoreError, runners.RunnerError) as exc:
            _err(str(exc))
            raise typer.Exit(code=2)
        _ok(f"stopped '{stopped}'")
        _sync_sysproxy_off_after()
        return

    failures: list = []
    stopped, profile_failures = runners.stop_all_profiles()
    if stopped:
        _sync_sysproxy_off_after()
        _ok("остановлены профили: " + ", ".join(stopped))
    failures.extend(profile_failures)

    relay_was_active = awg.instance_active(RELAY_INSTANCE)
    ok_r, msg_r = _stop_relay_contour()
    if relay_was_active:
        (_ok if ok_r else _err)(msg_r)
        if not ok_r:
            failures.append(msg_r)

    if failures:
        _err("не всё остановлено: " + "; ".join(failures))
        raise typer.Exit(code=1)
    if not stopped and not relay_was_active:
        mode_mod.clear_mode()
        typer.echo("нет активных туннелей (профили и релей не запущены)")
        return
    mode_mod.clear_mode()
    _ok("VPN выключен: профили и релей остановлены; vpn guard продолжает "
        "работать")


@app.command("scheme")
def scheme_cmd(preset: str = typer.Argument(
        ..., help="'awg' | 'xray-split' | 'xhttp-split' | 'cdn' | "
                  "'proxy <profile>' | 'private <profile>'"),
        profile: Optional[str] = typer.Argument(
            None, help="имя из 'vpn profile list': для xray-split — "
                       "Reality/XHTTP-профиль (без имени — первый), для "
                       "proxy/private — любой профиль")) -> None:
    """Предустановленные конфигурации (зеркала 'vpn reset').

    vpn scheme awg               — AWG-туннель + доменный split (то же,
                                   что 'vpn start'); снимает xray-split,
                                   если был поднят
    vpn scheme xray-split [профиль] — системный split для xray-транспорта:
                                   tun xray0 (xray 26.x) → Reality (443) или
                                   XHTTP (8443); профиль — имя из
                                   'vpn profile list' (например 'phone' или
                                   'xhttp-web'), без имени — первый;
                                   домены из /opt/awg-split/domains.txt через
                                   tun, RU/GLM напрямую; снимает AWG-схему
                                   (взаимно исключительны, SPEC-20261114).
                                   'xhttp-split' — алиас (авто-выбор XHTTP)
    vpn scheme cdn               — канал выживания: CDN-профиль (ws) +
                                   системный прокси на его socks (браузер —
                                   весь через CF); имя профиля определяется
                                   автоматически (cdn-home → *cdn* → первый ws)
    vpn scheme proxy <profile>   —vpnm как ОБЩИЙ системный прокси: поднять
                                   профиль по заданному протоколу + sysproxy on
    vpn scheme private <profile> — vpnm как ЧАСТНЫЙ прокси: поднять профиль,
                                   системный прокси НЕ трогать; печатает
                                   socks-адрес и варианты: расширение браузера
                                   (SwitchyOmega/FoxyProxy), отдельный Chrome
                                   --proxy-server, вкладка терминала 'vpncli on';
                                   остальной трафик остаётся в текущей схеме

    Все пресеты идемпотентны; 'vpn reset' — общий выключатель.
    """
    from . import runners
    from . import sysproxy
    preset = preset.strip().lower()

    if preset == "awg":
        ok_r, msg_r = _stop_relay_contour()
        (_ok if ok_r else _err)(msg_r)
        if not ok_r:
            raise typer.Exit(code=1)
        ok, msg = _apply_awg_scheme()
        if not ok:
            _err(f"{msg} — повторите 'vpn scheme awg'")
            raise typer.Exit(code=1)
        from . import amneziawg as awg
        instance = awg.local_instance() or awg.DEFAULT_LOCAL_INSTANCE
        _write_direct_mode("amneziawg", iface=instance,
                           unit=f"{awg.AWG_UNIT_PREFIX}{instance}.service")
        _ok("схема awg активна: TLD-зоны/youtube/telegram через AWG, "
            "GLM/RU напрямую")
        return

    if preset in ("xray-split", "xhttp-split"):
        from . import xsplit as xsplit_mod
        ok_r, msg_r = _stop_relay_contour()
        (_ok if ok_r else _err)(msg_r)
        if not ok_r:
            raise typer.Exit(code=1)
        # xhttp-split — исторический алиас: при автовыборе ограничиваем XHTTP.
        default_protocol = ("xray-xhttp" if preset == "xhttp-split" else None)
        ok, msg = xsplit_mod.apply(profile, default_protocol=default_protocol)
        (_ok if ok else _err)(f"{preset}: {msg}")
        if not ok:
            raise typer.Exit(code=1)
        chosen = xsplit_mod.active_profile(xsplit_mod.run_cmd)
        _write_direct_mode("xray", iface=xsplit_mod.DEV,
                           unit=(xsplit_mod.unit_name(chosen)
                                 if chosen else None))
        _ok("схема xray-split активна: split-зоны через tun xray0 → "
            "Reality/XHTTP, GLM/RU напрямую (kill-switch: остановка юнита "
            "снимает правила); откат — 'vpn scheme awg'/'vpn scheme reset'")
        _browser_restart_hint()
        return

    if preset == "cdn":
        cdn = profiles.resolve_cdn()
        if not cdn:
            _err("CDN-профиля нет — импортируйте ws-ссылку "
                 "('vpn profile pull -H <host>' или 'vpn profile import')")
            raise typer.Exit(code=2)
        try:
            runners.up(cdn)
        except (profiles.ProfileStoreError, runners.RunnerError,
                LinkError) as exc:
            _err(f"{cdn}: {exc}")
            raise typer.Exit(code=2)
        port = int(str(profiles.get(cdn)["socks_port"]))
        _sync_sysproxy_after(cdn, "scheme")
        _ok(f"схема cdn активна: профиль '{cdn}' через Cloudflare "
            f"(socks 127.0.0.1:{port}); exit — 'vpn reset'/'vpn down'")
        return

    if preset in ("proxy", "private"):
        if not profile:
            _err(f"expected 'vpn scheme {preset} <profile>' "
                 "(см. 'vpn profile list')")
            raise typer.Exit(code=2)
        try:
            config_path = runners.up(profile)
        except (profiles.ProfileStoreError, runners.RunnerError,
                LinkError) as exc:
            _err(str(exc))
            raise typer.Exit(code=2)
        record = profiles.get(profile)
        port = int(record["socks_port"])
        if preset == "proxy":
            sysproxy.on(port)
            runners.set_sysproxy(True)
            _ok(f"ОБЩИЙ системный прокси: socks 127.0.0.1:{port} "
                f"(профиль '{profile}', весь браузерный трафик через него; "
                "Chrome перезапустить)")
        else:
            _ok(f"ЧАСТНЫЙ прокси поднят: профиль '{profile}' "
                f"слушает socks 127.0.0.1:{port}")
            typer.echo("Системный прокси не тронут. Подключайте адрес "
                       "127.0.0.1:%d точечно:" % port)
            typer.echo("  • расширение-прокси в Chrome (SwitchyOmega и т.п.) "
                       "→ socks5 127.0.0.1:%d" % port)
            typer.echo("  • отдельный Chrome: google-chrome "
                       "--user-data-dir=$HOME/.config/chrome-vpn "
                       "--proxy-server='socks5://127.0.0.1:%d'" % port)
            typer.echo("  • вкладка терминала: vpncli on %d" % port)
            typer.echo("  • curl/инструменты: --socks5-hostname "
                       "127.0.0.1:%d" % port)
            typer.echo("Остальной трафик остаётся в текущей схеме "
                       "(AWG-split/direct). Выключение профиля: 'vpn down'.")
        return

    _err("unknown preset: expected 'awg' | 'xray-split' | 'xhttp-split' | "
         "'cdn' | 'proxy <profile>' | 'private <profile>'")
    raise typer.Exit(code=2)


def _apply_awg_scheme() -> Tuple[bool, str]:
    """AWG-туннель + доменный split; снимает xray-split (взаимоисключимы)."""
    from . import amneziawg as awg
    from . import runners
    from . import xsplit as xsplit_mod
    if xsplit_mod.active_profile(runners.run_cmd):
        ok_x, msg_x = xsplit_mod.stop()
        (_ok if ok_x else _warn)(f"xsplit: {msg_x}")
    ok_t, msg_t = awg.tunnel_control("on")
    (_ok if ok_t else _err)(f"AWG: {msg_t}")
    if not ok_t:
        return False, "туннель не поднялся (split без iface не применяется)"
    ok_s, msg_s = awg.domain_split_apply("on")
    (_ok if ok_s else _warn)(f"split: {msg_s}")
    if not ok_s:
        return False, "доменный split не применён"
    return True, "AWG-туннель + доменный split"


def _browser_restart_hint() -> None:
    """Chrome держит TCP/QUIC-соединения и DNS-кэш: после включения split
    они продолжают идти напрямую и «ломают» youtube/gemini."""
    typer.echo("если браузер уже открыт — перезапустите его: старые "
               "TCP/QUIC-соединения идут мимо split")


# --- режимы direct ↔ relay (взаимоисключение) --------------------------------
#
# В v1 одновременно живёт ровно один контур: прямой (AWG-split @laptop или
# tun xray0) либо релейный (vpn-awg-split@relay, весь трафик в RU-релей →
# exit-нода). Иначе маршруты split-юнитов и relay-туннеля передерутся.
# vpn-guard.service — системный юнит с другим префиксом; ни один helper его
# не трогает (в user-списке фильтруется clean-proxy, guard-префикса там нет).

RELAY_INSTANCE = "relay"
RELAY_REALITY_PROFILE = "relay-reality"


def _relay_conf() -> Path:
    from . import amneziawg as awg
    return awg.LOCAL_CONF_DIR / f"{RELAY_INSTANCE}.conf"


def _endpoint_host(conf: Path) -> Optional[str]:
    """``Endpoint = host:port`` → host (для подписи режима; не секрет)."""
    try:
        text = conf.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip().lower() == "endpoint" and value.strip():
            return value.strip().rsplit(":", 1)[0]
    return None


def _stop_relay_contour() -> Tuple[bool, str]:
    """Погасить релейный контур (если активен); режим → none.

    Возвращает ``(ok, message)``; при ошибке systemctl ok=False — вызывающий
    решает, фатально это или нет (перед прямым стартом — фатально, иначе два
    контура окажутся живыми).
    """
    from . import amneziawg as awg
    from . import mode as mode_mod
    if not awg.instance_active(RELAY_INSTANCE):
        return True, "релей не активен"
    ok, msg = awg.instance_control(RELAY_INSTANCE, "off")
    if ok:
        mode_mod.clear_mode()
    return ok, msg


def _teardown_direct() -> list:
    """Снять прямой контур перед релеем; вернуть список сбоев.

    Останавливает все socks-профили (sysproxy off), xray-split и все
    AWG-инстансы (включая @laptop), доменный split — до остановки туннеля.
    """
    from . import amneziawg as awg
    from . import runners
    from . import xsplit as xsplit_mod
    failures: list = []

    stopped, profile_failures = runners.stop_all_profiles()
    if stopped:
        _sync_sysproxy_off_after()
        _ok("остановлены профили: " + ", ".join(stopped))
    failures.extend(profile_failures)

    active_x = xsplit_mod.active_profile(runners.run_cmd)
    if active_x:
        ok_x, msg_x = xsplit_mod.stop()
        (_ok if ok_x else _warn)(f"xsplit: {msg_x}")
        if not ok_x:
            failures.append(f"xsplit: {msg_x}")

    if awg.active_instances():
        direct_instances = [name for name in awg.active_instances()
                            if name != RELAY_INSTANCE]
        if direct_instances:
            ok_s, msg_s = awg.domain_split_apply("off")
            (_ok if ok_s else _warn)(f"split: {msg_s}")
            stopped_awg, awg_failures = awg.stop_instances(keep=RELAY_INSTANCE)
            if stopped_awg:
                _ok("остановлены AWG-инстансы: " + ", ".join(stopped_awg))
            failures.extend(awg_failures)
    return failures


def _write_direct_mode(proto: str, iface: Optional[str] = None,
                       unit: Optional[str] = None,
                       target: Optional[str] = None) -> None:
    from . import mode as mode_mod
    if not mode_mod.write_mode("direct", proto=proto, iface=iface,
                               unit=unit, target=target):
        _warn("не удалось записать mode.json (vpn guard покажет режим по "
              "sysfs)")


def _start_relay(proto: Optional[str]) -> None:
    """``vpn start relay``: AWG-контур через релей (или socks-фолбэк)."""
    from . import amneziawg as awg
    from . import mode as mode_mod
    from . import profiles
    from . import runners

    want = (proto or "awg").strip().lower()
    if want in ("amneziawg", "awg"):
        conf = _relay_conf()
        if not conf.is_file():
            _err(f"нет {conf} — получите конфиг на релее "
                 f"('vds relay add-client laptop') и положите в ~/.awg/"
                 f"{RELAY_INSTANCE}.conf (0600)")
            raise typer.Exit(code=2)
        if not awg.sudo_available():
            _err("нужен passwordless sudo для /etc/amnezia и systemctl — "
                 "без него прямой контур не гасится, релей не поднят "
                 "(ничего не изменено); настройте sudo -n или поднимите "
                 "релей вручную (DEPLOY-RU-RELAY.md §2.0)")
            raise typer.Exit(code=1)
        failures = _teardown_direct()
        if failures:
            _err("прямой контур не снят полностью: " + "; ".join(failures)
                 + " — релей не поднят, чтобы не смешать маршруты")
            raise typer.Exit(code=1)
        ok_c, msg_c = awg.install_instance_conf(RELAY_INSTANCE, conf)
        (_ok if ok_c else _err)(msg_c)
        if not ok_c:
            raise typer.Exit(code=1)
        ok_t, msg_t = awg.instance_control(RELAY_INSTANCE, "on")
        (_ok if ok_t else _err)(msg_t)
        if not ok_t:
            raise typer.Exit(code=1)
        target = _endpoint_host(conf)
        if not mode_mod.write_mode(
                "relay", proto="amneziawg", iface=RELAY_INSTANCE,
                unit=f"{awg.AWG_UNIT_PREFIX}{RELAY_INSTANCE}.service",
                target=target):
            _warn("mode.json не записан (vpn guard покажет режим по sysfs)")
        _ok(f"режим RELAY активен: весь трафик → {target or 'релей'} → "
            "exit-нода; RU-CIDR релей ведёт напрямую (ADR-007). "
            "Доменный split не применяется — его роль играет policy на релее")
        return

    if want in ("reality", "xray", "relay-reality"):
        failures = _teardown_direct()
        ok_r, msg_r = _stop_relay_contour()
        (_ok if ok_r else _err)(msg_r)
        if not ok_r:
            failures.append(msg_r)
        if failures:
            _err("контуры не сняты полностью: " + "; ".join(failures)
                 + " — фолбэк не поднят")
            raise typer.Exit(code=1)
        try:
            config_path = runners.up(RELAY_REALITY_PROFILE)
        except (profiles.ProfileStoreError, runners.RunnerError,
                LinkError) as exc:
            _err(f"{RELAY_REALITY_PROFILE}: {exc} — импортируйте ссылку с "
                 f"релея ('vds xray add-client relay-laptop')")
            raise typer.Exit(code=2)
        record = profiles.get(RELAY_REALITY_PROFILE)
        port = int(record["socks_port"])
        if not mode_mod.write_mode(
                "relay", proto="xray",
                unit=f"vpnm-{RELAY_REALITY_PROFILE}.service",
                target="relay-reality socks"):
            _warn("mode.json не записан")
        _ok(f"режим RELAY (фолбэк Reality) активен: socks "
            f"127.0.0.1:{port}, профиль '{RELAY_REALITY_PROFILE}' "
            f"(config: {config_path})")
        _warn("socks-фолбэк не маршрутизирует весь трафик сам — включите "
              "'vpn sysproxy on' или укажите приложению socks "
              f"127.0.0.1:{port}")
        return

    _err(f"неизвестный proto '{proto}': ожидается 'awg' | 'reality'")
    raise typer.Exit(code=2)


@app.command()
def start(scheme: str = typer.Argument(
        "awg", help="'awg' (по умолчанию) | 'relay' | 'reality' | 'xhttp'"),
        proto: Optional[str] = typer.Option(
            None, "--proto",
            help="для relay: 'awg' (по умолчанию, весь трафик через релей) "
                 "| 'reality' (socks-фолбэк relay-reality)")) -> None:
    """Поднять основную схему: AWG / релей / Reality-split / XHTTP-split.

    vpn start          — AWG-туннель + доменный split (по умолчанию, как
                         'vpn scheme awg'; зеркало 'vpn reset'); гасит релей,
                         если он был активен
    vpn start relay    — RU-релей (ADR-007): весь трафик → awg-туннель до
                         релея, его policy сам делит RU-CIDR/мир; гасит прямой
                         контур (профили + AWG-split + xray-split)
    vpn start relay --proto reality
                       — фолбэк: socks-профиль relay-reality
    vpn start reality  — Reality-split: tun xray0 → VLESS Reality (TCP 443)
    vpn start xhttp    — XHTTP-split: tun xray0 → VLESS XHTTP (TCP 8443)

    Протоколы, которые можно активировать: awg (AmneziaWG, UDP 443 через
    релей или 51825 напрямую), relay (awg; --proto reality — socks-фолбэк),
    reality (VLESS Reality, TCP 443), xhttp (VLESS XHTTP, TCP 8443).
    Hysteria2 (QUIC UDP 443) поднимается профилем: 'vpn up asv2-hy2'.

    Контуры взаимоисключающие: одновременно живёт либо прямой, либо релейный.
    Reality/XHTTP берут первый подходящий профиль из 'vpn profile list'
    (конкретный — 'vpn scheme xray-split <profile>'). Идемпотентно.
    """
    name = scheme.strip().lower()
    if name in ("relay", "relay-reality"):
        if name == "relay-reality" and proto is None:
            proto = "reality"
        _start_relay(proto)
        return

    is_direct_awg = name in ("awg", "default")
    if name in ("awg", "default", "reality", "xray", "xray-split",
                "xhttp", "xhttp-split"):
        ok_r, msg_r = _stop_relay_contour()
        (_ok if ok_r else _err)(msg_r)
        if not ok_r:
            _err("релейный контур не снят — прямой старт отменён, чтобы не "
                 "смешать маршруты")
            raise typer.Exit(code=1)
    if is_direct_awg:
        ok, msg = _apply_awg_scheme()
        if not ok:
            _warn(f"{msg} — повторите 'vpn start' или выполните "
                  "подсказки выше")
            raise typer.Exit(code=1)
        from . import amneziawg as awg
        instance = awg.local_instance() or awg.DEFAULT_LOCAL_INSTANCE
        _write_direct_mode("amneziawg", iface=instance,
                           unit=f"{awg.AWG_UNIT_PREFIX}{instance}.service")
        _ok("основная схема активна: TLD-зоны/youtube/telegram через AWG, "
            "GLM/RU напрямую (браузер — перезапустить, если sysproxy менялся)")
        return
    if name in ("reality", "xray", "xray-split"):
        from . import xsplit as xsplit_mod
        ok, msg = xsplit_mod.apply(default_protocol="xray-reality")
        (_ok if ok else _err)(f"reality: {msg}")
        if not ok:
            raise typer.Exit(code=1)
        _write_direct_mode("xray", iface=xsplit_mod.DEV,
                           unit=xsplit_mod.unit_name(
                               xsplit_mod.active_profile(xsplit_mod.run_cmd)
                               or "reality"))
        _ok("основная схема активна: Reality-split через tun xray0 (TCP 443), "
            "GLM/RU напрямую; откат — 'vpn scheme awg'/'vpn reset'")
        _browser_restart_hint()
        return
    if name in ("xhttp", "xhttp-split"):
        from . import xsplit as xsplit_mod
        ok, msg = xsplit_mod.apply(default_protocol="xray-xhttp")
        (_ok if ok else _err)(f"xhttp: {msg}")
        if not ok:
            raise typer.Exit(code=1)
        _write_direct_mode("xray", iface=xsplit_mod.DEV,
                           unit=xsplit_mod.unit_name(
                               xsplit_mod.active_profile(xsplit_mod.run_cmd)
                               or "xhttp"))
        _ok("основная схема активна: XHTTP-split через tun xray0 (TCP 8443), "
            "GLM/RU напрямую; откат — 'vpn scheme awg'/'vpn reset'")
        _browser_restart_hint()
        return
    _err("unknown scheme: expected 'awg' | 'relay' | 'reality' | 'xhttp'")
    raise typer.Exit(code=2)


@app.command()
def reset() -> None:
    """Вернуть всё к состоянию «как после перезагрузки ОС» (чистый старт).

    Останавливает активный socks-профиль (+sysproxy), демонтирует
    xray-split и доменный split, останавливает AWG-туннель, гасит DoH-цепочку
    — DNS возвращается к провайдеру (DHCP). Boot-guard остаётся включённым
    (защита загрузки). Идемпотентно: повторный запуск ничего не ломает.
    """
    from . import amneziawg as awg
    from . import doh as doh_mod
    from . import runners
    failures: list = []

    active = runners.get_active()
    if active:
        try:
            runners.down(active)
            _sync_sysproxy_off_after()
            _ok(f"профиль '{active}' остановлен, системный прокси off")
        except runners.RunnerError as exc:
            failures.append(f"профиль: {exc}")
    else:
        typer.echo("vpnm: нет активного профиля")

    from . import xsplit as xsplit_mod
    ok_x, msg_x = xsplit_mod.stop()
    (_ok if ok_x else _warn)(f"xsplit: {msg_x}")
    if not ok_x:
        failures.append("xsplit")
    ok_s, msg_s = awg.domain_split_apply("off")
    (_ok if ok_s else _warn)(f"split: {msg_s}")
    if not ok_s:
        failures.append("split")
    ok_t, msg_t = awg.tunnel_control("off")
    (_ok if ok_t else _warn)(f"AWG: {msg_t}")
    if not ok_t:
        failures.append("AWG")
    relay_was_active = awg.instance_active(RELAY_INSTANCE)
    ok_r, msg_r = _stop_relay_contour()
    if relay_was_active:
        (_ok if ok_r else _warn)(f"релей: {msg_r}")
        if not ok_r:
            failures.append("релей")
    from . import mode as mode_mod
    mode_mod.clear_mode()
    ok_d, msg_d = doh_mod.disable()
    (_ok if ok_d else _warn)(f"DoH: {msg_d}")
    if not ok_d:
        failures.append("DoH")

    if failures:
        _warn("не всё снялось: " + ", ".join(failures)
              + " — повторите 'vpn reset' или выполните напечатанные подсказки")
        raise typer.Exit(code=1)
    _ok("система в состоянии чистого старта: DNS у провайдера, "
        "туннелей и прокси нет")


@app.command()
def switch(name: str) -> None:
    """Переключиться на другой профиль: vpn switch <profile>."""
    from . import runners
    try:
        config_path = runners.switch(name)
    except (profiles.ProfileStoreError, runners.RunnerError, LinkError) as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    _ok(f"switched to '{name}' (config: {config_path})")
    _sync_sysproxy_after(name, "switch")


def _direct_unit_active(unit: Optional[str]) -> Optional[bool]:
    """Активен ли юнит прямого контура из mode.json (None = юнит неизвестен).

    Нужен ``vpn status``, чтобы не рапортовать DIRECT/RELAY при мёртвом юните
    (инцидент 2026-11-18: туннель снесён, а статус показывал живой режим).
    """
    from . import amneziawg as awg
    from . import xsplit as xsplit_mod
    if not unit:
        return None
    if unit.startswith(awg.AWG_UNIT_PREFIX) and unit.endswith(".service"):
        name = unit[len(awg.AWG_UNIT_PREFIX):-len(".service")]
        return awg.instance_active(name)
    if (unit.startswith(xsplit_mod.XRAY_SPLIT_UNIT_PREFIX)
            and unit.endswith(".service")):
        name = unit[len(xsplit_mod.XRAY_SPLIT_UNIT_PREFIX):-len(".service")]
        return xsplit_mod.active_profile(xsplit_mod.run_cmd) == name
    if unit.startswith("vpnm-") and unit.endswith(".service"):
        name = unit[len("vpnm-"):-len(".service")]
        return runners.is_active(name)
    return None


@app.command()
def status() -> None:
    """Режим (DIRECT/RELAY), awg-схема или активный профиль, unit, внешний IP."""
    from . import amneziawg as awg
    from . import mode as mode_mod
    from . import runners
    stored = mode_mod.read_mode()
    stored_mode = stored.get("mode")
    instances = awg.active_instances()
    relay_live = awg.instance_active(RELAY_INSTANCE)
    hint: Optional[str] = None
    if relay_live:
        target = stored.get("target") if stored_mode == "relay" else None
        line = f"RELAY (awg {RELAY_INSTANCE}"
        line += f" → {target})" if target else ")"
    elif instances:
        line = f"DIRECT (awg {instances[0]})"
    elif stored_mode == "relay":
        # mode.json считает, что релей активен, но юнита нет — не врать.
        unit = (stored.get("unit")
                or f"{awg.AWG_UNIT_PREFIX}{RELAY_INSTANCE}.service")
        line = f"RELAY (юнит {unit} неактивен — туннель не поднят)"
        hint = "подсказка: поднять релей — vpn start relay"
    elif stored_mode == "direct":
        unit = stored.get("unit")
        if _direct_unit_active(unit) is False:
            line = f"direct (юнит {unit} неактивен — туннель не поднят)"
            hint = "подсказка: поднять прямую схему — vpn start"
        else:
            line = mode_mod.describe(stored)
    else:
        line = "unknown (mode.json нет, туннелей не найдено)"
    typer.echo(f"mode:           {line}")
    if hint:
        typer.echo(hint)
    active = runners.get_active()
    if not active:
        # «no active profile» относится к socks-профилям (xray/sing-box), а не
        # к AWG: иначе выглядит так, будто схема не работает (2026-11-17).
        if relay_live:
            typer.echo("релей активен: весь трафик через awg-туннель до "
                       "релея (socks-профиль не нужен)")
        elif instances:
            typer.echo(f"awg-схема активна: доменный split через "
                       f"{awg.AWG_UNIT_PREFIX}{instances[0]}.service "
                       "(socks-профиль не нужен)")
        else:
            typer.echo("no active profile")
        return
    try:
        record = profiles.get(active)
    except profiles.ProfileStoreError as exc:
        _err(f"active profile record is broken: {exc}")
        raise typer.Exit(code=2)
    unit_active = runners.is_active(active)
    typer.echo(f"active profile: {active}")
    typer.echo(f"protocol:       {record['protocol']}")
    typer.echo(f"socks port:     127.0.0.1:{record['socks_port']}")
    typer.echo(f"unit state:     {'active' if unit_active else 'inactive/failed'}")
    ip = runners.external_ip(int(record["socks_port"]))
    if ip:
        typer.echo(f"external IP:    {ip}")
    else:
        _warn("external IP probe timed out or failed "
              "(curl through the SOCKS port did not answer within "
              f"{runners.CURL_TIMEOUT}s)")


@app.command("install-bins")
def install_bins() -> None:
    """Скачать/обновить бинарники xray-core и hysteria в ~/.local/lib/vpn."""
    from . import bins
    try:
        results = bins.install_all()
    except bins.BinError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    for bin_name, outcome in results:
        _ok(f"{bin_name}: {outcome}")


@app.command()
def install(linger: bool = typer.Option(
        False, "--linger", help="Run 'loginctl enable-linger' so user units "
        "survive logout"),
        autostart: Optional[str] = typer.Option(
            None, "--autostart",
            help="Enable the profile unit so it starts at login; with no "
                 "value the currently active profile is used")) -> None:
    """Установить бинарники; опц. --linger / --autostart [profile] (по умолчанию НЕ надо: политика чистого старта)."""
    from . import runners
    install_bins()
    if linger:
        proc = subprocess.run(["loginctl", "enable-linger"],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            _err(f"loginctl enable-linger failed: {proc.stderr.strip()}")
            raise typer.Exit(code=2)
        _ok("linger enabled for user units")
    if autostart is not None:
        if autostart == AUTOSTART_ACTIVE:
            active = runners.get_active()
            if not active:
                _err("no active profile; pass a profile name: "
                     "'vpn install --autostart <name>'")
                raise typer.Exit(code=1)
            autostart = active
        _autostart_enable(autostart)


def _autostart_enable(profile: str) -> None:
    """Enable the vpnm-<profile> unit (autostart at login)."""
    from . import runners
    try:
        profiles.get(profile)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    try:
        runners.enable_autostart(profile)
    except runners.RunnerError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    _ok(f"autostart enabled: {runners.unit_name(profile)}")


@app.command()
def autostart(action: str = typer.Argument(
        ..., help="'on', 'off' or 'status'")) -> None:
    """Автозапуск профилей при логине: vpn autostart status|on|off (по умолчанию выключен)."""
    from . import runners
    action = action.strip().lower()
    if action not in ("on", "off", "status"):
        _err("expected 'on', 'off' or 'status'")
        raise typer.Exit(code=2)
    if action == "on":
        active = runners.get_active()
        if not active:
            _err("no active profile; run 'vpn up <name>' first")
            raise typer.Exit(code=1)
        _autostart_enable(active)
    elif action == "off":
        active = runners.get_active()
        targets = ([active] if active and runners.is_enabled(active)
                   else runners.enabled_vpnm_units())
        if not targets:
            _warn("no enabled vpnm units; nothing to disable")
            return
        for name in targets:
            try:
                runners.disable_autostart(str(name))
            except runners.RunnerError as exc:
                _err(str(exc))
                raise typer.Exit(code=2)
            _ok(f"autostart disabled: {runners.unit_name(str(name))}")
    else:
        active = runners.get_active()
        enabled = runners.enabled_vpnm_units()
        if active:
            state = ("enabled" if runners.is_enabled(active)
                     else "disabled")
            typer.echo(f"active profile '{active}': autostart {state}")
        else:
            typer.echo("no active profile")
        if enabled:
            typer.echo("enabled units: " + ", ".join(
                runners.unit_name(str(n)) for n in enabled))
        else:
            typer.echo("no enabled vpnm units")


# --- WP-D: pull / sysproxy / doctor / TUI --------------------------------------

@profile_app.command("pull")
def profile_pull(
        host: Optional[str] = typer.Option(
            None, "--host", "-H", help="ssh target like root@asv1; when omitted, "
            "the saved host is used, otherwise asked interactively"),
        include_obfs: bool = typer.Option(
            False, "--include-obfs",
            help="также забрать obfs-профили hysteria2 (`<name>@obfs`, "
                 "obfs=salamander)")) -> None:
    """Подтянуть share-линки с VDS (vds gateway links по ssh): vpn profile pull -H root@asv1."""
    from . import pull as pull_mod
    from . import settings
    if not host:
        host = settings.get_host()
        if not host:
            host = typer.prompt("ssh host (user@server)")
            if not host.strip():
                _err("no host given")
                raise typer.Exit(code=1)
        else:
            _ok(f"using saved host '{host}'")
    try:
        actions = pull_mod.pull(host, include_obfs=include_obfs)
    except pull_mod.PullError as exc:
        _err(str(exc))
        raise typer.Exit(code=1)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    for name, action in actions:
        if action == "imported":
            _ok(f"imported '{name}'")
        elif action == "updated":
            _warn(f"updated '{name}' (link changed on the server)")
        else:
            typer.echo(f"skipped '{name}' (unchanged)")
    try:
        settings.set_host(host)
        _ok(f"saved host '{host}' to settings")
    except OSError as exc:
        _warn(f"could not save host to settings: {exc}")


@profile_app.command("archive")
def profile_archive(
        name: str = typer.Argument(..., help="Имя профиля (profiles.json) "
                                             "или stem AWG-конфига"),
        reason: str = typer.Option(
            ..., "--reason", help="Причина архивации (пишется в archived_reason)"),
        apply: bool = typer.Option(
            False, "--apply",
            help="Применить план (по умолчанию — только dry-run)"),
) -> None:
    """Пометить профиль архивным: мёртвый конфиг больше не выдаётся как
    рабочий (v1.1, D3/R4).

    Dry-run по умолчанию: печатает план (поля store + перенос ``.conf`` в
    ``~/.awg/archive/``). С ``--apply`` ставит ``status=archived`` и
    ``archived_reason`` в profiles.json и переносит клиентский ``.conf``
    (root/direct/relay) в ``archive/`` с сохранением прав 0600.
    """
    if not reason.strip():
        _err("--reason обязателен (почему профиль выводится из эксплуатации)")
        raise typer.Exit(code=2)
    try:
        plan = profiles.archive(name, reason, apply=apply)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=1)
    label = "applied" if plan["applied"] else "dry-run (nothing changed)"
    typer.secho(f"archive '{name}': {label}", fg=typer.colors.CYAN)
    fields = plan.get("store_fields") or {}
    typer.echo(f"  store: status={fields.get('status')} "
               f"archived_reason={fields.get('archived_reason')!r}")
    moves = plan.get("conf_moves") or []
    if moves:
        for move in moves:
            typer.echo(f"  conf: {move['from']} -> {move['to']}")
    else:
        typer.echo("  conf: локальный .conf не найден (переносить нечего)")
    if not plan["applied"]:
        typer.echo("  для применения добавьте --apply")


@app.command("sysproxy")
def sysproxy_cmd(on_off: str = typer.Argument(
        ..., help="'on', 'off' or 'status'")) -> None:
    """Системный прокси GNOME: vpn sysproxy on|off|status (RU-сайты см. ignore-hosts в epilog)."""
    from . import runners, settings, sysproxy
    on_off = on_off.strip().lower()
    if on_off not in ("on", "off", "status"):
        _err("expected 'on', 'off' or 'status'")
        raise typer.Exit(code=2)
    try:
        if on_off == "on":
            active = runners.get_active()
            if not active:
                _err("no active profile; run 'vpn up <name>' first to pick a "
                     "SOCKS port")
                raise typer.Exit(code=1)
            record = profiles.get(active)
            sysproxy.on(int(record["socks_port"]))
            runners.set_sysproxy(True)  # follow the active profile on up/switch
            _ok(f"system proxy on: socks 127.0.0.1:{record['socks_port']} "
                f"(profile '{active}')")
        elif on_off == "off":
            sysproxy.off()
            runners.set_sysproxy(False)  # no auto-follow until re-enabled
            _ok("system proxy off")
        else:
            mode, sock_host, sock_port = sysproxy.status()
            typer.echo(f"mode: {mode}")
            typer.echo(f"socks host: {sock_host}")
            typer.echo(f"socks port: {sock_port}")
    except sysproxy.SysproxyError as exc:
        _err(str(exc))
        raise typer.Exit(code=1)


@app.command("zapret")
def zapret_cmd(action: str = typer.Argument(
        ..., help="'on', 'off' or 'status'")) -> None:
    """Канал 0 zapret (nfqws DPI-desync): vpn zapret on|off|status (sudo)."""
    from . import zapret as zapret_mod
    action = action.strip().lower()
    if action not in ("on", "off", "status"):
        _err("expected 'on', 'off' or 'status'")
        raise typer.Exit(code=2)
    if action == "status":
        for line in zapret_mod.status_lines():
            typer.echo(line)
        return
    ok, message = zapret_mod.control(action)
    if ok:
        _ok(message)
    else:
        _err(message)
        raise typer.Exit(code=1)


@app.command("doh")
def doh_cmd(action: str = typer.Argument(
        ..., help="'on', 'off', 'status', 'guard' (+'on'/'off')"),
        extra: Optional[str] = typer.Argument(
        None, help="для 'guard': 'on' (по умолчанию) или 'off'")) -> None:
    """DoH-режим (чистый DNS): vpn doh on|off|status, boot-guard: vpn doh guard on|off.

    resolved → dnsmasq → cloudflared → Cloudflare DoH; откат одним
    mv возвращает DNS провайдеру. По политике чистого старта не
    включается при загрузке — только вручную на сессию.

    ``vpn doh guard on`` ставит systemd oneshot, который при загрузке
    возвращает drop-in в disabled-имя — гарантирует, что после ребута
    DNS никогда не мёртв (даже если прошлый сеанс закончился с doh on).
    """
    from . import doh as doh_mod
    action = action.strip().lower()
    extra_l = (extra or "").strip().lower()
    if action == "guard":
        action = "guard-off" if extra_l == "off" else "guard-on"
    if action not in ("on", "off", "status", "guard-on", "guard-off"):
        _err("expected 'on', 'off', 'status', 'guard' (+ 'on'/'off')")
        raise typer.Exit(code=2)
    if action == "status":
        for line in doh_mod.status_lines():
            typer.echo(line)
        return
    if action == "guard-on":
        ok, message = doh_mod.guard_install()
    elif action == "guard-off":
        ok, message = doh_mod.guard_remove()
    elif action == "on":
        ok, message = doh_mod.enable()
    else:
        ok, message = doh_mod.disable()
    if ok:
        _ok(message)
    else:
        _err(message)
        raise typer.Exit(code=1)


@app.command()
def doctor() -> None:
    """Полная диагностика: бинарники, порты, ssh-хост, DNS — vpn doctor."""
    from . import doctor as doctor_mod
    from . import settings
    checks, verdict = doctor_mod.run_doctor(settings.get_host())
    for status, subject, detail in checks:
        typer.echo(f"[{status}] {subject}: {detail}")
    colors: dict = {"ok": typer.colors.GREEN, "warn": typer.colors.YELLOW,
                    "fail": typer.colors.RED}
    typer.secho(f"verdict: {verdict}", fg=colors[verdict])
    if verdict == "fail":
        raise typer.Exit(code=1)


@app.command()
def repair(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Только диагностика и план, ничего не менять"),
    dns: str = typer.Option(
        "fix", "--dns", help="'fix' — вернуть DNS/трафик; 'off' — убрать "
        "управляемый drop-in vpn-dns-fix.conf (чистый старт)"),
    no_log: bool = typer.Option(
        False, "--no-log", help="Не писать лог-файл в ~/.local/share/vpn-manager/logs/"),
) -> None:
    """Диагностика всего прокси-стека в лог + возврат трафика без nano.

    «Полный блок» почти всегда = мёртвая DoH-цепочка при включённом
    drop-in (resolved -> 127.0.0.1) либо мёртвые DNS-серверы в resolved.
    Команда пишет полный снимок в лог и чинит DNS автоматически:
    гасит мёртвую цепочку и/или направляет resolved на живой резолвер
    через drop-in vpn-dns-fix.conf (resolved.conf руками не трогается).
    """
    from . import repair as repair_mod
    from . import settings as settings_mod
    from .runners import run_cmd as _run
    runner = _run
    log_lines: list = []

    dns = dns.strip().lower()
    if dns not in ("fix", "off"):
        _err("expected --dns fix|off")
        raise typer.Exit(code=2)

    if dns == "off":
        ok, msg = repair_mod.remove_dns_fix(runner)
        (_ok if ok else _err)(msg)
        if not ok:
            raise typer.Exit(code=1)
        return

    # 1) снимок всего состояния -> лог
    snapshot = repair_mod.snapshot_lines(runner)
    for line in snapshot:
        typer.echo(line)
    log_lines.extend(snapshot)

    # 2) doctor-чеки (статусы ok/warn/fail)
    from . import doctor as doctor_mod
    checks, verdict = doctor_mod.run_doctor(settings_mod.get_host())
    for status, subject, detail in checks:
        typer.echo(f"[{status}] {subject}: {detail}")
    log_lines.append("-- doctor --")
    log_lines.extend(f"[{s}] {subj}: {d}" for s, subj, d in checks)
    log_lines.append(f"verdict: {verdict}")

    # 3) восстановление DNS (сухой прогон по флагу)
    action, fix_lines = repair_mod.fix_dns(runner, dry_run=dry_run)
    typer.echo("-- восстановление DNS --")
    for line in fix_lines:
        typer.echo(line)
    log_lines.append("-- repair dns --")
    log_lines.extend(fix_lines)
    log_lines.append(f"action: {action}")

    # 4) лог
    if not no_log:
        log_path = repair_mod.write_log(log_lines)
        if log_path:
            _ok(f"log: {log_path}")
        else:
            _warn("не удалось записать лог-файл")

    if action == "manual":
        _warn("нужен root — выполните напечатанную ручную подсказку выше")
        raise typer.Exit(code=1)
    if action == "failed" or verdict == "fail":
        raise typer.Exit(code=1)


@app.command()
def monitor(as_json: bool = typer.Option(
        False, "--json", help="Machine-readable output (same matrix)"),
        zt_ping: Optional[str] = typer.Option(
            None, "--zt-ping",
            help="Ping an explicit ZeroTier peer IP and include the result "
                 "in the zerotier channel detail")) -> None:
    """Матрица состояний всех каналов (7).

    Каналы: vpnm, cdn, zerotier, amneziawg, xray-split, zapret, dns-doh.
    В таблице смысл, а не код: ✔ работает / ○ готов, не включён / — не
    настроен / ✖ ошибка; сырые ok/warn/not_installed/fail остаются в
    --json. Строка xray-split: unit/iface/правила + проба 204 через tun
    (Reality или XHTTP); amneziawg — AWG-split (N зон через туннель,
    GLM/RU напрямую); cdn — канал выживания за Cloudflare."""
    from . import channels as channels_mod
    outcome = channels_mod.run_monitor(peer_ip=zt_ping)
    if as_json:
        typer.echo(json.dumps(outcome, ensure_ascii=False, indent=2))
    else:
        _print_channels_table(outcome)
        healthy = int(str(outcome["healthy"]))
        total = int(str(outcome["total"]))
        typer.secho(
            f"healthy channels: {healthy}/{total}",
            fg=typer.colors.GREEN if healthy else typer.colors.RED)
    raise typer.Exit(code=channels_mod.exit_code(outcome))


def _print_channels_table(outcome: dict,
                          title: Optional[str] = "vpn channels") -> None:
    """Render the channel matrix (rich table, plain-text fallback)."""
    rows = list(outcome["channels"])
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        labels = {"ok": "OK", "warn": "выключен",
                  "not_installed": "не настроен", "fail": "ошибка"}
        for row in rows:
            typer.echo(f"{row['channel']}\t"
                       f"{labels.get(str(row['status']), row['status'])}\t"
                       f"{row['detail']}")
        return
    # Показываем человеку смысл, а не внутренний код (SPEC: warn/not_installed
    # читались как «сломано», хотя это «есть, но выключено/не настроено»).
    status_labels = {
        "ok": "✔ работает",
        "warn": "○ готов, не включён",
        "not_installed": "— не настроен",
        "fail": "✖ ошибка",
    }
    status_colors = {"ok": "green", "warn": "yellow",
                     "not_installed": "dim", "fail": "red"}
    table = Table(title=title)
    table.add_column("канал")
    table.add_column("состояние")
    table.add_column("детали")
    for row in rows:
        raw = str(row["status"])
        label = status_labels.get(raw, raw)
        table.add_row(
            str(row["channel"]),
            f"[{status_colors.get(raw, 'white')}]{label}[/]",
            str(row["detail"]))
    Console().print(table)


CHANNELS_SECTION_HEADER = "── Каналы (vpn/zapret/zerotier/amneziawg) ──"


def _print_channels_section() -> None:
    """Append the channel matrix (same as `vpn monitor`) to the probe report.

    The AWG tun channel is outside the socks profile model: when its
    interface is up we additionally measure the YouTube generate_204
    latency through the tun iface (traffic follows AllowedIPs); otherwise
    an informational "не поднят" line is shown. This section is
    informational only — switch/fastest semantics stay socks-only.
    """
    from . import channels as channels_mod
    typer.secho(CHANNELS_SECTION_HEADER, fg=typer.colors.CYAN)
    rows = channels_mod.check_all()
    _print_channels_table({"channels": rows}, title=None)
    awg_row = next((r for r in rows if r["channel"] == "amneziawg"), None)
    if awg_row is not None and awg_row["status"] == channels_mod.STATUS_OK:
        latency = channels_mod.awg_tunnel_latency()
        if latency is not None:
            typer.secho(f"awg (tun): {latency} ms", fg=typer.colors.GREEN)
        else:
            typer.secho("awg (tun): интерфейс поднят, замер задержки "
                        "не удался", fg=typer.colors.YELLOW)
    else:
        typer.secho("awg (tun): не поднят", fg=typer.colors.YELLOW)


@app.command("speed")
def speed_cmd(
        samples: int = typer.Option(2, "--samples", min=1, max=5,
                                    help="Замеров на канал (latency и bulk)"),
        flows: int = typer.Option(1, "--flows", min=1, max=8,
                                  help="Параллельных потоков в одном bulk-замере"),
        ttfb_samples: int = typer.Option(
            3, "--ttfb-samples", min=1, max=7,
            help="TTFB-замеров на канал (серийно; работает даже без bulk)"),
        timeout: float = typer.Option(35.0, "--timeout",
                                      help="Таймаут одного bulk-замера, с"),
        no_awg: bool = typer.Option(False, "--no-awg",
                                    help="Не мерить AmneziaWG-split"),
        include_cdn: bool = typer.Option(
            False, "--include-cdn",
            help="Мерить и CDN-профили (по умолчанию пропускаются: CF не отдаёт bulk)"),
        only: Optional[str] = typer.Option(
            None, "--profiles",
            help="Только эти профили (через запятую); по умолчанию все"),
        as_json: bool = typer.Option(False, "--json",
                                     help="Только JSON, без live-таблицы"),
        out: Optional[str] = typer.Option(
            None, "--out", help="Сохранить отчёт JSON в файл")) -> None:
    """Живой замер скорости: все каналы, несколько эндпоинтов, серийно.

    В отличие от `vpn probe`, меряет НЕСКОЛЬКО bulk-эндпоинтов с фолбэком
    (OVH -> Cloudflare -> GitHub) и показывает таблицу в реальном времени:
    статус, задержку и Мбит/с по каждому каналу по мере замера.
    """
    from . import speed as speed_mod
    from datetime import datetime as _dt

    records = profiles.list_profiles()
    known = [str(r["name"]) for r in records]
    if only:
        wanted = [n.strip() for n in only.split(",") if n.strip()]
        missing = [n for n in wanted if n not in known]
        for name in missing:
            _err(f"нет профиля '{name}'")
        if missing:
            raise typer.Exit(code=2)
        names = wanted
    else:
        names = known
    if not names and no_awg:
        _err("нет профилей для замера")
        raise typer.Exit(code=2)

    # CDN-профили по умолчанию не меряются (CF не отдаёт bulk) — не показываем
    # их и в live-таблице, чтобы не путать «ожиданием».
    from .links import PROTOCOL_XRAY_WS

    skipped = [] if include_cdn else [
        n for n in names
        if str(profiles.get(n)["protocol"]) == PROTOCOL_XRAY_WS
    ]
    measured = [n for n in names if n not in skipped]
    if not measured and no_awg:
        _err("нечего мерить: все профили — CDN (добавьте --include-cdn)")
        raise typer.Exit(code=2)

    snapshots: dict = {}
    state = {"index": 0, "total": len(measured) + (0 if no_awg else 1),
             "started": _dt.now()}

    def build_table():
        from rich.table import Table
        from rich.text import Text
        table = Table(title="vpn speed — живой замер", expand=False)
        table.add_column("канал", style="bold")
        table.add_column("протокол")
        table.add_column("статус")
        table.add_column("latency, с", justify="right")
        table.add_column("ttfb p50/p95, мс", justify="right")
        table.add_column("скорость, Мбит/с", justify="right")
        table.add_column("эндпоинт")
        table.add_column("примечание")
        for name in [*measured, *([] if no_awg else ["awg"])]:
            st = snapshots.get(name) or {"name": name, "protocol": "—",
                                         "status": "ожидание"}
            lat = st.get("latency_s")
            spd = st.get("speed_mbps")
            if spd is None:
                speed_cell = Text("—", style="dim")
            elif spd >= 50:
                speed_cell = Text(f"{spd:.1f}", style="green")
            elif spd >= 15:
                speed_cell = Text(f"{spd:.1f}", style="yellow")
            else:
                speed_cell = Text(f"{spd:.1f}", style="red")
            p50, p95 = st.get("ttfb_p50_ms"), st.get("ttfb_p95_ms")
            if p50 is None:
                ttfb_cell = Text("—", style="dim")
            else:
                style = "green" if p50 <= 700 else ("yellow" if p50 <= 1500
                                                    else "red")
                ttfb_cell = Text(f"{p50:.0f}/{p95:.0f}", style=style)
            status = str(st.get("status") or "")
            status_style = {"готово": "green", "ошибка": "red",
                            "нет данных": "yellow",
                            "только задержка": "yellow"}.get(status, "cyan")
            table.add_row(
                str(st.get("name") or name),
                str(st.get("protocol") or "—"),
                Text(status, style=status_style),
                "—" if lat is None else f"{lat:.2f}",
                ttfb_cell,
                speed_cell,
                str(st.get("endpoint") or ""),
                str(st.get("error") or st.get("note") or ""),
            )
        elapsed = (_dt.now() - state["started"]).total_seconds()
        table.caption = (f"канал {state['index']}/{state['total']} · "
                         f"{elapsed:.0f} с")
        return table

    renderer = None
    if not as_json:
        try:
            from rich.live import Live
            renderer = Live(build_table(), refresh_per_second=8,
                            transient=False)
            renderer.start()
        except ImportError:
            renderer = None
            typer.echo("[live недоступен: нет rich — прогресс построчно]")

    def on_event(ev: dict) -> None:
        st = ev.get("state")
        if isinstance(st, dict):
            snapshots[str(st.get("name"))] = st
        if ev.get("type") in ("channel_start", "channel_done"):
            state["index"] = int(ev.get("index", 0)) + 1
            state["total"] = int(ev.get("total", state["total"]))
        if renderer is not None:
            renderer.update(build_table())
        elif not as_json:
            kind = ev.get("type")
            if kind in ("channel_start", "channel_done"):
                typer.echo(f"  {ev.get('channel')}: {kind}")
            elif snapshots:
                last = snapshots.get(str(ev.get("channel")))
                if last and kind in ("latency", "ttfb", "speed"):
                    typer.echo(
                        f"  {last['name']}: latency={last['latency_s']} "
                        f"ttfb={last.get('ttfb_p50_ms')}мс "
                        f"speed={last['speed_mbps']}")

    try:
        report = speed_mod.run_speed(
            names, samples=samples, flows=flows, timeout=timeout,
            include_awg=not no_awg, include_cdn=include_cdn,
            on_event=on_event, ttfb_samples=ttfb_samples)
    except profiles.ProfileStoreError as exc:
        if renderer is not None:
            renderer.stop()
        _err(str(exc))
        raise typer.Exit(code=2)
    finally:
        if renderer is not None:
            renderer.stop()

    # финальная таблица (после Live) + вердикт
    if not as_json:
        try:
            from rich.console import Console
            Console().print(build_table())
            v = report.get("verdict") or {}
            if v.get("fastest"):
                _ok(f"быстрее всех: {v['fastest']}")
            if v.get("most_stable"):
                _ok(f"стабильнее всех: {v['most_stable']}")
            for note in v.get("notes") or []:
                _warn(note)
            if report.get("skipped"):
                _warn("пропущены CDN-профили (bulk через CF не измеряется): "
                      + ", ".join(str(n) for n in report["skipped"])
                      + "; включить: --include-cdn")
        except ImportError:
            for ch in report["channels"]:
                typer.echo(f"{ch['name']}	{ch.get('speed_mbps')}	"
                           f"{ch.get('latency_s')}	{ch.get('error')}")

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    target = out or str(speed_mod.REPORT_PATH)
    try:
        from pathlib import Path as _Path
        dest = _Path(target)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(payload + "\n", encoding="utf-8")
        try:
            dest.chmod(0o600)
        except OSError:
            pass
        if not as_json:
            _ok(f"отчёт сохранён: {dest}")
    except OSError as exc:
        _warn(f"не удалось сохранить отчёт: {exc}")

    if as_json:
        typer.echo(payload)


@app.command()
def scout(as_json: bool = typer.Option(
              False, "--json", help="scout.report/1 в stdout (машинный формат)"),
          sync: bool = typer.Option(
              False, "--sync",
              help="Сначала 'vpn guard sync' (один ssh — единственный сетевой "
                   "шаг), затем разведка по свежему вердикту"),
          probe_all: bool = typer.Option(
              False, "--probe-all",
              help="Полный параллельный опрос всех узлов (по умолчанию — "
                   "каскад: сортировка локально и остановка на первом хорошем)"),
          host: Optional[str] = typer.Option(
              None, "--host", help="ssh-хост для --sync (по умолчанию "
                                   "settings.json:ssh_host)"),
          ) -> None:
    """Активная разведка маршрутов: read-only probe + скоринг + рекомендация.

    Демон guard сеть не трогает — разведку делает только эта команда.
    По умолчанию — **Stealth Cascade**: кандидаты сортируются локально
    (pre_score: база + lock-443 + свежие G-04/G-01/G-03, без сети), затем
    проверяются по одному до первого хорошего (`ok`/`reachable`/`local_route`);
    к остальным узлам не уходит ни одного пакета. Между провалами — джиттер
    150–400 мс, общий бюджет 6000 мс, таймаут пробы 1500 мс.
    `--probe-all` — прежний полный параллельный опрос (2500 мс, 8 воркеров).

    Пробы: TCP+TLS 1.3 с настоящим SNI → HEAD / (Host: SNI) → чтение первых
    64 байт (HTTP/1.x|HTTP/2 → ok, TLS без HTTP → tls_only); Hysteria2 и AWG —
    только локальная маршрутизируемость, 0 байт в эфир (connect без send),
    плюс история G-04/G-01/G-03.

    Вывод: RECOMMENDED (score ≥50), FALLBACK на другом маршруте/протоколе,
    «отсеяны» с причинами. Бюджет каскада исчерпан → честный статус `budget`.
    Ничего не пишется: findings.jsonl, guard.json и server-verdict.json
    остаются неизменными (--sync пишет вердикт штатной командой vpn guard
    sync). Exit: 0 — рекомендация есть, 1 — нет.
    """
    from .scout.cli import run_scout_cli
    raise typer.Exit(code=run_scout_cli(as_json=as_json, sync=sync,
                                        host=host,
                                        probe_all_mode=probe_all))


@app.command()
def probe(switch_best: bool = typer.Option(
        True, "--switch-best/--no-switch",
        help="Switch to the fastest ok profile when done (default); "
             "--no-switch restores the initially active profile"),
        no_bulk: bool = typer.Option(
            False, "--no-bulk",
            help="Не замерять bulk-скорость (быстрый probe; без детекции "
                 "«тихой деградации» D-SPEED)"),
        bulk_bytes: int = typer.Option(
            1000000, "--bulk-bytes", help="Размер bulk-сэмпла, байт"),
        min_speed: float = typer.Option(
            2.0, "--min-speed", help="Порог degraded, Мбит/с")) -> None:
    """Замер задержек всех профилей и включение самого быстрого (--no-switch — только замер)."""
    from . import probe as probe_mod
    import sys
    import time as time_mod

    def _note(msg: str) -> None:
        # прогресс в stderr: таблица в stdout остаётся чистой (и для --json-потребителей)
        print(f"[info] {msg}", file=sys.stderr, flush=True)

    try:
        outcome = probe_mod.run_probe(switch_best=switch_best,
                                      sleep_fn=time_mod.sleep,
                                      note=_note, bulk=not no_bulk,
                                      bulk_bytes=bulk_bytes,
                                      min_speed=min_speed)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    _print_probe_table(outcome)
    _print_channels_section()
    fastest = outcome["fastest"]
    switched_to = outcome["switched_to"]
    if not fastest:
        _err("all profiles failed the probe; nothing to switch to")
        raise typer.Exit(code=1)
    if switched_to:
        _ok(f"switched to fastest: {fastest} ({_latency_of(outcome)} ms)")
        _sync_sysproxy_after(str(fastest), "probe")
    elif switch_best:
        _warn(f"fastest is {fastest} but the switch failed; "
              "the previously active profile was restored")


def _latency_of(outcome: dict) -> object:
    for row in outcome["rows"]:
        if row.get("name") == outcome.get("fastest"):
            return row.get("latency_ms")
    return "?"


def _print_probe_table(outcome: dict) -> None:
    """Render the probe results as a rich table (plain text fallback)."""
    bulk_enabled = bool(outcome.get("bulk_used", True))
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        for row in outcome["rows"]:
            status = "ok" if row.get("ok") else "fail"
            latency = row.get("latency_ms")
            line = (f"{row.get('name')}\t{row.get('protocol')}\t"
                    f"socks:{row.get('port')}\t{status}\t"
                    f"{'' if latency is None else f'{latency} ms'}")
            if row.get("error"):
                line += f"\t{row.get('error')}"
            typer.echo(line)
        return
    table = Table(title="vpn probe")
    table.add_column("profile")
    table.add_column("protocol")
    table.add_column("socks port")
    table.add_column("status")
    table.add_column("latency (median of 3)")
    table.add_column("speed (bulk)")
    table.add_column("error")
    for row in outcome["rows"]:
        status = "ok" if row.get("ok") else "fail"
        if row.get("degraded"):
            status = "degraded"
        latency = row.get("latency_ms")
        speed = row.get("speed_mbps")
        speed_cell = ("" if not bulk_enabled
                      else "n/a" if speed is None and row.get("ok")
                      else "" if speed is None
                      else f"{speed} Mbps" +
                           (" (!)" if row.get("degraded") else ""))
        table.add_row(
            str(row.get("name")), str(row.get("protocol")),
            f"127.0.0.1:{row.get('port')}", status,
            "" if latency is None else f"{latency} ms",
            speed_cell,
            str(row.get("error") or ""))
    Console().print(table)
    fastest = outcome.get("fastest")
    if fastest:
        typer.secho(f"fastest: {fastest} ({_latency_of(outcome)} ms)",
                    fg=typer.colors.GREEN)


def _tui() -> None:
    from . import tui
    if tui.questionary is None:
        _err("questionary is required for the interactive menu: "
             "pip install questionary")
        raise SystemExit(1)
    try:
        tui.main_no_args()
    except KeyboardInterrupt:
        typer.echo("")


def main() -> None:
    import sys

    if len(sys.argv) <= 1:
        _tui()
    else:
        app()


if __name__ == "__main__":
    main()
