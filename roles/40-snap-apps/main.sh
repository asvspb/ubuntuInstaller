#!/bin/bash

# Роль: 40-snap-apps
# Назначение: Установка рекомендуемых Snap приложений

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция установки snap приложений
install_snap_apps() {
  log "INFO" "Установка Snap приложений"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Установка Snap приложений (не выполнена)"
    return 0
  fi

  # Установка списка snap приложений из файла
  if [ -f "$SCRIPT_DIR/scripts/ubuntu_snap_packages.txt" ]; then
    while IFS= read -r app || [ -n "$app" ]; do
      # Пропускаем пустые строки и комментарии
      if [[ -n "$app" && ! "$app" =~ ^[[:space:]]*# ]]; then
        # Удаляем лишние пробелы
        app=$(echo "$app" | xargs)
        if [ -n "$app" ]; then
          ensure_snap_pkg "$app"
        fi
      fi
    done <"$SCRIPT_DIR/scripts/ubuntu_snap_packages.txt"
  else
    log "WARN" "Файл со списком snap пакетов не найден: $SCRIPT_DIR/scripts/ubuntu_snap_packages.txt"
  fi
}

# Основная функция выполнения роли
main() {
  log "INFO" "Запуск роли: 40-snap-apps"

  # Выполнение установки snap приложений
  install_snap_apps

  log "INFO" "Роль 40-snap-apps завершена"
}

# Вызов основной функции
main "$@"
