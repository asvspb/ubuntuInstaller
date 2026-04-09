#!/usr/bin/env bash
set -euo pipefail

# Определяем реальный HOME пользователя (даже при запуске через sudo/root)
# При sudo: $HOME=/root, $SUDO_USER=имя_пользователя
# Используем eval ~user для получения правильного пути
_real_home() {
  if [ -n "${SUDO_USER:-}" ] && [ "$(id -u)" -eq 0 ]; then
    eval echo "~${SUDO_USER}"
  elif [ -n "${SUDO_USER:-}" ]; then
    eval echo "~${SUDO_USER}"
  else
    echo "$HOME"
  fi
}
HOME="$(_real_home)"
export HOME

# Конфигурация (можно переопределить через переменные окружения)
CACHE_DIR="${CACHE_DIR:-$HOME/.cache}"
LOG_PREFIX="[cache-clean]"
HF_MAX_AGE_DAYS="${HF_MAX_AGE_DAYS:-0}"   # 0 = удалить ВСЁ содержимое ~/.cache/huggingface
CHROME_CLEAN="${CHROME_CLEAN:-1}"          # 1=чистить ~/.cache/google-chrome (если не запущен Chrome)
PUPPETEER_CLEAN="${PUPPETEER_CLEAN:-1}"
PLAYWRIGHT_CLEAN="${PLAYWRIGHT_CLEAN:-1}"
PIP_PURGE="${PIP_PURGE:-1}"
THUMBNAILS_CLEAN="${THUMBNAILS_CLEAN:-1}"
MESA_CLEAN="${MESA_CLEAN:-1}"
UV_CLEAN="${UV_CLEAN:-1}"
BRAVE_CLEAN="${BRAVE_CLEAN:-0}"            # по желанию
DRY_RUN="${DRY_RUN:-0}"

# Новые опции для расширенной безопасной очистки (по умолчанию включены только безопасные шаги)
CLEAN_CHROME_MODEL="${CLEAN_CHROME_MODEL:-1}"         # ~/.config/google-chrome/OptGuideOnDeviceModel -> корзина
CLEAN_VSCODE_CACHES="${CLEAN_VSCODE_CACHES:-1}"       # ~/.config/Code/{WebStorage,Cache,CachedData,CachedExtensionVSIXs}
CLEAN_PYPPETEER_SHARE="${CLEAN_PYPPETEER_SHARE:-1}"   # ~/.local/share/pyppeteer
EMPTY_TRASH="${EMPTY_TRASH:-1}"                        # очистить корзину в конце
JOURNALCTL_VACUUM="${JOURNALCTL_VACUUM:-1}"            # попытаться сжать журналы (sudo -n)
JOURNALCTL_VACUUM_SIZE="${JOURNALCTL_VACUUM_SIZE:-200M}"
SNAP_CLEAN_DISABLED="${SNAP_CLEAN_DISABLED:-1}"        # удалить отключённые ревизии snap (sudo -n)
UNINSTALL_ML="${UNINSTALL_ML:-0}"                      # Удалить тяжёлые ML-пакеты из pip (torch/nvidia/...) — ТОЛЬКО по явному включению
VACUUM_SQLITE="${VACUUM_SQLITE:-1}"
SQLITE_DB_PATHS="${SQLITE_DB_PATHS:-$HOME/Dev/my-coding/warandpeace/database/articles.db}"
ALLOW_RM="${ALLOW_RM:-0}"                              # если нет gio и нужно удалять без корзины (по умолчанию — безопасно: пропуск)

# Дополнительные (опциональные) системные чистки
APT_CLEAN="${APT_CLEAN:-1}"
APT_AUTOCLEAN="${APT_AUTOCLEAN:-1}"
APT_AUTOREMOVE="${APT_AUTOREMOVE:-1}"

CLEAN_TMP="${CLEAN_TMP:-1}"
TMP_MAX_AGE_DAYS="${TMP_MAX_AGE_DAYS:-7}"
CLEAN_VARTMP="${CLEAN_VARTMP:-1}"
VARTMP_MAX_AGE_DAYS="${VARTMP_MAX_AGE_DAYS:-7}"

CLEAN_SNAPD_CACHE="${CLEAN_SNAPD_CACHE:-1}"
CLEAN_VAR_CRASH="${CLEAN_VAR_CRASH:-1}"

