# HOWTO: Добавление новых ролей в Ubuntu Installer Framework

Это руководство описывает процесс добавления новых ролей в Ubuntu Installer Framework.

## Структура роли

Каждая роль представляет собой директорию в `roles/` с префиксом из числа (для определения порядка выполнения) и названием роли. Например: `40-my-new-role/`.

Структура директории роли:
```
roles/
└── 40-my-new-role/
    ├── main.sh          # Основной скрипт установки (обязательный)
    ├── uninstall.sh     # Скрипт удаления (опциональный)
    └── README.md        # Документация к роли (опциональный)
```

## Создание основного скрипта роли (main.sh)

Основной скрипт роли должен следовать определенным требованиям:

1. Начинаться с shebang и комментариев:
    ```bash
    #!/bin/bash

    # Роль: 40-my-new-role
    # Назначение: Краткое описание назначения роли
    ```

2. Подключать библиотеку функций:
    ```bash
    set -e

    # Путь к библиотеке функций
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
    source "$SCRIPT_DIR/lib.sh"
    ```

3. Реализовывать функцию установки:
    ```bash
    # Функция установки роли
    install_my_new_role() {
        log "INFO" "Установка My New Role"

        if [ "$DRY_RUN" = "true" ]; then
            log "INFO" "[DRY-RUN] Установка My New Role (не выполнена)"
            return 0
        fi

        # Реализация установки роли
        # Используйте функции из lib.sh:
        # - log для логирования
        # - ensure_pkg для установки пакетов
        # - execute_command для выполнения команд
        # - download_with_verification для скачивания и верификации файлов
        # и другие функции из библиотеки

        log "INFO" "My New Role установлена"
    }
    ```

4. Реализовывать основную функцию:
    ```bash
    # Основная функция выполнения роли
    main() {
        log "INFO" "Запуск роли: 40-my-new-role"
        
        # Выполнение установки роли
        install_my_new_role
        
        log "INFO" "Роль 40-my-new-role завершена"
    }

    # Вызов основной функции
    main "$@"
    ```

5. Быть идемпотентным (поддерживать многократный запуск без побочных эффектов).

## Создание скрипта удаления (uninstall.sh)

Скрипт удаления опциональный, но рекомендуется для всех ролей:

1. Начинаться с shebang и комментариев:
    ```bash
    #!/bin/bash

    # Скрипт удаления для роли 40-my-new-role
    # Удаляет компоненты, установленные в main.sh
    ```

2. Подключать библиотеку функций:
    ```bash
    set -e

    # Путь к библиотеке функций
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
    source "$SCRIPT_DIR/lib.sh"
    ```

3. Реализовывать функцию удаления:
    ```bash
    # Функция удаления роли
    uninstall_my_new_role() {
        log "INFO" "Удаление My New Role"

        if [ "$DRY_RUN" = "true" ]; then
            log "INFO" "[DRY-RUN] Удаление My New Role (не выполнена)"
            return 0
        fi

        # Реализация удаления роли
        # Откатывает все изменения, сделанные в main.sh

        log "INFO" "My New Role удалена"
    }
    ```

4. Реализовывать основную функцию:
    ```bash
    # Основная функция выполнения удаления
    main() {
        log "INFO" "Запуск удаления роли: 40-my-new-role"
        
        # Выполнение удаления роли
        uninstall_my_new_role
        
        log "INFO" "Удаление роли 40-my-new-role завершено"
    }

    # Вызов основной функции
    main "$@"
    ```

## Добавление роли в конфигурацию

Чтобы роль могла быть выполнена, её нужно добавить в конфигурационный файл `config.yaml`:

```yaml
roles_enabled:
  - name: 0-base-system
  - name: 10-dev-tools
    vars:
      install_vscode: true
      install_pycharm: false
  # Добавьте вашу роль в нужное место в списке
  - name: 40-my-new-role
    # Можно передать переменные в роль (опционально)
    vars:
      my_variable: "value"
  - name: 20-docker
    enabled: true
```

