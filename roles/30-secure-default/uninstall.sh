#!/bin/bash

# Скрипт удаления для роли 30-secure-default
# Откатывает настройки безопасности, установленные в main.sh

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция отката настроек безопасности
uninstall_secure_defaults() {
	log "INFO" "Откат настроек безопасности по умолчанию"

	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Откат настроек безопасности (не выполнено)"
		return 0
	fi

	# Отключение брандмауэра UFW
	log "INFO" "Отключение брандмауэра UFW"
	ufw --force disable

	# Отключение автоматических обновлений
	log "INFO" "Отключение автоматических обновлений"
	apt remove -y unattended-upgrades

	# Откат настроек аутентификации (если файлы были изменены)
	log "INFO" "Откат настроек аутентификации"
	
	# Восстановление оригинального файла common-auth, если есть резервная копия
	if [ -f /etc/pam.d/common-auth.backup ]; then
		cp /etc/pam.d/common-auth.backup /etc/pam.d/common-auth
		rm /etc/pam.d/common-auth.backup
	fi

	# Восстановление оригинального файла common-password, если есть резервная копия
	if [ -f /etc/pam.d/common-password.backup ]; then
		cp /etc/pam.d/common-password.backup /etc/pam.d/common-password
		rm /etc/pam.d/common-password.backup
	fi

	# Откат настроек SSH (восстановление резервной копии, если она существует)
	if [ -f /etc/ssh/sshd_config.backup ]; then
		cp /etc/ssh/sshd_config.backup /etc/ssh/sshd_config
		systemctl restart ssh
		rm /etc/ssh/sshd_config.backup
	fi

	log "INFO" "Настройки безопасности откачены"
}

# Основная функция выполнения удаления
main() {
	log "INFO" "Запуск удаления роли: 30-secure-default"
	
	# Выполнение отката настроек безопасности
	uninstall_secure_defaults
	
	log "INFO" "Удаление роли 30-secure-default завершено"
}

# Вызов основной функции
main "$@"