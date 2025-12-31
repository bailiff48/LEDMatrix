"""
Aircraft Lookup Utilities - Shared module for flight data enrichment.

This module provides standalone functions for:
- Aircraft make/model lookups (aircraft database + hexdb.io fallback)
- Aircraft type classification (FAA database + heuristics)
- Compass bearing calculations

PLUGIN ARCHITECTURE:
This module is designed to be imported independently by:
- flight_manager.py (display controller)
- flight_config_api.py (web API / What's Overhead plugin)
- Any future flight-related plugins

No dependencies on display manager, cache manager, or Flask.
"""

import requests
import time
import math
import json
import os
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ============================================
# Lazy-loaded database singletons
# ============================================
_aircraft_db = None
_aircraft_db_initialized = False

_faa_db = None
_faa_db_initialized = False

# Fallback cache for hexdb.io lookups
_fallback_cache = {}
_fallback_cache_loaded = False
_fallback_cache_path = "/home/ledpi/LEDMatrix/data/aircraft_fallback_cache.json"
_last_fallback_request = 0
_fallback_rate_limit = 1.0  # Minimum seconds between API calls


def _get_aircraft_db():
    """
    Lazy-load aircraft database singleton.
    Returns AircraftDatabase instance or None if unavailable.
    """
    global _aircraft_db, _aircraft_db_initialized
    
    if _aircraft_db_initialized:
        return _aircraft_db
    
    _aircraft_db_initialized = True
    
    try:
        from src.aircraft_db import AircraftDatabase
    except ImportError:
        logger.debug("aircraft_db module not available")
        return None
    
    db_paths = [
        "/home/ledpi/LEDMatrix/data/aircraft.db",
        os.path.expanduser("~/LEDMatrix/data/aircraft.db"),
    ]
    
    for db_path in db_paths:
        if os.path.exists(db_path):
            try:
                _aircraft_db = AircraftDatabase(db_path)
                if _aircraft_db.is_ready():
                    stats = _aircraft_db.get_stats()
                    logger.info(f"Aircraft database loaded: {stats.get('total_aircraft', 0):,} aircraft")
                    return _aircraft_db
            except Exception as e:
                logger.warning(f"Failed to load aircraft DB from {db_path}: {e}")
    
    logger.debug("Aircraft database not found")
    return None


def _get_faa_db():
    """
    Lazy-load FAA database singleton.
    Returns FAA database instance or None if unavailable.
    """
    global _faa_db, _faa_db_initialized
    
    if _faa_db_initialized:
        return _faa_db
    
    _faa_db_initialized = True
    
    try:
        from src.faa_database import get_faa_database
    except ImportError:
        logger.debug("faa_database module not available")
        return None
    
    try:
        _faa_db = get_faa_database()
        if _faa_db and _faa_db.is_ready():
            stats = _faa_db.get_stats()
            logger.info(f"FAA database ready: {stats.get('aircraft_count', 0):,} aircraft")
            return _faa_db
    except Exception as e:
        logger.warning(f"Failed to initialize FAA database: {e}")
    
    return None


def _load_fallback_cache() -> None:
    """Load the hexdb.io fallback cache from disk."""
    global _fallback_cache, _fallback_cache_loaded
    
    if _fallback_cache_loaded:
        return
    
    _fallback_cache_loaded = True
    
    try:
        if os.path.exists(_fallback_cache_path):
            with open(_fallback_cache_path, 'r') as f:
                _fallback_cache = json.load(f)
            logger.debug(f"Loaded {len(_fallback_cache)} entries from fallback cache")
    except Exception as e:
        logger.debug(f"Fallback cache not available: {e}")
        _fallback_cache = {}


def _save_fallback_cache() -> None:
    """Save the hexdb.io fallback cache to disk."""
    try:
        os.makedirs(os.path.dirname(_fallback_cache_path), exist_ok=True)
        with open(_fallback_cache_path, 'w') as f:
            json.dump(_fallback_cache, f)
    except Exception as e:
        logger.debug(f"Failed to save fallback cache: {e}")


