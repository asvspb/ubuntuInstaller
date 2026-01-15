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

# --- Configuration ---
# Timeout in seconds to wait for the IP to change.
if [ -z "${_ZT_TIMEOUT_SECONDS+x}" ]; then
    readonly _ZT_TIMEOUT_SECONDS=30
fi
# Interval in seconds between IP checks.
if [ -z "${_ZT_POLL_INTERVAL_SECONDS+x}" ]; then
    readonly _ZT_POLL_INTERVAL_SECONDS=2
fi
# URL to check for the public IP address.
if [ -z "${_ZT_IP_CHECK_URL+x}" ]; then
    readonly _ZT_IP_CHECK_URL="https://ipinfo.io/ip"
fi
# --- End Configuration ---

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

# Starts ZeroTier and waits for the public IP to change.
ztup() {
    echo "Getting initial IP address..."
    local initial_ip
    initial_ip=$(curl --silent --max-time 5 "$_ZT_IP_CHECK_URL")
    if [[ -z "$initial_ip" ]]; then
        echo "Error: Could not get the initial IP address from $_ZT_IP_CHECK_URL." >&2
        return 1
    fi
    echo "Initial IP: $initial_ip"

    echo "Starting ZeroTier..."
    if ! sudo systemctl start zerotier-one; then
        echo "Error: Failed to start ZeroTier service." >&2
        sudo systemctl status zerotier-one >&2
        return 1
    fi
    sudo systemctl status zerotier-one

    _zt_wait_for_ip_change "$initial_ip"
}

# Stops ZeroTier and waits for the public IP to revert.
ztd() {
    echo "Getting current IP address..."
    local initial_ip
    initial_ip=$(curl --silent --max-time 5 "$_ZT_IP_CHECK_URL")
    if [[ -z "$initial_ip" ]]; then
        echo "Error: Could not get the initial IP address from $_ZT_IP_CHECK_URL." >&2
        return 1
    fi
    echo "Current IP (before stopping): $initial_ip"

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
con1      - подключиться к удаленному серверу
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
tldr      - упрощенный хелпер линукс
ztup      - включить zerotier
ztd       - выключить zerotier
zts       - показать статус zerotier
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
