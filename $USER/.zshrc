# ~/.bashrc: executed by bash(1) for non-login shells.
# see /usr/share/doc/bash/examples/startup-files (in the package bash-doc)
# for examples

# If not running interactively, don't do anything
case $- in
    *i*) ;;
      *) return;;
esac

# don't put duplicate lines or lines starting with space in the history.
# See bash(1) for more options
HISTCONTROL=ignoreboth

# append to the history file, don't overwrite it

# for setting history length see HISTSIZE and HISTFILESIZE in bash(1)
HISTSIZE=1000
HISTFILESIZE=2000

# check the window size after each command and, if necessary,
# update the values of LINES and COLUMNS.

# If set, the pattern "**" used in a pathname expansion context will
# match all files and zero or more directories and subdirectories.
#shopt -s globstar

# make less more friendly for non-text input files, see lesspipe(1)
[ -x /usr/bin/lesspipe ] && eval "$(SHELL=/bin/sh lesspipe)"

# set variable identifying the chroot you work in (used in the prompt below)
if [ -z "${debian_chroot:-}" ] && [ -r /etc/debian_chroot ]; then
    debian_chroot=$(cat /etc/debian_chroot)
fi

# zsh interactive configuration
if [ -n "$ZSH_VERSION" ]; then
  emulate -L zsh
  # History behavior similar to bash's histappend/ignoreboth
  setopt APPEND_HISTORY HIST_IGNORE_DUPS HIST_IGNORE_SPACE
  HISTFILE="$HOME/.zsh_history"
  HISTSIZE=10000
  SAVEHIST=20000
  # Prompt and completion
  setopt PROMPT_SUBST
  autoload -Uz compinit && compinit
  # Prompt: green user@host, blue cwd
  if [ -n "$debian_chroot" ]; then
    PROMPT="($debian_chroot)%F{green}%n@%m%f:%F{blue}%~%f$ "
  else
    PROMPT="%F{green}%n@%m%f:%F{blue}%~%f$ "
  fi
  # Terminal title: user@host: dir
  precmd() { print -Pn "\e]0;%n@%m: %~\a" }

  # Powerlevel10k prompt settings (from previous .zshrc)
  POWERLEVEL9K_LEFT_PROMPT_ELEMENTS=(host user dir)
  POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS=(status root_indicator vcs battery time)
  POWERLEVEL9K_PROMPT_ON_NEWLINE=true
  POWERLEVEL9K_KUBECONTEXT_SHOW_ON_COMMAND='kubectl|helm|kubens|kubectx|oc|istioctl|kogito'

  # Zplug plugin manager and plugins (from previous .zshrc)
  if [ ! -d "$HOME/.zplug" ]; then
    command -v git >/dev/null 2>&1 && git clone https://github.com/b4b4r07/zplug "$HOME/.zplug" >/dev/null 2>&1 || true
  fi
  if [ -r "$HOME/.zplug/init.zsh" ]; then
    source "$HOME/.zplug/init.zsh"
    zplug romkatv/powerlevel10k, as:theme

    zplug "robbyrussell/oh-my-zsh", as:plugin, use:"lib/*.zsh"
    zplug "plugins/archlinux",         from:oh-my-zsh
    zplug "plugins/colored-man-pages", from:oh-my-zsh
    zplug "plugins/colorize",          from:oh-my-zsh
    zplug "lib/completion",            from:oh-my-zsh
    zplug "lib/history",               from:oh-my-zsh
    zplug "lib/key-bindings",          from:oh-my-zsh
    zplug "lib/termsupport",           from:oh-my-zsh
    zplug "lib/directories",           from:oh-my-zsh
    zplug "plugins/git",               from:oh-my-zsh
    zplug "plugins/history",           from:oh-my-zsh

    zplug "zsh-users/zsh-autosuggestions"
    # zplug "zsh-users/zsh-syntax-highlighting"
    zplug "zdharma/fast-syntax-highlighting"
    zplug "zsh-users/zsh-completions"
    zplug "zsh-users/zsh-history-substring-search"
    zplug "MichaelAquilina/zsh-you-should-use"

    if ! zplug check; then
      zplug install
    fi
    zplug load
  fi
