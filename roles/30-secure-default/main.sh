#!/bin/bash

# Роль: 30-secure-default
# Назначение: Комплексная настройка безопасности системы по умолчанию

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция настройки брандмауэра UFW
setup_firewall() {
	log "INFO" "Настройка брандмауэра UFW"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Настройка брандмауэра UFW (не выполнена)"
		return 0
	fi
	
	# Установка UFW если не установлен
	ensure_pkg "ufw"
	
	# Базовые правила брандмауэра
	log "INFO" "Настройка базовых правил брандмауэра"
	
	# Запрет всего по умолчанию
	ufw default deny incoming
	ufw default allow outgoing
	
	# Разрешение SSH (если система десктопная или серверная)
	local system_type=$(detect_system_type)
	if [ "$system_type" != "WSL" ]; then
		ufw allow ssh
	fi
	
	# Включение брандмауэра
	ufw --force enable
	
	log "INFO" "Брандмауэр UFW настроен"
}

# Функция настройки автоматических обновлений
setup_auto_updates() {
	log "INFO" "Настройка автоматических обновлений"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Настройка автоматических обновлений (не выполнена)"
		return 0
	fi
	
	# Установка необходимых пакетов
	ensure_pkg "unattended-upgrades"
	
	# Настройка автоматических обновлений
	log "INFO" "Конфигурация автоматических обновлений"
	
	# Создание конфигурационного файла для автоматических обновлений
	cat > /etc/apt/apt.conf.d/50unattended-upgrades <<EOF
// Automatically upgrade packages from these (origin:archive) pairs
Unattended-Upgrade::Allowed-Origins {
	"\${distro_id}:\${distro_codename}";
	"\${distro_id}:\${distro_codename}-security";
	// Extended Security Maintenance; doesn't necessarily exist for
	// every release and this system may not have it installed, but if
	// available, the policy for updates is such that unattended-upgrades
	// doesn't break things
	"\${distro_id}ESMApps:\${distro_codename}-apps-security";
	"\${distro_id}ESM:\${distro_codename}-infra-security";
};

// List of packages to not update (regexp are supported)
Unattended-Upgrade::Package-Blacklist {
	// The following matches all packages starting with linux-
	//  "linux-";
	
	// Use \$ to exclude the end of a package name, for example:
	//  "bash$";
};

// This option allows you to control if on a unclean dpkg exit
// unattended-upgrades will automatically run
//   dpkg --force-confold --configure -a
// The default is true, to ensure updates keep getting installed
Unattended-Upgrade::AutoFixInterruptedDpkg "true";

// Split the upgrade into the smallest possible chunks so that
// they can be interrupted with SIGTERM. This makes the upgrade
// a bit slower but it has the benefit that shutdown while a upgrade
// is running is possible (with a small delay)
Unattended-Upgrade::MinimalSteps "true";

// Install all unattended-upgrades when the machine is shuting down
// instead of doing it in the background while the machine is running
// This will (obviously) make shutdown slower
Unattended-Upgrade::InstallOnShutdown "false";

// Send email to this address for problems or packages upgrades
// If empty or unset then no email is sent, make sure that you
// have a working mail setup on your system. A package that provides
// 'mailx' must be installed. E.g. "user@example.com"
//Unattended-Upgrade::Mail "";

// Set this value to "true" to get emails only on errors. Default
// is to always send a mail if Unattended-Upgrade::Mail is set
//Unattended-Upgrade::MailOnlyOnError "true";

// Remove unused automatically installed kernel-related packages (kernel images, kernel headers and kernel version locked tools).
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";

// Do automatic removal of new unused dependencies after the upgrade
// (equivalent to apt-get autoremove)
Unattended-Upgrade::Remove-Unused-Dependencies "true";

// Automatically reboot *WITHOUT CONFIRMATION*
//  if the file /var/run/reboot-required is found after the upgrade
Unattended-Upgrade::Automatic-Reboot "false";

// If automatic reboot is enabled and needed, reboot at the specific
// time instead of immediately
//  Default: "now"
//Unattended-Upgrade::Automatic-Reboot-Time "02:00";

// Use apt bandwidth limit feature, this example limits the download
// speed to 70kb/sec
//Acquire::http::Dl-Limit "70";

// Enable logging to syslog. Default is False
Unattended-Upgrade::SyslogEnable "true";

// Specify syslog facility. Default is daemon
Unattended-Upgrade::SyslogFacility "daemon";

// Download and install upgrades only on AC power
// (i.e. skip or gracefully stop updates on battery)
// For desktop computers, this will typically lead to the
// machine being upgraded when the user presses the save the next
// time. For systems that are always connected to AC power, this
// setting is not needed.
Unattended-Upgrade::SkipUpdatesOnMeteredConnection "true";
EOF

	# Создание конфигурационного файла периодического запуска
	cat > /etc/apt/apt.conf.d/20auto-upgrades <<EOF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

	log "INFO" "Автоматические обновления настроены"
}

