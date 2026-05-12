#!/usr/bin/env bash
# MIGRATION PACK v2.0 — упаковка любого проекта для 100% копирования ИИ
# Запуск: bash scripts/migration-pack.sh
# Результат: project-migration-prompt.md — готовый промпт
#
# Особенности:
#   • Автоопределение языка
#   • XML+CDATA с эскейпингом ]]> → ]]]]><![CDATA[>
#   • MIME-encoding для точного определения текстовых файлов
#   • Инструкция по чанкованию (7 файлов за шаг)
#   • PRO TIP: self-extracting скрипт
#===============================================================================

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT_FILE="${PROJECT_ROOT}/project-migration-prompt.md"
BUNDLE_FILE="$(mktemp /tmp/migration-bundle-XXXXXX.xml)"
TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

# ─── 1. Автоопределение языка ───────────────────────────────────────────────

detect_lang() {
  if [ -f "${PROJECT_ROOT}/package.json" ]; then echo "node"
  elif [ -f "${PROJECT_ROOT}/requirements.txt" ] || [ -f "${PROJECT_ROOT}/setup.py" ] || [ -f "${PROJECT_ROOT}/pyproject.toml" ]; then echo "python"
  elif [ -f "${PROJECT_ROOT}/Cargo.toml" ]; then echo "rust"
  elif [ -f "${PROJECT_ROOT}/go.mod" ]; then echo "go"
  elif [ -f "${PROJECT_ROOT}/pom.xml" ] || [ -f "${PROJECT_ROOT}/build.gradle" ]; then echo "java"
  elif [ -f "${PROJECT_ROOT}/CMakeLists.txt" ]; then echo "cpp"
  elif [ -f "${PROJECT_ROOT}/Gemfile" ]; then echo "ruby"
  elif [ -f "${PROJECT_ROOT}/composer.json" ]; then echo "php"
  else echo "unknown"; fi
}

detect_install_cmd() {
  local lang="$1"
  case "$lang" in
    node) echo "npm install" ;;
    python) [ -f "${PROJECT_ROOT}/requirements.txt" ] && echo "pip install -r requirements.txt" || echo "pip install ." ;;
    rust) echo "cargo build" ;;
    go) echo "go mod download" ;;
    java) [ -f "${PROJECT_ROOT}/pom.xml" ] && echo "mvn install" || echo "gradle build" ;;
    cpp) echo "cmake --build build" ;;
    ruby) echo "bundle install" ;;
    php) echo "composer install" ;;
    *) echo "echo 'Install manually'" ;;
  esac
}

detect_dev_cmd() {
  local lang="$1"
  case "$lang" in
    node)
      if [ -f "${PROJECT_ROOT}/package.json" ]; then
        local cmd
        cmd="$(node -e "
          const p = require('${PROJECT_ROOT}/package.json');
          const s = p.scripts;
          console.log(s.dev || s.start || s.serve || s.develop || 'npm run dev');
        " 2>/dev/null || echo "npm run dev")"
        echo "$cmd"
      else echo "npm run dev"; fi
      ;;
    python) [ -f "${PROJECT_ROOT}/manage.py" ] && echo "python manage.py runserver" || echo "python main.py" ;;
    rust) echo "cargo run" ;;
    go) echo "go run ." ;;
    java) echo "mvn spring-boot:run" ;;
    *) echo "echo 'Start manually'" ;;
  esac
}

# ─── 2. Определение переменных (безопасный grep) ────────────────────────────

count_lines() {
  # Безопасный подсчёт: grep -c возвращает 1 если 0 совпадений
  local pattern="$1"
  local file="$2"
  grep -c "$pattern" "$file" 2>/dev/null || true
  return 0
}

LANG="$(detect_lang)"
INSTALL_CMD="$(detect_install_cmd "$LANG")"
DEV_CMD="$(detect_dev_cmd "$LANG")"
NAME="$(basename "${PROJECT_ROOT}")"

# ─── 3. Сбор файлов в XML+CDATA ──────────────────────────────────────────────

