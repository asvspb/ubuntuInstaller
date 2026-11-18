# VPN Manager (vpnm)

> Клиентский менеджер VPN-профилей для рабочей станции (Ubuntu 24.04)

Входит в [ubuntuInstaller](../) как модуль `scripts/vpn-manager/`. Серверная
сторона — [VDS Orchestrator](vds.md) (`vds gateway links`).

## Что это

`vpnm` — CLI + интерактивное TUI-меню для управления VPN-профилями на клиенте:

- **Xray VLESS Reality** (TCP 443), **Xray VLESS XHTTP** (TLS + xhttp) и
  **Xray VLESS WS за CDN/Cloudflare** (канал выживания)
- **Hysteria2** (QUIC, UDP 443), включая port hopping (`mport=`) и
  obfs salamander (`obfs=`) — серверные фичи vds (SPEC-20261112 WP-1)

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
│   ├── links.py           # парсер share-ссылок (vless://, hysteria2://), валидация mport/obfs
│   ├── profiles.py        # store профилей (0600), resolve_cdn (имя CDN-профиля)
│   ├── settings.py        # settings.json (ssh-хост для pull)
│   ├── runners.py         # генерация конфигов, systemd user units, up/down/switch
│   ├── xclient.py         # генератор конфигов xray/hysteria (hop, obfs)
│   ├── bins.py            # установщик бинарей xray-core / hysteria (с зеркалами)
│   ├── pull.py            # импорт ссылок с сервера по ssh
│   ├── probe.py           # замер задержек + bulk-скорость (D-SPEED), выбор быстрейшего
│   ├── speed.py           # живой мульти-эндпоинтный замер скорости (vpn speed)
│   ├── channels.py        # матрица каналов для `vpn monitor`
│   ├── sysproxy.py        # GNOME системный прокси через gsettings
│   ├── zapret.py          # канал 0: zapret/nfqws (DPI-desync без прокси)
│   ├── doh.py             # DoH-резолвер cloudflared
│   ├── amneziawg.py       # AWG: локальный туннель + клиенты на сервере
│   ├── doctor.py          # диагностика окружения
│   ├── repair.py          # снимок состояния + возврат трафика/DNS
│   ├── guard/             # vpn guard: потоковый разбор клиентских логов (см. раздел)
│   └── scout/             # vpn scout: активная разведка маршрутов (модель, контекст, пробы, скоринг, CLI)
└── tests/                 # pytest, без сети/root (моки subprocess)
```

## Пути на клиенте

| Путь | Содержимое |
|------|-----------|
| `~/.config/vpn-manager/profiles.json` | Профили (share-ссылки, 0600) |
| `~/.config/vpn-manager/state.json` | Активный профиль + флаг sysproxy |
| `~/.config/vpn-manager/settings.json` | Настройки (ssh-хост для pull) |
| `~/.local/share/vpn-manager/generated/` | Сгенерированные конфиги (0600) |
| `~/.local/share/vpn-manager/last-speed.json` | Отчёт последнего `vpn speed` (0600) |
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
| `vpn profile pull [--host root@asv1] [--include-obfs]` | Импорт ссылок с VDS-сервера по ssh (+ obfs-профили `<name>@obfs`) |
| `vpn profile list` / `remove <name>` / `show <name> [--link]` | Управление профилями; `--link` — исходная share-ссылка (секрет) |
| `vpn profile config <name> [--native]` | Конфиг текстом: share-ссылка или сгенерированный JSON/YAML (секрет) |
| `vpn profile qr <name> [--native]` | QR в терминале для телефона (нужен `qrencode`) |
| `vpn profile export [-o FILE] [--stdout]` | Бандл всех профилей для бэкапа/переноса (JSON, 0600, содержит секреты) |
| `vpn profile restore FILE [--replace] [--dry-run] [-y]` | Влить бандл: новые добавить, совпадающие skip/`--replace`; `-` — stdin |
| `vpn up [<name>] [--scout] [--dry-run]` / `vpn down [name]` / `vpn switch <name>` | Жизненный цикл (systemd user units); `--scout` — сначала read-only разведка маршрутов и поднять рекомендованный (`--dry-run` — только план). `<name>` и `--scout` взаимоисключающие (вместе → exit 2) |
| `vpn scout [--json] [--sync [--host H]] [--probe-all]` | Активная разведка маршрутов: по умолчанию Stealth Cascade (локальная сортировка → одна проба до первого хорошего); `--probe-all` — полный параллельный опрос. RECOMMENDED/FALLBACK/«отсеяны» (read-only; exit 0 — рекомендация есть) |
| `vpn start [awg\|relay\|reality\|xhttp] [--proto awg\|reality]` / `vpn reset` | Основная схема: по умолчанию **AWG**+доменный split, `relay` — RU-релей (ADR-007; `--proto reality` — socks-фолбэк `relay-reality`), `reality`/`xhttp` — xray-split / полный сброс в состояние «как после перезагрузки». Контуры взаимоисключающие |
| `vpn scheme awg\|xray-split [p]\|xhttp-split [p]\|cdn\|proxy <p>\|private <p>` | Пресеты: AWG+split / tun xray0 (Reality/XHTTP) / только XHTTP / CDN / общий прокси / частный прокси |
| `vpn probe` | Замер задержек + bulk-скорость (один эндпоинт), переключение на быстрейший |
| `vpn speed` | **Живой замер скорости**: все каналы, несколько эндпоинтов, real-time таблица |
| `vpn status` | Режим (`DIRECT`/`RELAY` из `mode.json` + live-юниты), активный профиль / awg-схема, состояние unit, внешний IP через SOCKS. При AWG печатает `awg-схема активна: доменный split через vpn-awg-split@<iface>.service` — `no active profile` относится только к socks-профилям и в этом режиме не выводится. Если `mode.json` говорит `relay`/`direct`, а юнит **неактивен**, статус не рапортует живой режим, а печатает `mode: RELAY (юнит … неактивен — туннель не поднят)` и подсказку `vpn start relay` (direct — `vpn start`) |
| `vpn sysproxy on\|off\|status` | GNOME системный прокси → SOCKS активного профиля |
| `vpn autostart on\|off\|status` | Автозапуск активного профиля при входе (systemctl --user enable) |
| `vpn zapret on\|off\|status` | Канал 0: zapret (nfqws DPI-desync) — сервис + прямой canary |
| `vpn doh on\|off\|status` | DoH-резолвер cloudflared (127.0.0.1:5053) |
| `vpn amneziawg status\|add-client\|remove-client\|tunnel\|qr` | AWG: локальный туннель, клиенты на сервере, QR-конфиг для телефона |
| `vpn doctor` | Диагностика: бинари, порты, v2rayA, ssh, DNS, zapret |
| `vpn repair` | Снимок состояния в лог + вернуть трафик/DNS |
| `vpn monitor [--json]` | Матрица каналов: vpnm / cdn / ZeroTier / amneziawg / xray-split / zapret / dns-doh (N/7) |
| `vpn guard run [--once] [--json\|--quiet] [--foreground] [--since -2m] [--trace]` | Потоковый анализ журналов: правила G-01…G-12, инциденты, findings (read-only) |
| `vpn guard status\|findings\|sources [--json]` | Состояние, история, покрытие источников |
| `vpn guard explain <id\|G-xx>` | «Почему сработало»: окно, сигналы, FP-проверки, действия (без `--json`) |
| `vpn guard rules [--json]` | Каталог правил, текущие пороги, `rules_hash`/`config_hash` |
| `vpn guard export [--since 7d] [-o FILE] [--keep-hosts]` | Обезличенный бандл findings+coverage (≤5 МБ, хосты → `<host>`) |
| `vpn guard selftest` | Проверка схем/парсера/правил/пост-сканера из установленного пакета |
| `vpn guard notify-test [--severity high]` | Тестовый баннер через тот же opt-in/порог (cooldown 12/час намеренно обойдён) + статус OK/причина отказа |
| `vpn guard sync [--host root@asv] [--timeout 10.0]` | **Единственная сетевая команда:** один ssh-запрос серверного вердикта |
| `vpn guard install [--apply] [--linger] [--sync-timer] [--netlink]` | user-юнит `vpn-guard.service` (opt-in, dry-run по умолчанию); `--netlink` — AF_NETLINK для sysfs-AWG |
| `vpn guard remove [--apply] [--purge]` | Выключить/удалить юнит guard (dry-run по умолчанию); `--purge` — вместе с findings/state |
| `vpn guard failover status [--json]` | Авто-переключение каналов: enabled/pin/cooldown/backoff/лок; досылает очередь уведомлений |
| `vpn guard failover run --once [--json]` | Один проход: триггер → local_net → лестница relay→hy2→awg→cdn → pin/blackout |
| `vpn guard failover reset` | Снять pin/cooldown/backoff (эксплуатационный сброс) |
| `vpn guard failover enable\|disable [--apply]` | Конфиг + юниты `vpn-failover.service`/`.path` (dry-run без `--apply`) |
| `vpn guard failover test-notify` | Проверить каналы failover: desktop (opt-in) и Telegram |
| `vpn install [--linger] [--autostart [profile]]` | Установка бинарей + linger + автозапуск профиля |
| `vpn install-bins` | Только бинари xray/hysteria (без linger/автозапуска) |

`vpn` также доступен как `python3 -m vpnm`.

### Активация — из вкладки терминала

Схемы и профили включаются обычной командой в любой вкладке терминала:

```bash
vpn start              # AWG + доменный split (по умолчанию); гасит релей
vpn start relay        # RU-релей: весь трафик → релей → exit-нода
vpn start relay --proto reality   # фолбэк: socks-профиль relay-reality
vpn start reality      # Reality-split (tun xray0, TCP 443)
vpn start xhttp        # XHTTP-split (tun xray0, TCP 8443)
vpn up <profile>       # socks-профиль (Reality/XHTTP/Hy2/CDN)
vpn down               # выключить профили и релей (guard остаётся)
vpn reset              # выключить всё
```

Вкладку после запуска можно закрыть: схема живёт в systemd-юните, действует
на всю систему (не только на эту вкладку) и держится до `vpn reset` или
перезагрузки ОС.

### VPN только для одной вкладки/браузера

`vpn start`, `vpn scheme proxy` и split-схемы включают VPN для **всего**
трафика. Точечно:

```bash
vpn reset                    # чистое состояние
vpn up phone                 # socks 127.0.0.1:2080; sysproxy НЕ включается
vpncli on                    # только текущая вкладка терминала (env)
vpncli status                # состояние + exit IP
vpncli off

