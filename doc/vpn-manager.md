# VPN Manager (vpnm)

> Клиентский менеджер VPN-профилей для рабочей станции (Ubuntu 24.04)

Входит в [ubuntuInstaller](../) как модуль `scripts/vpn-manager/`. Серверная
сторона — [VDS Orchestrator](vds.md) (`vds gateway links`).

## Что это

`vpnm` — CLI + интерактивное TUI-меню для управления VPN-профилями на клиенте:

- **Xray VLESS Reality** (TCP 443) и **Xray VLESS XHTTP** (TLS + xhttp)
- **Hysteria2** (QUIC, UDP 443)

Каждый профиль хранит share-ссылку (содержит приватные креды — файлы 0600),
генерирует клиентский конфиг и запускается как systemd user-unit
(`vpnm-<name>.service`). Локальный SOCKS5-порт профиля подключается к системному
прокси GNOME одной командой.

## Структура модуля

```
scripts/vpn-manager/
├── vpnm/
│   ├── __main__.py        # точка входа: python3 -m vpnm
│   ├── cli.py             # typer-команды
│   ├── tui.py             # интерактивное меню (questionary), `vpn` без аргументов
│   ├── links.py           # парсер share-ссылок (vless://, hysteria2://)
│   ├── profiles.py        # store профилей (0600)
│   ├── settings.py        # settings.json (ssh-хост для pull)
│   ├── runners.py         # генерация конфигов, systemd user units, up/down/switch
│   ├── xclient.py         # генератор конфигов xray/hysteria
│   ├── bins.py            # установщик бинарей xray-core / hysteria (с зеркалами)
│   ├── pull.py            # импорт ссылок с сервера по ssh
│   ├── sysproxy.py        # GNOME системный прокси через gsettings
│   └── doctor.py          # диагностика окружения
└── tests/                 # pytest, без сети/root (моки subprocess)
```

## Пути на клиенте

| Путь | Содержимое |
|------|-----------|
| `~/.config/vpn-manager/profiles.json` | Профили (share-ссылки, 0600) |
| `~/.config/vpn-manager/state.json` | Активный профиль + флаг sysproxy |
| `~/.config/vpn-manager/settings.json` | Настройки (ssh-хост для pull) |
| `~/.local/share/vpn-manager/generated/` | Сгенерированные конфиги (0600) |
| `~/.config/systemd/user/vpnm-*.service` | Systemd user units |
| `~/.local/lib/vpn/` | Бинари xray / hysteria |

## Установка

```bash
# бинари xray + hysteria в ~/.local/lib/vpn (зеркала ghproxy при блокировках)
vpn install --linger
```

## Команды

| Команда | Что делает |
|---------|-----------|
| `vpn` | TUI-меню (Подключить / Отключить / Переключить / Статус / Профили / Системный прокси / Doctor) |
| `vpn profile import <link>` | Импорт share-ссылки (`--name`, `--replace`) |
| `vpn profile pull [--host root@asv1]` | Импорт ссылок с VDS-сервера по ssh |
| `vpn profile list` / `remove <name>` / `show <name>` | Управление профилями |
| `vpn up <name>` / `vpn down [name]` / `vpn switch <name>` | Жизненный цикл (systemd user units) |
| `vpn status` | Активный профиль, состояние unit, внешний IP через SOCKS |
| `vpn sysproxy on\|off\|status` | GNOME системный прокси → SOCKS активного профиля |
| `vpn autostart on\|off\|status` | Автозапуск активного профиля при входе (systemctl --user enable) |
| `vpn zapret on\|off\|status` | Канал 0: zapret (nfqws DPI-desync) — сервис + прямой canary |
| `vpn doctor` | Диагностика: бинари, порты, v2rayA, ssh, DNS, zapret |
| `vpn monitor [--json]` | Матрица каналов: vpnm / ZeroTier / AmneziaWG / zapret (N/4) |
| `vpn install [--linger] [--autostart [profile]]` | Установка бинарей + linger + автозапуск профиля |