fi

# enable color support of ls and also add handy aliases
if [ -x /usr/bin/dircolors ]; then
    test -r ~/.dircolors && eval "$(dircolors -b ~/.dircolors)" || eval "$(dircolors -b)"
    alias ls='ls --color=auto'
    #alias dir='dir --color=auto'
    #alias vdir='vdir --color=auto'

    alias grep='grep --color=auto'
    alias fgrep='fgrep --color=auto'
    alias egrep='egrep --color=auto'
fi

# colored GCC warnings and errors
#export GCC_COLORS='error=01;31:warning=01;35:note=01;36:caret=01;32:locus=01:quote=01'

# some more ls aliases
alias ll='ls -alF'
alias la='ls -A'
alias l='ls -CF'

# sleep commands
alias disablesleep='sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target'
alias enablesleep='sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target'

# Add an "alert" alias for long running commands.  Use like so:
#   sleep 10; alert
alias alert='notify-send --urgency=low -i "$([ $? = 0 ] && echo terminal || echo error)" "$(history|tail -n1|sed -e '\''s/^\s*[0-9]\+\s*//;s/[;&|]\s*alert$//'\'')"'

# New terminal scheme
# https://gogh-co.github.io/Gogh/
alias trm='bash -c  "$(wget -qO- https://git.io/vQgMr)"'



#
# MY ALIASES
#
alias lan="echo -e '\n---------\nlan test\n---------' && echo 192.168.1.{1..254}|xargs -n1 -P0 ping -c1|grep 'bytes from'"
alias nettest="echo -e '\n---------\nping test\n---------' && ping -c 5 8.8.8.8 && lan && speedtest"
alias myip="curl -s https://ipinfo.io/json | jq -r '\"Current IP: \" + .ip, \
    \"City: \" + .city, \
    \"Region: \" + .region, \
    \"Country: \" + .country'"
