#!/bin/bash

# Скрипт валидации конфигурации для ubuntuInstaller
# Проверяет структуру и типы данных в YAML-файле конфигурации

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция вывода справки
show_help() {
  cat <<EOF
Использование: $0 [ФАЙЛ_КОНФИГУРАЦИИ]

Проверяет структуру и типы данных в файле конфигурации.

Пример:
    $0 config.yaml
    $0 profiles/desktop-developer.yaml
EOF
}

# Проверка наличия yq
if ! command -v yq &>/dev/null; then
  log "ERROR" "yq не найден. Установите его командой: sudo snap install yq"
  exit 1
fi

# Проверка аргументов
if [[ $# -eq 0 ]]; then
  show_help
  exit 1
elif [[ $# -eq 1 ]]; then
  if [[ $1 == "-h" || $1 == "--help" ]]; then
    show_help
    exit 0
  else
    CONFIG_FILE="$1"
  fi
else
  log "ERROR" "Неправильное количество аргументов"
  show_help
  exit 1
fi

# Функция валидации конфигурационного файла
validate_config() {
  local CONFIG_FILE=$1

  log "INFO" "Проверка конфигурационного файла: $CONFIG_FILE"

  # Проверка, что файл является валидным YAML
  if ! yq . "$CONFIG_FILE" >/dev/null 2>&1; then
    log "ERROR" "Файл $CONFIG_FILE не является валидным YAML"
    exit 1
  fi

  log "INFO" "YAML синтаксис корректен"

  # Проверка структуры конфигурации
  log "INFO" "Проверка структуры конфигурации..."

  # Проверка наличия обязательных полей
  if ! yq '.settings' "$CONFIG_FILE" >/dev/null 2>&1 || [ "$(yq '.settings' "$CONFIG_FILE" 2>/dev/null)" = "null" ]; then
    log "ERROR" "Отсутствует обязательное поле 'settings'"
    exit 1
  fi

  if ! yq '.profile' "$CONFIG_FILE" >/dev/null 2>&1 || [ "$(yq '.profile' "$CONFIG_FILE" 2>/dev/null)" = "null" ]; then
    log "ERROR" "Отсутствует обязательное поле 'profile'"
    exit 1
  fi

  if ! yq '.roles_enabled' "$CONFIG_FILE" >/dev/null 2>&1 || [ "$(yq '.roles_enabled' "$CONFIG_FILE" 2>/dev/null)" = "null" ]; then
    log "ERROR" "Отсутствует обязательное поле 'roles_enabled'"
    exit 1
  fi

  # Проверка типов данных в settings
  log "INFO" "Проверка типов данных в поле 'settings'..."

  if [ "$(yq '.settings.non_interactive' "$CONFIG_FILE" 2>/dev/null)" != "null" ]; then
    non_interactive_type=$(yq '.settings.non_interactive | type' "$CONFIG_FILE" 2>/dev/null)
    if [ "$non_interactive_type" != "!!bool" ]; then
      log "ERROR" "Поле 'settings.non_interactive' должно быть булевым значением, получено: $non_interactive_type"
      exit 1
    fi
    log "INFO" "  non_interactive: тип корректен (boolean)"
  fi

  if [ "$(yq '.settings.create_snapshot' "$CONFIG_FILE" 2>/dev/null)" != "null" ]; then
    create_snapshot_type=$(yq '.settings.create_snapshot | type' "$CONFIG_FILE" 2>/dev/null)
    if [ "$create_snapshot_type" != "!!bool" ]; then
      log "ERROR" "Поле 'settings.create_snapshot' должно быть булевым значением, получено: $create_snapshot_type"
      exit 1
    fi
    log "INFO" "  create_snapshot: тип корректен (boolean)"
  fi

  if [ "$(yq '.settings.remove_snapshots' "$CONFIG_FILE" 2>/dev/null)" != "null" ]; then
    remove_snapshots_type=$(yq '.settings.remove_snapshots | type' "$CONFIG_FILE" 2>/dev/null)
    if [ "$remove_snapshots_type" != "!!bool" ]; then
      log "ERROR" "Поле 'settings.remove_snapshots' должно быть булевым значением, получено: $remove_snapshots_type"
      exit 1
    fi
    log "INFO" "  remove_snapshots: тип корректен (boolean)"
  fi

  # Проверка типа profile
  local profile_type=$(yq '.profile | type' "$CONFIG_FILE" 2>/dev/null)
  if [ "$profile_type" != "!!str" ]; then
    log "ERROR" "Поле 'profile' должно быть строкой, получено: $profile_type"
    exit 1
  fi
  log "INFO" "  profile: тип корректен (string)"

  # Проверка структуры roles_enabled
  log "INFO" "Проверка структуры поля 'roles_enabled'..."

  local roles_count=$(yq '.roles_enabled | length' "$CONFIG_FILE" 2>/dev/null)
  if [ "$roles_count" = "null" ] || [ "$roles_count" -lt 0 ]; then
    log "ERROR" "Поле 'roles_enabled' должно быть массивом"
    exit 1
  fi

  log "INFO" "  Найдено ролей: $roles_count"

  # Проверка каждой роли
  for i in $(seq 0 $((roles_count - 1))); do
    role_name=$(yq ".roles_enabled[$i].name" "$CONFIG_FILE" 2>/dev/null)

    if [ "$role_name" = "null" ]; then
      log "ERROR" "Роль $i: отсутствует обязательное поле 'name'"
      exit 1
    fi

    role_name_type=$(yq ".roles_enabled[$i].name | type" "$CONFIG_FILE" 2>/dev/null)
    if [ "$role_name_type" != "!!str" ]; then
      log "ERROR" "Роль $i: поле 'name' должно быть строкой, получено: $role_name_type"
      exit 1
    fi

    # Проверка наличия поля enabled (если оно присутствует)
    if yq ".roles_enabled[$i].enabled" "$CONFIG_FILE" >/dev/null 2>&1 && [ "$(yq ".roles_enabled[$i].enabled" "$CONFIG_FILE" 2>/dev/null)" != "null" ]; then
      enabled_type=$(yq ".roles_enabled[$i].enabled | type" "$CONFIG_FILE" 2>/dev/null)
      if [ "$enabled_type" != "!!bool" ]; then
        log "ERROR" "Роль $i ($role_name): поле 'enabled' должно быть булевым значением, получено: $enabled_type"
        exit 1
      fi
    fi

    # Проверка наличия поля vars (если оно присутствует)
    if yq ".roles_enabled[$i].vars" "$CONFIG_FILE" >/dev/null 2>&1 && [ "$(yq ".roles_enabled[$i].vars" "$CONFIG_FILE" 2>/dev/null)" != "null" ]; then
      vars_type=$(yq ".roles_enabled[$i].vars | type" "$CONFIG_FILE" 2>/dev/null)
      if [ "$vars_type" != "!!map" ]; then
        log "ERROR" "Роль $i ($role_name): поле 'vars' должно быть объектом, получено: $vars_type"
        exit 1
      fi
    fi

    log "INFO" " Роль $i: $role_name - OK"
  done

  log "INFO" "Конфигурационный файл $CONFIG_FILE валиден"
}

# Проверка существования файла
if [[ ! -f "$CONFIG_FILE" ]]; then
  log "ERROR" "Файл конфигурации $CONFIG_FILE не найден"
  exit 1
fi

# Вызов функции валидации
validate_config "$CONFIG_FILE"