# отдельное окно/профиль Chrome целиком через VPN:
google-chrome --user-data-dir="$HOME/.config/chrome-vpn" \
  --proxy-server="socks5://127.0.0.1:2080"
```

- **вкладка терминала** — `vpncli on` (влияет только на эту сессию);
- **вкладка браузера** — только расширением (SwitchyOmega/FoxyProxy):
  профиль SOCKS5 `127.0.0.1:2080`, переключение per-site/per-tab;
- **отдельный Chrome** — `--user-data-dir` + `--proxy-server` (весь профиль,
  основной Chrome остаётся напрямую);
- `vpn scheme private <profile>` — то же самое, но с печатью адреса и
  подсказок, sysproxy не трогается;
- после `vpn reset` сначала `vpn up <profile>`: в чистом состоянии sysproxy
  выключен, поэтому обычный браузер останется прямым.

Ограничения: WebRTC может утекать реальный IP в per-tab режиме; QUIC через
SOCKS5 не ходит (Chrome сам откатывается на TCP).

Shell-guard `_check_vpn_active()` в `$USER/.zshrc` / `$USER/.bashrc` (блокирует запуск
Antigravity без активного VPN) признаёт, помимо ZeroTier/WireGuard/vpnm, AmneziaWG
(интерфейс `awg*`/`amnezia*` UP/UNKNOWN или вывод `awg show`) и xray-split
(юнит `vpn-xray-split@*` running + `xray0` up + ip rule `fwmark 0x23000`).

## `vpn speed` — живой замер скорости (2026-11-13)

**Зачем отдельная команда.** `vpn probe` меряет **один** bulk-эндпоинт
(`speed.cloudflare.com`) и потому врёт, когда сам эндпоинт зарезан
провайдером. Пример 2026-11-13: CF давал 0–7 Мбит/с на всех транспортах, а на
OVH те же каналы — 62–75 Мбит/с. `vpn speed` перебирает эндпоинты и показывает
процесс в реальном времени.

### Запуск

```bash
vpn speed                                 # все профили + AWG, 2 замера на канал
vpn speed --samples 3                     # 3 замера (медиана устойчивее)
vpn speed --flows 4                       # 4 параллельных потока (агрегат)
vpn speed --profiles mob,phone --no-awg   # только выбранные, без AWG
vpn speed --include-cdn                   # добавить CDN-профили (по умолчанию пропущены)
vpn speed --include-cdn --ttfb-samples 3  # + TTFB p50/p95 (работает даже без bulk)
vpn speed --json | jq .verdict            # машинный вывод без live-таблицы
vpn speed --out /tmp/speed.json           # свой путь отчёта
```

### Опции

| Опция | По умолчанию | Смысл |
|---|---|---|
| `--samples N` | 2 | Замеров на канал (и latency, и bulk); медиана |
| `--ttfb-samples N` | 3 | TTFB-замеров на канал (серийно, с паузой — И26); p50/p95 |
| `--flows N` | 1 | Параллельных потоков в одном bulk-замере (агрегат) |
| `--timeout S` | 35 | Таймаут одного bulk-замера, с |
| `--no-awg` | — | Не поднимать/мерить AmneziaWG-split |
| `--include-cdn` | выкл. | Мерить и CDN-профили (`xray-cdn-ws`); по умолчанию пропускаются |
| `--profiles a,b` | все | Подмножество профилей |
| `--json` | — | Только JSON в stdout (live-таблица выключена) |
| `--out PATH` | `last-speed.json` | Куда сохранить отчёт |

**CDN-профили по умолчанию не меряются**: через Cloudflare bulk не отдаётся
ни с одного источника, замер только тратит время и даёт «нет данных». В конце
прогона печатается предупреждение со списком пропущенных; включить —
`--include-cdn`.

### Что видно в реальном времени

`rich.Live`-таблица, обновление 8 раз/с; по мере замера меняются статус,
задержка и скорость:

| Колонка | Смысл |
|---|---|
| канал / протокол | профиль из store; AWG — отдельной строкой |
| статус | `запуск → задержка → ttfb → скорость → готово` / `ошибка` / `нет данных` / `только задержка` |
| latency, с | медиана замеров через туннель |
| ttfb p50/p95, мс | TTFB по `example.com` (серийно, `--ttfb-samples`, по умолчанию 3); **зелёный p50 ≤700**, **жёлтый ≤1500**, **красный выше** |
| скорость, Мбит/с | медиана; **зелёный ≥50**, **жёлтый ≥15**, **красный <15** |
| эндпоинт | какой источник реально отдал байты: `ovh10` / `cf3` / `gh-readme` |
| примечание | ошибка, нестабильность, причина «нет данных» |

Внизу — `канал N/M · время`, после прогона — вердикт «быстрее всех /
стабильнее всех» и предупреждения о разбросе.

### Пример полного прогона (2026-11-13, asv1)

| канал | протокол | latency, с | Мбит/с | эндпоинт |
|---|---|---|---|---|
| xhttp-web | xray-xhttp | 0.21 | **106.3** | ovh10 |
| phone | xray-reality | 0.33 | **74.6** | ovh10 |
| awg | amneziawg-split | 0.30 | **66.2** | ovh10 |
| mob | hysteria2 | 0.29 | **41.4** | ovh10 |
| ~~cdn-home~~ | xray-cdn-ws | 2.39 | — (bulk не отдался; с 2026-11-13 пропускается по умолчанию) | — |

Вердикт того прогона: быстрее всех `xhttp-web`, стабильнее всех `awg`.

### Как читать результаты

1. **Медиана, а не один сэмпл**: `--samples 2` по умолчанию; разброс виден в
   «примечании» (предупреждение, если max−min > 50 %).
2. **`--flows N`** отвечает на вопрос «это потолок канала или одного потока»:
   один поток ≈7–20 Мбит/с при RTT 70 мс — норма; 4 потока обычно дают ×3–4.
3. **`эндпоинт`** — диагностика: если у всех каналов `cf3` и низкие цифры,
   виноват CF, а не протокол; `ovh10` — эталон для внешнего канала.
4. **«нет данных»** ≠ «0 Мбит/с»: соединение живо (latency есть), но ни один
   bulk-источник не отдал байты (типично для CDN/CF).
5. **«только задержка»** — bulk 0 байт, но TTFB измерен: канал жив и применим
   как браузерный резерв, просто bulk зарезан. В отчёте `ttfb_p50_ms`/`ttfb_p95_ms`
   заполнены, статус не «ошибка».
6. **TTFB** (`time_starttransfer` до `example.com`) включает полный стек
   TCP+TLS+запрос и не зависит от bulk-шейпинга — единственная честная метрика
   для CDN-канала. p50 ≤700 мс — «можно пользоваться»; 700–1500 — терпимо;
   выше — каждый запрос платит полный TLS-хендшейк (мультиплексирование
   штатно не используется, см. заметку ниже).
7. **Вердикт** сравнивает только успешные каналы; `most_stable` — минимальный
   разброс среди каналов с ≥2 успешными замерами.

### Отчёт

`~/.local/share/vpn-manager/last-speed.json` (0600; `--out` — свой путь).
Фрагмент (поля канала — из `speed.Channel.as_dict()`):

```json
{
  "started": "2026-11-13T10:44:00+00:00",
  "finished": "2026-11-13T10:48:30+00:00",
  "params": {"samples": 2, "flows": 1, "timeout": 35.0},
  "channels": [
    {"name": "phone", "protocol": "xray-reality", "status": "готово",
     "latency_s": 0.33, "speed_mbps": 74.6, "endpoint": "ovh10",
     "egress": "103.45.247.172", "speed_samples": [74.6, 72.1],
     "latency_samples": [0.33, 0.34], "error": ""}
  ],
  "verdict": {"fastest": "xhttp-web", "most_stable": "awg", "notes": []}
}
```

### Методика и ограничения

- **CDN пропускается по умолчанию** (`--include-cdn` — включить): CF не отдаёт
  bulk, а latency CDN и так видна в `vpn monitor`/`vpn probe`.

- **Серийно (И26)**: каналы меряются по одному; паузы между замерами — это
  защита от behavioral-«заморозки» TSPU, а не «медлительность».
- **AWG** поднимается (`vpn start`) и снимается (`vpn reset`) автоматически,
  если не был активен; состояние системы после прогона — как до него.
- **Egress-проверка**: `api.ipify.org` через каждый туннель → должен быть IP
  сервера; иначе замер невалиден (трафик шёл напрямую).
- **CDN/CF**: bulk может не отдаться (CF→origin и CF-egress к OVH), latency
  при этом валидна. Для CDN важнее latency, чем Мбит/с.
- **Скорость ≠ доступность**: «окно»/блокировку ловит `vpn probe` и
  `vpn monitor`, а не `vpn speed`.

### `probe` vs `speed`

| | `vpn probe` | `vpn speed` |
|---|---|---|
| Цель | выбрать быстрейший профиль по latency (+bulk) и переключиться | измерить скорость всех каналов |
| Эндпоинты | один (CF) | OVH → CF → GitHub (фолбэк) |
| Потоки | 1 | `--flows` (1..8) |
| Вывод | итоговая таблица | live-таблица + вердикт |
| Профили | только socks | socks (без CDN) + AWG-tun |
| Отчёт | — | JSON 0600 |

### TUI и траблшутинг

Пункт меню **«Замер скорости (live)»** запускает то же самое.
Если у всех каналов «нет данных» — проверьте `curl -I https://proof.ovh.net`
напрямую (эндпоинт мог быть заблокирован) и попробуйте `--flows 4`/другой
эндпоинт; если `egress` не равен IP сервера — профиль не поднялся, смотрите
`vpn doctor`.

## Mux через Cloudflare — отклонён (2026-11-13)