`vpn` также доступен как `python3 -m vpnm`.

## Интеграция с VDS-сервером (profile pull)

На сервере (VDS Orchestrator) команда `vds gateway links` печатает share-ссылки
шлюзов в формате `<имя>: <link>`. `vpn profile pull` выполняет:

```
ssh <host> "vds gateway links"
```

и синхронизирует результат с локальным store:

- имя новое → импорт;
- ссылка изменилась → обновление (эквивалентно `--replace`) с предупреждением;
- ссылка совпадает → skip.

При первом успешном pull хост сохраняется в
`~/.config/vpn-manager/settings.json` и в дальнейшем используется по умолчанию;
если хост не задан — интерактивный вопрос. Ошибки ssh (нет связи, нет `vds`)
дают читаемое сообщение и exit 1.

## Примеры

```bash
# импорт вручную
vpn profile import 'vless://...#my-server'

# подтянуть все ссылки с сервера (и запомнить хост)
vpn profile pull --host root@asv1

# поднять профиль и включить системный прокси (дальше сопровождается сам)
vpn up my-server
vpn sysproxy on

# диагностика
vpn doctor
```

## Системный прокси: автосопровождение

`vpn sysproxy on` включает GNOME SOCKS-прокси на порт активного профиля **и
запоминает это в state.json** (`"sysproxy": true`). И `on`, и `off` зачищают
записи http/https/ftp (host+port) через `gsettings reset` — остаётся чистая
socks-only manual-конфигурация, чужие http-прокси не подхватываются.
Пока флаг включён, системный
прокси сопровождается автоматически:

- `vpn up <name>` и `vpn switch <name>` — прокси перепривязывается на SOCKS-порт
  нового активного профиля (`system proxy updated → 127.0.0.1:<port>`);
- `vpn down` — прокси выключается (mode `none`), чтобы браузер не остался без
  туннеля; флаг при этом **не сбрасывается** — следующий `up` снова включит
  прокси;
- `vpn probe` — после переключения на быстрейший профиль прокси тоже
  перепривязывается;
- `vpn sysproxy off` — выключает прокси и сбрасывает флаг (автосопровождение
  отключено до следующего `sysproxy on`).

То же поведение работает в TUI-меню (Подключить / Отключить / Переключить / Probe).

## Автозапуск

Профили запускаются как systemd user units (`vpnm-<name>.service`), поэтому
автозапуск — это `systemctl --user enable`:

```bash
# включить автозапуск при установке (профиль по имени или текущий активный)
vpn install --autostart mob
vpn install --autostart          # без значения — текущий активный
vpn install --linger --autostart # linger: юнит живёт и после logout

# управление после установки
vpn autostart on      # включить автозапуск активного профиля
vpn autostart off     # выключить (активный профиль; без активного — все enabled vpnm-юниты)
vpn autostart status  # состояние автозапуска + список enabled-юнитов
```

Юнит стартует при входе в систему (при `--linger` — и без активной сессии).
Отключение автозапуска не останавливает запущенный клиент.

## Doctor: что проверяется

Каждая строка — `ok` / `warn` / `fail`, в конце вердикт (exit 1 только при `fail`):

| Проверка | Что означает |
|----------|--------------|
| `bins` | Бинари xray/hysteria на месте и отвечают на `version` |
| `ports` | SOCKS-порты профилей свободны или заняты нашим unit'ом |
| `v2rayA` | Порт 1080 не занят чужим процессом (warn, не fail) |
| `ssh` | Связность с сохранённым хостом (если задан) |
| `dns` | Resolve + запрос через SOCKS активного профиля (только при наличии активного) |
| `active` | Нет активного профиля — warn, DNS пропускается |

## Мониторинг каналов (`vpn monitor`)

Матрица доступности каналов (rich-таблица; `--json` — та же матрица):

