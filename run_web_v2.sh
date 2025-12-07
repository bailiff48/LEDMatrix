#!/bin/bash

# LED Matrix Web Interface V2 Runner
# This script runs the web interface using system Python

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Setting up LED Matrix Web Interface V2..."

# Install dependencies using system Python
echo "Installing dependencies..."
python3 -m pip install --break-system-packages -r requirements_web_v2.txt

# Install rgbmatrix module from local source
echo "Installing rgbmatrix module..."
python3 -m pip install --break-system-packages -e rpi-rgb-led-matrix-master/bindings/python

# Run the web interface
echo "Starting web interface on http://0.0.0.0:5001"
python3 web_interface_v2.py 