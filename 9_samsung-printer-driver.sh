#!/bin/bash
echo " "
echo "Установка МФУ Samsung M2070"
echo "--------------------------------------------------------------"

cd ~/Downloads

wget https://ftp.hp.com/pub/softlib/software13/printers/SS/SL-M4580FX/uld_V1.00.39_01.17.tar.gz    

tar -xf uld_V1.00.39_01.17.tar.gz

cd ~/Downloads/uld

sudo bash install.sh

sudo ln -sf /usr/lib/sane/libsane-smfp.so* /usr/lib/x86_64-linux-gnu/sane/

sudo apt install libusb-0.1-4

cd ~/Downloads
rm uld_V1.00.39_01.17.tar.gz
