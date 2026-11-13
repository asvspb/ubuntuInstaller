#!/usr/bin/env bash
# bench-cdn-mux.sh — CDN baseline: TTFB + bulk + контроль (SPEC-20261113 WP-3).
#
# Имя файла историческое: раньше это был A/B mux/nomux. Mux через Cloudflare
# отклонён (TEST-20261113-cdn-mux: 1/5 успешных против 5/5 без mux), поэтому
# режим mux и флаг --mux удалены; скрипт меряет один канал — CDN как есть.
#
# Порядок:
#   1. КОНТРОЛЬ: 3× прямой TLS к https://asvpersonal.ru/ (без socks),
#      печатается p50 time_appconnect. Если p50 > 2 с — предупреждение:
#      возможен TSPU-эпизод, результаты туннеля могут быть искажены.
#   2. vpn up <profile> → SMOKE: один curl через socks (--max-time 8).
#      Не 2xx/3xx → «канал не поднялся/не отвечает», vpn reset, exit 1
#      (не тратим N×30 с на заведомо мёртвый канал).
#   3. BASELINE: N×(TTFB example.com, 1 МБ OVH) серийно, пауза ≥1 с (И26).
#   4. vpn reset; trap снимает профиль в любом случае.
#
# Серийно с паузами ≥1 с между пробами (И26 — серийные пробы). В конце
# `vpn reset`: профиль/юнит/socks-порт гарантированно сняты, ничего
# «включённого» не остаётся. Системный прокси не трогаем: если включён
# sysproxy auto-follow — vpn up переведёт его на CDN-порт, верните руками
# `vpn sysproxy on <порт>` после прогона.
#
# Использование: bench-cdn-mux.sh [profile] [socks_port]
#   profile    — CDN-профиль (по умолчанию cdn-home)
#   socks_port — его socks-порт (по умолчанию 2081)
#   SAMPLES=N  — число baseline-проб (по умолчанию 7; для короткого прогона 2)

set -u

PROFILE="${1:-cdn-home}"
SOCKS_PORT="${2:-2081}"
SAMPLES="${SAMPLES:-7}"
if ! [[ "$SAMPLES" =~ ^[0-9]+$ ]] || [ "$SAMPLES" -lt 1 ]; then
    printf 'ОШИБКА: SAMPLES должно быть целым ≥ 1 (сейчас: %s)\n' "$SAMPLES" >&2
    exit 1
fi

TTFB_URL="https://example.com/"
BULK_URL="https://proof.ovh.net/files/1Mb.dat"
BULK_TIMEOUT=30
PAUSE=1.5   # пауза между пробами, с (≥1 с — И26)

CONTROL_URL="https://asvpersonal.ru/"
CONTROL_SAMPLES=3
CONTROL_TIMEOUT=10
CONTROL_WARN=2   # p50 прямого TLS выше этого, с — подозрение на TSPU-эпизод

SMOKE_URL="https://example.com/"
SMOKE_TIMEOUT=8

SMOKE_CODE="—"
CONTROL_FILE="/tmp/bench-cdn-control.txt"
TTFB_FILE="/tmp/bench-cdn-ttfb.txt"
BULK_FILE="/tmp/bench-cdn-bulk.txt"

say()  { printf '\n=== %s ===\n' "$*"; }
die()  { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

# Перцентили считаются только по успешным пробам: строки "timeout" (проба не
# прошла — клиент умер/CF не ответил) исключаются, иначе p50 занижается нулями.
p50()  { grep -v timeout "$1" | sort -n | awk '{a[NR]=$1} END{if(NR)print a[int((NR+1)/2)]}'; }
p95()  { grep -v timeout "$1" | sort -n | awk '{a[NR]=$1} END{if(NR)print a[int(NR*0.95+0.5)]}'; }
fails() { grep -c timeout "$1" || true; }

run_control() {
    say "Контроль: прямой TLS к $CONTROL_URL (без socks)"
    local i value
    : > "$CONTROL_FILE"
    for i in $(seq 1 "$CONTROL_SAMPLES"); do
        value=$(curl -sL -o /dev/null --max-time "$CONTROL_TIMEOUT" \
                -w '%{time_appconnect}' "$CONTROL_URL" 2>/dev/null || echo "")
        if [ -n "$value" ] && awk -v t="$value" 'BEGIN{exit !(t>0)}'; then
            echo "$value" >> "$CONTROL_FILE"
            printf '  контроль #%d: TLS %s с\n' "$i" "$value"
        else
            echo "timeout" >> "$CONTROL_FILE"
            printf '  контроль #%d: ФЕЙЛ (прямой TLS не установился)\n' "$i"
        fi
        [ "$i" -lt "$CONTROL_SAMPLES" ] && sleep "$PAUSE"
    done
    CONTROL_P50=$(p50 "$CONTROL_FILE")
    if [ -z "$CONTROL_P50" ]; then
        printf 'ПРЕДУПРЕЖДЕНИЕ: контроль не измерился (прямой TLS не отвечает) — возможен TSPU-эпизод, результаты туннеля могут быть искажены\n' >&2
    elif awk -v t="$CONTROL_P50" -v w="$CONTROL_WARN" 'BEGIN{exit !(t>w)}'; then
        printf 'ПРЕДУПРЕЖДЕНИЕ: контроль p50 %s с > %s с — возможен TSPU-эпизод, результаты туннеля могут быть искажены\n' \
            "$CONTROL_P50" "$CONTROL_WARN" >&2
    fi
}

run_smoke() {
    say "Smoke: curl через socks 127.0.0.1:${SOCKS_PORT} ($SMOKE_URL)"
    local code
    code=$(curl -s -o /dev/null --max-time "$SMOKE_TIMEOUT" \
                -w '%{http_code}' --socks5-hostname "127.0.0.1:${SOCKS_PORT}" \
                "$SMOKE_URL" 2>/dev/null)
    code="${code:-000}"
    SMOKE_CODE="$code"
    case "$code" in
        2??|3??)
            printf '  smoke: HTTP %s — канал отвечает\n' "$code"
            return 0
            ;;
        *)
            printf '  smoke: HTTP %s — канал не поднялся/не отвечает\n' "$code" >&2
            return 1
            ;;
    esac
}

