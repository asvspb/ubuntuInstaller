#!/bin/bash

set -e

# Define the directory
dir="~/Dev"

# Check if the directory exists
if [ ! -d "$dir" ]; then
  # Create the directory
  mkdir -p "$dir"
  echo "Directory $dir was created."
else
  echo "Directory $dir already exists."
fi

# установщик ollama & bito
echo "                                                              "
echo "Устанавливаем ollama & bito-cli"
echo "--------------------------------------------------------------"
curl -fsSL https://ollama.com/install.sh | sh
sudo curl https://alpha.bito.ai/downloads/cli/install.sh -fsSL | bash



# запуск интерфейса на linux
echo "                                                              "
echo "Устанавливаем вебинтерфейс ollama"
echo "--------------------------------------------------------------"
docker run -d --network=host -v open-webui:/app/backend/data -e OLLAMA_API_BASE_URL=http://127.0.0.1:11434/api --name open-webui --restart always ghcr.io/open-webui/open-webui:main

# install fabric & pbpaste & shell-gpt 
echo "                                                              "
echo "Устанавливаем fabric & pbpaste && shell-gpt"
echo "--------------------------------------------------------------"
sudo apt-get install xclip pipx ffmpeg -y
pip install shell-gpt[litellm]


cd Dev
git clone git@github.com:danielmiessler/fabric.git

cd fabric/
pipx install .
pipx ensurepath
pipx completions

echo "                                                              "
echo "Добавление конфигурационного файла shell-gpt"
echo "--------------------------------------------------------------"

# Define the file and the lines to write
file=~/.config/shell_gpt/.sgptrc
lines="CHAT_CACHE_PATH=/tmp/chat_cache\nCACHE_PATH=/tmp/cache\nCHAT_CACHE_LENGTH=100\nCACHE_LENGTH=100\nREQUEST_TIMEOUT=60\nDEFAULT_MODEL=ollama/llama3\nDEFAULT_COLOR=magenta\nROLE_STORAGE_PATH=/home/asv-spb/.config/shell_gpt/roles\nDEFAULT_EXECUTE_SHELL_CMD=false\nDISABLE_STREAMING=false\nCODE_THEME=dracula\nOPENAI_FUNCTIONS_PATH=/home/asv-spb/.config/shell_gpt/functions\nOPENAI_USE_FUNCTIONS=true\nSHOW_FUNCTIONS_OUTPUT=false\nAPI_BASE_URL=default\nPRETTIFY_MARKDOWN=true\nUSE_LITELLM=true\nOPENAI_API_KEY=abcd"

# Write the lines to the file
echo -e "$lines" > "$file"

# Print success message
echo "                                                              "
echo "Добавление в систему ИИ успешно завершена"
echo "--------------------------------------------------------------"

