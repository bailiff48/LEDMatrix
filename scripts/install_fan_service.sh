#!/bin/bash
# Install the fan controller service

echo "Installing LED Matrix Fan Service..."

# Copy service file
sudo cp /home/ledpi/LEDMatrix/systemd/ledmatrix-fan.service /etc/systemd/system/

# Reload systemd and enable service
sudo systemctl daemon-reload
sudo systemctl enable ledmatrix-fan
sudo systemctl start ledmatrix-fan

echo "Fan service installed and started!"
sudo systemctl status ledmatrix-fan --no-pager
