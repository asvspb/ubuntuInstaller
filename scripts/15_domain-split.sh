#!/usr/bin/env bash
# ==============================================================================
# 15_domain-split.sh — доменный split-туннель поверх AWG (канал 1+)
#
# Механика (классический dnsmasq + nft + fwmark паттерн):
#   система (systemd-resolved) → dnsmasq (127.0.0.1:53) → cloudflared DoH
#   (127.0.0.1:5053 → Cloudflare). Лечит NXDOMAIN-перехват провайдера
#   глобально, а не только для youtube.
#   Домены из списка /opt/awg-split/domains.txt dnsmasq кладёт в nft set
#   inet/awg_split/routes (nftset=...). nft помечает пакеты на эти адреса
#   fwmark 0x21000, ip rule отправляет их в table 21000 с default dev
#   awg0-split. Всё остальное — напрямую (канал 0 zapret работает как раньше).
#
# ТРЕБУЕТ ROOT: nft/ip rule/resolved.
# Запуск:    bash scripts/15_domain-split.sh          (установка/переустановка)
#            sudo bash scripts/15_domain-split.sh --off    (полный демонтаж)
#            sudo bash scripts/15_domain-split.sh --status (текущее состояние)
# Идемпотентен: повторный запуск перегенерирует конфиг и правила.
#
# Совместимость с awg-quick (тонкое место, см. doc/vpn-manager.md):
#   ~/awg0-split.conf приводится к Table = off + AllowedIPs = 0.0.0.0/0, ::/0
#   (cryptokey-routing принимает всё, маршрутизацию ведёт nft/fwmark).
#   wg-quick/awg-quick при Table = off не создаёт своих маршрутов; fwmark
#   0xca6c на своём UDP-сокете AWG ставит всегда — правило nft «meta mark
#   0xca6c return» пропускает эти пакеты первыми, поэтому UDP-пакеты к
#   endpoint не зацикливаются в туннель (двойная защита: endpoint не входит
#   в доменный список и не попадает в set routes).
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
# Параметризация (SPEC-20261114-xhttp-split WP-1): одна механика обслуживает
# AWG-split (дефолты прежние) и XHTTP-split (17_xray-split.sh вызывает с
# --dev xray0 --mark 0x23000 --table 23000 --nft-table xray_split).
# Дефолты без флагов не изменились — backward-compat.
SPLIT_MODE="awg"            # awg | xray (влияет только на AWG-специфичные секции)
DOMAINS_FILE="/opt/awg-split/domains.txt"
DNSMASQ_CONF="/etc/dnsmasq.d/awg-split.conf"
NFT_TABLE="awg_split"
NFT_SET="routes"
MARK_HEX="0x21000"
RULE_PREF="21000"           # ip rule preference
ROUTE_TABLE="21000"
AWG_IFACE="${AWG_IFACE:-awg0-split}"
AWG_CONF="${AWG_CONF:-$HOME/awg0-split.conf}"   # конфиг awg-quick (Table=off)
EXCLUDE_FILE="/opt/awg-split/exclude.txt"
DOH_PORT="5053"
RESOLVED_CONF="/etc/systemd/resolved.conf.d/doh-local.conf"  # тот же файл, что у 13_dns-doh.sh
PUBLIC_DNS_CONF="/etc/systemd/resolved.conf.d/public-dns.conf"  # глобальный DNS=1.1.1.1 8.8.8.8 — обходит dnsmasq, нейтрализуем
# Юнит AWG: templated (14_awg-split.sh) или точечный — автодетект ниже.
AWG_UNIT_PREF=(vpn-awg-split@laptop.service vpn-awg-split.service)

# Реконструкция флагов для sudo re-exec: exec теряет сдвинутые positional
# params, поэтому параметры передаются явно (_FLAGS).
_FLAGS=""
# --- Разбор флагов (до sudo re-exec: аргументы передаются exec'ом) ---------------
while [ $# -gt 0 ]; do
    case "$1" in
        --dev)       AWG_IFACE="$2"; shift 2 ;;
        --mark)      MARK_HEX="$2"; shift 2 ;;
        --table)     ROUTE_TABLE="$2"; RULE_PREF="$2"; shift 2 ;;
        --nft-table) NFT_TABLE="$2"; shift 2 ;;
        --mode)      SPLIT_MODE="$2"; shift 2 ;;
        *) break ;;  # --status/--off и прочие разборы ниже
    esac
done
# Режим: AWG-специфичные секции (awg-конфиг, masquerade, автодетект юнита)
# выполняются только для awg*-интерфейса; для xray-туннеля (dev=xray0) они
# не нужны — xray сам держит сокеты с хостовым src-адресом.
case "${AWG_IFACE}" in awg*) : ;; *) SPLIT_MODE="xray" ;; esac
# DNSMASQ_CONF: дефолт — прежний путь (backward-compat, существующие
# установки живут в awg-split.conf); для другой nft-таблицы (xray_split) —
# отдельный файл по имени таблицы.
if [ "$NFT_TABLE" != "awg_split" ]; then
    DNSMASQ_CONF="/etc/dnsmasq.d/${NFT_TABLE}.conf"
fi
_FLAGS="--dev ${AWG_IFACE} --mark ${MARK_HEX} --table ${ROUTE_TABLE} --nft-table ${NFT_TABLE}"

