#!/bin/bash

set -e

ZT_NETWORK_FILE="$HOME/.zt-network"

if ! command -v zerotier-cli &> /dev/null; then
    echo "=== ZeroTier not found. Installing..."
    curl -s https://install.zerotier.com | bash
else
    echo "=== ZeroTier is already installed."
fi

cleanup_dead_networks() {
    local dead
    dead=$(sudo zerotier-cli listnetworks | grep -E "ACCESS_DENIED|NOT_FOUND|REQUESTING_CONFIGURATION" | awk '{print $3}')
    if [[ -n "$dead" ]]; then
        for nwid in $dead; do
            echo "=== Leaving dead network: $nwid"
            sudo zerotier-cli leave "$nwid"
        done
        echo "=== Dead networks cleaned up."
    fi
}

get_saved_network() {
    if [[ -f "$ZT_NETWORK_FILE" ]]; then
        cat "$ZT_NETWORK_FILE"
    fi
}

save_network() {
    echo "$1" > "$ZT_NETWORK_FILE"
    echo "=== Network $1 saved to $ZT_NETWORK_FILE"
}

join_and_configure() {
    local network_id="$1"

    echo "=== Joining network $network_id..."
    sudo zerotier-cli join "$network_id"

    echo "!!! Please go to https://my.zerotier.com/network/${network_id} and authorize this node"

    read -p "=== Press 'Enter' to continue after authorizing the node..."

    local attempts=0
    while ! sudo zerotier-cli listnetworks | grep "$network_id" | grep -q "OK"; do
        sleep 5
        attempts=$((attempts + 1))
        if [[ $attempts -gt 60 ]]; then
            echo "=== Timeout waiting for authorization."
            exit 1
        fi
        echo "=== Still waiting for authorization... (${attempts}0s)"
    done

    echo "=== Network $network_id authorized. Configuring..."
    sudo zerotier-cli set "$network_id" allowDNS=1 > /dev/null
    sudo zerotier-cli set "$network_id" allowDefault=1 > /dev/null
    sudo zerotier-cli set "$network_id" allowGlobal=1 > /dev/null

    save_network "$network_id"

    echo "=== Current networks:"
    sudo zerotier-cli listnetworks

    sudo systemctl disable zerotier-one.service
}

cleanup_dead_networks

saved=$(get_saved_network)
if [[ -n "$saved" ]]; then
    ok_networks=$(sudo zerotier-cli listnetworks | grep "OK" | awk '{print $3}')
    if echo "$ok_networks" | grep -q "$saved"; then
        echo "=== Already connected to saved network: $saved"
        echo "=== Current networks:"
        sudo zerotier-cli listnetworks
        echo ""
        read -p "=== Switch to a different network? (y/N): " choice
        if [[ "$choice" != "y" && "$choice" != "Y" ]]; then
            echo "=== Keeping current network."
            exit 0
        fi
    else
        echo "=== Saved network $saved is not connected."
    fi
fi

read -p "Enter ZeroTier Network ID: " NETWORK_ID

if [[ -z "$NETWORK_ID" ]]; then
    echo "Error: Network ID is required." >&2
    exit 1
fi

if [[ -n "$saved" && "$saved" != "$NETWORK_ID" ]]; then
    echo "=== Leaving old network: $saved"
    sudo zerotier-cli set "$saved" allowDefault=0 > /dev/null 2>&1 || true
    sudo zerotier-cli leave "$saved" 2>/dev/null || true
fi

join_and_configure "$NETWORK_ID"
