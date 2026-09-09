#!/bin/bash
# ==============================================================================
# 3_ubuntuPack.sh — Установка пакета разработчика, CLI-утилит и сред программирования
# ==============================================================================
set -e

C_RESET="\033[0m"
C_GREEN="\033[1;32m"
C_BLUE="\033[1;34m"
C_YELLOW="\033[1;33m"

info()    { echo -e "\n${C_BLUE}[ИНФО]${C_RESET} $*"; }
success() { echo -e "${C_GREEN}[УСПЕХ]${C_RESET} $*"; }
warn()    { echo -e "${C_YELLOW}[ВНИМАНИЕ]${C_RESET} $*"; }

sudo mkdir -p /etc/apt/keyrings

info "1. Подключение сторонних репозиториев (VS Code, Antigravity, ACLI, OpenRGB)"

# 1.1 VS Code
if [ ! -f /etc/apt/sources.list.d/vscode.list ]; then
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor --yes | sudo tee /etc/apt/keyrings/vscode.gpg >/dev/null
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/vscode.gpg] https://packages.microsoft.com/repos/vscode stable main" | sudo tee /etc/apt/sources.list.d/vscode.list >/dev/null
fi

# 1.2 Google Antigravity
if [ ! -f /etc/apt/sources.list.d/antigravity.list ]; then
    curl -fsSL https://us-central1-apt.pkg.dev/doc/repo-signing-key.gpg | sudo gpg --dearmor --yes -o /etc/apt/keyrings/antigravity-repo-key.gpg
    echo "deb [signed-by=/etc/apt/keyrings/antigravity-repo-key.gpg] https://us-central1-apt.pkg.dev/projects/antigravity-auto-updater-dev/ antigravity-debian main" | sudo tee /etc/apt/sources.list.d/antigravity.list >/dev/null
fi

# 1.3 Atlassian ACLI
if [ ! -f /etc/apt/sources.list.d/acli.list ]; then
    wget -nv -O- https://acli.atlassian.com/gpg/public-key.asc | sudo gpg --dearmor --yes -o /etc/apt/keyrings/acli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/acli-archive-keyring.gpg] https://acli.atlassian.com/linux/deb stable main" | sudo tee /etc/apt/sources.list.d/acli.list >/dev/null
fi

# 1.4 OpenRGB PPA
sudo add-apt-repository -y ppa:thopiekar/openrgb 2>/dev/null || true

info "2. Обновление пакетов и установка системных инструментов"
sudo apt update -y

# Базовые компиляторы и утилиты
sudo apt install -y build-essential gcc g++ cmake make default-jdk python3 python3-pip python3-venv pipx \
    code antigravity acli \
    btop iftop htop ncdu ranger duf zoxide rclone fzf ripgrep fd-find jq \
    wireguard ufw timeshift synaptic dconf-editor openrgb \
    vlc qbittorrent alacarte xclip copyq guake

# Установка современных fastfetch и eza
sudo apt install -y fastfetch 2>/dev/null || sudo apt install -y neofetch 2>/dev/null || true
sudo apt install -y eza 2>/dev/null || sudo apt install -y exa 2>/dev/null || true

# Установка bat (создание симлинка bat -> batcat, если требуется)
sudo apt install -y bat || true
mkdir -p ~/.local/bin
if command -v batcat &>/dev/null && ! command -v bat &>/dev/null; then
    ln -sf "$(which batcat)" ~/.local/bin/bat
    sudo ln -sf "$(which batcat)" /usr/local/bin/bat 2>/dev/null || true
fi

