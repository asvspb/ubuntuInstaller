# --- Configuration ---
# Timeout in seconds to wait for the IP to change.
if [ -z "${_ZT_TIMEOUT_SECONDS+x}" ]; then
    readonly _ZT_TIMEOUT_SECONDS=60
fi
# Interval in seconds between IP checks.
if [ -z "${_ZT_POLL_INTERVAL_SECONDS+x}" ]; then
    readonly _ZT_POLL_INTERVAL_SECONDS=2
fi
# URL to check for the public IP address.
if [ -z "${_ZT_IP_CHECK_URL+x}" ]; then
    readonly _ZT_IP_CHECK_URL="https://ipinfo.io/ip"
fi
_ZT_SERVER_TIMEOUT="${_ZT_SERVER_TIMEOUT:-60}"
_ZT_SERVER_POLL="${_ZT_SERVER_POLL:-5}"
# --- End Configuration ---

_zt_gateway_file="$HOME/.zt-gateway"
_zt_server_ip_file="$HOME/.zt-server-ip"

_zt_save_gateway() {
    local nwid=$(_zt_get_default_network)
    [[ -z "$nwid" ]] && return 1

    local gw
    gw=$(sudo zerotier-cli -j listnetworks 2>/dev/null | python3 -c "
import sys, json
for n in json.load(sys.stdin):
    if n.get('id') == '$nwid':
        for r in n.get('routes', []):
            if r.get('target') == '0.0.0.0/0' and r.get('via'):
                print(r['via'])
                break
    break
" 2>/dev/null)

    [[ -n "$gw" ]] && echo "$gw" > "$_zt_gateway_file"
}

_zt_save_server_ip() {
    local nwid=$(_zt_get_default_network)
    [[ -z "$nwid" ]] && return 1

    local server_ips
    server_ips=$(sudo zerotier-cli -j listpeers 2>/dev/null | python3 -c "
import sys, json
seen = set()
for p in json.load(sys.stdin):
    if p.get('role') != 'LEAF':
        continue
    for path in p.get('paths', []):
        addr = path.get('address', '')
        if '/' in addr and not addr.startswith('127.'):
            ip = addr.split('/')[0]
            if ip not in seen:
                seen.add(ip)
                print(ip)
" 2>/dev/null)

    [[ -n "$server_ips" ]] && echo "$server_ips" | sort -u > "$_zt_server_ip_file"
}

_zt_get_gateway() {
    if [[ -f "$_zt_gateway_file" ]]; then
        cat "$_zt_gateway_file"
    fi
}

_zt_get_all_server_ips() {
    local -a ips=()

    if [[ -f "$_zt_server_ip_file" ]]; then
        while IFS= read -r cached; do
            [[ -n "$cached" ]] && ips+=("$cached")
        done < "$_zt_server_ip_file"
    fi

    if sudo zerotier-cli -j listpeers &>/dev/null; then
        local peer_ips
        peer_ips=$(sudo zerotier-cli -j listpeers 2>/dev/null | python3 -c "
import sys, json
seen = set()
for p in json.load(sys.stdin):
    for path in p.get('paths', []):
        addr = path.get('address', '')
        if '/' in addr and not addr.startswith('127.'):
            ip = addr.split('/')[0]
            if ip not in seen:
                seen.add(ip)
                print(ip)
" 2>/dev/null)

        local ip
        while IFS= read -r ip; do
            [[ -n "$ip" ]] || continue
            local found=0
            local existing
            for existing in "${ips[@]}"; do
                [[ "$existing" == "$ip" ]] && found=1 && break
            done
            [[ "$found" == "0" ]] && ips+=("$ip")
        done <<< "$peer_ips"
    fi

    if [[ ${#ips[@]} -eq 0 ]]; then
        echo "9.9.9.9"
    else
        printf '%s\n' "${ips[@]}"
    fi
}

_zt_wait_for_connectivity() {
    printf "Checking internet connectivity...\n"
    if ! ping -c 1 -W 1 8.8.8.8 >/dev/null 2>&1; then
        printf "  %-20s FAIL\n" "8.8.8.8"
        printf "No internet. ZeroTier will NOT be started.\n"
        myip
        return 1
    fi
    printf "  %-20s OK\n" "8.8.8.8"

    printf "Checking ZT servers...\n"
    local -a servers
    if [[ -f "$_zt_server_ip_file" ]]; then
        while IFS= read -r ip; do
            [[ -n "$ip" ]] && servers+=("$ip")
        done < "$_zt_server_ip_file"
    fi
    if [[ ${#servers[@]} -eq 0 ]]; then
        printf "  No saved ZT servers. Skipping server check.\n"
        return 0
    fi

    local ok_count=0
    local ip
    for ip in "${servers[@]}"; do
        printf "  %-20s " "$ip"
        if ping -c 1 -W 1 "$ip" >/dev/null 2>&1; then
            printf "OK\n"
            ok_count=$((ok_count + 1))
        else
            printf "FAIL\n"
        fi
    done

    if [[ $ok_count -eq 0 ]]; then
        printf "All %d ZT servers unreachable. ZeroTier will NOT be started.\n" "${#servers[@]}"
        myip
        return 1
    fi

    printf "ZT servers: %d/%d available\n" "$ok_count" "${#servers[@]}"
    return 0
}

# Private helper function to wait for the public IP address to change.
# This function is not intended to be called directly by the user.
#
# Usage: _zt_wait_for_ip_change <initial_ip>
_zt_wait_for_ip_change() {
    local initial_ip="$1"
    local timeout="$_ZT_TIMEOUT_SECONDS"
    local poll="$_ZT_POLL_INTERVAL_SECONDS"
    local url="$_ZT_IP_CHECK_URL"

    printf "Waiting for IP address to change"

    sh -c '
      init="$1"; timeout="$2"; poll="$3"; url="$4"
      retries=$((timeout / poll))
      i=0
      while [ "$i" -lt "$retries" ]; do
        current_ip=$(curl -s --max-time 5 "$url")
        if [ -n "$current_ip" ] && [ "$current_ip" != "$init" ]; then
          exit 0
        fi
        printf "."
        sleep "$poll"
        i=$((i+1))
      done
      exit 1
    ' _ "$initial_ip" "$timeout" "$poll" "$url"
    local rc=$?

    if [ "$rc" -eq 0 ]; then
        printf "\n\nSuccess! IP address has changed.\n"
        myip
        return 0
    else
        printf "\n\nTimeout! IP address did not change within %d seconds.\n" "$timeout"
        printf "Current IP:\n"
        myip
        return 1
    fi
}

_zt_default_network_file="$HOME/.zt-network"

_zt_get_default_network() {
    if [[ -f "$_zt_default_network_file" ]]; then
        cat "$_zt_default_network_file"
    fi
}

_zt_set_default_network() {
    echo "$1" > "$_zt_default_network_file"
}

ztls() {
    echo "=== ZeroTier Networks ==="
    sudo zerotier-cli listnetworks | while IFS=' ' read -r nwid name mac zt_status type dev ip; do
        if [[ "$nwid" == "200" ]]; then
            nwid="$name"; name="$mac"; mac="$zt_status"; zt_status="$type"; type="$dev"; dev="$ip"; ip=""
        fi
        if [[ -z "$ip" && "$zt_status" != "listnetworks" ]]; then
            echo "  $nwid  $name  [$zt_status]  (no IP)"
        elif [[ "$zt_status" != "listnetworks" ]]; then
            local ad=""
            local ad_val=$(sudo zerotier-cli get "$nwid" allowDefault 2>/dev/null || echo "")
            if [[ "$ad_val" == "1" ]]; then ad=" <DEFAULT>"; fi
            local saved=$(_zt_get_default_network)
            if [[ "$nwid" == "$saved" ]]; then ad="${ad} [saved]"; fi
            echo "  $nwid  $name  [$zt_status]  $ip${ad}"
        fi
    done
    echo ""
    local saved=$(_zt_get_default_network)
    if [[ -n "$saved" ]]; then
        echo "Saved default network: $saved"
    else
        echo "No saved default network (run: ztswitch <network_id>)"
    fi
}

ztstop() {
    echo "=== Stopping ZeroTier ==="
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'ztnet_zerotier'; then
        echo "Stopping Docker: ztnet_zerotier, ztnet, ztnet_postgres..."
        docker stop ztnet_zerotier ztnet ztnet_postgres 2>/dev/null
    fi
    if systemctl is-active --quiet zerotier-one 2>/dev/null; then
        echo "Stopping system zerotier-one..."
        sudo systemctl stop zerotier-one 2>/dev/null
    fi
    if pgrep -x zerotier-one >/dev/null 2>&1; then
        echo "Killing zerotier-one processes..."
        sudo pkill -9 -x zerotier-one 2>/dev/null
    fi
    echo "Done. All ZeroTier services stopped."
    myip
}

ztcleanup() {
    local dead=$(sudo zerotier-cli listnetworks | grep -E "ACCESS_DENIED|NOT_FOUND|REQUESTING_CONFIGURATION" | awk '{print $3}')
    if [[ -z "$dead" ]]; then
        echo "No dead networks to clean up."
        return 0
    fi
    for nwid in $dead; do
        echo "Leaving: $nwid"
        sudo zerotier-cli leave "$nwid"
    done
    echo "Cleanup done."
}

ztsw() {
    local -a nwids
    while IFS= read -r line; do nwids+=("$line"); done < <(sudo zerotier-cli listnetworks | awk '/OK/{print $3}')

    if [[ ${#nwids[@]} -lt 2 ]]; then
        echo "Need at least 2 connected networks, found: ${#nwids[@]}"
        ztls
        return 1
    fi

    local current_nwid=""
    local nwid
    for nwid in "${nwids[@]}"; do
        local ad=$(sudo zerotier-cli get "$nwid" allowDefault 2>/dev/null || echo "0")
        if [[ "$ad" == "1" ]]; then
            current_nwid="$nwid"
            break
        fi
    done

    if [[ -z "$current_nwid" ]]; then
        current_nwid=$(_zt_get_default_network)
    fi

    if [[ -z "$current_nwid" ]]; then
        echo "Cannot determine active network."
        ztls
        return 1
    fi

    local target_nwid=""
    for nwid in "${nwids[@]}"; do
        if [[ "$nwid" != "$current_nwid" ]]; then
            target_nwid="$nwid"
            break
        fi
    done

    local current_name=$(sudo zerotier-cli listnetworks | grep "$current_nwid" | awk '/OK/{print $4}')
    local target_name=$(sudo zerotier-cli listnetworks | grep "$target_nwid" | awk '/OK/{print $4}')

    echo "=== Switching ZeroTier network ==="
    echo "From: $current_name ($current_nwid)"
    echo "To:   $target_name ($target_nwid)"

    echo "Disabling default route on $current_name..."
    sudo zerotier-cli set "$current_nwid" allowDefault=0 > /dev/null 2>&1
    sleep 1

    echo "Getting initial IP address..."
    local initial_ip
    initial_ip=$(curl --silent --max-time 10 "$_ZT_IP_CHECK_URL")
    if [[ -z "$initial_ip" ]]; then
        echo "Error: Could not get initial IP. Reverting..."
        sudo zerotier-cli set "$current_nwid" allowDefault=1 > /dev/null 2>&1
        return 1
    fi
    myip

    echo "Enabling default route on $target_name..."
    sudo zerotier-cli set "$target_nwid" allowDNS=1 > /dev/null
    sudo zerotier-cli set "$target_nwid" allowDefault=1 > /dev/null
    sudo zerotier-cli set "$target_nwid" allowGlobal=1 > /dev/null

    _zt_set_default_network "$target_nwid"
    _zt_save_gateway
    _zt_save_server_ip

    if _zt_wait_for_ip_change "$initial_ip"; then
        _zt_save_gateway
        _zt_save_server_ip
        echo ""
        ztls
        return 0
    fi

    echo "Reverting..."
    sudo zerotier-cli set "$target_nwid" allowDefault=0 > /dev/null 2>&1
    sudo zerotier-cli set "$current_nwid" allowDefault=1 > /dev/null 2>&1
    _zt_set_default_network "$current_nwid"
    return 1
}

ztswitch() {
    if [[ -z "$1" ]]; then
        echo "Usage: ztswitch <network_id>"
        echo ""
        echo "Current networks:"
        ztls
        return 1
    fi

    local new_nwid="$1"

    echo "=== Switching to network $new_nwid ==="

    local saved=$(_zt_get_default_network)
    if [[ -n "$saved" && "$saved" != "$new_nwid" ]]; then
        echo "Leaving old default network: $saved"
        sudo zerotier-cli set "$saved" allowDefault=0 > /dev/null 2>&1
        sudo zerotier-cli leave "$saved" 2>/dev/null
    fi

    echo "Joining $new_nwid..."
    if ! sudo zerotier-cli join "$new_nwid"; then
        echo "Error: Failed to join network." >&2
        return 1
    fi

    echo "Waiting for authorization..."
    local attempts=0
    while ! sudo zerotier-cli listnetworks | grep "$new_nwid" | grep -q "OK"; do
        local st=$(sudo zerotier-cli listnetworks | grep "$new_nwid" | awk '{print $5}')
        if [[ "$st" == "ACCESS_DENIED" ]]; then
            echo "ACCESS_DENIED - authorize node at https://my.zerotier.com/network/$new_nwid"
        fi
        sleep 5
        attempts=$((attempts + 1))
        if [[ $attempts -gt 60 ]]; then
            echo "Timeout waiting for authorization."
            return 1
        fi
    done

    echo "Enabling default route..."
    sudo zerotier-cli set "$new_nwid" allowDNS=1 > /dev/null
    sudo zerotier-cli set "$new_nwid" allowDefault=1 > /dev/null
    sudo zerotier-cli set "$new_nwid" allowGlobal=1 > /dev/null

    _zt_set_default_network "$new_nwid"
    _zt_save_gateway
    _zt_save_server_ip

    echo "=== Network $new_nwid is now default. ==="
    myip
    ztls
}

# Starts ZeroTier and waits for the public IP to change.
ztup() {
    if ! _zt_wait_for_connectivity; then
        return 1
    fi

    if systemctl is-active --quiet zerotier-one 2>/dev/null; then
        local saved=$(_zt_get_default_network)
        local zt_ok=0
        if [[ -n "$saved" ]]; then
            for nwid in $(sudo zerotier-cli listnetworks 2>/dev/null | grep "OK" | awk '{print $3}'); do
                if [[ "$nwid" == "$saved" ]]; then
                    zt_ok=1
                    break
                fi
            done
        fi
        if [[ "$zt_ok" == "1" ]]; then
            echo "ZeroTier is already running with network $saved"
            myip
            return 0
        fi
    fi

    echo "Getting initial IP address..."
    local initial_ip
    initial_ip=$(curl --silent --max-time 5 "$_ZT_IP_CHECK_URL")
    if [[ -z "$initial_ip" ]]; then
        echo "Error: Could not get the initial IP address from $_ZT_IP_CHECK_URL." >&2
        return 1
    fi
    myip

    echo "Starting ZeroTier..."
    if ! sudo systemctl start zerotier-one; then
        echo "Error: Failed to start ZeroTier service." >&2
        sudo systemctl status zerotier-one >&2
        return 1
    fi
    sudo systemctl status zerotier-one

    sleep 2

    local saved=$(_zt_get_default_network)
    if [[ -n "$saved" ]]; then
        local has_default=0
        for nwid in $(sudo zerotier-cli listnetworks | grep "OK" | awk '{print $3}'); do
            local ad=$(sudo zerotier-cli get "$nwid" allowDefault 2>/dev/null || echo "0")
            if [[ "$ad" == "1" ]]; then
                has_default=1
            fi
        done
        if [[ "$has_default" == "0" ]]; then
            echo "Enabling default route for saved network: $saved"
            sudo zerotier-cli set "$saved" allowDNS=1 > /dev/null
            sudo zerotier-cli set "$saved" allowDefault=1 > /dev/null
            sudo zerotier-cli set "$saved" allowGlobal=1 > /dev/null
        fi
    fi

    if _zt_wait_for_ip_change "$initial_ip"; then
        _zt_save_gateway
        _zt_save_server_ip
        return 0
    fi

    echo "Stopping ZeroTier (connection failed)..."
    sudo systemctl stop zerotier-one 2>/dev/null
    return 1
}

# Stops ZeroTier and waits for the public IP to revert.
ztd() {
    echo "Current IP before stopping:"
    local initial_ip
    initial_ip=$(curl --silent --max-time 5 "$_ZT_IP_CHECK_URL")
    if [[ -z "$initial_ip" ]]; then
        echo "Error: Could not get the initial IP address from $_ZT_IP_CHECK_URL." >&2
        return 1
    fi
    myip

    echo "Stopping ZeroTier..."
    if ! (sudo systemctl stop zerotier-one && sudo systemctl disable zerotier-one.service); then
        echo "Error: Failed to stop or disable ZeroTier service." >&2
        sudo systemctl status zerotier-one >&2
        return 1
    fi
    sudo systemctl status zerotier-one

    _zt_wait_for_ip_change "$initial_ip"
}

# Function to display help information.
myhelp() {
    cat <<-'EOF'
bigfiles    - покажет размеры самых больших файлов
cls         - очистка от мусора
code        - обновление vscode, js, py, gemini-cli, qwen-cli
con1        - подключиться к удаленному серверу (root)
con2        - подключиться к удаленному серверу (user)
dns-xbox    - переключить DNS для Xbox
dns-restore - восстановить DNS
dns-status  - показать текущий DNS
fzf         - консольный поисковик
gca         - автокомит и пуш на репозиторий
lan         - показывает список IP в локальной сети
myip        - показывает текущий IP
ncdu        - показывает размеры директорий
nettest     - проверка пинга, опрос локальной сети, замер скорости интернета
nmon        - миниторинг сетевых процессов
obsid       - сохранение obsidian
pbcopy      - скопировать в буфер обмена
pbpaste     - вставить из буфера обмена
ranger      - консольный файловый менеджер
smon        - миниторинг процессов
stt         - консольный замер скорости
systart     - полный цикл: апгрейд, обновление инструментов, очистка
sysupg      - апгрейд всей системы
tldr        - упрощенный хелпер линукс
trm         - сменить цветовую схему терминала (Gogh)
zts         - показать текущий IP и статус ZeroTier
ztup        - включить zerotier
ztd         - выключить zerotier
ztls        - показать статус zerotier и список сетей
ztswitch    - сменить основную сеть: ztswitch <network_id>
ztstop      - принудительно остановить все службы ZeroTier
ztcleanup   - удалить мертвые сети (ACCESS_DENIED/NOT_FOUND)
ztsw        - переключиться на другую ZT сеть (автоматически)
EOF
}
