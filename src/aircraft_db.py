"""
Aircraft Database Manager for LED Matrix Flight Tracker

Downloads OpenSky Network's aircraft database CSV and converts it to SQLite
for fast local lookups by icao24 hex code.

Usage:
    # First-time setup (downloads ~100MB CSV, creates ~50MB SQLite)
    python3 aircraft_db.py --setup
    
    # Test lookup
    python3 aircraft_db.py --lookup a12345
    
    # Check database stats
    python3 aircraft_db.py --stats

Integration with FlightLiveManager:
    from aircraft_db import AircraftDatabase
    
    db = AircraftDatabase('/path/to/aircraft.db')
    info = db.lookup('a12345')
    if info:
        print(f"{info['manufacturer']} {info['model']}")
"""

import sqlite3
import csv
import os
import sys
import time
import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# OpenSky aircraft database CSV URL (July 2024 snapshot)
OPENSKY_CSV_URL = "https://opensky-network.org/datasets/metadata/aircraft-database-complete-2024-07.csv"

# Default paths
DEFAULT_DB_PATH = os.path.expanduser("~/LEDMatrix/data/aircraft.db")
DEFAULT_CSV_PATH = os.path.expanduser("~/LEDMatrix/data/aircraft-database.csv")


class AircraftDatabase:
    """
    SQLite-backed aircraft database for fast icao24 lookups.
    
    Provides manufacturer, model, typecode, registration, and operator
    information for aircraft based on their Mode S transponder hex code.
    """
    
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        """
        Initialize the aircraft database.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.conn = None
        self._connect()
        
        # Simple in-memory cache for recently looked up aircraft
        self._cache: Dict[str, Optional[Dict[str, Any]]] = {}
        self._cache_max_size = 500  # Keep last 500 lookups
        
    def _connect(self) -> None:
        """Establish database connection."""
        if os.path.exists(self.db_path):
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            logger.debug(f"Connected to aircraft database: {self.db_path}")
        else:
            logger.warning(f"Aircraft database not found: {self.db_path}")
            logger.warning("Run 'python3 aircraft_db.py --setup' to download and create database")
            self.conn = None
    
    def is_ready(self) -> bool:
        """Check if database is available and populated."""
        if not self.conn:
            return False
        try:
            cursor = self.conn.execute("SELECT COUNT(*) FROM aircraft")
            count = cursor.fetchone()[0]
            return count > 0
        except sqlite3.Error:
            return False
    
    def lookup(self, icao24: str) -> Optional[Dict[str, Any]]:
        """
        Look up aircraft information by icao24 hex code.
        
        Args:
            icao24: 6-character Mode S transponder hex code (e.g., 'a12345')
            
        Returns:
            Dictionary with aircraft info, or None if not found:
            {
                'icao24': 'a12345',
                'registration': 'N12345',
                'manufacturer': 'Boeing',
                'model': '737-824',
                'typecode': 'B738',
                'operator': 'United Airlines',
                'operator_callsign': 'UNITED',
                'owner': 'United Airlines Inc',
                'country': 'United States'
            }
        """
        if not self.conn:
            return None
        
        # Normalize icao24 to lowercase
        icao24 = icao24.lower().strip()
        
        # Check cache first
        if icao24 in self._cache:
            return self._cache[icao24]
        
        try:
            cursor = self.conn.execute("""
                SELECT 
                    icao24,
                    registration,
                    manufacturerName as manufacturer,
                    model,
                    typecode,
                    operator,
                    operatorCallsign as operator_callsign,
                    owner,
                    country
                FROM aircraft
                WHERE icao24 = ?
            """, (icao24,))
            
            row = cursor.fetchone()
            
            if row:
                result = dict(row)
                # Clean up empty strings to None
                for key in result:
                    if result[key] == '':
                        result[key] = None
            else:
                result = None
            
            # Cache the result
            self._cache[icao24] = result
            
            # Prune cache if too large
            if len(self._cache) > self._cache_max_size:
                # Remove oldest 100 entries
                keys_to_remove = list(self._cache.keys())[:100]
                for key in keys_to_remove:
                    del self._cache[key]
            
            return result
            
        except sqlite3.Error as e:
            logger.error(f"Database lookup error for {icao24}: {e}")
            return None
    
    def get_display_string(self, icao24: str, max_length: int = 20) -> Optional[str]:
        """
        Get a formatted display string for the aircraft.
        
        Args:
            icao24: 6-character Mode S transponder hex code
            max_length: Maximum length of returned string
            
        Returns:
            Formatted string like "Boeing 737-824" or "Cessna 172S"
            or None if not found
        """
        info = self.lookup(icao24)
        if not info:
            return None
        
        # Build display string in priority order
        manufacturer = info.get('manufacturer', '')
        model = info.get('model', '')
        typecode = info.get('typecode', '')
        
        if manufacturer and model:
            # Full: "Boeing 737-824"
            display = f"{manufacturer} {model}"
        elif manufacturer and typecode:
            # Partial: "Boeing B738"
            display = f"{manufacturer} {typecode}"
        elif model:
            # Model only: "737-824"
            display = model
        elif typecode:
            # Typecode only: "B738"
            display = typecode
        else:
            return None
        
        # Truncate if needed
        if len(display) > max_length:
            display = display[:max_length-1] + "…"
        
        return display
    
    def get_short_type(self, icao24: str) -> Optional[str]:
        """
        Get short aircraft type code (e.g., 'B738', 'A320', 'C172').
        Useful for compact displays.
        """
        info = self.lookup(icao24)
        if info and info.get('typecode'):
            return info['typecode']
        return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        if not self.conn:
            return {'status': 'not_connected'}
        
        try:
            cursor = self.conn.execute("SELECT COUNT(*) FROM aircraft")
            total = cursor.fetchone()[0]
            
            cursor = self.conn.execute("""
                SELECT COUNT(*) FROM aircraft 
                WHERE manufacturerName IS NOT NULL AND manufacturerName != ''
            """)
            with_manufacturer = cursor.fetchone()[0]
            
            cursor = self.conn.execute("""
                SELECT COUNT(*) FROM aircraft 
                WHERE model IS NOT NULL AND model != ''
            """)
            with_model = cursor.fetchone()[0]
            
            # Get file size
            file_size_mb = os.path.getsize(self.db_path) / (1024 * 1024)
            
            return {
                'status': 'ready',
                'total_aircraft': total,
                'with_manufacturer': with_manufacturer,
                'with_model': with_model,
                'coverage_manufacturer': f"{with_manufacturer/total*100:.1f}%",
                'coverage_model': f"{with_model/total*100:.1f}%",
                'database_size_mb': f"{file_size_mb:.1f}",
                'cache_size': len(self._cache),
                'database_path': self.db_path
            }
        except sqlite3.Error as e:
            return {'status': 'error', 'error': str(e)}
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None


def download_csv(url: str = OPENSKY_CSV_URL, output_path: str = DEFAULT_CSV_PATH) -> bool:
    """
    Download the OpenSky aircraft database CSV.
    
    Args:
        url: URL to download from
        output_path: Where to save the CSV
        
    Returns:
        True if successful, False otherwise
    """
    print(f"Downloading aircraft database from OpenSky Network...")
    print(f"URL: {url}")
    print(f"This may take a few minutes (~100MB file)...")
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    try:
        response = requests.get(url, stream=True, timeout=300)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        pct = (downloaded / total_size) * 100
                        print(f"\r  Progress: {downloaded/(1024*1024):.1f}MB / {total_size/(1024*1024):.1f}MB ({pct:.1f}%)", end='')
        
        print(f"\n✓ Download complete: {output_path}")
        return True
        
    except requests.RequestException as e:
        print(f"\n✗ Download failed: {e}")
        return False


def convert_csv_to_sqlite(csv_path: str = DEFAULT_CSV_PATH, db_path: str = DEFAULT_DB_PATH) -> bool:
    """
    Convert the OpenSky CSV to SQLite database.
    
    Args:
        csv_path: Path to downloaded CSV
        db_path: Path for output SQLite database
        
    Returns:
        True if successful, False otherwise
    """
    print(f"\nConverting CSV to SQLite database...")
    print(f"Input:  {csv_path}")
    print(f"Output: {db_path}")
    
    if not os.path.exists(csv_path):
        print(f"✗ CSV file not found: {csv_path}")
        return False
    
    # Ensure output directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    # Remove existing database
    if os.path.exists(db_path):
        os.remove(db_path)
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Create table with only the columns we need
        cursor.execute("""
            CREATE TABLE aircraft (
                icao24 TEXT PRIMARY KEY,
                registration TEXT,
                manufacturerName TEXT,
                model TEXT,
                typecode TEXT,
                operator TEXT,
                operatorCallsign TEXT,
                owner TEXT,
                country TEXT
            )
        """)
        
        # Create index on icao24 for fast lookups
        cursor.execute("CREATE INDEX idx_icao24 ON aircraft(icao24)")
        
        # Read CSV and insert rows
        row_count = 0
        error_count = 0
        
        with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
            # The CSV uses single quotes around values, need custom handling
            reader = csv.DictReader(f, quotechar="'")
            
            batch = []
            batch_size = 10000
            
            for row in reader:
                try:
                    # Extract only columns we need
                    aircraft = (
                        row.get('icao24', '').lower().strip(),
                        row.get('registration', '').strip(),
                        row.get('manufacturerName', '').strip(),
                        row.get('model', '').strip(),
                        row.get('typecode', '').strip(),
                        row.get('operator', '').strip(),
                        row.get('operatorCallsign', '').strip(),
                        row.get('owner', '').strip(),
                        row.get('country', '').strip()
                    )
                    
                    # Skip rows without icao24
                    if aircraft[0]:
                        batch.append(aircraft)
                        row_count += 1
                    
                    # Insert in batches for performance
                    if len(batch) >= batch_size:
                        cursor.executemany("""
                            INSERT OR REPLACE INTO aircraft 
                            (icao24, registration, manufacturerName, model, typecode, 
                             operator, operatorCallsign, owner, country)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, batch)
                        conn.commit()
                        print(f"\r  Processed: {row_count:,} aircraft", end='')
                        batch = []
                        
                except Exception as e:
                    error_count += 1
                    if error_count <= 5:
                        logger.warning(f"Error processing row: {e}")
            
            # Insert remaining batch
            if batch:
                cursor.executemany("""
                    INSERT OR REPLACE INTO aircraft 
                    (icao24, registration, manufacturerName, model, typecode, 
                     operator, operatorCallsign, owner, country)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, batch)
                conn.commit()
        
        # Optimize database
        cursor.execute("VACUUM")
        conn.close()
        
        # Report results
        db_size = os.path.getsize(db_path) / (1024 * 1024)
        print(f"\n✓ Database created successfully!")
        print(f"  Aircraft records: {row_count:,}")
        print(f"  Database size: {db_size:.1f} MB")
        print(f"  Errors skipped: {error_count}")
        
        return True
        
    except Exception as e:
        print(f"\n✗ Conversion failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def setup_database(keep_csv: bool = False) -> bool:
    """
    Complete setup: download CSV and convert to SQLite.
    
    Args:
        keep_csv: If True, keep the CSV file after conversion
        
    Returns:
        True if successful
    """
    print("=" * 60)
    print("Aircraft Database Setup")
    print("=" * 60)
    print()
    
    # Step 1: Download CSV
    if not download_csv():
        return False
    
    # Step 2: Convert to SQLite
    if not convert_csv_to_sqlite():
        return False
    
    # Step 3: Optionally remove CSV to save space
    if not keep_csv and os.path.exists(DEFAULT_CSV_PATH):
        os.remove(DEFAULT_CSV_PATH)
        print(f"\n✓ Removed CSV file to save space")
    
    # Step 4: Test the database
    print("\n" + "=" * 60)
    print("Testing Database")
    print("=" * 60)
    
    db = AircraftDatabase()
    if db.is_ready():
        stats = db.get_stats()
        print(f"\n✓ Database ready!")
        print(f"  Total aircraft: {stats['total_aircraft']:,}")
        print(f"  With manufacturer: {stats['coverage_manufacturer']}")
        print(f"  With model: {stats['coverage_model']}")
        
        # Test a lookup
        test_codes = ['a0a0a0', 'a12345', '4ca000']
        print(f"\nSample lookups:")
        for code in test_codes:
            info = db.lookup(code)
            if info:
                display = db.get_display_string(code)
                print(f"  {code}: {display or 'No display info'}")
            else:
                print(f"  {code}: Not found")
        
        db.close()
        return True
    else:
        print("✗ Database test failed")
        return False


def main():
    """Command line interface."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Aircraft Database Manager')
    parser.add_argument('--setup', action='store_true', 
                       help='Download and setup the aircraft database')
    parser.add_argument('--keep-csv', action='store_true',
                       help='Keep CSV file after conversion (with --setup)')
    parser.add_argument('--lookup', type=str, metavar='ICAO24',
                       help='Look up an aircraft by icao24 hex code')
    parser.add_argument('--stats', action='store_true',
                       help='Show database statistics')
    parser.add_argument('--db', type=str, default=DEFAULT_DB_PATH,
                       help=f'Database path (default: {DEFAULT_DB_PATH})')
    
    args = parser.parse_args()
    
    if args.setup:
        success = setup_database(keep_csv=args.keep_csv)
        sys.exit(0 if success else 1)
    
    elif args.lookup:
        db = AircraftDatabase(args.db)
        if not db.is_ready():
            print("Database not ready. Run --setup first.")
            sys.exit(1)
        
        info = db.lookup(args.lookup)
        if info:
            print(f"\nAircraft: {args.lookup}")
            print("-" * 40)
            for key, value in info.items():
                if value:
                    print(f"  {key}: {value}")
            print()
            display = db.get_display_string(args.lookup)
            print(f"Display string: {display}")
        else:
            print(f"Aircraft {args.lookup} not found in database")
        
        db.close()
    
    elif args.stats:
        db = AircraftDatabase(args.db)
        stats = db.get_stats()
        print("\nAircraft Database Statistics")
        print("-" * 40)
        for key, value in stats.items():
            print(f"  {key}: {value}")
        db.close()
    
    else:
        parser.print_help()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
