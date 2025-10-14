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

# Global options
DRY_RUN=false
AUTO_INSTALL_CUPS_CLIENT=false
AUTO_SET_DEFAULT_PRINTER=false

# Parse CLI arguments
parse_args() {
  for arg in "$@"; do
    case "$arg" in
    --dry-run)
      DRY_RUN=true
      ;;
    --auto-install-cups-client)
      AUTO_INSTALL_CUPS_CLIENT=true
      ;;
    --auto-set-default-printer)
      AUTO_SET_DEFAULT_PRINTER=true
      ;;
    -h | --help)
      echo "Usage: $0 [--dry-run] [--auto-install-cups-client] [--auto-set-default-printer]"
      exit 0
      ;;
    *)
      echo "Warning: Unknown option: $arg"
      ;;
    esac
  done

  if [ "$DRY_RUN" = true ]; then
    echo "Performing a dry run. No actual changes will be made."
  fi
}

# --- Install via the SULDR Repository ---
install_samsung_driver() {
  info "Executing: Install via SULDR Repository"
  set -e

  info "Checking for existing driver..."
  if dpkg -s suld-driver2-1.00.39 &>/dev/null; then
    echo "Samsung driver is already installed. Skipping installation."
    return 0
  fi
  # Using global DRY_RUN parsed from CLI arguments

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
      echo "deb https://www.bchemnet.com/suldr/ debian extra" | sudo tee "$SULDR_SOURCE_LIST" >/dev/null
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

# --- Default printer check ---
check_default_printer() {
  info "Checking system default printer"

  # Ensure lpstat is available (cups-client)
  if ! command -v lpstat >/dev/null 2>&1; then
    if [ "$AUTO_INSTALL_CUPS_CLIENT" = true ]; then
      info "cups-client not found. Installing cups-client"
      if [ "$DRY_RUN" = false ]; then
        sudo apt-get update
        sudo apt-get install -y cups-client
      else
        echo "[Dry Run] Would install cups-client"
      fi
    else
      echo "lpstat command not found. Install 'cups-client' to enable printer status checks."
      echo "Example: sudo apt-get install -y cups-client"
      return 0
    fi
  fi

  # Check CUPS service status
  if command -v systemctl >/dev/null 2>&1; then
    if ! systemctl is-active --quiet cups; then
      echo "CUPS service is not active. You may need to start it: sudo systemctl enable --now cups"
    fi
  fi

  DEFAULT=$(lpstat -d 2>/dev/null | awk -F': ' '/system default destination:/ {print $2}')
  if [ -n "$DEFAULT" ]; then
    echo "System default printer: $DEFAULT"
    return 0
  fi

  echo "No system default printer is set."
  PRINTERS=$(lpstat -p 2>/dev/null | awk '/^printer / {print $2}')
  if [ -n "$PRINTERS" ]; then
    PRINTER_COUNT=$(printf "%s\n" "$PRINTERS" | awk 'NF' | wc -l | tr -d ' ')
    if [ "$PRINTER_COUNT" -eq 1 ] && [ "$AUTO_SET_DEFAULT_PRINTER" = true ]; then
      ONLY_PRINTER=$(printf "%s\n" "$PRINTERS" | head -n1)
      info "Setting system default printer to: $ONLY_PRINTER"
      if [ "$DRY_RUN" = false ]; then
        sudo lpadmin -d "$ONLY_PRINTER"
        echo "System default printer set to: $ONLY_PRINTER"
      else
        echo "[Dry Run] Would set system default printer: sudo lpadmin -d \"$ONLY_PRINTER\""
      fi
    else
      echo "Detected printers:"
      echo "$PRINTERS"
      echo "Set a default printer with:"
      echo "  sudo lpadmin -d <PRINTER_NAME>    # system-wide default"
      echo "  lpoptions -d <PRINTER_NAME>       # user default"
    fi
  else
    echo "No printers are configured yet. Add a printer via 'Settings' > 'Printers' or using lpadmin."
  fi
}

# --- Main Execution ---
parse_args "$@"
install_samsung_driver

check_default_printer

echo
echo "The script has finished."
