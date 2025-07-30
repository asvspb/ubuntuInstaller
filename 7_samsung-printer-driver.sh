#!/bin/bash

#================================================================
# Installation Menu for Samsung M2070 MFP on Ubuntu 22.04+
# This script provides two of the best methods to choose from.
#================================================================

# Helper function for logging
info() {
    echo
    echo "--- $1 ---"
}

# --- Option 1: Install via the SULDR Repository (Recommended & Fixed) ---
install_suld_fixed() {
    info "Executing: Install via SULDR Repository (Recommended Method)"
    set -e

    info "Adding the Samsung Unified Linux Driver Repository (SULDR)"
    SULDR_SOURCE_LIST="/etc/apt/sources.list.d/samsung-uld.list"
    if [ ! -f "$SULDR_SOURCE_LIST" ]; then
        echo "deb https://www.bchemnet.com/suldr/ debian extra" | sudo tee "$SULDR_SOURCE_LIST"
    else
        echo "SULDR repository source file already exists."
    fi

    info "Installing the repository keyring"
    # The original script tried to add keys manually, which can fail.
    # This method of downloading and installing the keyring package is more reliable.
    KEYRING_DEB=$(mktemp --suffix=.deb)
    wget -qO "$KEYRING_DEB" https://www.bchemnet.com/suldr/pool/debian/extra/su/suld-keyring_2_all.deb
    sudo dpkg -i "$KEYRING_DEB"
    rm "$KEYRING_DEB"

    info "Updating package lists"
    sudo apt-get update

    info "Installing the Samsung printer and scanner driver"
    # The M2070 series uses the 1.00.39 driver version.
    sudo apt-get install -y suld-driver2-1.00.39

    info "Installation complete!"
    echo
    echo "Your Samsung M2070 MFP should now be ready."
    echo "To add the printer, go to 'Settings' > 'Printers' > 'Add Printer'."
    echo "To test the scanner, use an application like 'Document Scanner' (simple-scan)."
    set +e
}


# --- Option 2: Install from Standard Ubuntu Repositories ---
install_ubuntu_default() {
    info "Executing: Install from Standard Ubuntu Repositories"
    set -e

    info "Updating package lists"
    sudo apt update

    info "Installing printing drivers"
    sudo apt install -y cups printer-driver-splix printer-driver-samsung

    info "Installing scanning software"
    sudo apt install -y sane-utils libsane-extras simple-scan

    info "Adding user to required groups"
    sudo usermod -a -G lp,scanner $USER

    info "Starting CUPS printing service"
    sudo systemctl start cups
    sudo systemctl enable cups

    info "Installation completed!"
    echo
    echo "This method uses stable drivers from the Ubuntu repository."
    echo "A reboot or re-login may be required."
    set +e
}


# --- Main Menu ---
while true; do
    clear
    echo "======================================================="
    echo "      Samsung M2070 MFP Driver Installation Menu       "
    echo "======================================================="
    echo "Please choose your preferred installation method:"
    echo
    echo "1) Install via SULDR Repository (Recommended)"
    echo "   (Reliable method with a model-specific driver)"
    echo
    echo "2) Install from standard Ubuntu Repositories"
    echo "   (Safest method, but scanner function is not guaranteed)"
    echo
    echo "3) Exit"
    echo
    read -p "Enter your choice (1-3): " choice

    case $choice in
        1)
            install_suld_fixed
            break
            ;;
        2)
            install_ubuntu_default
            break
            ;;
        3)
            echo "Exiting the installer."
            exit 0
            ;;
        *)
            echo "Error: Invalid choice. Please enter a number between 1 and 3."
            sleep 2
            ;;
    esac
done

echo
echo "The script has finished."
