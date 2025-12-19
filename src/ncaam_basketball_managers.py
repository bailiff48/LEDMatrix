import logging
from datetime import datetime
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Set
import time

import pytz
import requests

from src.base_classes.basketball import Basketball, BasketballLive
from src.base_classes.sports import SportsRecent, SportsUpcoming
from src.cache_manager import CacheManager
from src.display_manager import DisplayManager

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):
        pass


# Constants
ESPN_NCAAMB_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"

# =============================================================================
# CONFERENCE MAPPING FOR RELIABLE ESPN API QUERIES
# =============================================================================
# ESPN's API with groups=50 (all D1) + limit=500 is unreliable and randomly
# misses games. Conference-specific queries are much more reliable.

BASKETBALL_TEAM_CONFERENCES: Dict[str, int] = {
    # BIG TEN (Group 7)
    'IOWA': 7, 'MICH': 7, 'MSU': 7, 'OSU': 7, 'PSU': 7, 'IND': 7,
    'ILL': 7, 'NEB': 7, 'NW': 7, 'MINN': 7, 'WIS': 7, 'PUR': 7,
    'MD': 7, 'RUTG': 7, 'UCLA': 7, 'USC': 7, 'OREG': 7, 'WASH': 7,
    # BIG 12 (Group 8)
    'ISU': 8, 'KU': 8, 'KSU': 8, 'OKST': 8, 'TCU': 8, 'BAY': 8,
    'TTU': 8, 'WVU': 8, 'CIN': 8, 'UCF': 8, 'HOU': 8, 'BYU': 8,
    'ARIZ': 8, 'ASU': 8, 'COLO': 8, 'UTAH': 8,
    # SEC (Group 3)
    'MIZ': 3, 'MIZZ': 3, 'ALA': 3, 'BAMA': 3, 'ARK': 3, 'AUB': 3,
    'FLA': 3, 'UGA': 3, 'UK': 3, 'LSU': 3, 'MSST': 3, 'MISS': 3,
    'OM': 3, 'SCAR': 3, 'SC': 3, 'TENN': 3, 'TA&M': 3, 'TAMU': 3,
    'VAN': 3, 'VANDY': 3, 'TEX': 3, 'OU': 3, 'OKLA': 3,
    # ACC (Group 2)
    'BC': 2, 'CLEM': 2, 'DUKE': 2, 'FSU': 2, 'GT': 2, 'LOU': 2,
    'MIA': 2, 'UNC': 2, 'NCST': 2, 'ND': 2, 'PITT': 2, 'SYR': 2,
    'UVA': 2, 'VT': 2, 'WAKE': 2, 'CAL': 2, 'STAN': 2, 'SMU': 2,
    # BIG EAST (Group 4)
    'BUT': 4, 'CONN': 4, 'UCONN': 4, 'CREI': 4, 'DEP': 4, 'GTOWN': 4,
    'MARQ': 4, 'PROV': 4, 'SHU': 4, 'STJ': 4, 'NOVA': 4, 'XAV': 4,
}

CONFERENCE_NAMES = {
    7: "Big Ten", 8: "Big 12", 3: "SEC", 2: "ACC", 4: "Big East",
    9: "Pac-12", 62: "American", 44: "Mountain West", 50: "Division I"
}


def get_conferences_for_teams(favorite_teams: list) -> Set[int]:
    """Get ESPN conference group IDs for a list of favorite teams."""
    conferences = set()
    for team in favorite_teams:
        team_upper = team.upper()
        if team_upper in BASKETBALL_TEAM_CONFERENCES:
            conferences.add(BASKETBALL_TEAM_CONFERENCES[team_upper])
    return conferences


