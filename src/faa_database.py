"""
FAA Aircraft Registration Database Module

Provides authoritative aircraft type classification using the FAA's
publicly available aircraft registration database.

This eliminates the need for heuristic-based classification (altitude/speed)
which often misclassifies aircraft like slow-flying Cessnas as helicopters.

FAA Data Source:
https://registry.faa.gov/database/ReleasableAircraft.zip

Key files used:
- MASTER.txt: Aircraft registration with Type and Mode S hex codes
- ACFTREF.txt: Aircraft reference file with manufacturer/model details

Usage:
    from src.faa_database import FAADatabase
    
    faa_db = FAADatabase()
    
    # Lookup by ICAO24 (Mode S hex) - most reliable
    info = faa_db.lookup(icao24='a12345')
    
    # Lookup by N-number from callsign
    info = faa_db.lookup(callsign='N12345')
    
    # Returns dict with 'type', 'manufacturer', 'model', etc.
"""

import os
import csv
import json
import time
import zipfile
import logging
import requests
import threading
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class FAADatabase:
    """
    FAA Aircraft Registration Database for accurate type classification.
    
    Maps ICAO24 hex codes and N-numbers to definitive aircraft types.
    No more guessing based on altitude and speed!
    """
    
    # FAA Type Aircraft codes to our icon types
    # From FAA documentation:
    # 1=Glider, 2=Balloon, 3=Blimp, 4=Fixed wing single, 5=Fixed wing multi,
    # 6=Rotorcraft, 7=Weight-shift, 8=Powered Parachute, 9=Gyroplane, H=Hybrid, O=Other
    TYPE_MAPPING = {
        '1': 'GLIDER',  # Glider/Sailplane - distinctive long wings
        '2': 'BALLOON', # Hot air balloon
        '3': 'BALLOON', # Blimp/Dirigible (use balloon icon)
        '4': 'GA',      # Fixed wing single engine
        '5': 'JET',     # Fixed wing multi engine - refined in _classify_type()
        '6': 'HELO',    # Rotorcraft - THE KEY ONE!
        '7': 'GA',      # Weight-shift-control (ultralight)
        '8': 'CHUTE',   # Powered Parachute
        '9': 'HELO',    # Gyroplane (rotary wing)
        'H': 'GA',      # Hybrid Lift
        'O': 'UNK',     # Other
    }
    
    # FAA Engine Type codes for additional classification help
    # 0=None, 1=Reciprocating, 2=Turbo-prop, 3=Turbo-shaft, 4=Turbo-jet,
    # 5=Turbo-fan, 6=Ramjet, 7=2-Cycle, 8=4-Cycle, 9=Unknown, 10=Electric, 11=Rotary
    ENGINE_TURBINE_CODES = {'2', '3', '4', '5', '6'}  # These are jets/turbines
    
    # Download URL
    FAA_DATABASE_URL = "https://registry.faa.gov/database/ReleasableAircraft.zip"
    
    def __init__(self, data_dir: str = "/home/ledpi/LEDMatrix/data/faa"):
        """
        Initialize FAA Database.
        
        Args:
            data_dir: Directory to store FAA data files
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Lookup tables - populated by _load_database()
        self.by_icao24 = {}     # Mode S hex -> aircraft info
        self.by_nnumber = {}    # N-number (without N prefix) -> aircraft info
        
        # Metadata
        self.last_update = None
        self.aircraft_count = 0
        self._loaded = False
        self._lock = threading.Lock()
        
        # Try to load existing database
        self._load_database()
    
    def _load_database(self) -> bool:
        """
        Load FAA database from local files.
        
        Returns:
            True if successfully loaded, False if files don't exist
        """
        master_file = self.data_dir / "MASTER.txt"
        ref_file = self.data_dir / "ACFTREF.txt"
        meta_file = self.data_dir / "metadata.json"
        
        if not master_file.exists():
            logger.warning(f"FAA database not found at {master_file}")
            logger.info("Run faa_db.download_and_update() to fetch the database")
            return False
        
        try:
            # Load metadata
            if meta_file.exists():
                with open(meta_file, 'r') as f:
                    meta = json.load(f)
                    self.last_update = meta.get('last_update')
            
            # Load aircraft reference file first (for manufacturer/model names)
            acft_ref = {}
            if ref_file.exists():
                acft_ref = self._parse_acftref(ref_file)
                logger.info(f"Loaded {len(acft_ref)} aircraft reference entries")
            
            # Parse MASTER.txt and build lookup tables
            count = self._parse_master(master_file, acft_ref)
            
            self.aircraft_count = count
            self._loaded = True
            
            logger.info(f"FAA database loaded: {count:,} aircraft")
            logger.info(f"  By ICAO24: {len(self.by_icao24):,} entries")
            logger.info(f"  By N-number: {len(self.by_nnumber):,} entries")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to load FAA database: {e}")
            return False
    
    def _parse_acftref(self, filepath: Path) -> Dict[str, Dict]:
        """
        Parse ACFTREF.txt to get manufacturer and model names.
        
        Returns dict mapping MFR_MODEL_CODE -> {manufacturer, model, type_aircraft, etc.}
        """
        ref = {}
        
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) < 4:
                        continue
                    
                    # ACFTREF format (comma-delimited):
                    # 0: MFR_MODEL_CODE (7 chars)
                    # 1: MANUFACTURER (30 chars)
                    # 2: MODEL (20 chars)
                    # 3: TYPE_AIRCRAFT (1 char)
                    # 4: TYPE_ENGINE (2 chars)
                    # ...more fields
                    
                    code = row[0].strip()
                    if not code:
                        continue
                    
                    ref[code] = {
                        'manufacturer': row[1].strip() if len(row) > 1 else '',
                        'model': row[2].strip() if len(row) > 2 else '',
                        'type_aircraft': row[3].strip() if len(row) > 3 else '',
                        'type_engine': row[4].strip() if len(row) > 4 else '',
                        'num_engines': row[9].strip() if len(row) > 9 else '',
                        'num_seats': row[10].strip() if len(row) > 10 else '',
                    }
        
        except Exception as e:
            logger.warning(f"Error parsing ACFTREF.txt: {e}")
        
        return ref
    
    def _parse_master(self, filepath: Path, acft_ref: Dict) -> int:
        """
        Parse MASTER.txt to build lookup tables.
        
        Returns count of aircraft processed.
        """
        count = 0
        
        try:
            with open(filepath, 'r', encoding='utf-8-sig', errors='replace') as f:
                reader = csv.reader(f)
                
                # Skip header row
                header = next(reader, None)
                if header:
                    logger.debug(f"MASTER.txt columns: {len(header)}")
                
                # Actual CSV column indices from FAA file:
                # 0: N-NUMBER
                # 1: SERIAL NUMBER
                # 2: MFR MDL CODE
                # 3: ENG MFR MDL
                # 4: YEAR MFR
                # 5: TYPE REGISTRANT
                # 6: NAME
                # 7-8: STREET, STREET2
                # 9-14: CITY, STATE, ZIP, REGION, COUNTY, COUNTRY
                # 15-16: LAST ACTION DATE, CERT ISSUE DATE
                # 17: CERTIFICATION
                # 18: TYPE AIRCRAFT  <-- This is what we need!
                # 19: TYPE ENGINE
                # 20: STATUS CODE
                # 21: MODE S CODE (octal)
                # 22: FRACT OWNER
                # 23: AIR WORTH DATE
                # 24-28: OTHER NAMES (1-5)
                # 29: EXPIRATION DATE
                # 30: UNIQUE ID
                # 31: KIT MFR
                # 32: KIT MODEL
                # 33: MODE S CODE HEX  <-- This is the ICAO24!
                
                for row in reader:
                    if len(row) < 21:  # Need at least through STATUS CODE
                        continue
                    
                    n_number = row[0].strip().upper()
                    mfr_model_code = row[2].strip() if len(row) > 2 else ''
                    registrant = row[6].strip().upper() if len(row) > 6 else ''
                    type_aircraft = row[18].strip() if len(row) > 18 else ''
                    type_engine = row[19].strip() if len(row) > 19 else ''
                    status_code = row[20].strip() if len(row) > 20 else ''
                    
                    # Mode S hex is in column 33
                    mode_s_hex = ''
                    if len(row) > 33:
                        mode_s_hex = row[33].strip().upper()
                    
                    # Skip invalid/deregistered aircraft
                    if status_code and status_code not in ('V', 'A', 'M', 'T', 'N', 'R', 'S'):
                        continue
                    
                    if not n_number:
                        continue
                    
                    # Skip header-like rows
                    if n_number == 'N-NUMBER':
                        continue
                    
                    # Look up reference info
                    ref_info = acft_ref.get(mfr_model_code, {})
                    
                    # Determine our aircraft type
                    aircraft_type = self._classify_type(type_aircraft, type_engine, ref_info)
                    
                    # Check if this is a cargo carrier by registrant name
                    cargo_type = self._is_cargo_carrier(registrant)
                    if cargo_type and aircraft_type in ('JET', 'TWIN'):
                        aircraft_type = cargo_type  # UPS, FDX, AMAZON, DHL, or CARGO
                    
                    # Build info dict
                    info = {
                        'type': aircraft_type,
                        'type_aircraft': type_aircraft,
                        'type_engine': type_engine,
                        'manufacturer': ref_info.get('manufacturer', ''),
                        'model': ref_info.get('model', ''),
                        'n_number': f"N{n_number}",
                        'registrant': registrant,
                        'is_cargo': bool(cargo_type),
                        'cargo_carrier': cargo_type,
                        'source': 'FAA'
                    }
                    
                    # Store by N-number
                    self.by_nnumber[n_number] = info
                    
                    # Store by ICAO24 hex if available
                    if mode_s_hex:
                        # Clean up hex - remove leading zeros for consistent lookup
                        mode_s_hex = mode_s_hex.lstrip('0')
                        if mode_s_hex:
                            self.by_icao24[mode_s_hex] = info
                    
                    count += 1
                    
                    # Progress logging for large file
                    if count % 50000 == 0:
                        logger.debug(f"Processed {count:,} aircraft...")
        
        except Exception as e:
            logger.error(f"Error parsing MASTER.txt: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        return count
    
    def _classify_type(self, type_aircraft: str, type_engine: str, ref_info: Dict) -> str:
        """
        Classify aircraft to our icon types based on FAA codes.
        
        Priority:
        1. Type aircraft code (most definitive)
        2. Engine type for multi-engine (turbine = JET, piston = TWIN)
        3. Default mapping
        """
        # Rotorcraft is definitive
        if type_aircraft == '6':
            return 'HELO'
        
        # Gyroplane is also rotary wing
        if type_aircraft == '9':
            return 'HELO'
        
        # Single engine fixed wing - almost always GA
        if type_aircraft == '4':
            return 'GA'
        
        # Multi-engine fixed wing - distinguish TWIN (piston) from JET (turbine)
        if type_aircraft == '5':
            if type_engine in self.ENGINE_TURBINE_CODES:
                return 'JET'   # Turboprop, turbojet, turbofan = JET icon
            return 'TWIN'      # Piston twin (Baron, Seneca, etc.)
        
        # Glider
        if type_aircraft == '1':
            return 'GLIDER'
        
        # Balloon or Blimp
        if type_aircraft in ('2', '3'):
            return 'BALLOON'
        
        # Powered Parachute
        if type_aircraft == '8':
            return 'CHUTE'
        
        # Use mapping for everything else
        return self.TYPE_MAPPING.get(type_aircraft, 'UNK')
    
    def _is_cargo_carrier(self, registrant: str) -> str:
        """
        Check if registrant name indicates a cargo carrier.
        
        Args:
            registrant: FAA registrant name (uppercased)
        
        Returns:
            Carrier type code (UPS, FDX, AMAZON, DHL, CARGO) or empty string if not cargo
        """
        if not registrant:
            return ''
        
        # Specific carriers with branded icons
        if 'UNITED PARCEL' in registrant or 'UPS' in registrant:
            return 'UPS'
        
        if 'FEDERAL EXPRESS' in registrant or 'FEDEX' in registrant:
            return 'FDX'
        
        if 'AMAZON' in registrant or 'PRIME AIR' in registrant:
            return 'AMAZON'
        
        if 'DHL' in registrant:
            return 'DHL'
        
        # Atlas Air often flies for Amazon
        if 'ATLAS AIR' in registrant:
            return 'AMAZON'
        
        # ABX Air often flies for DHL/Amazon
        if 'ABX AIR' in registrant:
            return 'AMAZON'
        
        # Other cargo carriers -> generic CARGO
        other_cargo = [
            'KALITTA',
            'CARGOLUX',
            'POLAR AIR',
            'SOUTHERN AIR',
            'WESTERN GLOBAL',
            'WORLD AIRWAYS',
            'NIPPON CARGO',
            'CATHAY CARGO',
            'AIR TRANSPORT INT',
            'AMERIJET',
            'MARTINAIRE',
            'EMPIRE AIRLINES',
            'MOUNTAIN AIR CARGO',
        ]
        
        if any(keyword in registrant for keyword in other_cargo):
            return 'CARGO'
        
        return ''
    
    def lookup(self, icao24: str = None, callsign: str = None) -> Optional[Dict[str, Any]]:
        """
        Look up aircraft information.
        
        Args:
            icao24: ICAO24 hex code (Mode S transponder code)
            callsign: Aircraft callsign (if N-number format)
        
        Returns:
            Dict with aircraft info, or None if not found
        """
        if not self._loaded:
            return None
        
        with self._lock:
            # Try ICAO24 first (most reliable)
            if icao24:
                icao_upper = icao24.upper().lstrip('0')
                if icao_upper in self.by_icao24:
                    return self.by_icao24[icao_upper].copy()
            
            # Try N-number from callsign
            if callsign:
                callsign_upper = callsign.upper().strip()
                if callsign_upper.startswith('N'):
                    n_num = callsign_upper[1:]  # Remove 'N' prefix
                    if n_num in self.by_nnumber:
                        return self.by_nnumber[n_num].copy()
        
        return None
    
    def get_aircraft_type(self, icao24: str = None, callsign: str = None) -> Optional[str]:
        """
        Quick lookup to get just the aircraft type.
        
        Returns:
            Type code ('GA', 'JET', 'HELO', etc.) or None if not found
        """
        info = self.lookup(icao24=icao24, callsign=callsign)
        return info.get('type') if info else None
    
    def download_and_update(self, force: bool = False) -> bool:
        """
        Download fresh FAA database and update lookup tables.
        
        Args:
            force: If True, download even if recent data exists
        
        Returns:
            True if successfully updated
        """
        # Check if we already have recent data
        if not force and self._loaded:
            meta_file = self.data_dir / "metadata.json"
            if meta_file.exists():
                with open(meta_file, 'r') as f:
                    meta = json.load(f)
                    last_update = meta.get('last_update', 0)
                    # Skip if updated within last 24 hours
                    if time.time() - last_update < 86400:
                        logger.info("FAA database is up to date (updated within 24 hours)")
                        return True
        
        logger.info("Downloading FAA aircraft database (~60MB)...")
        
        try:
            # Download zip file
            zip_path = self.data_dir / "ReleasableAircraft.zip"
            
            response = requests.get(self.FAA_DATABASE_URL, stream=True, timeout=120)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(zip_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0 and downloaded % (1024 * 1024) == 0:
                        pct = (downloaded / total_size) * 100
                        logger.debug(f"Download progress: {pct:.1f}%")
            
            logger.info(f"Downloaded {downloaded / (1024*1024):.1f} MB")
            
            # Extract required files
            logger.info("Extracting FAA data files...")
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Extract only the files we need
                for filename in ['MASTER.txt', 'ACFTREF.txt']:
                    try:
                        zf.extract(filename, self.data_dir)
                        logger.debug(f"Extracted {filename}")
                    except KeyError:
                        logger.warning(f"File {filename} not found in archive")
            
            # Clean up zip file to save space
            zip_path.unlink()
            
            # Save metadata
            meta_file = self.data_dir / "metadata.json"
            with open(meta_file, 'w') as f:
                json.dump({
                    'last_update': time.time(),
                    'source': self.FAA_DATABASE_URL
                }, f)
            
            # Reload the database
            with self._lock:
                self.by_icao24.clear()
                self.by_nnumber.clear()
            
            success = self._load_database()
            
            if success:
                logger.info("FAA database update complete!")
            
            return success
            
        except Exception as e:
            logger.error(f"Failed to download FAA database: {e}")
            return False
    
    def is_ready(self) -> bool:
        """Check if database is loaded and ready for lookups."""
        return self._loaded
    
    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        return {
            'loaded': self._loaded,
            'aircraft_count': self.aircraft_count,
            'by_icao24_count': len(self.by_icao24),
            'by_nnumber_count': len(self.by_nnumber),
            'last_update': self.last_update,
            'data_dir': str(self.data_dir)
        }


# Global singleton instance
_faa_db_instance = None
_faa_db_lock = threading.Lock()


def get_faa_database(data_dir: str = "/home/ledpi/LEDMatrix/data/faa") -> FAADatabase:
    """
    Get the global FAA database instance (singleton pattern).
    
    This ensures we only load the ~300K aircraft records once.
    """
    global _faa_db_instance
    
    with _faa_db_lock:
        if _faa_db_instance is None:
            _faa_db_instance = FAADatabase(data_dir)
        return _faa_db_instance


# CLI for manual database updates
if __name__ == '__main__':
    import argparse
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    parser = argparse.ArgumentParser(description='FAA Aircraft Database Manager')
    parser.add_argument('--update', action='store_true', help='Download/update FAA database')
    parser.add_argument('--force', action='store_true', help='Force update even if recent')
    parser.add_argument('--lookup', type=str, help='Look up aircraft by ICAO24 or N-number')
    parser.add_argument('--stats', action='store_true', help='Show database statistics')
    
    args = parser.parse_args()
    
    db = FAADatabase()
    
    if args.update:
        db.download_and_update(force=args.force)
    
    if args.lookup:
        query = args.lookup.upper()
        if query.startswith('N'):
            result = db.lookup(callsign=query)
        else:
            result = db.lookup(icao24=query)
        
        if result:
            print(f"\nAircraft Found:")
            for key, value in result.items():
                print(f"  {key}: {value}")
        else:
            print(f"\nNo aircraft found for: {query}")
    
    if args.stats or (not args.update and not args.lookup):
        stats = db.get_stats()
        print(f"\nFAA Database Status:")
        print(f"  Loaded: {stats['loaded']}")
        print(f"  Aircraft Count: {stats['aircraft_count']:,}")
        print(f"  ICAO24 Lookups: {stats['by_icao24_count']:,}")
        print(f"  N-Number Lookups: {stats['by_nnumber_count']:,}")
        print(f"  Data Directory: {stats['data_dir']}")
        if stats['last_update']:
            from datetime import datetime
            update_time = datetime.fromtimestamp(stats['last_update'])
            print(f"  Last Update: {update_time.strftime('%Y-%m-%d %H:%M:%S')}")
