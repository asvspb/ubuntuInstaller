#!/usr/bin/env bash
# =============================================================================
#  VDS Orchestrator — Bootstrap Installer
#  Version: 2.0
#
#  Единственный Bash-скрипт для подготовки окружения и передачи управления Python.
#  Создаёт venv, устанавливает зависимости, создаёт симлинк `vds`.
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[x]${NC} $*" >&2; exit 1; }

validate_install_dir() {
    local dir="$1"
    
    # Проверка на пустое значение
    [[ -z "$dir" ]] && err "INSTALL_DIR не может быть пустым"
    
    # Проверка на абсолютный путь
    [[ "$dir" != /* ]] && err "INSTALL_DIR должен быть абсолютным путем: $dir"
    
    # Проверка на системные директории
    case "$dir" in
        /|/usr|/etc|/home|/var|/bin|/sbin|/lib|/boot|/root)
            err "INSTALL_DIR не может быть системной директорией: $dir"
            ;;
    esac
    
    # Проверка на поддиректории системных путей
    case "$dir" in
        /usr/*|/etc/*|/bin/*|/sbin/*|/lib/*|/boot/*)
            err "INSTALL_DIR не может быть поддиректорией системного пути: $dir"
            ;;
    esac
    
    return 0
}

[[ $EUID -ne 0 ]] && err "Запустите от root: sudo bash $0"

INSTALL_DIR="${INSTALL_DIR:-/opt/my-vds}"
REPO_URL="${REPO_URL:-https://github.com/asvspb/my-first-vds}"

validate_install_dir "$INSTALL_DIR"

echo ""
echo -e "${BOLD}${CYAN}VDS Orchestrator — Bootstrap Installer${NC}"
echo ""

# ── 1. Обновление системы ─────────────────────────────────────────────────────
log "Шаг 1/6: Обновление системы..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-dev python3-pip git curl wget gcc ca-certificates
log "Системные зависимости установлены"

# ── 2. Создание директории проекта ────────────────────────────────────────────
log "Шаг 2/6: Создание директории проекта..."
mkdir -p "${INSTALL_DIR}"

if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SCRIPT_DIR="$PWD"
fi
if [[ -f "${SCRIPT_DIR}/requirements.txt" ]]; then
    log "Копирование файлов из ${SCRIPT_DIR}..."
    cp -r "${SCRIPT_DIR}/"* "${INSTALL_DIR}/"
else
    log "Клонирование репозитория ${REPO_URL}..."
    if [[ -d "${INSTALL_DIR}/.git" ]]; then
        cd "${INSTALL_DIR}"
        git pull --quiet
    else
        if [[ -d "${INSTALL_DIR}" ]]; then
            validate_install_dir "$INSTALL_DIR"
            rm -rf "${INSTALL_DIR}"
        fi
        git clone --quiet "${REPO_URL}" "${INSTALL_DIR}"
    fi
fi
log "Файлы проекта размещены в ${INSTALL_DIR}"

# ── 3. Создание виртуального окружения ────────────────────────────────────────
log "Шаг 3/6: Создание виртуального окружения..."
if [[ -d "${INSTALL_DIR}/venv" ]]; then
    warn "venv уже существует — пропускаем создание"
else
    python3 -m venv "${INSTALL_DIR}/venv"
    log "venv создан: ${INSTALL_DIR}/venv"
fi

# ── 4. Установка Python-зависимостей ──────────────────────────────────────────
log "Шаг 4/6: Установка Python-зависимостей..."
"${INSTALL_DIR}/venv/bin/pip" install --upgrade pip --quiet
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" --quiet
log "Python-библиотеки установлены"

# ── 5. Создание симлинка `vds` ────────────────────────────────────────────────
log "Шаг 5/6: Создание команды vds..."
cat > /usr/local/bin/vds <<EOF
#!/bin/bash
export PYTHONPATH="${INSTALL_DIR}"
exec ${INSTALL_DIR}/venv/bin/python -m src.main "\$@"
EOF
chmod +x /usr/local/bin/vds
log "Команда vds доступна глобально"


# ── 6. Проверка установки ────────────────────────────────────────────────────
log "Шаг 6/6: Проверка установки..."
if vds --help &>/dev/null; then
    log "vds CLI работает корректно"
else
    warn "Проверка vds --help не удалась — проверьте установку"
fi

echo ""
echo -e "${BOLD}${GREEN}Установка завершена!${NC}"
echo ""
echo -e "  Команды:"
echo -e "    ${CYAN}vds --help${NC}           — справка"
echo -e "    ${CYAN}vds sysinfo${NC}          — статус сервера"
echo -e "    ${CYAN}vds zerotier install${NC} — установить ZeroTier"
echo -e "    ${CYAN}vds zerotier status${NC}  — статус ZeroTier"
echo -e "    ${CYAN}vds wireguard install${NC} — установить WireGuard"
echo -e "    ${CYAN}vds cleanup${NC}          — очистка системы"
echo ""
