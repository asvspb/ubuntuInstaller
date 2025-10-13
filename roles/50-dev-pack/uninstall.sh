#!/bin/bash

# Скрипт удаления для роли 50-dev-pack
# Удаляет пакеты разработчика, установленные в main.sh

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция удаления пакетов разработчика
uninstall_dev_pack() {
	log "INFO" "Удаление пакетов разработчика"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Удаление пакетов разработчика (не выполнено)"
		return 0
	fi

	# Удаление пакетов через apt
	log "INFO" "Удаление пакетов через apt"
	local dev_packages="vim htop glances tree jq wget curl git docker.io docker-compose python3 python3-pip nodejs npm default-jre default-jdk maven gradle code"
	
	# Удаление пакетов
	for package in $dev_packages; do
		if is_pkg_installed "$package"; then
			log "INFO" "Удаление пакета: $package"
			apt remove -y --purge "$package"
		else
			log "INFO" "Пакет $package не установлен"
		fi
	done

	# Удаление pip-пакетов
	log "INFO" "Удаление Python пакетов через pip"
	if command -v pip3 &>/dev/null; then
		# Удаляем установленные пакеты
		pip3 uninstall -y requests flask django numpy pandas matplotlib
	else
		log "INFO" "pip3 не установлен"
	fi

	# Удаление npm-пакетов
	log "INFO" "Удаление Node.js пакетов через npm"
	if command -v npm &>/dev/null; then
		# Удаляем глобально установленные пакеты
		npm uninstall -g typescript @angular/cli
	else
		log "INFO" "npm не установлен"
	fi

	# Удаление snap-пакетов для разработчиков
	log "INFO" "Удаление Snap пакетов для разработчиков"
	local snap_packages="postman insomnia mysql-workbench-community"
	
	for package in $snap_packages; do
		if snap list | grep -q "^$package "; then
			log "INFO" "Удаление Snap пакета: $package"
			snap remove "$package"
		else
			log "INFO" "Snap пакет $package не установлен"
		fi
	done
}

# Основная функция выполнения удаления
main() {
	log "INFO" "Запуск удаления роли: 50-dev-pack"
	
	# Выполнение удаления пакетов разработчика
	uninstall_dev_pack
	
	log "INFO" "Удаление роли 50-dev-pack завершено"
}

# Вызов основной функции
main "$@"