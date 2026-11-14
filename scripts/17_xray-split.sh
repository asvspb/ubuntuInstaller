#!/usr/bin/env bash
# ==============================================================================
# 17_xray-split.sh — системный XHTTP-split: xray tun (xray0) + доменный split
# (SPEC-20261114-xhttp-split WP-2)
#
# Механика та же, что у AWG (dnsmasq → nft set → fwmark → table → dev), но
# dev = xray0 — tun-инбаунд xray-клиента (Xray v26.1.23+, свой пиннутый
# бинарь: socks-профили vpnm остаются на 25.1.1 без регресса).
#
#   vpn-xray-split@<профиль>.service:
#      ExecStart     = xray (26.x) run -c /etc/vpn-manager/xray-split-%i.json
#      ExecStartPost = vds-xsplit-cleanup --apply   (rule/table поверх tun)
#      ExecStopPost  = vds-xsplit-cleanup          (kill-switch: снять правила,
#                                                     черной дыры не будет)
#   nft table inet/xray_split + dnsmasq-конфиг xray_split.conf — ставит
#   scripts/15_domain-split.sh --dev xray0 --mark 0x23000 --table 23000
#   --nft-table xray_split (общая механика, один domains.txt).
#
# КОНФИГ юнита генерирует vpnm (vpnm/xsplit.py) в /etc/vpn-manager
# xray-split-<профиль>.json (0600) из share-link профиля xray-xhttp.
# Этот скрипт сам конфиги НЕ пишет — только юнит, бинарь, split, запуск.
#
# ТРЕБУЕТ ROOT.
#   sudo bash scripts/17_xray-split.sh --profile <p>   # unit+start+split
#   sudo bash scripts/17_xray-split.sh --status
#   sudo bash scripts/17_xray-split.sh --off           # stop/disable + cleanup
# Идемпотентен.
# ==============================================================================
set -e

C_RESET="\033[0m"; C_GREEN="\033[1;32m"; C_BLUE="\033[1;34m"
C_YELLOW="\033[1;33m"; C_RED="\033[1;31m"
info()    { echo -e "\n${C_BLUE}[INFO]${C_RESET} $*"; }
success() { echo -e "${C_GREEN}[OK]${C_RESET} $*"; }
warn()    { echo -e "${C_YELLOW}[WARN]${C_RESET} $*"; }
err()     { echo -e "${C_RED}[ERR]${C_RESET} $*" >&2; }

# --- Параметры -----------------------------------------------------------------
DEV="xray0"
MARK_HEX="0x23000"
ROUTE_TABLE="23000"
NFT_TABLE="xray_split"
CONF_DIR="/etc/vpn-manager"
BIN_DIR="/usr/local/lib/vpn-xray-split"
XRAY_BIN="${BIN_DIR}/xray"
XRAY_TUN_VERSION="26.1.23"   # первый релиз с tun-инбаундом (PR #5464/#5509)
UNIT_DST="/etc/systemd/system/vpn-xray-split@.service"
CLEANUP_DST="/usr/local/sbin/vds-xsplit-cleanup"
UNIT_NAME="vpn-xray-split@"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOMAIN_SPLIT_15="${XRAY_SPLIT_15:-${SCRIPT_DIR}/15_domain-split.sh}"

# --- Root gate -------------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    if ! sudo -n true 2>/dev/null; then
        warn "нет passwordless sudo — сейчас будет запрошен пароль"
    fi
    exec sudo bash "$0" "$@"
fi

for tool in systemctl nft ip python3 curl; do
    command -v "$tool" >/dev/null 2>&1 || { err "$tool не найден"; exit 1; }
done

# --- helpers ---------------------------------------------------------------------
latest_config() {
    local f newest="" mtime=0
    for f in "${CONF_DIR}"/xray-split-*.json; do
        [ -f "$f" ] || continue
        m=$(stat -c %Y "$f")
        if [ "$m" -gt "$mtime" ]; then newest="$f"; mtime=$m; fi
    done
    echo "$newest"
}

profile_from_config() {
    local f="$1"
    basename "$f" | sed 's/^xray-split-//; s/\.json$//'
}

