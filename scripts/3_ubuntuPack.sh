#! /bin/bash

set -e

echo " "
echo "Installing repositories"
echo "--------------------------------------------------------------"
sudo add-apt-repository ppa:thopiekar/openrgb -y
sudo add-apt-repository ppa:trebelnik-stefina/grub-customizer -y
sudo add-apt-repository ppa:ubuntuhandbook1/rhythmbox -y
sudo add-apt-repository -y ppa:deadsnakes/ppa -y #python

echo " "
echo "Installing keys"
echo "--------------------------------------------------------------"

#vscode
curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor | sudo tee /usr/share/keyrings/vscode.gpg >/dev/null
echo deb [arch=amd64 signed-by=/usr/share/keyrings/vscode.gpg] https://packages.microsoft.com/repos/vscode stable main | sudo tee /etc/apt/sources.list.d/vscode.list

sudo apt update -y
echo " "
echo "Installing programming environment"
echo "--------------------------------------------------------------"
# install js
# Проверяем, установлена ли уже подходящая версия Node.js через NVM
if [ -f "$HOME/.nvm/nvm.sh" ]; then
    NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"
    
    if command -v node &>/dev/null; then
        NODE_VERSION=$(node --version)
        if [[ $NODE_VERSION =~ ^v(2[0-9]|3[0-9]). ]]; then
            echo "Подходящая версия Node.js уже установлена: $NODE_VERSION"
        else
            echo "Установлена несовместимая версия Node.js: $NODE_VERSION"
            echo "Рекомендуется обновить до версии 20.x или выше"
        fi
    else
        echo "Node.js не найден, убедитесь, что NVM активирован правильно"
    fi
else
    # Если NVM не установлен, используем установку через официальный скрипт
    curl -qL https://www.npmjs.com/install.sh | sh
fi

# install latest python
sudo apt install python3 python3-pip -y
sudo pip3 install --upgrade pip --break-system-packages

# install vsc java
sudo apt install code gcc default-jdk -y
# install system packages
sudo apt install ncdu ranger btop iftop htop neofetch rpm wireguard jq guake copyq xclip pipx -y
sudo apt install inxi cpu-x tldr fzf rhythmbox vlc alacarte qbittorrent software-properties-common exa batcat fd-find ripgrep duf zoxide rclone -y
sudo apt install grub-customizer gparted synaptic openrgb ufw timeshift nala dconf-editor -y

echo " "
echo "Installing Warp Terminal"
echo "--------------------------------------------------------------"
wget https://app.warp.dev/download?package=deb -O warp.deb
sudo apt install ./warp.deb -y
rm warp.deb

echo " "
echo "Installing code CLI's"
echo "--------------------------------------------------------------"
# Активация NVM использование npm из NVM
if [ -f "$HOME/.nvm/nvm.sh" ]; then
    NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"
    
    if command -v npm &>/dev/null; then
        echo "Используем npm из NVM: $(npm --version)"
        npm install -g @google/gemini-cli@latest
        npm install -g @qwen-code/qwen-code@latest
        npm install -g codebuff
        npm install -g @github/copilot
    else
        # Если активация NVM не помогла, пробуем использовать npm напрямую из NVM
        NVM_NPM_PATH="$HOME/.nvm/versions/node/v24.6.0/bin/npm"
        if [ -f "$NVM_NPM_PATH" ]; then
            echo "Используем npm напрямую из NVM: $($NVM_NPM_PATH --version)"
            $NVM_NPM_PATH install -g @google/gemini-cli@latest
            $NVM_NPM_PATH install -g @qwen-code/qwen-code@latest
            $NVM_NPM_PATH install -g codebuff
            $NVM_NPM_PATH install -g @github/copilot
        else
            echo "WARN: npm не найден в NVM по пути $NVM_NPM_PATH"
        fi
    fi
else
    # Если NVM не установлен, пробуем использовать npm напрямую из NVM (на случай, если он был установлен в другом месте)
    NVM_NPM_PATH="$HOME/.nvm/versions/node/v24.6.0/bin/npm"
    if [ -f "$NVM_NPM_PATH" ]; then
        echo "Используем npm напрямую из NVM: $($NVM_NPM_PATH --version)"
        $NVM_NPM_PATH install -g @google/gemini-cli@latest
        $NVM_NPM_PATH install -g @qwen-code/qwen-code@latest
        $NVM_NPM_PATH install -g codebuff
        $NVM_NPM_PATH install -g @github/copilot
    else
        echo "WARN: npm не найден в системе (ни в NVM, ни напрямую)"
    fi
fi

echo " "
echo "Installing speedtest"
echo "--------------------------------------------------------------"
curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash
sudo apt-get install speedtest

echo " "
echo "Installing wireguard"
echo "--------------------------------------------------------------"
# Path to the WireGuard configuration file
wg_conf="/etc/wireguard/wg0.conf"
sudo touch "$wg_conf"

sudo chmod 0600 /etc/wireguard/wg0.conf
sudo systemctl start wg-quick@wg0.service
sudo ln -sf /usr/bin/resolvectl /usr/local/bin/resolvconf

echo "--------------------------------------------------------------"
echo "Installation completed successfully"
echo "--------------------------------------------------------------"
