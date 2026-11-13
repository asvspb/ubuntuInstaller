# VDS Orchestrator

> Универсальный Python CLI для управления VDS/VPS на Ubuntu 24.04

Версия: **3.0** | Отдельный репозиторий
[my-first-vds](https://github.com/asvspb/my-first-vds) — серверная часть связки
с ubuntuInstaller. Vendored-копии в этом репозитории нет: деплой
(`scripts/8_vds-server.sh`, `scripts/11_vds-deploy.sh`) берёт рабочий checkout
(`VDS_REPO`, по умолчанию `~/Dev/my-coding/vds`).

## Что это

VDS Orchestrator — единый инструмент инициализации и управления выделенным
сервером. Управляется через интерактивное TUI-меню или командную строку.

**Основной сценарий — gateway-протоколы против блокировок:**

- **Xray VLESS Reality** (TCP 443), **VLESS XHTTP** (инблэнд 8443 за
  SNI-фолбэком, ADR-003) и **VLESS+WS за CDN/Cloudflare** (origin 2053, ADR-006)
- **Hysteria2** (QUIC, UDP 443) с **port hopping** (`mport=`, ADR-008) и
  **obfs salamander** (`obfs=`, SPEC-20261112) — параллельный инстанс
- **AmneziaWG** (обфусцированный WireGuard, UDP 51825, ADR-005)
- **ACME/Let's Encrypt** по домену (HTTP-01, deploy-hook для Hysteria2)
- Общее хранилище клиентов `state.json` и `vds gateway links` для
  `vpn profile pull` на ноутбуке

**Архив/опционально:** ZeroTier + ZTNET Panel (Docker), WireGuard, Coturn,
системный dashboard, первичная настройка и очистка.

## Структура репозитория

```
vds/
├── install.sh              # Bootstrap-установщик (запускается на сервере)
├── preinstall.sh           # Базовая подготовка сервера
├── requirements.txt        # Python-зависимости
├── pyproject.toml          # Метаданные пакета (vds-cli)
├── systemd/                # Шаблоны юнитов
│   ├── vds-xray.service.tpl
│   ├── vds-hysteria2.service.tpl
│   ├── vds-hysteria2-hop.service.tpl     # port hopping (nat-цепочка, ADR-008)
│   ├── vds-hysteria2-obfs.service.tpl    # obfs salamander
│   ├── vds-amneziawg.service.tpl
│   ├── vds-watchdog.service / .timer     # ZeroTier (архив)
│   ├── vds-reconcile.service / .timer    # ZeroTier (архив)
│   └── sysctl/99-vds-gateway.conf.tpl
├── docs/                   # DEPLOY.md, VPN-PROTOCOLS.md, CDN-FRONTS.md,
│                           # CENSORSHIP-*.md, MIGRATE-VDS.md, TEAM.md, adr/
└── src/
    ├── main.py             # Точка входа CLI (typer + questionary)
    ├── core/               # logger, shell, lock, protocol, netcheck, secrets, units
    ├── xray/               # Reality / XHTTP / CDN
    ├── hysteria2/          # QUIC, hop, obfs, sysctl
    ├── amneziawg/          # AmneziaWG
    ├── acme/               # ACME/LE + deploy-hook
    ├── gateway/            # paths, store, links, blockcheck
    ├── zerotier/ wireguard/ coturn/   # архив/опционально
    ├── sysinfo/            # Dashboard (CPU, RAM, диск, Docker)
    └── system/             # server-setup, cleanup
```

Тесты: `tests/` (unit, без root/сети — `pytest -q`, 489 тестов).

## Быстрый старт

### С локальной машины (рекомендуется)

```bash
bash ~/Dev/ubuntuInstaller/scripts/11_vds-deploy.sh   # one-shot: всё сразу

# Полный набор протоколов + домен — через env (неинтерактивно):
VDS_PROTOCOLS=reality,hysteria2,obfs,hop,xhttp,cdn,awg \
VDS_DOMAIN=vpn.example.ru VDS_EMAIL=admin@example.ru \
    bash ~/Dev/ubuntuInstaller/scripts/11_vds-deploy.sh
```

One-shot деплой: SSH-ключ → rsync репо (`VDS_REPO`) → `install.sh` на сервере →
`vds gateway init --apply` (VDS_PROTOCOLS, по умолчанию `reality,hysteria2`;
при заданном домене — ACME/use-domain; доменные шаги ACME/XHTTP/CDN при DNS,
не указывающем на сервер, пропускаются с warning) → авто-детект GitHub
(при блокировке — зеркало) → демо-клиенты phone/mob/laptop → healthcheck-таймер →
печать share-links. Полная инструкция и чеклист — `docs/DEPLOY.md` в репозитории
`vds`.

Сервер без протоколов, только меню:

```bash
bash ~/Dev/ubuntuInstaller/scripts/8_vds-server.sh
```

Скрипт: запросит IP → скопирует SSH-ключ → синхронизирует рабочий checkout
(`VDS_REPO`) → запустит `install.sh` → откроет меню `vds`. `gateway/state.json`
живого сервера исключён из rsync.

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

# Системная информация / обслуживание
vds sysinfo                      # Dashboard: CPU, RAM, диск, Docker
vds server-setup                 # Мастер первичной настройки (SSH, UFW, swap)
vds cleanup                      # Очистка (логи, кэши, старые Docker-слои)
```

### Gateway: состояние и ссылки

```bash
vds gateway init                 # Идемпотентное развёртывание с нуля (dry-run: план + проверки)
vds gateway init --apply         # reality+hysteria2+healthcheck; --protocols obfs,hop,xhttp,cdn,awg — всё
vds gateway status               # state.json, клиенты, протоколы
vds gateway links                # Share-links всех клиентов (--protocol xray|hysteria2|amneziawg)
vds gateway links --include-obfs # + obfs-ссылки hysteria2 (`<name>@obfs`, порт obfs-инстанса)
vds gateway blockcheck           # «блок IP» vs «деградация» vs «локальная проблема»
```

### Xray (Reality / XHTTP / CDN)

```bash
vds xray install [--sni www.cloudflare.com]   # Reality, TCP 443 (маска по умолчанию www.google.com)
vds xray add-client <name>                    # Клиент + vless://...
vds xray add-transport                        # XHTTP-инблэнд за Reality-фолбэком (8443)
vds xray remove-transport                     # Снять XHTTP (инблэнд + XHTTP-клиенты)
vds xray use-domain --domain vpn.example.ru   # Reality-ссылки по домену (state.domain)
vds xray add-cdn --domain cdn.example.ru      # CDN-транспорт VLESS+WS+TLS (ADR-006)
vds xray add-cdn-client <name>                # CDN-клиент + ссылка (--front — фронт пула)
vds xray add-cdn-front <domain>               # Фронт в пул CDN (сертификат в инблэнде)
vds xray status                               # Юнит, валидность конфига, порт, journal
vds xray remove [--apply]                     # Удаление (dry-run по умолчанию)
```

### Hysteria2 (QUIC, hop, obfs, TLS)

```bash
vds hysteria2 install                         # QUIC UDP 443 (self-signed без домена)
vds hysteria2 add-client <name>               # Клиент + hysteria2://...
vds hysteria2 add-hop-ports 20000-20500        # port hopping (--apply; по умолчанию dry-run)
vds hysteria2 remove-hop-ports [--apply]      # Снять hop (живой udp/443 не трогается)
vds hysteria2 add-obfs [--port 51826]         # obfs salamander (параллельный инстанс)
vds hysteria2 rotate-obfs                     # Ротация пароля (клиентам — ре-импорт ссылки)
vds hysteria2 remove-obfs [--apply]           # Снять obfs-инстанс
vds hysteria2 apply-cert [--apply|--restore]  # self-signed ↔ LE (без флага — dry-run)
vds hysteria2 status                          # Юнит, UDP-порт, сертификат, journal
vds hysteria2 remove --apply                  # Удаление (sysctl-тюнинг не откатывается)
```

### AmneziaWG

```bash
vds amneziawg install                         # PPA ppa:amnezia/ppa, UDP 51825
vds amneziawg add-client <name>               # .conf (0600) для импорта в AmneziaVPN
vds amneziawg remove-client <name> [--apply]  # Удалить клиента (dry-run по умолчанию)
vds amneziawg status                          # Юнит, DKMS, UDP-порт, peers (секреты скрыты)
vds amneziawg remove [--apply]                # Удалить сервис (пакеты PPA — вручную)
```

### ACME

```bash
vds acme issue --domain vpn.example.ru --email admin@example.ru   # LE (HTTP-01) + deploy-hook
```

### ZeroTier / WireGuard / Coturn (архив)

```bash
vds zerotier install | watchdog | reconcile [--init|--apply] | diagnose | add-network
vds wireguard install
```

## Share-линки и клиентская сторона

`vds gateway links` печатает `имя: ссылка`; на ноутбуке
`vpn profile pull -H root@asv1 [--include-obfs]` импортирует их в vpnm
(store 0600). Скорость каналов после импорта — `vpn speed` (живая таблица,
несколько эндпоинтов; см. `doc/vpn-manager.md`). С `--include-obfs` сервер добавляет вторую строку
`<name>@obfs` — ссылку obfs-инстанса (salamander, отдельный UDP-порт).

- **Reality/XHTTP/CDN**: `vless://...` (CDN — `vless` за Cloudflare, имя профиля
  может быть вида `<name>@<front>`).
