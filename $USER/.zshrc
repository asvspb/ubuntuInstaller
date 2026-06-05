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

# ZeroTier functions (config, helpers, zt*, myhelp)
[ -f ~/.zt-functions.sh ] && source ~/.zt-functions.sh

# Zsh-optimized version of ztsw (uses zsh-specific ${(f)...} syntax)
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
