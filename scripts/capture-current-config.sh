#!/bin/bash
# ==============================================================================
# capture-current-config.sh — Создание полного слепка настроек текущей системы:
#                             десктоп GNOME (док, иконки, темы), Zsh/Bash,
#                             VS Code, CopyQ, Guake, btop, mc, ярлыки и скрипты.
# ==============================================================================
set -e

C_RESET="\033[0m"
C_BOLD="\033[1m"
C_GREEN="\033[1;32m"
C_BLUE="\033[1;34m"
C_YELLOW="\033[1;33m"

info()    { echo -e "\n${C_BLUE}[ИНФО]${C_RESET} $*"; }
success() { echo -e "${C_GREEN}[УСПЕХ]${C_RESET} $*"; }
warn()    { echo -e "${C_YELLOW}[ВНИМАНИЕ]${C_RESET} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
USER_DIR="$REPO_DIR/\$USER"

# Определение реального домашнего каталога пользователя
REAL_HOME="/home/${SUDO_USER:-asv-spb}"
[[ -d "$REAL_HOME" ]] || REAL_HOME="$HOME"

mkdir -p "$USER_DIR" "$USER_DIR/.config" "$USER_DIR/.local/bin" "$USER_DIR/.local/share/applications" "$USER_DIR/Dev"

safe_cp() {
    local src="$1"
    local dest="$2"
    if [[ -e "$src" ]]; then
        if [[ "$src" -ef "$dest" ]]; then
            return 0
        fi
        cp --remove-destination -rf "$src" "$dest" 2>/dev/null || cp -rf "$src" "$dest" 2>/dev/null || true
    fi
}

info "1. Захват параметров десктопа GNOME (Док, иконки, обои, тема, шорткаты)"
mkdir -p "$USER_DIR/.config/dconf"
if [[ -f "$REAL_HOME/.config/dconf/user" ]]; then
    safe_cp "$REAL_HOME/.config/dconf/user" "$USER_DIR/.config/dconf/user"
    success "База dconf (user) успешно сохранена!"
fi

info "2. Захват конфигураций Shell (Zsh, Bash, ZeroTier, Git)"
for file in .bashrc .zshrc .zt-functions.sh .gitconfig .p10k.zsh; do
    if [[ -f "$REAL_HOME/$file" ]]; then
        safe_cp "$REAL_HOME/$file" "$USER_DIR/$file"
        success "Скопирован: ~/$file"
    fi
done
rm -f "$USER_DIR/.config/gtk-3.0/bookmarks" 2>/dev/null || true

info "3. Захват настроек VS Code (settings, keybindings, snippets, MCP)"
mkdir -p "$USER_DIR/.config/Code/User"
for vsc in settings.json keybindings.json; do
    if [[ -f "$REAL_HOME/.config/Code/User/$vsc" ]]; then
        safe_cp "$REAL_HOME/.config/Code/User/$vsc" "$USER_DIR/.config/Code/User/$vsc"
        success "Скопирован конфиг VS Code: $vsc"
    fi
done
rm -f "$USER_DIR/.config/gtk-3.0/bookmarks" 2>/dev/null || true

if [[ -d "$REAL_HOME/.config/Code/User/snippets" ]]; then
    mkdir -p "$USER_DIR/.config/Code/User/snippets"
    cp -rf "$REAL_HOME/.config/Code/User/snippets/"* "$USER_DIR/.config/Code/User/snippets/" 2>/dev/null || true
    success "Скопированы сниппеты кода VS Code"
fi

if [[ -d "$REAL_HOME/.config/Code/User/mcp" ]]; then
    mkdir -p "$USER_DIR/.config/Code/User/mcp"
    cp -rf "$REAL_HOME/.config/Code/User/mcp/"* "$USER_DIR/.config/Code/User/mcp/" 2>/dev/null || true
    success "Скопированы MCP-конфигурации VS Code"
fi

if command -v code &>/dev/null; then
    code --list-extensions > "$USER_DIR/Dev/vscode-extensions.txt" 2>/dev/null || true
    success "Список установленных плагинов VS Code сохранен в: \$USER/Dev/vscode-extensions.txt"
fi

info "4. Захват конфигураций прикладных программ (~/.config/)"
for app in copyq guake btop mc OpenRGB warp-terminal autostart mimeapps.list gtk-3.0 gtk-4.0 nekobox; do
    if [[ -d "$REAL_HOME/.config/$app" ]]; then
        mkdir -p "$USER_DIR/.config/$app"
        safe_cp "$REAL_HOME/.config/$app" "$USER_DIR/.config/"
        success "Захвачен каталог: ~/.config/$app"
    elif [[ -f "$REAL_HOME/.config/$app" ]]; then
        safe_cp "$REAL_HOME/.config/$app" "$USER_DIR/.config/$app"
        success "Захвачен файл: ~/.config/$app"
    fi
done
rm -f "$USER_DIR/.config/gtk-3.0/bookmarks" 2>/dev/null || true

info "5. Захват пользовательских скриптов и ярлыков (~/.local/)"
for script in clean-sys.sh code-updater.sh dns-switch.sh; do
    if [[ -f "$REAL_HOME/.local/bin/$script" ]]; then
        safe_cp "$REAL_HOME/.local/bin/$script" "$USER_DIR/$script"
        chmod +x "$USER_DIR/$script"
        success "Захвачен скрипт: ~/.local/bin/$script"
    elif [[ -f "$REAL_HOME/$script" ]]; then
        safe_cp "$REAL_HOME/$script" "$USER_DIR/$script"
        chmod +x "$USER_DIR/$script"
        success "Захвачен скрипт: ~/$script"
    fi
done
rm -f "$USER_DIR/.config/gtk-3.0/bookmarks" 2>/dev/null || true

if [[ -f "$REAL_HOME/.local/bin/telegram-clean.sh" ]]; then
    safe_cp "$REAL_HOME/.local/bin/telegram-clean.sh" "$USER_DIR/.local/bin/telegram-clean.sh"
    chmod +x "$USER_DIR/.local/bin/telegram-clean.sh"
    success "Захвачен лаунчер Telegram с TRIM"
fi

if [[ -d "$REAL_HOME/.local/share/applications" ]]; then
    cp -rf "$REAL_HOME/.local/share/applications/"* "$USER_DIR/.local/share/applications/" 2>/dev/null || true
    success "Захвачены кастомные ярлыки .desktop"
fi

info "6. Захват тем оформления (~/.themes/) и шрифтов (~/.fonts/)"
if [[ -d "$REAL_HOME/.fonts" ]]; then
    mkdir -p "$USER_DIR/.fonts"
    cp -rf "$REAL_HOME/.fonts/"* "$USER_DIR/.fonts/" 2>/dev/null || true
    success "Шрифты скопированы в: \$USER/.fonts/"
fi

if [[ -d "$REAL_HOME/.themes" ]]; then
    mkdir -p "$USER_DIR/themes"
    cp -rf "$REAL_HOME/.themes/"* "$USER_DIR/themes/" 2>/dev/null || true
    success "Темы скопированы в: \$USER/themes/"
fi

if [[ -d "$REAL_HOME/Templates" ]]; then
    mkdir -p "$USER_DIR/Templates"
    cp -rf "$REAL_HOME/Templates/"* "$USER_DIR/Templates/" 2>/dev/null || true
    success "Шаблоны документов скопированы в: \$USER/Templates/"
fi

echo
success "======================================================================="
success " 🎉 ПОЛНЫЙ СНИМОК НАСТРОЕК УСПЕШНО СОХРАНЕН В \$USER/ !"
success " Теперь deploy-dotfiles.sh перенесет 100% вашего рабочего окружения"
success " (десктоп, док, тему, Zsh, VS Code, ярлыки) на любую новую систему."
success "======================================================================="
