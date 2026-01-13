#!/bin/bash

# Script for automatic update of Visual Studio Code
# Checks the current version of VSCode, downloads and installs the latest version if necessary

set -e

# Color codes for standardized output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

# Log file
LOG_FILE="$HOME/vscode-updater.log"

# Unified logging function for consistent output to console and file
log_message() {
    local level=$1
    local message=$2
    local log_to_file=${3:-true}  # Default to true for logging to file
    local timestamp_short=$(date '+%H:%M:%S')
    local timestamp_full=$(date '+%Y-%m-%d %H:%M:%S')

    # Console output with colors and short timestamp
    case $level in
        "INFO")
            echo -e "${timestamp_short} ${BLUE}[INFO]${NC}    $message"
            ;;
        "SUCCESS")
            echo -e "${timestamp_short} ${GREEN}[SUCCESS]${NC} $message"
            ;;
        "WARN")
            echo -e "${timestamp_short} ${YELLOW}[WARN]${NC}    $message"
            ;;
        "ERROR")
            echo -e "${timestamp_short} ${RED}[ERROR]${NC}   $message"
            ;;
        "HEADER")
            echo -e "${CYAN}========================================================================${NC}"
            echo -e "${CYAN} $message${NC}"
            echo -e "${CYAN}========================================================================${NC}"
            ;;
        *)
            echo -e "${timestamp_short} [UNKNOWN] $message"
            ;;
    esac

    # File logging with full timestamp and level
    if [ "$log_to_file" = true ]; then
        echo "${timestamp_full} [$level] $message" >> "$LOG_FILE"
    fi
}

# Function to print a formatted header (maintaining backward compatibility)
print_header() {
    log_message "HEADER" "$1"
}

log_message "INFO" "Script initialization complete, starting updates"
print_header "Updating js && python..."
cd ~ # Change to home directory to ensure write permissions
# Install npm through apt for security reasons instead of piping from website
log_message "INFO" "Updating system packages and installing Node.js/NPM..."
sudo apt update && sudo apt install nodejs npm -y
log_message "INFO" "Installing/updating Python and pip..."
sudo apt install python3 python3-pip -y && sudo pip3 install --upgrade pip --break-system-packages --root-user-action=ignore

log_message "HEADER" "Updating CODE CLI's..."
log_message "INFO" "Installing/Updating CODE CLI tools..."
npm install -g @google/gemini-cli@latest && npm install -g @qwen-code/qwen-code@latest && npm install -g @github/copilot && npm install -g codebuff && npm install -g @kilocode/cli
log_message "SUCCESS" "CODE CLI tools updated successfully"

# Determine system architecture
ARCH=$(uname -m)
case $ARCH in
    x86_64)
        ARCH="x64"
        ;;
    aarch64|arm64)
        ARCH="arm64"
        ;;
    armv7l)
        ARCH="armhf"
        ;;
    *)
        log_message "WARN" "Unknown architecture $ARCH, defaulting to x64"
        ARCH="x64"
        ;;
esac

# Determine package type based on distribution
if [ -f /etc/debian_version ] || [ -f /etc/ubuntu_version ]; then
    PKG_TYPE="deb"
elif [ -f /etc/redhat-release ] || [ -f /etc/fedora-release ] || [ -f /etc/centos-release ]; then
    PKG_TYPE="rpm"
else
    PKG_TYPE="deb"  # Default to deb
fi

# URL to download the latest version of VSCode
DOWNLOAD_URL="https://code.visualstudio.com/sha/download?build=stable&os=linux-$PKG_TYPE-$ARCH"

# Temporary file for download
if [ "$PKG_TYPE" = "rpm" ]; then
    TEMP_PKG="/tmp/vscode.rpm"
else
    TEMP_PKG="/tmp/vscode.deb"
fi

# Legacy log function replaced by log_message

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
    # The API returns JSON with products, we need to extract productVersion for our platform
    curl -sSL "https://code.visualstudio.com/sha?build=stable" | grep -o '"productVersion":"[^"]*' | head -1 | cut -d'"' -f4
}

# Function to verify the integrity of the downloaded file
verify_package() {
    if [ ! -f "$TEMP_PKG" ]; then
        log_message "ERROR" "Package file not found"
        return 1
    fi

    # Check that the file is not empty
    if [ ! -s "$TEMP_PKG" ]; then
        log_message "ERROR" "Package file is empty"
        return 1
    fi

    # Additional verification - check if it's a proper package
    if [ "$PKG_TYPE" = "rpm" ]; then
        if rpm -qp "$TEMP_PKG" >/dev/null 2>&1; then
            log_message "INFO" "Package file passed integrity check"
            return 0
        else
            log_message "ERROR" "Package file is not a valid RPM package"
            return 1
        fi
    else
        if dpkg-deb --info "$TEMP_PKG" >/dev/null 2>&1; then
            log_message "INFO" "Package file passed integrity check"
            return 0
        else
            log_message "ERROR" "Package file is not a valid Debian package"
            return 1
        fi
    fi
}

