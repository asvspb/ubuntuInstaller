# Ubuntu Installer

This repository contains a set of scripts to automate the setup of a new Ubuntu installation.

## Scripts

*   `1_ubuntuStart`: Sets the system time, installs a minimal set of networking tools, Chrome, and Telegram. It also disables the sudo password prompt.
*   `2_ubuntuDocker`: Installs Docker and a development environment. Requires a reboot after execution.
*   `3_ubuntuPack`: Installs a comprehensive suite of applications for developers.
*   `4_py-update`: Installs the latest versions of Python.
*   `5_js-update`: Installs the latest JavaScript environment.
*   `6_snap-apps`: Installs recommended Snap applications.
*   `7_samsung-printer-driver`: Installs the driver for Samsung printers.
*   `8_zerotier-client_en`: Installs the ZeroTier client for Ubuntu.

## Additional Files

*   `$USER`: Contains minimal Ubuntu settings.
*   `asv.code-profile`: Visual Studio Code profile.
*   `music/`: Contains links to radio streams.
*   `.local/`: Contains Warp color schemes.

## Usage

To use these scripts, you will need to unpack the archive using `-ssh`.

To run the Samsung printer driver script, navigate to the `scripts` directory and execute the following command:

```bash
sudo ./7_samsung-printer-driver.sh
```

---

ENJOY SETTING UP UBUNTU!
