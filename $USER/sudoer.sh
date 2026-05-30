#!/bin/bash

# Проверяем, какие команды может выполнять текущий пользователь через sudo
sudo -l


# Имя текущего пользователя (не root, а того, кто запустил скрипт через sudo)
REAL_USER=${SUDO_USER:-$USER}

# Файл конфигурации, который мы будем создавать/удалять
CONFIG_FILE="/etc/sudoers.d/${REAL_USER}-nopasswd"

# Проверка, запущен ли скрипт от root
if [ "$EUID" -ne 0 ]; then
    echo "Please run this script with sudo:"
    echo "   sudo ./sudoer.sh [on|off]"
  exit 1
fi

case "$1" in
  off)
    echo "Disabling sudo password for user $REAL_USER..."
    echo "$REAL_USER ALL=(ALL) NOPASSWD:ALL" > "$CONFIG_FILE"
    chmod 0440 "$CONFIG_FILE"
    if visudo -c -f "$CONFIG_FILE" > /dev/null; then
        echo "Done! sudo will not ask for password."
    else
        echo "Syntax error! Reverting..."
        rm "$CONFIG_FILE"
        exit 1
    fi
    ;;

  on)
    echo "Enabling sudo password for user $REAL_USER..."
    if [ -f "$CONFIG_FILE" ]; then
      rm "$CONFIG_FILE"
      echo "Done! Password is now required."
    else
      echo "Password is already enabled (config file not found)."
    fi
    ;;

  *)
    echo "Usage:"
    echo "  sudo ./sudoer.sh off  - Disable password (convenient for setup)"
    echo "  sudo ./sudoer.sh on   - Enable password back (secure)"
    exit 1
    ;;
esac

# Проверяем, какие команды может выполнять текущий пользователь через sudo
sudo -l