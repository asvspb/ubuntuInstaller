"""vpn-manager CLI (typer).

Working: profile import/list/remove/show/pull, up/down/switch/status,
install-bins, install --linger, sysproxy on/off/status, doctor (WP-D),
repair (diagnose-to-log + restore traffic/DNS), interactive TUI when
invoked without arguments.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Optional

import typer

from . import profiles
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
         "Hysteria2) через локальный SOCKS + каналы (AmneziaWG, zapret, "
         "ZeroTier). Без аргументов — интерактивное TUI-меню.",
    epilog="""
Политика ноутбука (2026-11-11): при загрузке системы — только чистый
трафик провайдера. Любой VPN включается вручную и живёт до выключения
или ребута; автостарт всех VPN-юнитов отключён.

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
  vpn down                   # остановить socks-клиент (системный прокси
                             # выключится автоматически)
  vpn doh off                # DNS мгновенно вернётся к провайдеру (DHCP),
                             # сервисы остановятся; статус: vpn doh status

Предустановленные конфигурации (зеркала 'vpn reset'):

  vpn scheme awg              # основная схема: AWG + доменный split
                              # (= 'vpn start')
  vpn scheme cdn              # канал выживания: CDN-профиль (ws) + системный
                              # прокси (профиль определяется автоматически)
  vpn scheme proxy <profile>  # vpnm как ОБЩИЙ системный прокси по
                              # заданному протоколу (весь браузер через него)
  vpn scheme private <profile># vpnm как ЧАСТНЫЙ прокси: sysproxy не трогается,
                              # печать socks-адреса для прокси-приложений
                              # браузера (расширения/профили/vpncli)

Диагностика:

  vpn monitor                # матрица каналов: vpnm / zapret / zerotier /
                             # amneziawg / dns-doh (+ zapret: on|off)
  vpn status                 # активный профиль, unit, внешний IP (через socks)
  vpn doctor                 # бинарники, порты, ssh-хост, DNS — всё разом
  vpn repair                 # снимок состояния в лог + вернуть трафик
                             # (починить DNS без правки resolved.conf)
  vpn probe --no-switch      # замерить задержки, ничего не переключать

Канал выживания (CDN за Cloudflare, переживает блок IP asv1):

  vpn up cdn-home            # socks 127.0.0.1:2081; sysproxy последует;
                             # браузеру нужен перезапуск после смены порта
  vpn monitor                # строка 'cdn': ok=через CF жив; warn=резерв
                             # ('vpn up cdn-home'); ok в amneziawg показывает
                             # схему split: N зон через туннель, GLM/RU напрямую

Куда смотрит браузер (одной строкой):

  gsettings get org.gnome.system.proxy mode && \
    gsettings get org.gnome.system.proxy.socks host && \
    gsettings get org.gnome.system.proxy.socks port
  # none -> напрямую (RU+TLD-зоны через AWG-split); 2081 -> CDN;
  # 2080 -> Reality/XHTTP; 2083 -> Hysteria2. Chrome применяет
  # системный прокси ТОЛЬКО при старте — после смены перезапустить.

Профили и сервер:

  vpn profile pull -H root@asv1   # подтянуть share-линки с VDS (asv1)
  vpn profile list                # сохранённые профили (без секретов)
  vpn profile show <profile>      # детали + конфиг (ПЕЧАТАЕТ СЕКРЕТЫ)
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
def profile_show(name: str) -> None:
    """Детали профиля + сгенерированный конфиг (ПЕЧАТАЕТ СЕКРЕТЫ)."""
    try:
        record = profiles.get(name)
    except profiles.ProfileStoreError as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
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
def up(
    name: str,
    mux: Optional[bool] = typer.Option(
        None, "--mux/--no-mux",
        help="Mux для CDN-профиля (разово; по умолчанию — настройка 'vpn mux')"),
) -> None:
    """Запустить профиль: vpn up <profile> (socks-порт см. в vpn profile list)."""
    from . import runners
    try:
        config_path = runners.up(name, mux=mux)
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
    """Остановить активный socks-клиент (системный прокси выключится сам)."""
    from . import runners
    try:
        stopped = runners.down(name)
    except (profiles.ProfileStoreError, runners.RunnerError) as exc:
        _err(str(exc))
        raise typer.Exit(code=2)
    _ok(f"stopped '{stopped}'")
    _sync_sysproxy_off_after()


@app.command("mux")
def mux_cmd(action: str = typer.Argument(
        ..., help="'on' | 'off' | 'status'")) -> None:
    """Mux для CDN-профилей (VLESS+WS): vpn mux on|off|status.

    Mux мультиплексирует несколько потоков в одном TLS-соединении — каждый
    запрос не платит полный TLS→WS→VLESS стек. Применяется ТОЛЬКО к
    WS-профилям (CDN через Cloudflare): Vision несовместим с Mux, XHTTP
    мультиплексирует сам. По умолчанию выключен.
    """
    from . import settings
    action = action.strip().lower()
    if action == "on":
        settings.set_mux(True)
        _ok("mux on (применится при следующем 'vpn up <CDN-профиль>')")
    elif action == "off":
        settings.set_mux(False)
        _ok("mux off")
    elif action == "status":
        enabled = settings.get_mux()
        state = "on" if enabled else "off"
        typer.echo(f"mux: {state}")
        _warn("mux применяется только к CDN-профилям (VLESS+WS); "
              "Reality/XHTTP его не используют")
    else:
        _err("mux: ожидается on|off|status")
        raise typer.Exit(code=2)


