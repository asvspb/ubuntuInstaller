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

# Starts ZeroTier and waits for the public IP to change.
ztup() {
    echo "Getting initial IP address..."
    local initial_ip
    initial_ip=$(curl -s https://ipinfo.io/ip)
    if [[ -z "$initial_ip" ]]; then
        echo "Error: Could not get the initial IP address." >&2
        return 1
    fi
    echo "Initial IP: $initial_ip"

    echo "Starting ZeroTier..."
    sudo systemctl start zerotier-one && sudo systemctl status zerotier-one

    echo -n "Waiting for IP address to change"
    for i in {1..15}; do
        local current_ip
        current_ip=$(curl -s https://ipinfo.io/ip)
        if [[ -n "$current_ip" && "$current_ip" != "$initial_ip" ]]; then
            echo -e "\n\nSuccess! IP address has changed."
            myip
            return 0
        fi
        echo -n "."
        sleep 2
    done

    echo -e "\n\nTimeout! IP address did not change within 30 seconds."
    echo "Current IP:"
    myip
    return 1
}

# Stops ZeroTier and waits for the public IP to revert.
ztd() {
    echo "Getting current IP address..."
    local initial_ip
    initial_ip=$(curl -s https://ipinfo.io/ip)
    if [[ -z "$initial_ip" ]]; then
        echo "Error: Could not get the initial IP address." >&2
        return 1
    fi
    echo "Current IP (before stopping): $initial_ip"

    echo "Stopping ZeroTier..."
    sudo systemctl stop zerotier-one && sudo systemctl disable zerotier-one.service && sudo systemctl status zerotier-one

    echo -n "Waiting for IP address to change"
    for i in {1..15}; do
        local current_ip
        current_ip=$(curl -s https://ipinfo.io/ip)
        if [[ -n "$current_ip" && "$current_ip" != "$initial_ip" ]]; then
            echo -e "\n\nSuccess! IP address has changed."
            myip
            return 0
        fi
        echo -n "."
        sleep 2
    done

    echo -e "\n\nTimeout! IP address did not change within 30 seconds."
    echo "Current IP:"
    myip
    return 1
}

# Defines a single function 'myhelp' to display a cheat sheet of custom commands.
# This avoids cluttering the terminal on startup and provides a clean, on-demand help menu.
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
EOF
}




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

