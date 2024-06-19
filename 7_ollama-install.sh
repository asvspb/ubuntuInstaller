#!/bin/bash
echo "                                                              "
echo "Устанавливаем ollama & fabric & pbpaste"
echo "--------------------------------------------------------------"
# установщик сервера и окружения, включая CUDA
curl -fsSL https://ollama.com/install.sh | sh

# запуск на linux
docker run -d --network=host -v open-webui:/app/backend/data -e OLLAMA_API_BASE_URL=http://127.0.0.1:11434/api --name open-webui --restart always ghcr.io/open-webui/open-webui:main

# fabric & pbpaste install
sudo apt-get install xclip pipx fmpeg -y
cd Dev
git clone git@github.com:danielmiessler/fabric.git
cd fabric/
pipx install .
pipx ensurepath
pipx completions


