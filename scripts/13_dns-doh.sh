#!/usr/bin/env bash
# ==============================================================================
# 13_dns-doh.sh — локальный DoH-резолвер (cloudflared proxy-dns, порт 5053)
#
# Лечит DNS-перехват провайдера: на «отравленном» plaintext-DNS
# youtube/googlevideo отдают NXDOMAIN через любой обычный резолвер.
# Локальный cloudflared шлёт запросы в Cloudflare по HTTPS (DoH) и
# systemd-resolved направляет системный DNS на него.
#
# Что ставит:
#   - бинарь /usr/local/bin/cloudflared-proxy-dns-bin (cloudflared 2025.11.1 —
#     последняя версия с proxy-dns; в 2026.10+ эту подкоманду убрали);
#   - systemd-юнит cloudflared-proxy-dns.service (ExecStart ... proxy-dns
#     --port 5053 --upstream https://cloudflare-dns.com/dns-query);
#   - /etc/systemd/resolved.conf.d/doh-local.conf (DNS=127.0.0.1:5053);
#   - daemon-reload + enable --now + restart systemd-resolved.
#
# ТРЕБУЕТ ROOT: пишет в /usr/local/bin и /etc/systemd.
# Запуск: bash scripts/13_dns-doh.sh
# Идемпотентен: повторный запуск обновляет бинарь/юнит и не ломает состояние.
#
# Верификация: dig @127.0.0.1 -p 5053 www.youtube.com +short — непустой вывод.
#
# Rollback:
#   sudo rm /etc/systemd/resolved.conf.d/doh-local.conf
#   sudo systemctl disable --now cloudflared-proxy-dns.service
#   sudo systemctl restart systemd-resolved
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
CF_VERSION="2025.11.1"
CF_REPO="https://github.com/cloudflare/cloudflared/releases/download/${CF_VERSION}/cloudflared-linux-amd64"
# Зеркала скачивания (порядок = приоритет; пустая строка = напрямую)
GITHUB_MIRRORS=("https://ghproxy.net/" "https://gh-proxy.com/")

BIN_DST="/usr/local/bin/cloudflared-proxy-dns-bin"
UNIT_DST="/etc/systemd/system/cloudflared-proxy-dns.service"
RESOLVED_CONF_DIR="/etc/systemd/resolved.conf.d"
RESOLVED_CONF="$RESOLVED_CONF_DIR/doh-local.conf"
DOH_PORT="5053"
DOH_UPSTREAM="https://cloudflare-dns.com/dns-query"

# --- 0. Root -------------------------------------------------------------------
info "0. Проверка прав (скрипт пишет в /usr/local/bin и /etc/systemd)"
if [ "$(id -u)" -ne 0 ]; then
    if ! sudo -n true 2>/dev/null; then
        warn "нет passwordless sudo — сейчас будет запрошен пароль"
    fi
    exec sudo bash "$0" "$@"
fi
success "запуск с root-правами"

for tool in curl systemctl dig install; do
    command -v "$tool" >/dev/null 2>&1 || { err "$tool не найден — доустановите и повторите"; exit 1; }
done
success "curl / systemctl / dig / install на месте"

# --- 1. Бинарь cloudflared -------------------------------------------------------
info "1. Скачивание cloudflared ${CF_VERSION} (последняя с proxy-dns)"
TMP_BIN="$(mktemp)"
downloaded=""
for mirror in "${GITHUB_MIRRORS[@]}" ""; do
    url="${mirror}${CF_REPO}"
    info "  пробую: $url"
    if curl -fsSL --retry 3 --retry-delay 2 --max-time 300 "$url" -o "$TMP_BIN" \
       && [ -s "$TMP_BIN" ]; then
        downloaded="$url"; break
    fi
done
[ -n "$downloaded" ] || { err "скачивание не удалось ни через одно зеркало"; rm -f "$TMP_BIN"; exit 1; }
chmod 0755 "$TMP_BIN"

got_version="$("$TMP_BIN" --version 2>&1 | head -1)"
if echo "$got_version" | grep -q "${CF_VERSION}"; then
    success "версия подтверждена: $got_version"
else
    err "скачанный бинарь не ${CF_VERSION}: $got_version"
    rm -f "$TMP_BIN"; exit 1
fi
install -m 0755 "$TMP_BIN" "$BIN_DST"
rm -f "$TMP_BIN"
success "бинарь установлен: $BIN_DST"

# --- 2. systemd-юнит cloudflared-proxy-dns.service ---------------------------------
info "2. systemd-юнит cloudflared-proxy-dns.service"
cat > "$UNIT_DST" <<UNIT
[Unit]
Description=Cloudflare DoH local proxy (DNS channel 0.5) — ${CF_VERSION}
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=${BIN_DST} proxy-dns --port ${DOH_PORT} --upstream ${DOH_UPSTREAM}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
chmod 0644 "$UNIT_DST"
systemctl daemon-reload
success "юнит установлен: $UNIT_DST"

# --- 3. resolved: системный DNS → локальный DoH -------------------------------------
info "3. /etc/systemd/resolved.conf.d/doh-local.conf (DNS=127.0.0.1:${DOH_PORT})"
mkdir -p "$RESOLVED_CONF_DIR"
cat > "$RESOLVED_CONF" <<CONF
[Resolve]
DNS=127.0.0.1:${DOH_PORT}
CONF
chmod 0644 "$RESOLVED_CONF"
success "resolved-override записан: $RESOLVED_CONF"

# --- 4. Запуск сервисов -------------------------------------------------------------
info "4. systemctl enable --now cloudflared-proxy-dns + restart systemd-resolved"
systemctl enable --now cloudflared-proxy-dns.service >/dev/null 2>&1
systemctl restart cloudflared-proxy-dns.service
systemctl restart systemd-resolved
success "сервисы перезапущены"

# --- 5. Верификация ------------------------------------------------------------------
info "5. Верификация: dig @127.0.0.1 -p ${DOH_PORT} www.youtube.com +short"
sleep 2
if dig "@127.0.0.1" -p "$DOH_PORT" www.youtube.com +short | grep -q .; then
    success "DoH-резолвер отвечает (youtube резолвится мимо провайдера)"
else
    err "проверка не прошла: локальный :${DOH_PORT} не отвечает"
    err "смотрите журнал: journalctl -u cloudflared-proxy-dns -n 50"
    exit 1
fi

echo
echo -e "${C_GREEN}=============================================================${C_RESET}"
echo -e "${C_GREEN}[OK]${C_RESET} локальный DoH-резолвер установлен (DNS-канал 0.5)."
echo "Системный DNS: systemd-resolved → 127.0.0.1:${DOH_PORT} (cloudflared → Cloudflare DoH)"
echo "Проверка: dig @127.0.0.1 -p ${DOH_PORT} www.youtube.com +short"
echo "Статус:   vpn monitor (строка dns-doh)"
echo "Откат:"
echo "  sudo rm $RESOLVED_CONF"
echo "  sudo systemctl disable --now cloudflared-proxy-dns.service"
echo "  sudo systemctl restart systemd-resolved"
echo -e "${C_GREEN}=============================================================${C_RESET}"
