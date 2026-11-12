# 🛠️ Ubuntu Workstation Installer & Optimization Engine

Комплексный набор скриптов и конфигураций для автоматического развертывания и глубокой оптимизации рабочего окружения разработчика на **Ubuntu 24.04 / 22.04 LTS** и управления VDS-серверами.

---

## ⚡ Быстрый старт

Запустите единый мастер установки:
```bash
bash ~/Dev/ubuntuInstaller/install.sh
```
В интерактивном меню можно выбрать **полную установку «под ключ»** либо нужные компоненты по отдельности.

---

## 📁 Структура проекта

```
ubuntuInstaller/
├── install.sh                  # 🚀 Главный интерактивный диспетчер установки
├── scripts/                    # Модульные установочные скрипты
│   ├── 1_ubuntuStart.sh        # Базовая система: sudo без пароля, UTC-часы, Chrome, Telegram
│   ├── 2_ubuntuDocker.sh       # Docker Engine, Compose, Lazydocker
│   ├── 3_ubuntuPack.sh         # VS Code, Antigravity, NVM/Node, Python (pipx), fastfetch, CLI
│   ├── 4_snap-apps.sh          # Snap-пакеты (Obsidian, VLC, Chromium и др.)
│   ├── 5_systemOptimizations.sh# ⚡ Системный тюнинг ядра (RAM, BBR, inotify, SSD TRIM, логи)
│   ├── deploy-dotfiles.sh      # 👤 Автоматическое развертывание $USER/ -> $HOME
│   ├── 5_samsung-printer-driver.sh # Драйверы принтеров Samsung
│   ├── 6_zerotier-client.sh    # ZeroTier клиент и управление сетями
│   ├── 7_v2raya-proxy.sh       # v2rayA веб-интерфейс прокси
│   ├── 8_vds-server.sh         # Инициализация VDS через VDS Orchestrator (VDS_REPO)
│   ├── 9_local-proxy-tunnel.sh # Локальный SOCKS5/Nginx туннель
│   ├── 10_vpn-manager.sh       # Установка vpn-manager (бинари xray/hysteria, pipx, smoke-тест)
│   ├── 11_vds-deploy.sh        # One-shot деплой vds на сервер (VDS_REPO, rsync + install.sh + share-линки)
│   ├── 12_zapret.sh            # Обход DPI: zapret/nfqws (канал 0, без прокси)
│   ├── 13_dns-doh.sh           # DoH-резолвер cloudflared (127.0.0.1:5053)
│   ├── 14_awg-split.sh         # AmneziaWG split-tunnel
│   ├── 15_domain-split.sh      # Доменный split: dnsmasq + nft + fwmark
│   ├── 20_vds-migrate.sh       # Перенос state.json + secrets + сертификатов между серверами
│   └── vpn-manager/            # 🔐 VPN-менеджер клиента (vpnm): Xray/Hysteria2, TUI (doc/vpn-manager.md)
├── $USER/                      # Пользовательские dotfiles и конфигурации
│   ├── .bashrc / .zshrc        # Расширенная shell-конфигурация с алиасами
│   ├── .zt-functions.sh        # Функции управления ZeroTier (ztup, ztd, ztswitch)
│   ├── clean-sys.sh            # Глубокая очистка системы (алиас cls)
│   ├── .local/bin/telegram-clean.sh # Автоочистка кэша Telegram + TRIM SSD
│   ├── .config/                # Настройки Guake, CopyQ, OpenRGB, GTK, dconf
│   ├── .var/ / .local/         # Ptyxis (терминал Ubuntu 26), палитры Royal blue/sky, ярлыки
│   └── themes/ / .fonts/       # Темы оформления и шрифты
└── doc/                        # vpn-manager.md, vds.md, PROGRAM_DESCRIPTIONS.md и гайды
```

> Серверная часть (`vds`) живёт в отдельном репозитории
> [my-first-vds](https://github.com/asvspb/my-first-vds) — здесь её копии нет
> (vendored `vds/` удалён 2026-11-12; `8_vds-server.sh` и `11_vds-deploy.sh`
> берут рабочий репозиторий, путь переопределяется через `VDS_REPO`).

---

## ⚡ Встроенные системные оптимизации (`5_systemOptimizations.sh`):

1. **Тюнинг памяти (`vm.swappiness = 10`, `vm.vfs_cache_pressure = 50`):** Максимальный приоритет быстрой RAM (32 ГБ), кэш файлов удерживается в памяти.
2. **Лимиты разработчика (`fs.inotify.max_user_watches = 524288`):** Защита от ошибок переполнения вотчеров в VS Code, Vite, React и Webpack.
3. **Google TCP BBR:** Ускорение сетевого стека и снижение пинга/задержек через VPN и ZeroTier.
4. **SSD TRIM (`fstrim.timer`):** Автоматическое еженедельное обслуживание блоков SSD для сохранения пиковой скорости.
5. **Ротация логов Docker:** Защита диска от разрастания контейнерных логов (`max-size: 50m`).
6. **Ограничение системных логов:** Фиксация размера `systemd-journald` до 250 МБ.
7. **Отключение Apport:** Устранение назойливых всплывающих краш-репортов.
8. **Защита USB-периферии от зависания при сне:** Разрешение пробуждения (wakeup) и отключение агрессивного autosuspend для мыши и USB-хабов (защита от ошибки `-71` при отключении монитора).
9. **Смена раскладки клавиатуры (Left Alt + Space и Right Alt):**
   - Комбинация **Left Alt + Space** переключает раскладку (системное меню окна `<Alt>space` отключено).
   - Одиночное нажатие **Right Alt** переключает раскладку с OSD-индикатором через связку `xcape` (`Alt_R=XF86Launch9`).
   - Клавиша **Left Ctrl** полностью освобождена и используется исключительно как стандартный модификатор.
10. **Непрерывный TRIM и гарантированное удаление данных:** Опция монтирования `discard` в `/etc/fstab` для немедленного аппаратного зануления блоков SSD при удалении файлов. Консольная утилита `wipe` и интеграция в контекстное меню проводника Nautilus («Сценарии» → «Уничтожить без восстановления (Wipe)») для безвозвратного уничтожения данных (перезапись шумом + нулями + аппаратный NVMe TRIM).