Идея включить Mux.Cool (`mux: {enabled, concurrency}`) для CDN-профиля
(`xray-cdn-ws`) проверена и **отклонена**: через Cloudflare канал с mux
нестабилен — 1/5 успешных запросов против 5/5 без mux в тот же момент;
`concurrency` 1 и 2 не помогают (соединение залипает после первого запроса).
Код mux из `vpnm` удалён, флагов `--mux`/`--no-mux` и команды `vpn mux` нет.

Ref: `TEST-20261113-cdn-mux` (приёмка SPEC-20261113-cdn-usability, WP-1 —
FAIL). Реальное мультиплексирование для CDN — отдельная идея **XHTTP через
Cloudflare** (новая SPEC), а не Mux.Cool.

## XHTTP: `xPaddingBytes` + `xmux` (GR-25, 2026-11-16)

Для XHTTP-профилей (`xray-xhttp`, и socks `vpn up xhttp-*`, и tun
`vpn start xhttp`) клиентский конфиг несёт анти-ML профиль из
SPEC-20261116-xhttp-xmux-padding §2.2:

```json
"xhttpSettings": {
  "path": "/<secret>", "host": "<sni>", "mode": "auto",
  "xPaddingBytes": "100-1000",
  "xmux": {"maxConcurrency": "2-4", "maxConnections": 0,
           "cMaxReuseTimes": "32-64", "hMaxRequestTimes": "64-128",
           "hMaxReusableSecs": "180-300", "keepAlivePeriod": 0}
}
```

- `xPaddingBytes: 100-1000` — рандомная длина служебного padding'а на каждый
  фрейм: размывает гистограмму размеров пакетов (SPLT), по которой
  ML-классификатор АСБИ узнаёт дефолтный XHTTP по первым 10–15 пакетам.
- `xmux` — управление жизненным циклом **сокетов транспорта**: 2–4
  параллельных соединения (как Chrome к HTTP/2 origin), не более 32–64
  туннельных сессий на сокет (`cMaxReuseTimes`), не более 64–128 HTTP-запросов
  внутри соединения (`hMaxRequestTimes`) и жёсткий TTL 180–300 с
  (`hMaxReusableSecs`). `keepAlivePeriod: 0` — никаких пингов в простое.
- **Ротация сокетов штатная, не разрыв.** Долгое скачивание (стрим, образ)
  идёт непрерывно, а в `ss -tn dst <сервер>:8443` видно 2–4 сокета, которые
  плавно сменяются каждые 3–5 минут с новым исходящим портом. Для DPI/NetFlow
  это серия независимых коротких HTTPS-сессий, а не одна «VPN-труба» на
  сотни мегабайт.
- Не путать с **Mux.Cool** (отклонён выше): `xmux` живёт внутри
  `xhttpSettings` транспорта, outbound-level `mux` в конфиге отсутствует.
- Reality/CDN-WS конфиги этих полей не получают; серверный инблэнд vds с
  `xPaddingBytes` полностью совместим и со старыми клиентами (только `path`).

Проверка на живой машине — T-XM6 (A/B): 50 МБ через `vpn start xhttp` →
`ss -tn dst <SERVER_IP>:8443` показывает 2–4 сокета и их ротацию за 3–5 минут
без обрыва скачивания.

## Бенчмарк CDN (`scripts/bench-cdn-mux.sh`)

Воспроизводимый baseline живого CDN-профиля. Имя файла историческое: A/B
mux/nomux из него удалён (mux через CF отклонён, см. заметку выше), режима
mux и флага `--mux` больше нет — скрипт меряет один канал как есть.

```bash
bash scripts/bench-cdn-mux.sh              # профиль cdn-home, socks 2081
bash scripts/bench-cdn-mux.sh cdn-home 2081
SAMPLES=2 bash scripts/bench-cdn-mux.sh    # короткий прогон
```

Что делает:

1. **Контроль** — 3× прямой TLS к `https://asvpersonal.ru/` без socks,
   печатает p50 `time_appconnect`. Это проверка «канал до CF жив сейчас»:
   p50 ≤ 2 с — ок; > 2 с (или контроль не измерился) — предупреждение
   «возможен TSPU-эпизод, результаты туннеля могут быть искажены».
2. `vpn up <profile>` и **smoke** — один `curl` через socks с
   `--max-time 8`. Если HTTP-код не 2xx/3xx — «канал не поднялся/не
   отвечает», скрипт делает `vpn reset` и выходит с кодом 1, не тратя
   N×30 с на заведомо мёртвый канал.
3. **Baseline** — N×(TTFB example.com, 1 МБ OVH) серийно с паузой 1.5 с
   (И26); N по умолчанию 7, переопределяется `SAMPLES`.
4. `vpn reset` в конце; `trap` снимает профиль при любом выходе.

Итог и что означают цифры:

| метрика | смысл |
|---|---|
| контроль p50 TLS | время TLS-хендшейка напрямую до CF; > 2 с — среда искажена |
| smoke | HTTP-код через туннель; не 2xx/3xx — канал не работает |
| ttfb p50/p95, с | время до первого байта `example.com` через туннель (nearest-rank по успешным пробам) |
| bulk медиана, Мбит/с | 1 МБ с OVH; пусто/0 — bulk зарезан (для CDN это норма) |
| ttfb fail / bulk fail | сколько проб не прошло (timeout/`000`) |

Контроль отделяет «проблему канала» от «проблемы среды»: CF TLS-залипание
и TSPU-эпизоды искажают цифры туннеля, поэтому прогон стоит повторять в
разное время и фиксировать дату/время.

## Probe: серийные пробы с паузами (инвариант И26)

`vpn probe` намеренно работает **последовательно**: профили перебираются по
одному, между профилями пауза `SLEEP_BETWEEN` (1 с), а между тремя замерами
внутри профиля — `SAMPLE_PAUSE` (1 с, с 2026-11-13). Причина: TSPU
behavioral-«заморозка» (blackhole ~120 с) триггерится серией хендшейков к
одному SNI; параллельные пробы сами создают инцидент. Инвариант: **один SNI
= один инбаунд, пробы серийно и разнесённо во времени**. При ручной приёмке —
не запускать `openssl`/`ssh`/`curl` к тому же SNI параллельно.

## Сохранение и передача профилей

Бандл — один JSON со всеми профилями (share-ссылки). Конфиги отдельно не
хранятся: они генерируются заново из ссылок, поэтому бандла достаточно для
переноса на другую машину.

```bash
vpn profile export                      # ~/vpn-profiles-<дата>.json, 0600
vpn profile export -o /tmp/vpn.json     # свой путь (--force для перезаписи)
vpn profile export --stdout | age -p > vpn-profiles.age   # шифрование

scp ~/vpn-profiles-*.json user@new-laptop:~
vpn profile restore ~/vpn-profiles-*.json --dry-run       # план
vpn profile restore ~/vpn-profiles-*.json                 # merge + подтверждение
vpn profile restore ~/vpn-profiles-*.json --replace -y    # перезаписать совпадения
age -d vpn-profiles.age | vpn profile restore - -y        # из stdin
```

- файл содержит секреты (UUID/пароли), всегда 0600 — не коммитить и не
  пересылать открытым текстом;
- `restore` валидирует **все** ссылки до записи: битый профиль отменяет импорт;
- по умолчанию совпадающие имена пропускаются; `--replace` перезаписывает;
- `--dry-run` показывает план без изменения store;
- после импорта: `vpn profile list` → `vpn up <profile>`;
- одна ссылка для телефона: `vpn profile show <profile> --link` (секрет).

### QR и конфиг текстом (как `wg`)

```bash
vpn profile config phone            # share-ссылка (vless://…) текстом
vpn profile config phone --native   # JSON (xray) / YAML (hysteria2)
vpn profile qr phone                # QR в терминале → сканировать телефоном
vpn profile qr phone --native       # QR самого конфига (плотный)
```

QR строит системный `qrencode` (`sudo apt install qrencode`); share-ссылка
передаётся ему через stdin, поэтому секрет не попадает в argv/логи.
Сканируется v2rayNG, Hiddify, Streisand и другими Xray-клиентами.

## Интеграция с VDS-сервером (profile pull)

На сервере (VDS Orchestrator) команда `vds gateway links` печатает share-ссылки
шлюзов в формате `<имя>: <link>`. `vpn profile pull` выполняет:

```
ssh <host> "vds gateway links"            # обычный режим
ssh <host> "vds gateway links --include-obfs"   # + obfs-профили
```

Флаг `--include-obfs` забирает и obfs-ссылку каждого hysteria2-клиента
(`<name>@obfs`, отдельный UDP-порт, `obfs=salamander`): в store появляется
вторая запись рядом с основной — обе синхронизируются независимо.

Синхронизация результата с локальным store:

- имя новое → импорт;
- ссылка изменилась → обновление (эквивалентно `--replace`) с предупреждением;
- ссылка совпадает → skip.

При первом успешном pull хост сохраняется в
`~/.config/vpn-manager/settings.json` и в дальнейшем используется по умолчанию;
если хост не задан — интерактивный вопрос. Ошибки ssh (нет связи, нет `vds`)
дают читаемое сообщение и exit 1.

## Hysteria2: port hopping и obfs (серверные фичи vds)

Сервер vds может отдавать ссылки двух «усиленных» инстансов Hysteria2
(SPEC-20261112 WP-1), и vpnm рендерит их в клиентский конфиг:

| Параметр ссылки | Клиентский конфиг (hysteria2 YAML) |
|---|---|
| `mport=20000-20500` (или `a-b,c`) | `server: host:443,20000-20500` + `transport.udp.hopInterval: 30s` — клиент прыгает по портам |
| `obfs=salamander&obfs-password=...` | блок `obfs: {type: salamander, salamander: {password: ...}}` |

Валидация — на импорте (`vpn profile import` / `pull`): битый `mport`
(вне 1–65535, `start > end`), неизвестный тип `obfs` или `obfs` без
`obfs-password` дают `LinkError` с читаемым сообщением, а не «тихо без
обфускации». Переимпорт после изменения серверной ссылки — обычный
`vpn profile pull` (ссылка изменилась → запись обновляется).

## CDN-профиль без хардкода имени

