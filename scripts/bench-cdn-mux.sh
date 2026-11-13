#!/usr/bin/env bash
# bench-cdn-mux.sh — A/B-бенчмарк Mux на CDN-профиле (SPEC-20261113 WP-3).
#
# nomux:  vpn up <profile>; 7×(TTFB example.com, 1 МБ OVH); vpn down
# mux:    vpn up <profile> --mux; то же
# Итог:   сырые замеры + p50/p95 TTFB + медианный throughput обоих режимов.
#
# Серийно с паузами ≥1 с между пробами (И26 — серийные пробы). В конце
# `vpn reset`: профиль/юнит/socks-порт гарантированно сняты, ничего
# «включённого» не остаётся. Системный прокси не трогаем: если включён
# sysproxy auto-follow — bench его переведёт на CDN-порт, верните руками
# `vpn sysproxy on <порт>` после прогона.
#
# Использование: bench-cdn-mux.sh [profile] [socks_port]
#   profile    — CDN-профиль (по умолчанию cdn-home)
#   socks_port — его socks-порт (по умолчанию 2081)

set -u

PROFILE="${1:-cdn-home}"
SOCKS_PORT="${2:-2081}"
SAMPLES=7
TTFB_URL="https://example.com/"
BULK_URL="https://proof.ovh.net/files/1Mb.dat"
BULK_TIMEOUT=30
PAUSE=1.5   # пауза между пробами, с (≥1 с — И26)

say()  { printf '\n=== %s ===\n' "$*"; }
die()  { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

# Перцентили считаются только по успешным пробам: строки "timeout" (проба не
# прошла — клиент умер/CF не ответил) исключаются, иначе p50 занижается нулями.
p50()  { grep -v timeout "$1" | sort -n | awk '{a[NR]=$1} END{if(NR)print a[int((NR+1)/2)]}'; }
p95()  { grep -v timeout "$1" | sort -n | awk '{a[NR]=$1} END{if(NR)print a[int(NR*0.95+0.5)]}'; }
fails() { grep -c timeout "$1" || true; }

run_mode() {
    local mode="$1" mux_flag="${2:-}"
    say "Режим: $mode (vpn up $PROFILE $mux_flag)"
    vpn down >/dev/null 2>&1 || true
    vpn up "$PROFILE" $mux_flag || die "vpn up $PROFILE $mux_flag не поднялся"
    sleep 3   # дать клиенту забиндить socks и закрыть TLS

    local i ttfb size elapsed mbps
    : > "/tmp/bench-cdn-${mode}-ttfb.txt"
    : > "/tmp/bench-cdn-${mode}-bulk.txt"
    for i in $(seq 1 "$SAMPLES"); do
        ttfb=$(curl -sL -o /dev/null --max-time 30 -w '%{time_starttransfer}' \
               --socks5-hostname "127.0.0.1:${SOCKS_PORT}" "$TTFB_URL" || echo "")
        if [ -n "$ttfb" ] && awk -v t="$ttfb" 'BEGIN{exit !(t>0)}'; then
            echo "$ttfb" >> "/tmp/bench-cdn-${mode}-ttfb.txt"
            printf '  ttfb #%d: %s с\n' "$i" "$ttfb"
        else
            echo "timeout" >> "/tmp/bench-cdn-${mode}-ttfb.txt"
            printf '  ttfb #%d: ФЕЙЛ (клиент умер или CF не ответил)\n' "$i"
        fi
        sleep "$PAUSE"

        read -r size elapsed < <(curl -sL -o /dev/null --max-time "$BULK_TIMEOUT" \
            -w '%{size_download} %{time_total}' \
            --socks5-hostname "127.0.0.1:${SOCKS_PORT}" "$BULK_URL" || echo "0 0")
        if [ "${size:-0}" -gt 0 ] && [ "${elapsed:-0}" != "0" ]; then
            mbps=$(awk -v s="$size" -v t="$elapsed" 'BEGIN{printf "%.2f", s*8/t/1000000}')
        else
            mbps="timeout"
        fi
        echo "$mbps" >> "/tmp/bench-cdn-${mode}-bulk.txt"
        printf '  bulk #%d: %s байт за %s с = %s\n' "$i" "$size" "$elapsed" "$mbps"
        sleep "$PAUSE"
    done

    vpn down >/dev/null 2>&1 || true
    sleep 2
}

command -v vpn >/dev/null || die "vpn не найден в PATH"
command -v curl >/dev/null || die "curl не найден"

trap 'vpn down >/dev/null 2>&1' EXIT

say "Бенчмарк CDN mux A/B: профиль=$PROFILE порт=$SOCKS_PORT сэмплов=$SAMPLES"
date '+начато: %Y-%m-%d %H:%M:%S'

run_mode nomux
run_mode mux "--mux"

# финальная уборка: reset гасит юниты/интерфейсы, ничего не остаётся включённым
say "Уборка: vpn reset"
vpn reset || true

NOMUX_TTFB="/tmp/bench-cdn-nomux-ttfb.txt"; NOMUX_BULK="/tmp/bench-cdn-nomux-bulk.txt"
MUX_TTFB="/tmp/bench-cdn-mux-ttfb.txt";     MUX_BULK="/tmp/bench-cdn-mux-bulk.txt"

say "Итог (p50/p95 — nearest-rank по успешным пробам; фейлы исключены)"
printf '%-10s %-14s %-14s %-22s %-8s %-8s\n' 'режим' 'ttfb p50, с' 'ttfb p95, с' \
    'bulk медиана, Мбит/с' 'ttfb fail' 'bulk fail'
printf '%-10s %-14s %-14s %-22s %-8s %-8s\n' nomux \
    "$(p50 "$NOMUX_TTFB")" "$(p95 "$NOMUX_TTFB")" "$(p50 "$NOMUX_BULK")" \
    "$(fails "$NOMUX_TTFB")" "$(fails "$NOMUX_BULK")"
printf '%-10s %-14s %-14s %-22s %-8s %-8s\n' mux \
    "$(p50 "$MUX_TTFB")" "$(p95 "$MUX_TTFB")" "$(p50 "$MUX_BULK")" \
    "$(fails "$MUX_TTFB")" "$(fails "$MUX_BULK")"

date '+закончено: %Y-%m-%d %H:%M:%S'
say "Сырые файлы: $NOMUX_TTFB $NOMUX_BULK $MUX_TTFB $MUX_BULK"
