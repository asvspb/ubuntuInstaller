#!/bin/bash

set -e

echo " "
echo "Setting up passwords"
echo "--------------------------------------------------------------"

# so that it does not ask for a password with sudo
echo "${USER} ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/90-nopasswd
sudo chmod 0440 /etc/sudoers.d/90-nopasswd

# so that it does not wait for confirmation during installation
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

# so that it does not ask for authenticity of host gitlab.com
mkdir -p ~/.ssh
chmod 0700 ~/.ssh
cat <<EOF > ~/.ssh/config
Host gitlab.com
  StrictHostKeyChecking no
  UserKnownHostsFile=/dev/null
EOF

echo " "
echo "Setting the time"
echo "--------------------------------------------------------------"
sudo timedatectl set-local-rtc 1 --adjust-system-clock
sudo timedatectl

# minimize application on click in the dock
gsettings set org.gnome.shell.extensions.dash-to-dock click-action 'minimize'

echo "                                                              "
echo "Installing system applications"
echo "--------------------------------------------------------------"
sudo apt update -y
sudo apt-get install git gh mc tmux zsh mosh curl wget ca-certificates net-tools make apt-transport-https gpg gnupg -y

echo "                                                              "
echo "Installing python & nodejs"
echo "--------------------------------------------------------------"
# install python
sudo apt install python3 python3-pip python3-venv python3-tk python3-py -y


# install nvm + node
sudo apt install npm nodejs -y


# install gnome extensions
sudo apt install dconf-editor gnome-shell-extensions gnome-tweaks ubuntu-restricted-extras -y

echo "                                                              "
echo "Installing telegram"
echo "--------------------------------------------------------------"
snap install telegram-desktop

echo "                                                              "
echo "Installing Chrome"
echo "--------------------------------------------------------------"
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo dpkg -i ./google-chrome*.deb
sudo apt-get install -f
sudo rm ./google-chrome*.deb

sudo apt -f install

echo "                                                              "
echo "You can copy system files to: /$HOME"
echo "--------------------------------------------------------------"