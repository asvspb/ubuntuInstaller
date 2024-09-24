import os
import requests
from bs4 import BeautifulSoup
import re
import subprocess
import sys
import getpass

# Define download directory
download_dir = os.path.expanduser("~/Downloads/VirtualBox/")

# Create download directory if it does not exist
if not os.path.exists(download_dir):
    os.makedirs(download_dir)

def add_user_to_vboxusers():
    current_user = getpass.getuser()
    try:
        subprocess.run(['sudo', 'usermod', '-a', '-G', 'vboxusers', current_user], check=True)
        print(f"\nUser {current_user} added to vboxusers group.")
    except subprocess.CalledProcessError:
        print("\nFailed to add user to vboxusers group. Make sure you have sudo privileges.")

def run_vboxconfig():
    try:
        subprocess.run(['sudo', '/sbin/vboxconfig'], check=True)
        print("VirtualBox kernel modules configured successfully.")
    except subprocess.CalledProcessError:
        print("Failed to configure VirtualBox kernel modules.")

def get_latest_virtualbox_version():
    print("\nSearching for new Virtualbox version....")
    url = "https://www.virtualbox.org/wiki/Downloads"
    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'html.parser')

    version_pattern = r'https://download\.virtualbox\.org/virtualbox/(\d+\.\d+\.\d+)/'
    versions = soup.find_all(href=re.compile(version_pattern))

    if versions:
        latest_version = max(re.search(version_pattern, v['href']).group(1) for v in versions)
        return latest_version

    return None

VIRTUALBOX_VERSION = get_latest_virtualbox_version() or "7.1.0"

distros = {
    "1": "Oracle Linux 9 / Red Hat Enterprise Linux 9",
    "2": "Oracle Linux 8 / Red Hat Enterprise Linux 8",
    "3": "Ubuntu 24.04",
    "4": "Ubuntu 22.04",
    "5": "Ubuntu 20.04",
    "6": "Debian 12",
    "7": "Debian 11",
    "8": "openSUSE 15.3 / 15.4 / 15.5 / 15.6",
    "9": "Fedora 40",
    "10": "Fedora 36 / 37 / 38 / 39",
    "11": "All distributions"
}

def get_download_url(version, distro_choice):
    base_url = f"https://download.virtualbox.org/virtualbox/{version}/"

    urls = {
        "1": f"{base_url}VirtualBox-{version}-1.el9.x86_64.rpm",  # Oracle Linux 9 / RHEL 9
        "2": f"{base_url}VirtualBox-{version}-1.el8.x86_64.rpm",  # Oracle Linux 8 / RHEL 8
        "3": f"{base_url}virtualbox-7.1_{version}-164728~Ubuntu~noble_amd64.deb",  # Ubuntu 24.04
        "4": f"{base_url}virtualbox-7.1_{version}-164728~Ubuntu~jammy_amd64.deb",  # Ubuntu 22.04
        "5": f"{base_url}virtualbox-7.1_{version}-164728~Ubuntu~focal_amd64.deb",  # Ubuntu 20.04
        "6": f"{base_url}virtualbox-7.1_{version}-164728~Debian~bookworm_amd64.deb",  # Debian 12
        "7": f"{base_url}virtualbox-7.1_{version}-164728~Debian~bullseye_amd64.deb",  # Debian 11
        "8": f"{base_url}VirtualBox-{version}-1.x86_64.rpm",  # openSUSE
        "9": f"{base_url}VirtualBox-{version}-1.fc40.x86_64.rpm",  # Fedora 40
        "10": f"{base_url}VirtualBox-{version}-1.fc36.x86_64.rpm",  # Fedora 36-39
        "11": f"{base_url}VirtualBox-{version}.tar.bz2"  # All distributions (source)
    }

    return urls.get(distro_choice)

def print_menu():
    print("\nPlease choose the appropriate package for your Linux distribution:")
    for key, value in distros.items():
        print(f"{key}. {value}")

def run_command(command):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    output, error = process.communicate()
    return output.decode(), error.decode(), process.returncode

def install_dependencies():
    print("\nUpdating package list...")
    run_command("sudo apt-get update")

    print("\nInstalling dependencies...")
    run_command("sudo apt-get install -y libxcb-cursor0")

