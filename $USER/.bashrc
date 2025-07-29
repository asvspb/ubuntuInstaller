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
shopt -s histappend

# for setting history length see HISTSIZE and HISTFILESIZE in bash(1)
HISTSIZE=1000
HISTFILESIZE=2000

# check the window size after each command and, if necessary,
# update the values of LINES and COLUMNS.
shopt -s checkwinsize

# If set, the pattern "**" used in a pathname expansion context will
# match all files and zero or more directories and subdirectories.
#shopt -s globstar

# make less more friendly for non-text input files, see lesspipe(1)
[ -x /usr/bin/lesspipe ] && eval "$(SHELL=/bin/sh lesspipe)"

# set variable identifying the chroot you work in (used in the prompt below)
if [ -z "${debian_chroot:-}" ] && [ -r /etc/debian_chroot ]; then
    debian_chroot=$(cat /etc/debian_chroot)
fi

# set a fancy prompt (non-color, unless we know we "want" color)
case "$TERM" in
    xterm-color|*-256color) color_prompt=yes;;
esac

# uncomment for a colored prompt, if the terminal has the capability; turned
# off by default to not distract the user: the focus in a terminal window
# should be on the output of commands, not on the prompt
#force_color_prompt=yes

if [ -n "$force_color_prompt" ]; then
    if [ -x /usr/bin/tput ] && tput setaf 1 >&/dev/null; then
	# We have color support; assume it's compliant with Ecma-48
	# (ISO/IEC-6429). (Lack of such support is extremely rare, and such
	# a case would tend to support setf rather than setaf.)
	color_prompt=yes
    else
	color_prompt=
    fi
fi

if [ "$color_prompt" = yes ]; then
    PS1='${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '
else
    PS1='${debian_chroot:+($debian_chroot)}\u@\h:\w\$ '
fi
unset color_prompt force_color_prompt

# If this is an xterm set the title to user@host:dir
case "$TERM" in
xterm*|rxvt*)
    PS1="\[\e]0;${debian_chroot:+($debian_chroot)}\u@\h: \w\a\]$PS1"
    ;;
*)
    ;;
esac

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
alias cls="sudo apt autoremove -y && sudo apt clean && sudo journalctl --vacuum-time=2weeks"
alias stt="speedtest"
alias smon="sudo btop"
NET_ADAPTER=$(ifconfig | grep -oE '^[^ ]+:' | grep -oE 'wl[^:]+')
alias nmon="sudo iftop -i $NET_ADAPTER"
alias jsup="bash $HOME/Dev/ubuntuInstaller/5_js-update.sh"
alias pyup="bash $HOME/Dev/ubuntuInstaller/4_py-update.sh"
alias vsc="sudo apt update && sudo apt install --only-upgrade code"
alias sysupg="sudo apt update && sudo apt upgrade"
alias obsid="cd ~/Dev/Obsidian-Vault/ && gca"
alias bigfiles="sudo du -ah --max-depth=1 | sort -rh"
alias pbcopy='xclip -selection clipboard'
alias pbpaste='xclip -selection clipboard -o'
alias zts='sudo systemctl status zerotier-one'

# --- Configuration ---
# Timeout in seconds to wait for the IP to change.
readonly _ZT_TIMEOUT_SECONDS=30
# Interval in seconds between IP checks.
readonly _ZT_POLL_INTERVAL_SECONDS=2
# URL to check for the public IP address.
readonly _ZT_IP_CHECK_URL="https://ipinfo.io/ip"
# --- End Configuration ---

# Private helper function to wait for the public IP address to change.
# This function is not intended to be called directly by the user.
#
# Usage: _zt_wait_for_ip_change <initial_ip>
_zt_wait_for_ip_change() {
    local initial_ip="$1"
    local retries=$((_ZT_TIMEOUT_SECONDS / _ZT_POLL_INTERVAL_SECONDS))

    printf "Waiting for IP address to change"
    for ((i = 0; i < retries; i++)); do
        # Fetch current IP, with a 5-second timeout for the request.
        local current_ip
        current_ip=$(curl --silent --max-time 5 "$_ZT_IP_CHECK_URL")

        if [[ -n "$current_ip" && "$current_ip" != "$initial_ip" ]]; then
            printf "\n\nSuccess! IP address has changed.\n"
            myip
            return 0
        fi
        printf "."
        sleep "$_ZT_POLL_INTERVAL_SECONDS"
    done

    printf "\n\nTimeout! IP address did not change within %d seconds.\n" "$_ZT_TIMEOUT_SECONDS"
    printf "Current IP:\n"
    myip
    return 1
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
lan       - показывает список IP в локальной сети
nettest   - проверка пинга, опрос локальной сети, замер скорости интернета
myip      - показывает текущий IP
smon      - миниторинг процессов
nmon      - миниторинг сетевых процессов
stt       - консольный замер скорости
fzf       - консольный поисковик
tldr      - упрощенный хелпер линукс
ranger    - консольный файловый менеджер
ncdu      - показывает размеры директорий
cls       - очистка от мусора
obsid     - сохранение obsidian
jsup      - обновление js
pyup      - обновление python
vsc       - обновление vscode
sysupg    - апгрейд всей системы
bigfiles  - покажет размеры самых больших фалов
gca       - автокомит и пуш на репозиторий
pbcopy    - скопировать в буфер обмена
pbpaste   - вставить из буфера обмена
ztup      - включить zerotier
ztd       - выключить zerotier
zts       - показать статус zerotier
EOF
}



# Alias definitions.
# You may want to put all your additions into a separate file like
# ~/.bash_aliases, instead of adding them here directly.
# See /usr/share/doc/bash-doc/examples in the bash-doc package.

if [ -f ~/.bash_aliases ]; then
    . ~/.bash_aliases
fi

# enable programmable completion features (you don't need to enable
# this, if it's already enabled in /etc/bash.bashrc and /etc/profile
# sources /etc/bash.bashrc).
if ! shopt -oq posix; then
  if [ -f /usr/share/bash-completion/bash_completion ]; then
    . /usr/share/bash-completion/bash_completion
  elif [ -f /etc/bash_completion ]; then
    . /etc/bash_completion
  fi
fi

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion
