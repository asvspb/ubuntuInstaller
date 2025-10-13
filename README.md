# Ubuntu Installer Framework

This repository contains a framework to automate the setup of a new Ubuntu installation using a declarative approach.

## Framework Components

*   `install.sh`: Main installation script that orchestrates the installation process.
*   `config.yaml`: Configuration file defining what components to install (profiles, roles, variables).
*   `lib.sh`: Common library functions for all modules (logging, error handling, package management, etc.).
*   `Makefile`: Contains targets for linting, formatting, dry-run, and installation.
*   `roles/`: Directory containing modular roles for different components (base-system, dev-tools, docker, etc.).

## Scripts (Legacy)

*   `scripts/1_ubuntuStart.sh`: Legacy script - Sets the system time, installs a minimal set of networking tools, Chrome, and Telegram. It also disables the sudo password prompt.
*   `scripts/2_ubuntuDocker.sh`: Legacy script - Installs Docker and a development environment. Requires a reboot after execution.
*   `scripts/3_ubuntuPack.sh`: Legacy script - Installs a comprehensive suite of applications for developers.
*   `scripts/4_snap-apps.sh`: Legacy script - Installs recommended Snap applications.
*   `scripts/5_samsung-printer-driver.sh`: Legacy script - Installs the driver for Samsung printers.
*   `scripts/6_zerotier-client.sh`: Legacy script - Installs the ZeroTier client for Ubuntu.
*   `scripts/7_vbox.py`: Legacy script - Installs VirtualBox and sets up a development environment for virtual machines.
*   `scripts/ubuntu_snap_packages.txt`: Contains a list of recommended snap packages.

## Additional Files

*   `$USER/`: Contains minimal Ubuntu settings, bash/zsh configurations, gitconfig, and cleanup scripts.
*   `$USER/Dev/AI.code-profile`: Visual Studio Code profile.
*   `music/`: Contains links to radio streams.
*   `$USER/OpenRGB/`: Contains scripts and configurations for OpenRGB.
*   `$USER/Templates/`: Contains HTML, JavaScript, Python, and README templates.
*   `$USER/themes/`: Contains themes for Ubuntu (BigSur, Graphite, Monterey, Ventoy-Dark, xu).
*   `PROGRAM_DESCRIPTIONS.md`: Contains detailed descriptions of programs installed by the scripts.
*   `IMPROVEMENT_SUGGESTIONS.md`: Development roadmap for transitioning from scripts to framework.

## Supported Versions

*   Ubuntu 22.04 LTS
*   Ubuntu 24.04 LTS
*   Architecture: amd64

## Architecture

The Ubuntu Installer Framework follows a modular, declarative approach:

### Configuration-Driven Installation
* Uses YAML-based configuration files to define the desired system state
* Supports different profiles (developer, server, wsl) with customizable roles
* Variables can be passed to roles to customize behavior

### Modular Roles System
* Each component is encapsulated in a role (e.g., base-system, dev-tools, docker)
* Roles are stored in the `roles/` directory with numeric prefixes for execution order
* Roles are idempotent - can be run multiple times without side effects

### Library Functions
* `lib.sh` provides common functions for logging, error handling, package management, etc.
* Includes functions for dry-run support, system checks, and security verification
* All scripts and roles utilize these common functions

### Makefile Integration
* Standardized interface for common operations (lint, fmt, dry-run, install)
* Supports both development and production workflows

## Usage

### Using the New Framework

1. Clone the repository:
    ```bash
    git clone https://github.com/asvspb/ubuntuInstaller.git
    ```
2. Customize the `config.yaml` file to specify your desired configuration:
    ```yaml
    settings:
      non_interactive: true
      log_file: "/var/log/ubuntuInstaller/install.log"
    profile: "auto" # Автоматическое определение профиля (desktop-developer, server, wsl)
    roles_enabled:
      - name: 0-base-system
      - name: 10-dev-tools
        vars:
          install_vscode: true
          install_pycharm: false
      - name: 20-docker
        enabled: true
    ```
3. Run a dry-run to simulate the installation:
    ```bash
    # Using make (simplified simulation)
    make dry-run
    
    # Using the main script (full simulation with configuration processing)
    ./install.sh --dry-run
    ```
4. Run the installation:
    ```bash
    sudo ./install.sh install
    # Or using make
    sudo make install
    ```
5. To uninstall components:
    ```bash
    sudo ./install.sh uninstall
    # Or using make
    sudo make uninstall
    ```
