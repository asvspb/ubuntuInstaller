#!/bin/bash

# Script for automatic update of Visual Studio Code
# Checks the current version of VSCode, downloads and installs the latest version if necessary

set -e

# Function to print a formatted header
print_header() {
	echo ""
	echo "========================================================================"
	echo "  $1"
	echo "========================================================================"
	echo ""
}

print_header "Updating js && python..."
cd ~ # Change to home directory to ensure write permissions
# Проверяем, установлена ли уже подходящая версия Node.js через NVM
if [ -f "$HOME/.nvm/nvm.sh" ]; then
    NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"
    
    if command -v node &>/dev/null; then
        NODE_VERSION=$(node --version)
        if [[ $NODE_VERSION =~ ^v(2[0-9]|3[0-9]). ]]; then
            echo "Подходящая версия Node.js уже установлена: $NODE_VERSION"
        else
            echo "Установлена несовместимая версия Node.js: $NODE_VERSION"
            echo "Рекомендуется обновить до версии 20.x или выше"
        fi
    else
        echo "Node.js не найден, убедитесь, что NVM активирован правильно"
    fi
else
    # Если NVM не установлен, используем системную установку (не рекомендуется)
    curl -qL https://www.npmjs.com/install.sh | sh
fi
sudo apt install python3 python3-pip -y && sudo pip3 install --upgrade pip --break-system-packages

print_header "Updating CODE CLI's..."
# Активация NVM использование npm из NVM
if [ -f "$HOME/.nvm/nvm.sh" ]; then
    NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"
    
    if command -v npm &>/dev/null; then
        echo "Используем npm из NVM: $(npm --version)"
        npm install -g @google/gemini-cli@latest
        npm install -g @qwen-code/qwen-code@latest
        npm install -g @github/copilot
        npm install -g codebuff
    else
        # Если активация NVM не помогла, пробуем использовать npm напрямую из NVM
        NVM_NPM_PATH="$HOME/.nvm/versions/node/v24.6.0/bin/npm"
        if [ -f "$NVM_NPM_PATH" ]; then
            echo "Используем npm напрямую из NVM: $($NVM_NPM_PATH --version)"
            $NVM_NPM_PATH install -g @google/gemini-cli@latest
            $NVM_NPM_PATH install -g @qwen-code/qwen-code@latest
            $NVM_NPM_PATH install -g @github/copilot
            $NVM_NPM_PATH install -g codebuff
        else
            echo "WARN: npm не найден в NVM по пути $NVM_NPM_PATH"
        fi
    fi
else
    # Если NVM не установлен, пробуем использовать npm напрямую из NVM (на случай, если он был установлен в другом месте)
    NVM_NPM_PATH="$HOME/.nvm/versions/node/v24.6.0/bin/npm"
    if [ -f "$NVM_NPM_PATH" ]; then
        echo "Используем npm напрямую из NVM: $($NVM_NPM_PATH --version)"
        $NVM_NPM_PATH install -g @google/gemini-cli@latest
        $NVM_NPM_PATH install -g @qwen-code/qwen-code@latest
        $NVM_NPM_PATH install -g @github/copilot
        $NVM_NPM_PATH install -g codebuff
    else
        echo "WARN: npm не найден в системе (ни в NVM, ни напрямую)"
    fi
fi

# Log file
LOG_FILE="$HOME/vscode-updater.log"

# URL to download the latest version of VSCode
DOWNLOAD_URL="https://code.visualstudio.com/sha/download?build=stable&os=linux-deb-x64"

# Temporary file for download
TEMP_DEB="/tmp/vscode.deb"

