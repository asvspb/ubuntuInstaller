#!/bin/bash

# Роль: 80-virtualbox
# Назначение: Установка VirtualBox и настройка среды для виртуальных машин

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция установки VirtualBox
install_virtualbox() {
	log "INFO" "Установка VirtualBox и настройка среды для виртуальных машин"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Установка VirtualBox (не выполнена)"
		return 0
	fi

	# Проверяем, установлен ли уже VirtualBox
	if is_pkg_installed "virtualbox"; then
		log "INFO" "VirtualBox уже установлен"
		return 0
	fi

	# Установка VirtualBox из репозитория Ubuntu
	log "INFO" "Установка VirtualBox из репозитория Ubuntu"
	install_packages "virtualbox virtualbox-ext-pack"

	# Установка дополнительных пакетов для разработки виртуальных машин
	log "INFO" "Установка дополнительных пакетов"
	install_packages "qemu-kvm virt-manager virtinst virt-viewer libvirt-daemon-system libvirt-clients bridge-utils"

	# Добавление текущего пользователя в группы libvirt и kvm
	if [ "$USER" != "root" ]; then
		if ! groups "$USER" | grep -q '\blibvirt\b'; then
			usermod -aG libvirt "$USER"
			log "INFO" "Пользователь $USER добавлен в группу libvirt"
		else
			log "INFO" "Пользователь $USER уже состоит в группе libvirt"
		fi

		if ! groups "$USER" | grep -q '\bkvm\b'; then
			usermod -aG kvm "$USER"
			log "INFO" "Пользователь $USER добавлен в группу kvm"
		else
			log "INFO" "Пользователь $USER уже состоит в группе kvm"
		fi
	fi

	# Включение и запуск сервисов libvirt
	systemctl enable libvirtd
	systemctl start libvirtd

	log "INFO" "VirtualBox и связанные компоненты установлены"
}

# Основная функция выполнения роли
main() {
	log "INFO" "Запуск роли: 80-virtualbox"
	
	# Выполнение установки VirtualBox
	install_virtualbox
	
	log "INFO" "Роль 80-virtualbox завершена"
}

# Вызов основной функции
main "$@"