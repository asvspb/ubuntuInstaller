#!/bin/bash

# Роль: 80-virtualbox
# Назначение: Установка VirtualBox из официального репозитория с точной версией для Ubuntu

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Базовый URL для VirtualBox
VB_BASE_URL="https://download.virtualbox.org/virtualbox"
LATEST_STABLE_URL="${VB_BASE_URL}/LATEST-STABLE.TXT"
DOWNLOAD_BASE="$HOME/Downloads/VirtualBox"

# Функция получения последней стабильной версии VirtualBox
get_latest_stable_version() {
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Получение последней стабильной версии VirtualBox (не выполнено)"
		echo "7.0.12"
		return 0
	fi

	# Попытка получить версию из LATEST-STABLE.TXT
	local version=$(curl -s "$LATEST_STABLE_URL" 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1)
	
	if [ -z "$version" ]; then
		log "WARN" "Не удалось получить версию из LATEST-STABLE.TXT"
		# В качестве резервного варианта, можно использовать жестко заданную версию или получить из списка
	version="7.0.12"  # использовать актуальную версию на момент разработки
	fi

	log "INFO" "Последняя стабильная версия VirtualBox: $version"
	echo "$version"
}

# Функция определения кодового имени дистрибутива
detect_distro_codename() {
	local codename=""
	
	# Попытка получить кодовое имя из /etc/os-release
	if [ -f /etc/os-release ]; then
	codename=$(grep '^VERSION_CODENAME=' /etc/os-release | cut -d'=' -f2)
		if [ -z "$codename" ]; then
			# Попытка получить из UBUNTU_CODENAME
			codename=$(grep '^UBUNTU_CODENAME=' /etc/os-release | cut -d'=' -f2)
		fi
	fi
	
	# Если не получилось из файла, используем lsb_release
	if [ -z "$codename" ]; then
		if command -v lsb_release &>/dev/null; then
			codename=$(lsb_release -cs)
		fi
	fi
	
	# Если все еще пусто, используем jammy по умолчанию
	if [ -z "$codename" ]; then
		log "WARN" "Не удалось определить кодовое имя дистрибутива, используем jammy по умолчанию"
		codename="jammy"
	fi

	log "INFO" "Кодовое имя дистрибутива: $codename"
	echo "$codename"
}

# Функция поиска подходящих артефактов для текущей системы
find_artifacts() {
local version=$1
local codename=$2

# Экранируем переменные перед логированием
local safe_version="${version//[$'\t\r\n']/}"
local safe_codename="${codename//[$'\t\r\n']/}"

log "INFO" "Поиск артефактов для версии $safe_version и кодового имени $safe_codename"

if [ "$DRY_RUN" = "true" ]; then
	log "INFO" "[DRY-RUN] Поиск артефактов (не выполнено)"
	return 0
fi

	# Получение списка файлов для версии
	local version_url="${VB_BASE_URL}/${version}/"
	local files=$(curl -s "$version_url" 2>/dev/null | grep -o 'href="[^"]*"' | sed 's/href="//' | sed 's/"$//' | grep -E '\.(deb|vbox-extpack|iso)$')
	
	# Поиск .deb файла для Ubuntu с подходящим кодовым именем
	local deb_url=""
	local deb_candidates=$(echo "$files" | grep -F "Ubuntu" | grep -F "$codename" | grep -F "amd64.deb" | sort -r)
	if [ -n "$deb_candidates" ]; then
		deb_url="${VB_BASE_URL}/${version}/$(echo "$deb_candidates" | head -n 1)"
	fi
	
	# Поиск Extension Pack
	local extpack_url=""
	local extpack_candidates=$(echo "$files" | grep -i -E "extension.*pack|extpack" | grep -F "$version" | sort -r)
	if [ -n "$extpack_candidates" ]; then
		extpack_url="${VB_BASE_URL}/${version}/$(echo "$extpack_candidates" | head -n 1)"
	fi
	
	# Поиск ISO с Guest Additions
	local iso_url=""
	local iso_candidates=$(echo "$files" | grep -i -E "guest.*additions|vboxguestadditions" | grep -F "$version" | sort -r)
	if [ -n "$iso_candidates" ]; then
		iso_url="${VB_BASE_URL}/${version}/$(echo "$iso_candidates" | head -n 1)"
	fi
	
	# Вывод найденных артефактов в переменные окружения
	if [ -n "$deb_url" ]; then
		VB_DEB_URL="$deb_url"
		log "INFO" "Найден .deb файл: $VB_DEB_URL"
	fi
	
	if [ -n "$extpack_url" ]; then
		VB_EXTPACK_URL="$extpack_url"
		log "INFO" "Найден Extension Pack: $VB_EXTPACK_URL"
	fi
	
	if [ -n "$iso_url" ]; then
		VB_ISO_URL="$iso_url"
		log "INFO" "Найден Guest Additions ISO: $VB_ISO_URL"
	fi
}

# Функция проверки установленной версии VirtualBox
vbox_version() {
	if command -v VBoxManage &>/dev/null; then
		VBoxManage --version 2>/dev/null | head -n 1
	else
		echo ""
	fi
}