# Дефолтный список доменов. Редактируйте в $DOMAINS_FILE после установки.
DEFAULT_DOMAINS=(
    youtube.com
    # короткие ссылки YouTube: домен .be, TLD-зона 'be' не входит в split
    # (инцидент 2026-11-13: https://youtu.be/... открывался напрямую → блок)
    youtu.be
    googlevideo.com
    ytimg.com
    ggpht.com
    youtubei.googleapis.com
    telegram.org
    t.me
    telesco.pe
    cdn-telegram.org
    teleg.ru
    # --- Google-кластер (поиск, Gemini, Meet, аккаунты, reCAPTCHA): ---
    google.com
    googleapis.com
    gstatic.com
    googleusercontent.com
    ggpht.com
    gvt1.com
    gvt2.com
    recaptcha.net
        # --- целые ходовые зоны через VPN (по запросу владельца 2026-11-11): ---
    # dnsmasq матчит суффиксно: 'com' = ВЕСЬ *.com. Убери строку, если зона
    # должна идти напрямую. NB: RU-проекты на .com (госуслуги-агрегаторы,
    # банки) тоже уйдут через туннель, и GLM/западные AI тоже — следи за
    # скоростью туннеля.
    com
    org
    net
    io
    dev
    ai
    # --- дополни своим списком (примеры заблокированных западных): ---
    instagram.com
    facebook.com
    x.com
    openai.com
)

# Домены, которые НЕЛЬЗЯ пускать через туннель (ходят напрямую даже если
# попали в TLD-зону). Суффикс-матчинг dnsmasq: 'z.ai' покрывает api.z.ai.
# Причина: AWG (UDP через TSPU-поддаваемый IP) рвёт GLM/ZCode-API
# (инцидент 2026-11-11: EHOSTUNREACH 43.109.226.10 при split .ai/.com).
DEFAULT_EXCLUDE_DOMAINS=(
    z.ai
    bigmodel.cn
    chatglm.cn
    zhipuai.cn
    # pypi напрямую доступен из РФ; через AWG его душит TSPU (pipx зависал)
    pypi.org
    pythonhosted.org
)

# Статические IP-диапазоны для nft-set: хосты, ходящие МИМО DNS
# (приложения с хардкод-адресами). Классика — Telegram: клиент коннектится
# к захардкоженным DC-IP (149.154.x, 91.108.x), доменный split их не видит,
# трафик уходит напрямую и блокируется (инцидент 2026-11-12: TG мёртв при
# живом youtube). Официальные диапазоны telegram.org.
STATIC_ROUTES_NETS=(
    91.108.4.0/22
    91.108.8.0/22
    91.108.12.0/22
    91.108.16.0/22
    91.108.20.0/22
    91.108.56.0/22
    149.154.160.0/20
    2001:67c:4e8::/48
    2001:b28:f23d::/48
    2001:b28:f23f::/48
)

# Новые дефолты для idempotent-merge в существующий domains.txt
# (то, что появилось после первой установки — добавляется, существующее не трогается)
MERGE_EXTRA_DOMAINS=(
    youtu.be
    google.com
    googleapis.com
    gstatic.com
    googleusercontent.com
    gvt1.com
    gvt2.com
    recaptcha.net
)

# --- 0. Root -------------------------------------------------------------------
if [ "$1" = "--status" ]; then
    # status можно смотреть и без root (nft покажет только с root — сами запросим)
    if [ "$(id -u)" -ne 0 ]; then exec sudo bash "$0" ${_FLAGS} "$@"; fi
elif [ "$(id -u)" -ne 0 ]; then
    if ! sudo -n true 2>/dev/null; then
        warn "нет passwordless sudo — сейчас будет запрошен пароль"
    fi
    exec sudo bash "$0" ${_FLAGS} "$@"
fi

for tool in systemctl nft ip dig install awk; do
    command -v "$tool" >/dev/null 2>&1 || { err "$tool не найден — доустановите и повторите"; exit 1; }
done

# AWG-конфиг живёт в home пользователя (sudo re-exec меняет HOME) — восстанавливаем
if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
    SUDO_USER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
    [ -n "$SUDO_USER_HOME" ] && AWG_CONF="${AWG_CONF/$HOME/$SUDO_USER_HOME}"
    AWG_CONF="${AWG_CONF/#\/root\//$SUDO_USER_HOME/}"
fi
if [ "$SPLIT_MODE" = "awg" ]; then
# --- Автодетект iface и конфига AWG (юнит может быть vpn-awg-split@<любой>) -------
# конфиг берём из ExecStart активного юнита; iface = базовое имя конфига
# (правило wg-quick/awg-quick), проверяем по 'awg show interfaces'.
awg_unit_conf() {
    local u
    for u in vpn-awg-split@* vpn-awg-split; do
        [ -n "$(systemctl list-units "$u" --all --no-legend 2>/dev/null)" ] || continue
        local ex
        ex="$(systemctl show "$u" --property=ExecStart 2>/dev/null | grep -oE '/etc/amnezia/amneziawg/[^ "]+')"
        [ -n "$ex" ] && { echo "$ex"; return 0; }
    done
    echo ""
}
_unit_conf="$(awg_unit_conf)"
if [ -n "$_unit_conf" ] && [ -f "$_unit_conf" ]; then
    AWG_CONF="$_unit_conf"
    AWG_IFACE="${AWG_IFACE:-$(basename "${AWG_CONF%.conf}")}"
elif [ -z "${AWG_CONF:-}" ] || [ "${AWG_CONF:-}" = "$HOME/awg0-split.conf" ]; then
    for _c in "$HOME/awg0-laptop.conf" "$HOME/awg0-split.conf" "$HOME/laptop.conf"; do
        [ -f "$_c" ] && { AWG_CONF="$_c"; AWG_IFACE="${AWG_IFACE:-$(basename "${AWG_CONF%.conf}")}"; break; }
    done