alias gca="git add . && git commit -m'Auto-commit' && git push"
alias cls="sudo ~/clean-sys.sh"
alias stt="speedtest"
alias smon="sudo btop"
NET_ADAPTER=$(ifconfig | grep -oE '^[^ ]+:' | grep -oE 'wl[^:]+')
alias nmon="sudo iftop -i $NET_ADAPTER"
alias sysupg="sudo apt update && sudo apt upgrade -y"
alias obsid="cd ~/Dev/Obsidian-Vault/ && gca"
alias bigfiles="sudo du -ah --max-depth=1 | sort -rh"
alias pbcopy='xclip -selection clipboard'
alias pbpaste='xclip -selection clipboard -o'
alias zts='myip && sudo systemctl status zerotier-one'
alias con1='ssh root@193.148.59.14' #hiplet server
alias con2='ssh asv-spb@193.148.59.14' #hiplet server
alias code='~/code-updater.sh'
alias systart='sysupg; code; cls'
# DNS Switch aliases
alias dns-xbox="sudo ~/dns-switch.sh xbox"
alias dns-restore="sudo ~/dns-switch.sh restore"
alias dns-status="sudo ~/dns-switch.sh status"

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

    # Run polling in a clean child shell so tracing/verbose options from the parent don't leak into it
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
    nwids=(${(f)"$(sudo zerotier-cli listnetworks | awk '/OK/{print $3}')"})

    if [[ ${#nwids} -lt 2 ]]; then
        echo "Need at least 2 connected networks, found: ${#nwids}"
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
    # Use a subshell to group commands and check the overall result.
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
bigfiles  - покажет размеры самых больших файлов
cls       - очистка от мусора
code      - обновление vscode, js, py, gemini-cli, qwen-cli
con1      - подключиться к удаленному серверу (root)
con2      - подключиться к удаленному серверу (user)
dns-xbox  - переключить DNS для Xbox
dns-restore - восстановить DNS
dns-status  - показать текущий DNS
fzf       - консольный поисковик
gca       - автокомит и пуш на репозиторий
lan       - показывает список IP в локальной сети
myip      - показывает текущий IP
ncdu      - показывает размеры директорий
nettest   - проверка пинга, опрос локальной сети, замер скорости интернета
nmon      - миниторинг сетевых процессов
obsid     - сохранение obsidian
pbcopy    - скопировать в буфер обмена
pbpaste   - вставить из буфера обмена
ranger    - консольный файловый менеджер
smon      - миниторинг процессов
stt       - консольный замер скорости
sysupg    - апгрейд всей системы
systart   - полный цикл: апгрейд, обновление инструментов, очистка
tldr      - упрощенный хелпер линукс
ztup      - включить zerotier
ztd       - выключить zerotier
ztls      - показать статус zerotier и список сетей
ztswitch  - сменить основную сеть: ztswitch <network_id>
ztstop    - принудительно остановить все службы ZeroTier
ztcleanup - удалить мертвые сети (ACCESS_DENIED/NOT_FOUND)
ztsw      - переключиться на другую ZT сеть (автоматически)
agy-reinstall - полная переустановка Antigravity
2agy      - запустить 2 экземпляра agy в разных окнах
agy1      - запустить agy под первым аккаунтом в текущем окне
agy2      - запустить agy под вторым аккаунтом в текущем окне
EOF
}



# Alias definitions.
# You may want to put all your additions into a separate file like
# ~/.bash_aliases, instead of adding them here directly.
# See /usr/share/doc/bash-doc/examples in the bash-doc package.



# Environment from previous .zshrc
[ -z "$TERM" ] && export TERM="xterm-256color"
export KWIN_TRIPLE_BUFFER="${KWIN_TRIPLE_BUFFER:-1}"
[ -z "$LC_ALL" ] && export LC_ALL="en_GB.UTF-8"

# Rust cargo binaries
export PATH="$HOME/.cargo/bin:$PATH"

# SDKMAN
export SDKMAN_DIR="$HOME/.sdkman"
[ -s "$SDKMAN_DIR/bin/sdkman-init.sh" ] && source "$SDKMAN_DIR/bin/sdkman-init.sh"

# Fancy system info on interactive zsh
if [ -n "$ZSH_VERSION" ] && [[ $- == *i* ]] && command -v neofetch >/dev/null 2>&1; then
  neofetch
fi

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm

















# Added by Antigravity CLI installer
export PATH="/home/asv-spb/.local/bin:$PATH"

# --- Antigravity Functions ---
agy-reinstall() {
    echo "Начинаем полное удаление Antigravity CLI и всех связанных данных о Gemini..."

    pkill -x agy 2>/dev/null
    pkill -x agentapi 2>/dev/null
    echo "Завершены активные процессы agy и agentapi"

    rm -f "$HOME/.local/bin/antigravity" "$HOME/.local/bin/agy"
    echo "Удалены файлы ~/.local/bin/antigravity и ~/.local/bin/agy"

    rm -rf "$HOME/Downloads/Antigravity"
    echo "Удалена директория ~/Downloads/Antigravity"

    rm -rf "$HOME/.gemini" "$HOME/.antigravity" "$HOME/.antigravitycli" "$HOME/.config/Antigravity" "$HOME/.local/share/antigravity-ide" "$HOME/.cache/antigravity"
    echo "Удалены директории конфигурации и кэша"

    echo "Очистка системной связки ключей (Keyring)..."
    if command -v python3 &>/dev/null; then
        python3 -c "
try:
    import secretstorage
    connection = secretstorage.dbus_init()
    collection = secretstorage.get_default_collection(connection)
    for item in list(collection.get_all_items()):
        attrs = item.get_attributes()
        if attrs.get('application') == 'Antigravity' or (attrs.get('service') == 'gemini' and attrs.get('username') == 'antigravity'):
            item.delete()
except Exception:
    pass
" 2>/dev/null
        echo "Связка ключей Keyring очищена."
    fi

    echo "Начинаем установку Antigravity CLI..."
    # Автоматическая установка keyrings.alt для работы PlaintextKeyring (чтобы проблема с хранилищем не повторилась)
    if ! pip3 show keyrings.alt >/dev/null 2>&1; then
        echo "Устанавливаем пакет keyrings.alt..."
        pip3 install --quiet --user keyrings.alt || true
    fi
    curl -fsSL https://antigravity.google/cli/install.sh | bash
    echo "Установка завершена! Перезапустите терминал или выполните source ~/.bashrc (или ~/.zshrc)"
}

_dbus_isolated_conf() {
    local fake_home="$1"
    local dbus_conf="$fake_home/.dbus-isolated.conf"
    if [ ! -f "$dbus_conf" ]; then
        cat > "$dbus_conf" << 'DBUSCONF'
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=/tmp</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
    <allow user="*"/>
  </policy>
  <policy context="mandatory">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
DBUSCONF
    fi
}

_setup_agy_home() {
    local fake_home="$1"
    mkdir -p "$fake_home"
    for item in .ssh .gitconfig .bashrc .zshrc .bash_profile .profile; do
        if [ -e "$HOME/$item" ] && [ ! -e "$fake_home/$item" ]; then
            ln -s "$HOME/$item" "$fake_home/$item"
        fi
    done

    mkdir -p "$fake_home/bin"
    local browser_wrapper="$fake_home/bin/xdg-open"
    if [ ! -e "$browser_wrapper" ]; then
        echo '#!/bin/bash' > "$browser_wrapper"
        echo 'export HOME="/home/asv-spb"' >> "$browser_wrapper"
        echo 'exec /usr/bin/xdg-open "$@"' >> "$browser_wrapper"
        chmod +x "$browser_wrapper"
    fi
    if [ ! -e "$fake_home/bin/google-chrome" ]; then
        ln -sf "$browser_wrapper" "$fake_home/bin/google-chrome"
    fi

    _dbus_isolated_conf "$fake_home"

    if ! HOME="$fake_home" pip3 show keyrings.alt >/dev/null 2>&1; then
        echo "Installing keyrings.alt for $fake_home..."
        HOME="$fake_home" pip3 install --quiet --user keyrings.alt || true
    fi
}

_start_isolated_dbus() {
    local conf="$1"
    local dbus_output
    dbus_output=$(dbus-daemon --config-file="$conf" --print-address=1 --print-pid=1 --fork 2>/dev/null)
    _ISOLATED_DBUS_ADDR=$(echo "$dbus_output" | head -1)
    _ISOLATED_DBUS_PID=$(echo "$dbus_output" | sed -n '2p')
    if [ -z "$_ISOLATED_DBUS_ADDR" ] || ! echo "$_ISOLATED_DBUS_ADDR" | grep -q '^unix:'; then
        echo "ERROR: Failed to start isolated dbus-daemon" >&2
        return 1
    fi
    return 0
}

_stop_isolated_dbus() {
    [ -n "$_ISOLATED_DBUS_PID" ] && kill "$_ISOLATED_DBUS_PID" 2>/dev/null
    _ISOLATED_DBUS_ADDR=""
    _ISOLATED_DBUS_PID=""
}

agy1() {
    local AGY_HOME="$HOME/.agy_account_1"
    _setup_agy_home "$AGY_HOME"
    export XDG_RUNTIME_DIR="$AGY_HOME/.runtime"
    mkdir -p "$XDG_RUNTIME_DIR"
    chmod 700 "$XDG_RUNTIME_DIR"

    if ! _start_isolated_dbus "$AGY_HOME/.dbus-isolated.conf"; then
        return 1
    fi

    HOME="$AGY_HOME" \
    PATH="$AGY_HOME/bin:$PATH:/home/asv-spb/.local/bin" \
    DBUS_SESSION_BUS_ADDRESS="$_ISOLATED_DBUS_ADDR" \
    command agy "$@"

    _stop_isolated_dbus
}

agy2() {
    local AGY_HOME="$HOME/.agy_account_2"
    _setup_agy_home "$AGY_HOME"
    export XDG_RUNTIME_DIR="$AGY_HOME/.runtime"
    mkdir -p "$XDG_RUNTIME_DIR"
    chmod 700 "$XDG_RUNTIME_DIR"

    if ! _start_isolated_dbus "$AGY_HOME/.dbus-isolated.conf"; then
        return 1
    fi

    HOME="$AGY_HOME" \
    PATH="$AGY_HOME/bin:$PATH:/home/asv-spb/.local/bin" \
    DBUS_SESSION_BUS_ADDRESS="$_ISOLATED_DBUS_ADDR" \
    command agy "$@"

    _stop_isolated_dbus
}

2agy() {
    local AGY_HOME_1="$HOME/.agy_account_1"
    local AGY_HOME_2="$HOME/.agy_account_2"

    _setup_agy_home "$AGY_HOME_1"
    _setup_agy_home "$AGY_HOME_2"

    echo "Starting isolated agy terminals..."

    gnome-terminal --window --title="AGY - Account 1" -- bash -c "
        echo '--- AGY Account 1 (isolated) ---'
        export HOME=\"$AGY_HOME_1\"
        export PATH=\"$AGY_HOME_1/bin:\$PATH:/home/asv-spb/.local/bin\"
        export XDG_RUNTIME_DIR=\"$AGY_HOME_1/.runtime\"
        mkdir -p \"\$XDG_RUNTIME_DIR\"
        chmod 700 \"\$XDG_RUNTIME_DIR\"
        _dbo=\$(dbus-daemon --config-file=\"$AGY_HOME_1/.dbus-isolated.conf\" --print-address=1 --print-pid=1 --fork 2>/dev/null)
        _dba=\$(echo \"\$_dbo\" | head -1)
        _dbp=\$(echo \"\$_dbo\" | sed -n '2p')
        if [ -n \"\$_dba\" ] && echo \"\$_dba\" | grep -q '^unix:'; then
            DBUS_SESSION_BUS_ADDRESS=\"\$_dba\" agy
            kill \"\$_dbp\" 2>/dev/null
        else
            echo 'ERROR: Failed to start isolated dbus-daemon'
        fi
        exec bash
    "

    gnome-terminal --window --title="AGY - Account 2" -- bash -c "
        echo '--- AGY Account 2 (isolated) ---'
        export HOME=\"$AGY_HOME_2\"
        export PATH=\"$AGY_HOME_2/bin:\$PATH:/home/asv-spb/.local/bin\"
        export XDG_RUNTIME_DIR=\"$AGY_HOME_2/.runtime\"
        mkdir -p \"\$XDG_RUNTIME_DIR\"
        chmod 700 \"\$XDG_RUNTIME_DIR\"
        _dbo=\$(dbus-daemon --config-file=\"$AGY_HOME_2/.dbus-isolated.conf\" --print-address=1 --print-pid=1 --fork 2>/dev/null)
        _dba=\$(echo \"\$_dbo\" | head -1)
        _dbp=\$(echo \"\$_dbo\" | sed -n '2p')
        if [ -n \"\$_dba\" ] && echo \"\$_dba\" | grep -q '^unix:'; then
            DBUS_SESSION_BUS_ADDRESS=\"\$_dba\" agy
            kill \"\$_dbp\" 2>/dev/null
        else
            echo 'ERROR: Failed to start isolated dbus-daemon'
        fi
        exec bash
    "

    echo "Done!"
}
# --- End Antigravity Functions ---
