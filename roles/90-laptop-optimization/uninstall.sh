#!/bin/bash

# Скрипт удаления для роли 90-laptop-optimization
# Откатывает оптимизации для ноутбуков, установленные в main.sh

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция отката оптимизаций для ноутбуков
uninstall_laptop_optimization() {
	log "INFO" "Откат оптимизаций для ноутбуков"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Откат оптимизаций для ноутбуков (не выполнено)"
		return 0
	fi

	# Определение типа системы (ноутбук/десктоп)
	local form_factor=$(detect_system_form_factor)
	log "INFO" "Тип системы (форм-фактор): $form_factor"
	
	# Откат только для ноутбуков
	if [ "$form_factor" != "laptop" ]; then
		log "INFO" "Система не является ноутбуком, пропуск отката оптимизаций"
		return 0
	fi

	# Отключение и удаление TLP
	log "INFO" "Отключение и удаление TLP"
	systemctl stop tlp 2>/dev/null || true
	systemctl disable tlp 2>/dev/null || true
	apt remove -y --purge tlp tlp-rdw

	# Удаление powertop
	log "INFO" "Удаление powertop"
	apt remove -y --purge powertop

	# Удаление конфигурационных файлов TLP
	log "INFO" "Удаление конфигурационных файлов TLP"
	if [ -d /etc/tlp.d ]; then
		rm -rf /etc/tlp.d
	fi

	if [ -f /etc/default/tlp ]; then
		rm -f /etc/default/tlp
	fi

	# Удаление временных файлов powertop (если есть)
	log "INFO" "Удаление временных файлов powertop"
	if [ -f /tmp/powertop.calibrate ]; then
		rm -f /tmp/powertop.calibrate
	fi

	log "INFO" "Оптимизации для ноутбуков откачены"
}

# Основная функция выполнения удаления
main() {
	log "INFO" "Запуск удаления роли: 90-laptop-optimization"
	
	# Выполнение отката оптимизаций для ноутбуков
	uninstall_laptop_optimization
	
	log "INFO" "Удаление роли 90-laptop-optimization завершено"
}

# Вызов основной функции
main "$@"