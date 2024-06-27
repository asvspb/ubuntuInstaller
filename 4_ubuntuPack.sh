#! /bin/bash

set -e

echo " "
echo "Установка репозиториев"
echo "--------------------------------------------------------------"
sudo add-apt-repository ppa:thopiekar/openrgb -y
sudo add-apt-repository ppa:trebelnik-stefina/grub-customizer -y
sudo add-apt-repository ppa:ubuntuhandbook1/rhythmbox -y
sudo add-apt-repository -y ppa:deadsnakes/ppa -y #python

echo " "
echo "Установка ключей"
echo "--------------------------------------------------------------"

#vscode
wget -O- https://packages.microsoft.com/keys/microsoft.asc | sudo gpg --dearmor | sudo tee /usr/share/keyrings/vscode.gpg
echo deb [arch=amd64 signed-by=/usr/share/keyrings/vscode.gpg] https://packages.microsoft.com/repos/vscode stable main | sudo tee /etc/apt/sources.list.d/vscode.list

sudo apt update -y
echo " "
echo "Установка окружения для программирования"
echo "--------------------------------------------------------------"

# установка vsc java
sudo apt install code gcc default-jdk -y
# установка системных пакетов
sudo apt install ncdu ranger btop iftop htop neofetch rpm wireguard jq guake copyq xclip pipx -y
sudo apt install inxi cpu-x tldr fzf rhythmbox vlc alacarte qbittorrent software-properties-common  -y
sudo apt install grub-customizer gparted synaptic openrgb ufw timeshift nala dconf-editor -y


echo " "
echo "Установка speedtest"
echo "--------------------------------------------------------------"
curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash
sudo apt-get install speedtest

echo " "
echo "Установка wireguard"
echo "--------------------------------------------------------------"
# Путь к файлу конфигурации WireGuard
wg_conf="/etc/wireguard/wg0.conf"
sudo touch "$wg_conf"

sudo chmod 0600 /etc/wireguard/wg0.conf
sudo systemctl start wg-quick@wg0.service
sudo ln -sf /usr/bin/resolvectl /usr/local/bin/resolvconf

echo "--------------------------------------------------------------"
echo "Установка завершена успешно"
echo "--------------------------------------------------------------"
