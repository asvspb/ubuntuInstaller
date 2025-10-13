#!/bin/bash

# Мини-TUI для выбора профиля/ролей и запуска установки
# Использует whiptail для создания интерактивного интерфейса

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция отображения главного меню
show_main_menu() {
    local choice
    choice=$(whiptail \
        --title "Ubuntu Installer Framework" \
        --menu "\nВыберите действие:" \
        15 60 4 \
        "1" "Установить компоненты" \
        "2" "Удалить компоненты" \
        "3" "Обновить компоненты" \
        "4" "Выход" \
        3>&1 1>&2 2>&3)

    case $choice in
        1)
            run_installation
            ;;
        2)
            run_uninstallation
            ;;
        3)
            run_update
            ;;
        4)
            exit 0
            ;;
        *)
            exit 0
            ;;
    esac
}

# Функция для выбора профиля
select_profile() {
    local profile
    profile=$(whiptail \
        --title "Выбор профиля" \
        --menu "\nВыберите профиль системы:" \
        15 60 4 \
        "desktop-developer" "Рабочая станция разработчика" \
        "server" "Сервер" \
        "wsl" "Windows Subsystem for Linux" \
        3>&1 1>&2 2>&3)
    
    echo "$profile"
}

# Функция для выбора ролей
select_roles() {
    # Определение ролей по умолчанию
    local roles=()
    local role_dirs=("$SCRIPT_DIR/roles"/*/)
    
    # Создание массива опций для whiptail
    local options=()
    for role_dir in "${role_dirs[@]}"; do
        role_name=$(basename "$role_dir")
        # Проверяем, существует ли main.sh в директории роли
        if [ -f "$role_dir/main.sh" ]; then
            # Добавляем роль в список с начальным значением (включена по умолчанию)
            options+=("$role_name" "$role_name" "ON")
        fi
    done
    
    # Вызов whiptail для выбора ролей
    local selected_roles
    selected_roles=$(whiptail \
        --title "Выбор ролей" \
        --checklist "\nВыберите компоненты для установки:" \
        20 70 10 \
        "${options[@]}" \
        3>&1 1>&2 2>&3)
    
    # Возвращаем выбранные роли
    echo "$selected_roles"
}

# Функция для генерации конфигурационного файла
generate_config() {
    local profile=$1
    local selected_roles=$2
    
    # Удаление кавычек из строки ролей
    selected_roles=${selected_roles//\"/}
    
    # Создание временного конфигурационного файла
    cat > /tmp/ubuntu_installer_config.yaml <<EOF
settings:
  non_interactive: false
 log_file: "/var/log/ubuntuInstaller/install.log"
profile: "$profile"
roles_enabled:
EOF

    # Добавление ролей в конфигурационный файл
    IFS=',' read -ra role_array <<< "$selected_roles"
    for role in "${role_array[@]}"; do
        # Убираем лишние пробелы
        role=$(echo "$role" | xargs)
        if [ -n "$role" ]; then
            echo "  - name: $role" >> /tmp/ubuntu_installer_config.yaml
        fi
    done
}

# Функция для выполнения установки
run_installation() {
    local profile
    profile=$(select_profile)
    
    local selected_roles
    selected_roles=$(select_roles)
    
    generate_config "$profile" "$selected_roles"
    
    if whiptail --yesno "Вы хотите запустить установку с выбранными параметрами?\n\nПрофиль: $profile\nРоли: $selected_roles" 15 60; then
        sudo cp /tmp/ubuntu_installer_config.yaml ./config.yaml
        sudo ./install.sh install -c ./config.yaml
        whiptail --msgbox "Установка завершена!" 10 60
    else
        show_main_menu
    fi
}

# Функция для выполнения удаления
run_uninstallation() {
    local profile
    profile=$(select_profile)
    
    local selected_roles
    selected_roles=$(select_roles)
    
    generate_config "$profile" "$selected_roles"
    
    if whiptail --yesno "Вы хотите удалить выбранные компоненты?\n\nВнимание: Это действие может быть необратимым!\n\nПрофиль: $profile\nРоли: $selected_roles" 15 60; then
        sudo cp /tmp/ubuntu_installer_config.yaml ./config.yaml
        sudo ./install.sh uninstall -c ./config.yaml
        whiptail --msgbox "Удаление завершено!" 10 60
    else
        show_main_menu
    fi
}

# Функция для выполнения обновления
run_update() {
    if whiptail --yesno "Вы хотите обновить установленные компоненты?" 10 60; then
        sudo ./install.sh update
        whiptail --msgbox "Обновление завершено!" 10 60
    else
        show_main_menu
    fi
}

# Проверка наличия whiptail
if ! command -v whiptail &> /dev/null; then
    log "ERROR" "whiptail не установлен. Установите его с помощью: sudo apt install whiptail"
    exit 1
fi

# Запуск главного меню
show_main_menu