`vpn scheme cdn` и строка `cdn` в `vpn monitor` не привязаны к имени `cdn-home`:
профиль выбирается из store по протоколу `xray-cdn-ws` с приоритетом
`cdn-home` → имя, содержащее `cdn` → первый ws-профиль. Нет ни одного —
команда завершается с подсказкой импортировать ws-ссылку (`vds gateway links`).

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

## `vpn guard` — разбор клиентских логов в реальном времени (2026-11-16)

Серверный `vds logs` видит эффекты на NIC и активные пробы; целый класс отказов
виден **только с клиента** — SNI-drop, DNS-подмена, whitelist-режим, «handshake
прошёл, данные умерли» на конкретном пути оператора. `vpn guard` — потоковый
конвейер `journalctl -f → parse → window → rules → correlate → finding`, который
за секунды отвечает «что сломалось, на каком слое, насколько уверены и что
делать». Спека: `vds/docs/team/specs/SPEC-20261116-vpn-guard.md`, ADR-014.

```bash
vpn guard rules                 # каталог G-01…G-12 + пороги + rules_hash/config_hash
vpn guard run --foreground      # демон (обычно через vpn-guard.service)
vpn guard run --once --json     # разобрать backfill и выйти (NDJSON findings)
vpn guard run --trace           # + trace.ndjson (≤10 МБ, 1 ч, только после redact)
vpn guard status                # running/state/счётчики/открытые инциденты (exit 0/1/2)
vpn guard findings --since 24h  # история findings (JSONL 0600)
vpn guard explain G-03          # окно, счётчики, FP-проверки, действие
vpn guard sources [--recheck]   # покрытие: права, слепые зоны, unparsed
vpn guard export --since 7d -o /tmp/guard-bundle
vpn guard selftest              # схемы/парсер/правила/пост-сканер (exit 0/1)
vpn guard notify-test           # тестовый баннер: канал доставки жив? (cooldown обойдён)
vpn guard sync --host root@asv1 # единственная сетевая команда (read-only ssh)
vpn guard install --apply       # user-юнит vpn-guard.service (+ --linger)
vpn guard remove --apply        # снять юнит (dry-run без --apply; --purge — с данными)
```

### Правила

| ID | Правило | Слой / стадия |
|----|---------|---------------|
| G-01 | `no_handshake_after_start` — app жив (banner/dial), handshake нет 25 с | `tunnel_down` / `pre_handshake` |
| G-02 | `reset_storm` — ≥3 reset/EOF за 60 с после handshake | `censorship_suspect` / `post_handshake` |
| G-03 | `post_handshake_drop` — ≥3 циклов handshake→обрыв (≤5 с) за 120 с | `censorship_suspect` / `data_phase` |
| G-04 | `udp_response_blackhole` — QUIC ok, ответов нет; AWG `tx↑/rx≈0` | `censorship_suspect` / `udp_response` |
| G-05 | `auth_failure` — `auth_fail` без `auth_ok` 5 мин | `config` / `pre_handshake` |
| G-06 | `dns_broken` — ≥2 DNS-ошибок за 60 с или SERVFAIL на ≥2 именах (подавляется G-07: нет route → `local_net_down`) | `dns` / `local` |
| G-07 | `local_network_down` — `network_unreachable`/нет route | `local_net` / `local` |
| G-08 | `tls_cert_error` — сертификат без успешного TLS 5 мин | `config` / `tls_stall` |
| G-09 | `endpoint_silent` — ≥5 pre-handshake сбоев на host:port за 10 мин; **только VPN-транспорты** (`vpnm-*`, `vpn-xray-split@*`, `vpn-awg-split@*`, `awg-quick@*`, `vpn-relay-*`) | `endpoint_unreachable` / `pre_handshake` |
| G-10 | `unit_flap` — ≥3 exit (non-zero)/start за 10 мин | `local_fault` / `local` |
| G-11 | `degraded_throughput` — ≥2 отчётов `vpn speed` < 2.0 Мбит/с **подряд** за 24 ч | `degraded` / `data_phase` |
| G-12 | `guard_selfdegraded` — источник lost/очереди/unparsed/часы (AWG off и suspend/skew не считаются потерей источника) | `observability` / `local` |

Пороги — только в `~/.config/vpn-manager/guard.json` (никаких пользовательских
regex); актуальные значения и детерминированные `rules_hash`/`config_hash`
печатает `vpn guard rules --json`. Корневая причина инцидента выбирается по
SPEC §9.2: `local_net > local_fault > config > dns > tunnel_down >
censorship_suspect > endpoint_unreachable > degraded > observability`.

Уточнения по полевому чекпоинту WP-G6 (2026-11-17):

- **G-09 — только для VPN-транспортов.** Вспомогательные сервисы
  (`cloudflared-proxy-dns`, `dnsmasq`, `zapret`, системные/сетевые юниты)
  тоже пишут `conn_reset`/`dial_timeout`, но их молчание — локальный контекст,
  а не «молчащий эндпоинт». Классификация явная
  (`rules.is_vpn_transport_unit`), неизвестный юнит транспортом не считается.
- **Дедуп внутри батча.** Ключ `rule|unit|endpoint|cause` строится по
  эндпоинту, который попадает в finding, а не по эндпоинту события-триггера;
  один физический отказ даёт один finding (cooldown взводится на первом).
- **Порядок findings.** `vpn guard findings` (и `--json`) отдаёт строгую
  хронологию по `window.last` (при равенстве — порядок вставки), а не порядок
  строк в файле.
- **G-06 ↔ G-07.** Если в окне или в том же батче есть
  `network_unreachable`/`no_route`, G-06 подавляется (`local_net_down`):
  приоритет у G-07. G-06, сработавший *до* потери маршрута, не отзывается —
  инцидент всё равно классифицируется как `local_net` (приоритет §9.2).
- **G-12 не считает «потерей источника» штатные и локальные состояния:**
  остановленный AWG (`mode.json`/последний lifecycle `vpn_down`) и период
  suspend/resume (скачок realtime↔monotonic фиксируется как `clock_skew`).
- **IPv6-ENETUNREACH — не падение сети.** На IPv4-only хосте (домашние сети
  РФ, AWG/Xray-сплит без v6) `network is unreachable` на адресат из
  `[2606:4700::…]` означает «нет v6-маршрута»: парсер такие строки не
  эмитит как `network_unreachable`/`no_route` (учёт в `ignored_infra`,
  AC-5 не раздувается), поэтому G-07 не спамит и не маскирует G-06.
  Источник шума дополнительно убран: `cloudflared-proxy-dns` получает только
  IPv4-литералы (`--upstream 1.1.1.1/1.0.0.1`, `--bootstrap 162.159.36.1/46.1`).

### Приватность и артефакты

- маскирование **до записи**: UUID, ключи/пароли/obfs/shortId, share-ссылки,
  URL с токенами, e-mail; текст строки не сохраняется — только счётчики,
  окна и `sha1`-хэши шаблонов;
- пост-сканер секретов проверяет finding/coverage/export; собственные
  `*_hash`/`*_sha256` и `sha256:<64 hex>` — не секреты (иначе экспорт был бы
  невозможен);
- `vpn guard export` по умолчанию заменяет хосты на `<host>`; `--keep-hosts` —
  осознанный opt-in; при найденном секрете (в т.ч. уже лежащем в
  `findings.jsonl`) печатаются **только пути полей** и команда завершается
  кодом 2;
- нотификации на десктоп — opt-in (`notify.desktop=true`, `min_severity=high`),
  `notify-send` вызывается списком аргументов (без shell), не чаще 12/час;
  без D-Bus finding не теряется — остаётся строка в журнале;
  `vpn guard notify-test` проверяет **фактическую доставку** тем же путём
  (opt-in, порог, наличие `notify-send`/D-Bus), печатает `OK` или причину
  отказа с подсказкой; cooldown 12/час обойдён намеренно — это ручная
  проверка канала, а не находка guard; в `findings.jsonl` ничего не пишется;
- счётчик `redaction_hits` и кольцо G-11 живут в `guard/state.json` и
  переживают рестарт/`kill -9`.

| Путь | Права | TTL/лимит |
|------|-------|-----------|
| `~/.local/share/vpn-manager/guard/findings.jsonl` | 0600 | 14 дней / 20 МБ |
| `~/.local/share/vpn-manager/guard/state.json` | 0600 | ≤64 КБ (счётчики, инциденты, кольцо G-11) |
| `~/.local/share/vpn-manager/guard/coverage.json` | 0600 | перезапись |
| `~/.local/share/vpn-manager/guard/trace.ndjson` | 0600 | opt-in, 1 ч / ≤10 МБ |
| `~/.config/vpn-manager/guard.json` | 0600 | пороги/флаги |
| `~/.local/share/vpn-manager/server-verdict.json` | 0600 | только allowlist серверных полей |

`vpn guard export` пишет каталог (`manifest.json` + `findings.ndjson` +
`coverage.json`) или один `*.json`-файл; если coverage ещё не собирался,
`manifest.coverage_included=false`, а в бандле нет `coverage.json` — честно,
вместо выдуманного «всё ok».

### Чего guard не делает (v1, read-only)

- не рестартит, не переключает, не «лечит» и не правит конфиги — только
  диагностирует (`action` в finding — текст для человека);
- не делает сетевых проб — активная проверка остаётся за `vpn monitor`/`vpn probe`;
  сеть есть только у явной команды `vpn guard sync`;
- не требует root: системный журнал — best-effort (`coverage.status=denied`),
  AWG читается из world-readable `/sys/class/net/<iface>/statistics/*`;
- не отправляет логи и телеметрию никуда; `vpn guard install` не ставится
  автоматически при `vpn install` (политика чистого старта).

JSON-контракты — `guard.event/1`, `guard.finding/1`, `guard.incident/1`,
`guard.status/1`, `guard.coverage/1`, `guard.server_verdict/1`, `guard.config/1`
(`vpnm/guard/schemas/`, package-data); `vpn guard selftest` проверяет, что
`pip install` довёз схемы, парсер, правила и пост-сканер.

## `vpn guard failover` — авто-переключение каналов (2026-11-18, WP-FAILOVER)

