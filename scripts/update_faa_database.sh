#!/bin/bash
# Weekly FAA database update script
# Run via cron: 0 3 * * 0 /home/ledpi/LEDMatrix/scripts/update_faa_database.sh

cd /home/ledpi/LEDMatrix
echo "$(date): Starting FAA database update..."

# Download and extract
cd data/faa
wget -q --timeout=300 -O ReleasableAircraft.zip "https://registry.faa.gov/database/ReleasableAircraft.zip"

if [ $? -eq 0 ]; then
    unzip -o ReleasableAircraft.zip
    rm ReleasableAircraft.zip
    echo "$(date): FAA database updated successfully"
else
    echo "$(date): FAA database update failed"
fi
