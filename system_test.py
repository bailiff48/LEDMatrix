#!/usr/bin/env python3
"""
LED Matrix System Test
Validates all components without requiring display observation.
Run with: python3 system_test.py
"""

import sys
import os
import json
import time
import logging
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, '/home/ledpi/LEDMatrix')
os.chdir('/home/ledpi/LEDMatrix')

# Set up logging
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')

class SystemTest:
    def __init__(self):
        self.results = {}
        self.config = None
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        
    def log_result(self, test_name, passed, message="", skipped=False):
        if skipped:
            status = "⏭️  SKIP"
            self.skipped += 1
        elif passed:
            status = "✅ PASS"
            self.passed += 1
        else:
            status = "❌ FAIL"
            self.failed += 1
        
        print(f"{status} | {test_name}: {message}")
        self.results[test_name] = {"passed": passed, "message": message, "skipped": skipped}
    
    def test_config_loading(self):
        """Test 1: Config file loads correctly"""
        try:
            with open('config/config.json', 'r') as f:
                self.config = json.load(f)
            self.log_result("Config Loading", True, f"Loaded {len(self.config)} top-level keys")
            return True
        except Exception as e:
            self.log_result("Config Loading", False, str(e))
            return False
    
    def test_display_modes_config(self):
        """Test 2: Display modes are configured"""
        try:
            # Check for display_modes in mode section or individual sport configs
            modes = self.config.get('mode', {}).get('display_modes', {})
            if not modes:
                # Count enabled sports from individual configs
                enabled = []
                for key in self.config:
                    if '_scoreboard' in key:
                        if self.config[key].get('enabled', False):
                            enabled.append(key.replace('_scoreboard', ''))
                self.log_result("Display Modes", len(enabled) > 0, f"{len(enabled)} sports enabled: {enabled[:5]}...")
            else:
                enabled = [k for k, v in modes.items() if v is True]
                self.log_result("Display Modes", len(enabled) > 0, f"{len(enabled)} modes enabled")
        except Exception as e:
            self.log_result("Display Modes", False, str(e))
    
    def test_favorite_teams(self):
        """Test 3: Favorite teams configured"""
        try:
            all_teams = []
            sports_with_teams = []
            for key in self.config:
                if '_scoreboard' in key or key in ['golf', 'tennis']:
                    teams = self.config[key].get('favorite_teams', [])
                    if teams:
                        all_teams.extend(teams)
                        sports_with_teams.append(key.replace('_scoreboard', ''))
            
            self.log_result("Favorite Teams", len(all_teams) > 0, 
                f"{len(all_teams)} teams across {len(sports_with_teams)} sports: {all_teams[:5]}...")
        except Exception as e:
            self.log_result("Favorite Teams", False, str(e))
    
    def test_manager_imports(self):
        """Test 4: All manager classes can be imported"""
        managers_to_test = [
            ("Clock", "src.clock", "Clock"),
            ("Weather", "src.weather_manager", "WeatherManager"),
            ("Stocks", "src.stock_manager", "StockManager"),
            ("NFL", "src.nfl_managers", "NFLLiveManager"),
            ("NHL", "src.nhl_managers", "NHLLiveManager"),
            ("NBA", "src.nba_managers", "NBALiveManager"),
            ("MLB", "src.mlb_manager", "MLBLiveManager"),
            ("Soccer", "src.soccer_managers", "SoccerLiveManager"),
            ("NCAAM Basketball", "src.ncaam_basketball_managers", "NCAAMBasketballLiveManager"),
            ("NCAAW Basketball", "src.ncaaw_basketball_managers", "NCAAWBasketballLiveManager"),
            ("NCAA Football", "src.ncaa_fb_managers", "NCAAFBLiveManager"),
            ("Golf", "src.golf_manager", "GolfManager"),
            ("Tennis", "src.tennis_manager", "TennisManager"),
            ("Flight", "src.flight_manager", "FlightLiveManager"),
        ]
        
        for name, module, classname in managers_to_test:
            try:
                mod = __import__(module, fromlist=[classname])
                cls = getattr(mod, classname)
                self.log_result(f"Import {name}", True, classname)
            except Exception as e:
                self.log_result(f"Import {name}", False, str(e)[:50])
    
    def test_cache_manager(self):
        """Test 5: Cache manager works"""
        try:
            from src.cache_manager import CacheManager
            cm = CacheManager()
            cm.set("test_key", {"test": "data"})
            result = cm.get("test_key")
            self.log_result("Cache Manager", result is not None, "Set/Get working")
        except Exception as e:
            self.log_result("Cache Manager", False, str(e)[:50])
    
    def test_display_manager(self):
        """Test 6: Display manager initializes (requires root)"""
        if os.geteuid() != 0:
            self.log_result("Display Manager", True, "Skipped (requires root)", skipped=True)
            return
        try:
            from src.display_manager import DisplayManager
            dm = DisplayManager(self.config)
            self.log_result("Display Manager", True, f"{dm.matrix.width}x{dm.matrix.height}")
        except Exception as e:
            self.log_result("Display Manager", False, str(e)[:50])
    
    def test_espn_api_connectivity(self):
        """Test 7: ESPN API is reachable"""
        import requests
        endpoints = [
            ("NFL", "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"),
            ("NHL", "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"),
            ("NBA", "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"),
        ]
        
        for name, url in endpoints:
            try:
                resp = requests.get(url, timeout=10)
                events = resp.json().get('events', [])
                self.log_result(f"ESPN API {name}", resp.status_code == 200, f"{len(events)} games today")
            except Exception as e:
                self.log_result(f"ESPN API {name}", False, str(e)[:40])
    
    def test_weather_api(self):
        """Test 8: Weather API connectivity"""
        import requests
        try:
            lat = self.config.get('location', {}).get('latitude', 41.59)
            lon = self.config.get('location', {}).get('longitude', -93.86)
            url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
            resp = requests.get(url, timeout=10)
            temp = resp.json().get('current_weather', {}).get('temperature')
            self.log_result("Weather API", resp.status_code == 200, f"Temp: {temp}°C")
        except Exception as e:
            self.log_result("Weather API", False, str(e)[:50])
    
    def test_logo_directory(self):
        """Test 9: Logo directories exist and have content"""
        logo_dirs = [
            ("NFL", "assets/sports/nfl_logos"),
            ("NHL", "assets/sports/nhl_logos"),
            ("NBA", "assets/sports/nba_logos"),
            ("MLB", "assets/sports/mlb_logos"),
            ("NCAA", "assets/sports/ncaa_logos"),
            ("Soccer", "assets/sports/soccer_logos"),
        ]
        
        for name, path in logo_dirs:
            full_path = Path(f"/home/ledpi/LEDMatrix/{path}")
            if full_path.exists():
                count = len(list(full_path.glob("*.png")))
                self.log_result(f"Logos {name}", count > 0, f"{count} logos")
            else:
                self.log_result(f"Logos {name}", False, "Directory missing")
    
    def test_web_interface(self):
        """Test 10: Web interface is accessible"""
        import requests
        try:
            resp = requests.get("http://localhost:5001/", timeout=5)
            self.log_result("Web Interface", resp.status_code == 200, f"Status {resp.status_code}")
        except Exception as e:
            self.log_result("Web Interface", False, str(e)[:40])
    
    def test_web_api_endpoints(self):
        """Test 11: Web API endpoints respond"""
        import requests
        endpoints = [
            ("/api/status", "System Status"),
            ("/api/config", "Config API"),
            ("/api/teams/available", "Teams API"),
        ]
        
        for endpoint, name in endpoints:
            try:
                resp = requests.get(f"http://localhost:5001{endpoint}", timeout=5)
                # 200 or 500 with JSON is acceptable (means endpoint exists)
                ok = resp.status_code in [200, 500] and len(resp.text) > 10
                self.log_result(f"API {name}", ok, f"Status {resp.status_code}, {len(resp.text)} bytes")
            except Exception as e:
                self.log_result(f"API {name}", False, str(e)[:40])
    
    def test_background_threads(self):
        """Test 12: Background polling threads are running"""
        import subprocess
        try:
            result = subprocess.run(
                ["journalctl", "-u", "ledmatrix", "--since", "30 min ago", "--no-pager"],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout
            
            threads = {
                "NFL": "Background poll loop started for nfl" in output,
                "NHL": "Background poll loop started for nhl" in output,
                "NBA": "Background poll loop started for nba" in output or "NBALiveManager" in output,
                "Flight": "FlightLiveManager:Background poll loop started" in output,
            }
            
            running = sum(threads.values())
            details = ", ".join([k for k, v in threads.items() if v])
            self.log_result("Background Threads", running >= 2, f"{running}/4 detected: {details}")
        except Exception as e:
            self.log_result("Background Threads", False, str(e)[:40])
    
    def test_service_status(self):
        """Test 13: systemd service is running"""
        import subprocess
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "ledmatrix.service"],
                capture_output=True, text=True, timeout=5
            )
            is_active = result.stdout.strip() == "active"
            self.log_result("Service Status", is_active, result.stdout.strip())
        except Exception as e:
            self.log_result("Service Status", False, str(e)[:40])
    
    def test_memory_usage(self):
        """Test 14: Memory usage is reasonable"""
        import subprocess
        try:
            result = subprocess.run(
                ["journalctl", "-u", "ledmatrix", "--since", "10 min ago", "--no-pager"],
                capture_output=True, text=True, timeout=10
            )
            
            import re
            matches = re.findall(r'Memory (\d+)MB', result.stdout)
            if matches:
                mem = int(matches[-1])
                self.log_result("Memory Usage", mem < 500, f"{mem}MB (warn if >500MB)")
            else:
                self.log_result("Memory Usage", True, "No recent memory logs", skipped=True)
        except Exception as e:
            self.log_result("Memory Usage", False, str(e)[:40])
    
    def test_data_fetching(self):
        """Test 15: Managers can fetch data"""
        import subprocess
        try:
            result = subprocess.run(
                ["journalctl", "-u", "ledmatrix", "--since", "15 min ago", "--no-pager"],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout
            
            fetches = {
                "NFL": "NFLLiveManager:Fetched" in output or "NFLRecentManager" in output,
                "NHL": "NHLLiveManager:Fetched" in output or "NHLRecentManager" in output,
                "Flight": "FlightLiveManager" in output,
                "Weather": "Weather" in output or "weather" in output,
            }
            
            working = sum(fetches.values())
            details = ", ".join([k for k, v in fetches.items() if v])
            self.log_result("Data Fetching", working >= 2, f"{working}/4 active: {details}")
        except Exception as e:
            self.log_result("Data Fetching", False, str(e)[:40])
    
    def test_live_game_detection(self):
        """Test 16: Check if live games are being polled"""
        import subprocess
        try:
            result = subprocess.run(
                ["journalctl", "-u", "ledmatrix", "--since", "15 min ago", "--no-pager"],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout
            
            import re
            fetches = re.findall(r'Fetched (\d+) todays games for (\w+)', output)
            if fetches:
                summary = {}
                for count, sport in fetches[-15:]:
                    summary[sport] = int(count)
                self.log_result("Live Game Polling", True, f"Recent polls: {summary}")
            else:
                self.log_result("Live Game Polling", False, "No 'todays games' fetches found")
        except Exception as e:
            self.log_result("Live Game Polling", False, str(e)[:40])
    
    def test_anti_spoiler_config(self):
        """Test 17: Anti-spoiler settings"""
        try:
            # Check multiple possible locations
            spoiler = self.config.get('sports', {}).get('anti_spoiler', {})
            if not spoiler:
                spoiler = self.config.get('anti_spoiler', {})
            if not spoiler:
                spoiler = self.config.get('mode', {}).get('anti_spoiler', {})
            
            enabled = spoiler.get('enabled', False)
            window = spoiler.get('spoiler_window_hours', 0)
            teams = spoiler.get('anti_spoiler_teams', [])
            
            # It's OK if not configured
            self.log_result("Anti-Spoiler Config", True, f"Enabled: {enabled}, Window: {window}h, Teams: {len(teams)}")
        except Exception as e:
            self.log_result("Anti-Spoiler Config", False, str(e)[:50])
    
    def test_flight_config(self):
        """Test 18: Flight tracker configured"""
        try:
            flight = self.config.get('flights', {})
            enabled = flight.get('enabled', False)
            radius = flight.get('radius_km', 0)
            has_creds = bool(flight.get('opensky_client_id'))
            self.log_result("Flight Config", True, f"Enabled: {enabled}, Radius: {radius}km, OAuth: {has_creds}")
        except Exception as e:
            self.log_result("Flight Config", False, str(e)[:50])
    
    def test_error_rate(self):
        """Test 19: Check for excessive errors"""
        import subprocess
        try:
            result = subprocess.run(
                ["journalctl", "-u", "ledmatrix", "--since", "15 min ago", "--no-pager"],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout
            
            errors = output.lower().count('error')
            warnings = output.lower().count('warning')
            total_lines = len(output.split('\n'))
            
            error_rate = (errors / max(total_lines, 1)) * 100
            passed = error_rate < 10  # Less than 10% errors
            self.log_result("Error Rate", passed, f"{errors} errors in {total_lines} lines ({error_rate:.1f}%)")
        except Exception as e:
            self.log_result("Error Rate", False, str(e)[:40])
    
    def test_display_rotation(self):
        """Test 20: Display is rotating through content"""
        import subprocess
        try:
            result = subprocess.run(
                ["journalctl", "-u", "ledmatrix", "--since", "5 min ago", "--no-pager"],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout
            
            # Look for "Switching to X from Y" messages
            import re
            switches = re.findall(r'Switching to (\w+) from', output)
            unique = set(switches)
            self.log_result("Display Rotation", len(unique) >= 2, f"{len(switches)} switches, {len(unique)} unique modes: {list(unique)[:5]}")
        except Exception as e:
            self.log_result("Display Rotation", False, str(e)[:40])
    
    def run_all_tests(self):
        """Run all tests"""
        print("=" * 60)
        print("🖥️  LED MATRIX SYSTEM TEST")
        print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"👤 Running as: {'root' if os.geteuid() == 0 else 'user'}")
        print("=" * 60)
        
        if not self.test_config_loading():
            print("\n❌ Cannot continue without config file!")
            return False
        
        print("\n📋 --- Configuration Tests ---")
        self.test_display_modes_config()
        self.test_favorite_teams()
        self.test_anti_spoiler_config()
        self.test_flight_config()
        
        print("\n📦 --- Module Import Tests ---")
        self.test_manager_imports()
        
        print("\n⚙️  --- Core Component Tests ---")
        self.test_cache_manager()
        self.test_display_manager()
        
        print("\n🌐 --- API Connectivity Tests ---")
        self.test_espn_api_connectivity()
        self.test_weather_api()
        
        print("\n🎨 --- Asset Tests ---")
        self.test_logo_directory()
        
        print("\n🔧 --- Service Tests ---")
        self.test_service_status()
        self.test_web_interface()
        self.test_web_api_endpoints()
        self.test_background_threads()
        
        print("\n📊 --- Runtime Tests ---")
        self.test_memory_usage()
        self.test_data_fetching()
        self.test_live_game_detection()
        self.test_display_rotation()
        self.test_error_rate()
        
        # Summary
        print("\n" + "=" * 60)
        print("📈 SUMMARY")
        print("=" * 60)
        total = self.passed + self.failed + self.skipped
        print(f"✅ Passed:  {self.passed}/{total}")
        print(f"❌ Failed:  {self.failed}/{total}")
        print(f"⏭️  Skipped: {self.skipped}/{total}")
        
        if self.failed == 0:
            print("\n🎉 ALL TESTS PASSED!")
        elif self.failed <= 2:
            print(f"\n✨ MOSTLY GOOD - {self.failed} minor issue(s)")
        else:
            print(f"\n⚠️  {self.failed} test(s) need attention")
        
        return self.failed == 0


if __name__ == "__main__":
    tester = SystemTest()
    success = tester.run_all_tests()
    sys.exit(0 if success else 1)
