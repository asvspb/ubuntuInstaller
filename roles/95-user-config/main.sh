#!/bin/bash

# Роль: 95-user-config
# Назначение: Установка конфигурационных файлов из директории $USER в домашнюю директорию пользователя

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция установки конфигурационных файлов
install_user_config() {
	log "INFO" "Установка конфигурационных файлов из директории $USER в домашнюю директорию"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Установка конфигурационных файлов (не выполнена)"
		return 0
	fi

	# Определение домашней директории текущего пользователя
	local home_dir="$HOME"
	local user_dir="$SCRIPT_DIR/$USER"
	
	log "INFO" "Копирование конфигурационных файлов из $user_dir в $home_dir"
	
	# Создание резервной копии существующих файлов и копирование новых
	if [ -f "$user_dir/.bashrc" ]; then
		log "INFO" "Копирование .bashrc"
		# Создание резервной копии, если файл существует
		if [ -f "$home_dir/.bashrc" ]; then
			cp "$home_dir/.bashrc" "$home_dir/.bashrc.backup"
		fi
		cp "$user_dir/.bashrc" "$home_dir/.bashrc"
	fi
	
	if [ -f "$user_dir/.zshrc" ]; then
		log "INFO" "Копирование .zshrc"
		# Создание резервной копии, если файл существует
		if [ -f "$home_dir/.zshrc" ]; then
			cp "$home_dir/.zshrc" "$home_dir/.zshrc.backup"
		fi
		cp "$user_dir/.zshrc" "$home_dir/.zshrc"
	fi
	
	if [ -f "$user_dir/.gitconfig" ]; then
		log "INFO" "Копирование .gitconfig"
		# Создание резервной копии, если файл существует
		if [ -f "$home_dir/.gitconfig" ]; then
			cp "$home_dir/.gitconfig" "$home_dir/.gitconfig.backup"
		fi
		cp "$user_dir/.gitconfig" "$home_dir/.gitconfig"
	fi
	
	# Копирование скрипта clean-sys.sh
	if [ -f "$user_dir/clean-sys.sh" ]; then
		log "INFO" "Копирование clean-sys.sh"
		cp "$user_dir/clean-sys.sh" "$home_dir/clean-sys.sh"
		chmod +x "$home_dir/clean-sys.sh"
	fi
	
	# Копирование скрипта code-updater.sh
	if [ -f "$user_dir/code-updater.sh" ]; then
		log "INFO" "Копирование code-updater.sh"
		cp "$user_dir/code-updater.sh" "$home_dir/code-updater.sh"
		chmod +x "$home_dir/code-updater.sh"
	fi
	
	# Копирование директории .config если существует
	if [ -d "$user_dir/.config" ]; then
		log "INFO" "Копирование директории .config"
		# Создание резервной копии, если директория существует
		if [ -d "$home_dir/.config" ]; then
			cp -r "$home_dir/.config" "$home_dir/.config.backup"
		fi
		cp -r "$user_dir/.config" "$home_dir/"
	fi
	
	# Копирование директории .fonts если существует
	if [ -d "$user_dir/.fonts" ]; then
		log "INFO" "Копирование директории .fonts"
		# Создание резервной копии, если директория существует
		if [ -d "$home_dir/.fonts" ]; then
			cp -r "$home_dir/.fonts" "$home_dir/.fonts.backup"
		fi
		cp -r "$user_dir/.fonts" "$home_dir/"
	fi
	
	# Копирование директории .local если существует
	if [ -d "$user_dir/.local" ]; then
		log "INFO" "Копирование директории .local"
		# Создание резервной копии, если директория существует
		if [ -d "$home_dir/.local" ]; then
			cp -r "$home_dir/.local" "$home_dir/.local.backup"
		fi
		cp -r "$user_dir/.local" "$home_dir/"
	fi
	
	# Копирование директории Dev если существует
	if [ -d "$user_dir/Dev" ]; then
		log "INFO" "Копирование директории Dev"
		# Создание резервной копии, если директория существует
		if [ -d "$home_dir/Dev" ]; then
			cp -r "$home_dir/Dev" "$home_dir/Dev.backup"
		fi
		cp -r "$user_dir/Dev" "$home_dir/"
	fi
	
	# Копирование директории OpenRGB если существует
	if [ -d "$user_dir/OpenRGB" ]; then
		log "INFO" "Копирование директории OpenRGB"
		# Создание резервной копии, если директория существует
		if [ -d "$home_dir/OpenRGB" ]; then
			cp -r "$home_dir/OpenRGB" "$home_dir/OpenRGB.backup"
		fi
		cp -r "$user_dir/OpenRGB" "$home_dir/"
	fi
	
	# Копирование директории Templates если существует
	if [ -d "$user_dir/Templates" ]; then
		log "INFO" "Копирование директории Templates"
		# Создание резервной копии, если директория существует
		if [ -d "$home_dir/Templates" ]; then
			cp -r "$home_dir/Templates" "$home_dir/Templates.backup"
		fi
		cp -r "$user_dir/Templates" "$home_dir/"
	fi
	
	# Копирование директории themes если существует
	if [ -d "$user_dir/themes" ]; then
		log "INFO" "Копирование директории themes"
		# Создание резервной копии, если директория существует
		if [ -d "$home_dir/themes" ]; then
			cp -r "$home_dir/themes" "$home_dir/themes.backup"
		fi
		cp -r "$user_dir/themes" "$home_dir/"
	fi
	
	log "INFO" "Конфигурационные файлы успешно установлены"
}

# Основная функция выполнения роли
main() {
	log "INFO" "Запуск роли: 95-user-config"
	
	# Выполнение установки конфигурационных файлов
	install_user_config
	
	log "INFO" "Роль 95-user-config завершена"
}

# Вызов основной функции
main "$@"