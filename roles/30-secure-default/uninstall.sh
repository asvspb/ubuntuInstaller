#!/bin/bash

# Роль: 30-secure-default
# Назначение: Удаление настроек безопасности системы по умолчанию

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция удаления настроек брандмауэра UFW
remove_firewall() {
  log "INFO" "Удаление настроек брандмауэра UFW"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Удаление настроек брандмауэра UFW (не выполнена)"
    return 0
  fi

  # Отключение брандмауэра UFW
  if command -v ufw &>/dev/null; then
    log "INFO" "Отключение брандмауэра UFW"
    ufw --force disable

    # Сброс правил брандмауэра
    ufw --force reset
  fi

  log "INFO" "Настройки брандмауэра UFW удалены"
}

# Функция удаления настроек автоматических обновлений
remove_auto_updates() {
  log "INFO" "Удаление настроек автоматических обновлений"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Удаление настроек автоматических обновлений (не выполнена)"
    return 0
  fi

  # Удаление конфигурационных файлов автоматических обновлений
  if [ -f /etc/apt/apt.conf.d/50unattended-upgrades ]; then
    log "INFO" "Удаление конфигурационного файла 50unattended-upgrades"
    rm -f /etc/apt/apt.conf.d/50unattended-upgrades
  fi

  if [ -f /etc/apt/apt.conf.d/20auto-upgrades ]; then
    log "INFO" "Удаление конфигурационного файла 20auto-upgrades"
    rm -f /etc/apt/apt.conf.d/20auto-upgrades
  fi

  # Восстановление резервных копий, если они существуют
  if [ -f /etc/apt/apt.conf.d/50unattended-upgrades.backup ]; then
    log "INFO" "Восстановление резервной копии 50unattended-upgrades"
    mv /etc/apt/apt.conf.d/50unattended-upgrades.backup /etc/apt/apt.conf.d/50unattended-upgrades
  fi

  if [ -f /etc/apt/apt.conf.d/20auto-upgrades.backup ]; then
    log "INFO" "Восстановление резервной копии 20auto-upgrades"
    mv /etc/apt/apt.conf.d/20auto-upgrades.backup /etc/apt/apt.conf.d/20auto-upgrades
  fi

  log "INFO" "Настройки автоматических обновлений удалены"
}

# Функция удаления усиления параметров аутентификации
remove_hardened_authentication() {
  log "INFO" "Удаление усиления параметров аутентификации"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Удаление усиления параметров аутентификации (не выполнена)"
    return 0
  fi

  # Восстановление резервных копий файлов аутентификации
  if [ -f /etc/pam.d/common-auth.backup ]; then
    log "INFO" "Восстановление резервной копии common-auth"
    mv /etc/pam.d/common-auth.backup /etc/pam.d/common-auth
  fi

  if [ -f /etc/pam.d/common-password.backup ]; then
    log "INFO" "Восстановление резервной копии common-password"
    mv /etc/pam.d/common-password.backup /etc/pam.d/common-password
  fi

  if [ -f /etc/security/faillock.conf.backup ]; then
    log "INFO" "Восстановление резервной копии faillock.conf"
    mv /etc/security/faillock.conf.backup /etc/security/faillock.conf
  fi

  # Удаление текущих файлов, если резервные копии отсутствуют
  if [ ! -f /etc/pam.d/common-auth.backup ] && [ -f /etc/pam.d/common-auth ]; then
    log "INFO" "Удаление текущего файла common-auth"
    rm -f /etc/pam.d/common-auth
  fi

  if [ ! -f /etc/pam.d/common-password.backup ] && [ -f /etc/pam.d/common-password ]; then
    log "INFO" "Удаление текущего файла common-password"
    rm -f /etc/pam.d/common-password
  fi

  if [ ! -f /etc/security/faillock.conf.backup ] && [ -f /etc/security/faillock.conf ]; then
    log "INFO" "Удаление текущего файла faillock.conf"
    rm -f /etc/security/faillock.conf
  fi

  log "INFO" "Усиление параметров аутентификации удалено"
}

# Функция удаления настроек безопасности SSH
remove_hardened_ssh() {
  log "INFO" "Удаление настроек безопасности SSH"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Удаление настроек безопасности SSH (не выполнена)"
    return 0
  fi

  # Восстановление резервной копии конфигурации SSH
  if [ -f /etc/ssh/sshd_config.backup ]; then
    log "INFO" "Восстановление резервной копии sshd_config"
    mv /etc/ssh/sshd_config.backup /etc/ssh/sshd_config
  fi

  # Перезапуск SSH сервиса для применения изменений
  if systemctl is-active --quiet ssh; then
    systemctl restart ssh
  fi

  log "INFO" "Настройки безопасности SSH удалены"
}

# Функция удаления настроек аудита системы
remove_audit() {
  log "INFO" "Удаление настроек аудита системы"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Удаление настроек аудита системы (не выполнена)"
    return 0
  fi

  # Удаление правил аудита
  if [ -f /etc/audit/rules.d/audit.rules ]; then
    log "INFO" "Удаление правил аудита"
    # Удаление строк, добавленных нашей ролью
    sed -i '/# Правила аудита для безопасности/,+20d' /etc/audit/rules.d/audit.rules
  fi

  # Перезапуск службы аудита
  if systemctl is-active --quiet auditd; then
    systemctl restart auditd
  fi

  log "INFO" "Настройки аудита системы удалены"
}

# Функция удаления настроек безопасности ядра
remove_hardened_kernel() {
  log "INFO" "Удаление настроек безопасности ядра"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Удаление настроек безопасности ядра (не выполнена)"
    return 0
  fi

  # Восстановление резервной копии настроек sysctl
  if [ -f /etc/sysctl.conf.backup ]; then
    log "INFO" "Восстановление резервной копии sysctl.conf"
    mv /etc/sysctl.conf.backup /etc/sysctl.conf
  fi

  # Применение восстановленных настроек
  if [ -f /etc/sysctl.conf ]; then
    sysctl -p
  fi

  log "INFO" "Настройки безопасности ядра удалены"
}

# Функция удаления настроек безопасности файловой системы
remove_hardened_filesystem() {
  log "INFO" "Удаление настроек безопасности файловой системы"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Удаление настроек безопасности файловой системы (не выполнена)"
    return 0
  fi

  # Здесь мы не восстанавливаем права доступа к файлам,
  # так как это может быть небезопасно. Вместо этого просто логируем.
  log "INFO" "Настройки прав доступа к файловой системе не изменены для безопасности"

  log "INFO" "Настройки безопасности файловой системы удалены"
}

# Функция удаления настроек безопасности по умолчанию
remove_secure_defaults() {
  log "INFO" "Удаление настроек безопасности системы по умолчанию"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Удаление настроек безопасности (не выполнена)"
    return 0
  fi

  # Выполнение всех функций удаления настроек безопасности
  remove_firewall
  remove_auto_updates
  remove_hardened_authentication
  remove_hardened_ssh
  remove_audit
  remove_hardened_kernel
  remove_hardened_filesystem

  log "INFO" "Удаление настроек безопасности завершено"
}

# Основная функция выполнения роли
main() {
  log "INFO" "Запуск удаления роли: 30-secure-default"

  # Выполнение удаления настроек безопасности по умолчанию
  remove_secure_defaults

  log "INFO" "Роль 30-secure-default успешно удалена"
}

# Вызов основной функции
main "$@"
