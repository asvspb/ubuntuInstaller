#!/usr/bin/env bash
# ==============================================================================
# 12_zapret.sh — установка zapret как «канала 0» (DPI-desync, без VPN)
# Ставит nfqws из github.com/bol-van/zapret, hostlists в /opt/zapret,
# systemd-юнит zapret.service и ежедневный таймер обновления hostlist.
#
# ТРЕБУЕТ ROOT: nfqws работает с iptables/nftables и raw-сокетами.
# Без sudo запуск невозможен — скрипт попросит пароль через sudo.
#
# Запуск: bash scripts/12_zapret.sh
# Идемпотентен: повторный запуск обновляет дерево/бинарь и не ломает config.
#
# После установки НЕ запускается автоматически подобранная стратегия:
# скрипт пытается скачать community-стратегию (см. STRATEGY_URLS ниже —
# редактируйте), а при неудаче печатает инструкцию blockcheck. Каркас
# ставится и без стратегии: подберите стратегию вручную и впишите
# NFQWS_OPT_* в /opt/zapret/config.
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
ZAPRET_REPO="https://github.com/bol-van/zapret"
# Зеркала клонирования (порядок = приоритет; пустая строка = напрямую)
GITHUB_MIRRORS=("https://ghproxy.net/" "https://ghfast.top/")

# Community-«ru-стратегии» (экспериментальные; репозитории сообщества
# меняются — при 404 просто добавьте актуальный URL сюда).
STRATEGY_URLS=(
    "https://ghproxy.net/https://raw.githubusercontent.com/Flowseal/zapret-discord-youtube/main/strategy.txt"
    "https://ghproxy.net/https://raw.githubusercontent.com/webdev778/zapret-discord-youtube-linux/main/strategy"
)

# Hostlist: community-список заблокированных доменов (antifilter).
# Меняется одной правкой здесь; таймер ниже качает его раз в сутки.
HOSTLIST_URL="https://community.antifilter.download/list/community.lst"

ZAPRET_DIR="/opt/zapret"
SRC_DIR="$ZAPRET_DIR/src"
BIN_DST="/usr/local/bin/nfqws"
HOSTLIST="$ZAPRET_DIR/ipset/zapret-hosts-user.txt"

# --- 0. Root -------------------------------------------------------------------
info "0. Проверка прав (zapret требует root: iptables + raw-сокеты)"
if [ "$(id -u)" -ne 0 ]; then
    if ! sudo -n true 2>/dev/null; then
        warn "нет passwordless sudo — сейчас будет запрошен пароль"
    fi
    exec sudo bash "$0" "$@"
fi
success "запуск с root-правами"

for tool in git make gcc curl systemctl; do
    command -v "$tool" >/dev/null 2>&1 || { err "$tool не найден — доустановите и повторите"; exit 1; }
done
success "git / make / gcc / curl / systemctl на месте"

# --- 1. Исходники zapret ---------------------------------------------------------
info "1. Исходники zapret (болван bol-van/zapret, зеркала ghproxy)"
if [ -d "$SRC_DIR/.git" ]; then
    (cd "$SRC_DIR" && git pull --ff-only) && success "дерево обновлено (git pull)" \
        || warn "git pull не удался — работаем с текущим деревом"
else
    mkdir -p "$ZAPRET_DIR"
    cloned=""
    for mirror in "${GITHUB_MIRRORS[@]}" ""; do
        url="${mirror}${ZAPRET_REPO}.git"
        info "  пробую: $url"
        if git clone --depth 1 "$url" "$SRC_DIR"; then
            cloned="$url"; break
        fi
    done
    [ -n "$cloned" ] || { err "клонирование не удалось ни через одно зеркало"; exit 1; }
    success "склонировано ($cloned)"
fi

# --- 2. Сборка nfqws --------------------------------------------------------------
info "2. Сборка nfqws (make -C nfqws)"
if make -C "$SRC_DIR/nfqws" 2>&1 | tail -3; then
    [ -x "$SRC_DIR/nfqws/nfqws" ] || { err "бинарь nfqws после сборки не найден"; exit 1; }
    install -m 0755 "$SRC_DIR/nfqws/nfqws" "$BIN_DST"
    success "nfqws установлен: $BIN_DST ($("$BIN_DST" --version 2>&1 | head -1))"
else
    err "сборка nfqws не удалась — проверьте вывод make выше"
    exit 1
fi

# --- 3. Config + hostlists ---------------------------------------------------------
info "3. Конфиг и hostlists ($ZAPRET_DIR)"
mkdir -p "$ZAPRET_DIR/ipset"
if [ ! -f "$ZAPRET_DIR/config" ]; then
    cp "$SRC_DIR/config.default" "$ZAPRET_DIR/config"
    success "config создан из config.default"
