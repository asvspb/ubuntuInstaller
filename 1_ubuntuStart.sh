#!/bin/bash

set -e

echo " "
echo "Настройка паролей"
echo "--------------------------------------------------------------"
# чтоб не спрашивал пароль при sudo
echo "${USER} ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/90-nopasswd

# чтоб не ждал подтверждения при установке
export DEBIAN_FRONTEND=noninteractive
if [ -f /etc/needrestart/needrestart.conf ]; then
  # Update existing config file
  sudo sed -i '/\$nrconf{restart}/s/^#//g' /etc/needrestart/needrestart.conf
  sudo sed -i "/nrconf{restart}/s/'i'/'a'/g" /etc/needrestart/needrestart.conf
else
  # Create new config file
  sudo mkdir -p /etc/needrestart
  cat <<EOF | sudo tee /etc/needrestart/needrestart.conf
$nrconf{restart} = 'a'
EOF
fi

# чтоб не спрашивал authenticity of host gitlab.com
mkdir -p ~/.ssh
chmod 0700 ~/.ssh
cat <<EOF > ~/.ssh/config
Host gitlab.com
  StrictHostKeyChecking no
  UserKnownHostsFile=/dev/null
EOF

echo " "
echo "Установка времени"
echo "--------------------------------------------------------------"
sudo timedatectl set-local-rtc 1 --adjust-system-clock
sudo timedatectl

# сворачивание приложение по клику в доке
gsettings set org.gnome.shell.extensions.dash-to-dock click-action 'minimize'

echo "                                                              "
echo "Устанавливаем системные приложения"
echo "--------------------------------------------------------------"
sudo apt update -y
sudo apt-get install git gh mc tmux zsh mosh curl wget ca-certificates net-tools make yarn apt-transport-https gpg gnupg -y

echo "                                                              "
echo "Устанавливаем python & nodejs"
echo "--------------------------------------------------------------"
# устанавливаем python
sudo apt install python3 python3-pip python3-venv python3-tk python3-py -y

# устанавливаем nvm + node
sudo apt install npm nodejs -y


# установка расширений гном
sudo apt install gnome-shell-extensions gnome-tweaks ubuntu-restricted-extras -y

echo "                                                              "
echo "Установка telegram"
echo "--------------------------------------------------------------"
snap install telegram-desktop

echo "                                                              "
echo "Установка Chrome"
echo "--------------------------------------------------------------"
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo dpkg -i ./google-chrome*.deb
sudo apt-get install -f
sudo rm ./google-chrome*.deb

sudo apt -f install

echo "                                                              "
echo "Можно копировать системные файлы в: /$HOME"
echo "--------------------------------------------------------------"
