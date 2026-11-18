# SPEC-20261118-vpn-qr: Подкоманда `vpn qr` — генерация QR-кодов и клиентских профилей подключения

- **ID:** SPEC-20261118-vpn-qr
- **Версия:** rev.3.2
- **Статус:** G2 PASS — APPROVED & FROZEN (2026-11-18)
- **Репозиторий исполнения (КРИТИЧНО):** `~/Dev/ubuntuInstaller` (`scripts/vpn-manager/`).
  НЕ трогать серверный репозиторий `~/Dev/my-coding/vds/src/**`.

## Затрагиваемые файлы

| Файл | Что делаем |
|---|---|
| `scripts/vpn-manager/pyproject.toml` | **Обязательно (D1):** добавить `"vpnm.qrgen"` в явный список `[tool.setuptools] packages` |
| `scripts/vpn-manager/vpnm/cli.py` | Регистрация команды `qr` в Typer-приложении |
| `scripts/vpn-manager/vpnm/links.py` | Чтение query-ключа `spx` наряду с `spath` при парсинге `vless://` |
| `scripts/vpn-manager/vpnm/amneziawg.py` | Переиспользование логики очистки `Table =` / `SaveConfig` без поломки существующих сигнатур |
| `scripts/vpn-manager/vpnm/qrgen/*` | Новый изолированный пакет; существующий `vpnm/qr.py` не конвертировать в пакет и не ломать |
| `scripts/vpn-manager/tests/qrgen/*` | Юнит- и CLI-тесты |
| `doc/specs/SPEC-20261118-vpn-qr.md`, `doc/vpn-manager.md`, `doc/PROGRAM_DESCRIPTIONS.md` | Документация |

**Запрещено трогать:** `vds/**`, `vpnm/qr.py`, `tests/test_profile_qr.py`, `vpnm/guard/*`.

## 1. Цель, границы и инварианты

### 1.1. Цель и источники данных (P1)

Дать пользователю детерминированную, полностью офлайновую Typer-команду `vpn qr`,
которая собирает валидный клиентский профиль подключения и рендерит его через
системный `qrencode` с учётом:

- **Маршрута (route):** `--direct` или `--relay`
- **Протокола (proto):** `--awg`, `--reality`, `--xhttp` или `--hy`
- **Целевой ОС (os):** `--android`, `--ios`, `--windows`, `--macos` или `--linux`

**Инвариант изоляции путей (P1).** Резолверу запрещено хардкодить `~` или вызывать
`Path.home()` напрямую в обход констант модулей `vpnm`. Чтение состояния выполняется
строго через:

- `vpnm.profiles.STORE_PATH` (управляется env `VPN_MANAGER_CONFIG_DIR`);
- `vpnm.mode.read_mode()` (управляется env `VPN_MANAGER_DATA_DIR`);
- `vpnm.amneziawg.LOCAL_CONF_DIR` (управляется env `VPN_MANAGER_AWG_CONF_DIR`);
- `vpnm.amneziawg.AWG_SYSTEM_CONF_DIR` (`/etc/amnezia/amneziawg`), чтение
  конкретного `<name>.conf` — только через read-only `sudo -n cat`, замоканный в тестах.

Это гарантирует, что прогон pytest никогда не затронет и не прочитает живые
конфиги хоста.

### 1.2. Не-цели (Non-Goals v1)

- Никаких мутаций состояния: не вызываются `systemctl`, `ufw`, `vpn switch/up/down`,
  не создаются пиры.
- Никаких сетевых сокетов.
- Никаких новых внешних Python-зависимостей в `pyproject.toml`: используется только
  системный `qrencode` (4.1.1+).
- Контейнер `vpn://` (`--client amneziavpn`) отложен в v2 (в v1 возвращает
  `EXIT_UNSUPPORTED_COMBO = 3`).

### 1.3. Неподвижные инварианты безопасности и архитектуры

**Секреты не попадают в argv, stderr, логи и дефолтный stdout:**

- Вызов `qrencode` всегда использует явный аргумент `-r /dev/stdin` и получает
  payload через stdin (`input=payload`).
- В режиме `--format qr` (по умолчанию) печатаются только безопасная шапка
  метаданных, `warnings`, `Hint` и сам QR-код. Сырой URI / INI под QR печатается
  только при явном флаге `--show-uri`.
