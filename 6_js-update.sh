#!/bin/bash
echo "                                                              "
echo "Устанавливаем nodejs"
echo "--------------------------------------------------------------"
# устанавливаем nvm + node
nvm_version=$(curl -s https://api.github.com/repos/nvm-sh/nvm/releases/latest | grep -oP '"tag_name": "\K.*?(?=")')
curl -o- "https://raw.githubusercontent.com/nvm-sh/nvm/$nvm_version/install.sh" | bash
source ~/.nvm/nvm.sh 	# инициализация
source ~/.bashrc 	# перезапуск оболочки
nvm list
npm install -g npm@latest
nvm install node

