#!/usr/bin/env bash
# =============================================================================
#  8_vds-server.sh — Инициализация нового VDS-сервера
#  Часть ubuntuInstaller
#
#  Запускается на ЛОКАЛЬНОЙ машине. Копирует SSH-ключ, устанавливает
#  VDS Orchestrator на удалённый сервер и открывает интерактивное меню vds.
# =============================================================================

set -e

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[x]${NC} $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VDS_DIR="$(dirname "$SCRIPT_DIR")/vds"

echo -e "${BOLD}${CYAN}=== VDS Server Setup ===${NC}"
echo ""

# ── 1. Данные сервера ──────────────────────────────────────────────────────────
read -rp "IP-адрес сервера: " SERVER_IP </dev/tty
[[ -z "$SERVER_IP" ]] && err "IP не может быть пустым"

read -rp "Пользователь (по умолчанию root): " SERVER_USER </dev/tty
SERVER_USER="${SERVER_USER:-root}"

INSTALL_DIR="/opt/my-vds"
REMOTE="${SERVER_USER}@${SERVER_IP}"

echo ""

# ── 2. SSH-ключ ────────────────────────────────────────────────────────────────
if [[ ! -f ~/.ssh/id_rsa && ! -f ~/.ssh/id_ed25519 ]]; then
    warn "SSH-ключ не найден — генерируем новый (ed25519)..."
    ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
else
    log "Локальный SSH-ключ найден."
fi

warn "Сейчас потребуется ПАРОЛЬ от сервера (один раз)."
ssh-copy-id -o StrictHostKeyChecking=accept-new "$REMOTE" || err "Не удалось скопировать SSH-ключ"
log "SSH-ключ скопирован."
echo ""

# ── 3. Копирование VDS-проекта или скачивание ─────────────────────────────────
if [[ -d "$VDS_DIR" && -f "$VDS_DIR/requirements.txt" ]]; then
    log "Найден локальный VDS-проект: $VDS_DIR"
    log "Копирование на сервер ${REMOTE}:${INSTALL_DIR} ..."
    ssh -o StrictHostKeyChecking=accept-new "$REMOTE" "mkdir -p ${INSTALL_DIR}"
    rsync -az --exclude='.git' --exclude='venv' \
        --exclude='__pycache__' --exclude='*.pyc' --exclude='.kilo' \
        "$VDS_DIR/" "${REMOTE}:${INSTALL_DIR}/"
    log "Файлы скопированы."
    INSTALL_CMD="INSTALL_DIR=${INSTALL_DIR} bash ${INSTALL_DIR}/install.sh"
else
    warn "Локальный VDS-проект не найден — скачиваем с GitHub."
    INSTALL_CMD="curl -fsSL https://raw.githubusercontent.com/asvspb/my-first-vds/main/install.sh | bash"
fi

# ── 4. Установка на сервере ───────────────────────────────────────────────────
log "Запуск установки на ${SERVER_IP} ..."
ssh -o StrictHostKeyChecking=accept-new "$REMOTE" "bash -c '$INSTALL_CMD'" \
    || err "Установка не удалась — проверьте логи выше"

echo ""
log "Сервер ${SERVER_IP} готов!"
warn "Открываем меню vds..."
sleep 1

# ── 5. Вход на сервер ─────────────────────────────────────────────────────────
ssh -t "$REMOTE" "vds"