else
    success "config уже есть — не трогаю (idempotent)"
fi
chmod 0644 "$ZAPRET_DIR/config"
if [ ! -f "$HOSTLIST" ]; then
    : > "$HOSTLIST"   # пустой user-hostlist; наполнит таймер или blockcheck
    success "пустой user-hostlist создан: $HOSTLIST"
fi
# Включаем user-hostlist в конфиге, если ещё не включён
if ! grep -q '^HOSTLIST=' "$ZAPRET_DIR/config"; then
    echo 'HOSTLIST='"$HOSTLIST" >> "$ZAPRET_DIR/config"
    warn "в config добавлен HOSTLIST=$HOSTLIST"
fi

# --- 4. systemd-юнит zapret.service -------------------------------------------------
info "4. systemd-юнит zapret.service"
cat > /etc/systemd/system/zapret.service <<UNIT
[Unit]
Description=zapret DPI bypass (channel 0, nfqws)
After=network.target

[Service]
Type=forking
ExecStart=$ZAPRET_DIR/init.d/systemd/zapret start
ExecStop=$ZAPRET_DIR/init.d/systemd/zapret stop
ExecReload=$ZAPRET_DIR/init.d/systemd/zapret restart
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
success "юнит установлен: /etc/systemd/system/zapret.service"

# --- 5. Таймер обновления hostlist (раз в сутки) -------------------------------------
info "5. Таймер обновления hostlist раз в сутки"
cat > /usr/local/bin/zapret-hostlist-update <<LOADER
#!/usr/bin/env bash
# Обновляет community-hostlist (см. scripts/12_zapret.sh, HOSTLIST_URL).
set -e
TMP="\$(mktemp)"
curl -fsS --max-time 60 "$HOSTLIST_URL" -o "\$TMP"
mv "\$TMP" "$HOSTLIST"
systemctl try-reload-or-restart zapret.service 2>/dev/null || true
LOADER
chmod 0755 /usr/local/bin/zapret-hostlist-update
cat > /etc/systemd/system/zapret-hostlist.service <<HSVC
[Unit]
Description=Update zapret community hostlist
[Service]
Type=oneshot
ExecStart=/usr/local/bin/zapret-hostlist-update
HSVC
cat > /etc/systemd/system/zapret-hostlist.timer <<HTMR
[Unit]
Description=Daily zapret hostlist refresh
[Timer]
OnCalendar=daily
Persistent=true
[Install]
WantedBy=timers.target
HTMR
systemctl daemon-reload
systemctl enable --now zapret-hostlist.timer >/dev/null 2>&1 || true
/usr/local/bin/zapret-hostlist-update && success "hostlist скачан ($HOSTLIST_URL)" \
    || warn "первая загрузка hostlist не удалась — таймер повторит завтра; правьте HOSTLIST_URL"
success "таймер zapret-hostlist.timer включён"

# --- 6. Стратегия: community → blockcheck ---------------------------------------------
info "6. Стратегия обхода (NFQWS_OPT_* в $ZAPRET_DIR/config)"
strategy_ok=""
for url in "${STRATEGY_URLS[@]}"; do
    if curl -fsS --max-time 20 "$url" -o /tmp/zapret-strategy.txt 2>/dev/null \
       && [ -s /tmp/zapret-strategy.txt ]; then
        install -m 0644 /tmp/zapret-strategy.txt "$ZAPRET_DIR/strategy-community.txt"
        strategy_ok="$url"
        break
    fi
done
rm -f /tmp/zapret-strategy.txt

echo
echo -e "${C_GREEN}=============================================================${C_RESET}"
echo -e "${C_GREEN}[OK]${C_RESET} каркас zapret установлен (канал 0)."
if [ -n "$strategy_ok" ]; then
    echo "Community-стратегия скачана: $ZAPRET_DIR/strategy-community.txt"
    echo "(${strategy_ok})"
    echo "Перенесите параметры в NFQWS_OPT_* в $ZAPRET_DIR/config и запустите:"
    echo "  sudo systemctl restart zapret"
else
    echo "Community-стратегия недоступна — подберите вручную (интерактивно, ~30 мин):"
    echo "  cd $SRC_DIR && sudo bash blockcheck.sh"
    echo "Затем впишите подобранные параметры в NFQWS_OPT_* в $ZAPRET_DIR/config и:"
    echo "  sudo systemctl restart zapret"
    echo "Проверка: curl -s -o /dev/null -w '%{http_code}' https://www.youtube.com/generate_204"
    echo "  → 204 означает, что direct DPI-bypass жив (канал 0 ok)."
fi
echo "Управление: vpn zapret on|off|status (или sudo systemctl start/stop zapret)"
echo "Мониторинг: vpn monitor (строка zapret) / vpn doctor"
echo -e "${C_GREEN}=============================================================${C_RESET}"
