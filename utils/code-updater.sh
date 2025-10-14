#!/bin/bash

# Скрипт для обновления Visual Studio Code и связанных инструментов
# Используется в Ubuntu Installer Framework

set -e

# Функция логирования
log() {
  local level=$1
  shift
  local message="$1"
  local timestamp=$(date '+%Y-%m-%d %H:%M:%S')

  case $level in
  "INFO")
    echo -e "\033[0;32m[INFO]\033[0m [$timestamp] $message"
    ;;
  "WARN")
    echo -e "\033[1;33m[WARN]\033[0m [$timestamp] $message"
    ;;
  "ERROR")
    echo -e "\033[0;31m[ERROR]\033[0m [$timestamp] $message"
    ;;
  *)
    echo -e "[$level] [$timestamp] $message"
    ;;
  esac
}

# Функция проверки наличия VS Code
check_vscode() {
  log "INFO" "Проверка наличия Visual Studio Code"

  if command -v code &>/dev/null; then
    local version=$(code --version | head -n1)
    log "INFO" "Visual Studio Code установлен, версия: $version"
    return 0
  else
    log "WARN" "Visual Studio Code не установлен"
    return 1
  fi
}

# Функция установки VS Code
install_vscode() {
  log "INFO" "Установка Visual Studio Code"

  # Проверка, установлен ли уже VS Code
  if check_vscode; then
    log "INFO" "Visual Studio Code уже установлен"
    return 0
  fi

  # Скачивание и установка VS Code
  local temp_dir=$(mktemp -d)
  local deb_file="$temp_dir/code.deb"

  log "INFO" "Скачивание Visual Studio Code"
  wget -O "$deb_file" "https://code.visualstudio.com/sha/download?build=stable&os=linux-deb-x64"

  log "INFO" "Установка Visual Studio Code"
  sudo dpkg -i "$deb_file"
  sudo apt-get install -f -y

  # Очистка временных файлов
  rm -rf "$temp_dir"

  log "INFO" "Visual Studio Code установлен"
}

# Функция обновления VS Code
update_vscode() {
  log "INFO" "Обновление Visual Studio Code"

  # Проверка наличия VS Code
  if ! check_vscode; then
    log "WARN" "Visual Studio Code не установлен, установка"
    install_vscode
    return 0
  fi

  # Обновление VS Code через APT
  log "INFO" "Обновление Visual Studio Code через APT"
  sudo apt update
  sudo apt upgrade -y code

  local new_version=$(code --version | head -n1)
  log "INFO" "Visual Studio Code обновлен, новая версия: $new_version"
}

# Функция установки расширений VS Code
install_vscode_extensions() {
  log "INFO" "Установка расширений Visual Studio Code"

  # Проверка наличия VS Code
  if ! check_vscode; then
    log "ERROR" "Visual Studio Code не установлен"
    return 1
  fi

  # Список рекомендуемых расширений
  local extensions=(
    "ms-python.python"
    "ms-vscode.vscode-json"
    "ms-vscode.vscode-yaml"
    "ms-vscode.sublime-keybindings"
    "ms-vscode.hexeditor"
    "ms-vscode.remote-explorer"
    "ms-vscode-remote.remote-ssh"
    "ms-vscode-remote.remote-containers"
    "ms-vscode-remote.remote-wsl"
    "ms-azuretools.vscode-docker"
    "ms-vscode.live-server"
    "ms-vscode.powershell"
    "ms-vscode.vs-keybindings"
    "ms-vscode.vscode-theme-seti"
    "ms-vscode.wordcount"
    "ms-vscode.markdown-language-features"
    "ms-vscode.theme-markdownkit"
    "ms-vscode.theme-tomorrowkit"
    "ms-vscode.anycode"
    "ms-vscode.anycode-go"
    "ms-vscode.anycode-java"
    "ms-vscode.anycode-python"
    "ms-vscode.anycode-rust"
    "ms-vscode.anycode-typescript"
    "ms-vscode.anycode-cpp"
    "ms-vscode.anycode-csharp"
    "ms-vscode.anycode-php"
    "ms-vscode.anycode-ruby"
    "ms-vscode.anycode-swift"
    "ms-vscode.anycode-kotlin"
    "ms-vscode.anycode-scala"
    "ms-vscode.anycode-perl"
    "ms-vscode.anycode-r"
    "ms-vscode.anycode-lua"
    "ms-vscode.anycode-haskell"
    "ms-vscode.anycode-erlang"
    "ms-vscode.anycode-elixir"
    "ms-vscode.anycode-clojure"
    "ms-vscode.anycode-fsharp"
    "ms-vscode.anycode-elm"
    "ms-vscode.anycode-ocaml"
    "ms-vscode.anycode-purescript"
    "ms-vscode.anycode-idris"
    "ms-vscode.anycode-agda"
    "ms-vscode.anycode-coq"
    "ms-vscode.anycode-isabelle"
    "ms-vscode.anycode-lean"
    "ms-vscode.anycode-mathematica"
    "ms-vscode.anycode-matlab"
    "ms-vscode.anycode-octave"
    "ms-vscode.anycode-julia"
    "ms-vscode.anycode-fortran"
    "ms-vscode.anycode-d"
    "ms-vscode.anycode-nim"
    "ms-vscode.anycode-crystal"
    "ms-vscode.anycode-zig"
    "ms-vscode.anycode-v"
    "ms-vscode.anycode-gleam"
  )

  # Установка расширений
  for extension in "${extensions[@]}"; do
    log "INFO" "Установка расширения: $extension"
    code --install-extension "$extension" || log "WARN" "Не удалось установить расширение: $extension"
  done

  log "INFO" "Установка расширений Visual Studio Code завершена"
}

