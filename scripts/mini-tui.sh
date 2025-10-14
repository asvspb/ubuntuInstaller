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
        "auto" "Автоматическое определение" \
        3>&1 1>&2 2>&3)
    
    echo "$profile"
}

# Функция для получения описания роли из комментариев в main.sh
get_role_description() {
    local role_name=$1
    local role_dir="$SCRIPT_DIR/roles/$role_name"
    
    if [ -f "$role_dir/main.sh" ]; then
        # Ищем строку с описанием роли в комментариях
        local description=$(grep -E "^# Назначение:" "$role_dir/main.sh" | head -n1 | sed 's/^# Назначение: //')
        if [ -z "$description" ]; then
            case $role_name in
                "95-user-config")
                    description="Установка конфигурационных файлов из директории $USER"
                    ;;
                *)
                    description="Роль $role_name"
                    ;;
            esac
        fi
        echo "$description"
    else
        echo "Роль $role_name"
    fi
}

# Функция для выбора ролей с описаниями
select_roles() {
    # Определение ролей по умолчанию
    local roles=()
    local role_dirs=("$SCRIPT_DIR/roles"/*/)
    
    # Создание массива опций для whiptail с описаниями
    local options=()
    for role_dir in "${role_dirs[@]}"; do
        role_name=$(basename "$role_dir")
        # Проверяем, существует ли main.sh в директории роли
        if [ -f "$role_dir/main.sh" ]; then
            # Получаем описание роли
            local description=$(get_role_description "$role_name")
            # Добавляем роль в список с начальным значением (включена по умолчанию)
            options+=("$role_name" "$description" "ON")
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

# Функция для настройки глобальных параметров
configure_global_settings() {
    # Запрашиваем у пользователя настройки
    local non_interactive=$(whiptail --yesno "Использовать неинтерактивный режим (без дополнительных вопросов во время установки)?" 10 70 3>&1 1>&2 2>&3 && echo "true" || echo "false")
    
    # Возвращаем настройки в формате YAML
    echo "  non_interactive: $non_interactive"
}

# Функция для настройки переменных роли
configure_role_vars() {
    local role_name=$1
    local vars_config=""
    
    # Определяем специфические переменные для некоторых ролей
    case $role_name in
        "70-zerotier-client")
            local network_id=$(whiptail --inputbox "Введите Network ID для ZeroTier (оставьте пустым, если не нужно):" 10 60 3>&1 1>&2 2>&3)
            if [ -n "$network_id" ]; then
                vars_config="  network_id: $network_id"
            fi
            ;;
        "10-dev-tools")
            local install_vscode=$(whiptail --yesno "Установить Visual Studio Code?" 10 60 3>&1 1>&2 2>&3 && echo "true" || echo "false")
            local install_pycharm=$(whiptail --yesno "Установить PyCharm Community Edition?" 10 60 3>&1 1>&2 2>&3 && echo "true" || echo "false")
            vars_config="  install_vscode: $install_vscode\n  install_pycharm: $install_pycharm"
            ;;
        *)
            # Для других ролей можно добавить настройку через общий интерфейс, если нужно
            ;;
    esac
    
    echo -e "$vars_config"
}

# Функция для генерации конфигурационного файла
generate_config() {
    local profile=$1
    local selected_roles=$2
    local global_settings=$3
    
    # Удаление кавычек из строки ролей
    selected_roles=${selected_roles//\"/}
    
    # Создание временного конфигурационного файла
    cat > /tmp/ubuntu_installer_config.yaml <<EOF
settings:
$(echo "$global_settings")
 log_file: "/var/log/ubuntuInstaller/install-$(date +%Y-%m-%d).log"
profile: "$profile"
roles_enabled:
EOF
    
    # Добавление ролей в конфигурационный файл
    IFS=',' read -ra role_array <<< "$selected_roles"
    for role in "${role_array[@]}"; do
        # Убираем лишние пробелы
        role=$(echo "$role" | xargs)
        if [ -n "$role" ]; then
            # Проверяем, есть ли у роли переменные для настройки
            local role_vars=$(configure_role_vars "$role")
            if [ -n "$role_vars" ]; then
                echo "  - name: $role" >> /tmp/ubuntu_installer_config.yaml
                echo "    vars:" >> /tmp/ubuntu_installer_config.yaml
                # Добавляем переменные с отступом
                while IFS= read -r line; do
                    if [ -n "$line" ]; then
                        echo "      $line" >> /tmp/ubuntu_installer_config.yaml
                    fi
                done <<< "$(echo -e "$role_vars")"
            else
                echo "  - name: $role" >> /tmp/ubuntu_installer_config.yaml
            fi
        fi
    done
}

# Функция для выполнения установки
run_installation() {
    local profile
    profile=$(select_profile)
    
    # Настройка глобальных параметров
    local global_settings=$(configure_global_settings)
    
    local selected_roles
    selected_roles=$(select_roles)
    
    generate_config "$profile" "$selected_roles" "$global_settings"
    
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
    
    # Настройка глобальных параметров
    local global_settings=$(configure_global_settings)
    
    local selected_roles
    selected_roles=$(select_roles)
    
    generate_config "$profile" "$selected_roles" "$global_settings"
    
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
    # Настройка глобальных параметров
    local global_settings=$(configure_global_settings)
    
    # Создаем минимальный конфигурационный файл для обновления
    cat > /tmp/ubuntu_installer_config.yaml <<EOF
settings:
$(echo "$global_settings")
 log_file: "/var/log/ubuntuInstaller/install-$(date +%Y-%m-%d).log"
profile: "auto"
roles_enabled: []
EOF
    
    if whiptail --yesno "Вы хотите обновить установленные компоненты?" 10 60; then
        sudo cp /tmp/ubuntu_installer_config.yaml ./config.yaml
        sudo ./install.sh update -c ./config.yaml
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