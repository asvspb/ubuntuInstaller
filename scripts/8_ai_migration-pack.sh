#!/usr/bin/env bash
# MIGRATION PACK v3.0 — упаковка проекта для 100% копирования ИИ
# Запуск: bash scripts/8_ai_migration-pack.sh [--mode full|ui|backend|split] [--project /path/to/project]
# Результат: project-migration-prompt.md (и доп. файлы при --mode split|ui)
#
# Режимы:
#   --mode full    — весь проект целиком (по умолчанию)
#   --mode ui      — только UI/UX как standalone проект
#   --mode backend — только backend
#   --mode split   — оба проекта отдельно (ui + backend)
#
# Особенности:
#   • Автоопределение языка и фреймворка
#   • XML+CDATA с эскейпингом ]]> → ]]]]><![CDATA[>
#   • MIME-encoding для точного определения текстовых файлов
#   • Трассировка импортов для 100% точности UI-экстракции
#   • Генерация mock-сервера и standalone package.json для UI
#   • Инструкция по чанкованию (7 файлов за шаг)
#===============================================================================

set -uo pipefail

PROJECT_ROOT=""
MODE="full"

while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:-full}"; shift 2 ;;
    --mode=*) MODE="${1#--mode=}"; shift ;;
    --project) PROJECT_ROOT="${2:-}"; shift 2 ;;
    --project=*) PROJECT_ROOT="${1#--project=}"; shift ;;
    -h|--help)
      echo "Usage: $0 [--mode full|ui|backend|split] [--project /path/to/project]"
      echo ""
      echo "Modes:"
      echo "  full    — весь проект целиком (по умолчанию)"
      echo "  ui      — только UI/UX как standalone проект"
      echo "  backend — только backend"
      echo "  split   — оба проекта отдельно (ui + backend)"
      echo ""
      echo "If --project is omitted, uses current working directory."
      exit 0 ;;
    *) shift ;;
  esac
done

if [ -z "$PROJECT_ROOT" ]; then
  PROJECT_ROOT="$(pwd)"
fi
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd)" || {
  echo "ERROR: project directory not found: $PROJECT_ROOT" >&2; exit 1
}

TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
NAME="$(basename "${PROJECT_ROOT}")"

case "$MODE" in
  full|ui|backend|split) ;;
  *) echo "ERROR: unknown mode '$MODE'. Use: full|ui|backend|split" >&2; exit 1 ;;
esac

OUTPUT_FULL="${PROJECT_ROOT}/project-migration-prompt.md"
OUTPUT_UI="${PROJECT_ROOT}/project-migration-ui.md"
OUTPUT_BACKEND="${PROJECT_ROOT}/project-migration-backend.md"
BUNDLE_FILE="$(mktemp /tmp/migration-bundle-XXXXXX.xml)"
BUNDLE_UI_FILE="$(mktemp /tmp/migration-bundle-ui-XXXXXX.xml)"
BUNDLE_BACKEND_FILE="$(mktemp /tmp/migration-bundle-backend-XXXXXX.xml)"
UI_DEPS_FILE="$(mktemp /tmp/migration-ui-deps-XXXXXX.json)"
API_ROUTES_FILE="$(mktemp /tmp/migration-api-routes-XXXXXX.txt)"

cleanup() { rm -f "$BUNDLE_FILE" "$BUNDLE_UI_FILE" "$BUNDLE_BACKEND_FILE" "$UI_DEPS_FILE" "$API_ROUTES_FILE"; }
trap cleanup EXIT

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

# ─── 2. Автоопределение фреймворка ──────────────────────────────────────────

detect_framework() {
  [ ! -f "${PROJECT_ROOT}/package.json" ] && { echo "none"; return 0; }

  local deps
  deps="$(node -e "
    const p = require('${PROJECT_ROOT}/package.json');
    const all = Object.keys(Object.assign({}, p.dependencies || {}, p.devDependencies || {}));
    console.log(all.join(' '));
  " 2>/dev/null || echo "")"

  if echo "$deps" | grep -qw 'nuxt'; then echo "nuxt"
  elif echo "$deps" | grep -qw 'next'; then echo "next"
  elif echo "$deps" | grep -qw '@angular/core'; then echo "angular"
  elif echo "$deps" | grep -qw 'svelte'; then echo "svelte"
  elif echo "$deps" | grep -qw 'vue'; then echo "vue"
  elif echo "$deps" | grep -qw 'react'; then echo "react"
  elif echo "$deps" | grep -qw 'solid-js'; then echo "solid"
  elif echo "$deps" | grep -qw 'preact'; then echo "preact"
  elif [ -f "${PROJECT_ROOT}/vite.config.ts" ] || [ -f "${PROJECT_ROOT}/vite.config.js" ]; then echo "vite-vanilla"
  else echo "none"; fi
}

detect_framework_meta() {
  local fw="$1"
  local has_ts="no"
  local has_router="no"
  local has_state="no"
  local has_css_framework="none"
  local has_component_lib="none"

  [ -f "${PROJECT_ROOT}/tsconfig.json" ] && has_ts="yes"

  local deps
  deps="$(node -e "
    const p = require('${PROJECT_ROOT}/package.json');
    const all = Object.keys(Object.assign({}, p.dependencies || {}, p.devDependencies || {}));
    console.log(all.join(' '));
  " 2>/dev/null || echo "")"

  echo "$deps" | grep -qwE 'vue-router|react-router|@angular/router|@sveltejs/router' && has_router="yes"
  echo "$deps" | grep -qwE 'pinia|vuex|redux|zustand|mobx|jotai|recoil|@ngrx/store|nanostores' && has_state="yes"
  echo "$deps" | grep -qwE 'tailwindcss' && has_css_framework="tailwind"
  echo "$deps" | grep -qwE 'bootstrap' && has_css_framework="bootstrap"
  echo "$deps" | grep -qwE '@mui|@chakra-ui|ant-design|@ant-design|element-plus|vuetify|naive-ui|@headlessui|radix-ui|shadcn' && has_component_lib="detected"

  echo "typescript=${has_ts} router=${has_router} state=${has_state} css_framework=${has_css_framework} component_lib=${has_component_lib}"
}

