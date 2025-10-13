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
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Установка пакетов для разработчиков (не выполнена)"
		return 0
	fi

	# Установка пакетов через apt
	log "INFO" "Установка пакетов через apt"
	local dev_packages="vim htop glances tree jq wget curl git docker.io docker-compose python3 python3-pip nodejs npm default-jre default-jdk maven gradle code"
	install_packages "$dev_packages"

	# Установка pip-пакетов
	log "INFO" "Установка Python пакетов через pip"
	if command -v pip3 &>/dev/null; then
		pip3 install --upgrade pip
		pip3 install requests flask django numpy pandas matplotlib
	fi

	# Установка npm-пакетов
	log "INFO" "Установка Node.js пакетов через npm"
	if command -v npm &>/dev/null; then
		npm install -g npm@latest
		npm install -g typescript @angular/cli
	fi

	# Установка snap-пакетов для разработчиков
	log "INFO" "Установка Snap пакетов для разработчиков"
	ensure_snap_pkg postman
	ensure_snap_pkg insomnia
	ensure_snap_pkg mysql-workbench-community
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