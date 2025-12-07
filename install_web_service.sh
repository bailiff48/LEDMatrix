#!/bin/bash

# LED Matrix Web Interface Service Installer
# This script installs and enables the web interface systemd service

set -e

echo "Installing LED Matrix Web Interface Service..."

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (use sudo)"
    exit 1
fi

# Copy the service file to systemd directory
echo "Copying service file to /etc/systemd/system/"
cp ledmatrix-web.service /etc/systemd/system/

# Reload systemd to recognize the new service
echo "Reloading systemd..."
systemctl daemon-reload

# Enable the service to start on boot
echo "Enabling ledmatrix-web.service..."
systemctl enable ledmatrix-web.service

# Start the service
echo "Starting ledmatrix-web.service..."
systemctl start ledmatrix-web.service

# Check service status
echo "Checking service status..."
systemctl status ledmatrix-web.service --no-pager

echo ""
echo "Web interface service installed and started!"
echo "The web interface will now start automatically when:"
echo "1. The system boots"
echo "2. The 'web_display_autostart' setting is true in config/config.json"
echo ""
echo "To check the service status: systemctl status ledmatrix-web.service"
echo "To view logs: journalctl -u ledmatrix-web.service -f"
echo "To stop the service: systemctl stop ledmatrix-web.service"
echo "To disable autostart: systemctl disable ledmatrix-web.service"
