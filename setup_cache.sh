#!/bin/bash

# LEDMatrix Cache Setup Script
# This script sets up a persistent cache directory for LEDMatrix

echo "Setting up LEDMatrix persistent cache directory..."

# Create the cache directory
sudo mkdir -p /var/cache/ledmatrix

# Get the real user (not root when running with sudo)
REAL_USER=${SUDO_USER:-$USER}

# Set ownership to the real user first
sudo chown $REAL_USER:$REAL_USER /var/cache/ledmatrix

# Set permissions to 777 to allow daemon user to write
sudo chmod 777 /var/cache/ledmatrix

echo "Cache directory created: /var/cache/ledmatrix"
echo "Ownership set to: $REAL_USER"
echo "Permissions set to: 777 (writable by all users including daemon)"

# Test if the directory is writable by the current user
if [ -w /var/cache/ledmatrix ]; then
    echo "✓ Cache directory is writable by current user"
else
    echo "✗ Cache directory is not writable by current user"
    exit 1
fi

# Test if the directory is writable by daemon user (which the system runs as)
if sudo -u daemon test -w /var/cache/ledmatrix; then
    echo "✓ Cache directory is writable by daemon user"
else
    echo "✗ Cache directory is not writable by daemon user"
    echo "This might cause issues when running with sudo"
fi

echo ""
echo "Setup complete! LEDMatrix will now use persistent caching."
echo "The cache will survive system restarts."
echo ""
echo "If you see warnings about using temporary cache directory,"
echo "the system will automatically fall back to /tmp/ledmatrix_cache/" 