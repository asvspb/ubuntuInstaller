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

# Функция безопасного скачивания файла с верификацией
download_with_verification() {
	local url=$1
	local dest_path=$2
	local expected_hash=$3
	local hash_algorithm=${4:-sha256}
	local signature_url=$5
	local public_key_url=$6

	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Скачивание файла из $url в $dest_path (не выполнено)"
		return 0
	fi

	log "INFO" "Скачивание файла из $url в $dest_path"
	
	# Скачивание файла
	if ! curl -L -o "$dest_path" "$url"; then
	log "ERROR" "Не удалось скачать файл из $url"
		return 1
	fi

	# Скачивание публичного ключа, если указан
	local temp_key_path=""
	if [ -n "$public_key_url" ]; then
		temp_key_path="/tmp/pubkey.$$.gpg"
		if ! curl -L -o "$temp_key_path" "$public_key_url"; then
			log "ERROR" "Не удалось скачать публичный ключ из $public_key_url"
			rm -f "$dest_path"
			return 1
		fi
	fi

	# Скачивание подписи, если указана
	local temp_signature_path=""
	if [ -n "$signature_url" ]; then
		temp_signature_path="/tmp/signature.$$.asc"
		if ! curl -L -o "$temp_signature_path" "$signature_url"; then
			log "ERROR" "Не удалось скачать подпись из $signature_url"
			rm -f "$dest_path" "$temp_key_path"
			return 1
	fi
	fi

	# Проверка подписи, если она указана
	if [ -n "$temp_signature_path" ]; then
		if ! gpg_verify "$dest_path" "$temp_signature_path" "$temp_key_path"; then
			log "ERROR" "Проверка GPG подписи не пройдена для $dest_path"
			rm -f "$dest_path" "$temp_signature_path" "$temp_key_path"
			return 1
		fi
		log "INFO" "GPG подпись для $dest_path подтверждена"
	fi

	# Проверка хэша, если он указан
	if [ -n "$expected_hash" ]; then
	if ! hash_verify "$dest_path" "$expected_hash" "$hash_algorithm"; then
			log "ERROR" "Хэш файла $dest_path не совпадает с ожидаемым"
			rm -f "$dest_path" "$temp_signature_path" "$temp_key_path"
			return 1
		fi
		log "INFO" "Хэш файла $dest_path подтверждён"
	fi

	# Удаление временных файлов
	if [ -n "$temp_signature_path" ] && [ -f "$temp_signature_path" ]; then
		rm -f "$temp_signature_path"
	fi
	if [ -n "$temp_key_path" ] && [ -f "$temp_key_path" ]; then
		rm -f "$temp_key_path"
	fi

	return 0
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

# Функция определения типа системы (десктоп/сервер/WSL/VM)
detect_system_type() {
	# Проверка на WSL
	if [ -d /proc/sys/fs/binfmt_misc ] && [ -f /proc/sys/fs/binfmt_misc/WSLInterop ]; then
		echo "WSL"
		return
	fi

	# Проверка на виртуальную машину
	if [ -f /sys/class/dmi/id/product_name ]; then
		local product_name=$(cat /sys/class/dmi/id/product_name 2>/dev/null)
		case "$product_name" in
			*VMware*|*VirtualBox*|*KVM*|*QEMU*|*Virtual\ Machine*|*VM*|*Hypervisor*)
				echo "VM"
				return
				;;
		esac
	fi

	# Проверка наличие графической среды
	if [ -n "$XDG_SESSION_TYPE" ] || [ -n "$DISPLAY" ]; then
		echo "desktop"
	else
		echo "server"
	fi
}

# Функция определения типа видеокарты
detect_gpu_type() {
	if command -v lspci &>/dev/null; then
		local gpu_info=$(lspci -nn | grep -i '\[03')
		if echo "$gpu_info" | grep -qi nvidia; then
			echo "NVIDIA"
		elif echo "$gpu_info" | grep -qi amd; then
			echo "AMD"
		elif echo "$gpu_info" | grep -qi intel; then
			echo "Intel"
		else
			echo "Unknown"
		fi
	else
		echo "Unknown"
	fi
}

# Функция определения типа системы (ноутбук/десктоп)
detect_system_form_factor() {
	# Проверка на ноутбук по наличию батареи
	if [ -d /sys/class/power_supply ]; then
		for dir in /sys/class/power_supply/*; do
			if [ -f "$dir/type" ] && [ "$(cat $dir/type 2>/dev/null)" = "Battery" ]; then
				echo "laptop"
				return
			fi
		done
	fi
	echo "desktop"
}

# Функция проверки, установлена ли роль
is_role_installed() {
	local role_name=$1
	local state_file="${STATE_FILE:-/var/lib/ubuntuInstaller.state}"
	if [ -f "$state_file" ]; then
		if grep -q "^$role_name$" "$state_file"; then
			return 0
		else
			return 1
	fi
	else
		return 1
	fi
}

# Функция создания снапшота с помощью Timeshift (если доступен)
create_snapshot() {
	local description=$1
	local snapshot_path=$2
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Создание снапшота: $description (не выполнено)"
		return 0
	fi

	# Проверяем, установлен ли Timeshift
	if command -v timeshift &>/dev/null; then
		log "INFO" "Создание снапшота с помощью Timeshift: $description"
		
		# Создание снапшота
		if [ -n "$snapshot_path" ]; then
			sudo timeshift --create --comments "$description" --snapshot-device "$snapshot_path" --target "$snapshot_path"
		else
			sudo timeshift --create --comments "$description"
		fi
		
		if [ $? -eq 0 ]; then
			log "INFO" "Снапшот успешно создан"
			return 0
		else
			log "WARN" "Не удалось создать снапшот с помощью Timeshift"
			return 1
		fi
	else
		log "WARN" "Timeshift не установлен, невозможно создать снапшот"
		return 1
	fi
}

# Функция проверки доступности снапшотов
check_snapshot_support() {
	# Проверяем наличие Timeshift
	if command -v timeshift &>/dev/null; then
		echo "timeshift"
		return 0
	fi
	
	# Проверяем, является ли система Btrfs
	if [ -f /proc/filesystems ] && grep -q btrfs /proc/filesystems; then
	if mount | grep -q "btrfs.*on /"; then
			echo "btrfs"
			return 0
		fi
	fi
	
	# Другие системы снапшотов можно добавить позже
	echo "none"
	return 0
}
