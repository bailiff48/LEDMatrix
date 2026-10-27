#!/bin/bash
# Installs the Wi-Fi setup service (TickerSetup hotspot + setup page on port 80).
# Run once per Pi:  sudo bash ~/LEDMatrix/scripts/install_wifi_setup.sh
set -e
cd "$(dirname "$0")/.."
if [ "$EUID" -ne 0 ]; then echo "Run with sudo"; exit 1; fi

command -v nmcli >/dev/null || { echo "NetworkManager (nmcli) is required"; exit 1; }
command -v iw >/dev/null || apt-get install -y iw || echo "Note: 'iw' not installed (optional)"

# Anything else on port 80 would block the setup page
if ss -ltnp 2>/dev/null | grep -q ':80 '; then
  echo "WARNING: something is already listening on port 80:"; ss -ltnp | grep ':80 '
fi

# Old hotspot profiles from the previous approach
nmcli connection delete Hotspot >/dev/null 2>&1 || true
nmcli connection delete TickerSetup-AP >/dev/null 2>&1 || true

install -m 644 ledmatrix-wifi-setup.service /etc/systemd/system/ledmatrix-wifi-setup.service
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
systemctl daemon-reload
systemctl enable ledmatrix-wifi-setup.service
systemctl restart ledmatrix-wifi-setup.service
echo "Installed. Status:"; systemctl --no-pager --lines=5 status ledmatrix-wifi-setup.service || true
