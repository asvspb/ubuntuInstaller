#!/bin/bash

sudo systemctl stop docker
cd ~/Dev/coronachess && make down

echo '-------------------------------------------------------------------'
echo '---------------------- REBOOT IN 5 SEC ----------------------------'
echo '-------------------------------------------------------------------'
sleep 5
sudo reboot

