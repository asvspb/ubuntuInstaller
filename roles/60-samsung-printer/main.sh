#!/bin/bash

# Роль: 60-samsung-printer
# Назначение: Установка драйвера для Samsung принтеров из SULDR репозитория

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция установки драйвера Samsung принтера из SULDR репозитория
install_samsung_printer_driver() {
  log "INFO" "Установка драйвера Samsung M2070 MFP из SULDR репозитория"

  # Проверка, установлен ли уже драйвер
  if is_pkg_installed "suld-driver2-1.00.39"; then
    log "INFO" "Samsung драйвер уже установлен. Пропуск установки."
    return 0
  fi

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Установка драйвера Samsung принтера из SULDR репозитория (не выполнена)"
    return 0
  fi

  # Установка необходимых пакетов
  log "INFO" "Установка необходимых пакетов"
  install_packages "wget"

  # Установка репозитория ключа
  log "INFO" "Установка репозитория ключа"
  KEYRING_DEB=$(mktemp --suffix=.deb)
  download_with_verification "https://www.bchemnet.com/suldr/pool/debian/extra/su/suld-keyring_2_all.deb" "$KEYRING_DEB"

  # Установка ключа
  sudo dpkg -i "$KEYRING_DEB"
  rm "$KEYRING_DEB"

  # Добавление SULDR репозитория
  log "INFO" "Добавление Samsung Unified Linux Driver Repository (SULDR)"
  SULDR_SOURCE_LIST="/etc/apt/sources.list.d/samsung-uld.list"
  if [ ! -f "$SULDR_SOURCE_LIST" ]; then
    echo "deb https://www.bchemnet.com/suldr/ debian extra" | sudo tee "$SULDR_SOURCE_LIST" >/dev/null
  else
    log "INFO" "SULDR репозиторий уже существует."
  fi

  # Обновление списков пакетов
  log "INFO" "Обновление списков пакетов"
  apt update

  # Установка драйвера Samsung
  log "INFO" "Установка Samsung принтер и сканер драйвера"
  install_packages "suld-driver2-1.00.39"

  log "INFO" "Установка Samsung M2070 MFP завершена!"
}

# Функция проверки и установки системного принтера по умолчанию
check_default_printer() {
  log "INFO" "Проверка системного принтера по умолчанию"

  if [ "$UBUNTU_INSTALLER_DRY_RUN" = "true" ]; then
    log "INFO" "[DRY-RUN] Проверка системного принтера по умолчанию (не выполнена)"
    return 0
  fi

  # Проверка наличия lpstat (cups-client)
  if ! command -v lpstat >/dev/null 2>&1; then
    log "INFO" "cups-client не найден. Установка cups-client"
    install_packages "cups-client"
  fi

  # Проверка статуса CUPS сервиса
  if command -v systemctl >/dev/null 2>&1; then
    if ! systemctl is-active --quiet cups; then
      log "INFO" "CUPS сервис не активен. Включение и запуск CUPS сервиса"
      sudo systemctl enable --now cups
    fi
  fi

  # Получение системного принтера по умолчанию
  DEFAULT=$(lpstat -d 2>/dev/null | awk -F': ' '/system default destination:/ {print $2}')
  if [ -n "$DEFAULT" ]; then
    log "INFO" "Системный принтер по умолчанию: $DEFAULT"
    return 0
  fi

  log "INFO" "Системный принтер по умолчанию не установлен."
  PRINTERS=$(lpstat -p 2>/dev/null | awk '/^printer / {print $2}')
  if [ -n "$PRINTERS" ]; then
    PRINTER_COUNT=$(printf "%s\n" "$PRINTERS" | awk 'NF' | wc -l | tr -d ' ')
    if [ "$PRINTER_COUNT" -eq 1 ]; then
      ONLY_PRINTER=$(printf "%s\n" "$PRINTERS" | head -n1)
      log "INFO" "Установка системного принтера по умолчанию: $ONLY_PRINTER"
      sudo lpadmin -d "$ONLY_PRINTER"
      log "INFO" "Системный принтер по умолчанию установлен: $ONLY_PRINTER"
    else
      log "INFO" "Обнаружено несколько принтеров:"
      echo "$PRINTERS"
      log "INFO" "Установите принтер по умолчанию с помощью: sudo lpadmin -d <PRINTER_NAME>"
    fi
  else
    log "INFO" "Принтеры еще не настроены. Добавьте принтер через 'Настройки' > 'Принтеры' или с помощью lpadmin."
  fi
}

# Основная функция выполнения роли
main() {
  log "INFO" "Запуск роли: 60-samsung-printer"

  # Выполнение установки драйвера Samsung принтера
  install_samsung_printer_driver

  # Проверка и установка принтера по умолчанию
  check_default_printer

  log "INFO" "Роль 60-samsung-printer завершена"
}

# Вызов основной функции
main "$@"
