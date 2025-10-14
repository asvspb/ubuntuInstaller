#!/bin/bash

# Мини-TUI для выбора профиля/ролей и запуска установки
# Использует whiptail для создания интерактивного интерфейса
#
# ПРИМЕЧАНИЕ: Устаревшая версия этого скрипта находится в директории scripts/
# Пожалуйста, используйте эту версию в корне проекта

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
    20 70 9 \
    "1" "Установить компоненты" \
    "2" "Удалить компоненты" \
    "3" "Обновить компоненты" \
    "4" "Сделать бекап timeshift" \
    "5" "Восстановить из бекапа" \
    "6" "Удалить бекап" \
    "7" "Ручная установка" \
    "8" "Автоматическая установка" \
    "9" "Удаление установленных пакетов" \
    "10" "Выход" \
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
    create_timeshift_backup
    ;;
  5)
    restore_from_backup
    ;;
  6)
    delete_backup
    ;;
  7)
    manual_installation
    ;;
  8)
    auto_installation
    ;;
  9)
    remove_installed_packages
    ;;
  10)
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
  # Удаляем лишние кавычки и пробелы, разделяем по новой строке, а не по пробелам внутри ролей
  echo "$selected_roles" | tr -d '"' | sed 's/  */ /g' | xargs
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

  # Создание временного конфигурационного файла
  cat >/tmp/ubuntu_installer_config.yaml <<EOF
settings:
$(echo "$global_settings")
  log_file: "/var/log/ubuntuInstaller/install-$(date +%Y-%m-%d).log"
profile: "$profile"
roles_enabled:
EOF

  # Добавление ролей в конфигурационный файл
  # Обрабатываем каждую роль, разделенную пробелами
  for role in $selected_roles; do
    # Убираем лишние пробелы и кавычки
    role=$(echo "$role" | xargs | tr -d '"')
    if [ -n "$role" ]; then
      # Проверяем, есть ли у роли переменные для настройки
      local role_vars=$(configure_role_vars "$role")
      if [ -n "$role_vars" ]; then
        echo "  - name: $role" >>/tmp/ubuntu_installer_config.yaml
        echo "    vars:" >>/tmp/ubuntu_installer_config.yaml
        # Добавляем переменные с отступом
        while IFS= read -r line; do
          if [ -n "$line" ]; then
            echo "      $line" >>/tmp/ubuntu_installer_config.yaml
          fi
        done <<<"$(echo -e "$role_vars")"
      else
        echo "  - name: $role" >>/tmp/ubuntu_installer_config.yaml
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
  cat >/tmp/ubuntu_installer_config.yaml <<EOF
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

# Функция для создания бекапа с помощью timeshift
create_timeshift_backup() {
  if ! command -v timeshift &>/dev/null; then
    whiptail --msgbox "Timeshift не установлен. Установите его с помощью: sudo apt install timeshift" 10 60
    show_main_menu
    return
  fi

  local description=$(whiptail --inputbox "Введите описание для бекапа:" 10 60 3>&1 1>&2 2>&3)

  if [ -z "$description" ]; then
    whiptail --msgbox "Описание не может быть пустым" 10 60
    show_main_menu
    return
  fi

  # Проверяем, запущен ли скрипт с правами root
  if [ "$EUID" -ne 0 ]; then
    whiptail --msgbox "Для создания бекапа необходимы права root" 10 60
    show_main_menu
    return
  fi

  log "INFO" "Создание бекапа с помощью Timeshift: $description"

  # Создание снапшота
  if sudo timeshift --create --comments "$description"; then
    whiptail --msgbox "Бекап успешно создан" 10 60
  else
    whiptail --msgbox "Ошибка при создании бекапа" 10 60
  fi

  show_main_menu
}

