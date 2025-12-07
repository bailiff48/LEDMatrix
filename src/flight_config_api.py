"""
API routes for flight tracker configuration.
Handles GET/POST for flight settings and provides test/status endpoints.
"""

from flask import jsonify, request, send_from_directory
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

def register_flight_config_routes(app):
    """Register flight configuration routes with the Flask app."""
    
    @app.route('/flight-config')
    def flight_config_page():
        """Serve the flight configuration HTML page."""
        static_dir = Path(app.root_path) / 'static'
        return send_from_directory(static_dir, 'flight_config.html')
    
    @app.route('/api/flight-config', methods=['GET'])
    def get_flight_config():
        """Get current flight configuration."""
        try:
            from src.config_manager import ConfigManager
            config_manager = ConfigManager()
            config = config_manager.load_config()
            
            # Get flight config with defaults
            flight_config = config.get('flights', {})
            
            # Get display duration from display_durations
            display_durations = config.get('display', {}).get('display_durations', {})
            display_duration = display_durations.get('flight_live', 10)
            
            response = {
                'enabled': flight_config.get('enabled', False),
                'opensky_client_id': flight_config.get('opensky_client_id', ''),
                'opensky_client_secret': flight_config.get('opensky_client_secret', ''),
                'home_lat': flight_config.get('home_lat', 41.6),
                'home_lon': flight_config.get('home_lon', -93.6),
                'radius_km': flight_config.get('radius_km', 50),
                'max_flights': flight_config.get('max_flights', 10),
                'min_altitude_m': flight_config.get('min_altitude_m', 500),
                'update_interval': flight_config.get('update_interval', 30),
                'start_hour': flight_config.get('start_hour', 6),
                'end_hour': flight_config.get('end_hour', 23),
                'display_duration': display_duration
            }
            
            return jsonify(response)
            
        except Exception as e:
            logger.error(f"Error loading flight config: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/flight-config', methods=['POST'])
    def save_flight_config():
        """Save flight configuration."""
        try:
            from src.config_manager import ConfigManager
            config_manager = ConfigManager()
            
            # Get current config
            config = config_manager.load_config()
            
            # Get posted data
            data = request.json
            
            # Update flights section
            if 'flights' not in config:
                config['flights'] = {}
            
            # Update all fields
            config['flights']['enabled'] = data.get('enabled', False)
            config['flights']['opensky_client_id'] = data.get('opensky_client_id', '')
            config['flights']['opensky_client_secret'] = data.get('opensky_client_secret', '')
            config['flights']['home_lat'] = float(data.get('home_lat', 41.6))
            config['flights']['home_lon'] = float(data.get('home_lon', -93.6))
            config['flights']['radius_km'] = int(data.get('radius_km', 50))
            config['flights']['max_flights'] = int(data.get('max_flights', 10))
            config['flights']['min_altitude_m'] = int(data.get('min_altitude_m', 500))
            config['flights']['update_interval'] = int(data.get('update_interval', 30))
            config['flights']['start_hour'] = int(data.get('start_hour', 6))
            config['flights']['end_hour'] = int(data.get('end_hour', 23))
            
            # Also add to display durations if not present
            if 'display' not in config:
                config['display'] = {}
            if 'display_durations' not in config['display']:
                config['display']['display_durations'] = {}
            
            # Save display duration to flight_live key
            display_duration = int(data.get('display_duration', 10))
            config['display']['display_durations']['flight_live'] = display_duration
            
            # Save config
            config_manager.save_config(config)
            
            logger.info(f"Flight config saved: enabled={data.get('enabled')}, "
                       f"lat={data.get('home_lat')}, lon={data.get('home_lon')}, "
                       f"radius={data.get('radius_km')}km")
            
            return jsonify({
                'status': 'success',
                'message': 'Flight configuration saved successfully'
            })
            
        except Exception as e:
            logger.error(f"Error saving flight config: {e}")
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/flight-status', methods=['GET'])
    def get_flight_status():
        """Get current flight tracker status."""
        try:
            # Try to import flight manager - FIXED: FlightLiveManager not FlightManager
            try:
                from src.flight_manager import FlightLiveManager
            except ImportError as e:
                logger.error(f"Could not import FlightLiveManager: {e}")
                return jsonify({
                    'error': 'Flight manager not available',
                    'flights_visible': 0,
                    'auth_status': 'not_loaded',
                    'polling_window': {
                        'currently_active': False
                    }
                })
            
            # Get config to return status based on configuration
            from src.config_manager import ConfigManager
            import datetime
            
            config_manager = ConfigManager()
            config = config_manager.load_config()
            flight_config = config.get('flights', {})
            
            # Check if currently within polling window
            now = datetime.datetime.now()
            current_hour = now.hour
            start_hour = flight_config.get('start_hour', 6)
            end_hour = flight_config.get('end_hour', 23)
            
            # Handle polling window that might cross midnight
            if start_hour <= end_hour:
                currently_active = start_hour <= current_hour < end_hour
            else:
                # Window crosses midnight (e.g., 23-6 means 11pm to 6am)
                currently_active = current_hour >= start_hour or current_hour < end_hour
            
            # Determine auth status based on config
            has_credentials = bool(
                flight_config.get('opensky_client_id') and 
                flight_config.get('opensky_client_secret')
            )
            auth_status = 'valid' if has_credentials else 'not_configured'
            
            # Build status response
            # Note: This web process doesn't have access to real-time flight data
            # since FlightLiveManager runs in the separate display_controller process
            status = {
                'enabled': flight_config.get('enabled', False),
                'home_location': {
                    'latitude': flight_config.get('home_lat', 41.6),
                    'longitude': flight_config.get('home_lon', -93.6)
                },
                'radius_km': flight_config.get('radius_km', 8),
                'polling_window': {
                    'start_hour': start_hour,
                    'end_hour': end_hour,
                    'currently_active': currently_active and flight_config.get('enabled', False)
                },
                'flights_visible': 0,  # Web interface can't access real-time data from display process
                'last_update': None,   # Would need shared state between processes
                'auth_status': auth_status,
                'consecutive_errors': 0,
                'note': 'Real-time flight data only available in display process'
            }
            
            return jsonify(status)
            
        except Exception as e:
            logger.error(f"Error getting flight status: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/flight-test', methods=['GET'])
    def test_flight_connection():
        """Test OpenSky API connection."""
        try:
            from src.config_manager import ConfigManager
            import requests
            import time
            
            config_manager = ConfigManager()
            config = config_manager.load_config()
            flight_config = config.get('flights', {})
            
            client_id = flight_config.get('opensky_client_id', '')
            client_secret = flight_config.get('opensky_client_secret', '')
            
            if not client_id or not client_secret:
                return jsonify({
                    'success': False,
                    'error': 'OpenSky credentials not configured'
                })
            
            # Try to get OAuth token
            url = "https://opensky-network.org/api/oauth2/token"
            
            data = {
                'grant_type': 'client_credentials',
                'client_id': client_id,
                'client_secret': client_secret,
                'scope': 'opensky_default'
            }
            
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            response = requests.post(url, data=data, headers=headers, timeout=10)
            
            if response.status_code != 200:
                return jsonify({
                    'success': False,
                    'error': f'Authentication failed (HTTP {response.status_code})'
                })
            
            token_data = response.json()
            access_token = token_data.get('access_token')
            
            if not access_token:
                return jsonify({
                    'success': False,
                    'error': 'No access token received'
                })
            
            # Try to fetch flights
            home_lat = flight_config.get('home_lat', 41.6)
            home_lon = flight_config.get('home_lon', -93.6)
            radius_km = flight_config.get('radius_km', 50)
            
            # Calculate bounding box
            import math
            lat_delta = radius_km / 111.0
            lon_delta = radius_km / (111.0 * math.cos(math.radians(home_lat)))
            
            lat_min = home_lat - lat_delta
            lat_max = home_lat + lat_delta
            lon_min = home_lon - lon_delta
            lon_max = home_lon + lon_delta
            
            # Query API
            api_url = (f"https://opensky-network.org/api/states/all?"
                      f"lamin={lat_min:.6f}&lomin={lon_min:.6f}&"
                      f"lamax={lat_max:.6f}&lomax={lon_max:.6f}")
            
            api_headers = {
                'Authorization': f'Bearer {access_token}'
            }
            
            api_response = requests.get(api_url, headers=api_headers, timeout=15)
            
            if api_response.status_code != 200:
                return jsonify({
                    'success': False,
                    'error': f'API query failed (HTTP {api_response.status_code})'
                })
            
            data = api_response.json()
            states = data.get('states', [])
            flight_count = len(states) if states else 0
            
            return jsonify({
                'success': True,
                'flight_count': flight_count,
                'message': f'Successfully connected to OpenSky API'
            })
            
        except requests.exceptions.Timeout:
            return jsonify({
                'success': False,
                'error': 'Request timeout - check your internet connection'
            })
        except requests.exceptions.RequestException as e:
            return jsonify({
                'success': False,
                'error': f'Network error: {str(e)}'
            })
        except Exception as e:
            logger.error(f"Error testing flight connection: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            })
    
    logger.info("Flight configuration routes registered")
