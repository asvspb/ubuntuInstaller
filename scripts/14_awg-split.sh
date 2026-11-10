#!/usr/bin/env bash
# ==============================================================================
# 14_awg-split.sh — AmneziaWG-клиент + split-туннель (канал 1)
#
# Ставит awg-quick/awg из ppa:amnezia/ppa (клиенту dkms/модуль ядра НЕ нужен —
# AWG-пакеты совместимы с модулем wireguard в ядре), забирает конфиг с
# VDS-сервера по scp, ставит templated-юнит vpn-awg-split@<CONF>.service
# и поднимает split-туннель.
#
# ТРЕБУЕТ ROOT: awg-quick правит таблицу маршрутизации.
# Запуск: bash scripts/14_awg-split.sh
# Идемпотентен: повторный запуск обновляет конфиг/юнит и не ломает состояние.
#
# Server-side: конфиг создаёт VDS Orchestrator (см. scripts/11_vds-deploy.sh).
#
# STRATEGY_IPS (в шапке, редактируйте): split-список AllowedIPs, который
# скрипт предлагает вместо серверного full-tunnel (0.0.0.0/0).
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

# --- Параметры (редактируйте под себя) -----------------------------------------
# Split-AllowedIPs: диапазоны google (youtube) + telegram.
STRATEGY_IPS=(
    "8.8.8.8/32" "8.8.4.4/32"                # google dns (резерв)
    "142.250.0.0/15" "172.217.0.0/16"        # google / youtube frontend
    "173.194.0.0/16" "74.125.0.0/16"         # google services / video
    "216.58.192.0/19" "64.233.160.0/19"      # google video edge
    "108.177.0.0/17" "34.64.0.0/10"          # google infra / GCP
    "91.108.0.0/16"                          # telegram core
    "149.154.160.0/20"                       # telegram DC
    "95.161.64.0/20"                         # telegram (ru edge)
)

AWG_CONF_DIR="/etc/amnezia/amneziawg"   # стандартное место конфигов awg-quick
SERVER_CONF_PATH="/opt/my-vds/gateway/clients/amneziawg"
UNIT_DST="/etc/systemd/system/vpn-awg-split@.service"

# --- 0. Root -------------------------------------------------------------------
info "0. Проверка прав (awg-quick правит маршруты)"
if [ "$(id -u)" -ne 0 ]; then
    if ! sudo -n true 2>/dev/null; then
        warn "нет passwordless sudo — сейчас будет запрошен пароль"
    fi
    exec sudo bash "$0" "$@"
fi
success "запуск с root-правами"

for tool in scp ssh systemctl ip; do
    command -v "$tool" >/dev/null 2>&1 || { err "$tool не найден — доустановите и повторите"; exit 1; }
done
success "scp / ssh / systemctl / ip на месте"

# --- 1. Вопросы ------------------------------------------------------------------
echo
read -r -p "SERVER_IP (или host-алиас из ~/.ssh/config): " SERVER_IP
[ -n "$SERVER_IP" ] || { err "SERVER_IP пуст"; exit 1; }
read -r -p "SSH USER на сервере [root]: " SSH_USER
SSH_USER="${SSH_USER:-root}"
read -r -p "Имя конфига [laptop]: " CONF_NAME
CONF_NAME="${CONF_NAME:-laptop}"
success "цель: ${SSH_USER}@${SERVER_IP}, конфиг: ${CONF_NAME}"

# --- 2. amneziawg-tools из ppa:amnezia/ppa ----------------------------------------
info "2. Установка amneziawg-tools (ppa:amnezia/ppa; dkms не нужен — только CLI)"
if command -v awg-quick >/dev/null 2>&1 && command -v awg >/dev/null 2>&1; then
    success "awg уже установлен ($(awg --version 2>&1 | head -1))"
else
    if ! grep -rq "^deb .*/amnezia/ppa" /etc/apt/sources.list.d/ 2>/dev/null; then
        add-apt-repository -y ppa:amnezia/ppa
    fi
    apt-get update -qq
    apt-get install -y amneziawg-tools
    command -v awg-quick >/dev/null 2>&1 || { err "awg-quick после установки не найден"; exit 1; }
    success "amneziawg-tools установлен"
fi

# --- 3. Fetch конфига с сервера ----------------------------------------------------
info "3. Забираю конфиг с сервера: ${SSH_USER}@${SERVER_IP}:${SERVER_CONF_PATH}/${CONF_NAME}.conf"
LOCAL_AWG_DIR="$HOME/.awg"
mkdir -p "$LOCAL_AWG_DIR"
if ! scp -q "${SSH_USER}@${SERVER_IP}:${SERVER_CONF_PATH}/${CONF_NAME}.conf" \
        "${LOCAL_AWG_DIR}/${CONF_NAME}.conf"; then
    err "не удалось забрать конфиг — сервер с vds ещё не установлен?"
    err "сначала разверните сервер: bash scripts/11_vds-deploy.sh"
    err "(конфиг создаёт VDS Orchestrator: vds gateway ... amneziawg)"
    exit 1
fi
chmod 0600 "${LOCAL_AWG_DIR}/${CONF_NAME}.conf"
success "конфиг: ${LOCAL_AWG_DIR}/${CONF_NAME}.conf (0600)"