# ─── 3. Классификация файлов: ui / backend / shared ────────────────────────

UI_EXTENSIONS="vue jsx tsx svelte html css scss less styl svg
               astro mjt nunjucks hbs handlebars pug twig"

UI_DIR_PATTERNS="components views pages styles assets public layouts
                 composables hooks stores router directives filters
                 plugins themes icons fonts mocks fixtures"

BACKEND_DIR_PATTERNS="api server routes controllers models services
                      middleware database migrations seeds repository
                      dto schemas entities listeners workers tasks
                      commands handlers events subscribers"

SHARED_DIR_PATTERNS="types interfaces utils helpers constants config
                     shared lib common shared-types"

BACKEND_EXTENSIONS="py go rs java rb php cs c cpp h hpp"

classify_file() {
  local relpath="$1"
  local filename="$(basename "$relpath")"
  local ext="${filename##*.}"
  local dir="$(dirname "$relpath")"

  if [ "$filename" = "package.json" ] || [ "$filename" = "tsconfig.json" ] \
     || [ "$filename" = "jsconfig.json" ]; then
    echo "shared"; return 0
  fi

  if [ "$filename" = "vite.config.ts" ] || [ "$filename" = "vite.config.js" ] \
     || [ "$filename" = "webpack.config.js" ] || [ "$filename" = "webpack.config.ts" ] \
     || [ "$filename" = "tailwind.config.js" ] || [ "$filename" = "tailwind.config.ts" ] \
     || [ "$filename" = "postcss.config.js" ] || [ "$filename" = "postcss.config.ts" ] \
     || [ "$filename" = ".babelrc" ] || [ "$filename" = "babel.config.js" ] \
     || [ "$filename" = "next.config.js" ] || [ "$filename" = "next.config.mjs" ] \
     || [ "$filename" = "nuxt.config.ts" ] || [ "$filename" = "nuxt.config.js" ] \
     || [ "$filename" = "angular.json" ] || [ "$filename" = "svelte.config.js" ] \
     || [ "$filename" = "index.html" ]; then
    echo "ui"; return 0
  fi

  for ue in $UI_EXTENSIONS; do
    [ "$ext" = "$ue" ] && { echo "ui"; return 0; }
  done

  for ud in $UI_DIR_PATTERNS; do
    echo "$dir" | grep -qE "(^|/)$ud(/|$)" && { echo "ui"; return 0; }
  done

  for bd in $BACKEND_DIR_PATTERNS; do
    echo "$dir" | grep -qE "(^|/)$bd(/|$)" && { echo "backend"; return 0; }
  done

  for be in $BACKEND_EXTENSIONS; do
    [ "$ext" = "$be" ] && { echo "backend"; return 0; }
  done

  for sd in $SHARED_DIR_PATTERNS; do
    echo "$dir" | grep -qE "(^|/)$sd(/|$)" && { echo "shared"; return 0; }
  done

  echo "shared"
}

# ─── 4. Трассировка импортов (для 100% точности UI) ─────────────────────────

trace_imports() {
  local relpath="$1"
  local visited_file="$2"
  local deps_file
  deps_file="$(mktemp /tmp/migration-trace-deps-XXXXXX)"
  : > "$deps_file"

  grep -oE "(from|import)[[:space:]]+['\"]\.{1,2}/[^'\"]+['\"]" "$PROJECT_ROOT/$relpath" 2>/dev/null \
    | sed "s/.*['\"]//;s/['\"]//;s/\.\(ts\|tsx\|js\|jsx\|vue\|svelte\)$//;s/\/index$//" \
    > "$deps_file"

  while IFS= read -r imp; do
    [ -z "$imp" ] && continue
    local base_dir
    base_dir="$(dirname "$relpath")"
    local resolved="${base_dir}/${imp}"
    resolved="$(echo "$resolved" | sed 's|/\./|/|g;s|/\([^/]*\)/\.\./|/|g;s|^/||')"

    for ext in "" ".ts" ".tsx" ".js" ".jsx" ".vue" ".svelte" "/index.ts" "/index.tsx" "/index.js" "/index.jsx"; do
      local candidate="${resolved}${ext}"
      if [ -f "${PROJECT_ROOT}/${candidate}" ]; then
        if ! grep -qxF "$candidate" "$visited_file" 2>/dev/null; then
          echo "$candidate" >> "$visited_file"
          echo "$candidate"
          trace_imports "$candidate" "$visited_file"
        fi
        break
      fi
    done
  done < "$deps_file"
  rm -f "$deps_file"
}

