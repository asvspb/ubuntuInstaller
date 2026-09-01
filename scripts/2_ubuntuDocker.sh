#!/bin/bash
# ==============================================================================
# 2_ubuntuDocker.sh — Установка Docker Engine, Docker Compose и Lazydocker
# ==============================================================================
set -e

C_RESET="\033[0m"
C_GREEN="\033[1;32m"
C_BLUE="\033[1;34m"

info()    { echo -e "\n${C_BLUE}[ИНФО]${C_RESET} $*"; }
success() { echo -e "${C_GREEN}[УСПЕХ]${C_RESET} $*"; }

TARGET_USER="${SUDO_USER:-$USER}"

info "1. Удаление устаревших/конфликтующих пакетов Docker"
for pkg in docker.io docker-doc docker-compose podman-docker containerd runc; do
    sudo apt remove -y "$pkg" 2>/dev/null || true
done

info "2. Настройка официального репозитория Docker"
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

UBUNTU_CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME} stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

info "3. Установка Docker Engine и Compose плагина"
sudo apt update -y
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

info "4. Настройка прав пользователя для Docker"
sudo usermod -aG docker "${TARGET_USER}"
sudo systemctl enable --now docker

info "5. Установка Lazydocker (TUI для Docker)"
if ! command -v lazydocker &>/dev/null; then
    LAZYDOCKER_VERSION=$(curl -s "https://api.github.com/repos/jesseduffield/lazydocker/releases/latest" | grep -Po '"tag_name": "v\K[0-9.]+' || echo "0.24.1")
    curl -Lo /tmp/lazydocker.tar.gz "https://github.com/jesseduffield/lazydocker/releases/latest/download/lazydocker_${LAZYDOCKER_VERSION}_Linux_x86_64.tar.gz"
    mkdir -p /tmp/lazydocker-temp
    tar xf /tmp/lazydocker.tar.gz -C /tmp/lazydocker-temp
    sudo mv /tmp/lazydocker-temp/lazydocker /usr/local/bin/lazydocker
    sudo chmod +x /usr/local/bin/lazydocker
    rm -rf /tmp/lazydocker.tar.gz /tmp/lazydocker-temp
    success "Lazydocker v${LAZYDOCKER_VERSION} успешно установлен!"
else
    success "Lazydocker уже установлен: $(lazydocker --version 2>/dev/null || true)"
fi

mkdir -p ~/Dev

echo
success "======================================================================="
success " Docker успешно установлен и настроен!"
success " ВНИМАНИЕ: Для применения группы docker выполните: newgrp docker"
success " или перезагрузите систему перед использованием docker без sudo."
success "======================================================================="
