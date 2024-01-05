#!/bin/bash

VM_NAME="coronachess22.04"
VM_IP="192.168.1.160"
SSH_USER="ubuntu"
START_TIMEOUT=500  # Timeout in seconds
SSH_TIMEOUT=200    # Timeout for SSH connection in seconds

# Check if the VM is already running
if VBoxManage showvminfo "$VM_NAME" | grep -q "running (since"; then
  echo "The virtual machine is already running."
else
  # Start the VM
  VBoxManage startvm "$VM_NAME" --type headless

  # Wait for the VM to start
  start_time=$(date +%s)
  while ! VBoxManage showvminfo "$VM_NAME" | grep -q "running (since"; do
    current_time=$(date +%s)
    elapsed_time=$((current_time - start_time))
    if [[ $elapsed_time -ge $START_TIMEOUT ]]; then
      echo "Timeout: Failed to start the virtual machine within $START_TIMEOUT seconds."
      exit 1
    fi
    sleep 1
  done

fi

# Sleep for 2 minutes before attempting SSH connection
echo "Waiting for VM services before attempting SSH connection..."
sleep 1m

# Attempt SSH connection to the VM and run 'make up' in the coronachess/ directory
echo "Attempting to connect to the VM via SSH..."
connected=false
while ! $connected; do
  ssh -o ConnectTimeout=$SSH_TIMEOUT "$SSH_USER@$VM_IP" && connected=true
  sleep 1
done