# --- 4. AllowedIPs: full-tunnel → split ---------------------------------------------
info "4. Проверка AllowedIPs"
CURRENT_AIPS="$(awk -F' = ' '/^AllowedIPs/{print $2; exit}' "${LOCAL_AWG_DIR}/${CONF_NAME}.conf")"
if echo "$CURRENT_AIPS" | grep -q "0.0.0.0/0"; then
    warn "серверный конфиг full-tunnel: AllowedIPs = ${CURRENT_AIPS}"
    echo "  Split-туннель пропустит через AWG только google/telegram:"
    printf '    %s\n' "${STRATEGY_IPS[@]}"
    read -r -p "Заменить AllowedIPs на split-список? [Y/n]: " ANSWER
    if [ "${ANSWER:-Y}" != "Y" ] && [ "${ANSWER:-Y}" != "y" ]; then
        warn "оставляю full-tunnel как есть"
    else
        AIPS_STR="$(IFS=', '; echo "${STRATEGY_IPS[*]}")"
        sed -i "s|^AllowedIPs.*|AllowedIPs = ${AIPS_STR}|" "${LOCAL_AWG_DIR}/${CONF_NAME}.conf"
        chmod 0600 "${LOCAL_AWG_DIR}/${CONF_NAME}.conf"
        success "AllowedIPs заменён на split-список (${AIPS_STR})"
    fi
else
    success "AllowedIPs уже split/кастомный: ${CURRENT_AIPS} — не трогаю"
fi

# --- 5. Копия конфига в /etc/amnezia/amneziawg ---------------------------------------
info "5. Конфиг в ${AWG_CONF_DIR} (стандартное место awg-quick, читается root-юнитом)"
mkdir -p "$AWG_CONF_DIR"
install -m 0600 "${LOCAL_AWG_DIR}/${CONF_NAME}.conf" "${AWG_CONF_DIR}/${CONF_NAME}.conf"
success "${AWG_CONF_DIR}/${CONF_NAME}.conf (0600)"

# --- 6. systemd-юнит vpn-awg-split@.service -------------------------------------------
info "6. Templated-юнит vpn-awg-split@.service (инстанс = имя конфига)"
cat > "$UNIT_DST" <<UNIT
[Unit]
Description=AWG split tunnel (%i) via AmneziaWG
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/awg-quick up ${AWG_CONF_DIR}/%i.conf
ExecStop=/usr/bin/awg-quick down ${AWG_CONF_DIR}/%i.conf

[Install]
WantedBy=multi-user.target
UNIT
chmod 0644 "$UNIT_DST"
systemctl daemon-reload
success "юнит установлен: $UNIT_DST"

info "7. Запуск: systemctl enable --now vpn-awg-split@${CONF_NAME}"
systemctl enable --now "vpn-awg-split@${CONF_NAME}.service" >/dev/null 2>&1
systemctl restart "vpn-awg-split@${CONF_NAME}.service"
success "юнит vpn-awg-split@${CONF_NAME} включён"

# --- 8. Верификация -------------------------------------------------------------------
info "8. Верификация: awg show (handshake) + ping первого IP из AllowedIPs"
if ! awg show; then
    err "awg show пуст — интерфейс не поднялся; журнал: journalctl -u vpn-awg-split@${CONF_NAME} -n 50"
    exit 1
fi
if ! awg show "${CONF_NAME}" 2>/dev/null | grep -q "latest handshake"; then
    warn "handshake ещё не состоялся (сервер недоступен или ключи не совпадают)"
    warn "проверьте: ping ${SERVER_IP}, порт/порт-forward AWG на сервере"
else
    success "handshake получен"
fi
FIRST_IP="$(awk -F' = ' '/^AllowedIPs/{print $2; exit}' "${AWG_CONF_DIR}/${CONF_NAME}.conf" | cut -d',' -f1 | tr -d ' ')"
FIRST_IP="${FIRST_IP%%/*}"
if [ -n "$FIRST_IP" ] && ping -c 1 -W 2 "$FIRST_IP" >/dev/null 2>&1; then
    success "ping ${FIRST_IP} — туннель пропускает трафик"
else
    warn "ping ${FIRST_IP} не прошёл — маршруты ещё не применились; повторите через пару секунд: ping ${FIRST_IP}"
fi

echo
echo -e "${C_GREEN}=============================================================${C_RESET}"
echo -e "${C_GREEN}[OK]${C_RESET} AWG split-туннель установлен (канал 1: ${CONF_NAME})."
echo "Управление:"
echo "  sudo systemctl start/stop vpn-awg-split@${CONF_NAME}"
echo "  awg show"
echo "Мониторинг: vpn monitor (строка amneziawg — ok при поднятом интерфейсе)"
echo "Конфиг: ${AWG_CONF_DIR}/${CONF_NAME}.conf (копия: ~/.awg/${CONF_NAME}.conf, 0600)"
echo "Down:    sudo systemctl stop vpn-awg-split@${CONF_NAME}"
echo "Откат:   sudo systemctl disable --now vpn-awg-split@${CONF_NAME} && sudo rm ${UNIT_DST}"
echo -e "${C_GREEN}=============================================================${C_RESET}"