## Переменные роли

Роли могут принимать переменные, которые передаются через конфигурационный файл. Для доступа к переменным в скрипте роли используйте:

```bash
# Получение переменных роли
local my_variable=$(echo "$UBUNTU_INSTALLER_ROLE_VARS" | yq '.my_variable // "default_value"' 2>/dev/null || echo "default_value")
```

## Тестирование роли

Перед добавлением роли в основную ветку рекомендуется протестировать её:

1. Проверьте работу в режиме симуляции:
    ```bash
    ./install.sh --dry-run -c config.yaml
    ```

2. Проверьте установку:
    ```bash
    sudo ./install.sh install -c config.yaml
    ```

3. Проверьте обновление:
    ```bash
    sudo ./install.sh update -c config.yaml
    ```

4. Проверьте удаление:
    ```bash
    sudo ./install.sh uninstall -c config.yaml
    ```

## Лучшие практики

1. Используйте функции из `lib.sh` вместо прямых вызовов команд.
2. Поддерживайте режим `--dry-run` для симуляции выполнения.
3. Делайте роли идемпотентными (поддерживающими многократный запуск).
4. Обрабатывайте ошибки с помощью логирования.
5. Проверяйте существование файлов и пакетов перед их использованием.
6. Используйте переменные окружения из `lib.sh` (например, `$DRY_RUN`).
7. Документируйте роли в README.md внутри директории роли.
8. Тестируйте роли на разных версиях Ubuntu и в разных окружениях (десктоп, сервер, WSL, VM).

## Пример простой роли

Пример простой роли `40-hello-world`:

`roles/40-hello-world/main.sh`:
```bash
#!/bin/bash

# Роль: 40-hello-world
# Назначение: Пример простой роли, которая выводит приветствие

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция установки роли
install_hello_world() {
    log "INFO" "Установка Hello World Role"

    if [ "$DRY_RUN" = "true" ]; then
        log "INFO" "[DRY-RUN] Установка Hello World Role (не выполнена)"
        return 0
    fi

    # Создание файла приветствия
    echo "Hello, World!" > /tmp/hello-world.txt
    log "INFO" "Файл приветствия создан: /tmp/hello-world.txt"

    log "INFO" "Hello World Role установлена"
}

# Основная функция выполнения роли
main() {
    log "INFO" "Запуск роли: 40-hello-world"
    
    # Выполнение установки роли
    install_hello_world
    
    log "INFO" "Роль 40-hello-world завершена"
}

# Вызов основной функции
main "$@"
```

`roles/40-hello-world/uninstall.sh`:
```bash
#!/bin/bash

# Скрипт удаления для роли 40-hello-world
# Удаляет файл приветствия

set -e

# Путь к библиотеке функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# Функция удаления роли
uninstall_hello_world() {
    log "INFO" "Удаление Hello World Role"

    if [ "$DRY_RUN" = "true" ]; then
        log "INFO" "[DRY-RUN] Удаление Hello World Role (не выполнена)"
        return 0
    fi

    # Удаление файла приветствия
    if [ -f /tmp/hello-world.txt ]; then
        rm /tmp/hello-world.txt
        log "INFO" "Файл приветствия удален: /tmp/hello-world.txt"
    else
        log "INFO" "Файл приветствия не найден"
    fi

    log "INFO" "Hello World Role удалена"
}

# Основная функция выполнения удаления
main() {
    log "INFO" "Запуск удаления роли: 40-hello-world"
    
    # Выполнение удаления роли
    uninstall_hello_world
    
    log "INFO" "Удаление роли 40-hello-world завершено"
}

# Вызов основной функции
main "$@"
```

`roles/40-hello-world/README.md`:
```markdown
# Роль 40-hello-world

Эта роль создает простой файл приветствия `/tmp/hello-world.txt`.

## Переменные

Эта роль не использует переменные.

## Зависимости

Эта роль не имеет внешних зависимостей.

## Совместимость

Роль совместима со всеми поддерживаемыми версиями Ubuntu.