| Канал | Что проверяет | Статусы |
|-------|---------------|---------|
| `vpnm` | активный профиль + egress-probe через SOCKS | ok / fail / not_installed |
| `zerotier` | сервис, `zerotier-cli status`, joined-сети | ok / warn / fail / not_installed (в архиве) |
| `amneziawg` | артефакты (бинарь/конфиги/интерфейс) — слот | not_installed (транспорт в разработке) |
| `zapret` | канал 0: сервис активен → **прямой** canary на youtube generate_204 | ok / warn / fail / not_installed |

В конце — `healthy channels: N/4` (exit 1, если ни один канал не ok).
Строка `zapret`: `not_installed` — нейтрально (канал 0 не ставили);
`ok` — «direct DPI-bypass жив»; `fail` — сервис активен, но canary не прошла
(стратегия сломалась — blockcheck заново); `warn` — установлен, но не запущен.

## Канал 0: zapret (DPI-desync без VPN)

[zapret](https://github.com/bol-van/zapret) (nfqws) не туннелирует трафик и
не меняет IP: он модифицирует пакеты на лету (TCP-фрагментация, TTL-трюки и
пр.), чтобы DPI провайдера не распознал соединения к youtube/discord. Это
**комплемент** к vpn-manager, а не замена: VPN-профили (каналы vpnm) дают
смену IP и полный туннель, zapret — только разворачивание DPI-блокировок на
прямых соединениях.

**Что даёт:**

- youtube / discord / Rutube и всё из hostlist работает без VPN, с вашим IP;
- не конфликтует с профилями: SOCKS-прокси vpnm идёт мимо nfqws-правил
  (туннелированный трафик не нуждается в desync).

**Ограничения:**

- **не обходит IP-блоки** — если сервер забанен целиком по IP, desync не
  поможет (это работа для VPN-каналов);
- **стратегии ломаются**: DPI-паттерны провайдеров меняются — при `fail`
  в monitor перезапустите `blockcheck.sh` и обновите `NFQWS_OPT_*` в
  `/opt/zapret/config`;
- требует root (iptables/nftables + raw-сокеты).

**Установка** (на этой машине, интерактивно попросит sudo):

```bash
bash scripts/12_zapret.sh
```

Скрипт: клонирует bol-van/zapret (зеркала ghproxy.net → ghfast.top → direct),
собирает nfqws в `/usr/local/bin`, ставит каркас `/opt/zapret` (config,
ipset/), systemd-юнит `zapret.service` и суточный таймер обновления
community-hostlist (antifilter, URL редактируется в шапке скрипта).
Пытается скачать community-«ru-стратегию»; если недоступно — печатает
инструкцию `blockcheck.sh` (интерактивный, долгий — автоматизировать не
имеет смысла). Подобранные параметры впишите в `NFQWS_OPT_*` в
`/opt/zapret/config` и `sudo systemctl restart zapret`.

**Управление:**

```bash
vpn zapret status   # сервис + прямой canary (204 = жив)
vpn zapret on       # systemctl start через sudo -n; нет прав → печатает команду
vpn zapret off      # аналогично stop
vpn monitor         # строка zapret в матрице каналов
vpn doctor          # строка zapret (не установлен → warn, не fail)
```

То же самое — пункт «Канал 0 (zapret)» в TUI-меню (`vpn`).

## Зависимости

`typer`, `questionary` (TUI), `curl` (probe DNS/external IP), `ssh`,
`systemd` (user units), `gsettings` (GNOME, только для sysproxy).

## Разработка

Тесты: `python3 -m pytest` из `scripts/vpn-manager/` (моки subprocess, без сети).
Секреты (share-ссылки) не логируются; store и конфиги всегда 0600.

### Синхронизация repo ↔ установленная команда
После правок кода модуля переустанавливайте пакет: `pipx install --force scripts/vpn-manager` — установленный `vpn` не подхватывает изменения автоматически (не editable-инсталляция).
