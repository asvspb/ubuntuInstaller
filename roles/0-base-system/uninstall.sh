#!/bin/bash

# Скрипт удаления для роли 0-base-system
# Удаляет пакеты и откатывает настройки, установленные в main.sh

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция удаления базовой системы
uninstall_base_system() {
  log "INFO" "Удаление компонентов базовой системы"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Удаление компонентов базовой системы (не выполнено)"
    return 0
  fi

  # Удаление пакетов, установленных в main.sh
  local base_packages="git gh mc tmux zsh mosh curl wget ca-certificates net-tools make apt-transport-https gpg gnupg ubuntu-restricted-extras ncdu ranger btop iftop htop neofetch rpm wireguard jq pipx inxi cpu-x tldr fzf alacarte grub-customizer gparted synaptic nala"

  # Удаление GUI-зависимых пакетов (если они были установлены)
  local system_type=$(detect_system_type)
  if [ "$system_type" != "server" ] && [ "$system_type" != "WSL" ]; then
    base_packages="$base_packages dconf-editor gnome-shell-extensions gnome-tweaks guake copyq xclip openrgb ufw timeshift"
  fi

  # Удаление пакетов
  log "INFO" "Удаление базовых пакетов"
  apt remove -y --purge $base_packages

  # Удаление snap пакетов
  if [ "$system_type" != "WSL" ]; then
    log "INFO" "Удаление базовых Snap пакетов"
    if snap list | grep -q "^telegram-desktop "; then
      sudo snap remove telegram-desktop
    fi
  fi

  # Удаление настроек sudo без пароля (если файл существует)
  if [ -f /etc/sudoers.d/90-nopasswd ]; then
    log "INFO" "Удаление настроек sudo без пароля"
    sudo rm /etc/sudoers.d/90-nopasswd
  fi

  # Удаление настроек SSH
  if [ -f ~/.ssh/config ]; then
    log "INFO" "Удаление настроек SSH"
    rm ~/.ssh/config
  fi

  log "INFO" "Компоненты базовой системы удалены"
}

# Основная функция выполнения удаления
main() {
  log "INFO" "Запуск удаления роли: 0-base-system"

  # Выполнение удаления базовой системы
  uninstall_base_system

  log "INFO" "Удаление роли 0-base-system завершено"
}

# Вызов основной функции
main "$@"