Отдельный **исполняющий** агент (SPEC-20261118-vpn-failover, ADR-015): когда
прямой 443/tcp закрыт серверным локом, переключает на резерв без участия
человека. `vpn-guard.service` остаётся read-only — это другой процесс и другой
юнит (`vpn-failover.service` + `vpn-failover.path`, префикс `vpn-`, не `vpnm-`).

- **Триггеры узкие:** `G-01` (high), `G-09` (≥ medium) и **производное**
  `expected_lockdown` (свежий `server-verdict.json` ≤10 мин + `lockdown.active`
  + активный профиль из `trigger_profiles`). `G-02` и ТСПУ-подписи **не**
  триггерят.
- **Шаг 0 — `local_net_check`:** нет default route / связи вне туннеля →
  `suppressed_local_net_down`, ни одного переключения.
- **Лестница:** `relay → asv2-hy2 → asv2-awg → asv2-cdn`; на каждом кандидате
  `pre_flight` (≤2 с) → switch → `post_flight` (≤4 с). Провал `pre_flight` =
  `dead_entry` (switch не вызывается), провал `post_flight` = `dead_exit` +
  cooldown кандидату 600 с.
- **Ручная остановка важнее:** `vpn down`/`vpn reset` ставят маркер
  (`activity_scope`) и глушат агент на 5 мин; отдельного `desired_state` нет.
- **Total blackout:** ровно один проход, backoff `120 → 300 → 900` с,
  `urgency=critical` и запись в очередь; при `kill_switch=false` —
  возврат на штатный маршрут (`vpn down`).
- **Никакого авто-возврата:** событие сервера `lockdown_off` только снимает
  `pin.lockdown_active` и уведомляет — человек сам решает, возвращаться ли на
  `vpn switch asv2-reality`.

```bash
vpn guard failover status            # enabled/pin/cooldown/backoff/лок + досылка очереди
vpn guard failover run --once        # один проход (вызывает юнит)
vpn guard failover reset             # снять pin/cooldown/backoff
vpn guard failover enable --apply    # config.failover.enabled=true + юниты
vpn guard failover disable --apply   # снять юниты (guard продолжает работать)
vpn guard failover test-notify       # проверить desktop/Telegram
```

Конфиг (`~/.config/vpn-manager/guard.json`), по умолчанию **выключено**:

```json
{
  "failover": {
    "enabled": false,
    "trigger_profiles": ["asv2-reality"],
    "ladder": ["relay", "asv2-hy2", "asv2-awg", "asv2-cdn"],
    "cooldown_sec": 900, "max_switches_per_hour": 3,
    "candidate_cooldown_sec": 600, "backoff_sec": [120, 300, 900],
    "pre_flight_timeout_sec": 2.0, "post_flight_timeout_sec": 4.0,
    "confirm_lockdown_via_sync": true, "kill_switch": false
  },
  "notify": {
    "failover_desktop": false,
    "telegram": {"enabled": false,
                 "bot_token_file": "~/.config/vpn-manager/telegram.token",
                 "chat_id": "", "timeout_sec": 4.0}
  }
}
```

Desktop-баннеры — **отдельный opt-in** `notify.failover_desktop` (не наследует
`notify.desktop`). Telegram-токен — только из файла 0600 (шире — warning, но
чтение продолжается); токен попадает в `<redacted:tg_token>` и не утекает в
исключения (`URLError` содержит URL). Недоставленное уведомление уходит в
`pending_alerts.jsonl` (FIFO ≤50/≤1 МБ) и досылается после успешного
`post_flight`, в `vpn guard sync` и `vpn guard failover status`.

Артефакты (все 0600, каталог 0700, через `store.py` со схемами
`guard.failover_state/1`, `guard.failover_history/1`,
`guard.pending_alerts/1`):

| Путь | Лимит |
|------|-------|
| `guard/failover_state.json` | ≤16 КБ: pin, cooldown-метки, backoff |
| `guard/failover_history.jsonl` | 5 × 4 МБ / 14 дней (как findings) |
| `guard/pending_alerts.jsonl` | ≤50 записей / ≤1 МБ, FIFO |

## `vpn scout` — активная разведка маршрутов (2026-11-17, WP-S1…S4)

Демон `vpn-guard.service` принципиально не открывает сокетов: он видит только
то, что уже лежит на диске. Вопрос «какой маршрут сейчас реально жив, если
сервер на локдауне, а TSPU фильтрует TLS» требует активных проб — их делает
только явная команда `vpn scout`. Она read-only: `findings.jsonl`,
`guard.json` и `server-verdict.json` не пишутся (проверяется тестом по
sha256 + `st_mtime_ns`).

```bash
vpn scout                  # каскад (по умолчанию): RECOMMENDED / FALLBACK / «отсеяны»
vpn scout --json           # scout.report/1 (см. ниже) — для скриптов
vpn scout --probe-all      # полный параллельный опрос всех узлов (осознанно)
vpn scout --sync           # сначала vpn guard sync (единственный ssh), затем разведка
vpn scout --sync --host root@asv1
vpn up --scout             # разведка (каскад) + поднять рекомендованный маршрут
vpn up --scout --dry-run   # только напечатать, что было бы запущено
```

Exit-коды: `0` — рекомендация есть; `1` — нет ни одного маршрута с score ≥50
(отчёт всё равно печатается); `2` — только ошибка разбора аргументов (typer),
сама команда возвращает 0 или 1.

### Stealth Cascade (2026-11-18) — не светиться перед ТСПУ и не взводить probe-watch

Полный параллельный залп — это ровно тот трафик, который **демаскирует**
клиента (много SYN/TLS с разных SNI к разным узлам за доли секунды) и может
**самовзвести** probe-watch на сервере (пачка проб в locked-порт = повод для
lockdown). Поэтому по умолчанию `vpn scout` работает так:

1. **Сортировка до любых проб** — локальный `pre_score` (в сеть не ходит):
   базовая оценка матрицы + lock-443 (свежий `server-verdict.json`, ≤10 мин:
   direct xray-443 → 0, relay/AWG/Hy2 +10) + недавний G-04 на endpoint
   (−60, UDP-протоколы) + недавний G-01/G-03 для профиля (−12). Порядок
   детерминирован (score ↓, затем стабильный ключ кандидата).
2. **Только топ-1.** Проверяется один узел. Если статус хороший (`ok` /
   `reachable` / `local_route`) — **немедленная остановка**: к остальным
   узлам не уходит ни одного пакета (ни DNS к их адресам, ни сокета).
3. **Провал → джиттер 150–400 мс** (случайный, `time.sleep(random.uniform(
   0.15, 0.4))`) и следующий по порядку. Джиттер только *между* попытками:
   перед первой и после последней паузы нет. Фиксированный ритм серии
   неудачных коннектов — тоже машинная подпись.
4. **Бюджет каскада** — `CASCADE_BUDGET_MS = 6000`, на пробу — существующие
   1500 мс. Бюджет исчерпан → честный статус `budget` у той пробы, которая не
   стартовала (не молчание).
5. **FALLBACK не проверяется**: он выбирается из непроверенных узлов по тому же
   `pre_score` и помечается в выводе `(не проверялся)` — это страховка «если
   будете переключаться, начните отсюда», а не результат пробы.

Параллельный залп остался только по явному `--probe-all` (2500 мс, 8 воркеров);
это осознанный выбор владельца «хочу полную картину и принимаю трафик».

Следствие для уведомлений: если активный профиль не попал в проверенные узлы,
`guard-notify` о его блокировке в этом прогоне не сработает — это цена
«0 пакетов к остальным» (в `--probe-all` проверяются все, и уведомление
работает как раньше).

### Откуда берутся кандидаты (не выдуманы)

| Источник | Как читается | Кандидат |
|----------|--------------|----------|
| `~/.config/vpn-manager/profiles.json` | `vpnm.profiles.list_profiles` + `vpnm.links.parse_link` | `xray-reality` / `xray-xhttp` / `xray-cdn` (ws) / `hy2` |
| `~/.awg/*.conf` | строка `Endpoint = host:port` (ключи/PSK не читаются) | `awg`; маршрут — по endpoint: relay-хост → `relay`, иначе `direct` |
| `/etc/amnezia/amneziawg/*.conf` | тот же `Endpoint`, только если каталог читается без root (иначе молча пропускается) | `awg` (как правило `direct`, профиль `system:<имя>`) |
| `~/.config/vpn-manager/state.json` | `runners.get_active()` | активный профиль (для проверки «текущий канал заблокирован») |

Уточнения, которые важно знать:

- **SNI** берётся из той же параметрики, что рендерит `xclient`: `sni` из
  share-ссылки, иначе host (для ws/CDN — `sni`, затем `host`-заголовок);
- **Hy2 port-hop** — параметр `mport` ссылки (`20000-20500`), он же попадает в
  multi-port адрес сервера в сгенерированном конфиге; в отчёте это
  `port_hop_range`;
- **маршрут AWG определяется по Endpoint, а не по имени файла**:
  `android.conf`, `windows.conf`, `relay.conf` — это один и тот же физический
  туннель, если их endpoint совпадает. Relay-хосты берутся из факта: `Endpoint`
  из `relay.conf` (если есть) + канонический `relay.asvpersonal.ru` + опциональный
  `relay_endpoint`/`relay_host` в `settings.json`; сравнение хоста без учёта
  регистра и точки в конце. Конфиг с relay-endpoint получает `route=relay`,
  любой другой — `direct`. Поэтому клиентские копии на один endpoint
  схлопываются в **один** кандидат `relay/awg` (при совпадении имени
  предпочитается инстанс `relay`), а не дают фальшивую пару
  «direct/awg primary + relay/awg fallback»;
- **маршрут store-профилей** по-прежнему по имени: `relay` только для профиля
  `relay-reality` (socks-фолбэк `vpn start relay --proto reality`), остальные
  профили — `direct`. Секреты (uuid/пароль/obfs/приватный ключ) в Candidate и в
  отчёт не попадают;
- дубли между store-профилями и AWG-конфигами (одинаковый route/proto/host:port)
  схлопываются, факт дубля печатается в `context.notes`; копии одного и того же
  AWG-туннеля (`android`/`windows`/`relay` на один endpoint) схлопываются молча —
  это не дубль маршрута, а одно устройство под разными именами.

