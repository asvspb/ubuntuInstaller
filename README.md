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
│   ├── 8_vds-server.sh         # Инициализация VDS через VDS Orchestrator
│   └── 9_local-proxy-tunnel.sh # Локальный SOCKS5/Nginx туннель
├── $USER/                      # Пользовательские dotfiles и конфигурации
│   ├── .bashrc / .zshrc        # Расширенная shell-конфигурация с алиасами
│   ├── .zt-functions.sh        # Функции управления ZeroTier (ztup, ztd, ztswitch)
│   ├── clean-sys.sh            # Глубокая очистка системы (алиас cls)
│   ├── .local/bin/telegram-clean.sh # Автоочистка кэша Telegram + TRIM SSD
│   ├── .config/                # Настройки Guake, CopyQ, OpenRGB, GTK, dconf
│   └── themes/ / .fonts/       # Темы оформления и шрифты
└── vds/                        # VDS Orchestrator (Python CLI для управления серверами)
```

---

## ⚡ Встроенные системные оптимизации (`5_systemOptimizations.sh`):

1. **Тюнинг памяти (`vm.swappiness = 10`, `vm.vfs_cache_pressure = 50`):** Максимальный приоритет быстрой RAM (32 ГБ), кэш файлов удерживается в памяти.
2. **Лимиты разработчика (`fs.inotify.max_user_watches = 524288`):** Защита от ошибок переполнения вотчеров в VS Code, Vite, React и Webpack.
3. **Google TCP BBR:** Ускорение сетевого стека и снижение пинга/задержек через VPN и ZeroTier.
4. **SSD TRIM (`fstrim.timer`):** Автоматическое еженедельное обслуживание блоков SSD для сохранения пиковой скорости.
5. **Ротация логов Docker:** Защита диска от разрастания контейнерных логов (`max-size: 50m`).
6. **Ограничение системных логов:** Фиксация размера `systemd-journald` до 250 МБ.
7. **Отключение Apport:** Устранение назойливых всплывающих краш-репортов.