# Функция усиления параметров аутентификации
harden_authentication() {
	log "INFO" "Усиление параметров аутентификации"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Усиление параметров аутентификации (не выполнена)"
		return 0
	fi
	
	# Установка необходимых пакетов для усиления аутентификации
	ensure_pkg "libpam-pwquality"
	
	# Настройка политики блокировки при неудачных попытках входа
	log "INFO" "Настройка политики блокировки учетных записей"
	if [ -f /etc/pam.d/common-auth ]; then
		# Создаем резервную копию
		cp /etc/pam.d/common-auth /etc/pam.d/common-auth.backup
		
		# Добавляем модуль pam_tally2 для отслеживания неудачных попыток
		if ! grep -q "pam_tally2.so" /etc/pam.d/common-auth; then
			sed -i '/auth\s\+[required|requisite]\s\+pam_unix.so/a auth        required                      pam_tally2.so onerr=fail audit silent deny=5 unlock_time=900' /etc/pam.d/common-auth
		fi
	fi
	
	# Настройка параметров сложности паролей
	log "INFO" "Настройка параметров сложности паролей"
	if [ -f /etc/pam.d/common-password ]; then
		# Создаем резервную копию
		cp /etc/pam.d/common-password /etc/pam.d/common-password.backup
		
		# Включаем проверку сложности паролей через pam_pwquality
		if ! grep -q "pam_pwquality.so" /etc/pam.d/common-password; then
			sed -i 's/nullok obscure use_authtok try_first_pass retry=3/nullok retry=3 minlen=12 difok=3 ucredit=-1 lcredit=-1 dcredit=-1 ocredit=-1/' /etc/pam.d/common-password
			sed -i '/password\s\+[success=1 default=ignore]\s\+pam_unix.so/a password    requisite                     pam_pwquality.so retry=3 minlen=12 difok=3 ucredit=-1 lcredit=-1 dcredit=-1 ocredit=-1' /etc/pam.d/common-password
		fi
	fi
	
	# Настройка времени блокировки учетной записи
	log "INFO" "Настройка времени блокировки учетных записей"
	if [ -f /etc/security/faillock.conf ]; then
		cp /etc/security/faillock.conf /etc/security/faillock.conf.backup
		echo "deny = 5" >> /etc/security/faillock.conf
		echo "unlock_time = 900" >> /etc/security/faillock.conf
		echo "audit" >> /etc/security/faillock.conf
	fi
	
	log "INFO" "Параметры аутентификации усилены"
}

# Функция настройки безопасности SSH
harden_ssh() {
	log "INFO" "Настройка безопасности SSH"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Настройка безопасности SSH (не выполнена)"
		return 0
	fi
	
	# Установка OpenSSH server если не установлен
	ensure_pkg "openssh-server"
	
	# Создаем резервную копию конфигурации SSH
	if [ -f /etc/ssh/sshd_config ]; then
		cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup
		
		# Настройка параметров безопасности SSH
		log "INFO" "Применение параметров безопасности SSH"
		
		# Запрет входа root по SSH
		sed -i 's/#PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
		sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
		
		# Отключение аутентификации по паролю (только по ключам)
		sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
		sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
		
		# Отключение X11 forwarding
		sed -i 's/X11Forwarding yes/X11Forwarding no/' /etc/ssh/sshd_config
		
		# Настройка таймаута соединения
		sed -i 's/#ClientAliveInterval 0/ClientAliveInterval 300/' /etc/ssh/sshd_config
		sed -i 's/#ClientAliveCountMax 3/ClientAliveCountMax 2/' /etc/ssh/sshd_config
		
		# Ограничение максимального количества аутентификационных попыток
		sed -i 's/#MaxAuthTries 6/MaxAuthTries 3/' /etc/ssh/sshd_config
		
		# Ограничение максимального количества сессий
		sed -i 's/#MaxSessions 10/MaxSessions 2/' /etc/ssh/sshd_config
		
		# Отключение пустых паролей
		sed -i 's/#PermitEmptyPasswords no/PermitEmptyPasswords no/' /etc/ssh/sshd_config
		
		# Использование только протокола SSH2
		sed -i 's/#Protocol 2/' /etc/ssh/sshd_config
		
		# Ограничение доступа к демону SSH только для определенных пользователей (опционально)
		# echo "AllowUsers yourusername" >> /etc/ssh/sshd_config
	fi
	
	# Перезапуск SSH сервиса для применения изменений
	systemctl restart ssh
	
	log "INFO" "Безопасность SSH настроена"
}