# Функция сравнения версий
compare_versions() {
	local installed=$1
	local latest=$2
	
	if [ -z "$installed" ]; then
		return 1 # установлено не было, нужно устанавливать
	fi
	
	# Извлечение основной версии (без суффиксов типа r123456)
	local installed_base=$(echo "$installed" | sed 's/^\([0-9]\+\.[0-9]\+\.[0-9]\+\).*/\1/')
	local latest_base=$(echo "$latest" | sed 's/^\([0-9]\+\.[0-9]\+\.[0-9]\+\).*/\1/')
	
	if [ "$installed_base" = "$latest_base" ]; then
		return 0  # версии совпадают
	else
		return 1  # версии разные
	fi
}

# Функция установки VirtualBox
install_virtualbox() {
	log "INFO" "Установка VirtualBox из официального репозитория"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Установка VirtualBox (не выполнена)"
		return 0
	fi

	# Создание директории для загрузок
	mkdir -p "$DOWNLOAD_BASE"
	
	# Проверка установленной версии
	local current_version=$(vbox_version)
	log "INFO" "Текущая версия VirtualBox: ${current_version:-'не установлена'}"
	
	# Получение последней стабильной версии
	local latest_version_raw
	if [ "$DRY_RUN" = "true" ]; then
		latest_version_raw="7.0.12"
	else
	latest_version_raw=$(curl -s "$LATEST_STABLE_URL" 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1)
		if [ -z "$latest_version_raw" ]; then
			latest_version_raw="7.0.12"
		fi
	fi
	local latest_version="$latest_version_raw"
	log "INFO" "Последняя стабильная версия VirtualBox: $latest_version"
	
	# Проверка, нужна ли обновление
	if compare_versions "$current_version" "$latest_version"; then
		log "INFO" "Версия VirtualBox $current_version уже актуальна, выход."
		return 0
	fi

	# Определение кодового имени дистрибутива
	local codename_raw=""
	
	# Попытка получить кодовое имя из /etc/os-release
	if [ -f /etc/os-release ]; then
		codename_raw=$(grep '^VERSION_CODENAME=' /etc/os-release | cut -d'=' -f2)
		if [ -z "$codename_raw" ]; then
			# Попытка получить из UBUNTU_CODENAME
			codename_raw=$(grep '^UBUNTU_CODENAME=' /etc/os-release | cut -d'=' -f2)
		fi
	fi
	
	# Если не получилось из файла, используем lsb_release
	if [ -z "$codename_raw" ]; then
		if command -v lsb_release &>/dev/null; then
			codename_raw=$(lsb_release -cs)
		fi
	fi
	
	# Если все еще пусто, используем jammy по умолчанию
	if [ -z "$codename_raw" ]; then
		log "WARN" "Не удалось определить кодовое имя дистрибутива, используем jammy по умолчанию"
		codename_raw="jammy"
	fi
	local codename="$codename_raw"
	log "INFO" "Кодовое имя дистрибутива: $codename"
	
	# Поиск артефактов
	find_artifacts "$latest_version" "$codename"
	
	# Проверка, найден ли .deb файл
	if [ -z "$VB_DEB_URL" ]; then
		log "ERROR" "Не найден подходящий .deb файл для Ubuntu $codename"
		return 1
	fi
	
	# Загрузка .deb файла
	local deb_name=$(basename "$VB_DEB_URL")
	local deb_path="$DOWNLOAD_BASE/$deb_name"
	log "INFO" "Загрузка $VB_DEB_URL -> $deb_path"
	download_with_verification "$VB_DEB_URL" "$deb_path"
	
	# Установка зависимостей для сборки
	log "INFO" "Установка зависимостей для сборки"
	install_packages "dkms build-essential"
	local headers_pkg="linux-headers-$(uname -r)"
	if ! is_pkg_installed "$headers_pkg"; then
		install_packages "$headers_pkg"
	fi
	
	# Установка VirtualBox из .deb файла
	log "INFO" "Установка VirtualBox из $deb_path"
	install_packages "$deb_path"
	
	# Запуск vboxconfig для настройки
	if [ -f /sbin/vboxconfig ]; then
		sudo /sbin/vboxconfig
	fi
	
	# Добавление текущего пользователя в группу vboxusers
	if [ "$USER" != "root" ]; then
		if ! groups "$USER" | grep -q '\bvboxusers\b'; then
			sudo usermod -aG vboxusers "$USER"
			log "INFO" "Пользователь $USER добавлен в группу vboxusers"
		else
			log "INFO" "Пользователь $USER уже состоит в группе vboxusers"
	fi
	fi
	
	# Установка Extension Pack если доступен
	if [ -n "$VB_EXTPACK_URL" ]; then
		local extpack_name=$(basename "$VB_EXTPACK_URL")
		local extpack_path="$DOWNLOAD_BASE/$extpack_name"
		log "INFO" "Загрузка Extension Pack $VB_EXTPACK_URL -> $extpack_path"
		download_with_verification "$VB_EXTPACK_URL" "$extpack_path"
		
		# Установка Extension Pack
		if command -v VBoxManage &>/dev/null; then
			log "INFO" "Установка Extension Pack из $extpack_path"
			echo "y" | sudo VBoxManage extpack install --replace "$extpack_path"
			log "INFO" "Extension Pack установлен"
		fi
		
		# Удаление файла после установки
	rm -f "$extpack_path"
	fi
	
	# Удаление .deb файла после установки
	rm -f "$deb_path"
	
	log "INFO" "VirtualBox успешно установлен"
}

# Основная функция выполнения роли
main() {
	log "INFO" "Запуск роли: 80-virtualbox"
	
	# Выполнение установки VirtualBox
	install_virtualbox
	
	log "INFO" "Роль 80-virtualbox завершена"
}

# Вызов основной функции
main "$@"