CLEAN_OLD_LOG_GZ="${CLEAN_OLD_LOG_GZ:-1}"
LOG_GZ_RETENTION_DAYS="${LOG_GZ_RETENTION_DAYS:-7}"

SNAP_SET_RETAIN="${SNAP_SET_RETAIN:-1}"
SNAP_RETAIN_N="${SNAP_RETAIN_N:-2}"

NPM_CACHE_VERIFY="${NPM_CACHE_VERIFY:-1}"
NPM_CACHE_CLEAN="${NPM_CACHE_CLEAN:-0}"         # 0=безопасно: только verify, clean --force требует явного включения
CLEAN_NPM_CACHE_DIR="${CLEAN_NPM_CACHE_DIR:-0}"  # 0=не удалять _cacache напрямую (ломает npm)

FLATPAK_UNUSED="${FLATPAK_UNUSED:-1}"

DOCKER_PRUNE="${DOCKER_PRUNE:-1}"
DOCKER_PRUNE_VOLUMES="${DOCKER_PRUNE_VOLUMES:-0}"
# Расширенная безопасная очистка Docker (тонкая настройка)
DOCKER_SAFE_PRUNE="${DOCKER_SAFE_PRUNE:-1}"
DOCKER_PRUNE_CONTAINERS_UNTIL="${DOCKER_PRUNE_CONTAINERS_UNTIL:-48h}"
DOCKER_PRUNE_IMAGES_UNTIL="${DOCKER_PRUNE_IMAGES_UNTIL:-168h}"
DOCKER_PRUNE_BUILDER_UNTIL="${DOCKER_PRUNE_BUILDER_UNTIL:-168h}"
DOCKER_DEEP_PRUNE="${DOCKER_DEEP_PRUNE:-0}"
DOCKER_DEEP_PRUNE_UNTIL="${DOCKER_DEEP_PRUNE_UNTIL:-336h}"

# Очистка профилей браузеров (кэши внутри ~/.config)
CLEAN_CHROME_PROFILE_CACHE="${CLEAN_CHROME_PROFILE_CACHE:-1}"

# Очистка Timeshift (по умолчанию выключено для безопасности)
CLEAN_TIMESHIFT="${CLEAN_TIMESHIFT:-0}"

# Очистка Node.js, Chrome cache, Poetry cache, Copilot cache
OLD_NODE_CLEAN="${OLD_NODE_CLEAN:-1}"
CHROME_CACHE_CLEAN="${CHROME_CACHE_CLEAN:-1}"
POETRY_CACHE_CLEAN="${POETRY_CACHE_CLEAN:-1}"
COPILOT_CACHE_CLEAN="${COPILOT_CACHE_CLEAN:-1}"

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
  [ -e "$p" ] || { return 0; }
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

clean_chrome_profile_cache() {
  [ "$CLEAN_CHROME_PROFILE_CACHE" = "1" ] || return 0
  local base="$HOME/.config/google-chrome"
  if proc_running chrome || proc_running "google-chrome"; then
    log "skip: Chrome запущен, кэш профиля не трогаем"
    return 0
  fi
  log "Очистка кэшей в профилях Chrome..."
  find "$base" -type d \( -name "Cache" -o -name "Code Cache" -o -name "GPUCache" -o -name "Service Worker" \) -exec rm -rf {} + 2>/dev/null || true
}

