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
	if [ "$DRY_RUN_FLAG" = false ]; then
		local roles_count=$(yq '.roles_enabled // [] | length' "$CONFIG_FILE")
	else
		local roles_count=$(yq '.roles_enabled // [] | length' "$CONFIG_FILE" 2>/dev/null || echo "0")
	fi

	log "INFO" "Найдено ролей для установки: $roles_count"

	# Подсчет реально включенных ролей
	local enabled_roles_count=0
	
	# Обработка каждой роли из конфигурации
	for i in $(seq 0 $((roles_count - 1))); do
		local role_name=$(yq ".roles_enabled[$i].name" "$CONFIG_FILE" 2>/dev/null || echo "null")
		local role_enabled_raw=$(yq ".roles_enabled[$i].enabled" "$CONFIG_FILE" 2>/dev/null)
		# Если значение не найдено (null), используем значение по умолчанию (true)
		if [ "$role_enabled_raw" = "null" ] || [ -z "$role_enabled_raw" ]; then
			local role_enabled="true"
		else
			local role_enabled="$role_enabled_raw"
	fi
		
		# Проверяем, что роль существует
		if [ "$role_name" = "null" ]; then
			log "WARN" "Не удалось получить имя роли $i, пропуск"
			continue
		fi
		
		# В симуляции, если yq не может получить значение, и оно вернулось как "null",
		# используем значение по умолчанию (true)
		if [ "$DRY_RUN_FLAG" = true ] && [ "$role_enabled" = "null" ]; then
			role_enabled=$(yq ".roles_enabled[$i].enabled" "$CONFIG_FILE" 2>/dev/null || echo "null")
			if [ "$role_enabled" = "null" ]; then
				role_enabled="true"
			fi
		fi
		
		# Увеличиваем счетчик включенных ролей только если роль не отключена
		if [ "$role_enabled" != "false" ]; then
			enabled_roles_count=$((enabled_roles_count + 1))
		else
			log "INFO" "Роль $role_name отключена в конфигурации, пропуск"
			continue
		fi
		
		# Путь к директории роли
		local role_dir="$SCRIPT_DIR/roles/$role_name"
		
		# Проверяем существование директории роли
		if [ ! -d "$role_dir" ]; then
			log "ERROR" "Директория роли $role_name не найдена: $role_dir"
			continue
		fi
		
		# Путь к скрипту main роли
		local role_script="$role_dir/main.sh"
		
		# Проверяем существование скрипта роли
		if [ ! -f "$role_script" ]; then
			log "ERROR" "Скрипт main.sh для роли $role_name не найден: $role_script"
			continue
		fi
		
		if [ "$DRY_RUN_FLAG" = true ]; then
			log "INFO" "[DRY-RUN] Запуск роли $role_name (симуляция)"
		else
			log "INFO" "Запуск роли $role_name"
			# Передаем в роль переменные, если они определены
			local role_vars=$(yq ".roles_enabled[$i].vars // {}" "$CONFIG_FILE" 2>/dev/null || echo "{}")
			if [ "$role_vars" != "{}" ]; then
				export UBUNTU_INSTALLER_ROLE_VARS="$role_vars"
			fi
			# Выполняем скрипт роли
			bash "$role_script"
		fi
	done
	
	log "INFO" "Найдено включенных ролей для установки: $enabled_roles_count"
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