# Функция настройки аудита системы
setup_audit() {
	log "INFO" "Настройка аудита системы"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Настройка аудита системы (не выполнена)"
		return 0
	fi
	
	# Установка необходимых пакетов для аудита
	ensure_pkg "auditd audispd-plugins"
	
	# Настройка правил аудита
	log "INFO" "Настройка правил аудита"
	
	# Добавляем правила аудита в конфигурационный файл
	cat >> /etc/audit/rules.d/audit.rules <<EOF
# Правила аудита для безопасности

# Аудит доступа к файлам конфигурации
-w /etc/passwd -p wa -k identity
-w /etc/group -p wa -k identity
-w /etc/shadow -p wa -k identity
-w /etc/gshadow -p wa -k identity

# Аудит изменений в системных каталогах
-w /etc/sudoers -p wa -k privileged-actions
-w /etc/security -p wa -k privileged-actions

# Аудит использования привилегированных команд
-a always,exit -F path=/bin/su -F perm=x -F auid>=1000 -F auid!=4294967295 -k privileged-actions
-a always,exit -F path=/usr/bin/sudo -F perm=x -F auid>=1000 -F auid!=4294967295 -k privileged-actions

# Аудит сетевой активности
-a always,exit -F arch=b64 -S setsockopt -k network
-a always,exit -F arch=b32 -S setsockopt -k network
EOF

	# Перезапуск службы аудита
	systemctl restart auditd
	
	log "INFO" "Аудит системы настроен"
}

# Функция настройки безопасности ядра
harden_kernel() {
	log "INFO" "Настройка безопасности ядра"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Настройка безопасности ядра (не выполнена)"
		return 0
	fi
	
	# Настройка параметров безопасности ядра через sysctl
	log "INFO" "Настройка параметров безопасности ядра"
	
	# Создаем резервную копию текущих настроек
	if [ -f /etc/sysctl.conf ]; then
		cp /etc/sysctl.conf /etc/sysctl.conf.backup
	fi
	
	# Добавляем параметры безопасности ядра
	cat >> /etc/sysctl.conf <<EOF
# Параметры безопасности ядра

# Включение защиты от SYN flood
net.ipv4.tcp_syncookies=1

# Включение защиты от ICMP redirect
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.default.accept_redirects=0
net.ipv6.conf.all.accept_redirects=0
net.ipv6.conf.default.accept_redirects=0

# Включение защиты от IP spoofing
net.ipv4.conf.all.rp_filter=1
net.ipv4.conf.default.rp_filter=1

# Включение защиты от source routing
net.ipv4.conf.all.accept_source_route=0
net.ipv4.conf.default.accept_source_route=0
net.ipv6.conf.all.accept_source_route=0
net.ipv6.conf.default.accept_source_route=0

# Включение логгирования подозрительных пакетов
net.ipv4.conf.all.log_martians=1
net.ipv4.conf.default.log_martians=1

# Отключение ICMP broadcast
net.ipv4.icmp_echo_ignore_broadcasts=1

# Включение защиты от ICMP bogus error responses
net.ipv4.icmp_ignore_bogus_error_responses=1

# Ограничение максимального количества соединений на порт
net.core.somaxconn=1024

# Включение случайного расположения сегментов памяти (ASLR)
kernel.randomize_va_space=2

# Ограничение доступа к dmesg
kernel.dmesg_restrict=1

# Ограничение доступа к kptr
kernel.kptr_restrict=1

# Включение защиты от ptrace
kernel.yama.ptrace_scope=1
EOF

	# Применение настроек sysctl
	sysctl -p
	
	log "INFO" "Безопасность ядра настроена"
}

# Функция настройки безопасности файловой системы
harden_filesystem() {
	log "INFO" "Настройка безопасности файловой системы"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Настройка безопасности файловой системы (не выполнена)"
		return 0
	fi
	
	# Настройка прав доступа к критическим файлам
	log "INFO" "Настройка прав доступа к критическим файлам"
	
	# Защита файла shadow
	chmod 640 /etc/shadow
	chown root:shadow /etc/shadow
	
	# Защита файла passwd
	chmod 644 /etc/passwd
	chown root:root /etc/passwd
	
	# Защита файла group
	chmod 644 /etc/group
	chown root:root /etc/group
	
	# Защита файла sudoers
	chmod 440 /etc/sudoers
	chown root:root /etc/sudoers
	
	# Защита каталога ssh
	chmod 700 /etc/ssh
	chown root:root /etc/ssh
	
	log "INFO" "Безопасность файловой системы настроена"
}

# Функция настройки безопасности по умолчанию
setup_secure_defaults() {
	log "INFO" "Комплексная настройка безопасности системы по умолчанию"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Комплексная настройка безопасности (не выполнена)"
		return 0
	fi

	# Выполнение всех функций настройки безопасности
	setup_firewall
	setup_auto_updates
	harden_authentication
	harden_ssh
	setup_audit
	harden_kernel
	harden_filesystem
	
	log "INFO" "Комплексная настройка безопасности завершена"
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