show_top_large_files() {
    log "--- ТОП-10 САМЫХ БОЛЬШИХ ФАЙЛОВ В $HOME ---"
    find "$HOME" -type f -not -path '*/.*' -exec du -h {} + 2>/dev/null | sort -rh | head -n 10 || true
    log "--- ТОП-10 САМЫХ БОЛЬШИХ СКРЫТЫХ ПАПОК (КЭШИ/КОНФИГИ) ---"
    du -sh "$HOME"/.* 2>/dev/null | sort -rh | head -n 10 || true
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
  [ -d "$dir" ] || { log "skip (нет каталога): $dir"; return 0; }
  case "$dir" in
    "$HOME/.cache"/*) ;;  # защита от случайного rm вне ~/.cache
    *) log "ОТКАЗ: $dir не под $HOME/.cache"; return 1;;
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
  [ -d "$dir" ] || { log "skip: нет $dir"; return 0; }
  if [ -n "$proc" ] && proc_running "$proc"; then
    log "skip ($name): процесс '$proc' активен"
    return 0
  fi
  log "Очистка $name: $dir"
  purge_dir_contents "$dir" || true
}

clean_huggingface() {
  local dir="$CACHE_DIR/huggingface"
  [ -d "$dir" ] || { log "skip: нет $dir"; return 0; }

  if [ "$HF_MAX_AGE_DAYS" = "0" ]; then
    log "Очистка huggingface ЦЕЛИКОМ"
    purge_dir_contents "$dir" || true
  else
    log "Очистка huggingface: файлы старше $HF_MAX_AGE_DAYS дн."
    if [ "$DRY_RUN" = "1" ]; then
      find "$dir" -type f -mtime +"$HF_MAX_AGE_DAYS" -print | head -n 20 \
        | sed 's/^/DRY-RUN: удалил бы: /' || true
    else
      find "$dir" -type f -mtime +"$HF_MAX_AGE_DAYS" -delete || true
      find "$dir" -type d -empty -delete || true
    fi
  fi
}

clean_pip() {
  [ "$PIP_PURGE" = "1" ] || return 0
  if [ "$(id -u)" -eq 0 ]; then
    log "skip: pip cache пропускается (запуск от root)"
    return 0
  fi
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

clean_old_node_versions() {
  [ "$OLD_NODE_CLEAN" = "1" ] || return 0
  local nvm_dir="$HOME/.nvm"
  [ -d "$nvm_dir/versions/node" ] || { log "skip: нет $nvm_dir/versions/node"; return 0; }

  # Определяем текущую активную версию
  local current_ver=""
  if [ -f "$nvm_dir/alias/default" ]; then
    current_ver=$(basename "$(readlink -f "$nvm_dir/versions/node/$(cat "$nvm_dir/alias/default" 2>/dev/null)" 2>/dev/null)" 2>/dev/null || true)
  fi
  if [ -z "$current_ver" ] && command -v node >/dev/null 2>&1; then
    current_ver="v$(node --version 2>/dev/null | sed 's/^v//')"
  fi
  [ -n "$current_ver" ] || { log "skip: не удалось определить текущую Node.js версию"; return 0; }

  log "Текущая Node.js: $current_ver. Удаляю старые..."
  local cleaned=0
  for ver_dir in "$nvm_dir/versions/node"/v*/; do
    [ -d "$ver_dir" ] || continue
    local ver
    ver=$(basename "$ver_dir")
    if [ "$ver" != "$current_ver" ]; then
      local sz
      sz=$(size_of "$ver_dir")
      log "Удаление Node.js $ver ($sz)"
      if [ "$DRY_RUN" != "1" ]; then
        rm -rf "$ver_dir" || log "warn: не удалось удалить $ver_dir"
      fi
      cleaned=1
    fi
  done
  [ "$cleaned" = "1" ] || log "Старых версий Node.js не найдено"
}

clean_chrome_cache_full() {
  [ "$CHROME_CACHE_CLEAN" = "1" ] || return 0
  local dir="$HOME/.cache/google-chrome"
  [ -d "$dir" ] || { log "skip: нет $dir"; return 0; }
  if proc_running chrome || proc_running "google-chrome"; then
    log "skip: Chrome запущен — пропускаю очистку кэша"
    return 0
  fi
  local sz
  sz=$(size_of "$dir")
  log "Очистка Chrome cache ($sz): $dir"
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: rm -rf $dir"
  else
    rm -rf "$dir" || log "warn: не удалось удалить $dir"
  fi
}

clean_poetry_cache() {
  [ "$POETRY_CACHE_CLEAN" = "1" ] || return 0
  local dir="$HOME/.cache/pypoetry"
  [ -d "$dir" ] || { log "skip: нет $dir"; return 0; }
  local sz
  sz=$(size_of "$dir")
  log "Очистка Poetry cache ($sz): $dir"
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: rm -rf $dir"
  else
    rm -rf "$dir" || log "warn: не удалось удалить $dir"
  fi
}

clean_copilot_cache() {
  [ "$COPILOT_CACHE_CLEAN" = "1" ] || return 0
  local dir="$HOME/.cache/copilot"
  [ -d "$dir" ] || { log "skip: нет $dir"; return 0; }
  local sz
  sz=$(size_of "$dir")
  log "Очистка Copilot cache ($sz): $dir"
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: rm -rf $dir"
  else
    rm -rf "$dir" || log "warn: не удалось удалить $dir"
  fi
}

