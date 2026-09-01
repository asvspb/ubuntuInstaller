# 🛠️ Ubuntu Workstation Installer & Environment Engine

Комплексный набор скриптов и конфигураций для автоматического развертывания рабочего окружения разработчика на **Ubuntu 24.04 / 22.04 LTS** и управления VDS-серверами.

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
├── install.sh             # 🚀 Главный интерактивный диспетчер установки
├── scripts/               # Модульные установочные скрипты
│   ├── 1_ubuntuStart.sh   # Базовая система: sudo без пароля, UTC-часы, Chrome, Telegram
│   ├── 2_ubuntuDocker.sh  # Docker Engine, Compose, Lazydocker
│   ├── 3_ubuntuPack.sh    # VS Code, Antigravity, NVM/Node, Python (pipx), fastfetch, CLI
│   ├── 4_snap-apps.sh     # Snap-пакеты (Obsidian, VLC, Chromium и др.)
│   ├── deploy-dotfiles.sh # 👤 Автоматическое развертывание $USER/ -> $HOME
│   ├── 5_samsung-printer-driver.sh # Драйверы принтеров Samsung
│   ├── 6_zerotier-client.sh        # ZeroTier клиент и управление сетями
│   ├── 7_v2raya-proxy.sh           # v2rayA веб-интерфейс прокси
│   ├── 8_vds-server.sh             # Инициализация VDS через VDS Orchestrator
│   └── 9_local-proxy-tunnel.sh     # Локальный SOCKS5/Nginx туннель
├── $USER/                 # Пользовательские dotfiles и конфигурации
│   ├── .bashrc / .zshrc   # Расширенная shell-конфигурация с алиасами
│   ├── .zt-functions.sh   # Функции управления ZeroTier (ztup, ztd, ztswitch)
│   ├── clean-sys.sh       # Глубокая очистка системы (алиас cls)
│   ├── .local/bin/telegram-clean.sh # Автоочистка кэша Telegram + TRIM SSD
│   ├── .config/           # Настройки Guake, CopyQ, OpenRGB, GTK, dconf
│   └── themes/ / .fonts/  # Темы оформления и шрифты
└── vds/                   # VDS Orchestrator (Python CLI для управления серверами)
```

---

## 🌟 Что было оптимизировано:

1. 🟢 **Полная совместимость с Ubuntu 24.04 LTS (Noble Numbat):**
   * Удалены устаревшие/несовместимые PPA (`grub-customizer`).
   * Настройка Python переведена на стандарт **PEP 668 / `pipx`** (исключены ошибки `externally-managed-environment`).
   * Заменены заброшенные утилиты: `neofetch` ➔ **`fastfetch`**, `exa` ➔ **`eza`**.
2. 🟢 **Автоматизация переноса Dotfiles (`deploy-dotfiles.sh`):**
   * Все ваши конфигурации из `$USER/` теперь сами раскладываются по `~/.config`, `~/.local/bin`, `~/.local/share/applications` с созданием резервной копии старых файлов.
3. 🟢 **Единый интерактивный запуск (`install.sh`):**
   * Установка всех шагов одной командой без необходимости вручную запускать каждый скрипт.
4. 🟢 **Чистый код без дублирования:**
   * Оптимизированы вызовы `apt update`, убраны лишние повторы в настройках `sudoers` и `needrestart`.