def install_virtualbox(filename):
    print("\nInstalling VirtualBox...")
    output, error, return_code = run_command(f"sudo dpkg -i {filename}")

    if return_code != 0:
        print("An error occurred during installation. Attempting to fix...")
        run_command("sudo apt-get install -f -y")
        output, error, return_code = run_command(f"sudo dpkg -i {filename}")

    if return_code == 0:
        print("Installation completed successfully.")
    else:
        print("Installation failed. Please check the error messages above.")
        print("Error output:")
        print(error)

def verify_url(url):
    try:
        response = requests.head(url)
        return response.status_code == 200
    except requests.RequestException:
        return False

def get_extension_pack_url(version):
    base_url = f"https://download.virtualbox.org/virtualbox/{version}/"
    try:
        response = requests.get(base_url)
        soup = BeautifulSoup(response.content, 'html.parser')
        for link in soup.find_all('a'):
            href = link.get('href')
            if href and href.endswith('.vbox-extpack'):
                return base_url + href
    except requests.RequestException as e:
        print(f"Error getting file list: {e}")
    return None

def get_guest_additions_url(version):
    return f"https://download.virtualbox.org/virtualbox/{version}/VBoxGuestAdditions_{version}.iso"

def download_file(url, distro_name=None):
    if not verify_url(url):
        print(f"Error: Could not find file at URL {url}")
        print("Please check the VirtualBox version and try again.")
        return None

    filename = url.split("/")[-1]
    filepath = os.path.join(download_dir, filename)
    if distro_name:
        print(f"Downloading VirtualBox version {VIRTUALBOX_VERSION} for {distro_name}...")
    else:
        print(f"Downloading file: {filename}")
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))
        block_size = 1024  # 1 Kibibyte
        with open(filepath, "wb") as f:
            for data in response.iter_content(block_size):
                size = f.write(data)

        print("Download completed. File saved to:", filepath)
        return filepath
    except requests.RequestException as e:
        print(f"Error downloading file: {e}")
        return None

def main():
    print(f"\nLatest VirtualBox version: {VIRTUALBOX_VERSION}")
    print_menu()
    choice = input("\nEnter your choice (1-11): ")

    if choice in distros:
        distro_name = distros[choice]
        url = get_download_url(VIRTUALBOX_VERSION, choice)
        if url:
            if verify_url(url):
                filepath = download_file(url, distro_name)
                if filepath:
                    if choice in ["3", "4", "5", "6", "7"]:  # Debian-based systems
                        install_dependencies()
                    install_virtualbox(filepath)
            else:
                print(f"Error: Could not find file at URL {url}")
                print("Please check the VirtualBox version and try again.")
        else:
            print("An error occurred while getting the download URL.")
    else:
        print("Invalid choice. Please choose a number from 1 to 11.")

    # Add user to vboxusers group
    add_user_to_vboxusers()
    # Run vboxconfig
    run_vboxconfig()
    # Extension Pack
    extension_pack_url = get_extension_pack_url(VIRTUALBOX_VERSION)
    if extension_pack_url:
        print(f"\nDownloading Virtualbox {VIRTUALBOX_VERSION} Extension Pack ....")
        extension_pack_path = download_file(extension_pack_url)
        if extension_pack_path:
            install_extension_pack(extension_pack_path)
        else:
            print(f"Failed to download Extension Pack for VirtualBox {VIRTUALBOX_VERSION}")
    else:
        print(f"Could not find URL for VirtualBox {VIRTUALBOX_VERSION} Extension Pack")
    # Guest Additions
    guest_additions_url = get_guest_additions_url(VIRTUALBOX_VERSION)
    print(f"\nDownloading Virtualbox {VIRTUALBOX_VERSION} Guest Additions ....")
    guest_additions_path = download_file(guest_additions_url)
    if not guest_additions_path:
        print(f"Failed to download Guest Additions for VirtualBox {VIRTUALBOX_VERSION}")

def install_extension_pack(filepath):
    print("Installing VirtualBox Extension Pack...")
    command = f"echo y | sudo VBoxManage extpack install --replace {filepath}"
    output, error, return_code = run_command(command)

    if return_code == 0:
        print("Extension Pack installed successfully.")
    else:
        print("Failed to install Extension Pack. Error:")
        print(error)

if __name__ == "__main__":
    main()
