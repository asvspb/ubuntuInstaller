#!/bin/bash

# Роль: 70-zerotier-client
# Назначение: Установка ZeroTier клиента для Ubuntu

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция установки ZeroTier клиента
install_zerotier_client() {
	log "INFO" "Установка ZeroTier клиента для Ubuntu"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Установка ZeroTier клиента (не выполнена)"
		return 0
	fi

	# Проверяем, установлен ли уже ZeroTier
	if is_pkg_installed "zerotier-one"; then
		log "INFO" "ZeroTier уже установлен"
		return 0
	fi

	# Скачивание и установка ZeroTier
	log "INFO" "Скачивание и установка ZeroTier"
	
	# Добавление официального GPG ключа ZeroTier
	local pubkey_url="https://raw.githubusercontent.com/zerotier/ZeroTierOne/main/doc/contact%40zerotier.com.gpg"
	local pubkey_file="/tmp/zerotier-key.gpg"
	
	if curl -L -o "$pubkey_file" "$pubkey_url"; then
		gpg --import "$pubkey_file"
		rm -f "$pubkey_file"
	else
		log "WARN" "Не удалось скачать публичный ключ ZeroTier, используем альтернативный способ"
	fi

	# Установка ZeroTier через скрипт
	if curl -s https://install.zerotier.com | bash; then
	log "INFO" "ZeroTier успешно установлен"
	else
		log "ERROR" "Не удалось установить ZeroTier через скрипт, пробуем через apt"
		# Добавление репозитория ZeroTier
		local codename=$(lsb_release -cs)
		echo "deb http://download.zerotier.com/debian/$codename $codename main" | sudo tee /etc/apt/sources.list.d/zerotier.list
		
		# Обновление списка пакетов
	apt update
		
		# Установка пакета
		install_packages "zerotier-one"
	fi

	# Включение и запуск сервиса ZeroTier
	systemctl enable zerotier-one
	systemctl start zerotier-one
}

# Основная функция выполнения роли
main() {
	log "INFO" "Запуск роли: 70-zerotier-client"
	
	# Выполнение установки ZeroTier клиента
	install_zerotier_client
	
	log "INFO" "Роль 70-zerotier-client завершена"
}

# Вызов основной функции
main "$@"