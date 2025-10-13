#!/bin/bash

# Скрипт удаления для роли 80-virtualbox
# Удаляет VirtualBox и связанные компоненты, установленные в main.sh

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция удаления VirtualBox
uninstall_virtualbox() {
	log "INFO" "Удаление VirtualBox и связанных компонентов"
	
	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Удаление VirtualBox (не выполнено)"
		return 0
	fi

	# Остановка и отключение сервисов VirtualBox
	log "INFO" "Остановка и отключение сервисов VirtualBox"
	systemctl stop vboxdrv 2>/dev/null || true
	systemctl disable vboxdrv 2>/dev/null || true

	# Удаление пакетов VirtualBox
	log "INFO" "Удаление пакетов VirtualBox"
	local vbox_packages="virtualbox virtualbox-ext-pack virtualbox-dkms"
	
	for package in $vbox_packages; do
		if is_pkg_installed "$package"; then
			log "INFO" "Удаление пакета: $package"
			apt remove -y --purge "$package"
		else
			log "INFO" "Пакет $package не установлен"
		fi
	done

	# Удаление дополнительных пакетов для виртуальных машин
	log "INFO" "Удаление дополнительных пакетов для виртуальных машин"
	local vm_packages="qemu-kvm virt-manager virtinst virt-viewer libvirt-daemon-system libvirt-clients bridge-utils"
	
	for package in $vm_packages; do
		if is_pkg_installed "$package"; then
			log "INFO" "Удаление пакета: $package"
			apt remove -y --purge "$package"
		else
			log "INFO" "Пакет $package не установлен"
		fi
	done

	# Удаление пользователя из групп libvirt и kvm (если он там состоит)
	if [ "$USER" != "root" ]; then
		if groups "$USER" | grep -q '\blibvirt\b'; then
			log "INFO" "Удаление пользователя $USER из группы libvirt"
			gpasswd -d "$USER" libvirt
		else
			log "INFO" "Пользователь $USER не состоит в группе libvirt"
		fi

		if groups "$USER" | grep -q '\bkvm\b'; then
			log "INFO" "Удаление пользователя $USER из группы kvm"
			gpasswd -d "$USER" kvm
		else
			log "INFO" "Пользователь $USER не состоит в группе kvm"
		fi
	fi

	# Остановка и отключение сервисов libvirt
	log "INFO" "Остановка и отключение сервисов libvirt"
	systemctl stop libvirtd 2>/dev/null || true
	systemctl disable libvirtd 2>/dev/null || true

	# Удаление конфигурационных файлов и директорий
	log "INFO" "Удаление конфигурационных файлов VirtualBox"
	if [ -d /etc/vbox ]; then
		rm -rf /etc/vbox
	fi

	if [ -d /var/lib/vbox ]; then
		rm -rf /var/lib/vbox
	fi

	if [ -d /etc/libvirt ]; then
		rm -rf /etc/libvirt
	fi

	if [ -d /var/lib/libvirt ]; then
		rm -rf /var/lib/libvirt
	fi

	log "INFO" "VirtualBox и связанные компоненты удалены"
}

# Основная функция выполнения удаления
main() {
	log "INFO" "Запуск удаления роли: 80-virtualbox"
	
	# Выполнение удаления VirtualBox
	uninstall_virtualbox
	
	log "INFO" "Удаление роли 80-virtualbox завершено"
}

# Вызов основной функции
main "$@"