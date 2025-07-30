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

    info "Checking for existing driver..."
    if dpkg -s suld-driver2-1.00.39 &> /dev/null; then
        echo "Samsung driver is already installed. Exiting."
        exit 0
    fi
    DRY_RUN=false
    if [ "$1" == "--dry-run" ]; then
        DRY_RUN=true
        echo "Performing a dry run. No actual changes will be made."
    fi

    info "Installing the repository keyring"
    KEYRING_DEB=$(mktemp --suffix=.deb)
    wget -qO "$KEYRING_DEB" https://www.bchemnet.com/suldr/pool/debian/extra/su/suld-keyring_2_all.deb
    if [ "$DRY_RUN" = false ]; then
        sudo dpkg -i "$KEYRING_DEB"
    else
        echo "[Dry Run] Would install keyring from $KEYRING_DEB"
    fi
    rm "$KEYRING_DEB"

    info "Adding the Samsung Unified Linux Driver Repository (SULDR)"
    SULDR_SOURCE_LIST="/etc/apt/sources.list.d/samsung-uld.list"
    if [ ! -f "$SULDR_SOURCE_LIST" ]; then
        if [ "$DRY_RUN" = false ]; then
            echo "deb https://www.bchemnet.com/suldr/ debian extra" | sudo tee "$SULDR_SOURCE_LIST" > /dev/null
        else
            echo "[Dry Run] Would add SULDR repository to $SULDR_SOURCE_LIST"
        fi
    else
        echo "SULDR repository source file already exists."
    fi

    info "Updating package lists"
    if [ "$DRY_RUN" = false ]; then
        sudo apt-get update
    else
        echo "[Dry Run] Would run apt-get update"
    fi

    info "Installing the Samsung printer and scanner driver"
    if [ "$DRY_RUN" = false ]; then
        # The M2070 series uses the 1.00.39 driver version.
        sudo apt-get install -y suld-driver2-1.00.39
    else
        echo "[Dry Run] Would install suld-driver2-1.00.39"
    fi

    info "Installation complete!"
    echo
    if [ "$DRY_RUN" = false ]; then
        echo "Your Samsung M2070 MFP should now be ready."
        echo "To add the printer, go to 'Settings' > 'Printers' > 'Add Printer'."
        echo "To test the scanner, use an application like 'Document Scanner' (simple-scan)."
    fi
    set +e
}

# --- Main Execution ---
install_samsung_driver "$@"

echo
echo "The script has finished."
