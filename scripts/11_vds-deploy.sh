#!/usr/bin/env bash
# =============================================================================
#  11_vds-deploy.sh — One-shot деплой VDS Orchestrator на новый сервер
#  Часть ubuntuInstaller
#
#  Запускается на ЛОКАЛЬНОЙ машине. Копирует SSH-ключ, синхронизирует репо
#  vds на сервер (rsync с обязательными excludes, включая gateway/state.json —
#  runtime-файл живого сервера), устанавливает Xray + Hysteria2,
#  при необходимости включает GitHub-mirror, выпускает LE-сертификат
#  и XHTTP-транспорт (если задан домен), создаёт демо-клиентов
#  и печатает share-links для импорта в клиентские приложения.
#
#  Идемпотентен: повторный запуск не ломает состояние
#  («уже установлен» / «сертификат уже выдан» / «клиент уже есть» — ок).
# =============================================================================

set -e

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[x]${NC} $*" >&2; exit 1; }

fail_step() {
    # fail_step <номер> <название> <как докатить вручную одной строкой>
    echo -e "${RED}[x]${NC} Шаг ${1} (${2}) упал." >&2
    echo -e "${RED}[x]${NC} Докатить вручную: ${3}" >&2
    exit 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_REPO="/home/asv-spb/Dev/my-coding/vds"
INSTALL_DIR="/opt/my-vds"
ACCEPT_USER="vpn-accept"

# Тестовый release-ассет Xray (версия фиксируется тегом, как в src/xray/install.py).
GITHUB_PROBE_URL="https://github.com/XTLS/Xray-core/releases/download/v25.1.1/Xray-linux-64.zip.dgst"
GITHUB_MIRRORS=("https://ghproxy.net" "https://gh-proxy.com")

# rsync-excludes: один массив, используется и в реальном rsync, и в dry-run
# проверке (DEPLOY.md). gateway/state.json исключается ОБЯЗАТЕЛЬНО — это
# runtime-файл живого сервера; rsync --delete без exclude стирает его.
RSYNC_EXCLUDES=(
    --exclude='.git'
    --exclude='.zcode'
    --exclude='venv'
    --exclude='__pycache__'
    --exclude='.pytest_cache'
    --exclude='*.pyc'
    --exclude='gateway/state.json'
    --exclude='upload-ssh-cert.sh'
    --exclude='xhttp.log'
)

echo -e "${BOLD}${CYAN}=== VDS Deploy (one-shot) ===${NC}"
echo ""

# ── 0. Параметры ──────────────────────────────────────────────────────────────
read -rp "IP-адрес сервера: " SERVER_IP </dev/tty
[[ -z "$SERVER_IP" ]] && err "IP не может быть пустым"

read -rp "Пользователь (по умолчанию root): " SERVER_USER </dev/tty
SERVER_USER="${SERVER_USER:-root}"

read -rp "Домен (Enter = без домена, без ACME/XHTTP): " SERVER_DOMAIN </dev/tty
SERVER_EMAIL=""
if [[ -n "$SERVER_DOMAIN" ]]; then
    read -rp "Email для Let's Encrypt: " SERVER_EMAIL </dev/tty
    [[ -z "$SERVER_EMAIL" ]] && err "Для домена нужен email LE"
fi

REMOTE="${SERVER_USER}@${SERVER_IP}"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new)

[[ -d "$LOCAL_REPO" && -f "$LOCAL_REPO/install.sh" ]] \
    || err "Локальный репо не найден: $LOCAL_REPO (скачивание с GitHub в этом скрипте не предусмотрено)"

echo ""
log "План: ${REMOTE}:${INSTALL_DIR} | домен: ${SERVER_DOMAIN:-нет} | протоколы: Xray + Hysteria2"
echo ""

# ── 1. SSH-ключ ───────────────────────────────────────────────────────────────
if [[ ! -f ~/.ssh/id_rsa && ! -f ~/.ssh/id_ed25519 ]]; then
    warn "SSH-ключ не найден — генерируем новый (ed25519)..."
    ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
else
    log "Шаг 1/9: локальный SSH-ключ найден."
fi

warn "Сейчас потребуется ПАРОЛЬ от сервера (один раз)."
ssh-copy-id "${SSH_OPTS[@]}" "$REMOTE" \
    || fail_step 1 "ssh-copy-id" \
       "ssh-copy-id ${SERVER_USER}@${SERVER_IP} (проверьте пароль/доступ по SSH)"
log "Шаг 1/9: SSH-ключ скопирован."
echo ""

# ── 2. rsync репо на сервер ───────────────────────────────────────────────────
log "Шаг 2/9: синхронизация ${LOCAL_REPO} → ${REMOTE}:${INSTALL_DIR} ..."
ssh "${SSH_OPTS[@]}" "$REMOTE" "mkdir -p ${INSTALL_DIR}" \
    || fail_step 2 "mkdir ${INSTALL_DIR}" \
       "ssh ${REMOTE} 'mkdir -p ${INSTALL_DIR}'"
