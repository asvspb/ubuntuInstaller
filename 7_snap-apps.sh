#!/bin/bash
echo " "
echo "Установка системных приложений snap"
echo "--------------------------------------------------------------"
# Путь к файлу, в котором сохранен список пакетов snap
PACKAGE_FILE="ubuntu_snap_packages.txt"

# Чтение файла и установка пакетов, если они отсутствуют в системе
while IFS= read -r package; do
  if ! snap list "$package" 2>/dev/null | grep -q "$package"; then
    echo "Установка пакета $package..."
    sudo snap install "$package"
  else
    echo "Пакет $package уже установлен."
  fi
done < "$PACKAGE_FILE"

sudo snap install obsidian --classic
sudo snap install gitkraken --classic
