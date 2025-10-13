#!/bin/bash

# Роль: 30-secure-default
# Назначение: Установка и настройка параметров безопасности по умолчанию

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция настройки безопасности по умолчанию
setup_secure_defaults() {
	log "INFO" "Настройка параметров безопасности по умолчанию"
	
	if [ "$DRY_RUN" = "true" ]; then
	log "INFO" "[DRY-RUN] Настройка безопасности по умолчанию (не выполнена)"
		return 0
	fi

	# Включение брандмауэра UFW
	log "INFO" "Включение брандмауэра UFW"
	ufw enable

	# Настройка автоматических обновлений
	log "INFO" "Настройка автоматических обновлений"
	apt install -y unattended-upgrades
	dpkg-reconfigure -plow unattended-upgrades

	# Настройка параметров аутентификации
	log "INFO" "Настройка параметров аутентификации"
	
	# Установка политики блокировки при неудачных попытках входа
	if [ -f /etc/pam.d/common-auth ]; then
	# Проверяем, не установлена ли уже настройка
		if ! grep -q "pam_tally2.so" /etc/pam.d/common-auth; then
			sed -i '/auth\s\+[required|requisite]\s\+pam_unix.so/a auth        required                      pam_tally2.so onerr=fail audit silent deny=5 unlock_time=900' /etc/pam.d/common-auth
		fi
	fi

	# Настройка параметров паролей
	if [ -f /etc/pam.d/common-password ]; then
	# Включаем проверку сложности паролей
		if ! grep -q "pam_pwquality.so" /etc/pam.d/common-password; then
			sed -i 's/nullok obscure use_authtok try_first_pass retry=3/nullok retry=3 minlen=8 difok=3/' /etc/pam.d/common-password
			sed -i '/password\s\+[success=1 default=ignore]\s\+pam_unix.so/a password    requisite                     pam_pwquality.so retry=3 minlen=8 difok=3' /etc/pam.d/common-password
		fi
	fi

	# Настройка параметров безопасности SSH
	log "INFO" "Настройка параметров безопасности SSH"
	
	# Создаем резервную копию конфигурации SSH
	if [ -f /etc/ssh/sshd_config ]; then
		cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup

		# Настройка параметров безопасности
		sed -i 's/#PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
		sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
		sed -i 's/#PasswordAuthentication yes/' /etc/ssh/sshd_config
		sed -i 's/X11Forwarding yes/X11Forwarding no/' /etc/ssh/sshd_config
		sed -i 's/#ClientAliveInterval 0/ClientAliveInterval 300/' /etc/ssh/sshd_config
		sed -i 's/#ClientAliveCountMax 3/ClientAliveCountMax 2/' /etc/ssh/sshd_config
	fi

	# Перезапуск SSH сервиса для применения изменений
	systemctl restart ssh

	log "INFO" "Настройка параметров безопасности завершена"
}

# Основная функция выполнения роли
main() {
	log "INFO" "Запуск роли: 30-secure-default"
	
	# Выполнение настройки безопасности по умолчанию
	setup_secure_defaults
	
	log "INFO" "Роль 30-secure-default завершена"
}

# Вызов основной функции
main "$@"