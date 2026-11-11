#!/usr/bin/env bash
# =============================================================================
#  20_vds-migrate.sh — перенос состояния VDS Orchestrator на новый сервер
#  Часть ubuntuInstaller. Пара к 11_vds-deploy.sh (см. vds/docs/MIGRATE-VDS.md)
#
#  Запускается на ЛОКАЛЬНОЙ машине:
#    bash 20_vds-migrate.sh root@старый root@новый          # план (dry-run)
#    bash 20_vds-migrate.sh root@старый root@новый --apply  # выполнить
#
#  Переносит ТОЛЬКО state и секреты (gateway/state.json, /etc/letsencrypt,
#  /etc/hysteria, /etc/amnezia) — код репозитория ставится отдельно
#  11_vds-deploy.sh (запустить ДО этого скрипта). Тарбол идёт через
#  локальный tmp (0600), не сервер-сервер; после переноса удаляется с обеих
#  сторон. Старый сервер только читается — миграция обратима возвратом DNS.
#
#  Деструктивные действия — только на НОВОМ сервере (распаковка поверх) и
#  только с --apply; без флага — dry-run с планом.
# =============================================================================

set -e

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
log()  { echo -e "${GREEN}[+]${NC} $*"; }
info() { echo -e "${CYAN}[i]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[x]${NC} $*" >&2; exit 1; }

OLD_HOST="${1:-}"
NEW_HOST="${2:-}"
APPLY="${3:-}"

[ -z "$OLD_HOST" ] || [ -z "$NEW_HOST" ] && {
    err "usage: $0 root@старый root@новый [--apply]"; }
[ "$OLD_HOST" != "$NEW_HOST" ] || err "старый и новый хост совпадают"

INSTALL_DIR="/opt/my-vds"
STATE="$INSTALL_DIR/gateway/state.json"
TMP_ARCHIVE="/tmp/vds-state-migrate.tgz"
REMOTE_PATHS="opt/my-vds/gateway/state.json etc/letsencrypt etc/hysteria etc/amnezia"

ssh_probe() {
    timeout 10 ssh -o ConnectTimeout=8 -o BatchMode=yes "$1" true \
        || err "ssh $1 недоступен (ключ/BatchMode)"
}

# --- 0. доступность обоих --------------------------------------------------------
info "проверка ssh: $OLD_HOST / $NEW_HOST"
ssh_probe "$OLD_HOST"
ssh_probe "$NEW_HOST"

# --- 1. инвентаризация на старом -------------------------------------------------
info "1/5: инвентаризация состояния на $OLD_HOST"
FOUND=$(ssh "$OLD_HOST" "for p in $REMOTE_PATHS; do [ -e \"/\$p\" ] && echo \"\$p\"; done")
[ -n "$FOUND" ] || err "на $OLD_HOST не найдено ни state.json, ни секретов — переносить нечего"
echo "$FOUND" | sed 's/^/    найдено: \//'

# --- 2. тарбол на старом ---------------------------------------------------------
info "2/5: сборка тарбола на $OLD_HOST"
ssh "$OLD_HOST" "cd / && tar czf $TMP_ARCHIVE $FOUND \
    && chmod 0600 $TMP_ARCHIVE && ls -lh $TMP_ARCHIVE"

# --- 3. забрать в локальный tmp ---------------------------------------------------
info "3/5: тарбол -> локальный tmp (не сервер-сервер)"
scp -q "$OLD_HOST:$TMP_ARCHIVE" "$TMP_ARCHIVE"
chmod 0600 "$TMP_ARCHIVE"
SHA_LOCAL=$(sha256sum "$TMP_ARCHIVE" | cut -d' ' -f1)

# --- 4. залить на новый + распаковка --------------------------------------------
info "4/5: $NEW_HOST <- тарбол ($(du -h "$TMP_ARCHIVE" | cut -f1))"
scp -q "$TMP_ARCHIVE" "$NEW_HOST:$TMP_ARCHIVE"
ssh "$NEW_HOST" "sha256sum $TMP_ARCHIVE | grep -q '^$SHA_LOCAL ' \
    || { echo 'checksum mismatch'; exit 1; }"
info "checksum ok: ${SHA_LOCAL:0:12}…"

if [ "$APPLY" != "--apply" ]; then
    ssh "$NEW_HOST" "rm -f $TMP_ARCHIVE"
    rm -f "$TMP_ARCHIVE"
    warn "DRY-RUN: тарбол проверен и УДАЛЕН, состояние не менялось."
    warn "Выполнить перенос: $0 $OLD_HOST $NEW_HOST --apply"
    exit 0
fi

# --- 5. --apply: распаковка поверх нового, права, рестарт ------------------------
info "5/5: --apply: распаковка на $NEW_HOST"
ssh "$NEW_HOST" "tar xzf $TMP_ARCHIVE -C / \
    && chown root:root $STATE && chmod 0600 $STATE \
    && rm -f $TMP_ARCHIVE \
    && systemctl restart vds-xray vds-hysteria2 2>/dev/null \
    && systemctl is-active vds-xray vds-hysteria2"
rm -f "$TMP_ARCHIVE"
ssh "$OLD_HOST" "rm -f $TMP_ARCHIVE"
log "состояние перенесено; vds-xray/vds-hysteria2 перезапущены"

cat <<NEXT
=============================================================
[OK] Перенос завершён. Дальше (см. vds/docs/MIGRATE-VDS.md):
  - DNS: A-запись домена -> IP нового сервера (TTL 300)
  - если DNS уже смотрит на новый: vds acme issue --domain <домен>
  - ноутбук: vpn profile pull -H $NEW_HOST
  - AWG-конфиги клиентов: Endpoint -> <новый IP>:51825
  - старый сервер: погасить через 1-2 дня (DNS/кэши)
=============================================================
NEXT
