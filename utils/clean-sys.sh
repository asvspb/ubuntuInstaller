#!/bin/bash

# Скрипт для очистки системы от временных файлов и ненужных данных
# Используется в Ubuntu Installer Framework

set -e

# Функция логирования
log() {
    local level=$1
    shift
    local message="$1"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    case $level in
        "INFO")
            echo -e "\033[0;32m[INFO]\033[0m [$timestamp] $message"
            ;;
        "WARN")
            echo -e "\033[1;33m[WARN]\033[0m [$timestamp] $message"
            ;;
        "ERROR")
            echo -e "\033[0;31m[ERROR]\033[0m [$timestamp] $message"
            ;;
        *)
            echo -e "[$level] [$timestamp] $message"
            ;;
    esac
}

# Функция очистки временных файлов
clean_temp_files() {
    log "INFO" "Очистка временных файлов"
    
    # Очистка /tmp
    if [ -d /tmp ] && [ -w /tmp ]; then
        find /tmp -type f -atime +7 -delete 2>/dev/null || true
        find /tmp -type d -empty -delete 2>/dev/null || true
    fi
    
    # Очистка ~/.cache
    if [ -d "$HOME/.cache" ] && [ -w "$HOME/.cache" ]; then
        find "$HOME/.cache" -type f -atime +7 -delete 2>/dev/null || true
        find "$HOME/.cache" -type d -empty -delete 2>/dev/null || true
    fi
    
    # Очистка ~/.thumbnails
    if [ -d "$HOME/.thumbnails" ] && [ -w "$HOME/.thumbnails" ]; then
        rm -rf "$HOME/.thumbnails"/*
    fi
    
    log "INFO" "Очистка временных файлов завершена"
}

# Функция очистки логов
clean_logs() {
    log "INFO" "Очистка логов"
    
    # Очистка старых логов в /var/log
    if [ -d /var/log ] && [ -w /var/log ]; then
        find /var/log -name "*.log" -type f -mtime +30 -delete 2>/dev/null || true
        find /var/log -name "*.gz" -type f -mtime +30 -delete 2>/dev/null || true
    fi
    
    log "INFO" "Очистка логов завершена"
}

# Функция очистки кэша пакетов
clean_package_cache() {
    log "INFO" "Очистка кэша пакетов"
    
    # Очистка кэша APT
    if command -v apt &>/dev/null; then
        sudo apt autoclean -y
        sudo apt autoremove -y
    fi
    
    # Очистка кэша Snap
    if command -v snap &>/dev/null; then
        sudo snap set system refresh.retain=2
    fi
    
    log "INFO" "Очистка кэша пакетов завершена"
}

# Функция очистки Docker
clean_docker() {
    log "INFO" "Очистка Docker"
    
    # Очистка неиспользуемых данных Docker
    if command -v docker &>/dev/null; then
        docker system prune -af 2>/dev/null || true
        docker volume prune -f 2>/dev/null || true
    fi
    
    log "INFO" "Очистка Docker завершена"
}

# Основная функция
main() {
    log "INFO" "Запуск очистки системы"
    
    # Выполнение всех функций очистки
    clean_temp_files
    clean_logs
    clean_package_cache
    clean_docker
    
    log "INFO" "Очистка системы завершена"
}

# Запуск основной функции
main "$@"