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
│   └── repair.py          # снимок состояния + возврат трафика/DNS
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
| `vpn profile list` / `remove <name>` / `show <name>` | Управление профилями |
| `vpn up <name>` / `vpn down [name]` / `vpn switch <name>` | Жизненный цикл (systemd user units) |
| `vpn scheme awg\|cdn\|proxy <p>\|private <p>` | Пресеты: AWG+split / CDN / общий прокси / частный прокси |
| `vpn probe` | Замер задержек + bulk-скорость (один эндпоинт), переключение на быстрейший |
| `vpn speed` | **Живой замер скорости**: все каналы, несколько эндпоинтов, real-time таблица |
| `vpn status` | Активный профиль, состояние unit, внешний IP через SOCKS |
| `vpn sysproxy on\|off\|status` | GNOME системный прокси → SOCKS активного профиля |
| `vpn autostart on\|off\|status` | Автозапуск активного профиля при входе (systemctl --user enable) |
| `vpn zapret on\|off\|status` | Канал 0: zapret (nfqws DPI-desync) — сервис + прямой canary |
| `vpn doh on\|off\|status` | DoH-резолвер cloudflared (127.0.0.1:5053) |
| `vpn amneziawg status\|add-client\|remove-client\|tunnel` | AWG: локальный туннель и клиенты на сервере |
| `vpn doctor` | Диагностика: бинари, порты, v2rayA, ssh, DNS, zapret |
| `vpn repair` | Снимок состояния в лог + вернуть трафик/DNS |
| `vpn monitor [--json]` | Матрица каналов: vpnm / cdn / amneziawg / zapret / ZeroTier / dns-doh (N/6) |
| `vpn install [--linger] [--autostart [profile]]` | Установка бинарей + linger + автозапуск профиля |
| `vpn install-bins` | Только бинари xray/hysteria (без linger/автозапуска) |

`vpn` также доступен как `python3 -m vpnm`.

Shell-guard `_check_vpn_active()` в `$USER/.zshrc` / `$USER/.bashrc` (блокирует запуск
Antigravity без активного VPN) признаёт, помимо ZeroTier/WireGuard/vpnm, и AmneziaWG:
интерфейс `awg*`/`amnezia*` в состоянии UP/UNKNOWN или вывод `awg show`.

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

## Мониторинг каналов (`vpn monitor`)

Матрица доступности каналов (rich-таблица; `--json` — та же матрица):

| Канал | Что проверяет | Статусы |
|-------|---------------|---------|
| `vpnm` | активный профиль + egress-probe через SOCKS | ok / fail / not_installed |
| `zerotier` | сервис, `zerotier-cli status`, joined-сети | ok / warn / fail / not_installed (в архиве) |
| `amneziawg` | артефакты (бинарь/конфиги/интерфейс) — слот | not_installed / ok (iface up) / warn |
| `zapret` | канал 0: сервис активен → **прямой** canary на youtube generate_204 | ok / warn / fail / not_installed |
| `dns-doh` | канал 0.5: сервис `cloudflared-proxy-dns` активен + `dig @127.0.0.1 -p 5053 www.youtube.com +short` непуст | ok / fail / not_installed |

В конце — `healthy channels: N/5` (exit 1, если ни один канал не ok).
Строка `zapret`: `not_installed` — нейтрально (канал 0 не ставили);
`ok` — «direct DPI-bypass жив»; `fail` — сервис активен, но canary не прошла
(стратегия сломалась — blockcheck заново); `warn` — установлен, но не запущен.
Строка `dns-doh`: `not_installed` — нейтрально (локальный DoH не ставили);
`ok` — «DoH-резолвер жив (локальный cloudflared)»; `fail` — сервис активен,
но dig пуст (upstream недоступен/перехвачен), либо сервис установлен, но не
запущен (системный DNS молча деградирует до plaintext-перехвата).

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

## Разработка

Тесты: `python3 -m pytest` из `scripts/vpn-manager/` (моки subprocess, без сети).
Секреты (share-ссылки) не логируются; store и конфиги всегда 0600.

### Синхронизация repo ↔ установленная команда
После правок кода модуля переустанавливайте пакет: `pipx install --force scripts/vpn-manager` — установленный `vpn` не подхватывает изменения автоматически (не editable-инсталляция).


## Канал 1б: xray-split (tun, Reality/XHTTP, SPEC-20261114)

Системный зонный split на xray-транспорте (Reality **или** XHTTP) — та же
механика, что у AWG (dnsmasq → nft set → fwmark → table → dev), но
`dev = xray0`: tun-инбаунд xray-клиента (v26.1.23+; сервер asv1 тоже обновлён
до 26.1.23 — ADR-010; socks-профили vpnm могут оставаться на 25.1.1, у
split-юнита свой бинарь в `/usr/local/lib/vpn-xray-split`).

```bash
vpn scheme xray-split [профиль]    # Reality/XHTTP профиль из store (root)
vpn scheme xhttp-split [профиль]   # алиас: автовыбор только xray-xhttp
vpn scheme awg                     # назад: снимает xsplit (взаимно исключительны)
vpn scheme reset                   # снять всё
sudo bash scripts/17_xray-split.sh --status | --off
```

- конфиг: `/etc/vpn-manager/xray-split-<профиль>.json` (0600) генерирует vpnm
  из share-link (Reality: flow=xtls-rprx-vision; XHTTP: path); секрет (UUID)
  не попадает в argv/логи (tmp 0600 + sudo install);
- защита от петли: `sockopt.mark=0xca6c` на outbound — nft возвращает этот
  mark, иначе домен сервера в зоне `com` уводил бы xray в собственный tun;
- split-правила: `15_domain-split.sh --dev xray0 --mark 0x23000 --table 23000
  --nft-table xray_split`; домены/исключения — те же `domains.txt`/`exclude.txt`;
- kill-switch: `ExecStopPost=vds-xsplit-cleanup` — остановка/падение юнита
  снимает ip rule/table, интернета-дыры не будет (трафик уходит напрямую);
- самохил: `ExecStartPost=vds-xsplit-cleanup --apply` — после рестартов юнита
  правила восстанавливаются сами;
- DoH: схема сама включает цепочку (`_ensure_doh`) и ждёт готовности резолвера
  перед стартом split — иначе dnsmasq уходит в мёртвый 5053;
- монитор: строка `xray-split` в `vpn monitor` (unit/iface/mark/table + прямая
  проба 204 через tun).

**Ограничения (SPEC §8):**
- клиенты — только Xray-core (vpnm/tun на ноутбуке; мобильным — обычные
  share-ссылки Reality/CDN);
- UDP-over-TCP: звонки/игры хуже (xray туннелирует UDP поверх TCP) — для UDP
  остаётся AWG (`vpn scheme awg`);
- провайдерский троттлинг порта схемой не лечится; Reality-split идёт через
  tcp/443, XHTTP-split — через tcp/8443.

## Планы: split для других транспортов

Сейчас системный зонный split есть у AWG (канал 1) и xray-транспортов —
Reality/XHTTP (канал 1б).
Разбор «может ли Reality / Hysteria2 / CDN стать основным со split» (что
мешает, оценки, порядок внедрения, почему Docker не решает) — в vds:
[docs/SPLIT-TRANSPORTS.md](https://github.com/asvspb/my-first-vds/blob/main/docs/SPLIT-TRANSPORTS.md).