- В режиме `--format json` по умолчанию `"payload": null` (отдаются только
  `"payload_bytes"` и `"payload_sha256": "sha256:<64hex>"`). Поле `"payload"`
  заполняется строкой только при передаче `--show-uri`.
- В stderr при любых ошибках никогда не выводятся `PrivateKey`, `PresharedKey`,
  `uuid`, `pbk`, `password`, `obfs-password` или полный URI.

**Атомарная запись и права 0600 (`--out`):**

- Любой файл `--out` записывается через временный файл в целевом каталоге +
  `os.chmod(tmp_path, 0o600)` + `os.replace(tmp_path, out_path)`.
- При перезаписи существующего файла с `--force` результирующий файл также
  гарантированно получает права 0600.

**Read-only subprocess allowlist** (совместимость с `tests/conftest.py`).
Разрешены только два внешних вызова:

1. `qrencode ... -r /dev/stdin`
2. `sudo -n cat <AWG_SYSTEM_CONF_DIR>/<name>.conf`

Запрещены `systemctl`, `os.listdir(AWG_SYSTEM_CONF_DIR)`, интерактивный `sudo`
без `-n` и `shell=True`.

**Неприкосновенность `vpnm/qr.py`.** Модуль не превращается в пакет и не
модифицируется. Весь новый код размещается в `vpnm/qrgen/` и регистрируется в
`[tool.setuptools] packages` в `pyproject.toml` (D1).

## 2. CLI-интерфейс (Typer)

### 2.1. Синтаксис

Регистрация в `vpnm/cli.py` через `@app.command("qr")`.

```
vpn qr (--direct | --relay) (--awg | --reality | --xhttp | --hy) \
       (--android | --ios | --windows | --macos | --linux) [OPTIONS]
```

В начале команды проверяется, что в каждой из 3 групп выбран ровно один флаг
(иначе сообщение в stderr и `raise typer.Exit(code=2)`):

- **Маршрут:** `--direct`, `--relay`
- **Протокол:** `--awg`, `--reality`, `--xhttp`, `--hy` (плюс алиас `--hy2`)
- **Целевая ОС:** `--android`, `--ios`, `--windows`, `--macos`, `--linux`

### 2.2. Опциональные флаги и валидация `--profile` (P3)

