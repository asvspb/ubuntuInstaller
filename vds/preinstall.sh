#!/usr/bin/env bash
# Version: 1.1

# 1. Чиним прерванные ранее установки (из-за tzdata)
sudo dpkg --configure -a
sudo apt --fix-broken install -y

# 2. Устанавливаем часовой пояс в UTC
echo "Установка часового пояса..."
sudo timedatectl set-timezone UTC

# 3. Обновляем списки пакетов
echo "Обновление списка пакетов..."
sudo apt update

# 4. Обновляем систему в неинтерактивном режиме (чтобы не было розовых окон)
echo "Обновление системы..."
sudo DEBIAN_FRONTEND=noninteractive apt upgrade -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold"

# 5. Устанавливаем нужные программы без вопросов
echo "Установка утилит..."
sudo DEBIAN_FRONTEND=noninteractive apt install -y git curl wget mc

# 6. Проверяем, что всё работает
echo "Готово! Версия Git:"
git --version