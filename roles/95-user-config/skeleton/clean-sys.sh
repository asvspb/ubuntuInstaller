#!/usr/bin/env bash
set -euo pipefail

# Конфигурация (можно переопределить через переменные окружения)
CACHE_DIR="${CACHE_DIR:-$HOME/.cache}"
LOG_PREFIX="[cache-clean]"
HF_MAX_AGE_DAYS="${HF_MAX_AGE_DAYS:-0}" # 0 = удалить ВСЁ содержимое ~/.cache/huggingface
CHROME_CLEAN="${CHROME_CLEAN:-1}"       # 1=чистить ~/.cache/google-chrome (если не запущен Chrome)
PUPPETEER_CLEAN="${PUPPETEER_CLEAN:-1}"
PLAYWRIGHT_CLEAN="${PLAYWRIGHT_CLEAN:-1}"
PIP_PURGE="${PIP_PURGE:-1}"
THUMBNAILS_CLEAN="${THUMBNAILS_CLEAN:-1}"
MESA_CLEAN="${MESA_CLEAN:-1}"
UV_CLEAN="${UV_CLEAN:-1}"
BRAVE_CLEAN="${BRAVE_CLEAN:-0}" # по желанию
DRY_RUN="${DRY_RUN:-0}"

# Новые опции для расширенной безопасной очистки (по умолчанию включены только безопасные шаги)
CLEAN_CHROME_MODEL="${CLEAN_CHROME_MODEL:-1}"       # ~/.config/google-chrome/OptGuideOnDeviceModel -> корзина
CLEAN_VSCODE_CACHES="${CLEAN_VSCODE_CACHES:-1}"     # ~/.config/Code/{WebStorage,Cache,CachedData,CachedExtensionVSIXs}
CLEAN_PYPPETEER_SHARE="${CLEAN_PYPPETEER_SHARE:-1}" # ~/.local/share/pyppeteer
EMPTY_TRASH="${EMPTY_TRASH:-1}"                     # очистить корзину в конце
JOURNALCTL_VACUUM="${JOURNALCTL_VACUUM:-1}"         # попытаться сжать журналы (sudo -n)
JOURNALCTL_VACUUM_SIZE="${JOURNALCTL_VACUUM_SIZE:-200M}"
SNAP_CLEAN_DISABLED="${SNAP_CLEAN_DISABLED:-1}" # удалить отключённые ревизии snap (sudo -n)
UNINSTALL_ML="${UNINSTALL_ML:-0}"               # Удалить тяжёлые ML-пакеты из pip (torch/nvidia/...) — ТОЛЬКО по явному включению
VACUUM_SQLITE="${VACUUM_SQLITE:-1}"
SQLITE_DB_PATHS="${SQLITE_DB_PATHS:-$HOME/Dev/my-coding/warandpeace/database/articles.db}"
ALLOW_RM="${ALLOW_RM:-0}" # если нет gio и нужно удалять без корзины (по умолчанию — безопасно: пропуск)

# Дополнительные (опциональные) системные чистки
APT_CLEAN="${APT_CLEAN:-0}"
APT_AUTOCLEAN="${APT_AUTOCLEAN:-0}"
APT_AUTOREMOVE="${APT_AUTOREMOVE:-0}"

CLEAN_TMP="${CLEAN_TMP:-1}"
TMP_MAX_AGE_DAYS="${TMP_MAX_AGE_DAYS:-7}"
CLEAN_VARTMP="${CLEAN_VARTMP:-1}"
VARTMP_MAX_AGE_DAYS="${VARTMP_MAX_AGE_DAYS:-7}"

CLEAN_SNAPD_CACHE="${CLEAN_SNAPD_CACHE:-1}"
CLEAN_VAR_CRASH="${CLEAN_VAR_CRASH:-1}"

CLEAN_OLD_LOG_GZ="${CLEAN_OLD_LOG_GZ:-1}"
LOG_GZ_RETENTION_DAYS="${LOG_GZ_RETENTION_DAYS:-14}"