install_xray_bin() {
    if [ -x "$XRAY_BIN" ] && "$XRAY_BIN" version 2>/dev/null | grep -q "$XRAY_TUN_VERSION"; then
        success "xray-tun бинарь на месте ($("$XRAY_BIN" version | head -1))"
        return 0
    fi
    info "установка Xray ${XRAY_TUN_VERSION} в ${BIN_DIR} (tun-инбаунд; v25.x tun не знает)"
    mkdir -p "$BIN_DIR"
    local url="https://github.com/XTLS/Xray-core/releases/download/v${XRAY_TUN_VERSION}/Xray-linux-64.zip"
    local tmp; tmp="$(mktemp -d)"
    local downloaded=""
    for mirror in "" "https://ghproxy.net/" "https://gh-proxy.com/"; do
        if curl -fsSL --retry 3 --retry-delay 2 --max-time 300 "${mirror}${url}" -o "$tmp/x.zip" && [ -s "$tmp/x.zip" ]; then
            downloaded=1; break
        fi
    done
    [ -n "$downloaded" ] || { err "скачивание Xray не удалось (все зеркала)"; rm -rf "$tmp"; exit 1; }
    # unzip может отсутствовать — python3 zipfile есть везде
    python3 - "$tmp/x.zip" "$tmp" <<'PY'
import sys, zipfile
zipfile.ZipFile(sys.argv[1]).extract("xray", sys.argv[2])
PY
    [ -x "$tmp/xray" ] || { err "бинарь xray не найден в архиве"; rm -rf "$tmp"; exit 1; }
    install -m 0755 "$tmp/xray" "$XRAY_BIN"
    rm -rf "$tmp"
    "$XRAY_BIN" version | head -1 | grep -q "$XRAY_TUN_VERSION" \
        || { err "версия бинаря не ${XRAY_TUN_VERSION}"; exit 1; }
    success "xray-tun установлен: $XRAY_BIN"
}

install_cleanup() {
    cat > "$CLEANUP_DST" <<CLEANUP
#!/usr/bin/env bash
# vds-xsplit-cleanup — правила/таблица XHTTP-split (ставится 17_xray-split.sh)
#   без аргументов: снять ip rule fwmark ${MARK_HEX} + route table ${ROUTE_TABLE}
#                   (kill-switch: ExecStopPost — без черной дыры)
#   --apply:        поднять rule/table поверх живого ${DEV} (ExecStartPost,
#                   self-heal после рестартов юнита)
set -e
case "\$1" in
    --apply)
        ip link show "${DEV}" >/dev/null 2>&1 || exit 0
        sysctl -qw net.ipv4.conf.all.rp_filter=0
        sysctl -qw "net.ipv4.conf.${DEV}.rp_filter=0" 2>/dev/null || true
        ip rule add pref ${ROUTE_TABLE} fwmark ${MARK_HEX} table ${ROUTE_TABLE} 2>/dev/null || true
        ip route replace table ${ROUTE_TABLE} default dev "${DEV}"
        ;;
    *)
        while ip rule show | grep -q "fwmark ${MARK_HEX}"; do
            ip rule del fwmark ${MARK_HEX} table ${ROUTE_TABLE} 2>/dev/null || break
        done
        ip route flush table ${ROUTE_TABLE} 2>/dev/null || true
        ;;
esac
CLEANUP
    chmod 0755 "$CLEANUP_DST"
    success "cleanup-скрипт: $CLEANUP_DST"
}

install_unit() {
    cat > "$UNIT_DST" <<UNIT
[Unit]
Description=VPN XHTTP split tun (${DEV}) — profile %i (SPEC-20261114)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${XRAY_BIN} run -c ${CONF_DIR}/xray-split-%i.json
ExecStartPost=${CLEANUP_DST} --apply
ExecStopPost=${CLEANUP_DST}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
    chmod 0644 "$UNIT_DST"
    systemctl daemon-reload
    success "юнит ${UNIT_NAME} установлен (daemon-reload)"
}

apply_domain_split() {
    [ -f "$DOMAIN_SPLIT_15" ] || { err "15_domain-split.sh не найден: $DOMAIN_SPLIT_15 (env XRAY_SPLIT_15)"; exit 1; }
    info "доменный split поверх ${DEV} (15_domain-split.sh: nft ${NFT_TABLE}, mark ${MARK_HEX}, table ${ROUTE_TABLE})"
    bash "$DOMAIN_SPLIT_15" --dev "$DEV" --mark "$MARK_HEX" --table "$ROUTE_TABLE" --nft-table "$NFT_TABLE"
}

# --- --status ---------------------------------------------------------------------
if [ "$1" = "--status" ]; then
    echo "=== xray-split: статус ==="
    echo "юнит:            $(systemctl is-active ${UNIT_NAME}*.service 2>/dev/null | head -1 || echo unknown) ($(systemctl list-units "${UNIT_NAME}*" --no-legend 2>/dev/null | awk '{print $1}' | head -1))"
    echo "конфиги:         $(ls "${CONF_DIR}"/xray-split-*.json 2>/dev/null | tr '\n' ' ' || echo 'нет')"
    echo "бинарь:          $("$XRAY_BIN" version 2>/dev/null | head -1 || echo 'нет')"
    echo "интерфейс:       $(ip link show "$DEV" >/dev/null 2>&1 && echo "$DEV up" || echo "$DEV нет")"
    echo "nft-таблица:     $(nft list table "inet $NFT_TABLE" >/dev/null 2>&1 && echo 'есть' || echo 'нет')"
    echo "ip rule:         $(ip rule show | grep -c "fwmark $MARK_HEX" || true) шт (fwmark $MARK_HEX)"
    echo "route t$ROUTE_TABLE: $(ip route show table "$ROUTE_TABLE" 2>/dev/null || echo 'пусто')"
    exit 0
fi

