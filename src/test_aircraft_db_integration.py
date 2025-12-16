#!/usr/bin/env python3
"""
Test Aircraft Database Integration

Run this script to verify:
1. Database is downloaded and converted
2. Lookups work correctly
3. Integration with flight manager

Usage:
    # Full setup and test
    python3 test_aircraft_db_integration.py --setup
    
    # Just test existing database
    python3 test_aircraft_db_integration.py
    
    # Test specific icao24 codes
    python3 test_aircraft_db_integration.py --test a12345 ab1234 c0ffee
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_module_import():
    """Test 1: Can we import the aircraft_db module?"""
    print("\n" + "=" * 60)
    print("TEST 1: Module Import")
    print("=" * 60)
    
    try:
        from aircraft_db import AircraftDatabase, setup_database, DEFAULT_DB_PATH
        print("✓ aircraft_db module imported successfully")
        print(f"  Default DB path: {DEFAULT_DB_PATH}")
        return True
    except ImportError as e:
        print(f"✗ Failed to import aircraft_db: {e}")
        print("  Make sure aircraft_db.py is in the same directory")
        return False


def test_database_exists():
    """Test 2: Does the database file exist?"""
    print("\n" + "=" * 60)
    print("TEST 2: Database File")
    print("=" * 60)
    
    from aircraft_db import DEFAULT_DB_PATH
    
    if os.path.exists(DEFAULT_DB_PATH):
        size_mb = os.path.getsize(DEFAULT_DB_PATH) / (1024 * 1024)
        print(f"✓ Database exists: {DEFAULT_DB_PATH}")
        print(f"  Size: {size_mb:.1f} MB")
        return True
    else:
        print(f"✗ Database not found: {DEFAULT_DB_PATH}")
        print("  Run with --setup to download and create database")
        return False


def test_database_connection():
    """Test 3: Can we connect and query the database?"""
    print("\n" + "=" * 60)
    print("TEST 3: Database Connection")
    print("=" * 60)
    
    from aircraft_db import AircraftDatabase
    
    db = AircraftDatabase()
    
    if db.is_ready():
        stats = db.get_stats()
        print(f"✓ Database connected and ready")
        print(f"  Total aircraft: {stats.get('total_aircraft', 0):,}")
        print(f"  With manufacturer: {stats.get('coverage_manufacturer', 'N/A')}")
        print(f"  With model: {stats.get('coverage_model', 'N/A')}")
        db.close()
        return True
    else:
        print("✗ Database not ready or empty")
        db.close()
        return False


def test_sample_lookups():
    """Test 4: Test some sample lookups"""
    print("\n" + "=" * 60)
    print("TEST 4: Sample Lookups")
    print("=" * 60)
    
    from aircraft_db import AircraftDatabase
    
    db = AircraftDatabase()
    
    if not db.is_ready():
        print("✗ Database not ready")
        return False
    
    # Test some common icao24 codes (US aircraft start with 'a')
    test_codes = [
        'a12345',  # Random US
        'a0a0a0',  # Another US
        '4ca000',  # Ireland
        '400000',  # UK
        '3c0000',  # Germany
    ]
    
    found_count = 0
    
    for code in test_codes:
        info = db.lookup(code)
        if info:
            found_count += 1
            display = db.get_display_string(code)
            reg = info.get('registration', 'N/A')
            print(f"  ✓ {code}: {display or 'No display'} (Reg: {reg})")
        else:
            print(f"  - {code}: Not in database")
    
    print(f"\n  Found {found_count}/{len(test_codes)} test codes")
    
    db.close()
    return True


def test_display_string():
    """Test 5: Test display string formatting"""
    print("\n" + "=" * 60)
    print("TEST 5: Display String Formatting")
    print("=" * 60)
    
    from aircraft_db import AircraftDatabase
    
    db = AircraftDatabase()
    
    if not db.is_ready():
        print("✗ Database not ready")
        return False
    
    # Find a few aircraft with known good data
    cursor = db.conn.execute("""
        SELECT icao24, manufacturerName, model, typecode, registration
        FROM aircraft 
        WHERE manufacturerName != '' AND model != ''
        LIMIT 5
    """)
    
    rows = cursor.fetchall()
    
    for row in rows:
        icao24 = row[0]
        display = db.get_display_string(icao24, max_length=25)
        print(f"  {icao24}: {display}")
        print(f"    Raw: {row[1]} {row[2]} ({row[3]}) - {row[4]}")
    
    db.close()
    return True


def test_cache_performance():
    """Test 6: Test cache performance"""
    print("\n" + "=" * 60)
    print("TEST 6: Cache Performance")
    print("=" * 60)
    
    import time
    from aircraft_db import AircraftDatabase
    
    db = AircraftDatabase()
    
    if not db.is_ready():
        print("✗ Database not ready")
        return False
    
    # Get a sample of icao24 codes
    cursor = db.conn.execute("SELECT icao24 FROM aircraft LIMIT 100")
    codes = [row[0] for row in cursor.fetchall()]
    
    # First pass - cold cache
    start = time.time()
    for code in codes:
        db.lookup(code)
    cold_time = time.time() - start
    
    # Second pass - warm cache
    start = time.time()
    for code in codes:
        db.lookup(code)
    warm_time = time.time() - start
    
    print(f"  Cold cache (100 lookups): {cold_time*1000:.1f}ms")
    print(f"  Warm cache (100 lookups): {warm_time*1000:.1f}ms")
    print(f"  Cache speedup: {cold_time/warm_time:.1f}x")
    print(f"  Current cache size: {len(db._cache)}")
    
    db.close()
    return True


def test_flight_manager_integration():
    """Test 7: Test integration with FlightLiveManager"""
    print("\n" + "=" * 60)
    print("TEST 7: FlightLiveManager Integration")
    print("=" * 60)
    
    try:
        from flight_manager_enhanced import FlightLiveManager, AIRCRAFT_DB_AVAILABLE
        print(f"  ✓ flight_manager_enhanced imported")
        print(f"  Aircraft DB available: {AIRCRAFT_DB_AVAILABLE}")
        
        # Test enrichment method standalone
        if AIRCRAFT_DB_AVAILABLE:
            from aircraft_db import AircraftDatabase
            db = AircraftDatabase()
            if db.is_ready():
                # Create mock flight data
                mock_flight = {
                    'icao24': 'a12345',
                    'callsign': 'UAL123',
                    'altitude_ft': 35000,
                    'speed_knots': 450,
                }
                
                info = db.lookup(mock_flight['icao24'])
                if info:
                    print(f"  Sample enrichment for {mock_flight['icao24']}:")
                    print(f"    Manufacturer: {info.get('manufacturer')}")
                    print(f"    Model: {info.get('model')}")
                    print(f"    Typecode: {info.get('typecode')}")
                else:
                    print(f"  No DB entry for test icao24 {mock_flight['icao24']}")
                
                db.close()
        
        return True
        
    except ImportError as e:
        print(f"  ✗ Failed to import flight_manager_enhanced: {e}")
        return False


def run_setup():
    """Run full database setup"""
    print("\n" + "=" * 60)
    print("AIRCRAFT DATABASE SETUP")
    print("=" * 60)
    
    from aircraft_db import setup_database
    
    success = setup_database(keep_csv=False)
    
    if success:
        print("\n✓ Setup completed successfully!")
    else:
        print("\n✗ Setup failed!")
    
    return success


def test_specific_codes(codes):
    """Test specific icao24 codes provided by user"""
    print("\n" + "=" * 60)
    print("TESTING SPECIFIC ICAO24 CODES")
    print("=" * 60)
    
    from aircraft_db import AircraftDatabase
    
    db = AircraftDatabase()
    
    if not db.is_ready():
        print("✗ Database not ready")
        return False
    
    for code in codes:
        code = code.lower().strip()
        info = db.lookup(code)
        
        print(f"\n{code}:")
        if info:
            print(f"  Registration: {info.get('registration') or 'N/A'}")
            print(f"  Manufacturer: {info.get('manufacturer') or 'N/A'}")
            print(f"  Model: {info.get('model') or 'N/A'}")
            print(f"  Typecode: {info.get('typecode') or 'N/A'}")
            print(f"  Operator: {info.get('operator') or 'N/A'}")
            print(f"  Display: {db.get_display_string(code)}")
        else:
            print("  Not found in database")
    
    db.close()
    return True


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Test Aircraft Database Integration')
    parser.add_argument('--setup', action='store_true',
                       help='Download and setup the aircraft database')
    parser.add_argument('--test', nargs='*', metavar='ICAO24',
                       help='Test specific icao24 codes')
    
    args = parser.parse_args()
    
    if args.setup:
        # Run setup then tests
        if not run_setup():
            sys.exit(1)
        print("\n" + "=" * 60)
        print("Running verification tests...")
        print("=" * 60)
    
    if args.test:
        # Test specific codes
        test_specific_codes(args.test)
        sys.exit(0)
    
    # Run all tests
    results = {}
    
    results['import'] = test_module_import()
    if not results['import']:
        print("\n✗ Cannot continue without aircraft_db module")
        sys.exit(1)
    
    results['exists'] = test_database_exists()
    if not results['exists']:
        print("\n⚠ Run with --setup to download and create database")
        sys.exit(1)
    
    results['connection'] = test_database_connection()
    if results['connection']:
        results['lookups'] = test_sample_lookups()
        results['display'] = test_display_string()
        results['cache'] = test_cache_performance()
    
    results['integration'] = test_flight_manager_integration()
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}  {test_name}")
    
    print(f"\n  Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED!")
        print("\nNext steps:")
        print("  1. Copy aircraft_db.py to ~/LEDMatrix/src/")
        print("  2. Copy flight_manager_enhanced.py to ~/LEDMatrix/src/flight_manager.py")
        print("  3. Restart ledmatrix service")
    else:
        print("\n⚠ Some tests failed - check output above")
    
    sys.exit(0 if passed == total else 1)


if __name__ == '__main__':
    main()