# Функция обновления инструментов разработки
update_dev_tools() {
  log "INFO" "Обновление инструментов разработки"

  # Обновление Node.js и npm
  if command -v node &>/dev/null; then
    log "INFO" "Обновление Node.js и npm"

    # Проверяем, установлен ли NVM
    if [ -f "$HOME/.nvm/nvm.sh" ]; then
      NVM_DIR="$HOME/.nvm"
      [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
      [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"

      if command -v npm &>/dev/null; then
        log "INFO" "Используем npm из NVM: $(npm --version)"
        npm install -g npm@latest
        # Проверяем версию Node.js и обновляем только если нужно
        NODE_VERSION=$(node --version)
        if [[ $NODE_VERSION =~ ^v(2[0-9]|3[0-9]). ]]; then
          log "INFO" "Текущая версия Node.js совместима: $NODE_VERSION"
        else
          log "INFO" "Обновление Node.js с $NODE_VERSION до последней версии"
          npm install -g node@latest
        fi
      else
        # Если активация NVM не помогла, пробуем использовать npm напрямую из NVM
        NVM_NPM_PATH="$HOME/.nvm/versions/node/v24.6.0/bin/npm"
        if [ -f "$NVM_NPM_PATH" ]; then
          log "INFO" "Используем npm напрямую из NVM: $($NVM_NPM_PATH --version)"
          $NVM_NPM_PATH install -g npm@latest
          # Проверяем версию Node.js и обновляем только если нужно
          NODE_VERSION=$(node --version)
          if [[ $NODE_VERSION =~ ^v(2[0-9]|3[0-9]). ]]; then
            log "INFO" "Текущая версия Node.js совместима: $NODE_VERSION"
          else
            log "INFO" "Обновление Node.js с $NODE_VERSION до последней версии"
            $NVM_NPM_PATH install -g node@latest
          fi
        else
          log "WARN" "npm не найден в NVM по пути $NVM_NPM_PATH"
        fi
      fi
    else
      # Если NVM не установлен, пробуем использовать npm напрямую из NVM (на случай, если он был установлен в другом месте)
      NVM_NPM_PATH="$HOME/.nvm/versions/node/v24.6.0/bin/npm"
      if [ -f "$NVM_NPM_PATH" ]; then
        log "INFO" "Используем npm напрямую из NVM: $($NVM_NPM_PATH --version)"
        $NVM_NPM_PATH install -g npm@latest
        # Проверяем версию Node.js и обновляем только если нужно
        NODE_VERSION=$(node --version)
        if [[ $NODE_VERSION =~ ^v(2[0-9]|3[0-9]). ]]; then
          log "INFO" "Текущая версия Node.js совместима: $NODE_VERSION"
        else
          log "INFO" "Обновление Node.js с $NODE_VERSION до последней версии"
          $NVM_NPM_PATH install -g node@latest
        fi
      else
        log "WARN" "npm не найден в системе (ни в NVM, ни напрямую)"
      fi
    fi
  else
    log "INFO" "Node.js не установлен"
  fi

  # Обновление Python пакетов
  if command -v pip3 &>/dev/null; then
    log "INFO" "Обновление Python пакетов"
    pip3 install --upgrade pip
    pip3 list --outdated --format=freeze | grep -v '^\-e' | cut -d = -f 1 | xargs -n1 pip3 install -U
  fi

  # Обновление Ruby гемов
  if command -v gem &>/dev/null; then
    log "INFO" "Обновление Ruby гемов"
    gem update --system
    gem update
  fi

  log "INFO" "Обновление инструментов разработки завершено"
}

# Основная функция
main() {
  log "INFO" "Запуск обновления Visual Studio Code и инструментов разработки"

  # Обновление VS Code
  update_vscode

  # Установка расширений VS Code
  install_vscode_extensions

  # Обновление инструментов разработки
  update_dev_tools

  log "INFO" "Обновление Visual Studio Code и инструментов разработки завершено"
}

# Запуск основной функции
main "$@"