show_home_summary() {
  echo "=============================================="
  echo "  СВОДКА ПО РАЗМЕРАМ В ДОМАШНЕЙ ДИРЕКТОРИИ"
  echo "=============================================="

  echo "--- ТОП-30 ЭЛЕМЕНТОВ В ~ ---"
  du -sh "$HOME"/* "$HOME"/.[!.]* 2>/dev/null | sort -rh | head -30 || true

  echo "--- .config (топ-15) ---"
  du -sh "$HOME/.config"/*/ 2>/dev/null | sort -rh | head -15 || true

  echo "--- .cache (топ-15) ---"
  du -sh "$HOME/.cache"/*/ 2>/dev/null | sort -rh | head -15 || true

  echo "--- .local/share (топ-15) ---"
  du -sh "$HOME/.local/share"/*/ 2>/dev/null | sort -rh | head -15 || true

  echo "--- snap (топ-15) ---"
  du -sh "$HOME/snap"/*/ 2>/dev/null | sort -rh | head -15 || true

  echo "--- .nvm версии ---"
  du -sh "$HOME/.nvm/versions/node"/*/ 2>/dev/null | sort -rh || true

  echo "--- ИТОГО .nvm / .npm ---"
  du -sh "$HOME/.nvm" "$HOME/.npm" 2>/dev/null || true

  # --- Собираем данные для таблиц ---
  local s_chrome_cfg=0 s_chrome_cache=0 s_brave_cfg=0 s_brave_cache=0 s_vscode_cfg=0
  local s_codeium=0 s_cline=0 s_copilot_cache=0 s_copilot_cfg=0 s_qwen=0
  local s_nvm=0 s_npm_cache=0 s_pip=0 s_poetry_cache=0 s_poetry_local=0 s_uv_cache=0 s_uv_local=0
  local s_playwright=0 s_playwright_go=0 s_puppeteer=0 s_chromium_snap=0
  local s_snap_chromium=0 s_snap_firefox=0 s_snap_telegram=0 s_snap_obsidian=0
  local s_kilo=0 s_acli=0 s_backgrounds=0 s_vs_ext=0

  _sz() { local r; r=$(du -sm "$1" 2>/dev/null | awk '{print $1}' || true); echo "${r:-0}"; }

  s_chrome_cfg=$(_sz "$HOME/.config/google-chrome")
  s_chrome_cache=$(_sz "$HOME/.cache/google-chrome")
  s_brave_cfg=$(_sz "$HOME/.config/BraveSoftware")
  s_brave_cache=$(_sz "$HOME/.cache/BraveSoftware")
  s_vscode_cfg=$(_sz "$HOME/.config/Code")
  s_vs_ext=$(_sz "$HOME/.vscode")
  s_codeium=$(_sz "$HOME/.codeium")
  s_cline=$(_sz "$HOME/.cline")
  s_copilot_cache=$(_sz "$HOME/.cache/copilot")
  s_copilot_cfg=$(_sz "$HOME/.copilot")
  s_qwen=$(_sz "$HOME/.qwen")
  s_nvm=$(_sz "$HOME/.nvm")
  s_npm_cache=$(_sz "$HOME/.npm")
  s_pip=$(_sz "$HOME/.cache/pip")
  s_poetry_cache=$(_sz "$HOME/.cache/pypoetry")
  s_poetry_local=$(_sz "$HOME/.local/share/pypoetry")
  s_uv_cache=$(_sz "$HOME/.cache/uv")
  s_uv_local=$(_sz "$HOME/.local/share/uv")
  s_playwright=$(_sz "$HOME/.cache/ms-playwright")
  s_playwright_go=$(_sz "$HOME/.cache/ms-playwright-go")
  s_puppeteer=$(_sz "$HOME/.cache/puppeteer")
  s_chromium_snap=$(_sz "$HOME/.chromium-browser-snapshots")
  s_snap_chromium=$(_sz "$HOME/snap/chromium")
  s_snap_firefox=$(_sz "$HOME/snap/firefox")
  s_snap_telegram=$(_sz "$HOME/snap/telegram-desktop")
  s_snap_obsidian=$(_sz "$HOME/snap/obsidian")
  s_kilo=$(_sz "$HOME/.local/share/kilo")
  s_acli=$(_sz "$HOME/.local/share/acli")
  s_backgrounds=$(_sz "$HOME/.local/share/backgrounds")

  # Вычисляем суммарные категории
  local chrome_total=$((s_chrome_cfg + s_chrome_cache))
  local brave_total=$((s_brave_cfg + s_brave_cache))
  local ai_total=$((s_codeium + s_cline + s_copilot_cache + s_copilot_cfg + s_qwen))
  local node_total=$((s_nvm + s_npm_cache))
  local poetry_total=$((s_poetry_cache + s_poetry_local))
  local uv_total=$((s_uv_cache + s_uv_local))
  local playwright_total=$((s_playwright + s_playwright_go))
  local snap_total=$((s_snap_chromium + s_snap_firefox + s_snap_telegram + s_snap_obsidian))
  local copilot_total=$((s_copilot_cache + s_copilot_cfg))

  echo "=============================================="
  echo "  TOP APPLICATIONS (ПОСЛЕ ОЧИСТКИ)"
  echo "=============================================="
  printf "%-35s %10s MB  %s\n" "Приложение" "Размер" "Расположение"
  printf "%-35s %10s MB  %s\n" "-----------------------------------" "----------" "----------------------------"
  printf "%-35s %10s MB  %s\n" "Google Chrome" "$chrome_total" ".config + .cache"
  printf "%-35s %10s MB  %s\n" "VS Code" "$s_vscode_cfg" ".config/Code/"
  printf "%-35s %10s MB  %s\n" "Snap: Chromium" "$s_snap_chromium" "snap/chromium/"
  printf "%-35s %10s MB  %s\n" "Snap: Telegram" "$s_snap_telegram" "snap/telegram-desktop/"
  printf "%-35s %10s MB  %s\n" "Codeium" "$s_codeium" ".codeium/"
  printf "%-35s %10s MB  %s\n" "VS Code extensions" "$s_vs_ext" ".vscode/"
  printf "%-35s %10s MB  %s\n" "Cline" "$s_cline" ".cline/"
  printf "%-35s %10s MB  %s\n" "Brave" "$brave_total" ".config + .cache"
  printf "%-35s %10s MB  %s\n" "Snap: Firefox" "$s_snap_firefox" "snap/firefox/"
  printf "%-35s %10s MB  %s\n" "Playwright" "$playwright_total" ".cache/ms-playwright(+go)"
  printf "%-35s %10s MB  %s\n" "Puppeteer" "$s_puppeteer" ".cache/puppeteer/"
  printf "%-35s %10s MB  %s\n" "Chromium snapshots" "$s_chromium_snap" ".chromium-browser-snapshots/"
  printf "%-35s %10s MB  %s\n" "pip cache" "$s_pip" ".cache/pip/"
  printf "%-35s %10s MB  %s\n" "Poetry" "$poetry_total" ".cache + .local"
  printf "%-35s %10s MB  %s\n" "uv" "$uv_total" ".cache + .local"
  printf "%-35s %10s MB  %s\n" "Copilot" "$copilot_total" ".cache + .copilot"
  printf "%-35s %10s MB  %s\n" "Snap: Obsidian" "$s_snap_obsidian" "snap/obsidian/"
  printf "%-35s %10s MB  %s\n" "Kilo" "$s_kilo" ".local/share/"
  printf "%-35s %10s MB  %s\n" "acli" "$s_acli" ".local/share/acli/"
  printf "%-35s %10s MB  %s\n" "Backgrounds" "$s_backgrounds" ".local/share/backgrounds/"
  echo ""

  echo "=============================================="
  echo "  ПО КАТЕГОРИЯМ"
  echo "=============================================="
  printf "%-35s %10s MB  %s\n" "Категория" "Размер" "Состав"
  printf "%-35s %10s MB  %s\n" "-----------------------------------" "----------" "----------------------------"
  printf "%-35s %10s MB  %s\n" "Browsers" "$((chrome_total + brave_total + s_snap_chromium + s_snap_firefox))" "Chrome, Brave, Chromium snap, Firefox snap"
  printf "%-35s %10s MB  %s\n" "Dev Tools" "$((s_vscode_cfg + s_vs_ext))" "VS Code + extensions"
  printf "%-35s %10s MB  %s\n" "AI Coding Assistants" "$ai_total" "Codeium, Cline, Copilot, Qwen"
  printf "%-35s %10s MB  %s\n" "Node.js Tooling" "$node_total" "nvm + npm cache"
  printf "%-35s %10s MB  %s\n" "Python Tooling" "$((s_pip + poetry_total + uv_total))" "pip + poetry + uv"
  printf "%-35s %10s MB  %s\n" "Browser Automation" "$((playwright_total + s_puppeteer))" "Playwright + Puppeteer"
  printf "%-35s %10s MB  %s\n" "Snap Apps" "$snap_total" "Chromium, Telegram, Firefox, Obsidian"
  echo ""

  echo "=============================================="
  echo "  ПОТЕНЦИАЛЬНЫЕ КАНДИДАТЫ НА УДАЛЕНИЕ"
  echo "=============================================="
  printf "%-35s %10s MB  %s\n" "Что" "Экономия" "Комментарий"
  printf "%-35s %10s MB  %s\n" "-----------------------------------" "----------" "----------------------------"
  printf "%-35s %10s MB  %s\n" "Chrome cache" "$s_chrome_cache" "Сбросит кэш, можно почистить из браузера"
  printf "%-35s %10s MB  %s\n" "Playwright + Puppeteer" "$((playwright_total + s_puppeteer))" "Если не используется для тестов"
  printf "%-35s %10s MB  %s\n" "pip cache" "$s_pip" "pip cache purge"
  printf "%-35s %10s MB  %s\n" "Poetry cache" "$s_poetry_cache" "Можно чистить"
  printf "%-35s %10s MB  %s\n" "Snap: Chromium" "$s_snap_chromium" "Chrome уже установлен"
  printf "%-35s %10s MB  %s\n" "Snap: Obsidian" "$s_snap_obsidian" "Если не используется"
  printf "%-35s %10s MB  %s\n" "Chromium snapshots" "$s_chromium_snap" "Скорее всего от Playwright/Puppeteer"
  printf "%-35s %10s MB  %s\n" "Copilot cache" "$s_copilot_cache" "Кэш Copilot"
  printf "%-35s %10s MB  %s\n" "BraveSoftware" "$brave_total" "Если не используется"
  printf "%-35s %10s MB  %s\n" "Backgrounds" "$s_backgrounds" "Обои/фоновые изображения"
  echo ""
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
  command -v snap >/dev/null 2>&1 || { log "skip: snap не найден"; return 0; }
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
  command -v python3 >/dev/null 2>&1 || { log "skip: нет python3"; return 0; }
  local pkgs
  pkgs=$(python3 -m pip freeze 2>/dev/null | awk -F'==' 'BEGIN{IGNORECASE=1} /^torch|^triton|^bitsandbytes|^nvidia-|^cuda|^cudnn|^cublas|^cusparselt/{print $1}' | sort -u | tr '\n' ' ' ) || true
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
  command -v sqlite3 >/dev/null 2>&1 || { log "skip: нет sqlite3"; return 0; }
  local saved=0
  IFS=$'\n'
  for db in $SQLITE_DB_PATHS; do
    [ -f "$db" ] || { log "skip: нет БД $db"; continue; }
    local before after
    before=$(stat -c %s "$db" 2>/dev/null || echo 0)
    if [ "$DRY_RUN" = "1" ]; then
      log "DRY-RUN: sqlite3 $db 'VACUUM;'"
    else
      sqlite3 "$db" 'VACUUM;' 2>/dev/null || true
    fi
    after=$(stat -c %s "$db" 2>/dev/null || echo 0)
    if [ "$after" -lt "$before" ]; then
      saved=$(( saved + (before - after) ))
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
  command -v snap >/dev/null 2>&1 || { log "skip: snap не найден"; return 0; }
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

clean_npm_cache_dir() {
  [ "$CLEAN_NPM_CACHE_DIR" = "1" ] || return 0
  local dir="$HOME/.npm/_cacache"
  [ -d "$dir" ] || { log "skip: нет $dir"; return 0; }
  log "Очистка npm cacache: $dir"
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: rm -rf $dir"
  else
    rm -rf "$dir" || log "warn: не удалось удалить $dir"
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

clean_timeshift() {
  [ "$CLEAN_TIMESHIFT" = "1" ] || return 0
  command -v timeshift >/dev/null 2>&1 || { log "skip: timeshift не найден"; return 0; }
  if ! sudo -n true 2>/dev/null; then
    log "skip: требуется sudo для очистки timeshift"
    return 0
  fi

  # Проверяем, есть ли вообще снимки
  # `timeshift --list` has a non-zero exit code if no snapshots are found on some systems.
  # So we check the output.
  local snapshot_list
  snapshot_list=$(sudo timeshift --list 2>/dev/null || true)
  if ! echo "$snapshot_list" | grep -qE '^[0-9]+\s+>'; then
    log "Снимки Timeshift не найдены."
    return 0
  fi

  log "Удаление ВСЕХ снимков Timeshift..."

  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: yes | sudo timeshift --delete-all"
    echo "$snapshot_list" | awk 'NR > 2 {print "DRY-RUN: would delete snapshot", $3}'
    return 0
  fi

  # timeshift --delete-all требует интерактивного подтверждения
  if yes | sudo timeshift --delete-all; then
    log "Все снимки Timeshift удалены."
  else
    log "warn: не удалось удалить снимки Timeshift."
  fi
}

clean_tmp_all()   { [ "$CLEAN_TMP" = "1" ] && cleanup_tmp_path "/tmp" "$TMP_MAX_AGE_DAYS"; }
clean_vartmp_all(){ [ "$CLEAN_VARTMP" = "1" ] && cleanup_tmp_path "/var/tmp" "$VARTMP_MAX_AGE_DAYS"; }

human() { numfmt --to=iec --suffix=B 2>/dev/null; }
free_bytes() { df -B1 --output=avail / | tail -1 | tr -d ' '; }

log "Начало. Размер $CACHE_DIR = $(size_of "$CACHE_DIR")"
BEFORE_FREE=$(free_bytes)
log "Свободно ДО: $(printf "%s" "$BEFORE_FREE" | human)"

# Основные чистки
clean_pip
clean_huggingface
[ "$CHROME_CLEAN" = "1" ]     && clean_dir_if_idle "google-chrome" "$CACHE_DIR/google-chrome" "chrome"
[ "$PUPPETEER_CLEAN" = "1" ]  && clean_dir_if_idle "puppeteer"     "$CACHE_DIR/puppeteer"     ""
[ "$PLAYWRIGHT_CLEAN" = "1" ] && clean_dir_if_idle "ms-playwright-go" "$CACHE_DIR/ms-playwright-go" ""
[ "$THUMBNAILS_CLEAN" = "1" ] && purge_dir_contents "$CACHE_DIR/thumbnails"
[ "$MESA_CLEAN" = "1" ]       && purge_dir_contents "$CACHE_DIR/mesa_shader_cache"
[ "$UV_CLEAN" = "1" ]         && purge_dir_contents "$CACHE_DIR/uv"
[ "$BRAVE_CLEAN" = "1" ]      && clean_dir_if_idle "BraveSoftware" "$CACHE_DIR/BraveSoftware" "brave"

# Новые шаги (повторяют проделанные нами операции)
clean_chrome_model
clean_vscode_caches
clean_pyppeteer_share
clean_chrome_profile_cache

# Очистка Node.js, Chrome cache, Poetry cache, Copilot cache
clean_old_node_versions
clean_chrome_cache_full
clean_poetry_cache
clean_copilot_cache

# Системные шаги (по возможности без пароля sudo)
vacuum_journal
clean_old_log_gz
snap_set_retain
snap_cleanup_disabled
clean_snapd_cache
clean_var_crash
apt_maintenance
clean_timeshift
clean_tmp_all
clean_vartmp_all

# Пакетные менеджеры/платформы (опционально)
npm_cache_ops
clean_npm_cache_dir
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
DELTA=$(( AFTER_FREE - BEFORE_FREE ))
log "Свободно ПОСЛЕ: $(printf "%s" "$AFTER_FREE" | human) (Δ=$(printf "%s" "$DELTA" | human))"

show_home_summary

# Дополнительно мелкие кэши (если есть)
purge_dir_contents "$CACHE_DIR/node-gyp" || true
purge_dir_contents "$CACHE_DIR/yarn"     || true
purge_dir_contents "$CACHE_DIR/vscode-ripgrep" || true

log "Готово. Размер $CACHE_DIR = $(size_of "$CACHE_DIR")"