# --- --cleanup (ExecStartPost/ExecStopPost внутренний) ----------------------------
if [ "$1" = "--cleanup" ]; then
    exec "$CLEANUP_DST" "${2:-}"
fi

# --- --off: stop/disable + полный демонтаж xray-части ------------------------------
if [ "$1" = "--off" ]; then
    info "демонтаж xray-split"
    for u in $(systemctl list-units "${UNIT_NAME}*" --all --no-legend 2>/dev/null | awk '{print $1}'); do
        systemctl stop "$u" 2>/dev/null || true   # ExecStopPost снимет rule/table
        systemctl disable "$u" 2>/dev/null || true
        success "юнит ${u} остановлен"
    done
    if [ -f "$UNIT_DST" ]; then
        rm -f "$UNIT_DST" && systemctl daemon-reload
        success "юнит-шаблон удалён"
    fi
    nft delete table "inet $NFT_TABLE" 2>/dev/null && success "nft-таблица inet/${NFT_TABLE} удалена" || warn "nft-таблицы не было"
    if [ -f "/etc/dnsmasq.d/${NFT_TABLE}.conf" ]; then
        rm -f "/etc/dnsmasq.d/${NFT_TABLE}.conf" && systemctl restart dnsmasq.service 2>/dev/null || true
        success "dnsmasq-конфиг ${NFT_TABLE}.conf удалён"
    fi
    # ip rule/route — страховка, если ExecStopPost не доехал
    while ip rule show | grep -q "fwmark ${MARK_HEX}"; do
        ip rule del fwmark "$MARK_HEX" table "$ROUTE_TABLE" 2>/dev/null || break
    done
    ip route flush table "$ROUTE_TABLE" 2>/dev/null || true
    success "ip rule/route table ${ROUTE_TABLE} сняты (интернет напрямую)"
    # DNS-цепочку (dnsmasq/DoH) не трогаем — её ведёт 15/--off и vpn reset
    echo
    success "xray-split демонтирован (DNS остался как был; полный демонтаж: vpn scheme reset)"
    exit 0
fi

# --- основной путь: --profile <p> ---------------------------------------------------
PROFILE=""
if [ "$1" = "--profile" ]; then
    PROFILE="${2:-}"
elif [ -n "$1" ]; then
    err "неизвестный аргумент '$1' (ожидалось --profile <p> | --status | --off | --cleanup)"
    exit 1
fi
[ -n "$PROFILE" ] || PROFILE="$(basename "$(latest_config)" | sed 's/^xray-split-//; s/\.json$//')"
[ -n "$PROFILE" ] || { err "профиль не задан и конфигов нет — сначала 'vpn scheme xhttp-split' (vpnm генерирует конфиг)"; exit 1; }

CONF="${CONF_DIR}/xray-split-${PROFILE}.json"
[ -f "$CONF" ] || { err "конфиг ${CONF} не найден — сначала 'vpn scheme xhttp-split' (vpnm генерирует через sudo)"; exit 1; }

info "профиль: ${PROFILE} (конфиг ${CONF})"
install_xray_bin
"$XRAY_BIN" -test -c "$CONF" >/dev/null 2>&1 \
    || { err "конфиг ${CONF} не проходит xray -test — проверьте 'sudo ${XRAY_BIN} -test -c ${CONF}'"; exit 1; }
success "конфиг валиден (xray -test)"

# 1. юнит + старт (tun-iface появится из юнита)
install_cleanup
install_unit
systemctl enable --now "${UNIT_NAME}${PROFILE}.service" >/dev/null 2>&1 || true
systemctl restart "${UNIT_NAME}${PROFILE}.service"
sleep 2
if ! systemctl is-active --quiet "${UNIT_NAME}${PROFILE}.service"; then
    err "юнит ${UNIT_NAME}${PROFILE} не поднялся — журнал: journalctl -u ${UNIT_NAME}${PROFILE} -n 50"
    exit 1
fi
success "юнит ${UNIT_NAME}${PROFILE} активен"

# 2. доменный split поверх tun (nft/dnsmasq/resolved)
apply_domain_split

# 3. правила поверх живого iface (idempotent, дубль ExecStartPost — ок)
"$CLEANUP_DST" --apply

echo
echo -e "${C_GREEN}=============================================================${C_RESET}"
echo -e "${C_GREEN}[OK]${C_RESET} XHTTP-split поднят: ${DEV} → VLESS+XHTTP → сервер."
echo "Механика: домены из /opt/awg-split/domains.txt → nft ${NFT_TABLE} →"
echo "          mark ${MARK_HEX} → table ${ROUTE_TABLE} → dev ${DEV} (tun xray 26.x)"
echo "Проверки:"
echo "  sudo bash scripts/17_xray-split.sh --status"
echo "  curl --noproxy '*' -o /dev/null -w '%{http_code}' https://www.youtube.com/generate_204"
echo "Kill-switch: остановка юнита снимает rule/table (без черной дыры)."
echo "Демонтаж:    sudo bash scripts/17_xray-split.sh --off"
echo -e "${C_GREEN}=============================================================${C_RESET}"