fi
# iface: если ещё не определён — берём живой из 'awg show'
if [ -z "${AWG_IFACE:-}" ] || [ "${AWG_IFACE:-}" = "awg0-split" ]; then
    _live="$(sudo -n awg show interfaces 2>/dev/null || awg show interfaces 2>/dev/null | head -1)"
    [ -n "$_live" ] && AWG_IFACE="$_live"
fi
info "AWG-конфиг: ${AWG_CONF}"
info "AWG-интерфейс: ${AWG_IFACE}"

# Туннельная подсеть (из Address конфига, /24 от туннельного IP) — для masquerade
AWG_ADDR="$(grep -m1 '^Address' "$AWG_CONF" 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
TUNNEL_NET="$(echo "${AWG_ADDR:-10.9.0.2}" | cut -d. -f1-3).0/24"

# --- Автодетект AWG-юнита ---------------------------------------------------------
AWG_UNIT=""
for u in "${AWG_UNIT_PREF[@]}"; do
    if systemctl list-unit-files 2>/dev/null | grep -q "^${u%.service}"; then
        AWG_UNIT="$u"; break
    fi
done

awg_restart() {
    # перезапуск AWG-туннеля после правки конфига; не фатально, если юнит не найден
    if [ -n "$AWG_UNIT" ]; then
        systemctl restart "$AWG_UNIT" && return 0
    fi
    # fallback: если интерфейс уже поднят, конфиг применится при следующем up
    warn "AWG-юнит не найден/не перезапустился — конфиг ${AWG_CONF} примените вручную: awg-quick down/up"
    return 0
}
else
    # xray-режим: AWG-конфиг/юнит не нужны; iface задаётся --dev и должен
    # существовать к моменту запуска (его поднимает vpn-xray-split@).
    info "режим: xray-split (iface ${AWG_IFACE}, mark ${MARK_HEX}, table ${ROUTE_TABLE}, nft ${NFT_TABLE})"
    TUNNEL_NET=""
fi

# --- helpers ---------------------------------------------------------------------
# домены из файла (без комментариев/пустых), по одному на строку
read_domains() {
    grep -vE '^\s*(#|$)' "$DOMAINS_FILE" | sed 's/[[:space:]]//g' | sort -u
}

# --- --status ---------------------------------------------------------------------
if [ "$1" = "--status" ]; then
    echo "=== доменный split: статус ==="
    echo "домены в списке: $( [ -f "$DOMAINS_FILE" ] && read_domains | wc -l || echo 0 )  ($DOMAINS_FILE)"
    echo "dnsmasq-конфиг:  $( [ -f "$DNSMASQ_CONF" ] && echo 'есть' || echo 'нет' )  ($DNSMASQ_CONF)"
    echo "dnsmasq:         $(systemctl is-active dnsmasq.service 2>/dev/null || echo unknown)"
    echo "cloudflared:     $(systemctl is-active cloudflared-proxy-dns.service 2>/dev/null || echo unknown)"
    echo "resolved DNS:    $(grep -h '^DNS=' /etc/systemd/resolved.conf.d/*.conf 2>/dev/null | sort -u | tr '\n' ' ')"
    echo "nft-таблица:     $(sudo nft list table "inet $NFT_TABLE" >/dev/null 2>&1 && echo 'есть' || echo 'нет')"
    echo "ip rule:         $(ip rule show | grep -c "fwmark $MARK_HEX" || true) шт (fwmark $MARK_HEX)"
    echo "route t$ROUTE_TABLE: $(ip route show table "$ROUTE_TABLE" 2>/dev/null || echo 'пусто')"
    echo "интерфейс:       $(ip link show "$AWG_IFACE" >/dev/null 2>&1 && echo "$AWG_IFACE up" || echo "$AWG_IFACE нет")"
    if [ "$SPLIT_MODE" = "awg" ]; then
        echo "AWG-юнит:        ${AWG_UNIT:-не найден}"
    else
        echo "режим:           xray-split (dev ${AWG_IFACE}, mark ${MARK_HEX}, table ${ROUTE_TABLE}, nft ${NFT_TABLE})"
        echo "xsplit-юнит:     $(systemctl is-active vpn-xray-split@*.service 2>/dev/null | head -1 || echo unknown)"
    fi
    echo "проверка DNS:    $(dig "@127.0.0.1" www.youtube.com +short 2>/dev/null | head -1 || echo 'нет ответа')"
    exit 0
fi

