#!/bin/bash

# Скрипт для тестирования документации и примеров конфигурационных файлов
# Проверяет корректность синтаксиса YAML и работу примеров с фреймворком

set -e

# Путь к директории проекта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Функция логирования
log() {
  local level=$1
  shift
  local message="$1"
  local timestamp=$(date '+%Y-%m-%d %H:%M:%S')

  case $level in
  "INFO")
    echo -e "\033[0;32m[INFO]\033[0m [$timestamp] $message"
    ;;
  "WARN")
    echo -e "\033[1;3m[WARN]\033[0m [$timestamp] $message"
    ;;
  "ERROR")
    echo -e "\033[0;31m[ERROR]\033[0m [$timestamp] $message"
    ;;
  *)
    echo -e "[$level] [$timestamp] $message"
    ;;
  esac
}

# Функция проверки наличия необходимых инструментов
check_dependencies() {
  log "INFO" "Проверка наличия необходимых инструментов"

  local deps=("yq" "shellcheck")
  local missing_deps=()

  for dep in "${deps[@]}"; do
    if ! command -v "$dep" &>/dev/null; then
      missing_deps+=("$dep")
    fi
  done

  if [ ${#missing_deps[@]} -gt 0 ]; then
    log "ERROR" "Отсутствуют необходимые инструменты: ${missing_deps[*]}"
    log "INFO" "Установите их с помощью:"
    log "INFO" "  sudo apt install yq shellcheck"
    return 1
  fi

  log "INFO" "Все необходимые инструменты установлены"
  return 0
}

# Функция проверки синтаксиса YAML файлов
validate_yaml_syntax() {
  local yaml_files=("$@")

  log "INFO" "Проверка синтаксиса YAML файлов"

  for yaml_file in "${yaml_files[@]}"; do
    if [ ! -f "$yaml_file" ]; then
      log "WARN" "Файл $yaml_file не найден, пропуск"
      continue
    fi

    log "INFO" "Проверка файла: $yaml_file"

    # Проверка синтаксиса YAML
    if yq '.' "$yaml_file" >/dev/null 2>&1; then
      log "INFO" "Файл $yaml_file прошел проверку синтаксиса"
    else
      log "ERROR" "Файл $yaml_file содержит ошибки синтаксиса"
      return 1
    fi
  done

  return 0
}

# Функция проверки работы примеров с фреймворком
test_examples() {
  local example_files=("$@")

  log "INFO" "Проверка работы примеров с фреймворком"

  for example_file in "${example_files[@]}"; do
    if [ ! -f "$example_file" ]; then
      log "WARN" "Файл $example_file не найден, пропуск"
      continue
    fi

    log "INFO" "Тестирование примера: $example_file"

    # Проверка в режиме симуляции
    if "$SCRIPT_DIR/install.sh" --dry-run -c "$example_file"; then
      log "INFO" "Пример $example_file успешно прошел тестирование в режиме симуляции"
    else
      log "ERROR" "Пример $example_file не прошел тестирование в режиме симуляции"
      return 1
    fi
  done

  return 0
}

# Функция проверки документации
validate_documentation() {
  log "INFO" "Проверка документации"

  local doc_files=(
    "$SCRIPT_DIR/README.md"
    "$SCRIPT_DIR/README.quickstart.md"
    "$SCRIPT_DIR/README.compatibility.md"
    "$SCRIPT_DIR/HOWTO.add-role.md"
  )

  for doc_file in "${doc_files[@]}"; do
    if [ ! -f "$doc_file" ]; then
      log "WARN" "Файл документации $doc_file не найден, пропуск"
      continue
    fi

    log "INFO" "Проверка файла документации: $doc_file"

    # Проверка наличия обязательных разделов
    if [ "$doc_file" = "$SCRIPT_DIR/README.md" ]; then
      local required_sections=("## Framework Components" "## Usage" "## Makefile Targets")
      for section in "${required_sections[@]}"; do
        if ! grep -q "$section" "$doc_file"; then
          log "ERROR" "В файле $doc_file отсутствует обязательный раздел: $section"
          return 1
        fi
      done
    fi

    log "INFO" "Файл документации $doc_file прошел проверку"
  done

  return 0
}

# Основная функция
main() {
  log "INFO" "Запуск тестирования документации и примеров"

  # Проверка зависимостей
  if ! check_dependencies; then
    log "ERROR" "Проверка зависимостей не пройдена"
    exit 1
  fi

  # Проверка синтаксиса YAML файлов
  local yaml_files=(
    "$SCRIPT_DIR/config.yaml"
    "$SCRIPT_DIR/examples/desktop-developer.yaml"
    "$SCRIPT_DIR/examples/server.yaml"
    "$SCRIPT_DIR/examples/wsl.yaml"
    "$SCRIPT_DIR/examples/minimal.yaml"
  )

  if ! validate_yaml_syntax "${yaml_files[@]}"; then
    log "ERROR" "Проверка синтаксиса YAML файлов не пройдена"
    exit 1
  fi

  # Проверка работы примеров
  local example_files=(
    "$SCRIPT_DIR/examples/desktop-developer.yaml"
    "$SCRIPT_DIR/examples/server.yaml"
    "$SCRIPT_DIR/examples/wsl.yaml"
    "$SCRIPT_DIR/examples/minimal.yaml"
  )

  if ! test_examples "${example_files[@]}"; then
    log "ERROR" "Проверка работы примеров не пройдена"
    exit 1
  fi

  # Проверка документации
  if ! validate_documentation; then
    log "ERROR" "Проверка документации не пройдена"
    exit 1
  fi

  log "INFO" "Все тесты документации и примеров пройдены успешно"
}

# Запуск основной функции
main "$@"
