import requests
import time
import math
import threading
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

logger = logging.getLogger(__name__)

class FlightLiveManager:
    """
    LIVE Flight Tracker - Interrupts display when flights are overhead.
    
    Works like Chuck's live sports managers - when flights enter the configured
    radius, this manager takes priority and displays "what's overhead RIGHT NOW."
    
    Based on Bailey's ESP32 implementation, adapted for Raspberry Pi with live
    game interruption pattern.
    
    BACKGROUND THREADING: All API polling happens in a background thread to
    prevent display stuttering. The main loop only reads cached data.
    """
    
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
        self.update_interval = self.flight_config.get('update_interval', 10)  # Update every 10 seconds (faster for live)
        self.last_flight_switch = 0  # When we last switched displayed flight
        self.flight_display_duration = self.flight_config.get('flight_display_duration', 5)  # Show each flight for 5 seconds
        self.last_display_update = 0
        
        # Logging control
        self.last_log_time = 0
        self.log_interval = 30  # Log status every 30 seconds
        
        # Error handling
        self.consecutive_errors = 0
        self.last_error_time = 0
        self.error_backoff_time = 60
        self.max_consecutive_errors = 5
        
        # BACKGROUND THREADING - Thread-safe access to flight data
        self._lock = threading.Lock()
        self._poll_thread = None
        self._stop_event = threading.Event()
        self._polling_active = False

        # === MAX DISPLAY TIME FEATURE ===
        # Prevents flights from dominating display for minutes at a time
        self._interruption_start_time = None  # When current interruption session started
        self._max_interruption_time = self.flight_config.get('max_interruption_time', 30)  # Default 30 seconds
        self._cooldown_flights = {}  # {icao24: last_displayed_time} - prevent re-interrupt
        self._cooldown_duration = self.flight_config.get('cooldown_duration', 120)  # 2 min before same flight can re-interrupt
        self._interruption_active = False  # Are we currently in an interruption session?
        
        # Colors for display
        self.COLORS = {
            'white': (255, 255, 255),
            'gray': (128, 128, 128),
            'green': (0, 255, 0),
            'yellow': (255, 255, 0),
            'red': (255, 0, 0),
            'cyan': (0, 255, 255),
            'orange': (255, 165, 0)
        }
        
        # Display dimensions
        
        # Aircraft type icons
        self.aircraft_icons = {}
        self._load_aircraft_icons()

        self.display_width = self.display_manager.matrix.width if hasattr(self.display_manager, 'matrix') else 128
        self.display_height = self.display_manager.matrix.height if hasattr(self.display_manager, 'matrix') else 32
        

    @property
    def live_games(self):
        """Alias for live_flights to match sports manager pattern.
        Returns empty list if interruption timed out (so display controller releases priority)."""
        if self._interruption_active:
            return self.live_flights
        return []
        self.logger.info(f"FlightLiveManager initialized: lat={self.home_lat}, lon={self.home_lon}, radius={self.radius_km}km (~{self.radius_km * 0.621371:.1f} miles)")
        self.logger.info(f"Live interruption mode: Will display when flights enter {self.radius_km}km radius")
        self.logger.info(f"Update interval: {self.update_interval}s, Display duration per flight: {self.flight_display_duration}s")
        self.logger.info("Background threading enabled - polling will not block main display loop")
    
    def start_background_polling(self) -> None:
        """
        Start the background polling thread.
        Should be called after initialization to begin flight tracking.
        """
        if not self.enabled:
            self.logger.info("Flight tracking disabled, not starting background polling")
            return
        
        if self._polling_active:
            self.logger.warning("Background polling already active")
            return
        
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._background_poll_loop,
            name="FlightPollThread",
            daemon=True  # Thread will exit when main program exits
        )
        self._poll_thread.start()
        self._polling_active = True
        self.logger.info("Background flight polling started")
    
    def stop_background_polling(self) -> None:
        """
        Stop the background polling thread.
        Should be called during cleanup/shutdown.
        """
        if not self._polling_active:
            return
        
        self.logger.info("Stopping background flight polling...")
        self._stop_event.set()
        
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5.0)
            if self._poll_thread.is_alive():
                self.logger.warning("Background polling thread did not stop cleanly")
        
        self._polling_active = False
        self.logger.info("Background flight polling stopped")
    
    def _background_poll_loop(self) -> None:
        """
        Background thread loop that handles all API polling.
        Updates self.live_flights in a thread-safe manner.
        """
        self.logger.info("Background poll loop started")
        
        while not self._stop_event.is_set():
            try:
                # Do the actual fetch (this is the blocking operation)
                self._fetch_flights_internal()
            except Exception as e:
                self.logger.error(f"Error in background poll loop: {e}", exc_info=True)
            
            # Sleep in small increments so we can respond to stop_event quickly
            sleep_time = self.update_interval
            while sleep_time > 0 and not self._stop_event.is_set():
                time.sleep(min(1.0, sleep_time))
                sleep_time -= 1.0
        
        self.logger.info("Background poll loop ended")
    
    def _is_within_polling_window(self) -> bool:
        """Check if current time is within configured polling window."""
        now = datetime.now()
        current_hour = now.hour
        
        # Handle windows that cross midnight
        if self.start_hour <= self.end_hour:
            return self.start_hour <= current_hour < self.end_hour
        else:
            return current_hour >= self.start_hour or current_hour < self.end_hour
    
    def _refresh_oauth_token(self) -> bool:
        """
        Refresh OAuth2 access token using client credentials flow.
        Returns True if successful, False otherwise.
        """
        if not self.client_id or not self.client_secret:
            logger.error("OpenSky OAuth2 credentials not configured")
            return False
        
        # Check if token is still valid (with 5 minute buffer)
        if self.access_token and time.time() < (self.token_expiry - 300):
            return True
        
        try:
            url = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
            
            data = {
                'grant_type': 'client_credentials',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
            }
            
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
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
        """
        Calculate bounding box for API query based on home location and radius.
        Returns (lat_min, lat_max, lon_min, lon_max).
        
        Uses ~111km per degree latitude approximation.
        Adjusts longitude based on latitude (degrees compress near poles).
        """
        # Approximate conversion: 1 degree latitude ≈ 111 km
        lat_delta = self.radius_km / 111.0
        
        # Longitude degrees vary with latitude: 1 degree ≈ 111 * cos(latitude) km
        lon_delta = self.radius_km / (111.0 * math.cos(math.radians(self.home_lat)))
        
        lat_min = self.home_lat - lat_delta
        lat_max = self.home_lat + lat_delta
        lon_min = self.home_lon - lon_delta
        lon_max = self.home_lon + lon_delta
        
        return (lat_min, lat_max, lon_min, lon_max)
    
    def _calculate_distance(self, lat: float, lon: float) -> float:
        """
        Calculate approximate distance in km from home to given coordinates.
        Uses simple Euclidean approximation (good enough for small distances).
        """
        dx = (lon - self.home_lon) * 111.0 * math.cos(math.radians(self.home_lat))
        dy = (lat - self.home_lat) * 111.0
        return math.sqrt(dx*dx + dy*dy)

    def _calculate_bearing(self, lat: float, lon: float) -> str:
        """
        Calculate compass direction from home to given coordinates.
        Returns cardinal/intercardinal direction (N, NE, E, SE, S, SW, W, NW).
        """
        # Calculate bearing angle
        dx = (lon - self.home_lon) * math.cos(math.radians(self.home_lat))
        dy = lat - self.home_lat
        
        # Get angle in degrees (0 = North, 90 = East, etc.)
        angle = math.degrees(math.atan2(dx, dy))
        if angle < 0:
            angle += 360
        
        # Convert to 8-point compass
        directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
        index = int((angle + 22.5) / 45) % 8
        return directions[index]
    def _load_aircraft_icons(self):
        """Load aircraft type icons from assets directory."""
        icon_dir = Path(__file__).parent.parent / 'assets' / 'logos' / 'aircraft'
        icon_files = {
            'JET': 'jet.png',
            'MIL': 'military.png',
            'HELO': 'helicopter.png',
            'GA': 'ga.png',
            'UNK': 'unknown.png'
        }
        
        for type_code, filename in icon_files.items():
            icon_path = icon_dir / filename
            if icon_path.exists():
                try:
                    icon = Image.open(icon_path).convert('RGBA')
                    # Resize to 16x16 if needed
                    if icon.size != (16, 16):
                        icon = icon.resize((16, 16), Image.LANCZOS)
                    self.aircraft_icons[type_code] = icon
                    self.logger.debug(f"Loaded aircraft icon: {type_code}")
                except Exception as e:
                    self.logger.warning(f"Failed to load aircraft icon {filename}: {e}")
        
        self.logger.info(f"Loaded {len(self.aircraft_icons)} aircraft icons")

    def _infer_aircraft_type(self, callsign: str, altitude_ft: int, speed_knots: int) -> str:
        """
        Infer aircraft type from callsign patterns and flight characteristics.
        Returns type_code: MIL, JET, HELO, GA, or UNK
        """
        callsign = (callsign or '').upper().strip()
        
        # Military patterns
        military_patterns = ['REACH', 'TETON', 'EVAC', 'RESCUE', 'ARMY', 'NAVY', 
                           'GUARD', 'DUKE', 'HAWK', 'VIPER', 'RCH', 'CNV', 'PAT',
                           'IRON', 'STEEL', 'BLADE', 'SABER', 'TOPCAT', 'BOXER',
                           'KARMA', 'RAID', 'SKULL', 'BONE', 'DEATH']
        for pattern in military_patterns:
            if callsign.startswith(pattern):
                return 'MIL'
        
        # Helicopter patterns
        heli_patterns = ['LIFE', 'MEDEVAC', 'HELI', 'COPTER', 'AIR1', 'MERCY']
        for pattern in heli_patterns:
            if pattern in callsign:
                return 'HELO'
        
        # Low & slow = likely helicopter
        if altitude_ft and speed_knots and altitude_ft < 3000 and speed_knots < 120:
            return 'HELO'
        
        # Commercial airlines
        airlines = ['AAL', 'UAL', 'DAL', 'SWA', 'JBU', 'ASA', 'FFT', 'NKS', 
                   'SKW', 'ENY', 'RPA', 'EDV', 'FDX', 'UPS', 'GTI', 'ABX',
                   'EJA', 'LXJ', 'XOJ', 'TVS', 'XAJ', 'LEA', 'WWI']
        for code in airlines:
            if callsign.startswith(code):
                return 'JET'
        
        # High altitude = jet
        if altitude_ft and altitude_ft > 25000:
            return 'JET'
        
        # N-numbers = general aviation
        if callsign.startswith('N') and len(callsign) <= 6:
            return 'GA'
        
        return 'UNK'


    
    def _fetch_flights_internal(self) -> None:
        """
        Internal method to fetch flight data from OpenSky Network API.
        Called by background thread. Updates self.live_flights thread-safely.
        """
        current_time = time.time()
        
        # Check if enabled
        if not self.enabled:
            with self._lock:
                self.live_flights = []
                self.current_flight = None
            return
        
        # Check polling window
        if not self._is_within_polling_window():
            # Only log occasionally
            if current_time - self.last_log_time > self.log_interval:
                self.logger.debug("Outside polling window, no flight tracking")
                self.last_log_time = current_time
            with self._lock:
                self.live_flights = []
                self.current_flight = None
            return
        
        # Check error backoff
        if self.consecutive_errors >= self.max_consecutive_errors:
            if current_time - self.last_error_time < self.error_backoff_time:
                return
            else:
                # Reset errors after backoff
                self.consecutive_errors = 0
                self.error_backoff_time = 60
        
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
            
            headers = {
                'Authorization': f'Bearer {self.access_token}'
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            increment_api_counter('opensky')
            
            if response.status_code == 200:
                data = response.json()
                
                # Process flight states into LIVE FLIGHTS
                new_live_flights = []
                states = data.get('states', [])
                
                if states:
                    for state in states:
                        # OpenSky state vector format:
                        # 0: icao24, 1: callsign, 2: origin_country, 3: time_position,
                        # 4: last_contact, 5: longitude, 6: latitude, 7: baro_altitude,
                        # 8: on_ground, 9: velocity, 10: true_track, 11: vertical_rate
                        
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
                        
                        # Calculate distance
                        distance_km = self._calculate_distance(lat, lon)
                        
                        # Convert altitude to feet (aviation standard)
                        altitude_ft = int(altitude_m * 3.28084) if altitude_m else None
                        
                        # Convert velocity to knots if available
                        speed_knots = int(velocity * 1.94384) if velocity else None
                        
                        new_live_flights.append({
                            'icao24': icao24,
                            'callsign': callsign,
                            'altitude_ft': altitude_ft,
                            'altitude_m': altitude_m,
                            'distance_km': distance_km,
                            'direction': self._calculate_bearing(lat, lon),
                            'aircraft_type': self._infer_aircraft_type(callsign, altitude_ft, speed_knots),
                            'speed_knots': speed_knots,
                            'heading': heading,
                            'vertical_rate': vertical_rate,
                            'lat': lat,
                            'lon': lon,
                            'timestamp': current_time
                        })
                        
                        if len(new_live_flights) >= self.max_flights:
                            break
                    
                    # Sort by distance (closest first) - most relevant for "overhead" awareness
                    new_live_flights.sort(key=lambda f: f['distance_km'])
                
                # THREAD-SAFE UPDATE of live flights list
                with self._lock:
                    self.live_flights = new_live_flights
                    
                    # Set current flight if we have any
                    if self.live_flights:
                        # If we don't have a current flight, or current flight is no longer in range
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
                    if new_live_flights:
                        self.logger.info(f"LIVE FLIGHTS: {len(new_live_flights)} overhead")
                        for flight in new_live_flights[:3]:  # Log first 3
                            self.logger.info(
                                f"  {flight['callsign']}: {flight['distance_km']:.1f}km, "
                                f"@{flight['altitude_ft']}ft, {flight.get('speed_knots', '?')}kts"
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
        """
        Update method called by main loop - NOW NON-BLOCKING.
        Only handles flight rotation, does NOT fetch from API.
        All API fetching is done in the background thread.
        """
        # Rotate through flights if we have multiple
        current_time = time.time()
        
        with self._lock:
            if len(self.live_flights) > 1 and current_time - self.last_flight_switch >= self.flight_display_duration:
                self.current_flight_index = (self.current_flight_index + 1) % len(self.live_flights)
                self.current_flight = self.live_flights[self.current_flight_index]
                self.last_flight_switch = current_time
                self.logger.debug(f"Switched to flight {self.current_flight_index + 1}/{len(self.live_flights)}: {self.current_flight.get('callsign')}")
    
    def display(self, force_clear: bool = False) -> None:
        """
        Display current flight (LIVE PATTERN).
        Called by main loop when this manager is active (flights overhead).
        """
        with self._lock:
            current = self.current_flight
        
        if not current:
            return
        
        try:
            # Create display image for current flight
            flight_image = self._create_flight_display(current)
            
            # Set the image in display manager
            self.display_manager.image = flight_image
            self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
            
            # Update the display
            self.display_manager.update_display()
            
        except Exception as e:
            self.logger.error(f"Error displaying flight: {e}", exc_info=True)
    
    def _clean_cooldown_flights(self) -> None:
        """Remove flights from cooldown that have expired."""
        current_time = time.time()
        expired = [icao for icao, timestamp in self._cooldown_flights.items() 
                   if current_time - timestamp > self._cooldown_duration]
        for icao in expired:
            del self._cooldown_flights[icao]

    def has_live_content(self) -> bool:
        """
        Check if there are live flights that should interrupt display (LIVE PATTERN).
        Main loop uses this to determine if this manager should interrupt.
        
        Implements max display time - returns False after interruption exceeds limit,
        even if flights are still in range. Prevents flights from dominating display.
        Thread-safe with _lock for flight data access.
        """
        with self._lock:
            flight_count = len(self.live_flights)
            current_flights = list(self.live_flights)  # Copy for use outside lock
        
        # No flights in range = no live content
        if flight_count == 0:
            # Reset interruption state when all flights leave
            if self._interruption_active:
                self.logger.info("All flights left range - ending interruption session")
                self._interruption_active = False
                self._interruption_start_time = None
            return False
        
        current_time = time.time()
        
        # Clean up expired cooldowns
        self._clean_cooldown_flights()
        
        # === CASE 1: Not currently interrupting ===
        if not self._interruption_active:
            # Check if any flight can trigger new interruption (not in cooldown)
            has_new = False
            for flight in current_flights:
                if flight['icao24'] not in self._cooldown_flights:
                    has_new = True
                    break
            
            if has_new:
                # Start new interruption session
                self._interruption_active = True
                self._interruption_start_time = current_time
                
                # Add all current flights to cooldown
                for flight in current_flights:
                    self._cooldown_flights[flight['icao24']] = current_time
                
                self.logger.info(f"Starting flight interruption session with {flight_count} flight(s), max {self._max_interruption_time}s")
                return True
            else:
                # All flights are in cooldown, don't interrupt
                return False
        
        # === CASE 2: Currently interrupting ===
        # Check if max display time exceeded
        elapsed = current_time - self._interruption_start_time
        if elapsed >= self._max_interruption_time:
            self.logger.info(f"Max interruption time ({self._max_interruption_time}s) reached - returning to normal rotation")
            self._interruption_active = False
            self._interruption_start_time = None
            
            # Refresh cooldown timestamps for all current flights
            for flight in current_flights:
                self._cooldown_flights[flight['icao24']] = current_time
            
            return False
        
        # Check if a NEW flight entered range (one not in cooldown)
        new_flight_entered = False
        for flight in current_flights:
            if flight['icao24'] not in self._cooldown_flights:
                # New flight! Add to cooldown and note it
                self._cooldown_flights[flight['icao24']] = current_time
                new_flight_entered = True
                self.logger.info(f"New flight entered range during interruption: {flight['callsign']}")
        
        # If new flight entered, reset the timer to give it display time
        if new_flight_entered:
            self._interruption_start_time = current_time
            self.logger.info("Resetting interruption timer for new flight")
        
        # Still within time limit, continue interruption
        return True

    
    def get_live_flights_count(self) -> int:
        """Thread-safe method to get current flight count."""
        with self._lock:
            return len(self.live_flights)
    
    def _create_flight_display(self, flight: Dict[str, Any]) -> Image.Image:
        """
        Create a PIL image displaying a single flight.
        Format: Large callsign, distance, altitude, speed, heading
        """
        # Create blank image
        img = Image.new('RGB', (self.display_width, self.display_height), color=(0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Try to load fonts
        try:
            font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
            font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 11)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 8)
        except:
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()
            font_small = ImageFont.load_default()
        
        callsign = flight['callsign']
        distance_km = flight['distance_km']
        altitude_ft = flight['altitude_ft']
        speed_knots = flight.get('speed_knots')
        heading = flight.get('heading')
        aircraft_type = flight.get('aircraft_type', 'UNK')
        direction = flight.get('direction', '')
        
        
        # Get aircraft icon
        icon = self.aircraft_icons.get(aircraft_type)
        icon_width = 16 if icon else 0
        icon_spacing = 2 if icon else 0
        
        # Line 1: Icon + callsign (centered top)
        bbox = draw.textbbox((0, 0), callsign, font=font_large)
        text_width = bbox[2] - bbox[0]
        total_width = icon_width + icon_spacing + text_width
        start_x = (self.display_width - total_width) // 2
        
        # Draw icon if available
        if icon:
            # Paste icon (handle transparency)
            img.paste(icon, (start_x, 1), icon if icon.mode == 'RGBA' else None)
        
        # Draw callsign text after icon
        text_x = start_x + icon_width + icon_spacing
        draw.text((text_x, 1), callsign, fill=self.COLORS['orange'], font=font_large)
        
        # Line 2: Distance and altitude
        direction = flight.get('direction', '')
        
        dist_text = f"{direction} {distance_km:.1f}km"
        alt_text = f"{altitude_ft}ft" if altitude_ft else "?ft"
        line2 = f"{dist_text}  {alt_text}"
        bbox = draw.textbbox((0, 0), line2, font=font_medium)
        line2_width = bbox[2] - bbox[0]
        line2_x = (self.display_width - line2_width) // 2
        draw.text((line2_x, 19), line2, fill=self.COLORS['white'], font=font_medium)
        
        # Optional: Show count if multiple flights (thread-safe read)
        with self._lock:
            flight_count = len(self.live_flights)
            current_idx = self.current_flight_index
        
        if flight_count > 1:
            count_text = f"{current_idx + 1}/{flight_count}"
            draw.text((2, 1), count_text, fill=self.COLORS['gray'], font=font_small)
        
        return img
    
    def get_status(self) -> Dict[str, Any]:
        """Get current status for web interface."""
        with self._lock:
            live_count = len(self.live_flights)
            current_callsign = self.current_flight.get('callsign') if self.current_flight else None
        
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
            'live_flights': live_count,
            'current_flight': current_callsign,
            'has_live_content': live_count > 0,
            'last_update': datetime.fromtimestamp(self.last_update).isoformat() if self.last_update else None,
            'auth_status': 'valid' if self.access_token and time.time() < self.token_expiry else 'needs_refresh',
            'consecutive_errors': self.consecutive_errors,
            'update_interval': self.update_interval,
            'display_duration_per_flight': self.flight_display_duration,
            'background_polling': self._polling_active
        }
