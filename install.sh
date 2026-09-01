#!/bin/bash
# ==============================================================================
# install.sh — Главный диспетчер настройки окружения Ubuntu
# ==============================================================================
set -e

C_RESET="\033[0m"
C_BOLD="\033[1m"
C_GREEN="\033[1;32m"
C_BLUE="\033[1;34m"
C_YELLOW="\033[1;33m"
C_CYAN="\033[1;36m"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$SCRIPT_DIR/scripts"

show_menu() {
    clear 2>/dev/null || true
    echo -e "${C_BOLD}======================================================================${C_RESET}"
    echo -e "${C_CYAN}         🛠️  UBUNTU WORKSTATION INSTALLER & OPTIMIZER ENGINE          ${C_RESET}"
    echo -e "${C_BOLD}======================================================================${C_RESET}"
    echo -e "Выберите сценарий настройки:"
    echo -e "  ${C_BOLD}[1]${C_RESET} 🚀 ${C_GREEN}ПОЛНАЯ УСТАНОВКА «ПОД КЛЮЧ»${C_RESET} (Шаги 1-5 + Dotfiles)"
    echo -e "  --------------------------------------------------------------------"
    echo -e "  ${C_BOLD}[2]${C_RESET} ⚙️  Шаг 1: Базовая система, sudo, Chrome, Telegram"
    echo -e "  ${C_BOLD}[3]${C_RESET} 🐳 Шаг 2: Docker Engine, Compose, Lazydocker"
    echo -e "  ${C_BOLD}[4]${C_RESET} 📦 Шаг 3: Пакет разработчика (VS Code, Antigravity, Node, CLI)"
    echo -e "  ${C_BOLD}[5]${C_RESET} 📱 Шаг 4: Snap-приложения (Obsidian, VLC, Chromium и др.)"
    echo -e "  ${C_BOLD}[6]${C_RESET} ⚡ Шаг 5: Системные оптимизации (Sysctl, SSD TRIM, BBR, журналы)"
    echo -e "  ${C_BOLD}[7]${C_RESET} 👤 Шаг 6: Развертывание конфигов и dotfiles (\$USER -> \$HOME)"
    echo -e "  --------------------------------------------------------------------"
    echo -e "  ${C_BOLD}[8]${C_RESET} 🌐 ZeroTier клиент и управление VDS-сервером"
    echo -e "  ${C_BOLD}[9]${C_RESET} 🔒 v2rayA прокси и локальный SOCKS5-туннель"
    echo -e "  ${C_BOLD}[0]${C_RESET} ❌ Выход"
    echo -e "======================================================================"
    read -rp "Ваш выбор [0-9]: " choice

    case "$choice" in
        1)
            echo -e "\n${C_GREEN}Запуск полной автоматической установки...${C_RESET}"
            bash "$SCRIPTS_DIR/1_ubuntuStart.sh"
            bash "$SCRIPTS_DIR/2_ubuntuDocker.sh"
            bash "$SCRIPTS_DIR/3_ubuntuPack.sh"
            bash "$SCRIPTS_DIR/4_snap-apps.sh"
            bash "$SCRIPTS_DIR/5_systemOptimizations.sh"
            bash "$SCRIPTS_DIR/deploy-dotfiles.sh"
            echo -e "\n${C_GREEN}🎉 ВСЕ ШАГИ УСПЕШНО ЗАВЕРШЕНЫ! Перезагрузите ПК для применения всех групп и настроек.${C_RESET}"
            ;;
        2) bash "$SCRIPTS_DIR/1_ubuntuStart.sh" ;;
        3) bash "$SCRIPTS_DIR/2_ubuntuDocker.sh" ;;
        4) bash "$SCRIPTS_DIR/3_ubuntuPack.sh" ;;
        5) bash "$SCRIPTS_DIR/4_snap-apps.sh" ;;
        6) bash "$SCRIPTS_DIR/5_systemOptimizations.sh" ;;
        7) bash "$SCRIPTS_DIR/deploy-dotfiles.sh" ;;
        8)
            echo -e "\n[1] Установить ZeroTier клиент | [2] Инициализация VDS сервера"
            read -rp "Выбор [1-2]: " sub
            if [[ "$sub" == "1" ]]; then bash "$SCRIPTS_DIR/6_zerotier-client.sh"
            elif [[ "$sub" == "2" ]]; then bash "$SCRIPTS_DIR/8_vds-server.sh"; fi
            ;;
        9)
            echo -e "\n[1] Установить v2rayA | [2] Локальный прокси-туннель"
            read -rp "Выбор [1-2]: " sub
            if [[ "$sub" == "1" ]]; then bash "$SCRIPTS_DIR/7_v2raya-proxy.sh"
            elif [[ "$sub" == "2" ]]; then bash "$SCRIPTS_DIR/9_local-proxy-tunnel.sh"; fi
            ;;
        0) exit 0 ;;
        *) echo "Неверный выбор"; sleep 1; show_menu ;;
    esac
}

show_menu
