#!/bin/bash

# Скрипт удаления для роли 70-zerotier-client
# Удаляет ZeroTier клиент, установленный в main.sh

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция удаления ZeroTier клиента
uninstall_zerotier_client() {
  log "INFO" "Удаление ZeroTier клиента"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Удаление ZeroTier клиента (не выполнено)"
    return 0
  fi

  # Остановка и отключение сервиса ZeroTier
  log "INFO" "Остановка и отключение сервиса ZeroTier"
  systemctl stop zerotier-one 2>/dev/null || true
  systemctl disable zerotier-one 2>/dev/null || true

  # Удаление пакета ZeroTier
  log "INFO" "Удаление пакета ZeroTier"
  if is_pkg_installed "zerotier-one"; then
    apt remove -y --purge zerotier-one
  else
    log "INFO" "Пакет zerotier-one не установлен"
  fi

  # Удаление репозитория ZeroTier (если он был добавлен)
  log "INFO" "Удаление репозитория ZeroTier"
  if [ -f /etc/apt/sources.list.d/zerotier.list ]; then
    rm -f /etc/apt/sources.list.d/zerotier.list
    # Обновление списка пакетов
    apt update
  fi

  # Удаление публичного ключа ZeroTier
  if [ -f /etc/apt/trusted.gpg.d/zerotier.gpg ]; then
    rm -f /etc/apt/trusted.gpg.d/zerotier.gpg
  fi

  # Удаление конфигурационных файлов
  log "INFO" "Удаление конфигурационных файлов ZeroTier"
  if [ -d /var/lib/zerotier-one ]; then
    rm -rf /var/lib/zerotier-one
  fi

  if [ -d /etc/zerotier ]; then
    rm -rf /etc/zerotier
  fi

  log "INFO" "ZeroTier клиент удален"
}

# Основная функция выполнения удаления
main() {
  log "INFO" "Запуск удаления роли: 70-zerotier-client"

  # Выполнение удаления ZeroTier клиента
  uninstall_zerotier_client

  log "INFO" "Удаление роли 70-zerotier-client завершено"
}

# Вызов основной функции
main "$@"
