#!/bin/bash

# Скрипт удаления для роли 20-docker
# Удаляет Docker и связанные компоненты

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция удаления Docker
uninstall_docker() {
  log "INFO" "Удаление Docker и связанных компонентов"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Удаление Docker (не выполнено)"
    return 0
  fi

  # Определение типа системы
  local system_type=$(detect_system_type)
  log "INFO" "Тип системы: $system_type"

  # Проверка, была ли установка Docker пропущена из-за WSL
  if [ "$system_type" = "WSL" ]; then
    log "INFO" "Docker не устанавливался в WSL, пропуск удаления"
    return 0
  fi

  # Остановка и отключение Docker сервиса
  log "INFO" "Остановка и отключение Docker сервиса"
  systemctl stop docker 2>/dev/null || true
  systemctl disable docker 2>/dev/null || true

  # Удаление текущего пользователя из группы docker
  if [ "$USER" != "root" ]; then
    if groups "$USER" | grep -q '\bdocker\b'; then
      usermod -G $(groups $USER | sed 's/ docker//g' | tr ' ' ',') "$USER"
      log "INFO" "Пользователь $USER удален из группы docker"
    else
      log "INFO" "Пользователь $USER не состоит в группе docker"
    fi
  fi

  # Удаление Docker
  log "INFO" "Удаление Docker пакетов"
  apt remove -y --purge docker-ce docker-ce-cli containerd.io docker-compose-plugin

  # Удаление оставшихся пакетов Docker
  apt autoremove -y

  # Удаление конфигурационных файлов и директорий
  log "INFO" "Удаление конфигурационных файлов Docker"
  sudo rm -rf /etc/docker
  sudo rm -rf /var/lib/docker
  sudo rm -rf /etc/apt/keyrings/docker.gpg
  sudo rm -f /etc/apt/sources.list.d/docker.list

  # Удаление временных файлов
  sudo rm -f /tmp/daemon.json

  log "INFO" "Docker и связанные компоненты удалены"
}

# Основная функция выполнения удаления
main() {
  log "INFO" "Запуск удаления роли: 20-docker"

  # Выполнение удаления Docker
  uninstall_docker

  log "INFO" "Удаление роли 20-docker завершено"
}

# Вызов основной функции
main "$@"