def _shorten_manufacturer(manufacturer: str) -> str:
    """Shorten common manufacturer names for display."""
    if not manufacturer:
        return ''
    
    upper = manufacturer.upper()
    
    if 'BOEING' in upper:
        return 'Boeing'
    elif 'AIRBUS' in upper:
        return 'Airbus'
    elif 'CESSNA' in upper:
        return 'Cessna'
    elif 'PIPER' in upper:
        return 'Piper'
    elif 'EMBRAER' in upper:
        return 'Embraer'
    elif 'BOMBARDIER' in upper:
        return 'Bombardier'
    elif 'GULFSTREAM' in upper:
        return 'Gulfstream'
    elif 'BEECHCRAFT' in upper or 'BEECH' in upper:
        return 'Beechcraft'
    elif 'CIRRUS' in upper:
        return 'Cirrus'
    elif 'MOONEY' in upper:
        return 'Mooney'
    elif 'DIAMOND' in upper:
        return 'Diamond'
    else:
        return manufacturer.title()[:12]


def _fallback_lookup(icao24: str) -> Dict:
    """
    Fallback lookup using hexdb.io API when local DB misses.
    Results are cached persistently since icao24 = same physical aircraft.
    
    Args:
        icao24: ICAO 24-bit aircraft address (hex string)
    
    Returns:
        Dict with display_type, typecode, registration, operator, source
        Empty dict if not found
    """
    global _last_fallback_request, _fallback_cache
    
    # Ensure cache is loaded
    _load_fallback_cache()
    
    # Check cache first
    if icao24 in _fallback_cache:
        cached = _fallback_cache[icao24]
        if cached:
            return cached
        return {}
    
    # Rate limit - be nice to free API
    current_time = time.time()
    if current_time - _last_fallback_request < _fallback_rate_limit:
        return {}
    
    try:
        _last_fallback_request = current_time
        url = f"https://hexdb.io/api/v1/aircraft/{icao24}"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            
            # Check for "not found" response
            if data.get('status') == '404' or data.get('error'):
                _fallback_cache[icao24] = None
                _save_fallback_cache()
                return {}
            
            # Build result
            result = {}
            manufacturer = data.get('Manufacturer', '')
            model = data.get('Type', '')
            typecode = data.get('ICAOTypeCode', '')
            
            if manufacturer and model:
                short_mfr = _shorten_manufacturer(manufacturer)
                result['display_type'] = f"{short_mfr} {model}"
            elif model:
                result['display_type'] = model
            elif typecode:
                result['display_type'] = typecode
            
            result['typecode'] = typecode
            result['registration'] = data.get('Registration')
            result['operator'] = data.get('RegisteredOwners')
            result['source'] = 'hexdb.io'
            
            # Cache the result
            _fallback_cache[icao24] = result
            _save_fallback_cache()
            
            logger.debug(f"Fallback lookup success: {icao24} -> {result.get('display_type', 'unknown')}")
            return result
            
        elif response.status_code == 404:
            _fallback_cache[icao24] = None
            _save_fallback_cache()
            return {}
        else:
            return {}
            
    except Exception as e:
        logger.debug(f"Fallback lookup error for {icao24}: {e}")
        return {}


# ============================================
# Public API - Import these functions
# ============================================

