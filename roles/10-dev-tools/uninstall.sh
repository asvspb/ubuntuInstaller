#!/bin/bash

# Скрипт удаления для роли 10-dev-tools
# Удаляет пакеты и откатывает настройки, установленные в main.sh

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция удаления инструментов разработчика
uninstall_dev_tools() {
	log "INFO" "Удаление инструментов разработчика"

	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Удаление инструментов разработчика (не выполнено)"
		return 0
	fi

	# Получение переменных роли
	local install_vscode=$(echo "$UBUNTU_INSTALLER_ROLE_VARS" | yq '.install_vscode // true' 2>/dev/null || echo "true")
	local install_pycharm=$(echo "$UBUNTU_INSTALLER_ROLE_VARS" | yq '.install_pycharm // false' 2>/dev/null || echo "false")

	# Удаление базовых инструментов разработки
	log "INFO" "Удаление базовых инструментов разработки"
	local dev_packages="build-essential cmake gcc g++ gdb valgrind git git-lfs gh nodejs npm python3 python3-pip python3-venv python3-dev default-jdk docker.io"
	apt remove -y --purge $dev_packages

	# Удаление VSCode если был установлен
	if [ "$install_vscode" = "true" ]; then
		log "INFO" "Удаление Visual Studio Code"
		if is_pkg_installed "code"; then
			apt remove -y --purge code
			# Удаление репозитория и ключа
			sudo rm -f /etc/apt/keyrings/packages.microsoft.gpg
			sudo rm -f /etc/apt/sources.list.d/vscode.list
		else
			log "INFO" "Visual Studio Code не установлен"
		fi
	else
		log "INFO" "Удаление Visual Studio Code пропущено согласно конфигурации"
	fi

	# Удаление PyCharm если был установлен
	if [ "$install_pycharm" = "true" ]; then
		log "INFO" "Удаление PyCharm Community Edition"
		if snap list | grep -q "^pycharm-community "; then
			sudo snap remove pycharm-community
		else
			log "INFO" "PyCharm Community Edition не установлен"
		fi
	else
		log "INFO" "Удаление PyCharm пропущено согласно конфигурации"
	fi

	# Удаление инструментов через pipx
	log "INFO" "Удаление инструментов через pipx"
	
	# Проверяем, установлены ли инструменты
	if pipx list | grep -q "black"; then
		pipx uninstall black
		log "INFO" "black удален через pipx"
	else
		log "INFO" "black не установлен через pipx"
	fi
	
	if pipx list | grep -q "flake8"; then
		pipx uninstall flake8
		log "INFO" "flake8 удален через pipx"
	else
		log "INFO" "flake8 не установлен через pipx"
	fi
	
	if pipx list | grep -q "autopep8"; then
		pipx uninstall autopep8
		log "INFO" "autopep8 удален через pipx"
	else
		log "INFO" "autopep8 не установлен через pipx"
	fi

	log "INFO" "Инструменты разработчика удалены"
}

# Основная функция выполнения удаления
main() {
	log "INFO" "Запуск удаления роли: 10-dev-tools"
	
	# Выполнение удаления инструментов разработчика
	uninstall_dev_tools
	
	log "INFO" "Удаление роли 10-dev-tools завершено"
}

# Вызов основной функции
main "$@"