# --- --off: полный демонтаж ---------------------------------------------------------
if [ "$1" = "--off" ]; then
    info "демонтаж доменного split-туннеля"
    rm -f "$DNSMASQ_CONF" && success "dnsmasq-конфиг удалён: $DNSMASQ_CONF"
    nft delete table "inet $NFT_TABLE" 2>/dev/null && success "nft-таблица inet/${NFT_TABLE} удалена" || warn "nft-таблицы не было"
    while ip rule show | grep -q "fwmark ${MARK_HEX}"; do
        ip rule del fwmark "$MARK_HEX" table "$ROUTE_TABLE" 2>/dev/null || break
    done
    ip route flush table "$ROUTE_TABLE" 2>/dev/null || true
    success "ip rule/route table ${ROUTE_TABLE} удалены"
    # resolved: вернуть DNS провайдеру (clean-boot). Раньше здесь писался
    # DNS=127.0.0.1:5053 при выключаемом cloudflared → после tunnel off DNS
    # был мёртв до ручного 'vpn doh off' (инцидент 2026-11-11).
    if [ -f "$RESOLVED_CONF" ]; then
        mv "$RESOLVED_CONF" "${RESOLVED_CONF%.conf}.conf.disabled-manualvpn" \
            && success "resolved drop-in откачен (DNS → провайдер/DHCP)"
    fi
    # clean-boot: остановить и выключить DNS-цепочку (сессия закончилась)
    systemctl disable --now dnsmasq.service 2>/dev/null || true
    systemctl disable --now cloudflared-proxy-dns.service 2>/dev/null || true
    systemctl restart systemd-resolved.service
    # вернуть public-dns.conf (глобальный 1.1.1.1/8.8.8.8), если нейтрализовали
    if [ -f "${PUBLIC_DNS_CONF}.disabled-by-domain-split" ]; then
        mv "${PUBLIC_DNS_CONF}.disabled-by-domain-split" "$PUBLIC_DNS_CONF"
        success "public-dns.conf восстановлен"
    fi
    echo
    echo -e "${C_GREEN}=============================================================${C_RESET}"
    echo -e "${C_GREEN}[OK]${C_RESET} доменный split демонтирован."
    echo "Осталось вручную (по желанию):"
    if [ "$SPLIT_MODE" = "awg" ]; then
        echo "  - вернуть AWG-конфиг ${AWG_CONF} к split-AllowedIPs (backup: ${AWG_CONF}.bak.*)"
    else
        echo "  - xsplit-юнит: sudo systemctl disable --now vpn-xray-split@<профиль> (17_xray-split.sh --off)"
    fi
    echo "  - stop сервисов: sudo systemctl disable --now dnsmasq (если нужен только сплит)"
    echo "  - список доменов сохранён: $DOMAINS_FILE"
    echo -e "${C_GREEN}=============================================================${C_RESET}"
    exit 0
fi

# --- 1. Зависимости -----------------------------------------------------------------
info "1. Зависимости (dnsmasq, nftables)"
MISSING=()
# dnsmasq-base бывает установлен без юнита (зависимость) — нам нужен пакет dnsmasq
if ! systemctl list-unit-files 2>/dev/null | grep -q '^dnsmasq.service'; then
    MISSING+=(dnsmasq)
fi
command -v nft >/dev/null 2>&1 || MISSING+=(nftables)
if [ "${#MISSING[@]}" -gt 0 ]; then
    apt-get update -qq
    apt-get install -y "${MISSING[@]}"
fi
# clean-boot: только старт на сессию, НЕ enable (при загрузке DNS = провайдер)
systemctl start dnsmasq.service >/dev/null 2>&1 || true

# --- 1.5. Ubuntu-сборка dnsmasq без HAVE_NFTSET? → пересобираем из исходников -------
# (jammy dnsmasq 2.91: 'no-nftset'; директива nftset нужна нам для routes-set)
DNSMASQ_BIN="$(command -v dnsmasq)"
DNSMASQ_DROPIN="/etc/systemd/system/dnsmasq.service.d/vds-nftset.conf"
# поддержка nftset смотрим по реальному /usr/sbin/dnsmasq (его зовёт systemd-юнит);
# DNSSEC тоже обязателен (Ubuntu init-скрипт передаёт trust-anchor)
dnsmasq_ok() {
    local copts
    copts="$(/usr/sbin/dnsmasq --version 2>/dev/null | grep 'Compile time options')"
    echo "$copts" | grep -q ' nftset' && echo "$copts" | grep -qv 'no-DNSSEC'
}
if ! dnsmasq_ok; then
    info "  dnsmasq без HAVE_NFTSET — сборка из исходников (COPTS=-DHAVE_NFTSET)"
    DNSMASQ_REPO="https://github.com/imp/dnsmasq"
    DNSMASQ_SRC="/opt/awg-split/src/dnsmasq"
    for tool in git make gcc; do
        command -v "$tool" >/dev/null 2>&1 || { err "$tool не найден — нужен для сборки dnsmasq с nftset"; exit 1; }
    done
    # libnftables-dev нужен для nftset.c (HAVE_NFTSET); nettle/hogweed — для DNSSEC
    # (Ubuntu init-скрипт всегда передаёт trust-anchor → без -DHAVE_DNSSEC не стартует)
    MISSING_DEV=()
    dpkg -s libnftables-dev >/dev/null 2>&1 || MISSING_DEV+=(libnftables-dev)
    dpkg -s nettle-dev >/dev/null 2>&1 || MISSING_DEV+=(nettle-dev)
    dpkg -s pkg-config >/dev/null 2>&1 || MISSING_DEV+=(pkg-config)
    [ "${#MISSING_DEV[@]}" -eq 0 ] || apt-get install -y "${MISSING_DEV[@]}" >/dev/null
    if [ -d "$DNSMASQ_SRC/.git" ]; then
        (cd "$DNSMASQ_SRC" && timeout 120 git pull --ff-only >/dev/null 2>&1) || true
    else
        mkdir -p "$(dirname "$DNSMASQ_SRC")"
        cloned=""
        rm -rf "$DNSMASQ_SRC"
        for mirror in "" "https://ghproxy.net/"; do
            info "  пробую: ${mirror}${DNSMASQ_REPO}"
            if timeout 120 git clone --depth 1 "${mirror}${DNSMASQ_REPO}" "$DNSMASQ_SRC"; then
                cloned=1; break
            fi
            rm -rf "$DNSMASQ_SRC"
        done
        [ -n "$cloned" ] || { err "клонирование dnsmasq не удалось"; exit 1; }
    fi
    make -C "$DNSMASQ_SRC" clean >/dev/null 2>&1 || true
    if make -C "$DNSMASQ_SRC" COPTS="-DHAVE_NFTSET -DHAVE_DNSSEC" 2>&1 | tail -2; then
        [ -x "$DNSMASQ_SRC/src/dnsmasq" ] || { err "бинарь dnsmasq после сборки не найден"; exit 1; }
        install -m 0755 "$DNSMASQ_SRC/src/dnsmasq" /usr/local/sbin/dnsmasq
        DNSMASQ_LOCAL="/usr/local/sbin/dnsmasq"
        "$DNSMASQ_LOCAL" --version | grep -q ' nftset' || { err "пересобранный dnsmasq всё ещё без nftset"; exit 1; }
        "$DNSMASQ_LOCAL" --version | grep 'Compile time options' | grep -qv 'no-DNSSEC' || { err "пересобранный dnsmasq без DNSSEC"; exit 1; }
        success "dnsmasq собран с nftset: $DNSMASQ_LOCAL"
        # Debian-юнит идёт через /etc/init.d/dnsmasq, который зовёт /usr/sbin/dnsmasq —
        # заменяем бинарь там (дистро-копия сохраняется в .dist один раз).
        DNSMASQ_SBIN="/usr/sbin/dnsmasq"
        copts_sbin="$("$DNSMASQ_SBIN" --version 2>/dev/null | grep 'Compile time options')"
        if ! (echo "$copts_sbin" | grep -q ' nftset' && echo "$copts_sbin" | grep -qv 'no-DNSSEC'); then
            [ -f "$DNSMASQ_SBIN.dist" ] || cp -a "$DNSMASQ_SBIN" "$DNSMASQ_SBIN.dist"
            install -m 0755 "$DNSMASQ_LOCAL" "$DNSMASQ_SBIN"
        fi
        rm -f "$DNSMASQ_DROPIN"
        systemctl daemon-reload
        success "бинарь установлен: $DNSMASQ_SBIN (дистро-версия: $DNSMASQ_SBIN.dist)"
    else
        err "сборка dnsmasq не удалась — nftset-маршрутизация невозможна"
        exit 1
    fi