class BaseNCAAMBasketballManager(Basketball):
    """Base class for NCAA MB managers with common functionality."""

    # Class variables for warning tracking
    _no_data_warning_logged = False
    _last_warning_time = 0
    _warning_cooldown = 60  # Only log warnings once per minute
    _last_log_times = {}
    _shared_data = None
    _SHARED_DATA_MAX_AGE = 120  # Clear shared data after 2 minutes
    _last_shared_update = 0

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: DisplayManager,
        cache_manager: CacheManager,
    ):
        self.logger = logging.getLogger("NCAAMB")  # Changed logger name
        super().__init__(
            config=config,
            display_manager=display_manager,
            cache_manager=cache_manager,
            logger=self.logger,
            sport_key="ncaam_basketball",
        )

        # Check display modes to determine what data to fetch
        display_modes = self.mode_config.get("display_modes", {})
        self.recent_enabled = display_modes.get("ncaam_basketball_recent", False)
        self.upcoming_enabled = display_modes.get("ncaam_basketball_upcoming", False)
        self.live_enabled = display_modes.get("ncaam_basketball_live", False)

        self.logger.info(
            f"Initialized NCAA Mens Basketball manager with display dimensions: {self.display_width}x{self.display_height}"
        )
        self.logger.info(f"Logo directory: {self.logo_dir}")
        self.logger.info(
            f"Display modes - Recent: {self.recent_enabled}, Upcoming: {self.upcoming_enabled}, Live: {self.live_enabled}"
        )
        self.league = "mens-college-basketball"

    def _get_weeks_data(self) -> Optional[Dict]:
        """
        OVERRIDE: Fetch data using conference-based queries for reliability.
        
        ESPN's API with groups=50 (all D1) randomly misses games.
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
        
        conf_names = [CONFERENCE_NAMES.get(c, str(c)) for c in conferences]
        self.logger.info(f"Conference fetch for teams {self.favorite_teams}: {conf_names}")
        
        # Use same date range as base class: 2 weeks back, 1 week forward
        now = datetime.now(pytz.utc)
        start_date = now + timedelta(weeks=-2)
        end_date = now + timedelta(weeks=1)
        date_str = f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"
        
        all_events = []
        seen_event_ids = set()
        
        for conf_id in conferences:
            conf_name = CONFERENCE_NAMES.get(conf_id, f"Group {conf_id}")
            
            try:
                params = {
                    'dates': date_str,
                    'groups': conf_id,
                    'limit': 100  # Per-conference, 100 is plenty
                }
                
                response = self.session.get(
                    ESPN_NCAAMB_SCOREBOARD_URL,
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

    def _fetch_ncaam_basketball_api_data(
        self, use_cache: bool = True
    ) -> Optional[Dict]:
        """
        Fetches the full season schedule for NCAA Mens Basketball using background threading.
        Returns cached data immediately if available, otherwise starts background fetch.
        """
        now = datetime.now(pytz.utc)
        season_year = now.year
        if now.month < 8:
            season_year = now.year - 1
        start_date = (now - timedelta(days=28)).strftime("%Y%m%d")
        end_date = (now + timedelta(days=14)).strftime("%Y%m%d")
        datestring = f"{start_date}-{end_date}"
        cache_key = f"{self.sport_key}_schedule_{season_year}"

        # Check cache first
        if use_cache:
            cached_data = self.cache_manager.get(cache_key)
            if cached_data:
                # Validate cached data structure
                if isinstance(cached_data, dict) and "events" in cached_data:
                    self.logger.info(f"Using cached schedule for {season_year}")
                    return cached_data
                elif isinstance(cached_data, list):
                    # Handle old cache format (list of events)
                    self.logger.info(
                        f"Using cached schedule for {season_year} (legacy format)"
                    )
                    return {"events": cached_data}
                else:
                    self.logger.warning(
                        f"Invalid cached data format for {season_year}: {type(cached_data)}"
                    )
                    # Clear invalid cache
                    self.cache_manager.clear_cache(cache_key)

        # Start background fetch
        self.logger.info(
            f"Starting background fetch for {season_year} season schedule..."
        )

        def fetch_callback(result):
            """Callback when background fetch completes."""
            if result.success:
                self.logger.info(
                    f"Background fetch completed for {season_year}: {len(result.data.get('events'))} events"
                )
            else:
                self.logger.error(
                    f"Background fetch failed for {season_year}: {result.error}"
                )

            # Clean up request tracking
            if season_year in self.background_fetch_requests:
                del self.background_fetch_requests[season_year]

        # Get background service configuration
        background_config = self.mode_config.get("background_service", {})
        timeout = background_config.get("request_timeout", 30)
        max_retries = background_config.get("max_retries", 3)
        priority = background_config.get("priority", 2)

        # Calculate date range (5 days back, 7 forward - matching original)
        from_date = (now - timedelta(days=28)).strftime("%Y%m%d")
        to_date = (now + timedelta(days=14)).strftime("%Y%m%d")
        date_range = f"{from_date}-{to_date}"
        
        # Submit background fetch request
        request_id = self.background_service.submit_fetch_request(
            sport="ncaa_mens_basketball",
            year=season_year,
            url=ESPN_NCAAMB_SCOREBOARD_URL,
            cache_key=cache_key,
            params={"dates": date_range, "limit": 500, "groups": 50},  # Background still uses groups=50
            headers=self.headers,
            timeout=timeout,
            max_retries=max_retries,
            priority=priority,
            callback=fetch_callback,
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
        if isinstance(self, NCAAMBasketballLiveManager):
            # Live games should fetch only current games, not entire season
            return self._fetch_todays_games()
        else:
            # Recent and Upcoming managers should use cached season data
            return self._fetch_ncaam_basketball_api_data(use_cache=True)


class NCAAMBasketballLiveManager(BaseNCAAMBasketballManager, BasketballLive):
    """Manager for live NCAA MB games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: DisplayManager,
        cache_manager: CacheManager,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger(
            "NCAAMBasketballLiveManager"
        )  # Changed logger name

        if self.test_mode:
            # More detailed test game for NCAA MB
            self.current_game = {
                "id": "test001",
                "home_abbr": "AUB",
                "home_id": "123",
                "away_abbr": "GT",
                "away_id": "asdf",
                "home_score": "21",
                "away_score": "17",
                "period": 3,
                "period_text": "Q3",
                "clock": "5:24",
                "home_logo_path": Path(self.logo_dir, "AUB.png"),
                "away_logo_path": Path(self.logo_dir, "GT.png"),
                "is_live": True,
                "is_final": False,
                "is_upcoming": False,
                "is_halftime": False,
            }
            self.live_games = [self.current_game]
            self.logger.info(
                "Initialized NCAAMBasketballLiveManager with test game: GT vs AUB"
            )
        else:
            self.logger.info(" Initialized NCAAMBasketballLiveManager in live mode")


class NCAAMBasketballRecentManager(BaseNCAAMBasketballManager, SportsRecent):
    """Manager for recently completed NCAA MB games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: DisplayManager,
        cache_manager: CacheManager,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger(
            "NCAAMBasketballRecentManager"
        )  # Changed logger name
        self.logger.info(
            f"Initialized NCAAMBasketballRecentManager with {len(self.favorite_teams)} favorite teams"
        )


class NCAAMBasketballUpcomingManager(BaseNCAAMBasketballManager, SportsUpcoming):
    """Manager for upcoming NCAA MB games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: DisplayManager,
        cache_manager: CacheManager,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger(
            "NCAAMBasketballUpcomingManager"
        )  # Changed logger name
        self.logger.info(
            f"Initialized NCAAMBasketballUpcomingManager with {len(self.favorite_teams)} favorite teams"
        )
