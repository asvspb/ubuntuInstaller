#!/bin/bash
# ==============================================================================
# 1_ubuntuStart.sh — Базовая настройка системы, sudo, часов и основных утилит
# ==============================================================================
set -e

C_RESET="\033[0m"
C_GREEN="\033[1;32m"
C_BLUE="\033[1;34m"

info()    { echo -e "\n${C_BLUE}[ИНФО]${C_RESET} $*"; }
success() { echo -e "${C_GREEN}[УСПЕХ]${C_RESET} $*"; }

TARGET_USER="${SUDO_USER:-$USER}"

info "1. Настройка прав sudo (без запроса пароля)"
echo "${TARGET_USER} ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/90-nopasswd >/dev/null
sudo chmod 0440 /etc/sudoers.d/90-nopasswd
success "Sudo без пароля настроен для ${TARGET_USER}"

info "2. Отключение интерактивных пауз при обновлениях (needrestart & debconf)"
export DEBIAN_FRONTEND=noninteractive
sudo mkdir -p /etc/needrestart
cat << 'NR_EOF' | sudo tee /etc/needrestart/needrestart.conf >/dev/null
$nrconf{restart} = 'a';
NR_EOF

info "3. Базовая настройка SSH для GitLab/GitHub"
mkdir -p ~/.ssh && chmod 0700 ~/.ssh
if [[ ! -f ~/.ssh/config ]] || ! grep -q "Host gitlab.com" ~/.ssh/config 2>/dev/null; then
    cat << 'SSH_EOF' >> ~/.ssh/config
Host gitlab.com
  StrictHostKeyChecking no
  UserKnownHostsFile=/dev/null
Host github.com
  StrictHostKeyChecking accept-new
SSH_EOF
    chmod 0600 ~/.ssh/config
fi

info "4. Настройка аппаратных часов (UTC)"
sudo timedatectl set-local-rtc 0 --adjust-system-clock 2>/dev/null || sudo timedatectl set-local-rtc 0 || true

info "5. Оптимизация Snap (хранить не более 2 ревизий пакетов)"
sudo snap set system refresh.retain=2 2>/dev/null || true

info "6. Настройка GNOME Dock (сворачивание окон по клику)"
gsettings set org.gnome.shell.extensions.dash-to-dock click-action 'minimize' 2>/dev/null || true

info "7. Обновление репозиториев и установка системных утилит"
sudo apt update -y
sudo apt install -y git gh mc tmux zsh mosh curl wget ca-certificates \
    net-tools make apt-transport-https gpg gnupg software-properties-common \
    dconf-editor gnome-tweaks ubuntu-restricted-extras

info "8. Установка Google Chrome"
if ! command -v google-chrome &>/dev/null; then
    wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/google-chrome.deb
    sudo apt install -y /tmp/google-chrome.deb
    rm -f /tmp/google-chrome.deb
    success "Google Chrome успешно установлен!"
else
    success "Google Chrome уже установлен."
fi

info "9. Установка Telegram Desktop"
if ! command -v telegram-desktop &>/dev/null; then
    sudo snap install telegram-desktop || true
    success "Telegram Desktop установлен!"
fi

echo
success "======================================================================="
success " Шаг 1 успешно завершен! Переходите к 2_ubuntuDocker.sh или install.sh"
success "======================================================================="
