#!/bin/bash

# Главный скрипт установки для ubuntuInstaller
# Использует библиотеку функций из lib.sh
# Поддерживает флаг --dry-run для симуляции установки

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Параметры командной строки
CONFIG_FILE="config.yaml"
DRY_RUN_FLAG=false
VERBOSE=false

# Функция вывода справки
show_help() {
	cat <<EOF
Использование: $0 [ОПЦИИ]

Опции:
    -c, --config FILE    Путь к конфигурационному файлу (по умолчанию: config.yaml)
    --dry-run           Симуляция установки без изменений в системе
    -v, --verbose       Подробный вывод
    -h, --help          Показать это справочное сообщение
EOF
}

# Парсинг аргументов командной строки
while [[ $# -gt 0 ]]; do
	case $1 in
	-c | --config)
		CONFIG_FILE="$2"
		shift 2
		;;
	--dry-run)
		DRY_RUN_FLAG=true
		shift
		;;
	-v | --verbose)
		VERBOSE=true
		shift
		;;
	-h | --help)
		show_help
		exit 0
		;;
	*)
		log "ERROR" "Неизвестный параметр: $1"
		show_help
		exit 1
		;;
	esac
done

# Установка переменной окружения для режима симуляции
if [ "$DRY_RUN_FLAG" = true ]; then
	export UBUNTU_INSTALLER_DRY_RUN=true
	log "INFO" "Режим симуляции включен"
else
	export UBUNTU_INSTALLER_DRY_RUN=false
fi

# Функция проверки наличия yq для парсинга YAML
check_yq() {
	if ! command -v yq &>/dev/null; then
		log "INFO" "yq не найден, устанавливаем..."
		if [ "$DRY_RUN_FLAG" = false ]; then
			# Установка yq через snap или через curl в зависимости от доступности
			if command -v snap &>/dev/null; then
				sudo snap install yq
			else
				# Установка yq через curl
				sudo wget -qO /usr/local/bin/yq "https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64"
				sudo chmod +x /usr/local/bin/yq
			fi
		else
			log "INFO" "[DRY-RUN] Установка yq (не выполнена)"
		fi
	fi
}

# Функция загрузки конфигурации из YAML файла
load_config() {
	if [ ! -f "$CONFIG_FILE" ]; then
		log "ERROR" "Конфигурационный файл $CONFIG_FILE не найден"
		exit 1
	fi

	log "INFO" "Загрузка конфигурации из $CONFIG_FILE"

	# Проверка доступности yq
	check_yq

	# Загрузка настроек из конфига
	if [ "$DRY_RUN_FLAG" = false ]; then
		# В реальном режиме
		NON_INTERACTIVE=$(yq '.settings.non_interactive // false' "$CONFIG_FILE")
		LOG_FILE=$(yq '.settings.log_file // "/var/log/ubuntuInstaller/install-$(date +%Y-%m-%d).log"' "$CONFIG_FILE")
		PROFILE=$(yq '.profile // "desktop-developer"' "$CONFIG_FILE")
	else
		# В режиме симуляции
		log "INFO" "[DRY-RUN] Чтение конфигурации (не из файла)"
		NON_INTERACTIVE=$(yq '.settings.non_interactive // false' "$CONFIG_FILE" 2>/dev/null || echo "false")
		LOG_FILE=$(yq '.settings.log_file // "/var/log/ubuntuInstaller/install-$(date +%Y-%m-%d).log"' "$CONFIG_FILE" 2>/dev/null || echo "/var/log/ubuntuInstaller/install-$(date +%Y-%m-%d).log")
		PROFILE=$(yq '.profile // "desktop-developer"' "$CONFIG_FILE" 2>/dev/null || echo "desktop-developer")
	fi

	export UBUNTU_INSTALLER_NON_INTERACTIVE=$NON_INTERACTIVE
	export UBUNTU_INSTALLER_LOG_FILE=$LOG_FILE

	log "INFO" "Профиль: $PROFILE"
	log "INFO" "Режим без подтверждения: $NON_INTERACTIVE"
}

# Функция запуска предустановочных проверок
run_preflight_checks() {
	log "INFO" "Запуск предустановочных проверок"
	preflight_checks
}

# Функция запуска ролей
run_roles() {
	log "INFO" "Запуск ролей из конфигурации"

	# Проверка доступности yq
	check_yq

	# Получение списка ролей из конфигурации
	local roles
	if [ "$DRY_RUN_FLAG" = false ]; then
		roles=$(yq '.roles_enabled // [] | length' "$CONFIG_FILE")
	else
		roles=$(yq '.roles_enabled // [] | length' "$CONFIG_FILE" 2>/dev/null || echo "0")
	fi

	log "INFO" "Найдено ролей для установки: $roles"

	# Если в режиме симуляции и yq недоступен, просто выводим сообщение
	if [ "$DRY_RUN_FLAG" = true ] && [ "$roles" = "0" ]; then
		log "INFO" "[DRY-RUN] Предполагаем, что есть 3 стандартные роли для установки"
		roles=3
	fi

	# В текущей реализации просто симулируем запуск ролей
	# В будущем здесь будет логика для последовательного запуска ролей из директории roles/
	for i in $(seq 1 $roles); do
		if [ "$DRY_RUN_FLAG" = true ]; then
			log "INFO" "[DRY-RUN] Запуск роли $(($i - 1)) (симуляция)"
		else
			# В реальной реализации здесь будет вызов конкретной роли
			log "INFO" "Запуск роли $(($i - 1))"
			# source "roles/$(($i-1))*"/*.sh
		fi
	done
}

# Основная функция
main() {
	log "INFO" "Запуск установки Ubuntu с использованием фреймворка"

	# Загрузка конфигурации
	load_config

	# Запуск предустановочных проверок
	run_preflight_checks

	# Запуск ролей
	run_roles

	log "INFO" "Установка завершена успешно"
}

# Запуск основной функции
main "$@"
