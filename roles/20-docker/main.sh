#!/bin/bash

# Роль: 20-docker
# Назначение: Установка и настройка Docker и связанных инструментов

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция установки Docker
install_docker() {
	log "INFO" "Установка Docker"

	if [ "$DRY_RUN" = "true" ]; then
		log "INFO" "[DRY-RUN] Установка Docker (не выполнена)"
		return 0
	fi

	# Определение типа системы
	local system_type=$(detect_system_type)
	log "INFO" "Тип системы: $system_type"

	# Проверка, поддерживается ли Docker в текущей системе
	if [ "$system_type" = "WSL" ]; then
		log "WARN" "Docker не поддерживается в WSL по умолчанию, пропуск установки"
		return 0
	fi

	# Удаление старых версий Docker (если есть)
	if is_pkg_installed "docker"; then
	apt remove -y docker docker-engine docker.io containerd runc
	fi

	# Установка зависимостей
	apt update
	apt install -y ca-certificates curl gnupg lsb-release

	# Добавление официального GPG ключа Docker
	if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
		mkdir -p /etc/apt/keyrings
		curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
	fi

	# Установка репозитория Docker
	local codename=$(lsb_release -cs)
	echo \
		"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
		$codename stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

	# Установка Docker
	apt update
	apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

	# Добавление текущего пользователя в группу docker (только если его там еще нет)
	if [ "$USER" != "root" ]; then
		if ! groups "$USER" | grep -q '\bdocker\b'; then
			usermod -aG docker "$USER"
			log "INFO" "Пользователь $USER добавлен в группу docker"
	else
			log "INFO" "Пользователь $USER уже состоит в группе docker"
		fi
	fi

	# Включение и запуск Docker сервиса
	systemctl enable docker
	systemctl start docker
}

# Функция настройки Docker
setup_docker() {
	log "INFO" "Настройка Docker"

	if [ "$DRY_RUN" = "true" ]; then
	log "INFO" "[DRY-RUN] Настройка Docker (не выполнена)"
		return 0
	fi

	# Создание директории для конфигурации Docker
	mkdir -p /etc/docker

	# Настройка daemon.json для дополнительных параметров
	cat > /tmp/daemon.json <<EOF
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "default-ulimits": {
    "nofile": {
      "Hard": 64000,
      "Name": "nofile",
      "Soft": 64000
    }
  }
}
EOF

	# Копирование конфигурации daemon.json (только если файл отличается)
	if ! cmp -s /tmp/daemon.json /etc/docker/daemon.json; then
		cp /tmp/daemon.json /etc/docker/daemon.json
	# Перезапуск Docker для применения настроек
		systemctl restart docker
	fi

	# Проверка работоспособности Docker (только в реальном режиме, не в симуляции)
	if [ "$DRY_RUN" != "true" ]; then
	log "INFO" "Проверка работоспособности Docker"
		if systemctl is-active --quiet docker; then
			docker run --rm hello-world
			log "INFO" "Проверка Docker прошла успешно"
	else
			log "WARN" "Сервис Docker не запущен, пропуск проверки"
		fi
	fi
}

# Основная функция выполнения роли
main() {
	log "INFO" "Запуск роли: 20-docker"

	# Выполнение установки Docker
	install_docker

	# Настройка Docker
	setup_docker

	log "INFO" "Роль 20-docker завершена"
}

# Вызов основной функции
main "$@"