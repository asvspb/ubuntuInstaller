# Ubuntu Installer

This repository contains a set of scripts to automate the setup of a new Ubuntu installation.

## Scripts

*   `1_ubuntuStart.sh`: Sets the system time, installs a minimal set of networking tools, Chrome, and Telegram. It also disables the sudo password prompt.
*   `2_ubuntuDocker.sh`: Installs Docker and a development environment. Requires a reboot after execution.
*   `3_ubuntuPack.sh`: Installs a comprehensive suite of applications for developers.
*   `4_snap-apps.sh`: Installs recommended Snap applications.
*   `5_samsung-printer-driver.sh`: Installs the driver for Samsung printers.
*   `6_zerotier-client.sh`: Installs the ZeroTier client for Ubuntu.
*   `7_vbox.py`: Installs VirtualBox and sets up a development environment for virtual machines.
*   `ubuntu_snap_packages.txt`: Contains a list of recommended snap packages.

## Additional Files

*   `$USER/`: Contains minimal Ubuntu settings, bash/zsh configurations, gitconfig, and cleanup scripts.
*   `$USER/Dev/AI.code-profile`: Visual Studio Code profile.
*   `music/`: Contains links to radio streams.
*   `$USER/OpenRGB/`: Contains scripts and configurations for OpenRGB.
*   `$USER/Templates/`: Contains HTML, JavaScript, Python, and README templates.
*   `$USER/themes/`: Contains themes for Ubuntu (BigSur, Graphite, Monterey, Ventoy-Dark, xu).
*   `scripts/`: Contains all the installation scripts.
*   `PROGRAM_DESCRIPTIONS.md`: Contains detailed descriptions of programs installed by the scripts.

## Usage

1. Clone the repository:
    ```bash
    git clone https://github.com/asv-soft/ubuntuInstaller.git
    ```
2. Navigate to the scripts directory:
    ```bash
    cd ubuntuInstaller/scripts
    ```
3. Make the desired script executable:
    ```bash
    chmod +x script_name.sh
    ```
4. Run the scripts in order, starting with `1_ubuntuStart.sh`:
    ```bash
    sudo ./1_ubuntuStart.sh
    ```


---