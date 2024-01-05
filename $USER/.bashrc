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
alias ghA="gh auth login"
alias ghX="gh repo create"
alias vu="sudo wg-quick up wg0 && ip"
alias vd="sudo wg-quick down wg0 && ip"
alias chU="sudo mount.cifs -o rw,username=ubuntu,password=123,uid=$USER '\\\\192.168.1.160\\homes' /media/coronachess && echo 'coronachess is mounted'"
alias chD="sudo umount /media/coronachess && echo 'coronachess disabled'"
alias ip="curl -s https://ipinfo.io/json | jq -r '\"Current IP: \" + .ip, \
    \"City: \" + .city, \
    \"Region: \" + .region, \
    \"Country: \" + .country'"
alias upd="python3 /home/asv-spb/Documents/FSOget-data.py"
alias svm="/home/asv-spb/Documents/startVMcorona.sh"
alias gca="git add . && git commit -m'Auto-commit' && git push"
alias cls="sudo apt autoremove -y && sudo apt clean && sudo journalctl --vacuum-time=2weeks"
alias cmu="cd ~/Dev/coronachess && make up"
alias cmd="cd ~/Dev/coronachess && make down"
alias cms="cd ~/Dev/coronachess && make start"
alias cmr="cd ~/Dev/coronachess && make restart"
alias stt="speedtest"
alias smon="sudo btop"
alias nmon="sudo iftop -i wlp34s0"
alias myhelp="echo 'lan - показывает список IP в локальной сети'
echo 'nettest - проверка пинга, опрос локальной сети, замер скорости интернета'
echo 'vu - включение vpn'
echo 'vd - выключение vpn'
echo 'chU - примонтирование диска короначез'
echo 'chD - отмонтирование диска короначез'
echo 'ip - показывает текущий IP'
echo 'upd - проверяет очередь ФСО'
echo 'svm - запускает виртуалку короны на вируталке и конектится с ней'
echo 'cmu - запускает виртуалку короны на локальном докере'
echo 'cmd - торомзит виртуалку короны на локальном докере'
echo 'cmr - торомзит и запускает виртуалку короны на локальном докере'
echo 'cms - заново компилирует корону на локальном докере'
echo 'smon - миниторинг процессов'
echo 'nmon - миниторинг сетевых процессов'
echo 'stt - консольный замер скорости'
echo 'fzf - консольный поисковик'
echo 'tldr - упрощенный хелпер линукс'
echo 'cls - очистка от мусора'
echo 'gca - автокомит  и пуш на репозиторий'
echo 'ghA - авторизация на github'
echo 'ghX - создание ветки'"

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
