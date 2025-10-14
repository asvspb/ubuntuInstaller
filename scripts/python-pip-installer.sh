#!/bin/bash

# Скрипт для установки Python пакетов с обходом конфликта системными пакетами
# Использует различные стратегии для предотвращения попыток удаления системных пакетов

set -e

# Функция для установки пакетов с обходом конфликта с системными пакетами
install_python_packages() {
    local packages=("$@")
    local user_install="${USER_INSTALL:-true}"
    
    echo "Установка Python пакетов: ${packages[*]}"
    
    # Попробуем разные стратегии установки
    if [ "$user_install" = "true" ]; then
        # Стратегия 1: Использование --break-system-packages
        echo "Попытка установки с --break-system-packages"
        if python3 -m pip install --break-system-packages --user "${packages[@]}" 2>&1; then
            echo "Установка прошла успешно с --break-system-packages"
            return 0
        else
            echo "Установка с --break-system-packages не удалась, пробуем следующую стратегию"
        fi
        
        # Стратегия 2: Использование --ignore-installed для зависимостей
        echo "Попытка установки с --ignore-installed"
        if python3 -m pip install --ignore-installed --user "${packages[@]}" 2>&1; then
            echo "Установка прошла успешно с --ignore-installed"
            return 0
        else
            echo "Установка с --ignore-installed не удалась, пробуем следующую стратегию"
        fi
        
        # Стратегия 3: Комбинация флагов
        echo "Попытка установки с комбинацией флагов"
        if python3 -m pip install --break-system-packages --ignore-installed --user "${packages[@]}" 2>&1; then
            echo "Установка прошла успешно с комбинацией флагов"
            return 0
        else
            echo "Установка с комбинацией флагов не удалась, пробуем следующую стратегию"
        fi
    else
        # Для системной установки
        echo "Попытка установки с --break-system-packages (системная)"
        if python3 -m pip install --break-system-packages "${packages[@]}" 2>&1; then
            echo "Системная установка прошла успешно с --break-system-packages"
            return 0
        else
            echo "Системная установка с --break-system-packages не удалась"
        fi
    fi
    
    echo "Все стратегии установки не увенчались успехом"
    return 1
}

# Проверка аргументов
if [ $# -eq 0 ]; then
    echo "Использование: $0 package1 [package2 ...]"
    exit 1
fi

# Вызов функции установки
install_python_packages "$@"