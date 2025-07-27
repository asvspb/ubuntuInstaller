#!/bin/bash

echo ""
echo "Installing standard python"
echo "--------------------------------------------------------------"

sudo apt install python3 python3-pip python3-venv python3-tk python3-py -y
# install libs
pip install requests beautifulsoup4

# fix python version 3.11 in the system
#sudo ln -s /usr/bin/python3.11 /usr/bin/python

# run python extended versions list
echo ""
echo "Checking extended python versions..."
echo "--------------------------------------------------------------"
python3 ./py-versions.py

# Get the output of the Python script
versions=$(python3 ./py-versions.py)

# Initialize variables
python3_security=""
python3_bugfix=""

# Parse the output of the Python script to extract relevant information
while read -r line; do
    # Extract version and maintenance status
    version=$(echo "$line" | awk '{print $3}')
    maintenance_status=$(echo "$line" | awk '{print $6}')

    # Check maintenance status and assign version accordingly
    if [[ "$maintenance_status" == "security" ]]; then
        python3_security=$version
    elif [[ "$maintenance_status" == "bugfix" ]]; then
        python3_bugfix=$version
    fi
done <<< "$versions"

# Update package lists
#sudo apt update

# Install Python versions based on maintenance status
if [[ -n "$python3_security" ]]; then
    sudo apt install "$python3_security" -y
fi

if [[ -n "$python3_bugfix" ]]; then
    sudo apt install "$python3_bugfix" -y
fi

echo ""
echo "Python is up to date!"
echo "--------------------------------------------------------------"