@app.command("scheme")
def scheme_cmd(preset: str = typer.Argument(
        ..., help="'awg' | 'cdn' | 'proxy <profile>' | 'private <profile>'"),
        profile: Optional[str] = typer.Argument(
            None, help="для 'proxy'/'private': имя профиля")) -> None:
    """Предустановленные конфигурации (зеркала 'vpn reset').

    vpn scheme awg               — основная схема: AWG-туннель + доменный
                                   split (то же, что 'vpn start')
    vpn scheme cdn               — канал выживания: CDN-профиль (ws) +
                                   системный прокси на его socks (браузер —
                                   весь через CF); имя профиля определяется
                                   автоматически (cdn-home → *cdn* → первый ws)
    vpn scheme proxy <profile>   —vpnm как ОБЩИЙ системный прокси: поднять
                                   профиль по заданному протоколу + sysproxy on
    vpn scheme private <profile> — vpnm как ЧАСТНЫЙ прокси: поднять профиль,
                                   системный прокси НЕ трогать; печать адреса
                                   socks для прокси-приложений браузера
                                   (расширения/профили/vpncli) — весь остальной
                                   трафик остаётся в текущей схеме

    Все пресеты идемпотентны; 'vpn reset' — общий выключатель.
    """
    from . import amneziawg as awg
    from . import runners
    from . import sysproxy
    preset = preset.strip().lower()

    if preset == "awg":
        ok_t, msg_t = awg.tunnel_control("on")
        (_ok if ok_t else _err)(f"AWG: {msg_t}")
        if not ok_t:
            _err("split не применяется без туннеля — повторите 'vpn scheme awg'")
            raise typer.Exit(code=1)
        ok_s, msg_s = awg.domain_split_apply("on")
        (_ok if ok_s else _warn)(f"split: {msg_s}")
        if not ok_s:
            raise typer.Exit(code=1)
        _ok("схема awg активна: TLD-зоны/youtube/telegram через AWG, "
            "GLM/RU напрямую")
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
            typer.echo("  • отдельный Chrome-профиль: google-chrome "
                       "--profile-directory=X "
                       "--proxy-server='socks5://127.0.0.1:%d'" % port)
            typer.echo("  • вкладка терминала: vpncli on %d" % port)
            typer.echo("  • curl/инструменты: --socks5-hostname "
                       "127.0.0.1:%d" % port)
            typer.echo("Остальной трафик остаётся в текущей схеме "
                       "(AWG-split/direct). Выключение профиля: 'vpn down'.")
        return

    _err("unknown preset: expected 'awg' | 'cdn' | 'proxy <profile>' | "
         "'private <profile>'")
    raise typer.Exit(code=2)


@app.command()
def start() -> None:
    """Поднять основную рабочую схему: AWG-туннель + доменный split.

    Зеркало 'vpn reset': туннель vpn-awg-split@laptop + маршрутизация зон
    из /opt/awg-split/domains.txt (ходовые TLD + youtube/telegram) через
    туннель, GLM-кластер и RU — напрямую; DNS-цепочка (dnsmasq+DoH с
    fallback 77.88.8.8) поднимается автоматически. Системный прокси не
    включается — схема работает на уровне маршрутов. Идемпотентно.
    """
    from . import amneziawg as awg
    failures: list = []
    ok_t, msg_t = awg.tunnel_control("on")
    (_ok if ok_t else _err)(f"AWG: {msg_t}")
    if not ok_t:
        failures.append("AWG-туннель")
        _err("split не применяется без туннеля — поднимите туннель и "
             "повторите 'vpn start'")
        raise typer.Exit(code=1)
    ok_s, msg_s = awg.domain_split_apply("on")
    (_ok if ok_s else _warn)(f"split: {msg_s}")
    if not ok_s:
        failures.append("доменный split")
    if failures:
        _warn("схема поднята частично: " + ", ".join(failures)
              + " — повторите 'vpn start' или выполните подсказки выше")
        raise typer.Exit(code=1)
    _ok("основная схема активна: TLD-зоны/youtube/telegram через AWG, "
        "GLM/RU напрямую (браузер — перезапустить, если sysproxy менялся)")


@app.command()
def reset() -> None:
    """Вернуть всё к состоянию «как после перезагрузки ОС» (чистый старт).

    Останавливает активный socks-профиль (+sysproxy), демонтирует доменный
    split и останавливает AWG-туннель, гасит DoH-цепочку — DNS возвращается
    к провайдеру (DHCP). Boot-guard остаётся включённым (защита загрузки).
    Идемпотентно: повторный запуск ничего не ломает.
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

    ok_s, msg_s = awg.domain_split_apply("off")
    (_ok if ok_s else _warn)(f"split: {msg_s}")
    if not ok_s:
        failures.append("split")
    ok_t, msg_t = awg.tunnel_control("off")
    (_ok if ok_t else _warn)(f"AWG: {msg_t}")
    if not ok_t:
        failures.append("AWG")
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


@app.command()
def status() -> None:
    """Активный профиль, состояние unit, внешний IP (замер через socks)."""
    from . import runners
    active = runners.get_active()
    if not active:
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
    """Матрица каналов: vpnm / cdn / amneziawg / zapret / zerotier / dns-doh.

    Строка amneziawg показывает схему AWG-split (N зон через туннель,
    GLM/RU напрямую); строка cdn — канал выживания за Cloudflare
    (ok=работает, warn=резерв 'vpn up <cdn-профиль>'). --json для скриптов."""
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
        for row in rows:
            typer.echo(f"{row['channel']}\t{row['status']}\t{row['detail']}")
        return
    status_colors = {"ok": "green", "warn": "yellow",
                     "not_installed": "yellow", "fail": "red"}
    table = Table(title=title)
    table.add_column("channel")
    table.add_column("status")
    table.add_column("detail")
    for row in rows:
        table.add_row(
            str(row["channel"]),
            f"[{status_colors.get(str(row['status']), 'white')}]"
            f"{row['status']}[/]",
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
