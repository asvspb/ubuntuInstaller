#!/bin/bash

echo ""
echo "Installing standard python and dependencies"
echo "--------------------------------------------------------------"

# Install necessary system packages for Python
sudo apt install python3 python3-pip python3-venv python3-tk -y

# Python script to be executed
PYTHON_SCRIPT='''
import urllib.request
import json

def get_python_versions():
    """
    Fetches python versions from the official CPython GitHub repository tags.
    """
    try:
        url = "https://api.github.com/repos/python/cpython/tags"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status != 200:
                return
            data = response.read()
            tags = json.loads(data.decode("utf-8"))

        minor_versions = set()
        for tag in tags:
            tag_name = tag["name"]
            if tag_name.startswith("v"):
                parts = tag_name[1:].split(".")
                if len(parts) == 3 and all(p.isdigit() for p in parts):
                    minor_version = f"{parts[0]}.{parts[1]}"
                    major, minor = int(parts[0]), int(parts[1])
                    if major == 3 and minor >= 7:
                        minor_versions.add(minor_version)
        
        for v in sorted(list(minor_versions)):
             print(f"Python version {v} is in bugfix")

    except Exception:
        pass

if __name__ == "__main__":
    get_python_versions()
'''

echo ""
echo "Checking extended python versions..."
echo "--------------------------------------------------------------"

# Get the output of the Python script
versions=$(python3 -c "$PYTHON_SCRIPT")

if [ -z "$versions" ]; then
    echo "Could not retrieve Python versions. Exiting."
    exit 1
fi

# Initialize variable to hold all bugfix versions
python3_bugfix_versions=""

# Parse the output of the Python script to extract relevant information
while read -r line; do
    version=$(echo "$line" | awk '{print $3}')
    maintenance_status=$(echo "$line" | awk '{print $6}')

    if [[ "$maintenance_status" == "bugfix" ]]; then
        python3_bugfix_versions="$python3_bugfix_versions $version"
    fi
done <<< "$versions"

# Install Python versions based on maintenance status
if [[ -n "$python3_bugfix_versions" ]]; then
    for v in $python3_bugfix_versions; do
        package_name="python$v"
        echo "Installing bugfix-supported Python version: $package_name"
        sudo apt install "$package_name" -y
    done
fi

echo ""
echo "Python is up to date!"
echo "--------------------------------------------------------------"
