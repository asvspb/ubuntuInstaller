#!/bin/bash

# Имя проекта
project_name="b5-sass-jquery_parsel-project"

# Создаем папку проекта и инициализируем npm
mkdir $project_name && cd $project_name
npm init -y

# Устанавливаем Parcel вместо Vite
npm install --save-dev parcel-bundler

# Устанавливаем остальные зависимости
npm install bootstrap @popperjs/core
npm install --save-dev sass jquery

# Структура папок и файлы
mkdir -p src/js src/scss
touch src/index.html src/js/main.js src/scss/styles.scss

# Заполняем src/index.html
echo "<!doctype html>
<html>

<head>
    <meta charset='UTF-8'>
    <meta name='viewport' content='width=device-width, initial-scale=1.0'>
    <title>Bootstrap 5 demo</title>
</head>

<body>
    <h1 class='logo mt-5'>Wellcome to <strong>Bootstrap 5</strong></h1>
    <p class="logo">included SASS and Jquery</p>
    <div class='container mt-2'>
        <button type='button' class='btn btn-primary'>Click</button>
    </div>
</body>
<script type='module' src='./js/main.js'></script>

</html>" > src/index.html

# Скрипт старта на Parcel
jq '.scripts |= .+ {"start": "parcel src/index.html"}' package.json > tmp && mv tmp package.json

# Импортируем Bootstrap в SCSS
echo "@import '~bootstrap/scss/bootstrap';
.container {
    display: flex;
    justify-content: center;
};
.logo {
    text-align: center;
    color: #0D6EFD;
}" > src/scss/styles.scss

# Импортируем JS Bootstrap в main.js
cat <<EOF > src/js/main.js
import '../scss/styles.scss';
import * as bootstrap from 'bootstrap';
import \$ from 'jquery';

\$(document).ready(function () {
    \$("button").click(function () {
        \$(this).hide()
    });
});
EOF

echo "Готово! Теперь нужно перейти в каталог проекта, установить зависимости 'npm install' и можно вызывать 'npm start'"
