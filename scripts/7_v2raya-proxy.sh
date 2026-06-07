#!/bin/bash

#================================================================
# v2rayA Proxy Installer for Ubuntu 22.04+
# Installs v2rayA (Web GUI) + v2fly/v2ray-core
# Provides SOCKS5/HTTP proxy setup with optional system-wide TUN mode
#================================================================

set -e

# --- Configuration ---
V2RAYA_VERSION="v2.2.7.5"
V2RAY_CORE_VERSION="v5.49.0"
V2RAYA_BIN="/usr/local/bin/v2raya"
V2RAY_CORE_BIN="/usr/local/bin/v2ray"
V2RAYA_CONFIG_DIR="/etc/v2raya"
V2RAYA_SERVICE="/etc/systemd/system/v2raya.service"
V2RAYA_LOG_FILE="/tmp/v2raya.log"

# Architecture
ARCH="amd64"
if [ "$(uname -m)" = "aarch64" ]; then ARCH="arm64"
fi

# --- Helpers ---
info()  { echo; echo "--- $1 ---"; }
warn()  { echo "[WARN] $1"; }
err()   { echo "[ERR] $1" >&2; exit 1; }

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        err "Run as root: sudo ./8_v2raya-proxy.sh"
    fi
}

# === Step 1: v2ray-core ===
install_v2ray_core() {
    info "Installing v2ray-core $V2RAY_CORE_VERSION ($ARCH)"
    if [ -f "$V2RAY_CORE_BIN" ] && $V2RAY_CORE_BIN --version &>/dev/null; then
        echo "Already installed: $($V2RAY_CORE_BIN --version | head -n1)"
        return 0
    fi

    local tmp="/tmp/v2raya-install-core-$$"
    mkdir -p "$tmp" && cd "$tmp"

    local url="https://github.com/v2fly/v2ray-core/releases/download/${V2RAY_CORE_VERSION}/v2ray-linux-${ARCH}.zip"
    wget -q --show-progress "$url" -O core.zip || {
        local tag=$(curl -s "https://api.github.com/repos/v2fly/v2ray-core/releases/latest" | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null)
        [ -z "$tag" ] && err "Failed to fetch v2ray-core"
        wget -q --show-progress "https://github.com/v2fly/v2ray-core/releases/download/${tag}/v2ray-linux-${ARCH}.zip" -O core.zip
    }

    unzip -qo core.zip
    mv v2ray "$V2RAY_CORE_BIN" && chmod +x "$V2RAY_CORE_BIN"
    for f in geoip.dat geosite.dat; do
        [ -f "$f" ] && mv "$f" "/usr/local/share/v2ray/" 2>/dev/null || true
    done

    cd / && rm -rf "$tmp"
    echo "Done: $($V2RAY_CORE_BIN --version | head -n1)"
}

# === Step 2: v2rayA ===
install_v2raya() {
    info "Installing v2rayA $V2RAYA_VERSION ($ARCH)"
    if [ -f "$V2RAYA_BIN" ] && $V2RAYA_BIN --version &>/dev/null; then
        echo "Already installed: $($V2RAYA_BIN --version)"
        return 0
    fi

    local tmp="/tmp/v2raya-install-v2raya-$$"
    mkdir -p "$tmp" && cd "$tmp"

    local url="https://github.com/v2rayA/v2rayA/releases/download/${V2RAYA_VERSION}/installer_linux_${ARCH}_${V2RAYA_VERSION#v}.tar.gz"
    wget -q --show-progress "$url" -O v2raya.tar.gz || {
        url="https://github.com/v2rayA/v2rayA/releases/download/${V2RAYA_VERSION}/installer_linux_${ARCH}_${V2RAYA_VERSION#v}.tar.xz"
        wget -q --show-progress "$url" -O v2raya.tar.xz
        tar -xf v2raya.tar.xz
        find . -name "v2raya" -type f -exec mv {} "$V2RAYA_BIN" \; 2>/dev/null || true
        cd / && rm -rf "$tmp"
        [ -f "$V2RAYA_BIN" ] && chmod +x "$V2RAYA_BIN" && echo "Done: $($V2RAYA_BIN --version)" && return 0
        err "Failed to install v2rayA"
    }

    tar -xf v2raya.tar.gz
    mv v2raya "$V2RAYA_BIN" && chmod +x "$V2RAYA_BIN"
    cd / && rm -rf "$tmp"
    echo "Done: $($V2RAYA_BIN --version)"
}

# === Step 3: systemd service ===
setup_service() {
    info "Creating systemd service"
    mkdir -p "$V2RAYA_CONFIG_DIR" /usr/local/share/v2ray

    cat > "$V2RAYA_SERVICE" << 'SERVICEEOF'
[Unit]
Description=A web GUI client of Project V which supports VMess, VLESS, SS, SSR, Trojan, Tuic and Juicity protocols
Documentation=https://v2raya.org
After=network.target nss-lookup.target iptables.service ip6tables.service nftables.service
Wants=network.target

[Service]
Environment="V2RAYA_CONFIG=/etc/v2raya"
Environment="V2RAYA_LOG_FILE=/tmp/v2raya.log"
Type=simple
User=root
LimitNPROC=500
LimitNOFILE=1000000
ExecStart=/usr/local/bin/v2raya
Restart=on-failure

[Install]
WantedBy=multi-user.target
SERVICEEOF
    chmod 644 "$V2RAYA_SERVICE"
    systemctl daemon-reload
    echo "Service created: $V2RAYA_SERVICE"
}

