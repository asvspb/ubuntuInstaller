# Ubuntu Installer Framework - Быстрый старт

Это краткое руководство поможет вам быстро начать работу с Ubuntu Installer Framework.

## Требования

- Ubuntu 22.04 LTS или 24.04 LTS
- Архитектура amd64
- Подключение к интернету

## Установка

1. Клонируйте репозиторий:
    ```bash
    git clone https://github.com/asvspb/ubuntuInstaller.git
    cd ubuntuInstaller
    ```

2. (Опционально) Настройте конфигурацию в `config.yaml`:
    ```yaml
    settings:
      non_interactive: true
      log_file: "/var/log/ubuntuInstaller/install.log"
    profile: "auto"  # Автоматическое определение профиля
    roles_enabled:
      - name: 0-base-system
      - name: 10-dev-tools
        vars:
          install_vscode: true
          install_pycharm: false
      - name: 20-docker
        enabled: true
    ```

3. Запустите установку:
    ```bash
    sudo ./install.sh
    # Или используя make
    sudo make install
    ```

## Основные команды

- `./install.sh install` - Установка компонентов
- `./install.sh uninstall` - Удаление компонентов
- `./install.sh update` - Обновление компонентов
- `./install.sh --dry-run` - Симуляция выполнения без изменений в системе

## Профили

Фреймворк поддерживает различные профили системы:
- `desktop-developer` - Полнофункциональная десктопная система для разработки
- `server` - Серверная система с минимальным набором компонентов
- `wsl` - Система для Windows Subsystem for Linux
- `auto` - Автоматическое определение профиля на основе характеристик системы

Вы можете указать профиль в конфигурационном файле или использовать один из предопределенных профилей:
    ```bash
    sudo ./install.sh -c profiles/desktop-developer.yaml
    sudo ./install.sh -c profiles/server.yaml
    sudo ./install.sh -c profiles/wsl.yaml
    ```

## Интерактивный режим

Фреймворк включает мини-TUI для интерактивного выбора профилей и ролей:
    ```bash
    sudo ./mini-tui.sh
    ```

Новое меню TUI включает следующие возможности:
- Установка компонентов с выбором профиля и ролей
- Удаление компонентов
- Обновление компонентов
- Создание и восстановление бекапов через Timeshift
- Удаление бекапов
- Ручное управление снапшотами (новая функция)
- Ручная установка пакетов
- Автоматическая установка
- Удаление установленных пакетов

## Дополнительная информация

Для получения подробной информации ознакомьтесь с полной документацией в файле `README.md`, инструкцией по управлению проектом через TUI-меню в файле `docs/TUI_MENU_GUIDE.md` и руководством по управлению снапшотами в файле `docs/TIMESHIFT_SNAPSHOT_MANAGEMENT.md`.