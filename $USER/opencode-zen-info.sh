#!/usr/bin/env bash
# OpenCode Zen Free Models — данные для копирования
# Запуск: bash opencode-zen-info.sh
# Динамически получает список моделей из API и определяет бесплатные

G=$(tput setaf 2)   # green
Y=$(tput setaf 3)   # yellow
C=$(tput setaf 6)   # cyan
R=$(tput setaf 1)   # red
B=$(tput bold)      # bold
D=$(tput sgr0)      # reset

SEPARATOR="${B}${G}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${D}"
API_BASE="https://opencode.ai/zen/v1"
TIMEOUT=15

echo ""
echo "$SEPARATOR"
echo "${B}${C}  OpenCode Zen Free Models API — Актуальные данные${D}"
echo "$SEPARATOR"
echo ""

# ── 1. Получаем список моделей из API ──────────────────────────────────
echo "${B}${Y}▶ Получение списка моделей из API...${D}"
MODELS_JSON=$(curl -s --max-time "$TIMEOUT" "$API_BASE/models" 2>/dev/null || true)

if [ -z "$MODELS_JSON" ]; then
  echo "  ${R}Ошибка: не удалось получить список моделей от $API_BASE/models${D}"
  echo "  Проверьте подключение к интернету."
  exit 1
fi

ALL_MODELS=$(echo "$MODELS_JSON" | jq -r '.data[].id' 2>/dev/null | sort)
TOTAL=$(echo "$ALL_MODELS" | grep -c .)
echo "  Получено ${B}${TOTAL}${D} моделей от API"
echo ""

# ── 2. Определяем кандидатов в бесплатные ──────────────────────────────
# Кандидаты: модели с суффиксом -free + известные бесплатные без суффикса
# Примечание: KNOWN_FREE может устареть — обновляйте при необходимости
KNOWN_FREE="big-pickle gpt-5-nano"

CANDIDATES=""
while IFS= read -r mid; do
  [ -z "$mid" ] && continue
  # Модели с -free в имени
  if echo "$mid" | grep -qi -- '-free'; then
    CANDIDATES="$CANDIDATES $mid"
  fi
done <<< "$ALL_MODELS"

# Добавляем известные бесплатные модели
for kf in $KNOWN_FREE; do
  if echo "$ALL_MODELS" | grep -qx "$kf" 2>/dev/null; then
    # Не добавляем дубликаты
    if ! echo "$CANDIDATES" | grep -qw "$kf" 2>/dev/null; then
      CANDIDATES="$CANDIDATES $kf"
    fi
  fi
done

CANDIDATES=$(echo "$CANDIDATES" | tr ' ' '\n' | sort -u | sed '/^$/d')
CANDIDATE_COUNT=$(echo "$CANDIDATES" | grep -c .)

echo "${B}${Y}▶ Проверка $CANDIDATE_COUNT кандидатов в бесплатные модели...${D}"
echo "  (каждая модель тестируется минимальным запросом)"
echo ""

# ── 3. Тестируем каждого кандидата ────────────────────────────────────
FREE_MODELS=""
STATUS_MAP=""

printf "  ${B}%-30s %-12s %-8s${D}\n" "ID модели" "Статус" "Время"
printf "  %-30s %-12s %-8s\n" "──────────────────────────────" "────────────" "────────"

while IFS= read -r mid; do
  [ -z "$mid" ] && continue

  BODY=$(printf '{"model":"%s","messages":[{"role":"user","content":"hi"}],"max_tokens":50}' "$mid")

  RESP=$(curl -s --max-time "$TIMEOUT" -w '\n__HTTP__%{http_code}__TIME__%{time_total}' \
    "$API_BASE/chat/completions" \
    -H "Content-Type: application/json" \
    -d "$BODY" 2>/dev/null || true)

  HTTP_CODE=$(echo "$RESP" | grep -o '__HTTP__[0-9]*' | sed 's/__HTTP__//' || true)
  ELAPSED=$(echo "$RESP" | grep -o '__TIME__[0-9.]*' | sed 's/__TIME__//' || true)
  RESP_BODY=$(echo "$RESP" | sed '/__HTTP__/d')

  # Модель бесплатная если: HTTP 200
  HAS_CONTENT=$(echo "$RESP_BODY" | jq -r '.choices[0].message.content // empty' 2>/dev/null || true)
  HAS_REASONING=$(echo "$RESP_BODY" | jq -r '.choices[0].message.reasoning_content // empty' 2>/dev/null || true)
  FINISH_REASON=$(echo "$RESP_BODY" | jq -r '.choices[0].finish_reason // ""' 2>/dev/null || true)
  MODEL_ID=$(echo "$RESP_BODY" | jq -r '.model // ""' 2>/dev/null || true)

  if [ "$HTTP_CODE" = "200" ]; then
    # HTTP 200 — модель доступна без ключа = бесплатная
    if [ -n "$HAS_CONTENT" ] || [ -n "$HAS_REASONING" ] || [ "$FINISH_REASON" = "length" ]; then
      printf "  ${G}%-30s %-12s %6ss${D}\n" "$mid" "✅ FREE" "${ELAPSED:-?}"
      FREE_MODELS="$FREE_MODELS $mid"
      STATUS_MAP="${STATUS_MAP}${mid}|✅|${MODEL_ID:-$mid}|${ELAPSED:-?}s