def lookup_aircraft_info(icao24: str) -> Dict:
    """
    Look up aircraft make/model from database, with hexdb.io fallback.
    
    Args:
        icao24: ICAO 24-bit aircraft address (hex string, e.g., 'a12345')
    
    Returns:
        Dict with keys:
        - display_type: Human-readable aircraft type (e.g., "Boeing 737-824")
        - typecode: ICAO type code (e.g., "B738")
        - registration: Aircraft registration (e.g., "N12345")
        - operator: Operator/owner name
        - source: Data source ('local' or 'hexdb.io')
        
        Empty dict if aircraft not found in any database.
    """
    info = None
    
    # Try local database first
    aircraft_db = _get_aircraft_db()
    if aircraft_db:
        info = aircraft_db.lookup(icao24)
    
    # If not found, try fallback API
    if not info:
        return _fallback_lookup(icao24)
    
    # Build result from local database
    result = {}
    manufacturer = info.get('manufacturer', '')
    model = info.get('model', '')
    typecode = info.get('typecode', '')
    
    if manufacturer and model:
        short_mfr = _shorten_manufacturer(manufacturer)
        result['display_type'] = f"{short_mfr} {model}"
    elif model:
        result['display_type'] = model
    elif typecode:
        result['display_type'] = typecode
    
    result['typecode'] = typecode
    result['registration'] = info.get('registration')
    result['operator'] = info.get('operator')
    result['source'] = 'local'
    
    return result


def infer_aircraft_type(callsign: str, altitude_ft: Optional[int] = None, 
                        speed_knots: Optional[int] = None, icao24: Optional[str] = None) -> str:
    """
    Determine aircraft type using FAA database (primary) with heuristic fallback.
    
    Args:
        callsign: Aircraft callsign (e.g., 'UAL123', 'N12345')
        altitude_ft: Altitude in feet (optional, for heuristics)
        speed_knots: Ground speed in knots (optional, for heuristics)
        icao24: ICAO 24-bit address for FAA lookup (optional)
    
    Returns:
        Type code string:
        - 'JET': Commercial jet
        - 'HELO': Helicopter
        - 'GA': General aviation (single engine)
        - 'TWIN': Twin engine prop
        - 'MIL': Military fixed-wing
        - 'MIL_HELO': Military helicopter
        - 'GLIDER': Glider/sailplane
        - 'BALLOON': Balloon
        - 'CHUTE': Parachute/ultralight
        - 'UPS', 'FDX', 'AMAZON', 'DHL': Specific cargo carriers
        - 'CARGO': Generic cargo
        - 'UNK': Unknown
    """
    callsign = (callsign or '').upper().strip()
    
    # === STEP 1: Check military patterns first (FAA DB doesn't track military) ===
    military_patterns = ['REACH', 'TETON', 'EVAC', 'RESCUE', 'ARMY', 'NAVY', 
                        'GUARD', 'DUKE', 'HAWK', 'VIPER', 'RCH', 'CNV', 'PAT',
                        'IRON', 'STEEL', 'BLADE', 'SABER', 'TOPCAT', 'BOXER',
                        'KARMA', 'RAID', 'SKULL', 'BONE', 'DEATH', 'DUSTOFF']
    
    heli_patterns = ['LIFE', 'MEDEVAC', 'HELI', 'COPTER', 'AIR1', 'MERCY', 'DUSTOFF']
    
    is_military = any(callsign.startswith(p) for p in military_patterns)
    is_helo_callsign = any(p in callsign for p in heli_patterns)
    
    if is_military:
        if is_helo_callsign:
            return 'MIL_HELO'
        # Low and slow military = likely helicopter (keep this heuristic for military)
        if altitude_ft and speed_knots and altitude_ft < 5000 and speed_knots < 180:
            return 'MIL_HELO'
        return 'MIL'
    
    # === STEP 2: Try FAA database lookup (definitive for US civil aircraft) ===
    faa_db = _get_faa_db()
    if faa_db and faa_db.is_ready():
        faa_type = faa_db.get_aircraft_type(icao24=icao24, callsign=callsign)
        if faa_type:
            # FAA database is authoritative - trust it completely!
            logger.debug(f"FAA DB: {callsign}/{icao24} -> {faa_type}")
            return faa_type
    
    # === STEP 3: Fallback heuristics for non-US aircraft ===
    
    # Civilian helicopter callsign patterns
    if is_helo_callsign:
        return 'HELO'
    
    # Specific cargo carriers (check BEFORE passenger airlines!)
    if callsign.startswith('UPS'):
        return 'UPS'
    if callsign.startswith(('FDX', 'FXE')):  # FedEx and FedEx Feeder
        return 'FDX'
    if callsign.startswith(('GTI', 'ATN', 'ABX')):  # Atlas/Amazon partners
        return 'AMAZON'
    if callsign.startswith(('DHL', 'BCS', 'DAE')):  # DHL and partners
        return 'DHL'
    
    # Other cargo carriers -> generic cargo icon
    other_cargo = ['KFS', 'CLX', 'MPH', 'PAC', 'SQC', 'BOX', 'GEC',
                   'ICL', 'NCR', 'AHK', 'CAL', 'CKS', 'NCA', 'POL']
    if any(callsign.startswith(code) for code in other_cargo):
        return 'CARGO'
    
    # Commercial passenger airlines (known ICAO prefixes)
    airlines = ['AAL', 'UAL', 'DAL', 'SWA', 'JBU', 'ASA', 'FFT', 'NKS', 
               'SKW', 'ENY', 'RPA', 'EDV', 'EJA', 'LXJ', 'XOJ', 'TVS', 
               'XAJ', 'LEA', 'WWI', 'VIR', 'BAW', 'AFR', 'DLH', 'KLM']
    if any(callsign.startswith(code) for code in airlines):
        return 'JET'
    
    # High altitude = jet
    if altitude_ft and altitude_ft > 25000:
        return 'JET'
    
    # N-numbers without FAA data = probably GA
    if callsign.startswith('N') and len(callsign) <= 6:
        return 'GA'
    
    return 'UNK'


