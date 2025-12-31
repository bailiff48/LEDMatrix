"""
Flight Check API - On-demand aircraft lookup

Adds an endpoint to check what aircraft are currently overhead,
independent of the main flight_manager display cycle.
"""

import math
import logging
import requests
from flask import jsonify
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Cache to avoid hammering OpenSky API
_flight_cache = {
    'data': None,
    'timestamp': None,
    'ttl_seconds': 30  # Cache for 30 seconds
}


def register_flight_check_routes(app):
    """Register the flight check API routes."""
    
    @app.route('/api/flights/current', methods=['GET'])
    def get_current_flights():
        """
        Get aircraft currently within configured radius.
        Returns cached data if available and fresh, otherwise fetches from OpenSky.
        """
        try:
            from src.config_manager import ConfigManager
            
            config_manager = ConfigManager()
            config = config_manager.load_config()
            flight_config = config.get('flights', {})
            
            # Check if flight tracking is configured
            if not flight_config.get('enabled', False):
                return jsonify({
                    'success': False,
                    'error': 'Flight tracking is not enabled',
                    'flights': []
                })
            
            # Get location settings
            home_lat = flight_config.get('home_lat')
            home_lon = flight_config.get('home_lon')
            radius_km = flight_config.get('radius_km', 15)
            min_altitude_m = flight_config.get('min_altitude_m', 150)
            
            if not home_lat or not home_lon:
                return jsonify({
                    'success': False,
                    'error': 'Home location not configured',
                    'flights': []
                })
            
            # Check cache first
            now = datetime.now()
            if (_flight_cache['data'] is not None and 
                _flight_cache['timestamp'] and
                (now - _flight_cache['timestamp']).seconds < _flight_cache['ttl_seconds']):
                logger.info("Returning cached flight data")
                return jsonify({
                    'success': True,
                    'cached': True,
                    'cache_age_seconds': (now - _flight_cache['timestamp']).seconds,
                    **_flight_cache['data']
                })
            
            # Fetch fresh data from OpenSky
            flights = fetch_flights_from_opensky(
                flight_config,
                home_lat, 
                home_lon, 
                radius_km,
                min_altitude_m
            )
            
            result = {
                'flights': flights,
                'count': len(flights),
                'radius_km': radius_km,
                'radius_miles': round(radius_km * 0.621371, 1),
                'home_lat': home_lat,
                'home_lon': home_lon,
                'timestamp': now.isoformat()
            }
            
            # Update cache
            _flight_cache['data'] = result
            _flight_cache['timestamp'] = now
            
            return jsonify({
                'success': True,
                'cached': False,
                **result
            })
            
        except Exception as e:
            logger.error(f"Error fetching current flights: {e}")
            return jsonify({
                'success': False,
                'error': str(e),
                'flights': []
            }), 500
    
    logger.info("Flight check routes registered")


def fetch_flights_from_opensky(flight_config, lat, lon, radius_km, min_altitude_m):
    """
    Fetch flights from OpenSky API within the specified radius.
    """
    # Get OAuth credentials
    client_id = flight_config.get('opensky_client_id', '')
    client_secret = flight_config.get('opensky_client_secret', '')
    
    # Calculate bounding box
    lat_delta = radius_km / 111.0
    lon_delta = radius_km / (111.0 * math.cos(math.radians(lat)))
    
    lat_min = lat - lat_delta
    lat_max = lat + lat_delta
    lon_min = lon - lon_delta
    lon_max = lon + lon_delta
    
    # Build API URL
    url = (f"https://opensky-network.org/api/states/all?"
           f"lamin={lat_min:.6f}&lomin={lon_min:.6f}&"
           f"lamax={lat_max:.6f}&lomax={lon_max:.6f}")
    
    headers = {}
    
    # Try OAuth authentication first
    if client_id and client_secret:
        token = get_oauth_token(client_id, client_secret)
        if token:
            headers['Authorization'] = f'Bearer {token}'
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            logger.warning(f"OpenSky API returned {response.status_code}")
            return []
        
        data = response.json()
        states = data.get('states', [])
        
        if not states:
            return []
        
        flights = []
        for state in states:
            try:
                icao24 = state[0]
                callsign = (state[1] or '').strip()
                lon_f = state[5]
                lat_f = state[6]
                altitude_m = state[7]  # Barometric altitude
                geo_altitude_m = state[13]  # Geometric altitude (often more accurate)
                on_ground = state[8]
                velocity = state[9]
                heading = state[10]
                
                # Use geometric altitude if available, else barometric
                alt = geo_altitude_m if geo_altitude_m is not None else altitude_m
                
                if not callsign or lon_f is None or lat_f is None:
                    continue
                
                if on_ground:
                    continue
                
                # Filter by minimum altitude
                if alt is not None and alt < min_altitude_m:
                    continue
                
                # Calculate actual distance
                dx = (lon_f - lon) * 111.0 * math.cos(math.radians(lat))
                dy = (lat_f - lat) * 111.0
                distance_km = math.sqrt(dx * dx + dy * dy)
                
                # Only include flights within actual radius (not just bounding box)
                if distance_km > radius_km:
                    continue
                
                # Calculate bearing
                bearing = calculate_bearing(lat, lon, lat_f, lon_f)
                direction = bearing_to_direction(bearing)
                
                altitude_ft = int(alt * 3.28084) if alt else 0
                speed_knots = int(velocity * 1.94384) if velocity else 0
                
                flights.append({
                    'callsign': callsign,
                    'icao24': icao24,
                    'distance_km': round(distance_km, 1),
                    'distance_miles': round(distance_km * 0.621371, 1),
                    'altitude_ft': altitude_ft,
                    'speed_knots': speed_knots,
                    'heading': int(heading) if heading else None,
                    'direction': direction,
                    'bearing': int(bearing)
                })
            except (IndexError, TypeError) as e:
                continue
        
        # Sort by distance
        flights.sort(key=lambda x: x['distance_km'])
        
        return flights
        
    except requests.RequestException as e:
        logger.error(f"Error fetching from OpenSky: {e}")
        return []


def get_oauth_token(client_id, client_secret):
    """Get OAuth2 token from OpenSky."""
    try:
        response = requests.post(
            'https://opensky-network.org/api/oauth/token',
            data={
                'grant_type': 'client_credentials',
                'client_id': client_id,
                'client_secret': client_secret
            },
            timeout=10
        )
        
        if response.status_code == 200:
            return response.json().get('access_token')
        else:
            logger.warning(f"OAuth token request failed: {response.status_code}")
            return None
            
    except Exception as e:
        logger.error(f"Error getting OAuth token: {e}")
        return None


def calculate_bearing(lat1, lon1, lat2, lon2):
    """Calculate bearing from point 1 to point 2."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    diff_lon = math.radians(lon2 - lon1)
    
    x = math.sin(diff_lon) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(diff_lon)
    
    bearing = math.atan2(x, y)
    bearing = math.degrees(bearing)
    bearing = (bearing + 360) % 360
    
    return bearing


def bearing_to_direction(bearing):
    """Convert bearing to compass direction."""
    directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    index = round(bearing / 22.5) % 16
    return directions[index]