"
    else
      printf "  ${Y}%-30s %-12s %6ss${D}\n" "$mid" "⚠️ FREE?" "${ELAPSED:-?}"
      FREE_MODELS="$FREE_MODELS $mid"
      STATUS_MAP="${STATUS_MAP}${mid}|⚠️|${MODEL_ID:-$mid}|${ELAPSED:-?}s
"
    fi
  else
    ERROR_MSG=$(echo "$RESP_BODY" | jq -r '.error.message // "HTTP '"$HTTP_CODE"'"' 2>/dev/null || echo "HTTP $HTTP_CODE")
    printf "  ${R}%-30s %-12s %6ss${D}\n" "$mid" "🔒 PAID" "${ELAPSED:-?}"
    STATUS_MAP="${STATUS_MAP}${mid}|🔒|${ERROR_MSG}|${ELAPSED:-?}s
"
  fi
done <<< "$CANDIDATES"

FREE_COUNT=$(echo "$FREE_MODELS" | tr ' ' '\n' | sed '/^$/d' | grep -c . 2>/dev/null || echo 0)
echo ""

# ── 4. Выводим результаты ─────────────────────────────────────────────
echo "$SEPARATOR"
echo ""

echo "${B}${Y}▶ API Эндпоинт${D}"
echo "  Base URL:     ${C}$API_BASE${D}"
echo "  Совместим:    OpenAI API (chat/completions, /models)"
echo "  Документация: ${C}https://opencode.ai/docs/zen${D}"
echo ""

echo "${B}${Y}▶ Аутентификация${D}"
echo "  Бесплатные модели: ${G}ключ НЕ нужен${D}"
echo "  Платные модели:    ${C}OPENCODE_API_KEY${D}"
echo "  Получить ключ:     opencode → /connect → OpenCode Zen"
echo ""

if [ "$FREE_COUNT" -eq 0 ]; then
  echo "  ${R}⚠️ Бесплатные модели не обнаружены!${D}"
  echo "  Возможно, API изменился или недоступен."
  FIRST_FREE="minimax-m2.5-free"
else
  echo "${B}${Y}▶ Бесплатные модели: ${G}${FREE_COUNT}${D} шт. (проверено прямо сейчас)"
  echo ""
  printf "  ${B}%-30s %-40s %-6s${D}\n" "ID модели" "Реальный провайдер" "Время"
  printf "  %-30s %-40s %-6s\n" "──────────────────────────────" "──────────────────────────────────────────" "──────"

  printf '%b' "$STATUS_MAP" | while IFS='|' read -r mid status provider time; do
    [ -z "$mid" ] && continue
    if [ "$status" = "✅" ]; then
      printf "  ${G}%-30s${D} %-40s %6s\n" "$mid" "$provider" "$time"
    elif [ "$status" = "⚠️" ]; then
      printf "  ${Y}%-30s${D} %-40s %6s\n" "$mid" "$provider (нестабильная)" "$time"
    fi
  done

  # Выбираем первую стабильную бесплатную модель для примеров
  FIRST_FREE=$(echo "$FREE_MODELS" | tr ' ' '\n' | sed '/^$/d' | head -1)
fi
echo ""

echo "${B}${Y}▶ Переменные окружения (для ~/.bashrc)${D}"
echo "  ${C}export OPENAI_BASE_URL=\"$API_BASE\"${D}"
echo "  ${C}export OPENAI_API_KEY=\"\"${D}    # пустая строка — Zen API не требует ключ для бесплатных моделей"
echo ""

echo "${B}${Y}▶ Примеры использования (модель: ${C}${FIRST_FREE}${D}${B})${D}"
echo ""

echo "  ${B}curl (без ключа):${D}"
cat << EXAMPLE
  curl $API_BASE/chat/completions \\
    -H "Content-Type: application/json" \\
    -d '{"model":"$FIRST_FREE","messages":[{"role":"user","content":"Привет!"}],"max_tokens":200}'
EXAMPLE
echo ""

echo "  ${B}Python (openai SDK):${D}"
cat << 'EXAMPLE'
  from openai import OpenAI
  client = OpenAI(base_url="https://opencode.ai/zen/v1", api_key="")  # пустой ключ — Zen API не требует авторизации
  resp = client.chat.completions.create(model="MODEL", messages=[{"role":"user","content":"Привет!"}])
  print(resp.choices[0].message.content)
EXAMPLE
echo ""

echo "  ${B}Aider:${D}"
echo "  ${C}aider --openai-api-base $API_BASE --model $FIRST_FREE${D}"
echo ""

echo "  ${B}Cline / KiloCode:${D}"
echo "  Base URL: ${C}$API_BASE${D}"
echo "  Model:    ${C}$FIRST_FREE${D}"
echo "  API Key:  ${C}(пустое поле)${D}"
echo ""

echo "$SEPARATOR"
echo "${B}  Всего моделей: $TOTAL | Бесплатных: $FREE_COUNT | Дата: $(date +%Y-%m-%d\ %H:%M)${D}"
echo ""
