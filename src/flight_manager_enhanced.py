"""
FlightLiveManager - Enhanced with Aircraft Database Integration

LIVE Flight Tracker that interrupts display when flights are overhead.
Now enhanced with OpenSky aircraft database lookups for make/model information.

Features:
- Real-time flight tracking via OpenSky Network API
- Aircraft make/model from local SQLite database
- Direction bearing from home location
- Aircraft type inference (fallback when DB misses)
- Aircraft type icons
- Max interruption timeout with cooldown
- Live interruption pattern (like live sports games)
"""

import requests
import time
import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont
import logging
import json
import os
from pathlib import Path

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):
        pass

# Import aircraft database for make/model lookups
try:
    from aircraft_db import AircraftDatabase
    AIRCRAFT_DB_AVAILABLE = True
except ImportError:
    AIRCRAFT_DB_AVAILABLE = False
    AircraftDatabase = None

logger = logging.getLogger(__name__)


class FlightLiveManager:
    """
    LIVE Flight Tracker - Interrupts display when flights are overhead.
    
    Works like Chuck's live sports managers - when flights enter the configured
    radius, this manager takes priority and displays "what's overhead RIGHT NOW."
    
    Enhanced with:
    - Aircraft make/model from OpenSky database
    - Direction bearing calculation
    - Aircraft type inference and icons
    - Max interruption timeout with cooldown
    """
    
    # Airlines and military callsign prefixes for type inference (fallback)
    AIRLINE_PREFIXES = {
        'AAL', 'UAL', 'DAL', 'SWA', 'JBU', 'ASA', 'FFT', 'NKS',  # Major US
        'SKW', 'ENY', 'RPA', 'EDV', 'PDT', 'CPZ', 'ASQ',          # Regional
        'FDX', 'UPS', 'GTI', 'ABX',                                # Cargo
        'EJA', 'LXJ', 'XOJ', 'TVS', 'XAJ', 'LEA', 'WWI',          # Biz jets
        'BAW', 'AFR', 'DLH', 'KLM', 'UAE', 'QFA', 'ANA', 'JAL',   # International
    }
    
    MILITARY_CALLSIGNS = {
        'TETON', 'REACH', 'GUARD', 'VIPER', 'HAWK', 'EAGLE',
        'TIGER', 'COBRA', 'BOXER', 'NAVY', 'ARMY', 'AIR',
        'RCH', 'CNV', 'PAT', 'EVAC', 'SPAR', 'SAM',
    }
    
    HELO_CALLSIGNS = {'LIFE', 'MEDEVAC', 'MERCY', 'ANGEL', 'RESCUE', 'HELI'}
    
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager):
        self.config = config
        self.display_manager = display_manager
        self.cache_manager = cache_manager
        self.flight_config = config.get('flights', {})
        self.location = config.get('location', {})
        self.logger = logging.getLogger('FlightLiveManager')
        
        # Core settings
        self.enabled = self.flight_config.get('enabled', False)
        self.home_lat = self.flight_config.get('home_lat', 41.6)  # Des Moines area default
        self.home_lon = self.flight_config.get('home_lon', -93.6)
        self.radius_km = self.flight_config.get('radius_km', 8.0)  # Default ~5 miles
        
        # OAuth2 credentials
        self.client_id = self.flight_config.get('opensky_client_id', '')
        self.client_secret = self.flight_config.get('opensky_client_secret', '')
        
        # Token management
        self.access_token = None
        self.token_expiry = 0
        
        # Time-based polling window (avoid overnight checks)
        self.start_hour = self.flight_config.get('start_hour', 6)   # 6 AM default
        self.end_hour = self.flight_config.get('end_hour', 23)      # 11 PM default
        
        # Display settings
        self.max_flights = self.flight_config.get('max_flights', 10)
        self.min_altitude_m = self.flight_config.get('min_altitude_m', 500)  # Filter ground traffic
        
        # LIVE GAME PATTERN - Track active flights like live games
        self.live_flights = []  # Flights currently in range (like live_games)
        self.current_flight = None  # Currently displayed flight (like current_game)
        self.current_flight_index = 0
        self.last_update = 0
        self.update_interval = self.flight_config.get('update_interval', 10)
        self.last_flight_switch = 0
        self.flight_display_duration = self.flight_config.get('flight_display_duration', 5)
        self.last_display_update = 0
        
        # Max interruption timeout (Session 41 feature)
        self.max_interruption_time = self.flight_config.get('max_interruption_time', 30)
        self.cooldown_duration = self.flight_config.get('cooldown_duration', 120)
        self.interruption_start_time = None
        self.cooldown_flights: Dict[str, float] = {}  # icao24 -> cooldown_end_time
        
        # Logging control
        self.last_log_time = 0
        self.log_interval = 30
        
        # Error handling
        self.consecutive_errors = 0
        self.last_error_time = 0
        self.error_backoff_time = 60
        self.max_consecutive_errors = 5
        
        # Colors for display
        self.COLORS = {
            'white': (255, 255, 255),
            'gray': (128, 128, 128),
            'green': (0, 255, 0),
            'yellow': (255, 255, 0),
            'red': (255, 0, 0),
            'cyan': (0, 255, 255),
            'orange': (255, 165, 0),
            'gold': (255, 215, 0),
            'skyblue': (135, 206, 235),
        }
        
        # Display dimensions
        self.display_width = self.display_manager.matrix.width if hasattr(self.display_manager, 'matrix') else 128
        self.display_height = self.display_manager.matrix.height if hasattr(self.display_manager, 'matrix') else 32
        
        # Aircraft type icons
        self.aircraft_icons = {}
        self._load_aircraft_icons()
        
        # ===== AIRCRAFT DATABASE INTEGRATION =====
        self.aircraft_db = None
        self._init_aircraft_database()
        
        self.logger.info(f"FlightLiveManager initialized: lat={self.home_lat}, lon={self.home_lon}, radius={self.radius_km}km (~{self.radius_km * 0.621371:.1f} miles)")
        self.logger.info(f"Live interruption mode: Will display when flights enter {self.radius_km}km radius")
        self.logger.info(f"Max interruption: {self.max_interruption_time}s, Cooldown: {self.cooldown_duration}s")
        if self.aircraft_db and self.aircraft_db.is_ready():
            stats = self.aircraft_db.get_stats()
            self.logger.info(f"Aircraft database: {stats.get('total_aircraft', 0):,} aircraft loaded")
        else:
            self.logger.warning("Aircraft database not available - using callsign inference only")
    
    def _init_aircraft_database(self) -> None:
        """Initialize the aircraft database for make/model lookups."""
        if not AIRCRAFT_DB_AVAILABLE:
            self.logger.warning("aircraft_db module not found - make/model lookups disabled")
            return
        
        # Try standard location
        db_paths = [
            os.path.expanduser("~/LEDMatrix/data/aircraft.db"),
            "/home/ledpi/LEDMatrix/data/aircraft.db",
            os.path.join(os.path.dirname(__file__), "data", "aircraft.db"),
        ]
        
        for db_path in db_paths:
            if os.path.exists(db_path):
                try:
                    self.aircraft_db = AircraftDatabase(db_path)
                    if self.aircraft_db.is_ready():
                        self.logger.info(f"Aircraft database loaded: {db_path}")
                        return
                except Exception as e:
                    self.logger.warning(f"Failed to load aircraft DB from {db_path}: {e}")
        
        self.logger.warning("Aircraft database not found. Run 'python3 aircraft_db.py --setup' to download.")
    
    def _load_aircraft_icons(self) -> None:
        """Load aircraft type icons from assets directory."""
        icons_dir = Path(__file__).parent / "assets" / "logos" / "aircraft"
        
        if not icons_dir.exists():
            # Try alternate location
            icons_dir = Path(os.path.expanduser("~/LEDMatrix/assets/logos/aircraft"))
        
        icon_files = {
            'JET': 'jet.png',
            'MIL': 'military.png',
            'HELO': 'helicopter.png',
            'MIL_HELO': 'military_helicopter.png',  # Military helicopter
            'GA': 'ga.png',
            'UNK': 'unknown.png',
            'STAR': 'star.png',  # Military prefix indicator
        }
        
        for icon_type, filename in icon_files.items():
            icon_path = icons_dir / filename
            if icon_path.exists():
                try:
                    img = Image.open(icon_path).convert('RGBA')
                    # Resize to 16x16 if needed
                    if img.size != (16, 16):
                        img = img.resize((16, 16), Image.Resampling.LANCZOS)
                    self.aircraft_icons[icon_type] = img
                except Exception as e:
                    self.logger.warning(f"Failed to load icon {filename}: {e}")
        
        if self.aircraft_icons:
            self.logger.info(f"Loaded {len(self.aircraft_icons)} aircraft icons")
    
    def _is_within_polling_window(self) -> bool:
        """Check if current time is within configured polling window."""
        now = datetime.now()
        current_hour = now.hour
        
        if self.start_hour <= self.end_hour:
            return self.start_hour <= current_hour < self.end_hour
        else:
            return current_hour >= self.start_hour or current_hour < self.end_hour
    
    def _refresh_oauth_token(self) -> bool:
        """Refresh OAuth2 access token using client credentials flow."""
        if not self.client_id or not self.client_secret:
            logger.error("OpenSky OAuth2 credentials not configured")
            return False
        
        if self.access_token and time.time() < (self.token_expiry - 300):
            return True
        
        try:
            url = "https://opensky-network.org/api/oauth2/token"
            
            data = {
                'grant_type': 'client_credentials',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'scope': 'opensky_default'
            }
            
            headers = {'Content-Type': 'application/x-www-form-urlencoded'}
            
            response = requests.post(url, data=data, headers=headers, timeout=10)
            
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data.get('access_token')
                expires_in = token_data.get('expires_in', 3600)
                self.token_expiry = time.time() + expires_in
                
                logger.info(f"OpenSky OAuth2 token refreshed, expires in {expires_in}s")
                return True
            else:
                logger.error(f"Failed to refresh OpenSky token: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Error refreshing OpenSky token: {e}")
            return False
    
    def _calculate_bounding_box(self) -> Tuple[float, float, float, float]:
        """Calculate bounding box for API query."""
        lat_delta = self.radius_km / 111.0
        lon_delta = self.radius_km / (111.0 * math.cos(math.radians(self.home_lat)))
        
        return (
            self.home_lat - lat_delta,
            self.home_lat + lat_delta,
            self.home_lon - lon_delta,
            self.home_lon + lon_delta
        )
    
    def _calculate_distance(self, lat: float, lon: float) -> float:
        """Calculate approximate distance in km from home to given coordinates."""
        dx = (lon - self.home_lon) * 111.0 * math.cos(math.radians(self.home_lat))
        dy = (lat - self.home_lat) * 111.0
        return math.sqrt(dx*dx + dy*dy)
    
    def _calculate_bearing(self, lat: float, lon: float) -> str:
        """
        Calculate compass bearing from home to aircraft position.
        Returns direction string: N, NE, E, SE, S, SW, W, NW
        """
        d_lon = math.radians(lon - self.home_lon)
        lat1 = math.radians(self.home_lat)
        lat2 = math.radians(lat)
        
        x = math.sin(d_lon) * math.cos(lat2)
        y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
        
        bearing = math.degrees(math.atan2(x, y))
        bearing = (bearing + 360) % 360
        
        # Convert to compass direction
        directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
        index = round(bearing / 45) % 8
        return directions[index]
    
    def _infer_aircraft_type(self, callsign: str, altitude_ft: Optional[int], 
                             speed_knots: Optional[int]) -> str:
        """
        Infer aircraft type from callsign and flight characteristics.
        Used as fallback when aircraft database doesn't have the aircraft.
        
        Returns: 'MIL', 'MIL_HELO', 'JET', 'HELO', 'GA', or 'UNK'
        """
        callsign_upper = callsign.upper()
        
        # Check for military callsigns
        is_military = False
        for mil_prefix in self.MILITARY_CALLSIGNS:
            if callsign_upper.startswith(mil_prefix):
                is_military = True
                break
        
        # Check for helicopter/medevac callsigns
        is_helo = False
        for helo_prefix in self.HELO_CALLSIGNS:
            if helo_prefix in callsign_upper:
                is_helo = True
                break
        
        # Military helicopter (military + helicopter indicators)
        if is_military and is_helo:
            return 'MIL_HELO'
        
        # Military helicopter can also be inferred from low/slow military flights
        if is_military:
            if altitude_ft and speed_knots:
                if altitude_ft < 5000 and speed_knots < 180:
                    return 'MIL_HELO'
            return 'MIL'
        
        # Civilian helicopter
        if is_helo:
            return 'HELO'
        
        # Check for airline prefixes (commercial jets)
        prefix = callsign_upper[:3]
        if prefix in self.AIRLINE_PREFIXES:
            return 'JET'
        
        # Check for N-number (general aviation)
        if callsign_upper.startswith('N') and len(callsign) >= 4:
            # Verify it looks like an N-number (N followed by digits/letters)
            rest = callsign_upper[1:]
            if rest[0].isdigit():
                return 'GA'
        
        # Infer from altitude and speed
        if altitude_ft and speed_knots:
            # High altitude = likely jet
            if altitude_ft > 25000:
                return 'JET'
            # Low and slow = likely helicopter or GA
            elif altitude_ft < 3000 and speed_knots < 120:
                return 'HELO'
            elif altitude_ft < 10000 and speed_knots < 200:
                return 'GA'
        
        return 'UNK'
    
    def _enrich_flight_data(self, flight: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich flight data with aircraft database information.
        Adds make/model, registration verification, and refined aircraft type.
        """
        icao24 = flight.get('icao24', '')
        
        # Default enrichment fields
        flight['aircraft_info'] = None
        flight['display_type'] = None  # e.g., "Boeing 737-824"
        flight['short_type'] = None    # e.g., "B738"
        
        # Try aircraft database lookup
        if self.aircraft_db and self.aircraft_db.is_ready():
            info = self.aircraft_db.lookup(icao24)
            
            if info:
                flight['aircraft_info'] = info
                
                # Build display type string
                manufacturer = info.get('manufacturer', '')
                model = info.get('model', '')
                typecode = info.get('typecode', '')
                
                if manufacturer and model:
                    # Shorten manufacturer names for display
                    short_mfr = self._shorten_manufacturer(manufacturer)
                    flight['display_type'] = f"{short_mfr} {model}"
                elif manufacturer and typecode:
                    short_mfr = self._shorten_manufacturer(manufacturer)
                    flight['display_type'] = f"{short_mfr} {typecode}"
                elif model:
                    flight['display_type'] = model
                elif typecode:
                    flight['display_type'] = typecode
                
                flight['short_type'] = typecode
                
                # Refine aircraft type based on database info
                if typecode:
                    flight['aircraft_type'] = self._type_from_typecode(typecode)
                
                self.logger.debug(f"Aircraft DB hit: {icao24} -> {flight['display_type']}")
            else:
                self.logger.debug(f"Aircraft DB miss: {icao24}")
        
        # Ensure aircraft_type is set (fallback to inference)
        if not flight.get('aircraft_type'):
            flight['aircraft_type'] = self._infer_aircraft_type(
                flight.get('callsign', ''),
                flight.get('altitude_ft'),
                flight.get('speed_knots')
            )
        
        return flight
    
    def _shorten_manufacturer(self, manufacturer: str) -> str:
        """Shorten manufacturer names for LED display."""
        shortcuts = {
            'BOEING': 'Boeing',
            'AIRBUS': 'Airbus',
            'CESSNA': 'Cessna',
            'PIPER': 'Piper',
            'BEECHCRAFT': 'Beech',
            'BOMBARDIER': 'Bombardier',
            'EMBRAER': 'Embraer',
            'GULFSTREAM': 'Gulfstream',
            'LEARJET': 'Learjet',
            'CIRRUS': 'Cirrus',
            'ROBINSON': 'Robinson',
            'BELL': 'Bell',
            'SIKORSKY': 'Sikorsky',
            'EUROCOPTER': 'Eurocopter',
            'TEXTRON': 'Textron',
            'RAYTHEON': 'Raytheon',
            'DE HAVILLAND': 'DHC',
            'DE HAVILLAND CANADA': 'DHC',
            'LOCKHEED': 'Lockheed',
            'MCDONNELL DOUGLAS': 'MD',
        }
        
        upper = manufacturer.upper()
        for long_name, short_name in shortcuts.items():
            if long_name in upper:
                return short_name
        
        # Return original if no shortcut found (capitalize properly)
        return manufacturer.title()[:12]  # Max 12 chars
    
    def _type_from_typecode(self, typecode: str) -> str:
        """Determine aircraft type from ICAO typecode."""
        if not typecode:
            return 'UNK'
        
        tc = typecode.upper()
        
        # Military helicopters (common military helo typecodes)
        if tc.startswith(('UH60', 'AH64', 'CH47', 'CH53', 'MH60', 'HH60', 'V22', 'UH1')):
            return 'MIL_HELO'
        
        # Civilian helicopters
        if tc.startswith(('R22', 'R44', 'R66', 'EC', 'AS', 'B06', 'B47', 'B04', 
                         'S76', 'S92', 'A10', 'B20', 'B41', 'BK', 'MD5', 'H1')):
            return 'HELO'
        
        # Military jets (common military typecodes)
        if tc.startswith(('F15', 'F16', 'F18', 'F22', 'F35', 'A10', 'B1', 'B2', 'B52',
                         'C17', 'C5', 'C130', 'KC', 'E3', 'E8', 'P8', 'T38', 'T6')):
            return 'MIL'
        
        # Large commercial jets
        if tc.startswith(('A3', 'A2', 'B73', 'B74', 'B75', 'B76', 'B77', 'B78', 
                         'B78', 'E17', 'E19', 'E29', 'MD', 'DC')):
            return 'JET'
        
        # Business jets
        if tc.startswith(('CL', 'GL', 'LJ', 'C5', 'C6', 'C7', 'G2', 'G3', 'G4', 
                         'G5', 'G6', 'FA', 'H25', 'BE4', 'PC24')):
            return 'JET'
        
        # GA single/twin piston
        if tc.startswith(('C1', 'C2', 'PA', 'BE', 'SR', 'DA', 'M20', 'P28', 'C17', 'C18', 'C20', 'C21')):
            return 'GA'
        
        return 'UNK'
    
    def _is_in_cooldown(self, icao24: str) -> bool:
        """Check if flight is in cooldown period."""
        if icao24 in self.cooldown_flights:
            if time.time() < self.cooldown_flights[icao24]:
                return True
            else:
                # Cooldown expired, remove from tracking
                del self.cooldown_flights[icao24]
        return False
    
    def _add_to_cooldown(self, icao24: str) -> None:
        """Add flight to cooldown tracking."""
        self.cooldown_flights[icao24] = time.time() + self.cooldown_duration
    
    def _fetch_flights(self) -> None:
        """Fetch flight data from OpenSky Network API."""
        current_time = time.time()
        
        # Check if enabled
        if not self.enabled:
            self.live_flights = []
            self.current_flight = None
            return
        
        # Check polling window
        if not self._is_within_polling_window():
            if current_time - self.last_log_time > self.log_interval:
                self.logger.debug("Outside polling window, no flight tracking")
                self.last_log_time = current_time
            self.live_flights = []
            self.current_flight = None
            return
        
        # Check error backoff
        if self.consecutive_errors >= self.max_consecutive_errors:
            if current_time - self.last_error_time < self.error_backoff_time:
                return
            else:
                self.consecutive_errors = 0
                self.error_backoff_time = 60
        
        # Check update interval
        if current_time - self.last_update < self.update_interval:
            return
        
        # Refresh OAuth token
        if not self._refresh_oauth_token():
            self.consecutive_errors += 1
            self.last_error_time = current_time
            return
        
        try:
            # Calculate bounding box
            lat_min, lat_max, lon_min, lon_max = self._calculate_bounding_box()
            
            # Build API URL
            url = (f"https://opensky-network.org/api/states/all?"
                   f"lamin={lat_min:.6f}&lomin={lon_min:.6f}&"
                   f"lamax={lat_max:.6f}&lomax={lon_max:.6f}")
            
            headers = {'Authorization': f'Bearer {self.access_token}'}
            
            response = requests.get(url, headers=headers, timeout=15)
            increment_api_counter('opensky')
            
            if response.status_code == 200:
                data = response.json()
                
                # Process flight states
                new_live_flights = []
                states = data.get('states', [])
                
                if states:
                    for state in states:
                        # OpenSky state vector format
                        icao24 = state[0]
                        callsign = state[1]
                        if callsign:
                            callsign = callsign.strip()
                        
                        lon = state[5]
                        lat = state[6]
                        altitude_m = state[7]
                        on_ground = state[8]
                        velocity = state[9]  # m/s
                        heading = state[10]  # degrees
                        vertical_rate = state[11]  # m/s
                        
                        # Skip if missing critical data
                        if not callsign or lon is None or lat is None:
                            continue
                        
                        # Skip ground traffic
                        if on_ground or (altitude_m and altitude_m < self.min_altitude_m):
                            continue
                        
                        # Skip if in cooldown
                        if self._is_in_cooldown(icao24):
                            continue
                        
                        # Calculate distance
                        distance_km = self._calculate_distance(lat, lon)
                        
                        # Calculate bearing from home
                        bearing = self._calculate_bearing(lat, lon)
                        
                        # Convert altitude to feet
                        altitude_ft = int(altitude_m * 3.28084) if altitude_m else None
                        
                        # Convert velocity to knots
                        speed_knots = int(velocity * 1.94384) if velocity else None
                        
                        # Build flight record
                        flight = {
                            'icao24': icao24,
                            'callsign': callsign,
                            'altitude_ft': altitude_ft,
                            'altitude_m': altitude_m,
                            'distance_km': distance_km,
                            'bearing': bearing,
                            'speed_knots': speed_knots,
                            'heading': heading,
                            'vertical_rate': vertical_rate,
                            'lat': lat,
                            'lon': lon,
                            'timestamp': current_time
                        }
                        
                        # Enrich with aircraft database info
                        flight = self._enrich_flight_data(flight)
                        
                        new_live_flights.append(flight)
                        
                        if len(new_live_flights) >= self.max_flights:
                            break
                    
                    # Sort by distance (closest first)
                    new_live_flights.sort(key=lambda f: f['distance_km'])
                
                # Track if this is a new interruption session
                previously_had_flights = len(self.live_flights) > 0
                now_has_flights = len(new_live_flights) > 0
                
                # Handle interruption timing
                if now_has_flights and not previously_had_flights:
                    # Starting new interruption session
                    self.interruption_start_time = current_time
                    self.logger.info(f"Flight interruption started with {len(new_live_flights)} flights")
                elif not now_has_flights and previously_had_flights:
                    # Ending interruption session - add flights to cooldown
                    for flight in self.live_flights:
                        self._add_to_cooldown(flight['icao24'])
                    self.interruption_start_time = None
                    self.logger.info("Flight interruption ended")
                
                # Update live flights list
                self.live_flights = new_live_flights
                
                # Set current flight if we have any
                if self.live_flights:
                    if not self.current_flight or not any(
                        f['icao24'] == self.current_flight.get('icao24') 
                        for f in self.live_flights
                    ):
                        self.current_flight_index = 0
                        self.current_flight = self.live_flights[0]
                        self.last_flight_switch = current_time
                else:
                    self.current_flight = None
                
                self.last_update = current_time
                self.consecutive_errors = 0
                
                # Log status occasionally
                if current_time - self.last_log_time > self.log_interval:
                    if self.live_flights:
                        self.logger.info(f"LIVE FLIGHTS: {len(self.live_flights)} overhead")
                        for flight in self.live_flights[:3]:
                            type_info = flight.get('display_type') or flight.get('aircraft_type', 'UNK')
                            self.logger.info(
                                f"  {flight['callsign']} [{type_info}]: {flight['bearing']} {flight['distance_km']:.1f}km, "
                                f"@{flight['altitude_ft']}ft"
                            )
                    else:
                        self.logger.info("No flights currently in range")
                    self.last_log_time = current_time
                
            elif response.status_code == 401:
                self.logger.error("OpenSky authentication failed")
                self.access_token = None
                self.consecutive_errors += 1
                self.last_error_time = current_time
                
            else:
                self.logger.error(f"OpenSky API error: {response.status_code}")
                self.consecutive_errors += 1
                self.last_error_time = current_time
                
        except Exception as e:
            self.logger.error(f"Error fetching flights: {e}")
            self.consecutive_errors += 1
            self.last_error_time = current_time
    
    def update(self) -> None:
        """Update flight data (called periodically by main loop)."""
        self._fetch_flights()
        
        # Check max interruption timeout
        if self.interruption_start_time:
            elapsed = time.time() - self.interruption_start_time
            if elapsed >= self.max_interruption_time:
                self.logger.info(f"Max interruption time ({self.max_interruption_time}s) reached, adding flights to cooldown")
                for flight in self.live_flights:
                    self._add_to_cooldown(flight['icao24'])
                self.live_flights = []
                self.current_flight = None
                self.interruption_start_time = None
                return
        
        # Rotate through flights if we have multiple
        current_time = time.time()
        if len(self.live_flights) > 1 and current_time - self.last_flight_switch >= self.flight_display_duration:
            self.current_flight_index = (self.current_flight_index + 1) % len(self.live_flights)
            self.current_flight = self.live_flights[self.current_flight_index]
            self.last_flight_switch = current_time
            self.logger.debug(f"Switched to flight {self.current_flight_index + 1}/{len(self.live_flights)}: {self.current_flight.get('callsign')}")
    
    def display(self) -> None:
        """Display current flight (LIVE PATTERN)."""
        if not self.current_flight:
            return
        
        try:
            flight_image = self._create_flight_display(self.current_flight)
            
            self.display_manager.image = flight_image
            self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
            self.display_manager.update_display()
            
        except Exception as e:
            self.logger.error(f"Error displaying flight: {e}", exc_info=True)
    
    def has_live_content(self) -> bool:
        """Check if there are live flights (LIVE PATTERN)."""
        return len(self.live_flights) > 0
    
    def _create_flight_display(self, flight: Dict[str, Any]) -> Image.Image:
        """
        Create a PIL image displaying a single flight.
        
        Layout:
        ┌────────────────────────────────────┐
        │ [⭐][icon] CALLSIGN  [type]        │  (star shown for military)
        │        Boeing 737-824              │
        │      NE 3.1km  35000ft             │
        └────────────────────────────────────┘
        """
        img = Image.new('RGB', (self.display_width, self.display_height), color=(0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Load fonts
        try:
            font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 12)
            font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 10)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 9)
        except:
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()
            font_small = ImageFont.load_default()
        
        callsign = flight['callsign']
        distance_km = flight['distance_km']
        altitude_ft = flight['altitude_ft']
        bearing = flight.get('bearing', '')
        aircraft_type = flight.get('aircraft_type', 'UNK')
        display_type = flight.get('display_type')  # e.g., "Boeing 737-824"
        
        # Check if this is a military aircraft (MIL or MIL_HELO)
        is_military = aircraft_type in ('MIL', 'MIL_HELO')
        
        y_offset = 0
        
        # Line 1: [Star] + Icon + Callsign + Type code
        x_pos = 2
        
        # Draw star icon first if military
        if is_military and 'STAR' in self.aircraft_icons:
            star_icon = self.aircraft_icons['STAR']
            img.paste(star_icon, (x_pos, y_offset), star_icon if star_icon.mode == 'RGBA' else None)
            x_pos += 14  # Star is 16px but we can overlap a bit
        
        # Draw aircraft icon if available
        # For military helo, use MIL_HELO icon; for regular military, use MIL icon
        icon = self.aircraft_icons.get(aircraft_type)
        if icon:
            # Paste icon (need to handle RGBA)
            img.paste(icon, (x_pos, y_offset), icon if icon.mode == 'RGBA' else None)
            x_pos += 18
        
        # Draw callsign
        draw.text((x_pos, y_offset), callsign, fill=self.COLORS['orange'], font=font_large)
        
        # Draw type code on right side
        # Simplify display label for military types
        type_label = 'MIL' if aircraft_type == 'MIL_HELO' else aircraft_type
        type_badge = f"[{type_label}]"
        type_bbox = draw.textbbox((0, 0), type_badge, font=font_small)
        type_width = type_bbox[2] - type_bbox[0]
        type_color = {
            'JET': self.COLORS['cyan'],
            'MIL': self.COLORS['gold'],
            'MIL_HELO': self.COLORS['gold'],
            'HELO': self.COLORS['skyblue'],
            'GA': self.COLORS['green'],
        }.get(aircraft_type, self.COLORS['gray'])
        draw.text((self.display_width - type_width - 2, y_offset + 2), type_badge, fill=type_color, font=font_small)
        
        # Line 2: Aircraft make/model (if available)
        y_offset = 12
        if display_type:
            # Truncate if too long
            if len(display_type) > 22:
                display_type = display_type[:21] + "…"
            
            bbox = draw.textbbox((0, 0), display_type, font=font_small)
            text_width = bbox[2] - bbox[0]
            x_centered = (self.display_width - text_width) // 2
            draw.text((x_centered, y_offset), display_type, fill=self.COLORS['white'], font=font_small)
            y_offset = 21
        else:
            y_offset = 13
        
        # Line 3: Bearing, distance, altitude
        dist_text = f"{bearing} {distance_km:.1f}km" if bearing else f"{distance_km:.1f}km"
        alt_text = f"{altitude_ft}ft" if altitude_ft else "?ft"
        info_line = f"{dist_text}  {alt_text}"
        
        bbox = draw.textbbox((0, 0), info_line, font=font_medium)
        info_width = bbox[2] - bbox[0]
        info_x = (self.display_width - info_width) // 2
        draw.text((info_x, y_offset), info_line, fill=self.COLORS['white'], font=font_medium)
        
        # Flight counter if multiple flights
        if len(self.live_flights) > 1:
            count_text = f"{self.current_flight_index + 1}/{len(self.live_flights)}"
            draw.text((self.display_width - 20, self.display_height - 9), count_text, 
                     fill=self.COLORS['gray'], font=font_small)
        
        return img
    
    def get_status(self) -> Dict[str, Any]:
        """Get current status for web interface."""
        # Build flight details with aircraft info
        flight_details = []
        for f in self.live_flights[:5]:
            detail = {
                'callsign': f['callsign'],
                'icao24': f['icao24'],
                'distance_km': f['distance_km'],
                'altitude_ft': f['altitude_ft'],
                'bearing': f.get('bearing'),
                'aircraft_type': f.get('aircraft_type'),
                'display_type': f.get('display_type'),
            }
            if f.get('aircraft_info'):
                detail['registration'] = f['aircraft_info'].get('registration')
                detail['operator'] = f['aircraft_info'].get('operator')
            flight_details.append(detail)
        
        # Aircraft DB stats
        db_stats = None
        if self.aircraft_db and self.aircraft_db.is_ready():
            db_stats = self.aircraft_db.get_stats()
        
        return {
            'enabled': self.enabled,
            'mode': 'live_interruption',
            'home_location': {
                'latitude': self.home_lat,
                'longitude': self.home_lon
            },
            'radius_km': self.radius_km,
            'radius_miles': round(self.radius_km * 0.621371, 1),
            'polling_window': {
                'start_hour': self.start_hour,
                'end_hour': self.end_hour,
                'currently_active': self._is_within_polling_window()
            },
            'live_flights': len(self.live_flights),
            'flight_details': flight_details,
            'current_flight': self.current_flight.get('callsign') if self.current_flight else None,
            'has_live_content': self.has_live_content(),
            'last_update': datetime.fromtimestamp(self.last_update).isoformat() if self.last_update else None,
            'auth_status': 'valid' if self.access_token and time.time() < self.token_expiry else 'needs_refresh',
            'consecutive_errors': self.consecutive_errors,
            'update_interval': self.update_interval,
            'display_duration_per_flight': self.flight_display_duration,
            'max_interruption_time': self.max_interruption_time,
            'cooldown_duration': self.cooldown_duration,
            'interruption_elapsed': int(time.time() - self.interruption_start_time) if self.interruption_start_time else None,
            'flights_in_cooldown': len(self.cooldown_flights),
            'aircraft_database': db_stats,
        }
