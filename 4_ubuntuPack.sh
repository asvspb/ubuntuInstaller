#! /bin/bash

set -e

echo " "
echo "Установка репозиториев"
echo "--------------------------------------------------------------"
sudo add-apt-repository ppa:thopiekar/openrgb -y
sudo add-apt-repository ppa:trebelnik-stefina/grub-customizer -y
sudo add-apt-repository ppa:ubuntuhandbook1/rhythmbox -y
sudo add-apt-repository -y ppa:deadsnakes/ppa -y #python
sudo add-apt-repository ppa:eugenesan/ppa -y #smartgit

echo " "
echo "Установка ключей"
echo "--------------------------------------------------------------"
sudo curl -o /usr/share/keyrings/syncthing-archive-keyring.gpg https://syncthing.net/release-key.gpg
echo "deb [signed-by=/usr/share/keyrings/syncthing-archive-keyring.gpg] https://apt.syncthing.net/ syncthing stable" | sudo tee /etc/apt/sources.list.d/syncthing.list

#gsmartcontrol
echo 'deb http://download.opensuse.org/repositories/home:/alex_sh:/gsmartcontrol:/stable_latest/xUbuntu_21.10/ /' | sudo tee /etc/apt/sources.list.d/home:alex_sh:gsmartcontrol:stable_latest.list
curl -fsSL https://download.opensuse.org/repositories/home:alex_sh:gsmartcontrol:stable_latest/xUbuntu_21.10/Release.key | gpg --dearmor | sudo tee /etc/apt/trusted.gpg.d/home_alex_sh_gsmartcontrol_stable_latest.gpg > /dev/null

#thorium
wget https://dl.thorium.rocks/debian/dists/stable/thorium.list
sudo mv thorium.list /etc/apt/sources.list.d/

# установка syncthing
type -p curl >/dev/null || (sudo apt update && sudo apt install curl -y)
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
&& sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
&& echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null 

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
sudo apt install ncdu ranger btop iftop htop neofetch rpm wireguard jq guake copyq syncthing thorium-browser -y
sudo apt install inxi cpu-x tldr fzf rhythmbox vlc alacarte qbittorrent software-properties-common  -y
sudo apt install grub-customizer gparted gsmartcontrol synaptic openrgb ufw timeshift nala smartgit -y
# плагин для js quokka
npm install jsdom-quokka-plugin

echo " "
echo "Установка speedtest"
echo "--------------------------------------------------------------"
curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash
sudo apt-get install speedtest

#запуск syncthing
echo " "
echo "Установка syncthing"
echo "--------------------------------------------------------------"
sudo systemctl start syncthing@$USER
sudo systemctl enable syncthing@$USER

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

echo " "
echo "Установка wireguard"
echo "--------------------------------------------------------------"
# Путь к файлу конфигурации WireGuard
wg_conf="/etc/wireguard/wg0.conf"
sudo touch "$wg_conf"

sudo chmod 0600 /etc/wireguard/wg0.conf
sudo systemctl start wg-quick@wg0.service
sudo ln -sf /usr/bin/resolvectl /usr/local/bin/resolvconf

# сворачивание приложение по клику в доке
gsettings set org.gnome.shell.extensions.dash-to-dock click-action 'minimize'


echo "--------------------------------------------------------------"
echo "Установка завершена успешно"
echo "--------------------------------------------------------------"
