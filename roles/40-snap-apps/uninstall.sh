#!/bin/bash

# Скрипт удаления для роли 40-snap-apps
# Удаляет snap-приложения, установленные в main.sh

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция удаления snap-приложений
uninstall_snap_apps() {
	log "INFO" "Удаление Snap приложений"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Удаление Snap приложений (не выполнено)"
		return 0
	fi

	# Удаление списка snap приложений из файла
	if [ -f "$SCRIPT_DIR/scripts/ubuntu_snap_packages.txt" ]; then
		while IFS= read -r app || [ -n "$app" ]; do
			# Пропускаем пустые строки и комментарии
			if [[ -n "$app" && ! "$app" =~ ^[[:space:]]*# ]]; then
				# Удаляем лишние пробелы
				app=$(echo "$app" | xargs)
				if [ -n "$app" ]; then
					# Проверяем, установлено ли приложение
					if snap list | grep -q "^$app "; then
						log "INFO" "Удаление Snap приложения: $app"
						sudo snap remove "$app"
					else
						log "INFO" "Snap приложение $app не установлено"
					fi
				fi
			fi
		done < "$SCRIPT_DIR/scripts/ubuntu_snap_packages.txt"
	else
		log "WARN" "Файл со списком snap пакетов не найден: $SCRIPT_DIR/scripts/ubuntu_snap_packages.txt"
	fi
}

# Основная функция выполнения удаления
main() {
	log "INFO" "Запуск удаления роли: 40-snap-apps"
	
	# Выполнение удаления snap-приложений
	uninstall_snap_apps
	
	log "INFO" "Удаление роли 40-snap-apps завершено"
}

# Вызов основной функции
main "$@"