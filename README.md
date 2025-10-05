# Ubuntu Installer

This repository contains a set of scripts to automate the setup of a new Ubuntu installation.

## Scripts

*   `1_ubuntuStart`: Sets the system time, installs a minimal set of networking tools, Chrome, and Telegram. It also disables the sudo password prompt.
*   `2_ubuntuDocker`: Installs Docker and a development environment. Requires a reboot after execution.
*   `3_ubuntuPack`: Installs a comprehensive suite of applications for developers.
*   `4_snap-apps`: Installs recommended Snap applications.
*   `5_samsung-printer-driver`: Installs the driver for Samsung printers.
*   `6_zerotier-client_en`: Installs the ZeroTier client for Ubuntu.
*   `7_vbox.py`: Installs VirtualBox and sets up a development environment for virtual machines.

## Additional Files

*   `$USER`: Contains minimal Ubuntu settings.
*   `AI.code-profile`: Visual Studio Code profile.
*   `music/`: Contains links to radio streams.
*   `.local/`: Contains Warp color schemes.
*   `OpenRGB`: Contains scripts for OpenRGB.
*   `Templates`: Contains HTML, JavaScript, Python, and README files.
*   `themes`: Contains themes for Ubuntu.
*   `wallpapers`: Contains wallpapers for Ubuntu.

(!) To use some of the scripts, you will need to unpack the archive using `*ssh`.
*   `archive.zip`: Contains the archive file.

## Usage

1.  Download the archive file from the repository.
2.  Unpack the archive using  `*ssh`.
    ```bash
    unzip archive.zip
    ```
3.  Run the scripts in order.



---

ENJOY SETTING UP UBUNTU!
