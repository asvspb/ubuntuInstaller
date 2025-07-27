#!/bin/bash
echo "                                                              "
echo "Installing nodejs"
echo "--------------------------------------------------------------"
# install nvm + node
nvm_version=$(curl -s https://api.github.com/repos/nvm-sh/nvm/releases/latest | grep -oP '"tag_name": "\K.*?(?=")')
curl -o- "https://raw.githubusercontent.com/nvm-sh/nvm/$nvm_version/install.sh" | bash
source ~/.nvm/nvm.sh 	# initialization
source ~/.bashrc 	# restart shell
nvm list
npm install -g npm@latest
nvm install node

# plugin for js quokka
sudo npm install -g jsdom-quokka-plugin