Вердикт сервера учитывается только **свежий (≤10 мин)** — строже, чем 65 мин у
самого guard: решение о маршруте по часовому `lockdown` было бы догадкой. Если
файл устарел, `context.notes` прямо говорит «lock не учитывается; `vpn guard
sync`». Дополнительно читаются недавние (30 мин) findings G-01/G-03/G-04.

### Пробы (WP-S2 + Stealth Cascade)

| Проба | Что делает | Статусы |
|-------|------------|----------|
| TCP/TLS | TCP connect → TLS 1.3 handshake с настоящим SNI → ровно `HEAD / HTTP/1.1\r\nHost: <SNI>\r\nUser-Agent: Mozilla/5.0\r\nConnection: close\r\n\r\n` → чтение первых ≤64 байт ответа | `ok` (TLS+HTTP: ответ начинается с `HTTP/1.`/`HTTP/2`), `tls_only` (handshake прошёл, HTTP-ответа нет: timeout/EOF/мусор — **не** hard fail), `timeout` (нет SYN-ACK), `refused` (RST на connect), `TSPU_TLS_FILTER` (RST **или** зависший ClientHello после connect), `tls_error`, `dns_error`, `no_route` |
| Hy2 и AWG (UDP) | **0 байт в эфир**: никаких QUIC Initial и `os.urandom`-датаграмм. Только `socket.connect((host,port))` — UDP-`connect` делает route lookup ядра и не отправляет ни байта (IPv4-first перебор адресов сохранён) + история G-04 (endpoint ≤30 мин) и G-01/G-03 (профиль ≤30 мин), которая решает без сокета | `local_route` (локальная маршрутизируемость), `recent_incident` (G-01/G-03 по профилю — не hard fail), `RECENT_UDP_BLACKHOLE` (G-04 ≤30 мин), `no_route` (ENETUNREACH на всех адресах), `dns_error` |
| lock-443 | `lockdown.active` в свежем вердикте | `SERVER_LOCKDOWN` для direct xray-443 **без сетевого коннекта** (не нагружаем только что закрытый порт) |

Почему UDP без пакетов: несжатый QUIC Initial или 32-байтная UDP-вспышка в
сторону фильтрующего middlebox — это ровно тот нежелательный трафик, который
может **самовзвести** probe-watch на другой стороне. Соединённый UDP-сокет без
`send` честно отвечает на единственный вопрос, который эта проба может
доказать — «есть ли в локальной таблице маршрутов путь к endpoint», — и
остаётся невидимым в эфире. `silence` (старый статус Hy2) в скоринге сохранён
для обратной совместимости, но в новом потоке не возникает.

**Двойной стек, IPv4-first (2026-11-17):** TCP/TLS и UDP-проверка перебирают
все адреса из `getaddrinfo` — IPv4 первыми, IPv6 последними (детерминированно),
а `ENETUNREACH`/`EHOSTUNREACH` на конкретном адресе не отсеивает кандидата (та
же грабля, что в guard, `904212f`); `no_route`/`error` — только когда исчерпаны
все адреса, в `detail` остаётся последняя ошибка.

Бюджеты: каскад — 6000 мс на весь прогон, 1500 мс на пробу,
последовательный; `--probe-all` — `ThreadPoolExecutor(max_workers=8)`, 2500 мс.
Таймаут каждой пробы урезается до остатка бюджета; не успевшие получают
`timeout`/`budget`, а не пропадают молча (тест: 4 «зависших» сокета по 1.5 с →
весь параллельный прогон < 2.5 с).

### Скоринг и вывод (WP-S3)

| База | Score |
|------|-------|
| direct + awg | 90 |
| direct + reality | 88 |
| direct + hy2 | 85 |
| relay + awg | 82 |
| relay + reality / hy2 | 80 |
| xhttp (любой маршрут) | 75 |
| cdn (любой маршрут) | 65 |

Поправки: **lock-443** — direct xray-443 → 0, relay/AWG/Hy2 +10; **TSPU TLS
filter** — direct Reality → 0, relay +15; **недавний G-04 на UDP-эндпоинте**
−60; **недавний G-01/G-03 по профилю** −12; **silence** −6; **`local_route`**
−6 (локальная маршрутизируемость, 0 байт в эфир — путь есть, транспорт не
доказан); **`tls_only`** −4 (handshake доказан, HTTP — нет); **RTT** −1 за
каждые полные 25 мс, но не больше −6. Тот же `pre_score` (без проб) задаёт
порядок каскада — см. «Stealth Cascade».

**Доказанный `ok` выше недоказанного `silence` (2026-11-18).** Живой прогон
показал перекос: `direct/xray-reality` со `status=ok` и RTT 218 мс набирал
88−8=80, а `direct/hy2` со `silence` (UDP-ответа нет, RTT неизмерим → штраф 0)
— 85, и выигрывал. Двусторонний доказанный канал проигрывал недоказанному.
Поэтому: `silence` (UDP без ответа) — это −6 с причиной «UDP без ответа
(silence): −6 за недоказанную проходимость»; применяется ко всем протоколам
со статусом silence (сейчас Hy2). AWG тоже не доказывает проходимость: его
UDP-проба идёт как `local_route` (connect без единого байта в эфир) и получает
те же −6 — молчание на неаутентифицированные пакеты ничего не подтверждает,
а `reachable` в новом потоке (Stealth Cascade) не возникает вовсе. Подпись
`silence` у Hy2 —
**не приговор**: сервер штатно не отвечает на неаутентифицированный Initial,
поэтому silence — неопределённость, а не доказательство блокировки; Hy2
остаётся рабочей ставкой, когда Reality отфильтрован/залочен или AWG поймал
G-04. Параллельно **потолок RTT-штрафа** `RTT_PENALTY_CAP = 6`: штраф =
`min(6, rtt_ms // 25)`, в причине при срезе добавляется `(cap −6)`. Медленный,
но доказанно работающий канал не должен терять больше 6 очков — иначе 400 мс
RTT (−16) стирали бы разницу между транспортами. Итог сценария владельца:
Reality ok @218 → 88−6=82 **выше** Hy2 silence → 85−6=79, рекомендация —
Reality. Человекочитаемый вывод дополнительно предупреждает, если
server-verdict **свежий** и `threat_level` = `elevated`/`high`:
`[info] threat=<уровень> — 443/tcp может уйти в lock-443; держите relay/AWG
под рукой` (только информирует, победителя не меняет). В JSON поле
`threat_level` продублировано на верхнем уровне `scout.report/1` — аддитивно,
прежние ключи не менялись.

Вывод: `RECOMMENDED` — лучший score ≥50; `FALLBACK` — лучший кандидат на
**другом маршруте**; если другого маршрута нет — лучший с другим протоколом
(в каскаде — только из непроверенных узлов, по `pre_score`); остальные —
«отсеяны» с человекочитаемыми причинами (`probe: TSPU_TLS_FILTER — ...`,
`lock-443: ...`, `score N < 50` и т.д.). Если рекомендации нет,
`vpn up --scout` откажется запускать что-либо (exit 1) и предложит полный отчёт.

JSON-контракт `scout.report/1`:

```json
{
  "schema": "scout.report/1",
  "created": "2026-11-17T12:00:00Z",
  "mode": "cascade",
  "probed": ["laptop"],
  "context": {"verdict": {}, "verdict_fresh": true, "verdict_age_s": 42,
              "threat_level": "quiet", "server_verdict": "quiet",
              "lockdown_443": false, "findings_recent": 1,
              "g04_endpoints": [], "incident_profiles": [],
              "notes": [], "active_profile": "phone"},
  "threat_level": "quiet",
  "recommended": {"route": "direct", "proto": "awg", "profile": "laptop",
                  "host": "203.0.113.10", "port": 51820, "sni": null,
                  "port_hop_range": null,
                  "probe": {"status": "local_route", "rtt_ms": null,
                            "detail": "локальная маршрутизируемость "
                                      "(0 байт в эфир)"},
                  "score": 84, "status": "recommended", "reasons": []},
  "fallback": {"…": "непроверенный узел, probe.status = not_probed"},
  "rejected": [],
  "candidates": [],
  "notify": {"attempted": false}
}
```

`mode` (`cascade`/`all`) и `probed` (список опрошенных профилей) — аддитивные
поля `scout.report/1`; старые ключи не менялись. В текстовом выводе первая
строка прямо говорит режим: `[scout] режим: каскад (проверен 1 узел)` или
`[scout] режим: полный опрос (N узлов)`; непроверенный FALLBACK помечен
`(не проверялся)`.

Если **текущий** активный канал сам заблокирован (`SERVER_LOCKDOWN`,
`TSPU_TLS_FILTER`, `refused`, `timeout`, `udp_refused`, `no_route`, `dns_error`,
`tls_error`, `error`, `budget`), scout переиспользует существующий
`guard-notify` (его opt-in и rate limit не меняются): баннер содержит
следующую команду — `vpn switch <profile>`, для AWG — `vpn start` /
`vpn start relay`. Ничего не отправляется, если `notify.desktop=false`.

### Чего scout не делает

- не пишет `findings.jsonl`, `guard.json`, `server-verdict.json` (только
  `--sync` — и то штатным `vpn guard sync`);
- не требует root: непривилегированные сокеты, чтение `~/.awg/*.conf`;
- не запускает и не останавливает туннели сам (это делает `vpn up`/`vpn start`
  по явной команде или `vpn up --scout`);
- не делает фоновых сетевых вызовов: демон остаётся без сети.

## Мониторинг каналов (`vpn monitor`)

Матрица доступности каналов (rich-таблица; `--json` — те же данные с сырыми
кодами статусов для скриптов):

