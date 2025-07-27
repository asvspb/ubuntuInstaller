#!/bin/bash

# Exit on error
set -e

# Check for root privileges
if [ "$EUID" -ne 0 ]; then
  echo "=== Please run as root"
  exit
fi
# check for installation of ZeroTier
if ! command -v zerotier-cli &> /dev/null; then
    echo "=== ZeroTier not found. Installing..."
    curl -s https://install.zerotier.com | bash
else
    echo "=== ZeroTier is already installed."
fi
# Prompt for ZeroTier network ID
read -p "Enter ZeroTier Network ID: " NETWORK_ID

echo "=== Joining network $NETWORK_ID..."
zerotier-cli join "$NETWORK_ID"


echo "!!! Please go to https://my.zerotier.com/network/ and authorize this new node"
echo "!!! for network $NETWORK_ID"

echo "=== Current networks:"
zerotier-cli listnetworks

echo "=== Waiting for authorization..."
while ! zerotier-cli listnetworks | grep -q "$NETWORK_ID"; do
  sleep 5
  echo "Still waiting for authorization..."
done

# Change client configuration
echo "=== Network $NETWORK_ID authorized. Configuring ZeroTier..."
sudo zerotier-cli set "$NETWORK_ID" allowDNS=1
sudo zerotier-cli set "$NETWORK_ID" allowDefault=1
sudo zerotier-cli set "$NETWORK_ID" allowGlobal=1

echo "=== Restarting ZeroTier service..."
systemctl restart zerotier-one

echo "=== Waiting for ZeroTier to connect..."
sleep 5

echo "=== Current ZeroTier status:"
zerotier-cli status

echo "=== Checking public IP address..."
curl ipinfo.io

echo "Well done!"
