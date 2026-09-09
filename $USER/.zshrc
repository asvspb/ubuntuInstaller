# ==============================================================================
# Modernized .zshrc (Antidote + fzf-tab + High-Speed Native Zsh Architecture)
# ==============================================================================

# Неинтерактивный режим — мгновенный выход
[[ $- != *i* ]] && return

# Гарантированная поддержка UTF-8 (для корректного отображения иконок и кириллицы)
export LANG="en_US.UTF-8"
export LC_ALL="en_US.UTF-8"
export LC_CTYPE="en_US.UTF-8"

# ------------------------------------------------------------------------------
# 1. Быстрая нативная история
# ------------------------------------------------------------------------------
HISTFILE="$HOME/.zsh_history"
HISTSIZE=50000
SAVEHIST=50000
setopt EXTENDED_HISTORY          # Запись таймстемпов выполнения
setopt SHARE_HISTORY             # Синхронизация истории между всеми окнами/вкладками
setopt HIST_EXPIRE_DUPS_FIRST    # Первыми вытеснять дубликаты при заполнении
setopt HIST_IGNORE_DUPS          # Не дублировать подряд идущие одинаковые команды
setopt HIST_IGNORE_SPACE         # Игнорировать команды с пробелом в начале (секреты)
setopt HIST_VERIFY               # Предпросмотр команды из истории перед запуском
setopt INC_APPEND_HISTORY        # Добавлять команду в файл сразу после выполнения

# ------------------------------------------------------------------------------
# 2. Навигация и удобство каталогов
# ------------------------------------------------------------------------------
setopt AUTO_CD                   # Переход в папку просто по имени (без cd)
setopt AUTO_PUSHD                # Автостек директорий (cd -<TAB>)
setopt PUSHD_IGNORE_DUPS         # Без повторов в стеке
setopt PROMPT_SUBST              # Подстановка переменных в промпт

# ------------------------------------------------------------------------------
# 3. Горячие клавиши (Home, End, Delete, стрелки)
# ------------------------------------------------------------------------------
bindkey -e                       # Режим emacs
bindkey '^[[H' beginning-of-line
bindkey '^[[F' end-of-line
bindkey '^[[3~' delete-char
bindkey '^[[1;5C' forward-word
bindkey '^[[1;5D' backward-word

