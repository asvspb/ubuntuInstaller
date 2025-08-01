#!/bin/bash

set -e

echo " "
echo "Setting up passwords"
echo "--------------------------------------------------------------"
# so that it does not ask for a password with sudo
sudo bash -c 'echo "$USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-nopasswd'
sudo chmod 0440 /etc/sudoers.d/90-nopasswd

# so that it does not wait for confirmation during installation
export DEBIAN_FRONTEND=noninteractive
if [ -f /etc/needrestart/needrestart.conf ]; then
  sudo sed -i '/\$nrconf{restart}/s/^#//g' /etc/needrestart/needrestart.conf
  sudo sed -i "/nrconf{restart}/s/'i'/'a'/g" /etc/needrestart/needrestart.conf
else
  sudo mkdir -p /etc/needrestart
  echo '$nrconf{restart}' = \'a\'';' > nrconf
  sudo cp nrconf /etc/needrestart/needrestart.conf
  rm nrconf
fi

# so that it does not ask for authenticity of host gitlab.com
mkdir -p ~/.ssh
chmod 0700 ~/.ssh
echo -e "Host gitlab.com\n   StrictHostKeyChecking no\n   UserKnownHostsFile=/dev/null" > ~/.ssh/config

echo " "
echo "Preliminary removal of old versions of docker"
echo "--------------------------------------------------------------"
# remove everything unnecessary
for pkg in docker.io docker-doc docker-compose podman-docker containerd runc; do sudo apt-get remove $pkg; done

echo " "
echo "Installing docker"
echo "--------------------------------------------------------------"
sudo apt install -y apt-transport-https ca-certificates curl software-properties-common wget gpg gnupg

# add key for docker
sudo rm -f /etc/apt/keyrings/docker.gpg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# add docker repository
echo \
  "deb [arch="$(dpkg --print-architecture)" signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  "$(. /etc/os-release && echo "$VERSION_CODENAME")" stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update

# packages for docker
sudo apt update
sudo apt install -y gawk m4 libpcre3-dev libxerces-c-dev libspdlog-dev libuchardet-dev libssh-dev libssl-dev libsmbclient-dev libnfs-dev libneon27-dev libarchive-dev cmake g++ -y

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin docker-ce-rootless-extras

echo " "
echo "Installing docker-compose"
echo "--------------------------------------------------------------"
# install Docker Compose
if [ ! -f /usr/local/bin/docker-compose ]; then
  sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-`uname -s`-`uname -m`" -o /usr/local/bin/docker-compose
  sudo chmod +x /usr/local/bin/docker-compose
fi

echo " "
echo "Installing lazydocker"
echo "--------------------------------------------------------------"
# Get the latest version tag of Lazydocker release from GitHub
LAZYDOCKER_VERSION=$(curl -s "https://api.github.com/repos/jesseduffield/lazydocker/releases/latest" | grep -Po '"tag_name": "v\K[0-9.]+')
curl -Lo lazydocker.tar.gz "https://github.com/jesseduffield/lazydocker/releases/latest/download/lazydocker_${LAZYDOCKER_VERSION}_Linux_x86_64.tar.gz"

mkdir lazydocker-temp
tar xf lazydocker.tar.gz -C lazydocker-temp
sudo mv lazydocker-temp/lazydocker /usr/local/bin
rm -rf lazydocker.tar.gz lazydocker-temp
lazydocker --version


echo " "
echo "Creating Dev folder"
echo "--------------------------------------------------------------"
mkdir -p ~/Dev

echo " "
echo "Creating docker group. Reboot required!"
echo "--------------------------------------------------------------"
sudo usermod -aG docker $USER
sudo systemctl start docker
sudo gpasswd -a $USER docker


echo '-------------------------------------------------------------------'
echo '---------------------- REBOOT IN 5 SEC ----------------------------'
echo '-------------------------------------------------------------------'
sleep 5
sudo reboot