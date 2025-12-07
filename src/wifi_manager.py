"""
WiFi Manager for LED Matrix Ticker
Handles WiFi setup mode (AP) and normal client mode with LED status display
Uses NetworkManager (nmcli) for all WiFi operations
"""

import subprocess
import time
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class WiFiManager:
    """Manages WiFi configuration with AP mode fallback and LED status display."""
    
    # Constants
    AP_SSID = "TickerSetup"
    AP_PASSWORD = "ledticker"  # Required by Broadcom WiFi chip - simple and memorable
    AP_IP = "192.168.4.1"
    CONFIG_FILE = Path.home() / "LEDMatrix" / "config" / "wifi_config.json"
    CONNECTION_TIMEOUT = 60  # seconds to wait for connection
    WIFI_INTERFACE = "wlan0"
    
    # LED message queue (will be read by display_controller)
    LED_MESSAGE_FILE = Path.home() / "LEDMatrix" / "config" / "wifi_status.json"
    
    def __init__(self, led_display=None):
        """
        Initialize WiFi Manager.
        
        Args:
            led_display: Optional LED display object for showing status messages
        """
        self.led_display = led_display
        self.config_file = self.CONFIG_FILE
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        
    # ==================== LED Status Messages ====================
    
    def show_led_message(self, message: str, duration: int = 5):
        """
        Show a message on the LED display.
        Writes to a JSON file that display_controller can read.
        
        Args:
            message: Text to display
            duration: How long to show message (seconds)
        """
        try:
            status = {
                'message': message,
                'timestamp': time.time(),
                'duration': duration
            }
            self.LED_MESSAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.LED_MESSAGE_FILE, 'w') as f:
                json.dump(status, f)
            logger.info(f"LED message: {message}")
            
            # If we have direct LED display access, use it
            if self.led_display:
                try:
                    self.led_display.show_text(message, duration=duration)
                except Exception as e:
                    logger.warning(f"Could not show LED message directly: {e}")
                    
        except Exception as e:
            logger.error(f"Error showing LED message: {e}")
    
    def clear_led_message(self):
        """Clear any WiFi status message from LED display."""
        try:
            if self.LED_MESSAGE_FILE.exists():
                self.LED_MESSAGE_FILE.unlink()
        except Exception as e:
            logger.error(f"Error clearing LED message: {e}")
    
    # ==================== Configuration Management ====================
    
    def load_config(self) -> Dict:
        """Load WiFi configuration from file."""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading WiFi config: {e}")
        return {}
    
    def save_config(self, config: Dict):
        """Save WiFi configuration to file."""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            logger.info("WiFi configuration saved")
        except Exception as e:
            logger.error(f"Error saving WiFi config: {e}")
            raise
    
    def is_configured(self) -> bool:
        """Check if WiFi has been configured."""
        config = self.load_config()
        return bool(config.get('ssid'))
    
    # ==================== Network Scanning ====================
    
    def scan_networks(self) -> List[Dict]:
        """
        Scan for available WiFi networks.
        
        Returns:
            List of dicts with network info: {ssid, signal, security}
        """
        try:
            # Use nmcli to scan for networks
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY', 'dev', 'wifi', 'list'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                logger.error(f"Network scan failed: {result.stderr}")
                return []
            
            networks = []
            seen_ssids = set()
            
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                    
                parts = line.split(':')
                if len(parts) >= 3:
                    ssid = parts[0].strip()
                    signal = parts[1].strip()
                    security = parts[2].strip()
                    
                    # Skip empty SSIDs and duplicates
                    if ssid and ssid not in seen_ssids:
                        seen_ssids.add(ssid)
                        networks.append({
                            'ssid': ssid,
                            'signal': int(signal) if signal.isdigit() else 0,
                            'security': security,
                            'encrypted': bool(security and security != '--')
                        })
            
            # Sort by signal strength
            networks.sort(key=lambda x: x['signal'], reverse=True)
            logger.info(f"Found {len(networks)} WiFi networks")
            return networks
            
        except Exception as e:
            logger.error(f"Error scanning networks: {e}")
            return []
    
    # ==================== Connection Management ====================
    
    def get_current_connection(self) -> Optional[Dict]:
        """
        Get information about current WiFi connection.
        
        Returns:
            Dict with connection info or None if not connected
        """
        try:
            # Check if connected
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'DEVICE,STATE,CONNECTION', 'dev', 'status'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            for line in result.stdout.strip().split('\n'):
                parts = line.split(':')
                if len(parts) >= 3 and parts[0] == self.WIFI_INTERFACE:
                    device, state, connection = parts[0], parts[1], parts[2]
                    
                    if state == 'connected':
                        # Get IP address
                        ip_result = subprocess.run(
                            ['ip', '-4', 'addr', 'show', self.WIFI_INTERFACE],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        
                        ip_address = None
                        for line in ip_result.stdout.split('\n'):
                            if 'inet ' in line:
                                ip_address = line.strip().split()[1].split('/')[0]
                                break
                        
                        return {
                            'connected': True,
                            'ssid': connection,
                            'ip_address': ip_address,
                            'interface': device
                        }
            
            return {'connected': False}
            
        except Exception as e:
            logger.error(f"Error getting current connection: {e}")
            return {'connected': False}
    
    def connect_to_network(self, ssid: str, password: str = None) -> Tuple[bool, str]:
        """
        Connect to a WiFi network.
        
        Args:
            ssid: Network SSID
            password: Network password (None for open networks)
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            logger.info(f"Attempting to connect to: {ssid}")
            self.show_led_message(f"Connecting to {ssid}...", duration=10)
            
            # First, try to connect to existing connection
            check_result = subprocess.run(
                ['nmcli', 'connection', 'show', ssid],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if check_result.returncode == 0:
                # Connection exists, activate it
                logger.info(f"Found existing connection for {ssid}, activating...")
                result = subprocess.run(
                    ['nmcli', 'connection', 'up', ssid],
                    capture_output=True,
                    text=True,
                    timeout=self.CONNECTION_TIMEOUT
                )
            else:
                # Create new connection
                logger.info(f"Creating new connection for {ssid}...")
                cmd = ['nmcli', 'dev', 'wifi', 'connect', ssid]
                if password:
                    cmd.extend(['password', password])
                
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.CONNECTION_TIMEOUT
                )
            
            if result.returncode == 0:
                # Wait a moment for connection to stabilize
                time.sleep(2)
                
                # Verify connection
                conn_info = self.get_current_connection()
                if conn_info.get('connected'):
                    # Save configuration
                    config = {
                        'ssid': ssid,
                        'configured_at': time.time()
                    }
                    self.save_config(config)
                    
                    ip = conn_info.get('ip_address', 'Unknown')
                    self.show_led_message(f"Connected! {ip}", duration=5)
                    logger.info(f"Successfully connected to {ssid} with IP {ip}")
                    return True, f"Connected successfully! IP: {ip}"
                else:
                    self.show_led_message("Connection failed", duration=5)
                    return False, "Connected but no IP address assigned"
            else:
                error_msg = result.stderr.strip()
                logger.error(f"Connection failed: {error_msg}")
                self.show_led_message("Connection failed", duration=5)
                return False, f"Connection failed: {error_msg}"
                
        except subprocess.TimeoutExpired:
            msg = "Connection timed out"
            logger.error(msg)
            self.show_led_message("Connection timeout", duration=5)
            return False, msg
        except Exception as e:
            msg = f"Error connecting: {str(e)}"
            logger.error(msg)
            self.show_led_message("Connection error", duration=5)
            return False, msg
    
    def disconnect(self):
        """Disconnect from current WiFi network."""
        try:
            subprocess.run(
                ['nmcli', 'dev', 'disconnect', self.WIFI_INTERFACE],
                capture_output=True,
                timeout=10
            )
            logger.info("Disconnected from WiFi")
        except Exception as e:
            logger.error(f"Error disconnecting: {e}")
    
    # ==================== Access Point Mode ====================
    
    def start_ap_mode(self) -> bool:
        """
        Start WiFi Access Point mode for configuration.
        Creates an open network that users can connect to for setup.
        
        Returns:
            True if AP mode started successfully
        """
        try:
            logger.info(f"Starting AP mode: {self.AP_SSID}")
            self.show_led_message(f"WiFi Setup", duration=3)
            time.sleep(3)
            self.show_led_message(f"Connect to: {self.AP_SSID}", duration=5)
            time.sleep(5)
            self.show_led_message(f"Password: {self.AP_PASSWORD}", duration=10)
            
            # Stop any existing connection
            self.disconnect()
            time.sleep(1)
            
            # Delete any existing hotspot connections
            subprocess.run(
                ['nmcli', 'connection', 'delete', 'Hotspot'],
                capture_output=True,
                timeout=10
            )
            subprocess.run(
                ['nmcli', 'connection', 'delete', 'TickerSetup-AP'],
                capture_output=True,
                timeout=10
            )
            
            # Use the simple hotspot command (works best with Broadcom chips)
            logger.info("Creating hotspot with nmcli...")
            cmd = [
                'nmcli', 'device', 'wifi', 'hotspot',
                'ifname', self.WIFI_INTERFACE,
                'con-name', 'Hotspot',
                'ssid', self.AP_SSID,
                'band', 'bg'  # 2.4GHz for maximum compatibility
            ]
            
            if self.AP_PASSWORD:
                cmd.extend(['password', self.AP_PASSWORD])
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                logger.info(f"AP mode started: {self.AP_SSID}")
                time.sleep(2)
                
                # Verify hotspot is running
                status = self.get_ap_status()
                if status.get('active'):
                    logger.info(f"AP mode confirmed active at {self.AP_IP}")
                    return True
                else:
                    logger.error("AP mode started but not verified")
                    return False
            else:
                logger.error(f"Failed to start AP mode: {result.stderr}")
                self.show_led_message("AP mode failed", duration=5)
                return False
                
        except Exception as e:
            logger.error(f"Error starting AP mode: {e}")
            self.show_led_message("Setup mode error", duration=5)
            return False
    
    def stop_ap_mode(self):
        """Stop Access Point mode."""
        try:
            logger.info("Stopping AP mode")
            
            # Turn off AP connection
            subprocess.run(
                ['nmcli', 'connection', 'down', 'TickerSetup-AP'],
                capture_output=True,
                timeout=10
            )
            
            # Also try the default Hotspot name just in case
            subprocess.run(
                ['nmcli', 'connection', 'down', 'Hotspot'],
                capture_output=True,
                timeout=10
            )
            
            # Delete the connections
            subprocess.run(
                ['nmcli', 'connection', 'delete', 'TickerSetup-AP'],
                capture_output=True,
                timeout=10
            )
            
            subprocess.run(
                ['nmcli', 'connection', 'delete', 'Hotspot'],
                capture_output=True,
                timeout=10
            )
            
            logger.info("AP mode stopped")
            
        except Exception as e:
            logger.error(f"Error stopping AP mode: {e}")
    
    def get_ap_status(self) -> Dict:
        """
        Get status of Access Point mode.
        
        Returns:
            Dict with AP status info
        """
        try:
            # Check if Hotspot connection is active
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'NAME,TYPE,DEVICE', 'connection', 'show', '--active'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            for line in result.stdout.strip().split('\n'):
                parts = line.split(':')
                if len(parts) >= 2 and 'hotspot' in parts[1].lower():
                    return {
                        'active': True,
                        'ssid': self.AP_SSID,
                        'ip': self.AP_IP,
                        'interface': parts[2] if len(parts) > 2 else self.WIFI_INTERFACE
                    }
            
            return {'active': False}
            
        except Exception as e:
            logger.error(f"Error getting AP status: {e}")
            return {'active': False}
    
    # ==================== Auto Mode Selection ====================
    
    def auto_configure(self) -> str:
        """
        Automatically configure WiFi based on current state.
        This is the main entry point called on boot.
        
        Returns:
            'client' if connected to WiFi
            'ap' if in setup mode
            'failed' if something went wrong
        """
        logger.info("Starting WiFi auto-configuration...")
        
        # Check if WiFi is configured
        if not self.is_configured():
            logger.info("No WiFi configured, starting AP mode")
            if self.start_ap_mode():
                return 'ap'
            else:
                return 'failed'
        
        # Try to connect to configured network
        config = self.load_config()
        ssid = config.get('ssid')
        
        logger.info(f"Attempting to connect to saved network: {ssid}")
        self.show_led_message(f"Connecting...", duration=5)
        
        # Check if already connected to the right network
        conn_info = self.get_current_connection()
        if conn_info.get('connected') and conn_info.get('ssid') == ssid:
            ip = conn_info.get('ip_address', 'Unknown')
            logger.info(f"Already connected to {ssid} with IP {ip}")
            self.show_led_message(f"Connected! {ip}", duration=3)
            self.clear_led_message()  # Clear after showing briefly
            return 'client'
        
        # Try to activate the saved connection
        try:
            result = subprocess.run(
                ['nmcli', 'connection', 'up', ssid],
                capture_output=True,
                text=True,
                timeout=self.CONNECTION_TIMEOUT
            )
            
            if result.returncode == 0:
                time.sleep(2)
                conn_info = self.get_current_connection()
                if conn_info.get('connected'):
                    ip = conn_info.get('ip_address', 'Unknown')
                    logger.info(f"Connected to {ssid} with IP {ip}")
                    self.show_led_message(f"Connected! {ip}", duration=3)
                    self.clear_led_message()
                    return 'client'
        except Exception as e:
            logger.error(f"Error connecting to saved network: {e}")
        
        # Connection failed, start AP mode
        logger.warning(f"Could not connect to {ssid}, starting AP mode")
        self.show_led_message("WiFi Failed - Setup Mode", duration=5)
        time.sleep(5)
        
        if self.start_ap_mode():
            return 'ap'
        else:
            return 'failed'
    
    # ==================== Utility Methods ====================
    
    def forget_network(self):
        """Forget the currently configured network."""
        try:
            config = self.load_config()
            ssid = config.get('ssid')
            
            if ssid:
                # Delete the connection
                subprocess.run(
                    ['nmcli', 'connection', 'delete', ssid],
                    capture_output=True,
                    timeout=10
                )
                logger.info(f"Forgot network: {ssid}")
            
            # Clear configuration
            if self.config_file.exists():
                self.config_file.unlink()
                logger.info("WiFi configuration cleared")
                
        except Exception as e:
            logger.error(f"Error forgetting network: {e}")
    
    def get_signal_strength(self) -> Optional[int]:
        """
        Get current WiFi signal strength.
        
        Returns:
            Signal strength percentage (0-100) or None if not connected
        """
        try:
            conn_info = self.get_current_connection()
            if not conn_info.get('connected'):
                return None
            
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'IN-USE,SIGNAL', 'dev', 'wifi'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            for line in result.stdout.strip().split('\n'):
                if line.startswith('*'):
                    parts = line.split(':')
                    if len(parts) >= 2:
                        return int(parts[1])
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting signal strength: {e}")
            return None


# ==================== Standalone Testing ====================

if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("WiFi Manager Test")
    print("=" * 50)
    
    manager = WiFiManager()
    
    print("\n1. Current connection status:")
    conn_info = manager.get_current_connection()
    print(json.dumps(conn_info, indent=2))
    
    print("\n2. Scanning for networks...")
    networks = manager.scan_networks()
    print(f"Found {len(networks)} networks:")
    for net in networks[:5]:  # Show top 5
        print(f"  - {net['ssid']}: {net['signal']}% ({net['security']})")
    
    print("\n3. Configuration status:")
    print(f"  Configured: {manager.is_configured()}")
    if manager.is_configured():
        config = manager.load_config()
        print(f"  Saved SSID: {config.get('ssid')}")
    
    print("\n4. AP mode status:")
    ap_status = manager.get_ap_status()
    print(json.dumps(ap_status, indent=2))
    
    print("\n5. Signal strength:")
    signal = manager.get_signal_strength()
    print(f"  {signal}%" if signal else "  Not connected")
    
    print("\n" + "=" * 50)
    print("Test complete!")
