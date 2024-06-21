export TERM="xterm-256color"
export KWIN_TRIPLE_BUFFER=1
export LC_ALL=en_GB.UTF-8

POWERLEVEL9K_LEFT_PROMPT_ELEMENTS=(host user dir)
POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS=(status root_indicator vcs battery time)
POWERLEVEL9K_PROMPT_ON_NEWLINE=true
POWERLEVEL9K_KUBECONTEXT_SHOW_ON_COMMAND='kubectl|helm|kubens|kubectx|oc|istioctl|kogito'

# ZPlug

if [[ ! -d ~/.zplug ]];then
    git clone https://github.com/b4b4r07/zplug ~/.zplug
fi
source ~/.zplug/init.zsh

# Theme
zplug romkatv/powerlevel10k, as:theme

# Aliases
zplug "robbyrussell/oh-my-zsh", as:plugin, use:"lib/*.zsh"

# Plugins
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
zplug "zdharma/fast-syntax-highlighting" # Работает намного быстрее предыдущего плагина и подсвечивает лучше
zplug "zsh-users/zsh-completions"
zplug "zsh-users/zsh-history-substring-search"
zplug "MichaelAquilina/zsh-you-should-use" # Сообщает о том, что для команды существует алиас

zplug check || zplug install
zplug load

alias k=kubectl
alias ktx=kubectx
alias disablesleep="sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target"
alias enablesleep="sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target"
alias ls="ls --color"
alias l="lsd --date '+%d.%m.%Y %H:%M' -lah"
alias enhance='function ne() { docker run --rm -v "$(pwd)/`dirname ${@:$#}`":/ne/input -it alexjc/neural-enhance ${@:1:$#-1} "input/`basename ${@:$#}`"; }; ne'
alias logout="loginctl terminate-user toxblh"
alias vnc="x0vncserver -display :0 -PasswordFile ~/.vnc/passwd"
alias vnc-fire="fire-res && x0vncserver -display :0 -PasswordFile ~/.vnc/passwd"
alias vnc-mac="mac-res && x0vncserver -display :0 -PasswordFile ~/.vnc/passwd"
alias native-res="xrandr --output DP-2 --mode 3440x1440 --display :0"
alias fire-res="xrandr --output DP-2 --mode 1280x800 --display :0"
alias mac-res="xrandr --output DP-2 --mode 1680x1050 --display :0"
alias dcam="sudo usbmuxd;iproxy 4747 4747 &;droidcam-cli 127.0.0.1 4747"




#
# MY ALIASES
#
alias lan="echo -e '\n---------\nlan test\n---------' && echo 192.168.1.{1..254}|xargs -n1 -P0 ping -c1|grep 'bytes from'"
alias nettest="echo -e '\n---------\nping test\n---------' && ping -c 5 8.8.8.8 && lan && speedtest"
alias myip="curl -s https://ipinfo.io/json | jq -r '\"Current IP: \" + .ip, \
    \"City: \" + .city, \
    \"Region: \" + .region, \
    \"Country: \" + .country'"
alias fso="python3 /home/asv-spb/Documents/FSOget-data.py"
alias gca="git add . && git commit -m'Auto-commit' && git push"
alias cls="sudo apt autoremove -y && sudo apt clean && sudo journalctl --vacuum-time=2weeks"
alias cmu="cd ~/Dev/coronachess && make up"
alias cmd="cd ~/Dev/coronachess && make down"
alias cms="cd ~/Dev/coronachess && make start"
alias cmr="cd ~/Dev/coronachess && make restart"
alias cdr='sudo systemctl stop docker && cd ~/Dev/coronachess && make down && docker pull registry.gitlab.com/cidious/coronachess/app && sleep 5 && sudo reboot'
alias stt="speedtest"
alias smon="sudo btop"
NET_ADAPTER=$(ifconfig | grep -oE '^[^ ]+:' | grep -oE 'wl[^:]+')
alias nmon="sudo iftop -i $NET_ADAPTER"
alias lmstop='sudo pkill -9 llama && sudo pkill -9 llama-service && sync && sudo sysctl -w vm.drop_caches=3'
alias jsupd="bash /home/asv-spb/Dev/ubuntuInstaller/6_js-update.sh"
alias vsc="sudo apt update && sudo apt install --only-upgrade code"
alias sysupd="sudo apt update && sudo apt list --upgradable"
alias sysupg="sudo apt update && sudo apt upgrade"
alias obsid="cd ~/Dev/Obsidian-Vault/ && gca"

alias pbcopy='xclip -selection clipboard'
alias pbpaste='xclip -selection clipboard -o'
alias py="python3"

alias myhelp="echo 'lmstop - остановка ollama и очистка памяти'
echo 'lan - показывает список IP в локальной сети'
echo 'nettest - проверка пинга, опрос локальной сети, замер скорости интернета'
echo 'myip - показывает текущий IP'
echo 'fso - проверяет очередь ФСО'
echo 'cms - заново компилирует корону на локальном докере'
echo 'cdr - тормозит докер, обновляет реестр, перегружает'
echo 'smon - миниторинг процессов'
echo 'nmon - миниторинг сетевых процессов'
echo 'stt - консольный замер скорости'
echo 'fzf - консольный поисковик'
echo 'tldr - упрощенный хелпер линукс'
echo 'ranger - консольный файловый менеджер'
echo 'ncdu - показывает размеры директорий'
echo 'cls - очистка от мусора'
echo 'obsid - сохранение obsidian'
echo 'jsupd - обновление js'
echo 'vsc - обновление vscode'
echo 'sysupd - обновление репозиториев'
echo 'sysupg - апгрейд всей системы'
echo 'gca - автокомит  и пуш на репозиторий'"


#
# DNF
#



export PATH=~/.cargo/bin:$PATH

#THIS MUST BE AT THE END OF THE FILE FOR SDKMAN TO WORK!!!
export SDKMAN_DIR="/home/toxblh/.sdkman"
[[ -s "/home/toxblh/.sdkman/bin/sdkman-init.sh" ]] && source "/home/toxblh/.sdkman/bin/sdkman-init.sh"

neofetch
___MY_VMOPTIONS_SHELL_FILE="${HOME}/.jetbrains.vmoptions.sh"; if [ -f "${___MY_VMOPTIONS_SHELL_FILE}" ]; then . "${___MY_VMOPTIONS_SHELL_FILE}"; fi

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion

PATH=~/.console-ninja/.bin:$PATH

# Created by `pipx` on 2024-06-19 07:42:30
export PATH="$PATH:/home/asv-spb/.local/bin"
if [ -f "/home/asv-spb/.config/fabric/fabric-bootstrap.inc" ]; then . "/home/asv-spb/.config/fabric/fabric-bootstrap.inc"; fi

# Shell-GPT integration BASH v0.2
_sgpt_bash() {
if [[ -n "$READLINE_LINE" ]]; then
    READLINE_LINE=$(sgpt --shell <<< "$READLINE_LINE" --no-interaction)
    READLINE_POINT=${#READLINE_LINE}
fi
}
bind -x '"\C-l": _sgpt_bash'
# Shell-GPT integration BASH v0.2
