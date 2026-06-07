#!/bin/bash

# Создаем папки для "виртуальных" домашних директорий
AGY_HOME_1="$HOME/.agy_account_1"
AGY_HOME_2="$HOME/.agy_account_2"

mkdir -p "$AGY_HOME_1"
mkdir -p "$AGY_HOME_2"

# Функция для проброса важных настроек из основной системы
setup_fake_home() {
    local fake_home="$1"
    # Создаем символические ссылки на важные конфиги, чтобы внутри agy работали git, ssh и т.д.
    for item in .ssh .gitconfig .bashrc .zshrc .bash_profile .profile; do
        if [ -e "$HOME/$item" ] && [ ! -e "$fake_home/$item" ]; then
            ln -s "$HOME/$item" "$fake_home/$item"
        fi
    done
}

setup_fake_home "$AGY_HOME_1"
setup_fake_home "$AGY_HOME_2"

echo "Запускаем терминалы с agy..."

# Запускаем первый терминал. 
# Мы переопределяем HOME только для этого процесса, чтобы agy сохранял свои данные (сессии/логины) в отдельном месте.
gnome-terminal --window --title="AGY - Account 1" -- bash -c "
    echo '--- AGY Аккаунт 1 ---'
    export HOME=\"$AGY_HOME_1\"
    # Добавляем путь к локальным бинарникам, так как мы изменили HOME
    export PATH=\"\$PATH:/home/asv-spb/.local/bin\"
    agy
    exec bash
"

# Запускаем второй терминал.
gnome-terminal --window --title="AGY - Account 2" -- bash -c "
    echo '--- AGY Аккаунт 2 ---'
    export HOME=\"$AGY_HOME_2\"
    export PATH=\"\$PATH:/home/asv-spb/.local/bin\"
    agy
    exec bash
"

echo "Готово!"
