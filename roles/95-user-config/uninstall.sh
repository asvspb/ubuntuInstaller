#!/bin/bash

# Роль: 95-user-config
# Назначение: Удаление конфигурационных файлов из домашней директории пользователя

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция удаления конфигурационных файлов
uninstall_user_config() {
	log "INFO" "Удаление конфигурационных файлов из домашней директории"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Удаление конфигурационных файлов (не выполнено)"
		return 0
	fi

	# Определение домашней директории текущего пользователя
	local home_dir="$HOME"
	
	log "INFO" "Удаление конфигурационных файлов из $home_dir"
	
	# Восстановление резервных копий файлов
	if [ -f "$home_dir/.bashrc.backup" ]; then
	log "INFO" "Восстановление резервной копии .bashrc"
		mv "$home_dir/.bashrc.backup" "$home_dir/.bashrc"
	elif [ -f "$home_dir/.bashrc" ]; then
		log "INFO" "Удаление .bashrc (оригинального файла не было)"
		rm -f "$home_dir/.bashrc"
	fi
	
	if [ -f "$home_dir/.zshrc.backup" ]; then
		log "INFO" "Восстановление резервной копии .zshrc"
		mv "$home_dir/.zshrc.backup" "$home_dir/.zshrc"
	elif [ -f "$home_dir/.zshrc" ]; then
	log "INFO" "Удаление .zshrc (оригинального файла не было)"
		rm -f "$home_dir/.zshrc"
	fi
	
	if [ -f "$home_dir/.gitconfig.backup" ]; then
		log "INFO" "Восстановление резервной копии .gitconfig"
		mv "$home_dir/.gitconfig.backup" "$home_dir/.gitconfig"
	elif [ -f "$home_dir/.gitconfig" ]; then
	log "INFO" "Удаление .gitconfig (оригинального файла не было)"
		rm -f "$home_dir/.gitconfig"
	fi
	
	# Удаление скриптов
	if [ -f "$home_dir/clean-sys.sh" ]; then
		log "INFO" "Удаление clean-sys.sh"
		rm -f "$home_dir/clean-sys.sh"
	fi
	
	if [ -f "$home_dir/code-updater.sh" ]; then
		log "INFO" "Удаление code-updater.sh"
		rm -f "$home_dir/code-updater.sh"
	fi
	
	# Восстановление директории .config из резервной копии
	if [ -d "$home_dir/.config.backup" ]; then
		log "INFO" "Восстановление резервной копии директории .config"
		rm -rf "$home_dir/.config"
		mv "$home_dir/.config.backup" "$home_dir/.config"
	elif [ -d "$home_dir/.config" ]; then
		log "INFO" "Удаление директории .config (оригинальной директории не было)"
		rm -rf "$home_dir/.config"
	fi
	
	# Восстановление директории .fonts из резервной копии
	if [ -d "$home_dir/.fonts.backup" ]; then
	log "INFO" "Восстановление резервной копии директории .fonts"
		rm -rf "$home_dir/.fonts"
		mv "$home_dir/.fonts.backup" "$home_dir/.fonts"
	elif [ -d "$home_dir/.fonts" ]; then
		log "INFO" "Удаление директории .fonts (оригинальной директории не было)"
		rm -rf "$home_dir/.fonts"
	fi
	
	# Восстановление директории .local из резервной копии
	if [ -d "$home_dir/.local.backup" ]; then
	log "INFO" "Восстановление резервной копии директории .local"
		rm -rf "$home_dir/.local"
		mv "$home_dir/.local.backup" "$home_dir/.local"
	elif [ -d "$home_dir/.local" ]; then
		log "INFO" "Удаление директории .local (оригинальной директории не было)"
		rm -rf "$home_dir/.local"
	fi
	
	# Восстановление директории Dev из резервной копии
	if [ -d "$home_dir/Dev.backup" ]; then
		log "INFO" "Восстановление резервной копии директории Dev"
		rm -rf "$home_dir/Dev"
		mv "$home_dir/Dev.backup" "$home_dir/Dev"
	elif [ -d "$home_dir/Dev" ]; then
		log "INFO" "Удаление директории Dev (оригинальной директории не было)"
		rm -rf "$home_dir/Dev"
	fi
	
	# Восстановление директории OpenRGB из резервной копии
	if [ -d "$home_dir/OpenRGB.backup" ]; then
		log "INFO" "Восстановление резервной копии директории OpenRGB"
		rm -rf "$home_dir/OpenRGB"
		mv "$home_dir/OpenRGB.backup" "$home_dir/OpenRGB"
	elif [ -d "$home_dir/OpenRGB" ]; then
		log "INFO" "Удаление директории OpenRGB (оригинальной директории не было)"
		rm -rf "$home_dir/OpenRGB"
	fi
	
	# Восстановление директории Templates из резервной копии
	if [ -d "$home_dir/Templates.backup" ]; then
	log "INFO" "Восстановление резервной копии директории Templates"
	rm -rf "$home_dir/Templates"
		mv "$home_dir/Templates.backup" "$home_dir/Templates"
	elif [ -d "$home_dir/Templates" ]; then
		log "INFO" "Удаление директории Templates (оригинальной директории не было)"
		rm -rf "$home_dir/Templates"
	fi
	
	# Восстановление директории themes из резервной копии
	if [ -d "$home_dir/themes.backup" ]; then
	log "INFO" "Восстановление резервной копии директории themes"
	rm -rf "$home_dir/themes"
	mv "$home_dir/themes.backup" "$home_dir/themes"
	elif [ -d "$home_dir/themes" ]; then
		log "INFO" "Удаление директории themes (оригинальной директории не было)"
		rm -rf "$home_dir/themes"
	fi
	
	log "INFO" "Конфигурационные файлы успешно удалены"
}

# Основная функция выполнения роли
main() {
	log "INFO" "Запуск удаления роли: 95-user-config"
	
	# Выполнение удаления конфигурационных файлов
	uninstall_user_config
	
	log "INFO" "Роль 95-user-config успешно удалена"
}

# Вызов основной функции
main "$@"