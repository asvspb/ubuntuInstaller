#!/bin/bash

# Эмуляция логики из install.sh для отладки
CONFIG_FILE="config-wsl.yaml"
DRY_RUN_FLAG=true

roles_count=$(yq '.roles_enabled // [] | length' "$CONFIG_FILE")

echo "Всего ролей: $roles_count"

enabled_roles_count=0

for i in $(seq 0 $((roles_count - 1))); do
  role_name=$(yq ".roles_enabled[$i].name" "$CONFIG_FILE" 2>/dev/null || echo "null")
  role_enabled=$(yq ".roles_enabled[$i].enabled // true" "$CONFIG_FILE" 2>/dev/null || echo "null")

  echo "Роль $i: $role_name, enabled: $role_enabled"

  # Проверяем, что роль существует
  if [ "$role_name" = "null" ]; then
    echo "  -> Пропуск: не удалось получить имя роли"
    continue
  fi

  # В симуляции, если yq не может получить значение, и оно вернулось как "null",
  # используем значение по умолчанию (true)
  if [ "$DRY_RUN_FLAG" = true ] && [ "$role_enabled" = "null" ]; then
    role_enabled=$(yq ".roles_enabled[$i].enabled" "$CONFIG_FILE" 2>/dev/null || echo "null")
    if [ "$role_enabled" = "null" ]; then
      role_enabled="true"
    fi
  fi

  echo "  -> После обработки: enabled = $role_enabled"

  # Увеличиваем счетчик включенных ролей только если роль не отключена
  if [ "$role_enabled" != "false" ]; then
    enabled_roles_count=$((enabled_roles_count + 1))
    echo "  -> Роль включена, счетчик: $enabled_roles_count"
  else
    echo "  -> Роль отключена, пропуск"
    continue
  fi

  echo "  -> [DRY-RUN] Запуск роли $role_name (симуляция)"
done

echo "Итого включенных ролей: $enabled_roles_count"