- **Hysteria2**: `hysteria2://<pw>@<host>:443/?sni=...&insecure=1` — без домена;
  с LE-сертификатом `insecure=1` не добавляется. Hop/obfs добавляются
  параметрами `mport=20000-20500` и `obfs=salamander&obfs-password=...`.
- **AmneziaWG**: `.conf` (0600) в `gateway/clients/amneziawg/`, забирается
  `vpn amneziawg add-client <name>` (ssh/scp → `~/.awg/`).

При приёмке — **серийные пробы с паузами** (инвариант И26): не запускать
`openssl s_client`/`ssh`/`curl` к одному и тому же SNI параллельно — серия
хендшейков сама вызывает behavioral-blackhole TSPU (~120 с). `vpn probe`
соблюдает это сам (пауза между замерами и профилями).

Секреты (UUID, пароли, приватные ключи) — только в серверных конфигах и
клиентском store; в лог и git не попадают. Порты TCP/UDP 443 (и 2053/51825
при использовании CDN/AWG) открываются в UFW.

## Systemd

Gateway-протоколы — статические сервисы, без таймеров:
`vds-xray.service`, `vds-hysteria2.service`, `vds-hysteria2-hop.service`
(oneshot: восстанавливает nat-цепочку после ребута), `vds-hysteria2-obfs.service`,
`vds-amneziawg.service`. Юниты ставятся только из шаблонов `systemd/*.tpl`.

