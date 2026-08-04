# Ubuntu Installer

Набор скриптов и конфигураций для автоматизации настройки нового Ubuntu-компьютера и управления VDS-серверами.

## Структура проекта

```
ubuntuInstaller/
├── scripts/            # Установочные скрипты (запускать по порядку)
├── vds/                # VDS Orchestrator — управление удалёнными серверами
├── $USER/              # Пользовательские конфиги (.bashrc, .zshrc, темы и др.)
├── doc/                # Документация
└── music/              # Ссылки на радиостримы
```

## Скрипты (scripts/)

Запускать на **локальной машине** в порядке нумерации:

| Скрипт | Описание |
|--------|----------|
| `1_ubuntuStart.sh` | Базовая настройка: время, сеть, Chrome, Telegram, sudo без пароля |
| `2_ubuntuDocker.sh` | Docker + среда разработки (требует перезагрузки) |
| `3_ubuntuPack.sh` | Расширенный набор приложений для разработчика |
| `4_snap-apps.sh` | Snap-пакеты |
| `5_samsung-printer-driver.sh` | Драйвер принтера Samsung |
| `6_zerotier-client.sh` | ZeroTier клиент + функции управления сетями |
| `7_v2raya-proxy.sh` | Установка и настройка v2rayA прокси |
| `8_vds-server.sh` | Инициализация нового VDS-сервера через VDS Orchestrator |
| `9_local-proxy-tunnel.sh` | Локальный SOCKS5/Nginx прокси для обхода системного VPN |

## VDS Orchestrator (vds/)

Python CLI для управления VDS-серверами на Ubuntu 24.04. Включает:
- Интерактивное TUI-меню (`vds`)
- Управление ZeroTier (watchdog, reconcile, NAT, диагностика)
- Управление WireGuard VPN
- Системный dashboard (CPU, RAM, Docker, диск)
- Первичная настройка сервера (пользователь, SSH, UFW, Docker)

👉 Подробнее: [doc/vds.md](doc/vds.md)

### Инициализация нового сервера

```bash
bash ~/Dev/ubuntuInstaller/scripts/8_vds-server.sh
```

## Пользовательские конфиги ($USER/)

| Путь | Содержимое |
|------|-----------|
| `.bashrc` / `.zshrc` | Shell-конфигурация, ZeroTier-функции |
| `.zt-functions.sh` | Функции управления ZeroTier (ztup, ztd, ztswitch) |
| `Dev/AI.code-profile` | VS Code профиль для AI-разработки |
| `Templates/` | Шаблоны HTML, JS, Python, README |
| `themes/` | Темы Ubuntu (BigSur, Graphite, Monterey, Ventoy-Dark) |
| `OpenRGB/` | Скрипты и конфиги для OpenRGB |
| `clean-sys.sh` | Глубокая очистка кэшей/корзины/журналов (симлинк `~/clean-sys.sh`, алиас `cls`) |
| `.local/bin/telegram-clean.sh` | Обёртка-лаунчер Telegram: после закрытия чистит кэш TG + корзину и прогоняет `fstrim` |
| `.local/share/applications/telegram-desktop_telegram-desktop.desktop` | Ярлык Telegram, запускающий обёртку вместо прямого `telegram-desktop` |

### `telegram-clean.sh` — автоочистка после Telegram

Обёртка над `/snap/bin/telegram-desktop`: запускает клиент, а после его полного закрытия

1. удаляет кэш Telegram (`cache`, `media_cache`, `wvother`, `wvbots/cache`) — база аккаунта и эмодзи не трогаются;
2. опустошает пользовательскую корзину `~/.local/share/Trash`;
3. выполняет `sudo -n fstrim -v /` — физическое стирание свободных блоков на SSD, чтобы удалённое было невосстановимо.

Развёртывание: симлинк `~/.local/bin/telegram-clean.sh` → `$USER/.local/bin/telegram-clean.sh`
(каталог `~/.local/bin` уже в `$PATH` через `.bashrc`); ярлык в `~/.local/share/applications/`
переопределяет системный `telegram-desktop_telegram-desktop.desktop`. Лог: `~/.local/share/telegram-clean.log`.
Конфигурация через env: `CLEAN_TG_CACHE`, `EMPTY_TRASH`, `DO_FSTRIM`, `DRY_RUN`, `TELEGRAM_TDATA`, `TRASH_DIR`.

## ZeroTier (клиент)

Функции управления ZeroTier на локальной машине — в `~/.zt-functions.sh` (подключается через `.bashrc`/`.zshrc`):

| Команда | Описание |
|---------|----------|
| `ztup` | Включить ZeroTier, дождаться смены IP |
| `ztd` | Отключить ZeroTier |
| `ztswitch <id>` | Переключиться на другую сеть |
| `zt_vds_networks` | Показать сети с VDS-сервера |
| `ztjoin_vds` | Подключиться к сети с VDS-сервера |

### Конфиг-файлы ZeroTier

| Файл | Описание |
|------|----------|
| `~/.zt-network` | Текущая активная ZT-сеть |
| `~/.zt-gateway` | IP exit-ноды в сети |
| `~/.zt-server-ip` | Кэш публичных IP ZT-пиров |
| `~/.zt-vds-server` | Адрес VDS-сервера (`user@ip`) |

## Документация (doc/)

| Файл | Описание |
|------|----------|
| [vds.md](doc/vds.md) | VDS Orchestrator: команды, архитектура, topology.json |
| [PROGRAM_DESCRIPTIONS.md](doc/PROGRAM_DESCRIPTIONS.md) | Описания устанавливаемых программ |
| [v2raya_guide.md](doc/v2raya_guide.md) | Руководство по v2rayA |
| [antigravity-isolation.md](doc/antigravity-isolation.md) | Изолированное окружение AI-агента |
| [local-proxy-tunnel.md](doc/local-proxy-tunnel.md) | Документация по локальному прокси (обход VPN) |

## Быстрый старт

```bash
# 1. Клонировать репозиторий
git clone https://github.com/asvspb/ubuntuInstaller.git ~/Dev/ubuntuInstaller

# 2. Настроить локальную машину (запускать по порядку)
cd ~/Dev/ubuntuInstaller/scripts
sudo bash 1_ubuntuStart.sh
sudo bash 2_ubuntuDocker.sh   # затем перезагрузка
sudo bash 3_ubuntuPack.sh
bash 6_zerotier-client.sh

# 3. Инициализировать новый VDS-сервер
bash 8_vds-server.sh
```

## Лицензия

MIT