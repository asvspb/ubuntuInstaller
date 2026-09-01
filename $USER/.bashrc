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
  # autoload -Uz compinit && compinit # Initialized by zplug
  # Prompt: green user@host, blue cwd
  if [ -n "$debian_chroot" ]; then
    PROMPT="($debian_chroot)%F{green}%n@%m%f:%F{blue}%~%f$ "
  else
    PROMPT="%F{green}%n@%m%f:%F{blue}%~%f$ "
  fi
  # Terminal title: user@host: dir
  precmd() { print -Pn "\e]0;%n@%m: %~\a"; }

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
    zplug "plugins/ubuntu",            from:oh-my-zsh
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
    zplug "zdharma-continuum/fast-syntax-highlighting"
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

# --- ZeroTier management functions (sourced from ~/.zt-functions.sh) ---
[ -f "$HOME/.zt-functions.sh" ] && source "$HOME/.zt-functions.sh"


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
zts            - показать текущий IP и статус ZeroTier
ztup           - включить zerotier
ztd            - выключить zerotier
ztls           - показать статус zerotier и список сетей (включая VDS-сети)
ztswitch       - сменить основную сеть: ztswitch <network_id>
ztstop         - принудительно остановить все службы ZeroTier
ztcleanup      - удалить мертвые сети (ACCESS_DENIED/NOT_FOUND)
ztsw           - переключиться на другую ZT сеть (автоматически)
zt_vds_networks - показать сети с VDS-сервера (~/.zt-vds-server)
ztjoin_vds      - интерактивное подключение к сети VDS-сервера

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
  <!-- NO <standard_session_servicedirs/> — ключевой момент изоляции -->
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
    set-title "GOLDEN project"
    local AGY_HOME="$HOME/.agy_account_1"
    _setup_agy_home "$AGY_HOME"
    local _runtime="$AGY_HOME/.runtime"
    mkdir -p "$_runtime"
    chmod 700 "$_runtime"

    if ! _start_isolated_dbus "$AGY_HOME/.dbus-isolated.conf"; then
        return 1
    fi

    HOME="$AGY_HOME" \
    PATH="$AGY_HOME/bin:$PATH:/home/asv-spb/.local/bin" \
    XDG_RUNTIME_DIR="$_runtime" \
    DBUS_SESSION_BUS_ADDRESS="$_ISOLATED_DBUS_ADDR" \
    command agy "$@"

    _stop_isolated_dbus
}

agy2() {
    set-title "SILVER project"
    local AGY_HOME="$HOME/.agy_account_2"
    _setup_agy_home "$AGY_HOME"
    local _runtime="$AGY_HOME/.runtime"
    mkdir -p "$_runtime"
    chmod 700 "$_runtime"

    if ! _start_isolated_dbus "$AGY_HOME/.dbus-isolated.conf"; then
        return 1
    fi

    HOME="$AGY_HOME" \
    PATH="$AGY_HOME/bin:$PATH:/home/asv-spb/.local/bin" \
    XDG_RUNTIME_DIR="$_runtime" \
    DBUS_SESSION_BUS_ADDRESS="$_ISOLATED_DBUS_ADDR" \
    command agy "$@"

    _stop_isolated_dbus
}

2agy() {
    echo "Starting isolated agy terminals..."
    gnome-terminal --window --title="GOLDEN project" -- bash -ic "echo '--- AGY Account 1 (isolated) ---'; agy1; exec bash"
    gnome-terminal --window --title="SILVER project" -- bash -ic "echo '--- AGY Account 2 (isolated) ---'; agy2; exec bash"
    echo "Done!"
}
# --- OpenCode Isolated Functions ---
oc1() {
    set-title "OpenCode - Account 1"
    local OC_HOME="$HOME/.oc_account_1"
    _setup_agy_home "$OC_HOME"
    export XDG_RUNTIME_DIR="$OC_HOME/.runtime"
    mkdir -p "$XDG_RUNTIME_DIR"
    chmod 700 "$XDG_RUNTIME_DIR"
    if ! _start_isolated_dbus "$OC_HOME/.dbus-isolated.conf"; then return 1; fi

    HOME="$OC_HOME" \
    PATH="$OC_HOME/bin:$PATH:/home/asv-spb/.local/bin" \
    DBUS_SESSION_BUS_ADDRESS="$_ISOLATED_DBUS_ADDR" \
    command /home/asv-spb/.opencode/bin/opencode "$@"

    _stop_isolated_dbus
}