# === Step 4: Start ===
start_service() {
    info "Starting v2rayA service"
    systemctl enable v2raya.service
    systemctl start v2raya.service
    sleep 3
    if systemctl is-active --quiet v2raya.service; then
        echo "v2rayA is running."
    else
        warn "Service not running. Check: journalctl -u v2raya -n 50"
        systemctl status v2raya.service --no-pager || true
    fi
}

# === Step 5: proxy-switch helper ===
setup_proxy_switch_script() {
    local REAL_USER="${SUDO_USER:-$USER}"
    local REAL_HOME=$(eval echo "~${REAL_USER}")
    local bin="${REAL_HOME}/.local/bin/proxy-switch"

    mkdir -p "$(dirname "$bin")"
    info "Installing proxy-switch -> $bin"

    # Используем вашу идею с 'HELPEREOF' — это отлично защищает внутренности
    cat > "$bin" << 'HELPEREOF'
#!/bin/bash
PROXY_HTTP="http://127.0.0.1:20171"
PROXY_HTTPS="http://127.0.0.1:20171"
PROXY_SOCKS5="socks5://127.0.0.1:20170"
NO_PROXY="localhost,127.0.0.1,::1,192.168.0.0/16,172.16.0.0/12,10.0.0.0/8"
PROXY_ENV="${HOME}/.proxy-env"

enable() {
    [ -f "${PROXY_ENV}" ] && echo "Already enabled." && return 0
    systemctl is-active --quiet v2raya.service 2>/dev/null || { echo "v2rayA not running"; return 1; }
    cat > "${PROXY_ENV}" << PROXYEOF
export http_proxy="${PROXY_HTTP}"
export https_proxy="${PROXY_HTTPS}"
export HTTP_PROXY="${PROXY_HTTP}"
export HTTPS_PROXY="${PROXY_HTTPS}"
export ALL_PROXY="${PROXY_SOCKS5}"
export no_proxy="${NO_PROXY}"
export NO_PROXY="${NO_PROXY}"
PROXYEOF
    echo "Proxy ENABLED. Run: source ${PROXY_ENV}"
    echo "(In new terminal proxy will auto-enable)"
}

disable() {
    rm -f "${PROXY_ENV}"
    echo "Proxy DISABLED. Unset vars or open new terminal."
}

status() {
    echo "=== Proxy Status ==="
    [ -f "${PROXY_ENV}" ] && echo "Status: ENABLED" && cat "${PROXY_ENV}" || echo "Status: DISABLED"
    echo
    systemctl is-active --quiet v2raya.service 2>/dev/null && echo "v2rayA: running (http://127.0.0.1:2017)" || echo "v2rayA: NOT running"
    if [ -f "${PROXY_ENV}" ]; then
        local ip=$(curl -s --connect-timeout 5 -x "${PROXY_HTTP}" https://api.ipify.org 2>/dev/null)
        [ -n "$ip" ] && echo "Proxy IP: $ip (OK)" || echo "Proxy test: FAILED"
    fi
}

case "${1:-status}" in on|enable) enable ;; off|disable) disable ;; *) status ;; esac
HELPEREOF

    chmod +x "$bin"
    chown "${REAL_USER}:${REAL_USER}" "$bin" 2>/dev/null || true

    # Накатываем интеграцию в шелл и фиксим права
    for rc in ".bashrc" ".zshrc"; do
        local rc_path="${REAL_HOME}/${rc}"
        if [ -f "$rc_path" ]; then
            grep -q "proxy-env" "$rc_path" 2>/dev/null || \
                echo "[ -f \"\$HOME/.proxy-env\" ] && source \"\$HOME/.proxy-env\"" >> "$rc_path"
            chown "${REAL_USER}:${REAL_USER}" "$rc_path" 2>/dev/null || true
        fi
    done

    [ -d "/usr/local/bin" ] && ln -sf "$bin" "/usr/local/bin/proxy-switch"
    echo "Helper installed. Run 'proxy-switch on' to enable proxy."
}

# === Step 6: Info ===
show_info() {
    echo
    echo "============================================================"
    echo "  v2rayA Installation Complete!"
    echo "============================================================"
    echo
    echo "  Web UI:   http://127.0.0.1:2017"
    echo "  SOCKS5:   127.0.0.1:20170"
    echo "  HTTP:     127.0.0.1:20171"
    echo
    echo "  Usage:"
    echo "    1. Open http://127.0.0.1:2017, add server, connect"
    echo "    2. For CLI proxy: proxy-switch on"
    echo "    3. For TUN (full system): Web UI -> Settings -> TUN mode"
    echo
    echo "  Reset Password:"
    echo "    sudo systemctl stop v2raya && sudo v2raya --reset-password && sudo systemctl start v2raya"
    echo
    echo "  !!! IMPORTANT: Do NOT use 'proxy-switch on' when TUN mode is active"
    echo "      in the Web UI — it will cause a routing loop!"
    echo
    echo "============================================================"
    echo
}

# === Main ===
main() {
    echo; echo "=== v2rayA Proxy Installer ==="; echo
    check_root
    mkdir -p "$V2RAYA_CONFIG_DIR" /usr/local/share/v2ray
    install_v2ray_core
    install_v2raya
    setup_service
    start_service
    setup_proxy_switch_script
    show_info
}

main "$@"