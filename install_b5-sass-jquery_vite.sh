#!/bin/bash

# Имя проекта
project_name="b5-sass-jquery-project"

# Создаем папку проекта и инициализируем npm
mkdir $project_name && cd $project_name
npm init -y

# Устанавливаем Vite
npm install --save-dev vite

# Устанавливаем Bootstrap и Popper.js
npm install bootstrap @popperjs/core

# Устанавливаем Sass
npm install --save-dev sass jquery

# Создаем структуру папок и файлов
mkdir -p dist src/js src/scss src/scss/base src/scss/abstracts src/scss/components src/scss/layout src/scss/pages src/scss/themes src/scss/vendors src/scss/utilities
touch src/index.html src/js/main.js src/scss/styles.scss vite.config.js src/scss/base/_index.scss src/scss/abstracts/_index.scss src/scss/components/_index.scss src/scss/layout/_index.scss src/scss/pages/_index.scss src/scss/themes/_index.scss src/scss/vendors/_index.scss src/scss/utilities/_index.scss

# Конфигурируем Vite
echo "const path = require('path')

export default {
  root: path.resolve(__dirname, 'src'),
  build: {
    outDir: '../dist'
  },
  server: {
    port: 8080
  }
}" > vite.config.js

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

# Добавляем скрипт запуска в package.json
jq '.scripts |= .+ {"start": "vite"}' package.json > tmp && mv tmp package.json

# Импортируем Bootstrap в SCSS
echo "@import 'bootstrap/scss/bootstrap';
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
\$(function () {
\$("button").on("click", function () {
\$(this).hide();
});
});
EOF

echo "Готово! Теперь можно запустить проект командой 'npm start'"