# ------------------------------------------------------------------------------
# 4. Оптимизированная инициализация автодополнения (кэшируется, старт ~5мс)
# ------------------------------------------------------------------------------
autoload -Uz compinit
if [[ -n ${ZDOTDIR:-$HOME}/.zcompdump(#qN.mh+24) ]]; then
  compinit
else
  compinit -C
fi

zstyle ':completion:*' matcher-list 'm:{a-zA-Z}={A-Za-z}' # Регистронезависимый поиск
zstyle ':completion:*' menu select                       # Управление стрелками в меню
zstyle ':completion:*' list-colors "${(s.:.)LS_COLORS}"   # Цвета файлов

# ------------------------------------------------------------------------------
# 5. Менеджер плагинов Antidote (статическая компиляция плагинов)
# ------------------------------------------------------------------------------
ANTIDOTE_DIR="${ZDOTDIR:-$HOME}/.antidote"
if [[ ! -d "$ANTIDOTE_DIR" ]]; then
  echo "Клонирование Antidote (быстрый менеджер плагинов)..."
  git clone --depth=1 https://github.com/mattmc3/antidote.git "$ANTIDOTE_DIR"
fi

source "$ANTIDOTE_DIR/antidote.zsh"

ZPLUGINS_TXT="${ZDOTDIR:-$HOME}/.zsh_plugins.txt"
ZPLUGINS_ZSH="${ZDOTDIR:-$HOME}/.zsh_plugins.zsh"

# Перекомпиляция только при изменении .zsh_plugins.txt
if [[ ! -f "$ZPLUGINS_ZSH" || "$ZPLUGINS_TXT" -nt "$ZPLUGINS_ZSH" ]]; then
  antidote bundle < "$ZPLUGINS_TXT" > "$ZPLUGINS_ZSH"
fi

# Мгновенная загрузка всех плагинов одним source (без подпроцессов и задержек)
source "$ZPLUGINS_ZSH"

# ------------------------------------------------------------------------------
# 6. Интерактивный UI с предпросмотром (fzf-tab)
# ------------------------------------------------------------------------------
# Превью для cd (через eza или ls)
zstyle ':fzf-tab:complete:cd:*' fzf-preview 'eza -1 --color=always $realpath 2>/dev/null || ls -1 --color=always $realpath'
# Превью содержимого файлов (через bat или head)
zstyle ':fzf-tab:complete:*:*' fzf-preview 'bat --color=always --line-range :50 $realpath 2>/dev/null || head -n 50 $realpath'
# Переключение групп клавишами , и .
zstyle ':fzf-tab:*' switch-group ',' '.'
# Цветовая гамма под Royal blue
zstyle ':fzf-tab:*' fzf-flags --color=bg+:#002851,hl+:#99ffff,pointer:#ff9ca3,info:#cdab8f

# Привязка history-substring-search к стрелкам вверх/вниз
bindkey '^[[A' history-substring-search-up
bindkey '^[[B' history-substring-search-down

# ------------------------------------------------------------------------------
# 7. Тема / Промпт
# ------------------------------------------------------------------------------
if command -v starship &>/dev/null; then
  eval "$(starship init zsh)"
else
  # Настройки Powerlevel10k
  POWERLEVEL9K_LEFT_PROMPT_ELEMENTS=(host user dir)
  POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS=(status root_indicator vcs battery time)
  POWERLEVEL9K_PROMPT_ON_NEWLINE=true
  POWERLEVEL9K_KUBECONTEXT_SHOW_ON_COMMAND='kubectl|helm|kubens|kubectx|oc|istioctl|kogito'
fi


# ------------------------------------------------------------------------------
# 8. Семантическая интеграция с эмулятором терминала (Ptyxis / GNOME / VTE)
# ------------------------------------------------------------------------------
if [[ -n "$VTE_VERSION" || -n "$PTYXIS_VERSION" || "$TERM_PROGRAM" == "Ptyxis" ]]; then
  # OSC 7: Синхронизация каталогов (открытие новых вкладок в том же пути)
  __vte_osc7() {
    local url_path=""
    if [[ -x /usr/libexec/vte-urlencode-cwd ]]; then
      url_path="$(/usr/libexec/vte-urlencode-cwd 2>/dev/null)"
    else
      url_path="${PWD}"
    fi
    printf '\e]7;file://%s%s\e\\' "${HOSTNAME:-localhost}" "${url_path}"
  }

  # OSC 133: Семантическая разметка (FinalTerm / Ptyxis маркеры и уведомления)
  __ptyxis_osc133_preexec() {
    printf '\e]133;C\a'
  }
  __ptyxis_osc133_precmd() {
    local exit_status=$?
    printf '\e]133;D;%s\a' "$exit_status"
    printf '\e]133;A\a'
  }

  autoload -Uz add-zsh-hook
  add-zsh-hook precmd __vte_osc7
  add-zsh-hook preexec __ptyxis_osc133_preexec
  add-zsh-hook precmd __ptyxis_osc133_precmd
fi

# ------------------------------------------------------------------------------
# 9. Современный CLI-инструментарий (zoxide, eza, bat, yazi)
# ------------------------------------------------------------------------------
# Zoxide (умный переход по каталогам `z`)
if command -v zoxide &>/dev/null; then
  eval "$(zoxide init zsh)"
fi

# Eza (современная замена ls с git-статусом и иконками)
if command -v eza &>/dev/null; then
  alias ls='eza --group-directories-first'
  alias ll='eza -lh --group-directories-first --git'
  alias la='eza -a --group-directories-first'
  alias lla='eza -lah --group-directories-first --git'
  alias lt='eza --tree --level=2'
else
  # enable color support of ls and also add handy aliases
  if [ -x /usr/bin/dircolors ]; then
      test -r ~/.dircolors && eval "$(dircolors -b ~/.dircolors)" || eval "$(dircolors -b)"
      alias ls='ls --color=auto'
      alias ll='ls -alF'
      alias la='ls -A'
      alias l='ls -CF'
  fi
fi

if [ -x /usr/bin/dircolors ]; then
    alias grep='grep --color=auto'
    alias fgrep='fgrep --color=auto'
    alias egrep='egrep --color=auto'
fi

# Bat (быстрый просмотр файлов с подсветкой)
if command -v bat &>/dev/null; then
  alias b='bat'
fi

# Yazi (файловый менеджер + переход в каталог по выходу)
if command -v yazi &>/dev/null; then
  function yy() {
    local tmp="$(mktemp -t "yazi-cwd.XXXXXX")" cwd
    yazi "$@" --cwd-file="$tmp"
    if cwd="$(command cat -- "$tmp")" && [ -n "$cwd" ] && [ "$cwd" != "$PWD" ]; then
      builtin cd -- "$cwd"
    fi
    rm -f -- "$tmp"
  }
fi

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

_check_vpn_active() {
    local zt_active=0
    local wg_active=0

    # Проверка ZeroTier (по статусу CLI или активным интерфейсам zt*)
    if sudo -n zerotier-cli status 2>/dev/null | grep -q "ONLINE" || ip -br link show 2>/dev/null | grep -qE "^zt.*(UP|UNKNOWN)"; then
        zt_active=1
    fi

    # Проверка WireGuard (по wg show или активным интерфейсам wg*)
    if sudo -n wg show 2>/dev/null | grep -q "interface" || ip -br link show 2>/dev/null | grep -qE "^wg.*(UP|UNKNOWN)"; then
        wg_active=1
    fi

    if [ "$zt_active" -eq 0 ] && [ "$wg_active" -eq 0 ]; then
        echo -e "\033[1;31m[БЛОКИРОВКА БЕЗОПАСНОСТИ]\033[0m ZeroTier и WireGuard ДЕАКТИВИРОВАНЫ!" >&2
        echo -e "Запуск Antigravity отменен для предотвращения утечки прямого IP." >&2
        echo -e "Для запуска включите ZeroTier (команда: \033[1;32mztup\033[0m) или WireGuard." >&2
        return 1
    fi

    return 0
}


agy1() {
    if ! _check_vpn_active; then return 1; fi
    set-title "GOLDEN project"
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
    if ! _check_vpn_active; then return 1; fi
    set-title "SILVER project"
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
    if ! _check_vpn_active; then return 1; fi
    echo "Starting isolated agy terminals..."
    gnome-terminal --window --title="GOLDEN project" -- zsh -ic "echo '--- AGY Account 1 (isolated) ---'; agy1; exec zsh"
    gnome-terminal --window --title="SILVER project" -- zsh -ic "echo '--- AGY Account 2 (isolated) ---'; agy2; exec zsh"
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