get_ui_transitive_deps() {
  local visited_file
  visited_file="$(mktemp /tmp/migration-trace-visited-XXXXXX)"
  : > "$visited_file"

  local ui_initial
  ui_initial="$(mktemp /tmp/migration-trace-initial-XXXXXX)"
  : > "$ui_initial"

  find "${PROJECT_ROOT}" -type f \
    ! -path '*/node_modules/*' ! -path '*/.git/*' ! -path '*/dist/*' ! -path '*/build/*' \
    ! -path '*/target/*' ! -path '*/__pycache__/*' ! -path '*/.next/*' \
    ! -path '*/.svelte-kit/*' ! -path '*/.expo/*' ! -path '*/venv/*' \
    ! -path '*/.venv/*' ! -path '*/.kilo/*' ! -path '*/.idea/*' \
    ! -path '*/.vscode/*' ! -path '*/prompts/*' ! -path '*/scripts/*' \
    ! -path '*/tests/*' ! -path '*/media/*' ! -path '*/playlists/*' \
    -print0 2>/dev/null \
    | while IFS= read -r -d '' fullpath; do
        relpath="${fullpath#${PROJECT_ROOT}/}"
        cls="$(classify_file "$relpath")"
        if [ "$cls" = "ui" ]; then
          echo "$relpath"
        fi
      done > "$ui_initial"

  while IFS= read -r relpath; do
    [ -z "$relpath" ] && continue
    echo "$relpath" >> "$visited_file"
    echo "$relpath"
    trace_imports "$relpath" "$visited_file"
  done < "$ui_initial"

  rm -f "$visited_file" "$ui_initial"
}

# ─── 5. Извлечение frontend-зависимостей из package.json ────────────────────

extract_frontend_deps() {
  local output_file="$1"
  [ ! -f "${PROJECT_ROOT}/package.json" ] && { echo "{}" > "$output_file"; return 0; }

  node -e "
    const p = require('${PROJECT_ROOT}/package.json');
    const allDeps = Object.assign({}, p.dependencies || {}, p.devDependencies || {});

    const uiPatterns = [
      'react', 'react-dom', 'react-router', 'react-router-dom', 'redux', '@reduxjs/toolkit',
      'zustand', 'mobx', 'recoil', 'jotai', 'effector',
      'vue', 'vue-router', 'vuex', 'pinia', 'vue-i18n',
      '@angular/core', '@angular/cli', '@angular/material',
      'svelte', '@sveltejs/kit',
      'next', 'nuxt', 'nuxt3',
      'vite', 'webpack', '@vitejs/plugin-vue', '@vitejs/plugin-react',
      'tailwindcss', 'postcss', 'autoprefixer', 'sass', 'less', 'stylus',
      '@mui/material', '@chakra-ui/react', 'ant-design', '@ant-design',
      'element-plus', 'vuetify', 'naive-ui', 'primevue',
      '@headlessui/react', '@headlessui/vue',
      'radix-ui', '@radix-ui/react-', 'class-variance-authority', 'clsx', 'tailwind-merge',
      'lucide-react', '@heroicons/react', 'framer-motion', 'react-hook-form',
      '@tanstack/react-query', '@tanstack/vue-query',
      'axios', 'socket.io-client', 'graphql', '@apollo/client',
      'typescript', '@types/', 'tslib',
      'vite-plugin-', 'unplugin-',
      '@iconify/', '@vueuse/', 'ahooks', 'react-use',
      'solid-js', 'preact', 'astro',
      '@storybook/', 'storybook',
      'msw', 'vitest', '@testing-library/',
      'eslint', 'prettier', 'stylelint',
      '@vue/tsconfig', 'vue-tsc'
    ];

    const uiDeps = {};
    const uiDevDeps = {};
    const depKeys = Object.keys(p.dependencies || {});
    const devDepKeys = Object.keys(p.devDependencies || {});

    function isUiPkg(name) {
      return uiPatterns.some(pat => {
        if (pat.endsWith('/') || pat.endsWith('-')) {
          return name.startsWith(pat);
        }
        return name === pat || name.startsWith(pat + '/');
      });
    }

    depKeys.forEach(k => {
      if (isUiPkg(k)) uiDeps[k] = p.dependencies[k];
    });
    devDepKeys.forEach(k => {
      if (isUiPkg(k)) uiDevDeps[k] = p.devDependencies[k];
    });

    const result = {
      name: (p.name || 'ui-project') + '-ui',
      version: p.version || '1.0.0',
      private: true,
      type: p.type || undefined,
      scripts: {
        dev: 'vite',
        build: 'vite build',
        preview: 'vite preview',
        mock: 'node mock-server.js'
      },
      dependencies: Object.keys(uiDeps).length ? uiDeps : undefined,
      devDependencies: Object.keys(uiDevDeps).length ? uiDevDeps : undefined
    };

    // Очистка undefined
    Object.keys(result).forEach(k => result[k] === undefined && delete result[k]);

    const fs = require('fs');
    fs.writeFileSync('$output_file', JSON.stringify(result, null, 2));
  " 2>/dev/null || echo '{}' > "$output_file"
}

# ─── 6. Обнаружение API-маршрутов в UI-коде ─────────────────────────────────

detect_api_routes() {
  local output_file="$1"
  : > "$output_file"

  find "${PROJECT_ROOT}" -type f \( -name '*.ts' -o -name '*.tsx' -o -name '*.js' -o -name '*.jsx' \
    -o -name '*.vue' -o -name '*.svelte' \) \
    ! -path '*/node_modules/*' ! -path '*/.git/*' ! -path '*/dist/*' \
    ! -path '*/scripts/*' ! -path '*/tests/*' \
    -print0 2>/dev/null \
    | xargs -0 grep -hoE "(fetch|axios\.(get|post|put|delete|patch|head)|api\.\w+|http\.\w+)\s*\(\s*['\"\`][^'\"\`]*['\"\`]" 2>/dev/null \
    | sed "s/.*['\"\`]//;s/['\"\`]$//" \
    | sort -u >> "$output_file"

  find "${PROJECT_ROOT}" -type f \( -name '*.ts' -o -name '*.tsx' -o -name '*.js' -o -name '*.jsx' \
    -o -name '*.vue' -o -name '*.svelte' \) \
    ! -path '*/node_modules/*' ! -path '*/.git/*' ! -path '*/dist/*' \
    ! -path '*/scripts/*' ! -path '*/tests/*' \
    -print0 2>/dev/null \
    | xargs -0 grep -hoE "(url|endpoint|path|uri|baseURL)\s*:\s*['\"\`][^'\"\`]*['\"\`]" 2>/dev/null \
    | sed "s/.*['\"\`]//;s/['\"\`]$//" \
    | grep -v '^\.' \
    | sort -u >> "$output_file"

  sort -u "$output_file" -o "$output_file"
}

