#!/bin/bash
echo "                                                              "
echo "Installing nodejs"
echo "--------------------------------------------------------------"
# install latest version of nodejs, nvm, and npm
LATEST_NVM_VERSION=$(curl -s "https://api.github.com/repos/nvm-sh/nvm/releases/latest" | grep -Po '"tag_name": "\K.*?(?=")')
curl -o- "https://raw.githubusercontent.com/nvm-sh/nvm/$LATEST_NVM_VERSION/install.sh" | bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
nvm install node
nvm use node
nvm alias default node
npm install -g npm@latest
npm install -g jsdom-quokka-plugin
npm install -g eslint