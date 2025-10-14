#!/bin/bash

# Роль: 10-dev-tools
# Назначение: Установка и настройка инструментов разработчика

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция установки инструментов разработчика
install_dev_tools() {
  log "INFO" "Установка инструментов разработчика"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Установка инструментов разработчика (не выполнена)"
    return 0
  fi

  # Получение переменных роли
  local install_vscode=$(echo "$UBUNTU_INSTALLER_ROLE_VARS" | yq '.install_vscode // true' 2>/dev/null || echo "true")
  local install_pycharm=$(echo "$UBUNTU_INSTALLER_ROLE_VARS" | yq '.install_pycharm // false' 2>/dev/null || echo "false")

  # Установка базовых инструментов разработки
  log "INFO" "Установка базовых инструментов разработки"
  local dev_packages="build-essential cmake gcc g++ gdb valgrind git git-lfs gh python3 python3-pip python3-venv python3-dev default-jdk"
  apt install -y $dev_packages

  # Установка VSCode если включено
  if [ "$install_vscode" = "true" ]; then
    log "INFO" "Установка Visual Studio Code"
    if ! is_pkg_installed "code"; then
      # Добавление ключа и репозитория для VSCode
      wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor >packages.microsoft.gpg
      sudo install -D -o root -g root -m 644 packages.microsoft.gpg /etc/apt/keyrings/packages.microsoft.gpg
      sh -c 'echo "deb [arch=amd64,arm64,armhf signed-by=/etc/apt/keyrings/packages.microsoft.gpg] https://packages.microsoft.com/repos/code stable main" > /etc/apt/sources.list.d/vscode.list'
      apt update
      apt install -y code
    else
      log "INFO" "Visual Studio Code уже установлен"
    fi
  else
    log "INFO" "Установка Visual Studio Code пропущена согласно конфигурации"
  fi

  # Установка PyCharm если включено
  if [ "$install_pycharm" = "true" ]; then
    log "INFO" "Установка PyCharm Community Edition"
    if ! snap list | grep -q "^pycharm-community "; then
      snap install pycharm-community --classic
    else
      log "INFO" "PyCharm Community Edition уже установлен"
    fi
  else
    log "INFO" "Установка PyCharm пропущена согласно конфигурации"
  fi

  # Установка других инструментов через pipx
  log "INFO" "Установка инструментов через pipx"

  # Проверяем, установлены ли уже инструменты
  if ! pipx list | grep -q "black"; then
    pipx install black
    log "INFO" "black установлен через pipx"
  else
    log "INFO" "black уже установлен через pipx"
  fi

  if ! pipx list | grep -q "flake8"; then
    pipx install flake8
    log "INFO" "flake8 установлен через pipx"
  else
    log "INFO" "flake8 уже установлен через pipx"
  fi

  if ! pipx list | grep -q "autopep8"; then
    pipx install autopep8
    log "INFO" "autopep8 установлен через pipx"
  else
    log "INFO" "autopep8 уже установлен через pipx"
  fi
}

# Основная функция выполнения роли
main() {
  log "INFO" "Запуск роли: 10-dev-tools"

  # Выполнение установки инструментов разработчика
  install_dev_tools

  log "INFO" "Роль 10-dev-tools завершена"
}

# Вызов основной функции
main "$@"
