#!/bin/bash

# Тестовый скрипт для проверки функции download_with_verification

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Установка режима симуляции
export UBUNTU_INSTALLER_DRY_RUN=true

# Тестирование функции в режиме симуляции
log "INFO" "Тестирование функции download_with_verification в режиме симуляции"

# Пример вызова функции (в режиме симуляции выполнение не произойдет)
download_with_verification \
    "https://example.com/file.deb" \
    "/tmp/test_file.deb" \
    "expected_hash_value" \
    "sha256" \
    "https://example.com/file.deb.asc" \
    "https://example.com/pubkey.gpg"

log "INFO" "Тестирование завершено"