command -v vpn >/dev/null || die "vpn не найден в PATH"
command -v curl >/dev/null || die "curl не найден"

# trap: профиль снимается при любом выходе (smoke-fail, Ctrl-C, ошибка)
trap 'vpn down >/dev/null 2>&1 || true' EXIT

say "CDN baseline: профиль=$PROFILE порт=$SOCKS_PORT проб=$SAMPLES"
date '+начато: %Y-%m-%d %H:%M:%S'

run_control

say "Поднимаю профиль: vpn up $PROFILE"
vpn down >/dev/null 2>&1 || true
if ! vpn up "$PROFILE"; then
    printf 'ОШИБКА: vpn up %s не поднялся; убираю состояние (vpn reset)\n' \
        "$PROFILE" >&2
    vpn reset >/dev/null 2>&1 || true
    exit 1
fi
sleep 3   # дать клиенту забиндить socks и закрыть TLS

if ! run_smoke; then
    printf 'ОШИБКА: канал не поднялся/не отвечает — baseline не запускаю\n' >&2
    vpn reset >/dev/null 2>&1 || true
    exit 1
fi

say "Baseline: TTFB $TTFB_URL + bulk $BULK_URL"
: > "$TTFB_FILE"
: > "$BULK_FILE"
for i in $(seq 1 "$SAMPLES"); do
    ttfb=$(curl -sL -o /dev/null --max-time 30 -w '%{time_starttransfer}' \
           --socks5-hostname "127.0.0.1:${SOCKS_PORT}" "$TTFB_URL" 2>/dev/null || echo "")
    if [ -n "$ttfb" ] && awk -v t="$ttfb" 'BEGIN{exit !(t>0)}'; then
        echo "$ttfb" >> "$TTFB_FILE"
        printf '  ttfb #%d: %s с\n' "$i" "$ttfb"
    else
        echo "timeout" >> "$TTFB_FILE"
        printf '  ttfb #%d: ФЕЙЛ (клиент умер или CF не ответил)\n' "$i"
    fi
    sleep "$PAUSE"

    # `_` отбрасывает остаток строки: при таймауте curl печатает -w-хвост без
    # перевода строки и `|| echo "0 0"` приклеивает второй ответ ("30.0 0"),
    # из-за чего elapsed ранее получал лишнее поле.
    read -r size elapsed _ < <(curl -sL -o /dev/null --max-time "$BULK_TIMEOUT" \
        -w '%{size_download} %{time_total}' \
        --socks5-hostname "127.0.0.1:${SOCKS_PORT}" "$BULK_URL" 2>/dev/null || echo "0 0")
    if [ "${size:-0}" -gt 0 ] && [ "${elapsed:-0}" != "0" ]; then
        mbps=$(awk -v s="$size" -v t="$elapsed" 'BEGIN{printf "%.2f", s*8/t/1000000}')
    else
        mbps="timeout"
    fi
    echo "$mbps" >> "$BULK_FILE"
    printf '  bulk #%d: %s байт за %s с = %s\n' "$i" "$size" "$elapsed" "$mbps"
    sleep "$PAUSE"
done

vpn down >/dev/null 2>&1 || true

# финальная уборка: reset гасит юниты/интерфейсы, ничего не остаётся включённым
say "Уборка: vpn reset"
vpn reset || true

say "Итог (p50/p95 — nearest-rank по успешным пробам; фейлы исключены)"
printf 'контроль p50 TLS: %s с (порог %s с)\n' "${CONTROL_P50:-—}" "$CONTROL_WARN"
printf 'smoke: HTTP %s\n' "${SMOKE_CODE:-ok}"
printf '%-10s %-14s %-14s %-22s %-8s %-8s\n' 'канал' 'ttfb p50, с' 'ttfb p95, с' \
    'bulk медиана, Мбит/с' 'ttfb fail' 'bulk fail'
printf '%-10s %-14s %-14s %-22s %-8s %-8s\n' "$PROFILE" \
    "$(p50 "$TTFB_FILE")" "$(p95 "$TTFB_FILE")" "$(p50 "$BULK_FILE")" \
    "$(fails "$TTFB_FILE")" "$(fails "$BULK_FILE")"

date '+закончено: %Y-%m-%d %H:%M:%S'
say "Сырые файлы: $CONTROL_FILE $TTFB_FILE $BULK_FILE"