# Функция для восстановления из бекапа с помощью timeshift
restore_from_backup() {
  if ! command -v timeshift &>/dev/null; then
    whiptail --msgbox "Timeshift не установлен. Установите его с помощью: sudo apt install timeshift" 10 60
    show_main_menu
    return
  fi

  # Проверяем, запущен ли скрипт с правами root
  if [ "$EUID" -ne 0 ]; then
    whiptail --msgbox "Для восстановления из бекапа необходимы права root" 10 60
    show_main_menu
    return
  fi

  # Получаем список доступных бекапов
  local snapshots=$(sudo timeshift --list | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}' | awk '{print $1, $2, $3, $4, $5}' | tr '\n' ' ')

  if [ -z "$snapshots" ]; then
    whiptail --msgbox "Не найдено доступных бекапов" 10 60
    show_main_menu
    return
  fi

  # Преобразуем список бекапов в формат, подходящий для whiptail
  local snapshot_array=()
  while IFS= read -r line; do
    if [ -n "$line" ]; then
      snapshot_array+=("$line" "$line" "OFF")
    fi
  done < <(sudo timeshift --list | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}')

  if [ ${#snapshot_array[@]} -eq 0 ]; then
    whiptail --msgbox "Не найдено доступных бекапов" 10 60
    show_main_menu
    return
  fi

  local selected_snapshot
  selected_snapshot=$(whiptail \
    --title "Выбор бекапа для восстановления" \
    --radiolist "\nВыберите бекап для восстановления:" \
    20 70 10 \
    "${snapshot_array[@]}" \
    3>&1 1>&2 2>&3)

  if [ -z "$selected_snapshot" ]; then
    whiptail --msgbox "Бекап не выбран" 10 60
    show_main_menu
    return
  fi

  # Извлекаем дату бекапа из выбранного значения
  local snapshot_date=$(echo "$selected_snapshot" | awk '{print $1}')

  if whiptail --yesno "Вы уверены, что хотите восстановить систему из бекапа $selected_snapshot?\n\nВНИМАНИЕ: Это действие может привести к потере данных!" 15 60; then
    log "INFO" "Восстановление из бекапа: $selected_snapshot"

    if sudo timeshift --restore --snapshot "$snapshot_date"; then
      whiptail --msgbox "Восстановление из бекапа завершено. Система будет перезагружена для применения изменений." 10 60
      sudo reboot
    else
      whiptail --msgbox "Ошибка при восстановлении из бекапа" 10 60
    fi
  fi

  show_main_menu
}

# Функция для ручной установки
manual_installation() {
  whiptail --msgbox "Ручная установка позволяет пользователю выбрать конкретные пакеты для установки.\n\nЭта функция требует знания команд apt и управления пакетами в Ubuntu." 15 60

  local package_input
  package_input=$(whiptail --inputbox "Введите список пакетов для установки (через пробел):" 15 60 3>&1 1>&2 2>&3)

  if [ -z "$package_input" ]; then
    whiptail --msgbox "Список пакетов пуст" 10 60
    show_main_menu
    return
  fi

  if whiptail --yesno "Вы хотите установить следующие пакеты:\n\n$package_input\n\nПродолжить?" 15 60; then
    log "INFO" "Начало ручной установки пакетов: $package_input"

    if [ "$DRY_RUN" = "true" ]; then
      log "INFO" "[DRY-RUN] Установка пакетов: $package_input (не выполнена)"
      whiptail --msgbox "Режим симуляции: пакеты не были установлены" 10 60
    else
      # Обновляем список пакетов
      if sudo apt update; then
        # Устанавливаем указанные пакеты
        if sudo apt install -y $package_input; then
          whiptail --msgbox "Пакеты успешно установлены" 10 60
        else
          whiptail --msgbox "Ошибка при установке пакетов" 10 60
        fi
      else
        whiptail --msgbox "Ошибка при обновлении списка пакетов" 10 60
      fi
    fi
  fi

  show_main_menu
}

# Функция для автоматической установки
auto_installation() {
  whiptail --msgbox "Автоматическая установка выполнит установку всех компонентов согласно выбранному профилю.\n\nЭто действие установит все рекомендуемые пакеты и настройки." 15 60

  if whiptail --yesno "Вы хотите выполнить автоматическую установку?\n\nВсе компоненты будут установлены согласно профилю по умолчанию." 15 60; then
    log "INFO" "Начало автоматической установки"

    # Запрашиваем у пользователя выбор профиля
    local profile
    profile=$(select_profile)

    # Настройка глобальных параметров
    local global_settings=$(configure_global_settings)

    # Выбираем все доступные роли
    local role_dirs=("$SCRIPT_DIR/roles"/*/)
    local all_roles=()
    for role_dir in "${role_dirs[@]}"; do
      role_name=$(basename "$role_dir")
      # Проверяем, существует ли main.sh в директории роли
      if [ -f "$role_dir/main.sh" ]; then
        all_roles+=("$role_name")
      fi
    done

    # Преобразуем массив в строку для конфигурации
    local roles_string=""
    for role in "${all_roles[@]}"; do
      if [ -z "$roles_string" ]; then
        roles_string="\"$role\""
      else
        roles_string="$roles_string,\"$role\""
      fi
    done

    generate_config "$profile" "$roles_string" "$global_settings"

    if [ "$DRY_RUN" = "true" ]; then
      log "INFO" "[DRY-RUN] Автоматическая установка (не выполнена)"
      whiptail --msgbox "Режим симуляции: автоматическая установка не была выполнена" 10 60
    else
      sudo cp /tmp/ubuntu_installer_config.yaml ./config.yaml
      sudo ./install.sh install -c ./config.yaml
      whiptail --msgbox "Автоматическая установка завершена!" 10 60
    fi
  fi

  show_main_menu
}

# Функция для удаления установленных пакетов
remove_installed_packages() {
  whiptail --msgbox "Удаление установленных пакетов позволяет пользователю выбрать конкретные пакеты для удаления.\n\nЭта функция требует знания команд apt и управления пакетами в Ubuntu." 15 60

  local package_input
  package_input=$(whiptail --inputbox "Введите список пакетов для удаления (через пробел):" 15 60 3>&1 1>&2 2>&3)

  if [ -z "$package_input" ]; then
    whiptail --msgbox "Список пакетов пуст" 10 60
    show_main_menu
    return
  fi

  if whiptail --yesno "Вы хотите удалить следующие пакеты:\n\n$package_input\n\nВНИМАНИЕ: Это может привести к удалению зависимых пакетов!" 15 60; then
    log "INFO" "Начало удаления пакетов: $package_input"

    if [ "$DRY_RUN" = "true" ]; then
      log "INFO" "[DRY-RUN] Удаление пакетов: $package_input (не выполнено)"
      whiptail --msgbox "Режим симуляции: пакеты не были удалены" 10 60
    else
      # Удаляем указанные пакеты
      if sudo apt remove -y $package_input; then
        whiptail --msgbox "Пакеты успешно удалены" 10 60
      else
        whiptail --msgbox "Ошибка при удалении пакетов" 10 60
      fi
    fi
  fi

  show_main_menu
}

# Функция для удаления бекапа
delete_backup() {
  if ! command -v timeshift &>/dev/null; then
    whiptail --msgbox "Timeshift не установлен. Установите его с помощью: sudo apt install timeshift" 10 60
    show_main_menu
    return
  fi

  # Проверяем, запущен ли скрипт с правами root
  if [ "$EUID" -ne 0 ]; then
    whiptail --msgbox "Для удаления бекапа необходимы права root" 10 60
    show_main_menu
    return
  fi

  # Получаем список доступных бекапов
  local snapshots=$(sudo timeshift --list | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}' | awk '{print $1, $2, $3, $4, $5}' | tr '\n' ' ')

  if [ -z "$snapshots" ]; then
    whiptail --msgbox "Не найдено доступных бекапов" 10 60
    show_main_menu
    return
  fi

  # Преобразуем список бекапов в формат, подходящий для whiptail
  local snapshot_array=()
  while IFS= read -r line; do
    if [ -n "$line" ]; then
      snapshot_array+=("$line" "$line" "OFF")
    fi
  done < <(sudo timeshift --list | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}')

  if [ ${#snapshot_array[@]} -eq 0 ]; then
    whiptail --msgbox "Не найдено доступных бекапов" 10 60
    show_main_menu
    return
  fi

  local selected_snapshot
  selected_snapshot=$(whiptail \
    --title "Выбор бекапа для удаления" \
    --radiolist "\nВыберите бекап для удаления:" \
    20 70 10 \
    "${snapshot_array[@]}" \
    3>&1 1>&2 2>&3)

  if [ -z "$selected_snapshot" ]; then
    whiptail --msgbox "Бекап не выбран" 10 60
    show_main_menu
    return
  fi

  # Извлекаем дату бекапа из выбранного значения
  local snapshot_date=$(echo "$selected_snapshot" | awk '{print $1}')

  if whiptail --yesno "Вы уверены, что хотите удалить бекап $selected_snapshot?\n\nВНИМАНИЕ: Это действие необратимо!" 15 60; then
    log "INFO" "Удаление бекапа: $selected_snapshot"

    if sudo timeshift --delete --snapshots "$snapshot_date"; then
      whiptail --msgbox "Бекап успешно удален" 10 60
    else
      whiptail --msgbox "Ошибка при удалении бекапа" 10 60
    fi
  fi

  show_main_menu
}

# Проверка наличия whiptail
if ! command -v whiptail &>/dev/null; then
  log "ERROR" "whiptail не установлен. Установите его с помощью: sudo apt install whiptail"
  exit 1
fi

# Запуск главного меню
show_main_menu