else
    success "dnsmasq с поддержкой nftset: $DNSMASQ_BIN"
fi
success "dnsmasq / nftables готовы"

# --- 2. cloudflared DoH (переиспользуем, если уже установлен 13_dns-doh.sh) ----------
info "2. DoH-резолвер (cloudflared-proxy-dns, 127.0.0.1:${DOH_PORT})"
if systemctl is-active --quiet cloudflared-proxy-dns.service 2>/dev/null \
   || [ -x /usr/local/bin/cloudflared-proxy-dns-bin ]; then
    # clean-boot: только старт, не enable
    systemctl start cloudflared-proxy-dns.service >/dev/null 2>&1 || true
    success "cloudflared-proxy-dns уже установлен — переиспользую"
else
    info "  cloudflared не найден — минимальная установка (см. scripts/13_dns-doh.sh для деталей)"
    CF_VERSION="2025.11.1"
    CF_REPO="https://github.com/cloudflare/cloudflared/releases/download/${CF_VERSION}/cloudflared-linux-amd64"
    GITHUB_MIRRORS=("https://ghproxy.net/" "https://gh-proxy.com/")
    BIN_DST="/usr/local/bin/cloudflared-proxy-dns-bin"
    TMP_BIN="$(mktemp)"
    downloaded=""
    for mirror in "${GITHUB_MIRRORS[@]}" ""; do
        url="${mirror}${CF_REPO}"
        info "  пробую: $url"
        if curl -fsSL --retry 3 --retry-delay 2 --max-time 300 "$url" -o "$TMP_BIN" && [ -s "$TMP_BIN" ]; then
            downloaded="$url"; break
        fi
    done
    [ -n "$downloaded" ] || { err "скачивание cloudflared не удалось — запустите сначала scripts/13_dns-doh.sh"; rm -f "$TMP_BIN"; exit 1; }
    chmod 0755 "$TMP_BIN"
    install -m 0755 "$TMP_BIN" "$BIN_DST"
    rm -f "$TMP_BIN"
    UNIT_DST="/etc/systemd/system/cloudflared-proxy-dns.service"
    cat > "$UNIT_DST" <<UNIT
[Unit]
Description=Cloudflare DoH local proxy — ${CF_VERSION}
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=${BIN_DST} proxy-dns --port ${DOH_PORT} --upstream https://cloudflare-dns.com/dns-query
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
    chmod 0644 "$UNIT_DST"
    systemctl daemon-reload
    systemctl start cloudflared-proxy-dns.service >/dev/null 2>&1 || true
    systemctl disable cloudflared-proxy-dns.service >/dev/null 2>&1 || true
    success "cloudflared-proxy-dns установлен, стартует на сессию (не enable)"
fi

# --- 3. Список доменов (не перетирать существующий!) -----------------------------------
info "3. Список доменов: $DOMAINS_FILE"
if [ -f "$DOMAINS_FILE" ]; then
    # idempotent-merge: новые дефолтные домены добавляем, файл не перетираем
    MERGED=0
    for d in "${MERGE_EXTRA_DOMAINS[@]}"; do
        if ! grep -qE "^\s*${d}\s*(#|$)" "$DOMAINS_FILE"; then
            echo "# добавлено 15_domain-split.sh (merge дефолтов)" >> "$DOMAINS_FILE"
            echo "$d" >> "$DOMAINS_FILE"
            MERGED=$((MERGED + 1))
        fi
    done
    if [ "$MERGED" -gt 0 ]; then
        success "существующий файл дополнен merge (+${MERGED} доменов Google-кластера)"
    else
        success "уже существует — не трогаю (merge: всё уже на месте)"
    fi
else
    mkdir -p "$(dirname "$DOMAINS_FILE")"
    {
        echo "# Доменный split (15_domain-split.sh): домены через AWG-туннель,"
        echo "# остальные — напрямую. Один домен на строку, комментарии #."
        echo "# После правки: sudo bash scripts/15_domain-split.sh (перегенерит dnsmasq-конфиг)"
        echo "# --- дополни своим списком ниже ---"
        for d in "${DEFAULT_DOMAINS[@]}"; do echo "$d"; done
    } > "$DOMAINS_FILE"
    chmod 0644 "$DOMAINS_FILE"
    success "создан со списком по умолчанию (${#DEFAULT_DOMAINS[@]} доменов)"
