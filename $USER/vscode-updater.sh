#!/bin/bash

# Скрипт для автоматического обновления Visual Studio Code
# Проверяет текущую версию VSCode, скачивает и устанавливает последнюю версию при необходимости

set -e

# Файл лога
LOG_FILE="$HOME/vscode-updater.log"

# URL для загрузки последней версии VSCode
DOWNLOAD_URL="https://code.visualstudio.com/sha/download?build=stable&os=linux-deb-x64"

# Временный файл для загрузки
TEMP_DEB="/tmp/vscode.deb"

# Функция для логирования
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Функция для проверки версии установленного VSCode
get_installed_version() {
    if command -v code &>/dev/null; then
        code --version | head -n 1
    else
        echo "not_installed"
    fi
}

# Функция для получения последней версии VSCode с сайта
get_latest_version() {
    # Получаем версию с официального сайта через API
    curl -sSL "https://code.visualstudio.com/sha?build=stable" | grep -oE '"productVersion":"[^"]+"' | head -1 | cut -d'"' -f4
}

# Функция для проверки целостности загруженного файла
verify_package() {
    if [ ! -f "$TEMP_DEB" ]; then
        log "ERROR: Файл пакета не найден"
        return 1
    fi
    
    # Проверяем, что файл не пустой
    if [ ! -s "$TEMP_DEB" ]; then
        log "ERROR: Файл пакета пустой"
        return 1
    fi
    
    # Проверяем контрольную сумму (простая проверка)
    if file "$TEMP_DEB" | grep -q "Debian binary package"; then
        log "INFO: Файл пакета прошел базовую проверку"
        return 0
    else
        log "ERROR: Файл пакета не является действительным Debian пакетом"
        return 1
    fi
}

# Функция для установки VSCode
install_vscode() {
    log "INFO: Начинаем установку VSCode"
    
    # Проверяем целостность пакета
    if ! verify_package; then
        log "ERROR: Проверка целостности пакета не пройдена"
        return 1
    fi
    
    # Устанавливаем пакет
    if sudo dpkg -i "$TEMP_DEB" &>/dev/null; then
        log "INFO: VSCode успешно установлен"
        # Удаляем временный файл
        rm -f "$TEMP_DEB"
        return 0
    else
        log "ERROR: Ошибка при установке VSCode"
        # Пытаемся исправить зависимости
        if sudo apt-get install -f -y &>/dev/null; then
            log "INFO: Зависимости успешно исправлены"
            return 0
        else
            log "ERROR: Не удалось исправить зависимости"
            return 1
        fi
    fi
}

# Функция для проверки, запущен ли скрипт в интерактивном режиме
is_interactive() {
    [ -t 0 ]
}

# Основная функция обновления
update_vscode() {
    log "INFO: Запуск проверки обновлений VSCode"
    
    # Получаем текущую версию
    INSTALLED_VERSION=$(get_installed_version)
    log "INFO: Текущая установленная версия: $INSTALLED_VERSION"
    
    # Получаем последнюю версию
    LATEST_VERSION=$(get_latest_version)
    if [ -z "$LATEST_VERSION" ]; then
        log "ERROR: Не удалось получить информацию о последней версии"
        return 1
    fi
    log "INFO: Последняя доступная версия: $LATEST_VERSION"
    
    # Проверяем, нуждается ли VSCode в обновлении
    if [ "$INSTALLED_VERSION" = "not_installed" ]; then
        log "INFO: VSCode не установлен"
        # Проверяем, запущен ли скрипт в интерактивном режиме
        if ! is_interactive; then
            log "INFO: Скрипт запущен в неинтерактивном режиме, установка пропущена"
            return 0
        fi
        log "INFO: Начинаем установку"
    elif [ "$INSTALLED_VERSION" = "$LATEST_VERSION" ]; then
        log "INFO: VSCode уже обновлен до последней версии"
        return 0
    else
        log "INFO: Найдена новая версия"
        # Проверяем, запущен ли скрипт в интерактивном режиме
        if ! is_interactive; then
            log "INFO: Скрипт запущен в неинтерактивном режиме, обновление пропущено"
            return 0
        fi
        log "INFO: Начинаем обновление"
    fi
    
    # Скачиваем последнюю версию
    log "INFO: Скачивание последней версии VSCode"
    if curl -sSL "$DOWNLOAD_URL" -o "$TEMP_DEB"; then
        log "INFO: Загрузка завершена успешно"
    else
        log "ERROR: Ошибка при загрузке файла"
        rm -f "$TEMP_DEB"
        return 1
    fi
    
    # Проверяем, запущен ли скрипт в интерактивном режиме перед установкой
    if ! is_interactive; then
        log "INFO: Скрипт запущен в неинтерактивном режиме, установка пропущена"
        rm -f "$TEMP_DEB"
        return 0
    fi
    
    # Устанавливаем VSCode
    if install_vscode; then
        log "INFO: Обновление VSCode завершено успешно"
        return 0
    else
        log "ERROR: Ошибка при обновлении VSCode"
        rm -f "$TEMP_DEB"
        return 1
    fi
}

# Создаем файл лога, если он не существует
if [ ! -f "$LOG_FILE" ]; then
    touch "$LOG_FILE"
    chmod 666 "$LOG_FILE"
fi

# Запускаем обновление
if update_vscode; then
    log "INFO: Скрипт выполнен успешно"
    exit 0
else
    log "ERROR: Скрипт завершился с ошибкой"
    exit 1
fi