build_bundle() {
  : > "$BUNDLE_FILE"
  echo '<files>' >> "$BUNDLE_FILE"

  while IFS= read -r -d '' fullpath; do
    relpath="${fullpath#${PROJECT_ROOT}/}"
    size="$(wc -c < "$fullpath" | tr -d ' ')"

    [ "$size" -eq 0 ] && { echo "  <!-- SKIP (empty): ${relpath} -->" >> "$BUNDLE_FILE"; continue; }
    [ "$size" -gt 512000 ] && { echo "  <!-- SKIP (too large: ${size}B): ${relpath} -->" >> "$BUNDLE_FILE"; continue; }

    if file -b --mime-encoding "$fullpath" | grep -qv 'binary'; then
      echo "  <file path=\"${relpath}\">" >> "$BUNDLE_FILE"
      echo '  <![CDATA[' >> "$BUNDLE_FILE"
      sed 's/]]>/]]]]><![CDATA[>/g' "$fullpath" >> "$BUNDLE_FILE"
      echo '' >> "$BUNDLE_FILE"
      echo '  ]]>' >> "$BUNDLE_FILE"
      echo "  </file>" >> "$BUNDLE_FILE"
      echo "" >> "$BUNDLE_FILE"
    else
      echo "  <!-- SKIP (binary): ${relpath} -->" >> "$BUNDLE_FILE"
    fi
  done < <(find "${PROJECT_ROOT}" -type f \
    ! -path '*/node_modules/*' \
    ! -path '*/.git/*' \
    ! -path '*/dist/*' \
    ! -path '*/build/*' \
    ! -path '*/target/*' \
    ! -path '*/__pycache__/*' \
    ! -path '*/.next/*' \
    ! -path '*/.svelte-kit/*' \
    ! -path '*/.expo/*' \
    ! -path '*/venv/*' \
    ! -path '*/.venv/*' \
    ! -path '*/.kilo/*' \
    ! -path '*/.idea/*' \
    ! -path '*/.vscode/*' \
    ! -path '*/prompts/*' \
    ! -path '*/scripts/*' \
    ! -path '*/tests/*' \
    ! -path '*/media/*' \
    ! -path '*/playlists/*' \
    ! -name '.env' \
    ! -name '*.log' \
    ! -name 'package-lock.json' \
    ! -name 'yarn.lock' \
    ! -name 'pnpm-lock.yaml' \
    ! -name 'Cargo.lock' \
    ! -name 'Gemfile.lock' \
    ! -name '.gitignore' \
    ! -name '.DS_Store' \
    ! -name 'Thumbs.db' \
    ! -name '*.jpg' ! -name '*.jpeg' ! -name '*.png' ! -name '*.gif' \
    ! -name '*.ico' ! -name '*.mp4' ! -name '*.mp3' ! -name '*.webm' \
    ! -name '*.woff' ! -name '*.woff2' ! -name '*.eot' ! -name '*.ttf' \
    ! -name '*.exe' ! -name '*.dll' ! -name '*.so' ! -name '*.dylib' \
    ! -name '*.pdf' ! -name '*.docx' ! -name '*.xlsx' \
    ! -name 'project-migration-prompt.md' \
    ! -name '.migration-bundle.xml' \
    ! -path '*/.migration-bundle*' \
    -print0 2>/dev/null | sort -z)

  echo '</files>' >> "$BUNDLE_FILE"
}

# ─── 4. Генерация промпта ────────────────────────────────────────────────────