# Function for logging
log() {
	echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Function to check the installed VSCode version
get_installed_version() {
	if command -v code &>/dev/null; then
		code --version | head -n 1
	else
		echo "not_installed"
	fi
}

# Function to get the latest VSCode version from the website
get_latest_version() {
	# Get the version from the official website via API
	curl -sSL "https://code.visualstudio.com/sha?build=stable" | grep -oE '''"productVersion":"[^"]+"''' | head -1 | cut -d'"' -f4
}

# Function to verify the integrity of the downloaded file
verify_package() {
	if [ ! -f "$TEMP_DEB" ]; then
		log "ERROR: Package file not found"
		return 1
	fi

	# Check that the file is not empty
	if [ ! -s "$TEMP_DEB" ]; then
		log "ERROR: Package file is empty"
		return 1
	fi

	# Check the checksum (simple check)
	if file "$TEMP_DEB" | grep -q "Debian binary package"; then
		log "INFO: Package file passed basic check"
		return 0
	else
		log "ERROR: Package file is not a valid Debian package"
		return 1
	fi
}

# Function to install VSCode
install_vscode() {
	log "INFO: Starting VSCode installation"

	# Verifying package integrity
	if ! verify_package; then
		log "ERROR: Package integrity check failed"
		return 1
	fi

	# Installing the package
	if sudo dpkg -i "$TEMP_DEB" &>/dev/null; then
		log "INFO: VSCode installed successfully"
		# Removing the temporary file
		rm -f "$TEMP_DEB"
		return 0
	else
		log "ERROR: Error during VSCode installation"
		# Trying to fix dependencies
		if sudo apt-get install -f -y &>/dev/null; then
			log "INFO: Dependencies fixed successfully"
			return 0
		else
			log "ERROR: Failed to fix dependencies"
			return 1
		fi
	fi
}

# Function to check if the script is running in interactive mode
is_interactive() {
	[ -t 0 ]
}

# Main update function
update_vscode() {
	print_header "Checking for VSCode Updates..."
	log "INFO: Starting VSCode update check"

	# Getting the current version
	INSTALLED_VERSION=$(get_installed_version)
	log "INFO: Current installed version: $INSTALLED_VERSION"

	# Getting the latest version
	LATEST_VERSION=$(get_latest_version)
	if [ -z "$LATEST_VERSION" ]; then
		log "ERROR: Failed to get latest version information"
		return 1
	fi
	log "INFO: Latest available version: $LATEST_VERSION"

	# Check if VSCode needs an update
	if [ "$INSTALLED_VERSION" = "not_installed" ]; then
		log "INFO: VSCode is not installed"
		# Check if the script is running in interactive mode
		if ! is_interactive; then
			log "INFO: Script is running in non-interactive mode, installation skipped"
			return 0
		fi
		log "INFO: Starting installation"
	elif [ "$INSTALLED_VERSION" = "$LATEST_VERSION" ]; then
		log "INFO: VSCode is already up to date"
		return 0
	else
		log "INFO: New version found"
		# Check if the script is running in interactive mode
		if ! is_interactive; then
			log "INFO: Script is running in non-interactive mode, update skipped"
			return 0
		fi
		log "INFO: Starting update"
	fi

	# Downloading the latest version
	log "INFO: Downloading the latest version of VSCode"
	if curl -sSL "$DOWNLOAD_URL" -o "$TEMP_DEB"; then
		log "INFO: Download completed successfully"
	else
		log "ERROR: Error downloading the file"
		rm -f "$TEMP_DEB"
		return 1
	fi

	# Check if the script is running in interactive mode before installation
	if ! is_interactive; then
		log "INFO: Script is running in non-interactive mode, installation skipped"
		rm -f "$TEMP_DEB"
		return 0
	fi

	# Installing VSCode
	if install_vscode; then
		log "INFO: VSCode update finished successfully"
		return 0
	else
		log "ERROR: Error updating VSCode"
		rm -f "$TEMP_DEB"
		return 1
	fi
}

# Create log file if it does not exist
if [ ! -f "$LOG_FILE" ]; then
	touch "$LOG_FILE"
	chmod 666 "$LOG_FILE"
fi

# Start the update
if update_vscode; then
	log "INFO: Script executed successfully"
	exit 0
else
	log "ERROR: Script finished with an error"
	exit 1
fi
