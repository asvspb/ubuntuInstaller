#!/bin/bash

# Роль: 50-dev-pack
# Назначение: Установка комплексного набора приложений для разработчиков

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция установки пакетов для разработчиков
install_dev_pack() {
  log "INFO" "Установка комплексного набора приложений для разработчиков"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Установка пакетов для разработчиков (не выполнена)"
    return 0
  fi

  # Установка пакетов через apt
  log "INFO" "Установка пакетов через apt"
  local dev_packages="vim htop glances tree jq wget curl git python3 python3-pip maven gradle code"
  install_packages "$dev_packages"

  # Установка pip-пакетов
  log "INFO" "Установка Python пакетов через pip"
  if command -v pip3 &>/dev/null; then
    pip3 install --upgrade pip --break-system-packages
    # Используем специальный скрипт для установки пакетов с обходом конфликта системными пакетами
    "$SCRIPT_DIR/scripts/python-pip-installer.sh" requests flask django numpy pandas matplotlib
  fi

  # Установка npm-пакетов
  log "INFO" "Установка Node.js пакетов через npm"

  # Проверяем, установлен ли NVM и доступен ли npm из NVM
  if [ -f "$HOME/.nvm/nvm.sh" ]; then
    # Активируем NVM в текущем контексте
    NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"

    if command -v npm &>/dev/null; then
      log "INFO" "Используем npm из NVM: $(npm --version)"
      npm install -g npm@latest
      npm install -g typescript @angular/cli
    else
      # Если активация NVM не помогла, пробуем использовать npm напрямую из NVM
      NVM_NPM_PATH="$HOME/.nvm/versions/node/v24.6.0/bin/npm"
      if [ -f "$NVM_NPM_PATH" ]; then
        log "INFO" "Используем npm напрямую из NVM: $($NVM_NPM_PATH --version)"
        $NVM_NPM_PATH install -g npm@latest
        $NVM_NPM_PATH install -g typescript @angular/cli
      else
        log "WARN" "npm не найден в NVM по пути $NVM_NPM_PATH"
      fi
    fi
  else
    # Если NVM не установлен, пробуем использовать npm напрямую из NVM (на случай, если он был установлен в другом месте)
    NVM_NPM_PATH="$HOME/.nvm/versions/node/v24.6.0/bin/npm"
    if [ -f "$NVM_NPM_PATH" ]; then
      log "INFO" "Используем npm напрямую из NVM: $($NVM_NPM_PATH --version)"
      $NVM_NPM_PATH install -g npm@latest
      $NVM_NPM_PATH install -g typescript @angular/cli
    else
      log "WARN" "npm не найден в системе (ни в NVM, ни напрямую)"
    fi
  fi

}

# Основная функция выполнения роли
main() {
  log "INFO" "Запуск роли: 50-dev-pack"

  # Выполнение установки пакетов для разработчиков
  install_dev_pack

  log "INFO" "Роль 50-dev-pack завершена"
}

# Вызов основной функции
main "$@"