Таймеры — только ZeroTier (архив, включаются `vds zerotier install`):

| Таймер | Описание | Интервал |
|---|---|---|
| `vds-watchdog.timer` | Мониторинг и авто-восстановление туннелей | каждые 2 мин |
| `vds-reconcile.timer` | Синхронизация с `topology.json` | каждые 5 мин |

```bash
systemctl list-timers vds-*
systemctl status vds-xray vds-hysteria2 vds-amneziawg
journalctl -u vds-hysteria2 -n 20
```

## Установочные пути

| Путь | Содержимое |
|---|---|
| `/opt/my-vds/` | Исходники и venv |
| `/opt/my-vds/gateway/state.json` | Клиенты и протоколы (0600) |
| `/opt/my-vds/gateway/clients/` | Клиентские артефакты (AWG `.conf`, 0600) |
| `/usr/local/etc/xray/config.json` | Конфиг Xray (0600) |
| `/etc/hysteria/config.yaml` | Конфиг Hysteria2 (0600) |
| `/etc/hysteria/obfs.yaml` | Конфиг obfs-инстанса (0600) |
| `/usr/local/sbin/vds-hy2-hop-apply.sh` | Идемпотентный hop-скрипт (`VDS_HY2_HOP`) |
| `/etc/amnezia/awg0.conf` | Конфиг AmneziaWG (0600) |
| `/etc/letsencrypt/live/<domain>/` | LE-сертификаты |
| `/etc/systemd/system/vds-*.service` | Сервисы |

## Документация репозитория vds

- `docs/DEPLOY.md` — деплой и чеклист проверки
- `docs/VPN-PROTOCOLS.md` — протоколы (включая §2.3.1 hop, §2.3.2 obfs, §2.3.3 TLS/ACME)
- `docs/CDN-FRONTS.md` — пул фронтов CDN
- `docs/MIGRATE-VDS.md` — перенос сервера
- `docs/adr/` — ADR-001…ADR-008
- `TECHDEBT.md` — бэклог
