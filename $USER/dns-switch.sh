#!/bin/bash

RESOLVED_CONF="/etc/systemd/resolved.conf"
BACKUP_FILE="/etc/systemd/resolved.conf.backup"
XBOX_DNS="111.88.96.50 111.88.96.51"

get_active_connection() {
    # Get active WiFi or ethernet connection
    local conn
    conn=$(nmcli -t -f NAME,TYPE connection show --active 2>/dev/null | grep -E 'wifi|ethernet' | head -1 | cut -d: -f1)
    if [[ -z "$conn" ]]; then
        # Fallback: any active connection with default route
        conn=$(nmcli -t -f NAME connection show --active | head -1)
    fi
    if [[ -z "$conn" ]]; then
        echo "Error: No active network connection found" >&2
        exit 1
    fi
    echo "$conn"
}

usage() {
    echo "Usage: sudo $0 {xbox|restore|status}"
    echo ""
    echo "Commands:"
    echo "  xbox     - Switch to Xbox DNS (111.88.96.50, 111.88.96.51)"
    echo "  restore  - Restore original DNS settings from backup"
    echo "  status   - Show current DNS configuration"
    exit 1
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "Error: This script requires root privileges. Use: sudo $0 $1"
        exit 1
    fi
}

set_xbox_dns() {
    check_root "xbox"

    local CONNECTION_NAME
    CONNECTION_NAME=$(get_active_connection)

    echo "=== Switching to Xbox DNS ==="
    echo "Active connection: $CONNECTION_NAME"

    # Backup current config if not exists
    if [[ ! -f "$BACKUP_FILE" ]]; then
        cp "$RESOLVED_CONF" "$BACKUP_FILE"
        echo "Backup created: $BACKUP_FILE"
    fi

    # Save per-connection backup for restore
    local conn_backup="/etc/systemd/resolved.conn.backup"
    nmcli -g ipv4.dns connection show "$CONNECTION_NAME" > "$conn_backup" 2>/dev/null

    # Set global DNS
    sed -i 's/^DNS=.*/DNS=111.88.96.50 111.88.96.51/' "$RESOLVED_CONF"
    if ! grep -q "^DNS=" "$RESOLVED_CONF"; then
        sed -i '/^\[Resolve\]/a DNS=111.88.96.50 111.88.96.51' "$RESOLVED_CONF"
    fi

    # Set interface DNS
    nmcli connection modify "$CONNECTION_NAME" ipv4.dns "$XBOX_DNS"
    nmcli connection modify "$CONNECTION_NAME" ipv4.ignore-auto-dns yes

    # Restart and reconnect
    systemctl restart systemd-resolved
    nmcli connection down "$CONNECTION_NAME" && nmcli connection up "$CONNECTION_NAME"

    echo "Done! Now using Xbox DNS."
    resolvectl status | grep -A2 "Global"
}

restore_dns() {
    check_root "restore"

    local CONNECTION_NAME
    CONNECTION_NAME=$(get_active_connection)

    if [[ ! -f "$BACKUP_FILE" ]]; then
        echo "Error: Backup file not found: $BACKUP_FILE"
        exit 1
    fi

    echo "=== Restoring original DNS settings ==="
    echo "Active connection: $CONNECTION_NAME"

    cp "$BACKUP_FILE" "$RESOLVED_CONF"

    # Restore interface DNS (remove ignore-auto-dns)
    nmcli connection modify "$CONNECTION_NAME" ipv4.dns ""
    nmcli connection modify "$CONNECTION_NAME" ipv4.ignore-auto-dns no

    # Restart and reconnect
    systemctl restart systemd-resolved
    nmcli connection down "$CONNECTION_NAME" && nmcli connection up "$CONNECTION_NAME"

    echo "Done! Original DNS settings restored."
    resolvectl status | grep -A2 "Global"
}

show_status() {
    local CONNECTION_NAME
    CONNECTION_NAME=$(get_active_connection)

    echo "=== Current DNS Configuration ==="
    resolvectl status
    echo ""
    echo "=== Active Connection: $CONNECTION_NAME ==="
    nmcli -f ipv4.dns,ipv4.ignore-auto-dns connection show "$CONNECTION_NAME" | grep -E "ipv4.dns|ignore-auto"
}

case "${1,,}" in
    xbox)
        set_xbox_dns
        ;;
    restore)
        restore_dns
        ;;
    status)
        show_status
        ;;
    *)
        usage
        ;;
esac
