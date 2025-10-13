#!/bin/bash

# Библиотека функций для ubuntuInstaller
# Содержит общие функции для всех модулей:
# - логирование (INFO/WARN/ERROR, вывод в файл + консоль)
# - run_with_retry
# - require_root
# - preflight_checks (дистрибутив/версия, сеть, свободное место)
# - is_pkg_installed
# - ensure_pkg
# - hash_verify
# - gpg_verify
# - поддержка --dry-run

set -e

# Переменные окружения
LOG_FILE="${UBUNTU_INSTALLER_LOG_FILE:-/var/log/ubuntuInstaller/install-$(date +%Y-%m-%d).log}"
DRY_RUN="${UBUNTU_INSTALLER_DRY_RUN:-false}"
NON_INTERACTIVE="${UBUNTU_INSTALLER_NON_INTERACTIVE:-false}"

# Создание директории для логов
sudo mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true

# Функция логирования
log() {
	local level=$1
	shift
	local message="$1"
	local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
	local log_entry="[$timestamp] [$level] $message"

	# Вывод в консоль
	case $level in
	"INFO")
		echo -e "\033[0;32m[INFO]\033[0m $message"
		;;
	"WARN")
		echo -e "\033[1;3m[WARN]\033[0m $message"
		;;
	"ERROR")
		echo -e "\033[0;31m[ERROR]\033[0m $message"
		;;
	*)
		echo -e "[$level] $message"
		;;
	esac

	# Запись в лог-файл
	echo "$log_entry" | sudo tee -a "$LOG_FILE" >/dev/null
}

# Функция для выполнения команд с повторными попытками
run_with_retry() {
	local max_attempts=3
	local timeout=10
	local attempt=1
	local exit_code=0

	while [ $attempt -le $max_attempts ]; do
		if timeout $timeout "$@"; then
			return 0
		else
			exit_code=$?
			log "WARN" "Команда '$*' не удалась (попытка $attempt/$max_attempts)"
			if [ $attempt -eq $max_attempts ]; then
				log "ERROR" "Команда '$*' не удалась после $max_attempts попыток"
				return $exit_code
			fi
			attempt=$((attempt + 1))
			sleep 5
		fi
	done
}

# Функция проверки прав root
require_root() {
	if [ "$EUID" -ne 0 ]; then
		log "ERROR" "Этот скрипт должен быть запущен с правами root"
		exit 1
	fi
}

# Функция проверки, установлен ли пакет
is_pkg_installed() {
	local package=$1
	if dpkg -l | grep -q "^ii  $package "; then
		return 0
	else
		return 1
	fi
}

# Функция установки пакета при необходимости
ensure_pkg() {
	local package=$1

	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Проверка пакета $package (установка не выполняется)"
		return 0
	fi

	if ! is_pkg_installed "$package"; then
		log "INFO" "Установка пакета $package"
		run_with_retry apt install -y "$package"
	else
		log "INFO" "Пакет $package уже установлен"
	fi
}

# Функция проверки хэша файла
hash_verify() {
	local file_path=$1
	local expected_hash=$2
	local algorithm=${3:-sha256}

	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Проверка хэша файла $file_path (проверка не выполняется)"
		return 0
	fi

	if [ ! -f "$file_path" ]; then
		log "ERROR" "Файл $file_path не найден"
		return 1
	fi

	local actual_hash
	case $algorithm in
	"sha256")
		actual_hash=$(sha256sum "$file_path" | cut -d' ' -f1)
		;;
	"md5")
		actual_hash=$(md5sum "$file_path" | cut -d' ' -f1)
		;;
	*)
		log "ERROR" "Неподдерживаемый алгоритм хэширования: $algorithm"
		return 1
		;;
	esac

	if [ "$actual_hash" = "$expected_hash" ]; then
		log "INFO" "Хэш файла $file_path верен"
		return 0
	else
		log "ERROR" "Хэш файла $file_path неверен. Ожидалось: $expected_hash, получено: $actual_hash"
		return 1
	fi
}

# Функция проверки GPG подписи
gpg_verify() {
	local file_path=$1
	local signature_path=$2
	local public_key_path=$3

	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Проверка GPG подписи для $file_path (проверка не выполняется)"
		return 0
	fi

	if [ ! -f "$file_path" ] || [ ! -f "$signature_path" ]; then
		log "ERROR" "Файл или подпись не найдены"
		return 1
	fi

	# Импорт публичного ключа, если он указан
	if [ -n "$public_key_path" ] && [ -f "$public_key_path" ]; then
		gpg --import "$public_key_path"
	fi

	# Проверка подписи
	gpg --verify "$signature_path" "$file_path"
}

# Функция предварительной проверки системы
preflight_checks() {
	log "INFO" "Выполнение предварительных проверок системы..."

	# Проверка версии Ubuntu
	if [ -f /etc/os-release ]; then
		. /etc/os-release
		if [ "$NAME" != "Ubuntu" ]; then
			log "ERROR" "Этот скрипт поддерживает только Ubuntu"
			exit 1
		fi

		# Проверка поддерживаемых версий
		case $VERSION_ID in
		"22.04" | "24.04")
			log "INFO" "Версия Ubuntu $VERSION_ID поддерживается"
			;;
		*)
			log "WARN" "Версия Ubuntu $VERSION_ID не проверялась, но может работать"
			;;
		esac
	else
		log "ERROR" "Не удалось определить версию операционной системы"
		exit 1
	fi

	# Проверка наличия подключения к интернету
	if ! ping -c 1 8.8.8.8 &>/dev/null; then
		log "ERROR" "Нет подключения к интернету"
		exit 1
	else
		log "INFO" "Подключение к интернету есть"
	fi

	# Проверка свободного места на диске (минимум 5 ГБ)
	local free_space
	free_space=$(df / --output=avail -B1G | tail -n 1 | tr -d ' ')
	if [ "$free_space" -lt 5 ]; then
		log "ERROR" "Недостаточно свободного места на диске. Требуется минимум 5 ГБ, доступно: ${free_space} ГБ"
		exit 1
	else
		log "INFO" "Свободное место на диске: ${free_space} ГБ"
	fi

	log "INFO" "Предварительные проверки успешно пройдены"
}

# Функция выполнения команды в режиме dry-run
execute_command() {
	local command="$1"

	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Команда: $command (не выполнена)"
		return 0
	else
		log "INFO" "Выполнение команды: $command"
		eval "$command"
	fi
}

# Функция для установки пакетов с поддержкой dry-run
install_packages() {
	local packages="$1"

	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Установка пакетов: $packages (не выполнена)"
		return 0
	fi

	log "INFO" "Установка пакетов: $packages"
	run_with_retry apt install -y $packages
}

# Функция для проверки и установки Snap пакетов
ensure_snap_pkg() {
	local package=$1

	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Проверка Snap пакета $package (установка не выполняется)"
		return 0
	fi

	if ! snap list | grep -q "^$package "; then
		log "INFO" "Установка Snap пакета $package"
		snap install "$package"
	else
		log "INFO" "Snap пакет $package уже установлен"
	fi
}