# ─── 7. Генерация mock-сервера ──────────────────────────────────────────────

generate_mock_server() {
  local api_file="$1"
  cat << 'MOCKEOF'
// mock-server.js — Auto-generated API mock server for standalone UI
// Install: npm install express cors --save-dev
// Run:     node mock-server.js
const express = require('express');
const cors = require('cors');
const app = express();
const PORT = 3001;

app.use(cors());
app.use(express.json());

// ─── Health ───
app.get('/api/health', (req, res) => res.json({ status: 'ok', timestamp: new Date().toISOString() }));

MOCKEOF

  while IFS= read -r route; do
    [ -z "$route" ] && continue
    local clean_route
    clean_route="$(echo "$route" | sed 's/`.*//;s/${.*}/:param/g;s/^ *//;s/ *$//')"
    [ -z "$clean_route" ] && continue

    if echo "$clean_route" | grep -qE '^/api'; then
      local method="GET"
      echo "$clean_route" | grep -qiE '(create|add|post|save|login|register|upload|send)' && method="POST"
      echo "$clean_route" | grep -qiE '(update|edit|patch|put|modify)' && method="PUT"
      echo "$clean_route" | grep -qiE '(delete|remove)' && method="DELETE"

      local handler_data
      case "$method" in
        GET) handler_data='[{ "id": 1, "mock": true }]' ;;
        POST) handler_data='{" id": 1, "created": true }' ;;
        PUT) handler_data='{ "id": 1, "updated": true }' ;;
        DELETE) handler_data='{ "deleted": true }' ;;
      esac

      local route_escaped
      route_escaped="$(echo "$clean_route" | sed 's/:\w\+/:param/g')"

      cat << MOCKROUTE
// ${method} ${route_escaped}
app.${method,,}('${route_escaped}', (req, res) => {
  console.log('[MOCK] ${method} ${route_escaped}', req.params, req.body);
  res.json(${handler_data});
});

MOCKROUTE
    fi
  done < "$api_file"

  cat << 'MOCKFOOTER'

// ─── Catch-all ───
app.all('/api/*', (req, res) => {
  console.log('[MOCK 404]', req.method, req.url);
  res.status(404).json({ error: 'Mock not found', method: req.method, url: req.url });
});