| Канал | Что проверяет | Статусы (коды) |
|-------|---------------|----------------|
| `vpnm` | активный профиль + egress-probe через SOCKS | ok / warn (профили есть, ни один не включён) / fail / not_installed |
| `cdn` | CDN-профиль + egress через Cloudflare | ok / warn (профиль остановлен) / fail / not_installed |
| `zerotier` | сервис, `zerotier-cli status`, joined-сети | ok / warn / fail / not_installed (в архиве) |
| `amneziawg` | артефакты (бинарь/конфиги/интерфейс) — слот | not_installed / ok (iface up) / warn |
| `xray-split` | unit/iface/config split-scheme + прямая проба 204 через `xray0` | ok / warn (конфиги есть, юнит остановлен) / fail / not_installed |
| `zapret` | канал 0: сервис активен → **прямой** canary на youtube generate_204 | ok / warn / fail / not_installed |
| `dns-doh` | канал 0.5: сервис `cloudflared-proxy-dns` активен + `dig @127.0.0.1 -p 5053 www.youtube.com +short` непуст | ok / warn / fail / not_installed |

В конце — `healthy channels: N/7` (exit 1, если ни один канал не ok).

### Как читать статусы

В таблице показывается смысл, а не внутренний код — `warn` и `not_installed`
раньше читались как «сломано», хотя означают «есть, но выключено» и «ещё не
настраивали»:

| В таблице | Код | Смысл и что делать |
|-----------|-----|--------------------|
| ✔ работает | `ok` | канал включён и проверка прошла |
| ○ готов, не включён | `warn` | всё установлено/настроено, но не активно: обычно `vpn up <profile>`, `vpn scheme awg\|xray-split` или `systemctl start <unit>` |
| — не настроен | `not_installed` | канала просто нет — нейтрально, не ошибка |
| ✖ ошибка | `fail` | включён, но проверка не прошла — смотреть колонку «детали» |

Сырые коды остаются в `vpn monitor --json` (для скриптов и мониторинга).

Строка `zapret`: «— не настроен» — нейтрально (канал 0 не ставили);
«✔ работает» — direct DPI-bypass жив; «✖ ошибка» — сервис активен, но canary
не прошла (стратегия сломалась — blockcheck заново); «○ готов, не включён» —
установлен, но не запущен.
Строка `dns-doh`: «— не настроен» — нейтрально (локальный DoH не ставили);
«✔ работает» — DoH-резолвер жив (локальный cloudflared); «✖ ошибка» — сервис
активен, но dig пуст (upstream недоступен/перехвачен), либо сервис установлен,
но не запущен (системный DNS молча деградирует до plaintext-перехвата).

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

## Канал DNS (DoH)

Провайдер перехватывает plaintext-DNS: youtube/googlevideo отдают NXDOMAIN
через любой обычный резолвер, и ни DPI-desync, ни VPN-профиль это не чинят
(запрос резолвится ещё до туннеля). Локальный DoH-резолвер закрывает
именно этот вектор.

**Установка** (на этой машине, интерактивно попросит sudo):

```bash
bash scripts/13_dns-doh.sh
```

Скрипт: скачивает cloudflared **2025.11.1** (последняя версия с
`proxy-dns`; в 2026.10+ подкоманду убрали) через зеркала
ghproxy.net → gh-proxy.com → direct, верифицирует `--version`, ставит
бинарь в `/usr/local/bin/cloudflared-proxy-dns-bin`, systemd-юнит
`cloudflared-proxy-dns.service` (порт 5053, upstream
`https://cloudflare-dns.com/dns-query`, `Restart=on-failure`) и override
`/etc/systemd/resolved.conf.d/doh-local.conf` (`DNS=127.0.0.1:5053`),
затем `daemon-reload`, `enable --now` и `restart systemd-resolved`.
Идемпотентен.

**Верификация:** `dig @127.0.0.1 -p 5053 www.youtube.com +short` — непустой
вывод. Мониторинг: строка `dns-doh` в `vpn monitor`.

**Откат:**

```bash
sudo rm /etc/systemd/resolved.conf.d/doh-local.conf
sudo systemctl disable --now cloudflared-proxy-dns.service
sudo systemctl restart systemd-resolved
```

## Канал 1: AWG-split через 14_awg-split.sh

