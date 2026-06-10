# VDS Orchestrator

> Универсальный Python CLI для управления VDS/VPS на Ubuntu 24.04

Версия: **3.0** | Входит в [ubuntuInstaller](../) как модуль `vds/`

## Что это

VDS Orchestrator — единый инструмент инициализации и управления выделенным сервером. Написан на Python, управляется через интерактивное TUI-меню или командную строку.

**Возможности:**
- Интерактивное TUI-меню (questionary + rich)
- ZeroTier + ZTNET Panel (Docker): установка, watchdog, reconciler топологии
- WireGuard VPN, Coturn TURN-сервер
- Системный dashboard: CPU, RAM, диск, Docker-контейнеры
- Первичная настройка: пользователь, SSH, UFW, Docker, Swap

## Структура модуля

```
vds/
├── install.sh              # Bootstrap-установщик (запускается на сервере)
├── preinstall.sh           # Базовая подготовка сервера
├── requirements.txt        # Python-зависимости
├── pyproject.toml          # Метаданные пакета
├── systemd/                # Systemd unit-файлы
│   ├── vds-watchdog.service / .timer
│   └── vds-reconcile.service / .timer
└── src/
    ├── main.py             # Точка входа CLI (typer + questionary)
    ├── core/               # Ядро: логгер, shell-утилиты, блокировки
    ├── zerotier/           # ZeroTier: установка, watchdog, reconcile, NAT, диагностика
    ├── wireguard/          # WireGuard VPN
    ├── coturn/             # TURN-сервер для WebRTC
    ├── sysinfo/            # Dashboard метрик сервера
    └── system/             # Базовая настройка, cleanup
```

## Быстрый старт

### С локальной машины (рекомендуется)

```bash
bash ~/Dev/ubuntuInstaller/scripts/8_vds-server.sh
```

Скрипт автоматически:
1. Запросит IP и пользователя сервера
2. Скопирует SSH-ключ (попросит пароль один раз)
3. Синхронизирует локальные исходники `vds/` на сервер через rsync
4. Запустит `install.sh` на сервере
5. Откроет интерактивное меню `vds`

### Напрямую на сервере

```bash
# Опционально — базовая подготовка
curl -fsSL https://raw.githubusercontent.com/asvspb/my-first-vds/main/preinstall.sh | sudo bash

# Установка
curl -fsSL https://raw.githubusercontent.com/asvspb/my-first-vds/main/install.sh | sudo bash
```

## Команды CLI

```bash
vds                              # Интерактивное TUI-меню
vds --help                       # Справка

# Системная информация
vds sysinfo                      # Dashboard: CPU, RAM, диск, Docker

# ZeroTier
vds zerotier install             # Установить ZeroTier + ZTNET Panel
vds zerotier watchdog            # Проверить/восстановить туннели
vds zerotier reconcile           # Синхронизировать топологию (dry-run)
vds zerotier reconcile --init    # Инициализировать topology.json с текущим состоянием
vds zerotier reconcile --apply   # Применить изменения
vds zerotier diagnose            # Диагностика сети
vds zerotier add-network         # Добавить сеть в topology.json

# WireGuard
vds wireguard install            # Установить WireGuard VPN

# Управление сервером
vds server-setup                 # Мастер первичной настройки
vds cleanup                      # Очистка (логи, кэши, старые Docker-слои)
```

## Systemd-таймеры

После установки ZeroTier автоматически активируются фоновые таймеры:

| Таймер | Описание | Интервал |
|--------|----------|----------|
| `vds-watchdog.timer` | Мониторинг и авто-восстановление ZeroTier-туннелей | каждые 2 мин |
| `vds-reconcile.timer` | Декларативная синхронизация с `topology.json` | каждые 5 мин |

```bash
# Проверка
systemctl list-timers vds-*
journalctl -u vds-watchdog -n 30
journalctl -u vds-reconcile -n 30
```

## topology.json

Файл `/opt/ztnet/topology.json` — декларативное описание желаемого состояния ZeroTier-сетей.
Reconciler сравнивает его с реальным состоянием через ZTNET API и устраняет расхождения.

```json
{
  "networks": {
    "<network_id>": {
      "name": "название сети",
      "members": {
        "<node_id>": {
          "name": "имя узла",
          "authorized": true
        }
      }
    }
  }
}
```

## Пути установки на сервере

| Путь | Содержимое |
|------|-----------|
| `/opt/my-vds/` | Исходники проекта |
| `/opt/my-vds/venv/` | Python виртуальное окружение |
| `/opt/ztnet/topology.json` | Конфиг топологии ZeroTier |
| `/usr/local/bin/vds` | Исполняемая команда `vds` |
| `/etc/systemd/system/vds-*.service` | Systemd-сервисы |
| `/etc/systemd/system/vds-*.timer` | Systemd-таймеры |

## Зависимости Python

| Библиотека | Назначение |
|------------|------------|
| `typer` | CLI-фреймворк |
| `questionary` | Интерактивное TUI-меню |
| `rich` | Красивый вывод в терминале |
| `psutil` | Системные метрики |
| `pydantic` | Валидация конфигов |
| `requests` | HTTP API к ZTNET |

## Разработка

Для добавления новой команды:
1. Создать модуль в `src/<category>/<module>.py`
2. Зарегистрировать в `src/main.py` через `@app.command()` или `@<category>_app.command()`
3. При необходимости добавить пункт в функцию `main_menu()`

## Требования

- Ubuntu 24.04 LTS
- Root-доступ (sudo)
- Интернет (Python 3.10+ и зависимости устанавливаются автоматически)
- Docker (для ZeroTier/ZTNET)
