#!/bin/bash
# Install the 4am daily reboot cron job for root

echo "Installing 4am daily reboot cron job..."

# Check if already exists
if sudo crontab -l 2>/dev/null | grep -q "/sbin/reboot"; then
    echo "Reboot cron job already exists."
else
    # Add to root's crontab
    (sudo crontab -l 2>/dev/null; echo "0 4 * * * /sbin/reboot") | sudo crontab -
    echo "Reboot cron job installed."
fi

echo "Current root crontab:"
sudo crontab -l