[AmneziaWG](https://github.com/amnezia-vpn) (AWG) — WireGuard с обфускацией
(jc/jmin/jmax/s1/s2/h1-h4): пакет выглядит не как WG-handshake и не
режется DPI. Канал 1 даёт split-туннель: через AWG идут только
google/telegram-диапазоны, остальной трафик — напрямую (под desync zapret).

**Установка** (интерактивно: SERVER_IP, SSH USER, имя конфига):

```bash
bash scripts/14_awg-split.sh
```

Скрипт: ставит `amneziawg-tools` из `ppa:amnezia/ppa` (клиенту dkms/модуль
ядра не нужен — только `awg`/`awg-quick`), забирает конфиг с сервера
`scp ${USER}@${SERVER_IP}:/opt/my-vds/gateway/clients/amneziawg/<CONF>.conf
~/.awg/<CONF>.conf` (0600; если сервер ещё не развёрнут — печатает ссылку
на `scripts/11_vds-deploy.sh`), ставит templated-юнит
`vpn-awg-split@.service` (инстанс = имя конфига,
`ExecStart=/usr/bin/awg-quick up /etc/amnezia/amneziawg/%i.conf`,
`Type=oneshot`, `RemainAfterExit=yes`, `After=network-online.target`) и
включает `vpn-awg-split@<CONF>`.

**AllowedIPs:** серверный конфиг может быть full-tunnel
(`AllowedIPs = 0.0.0.0/0`). Скрипт это замечает, печатает warning и
предлагает заменить на split-список из `STRATEGY_IPS` (в шапке скрипта —
редактируйте: диапазоны google + telegram). Замена через `sed` по
`AllowedIPs` в конфиге — только с подтверждением.

**Верификация:** `awg show` (handshake) + `ping -c1 -W2` первого IP из
AllowedIPs. Мониторинг: строка `amneziawg` в `vpn monitor` (ok при
поднятом интерфейсе `awg*`/`amz*`/`amn*`).

**Управление:**

```bash
sudo systemctl start/stop vpn-awg-split@laptop
awg show
```

## Канал 1+: доменный split через 15_domain-split.sh

Над AWG-туннелем (канал 1) работает **доменный split**: через AWG идёт
трафик только на домены из списка, всё остальное — напрямую. Механика —
классический `dnsmasq + nft + fwmark` паттерн:

```
systemd-resolved → dnsmasq (127.0.0.1:53) → cloudflared DoH (127.0.0.1:5053)
                                        ↘ домены из списка → nft set
inet/awg_split/routes → mark 0x21000 → table 21000 → default dev awg0-split
```

- **DoH**: переиспользуется установленный `cloudflared-proxy-dns`
  (порт 5053, см. канал DNS 0.5); при отсутствии — минимальная установка
  cloudflared 2025.11.1 изнутри скрипта.
- **dnsmasq** (`/etc/dnsmasq.d/awg-split.conf`): `server=127.0.0.1#5053`
  (все запросы — через DoH), `no-resolv`, `listen-address=127.0.0.1`,
  `port=53`, `bind-interfaces`, и по `nftset=/<домен>/4 inet#awg_split#routes`
  на каждый домен. Генерируется скриптом из файла списка — **не править
  руками**. Два найденных на живой машине факта: (1) семейство в `nftset`
  отделяется **пробелом** (`4 inet#...`), не двоеточием — с `4:` dnsmasq
  строит битую nft-команду и молча не наполняет set; (2) Ubuntu-jammy
  сборка dnsmasq собрана **без HAVE_NFTSET** — скрипт пересобирает dnsmasq
  из исходников с `COPTS="-DHAVE_NFTSET -DHAVE_DNSSEC"` (DNSSEC обязателен:
  init-скрипт Ubuntu всегда передаёт trust-anchor) и заменяет
  `/usr/sbin/dnsmasq` (дистро-копия в `dnsmasq.dist`).
- **systemd-resolved** (`/etc/systemd/resolved.conf.d/doh-local.conf`
  меняется на `DNS=127.0.0.1`, `Domains=~.`): **все** домены системы
  резолвятся через dnsmasq→DoH — это лечит NXDOMAIN-перехват провайдера
  глобально, а не только для youtube.
- **nft** (`table inet awg_split`): set `routes` (ipv4_addr, flags
  interval, auto-merge), chain `out` (hook output, priority mangle):
  `meta mark 0xca6c return` (не трогаем UDP-пакеты самого AWG — иначе
  endpoint-трафик зациклится в туннель), затем
  `ip daddr @routes mark set 0x21000`. Плюс `ip rule pref 21000 fwmark
  0x21000 table 21000` и `table 21000 default dev awg0-split`.

### Список доменов

`/opt/awg-split/domains.txt` — один домен на строку, комментарии `#`.
dnsmasq-суффикс-матчинг: запись `google.com` покрывает все поддомены.
Если сервис отдаёт ссылки на **другой TLD** (пример: `youtu.be` — `.be`),
его нужно вписать отдельно: TLD-зоны `com/org/net/io/dev/ai` такие домены
не покрывают. После правки — `sudo bash scripts/15_domain-split.sh`
(merge дефолтов идемпотентен, существующий список не перетирается).
Дефолт при установке:

- **YouTube**: `youtube.com`, **`youtu.be`** (короткие ссылки — домен `.be`,
  TLD-зона `be` в split не входит; инцидент 2026-11-13:
  `https://youtu.be/...` уходил напрямую и не открывался),
  `googlevideo.com`, `ytimg.com`, `ggpht.com`, `youtubei.googleapis.com`
- **Telegram**: `telegram.org`, `t.me`, `telesco.pe`, `cdn-telegram.org`,
  `teleg.ru`
- **Google-кластер** (поиск, Gemini, Meet, аккаунты, reCAPTCHA):
  `google.com`, `googleapis.com`, `gstatic.com`, `googleusercontent.com`,
  `gvt1.com`, `gvt2.com`, `recaptcha.net`
- примеры заблокированных западных: `instagram.com`, `facebook.com`,
  `x.com`, `openai.com` — **дополни своим списком**.

После правки файла — перезапуск `sudo bash scripts/15_domain-split.sh`
(перегенерит dnsmasq-конфиг; сам файл не перетирается, новые дефолты
добавляются merge'ом).

### Совместимость с awg-quick (тонкое место)

Скрипт приводит конфиг `~/awg0-split.conf` к `Table = off` (awg-quick не
создаёт своих маршрутов — маршрутизацией ведёт nft/fwmark) и
`AllowedIPs = 0.0.0.0/0, ::/0` (cryptokey-routing принимает всё). При
`Table = off` awg-quick по-прежнему ставит fwmark `0xca6c` на свой
UDP-сокет и добавляет `ip rule` fwmark-исключение для endpoint —
правило `meta mark 0xca6c return` в nft — вторая линия защиты от зацикливания.
Оба факта проверены на живой машине; при несоответствии скрипт пишет warning.

### Управление и демонтаж

```bash
sudo bash scripts/15_domain-split.sh           # установка / переустановка
sudo bash scripts/15_domain-split.sh --status  # текущее состояние
sudo bash scripts/15_domain-split.sh --off     # полный демонтаж
```

`--off` удаляет dnsmasq-конфиг, nft-таблицу, ip rule/route и возвращает
resolved на cloudflared DoH напрямую (`DNS=127.0.0.1:5053`). AWG-конфиг
возвращается к split-AllowedIPs вручную из `*.bak.*` при желании.

**Верификация:** `dig @127.0.0.1 www.youtube.com +short` непустой,
`sudo nft list set inet awg_split routes` содержит резолвнутые IP,
`curl --noproxy '*' -o /dev/null -w '%{http_code}'
https://www.youtube.com/generate_204` → 204, github → 200 (напрямую).
Мониторинг: строка `amneziawg` в `vpn monitor` — `ok (domain-split)` при
поднятом интерфейсе и установленном dnsmasq-конфиге.

## Зависимости

`typer`, `questionary` (TUI), `curl` (probe DNS/external IP), `ssh`,
`systemd` (user units), `gsettings` (GNOME, только для sysproxy).

Пиннутые бинари в `~/.local/lib/vpn/` (ставит `vpn` сам, `vpnm/bins.py`):
**Xray-core 26.1.23** — один и тот же для socks-профилей (`vpn up`) и
tun-split (`vpn start xhttp`), — и **hysteria v2.13.0**. Идемпотентность по
версии: бинарь 25.1.1 будет переустановлен.

## Разработка

Тесты: `python3 -m pytest` из `scripts/vpn-manager/` (моки subprocess, без сети).
Секреты (share-ссылки) не логируются; store и конфиги всегда 0600.

> **Тесты не ходят в систему.** Autouse-предохранитель в `tests/conftest.py`
> (`tests/sys_guard.py`) оборачивает `subprocess.run`/`Popen` и роняет тест
> на любой системно-мутирующей команде: `systemctl`/`awg`/`nft`/`ip`/
> `resolvectl`/`dnsmasq`/`cloudflared-proxy-dns`, запуске
> `17_xray-split.sh`, а также `sudo rm|mv|cp|install|chmod|…`. Причина —
> инцидент 2026-11-18: прогон `pytest` реально выполнил
> `sudo bash 17_xray-split.sh --off` + `systemctl stop vpn-awg-split@relay`
> и **снёс живой туннель**. Реальные system-команды в тестах запрещены;
> осознанный «живой» прогон — только явным `VPNM_TEST_ALLOW_SYSTEM=1`.

### Синхронизация repo ↔ установленная команда
После правок кода модуля переустанавливайте пакет: `pipx install --force scripts/vpn-manager` — установленный `vpn` не подхватывает изменения автоматически (не editable-инсталляция).


## Канал 1б: xray-split (tun, Reality/XHTTP, SPEC-20261114)

Системный зонный split на xray-транспорте (Reality **или** XHTTP) — та же
механика, что у AWG (dnsmasq → nft set → fwmark → table → dev), но
`dev = xray0`: tun-инбаунд xray-клиента (v26.1.23+; сервер asv1 тоже обновлён
до 26.1.23 — ADR-010; с GR-25 пин `vpnm` унифицирован: и socks-профили, и
split-юнит идут на **26.1.23** — `vpnm/bins.py: XRAY_VERSION`, у split-юнита
свой бинарь в `/usr/local/lib/vpn-xray-split`). Один пин нужен потому, что
`xPaddingBytes`/`xmux` в `xhttpSettings` понимает только 26.x.

```bash
vpn start                          # по умолчанию: AWG + доменный split
vpn start relay                    # RU-релей (см. «Канал 1в» ниже)
vpn start reality                  # Reality-split
vpn start xhttp                    # XHTTP-split
vpn scheme xray-split <profile>    # Reality/XHTTP; <profile> — имя из
                                   # 'vpn profile list' (без имени — первый)
vpn scheme xhttp-split [профиль]   # алиас: автовыбор только xray-xhttp
vpn scheme awg                     # назад: снимает xsplit (взаимно исключительны)
vpn reset                          # снять всё (общий выключатель; не preset)
sudo bash scripts/17_xray-split.sh --status | --off
```

- конфиг: `/etc/vpn-manager/xray-split-<профиль>.json` (0600) генерирует vpnm
  из share-link (Reality: flow=xtls-rprx-vision; XHTTP: path + `mode: auto`,
  `xPaddingBytes: 100-1000`, `xmux`-ротация сокетов — GR-25); секрет (UUID)
  не попадает в argv/логи (tmp 0600 + sudo install);
  конфиг остаётся после `--off`/`vpn reset` — это «резерв одной командой»
  (каталог 0700 root, поэтому статус в мониторе читается через `sudo -n ls`);
- защита от петли: `sockopt.mark=0xca6c` на outbound — nft возвращает этот
  mark, иначе домен сервера в зоне `com` уводил бы xray в собственный tun;
- сокеты XHTTP: 2–4 параллельных с ротацией каждые 3–5 минут (`xmux`, GR-25) —
  это штатное поведение, а не обрыв; `ss -tn dst <сервер>:8443` показывает
  смену исходящих портов;
- split-правила: `15_domain-split.sh --dev xray0 --mark 0x23000 --table 23000
  --nft-table xray_split`; домены/исключения — те же `domains.txt`/`exclude.txt`;
- kill-switch: `ExecStopPost=vds-xsplit-cleanup` — остановка/падение юнита
  снимает ip rule/table, интернета-дыры не будет (трафик уходит напрямую);
- самохил: `ExecStartPost=vds-xsplit-cleanup --apply` — после рестартов юнита
  правила восстанавливаются сами;
- DoH: схема сама включает цепочку (`_ensure_doh`) и ждёт готовности резолвера
  перед стартом split — иначе dnsmasq уходит в мёртвый 5053;
- монитор: строка `xray-split` в `vpn monitor` (unit/iface/mark/table + прямая
  проба 204 через tun);
- **браузер после включения**: Chrome/Firefox держат старые TCP/QUIC-соединения
  и DNS-кэш — они продолжают идти напрямую и «ломают» youtube/gemini. После
  первого `vpn start reality`/`xhttp` перезапустите браузер (новые вкладки не всегда помогают
  из-за переиспользования QUIC-сессии). `vpn start` печатает эту подсказку.

**Ограничения (SPEC §8):**
- клиенты — только Xray-core (vpnm/tun на ноутбуке; мобильным — обычные
  share-ссылки Reality/CDN);
- UDP-over-TCP: звонки/игры хуже (xray туннелирует UDP поверх TCP) — для UDP
  остаётся AWG (`vpn scheme awg`);
- провайдерский троттлинг порта схемой не лечится; Reality-split идёт через
  tcp/443, XHTTP-split — через tcp/8443.


## Канал 1в: RU-релей (`vpn start relay`, ADR-007)

Прямой и релейный контуры **взаимоисключающие**: `vpn start` всегда гасит
релей, `vpn start relay` — прямой контур (socks-профили, AWG-split `@laptop`,
xray-split). Если снять старый контур не удалось, новый не поднимается —
лучше явная ошибка, чем два маршрута одновременно.

```bash
vpn start relay                            # AWG → релей (весь трафик)
vpn start relay --proto reality            # фолбэк: socks relay-reality
vpn start relay-reality                    # алиас того же фолбэка
vpn down                                   # профили + релей off, guard жив
vpn status                                 # mode: RELAY (awg relay → <IP>)
```

- конфиг клиента: `~/.awg/relay.conf` (0600) — выдаётся на релее
  (`vds relay add-client laptop`); CLI копирует его в
  `/etc/amnezia/amneziawg/relay.conf` (0600) и поднимает
  `vpn-awg-split@relay.service` (шаблон `14_awg-split.sh`);
- доменный split на ноутбуке **не применяется**: RU-CIDR/мир делит policy
  самого релея (`vds-relay-policy`), клиентский AWG гонит всё в туннель;
- фолбэк `--proto reality` поднимает обычный socks-профиль `relay-reality`
  (весь трафик сам не заворачивает — включите `vpn sysproxy on` или укажите
  приложению socks-порт);
- `vpn-guard.service` не останавливается и не перезапускается ни одной из
  этих команд (`vpn down` тоже); guard лишь перечитывает sysfs-интерфейс.

### `mode.json` — контракт с `vpn guard`

Какой контур активен, CLI пишет в `~/.local/share/vpn-manager/mode.json`
(0600, schema `vpnm.mode/1`):

```json
{"schema": "vpnm.mode/1", "mode": "relay", "proto": "amneziawg",
 "iface": "relay", "unit": "vpn-awg-split@relay.service",
 "target": "203.0.113.10", "updated": "..."}
```

Это **не** `guard/activity.json`: тот файл — probe-маркер
`{cmd,started,until}` для FP-guard на время `probe`/`speed`/`monitor`, и
запись режима туда сломала бы `mark_activity_end`/`activity_active`. Guard
читает `mode.json` только как подсказку для статуса/`resolve_iface(hint)`;
источник правды о счётчиках — sysfs. При переключении `@laptop` → `@relay`
poller сам переразрешает интерфейс (без ложного `G-04 awg_iface_gone`).

## Планы: split для других транспортов

Сейчас системный зонный split есть у AWG (канал 1) и xray-транспортов —
Reality/XHTTP (канал 1б).
Разбор «может ли Reality / Hysteria2 / CDN стать основным со split» (что
мешает, оценки, порядок внедрения, почему Docker не решает) — в vds:
[docs/SPLIT-TRANSPORTS.md](https://github.com/asvspb/my-first-vds/blob/main/docs/SPLIT-TRANSPORTS.md).
