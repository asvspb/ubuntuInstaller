#!/bin/bash

set -e

echo "                                                              "
echo "Поиск новой версии Virtualbox...."
echo "--------------------------------------------------------------"

# Define download directory
download_dir="$HOME/Downloads/VirtualBox/"

# Create download directory if it does not exist
if [ ! -d "$download_dir" ]; then
    mkdir -p "$download_dir"
fi

# Get the codename of the current Ubuntu release
codename=$(lsb_release -cs)

# Go to the VirtualBox download page
wget -qO- https://www.virtualbox.org/wiki/Downloads > page.html

# Get the list of versions
ver=$(grep -oP 'https://download.virtualbox.org/virtualbox/[0-9]+\.[0-9]+\.[0-9]+/' page.html | sort -rV)

# Get the latest version for the current Ubuntu release
new_version=$(echo "$ver" | head -n1 | grep -oP '[0-9]+\.[0-9]+\.[0-9]+')
deb_file="virtualbox-7.0_${new_version}-158379~Ubuntu~${codename}_amd64.deb"

echo "                                                              "
echo "Последняя версия: Virtualbox $new_version"
echo "--------------------------------------------------------------"
rm page.html

# Build the new version URL
url="https://download.virtualbox.org/virtualbox/${new_version}/"

# Download the VirtualBox DEB file
echo "                                                              "
echo "Загружаем и устанавливаем: Virtualbox $new_version для Ubuntu($codename)"
echo "--------------------------------------------------------------"
deb_file_url="${url}${deb_file}"
curl -L -o "${download_dir}${deb_file}" "${deb_file_url}"

# Install VirtualBox from DEB file
sudo apt-get update
sudo apt-get install -y "${download_dir}${deb_file}"

# Add your user to the vboxusers group
sudo usermod -a -G vboxusers $USER
sudo /sbin/vboxconfig

# Define URL of VirtualBox extension pack
extension_pack_url="https://download.virtualbox.org/virtualbox/${new_version}/Oracle_VM_VirtualBox_Extension_Pack-${new_version}.vbox-extpack"

# Define URL of VirtualBox Guest Additions
guest_additions_url="https://download.virtualbox.org/virtualbox/${new_version}/VBoxGuestAdditions_${new_version}.iso"

# Download VirtualBox Extension Pack
echo "                                                              "
echo "Загружаем Virtualbox $new_version Extension Pack ...."
echo "--------------------------------------------------------------"
curl -L -o "${download_dir}Oracle_VM_VirtualBox_Extension_Pack.vbox-extpack" "${extension_pack_url}"

# Download VirtualBox Guest Additions
echo "                                                              "
echo "Загружаем Virtualbox $new_version Guest Additions ...."
echo "--------------------------------------------------------------"
curl -L -o "${download_dir}VBoxGuestAdditions_${new_version}.iso" "${guest_additions_url}"

echo "                                                              "
echo "Virtualbox $new_version для Ubuntu($codename) успешно установлен!"
echo "--------------------------------------------------------------"
echo "Extension Pack и Guest Additions находятся в $download_dir"

