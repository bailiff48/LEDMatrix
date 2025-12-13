import os
import time
import logging
import requests
import json
from typing import Dict, Any, Optional, List, Set
from datetime import datetime, timedelta
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager
import pytz
from src.base_classes.sports import SportsRecent, SportsUpcoming
from src.base_classes.football import Football, FootballLive
from pathlib import Path

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    def increment_api_counter(kind: str, count: int = 1):
        pass

# Constants
ESPN_NCAAFB_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"

# =============================================================================
# CONFERENCE MAPPING FOR RELIABLE ESPN API QUERIES
# Note: Football uses DIFFERENT group IDs than basketball!
# =============================================================================

FOOTBALL_TEAM_CONFERENCES: Dict[str, int] = {
    # BIG TEN (Group 5 for football)
    'IOWA': 5, 'MICH': 5, 'MSU': 5, 'OSU': 5, 'PSU': 5, 'IND': 5,
    'ILL': 5, 'NEB': 5, 'NW': 5, 'MINN': 5, 'WIS': 5, 'PUR': 5,
    'MD': 5, 'RUTG': 5, 'UCLA': 5, 'USC': 5, 'OREG': 5, 'WASH': 5,
    # BIG 12 (Group 4 for football)
    'ISU': 4, 'KU': 4, 'KSU': 4, 'OKST': 4, 'TCU': 4, 'BAY': 4,
    'TTU': 4, 'WVU': 4, 'CIN': 4, 'UCF': 4, 'HOU': 4, 'BYU': 4,
    'ARIZ': 4, 'ASU': 4, 'COLO': 4, 'UTAH': 4,
    # SEC (Group 8 for football)
    'MIZ': 8, 'MIZZ': 8, 'ALA': 8, 'BAMA': 8, 'ARK': 8, 'AUB': 8,
    'FLA': 8, 'UGA': 8, 'UK': 8, 'LSU': 8, 'MSST': 8, 'MISS': 8,
    'OM': 8, 'SCAR': 8, 'SC': 8, 'TENN': 8, 'TA&M': 8, 'TAMU': 8,
    'VAN': 8, 'VANDY': 8, 'TEX': 8, 'OU': 8, 'OKLA': 8,
    # ACC (Group 1 for football)
    'BC': 1, 'CLEM': 1, 'DUKE': 1, 'FSU': 1, 'GT': 1, 'LOU': 1,
    'MIA': 1, 'UNC': 1, 'NCST': 1, 'ND': 1, 'PITT': 1, 'SYR': 1,
    'UVA': 1, 'VT': 1, 'WAKE': 1, 'CAL': 1, 'STAN': 1, 'SMU': 1,
}

FOOTBALL_CONFERENCE_NAMES = {
    5: "Big Ten", 4: "Big 12", 8: "SEC", 1: "ACC",
    151: "American", 17: "Mountain West", 80: "FBS", 81: "FCS"
}


def get_conferences_for_teams(favorite_teams: list) -> Set[int]:
    """Get ESPN conference group IDs for a list of favorite teams."""
    conferences = set()
    for team in favorite_teams:
        team_upper = team.upper()
        if team_upper in FOOTBALL_TEAM_CONFERENCES:
            conferences.add(FOOTBALL_TEAM_CONFERENCES[team_upper])
    return conferences


