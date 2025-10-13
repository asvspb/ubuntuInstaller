#!/bin/bash

# Скрипт удаления для роли 60-samsung-printer
# Удаляет драйверы принтера Samsung, установленные в main.sh

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция удаления драйверов Samsung принтера
uninstall_samsung_printer() {
	log "INFO" "Удаление драйверов Samsung принтера"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Удаление драйверов Samsung принтера (не выполнено)"
		return 0
	fi

	# Удаление пакетов Samsung принтера
	log "INFO" "Удаление пакетов Samsung принтера"
	local samsung_packages="samsungmfp-driver-common samsungmfp-scanner samsungmfp-printer"
	
	for package in $samsung_packages; do
		if is_pkg_installed "$package"; then
			log "INFO" "Удаление пакета: $package"
			apt remove -y --purge "$package"
		else
			log "INFO" "Пакет $package не установлен"
		fi
	done

	# Удаление универсальных драйверов принтеров
	log "INFO" "Удаление универсальных драйверов принтеров"
	local printer_packages="printer-driver-splix printer-driver-hpcups hplip"
	
	for package in $printer_packages; do
		if is_pkg_installed "$package"; then
			log "INFO" "Удаление пакета: $package"
			apt remove -y --purge "$package"
		else
			log "INFO" "Пакет $package не установлен"
		fi
	done

	# Удаление временных файлов (если они остались)
	if [ -d "/tmp/samsung_printer_driver" ]; then
		log "INFO" "Удаление временных файлов"
		rm -rf "/tmp/samsung_printer_driver"
	fi
}

# Основная функция выполнения удаления
main() {
	log "INFO" "Запуск удаления роли: 60-samsung-printer"
	
	# Выполнение удаления драйверов Samsung принтера
	uninstall_samsung_printer
	
	log "INFO" "Удаление роли 60-samsung-printer завершено"
}

# Вызов основной функции
main "$@"