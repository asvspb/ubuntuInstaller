#!/bin/bash

# Роль: 00-base-system
# Назначение: Установка базовой системы и настройка основных параметров

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция установки базовой системы
install_base_system() {
	log "INFO" "Установка базовой системы"

	if [ "$DRY_RUN" = "true" ]; then
	log "INFO" "[DRY-RUN] Обновление списка пакетов (не выполнено)"
		log "INFO" "[DRY-RUN] Установка базовых пакетов (не выполнена)"
		return 0
	fi

	# Обновление списка пакетов
	log "INFO" "Обновление списка пакетов"
	apt update

	# Определение типа системы
	local system_type=$(detect_system_type)
	log "INFO" "Тип системы: $system_type"

	# Установка базовых пакетов
	log "INFO" "Установка базовых пакетов"
	local base_packages="git gh mc tmux zsh mosh curl wget ca-certificates net-tools make apt-transport-https gpg gnupg ubuntu-restricted-extras ncdu ranger btop iftop htop neofetch rpm wireguard jq pipx inxi cpu-x tldr fzf alacarte grub-customizer gparted synaptic nala"
	
	# Условная установка пакетов в зависимости от типа системы
	if [ "$system_type" != "server" ] && [ "$system_type" != "WSL" ]; then
	# Добавляем GUI-зависимые пакеты для десктопных систем
		base_packages="$base_packages dconf-editor gnome-shell-extensions gnome-tweaks guake copyq xclip openrgb ufw timeshift"
	fi

	apt install -y $base_packages

	# Установка snap пакетов (только не для WSL)
	if [ "$system_type" != "WSL" ]; then
		log "INFO" "Установка базовых Snap пакетов"
		ensure_snap_pkg telegram-desktop
	else
		log "INFO" "Пропуск установки Snap пакетов для WSL"
	fi
}

# Функция настройки безопасности
setup_security() {
	log "INFO" "Настройка базовой безопасности"

	# Установка переменной DRY_RUN из окружения
	local DRY_RUN="${UBUNTU_INSTALLER_DRY_RUN:-false}"

	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Настройка безопасности (не выполнена)"
		return 0
	fi

	# Включение брандмауэра UFW
	ufw enable

	# Настройка автоматических обновлений
	apt install -y unattended-upgrades
	dpkg-reconfigure -plow unattended-upgrades
}

# Функция настройки sudo без пароля (опционально)
setup_sudo_nopasswd() {
	log "INFO" "Настройка sudo без пароля"
	
	# Установка переменной DRY_RUN из окружения
	local DRY_RUN="${UBUNTU_INSTALLER_DRY_RUN:-false}"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Настройка sudo без пароля (не выполнена)"
		return 0
	fi
	
	# Проверка существования записи в sudoers.d
	if ! grep -q "${USER} ALL=(ALL) NOPASSWD:ALL" /etc/sudoers.d/90-nopasswd 2>/dev/null; then
		# Создание файла sudoers.d для пользователя
		echo "${USER} ALL=(ALL) NOPASSWD:ALL" | tee /etc/sudoers.d/90-nopasswd
		chmod 0440 /etc/sudoers.d/90-nopasswd
		log "INFO" "Запись для ${USER} добавлена в /etc/sudoers.d/90-nopasswd"
	else
		log "INFO" "Запись для ${USER} уже существует в /etc/sudoers.d/90-nopasswd"
	fi
}

# Функция настройки системных параметров
setup_system_settings() {
	log "INFO" "Настройка системных параметров"

	# Установка переменной DRY_RUN из окружения
	local DRY_RUN="${UBUNTU_INSTALLER_DRY_RUN:-false}"

	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Настройка системных параметров (не выполнена)"
		return 0
	fi

	# Установка времени - только если не установлена нужная настройка
if ! timedatectl status | grep -q "RTC in local TZ: yes"; then
		timedatectl set-local-rtc 1 --adjust-system-clock
		log "INFO" "Установлена настройка RTC в локальном часовом поясе"
	else
		log "INFO" "Настройка RTC в локальном часовом поясе уже установлена"
	fi

	# Настройка параметров GNOME
	if command -v gsettings &>/dev/null; then
		gsettings set org.gnome.shell.extensions.dash-to-dock click-action 'minimize'
	fi

		# Настройка параметров перезапуска служб
		export DEBIAN_FRONTEND=noninteractive
		if [ -f /etc/needrestart/needrestart.conf ]; then
			# Проверяем, не закомментирована ли уже строка и не установлена ли нужная настройка
			if ! grep -q '^\$nrconf{restart} = '\''a'\'';' /etc/needrestart/needrestart.conf; then
				sed -i '/\$nrconf{restart}/s/^#//g' /etc/needrestart/needrestart.conf
				sed -i "/nrconf{restart}/s/'i'/'a'/g" /etc/needrestart/needrestart.conf
				log "INFO" "Настройка автоматического перезапуска служб обновлена"
			else
				log "INFO" "Настройка автоматического перезапуска служб уже установлена"
			fi
		else
			mkdir -p /etc/needrestart
			echo '$nrconf{restart} = '\''a'\'';' >/etc/needrestart/needrestart.conf
			log "INFO" "Файл настроек needrestart создан"
		fi

	# Настройка SSH
	mkdir -p ~/.ssh
	chmod 0700 ~/.ssh
	cat <<EOF >~/.ssh/config
Host gitlab.com
  StrictHostKeyChecking no
  UserKnownHostsFile=/dev/null
EOF
}

# Основная функция выполнения роли
main() {
	log "INFO" "Запуск роли: 00-base-system"

	# Выполнение установки базовой системы
	install_base_system

	# Настройка безопасности
	setup_security

	# Настройка sudo без пароля
	setup_sudo_nopasswd

	# Настройка системных параметров
	setup_system_settings

	log "INFO" "Роль 00-base-system завершена"
}

# Вызов основной функции
main "$@"
