# Изоляция аккаунтов Antigravity CLI (agy)

## Проблема

Команды `agy1` и `agy2` запускают Antigravity CLI под разными аккаунтами Google,
но оба аккаунта загружают авторизацию **одного и того же пользователя**.

### Корневая причина

`agy` — Go-бинарник, использующий библиотеку `github.com/zalando/go-keyring`.
На Linux эта библиотека подключается к `org.freedesktop.secrets` (SecretService/gnome-keyring)
через D-Bus.

Цепочка утечки:

```
agy2() → dbus-run-session -- agy
  → go-keyring → org.freedesktop.secrets (D-Bus)
  → gnome-keyring-daemon (автоактивируется через /usr/share/dbus-1/services/org.freedesktop.secrets.service)
  → /home/asv-spb/.local/share/keyrings/login.keyring (общий системный keyring)
  → OAuth-токен первого аккаунта
```

Почему `HOME` override не помог:
- `gnome-keyring-daemon` определяет путь к keyring по **UID** через `/etc/passwd`,
  а не по переменной `$HOME`
- Поэтому оба аккаунта (`agy1`/`agy2`) читают **один и тот же keyring-файл**

Почему `PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring` не помог:
- Эта переменная влияет только на Python-библиотеку `keyring`
- Go-бинарник `agy` использует свою библиотеку `zalando/go-keyring`
- Переменная окружения Python для Go-кода бесполезна

## Решение

Заменить `dbus-run-session` на `dbus-daemon` с кастомной конфигурацией,
которая **не содержит `<standard_session_servicedirs/>`**.
Это предотвращает автоактивацию `gnome-keyring-daemon`.

Когда `go-keyring` не может подключиться к SecretService, он переключается
на **fallback** (файловое хранилище). Файловое хранилище зависит от `$HOME`,
который переопределён для каждого аккаунта → каждый аккаунт получает
свой отдельный keyring.

## Архитектура изоляции

```
~/.agy_account_1/          ← HOME для аккаунта 1
├── .ssh → ~/...ssh        (симлинк)
├── .gitconfig → ~/...     (симлинк)
├── .bashrc → ~/...        (симлинк)
├── .dbus-isolated.conf    (кастомный dbus config без SecretService)
├── .gemini/               (отдельные данные agy)
├── .local/                (отдельный keyring fallback)
├── .runtime/              (XDG_RUNTIME_DIR)
└── bin/
    ├── xdg-open           (обёртка для браузера)
    └── google-chrome → xdg-open

~/.agy_account_2/          ← HOME для аккаунта 2 (аналогичная структура)
```

## Функции в ~/.bashrc / ~/.zshrc

### `_dbus_isolated_conf($fake_home)`
Генерирует файл `$fake_home/.dbus-isolated.conf` — конфигурацию D-Bus сессии
без стандартных сервисных директорий (SecretService не будет автоактивирован).

Конфигурация:
```xml
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=/tmp</listen>
  <auth>EXTERNAL</auth>
  <!-- NO <standard_session_servicedirs/> — ключевой момент изоляции -->
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
    <allow user="*"/>
  </policy>
  <policy context="mandatory">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
```

### `_start_isolated_dbus($conf)`
Запускает `dbus-daemon --fork` с кастомной конфигурацией.
Возвращает адрес и PID через глобальные переменные:
- `_ISOLATED_DBUS_ADDR` — адрес D-Bus сессии
- `_ISOLATED_DBUS_PID` — PID dbus-daemon (для cleanup)

### `_stop_isolated_dbus()`
Убивает dbus-daemon по PID после выхода agy.

### `_setup_agy_home($fake_home)`
Создаёт изолированное окружение:
- Симлинки: `.ssh`, `.gitconfig`, `.bashrc`, `.zshrc`, `.bash_profile`, `.profile`
- Обёртки браузера: `bin/xdg-open`, `bin/google-chrome`
- D-Bus конфигурация через `_dbus_isolated_conf()`
- Установка `keyrings.alt` для Python

### `agy1` / `agy2`
Запускают agy с полной изоляцией:
1. `_setup_agy_home` — создаёт окружение
2. `_start_isolated_dbus` — запускает dbus-daemon без SecretService
3. `HOME=... XDG_RUNTIME_DIR=... DBUS_SESSION_BUS_ADDRESS=... command agy` — запускает agy
4. `_stop_isolated_dbus` — cleanup после выхода

### `2agy`
Запускает оба аккаунта одновременно в двух окнах `gnome-terminal`.
Каждое окно запускает свой `dbus-daemon` с изолированной конфигурацией.

## Исправленные баги

| # | Баг | Исправление |
|---|-----|-------------|
| 1 | `agy1`/`agy2` использовали `export XDG_RUNTIME_DIR` — переменная утекала в родительский шелл | Передаётся инлайн вместе с `HOME`/`DBUS_SESSION_BUS_ADDRESS` |
| 2 | `2agy` вызывал голый `agy` — при переопределённом `HOME` мог рекурсировать в себя | Исправлено на `command agy` для всегда вызова бинарника |
| 3 | `.dbus-isolated.conf` не создавался автоматически — `dbus-daemon` падал молча | Файл создаётся при первом вызове `_dbus_isolated_conf` |

## Диагностика

Проверить, под каким аккаунтом авторизован agy:
```bash
# Посмотреть последние логи аккаунта 1
tail -20 $(ls -t ~/.agy_account_1/.gemini/antigravity-cli/log/ | head -1)
# Ищите строку: applyAuthResult: email=...

# Посмотреть последние логи аккаунта 2
tail -20 $(ls -t ~/.agy_account_2/.gemini/antigravity-cli/log/ | head -1)
```

Если видите `ChainedAuth: authenticated via keyring (effective: keyring)` —
используется keyring. Если оба аккаунта показывают один email — изоляция нарушена.

Проверить, что SecretService недоступен на изолированной шине:
```bash
dbus_output=$(dbus-daemon --config-file=~/.agy_account_1/.dbus-isolated.conf --print-address=1 --print-pid=1 --fork)
addr=$(echo "$dbus_output" | head -1)
pid=$(echo "$dbus_output" | sed -n '2p')
DBUS_SESSION_BUS_ADDRESS="$addr" dbus-send --session --dest=org.freedesktop.secrets \
  --print-reply / org.freedesktop.DBus.Peer.Ping 2>&1
# Ожидаемый результат: ServiceUnknown (SecretService недоступен — изоляция работает)
kill "$pid"
```

## Переустановка Antigravity

Если нужна полная переустановка с очисткой всех данных:
```bash
agy-reinstall
```

Эта функция удалит бинарники, конфиги, кэш, очистит keyring и установит заново.
