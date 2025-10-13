#!/bin/bash

# Роль: 90-laptop-optimization
# Назначение: Установка и настройка оптимизации для ноутбуков (TLP и другие утилиты)

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция установки и настройки оптимизации для ноутбуков
install_laptop_optimization() {
	log "INFO" "Установка и настройка оптимизации для ноутбуков"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Установка оптимизации для ноутбуков (не выполнена)"
		return 0
	fi

	# Определение типа системы (ноутбук/десктоп)
	local form_factor=$(detect_system_form_factor)
	log "INFO" "Тип системы (форм-фактор): $form_factor"
	
	# Установка только для ноутбуков
	if [ "$form_factor" != "laptop" ]; then
		log "INFO" "Система не является ноутбуком, пропуск установки оптимизации"
		return 0
	fi

	# Установка TLP для оптимизации энергопотребления
	log "INFO" "Установка TLP для оптимизации энергопотребления"
	install_packages "tlp tlp-rdw"
	
	# Включение и запуск TLP
	systemctl enable tlp
	systemctl start tlp
	
	# Установка powertop для дополнительной оптимизации
	log "INFO" "Установка powertop для анализа энергопотребления"
	install_packages "powertop"
	
	# Автоматическая калибровка powertop (в фоновом режиме)
	if [ "$DRY_RUN" != "true" ]; then
	powertop --calibrate &
	fi
}

# Основная функция выполнения роли
main() {
	log "INFO" "Запуск роли: 90-laptop-optimization"
	
	# Выполнение установки и настройки оптимизации для ноутбуков
	install_laptop_optimization
	
	log "INFO" "Роль 90-laptop-optimization завершена"
}

# Вызов основной функции
main "$@"