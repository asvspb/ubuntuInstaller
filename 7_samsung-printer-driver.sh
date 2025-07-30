#!/bin/bash

#================================================================
# Installation Script for Samsung M2070 MFP on Ubuntu 22.04+
# This script installs the recommended driver from the SULDR repository.
#================================================================

# Helper function for logging
info() {
    echo
    echo "--- $1 ---"
}

# --- Install via the SULDR Repository --- 
install_samsung_driver() {
    info "Executing: Install via SULDR Repository"
    set -e

    info "Adding the Samsung Unified Linux Driver Repository (SULDR)"
    SULDR_SOURCE_LIST="/etc/apt/sources.list.d/samsung-uld.list"
    if [ ! -f "$SULDR_SOURCE_LIST" ]; then
        echo "deb https://www.bchemnet.com/suldr/ debian extra" | sudo tee "$SULDR_SOURCE_LIST" > /dev/null
    else
        echo "SULDR repository source file already exists."
    fi

    info "Installing the repository keyring"
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

# --- Main Execution ---
install_samsung_driver

echo
echo "The script has finished."