# Function to install VSCode
install_vscode() {
    log_message "INFO" "Starting VSCode installation..."

    # Verifying package integrity
    if ! verify_package; then
        log_message "ERROR" "Package integrity check failed"
        return 1
    fi

    # Installing the package
    if [ "$PKG_TYPE" = "rpm" ]; then
        log_message "INFO" "Installing RPM package..."
        if sudo rpm -Uvh "$TEMP_PKG" &>/dev/null; then
            log_message "SUCCESS" "VSCode installed successfully"
            # Removing the temporary file
            rm -f "$TEMP_PKG"
            return 0
        else
            log_message "ERROR" "Error during VSCode installation"
            # Trying to fix dependencies
            if sudo dnf install -f -y &>/dev/null || sudo yum install -f -y &>/dev/null; then
                log_message "SUCCESS" "Dependencies fixed successfully"
                return 0
            else
                log_message "ERROR" "Failed to fix dependencies"
                return 1
            fi
        fi
    else
        log_message "INFO" "Installing DEB package..."
        if sudo dpkg -i "$TEMP_PKG" &>/dev/null; then
            log_message "SUCCESS" "VSCode installed successfully"
            # Removing the temporary file
            rm -f "$TEMP_PKG"
            return 0
        else
            log_message "ERROR" "Error during VSCode installation"
            # Trying to fix dependencies
            if sudo apt-get install -f -y &>/dev/null; then
                log_message "SUCCESS" "Dependencies fixed successfully"
                return 0
            else
                log_message "ERROR" "Failed to fix dependencies"
                return 1
            fi
        fi
    fi
}

# Function to check if the script is running in interactive mode
is_interactive() {
    [ -t 0 ]
}

# Main update function
update_vscode() {
    log_message "HEADER" "Checking for VSCode Updates..."
    log_message "INFO" "Starting VSCode update check"

    # Getting the current version
    INSTALLED_VERSION=$(get_installed_version)
    log_message "INFO" "Current installed version: $INSTALLED_VERSION"

    # Getting the latest version
    LATEST_VERSION=$(get_latest_version)
    if [ -z "$LATEST_VERSION" ]; then
        log_message "ERROR" "Failed to get latest version information"
        return 1
    fi
    log_message "INFO" "Latest available version: $LATEST_VERSION"

    # Check if VSCode needs an update
    if [ "$INSTALLED_VERSION" = "not_installed" ]; then
        log_message "INFO" "VSCode is not installed"
        # Check if the script is running in interactive mode
        if ! is_interactive; then
            log_message "INFO" "Script is running in non-interactive mode, installation skipped"
            return 0
        fi
        log_message "INFO" "Starting installation"
    elif [ "$INSTALLED_VERSION" = "$LATEST_VERSION" ]; then
        log_message "INFO" "VSCode is already up to date"
        return 0
    else
        log_message "INFO" "New version found"
        # Check if the script is running in interactive mode
        if ! is_interactive; then
            log_message "INFO" "Script is running in non-interactive mode, update skipped"
            return 0
        fi
        log_message "INFO" "Starting update"
    fi

    # Downloading the latest version
    log_message "INFO" "Downloading the latest version of VSCode for architecture $ARCH ($PKG_TYPE package)..."
    if curl -sSL "$DOWNLOAD_URL" -o "$TEMP_PKG"; then
        log_message "SUCCESS" "Download completed successfully"
    else
        log_message "ERROR" "Error downloading the file"
        rm -f "$TEMP_PKG"
        return 1
    fi

    # Check if the script is running in interactive mode before installation
    if ! is_interactive; then
        log_message "INFO" "Script is running in non-interactive mode, installation skipped"
        rm -f "$TEMP_PKG"
        return 0
    fi

    # Installing VSCode
    if install_vscode; then
        log_message "SUCCESS" "VSCode update finished successfully"
        return 0
    else
        log_message "ERROR" "Error updating VSCode"
        rm -f "$TEMP_PKG"
        return 1
    fi
}

# Create log file if it does not exist
if [ ! -f "$LOG_FILE" ]; then
    touch "$LOG_FILE"
    chmod 666 "$LOG_FILE"
fi

# Additional helper functions for standardized output
check_prerequisites() {
    log_message "INFO" "Checking system prerequisites..."

    # Check if curl is installed
    if ! command -v curl &> /dev/null; then
        log_message "ERROR" "curl is not installed. Please install curl before running this script."
        exit 1
    fi

    # Check if sudo is available
    if ! command -v sudo &> /dev/null; then
        log_message "ERROR" "sudo is not available. Please install sudo or run this script as root."
        exit 1
    fi

    log_message "SUCCESS" "All prerequisites met"
}

# Check prerequisites before starting
check_prerequisites

# Start the update
if update_vscode; then
    log_message "SUCCESS" "Script executed successfully"
    exit 0
else
    log_message "ERROR" "Script finished with an error"
    exit 1
fi