app.listen(PORT, () => {
  console.log(\`[MOCK API] Running on http://localhost:\${PORT}\`);
  console.log('[MOCK API] Add routes above the catch-all as needed');
});
MOCKFOOTER
}

# ─── 8. Список исключений для find ──────────────────────────────────────────

FIND_EXCLUDES="\
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
! -path '*/playlists/*'"

FIND_NAME_EXCLUDES="\
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
! -name 'project-migration-ui.md' \
! -name 'project-migration-backend.md' \
! -name '.migration-bundle*'"

# ─── 9. Подсчёт строк ───────────────────────────────────────────────────────

count_lines() {
  local pattern="$1"
  local file="$2"
  local result
  result="$(grep -c "$pattern" "$file" 2>/dev/null)" || true
  echo "${result:-0}"
}

# ─── 10. Сбор файлов в XML+CDATA (общий) ────────────────────────────────────

build_bundle() {
  local bundle_file="$1"
  shift
  local filter="$1"

  : > "$bundle_file"
  echo '<files>' >> "$bundle_file"

  while IFS= read -r -d '' fullpath; do
    relpath="${fullpath#${PROJECT_ROOT}/}"
    size="$(wc -c < "$fullpath" | tr -d ' ')"

    [ "$size" -eq 0 ] && { echo "  <!-- SKIP (empty): ${relpath} -->" >> "$bundle_file"; continue; }
    [ "$size" -gt 512000 ] && { echo "  <!-- SKIP (too large: ${size}B): ${relpath} -->" >> "$bundle_file"; continue; }

    if [ -n "$filter" ] && [ "$filter" != "all" ]; then
      cls="$(classify_file "$relpath")"
      case "$filter" in
        ui)
          [ "$cls" = "backend" ] && continue
          ;;
        backend)
          [ "$cls" = "ui" ] && continue
          ;;
      esac
    fi

    if file -b --mime-encoding "$fullpath" | grep -qv 'binary'; then
      echo "  <file path=\"${relpath}\">" >> "$bundle_file"
      echo '  <![CDATA[' >> "$bundle_file"
      sed 's/]]>/]]]]><![CDATA[>/g' "$fullpath" >> "$bundle_file"
      echo '' >> "$bundle_file"
      echo '  ]]>' >> "$bundle_file"
      echo "  </file>" >> "$bundle_file"
      echo "" >> "$bundle_file"
    else
      echo "  <!-- SKIP (binary): ${relpath} -->" >> "$bundle_file"
    fi
  done < <(eval find "${PROJECT_ROOT}" -type f ${FIND_EXCLUDES} ${FIND_NAME_EXCLUDES} -print0 2>/dev/null | sort -z)

  echo '</files>' >> "$bundle_file"
}

# ─── 11. Сбор UI с трассировкой импортов ────────────────────────────────────

build_ui_bundle_with_tracing() {
  local bundle_file="$1"

  : > "$bundle_file"
  echo '<files>' >> "$bundle_file"

  local ui_files_list
  ui_files_list="$(mktemp /tmp/migration-ui-filelist-XXXXXX)"
  : > "$ui_files_list"

  get_ui_transitive_deps > "$ui_files_list"

  local total_included=0

  while IFS= read -r -d '' fullpath; do
    relpath="${fullpath#${PROJECT_ROOT}/}"
    size="$(wc -c < "$fullpath" | tr -d ' ')"

    [ "$size" -eq 0 ] && { echo "  <!-- SKIP (empty): ${relpath} -->" >> "$bundle_file"; continue; }
    [ "$size" -gt 512000 ] && { echo "  <!-- SKIP (too large: ${size}B): ${relpath} -->" >> "$bundle_file"; continue; }

    cls="$(classify_file "$relpath")"
    is_traced=""
    grep -qxF "$relpath" "$ui_files_list" 2>/dev/null && is_traced="yes"

    if [ "$cls" = "ui" ] || [ "$cls" = "shared" ] || [ -n "$is_traced" ]; then
      if file -b --mime-encoding "$fullpath" | grep -qv 'binary'; then
        echo "  <file path=\"${relpath}\">" >> "$bundle_file"
        echo '  <![CDATA[' >> "$bundle_file"
        sed 's/]]>/]]]]><![CDATA[>/g' "$fullpath" >> "$bundle_file"
        echo '' >> "$bundle_file"
        echo '  ]]>' >> "$bundle_file"
        echo "  </file>" >> "$bundle_file"
        echo "" >> "$bundle_file"
        total_included=$((total_included + 1))
      else
        echo "  <!-- SKIP (binary): ${relpath} -->" >> "$bundle_file"
      fi
    fi
  done < <(eval find "${PROJECT_ROOT}" -type f ${FIND_EXCLUDES} ${FIND_NAME_EXCLUDES} -print0 2>/dev/null | sort -z)

  echo '</files>' >> "$bundle_file"
  rm -f "$ui_files_list"
}

# ─── 12. Генерация промпта: полный проект ───────────────────────────────────

build_prompt_full() {
  local bundle_file="$1"
  local output_file="$2"
  local tf bs binary_lines

  tf="$(count_lines '<file path=' "$bundle_file")"
  bs="$(count_lines 'SKIP (binary)' "$bundle_file")"
  binary_lines="$(grep 'SKIP (binary)' "$bundle_file" 2>/dev/null | sed 's/.*: \(.*\) -->/\1/' | head -20 || true)"

  : > "$output_file"

  cat >> "$output_file" << EOF
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
- **Framework:** ${FRAMEWORK}
- **Captured at:** ${TIMESTAMP}
- **Total files:** ${tf}
- **Skipped binary:** ${bs}
- **Install command:** \`${INSTALL_CMD}\`
- **Dev command:** \`${DEV_CMD}\`

### Directory Structure
\`\`\`
EOF

  eval find "${PROJECT_ROOT}" -type d \
    ${FIND_EXCLUDES} \
    | sed "s|${PROJECT_ROOT}/||" | sort | head -50 >> "$output_file"

  cat >> "$output_file" << 'EOF'
```
EOF

  cat "$bundle_file" >> "$output_file"

  if [ -n "$binary_lines" ]; then
    echo "" >> "$output_file"
    echo "---" >> "$output_file"
    echo "" >> "$output_file"
    echo "## Skipped Binary Files (create valid placeholders)" >> "$output_file"
    echo "" >> "$output_file"
    while IFS= read -r line; do
      [ -n "$line" ] && echo "- \`${line}\`" >> "$output_file"
    done <<< "$binary_lines"
    echo "" >> "$output_file"
    echo "Create valid 1x1 transparent PNG/placeholder for each. Do NOT use 0-byte files." >> "$output_file"
    echo "" >> "$output_file"
  fi

  cat >> "$output_file" << EOF
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

# ─── 13. Генерация промпта: UI standalone ───────────────────────────────────

build_prompt_ui() {
  local bundle_file="$1"
  local output_file="$2"
  local tf bs binary_lines

  tf="$(count_lines '<file path=' "$bundle_file")"
  bs="$(count_lines 'SKIP (binary)' "$bundle_file")"
  binary_lines="$(grep 'SKIP (binary)' "$bundle_file" 2>/dev/null | sed 's/.*: \(.*\) -->/\1/' | head -20 || true)"

  local ui_pkg_content
  ui_pkg_content="$(cat "$UI_DEPS_FILE" 2>/dev/null || echo '{}')"

  local api_routes
  api_routes="$(cat "$API_ROUTES_FILE" 2>/dev/null || echo "")"

  local mock_server
  mock_server="$(generate_mock_server "$API_ROUTES_FILE")"

  local fw_meta
  fw_meta="$(detect_framework_meta "$FRAMEWORK")"

  : > "$output_file"

  cat >> "$output_file" << EOF
# MASTER PROMPT: 100% Exact UI/UX Copy of "${NAME}"

This prompt contains the COMPLETE UI/UX source code extracted from **${NAME}**.
Send it to any AI agent to recreate the **frontend as a standalone project** identically.

The UI project is **fully self-contained**:
- Includes mock API server for independent development
- All shared dependencies (types, utils, constants) are included via import tracing
- Ready to run without the original backend

---

## UI Project Info

- **Original project:** ${NAME}
- **Framework:** ${FRAMEWORK}
- **Framework meta:** ${fw_meta}
- **Extraction timestamp:** ${TIMESTAMP}
- **Total UI files:** ${tf}
- **Binary skipped:** ${bs}

---

## STEP 0: Create Project Skeleton

First, create the project root directory and these **auto-generated** files:

### package.json (auto-extracted frontend deps)
\`\`\`json
${ui_pkg_content}
\`\`\`

### vite.config.ts (standalone UI dev config)
\`\`\`typescript
import { defineConfig } from 'vite'
EOF

  case "$FRAMEWORK" in
    vue|nuxt)
      cat >> "$output_file" << 'VITEEOF'
import vue from '@vitejs/plugin-vue'
VITEEOF
      ;;
    react|next)
      cat >> "$output_file" << 'VITEEOF'
import react from '@vitejs/plugin-react'
VITEEOF
      ;;
    svelte)
      cat >> "$output_file" << 'VITEEOF'
import { svelte } from '@sveltejs/vite-plugin-svelte'
VITEEOF
      ;;
  esac

  cat >> "$output_file" << EOF
// Add framework plugin above if available in dependencies

export default defineConfig({
  plugins: [],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:3001',
        changeOrigin: true,
      },
    },
  },
  resolve: {
    alias: {
      '@': '/src',
    },
  },
})
\`\`\`

### mock-server.js (auto-generated API mock)
\`\`\`javascript
${mock_server}
\`\`\`

### .env.example
\`\`\`
VITE_API_URL=http://localhost:3001
\`\`\`

EOF

  if [ -n "$api_routes" ]; then
    cat >> "$output_file" << 'EOF'
### Detected API Routes
The following API endpoints were auto-detected from UI source code:
```
EOF
    echo "$api_routes" >> "$output_file"
    cat >> "$output_file" << 'EOF'
```
These are already wired into the mock server above. Extend as needed.

EOF
  fi

  cat >> "$output_file" << 'EOF'
---

## RULES — YOU MUST OBEY ALL OF THEM

1. **NO MODIFICATIONS:** Recreate every file character-for-character. Do not refactor, optimize, or modernize. Keep ALL comments (including non-English), ALL unused variables, ALL console.log statements, ALL commented-out code.

2. **CHUNK YOUR WORK [IMPORTANT]:** If there are more than 7 files, do NOT try to create them all at once. Create 7 files using your tools in parallel, then STOP and ask the user to type "continue". Repeat until ALL files are created.

3. **USE YOUR FILE TOOLS SILENTLY:** If you have file-editing tools (create_file, write_to_file, bash), use them. Do NOT print code blocks in chat — just execute and confirm.

4. **BIG FILE STRATEGY:** If a file inside CDATA is too large for one tool call, use append/write in chunks or generate via a script. Do not truncate.

5. **INSTALL FIRST:** Create package.json FIRST, then run `npm install` BEFORE any other files that need TypeScript/imports resolved.

6. **EXACT PATHS:** Respect `path="..."` exactly. Auto-create parent directories.

7. **BINARY PLACEHOLDERS [CRITICAL]:** For skipped binary files (marked as `<!-- SKIP (binary): ... -->`), create VALID minimal placeholders. A 0-byte .png or .ico will crash Vite/Webpack.
   - For images (.png, .jpg, .ico): create a 1x1 transparent pixel
   - Use this bash snippet for a valid 1x1 transparent PNG:
     ```bash
     printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDATx\x9cc\x62\x08\x05\x00\x00\x00\x00\xff\xff\x03\x00\x00\x04\x00\x01\x0c\x0c\x0c\x0c\x00\x00\x00\x00IEND\xaeB`\x82' > path/to/file.png
     ```

8. **EXACT VERSIONS:** Use dependency versions as specified in the auto-extracted package.json. Do not upgrade.

9. **CDATA ESCAPE FIX:** Content has `]]>` escaped as `]]]]><![CDATA[>`. Reverse this when writing: replace `]]]]><![CDATA[>` back to `]]>`.

10. **VERIFY:** After setup, run the following and confirm:
    ```bash
    npm install
    node mock-server.js &
    npm run dev
    ```
    Print "UI MIGRATION COMPLETE — all files created, dev server running on http://localhost:5173" when done.

---

## STEP 1: Create Skeleton Files

Create these files first:
1. `package.json` (from above)
2. `vite.config.ts` (from above)
3. `mock-server.js` (from above)
4. `.env.example` (from above)
5. `tsconfig.json` (from source below if exists)
6. `index.html` (from source below if exists)

---

## STEP 2: Source Code

Below is every UI/UX source file from the original project, wrapped in XML with CDATA.
All files have been validated via **import tracing** to ensure 100% of UI dependencies are included.

EOF

  cat "$bundle_file" >> "$output_file"

  if [ -n "$binary_lines" ]; then
    echo "" >> "$output_file"
    echo "---" >> "$output_file"
    echo "" >> "$output_file"
    echo "## Skipped Binary Files (create valid placeholders)" >> "$output_file"
    echo "" >> "$output_file"
    while IFS= read -r line; do
      [ -n "$line" ] && echo "- \`${line}\`" >> "$output_file"
    done <<< "$binary_lines"
    echo "" >> "$output_file"
    echo "Create valid 1x1 transparent PNG/placeholder for each. Do NOT use 0-byte files." >> "$output_file"
    echo "" >> "$output_file"
  fi

  cat >> "$output_file" << EOF
---

## End of Source Code

**Total UI files bundled:** ${tf}
**Skipped binary files:** ${bs}

### Final Instructions

1. Create skeleton files (package.json, vite.config, mock-server, .env)
2. Create ALL source files above (chunk: 7 at a time, ask "continue")
3. Create valid placeholders for skipped binary files
4. Run: \`npm install\`
5. Start mock API: \`node mock-server.js &\`
6. Start dev: \`npm run dev\`
7. Confirm: "UI MIGRATION COMPLETE — all ${tf} files created"
EOF
}

# ─── 14. Генерация промпта: Backend ─────────────────────────────────────────

build_prompt_backend() {
  local bundle_file="$1"
  local output_file="$2"
  local tf bs binary_lines

  tf="$(count_lines '<file path=' "$bundle_file")"
  bs="$(count_lines 'SKIP (binary)' "$bundle_file")"
  binary_lines="$(grep 'SKIP (binary)' "$bundle_file" 2>/dev/null | sed 's/.*: \(.*\) -->/\1/' | head -20 || true)"

  local api_routes
  api_routes="$(cat "$API_ROUTES_FILE" 2>/dev/null || echo "")"

  : > "$output_file"

  cat >> "$output_file" << EOF
# MASTER PROMPT: 100% Exact Backend Copy of "${NAME}"

This prompt contains the COMPLETE backend source code extracted from **${NAME}**.
The UI/UX has been separated into a standalone project.

---

## Backend Project Info

- **Original project:** ${NAME}
- **Language:** ${LANG}
- **Framework:** ${FRAMEWORK}
- **Extraction timestamp:** ${TIMESTAMP}
- **Total backend files:** ${tf}
- **Binary skipped:** ${bs}

EOF

  if [ -n "$api_routes" ]; then
    cat >> "$output_file" << 'EOF'
## Expected API Contract (detected from UI)
These endpoints are consumed by the separated UI project:
```
EOF
    echo "$api_routes" >> "$output_file"
    cat >> "$output_file" << 'EOF'
```
Ensure these endpoints remain available with the same request/response format.

EOF
  fi

  cat >> "$output_file" << EOF
---

## RULES — YOU MUST OBEY ALL OF THEM

1. **NO MODIFICATIONS:** Recreate every file character-for-character. Do not refactor, optimize, or modernize. Keep ALL comments (including non-English), ALL unused variables, ALL console.log statements, ALL commented-out code.

2. **CHUNK YOUR WORK [IMPORTANT]:** If there are more than 7 files, do NOT try to create them all at once. Create 7 files using your tools in parallel, then STOP and ask the user to type "continue". Repeat until ALL files are created.

3. **USE YOUR FILE TOOLS SILENTLY:** If you have file-editing tools (create_file, write_to_file, bash), use them. Do NOT print code blocks in chat — just execute and confirm.

4. **BIG FILE STRATEGY:** If a file inside CDATA is too large for one tool call, use append/write in chunks or generate via a script. Do not truncate.

5. **INSTALL FIRST:** Create package.json/requirements.txt FIRST, then run: \`${INSTALL_CMD}\`

6. **EXACT PATHS:** Respect \`path="..."\` exactly. Auto-create parent directories.

7. **EXACT VERSIONS:** Use dependency versions as specified. Do not upgrade.

8. **CDATA ESCAPE FIX:** Content has \`]]>\` escaped as \`]]]]><![CDATA[>\`. Reverse this when writing.

9. **VERIFY:** After setup, print "BACKEND MIGRATION COMPLETE — all ${tf} files created" when done.

---

## Source Code

EOF

  cat "$bundle_file" >> "$output_file"

  if [ -n "$binary_lines" ]; then
    echo "" >> "$output_file"
    echo "---" >> "$output_file"
    echo "" >> "$output_file"
    echo "## Skipped Binary Files (create valid placeholders)" >> "$output_file"
    echo "" >> "$output_file"
    while IFS= read -r line; do
      [ -n "$line" ] && echo "- \`${line}\`" >> "$output_file"
    done <<< "$binary_lines"
    echo "" >> "$output_file"
  fi

  cat >> "$output_file" << EOF
---

## End of Source Code

**Total backend files bundled:** ${tf}
**Skipped binary files:** ${bs}

### Final Instructions

1. Create ALL files above (chunk: 7 at a time, ask "continue")
2. Run install: \`${INSTALL_CMD}\`
3. Run: \`${DEV_CMD}\`
4. Confirm: "BACKEND MIGRATION COMPLETE — all ${tf} files created"
EOF
}

# ─── 15. Определение переменных ─────────────────────────────────────────────

LANG="$(detect_lang)"
INSTALL_CMD="$(detect_install_cmd "$LANG")"
DEV_CMD="$(detect_dev_cmd "$LANG")"
FRAMEWORK="$(detect_framework)"

echo "═══════════════════════════════════════════════════════"
echo "  MIGRATION PACK v3.0"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "  Project:    ${NAME}"
echo "  Language:   ${LANG}"
echo "  Framework:  ${FRAMEWORK}"
echo "  Mode:       ${MODE}"
echo "  Install:    ${INSTALL_CMD}"
echo "  Dev:        ${DEV_CMD}"
echo ""

if [ "$FRAMEWORK" = "none" ] && [ "$MODE" != "full" ]; then
  echo "WARNING: No frontend framework detected. Forcing --mode full."
  MODE="full"
fi

# ─── 16. Запуск ─────────────────────────────────────────────────────────────

case "$MODE" in
  full)
    echo "Bundling ALL source files..."
    build_bundle "$BUNDLE_FILE" "all"

    TF="$(count_lines '<file path=' "$BUNDLE_FILE")"
    BS="$(count_lines 'SKIP (binary)' "$BUNDLE_FILE")"
    echo "  Files bundled: ${TF}"
    echo "  Binary skipped: ${BS}"
    echo ""

    echo "Generating full migration prompt..."
    build_prompt_full "$BUNDLE_FILE" "$OUTPUT_FULL"

    SIZE="$(wc -c < "$OUTPUT_FULL" | tr -d ' ')"
    LINES="$(wc -l < "$OUTPUT_FULL" | tr -d ' ')"
    echo "Done!"
    echo "  Output:  ${OUTPUT_FULL}"
    echo "  Size:    $((SIZE / 1024)) KB ($SIZE bytes)"
    echo "  Lines:   ${LINES}"
    echo "  Sources: ${TF} files + ${BS} binary placeholders"
    echo ""
    echo "Next: send '${OUTPUT_FULL}' to any AI agent."
    ;;

  ui)
    echo "Extracting UI/UX files with import tracing..."
    build_ui_bundle_with_tracing "$BUNDLE_UI_FILE"

    echo "Extracting frontend dependencies..."
    extract_frontend_deps "$UI_DEPS_FILE"

    echo "Detecting API routes..."
    detect_api_routes "$API_ROUTES_FILE"
    API_COUNT="$(wc -l < "$API_ROUTES_FILE" | tr -d ' ')"
    echo "  API routes detected: ${API_COUNT}"

    TF="$(count_lines '<file path=' "$BUNDLE_UI_FILE")"
    BS="$(count_lines 'SKIP (binary)' "$BUNDLE_UI_FILE")"
    echo "  UI files bundled: ${TF}"
    echo "  Binary skipped: ${BS}"
    echo ""

    echo "Generating UI standalone migration prompt..."
    build_prompt_ui "$BUNDLE_UI_FILE" "$OUTPUT_UI"

    SIZE="$(wc -c < "$OUTPUT_UI" | tr -d ' ')"
    LINES="$(wc -l < "$OUTPUT_UI" | tr -d ' ')"
    echo "Done!"
    echo "  Output:     ${OUTPUT_UI}"
    echo "  Size:       $((SIZE / 1024)) KB ($SIZE bytes)"
    echo "  Lines:      ${LINES}"
    echo "  UI files:   ${TF} + ${BS} binary placeholders"
    echo "  API routes: ${API_COUNT} mock endpoints"
    echo ""
    echo "Next: send '${OUTPUT_UI}' to any AI agent."
    echo "The AI will recreate the UI as a standalone project."
    ;;

  backend)
    echo "Extracting backend files..."
    build_bundle "$BUNDLE_BACKEND_FILE" "backend"

    echo "Detecting API routes..."
    detect_api_routes "$API_ROUTES_FILE"

    TF="$(count_lines '<file path=' "$BUNDLE_BACKEND_FILE")"
    BS="$(count_lines 'SKIP (binary)' "$BUNDLE_BACKEND_FILE")"
    echo "  Backend files bundled: ${TF}"
    echo "  Binary skipped: ${BS}"
    echo ""

    echo "Generating backend migration prompt..."
    build_prompt_backend "$BUNDLE_BACKEND_FILE" "$OUTPUT_BACKEND"

    SIZE="$(wc -c < "$OUTPUT_BACKEND" | tr -d ' ')"
    LINES="$(wc -l < "$OUTPUT_BACKEND" | tr -d ' ')"
    echo "Done!"
    echo "  Output:         ${OUTPUT_BACKEND}"
    echo "  Size:           $((SIZE / 1024)) KB ($SIZE bytes)"
    echo "  Lines:          ${LINES}"
    echo "  Backend files:  ${TF} + ${BS} binary placeholders"
    echo ""
    echo "Next: send '${OUTPUT_BACKEND}' to any AI agent."
    ;;

  split)
    echo "═══ Phase 1: UI/UX ═══"
    echo ""
    echo "Extracting UI/UX files with import tracing..."
    build_ui_bundle_with_tracing "$BUNDLE_UI_FILE"

    echo "Extracting frontend dependencies..."
    extract_frontend_deps "$UI_DEPS_FILE"

    echo "Detecting API routes..."
    detect_api_routes "$API_ROUTES_FILE"
    API_COUNT="$(wc -l < "$API_ROUTES_FILE" | tr -d ' ')"
    echo "  API routes detected: ${API_COUNT}"

    TF_UI="$(count_lines '<file path=' "$BUNDLE_UI_FILE")"
    BS_UI="$(count_lines 'SKIP (binary)' "$BUNDLE_UI_FILE")"
    echo "  UI files: ${TF_UI} + ${BS_UI} binary"
    echo ""

    echo "Generating UI standalone prompt..."
    build_prompt_ui "$BUNDLE_UI_FILE" "$OUTPUT_UI"

    SIZE_UI="$(wc -c < "$OUTPUT_UI" | tr -d ' ')"
    echo "  Output: ${OUTPUT_UI} ($((SIZE_UI / 1024)) KB)"
    echo ""

    echo "═══ Phase 2: Backend ═══"
    echo ""
    echo "Extracting backend files..."
    build_bundle "$BUNDLE_BACKEND_FILE" "backend"

    TF_BE="$(count_lines '<file path=' "$BUNDLE_BACKEND_FILE")"
    BS_BE="$(count_lines 'SKIP (binary)' "$BUNDLE_BACKEND_FILE")"
    echo "  Backend files: ${TF_BE} + ${BS_BE} binary"
    echo ""

    echo "Generating backend prompt..."
    build_prompt_backend "$BUNDLE_BACKEND_FILE" "$OUTPUT_BACKEND"

    SIZE_BE="$(wc -c < "$OUTPUT_BACKEND" | tr -d ' ')"
    echo "  Output: ${OUTPUT_BACKEND} ($((SIZE_BE / 1024)) KB)"
    echo ""

    echo "═══════════════════════════════════════════════════════"
    echo "  SPLIT COMPLETE"
    echo "═══════════════════════════════════════════════════════"
    echo ""
    echo "  UI files:      ${TF_UI} (+${BS_UI} binary, ${API_COUNT} mock API)"
    echo "  Backend files: ${TF_BE} (+${BS_BE} binary)"
    echo ""
    echo "  Outputs:"
    echo "    1. ${OUTPUT_UI}       — send to AI for standalone UI"
    echo "    2. ${OUTPUT_BACKEND}  — send to AI for backend"
    echo ""
    echo "  Quick start:"
    echo "    ${INSTALL_CMD}"
    echo "    ${DEV_CMD}"
    ;;
esac