def calculate_bearing(home_lat: float, home_lon: float, target_lat: float, target_lon: float) -> str:
    """
    Calculate compass direction from home to target coordinates.
    
    Args:
        home_lat: Home latitude
        home_lon: Home longitude
        target_lat: Target latitude
        target_lon: Target longitude
    
    Returns:
        Cardinal/intercardinal direction: N, NE, E, SE, S, SW, W, NW
    """
    dx = (target_lon - home_lon) * math.cos(math.radians(home_lat))
    dy = target_lat - home_lat
    
    angle = math.degrees(math.atan2(dx, dy))
    if angle < 0:
        angle += 360
    
    directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
    index = int((angle + 22.5) / 45) % 8
    return directions[index]


def calculate_distance_km(home_lat: float, home_lon: float, target_lat: float, target_lon: float) -> float:
    """
    Calculate approximate distance in km from home to target.
    Uses simple Euclidean approximation (accurate for small distances).
    
    Args:
        home_lat: Home latitude
        home_lon: Home longitude
        target_lat: Target latitude
        target_lon: Target longitude
    
    Returns:
        Distance in kilometers
    """
    dx = (target_lon - home_lon) * 111.0 * math.cos(math.radians(home_lat))
    dy = (target_lat - home_lat) * 111.0
    return math.sqrt(dx * dx + dy * dy)


# ============================================
# Module status check
# ============================================

def get_status() -> Dict:
    """
    Get status of available databases for diagnostics.
    
    Returns:
        Dict with database availability and stats
    """
    status = {
        'aircraft_db': False,
        'faa_db': False,
        'fallback_cache_entries': 0
    }
    
    aircraft_db = _get_aircraft_db()
    if aircraft_db and aircraft_db.is_ready():
        status['aircraft_db'] = True
        stats = aircraft_db.get_stats()
        status['aircraft_db_count'] = stats.get('total_aircraft', 0)
    
    faa_db = _get_faa_db()
    if faa_db and faa_db.is_ready():
        status['faa_db'] = True
        stats = faa_db.get_stats()
        status['faa_db_count'] = stats.get('aircraft_count', 0)
    
    _load_fallback_cache()
    status['fallback_cache_entries'] = len(_fallback_cache)
    
    return status
