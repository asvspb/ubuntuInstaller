#!/bin/bash

# Exit on error
set -e

# check for installation of ZeroTier
if ! command -v zerotier-cli &> /dev/null; then
    echo "=== ZeroTier not found. Installing..."
    curl -s https://install.zerotier.com | bash
else
    echo "=== ZeroTier is already installed."
fi

# You can manually start the ZeroTier service with:
# sudo systemctl start zerotier-one

# Get a list of networks and filter unauthorized ones
unauthorized_networks=$(sudo zerotier-cli listnetworks | grep -E "ACCESS_DENIED|NOT_FOUND" | awk '{print $3}')

# Check if there are unauthorized networks
if [ -z "$unauthorized_networks" ]; then
  echo "=== Unauthorized networks not found."
  exit 0
fi

# Leave each unauthorized network
for nwid in $unauthorized_networks; do
  echo "=== Leaving unauthorized network: $nwid"
  sudo zerotier-cli leave "$nwid"
done

echo "=== All unauthorized networks have been successfully removed."

# Prompt for ZeroTier network ID
read -p "Enter ZeroTier Network ID: " NETWORK_ID

echo "=== Joining network $NETWORK_ID..."
sudo zerotier-cli join "$NETWORK_ID"


echo "!!! Please go to https://my.zerotier.com/network/ and authorize this new node"
echo "!!! for network $NETWORK_ID"

# press enter to continue
read -p "=== Press 'Enter' to continue after authorizing the node on the ZeroTier network..."

while ! sudo zerotier-cli listnetworks | grep "$NETWORK_ID" | grep -q "OK"; do
  sleep 30
  echo "=== Still waiting for authorization... (checking every 30s)"
done

# Change client configuration
echo "=== Network $NETWORK_ID authorized. Configuring ZeroTier..."
sudo zerotier-cli set "$NETWORK_ID" allowDNS=1
sudo zerotier-cli set "$NETWORK_ID" allowDefault=1
sudo zerotier-cli set "$NETWORK_ID" allowGlobal=1

echo "=== You may need to restart the ZeroTier service for the changes to take effect:"
echo "=== sudo systemctl restart zerotier-one"

echo "=== Current networks:"
sudo zerotier-cli listnetworks

sudo systemctl disable zerotier-one.service