rsync -az "${RSYNC_EXCLUDES[@]}" "$LOCAL_REPO/" "${REMOTE}:${INSTALL_DIR}/" \
    || fail_step 2 "rsync" \
       "rsync -az --exclude='gateway/state.json' ${LOCAL_REPO}/ ${REMOTE}:${INSTALL_DIR}/"
log "Шаг 2/9: файлы скопированы (state.json и .git исключены)."
echo ""

# ── 3. install.sh на сервере ──────────────────────────────────────────────────
log "Шаг 3/9: установка VDS Orchestrator на ${SERVER_IP} ..."
ssh "${SSH_OPTS[@]}" "$REMOTE" \
    "INSTALL_DIR=${INSTALL_DIR} bash ${INSTALL_DIR}/install.sh" \
    || fail_step 3 "install.sh" \
       "ssh ${REMOTE} 'INSTALL_DIR=${INSTALL_DIR} bash ${INSTALL_DIR}/install.sh' (смотрите логи выше)"
log "Шаг 3/9: установка завершена."
echo ""

# ── 4. Не-root юзер vpn-accept ────────────────────────────────────────────────
log "Шаг 4/9: не-root юзер ${ACCEPT_USER} + authorized_keys ..."
ssh "${SSH_OPTS[@]}" "$REMOTE" bash -s <<'REMOTE_USER_SETUP' \
    || fail_step 4 "vpn-accept" \
       "ssh ${REMOTE} — создайте пользователя вручную: useradd -m -s /bin/bash vpn-accept; install -d -m 700 -o vpn-accept -g vpn-accept /home/vpn-accept/.ssh; cp /root/.ssh/authorized_keys /home/vpn-accept/.ssh/; chown -R vpn-accept:vpn-accept /home/vpn-accept/.ssh"
set -e
if ! id vpn-accept >/dev/null 2>&1; then
    useradd -m -s /bin/bash vpn-accept
fi
install -d -m 700 -o vpn-accept -g vpn-accept /home/vpn-accept/.ssh
if [[ -f /root/.ssh/authorized_keys && ! -f /home/vpn-accept/.ssh/authorized_keys ]]; then
    cp /root/.ssh/authorized_keys /home/vpn-accept/.ssh/authorized_keys
    chown vpn-accept:vpn-accept /home/vpn-accept/.ssh/authorized_keys
    chmod 600 /home/vpn-accept/.ssh/authorized_keys
fi
REMOTE_USER_SETUP
log "Шаг 4/9: юзер ${ACCEPT_USER} готов."
echo ""

# ── 5. Авто-детект GitHub + зеркало ───────────────────────────────────────────
log "Шаг 5/9: проверка доступности GitHub с сервера ..."
probe_code="$(ssh "${SSH_OPTS[@]}" "$REMOTE" \
    "curl -s -o /dev/null -w '%{http_code}' --max-time 15 -L '${GITHUB_PROBE_URL}'" || true)"
if [[ "$probe_code" == "200" ]]; then
    log "Шаг 5/9: канал DIRECT — GitHub доступен (HTTP 200)."
else
    warn "GitHub недоступен с сервера (HTTP ${probe_code:-нет ответа}) — включаем зеркало."
    mirror_set=""
    for m in "${GITHUB_MIRRORS[@]}"; do
        m_code="$(ssh "${SSH_OPTS[@]}" "$REMOTE" \
            "curl -s -o /dev/null -w '%{http_code}' --max-time 15 -L '${m}/${GITHUB_PROBE_URL}'" || true)"
        if [[ "$m_code" == "200" ]]; then
            # VDS_GITHUB_MIRROR читается кодом из os.environ (src/*/install.py),
            # .env из INSTALL_DIR не подхватывается — поэтому /etc/environment.
            ssh "${SSH_OPTS[@]}" "$REMOTE" bash -s "${m}" <<'REMOTE_MIRROR_SETUP' \
                || fail_step 5 "VDS_GITHUB_MIRROR" \
                   "ssh ${REMOTE} \"echo 'VDS_GITHUB_MIRROR=<зеркало>' >> /etc/environment\""
set -e
mirror="$1"
line="VDS_GITHUB_MIRROR=${mirror}"
if grep -q '^VDS_GITHUB_MIRROR=' /etc/environment 2>/dev/null; then
    sed -i "s|^VDS_GITHUB_MIRROR=.*|${line}|" /etc/environment
else
    printf '\n%s\n' "$line" >> /etc/environment