fi
# exclude.txt: то, что всегда напрямую (GLM-кластер и т.п.)
if [ -f "$EXCLUDE_FILE" ]; then
    MERGED=0
    for d in "${DEFAULT_EXCLUDE_DOMAINS[@]}"; do
        if ! grep -qE "^\s*${d}\s*(#|$)" "$EXCLUDE_FILE"; then
            echo "$d" >> "$EXCLUDE_FILE"; MERGED=$((MERGED + 1))
        fi
    done
    [ "$MERGED" -gt 0 ] && success "exclude.txt дополнен (+${MERGED})"
else
    mkdir -p "$(dirname "$EXCLUDE_FILE")"
    {
        echo "# Всегда НАПРЯМУЮ (не через туннель), даже если домен попал в TLD-зону."
        echo "# Один домен на строку; суффикс-матчинг (z.ai покрывает api.z.ai)."
        for d in "${DEFAULT_EXCLUDE_DOMAINS[@]}"; do echo "$d"; done
    } > "$EXCLUDE_FILE"
    chmod 0644 "$EXCLUDE_FILE"
    success "exclude.txt создан (${#DEFAULT_EXCLUDE_DOMAINS[@]} доменов GLM-кластера)"
fi
DOMAIN_COUNT="$(read_domains | wc -l)"
[ "$DOMAIN_COUNT" -gt 0 ] || { err "в $DOMAINS_FILE нет ни одного домена"; exit 1; }
success "активных доменов: $DOMAIN_COUNT"

# --- 4. dnsmasq-конфиг -------------------------------------------------------------------
info "4. dnsmasq-конфиг: $DNSMASQ_CONF"
{
    echo "# Сгенерировано scripts/15_domain-split.sh — НЕ редактировать вручную."
    echo "# Правьте $DOMAINS_FILE и перезапускайте скрипт."
    echo "listen-address=127.0.0.1"
    echo "bind-interfaces"
    echo "port=53"
    echo "no-resolv"
    echo "no-poll"
    echo "strict-order"
    echo "server=127.0.0.1#${DOH_PORT}"
    # fallback: plaintext Я.ДНС когда DoH-апстрим флапает (TSPU-окна 2026-11-11:
    # CF таймаутил минутами, весь DNS умирал). Деградация вместо полного блока:
    # заблокированные домены получат подмену и не откроются, но RU/всё прочее живо.
    # NB: подмена резолвов заблокированных доменов Я.ДНС не попадает в nft-set
    # корректно? попадает (IP подмены тоже кладутся) — youtube может "не открыть"
    # но остальная сеть стабильна.
    echo "server=77.88.8.8"
    echo "cache-size=1000"
    echo "# Домены из списка → nft set inet/${NFT_TABLE}/${NFT_SET}"
    # ВАЖНО: семейство отделяется ПРОБЕЛОМ ('4 inet#...'), не двоеточием —
    # dnsmasq в runtime сверяет setname[1]==' ' и с '4:' строит битую nft-команду
    while IFS= read -r d; do
        echo "nftset=/${d}/4 inet#${NFT_TABLE}#${NFT_SET}"
    done < <(read_domains)
    echo "# exclude-домены: резолвы уходят в отдельный set, chain их пропускает"
    if [ -f "$EXCLUDE_FILE" ]; then
        while IFS= read -r d; do
            echo "nftset=/${d}/4 inet#${NFT_TABLE}#excluded"
        done < <(grep -vE '^\s*(#|$)' "$EXCLUDE_FILE" | sed 's/[[:space:]]//g' | sort -u)
    fi
} > "$DNSMASQ_CONF"
chmod 0644 "$DNSMASQ_CONF"
EXCLUDE_COUNT="$( [ -f "$EXCLUDE_FILE" ] && grep -vcE '^\s*(#|$)' "$EXCLUDE_FILE" || echo 0 )"
success "dnsmasq-конфиг записан (${DOMAIN_COUNT} маршрутов + ${EXCLUDE_COUNT} исключений)"

# --- 5. nft-таблица inet/awg_split + ip rule/route -----------------------------------------
info "5. nft/fwmark-маршрутизация (mark ${MARK_HEX} → table ${ROUTE_TABLE} dev ${AWG_IFACE})"
if ! ip link show "$AWG_IFACE" >/dev/null 2>&1; then
    if [ "$SPLIT_MODE" = "awg" ]; then
        err "интерфейс ${AWG_IFACE} не существует — сначала поднимите AWG: bash scripts/14_awg-split.sh"
    else
        err "интерфейс ${AWG_IFACE} не существует — сначала поднимите xsplit-юнит: sudo systemctl start vpn-xray-split@<профиль>"
    fi
    exit 1
fi

# rp_filter: ответные пакеты приходят с iface туннеля, а reverse-lookup в main
# говорит «src достижим через wlp44s0» → strict rp_filter роняет их (инцидент
# 2026-11-11: TCP SYN уходил, SYN-ACK отбрасывался ядром).
sysctl -qw net.ipv4.conf.all.rp_filter=0
sysctl -qw "net.ipv4.conf.${AWG_IFACE}.rp_filter=0"
success "rp_filter отключён (all + ${AWG_IFACE})"

