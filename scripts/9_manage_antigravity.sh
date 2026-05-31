#!/bin/bash

uninstall_antigravity() {
    echo "Начинаем полное удаление Antigravity CLI и всех связанных данных о Gemini..."

    # 1. Завершаем запущенные процессы agy и agentapi
    pkill -x agy 2>/dev/null
    pkill -x agentapi 2>/dev/null
    echo "Завершены активные процессы agy и agentapi"

    # 2. Удаляем исполняемые файлы и символические ссылки
    if [ -f "$HOME/.local/bin/antigravity" ] || [ -L "$HOME/.local/bin/antigravity" ]; then
        rm -f "$HOME/.local/bin/antigravity"
        echo "Удален файл/симлинк ~/.local/bin/antigravity"
    fi

    if [ -f "$HOME/.local/bin/agy" ] || [ -L "$HOME/.local/bin/agy" ]; then
        rm -f "$HOME/.local/bin/agy"
        echo "Удален файл/симлинк ~/.local/bin/agy"
    fi

    # 3. Удаляем распакованные файлы в Downloads, если они там есть
    if [ -d "$HOME/Downloads/Antigravity" ]; then
        rm -rf "$HOME/Downloads/Antigravity"
        echo "Удалена директория с бинарными файлами ~/Downloads/Antigravity"
    fi

    # 4. Удаляем все конфигурационные папки и данные (включая учетные данные Gemini)
    if [ -d "$HOME/.gemini" ]; then
        rm -rf "$HOME/.gemini"
        echo "Удалена директория настроек и данных ~/.gemini (включая oauth_creds.json и settings.json)"
    fi

    if [ -d "$HOME/.antigravity" ]; then
        rm -rf "$HOME/.antigravity"
        echo "Удалена директория настроек ~/.antigravity"
    fi

    if [ -d "$HOME/.antigravitycli" ]; then
        rm -rf "$HOME/.antigravitycli"
        echo "Удалена директория настроек ~/.antigravitycli"
    fi

    if [ -d "$HOME/.config/Antigravity" ]; then
        rm -rf "$HOME/.config/Antigravity"
        echo "Удалена конфигурационная директория ~/.config/Antigravity"
    fi

    if [ -d "$HOME/.local/share/antigravity-ide" ]; then
        rm -rf "$HOME/.local/share/antigravity-ide"
        echo "Удалены данные IDE ~/.local/share/antigravity-ide"
    fi

    if [ -d "$HOME/.cache/antigravity" ]; then
        rm -rf "$HOME/.cache/antigravity"
        echo "Удален кэш ~/.cache/antigravity"
    fi

    # 4.5 Удаляем токен авторизации и ключи шифрования из системной связки ключей (Keyring)
    echo "Очистка системной связки ключей (Keyring) от токенов Antigravity..."
    if command -v python3 &>/dev/null; then
        python3 -c "
try:
    import secretstorage
    connection = secretstorage.dbus_init()
    collection = secretstorage.get_default_collection(connection)
    for item in list(collection.get_all_items()):
        attrs = item.get_attributes()
        if attrs.get('application') == 'Antigravity' or (attrs.get('service') == 'gemini' and attrs.get('username') == 'antigravity'):
            item.delete()
except Exception:
    pass
" 2>/dev/null
        echo "Связка ключей Keyring очищена от записей Antigravity."
    fi

    # 5. Удаляем строки инициализации путей из файлов конфигурации оболочки
    remove_from_shell_config() {
        local file="$1"
        if [ -f "$file" ]; then
            if grep -q "# Added by Antigravity CLI installer" "$file"; then
                sed -i '/# Added by Antigravity CLI installer/{N;d;}' "$file"
                echo "Удалены строки инициализации из $file"
            fi
        fi
    }

    remove_from_shell_config "$HOME/.bashrc"
    remove_from_shell_config "$HOME/.zshrc"
    remove_from_shell_config "$HOME/.profile"

    echo "Antigravity CLI и все данные Gemini/agy полностью удалены из системы!"
}

install_antigravity() {
    echo "Начинаем установку Antigravity CLI с нуля..."
    curl -fsSL https://antigravity.google/cli/install.sh | bash
    echo "Установка Antigravity CLI завершена!"
}

case "$1" in
    install)
        install_antigravity
        ;;
    uninstall)
        uninstall_antigravity
        ;;
    reinstall)
        uninstall_antigravity
        install_antigravity
        ;;
    *)
        echo "Использование: $0 {install|uninstall|reinstall}"
        exit 1
esac