fi
REMOTE_MIRROR_SETUP
            mirror_set="$m"
            break
        fi
    done
    [[ -n "$mirror_set" ]] || fail_step 5 "зеркала недоступны" \
        "проверьте вручную: curl -sL -o /dev/null -w '%{http_code}' '${GITHUB_MIRRORS[0]}/${GITHUB_PROBE_URL}'"
    log "Шаг 5/9: канал MIRROR — ${mirror_set} записан в /etc/environment."
fi
echo ""

# ── 6. Установка протоколов ───────────────────────────────────────────────────
log "Шаг 6/9: установка Xray (Reality, TCP 443) ..."
ssh "${SSH_OPTS[@]}" "$REMOTE" \
    "vds xray install" \
    || fail_step 6 "vds xray install" \
       "ssh ${REMOTE} 'vds xray install'"
log "Шаг 6/9: установка Hysteria2 (QUIC, UDP 443) ..."
ssh "${SSH_OPTS[@]}" "$REMOTE" \
    "vds hysteria2 install" \
    || fail_step 6 "vds hysteria2 install" \
       "ssh ${REMOTE} 'vds hysteria2 install'"
for unit in vds-xray vds-hysteria2; do
    state="$(ssh "${SSH_OPTS[@]}" "$REMOTE" "systemctl is-active ${unit}.service" || true)"
    [[ "$state" == "active" ]] \
        || fail_step 6 "systemctl is-active ${unit}" \
           "ssh ${REMOTE} 'systemctl status ${unit}.service; journalctl -u ${unit}.service -n 50'"
done
log "Шаг 6/9: vds-xray и vds-hysteria2 активны."
echo ""

# ── 7. Домен: ACME + XHTTP ────────────────────────────────────────────────────
if [[ -n "$SERVER_DOMAIN" ]]; then
    log "Шаг 7/9: LE-сертификат для ${SERVER_DOMAIN} ..."
    ssh "${SSH_OPTS[@]}" "$REMOTE" \
        "vds acme issue --domain ${SERVER_DOMAIN} --email ${SERVER_EMAIL}" \
        || fail_step 7 "vds acme issue" \
           "ssh ${REMOTE} 'vds acme issue --domain ${SERVER_DOMAIN} --email ${SERVER_EMAIL}' (проверьте A-запись и открытый порт 80)"
    log "Шаг 7/9: XHTTP-транспорт (port 8443) ..."
    ssh "${SSH_OPTS[@]}" "$REMOTE" \
        "vds xray add-transport xhttp --domain ${SERVER_DOMAIN} --mode port --apply --yes" \
        || fail_step 7 "vds xray add-transport xhttp" \
           "ssh ${REMOTE} 'vds xray add-transport xhttp --domain ${SERVER_DOMAIN} --mode port --apply --yes'"
    ssh "${SSH_OPTS[@]}" "$REMOTE" \
        "vds xray use-domain --domain ${SERVER_DOMAIN}" \
        || fail_step 7 "vds xray use-domain" \
           "ssh ${REMOTE} 'vds xray use-domain --domain ${SERVER_DOMAIN}'"
    log "Шаг 7/9: домен ${SERVER_DOMAIN} подключён (ACME + XHTTP:8443)."
else
    log "Шаг 7/9: домен не задан — ACME/XHTTP пропущены."
fi
echo ""

# ── 8. Демо-клиенты ───────────────────────────────────────────────────────────
log "Шаг 8/9: демо-клиенты phone (Xray) и mob (Hysteria2) ..."
ssh "${SSH_OPTS[@]}" "$REMOTE" \
    "vds gateway status" \
    || fail_step 8 "vds gateway status" \
       "ssh ${REMOTE} 'vds gateway status'"

ssh "${SSH_OPTS[@]}" "$REMOTE" bash -s <<'REMOTE_CLIENTS' \
    || fail_step 8 "add-client" \
       "ssh ${REMOTE} 'vds xray add-client phone; vds hysteria2 add-client mob'"
set -e
if vds gateway status | grep -E "phone \(xray"; then
    echo "клиент phone уже есть — пропуск"
else
    vds xray add-client phone
fi
if vds gateway status | grep -E "mob \(hysteria2"; then
    echo "клиент mob уже есть — пропуск"
else
    vds hysteria2 add-client mob
fi
REMOTE_CLIENTS
log "Шаг 8/9: демо-клиенты готовы."
echo ""

# ── 9. Ссылки для импорта ─────────────────────────────────────────────────────
log "Шаг 9/9: получение share-links ..."
echo ""
echo -e "${BOLD}${CYAN}=== Ссылки для импорта в клиентские приложения ===${NC}"
echo ""
ssh "${SSH_OPTS[@]}" "$REMOTE" "vds gateway links" \
    || fail_step 9 "vds gateway links" \
       "ssh ${REMOTE} 'vds gateway links'"
echo ""
log "Деплой ${SERVER_IP} завершён. Ссылки выше — продукт скрипта."