# Установка git-delta (красивые diff-ы для git)
if ! command -v delta &>/dev/null; then
    DELTA_DEB_URL=$(curl -s https://api.github.com/repos/dandavison/delta/releases/latest | grep "browser_download_url.*_amd64.deb" | head -n 1 | cut -d '"' -f 4)
    if [[ -n "$DELTA_DEB_URL" ]]; then
        wget -q "$DELTA_DEB_URL" -O /tmp/git-delta.deb && sudo dpkg -i /tmp/git-delta.deb && rm -f /tmp/git-delta.deb || true
    fi
fi

# Установка eza (современная замена ls)
if ! command -v eza &>/dev/null; then
    EZA_TAR_URL=$(curl -s https://api.github.com/repos/eza-community/eza/releases/latest | grep "browser_download_url.*x86_64-unknown-linux-gnu.tar.gz" | head -n 1 | cut -d '"' -f 4)
    if [[ -n "$EZA_TAR_URL" ]]; then
        wget -q "$EZA_TAR_URL" -O /tmp/eza.tar.gz && tar -xzf /tmp/eza.tar.gz -C /tmp && sudo install -m 755 /tmp/eza /usr/local/bin/eza && rm -f /tmp/eza* || true
    fi
fi

# Установка yazi (современный файловый менеджер)
if ! command -v yazi &>/dev/null; then
    YAZI_ZIP_URL=$(curl -s https://api.github.com/repos/sxyazi/yazi/releases/latest | grep "browser_download_url.*x86_64-unknown-linux-musl.zip" | head -n 1 | cut -d '"' -f 4)
    if [[ -n "$YAZI_ZIP_URL" ]]; then
        wget -q "$YAZI_ZIP_URL" -O /tmp/yazi.zip && unzip -q /tmp/yazi.zip -d /tmp && sudo install -m 755 /tmp/yazi-*/yazi /usr/local/bin/yazi && sudo install -m 755 /tmp/yazi-*/ya /usr/local/bin/ya && rm -rf /tmp/yazi* || true
    fi
fi

# Установка JetBrainsMono Nerd Font (для отображения иконок в терминале, Yazi, eza, p10k)
if ! fc-list : family | grep -q "JetBrainsMono Nerd Font"; then
    info "Установка шрифта JetBrainsMono Nerd Font..."
    mkdir -p /tmp/nerd-fonts
    curl -sL https://github.com/ryanoasis/nerd-fonts/releases/latest/download/JetBrainsMono.tar.xz -o /tmp/nerd-fonts/JetBrainsMono.tar.xz
    tar -xJf /tmp/nerd-fonts/JetBrainsMono.tar.xz -C /tmp/nerd-fonts
    sudo mkdir -p /usr/local/share/fonts/truetype/nerd-fonts
    sudo cp /tmp/nerd-fonts/*.ttf /usr/local/share/fonts/truetype/nerd-fonts/ 2>/dev/null || true
    sudo fc-cache -f /usr/local/share/fonts 2>/dev/null || true
    rm -rf /tmp/nerd-fonts
fi

info "3. Настройка окружения Node.js (через NVM)"
if [ ! -d "$HOME/.nvm" ]; then
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
fi
export NVM_DIR="$HOME/.nvm"
# shellcheck disable=SC1091
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

nvm install --lts || true
nvm use --lts || true
nvm alias default 'lts/*' || true

info "4. Установка глобальных AI CLI утилит через npm"
npm install -g @google/gemini-cli@latest cline@latest opencode-ai@latest 2>/dev/null || true

info "5. Настройка Python CLI (соответствие PEP 668 / pipx)"
pipx ensurepath || true
pipx install keyrings.alt 2>/dev/null || true

info "6. Установка Warp Terminal"
if ! command -v warp-terminal &>/dev/null; then
    wget -q https://app.warp.dev/download?package=deb -O /tmp/warp.deb
    sudo apt install -y /tmp/warp.deb || true
    rm -f /tmp/warp.deb
    success "Warp Terminal установлен!"
fi

info "6.1. Установка Ptyxis Terminal (новый терминал Ubuntu 26 / GNOME)"
if ! command -v flatpak &>/dev/null; then
    sudo apt install -y flatpak
fi
sudo flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo 2>/dev/null || true
sudo flatpak install -y flathub app.devsuite.Ptyxis 2>/dev/null || true

# Регистрация Ptyxis в качестве системного терминала по умолчанию (x-terminal-emulator)
sudo tee /usr/local/bin/ptyxis-terminal.wrapper > /dev/null << 'EOF'
#!/bin/bash
FLATPAK_PTYXIS="/var/lib/flatpak/exports/bin/app.devsuite.Ptyxis"
if [[ ! -x "$FLATPAK_PTYXIS" ]]; then
    FLATPAK_PTYXIS="flatpak run app.devsuite.Ptyxis"
fi
if [[ "$1" == "-e" ]]; then
    shift
    exec $FLATPAK_PTYXIS -- "$@"
elif [[ "$1" == "-x" ]]; then
    shift
    exec $FLATPAK_PTYXIS -x "$@"
else
    exec $FLATPAK_PTYXIS "$@"
fi
EOF
sudo chmod +x /usr/local/bin/ptyxis-terminal.wrapper

sudo tee /usr/local/bin/ptyxis > /dev/null << 'EOF'
#!/bin/bash
exec /var/lib/flatpak/exports/bin/app.devsuite.Ptyxis "$@"
EOF
sudo chmod +x /usr/local/bin/ptyxis

sudo update-alternatives --install /usr/bin/x-terminal-emulator x-terminal-emulator /usr/local/bin/ptyxis-terminal.wrapper 60 2>/dev/null || true
sudo update-alternatives --set x-terminal-emulator /usr/local/bin/ptyxis-terminal.wrapper 2>/dev/null || true
success "Ptyxis Terminal установлен и назначен терминалом по умолчанию!"

info "7. Установка OnlyOffice Desktop Editors"
if ! command -v onlyoffice-desktopeditors &>/dev/null; then
    wget -q https://download.onlyoffice.com/install/desktop/editors/linux/onlyoffice-desktopeditors_amd64.deb -O /tmp/onlyoffice.deb
    sudo apt install -y /tmp/onlyoffice.deb || true
    rm -f /tmp/onlyoffice.deb
    success "OnlyOffice Desktop установлен!"
fi

info "8. Установка Speedtest CLI"
if ! command -v speedtest &>/dev/null; then
    curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash
    sudo apt install -y speedtest || true
fi

echo
success "======================================================================="
success " Шаг 3 успешно завершен! Пакет разработчика полностью установлен."
success "======================================================================="
