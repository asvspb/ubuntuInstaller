#!/bin/bash

set -e

ZT_NETWORK_FILE="$HOME/.zt-network"
ZT_VDS_SERVER_FILE="$HOME/.zt-vds-server"

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
    [[ -f "$ZT_NETWORK_FILE" ]] && cat "$ZT_NETWORK_FILE"
}

save_network() {
    echo "$1" > "$ZT_NETWORK_FILE"
    echo "=== Network $1 saved to $ZT_NETWORK_FILE"
}

join_and_configure() {
    local network_id="$1"
    local auth_url="${2:-https://my.zerotier.com/network/${network_id}}"

    echo "=== Joining network $network_id..."
    sudo zerotier-cli join "$network_id"

    echo "!!! Authorize this node at: ${auth_url}"
    read -p "=== Press 'Enter' after authorizing..."

    local attempts=0
    while ! sudo zerotier-cli listnetworks | grep "$network_id" | grep -q "OK"; do
        sleep 5
        attempts=$((attempts + 1))
        if [[ $attempts -gt 60 ]]; then
            echo "=== Timeout waiting for authorization."
            exit 1
        fi
        echo "=== Still waiting for authorization... (${attempts}x5s)"
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

# --- VDS Server: получить сети по SSH ---
fetch_vds_networks() {
    local server="$1"
    ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no "$server" \
'python3 -c "
import json, os
env_file = \"/opt/ztnet/.env.info\"
topo_file = \"/opt/ztnet/topology.json\"
env = {}
if os.path.exists(env_file):
    for line in open(env_file):
        k, _, v = line.strip().partition(\"=\")
        env[k] = v
url = env.get(\"ZTNET_URL\", \"\")
nets = {}
if os.path.exists(topo_file):
    data = json.load(open(topo_file))
    for nid, net in data.get(\"networks\", {}).items():
        nets[nid] = net
for nid in sorted(nets):
    name = nets[nid].get(\"name\", nid)
    role = nets[nid].get(\"role\", \"mesh\")
    print(f\"{nid} {name} {role} {url}\")
" 2>/dev/null' 2>/dev/null
}

# --- Основной поток ---
cleanup_dead_networks

saved=$(get_saved_network)
if [[ -n "$saved" ]]; then
    ok_networks=$(sudo zerotier-cli listnetworks | grep "OK" | awk '{print $3}')
    if echo "$ok_networks" | grep -q "$saved"; then
        echo "=== Already connected to saved network: $saved"
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

# Попытка получить сети с VDS-сервера
VDS_NETWORKS=""
VDS_SERVER=""

if [[ -f "$ZT_VDS_SERVER_FILE" ]]; then
    VDS_SERVER=$(head -1 "$ZT_VDS_SERVER_FILE")
    echo "=== Checking VDS server: $VDS_SERVER"
    VDS_NETWORKS=$(fetch_vds_networks "$VDS_SERVER")
    [[ -z "$VDS_NETWORKS" ]] && echo "=== Could not reach VDS server."
else
    read -p "Do you have a VDS server with ZeroTier networks? (y/N): " has_vds
    if [[ "$has_vds" == "y" || "$has_vds" == "Y" ]]; then
        read -p "Enter VDS server (e.g. root@185.212.128.160): " VDS_SERVER
        if [[ -n "$VDS_SERVER" ]]; then
            echo "$VDS_SERVER" > "$ZT_VDS_SERVER_FILE"
            echo "=== VDS server saved to $ZT_VDS_SERVER_FILE"
            echo "=== Fetching networks from $VDS_SERVER..."
            VDS_NETWORKS=$(fetch_vds_networks "$VDS_SERVER")
        fi
    fi
fi

# Если VDS-сети найдены — интерактивный выбор
NETWORK_ID=""
VDS_AUTH_URL=""

if [[ -n "$VDS_NETWORKS" ]]; then
    echo ""
    echo "=== Networks on VDS server ($VDS_SERVER):"
    declare -a nwids names roles urls
    i=0
    while IFS=' ' read -r nwid name role url; do
        nwids[$i]="$nwid"; names[$i]="$name"; roles[$i]="$role"; urls[$i]="$url"
        printf "  %d. %-20s  %-18s  %s\n" $((i+1)) "${names[$i]}" "${nwids[$i]}" "${roles[$i]}"
        i=$((i+1))
    done <<< "$VDS_NETWORKS"
    printf "  %d. Enter network ID manually\n" $((i+1))
    echo ""

    read -p "Select network (number): " sel
    if [[ "$sel" =~ ^[0-9]+$ && "$sel" -ge 1 && "$sel" -le "$i" ]]; then
        local_idx=$((sel - 1))
        NETWORK_ID="${nwids[$local_idx]}"
        VDS_AUTH_URL="${urls[$local_idx]}"
        [[ -z "$VDS_AUTH_URL" ]] && VDS_AUTH_URL="http://${VDS_SERVER#*@}:3000"
    fi
fi

if [[ -z "$NETWORK_ID" ]]; then
    read -p "Enter ZeroTier Network ID: " NETWORK_ID
    VDS_AUTH_URL="https://my.zerotier.com/network/${NETWORK_ID}"
fi

if [[ -z "$NETWORK_ID" ]]; then
    echo "Error: Network ID is required." >&2
    exit 1
fi

if [[ -n "$saved" && "$saved" != "$NETWORK_ID" ]]; then
    echo "=== Leaving old network: $saved"
    sudo zerotier-cli set "$saved" allowDefault=0 > /dev/null 2>&1 || true
    sudo zerotier-cli leave "$saved" 2>/dev/null || true
fi

join_and_configure "$NETWORK_ID" "$VDS_AUTH_URL"