# идемпотентно: recreate таблицы (nft ruleset для одной таблицы)
nft delete table "inet $NFT_TABLE" 2>/dev/null || true
# masquerade нужен только AWG: ядро гонит пакеты через awg0-split, сохраняя
# LAN-src; для xray-tun xray сам держит сокеты с хостовым src — правила нет.
if [ "$SPLIT_MODE" = "awg" ]; then
    MASQ_CHAIN="    chain postroute {
        type nat hook postrouting priority srcnat; policy accept;
        # fwmark-перенаправление меняет интерфейс, но НЕ source address сокета
        # (выбран по main-таблице = LAN IP). Сервер не маршрутизирует обратно
        # на LAN-адрес → masquerade в туннельный адрес (фикс 2026-11-11).
        oifname \"${AWG_IFACE}\" ip saddr != ${TUNNEL_NET} meta mark ${MARK_HEX} masquerade
    }"
else
    MASQ_CHAIN=""
fi
nft -f - <<NFT
table inet ${NFT_TABLE} {
    set ${NFT_SET} {
        type ipv4_addr
        flags interval
        auto-merge
    }
    set excluded {
        type ipv4_addr
        flags interval
        auto-merge
    }
${MASQ_CHAIN}
    chain out {
        type route hook output priority mangle; policy accept;
        # UDP-пакеты самого AWG (fwmark 0xca6c от awg-quick) — не трогаем,
        # иначе endpoint-трафик зациклится в туннель; в xray-режиме таких
        # пакетов не бывает — правило пустое и безвредное
        meta mark 0xca6c return
        # исключения (GLM-кластер и т.п.) — всегда напрямую, даже в TLD-зоне
        ip daddr @excluded return
        # пакеты на доменные IP → mark ${MARK_HEX}
        ip daddr @${NFT_SET} meta mark set ${MARK_HEX}
    }
}
NFT
# статические диапазоны (Telegram DC и т.п.) — вне DNS-механики
STATIC_V4="$(echo "${STATIC_ROUTES_NETS[@]}" | tr ' ' '\n' | grep -E '^[0-9]+\.' | paste -sd,)"
STATIC_V6="$(echo "${STATIC_ROUTES_NETS[@]}" | tr ' ' '\n' | grep -E '^2001' | paste -sd,)"
[ -n "$STATIC_V4" ] && nft add element "inet ${NFT_TABLE} ${NFT_SET}" "{ ${STATIC_V4} }"
[ -n "$STATIC_V6" ] && nft add element "inet ${NFT_TABLE} ${NFT_SET}" "{ ${STATIC_V6} }" 2>/dev/null || true
success "nft-таблица inet/${NFT_TABLE} создана (set ${NFT_SET}, chain out; +${#STATIC_ROUTES_NETS[@]} стат. диапазонов)"

# ip rule + default в table 21000 (идемпотентно)
if ! ip rule show | grep -q "^${RULE_PREF}:.*fwmark ${MARK_HEX}"; then
    ip rule add pref "$RULE_PREF" fwmark "$MARK_HEX" table "$ROUTE_TABLE"
fi
if ! ip route show table "$ROUTE_TABLE" 2>/dev/null | grep -q "^default dev ${AWG_IFACE}"; then
    ip route replace table "$ROUTE_TABLE" default dev "$AWG_IFACE"
fi
success "ip rule pref ${RULE_PREF} fwmark ${MARK_HEX} → table ${ROUTE_TABLE} (default dev ${AWG_IFACE})"

# --- 6. AWG-конфиг (только режим AWG; xray-tun конфига awg-quick не требует) ---
if [ "$SPLIT_MODE" = "awg" ]; then
info "6. AWG-конфиг ${AWG_CONF}: Table=off + AllowedIPs=0.0.0.0/0, ::/0 (cryptokey-routing)"
if [ ! -f "$AWG_CONF" ]; then
    err "конфиг ${AWG_CONF} не найден — сначала bash scripts/14_awg-split.sh"
    exit 1
fi
CONF_DIRTY=0
if ! grep -qE '^Table[[:space:]]*=[[:space:]]*off' "$AWG_CONF"; then
    if grep -qE '^Table[[:space:]]*=' "$AWG_CONF"; then
        sed -i 's|^Table[[:space:]]*=.*|Table = off|' "$AWG_CONF"
    else
        # вставить в секцию [Interface] (после Address, либо сразу после заголовка)
        if grep -q '^Address' "$AWG_CONF"; then
            sed -i '0,/^Address/s//Table = off\nAddress/' "$AWG_CONF"
        else
            sed -i '0,/^\[Interface\]/s//\[Interface\]\nTable = off/' "$AWG_CONF"
        fi
    fi
    CONF_DIRTY=1
    success "Table = off добавлен"
else
    success "Table = off уже стоит"
fi
if ! grep -E '^AllowedIPs' "$AWG_CONF" | grep -q "0.0.0.0/0"; then
    cp -a "$AWG_CONF" "${AWG_CONF}.bak.$(date +%Y%m%d%H%M%S)"
    sed -i 's|^AllowedIPs[[:space:]]*=.*|AllowedIPs = 0.0.0.0/0, ::/0|' "$AWG_CONF"
    CONF_DIRTY=1
    success "AllowedIPs заменён на 0.0.0.0/0, ::/0 (backup: ${AWG_CONF}.bak.*)"
else
    success "AllowedIPs уже 0.0.0.0/0"
fi
chmod 0600 "$AWG_CONF"
if [ "$CONF_DIRTY" -eq 1 ]; then
    awg_restart
    sleep 2
    # факт: awg-quick при Table=off ставит fwmark на сокет — проверяем
    if ! ip rule show | grep -q "fwmark 0xca6c"; then
        warn "awg-quick не добавил ip rule fwmark 0xca6c — fwmark-исключение endpoint держит только nft-правило"
    else
        success "awg-quick подтвердил fwmark 0xca6c (ip rule на месте)"
    fi
