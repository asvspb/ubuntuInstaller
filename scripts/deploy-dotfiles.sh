#!/bin/bash
# ==============================================================================
# deploy-dotfiles.sh — Автоматическое развертывание пользовательских конфигов
#                      из папки $USER/ в домашнюю директорию $HOME + dconf GNOME
# ==============================================================================
set -e

C_RESET="\033[0m"
C_GREEN="\033[1;32m"
C_BLUE="\033[1;34m"
C_YELLOW="\033[1;33m"

info()    { echo -e "\n${C_BLUE}[ИНФО]${C_RESET} $*"; }
success() { echo -e "${C_GREEN}[УСПЕХ]${C_RESET} $*"; }
warn()    { echo -e "${C_YELLOW}[ВНИМАНИЕ]${C_RESET} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
USER_DIR="$REPO_DIR/\$USER"

[[ -d "$USER_DIR" ]] || {
    echo "Директория \$USER не найдена по пути $USER_DIR" >&2
    exit 1
}

TS=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="$HOME/.dotfiles_backup_$TS"

backup_and_link() {
    local src="$1"
    local dest="$2"

    if [[ -e "$dest" || -L "$dest" ]]; then
        mkdir -p "$BACKUP_DIR"
        cp -rL "$dest" "$BACKUP_DIR/" 2>/dev/null || true
    fi
    mkdir -p "$(dirname "$dest")"
    cp -rf "$src" "$dest"
}

info "1. Развертывание базовых shell-конфигураций (.bashrc, .zshrc, .zt-functions.sh, .gitconfig)"
for file in .bashrc .zshrc .zt-functions.sh .gitconfig .p10k.zsh; do
    if [[ -f "$USER_DIR/$file" ]]; then
        backup_and_link "$USER_DIR/$file" "$HOME/$file"
        success "Синхронизирован: ~/$file"
    fi
done

info "2. Развертывание утилит в ~/.local/bin"
mkdir -p "$HOME/.local/bin"
for script in clean-sys.sh code-updater.sh dns-switch.sh; do
    if [[ -f "$USER_DIR/$script" ]]; then
        backup_and_link "$USER_DIR/$script" "$HOME/.local/bin/$script"
        chmod +x "$HOME/.local/bin/$script"
        ln -sf "$HOME/.local/bin/$script" "$HOME/$script"
        success "Установлен скрипт: ~/.local/bin/$script"
    fi
done

if [[ -f "$USER_DIR/.local/bin/telegram-clean.sh" ]]; then
    backup_and_link "$USER_DIR/.local/bin/telegram-clean.sh" "$HOME/.local/bin/telegram-clean.sh"
    chmod +x "$HOME/.local/bin/telegram-clean.sh"
    success "Установлен лаунчер: ~/.local/bin/telegram-clean.sh"
fi

info "3. Развертывание .desktop ярлыков (~/.local/share/applications/)"
mkdir -p "$HOME/.local/share/applications"
if [[ -d "$USER_DIR/.local/share/applications" ]]; then
    cp -rf "$USER_DIR/.local/share/applications/"* "$HOME/.local/share/applications/" 2>/dev/null || true
    success "Ярлыки приложений обновлены!"
fi

info "4. Развертывание настроек программ (~/.config/)"
mkdir -p "$HOME/.config"
if [[ -d "$USER_DIR/.config" ]]; then
    cp -rf "$USER_DIR/.config/"* "$HOME/.config/"
    success "Конфигурации ~/.config успешно обновлены!"
fi

info "5. Развертывание настроек десктопа GNOME (Док, тема, иконки, горячие клавиши)"
if [[ -f "$USER_DIR/.config/dconf/gnome-desktop.dconf" ]] && command -v dconf &>/dev/null; then
    dconf load /org/gnome/ < "$USER_DIR/.config/dconf/gnome-desktop.dconf" 2>/dev/null || true
    success "Параметры десктопа GNOME (док, темы, ярлыки) успешно применены!"
fi

info "6. Развертывание шрифтов (~/.fonts/) и шаблонов (~/Templates/)"
if [[ -d "$USER_DIR/.fonts" ]]; then
    mkdir -p "$HOME/.fonts"
    cp -rf "$USER_DIR/.fonts/"* "$HOME/.fonts/"
    command -v fc-cache &>/dev/null && fc-cache -f "$HOME/.fonts" 2>/dev/null || true
    success "Шрифты установлены и кэш обновлен!"
fi

if [[ -d "$USER_DIR/Templates" ]]; then
    mkdir -p "$HOME/Templates"
    cp -rf "$USER_DIR/Templates/"* "$HOME/Templates/"
    success "Шаблоны документов скопированы в ~/Templates/"
fi

if [[ -d "$USER_DIR/themes" ]]; then
    mkdir -p "$HOME/.themes"
    cp -rf "$USER_DIR/themes/"* "$HOME/.themes/"
    success "Темы интерфейса скопированы в ~/.themes/"
fi

if [[ -d "$USER_DIR/Dev" ]]; then
    mkdir -p "$HOME/Dev"
    cp -rf "$USER_DIR/Dev/"* "$HOME/Dev/"
    success "Профили VS Code скопированы в ~/Dev/"
fi

info "7. Установка расширений VS Code (если доступен code)"
if command -v code &>/dev/null && [[ -f "$USER_DIR/Dev/vscode-extensions.txt" ]]; then
    while IFS= read -r ext; do
        [[ -n "$ext" ]] && code --install-extension "$ext" --force 2>/dev/null || true
    done < "$USER_DIR/Dev/vscode-extensions.txt"
    success "Плагины VS Code успешно установлены!"
fi

echo
success "======================================================================="
success " Все пользовательские конфиги ($USER/) успешно применены!"
if [[ -d "$BACKUP_DIR" ]]; then
    info "Резервная копия старых конфигов сохранена в: $BACKUP_DIR"
fi
success "======================================================================="