6. To update installed components:
    ```bash
    sudo ./install.sh update
    # Or using make
    sudo make update
    ```

### Profiles
The framework supports different system profiles:
- `desktop-developer`: Полнофункциональная десктопная система для разработки
- `server`: Серверная система с минимальным набором компонентов
- `wsl`: Система для Windows Subsystem for Linux
- `auto`: Автоматическое определение профиля на основе характеристик системы

You can specify a profile in the config file or use one of the predefined profile configs in the `profiles/` directory:
    ```bash
    sudo ./install.sh -c profiles/desktop-developer.yaml
    sudo ./install.sh -c profiles/server.yaml
    sudo ./install.sh -c profiles/wsl.yaml
    ```

### Interactive Mode
The framework includes a mini-TUI for interactive selection of profiles and roles:
    ```bash
    sudo ./mini-tui.sh
    ```

### Makefile Targets

* `make lint` - Check syntax of all shell scripts using shellcheck
* `make fmt` - Format all shell scripts using shfmt
* `make dry-run` - Simulate installation without making changes (simplified)
* `make install` - Execute the installation process
* `make uninstall` - Remove installed components
* `make update` - Update installed components
* `make info` - Display framework information

### Configuration Options

The `config.yaml` file supports the following options:

* `settings.non_interactive` - Run without user prompts (default: true)
* `settings.log_file` - Path to log file (default: /var/log/ubuntuInstaller/install-$(date +%Y-%m-%d).log)
* `profile` - System profile (desktop-developer, server, wsl, auto)
* `roles_enabled` - List of roles to execute with optional variables
* `roles_enabled[n].enabled` - Enable or disable specific role (default: true)
* `roles_enabled[n].vars` - Variables to pass to role

### Role Development

To create a new role:
1. Create a directory in `roles/` with a numeric prefix (e.g., `30-new-role/`)
2. Add a `main.sh` script that uses functions from `lib.sh`
3. Optionally add an `uninstall.sh` script for role removal
4. Reference the role in `config.yaml`

Roles should be idempotent and support dry-run mode through the `DRY_RUN` variable.

### Phase 4: Rollback and State Management

The framework now supports advanced rollback and state management features:

#### Uninstall Support
* Each role can have an `uninstall.sh` script for component removal
* Use `./install.sh uninstall` or `make uninstall` to remove installed components
* Roles are removed in reverse order of installation

#### Pre-snapshots for Critical Roles
* Automatic snapshots are created before installing critical roles (0-base-system, 20-docker, 30-secure-default)
* Requires Timeshift to be installed for snapshot functionality
* Snapshots are created with descriptive comments for easy identification

#### Update Mechanism
* Use `./install.sh update` or `make update` to update installed components
* Roles are updated or reinstalled as needed
* State tracking ensures only necessary updates are performed

#### State Tracking
* Installed roles are tracked in `/var/lib/ubuntuInstaller.state`
* Prevents duplicate installations and enables proper update/uninstall operations
* State file is automatically managed by the framework

### Legacy Scripts (Deprecated)

1. Navigate to the scripts directory:
    ```bash
    cd ubuntuInstaller/scripts
    ```
2. Make the desired script executable:
    ```bash
    chmod +x script_name.sh
    ```
3. Run the scripts in order, starting with `1_ubuntuStart.sh`:
    ```bash
    sudo ./1_ubuntuStart.sh
    ```

The `config.yaml` file supports the following options:

* `settings.non_interactive` - Run without user prompts (default: true)
* `settings.log_file` - Path to log file (default: /var/log/ubuntuInstaller/install-$(date +%Y-%m-%d).log)
* `profile` - System profile (developer, server, wsl)
* `roles_enabled` - List of roles to execute with optional variables
* `roles_enabled[n].enabled` - Enable or disable specific role (default: true)

### Role Development

To create a new role:
1. Create a directory in `roles/` with a numeric prefix (e.g., `30-new-role/`)
2. Add a `main.sh` script that uses functions from `lib.sh`
3. Reference the role in `config.yaml`

Roles should be idempotent and support dry-run mode through the `DRY_RUN` variable.

### Legacy Scripts (Deprecated)

1. Navigate to the scripts directory:
    ```bash
    cd ubuntuInstaller/scripts
    ```
2. Make the desired script executable:
    ```bash
    chmod +x script_name.sh
    ```
3. Run the scripts in order, starting with `1_ubuntuStart.sh`:
    ```bash
    sudo ./1_ubuntuStart.sh
    ```




---