SNAP_SET_RETAIN="${SNAP_SET_RETAIN:-0}"
SNAP_RETAIN_N="${SNAP_RETAIN_N:-2}"

NPM_CACHE_VERIFY="${NPM_CACHE_VERIFY:-1}"
NPM_CACHE_CLEAN="${NPM_CACHE_CLEAN:-0}"

FLATPAK_UNUSED="${FLATPAK_UNUSED:-0}"

DOCKER_PRUNE="${DOCKER_PRUNE:-0}"
DOCKER_PRUNE_VOLUMES="${DOCKER_PRUNE_VOLUMES:-0}"
# Расширенная безопасная очистка Docker (тонкая настройка)
DOCKER_SAFE_PRUNE="${DOCKER_SAFE_PRUNE:-1}"
DOCKER_PRUNE_CONTAINERS_UNTIL="${DOCKER_PRUNE_CONTAINERS_UNTIL:-24h}"
DOCKER_PRUNE_IMAGES_UNTIL="${DOCKER_PRUNE_IMAGES_UNTIL:-168h}"
DOCKER_PRUNE_BUILDER_UNTIL="${DOCKER_PRUNE_BUILDER_UNTIL:-168h}"
DOCKER_DEEP_PRUNE="${DOCKER_DEEP_PRUNE:-0}"
DOCKER_DEEP_PRUNE_UNTIL="${DOCKER_DEEP_PRUNE_UNTIL:-336h}"

log() { printf '%s %s %s\n' "$(date '+%F %T')" "$LOG_PREFIX" "$*"; }
size_of() { du -sh "$1" 2>/dev/null | awk '{print $1}'; }

# Лок-файл, чтобы не было параллельных запусков
LOCKFILE="$CACHE_DIR/.cache-clean-weekly.lock"
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  log "Уже запущено — выхожу"
  exit 0
fi

# Вспомогательные функции
proc_running() { pgrep -x "$1" >/dev/null 2>&1; }

trash_path() {
  # безопасное перемещение в корзину, либо пропуск, если нет gio
  local p="$1"
  [ -e "$p" ] || {
    log "skip: нет $p"
    return 0
  }
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: переместил бы в корзину: $p"
    return 0
  fi
  if command -v gio >/dev/null 2>&1; then
    gio trash "$p" || log "warn: не удалось переместить в корзину: $p"
  else
    if [ "$ALLOW_RM" = "1" ]; then
      rm -rf -- "$p" || true
    else
      log "skip: нет gio (корзина), ALLOW_RM!=1, пропускаю $p"
    fi
  fi
}

empty_trash() {
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: очистил бы корзину"
    return 0
  fi
  if command -v gio >/dev/null 2>&1; then
    gio trash --empty || true
  else
    log "skip: нет gio для очистки корзины"
  fi
}

