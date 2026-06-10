# VDS Orchestrator

> Универсальный Python CLI для управления VDS/VPS на Ubuntu 24.04

Версия: **3.0**

## Обзор

VDS Orchestrator — единый инструмент инициализации и управления выделенным сервером. Входит в состав [ubuntuInstaller](../) как модуль `vds/`.

**Возможности:**
- Интерактивное TUI-меню (на базе `questionary`)
- Управление ZeroTier + ZTNET Panel (Docker)
- Watchdog и декларативный reconciler топологии сетей
- WireGuard VPN, Coturn TURN-сервер
- Системный dashboard: CPU, RAM, диск, Docker
- Первичная настройка сервера: пользователь, SSH, UFW, Docker, Swap

## Структура

```
vds/
├── install.sh          # Bootstrap-установщик (запускать на сервере)
├── preinstall.sh       # Базовая подготовка сервера
├── local-init.sh       # Устарел — используй scripts/8_vds-server.sh
├── requirements.txt    # Python-зависимости
├── pyproject.toml      # Метаданные пакета
├── systemd/            # Systemd unit-файлы
│   ├── vds-watchdog.service/.timer
│   └── vds-reconcile.service/.timer
└── src/
    ├── main.py         # Точка входа CLI (typer + questionary)
    ├── core/           # Ядро: логгер, shell-утилиты, блокировки
    ├── zerotier/       # ZeroTier: установка, watchdog, reconcile, NAT, диагностика
    ├── wireguard/      # WireGuard VPN
    ├── coturn/         # TURN-сервер для WebRTC
    ├── sysinfo/        # Dashboard метрик сервера
    └── system/         # Базовая настройка, cleanup
```

## Быстрый старт

### С локальной машины (через ubuntuInstaller)

```bash
bash ~/Dev/ubuntuInstaller/scripts/8_vds-server.sh
```

Скрипт: запросит IP → скопирует SSH-ключ → загрузит исходники → установит → откроет меню `vds`.

### Напрямую на сервере

```bash
# Предварительная подготовка (опционально)
curl -fsSL https://raw.githubusercontent.com/asvspb/my-first-vds/main/preinstall.sh | sudo bash

# Установка
curl -fsSL https://raw.githubusercontent.com/asvspb/my-first-vds/main/install.sh | sudo bash
```

## Команды CLI

```bash
vds                          # Интерактивное TUI-меню
vds --help                   # Справка по всем командам
vds sysinfo                  # Dashboard: CPU, RAM, диск, Docker
vds zerotier install         # Установить ZeroTier + ZTNET Panel
vds zerotier watchdog        # Проверить/восстановить туннели
vds zerotier reconcile       # Синхронизировать топологию
vds zerotier reconcile --init    # Инициализировать topology.json
vds zerotier reconcile --apply   # Применить изменения
vds zerotier diagnose        # Диагностика сети
vds zerotier add-network     # Добавить сеть в topology.json
vds wireguard install        # Установить WireGuard VPN
vds server-setup             # Мастер первичной настройки
vds cleanup                  # Очистка системы (логи, кэши, Docker)
```

## Systemd-таймеры

После установки ZeroTier автоматически активируются:

| Таймер | Описание | Интервал |
|--------|----------|----------|
| `vds-watchdog.timer` | Мониторинг и авто-восстановление ZeroTier-туннелей | каждые 2 мин |
| `vds-reconcile.timer` | Декларативная синхронизация с `topology.json` | каждые 5 мин |

```bash
# Проверка статуса
systemctl list-timers vds-*
journalctl -u vds-watchdog -n 20
```

## topology.json

Файл `/opt/ztnet/topology.json` описывает желаемое состояние ZeroTier-сетей. Reconciler сравнивает его с реальным и устраняет расхождения.

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

## Установочные пути

| Путь | Содержимое |
|------|-----------|
| `/opt/my-vds/` | Исходники и venv |
| `/opt/my-vds/venv/` | Python виртуальное окружение |
| `/opt/ztnet/topology.json` | Конфиг топологии ZeroTier |
| `/usr/local/bin/vds` | Симлинк команды `vds` |
| `/etc/systemd/system/vds-*.service` | Systemd-сервисы |

## Зависимости

| Библиотека | Назначение |
|------------|------------|
| `typer` | CLI-фреймворк |
| `questionary` | Интерактивное TUI-меню |
| `rich` | Красивый вывод в терминале |
| `psutil` | Системные метрики |
| `pydantic` | Валидация конфигураций |
| `requests` | HTTP API к ZTNET |

## Разработка

Для добавления новых команд:
1. Создать модуль в `src/<category>/<module>.py`
2. Зарегистрировать в `src/main.py` через `@app.command()` или `@<category>_app.command()`
3. При необходимости добавить пункт в `main_menu()`

## Требования

- Ubuntu 24.04 LTS
- Root-доступ
- Python 3.10+ (устанавливается автоматически)
- Docker (для ZeroTier/ZTNET)

## Лицензия

MIT