fi
fi # SPLIT_MODE=awg

# --- 7. systemd-resolved: DNS → 127.0.0.1 (dnsmasq), Domains=~. ---------------------------------
info "7. systemd-resolved: системный DNS → dnsmasq (127.0.0.1:53)"
mkdir -p /etc/systemd/resolved.conf.d
if grep -q '^DNS=127.0.0.1$' "$RESOLVED_CONF" 2>/dev/null \
   && grep -q '^Domains=~\.$' "$RESOLVED_CONF" 2>/dev/null; then
    success "resolved-override уже в целевом виде"
else
    cp -a "$RESOLVED_CONF" "${RESOLVED_CONF}.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
    cat > "$RESOLVED_CONF" <<CONF
[Resolve]
# DNS через локальный dnsmasq → cloudflared DoH (15_domain-split.sh)
DNS=127.0.0.1
Domains=~.
CONF
    chmod 0644 "$RESOLVED_CONF"
    success "resolved-override записан (backup рядом: ${RESOLVED_CONF}.bak.*)"
fi
# public-dns.conf (DNS=1.1.1.1 8.8.8.8, Domains=~.) конкурирует с dnsmasq:
# resolved шлёт часть запросов мимо dnsmasq → nftset не наполняется. Убираем
# (backup рядом; --off возвращает).
if [ -f "$PUBLIC_DNS_CONF" ] && ! [ -f "${PUBLIC_DNS_CONF}.disabled-by-domain-split" ]; then
    mv "$PUBLIC_DNS_CONF" "${PUBLIC_DNS_CONF}.disabled-by-domain-split"
    success "public-dns.conf нейтрализован (${PUBLIC_DNS_CONF}.disabled-by-domain-split)"
fi

# --- 8. Рестарт сервисов + верификация ------------------------------------------------------------
info "8. Рестарт dnsmasq + systemd-resolved, верификация"
# дождаться прогрева DoH-upstream (гонка 2026-11-11: dnsmasq жив, а 5053 ещё
# не отвечает → verification падала на свежем старте cloudflared)
for i in $(seq 1 15); do
    dig "@127.0.0.1" -p "${DOH_PORT}" ya.ru +short +time=2 +tries=1 | grep -q '.' \
        && break
    [ "$i" -eq 15 ] && { err "cloudflared DoH (127.0.0.1:${DOH_PORT}) не отвечает"; exit 1; }
    sleep 1
done
success "DoH-upstream прогрет (127.0.0.1:${DOH_PORT})"
systemctl restart dnsmasq.service
systemctl restart systemd-resolved.service
sleep 2

if ! systemctl is-active --quiet dnsmasq.service; then
    err "dnsmasq не поднялся — журнал: journalctl -u dnsmasq -n 30"
    exit 1
fi
# retry: одиночный флап DoH-апстрима не должен ронять весь сплит (инцидент
# 2026-11-11 11:00: cloudflare-dns.com таймаутил ~минуту — единая проверка
# отвергла установку целиком)
RESOLVED_IP=""
for i in $(seq 1 5); do
    RESOLVED_IP="$(dig "@127.0.0.1" www.youtube.com +short +time=3 +tries=1 2>/dev/null | grep -E '^[0-9]+\.' | head -1)"
    [ -n "$RESOLVED_IP" ] && break
    [ "$i" -lt 5 ] && warn "dig youtube не прошёл (попытка $i/5) — жду 3с (флап DoH-апстрима?)"
    sleep 3
done
if [ -z "$RESOLVED_IP" ]; then
    err "dnsmasq не резолвит www.youtube.com за 5 попыток — проверьте cloudflared-proxy-dns (порт ${DOH_PORT})"
    exit 1
fi
success "dig @127.0.0.1 www.youtube.com → ${RESOLVED_IP}"
sleep 1
if nft list set "inet ${NFT_TABLE} ${NFT_SET}" 2>/dev/null | grep -qE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+'; then
    success "nft set inet/${NFT_TABLE}/${NFT_SET} наполнился:"
    nft list set "inet ${NFT_TABLE} ${NFT_SET}" | grep -E '^\s+[0-9]+\.' | head -5 | sed 's/^/    /'
else
    warn "set ${NFT_SET} пока пуст — dnsmasq кладёт резолвы при первом запросе; повторите dig"
fi

echo
echo -e "${C_GREEN}=============================================================${C_RESET}"
echo -e "${C_GREEN}[OK]${C_RESET} доменный split-туннель установлен."
echo "Механика: resolved → dnsmasq(127.0.0.1:53) → cloudflared DoH(:${DOH_PORT});"
echo "          домены из ${DOMAINS_FILE} → nft set ${NFT_TABLE}/${NFT_SET} → mark ${MARK_HEX} → awg0-split"
echo "Проверки:"
echo "  dig @127.0.0.1 www.youtube.com +short"
echo "  sudo nft list set inet ${NFT_TABLE} ${NFT_SET}"
echo "  curl --noproxy '*' -o /dev/null -w '%{http_code}' https://www.youtube.com/generate_204"
echo "Список доменов: $DOMAINS_FILE (правка → sudo bash scripts/15_domain-split.sh)"
echo "Исключения (всегда напрямую): $EXCLUDE_FILE (GLM-кластер и т.п.)"
echo "Статус:  sudo bash scripts/15_domain-split.sh --status"
echo "Мониторинг: vpn monitor (строка amneziawg: ok (domain-split))"
echo "Демонтаж: sudo bash scripts/15_domain-split.sh --off"
echo -e "${C_GREEN}=============================================================${C_RESET}"
