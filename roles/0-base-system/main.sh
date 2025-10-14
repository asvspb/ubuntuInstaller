#!/bin/bash

# Роль: 00-base-system
# Назначение: Установка базовой системы и настройка основных параметров

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция установки базовой системы
install_base_system() {
  log "INFO" "Установка базовой системы"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Обновление списка пакетов (не выполнено)"
    log "INFO" "[DRY-RUN] Установка базовых пакетов (не выполнена)"
    return 0
  fi

  # Обновление списка пакетов
  log "INFO" "Обновление списка пакетов"
  apt update

  # Определение типа системы
  local system_type=$(detect_system_type)
  log "INFO" "Тип системы: $system_type"

  # Установка базовых пакетов
  log "INFO" "Установка базовых пакетов"
  local base_packages="git gh mc tmux zsh mosh curl wget ca-certificates net-tools make apt-transport-https gpg gnupg ubuntu-restricted-extras ncdu ranger btop iftop htop neofetch rpm wireguard jq pipx inxi cpu-x tldr fzf alacarte grub-customizer gparted synaptic nala"

  # Условная установка пакетов в зависимости от типа системы
  if [ "$system_type" != "server" ] && [ "$system_type" != "WSL" ]; then
    # Добавляем GUI-зависимые пакеты для десктопных систем
    base_packages="$base_packages dconf-editor gnome-shell-extensions gnome-tweaks guake copyq xclip openrgb ufw timeshift"
  fi

  apt install -y $base_packages

  # Установка snap пакетов (только не для WSL)
  if [ "$system_type" != "WSL" ]; then
    log "INFO" "Установка базовых Snap пакетов"
    ensure_snap_pkg telegram-desktop
  else
    log "INFO" "Пропуск установки Snap пакетов для WSL"
  fi
}

# Функция установки Chrome и lazydocker
install_additional_tools() {
  log "INFO" "Установка дополнительных инструментов (Chrome и lazydocker)"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Установка Chrome и lazydocker (не выполнена)"
    return 0
  fi

  # Определение типа системы
  local system_type=$(detect_system_type)

  # Установка Chrome (только для десктопных систем)
  if [ "$system_type" != "server" ] && [ "$system_type" != "WSL" ]; then
    log "INFO" "Установка Google Chrome из официального репозитория"

    # Добавление GPG-ключа Google
    if [ ! -f /etc/apt/keyrings/google-chrome.gpg ]; then
      log "INFO" "Добавление GPG-ключа Google"
      wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg
    fi

    # Добавление репозитория Google Chrome
    local repo_line="deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main"
    if ! grep -q "dl.google.com/linux/chrome/deb/" /etc/apt/sources.list.d/google-chrome.list 2>/dev/null; then
      log "INFO" "Добавление репозитория Google Chrome"
      echo "$repo_line" | sudo tee /etc/apt/sources.list.d/google-chrome.list
    fi

    # Обновление списка пакетов и установка Chrome
    sudo apt update
    sudo apt install -y google-chrome-stable
  else
    log "INFO" "Пропуск установки Google Chrome для $system_type"
  fi

  # Установка lazydocker (только если Docker установлен)
  if command -v docker &>/dev/null; then
    log "INFO" "Установка lazydocker"

    # Получение последней версии lazydocker
    local lazydocker_version=$(curl -s "https://api.github.com/repos/jesseduffield/lazydocker/releases/latest" | grep -Po '"tag_name": "v\K[0-9.]+')

    if [ -n "$lazydocker_version" ]; then
      local lazydocker_url="https://github.com/jesseduffield/lazydocker/releases/latest/download/lazydocker_${lazydocker_version}_Linux_x86_64.tar.gz"
      local temp_dir=$(mktemp -d)

      # Скачивание и установка lazydocker
      curl -Lo "$temp_dir/lazydocker.tar.gz" "$lazydocker_url"
      tar xf "$temp_dir/lazydocker.tar.gz" -C "$temp_dir"
      sudo install "$temp_dir/lazydocker" /usr/local/bin
      rm -rf "$temp_dir"

      log "INFO" "lazydocker версии $lazydocker_version установлен"
    else
      log "WARN" "Не удалось получить версию lazydocker"
    fi
  else
    log "INFO" "Docker не установлен, пропуск установки lazydocker"
  fi
}

# Функция настройки безопасности
setup_security() {
  log "INFO" "Настройка базовой безопасности"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Настройка безопасности (не выполнена)"
    return 0
  fi

  # Включение брандмауэра UFW
  ufw enable

  # Настройка автоматических обновлений
  apt install -y unattended-upgrades
  dpkg-reconfigure -plow unattended-upgrades
}