oc2() {
    set-title "OpenCode - Account 2"
    local OC_HOME="$HOME/.oc_account_2"
    _setup_agy_home "$OC_HOME"
    export XDG_RUNTIME_DIR="$OC_HOME/.runtime"
    mkdir -p "$XDG_RUNTIME_DIR"
    chmod 700 "$XDG_RUNTIME_DIR"
    if ! _start_isolated_dbus "$OC_HOME/.dbus-isolated.conf"; then return 1; fi

    HOME="$OC_HOME" \
    PATH="$OC_HOME/bin:$PATH:/home/asv-spb/.local/bin" \
    DBUS_SESSION_BUS_ADDRESS="$_ISOLATED_DBUS_ADDR" \
    command /home/asv-spb/.opencode/bin/opencode "$@"

    _stop_isolated_dbus
}

2oc() {
    echo "Starting isolated OpenCode terminals..."
    local shell_cmd="bash"
    [ -n "$ZSH_VERSION" ] && shell_cmd="zsh"
    gnome-terminal --window --title="OpenCode - Account 1" -- $shell_cmd -ic "echo '--- OpenCode Account 1 (isolated) ---'; oc1; exec $shell_cmd"
    gnome-terminal --window --title="OpenCode - Account 2" -- $shell_cmd -ic "echo '--- OpenCode Account 2 (isolated) ---'; oc2; exec $shell_cmd"
    echo "Done!"
}
# --- End OpenCode Functions ---

# --- Cline Isolated Functions ---
cl1() {
    set-title "Cline - Account 1"
    local CL_HOME="$HOME/.cl_account_1"
    _setup_agy_home "$CL_HOME"
    export XDG_RUNTIME_DIR="$CL_HOME/.runtime"
    mkdir -p "$CL_HOME/.runtime"
    chmod 700 "$CL_HOME/.runtime"
    if ! _start_isolated_dbus "$CL_HOME/.dbus-isolated.conf"; then return 1; fi

    HOME="$CL_HOME" \
    PATH="$CL_HOME/bin:$PATH:/home/asv-spb/.local/bin" \
    DBUS_SESSION_BUS_ADDRESS="$_ISOLATED_DBUS_ADDR" \
    command cline "$@"

    _stop_isolated_dbus
}

cl2() {
    set-title "Cline - Account 2"
    local CL_HOME="$HOME/.cl_account_2"
    _setup_agy_home "$CL_HOME"
    export XDG_RUNTIME_DIR="$CL_HOME/.runtime"
    mkdir -p "$CL_HOME/.runtime"
    chmod 700 "$CL_HOME/.runtime"
    if ! _start_isolated_dbus "$CL_HOME/.dbus-isolated.conf"; then return 1; fi

    HOME="$CL_HOME" \
    PATH="$CL_HOME/bin:$PATH:/home/asv-spb/.local/bin" \
    DBUS_SESSION_BUS_ADDRESS="$_ISOLATED_DBUS_ADDR" \
    command cline "$@"

    _stop_isolated_dbus
}

2cl() {
    echo "Starting isolated Cline terminals..."
    local shell_cmd="bash"
    [ -n "$ZSH_VERSION" ] && shell_cmd="zsh"
    gnome-terminal --window --title="Cline - Account 1" -- $shell_cmd -ic "echo '--- Cline Account 1 (isolated) ---'; cl1; exec $shell_cmd"
    gnome-terminal --window --title="Cline - Account 2" -- $shell_cmd -ic "echo '--- Cline Account 2 (isolated) ---'; cl2; exec $shell_cmd"
    echo "Done!"
}
# --- End Cline Functions ---

# --- End Antigravity Functions ---

# --- Terminal Title Function ---
# Automatically added to rename terminal tabs
set-title() {
    printf "\033]0;%s\007" "$1"
    if [ -n "$BASH_VERSION" ]; then
        export PS1="\[\e]0;$1\a\]\u@\h:\w\$ "
    elif [ -n "$ZSH_VERSION" ]; then
        export PROMPT="%{\e]0;$1\a%}%n@%m:%~%# "
    fi
}
# -------------------------------


# kimi-code
export PATH="/home/asv-spb/.kimi-code/bin:$PATH"

# opencode
export PATH=/home/asv-spb/.oc_account_1/.opencode/bin:$PATH