| Флаг | Тип / Допустимые значения | По умолчанию | Описание |
|---|---|---|---|
| `--profile <name>` | `Optional[str]` | `None` | Имя профиля в `profiles.STORE_PATH` (`asv2-reality`, `relay-reality`, `asv2-hy2`) или stem конфига AWG (`laptop`, `android`, `windows`, `relay`, `awg0-laptop`). Валидация (P3): обязано матчить `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$` (точки, `/`, `\`, `..`, ведущий `-`/`_` и пустая строка запрещены → код 2) |
| `--client <name>` | `auto`, `amneziawg`, `amneziavpn`, `v2rayng`, `hiddify`, `nekobox`, `v2rayn`, `shadowrocket`, `streisand` | `auto` | Целевой клиент (матрица совместимости — §3.3) |
| `--format <fmt>` | `qr`, `uri`, `png`, `svg`, `json` | `qr` | Формат вывода |
| `--out <path>` | `Optional[Path]` | `None` | Путь для сохранения файла. Обязателен для `png` и `svg`; запрещён для `qr` (код 2); опционален для `uri` и `json` |
| `--force` | `bool` | `False` | Разрешить перезапись существующего `--out` (с установкой прав 0600) |
| `--show-uri` | `bool` | `False` | В режиме `--format qr` вывести сырой URI/INI под QR; в режиме `--format json` заполнить поле `"payload"` |
| `--ascii` | `bool` | `False` | Рендерить QR типом `ASCII` / `ASCIIi` вместо `ANSIUTF8` / `ANSIUTF8i` |
| `--invert` | `bool` | `False` | Инвертировать цвета QR выбором типа `ANSIUTF8i` (или `ASCIIi` при `--ascii`) |
| `--no-tty-check` | `bool` | `False` | Отключить проверку ширины окна терминала перед выводом QR |

## 3. Матрица маршрутов, клиентов и алгоритм резолвера

### 3.1. Фактическая матрица (Маршрут × Протокол)

| Маршрут | Протокол | Статус | Источник креденшелов и параметров |
|---|---|---|---|
| `--direct` | `--reality` | ✅ Поддерживается | Direct-профиль Reality из `profiles.STORE_PATH` (напр. `asv2-reality`) |
| `--relay` | `--reality` | ✅ Поддерживается | Relay-профиль Reality из `profiles.STORE_PATH` (напр. `relay-reality` со своими `host`, `uuid`, `pbk`, `sid`, `sni=ya.ru`) |
| `--direct` | `--xhttp` | ✅ Поддерживается | Direct-профиль XHTTP из `profiles.STORE_PATH` + анти-ML параметры GR-25 (`vpnm/xclient.py`) |
| `--relay` | `--xhttp` | ❌ Код 3 | На релее нет XHTTP-инбаунда → `EXIT_UNSUPPORTED_COMBO` (3) |
| `--direct` | `--hy` | ✅ Поддерживается | Direct-профиль Hysteria2 из `profiles.STORE_PATH` (напр. `asv2-hy2`) |
| `--relay` | `--hy` | ❌ Код 3 | На релее нет Hysteria2-инбаунда → `EXIT_UNSUPPORTED_COMBO` (3) |
| `--relay` | `--awg` | ✅ Поддерживается | Релейный конфиг из `amneziawg.LOCAL_CONF_DIR` (`{android,windows,relay}.conf`) или `AWG_SYSTEM_CONF_DIR / "relay.conf"` |
| `--direct` | `--awg` | ✅ Поддерживается | Direct-конфиг AWG по приоритету §3.2C (активный `iface` из `mode.read_mode()`, напр. `AWG_SYSTEM_CONF_DIR / "laptop.conf"`) |

### 3.2. Детерминированный алгоритм резолвера (`resolver.py`)

#### A. Сбор `relay_hosts` и классификация профиля (direct vs relay)

`relay_hosts` собирается из:

1. `Endpoint` (часть host без порта) файла `amneziawg.LOCAL_CONF_DIR / "relay.conf"`,
   если он существует (напр. `relay.asvpersonal.ru`);
2. поля `target` из `mode.read_mode()`, только если `mode == "relay"` (или
   `route == "relay"`) и ключ `target` присутствует и непуст.

> В текущем direct-режиме `mode.read_mode()` возвращает
> `{"mode":"direct","proto":"amneziawg","iface":"laptop","unit":"vpn-awg-split@laptop.service"}`
> без `target` — резолвер не требует наличия `target`.

Правило классификации записи `p` из `profiles.STORE_PATH` (R1):

```python
name_is_relay = p.name == "relay" or p.name.startswith("relay-")
host_is_relay = p.host in relay_hosts
# relay, если name_is_relay or host_is_relay; иначе direct
```

Если `name_is_relay and relay_hosts and not host_is_relay` (как у `relay-reality`
с IP `185.80.91.135`), профиль классифицируется как relay и в `warnings`
добавляется:

```
профиль '<name>' классифицирован как relay по имени (host <host> отличается от домена в relay.conf)
```

#### B. Выбор профиля для `--reality`, `--xhttp`, `--hy`

Если задан `--profile NAME` (прошедший валидацию P3):

1. Находим профиль `NAME` в `profiles.STORE_PATH` (если нет → код 4).
2. Проверяем совпадение `proto` и классифицированного route (direct vs relay).
   При несовпадении → код 4.

Если `--profile` не задан:

1. Фильтруем профили по (`proto`, `route`).
2. Найдено 1 → возвращаем его; найдено 0 или >1 → fail-closed код 4 со списком
   кандидатов и подсказкой `--profile <NAME>`.

#### C. Выбор и чтение конфига для `--awg` (P1, P2, R3, R4, R8, Мелочь 4)

**Фильтрация файлов.** В `amneziawg.LOCAL_CONF_DIR` рассматриваются только
регулярные файлы, чьё имя заканчивается строго на `.conf` и чей stem матчит
`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$` (все `*.bak*`, `*.conf.*`, `.hidden` отсекаются).

**Классификация AWG-конфига:**

```python
is_relay_awg(stem, endpoint_host) == (
    stem == "relay" or stem.startswith("relay-") or endpoint_host in relay_hosts
)
is_direct_awg(stem, endpoint_host) == not is_relay_awg(stem, endpoint_host)
```

**Явный `--profile NAME` для `--awg`:**

1. Ищем `amneziawg.LOCAL_CONF_DIR / f"{NAME}.conf"`; если нет —
   `sudo -n cat <AWG_SYSTEM_CONF_DIR>/{NAME}.conf`. Не найден → код 4.
2. Проверяем соответствие `is_relay_awg` / `is_direct_awg` запрошенному
   `--relay` / `--direct`. При несовпадении → код 4.

**Авто-выбор для `--relay --awg` (без `--profile`).** Среди релейных конфигов
(`is_relay_awg == True`) в `LOCAL_CONF_DIR`:

| ОС | Приоритет файлов |
|---|---|
| `--android` / `--ios` | `android.conf` → `relay.conf` |
| `--windows` / `--macos` | `windows.conf` → `relay.conf` |
| `--linux` | `relay.conf` |

Если в `LOCAL_CONF_DIR` релейного конфига нет — fallback
`sudo -n cat <AWG_SYSTEM_CONF_DIR>/relay.conf` (с проверкой `is_relay_awg`).

**Авто-выбор для `--direct --awg` (без `--profile`)** (P2 + R3 + R4b):

1. **Приоритет 1** — локальный per-OS direct-конфиг в `LOCAL_CONF_DIR`. Фильтруем
   файлы, оставляя только direct-кандидатов (`is_direct_awg(stem, endpoint_host) == True`).
   Если среди них есть `<os>.conf` — берём его; иначе если найден ровно один
   direct-кандидат — берём его. (Релейные файлы в `LOCAL_CONF_DIR` здесь полностью
   игнорируются.)
2. **Приоритет 2** — активный пир из `mode.read_mode()`. Извлекаем `iface`
   (или парсим из `unit` вида `vpn-awg-split@<iface>.service`). Если `iface`
   валиден (`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`) и `not (iface == "relay" or
   iface.startswith("relay-"))`:
   - читаем `LOCAL_CONF_DIR / f"{iface}.conf"`, при отсутствии —
     `sudo -n cat <AWG_SYSTEM_CONF_DIR>/{iface}.conf`;
   - **обязательная проверка P2:** конфиг принимается, только если для него
     выполняется `is_direct_awg(iface, endpoint_host)`. Если в `mode.json` был
     активен релейный интерфейс или Endpoint указывает на релей — кандидат
     отвергается, управление переходит к Приоритету 3.
3. **Приоритет 3** — если Приоритеты 1 и 2 не дали direct-конфига: fail-closed
   код 4 (`EXIT_CONFIG_ERROR`) с подсказкой `--profile <NAME>` или
   `vpn amneziawg add-client`.

**Обязательный warning при выборе активного пира (R4b):**

```
используется конфиг активного пира '<iface>' (<Address>); одновременная работа
с этим хостом вызовет коллизию handshake. Для отдельного устройства создайте
пир: vpn amneziawg add-client <name>
```

**Проверка наличия `DNS` и `PersistentKeepalive` (Мелочь 4).** Если в выбранном
AWG INI (например, в `relay.conf`) отсутствует ключ `DNS` в `[Interface]` или
отсутствует `PersistentKeepalive` в `[Peer]`, резолвер добавляет в `warnings`:

```
в конфиге '<stem>.conf' отсутствует DNS и/или PersistentKeepalive (профиль
оптимизирован для split-туннеля хоста; на мобильном устройстве возможна потеря
DNS или NAT-таймаут)
```

### 3.3. Матрица совместимости `--client` × Протокол (Мелочь 2)

| Протокол | Допустимые `--client` | Дефолт при `--client auto` (по ОС) | Несовместимые `--client` (код 3) |
|---|---|---|---|
| `--awg` | `auto`, `amneziawg` | все ОС → `amneziawg` | `amneziavpn` (отложено в v2), `v2rayng`, `v2rayn`, `hiddify`, `nekobox`, `shadowrocket`, `streisand` |
| `--reality`, `--xhttp`, `--hy` | `auto`, `v2rayng`, `v2rayn`, `hiddify`, `nekobox`, `shadowrocket`, `streisand` | `android` → `v2rayng`; `ios` → `streisand`; `windows` → `v2rayn`; `macos` → `hiddify`; `linux` → `nekobox` (для xhttp → `hiddify`) | `amneziawg`, `amneziavpn` |

Любое значение `--client`, не входящее в общий список (`auto`, `amneziawg`,
`amneziavpn`, `v2rayng`, `v2rayn`, `hiddify`, `nekobox`, `shadowrocket`,
`streisand`), возвращает код 2 (`EXIT_USAGE`).

## 4. Спецификация генерации Payload (`builders.py`)

### 4.1. AmneziaWG (`--awg`) — сохранение параметров и strip `Table = off` (R2)

- Все клиентские поля выбранного `.conf` (`AllowedIPs`, `DNS`, `PersistentKeepalive`,
  `MTU`, `Jc..Jmax`, `S1..S2`, `H1..H4`, `I1..I5`) сохраняются как в источнике.
- Из секции `[Interface]` обязательно вырезаются system-only ключи wg-quick:
  `Table` (включая `Table = off` из `laptop.conf`), `SaveConfig`, `PreUp`, `PostUp`,
  `PreDown`, `PostDown` — логика `vpnm/amneziawg.py:57-61`.

### 4.2. VLESS Reality (`--reality`)

```
vless://<uuid>@<host>:<port>?encryption=none&flow=xtls-rprx-vision&fp=<fp>&pbk=<pbk>&security=reality&sid=<sid>&sni=<sni>[&spx=<url_encoded_spx>]&type=tcp#<profile_name>-<os>
```

- Непустой `spath` (или `spx`) из профиля эмитится как `spx=<url_encoded>`.
- В `vpnm/links.py` при парсинге `vless://` читается
  `params.get("spath") or params.get("spx")`.

### 4.3. VLESS XHTTP (`--xhttp`) с анти-ML профилем GR-25

```
vless://<uuid>@<host>:<port>?encryption=none&extra=<url_encoded_extra_json>&fp=<fp>[&host=<url_encoded_xhttp_host>]&mode=<mode>&path=<url_encoded_path>&security=<security>&sni=<sni>&type=xhttp#<profile_name>-<os>
```

- Без `flow`.
- Параметр `extra` строится из констант анти-ML профиля GR-25 в
  `vpnm/xclient.py:27-29, 113-120` (`xPaddingBytes` + `xmux`), сериализованных через
  `json.dumps(extra_dict, separators=(",", ":"), sort_keys=True)` и URL-encoded.

### 4.4. Hysteria2 (`--hy`)

```
hysteria2://<url_encoded_auth>@<host>:<port>/?sni=<sni>[&obfs=salamander&obfs-password=<url_encoded_obfs_pw>][&mport=<hop_range>][&pinSHA256=<pin>][&insecure=<0|1>]#<profile_name>-<os>
```

Схема строго `hysteria2://` (round-trip с `vpnm/links.py:28-35, 192`).

## 5. Рендеринг через `qrencode` и расчёт геометрии терминала (`render.py`)

### 5.1. Точные аргументы `qrencode` (R5, Мелочи 1 и 3)

- Флаг `-i` (`--ignorecase`) **никогда не передаётся**.
- Аргумент `-r /dev/stdin` **всегда** передаётся явно во всех вызовах; payload
  подаётся через stdin.

| `--format` | `--ascii` | `--invert` | Полная команда |
|---|---|---|---|
| `qr` (дефолт) | False | False | `qrencode -t ANSIUTF8 -m 4 -l <ECC> -o - -r /dev/stdin` |
| `qr` | False | True | `qrencode -t ANSIUTF8i -m 4 -l <ECC> -o - -r /dev/stdin` |
| `qr` | True | False | `qrencode -t ASCII -m 4 -l <ECC> -o - -r /dev/stdin` |
| `qr` | True | True | `qrencode -t ASCIIi -m 4 -l <ECC> -o - -r /dev/stdin` |
| `png` | — | — | `qrencode -t PNG -s 8 -m 4 -l <ECC> -o <tmp_path> -r /dev/stdin` |
| `svg` | — | — | `qrencode -t SVG -m 4 -l <ECC> -o <tmp_path> -r /dev/stdin` |

Уровень `<ECC>`: `M` при `len(payload.encode("utf-8")) <= 350`, иначе `L`.

### 5.2. Расчёт видимой ширины для TTY guard

Перед проверкой `shutil.get_terminal_size().columns` из строк вывода `qrencode`
удаляются все ANSI escape-последовательности:

```python
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

def visible_qr_width(qr_output: str) -> int:
    lines = [ANSI_RE.sub("", line.rstrip("\r\n")) for line in qr_output.splitlines()]
    return max((len(line) for line in lines if line), default=0)
```

Если `sys.stdout.isatty()` и не задан `--no-tty-check`, при
`visible_qr_width(qr_output) > term_columns` QR не печатается, в stderr выводится
требуемая и текущая ширина, код возврата 5.

## 6. Полная таблица кодов возврата (D2 исправлен — без `|` внутри ячеек)

| Код | Константа | Условия возврата |
|---|---|---|
| 0 | `EXIT_OK = 0` | Успешная генерация и вывод или сохранение |
| 2 | `EXIT_USAGE = 2` | (a) не выбран или выбрано более 1 флага в группе маршрута, протокола или ОС; (b) `--format png` или `--format svg` передан без `--out`; (c) `--out` передан вместе с `--format qr`; (d) неизвестное значение `--format` или `--client`; (e) невалидный `--profile` (не матчит `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`) |
| 3 | `EXIT_UNSUPPORTED_COMBO = 3` | (a) `--relay --xhttp`; (b) `--relay --hy`; (c) `--client amneziavpn` (отложено в v2); (d) несовместимая пара `--client` и протокола по таблице §3.3 |
| 4 | `EXIT_CONFIG_ERROR = 4` | (a) профиль не найден в `profiles.STORE_PATH`, `amneziawg.LOCAL_CONF_DIR` или `amneziawg.AWG_SYSTEM_CONF_DIR`; (b) найдено более 1 кандидата без `--profile` (и нет валидного direct-пира в `mode.read_mode()`); (c) указанный `--profile` имеет другой протокол или другой маршрут (direct vs relay); (d) отсутствуют обязательные крипто-поля в профиле или INI |
| 5 | `EXIT_RENDER_ERROR = 5` | (a) бинарник `qrencode` отсутствует или завершился с ошибкой; (b) видимая ширина QR (после ANSI-strip) превышает ширину TTY (без `--no-tty-check`); (c) файл `--out` уже существует, а `--force` не передан; (d) ошибка записи файла `--out` |

## 7. Критерии приёмки (Acceptance Criteria — G2 PASS)

Все тесты в `tests/qrgen/` работают исключительно на изолированных фикстурах через
переопределение `profiles.STORE_PATH`, `mode.read_mode` / `VPN_MANAGER_DATA_DIR`,
`amneziawg.LOCAL_CONF_DIR`, `amneziawg.AWG_SYSTEM_CONF_DIR` и мок `subprocess.run`
для `qrencode` и `sudo -n cat` (P1, R9).

**AC-QR-1 (CLI, Typer и защита `--profile` P3 + D2).** Матрица флагов; код 2 при
нарушении групп, при `--format png` или `svg` без `--out`, при `--out` с
`--format qr`, при неизвестном `--format` / `--client` и при попытках передать в
`--profile` значения `..`, `../etc/passwd`, `.hidden`, `laptop.conf`.
`tests/test_profile_qr.py` зелёный.

**AC-QR-2 (Резолв `relay-reality` при IP-хосте, R1).** На фикстуре с
`relay.conf -> relay.asvpersonal.ru:443`, `mode.json -> {"mode":"direct","iface":"laptop"}`
(без `target`) и `relay-reality -> 185.80.91.135` вызов
`vpn qr --relay --reality --windows` выбирает `relay-reality`, эмитит его
`uuid`/`pbk`/`sid` и `sni=ya.ru` + warning о классификации по имени.

**AC-QR-3 (Матрица `--relay` и `--client` × proto, B2 + Мелочь 2).**
`--relay --xhttp`, `--relay --hy`, `--client amneziavpn`, `--awg --client v2rayng`
и `--reality --client amneziawg` возвращают код 3 без вызова `qrencode`.

**AC-QR-4 (AWG Direct/Relay, P2, strip `Table = off`, warnings R4b + Мелочь 4).**

- `--relay --awg --android` выбирает `android.conf`, `--windows` — `windows.conf`,
  `--linux` — `relay.conf`; для `relay.conf` (без DNS и `PersistentKeepalive`) в
  `warnings` присутствует предупреждение об отсутствии DNS/`PersistentKeepalive`.
- `--direct --awg --android` при наличии только релейных файлов в `LOCAL_CONF_DIR`
  выбирает `<AWG_SYSTEM_CONF_DIR>/laptop.conf` по `iface="laptop"` из
  `mode.read_mode()`, проверяет, что его Endpoint (`213.108.23.119:51825`) не
  релейный (P2), игнорирует `awg0-laptop.conf` и `*.bak*`, вырезает `Table = off`
  и добавляет warning о коллизии активного пира `laptop` (R4b).
- Если в `mode.read_mode()` подставить `iface="relay"` (или конфиг, чей Endpoint
  принадлежит `relay_hosts`), `--direct --awg` не выбирает его (P2) и завершается
  с кодом 4.

**AC-QR-5 (Round-trip `links.py` и GR-25).**

- `hysteria2://` от `--direct --hy` и `vless://` (с `spx`) от `--direct --reality`
  проходят round-trip через `vpnm/links.py`;
- `--direct --xhttp` содержит `security=tls` и URL-encoded `extra`
  (GR-25 `xPaddingBytes` + `xmux` из `vpnm/xclient.py`), без `flow`.

**AC-QR-6 (Флаги `qrencode`, `-r /dev/stdin` и ANSI TTY-guard).**

- проверяются точные аргументы: `-t ANSIUTF8` (дефолт), `-t ANSIUTF8i` (`--invert`),
  `-t ASCII` (`--ascii`), `-t ASCIIi` (`--ascii --invert`), `-t PNG -s 8`
  (`--format png`), наличие `-r /dev/stdin` и отсутствие `-i`;
- TTY guard считает ширину после снятия ANSI-кодов.

**AC-QR-7 (Секреты и права 0600).**

- отсутствие секретов в stdout (при `--format qr` и `--format json` без
  `--show-uri`) и в stderr;
- права 0600 на `--out` (включая перезапись с `--force`).

**AC-QR-8 (Packaging D1, Allowlist subprocess и e2e zbarimg).**

- `"vpnm.qrgen"` присутствует в `[tool.setuptools] packages` в
  `scripts/vpn-manager/pyproject.toml` (D1);
- все тесты проходят под `tests/conftest.py` autouse-guard;
- опциональный тест `PNG (-s 8) -> zbarimg --raw -q` (skipif нет `qrencode`/`zbarimg`).

## 8. Инструкция для выдачи Реализатору (WP-QR1 → WP-QR2 → WP-QR3)

**Рабочий каталог:** `cd ~/Dev/ubuntuInstaller/scripts/vpn-manager`
(серверный репо `vds` не трогать).

**Порядок реализации:**

- **WP-QR1:** `vpnm/qrgen/{__init__,models,redact,resolver}.py` +
  `tests/qrgen/test_qrgen_resolver.py` (проверка P1, P2, P3, R1, R3, R4b,
  Мелочи 2 и 4).
- **WP-QR2:** `vpnm/qrgen/builders.py` + `vpnm/links.py` (`spx`) +
  `tests/qrgen/test_qrgen_builders.py` (R2 strip `Table = off`, GR-25 `extra`,
  round-trip `hysteria2://` и `vless://`).
- **WP-QR3:** `scripts/vpn-manager/pyproject.toml` (добавить `"vpnm.qrgen"` в
  `packages`, D1) + `vpnm/qrgen/{render,cli}.py` + регистрация в `vpnm/cli.py` +
  `tests/qrgen/{test_qrgen_render,test_qrgen_cli,test_qrgen_zbar_roundtrip}.py` +
  обновление `doc/vpn-manager.md` и `doc/PROGRAM_DESCRIPTIONS.md`.

**Обязательная верификация перед отчётом** (включая проверку импорта из pipx-venv, D1):

```bash
cd ~/Dev/ubuntuInstaller/scripts/vpn-manager
python3 -m pytest -q
python3 -m pytest tests/qrgen tests/test_profile_qr.py -q
pipx install --force -e . && \
  ~/.local/pipx/venvs/vpn-manager/bin/python -c "import vpnm.qrgen; print('ok')" && \
  vpn qr --help
```

> `pipx install --force -e` всё равно кладёт **копию** пакета в venv (проверено:
> в `site-packages` лежит реальный каталог `vpnm/`, без editable-finder), поэтому
> после каждой правки исходников reinstall обязателен, а импорт проверяется
> **из venv**, а не из дерева исходников.