# Функция настройки sudo без пароля (опционально)
setup_sudo_nopasswd() {
  log "INFO" "Настройка sudo без пароля"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Настройка sudo без пароля (не выполнена)"
    return 0
  fi

  # Получаем настройку из переменных роли
  local set_nopasswd=$(echo "$UBUNTU_INSTALLER_ROLE_VARS" | yq '.settings.set_nopasswd // true' 2>/dev/null || echo "true")

  # Проверяем, нужно ли устанавливать nopasswd
  if [ "$set_nopasswd" != "true" ] && [ "$set_nopasswd" != "1" ]; then
    log "INFO" "Пропуск настройки sudo без пароля по настройкам конфигурации"
    return 0
  fi

  # Проверка существования записи в sudoers.d
  if ! grep -q "${USER} ALL=(ALL) NOPASSWD:ALL" /etc/sudoers.d/90-nopasswd 2>/dev/null; then
    # Создание файла sudoers.d для пользователя
    echo "${USER} ALL=(ALL) NOPASSWD:ALL" | tee /etc/sudoers.d/90-nopasswd
    chmod 0440 /etc/sudoers.d/90-nopasswd
    log "INFO" "Запись для ${USER} добавлена в /etc/sudoers.d/90-nopasswd"
  else
    log "INFO" "Запись для ${USER} уже существует в /etc/sudoers.d/90-nopasswd"
  fi
}

# Функция настройки системных параметров
setup_system_settings() {
  log "INFO" "Настройка системных параметров"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Настройка системных параметров (не выполнена)"
    return 0
  fi

  # Установка времени - только если не установлена нужная настройка
  if ! timedatectl status | grep -q "RTC in local TZ: yes"; then
    timedatectl set-local-rtc 1 --adjust-system-clock
    log "INFO" "Установлена настройка RTC в локальном часовом поясе"
  else
    log "INFO" "Настройка RTC в локальном часовом поясе уже установлена"
  fi

  # Настройка параметров GNOME
  if command -v gsettings &>/dev/null; then
    gsettings set org.gnome.shell.extensions.dash-to-dock click-action 'minimize'
  fi

  # Настройка параметров перезапуска служб
  export DEBIAN_FRONTEND=noninteractive

  # Получаем настройку из переменных роли
  local tune_needrestart=$(echo "$UBUNTU_INSTALLER_ROLE_VARS" | yq '.settings.tune_needrestart // true' 2>/dev/null || echo "true")

  # Проверяем, нужно ли настраивать needrestart
  if [ "$tune_needrestart" = "true" ] || [ "$tune_needrestart" = "1" ]; then
    if [ -f /etc/needrestart/needrestart.conf ]; then
      # Проверяем, не закомментирована ли уже строка и не установлена ли нужная настройка
      if ! grep -q '^\$nrconf{restart} = '\''a'\'';' /etc/needrestart/needrestart.conf; then
        sed -i '/\$nrconf{restart}/s/^#//g' /etc/needrestart/needrestart.conf
        sed -i "/nrconf{restart}/s/'i'/'a'/g" /etc/needrestart/needrestart.conf
        log "INFO" "Настройка автоматического перезапуска служб обновлена"
      else
        log "INFO" "Настройка автоматического перезапуска служб уже установлена"
      fi
    else
      mkdir -p /etc/needrestart
      echo '$nrconf{restart} = '\''a'\'';' >/etc/needrestart/needrestart.conf
      log "INFO" "Файл настроек needrestart создан"
    fi
  else
    log "INFO" "Пропуск настройки needrestart по настройкам конфигурации"
  fi

  # Настройка SSH
  # Получаем настройку из переменных роли
  local ssh_relax_strict_hostkey=$(echo "$UBUNTU_INSTALLER_ROLE_VARS" | yq '.settings.ssh_relax_strict_hostkey // false' 2>/dev/null || echo "false")

  # Проверяем, нужно ли настраивать relaxed SSH
  if [ "$ssh_relax_strict_hostkey" = "true" ] || [ "$ssh_relax_strict_hostkey" = "1" ]; then
    mkdir -p ~/.ssh
    chmod 0700 ~/.ssh
    cat <<EOF >~/.ssh/config
Host gitlab.com
	 StrictHostKeyChecking no
	 UserKnownHostsFile=/dev/null
EOF
    log "INFO" "Настройка relaxed SSH для gitlab.com применена"
  else
    log "INFO" "Пропуск настройки relaxed SSH по настройкам конфигурации"
  fi
}

# Основная функция выполнения роли
main() {
  log "INFO" "Запуск роли: 00-base-system"

  # Выполнение установки базовой системы
  install_base_system

  # Установка дополнительных инструментов (Chrome и lazydocker)
  install_additional_tools

  # Настройка безопасности
  setup_security

  # Настройка sudo без пароля
  setup_sudo_nopasswd

  # Настройка системных параметров
  setup_system_settings

  log "INFO" "Роль 00-base-system завершена"
}

# Вызов основной функции
main "$@"
