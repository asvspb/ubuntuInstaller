#!/bin/bash

# Роль: 70-zerotier-client
# Назначение: Установка ZeroTier клиента для Ubuntu с поддержкой присоединения к сети

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция установки ZeroTier клиента
install_zerotier_client() {
  log "INFO" "Установка ZeroTier клиента для Ubuntu"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Установка ZeroTier клиента (не выполнена)"
    return 0
  fi

  # Проверка установки ZeroTier
  if ! command -v zerotier-cli &>/dev/null; then
    log "INFO" "ZeroTier не найден. Установка..."
    curl -s https://install.zerotier.com | bash
  else
    log "INFO" "ZeroTier уже установлен."
  fi

  # Включение и запуск сервиса ZeroTier
  systemctl enable zerotier-one
  systemctl start zerotier-one
}

# Функция обработки неавторизованных сетей
handle_unauthorized_networks() {
  log "INFO" "Получение списка сетей и фильтрация неавторизованных"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Обработка неавторизованных сетей (не выполнена)"
    return 0
  fi

  # Получение списка сетей и фильтрация неавторизованных
  unauthorized_networks=$(sudo zerotier-cli listnetworks | grep -E "ACCESS_DENIED|NOT_FOUND" | awk '{print $3}')

  # Проверка наличия неавторизованных сетей
  if [ -z "$unauthorized_networks" ]; then
    log "INFO" "Неавторизованные сети не найдены."
    return 0
  fi

  # Покидание каждой неавторизованной сети
  for nwid in $unauthorized_networks; do
    log "INFO" "Покидание неавторизованной сети: $nwid"
    sudo zerotier-cli leave "$nwid"
  done

  log "INFO" "Все неавторизованные сети успешно покинуты."
}

# Функция присоединения к сети ZeroTier
join_zerotier_network() {
  log "INFO" "Присоединение к сети ZeroTier"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Присоединение к сети ZeroTier (не выполнено)"
    return 0
  fi

  # Получение Network ID из переменной роли
  local network_id=$(echo "$UBUNTU_INSTALLER_ROLE_VARS" | yq '.network_id // ""' 2>/dev/null || echo "")

  if [ -z "$network_id" ]; then
    log "WARN" "Network ID не указан в переменных роли. Пропуск присоединения к сети."
    return 0
  fi

  log "INFO" "Присоединение к сети $network_id..."
  sudo zerotier-cli join "$network_id"

  log "INFO" "Пожалуйста, перейдите на https://my.zerotier.com/network/ и авторизуйте этот новый узел для сети $network_id"

  # Ожидание авторизации узла
  while ! sudo zerotier-cli listnetworks | grep "$network_id" | grep -q "OK"; do
    sleep 30
    log "INFO" "Все еще ожидаем авторизации... (проверка каждые 30с)"
  done

  # Настройка конфигурации клиента
  log "INFO" "Сеть $network_id авторизована. Настройка ZeroTier..."
  sudo zerotier-cli set "$network_id" allowDNS=1
  sudo zerotier-cli set "$network_id" allowDefault=1
  sudo zerotier-cli set "$network_id" allowGlobal=1

  log "INFO" "Возможно, потребуется перезапустить сервис ZeroTier для применения изменений: sudo systemctl restart zerotier-one"
}

# Функция отключения сервиса ZeroTier
disable_zerotier_service() {
  log "INFO" "Отключение сервиса ZeroTier"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Отключение сервиса ZeroTier (не выполнено)"
    return 0
  fi

  sudo systemctl disable zerotier-one.service
}

# Основная функция выполнения роли
main() {
  log "INFO" "Запуск роли: 70-zerotier-client"

  # Выполнение установки ZeroTier клиента
  install_zerotier_client

  # Обработка неавторизованных сетей
  handle_unauthorized_networks

  # Присоединение к сети ZeroTier (если указан Network ID)
  join_zerotier_network

  # Отображение текущих сетей
  log "INFO" "Текущие сети:"
  sudo zerotier-cli listnetworks

  # Отключение сервиса ZeroTier (по требованию оригинального скрипта)
  disable_zerotier_service

  log "INFO" "Роль 70-zerotier-client завершена"
}

# Вызов основной функции
main "$@"
