#!/bin/bash

# Скачать архивы с программой Jetbrains с официального сайта 
# https://www.jetbrains.com/products/ 
# скачать архив jetbra в эту же директорию 
# https://jetbra.in/5d84466e31722979266057664941a71893322460
# скопировать в эту же папку данный скрипт и запустить
# отредактировать меню alcarte

set -e

# Переменная, указывающая текущую директорию, где хранятся архивы
archives_dir="$(pwd)"

# Создаем директорию, если она не существует
mkdir -p ~/Programs

# Находим архивы, содержащие ключевые слова
find "$archives_dir" -type f -name "*pycharm*" -o -name "*PhpStorm*" -o -name "*Postman*" -o -name "*jetbra*" -o -name "*aqua*" | while read -r archive; do
    echo "Разархивирование архива: $archive"
    
    # Определяем тип архива с помощью команды 'file'
    archive_type=$(file -b --mime-type "$archive")
    
    # Проверяем тип архива и разархивируем, если директория jetbra не существует
    if [ ! -d "$install_dir" ]; then
        if [[ "$archive_type" == "application/zip" ]]; then
            unzip -q -o "$archive" -d ~/Programs/
        elif [[ "$archive_type" == "application/gzip" ]]; then
            tar -xzf "$archive" -C ~/Programs/
        else
            echo "Неизвестный тип архива: $archive_type"
        fi
    fi
done

echo "Все архивы были разархивированы и перемещены в ~/Programs/"

# Создаем список папок внутри ~/Programs
folder_list=$(find ~/Programs -maxdepth 1 -type d)

# Переменная для отслеживания обнаружения директории 'bin'
bin_found=false

# Проверяем каждую папку на наличие директории 'bin' и наличие директории 'jetbra' внутри 'bin'
for folder in $folder_list; do
    if [ -d "$folder/bin" ]; then
        
        # Проверяем наличие директории 'jetbra' внутри 'bin'
        if [ ! -d "$folder/bin/jetbra" ]; then
            cp -r ~/Programs/jetbra -d "$folder/bin/"
            echo "Папка 'jetbra' была скопирована в папку $folder/bin/"

            # Заходим в скопированную папку 'jetbra' и находим файл ja-netfilter.jar
            ja_netfilter_file=$(find "$folder/bin/jetbra" -maxdepth 1 -name "ja-netfilter.jar")

            # Переходим обратно в папку 'bin'
            cd "$folder/bin"

            # Находим файл .vmoptions
            vmoptions_file=$(find . -maxdepth 1 -name "*.vmoptions")

            # Проверяем, найдены ли оба файла
            if [ -n "$ja_netfilter_file" ] && [ -n "$vmoptions_file" ]; then
                # Добавляем нужные строки в файл .vmoptions, если их там еще нет
                if ! grep -q "jetbra" "$vmoptions_file"; then
                    echo "--add-opens=java.base/jdk.internal.org.objectweb.asm=ALL-UNNAMED" >> "$vmoptions_file"
                    echo "--add-opens=java.base/jdk.internal.org.objectweb.asm.tree=ALL-UNNAMED" >> "$vmoptions_file"
                    echo "-javaagent:$ja_netfilter_file=jetbrains" >> "$vmoptions_file"
                    echo "Добавлены волшебные строки в файл $vmoptions_file"
                fi
            fi
        fi
        
        # Устанавливаем флаг, что 'bin' была найдена
        bin_found=true
    fi
done

# Если 'bin' не была найдена, выведем соответствующее сообщение
if [ "$bin_found" = false ]; then
    echo "В папках в ~/Programs не обнаружена директория 'bin'"
fi
echo "Продукты JetBrains успешно активированны!"
