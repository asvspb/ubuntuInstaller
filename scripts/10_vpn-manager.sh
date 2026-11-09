#!/bin/bash
# ==============================================================================
# 10_vpn-manager.sh — установка vpn-manager CLI (команда `vpn`)
# Ставит бинари xray/hysteria, пакет в user-Python и проверяет `vpn --help`.
# НЕ требует root. Запуск: bash scripts/10_vpn-manager.sh
# ==============================================================================
set -e

C_RESET="\033[0m"
C_GREEN="\033[1;32m"
C_BLUE="\033[1;34m"
C_YELLOW="\033[1;33m"
C_RED="\033[1;31m"

info()    { echo -e "\n${C_BLUE}[INFO]${C_RESET} $*"; }
success() { echo -e "${C_GREEN}[OK]${C_RESET} $*"; }
warn()    { echo -e "${C_YELLOW}[WARN]${C_RESET} $*"; }
err()     { echo -e "${C_RED}[ERR]${C_RESET} $*" >&2; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG_DIR="$REPO_DIR/scripts/vpn-manager"

# --- 1. Проверки окружения ----------------------------------------------------
info "1. Проверки окружения (root не нужен)"
if ! command -v python3 >/dev/null 2>&1; then
    err "python3 не найден — установите Python 3.10+"
    exit 1
fi
PY_VER="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')"
success "python3 найден (версия $PY_VER)"

if ! python3 -c 'import typer' 2>/dev/null; then
    warn "typer не установлен — будет поставлен как зависимость пакета (pip install -e)"
fi

# --- 2. Бинари xray / hysteria ------------------------------------------------
info "2. Установка бинарей xray и hysteria (~/.local/lib/vpn)"
cd "$PKG_DIR"
if PYTHONPATH="$PKG_DIR" python3 -m vpnm install-bins; then
    success "бинари установлены (или уже нужной версии)"
else
    err "установка бинарей не удалась (все зеркала недоступны?)"
    exit 1
fi

# --- 3. Установка пакета: pipx → fallback pip --user ---------------------------
info "3. Установка пакета vpn-manager (команда 'vpn')"
if command -v pipx >/dev/null 2>&1; then
    if pipx install -e "$PKG_DIR" 2>/dev/null || \
       pipx upgrade vpn-manager >/dev/null 2>&1; then
        success "установлено через pipx"
    else
        warn "pipx не смог установить — fallback на pip --user"
        python3 -m pip install --user -e "$PKG_DIR"
    fi
else
    warn "pipx не найден — установка через pip --user"
    python3 -m pip install --user -e "$PKG_DIR"
fi

# PATH для pip --user installs
USER_BASE="$(python3 -m site --user-base 2>/dev/null || echo "$HOME/.local")"
case ":$PATH:" in
    *":$USER_BASE/bin:"*) ;;
    *) warn "$USER_BASE/bin нет в PATH — добавьте в ~/.zshrc: export PATH=\"\$PATH:$USER_BASE/bin\"" ;;
esac

# --- 4. Smoke-тест -------------------------------------------------------------
info "4. Smoke-тест: vpn --help"
VPN_BIN="$(command -v vpn || echo "$USER_BASE/bin/vpn")"
if "$VPN_BIN" --help >/dev/null; then
    success "vpn --help работает ($VPN_BIN)"
else
    err "vpn --help не запустился — проверьте PATH ($USER_BASE/bin)"
    exit 1
fi

success "vpn-manager установлен. Дальше: vpn profile import <link>, vpn install --linger, vpn up <name>"
