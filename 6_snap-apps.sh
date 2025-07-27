#!/bin/bash
echo " "
echo "Installing snap system applications"
echo "--------------------------------------------------------------"
# Path to the file where the list of snap packages is saved
PACKAGE_FILE="ubuntu_snap_packages.txt"

# Reading the file and installing packages if they are not in the system
while IFS= read -r package; do
  if ! snap list "$package" 2>/dev/null | grep -q "$package"; then
    echo "Installing package $package..."
    sudo snap install "$package"
  else
    echo "Package $package is already installed."
  fi
done < "$PACKAGE_FILE"

sudo snap install obsidian --classic
sudo snap install gitkraken --classic