class BaseNCAAFBManager(Football):
    """Base class for NCAA FB managers with common functionality."""
    # Class variables for warning tracking
    _no_data_warning_logged = False
    _last_warning_time = 0
    _warning_cooldown = 60  # Only log warnings once per minute
    _shared_data = None
    _SHARED_DATA_MAX_AGE = 120  # Clear shared data after 2 minutes
    _last_shared_update = 0
    _processed_games_cache = {}
    _MAX_PROCESSED_CACHE = 20  # Limit to prevent memory leaks
    _processed_games_timestamp = 0

    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        self.logger = logging.getLogger('NCAAFB')
        super().__init__(config=config, display_manager=display_manager, cache_manager=cache_manager, logger=self.logger, sport_key="ncaa_fb")
        
        # Check display modes to determine what data to fetch
        display_modes = self.mode_config.get("display_modes", {})
        self.recent_enabled = display_modes.get("ncaa_fb_recent", False)
        self.upcoming_enabled = display_modes.get("ncaa_fb_upcoming", False)
        self.live_enabled = display_modes.get("ncaa_fb_live", False)
        self.league = "college-football"

        self.logger.info(f"Initialized NCAAFB manager with display dimensions: {self.display_width}x{self.display_height}")
        self.logger.info(f"Logo directory: {self.logo_dir}")
        self.logger.info(f"Display modes - Recent: {self.recent_enabled}, Upcoming: {self.upcoming_enabled}, Live: {self.live_enabled}")

    def _get_weeks_data(self) -> Optional[Dict]:
        """
        OVERRIDE: Fetch data using conference-based queries for reliability.
        
        This method queries each relevant conference separately for reliable results.
        Falls back to base class behavior if conference fetch fails.
        """
        # If no favorite teams, fall back to base class behavior
        if not self.favorite_teams:
            self.logger.debug("No favorite teams, using default _get_weeks_data")
            return super()._get_weeks_data()
        
        # Get conferences for favorite teams
        conferences = get_conferences_for_teams(self.favorite_teams)
        
        if not conferences:
            self.logger.warning(f"No conferences found for teams {self.favorite_teams}, using fallback")
            return super()._get_weeks_data()
        
        conf_names = [FOOTBALL_CONFERENCE_NAMES.get(c, str(c)) for c in conferences]
        self.logger.info(f"Conference fetch for teams {self.favorite_teams}: {conf_names}")
        
        # Use same date range as base class: 2 weeks back, 1 week forward
        now = datetime.now(pytz.utc)
        start_date = now + timedelta(weeks=-2)
        end_date = now + timedelta(weeks=1)
        date_str = f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"
        
        all_events = []
        seen_event_ids = set()
        
        for conf_id in conferences:
            conf_name = FOOTBALL_CONFERENCE_NAMES.get(conf_id, f"Group {conf_id}")
            
            try:
                params = {
                    'dates': date_str,
                    'groups': conf_id,
                    'limit': 100  # Per-conference, 100 is plenty
                }
                
                response = self.session.get(
                    ESPN_NCAAFB_SCOREBOARD_URL,
                    params=params,
                    headers=self.headers,
                    timeout=10
                )
                response.raise_for_status()
                data = response.json()
                
                increment_api_counter('sports', 1)
                
                events = data.get('events', [])
                
                # Deduplicate (cross-conference games appear in both)
                new_count = 0
                for event in events:
                    event_id = event.get('id')
                    if event_id and event_id not in seen_event_ids:
                        seen_event_ids.add(event_id)
                        all_events.append(event)
                        new_count += 1
                
                self.logger.info(f"Fetched {conf_name}: {len(events)} events ({new_count} new)")
                
                time.sleep(0.15)  # Rate limiting
                
            except requests.exceptions.RequestException as e:
                self.logger.warning(f"Error fetching {conf_name}: {e}")
                continue
        
        if all_events:
            self.logger.info(f"Conference fetch complete: {len(all_events)} total events from {len(conferences)} conferences")
            return {'events': all_events}
        
        # Fall back to base class if conference fetch returned nothing
        self.logger.warning("Conference fetch returned no events, trying fallback")
        return super()._get_weeks_data()

    def _fetch_ncaa_fb_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Fetches the full season schedule for NCAAFB using week-by-week approach to ensure
        we get all games, then caches the complete dataset.
        
        This method now uses background threading to prevent blocking the display.
        """
        now = datetime.now(pytz.utc)
        season_year = now.year
        if now.month < 8:
            season_year = now.year - 1
        start_date = (now - timedelta(days=15)).strftime("%Y%m%d")
        end_date = (now + timedelta(days=15)).strftime("%Y%m%d")
        datestring = f"{start_date}-{end_date}"
        cache_key = f"ncaafb_schedule_{season_year}"

        if use_cache:
            cached_data = self.cache_manager.get(cache_key)
            if cached_data:
                # Validate cached data structure
                if isinstance(cached_data, dict) and 'events' in cached_data:
                    self.logger.info(f"Using cached schedule for {season_year}")
                    return cached_data
                elif isinstance(cached_data, list):
                    # Handle old cache format (list of events)
                    self.logger.info(f"Using cached schedule for {season_year} (legacy format)")
                    return {'events': cached_data}
                else:
                    self.logger.warning(f"Invalid cached data format for {season_year}: {type(cached_data)}")
                    # Clear invalid cache
                    self.cache_manager.clear_cache(cache_key)
        
        self.logger.info(f"Fetching full {season_year} season schedule from ESPN API...")

        # Start background fetch
        self.logger.info(f"Starting background fetch for {season_year} season schedule...")
        
        def fetch_callback(result):
            """Callback when background fetch completes."""
            if result.success:
                self.logger.info(f"Background fetch completed for {season_year}: {len(result.data.get('events'))} events")
            else:
                self.logger.error(f"Background fetch failed for {season_year}: {result.error}")
            
            # Clean up request tracking
            if season_year in self.background_fetch_requests:
                del self.background_fetch_requests[season_year]
        
        # Get background service configuration
        background_config = self.mode_config.get("background_service", {})
        timeout = background_config.get("request_timeout", 30)
        max_retries = background_config.get("max_retries", 3)
        priority = background_config.get("priority", 2)
        
        # Submit background fetch request
        request_id = self.background_service.submit_fetch_request(
            sport="ncaa_fb",
            year=season_year,
            url=ESPN_NCAAFB_SCOREBOARD_URL,
            cache_key=cache_key,
            params={"dates": datestring, "limit": 500},
            headers=self.headers,
            timeout=timeout,
            max_retries=max_retries,
            priority=priority,
            callback=fetch_callback
        )
        
        # Track the request
        self.background_fetch_requests[season_year] = request_id
        
        # For immediate response, try to get partial data (uses our conference-aware override)
        partial_data = self._get_weeks_data()
        if partial_data:
            return partial_data
        return None

    def _fetch_data(self) -> Optional[Dict]:
        """Fetch data using shared data mechanism or direct fetch for live."""
        if isinstance(self, NCAAFBLiveManager):
            return self._fetch_todays_games()
        else:
            return self._fetch_ncaa_fb_api_data(use_cache=True)


class NCAAFBLiveManager(BaseNCAAFBManager, FootballLive):
    """Manager for live NCAA FB games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config=config, display_manager=display_manager, cache_manager=cache_manager)
        self.logger = logging.getLogger('NCAAFBLiveManager')

        if self.test_mode:
            # More detailed test game for NCAA FB
            self.current_game = {
                "id": "testNCAAFB001",
                "home_id": "343", "away_id": "567",
                "home_abbr": "UGA", "away_abbr": "AUB",
                "home_score": "28", "away_score": "21",
                "period": 4, "period_text": "Q4", "clock": "01:15",
                "down_distance_text": "2nd & 5", 
                "possession": "UGA",
                "possession_indicator": "home",
                "home_timeouts": 1, "away_timeouts": 2,
                "home_logo_path": Path(self.logo_dir, "UGA.png"),
                "away_logo_path": Path(self.logo_dir, "AUB.png"),
                "is_live": True, "is_final": False, "is_upcoming": False, "is_halftime": False,
                "status_text": "Q4 01:15"
            }
            self.live_games = [self.current_game]
            logging.info("Initialized NCAAFBLiveManager with test game: AUB vs UGA")
        else:
            logging.info("Initialized NCAAFBLiveManager in live mode")


class NCAAFBRecentManager(BaseNCAAFBManager, SportsRecent):
    """Manager for recently completed NCAA FB games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('NCAAFBRecentManager')
        self.logger.info(f"Initialized NCAAFBRecentManager with {len(self.favorite_teams)} favorite teams")


class NCAAFBUpcomingManager(BaseNCAAFBManager, SportsUpcoming):
    """Manager for upcoming NCAA FB games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('NCAAFBUpcomingManager')
        self.logger.info(f"Initialized NCAAFBUpcomingManager with {len(self.favorite_teams)} favorite teams")