build_prompt() {
  local tf bs binary_lines line
  tf="$(count_lines '<file path=' "$BUNDLE_FILE")"
  bs="$(count_lines 'SKIP (binary)' "$BUNDLE_FILE")"
  binary_lines="$(grep 'SKIP (binary)' "$BUNDLE_FILE" 2>/dev/null | sed 's/.*: \(.*\) -->/\1/' | head -20 || true)"

  : > "$OUTPUT_FILE"

  cat >> "$OUTPUT_FILE" << EOF
# MASTER PROMPT: 100% Exact Copy of "${NAME}"

This prompt contains the COMPLETE source code of the application **${NAME}**.
Send it to any AI agent to recreate the project **identically**.

---

## Project Info

- **Project:** ${NAME}
- **Auto-detected language:** ${LANG}
- **Capture timestamp:** ${TIMESTAMP}

## First Steps for the AI Agent

1. Parse every \`<file path="...">\` block below
2. Extract content from \`<![CDATA[ ... ]]>\` (the \`]]>\` inside code has been escaped — undo when writing)
3. Recreate the file at the exact path
4. After ALL files are created, run: \`${INSTALL_CMD}\`
5. Then start the dev server: \`${DEV_CMD}\`

---

## RULES — YOU MUST OBEY ALL OF THEM

1. **NO MODIFICATIONS:** Recreate every file character-for-character. Do not refactor, optimize, or modernize. Keep ALL comments (including non-English), ALL unused variables, ALL console.log statements, ALL commented-out code.

2. **CHUNK YOUR WORK [IMPORTANT]:** If there are more than 7 files, do NOT try to create them all at once. Create 7 files using your tools in parallel, then STOP and ask the user to type "continue". Repeat until ALL files are created. This avoids hitting output token limits.

3. **USE YOUR FILE TOOLS SILENTLY:** If you have file-editing tools (create_file, write_to_file, bash), use them. Do NOT print code blocks in chat — just execute and confirm.

4. **BIG FILE STRATEGY:** If a file inside CDATA is too large for one tool call, use append/write in chunks or generate via a script. Do not truncate.

5. **INSTALL FIRST:** Create package.json (or equivalent) and install dependencies BEFORE starting the server.

6. **EXACT PATHS:** Respect \`path="..."\` exactly. Auto-create parent directories.

7. **BINARY PLACEHOLDERS [CRITICAL]:** For skipped binary files (marked as \`<!-- SKIP (binary): ... -->\`), create VALID minimal placeholders. A 0-byte .png or .ico will crash Vite/Webpack.
   - For images (.png, .jpg, .ico): create a 1x1 transparent pixel
   - Use this bash snippet for a valid 1x1 transparent PNG:
     \`\`\`bash
     printf '\\x89PNG\\r\\n\\x1a\\n\\x00\\x00\\x00\\rIHDR\\x00\\x00\\x00\\x01\\x00\\x00\\x00\\x01\\x08\\x06\\x00\\x00\\x00\\x1f\\x15\\xc4\\x89\\x00\\x00\\x00\\x0bIDATx\\x9cc\\x62\\x08\\x05\\x00\\x00\\x00\\x00\\xff\\xff\\x03\\x00\\x00\\x04\\x00\\x01\\x0c\\x0c\\x0c\\x0c\\x00\\x00\\x00\\x00IEND\\xaeB\`\\x82' > path/to/file.png
     \`\`\`

8. **EXACT VERSIONS:** Use dependency versions as specified. Do not upgrade.

9. **CDATA ESCAPE FIX:** Content has \`]]>\` escaped as \`]]]]><![CDATA[>\`. Reverse this when writing: replace \`]]]]><![CDATA[>\` back to \`]]>\`.

10. **VERIFY:** After setup, print "MIGRATION COMPLETE — all ${tf} files created" when done.

---

## PRO TIP: Fast Migration with Self-Extracting Script

Instead of calling file tools 50+ times, write a **single extraction script**:

**Option A: Python unpacker** (best)
- Read the XML \`<file>\` blocks below
- Parse \`path\` attr and \`CDATA\` content
- Handle \`]]]]><![CDATA[>\` → \`]]>\` unescaping
- Create all files and directories
- Touch valid placeholders for skipped binaries

**Option B: Node.js unpacker** (if Node project)
Same logic in JS.

**Option C: Direct tool calls** (fallback)
Create 7 files at a time, ask "continue" between chunks.

---

## Source Code

Below is every source file, wrapped in XML with CDATA for safe parsing.

## Project Metadata

- **Name:** ${NAME}
- **Language:** ${LANG}
- **Captured at:** ${TIMESTAMP}
- **Total files:** ${tf}
- **Skipped binary:** ${bs}
- **Install command:** \`${INSTALL_CMD}\`
- **Dev command:** \`${DEV_CMD}\`

### Directory Structure
\`\`\`
EOF

  find "${PROJECT_ROOT}" -type d \
    ! -path '*/node_modules/*' ! -path '*/.git/*' ! -path '*/dist/*' \
    ! -path '*/build/*' ! -path '*/target/*' ! -path '*/__pycache__/*' \
    ! -path '*/.next/*' ! -path '*/.svelte-kit/*' ! -path '*/.expo/*' \
    ! -path '*/venv/*' ! -path '*/.venv/*' ! -path '*/.idea/*' \
    ! -path '*/.vscode/*' ! -path '*/prompts/*' ! -path '*/scripts/*' \
    | sed "s|${PROJECT_ROOT}/||" | sort | head -50 >> "$OUTPUT_FILE"

  cat >> "$OUTPUT_FILE" << 'EOF'
```
EOF

  cat "$BUNDLE_FILE" >> "$OUTPUT_FILE"

  if [ -n "$binary_lines" ]; then
    echo "" >> "$OUTPUT_FILE"
    echo "---" >> "$OUTPUT_FILE"
    echo "" >> "$OUTPUT_FILE"
    echo "## Skipped Binary Files (create valid placeholders)" >> "$OUTPUT_FILE"
    echo "" >> "$OUTPUT_FILE"
    while IFS= read -r line; do
      [ -n "$line" ] && echo "- \`${line}\`" >> "$OUTPUT_FILE"
    done <<< "$binary_lines"
    echo "" >> "$OUTPUT_FILE"
    echo "Create valid 1x1 transparent PNG/placeholder for each. Do NOT use 0-byte files." >> "$OUTPUT_FILE"
    echo "" >> "$OUTPUT_FILE"
  fi

  cat >> "$OUTPUT_FILE" << EOF
---

## End of Source Code

**Total files bundled:** ${tf}
**Skipped binary files:** ${bs}

### Final Instructions

1. Create ALL files above (chunk: 7 at a time, ask "continue")
2. Create valid placeholders for skipped binary files
3. Run install: \`${INSTALL_CMD}\`
4. Run dev: \`${DEV_CMD}\`
5. Confirm with: "MIGRATION COMPLETE — all ${tf} files created"
EOF
}

# ─── 5. Запуск ──────────────────────────────────────────────────────────────

echo "Detecting project..."
echo "  Language:   ${LANG}"
echo "  Name:       ${NAME}"
echo "  Install:    ${INSTALL_CMD}"
echo "  Dev:        ${DEV_CMD}"
echo ""

echo "Bundling source files..."
build_bundle
TF="$(count_lines '<file path=' "$BUNDLE_FILE")"
BS="$(count_lines 'SKIP (binary)' "$BUNDLE_FILE")"
echo "  Files bundled: ${TF}"
echo "  Binary skipped: ${BS}"
echo ""

echo "Generating migration prompt..."
build_prompt

rm -f "$BUNDLE_FILE"

SIZE="$(wc -c < "$OUTPUT_FILE" | tr -d ' ')"
LINES="$(wc -l < "$OUTPUT_FILE" | tr -d ' ')"

echo "Done!"
echo "  Output:  ${OUTPUT_FILE}"
echo "  Size:    $((SIZE / 1024)) KB ($SIZE bytes)"
echo "  Lines:   ${LINES}"
echo "  Sources: ${TF} files + ${BS} binary placeholders"
echo ""
echo "Next step: send '${OUTPUT_FILE}' to any AI agent."
echo "The AI will recreate the project 100% identically."
echo ""
echo "Quick start:"
echo "  ${INSTALL_CMD}"
echo "  ${DEV_CMD}"