purge_dir_contents() {
  local dir="$1"
  [ -d "$dir" ] || {
    log "skip (нет каталога): $dir"
    return 0
  }
  case "$dir" in
  "$HOME/.cache"/*) ;; # защита от случайного rm вне ~/.cache
  *)
    log "ОТКАЗ: $dir не под $HOME/.cache"
    return 1
    ;;
  esac

  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: удалил бы содержимое $dir"
    return 0
  fi

  # Удаляем всё верхнего уровня (и файлы, и папки)
  find "$dir" -mindepth 1 -maxdepth 1 -print0 | xargs -0 -r rm -rf -- || true
}

clean_dir_if_idle() { # name dir proc_hint
  local name="$1" dir="$2" proc="$3"
  [ -d "$dir" ] || {
    log "skip: нет $dir"
    return 0
  }
  if [ -n "$proc" ] && proc_running "$proc"; then
    log "skip ($name): процесс '$proc' активен"
    return 0
  fi
  log "Очистка $name: $dir"
  purge_dir_contents "$dir" || true
}

clean_huggingface() {
  local dir="$CACHE_DIR/huggingface"
  [ -d "$dir" ] || {
    log "skip: нет $dir"
    return 0
  }

  if [ "$HF_MAX_AGE_DAYS" = "0" ]; then
    log "Очистка huggingface ЦЕЛИКОМ"
    purge_dir_contents "$dir" || true
  else
    log "Очистка huggingface: файлы старше $HF_MAX_AGE_DAYS дн."
    if [ "$DRY_RUN" = "1" ]; then
      find "$dir" -type f -mtime +"$HF_MAX_AGE_DAYS" -print | head -n 20 |
        sed 's/^/DRY-RUN: удалил бы: /' || true
    else
      find "$dir" -type f -mtime +"$HF_MAX_AGE_DAYS" -delete || true
      find "$dir" -type d -empty -delete || true
    fi
  fi
}

clean_pip() {
  [ "$PIP_PURGE" = "1" ] || return 0
  if command -v pip3 >/dev/null 2>&1; then
    if [ "$DRY_RUN" = "1" ]; then
      log "DRY-RUN: pip3 cache purge"
    else
      log "pip3 cache purge"
      pip3 cache purge || true
    fi
  elif command -v pip >/dev/null 2>&1; then
    if [ "$DRY_RUN" = "1" ]; then
      log "DRY-RUN: pip cache purge"
    else
      log "pip cache purge"
      pip cache purge || true
    fi
  else
    log "skip: pip не найден"
  fi
}

clean_chrome_model() {
  [ "$CLEAN_CHROME_MODEL" = "1" ] || return 0
  local dir="$HOME/.config/google-chrome/OptGuideOnDeviceModel"
  if proc_running chrome || proc_running chromium || proc_running "google-chrome"; then
    log "skip: chrome/chromium запущен — пропускаю удаление модели"
    return 0
  fi
  log "Chrome On-Device model -> корзина: $dir"
  trash_path "$dir"
}

clean_vscode_caches() {
  [ "$CLEAN_VSCODE_CACHES" = "1" ] || return 0
  local base="$HOME/.config/Code"
  for d in "WebStorage" "Cache" "CachedData" "CachedExtensionVSIXs"; do
    trash_path "$base/$d"
  done
}

clean_pyppeteer_share() {
  [ "$CLEAN_PYPPETEER_SHARE" = "1" ] || return 0
  trash_path "$HOME/.local/share/pyppeteer"
}

vacuum_journal() {
  [ "$JOURNALCTL_VACUUM" = "1" ] || return 0
  if sudo -n true 2>/dev/null; then
    if [ "$DRY_RUN" = "1" ]; then
      log "DRY-RUN: sudo journalctl --vacuum-size=$JOURNALCTL_VACUUM_SIZE"
    else
      log "journalctl vacuum до $JOURNALCTL_VACUUM_SIZE"
      sudo journalctl --vacuum-size="$JOURNALCTL_VACUUM_SIZE" || log "warn: journalctl vacuum не удался"
    fi
  else
    log "skip: требуется sudo для journalctl vacuum"
  fi
}

snap_cleanup_disabled() {
  [ "$SNAP_CLEAN_DISABLED" = "1" ] || return 0
  command -v snap >/dev/null 2>&1 || {
    log "skip: snap не найден"
    return 0
  }
  if sudo -n true 2>/dev/null; then
    if [ "$DRY_RUN" = "1" ]; then
      snap list --all | awk '/disabled/{print "DRY-RUN: snap remove", $1, "--revision=" $3}' || true
    else
      snap list --all | awk '/disabled/{print $1, $3}' | while read -r pkg rev; do
        [ -n "$pkg" ] && [ -n "$rev" ] || continue
        log "snap remove $pkg --revision=$rev"
        sudo snap remove "$pkg" --revision="$rev" || true
      done
    fi
  else
    log "skip: требуется sudo для snap remove"
  fi
}

uninstall_ml_packages() {
  [ "$UNINSTALL_ML" = "1" ] || return 0
  command -v python3 >/dev/null 2>&1 || {
    log "skip: нет python3"
    return 0
  }
  local pkgs
  pkgs=$(python3 -m pip freeze 2>/dev/null | awk -F'==' 'BEGIN{IGNORECASE=1} /^torch|^triton|^bitsandbytes|^nvidia-|^cuda|^cudnn|^cublas|^cusparselt/{print $1}' | sort -u | tr '\n' ' ') || true
  if [ -z "${pkgs:-}" ]; then
    log "ML-пакеты не найдены"
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: python3 -m pip uninstall -y ${pkgs}"
  else
    log "Удаление ML-пакетов: ${pkgs}"
    python3 -m pip uninstall -y ${pkgs} || true
  fi
}

vacuum_sqlite() {
  [ "$VACUUM_SQLITE" = "1" ] || return 0
  command -v sqlite3 >/dev/null 2>&1 || {
    log "skip: нет sqlite3"
    return 0
  }
  local saved=0
  IFS=$'\n'
  for db in $SQLITE_DB_PATHS; do
    [ -f "$db" ] || {
      log "skip: нет БД $db"
      continue
    }
    local before after
    before=$(stat -c %s "$db" 2>/dev/null || echo 0)
    if [ "$DRY_RUN" = "1" ]; then
      log "DRY-RUN: sqlite3 $db 'VACUUM;'"
    else
      sqlite3 "$db" 'VACUUM;' 2>/dev/null || true
    fi
    after=$(stat -c %s "$db" 2>/dev/null || echo 0)
    if [ "$after" -lt "$before" ]; then
      saved=$((saved + (before - after)))
      log "VACUUM: $db −$((before - after)) bytes"
    fi
  done
  unset IFS
}

# Очистка временных директорий
cleanup_tmp_path() {
  local path="$1" days="$2"
  if [ "$DRY_RUN" = "1" ]; then
    if sudo -n true 2>/dev/null; then
      log "DRY-RUN: sudo find $path -xdev -type f -mtime +$days -delete && sudo find $path -xdev -type d -empty -delete"
    else
      log "DRY-RUN: find $path -xdev -type f -user $(id -u) -mtime +$days -delete && find $path -xdev -type d -user $(id -u) -empty -delete"
    fi
    return 0
  fi
  if sudo -n true 2>/dev/null; then
    sudo find "$path" -xdev -type f -mtime +"$days" -delete || true
    sudo find "$path" -xdev -type d -empty -delete || true
  else
    find "$path" -xdev -type f -user "$(id -u)" -mtime +"$days" -delete || true
    find "$path" -xdev -type d -user "$(id -u)" -empty -delete || true
  fi
}

apt_maintenance() {
  if [ "$APT_CLEAN" != "1" ] && [ "$APT_AUTOCLEAN" != "1" ] && [ "$APT_AUTOREMOVE" != "1" ]; then
    return 0
  fi
  if ! sudo -n true 2>/dev/null; then
    log "skip: требуется sudo для apt maintenance"
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    [ "$APT_CLEAN" = "1" ] && log "DRY-RUN: sudo apt clean"
    [ "$APT_AUTOCLEAN" = "1" ] && log "DRY-RUN: sudo apt autoclean"
    [ "$APT_AUTOREMOVE" = "1" ] && log "DRY-RUN: sudo apt autoremove --purge -y"
  else
    [ "$APT_CLEAN" = "1" ] && sudo apt clean || true
    [ "$APT_AUTOCLEAN" = "1" ] && sudo apt autoclean || true
    [ "$APT_AUTOREMOVE" = "1" ] && sudo apt autoremove --purge -y || true
  fi
}

snap_set_retain() {
  [ "$SNAP_SET_RETAIN" = "1" ] || return 0
  command -v snap >/dev/null 2>&1 || {
    log "skip: snap не найден"
    return 0
  }
  if ! sudo -n true 2>/dev/null; then
    log "skip: требуется sudo для snap set system refresh.retain=$SNAP_RETAIN_N"
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: sudo snap set system refresh.retain=$SNAP_RETAIN_N"
  else
    sudo snap set system refresh.retain="$SNAP_RETAIN_N" || true
  fi
}

clean_snapd_cache() {
  [ "$CLEAN_SNAPD_CACHE" = "1" ] || return 0
  if ! sudo -n true 2>/dev/null; then
    log "skip: требуется sudo для очистки /var/cache/snapd"
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: sudo rm -rf /var/cache/snapd/*"
  else
    sudo rm -rf /var/cache/snapd/* || true
  fi
}

clean_var_crash() {
  [ "$CLEAN_VAR_CRASH" = "1" ] || return 0
  if ! sudo -n true 2>/dev/null; then
    log "skip: требуется sudo для очистки /var/crash"
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: sudo rm -f /var/crash/*"
  else
    sudo rm -f /var/crash/* || true
  fi
}

clean_old_log_gz() {
  [ "$CLEAN_OLD_LOG_GZ" = "1" ] || return 0
  if ! sudo -n true 2>/dev/null; then
    log "skip: требуется sudo для удаления старых .gz логов в /var/log"
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: sudo find /var/log -type f -name '*.gz' -mtime +$LOG_GZ_RETENTION_DAYS -delete"
  else
    sudo find /var/log -type f -name '*.gz' -mtime +"$LOG_GZ_RETENTION_DAYS" -delete || true
  fi
}

npm_cache_ops() {
  if command -v npm >/dev/null 2>&1; then
    if [ "$NPM_CACHE_VERIFY" = "1" ]; then
      if [ "$DRY_RUN" = "1" ]; then log "DRY-RUN: npm cache verify"; else npm cache verify || true; fi
    fi
    if [ "$NPM_CACHE_CLEAN" = "1" ]; then
      if [ "$DRY_RUN" = "1" ]; then log "DRY-RUN: npm cache clean --force"; else npm cache clean --force || true; fi
    fi
  else
    if [ "$NPM_CACHE_VERIFY" = "1" ] || [ "$NPM_CACHE_CLEAN" = "1" ]; then
      log "skip: npm не найден"
    fi
  fi
}

flatpak_unused() {
  [ "$FLATPAK_UNUSED" = "1" ] || return 0
  if ! command -v flatpak >/dev/null 2>&1; then
    log "skip: flatpak не найден"
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: flatpak uninstall --unused -y"
  else
    flatpak uninstall --unused -y || true
  fi
}

docker_prune() {
  [ "$DOCKER_PRUNE" = "1" ] || return 0
  if ! command -v docker >/dev/null 2>&1; then
    log "skip: docker не найден"
    return 0
  fi
  local vols=""
  [ "$DOCKER_PRUNE_VOLUMES" = "1" ] && vols=" --volumes"
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: docker system prune -af${vols}"
  else
    docker system prune -af${vols} || true
  fi
}

docker_prune_safe() {
  [ "$DOCKER_SAFE_PRUNE" = "1" ] || return 0
  if ! command -v docker >/dev/null 2>&1; then
    log "skip: docker не найден (safe prune)"
    return 0
  fi
  local cont_until="$DOCKER_PRUNE_CONTAINERS_UNTIL"
  local img_until="$DOCKER_PRUNE_IMAGES_UNTIL"
  local builder_until="$DOCKER_PRUNE_BUILDER_UNTIL"

  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: docker container prune -f --filter until=${cont_until}"
    log "DRY-RUN: docker image prune -f"
    log "DRY-RUN: docker builder prune -af --filter until=${builder_until}"
  else
    docker container prune -f --filter "until=${cont_until}" >/dev/null 2>&1 || true
    docker image prune -f >/dev/null 2>&1 || true
    docker builder prune -af --filter "until=${builder_until}" >/dev/null 2>&1 || true
  fi

  if [ "$DOCKER_DEEP_PRUNE" = "1" ]; then
    local deep_until="$DOCKER_DEEP_PRUNE_UNTIL"
    if [ "$DRY_RUN" = "1" ]; then
      log "DRY-RUN: docker system prune -af --volumes --filter until=${deep_until}"
    else
      docker system prune -af --volumes --filter "until=${deep_until}" >/dev/null 2>&1 || true
    fi
  fi
}

clean_tmp_all() { [ "$CLEAN_TMP" = "1" ] && cleanup_tmp_path "/tmp" "$TMP_MAX_AGE_DAYS"; }
clean_vartmp_all() { [ "$CLEAN_VARTMP" = "1" ] && cleanup_tmp_path "/var/tmp" "$VARTMP_MAX_AGE_DAYS"; }

human() { numfmt --to=iec --suffix=B 2>/dev/null; }
free_bytes() { df -B1 --output=avail / | tail -1 | tr -d ' '; }

log "Начало. Размер $CACHE_DIR = $(size_of "$CACHE_DIR")"
BEFORE_FREE=$(free_bytes)
log "Свободно ДО: $(printf "%s" "$BEFORE_FREE" | human)"

# Основные чистки
clean_pip
clean_huggingface
[ "$CHROME_CLEAN" = "1" ] && clean_dir_if_idle "google-chrome" "$CACHE_DIR/google-chrome" "chrome"
[ "$PUPPETEER_CLEAN" = "1" ] && clean_dir_if_idle "puppeteer" "$CACHE_DIR/puppeteer" ""
[ "$PLAYWRIGHT_CLEAN" = "1" ] && clean_dir_if_idle "ms-playwright-go" "$CACHE_DIR/ms-playwright-go" ""
[ "$THUMBNAILS_CLEAN" = "1" ] && purge_dir_contents "$CACHE_DIR/thumbnails"
[ "$MESA_CLEAN" = "1" ] && purge_dir_contents "$CACHE_DIR/mesa_shader_cache"
[ "$UV_CLEAN" = "1" ] && purge_dir_contents "$CACHE_DIR/uv"
[ "$BRAVE_CLEAN" = "1" ] && clean_dir_if_idle "BraveSoftware" "$CACHE_DIR/BraveSoftware" "brave"

# Новые шаги (повторяют проделанные нами операции)
clean_chrome_model
clean_vscode_caches
clean_pyppeteer_share

# Системные шаги (по возможности без пароля sudo)
vacuum_journal
clean_old_log_gz
snap_set_retain
snap_cleanup_disabled
clean_snapd_cache
clean_var_crash
apt_maintenance
clean_tmp_all
clean_vartmp_all

# Пакетные менеджеры/платформы (опционально)
npm_cache_ops
flatpak_unused
# Сначала безопасная чистка (тонкая), затем — опциональный тотальный prune
docker_prune_safe

docker_prune

# Удаление ML-пакетов и обслуживание БД (опционально)
uninstall_ml_packages
vacuum_sqlite

# Очистить корзину, чтобы фактически освободить место
[ "$EMPTY_TRASH" = "1" ] && empty_trash

AFTER_FREE=$(free_bytes)
DELTA=$((AFTER_FREE - BEFORE_FREE))
log "Свободно ПОСЛЕ: $(printf "%s" "$AFTER_FREE" | human) (Δ=$(printf "%s" "$DELTA" | human))"

# Дополнительно мелкие кэши (если есть)
purge_dir_contents "$CACHE_DIR/node-gyp" || true
purge_dir_contents "$CACHE_DIR/yarn" || true
purge_dir_contents "$CACHE_DIR/vscode-ripgrep" || true

log "Готово. Размер $CACHE_DIR = $(size_of "$CACHE_DIR")"
