"""
WiFi Manager API Routes
Flask routes for WiFi configuration, scanning, and connection management
"""

from flask import jsonify, request, send_from_directory, render_template
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def register_wifi_routes(app, wifi_manager):
    """
    Register WiFi management routes with the Flask app.
    
    Args:
        app: Flask application instance
        wifi_manager: WiFiManager instance
    """
    
    @app.route('/wifi-setup')
    def wifi_setup_page():
        """Serve the WiFi setup captive portal page."""
        try:
            static_dir = Path(app.root_path) / 'static'
            return send_from_directory(static_dir, 'wifi_setup.html')
        except Exception as e:
            logger.error(f"Error serving WiFi setup page: {e}")
            return "WiFi Setup Page Not Found", 404
    
    # Captive portal detection endpoints (for mobile devices)
    @app.route('/generate_204')
    @app.route('/gen_204')
    @app.route('/ncsi.txt')
    @app.route('/hotspot-detect.html')
    def captive_portal_redirect():
        """
        Handle captive portal detection from mobile devices.
        Redirects to WiFi setup page.
        """
        from flask import redirect, url_for
        return redirect(url_for('wifi_setup_page'))
    
    @app.route('/api/wifi/scan', methods=['GET'])
    def scan_networks():
        """
        Scan for available WiFi networks.
        
        Returns:
            JSON: {
                success: bool,
                networks: [{ssid, signal, security, encrypted}, ...]
            }
        """
        try:
            networks = wifi_manager.scan_networks()
            return jsonify({
                'success': True,
                'networks': networks,
                'count': len(networks)
            })
        except Exception as e:
            logger.error(f"Error scanning networks: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/wifi/connect', methods=['POST'])
    def connect_network():
        """
        Connect to a WiFi network.
        
        Request JSON: {
            ssid: str,
            password: str (optional)
        }
        
        Returns:
            JSON: {
                success: bool,
                message: str
            }
        """
        try:
            data = request.json
            ssid = data.get('ssid')
            password = data.get('password')
            
            if not ssid:
                return jsonify({
                    'success': False,
                    'message': 'Network name (SSID) is required'
                }), 400
            
            logger.info(f"Attempting to connect to network: {ssid}")
            success, message = wifi_manager.connect_to_network(ssid, password)
            
            if success:
                # Stop AP mode after successful connection
                try:
                    wifi_manager.stop_ap_mode()
                except Exception as e:
                    logger.warning(f"Error stopping AP mode: {e}")
                
                return jsonify({
                    'success': True,
                    'message': message
                })
            else:
                return jsonify({
                    'success': False,
                    'message': message
                }), 400
                
        except Exception as e:
            logger.error(f"Error connecting to network: {e}")
            return jsonify({
                'success': False,
                'message': f"Connection error: {str(e)}"
            }), 500
    
    @app.route('/api/wifi/status', methods=['GET'])
    def wifi_status():
        """
        Get current WiFi connection status.
        
        Returns:
            JSON: {
                connected: bool,
                ssid: str (if connected),
                ip_address: str (if connected),
                signal: int (if connected),
                ap_mode: bool
            }
        """
        try:
            conn_info = wifi_manager.get_current_connection()
            ap_status = wifi_manager.get_ap_status()
            
            response = {
                'connected': conn_info.get('connected', False),
                'ap_mode': ap_status.get('active', False)
            }
            
            if conn_info.get('connected'):
                response['ssid'] = conn_info.get('ssid')
                response['ip_address'] = conn_info.get('ip_address')
                response['signal'] = wifi_manager.get_signal_strength()
            
            if ap_status.get('active'):
                response['ap_ssid'] = ap_status.get('ssid')
                response['ap_ip'] = ap_status.get('ip')
            
            return jsonify(response)
            
        except Exception as e:
            logger.error(f"Error getting WiFi status: {e}")
            return jsonify({
                'error': str(e)
            }), 500
    
    @app.route('/api/wifi/disconnect', methods=['POST'])
    def disconnect_network():
        """
        Disconnect from current WiFi network.
        
        Returns:
            JSON: {
                success: bool,
                message: str
            }
        """
        try:
            wifi_manager.disconnect()
            return jsonify({
                'success': True,
                'message': 'Disconnected from WiFi'
            })
        except Exception as e:
            logger.error(f"Error disconnecting: {e}")
            return jsonify({
                'success': False,
                'message': str(e)
            }), 500
    
    @app.route('/api/wifi/forget', methods=['POST'])
    def forget_network():
        """
        Forget the currently configured network and enter setup mode.
        
        Returns:
            JSON: {
                success: bool,
                message: str
            }
        """
        try:
            wifi_manager.forget_network()
            
            # Start AP mode for reconfiguration
            if wifi_manager.start_ap_mode():
                return jsonify({
                    'success': True,
                    'message': 'Network forgotten. Setup mode activated.'
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'Network forgotten but failed to start setup mode'
                }), 500
                
        except Exception as e:
            logger.error(f"Error forgetting network: {e}")
            return jsonify({
                'success': False,
                'message': str(e)
            }), 500
    
    @app.route('/api/wifi/ap/start', methods=['POST'])
    def start_ap_mode():
        """
        Manually start Access Point mode.
        
        Returns:
            JSON: {
                success: bool,
                message: str
            }
        """
        try:
            if wifi_manager.start_ap_mode():
                return jsonify({
                    'success': True,
                    'message': f'Setup mode started. Connect to "{wifi_manager.AP_SSID}"'
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'Failed to start setup mode'
                }), 500
        except Exception as e:
            logger.error(f"Error starting AP mode: {e}")
            return jsonify({
                'success': False,
                'message': str(e)
            }), 500
    
    @app.route('/api/wifi/ap/stop', methods=['POST'])
    def stop_ap_mode():
        """
        Stop Access Point mode.
        
        Returns:
            JSON: {
                success: bool,
                message: str
            }
        """
        try:
            wifi_manager.stop_ap_mode()
            return jsonify({
                'success': True,
                'message': 'Setup mode stopped'
            })
        except Exception as e:
            logger.error(f"Error stopping AP mode: {e}")
            return jsonify({
                'success': False,
                'message': str(e)
            }), 500
    
    logger.info("WiFi management routes registered")


# ==================== Standalone Testing Server ====================

if __name__ == "__main__":
    """
    Run standalone WiFi setup server for testing.
    This is useful for testing the captive portal without the full LED Matrix system.
    """
    from flask import Flask
    import sys
    sys.path.append(str(Path(__file__).parent))
    from wifi_manager import WiFiManager
    
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'wifi-setup-secret-key'
    
    # Initialize WiFi Manager
    wifi_manager = WiFiManager()
    
    # Register routes
    register_wifi_routes(app, wifi_manager)
    
    # Root route redirects to setup
    @app.route('/')
    def index():
        from flask import redirect, url_for
        return redirect(url_for('wifi_setup_page'))
    
    print("=" * 60)
    print("WiFi Setup Server - Standalone Mode")
    print("=" * 60)
    print("\nStarting server on port 5003...")
    print("Access at: http://localhost:5003")
    print("\nPress Ctrl+C to stop")
    print("=" * 60)
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    app.run(host='0.0.0.0', port=5003, debug=True)
