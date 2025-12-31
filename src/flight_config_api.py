"""
Flight Tracker Configuration API - Plugin-portable design.

Handles:
- GET/POST for flight settings
- Test/status endpoints
- "What's Overhead?" on-demand lookup with aircraft enrichment

PLUGIN ARCHITECTURE:
- Uses shared aircraft_lookup module for database queries
- No dependencies on display_manager or FlightLiveManager
- Can be packaged as standalone plugin for Chuck's plugin store
"""

from flask import jsonify, request, send_from_directory
from pathlib import Path
import logging
import requests
import time
import math

logger = logging.getLogger(__name__)

# ============================================
# Shared OAuth state for on-demand lookups
# ============================================
_oauth_state = {
    'access_token': None,
    'token_expiry': 0
}

# Cache for on-demand flight lookups (30 second TTL)
_flight_cache = {
    'data': None,
    'timestamp': 0,
    'ttl': 30
}


def _refresh_oauth_token(client_id: str, client_secret: str) -> bool:
    """Refresh OAuth2 access token using client credentials flow."""
    if not client_id or not client_secret:
        return False
    
    # Check if token is still valid (with 5 minute buffer)
    if _oauth_state['access_token'] and time.time() < (_oauth_state['token_expiry'] - 300):
        return True
    
    try:
        url = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
        
        data = {
            'grant_type': 'client_credentials',
            'client_id': client_id,
            'client_secret': client_secret,
        }
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        response = requests.post(url, data=data, headers=headers, timeout=10)
        
        if response.status_code == 200:
            token_data = response.json()
            _oauth_state['access_token'] = token_data.get('access_token')
            expires_in = token_data.get('expires_in', 3600)
            _oauth_state['token_expiry'] = time.time() + expires_in
            return True
        else:
            logger.error(f"Failed to refresh OpenSky token: {response.status_code}")
            return False
            
    except Exception as e:
        logger.error(f"Error refreshing OpenSky token: {e}")
        return False


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
            
            flight_config = config.get('flights', {})
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
            config = config_manager.load_config()
            data = request.json
            
            if 'flights' not in config:
                config['flights'] = {}
            
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
            
            if 'display' not in config:
                config['display'] = {}
            if 'display_durations' not in config['display']:
                config['display']['display_durations'] = {}
            
            display_duration = int(data.get('display_duration', 10))
            config['display']['display_durations']['flight_live'] = display_duration
            
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
            return jsonify({'status': 'error', 'message': str(e)}), 500
    
    @app.route('/api/flight-status', methods=['GET'])
    def get_flight_status():
        """Get current flight tracker status."""
        try:
            from src.config_manager import ConfigManager
            import datetime
            
            config_manager = ConfigManager()
            config = config_manager.load_config()
            flight_config = config.get('flights', {})
            
            now = datetime.datetime.now()
            current_hour = now.hour
            start_hour = flight_config.get('start_hour', 6)
            end_hour = flight_config.get('end_hour', 23)
            
            if start_hour <= end_hour:
                currently_active = start_hour <= current_hour < end_hour
            else:
                currently_active = current_hour >= start_hour or current_hour < end_hour
            
            has_credentials = bool(
                flight_config.get('opensky_client_id') and 
                flight_config.get('opensky_client_secret')
            )
            
            # Get aircraft lookup status
            lookup_status = {}
            try:
                from src.aircraft_lookup import get_status
                lookup_status = get_status()
            except ImportError:
                lookup_status = {'aircraft_db': False, 'faa_db': False}
            
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
                'auth_status': 'configured' if has_credentials else 'not_configured',
                'databases': lookup_status,
                'note': 'Real-time flight count only available in display process'
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
            headers = {'Content-Type': 'application/x-www-form-urlencoded'}
            
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
            
            lat_delta = radius_km / 111.0
            lon_delta = radius_km / (111.0 * math.cos(math.radians(home_lat)))
            
            api_url = (f"https://opensky-network.org/api/states/all?"
                      f"lamin={home_lat - lat_delta:.6f}&lomin={home_lon - lon_delta:.6f}&"
                      f"lamax={home_lat + lat_delta:.6f}&lomax={home_lon + lon_delta:.6f}")
            
            api_response = requests.get(api_url, 
                                        headers={'Authorization': f'Bearer {access_token}'}, 
                                        timeout=15)
            
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
                'message': 'Successfully connected to OpenSky API'
            })
            
        except requests.exceptions.Timeout:
            return jsonify({'success': False, 'error': 'Request timeout'})
        except requests.exceptions.RequestException as e:
            return jsonify({'success': False, 'error': f'Network error: {str(e)}'})
        except Exception as e:
            logger.error(f"Error testing flight connection: {e}")
            return jsonify({'success': False, 'error': str(e)})

    # ============================================
    # What's Overhead? - Plugin-portable endpoint
    # ============================================
    @app.route('/api/flights/current', methods=['GET'])
    def get_current_flights():
        """
        On-demand flight lookup - "What's Overhead right now?"
        
        Returns all flights currently in radius with enriched data from
        the shared aircraft_lookup module (FAA DB, aircraft DB, hexdb.io).
        
        Uses 30-second cache to prevent API spam.
        """
        try:
            from src.config_manager import ConfigManager
            
            config_manager = ConfigManager()
            config = config_manager.load_config()
            flight_config = config.get('flights', {})
            
            if not flight_config.get('enabled', False):
                return jsonify({
                    'success': False,
                    'error': 'Flight tracking is not enabled',
                    'flights': []
                })
            
            home_lat = flight_config.get('home_lat', 41.6)
            home_lon = flight_config.get('home_lon', -93.6)
            radius_km = flight_config.get('radius_km', 8.0)
            client_id = flight_config.get('opensky_client_id', '')
            client_secret = flight_config.get('opensky_client_secret', '')
            min_altitude_m = flight_config.get('min_altitude_m', 500)
            
            # Check cache
            current_time = time.time()
            if (_flight_cache['data'] is not None and 
                current_time - _flight_cache['timestamp'] < _flight_cache['ttl']):
                logger.debug("Returning cached flight data")
                return jsonify(_flight_cache['data'])
            
            # Refresh OAuth token
            if not _refresh_oauth_token(client_id, client_secret):
                return jsonify({
                    'success': False,
                    'error': 'Failed to authenticate with OpenSky',
                    'flights': []
                })
            
            # Calculate bounding box
            lat_delta = radius_km / 111.0
            lon_delta = radius_km / (111.0 * math.cos(math.radians(home_lat)))
            
            # Fetch from OpenSky
            url = (f"https://opensky-network.org/api/states/all?"
                   f"lamin={home_lat - lat_delta:.6f}&lomin={home_lon - lon_delta:.6f}&"
                   f"lamax={home_lat + lat_delta:.6f}&lomax={home_lon + lon_delta:.6f}")
            
            headers = {'Authorization': f'Bearer {_oauth_state["access_token"]}'}
            response = requests.get(url, headers=headers, timeout=15)
            
            # Try to increment API counter
            try:
                from web_interface_v2 import increment_api_counter
                increment_api_counter('opensky')
            except:
                pass
            
            if response.status_code != 200:
                return jsonify({
                    'success': False,
                    'error': f'OpenSky API returned {response.status_code}',
                    'flights': []
                })
            
            data = response.json()
            states = data.get('states', [])
            
            # Import shared lookup module (plugin-portable!)
            has_enrichment = False
            try:
                from src.aircraft_lookup import lookup_aircraft_info, infer_aircraft_type, calculate_bearing
                has_enrichment = True
            except ImportError:
                logger.warning("aircraft_lookup module not available - basic data only")
                # Define fallback functions
                def lookup_aircraft_info(icao24):
                    return {}
                def infer_aircraft_type(callsign, alt, spd, icao):
                    return 'UNK'
                def calculate_bearing(hlat, hlon, tlat, tlon):
                    dx = (tlon - hlon) * math.cos(math.radians(hlat))
                    dy = tlat - hlat
                    angle = math.degrees(math.atan2(dx, dy))
                    if angle < 0:
                        angle += 360
                    directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
                    return directions[int((angle + 22.5) / 45) % 8]
            
            flights = []
            for state in (states or []):
                icao24 = state[0]
                callsign = state[1]
                if callsign:
                    callsign = callsign.strip()
                
                lon = state[5]
                lat = state[6]
                altitude_m = state[7]
                on_ground = state[8]
                velocity = state[9]
                
                # Skip missing data or ground traffic
                if not callsign or lon is None or lat is None:
                    continue
                if on_ground or (altitude_m and altitude_m < min_altitude_m):
                    continue
                
                # Calculate distance
                dx = (lon - home_lon) * 111.0 * math.cos(math.radians(home_lat))
                dy = (lat - home_lat) * 111.0
                distance_km = math.sqrt(dx * dx + dy * dy)
                
                # Convert units
                altitude_ft = int(altitude_m * 3.28084) if altitude_m else None
                speed_knots = int(velocity * 1.94384) if velocity else None
                distance_miles = distance_km * 0.621371
                
                # Calculate direction
                direction = calculate_bearing(home_lat, home_lon, lat, lon)
                
                # Enrich with database lookups
                aircraft_info = lookup_aircraft_info(icao24)
                aircraft_type = infer_aircraft_type(callsign, altitude_ft, speed_knots, icao24)
                
                flights.append({
                    'icao24': icao24,
                    'callsign': callsign,
                    'altitude_ft': altitude_ft,
                    'distance_km': round(distance_km, 1),
                    'distance_miles': round(distance_miles, 1),
                    'direction': direction,
                    'speed_knots': speed_knots,
                    'aircraft_type': aircraft_type,
                    'display_type': aircraft_info.get('display_type'),
                    'typecode': aircraft_info.get('typecode'),
                    'registration': aircraft_info.get('registration'),
                    'operator': aircraft_info.get('operator')
                })
            
            # Sort by distance
            flights.sort(key=lambda f: f['distance_km'])
            
            result = {
                'success': True,
                'count': len(flights),
                'radius_km': radius_km,
                'radius_miles': round(radius_km * 0.621371, 1),
                'timestamp': current_time,
                'enriched': has_enrichment,
                'flights': flights
            }
            
            # Cache result
            _flight_cache['data'] = result
            _flight_cache['timestamp'] = current_time
            
            return jsonify(result)
            
        except Exception as e:
            logger.error(f"Error in flight check: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': str(e),
                'flights': []
            })
    
    logger.info("Flight configuration routes registered (plugin-portable)")
