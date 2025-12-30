"""
NCAA Soccer Managers for LEDMatrix Sports Ticker
Handles NCAA Men's and Women's Soccer scoreboards

This module follows the same architecture as soccer_managers.py but is specifically
designed for NCAA Division I Soccer with separate configs for men's and women's leagues.

PLUGIN METADATA:
- Name: NCAA Soccer
- Version: 1.0.0
- Author: Bailey (LEDMatrix fork)
- Dependencies: display_manager, cache_manager, config_manager
- Config Keys: ncaam_soccer_scoreboard, ncaaw_soccer_scoreboard
- ESPN Leagues: usa.ncaa.m.1, usa.ncaa.w.1

PLUGIN REGISTRATION (for future plugin system):
To convert to plugin, extract this file + config section + display_controller registration
"""

import os
import time
import logging
import requests
import json
from typing import Dict, Any, Optional, List
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from datetime import datetime, timedelta, timezone
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager
from src.config_manager import ConfigManager
from src.odds_manager import OddsManager
from src.logo_downloader import download_missing_logo, get_soccer_league_key
from src.background_data_service import get_background_service
import pytz

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    def increment_api_counter(kind: str, count: int = 1):
        pass

# =============================================================================
# PLUGIN INFO - Can be extracted for plugin registration system
# =============================================================================
PLUGIN_INFO = {
    "name": "NCAA Soccer",
    "version": "1.0.0",
    "description": "NCAA Division I Men's and Women's Soccer scoreboards",
    "author": "Bailey",
    "sport_keys": ["ncaam_soccer", "ncaaw_soccer"],
    "config_keys": ["ncaam_soccer_scoreboard", "ncaaw_soccer_scoreboard"],
    "espn_leagues": {
        "ncaam_soccer": "usa.ncaa.m.1",
        "ncaaw_soccer": "usa.ncaa.w.1"
    },
    "display_modes": {
        "ncaam_soccer": ["ncaam_soccer_live", "ncaam_soccer_recent", "ncaam_soccer_upcoming"],
        "ncaaw_soccer": ["ncaaw_soccer_live", "ncaaw_soccer_recent", "ncaaw_soccer_upcoming"]
    }
}

# =============================================================================
# DEFAULT CONFIG - Can be merged into config.json or extracted for plugin
# =============================================================================
DEFAULT_CONFIG = {
    "ncaam_soccer_scoreboard": {
        "enabled": False,
        "league": "usa.ncaa.m.1",
        "live_priority": True,
        "live_game_duration": 30,
        "show_odds": False,
        "test_mode": False,
        "update_interval_seconds": 3600,
        "live_update_interval": 30,
        "recent_update_interval": 3600,
        "upcoming_update_interval": 3600,
        "recent_games_to_show": 1,
        "upcoming_games_to_show": 1,
        "show_favorite_teams_only": True,
        "show_all_live": False,
        "favorite_teams": [],
        "logo_dir": "assets/sports/ncaa_soccer_logos",
        "show_records": True,
        "display_modes": {
            "ncaam_soccer_live": True,
            "ncaam_soccer_recent": True,
            "ncaam_soccer_upcoming": True
        }
    },
    "ncaaw_soccer_scoreboard": {
        "enabled": False,
        "league": "usa.ncaa.w.1",
        "live_priority": True,
        "live_game_duration": 30,
        "show_odds": False,
        "test_mode": False,
        "update_interval_seconds": 3600,
        "live_update_interval": 30,
        "recent_update_interval": 3600,
        "upcoming_update_interval": 3600,
        "recent_games_to_show": 1,
        "upcoming_games_to_show": 1,
        "show_favorite_teams_only": True,
        "show_all_live": False,
        "favorite_teams": [],
        "logo_dir": "assets/sports/ncaa_soccer_logos",
        "show_records": True,
        "display_modes": {
            "ncaaw_soccer_live": True,
            "ncaaw_soccer_recent": True,
            "ncaaw_soccer_upcoming": True
        }
    }
}

# Display duration defaults (to add to config display_durations section)
DEFAULT_DISPLAY_DURATIONS = {
    "ncaam_soccer_live": 30,
    "ncaam_soccer_recent": 30,
    "ncaam_soccer_upcoming": 30,
    "ncaaw_soccer_live": 30,
    "ncaaw_soccer_recent": 30,
    "ncaaw_soccer_upcoming": 30
}

# =============================================================================
# CONSTANTS
# =============================================================================
ESPN_SOCCER_SCOREBOARD_URL = "http://site.api.espn.com/apis/site/v2/sports/soccer/{}/scoreboard"

LEAGUE_DISPLAY_NAMES = {
    "usa.ncaa.m.1": "NCAA Men's Soccer",
    "usa.ncaa.w.1": "NCAA Women's Soccer"
}

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(levelname)s:%(name)s:%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


# =============================================================================
# BASE NCAA SOCCER MANAGER
# =============================================================================
class BaseNCCASoccerManager:
    """
    Base class for NCAA Soccer managers with common functionality.
    
    This class handles:
    - ESPN API data fetching for a single NCAA soccer league
    - Game data extraction and filtering
    - Logo loading and caching
    - Display rendering (scorebug layout)
    """
    
    # Class-level shared data per league
    _shared_data = {}
    _last_shared_update = {}
    _warning_cooldown = 60
    _last_warning_time = 0
    
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, 
                 cache_manager: CacheManager, sport_key: str):
        """
        Initialize NCAA Soccer manager.
        
        Args:
            config: Full application config dict
            display_manager: Display manager instance
            cache_manager: Cache manager instance
            sport_key: Either 'ncaam_soccer' or 'ncaaw_soccer'
        """
        self.display_manager = display_manager
        self.config = config
        self.cache_manager = cache_manager
        self.sport_key = sport_key
        
        # Get sport-specific config
        config_key = f"{sport_key}_scoreboard"
        self.soccer_config = config.get(config_key, DEFAULT_CONFIG.get(config_key, {}))
        
        # Core settings
        self.is_enabled = self.soccer_config.get("enabled", False)
        self.show_odds = self.soccer_config.get("show_odds", False)
        self.test_mode = self.soccer_config.get("test_mode", False)
        self.logo_dir = self.soccer_config.get("logo_dir", "assets/sports/ncaa_soccer_logos")
        self.update_interval = self.soccer_config.get("update_interval_seconds", 3600)
        self.show_records = self.soccer_config.get("show_records", False)
        
        # ESPN league for this manager
        self.espn_league = self.soccer_config.get("league", PLUGIN_INFO["espn_leagues"].get(sport_key))
        self.league_name = LEAGUE_DISPLAY_NAMES.get(self.espn_league, "NCAA Soccer")
        
        # Game filtering
        self.favorite_teams = self.soccer_config.get("favorite_teams", [])
        self.show_favorite_teams_only = self.soccer_config.get("show_favorite_teams_only", False)
        self.recent_games_to_show = self.soccer_config.get("recent_games_to_show", 5)
        self.upcoming_games_to_show = self.soccer_config.get("upcoming_games_to_show", 5)
        self.upcoming_fetch_days = self.soccer_config.get("upcoming_fetch_days", 7)
        
        # Display dimensions
        self.display_width = self.display_manager.matrix.width
        self.display_height = self.display_manager.matrix.height
        
        # State
        self.last_update = 0
        self.current_game = None
        self._logo_cache = {}
        
        # Load fonts
        self.fonts = self._load_fonts()
        
        # Initialize odds manager
        self.odds_manager = OddsManager(self.cache_manager, None)
        
        # Initialize background data service
        self.background_service = get_background_service(self.cache_manager, max_workers=1)
        
        # Anti-spoiler support (from top-level config)
        self.anti_spoiler_teams = config.get("anti_spoiler_teams", [])
        self.anti_spoiler_delay_hours = config.get("anti_spoiler_delay_hours", 48)
        
        self.logger.info(f"[{self.league_name}] Initialized manager")
        self.logger.info(f"[{self.league_name}] ESPN League: {self.espn_league}")
        self.logger.info(f"[{self.league_name}] Favorite teams: {self.favorite_teams}")
        self.logger.info(f"[{self.league_name}] Show favorites only: {self.show_favorite_teams_only}")

    def _get_timezone(self):
        """Get configured timezone."""
        try:
            timezone_str = self.config.get('timezone', 'UTC')
            return pytz.timezone(timezone_str)
        except pytz.UnknownTimeZoneError:
            self.logger.warning(f"[{self.league_name}] Unknown timezone, falling back to UTC")
            return pytz.utc

    def _load_fonts(self) -> Dict[str, ImageFont.FreeTypeFont]:
        """Load fonts for display rendering."""
        fonts = {}
        try:
            fonts['score'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 10)
            fonts['time'] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
            fonts['team'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
            fonts['status'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
            self.logger.debug(f"[{self.league_name}] Loaded custom fonts")
        except IOError:
            self.logger.warning(f"[{self.league_name}] Custom fonts not found, using default")
            fonts['score'] = ImageFont.load_default()
            fonts['time'] = ImageFont.load_default()
            fonts['team'] = ImageFont.load_default()
            fonts['status'] = ImageFont.load_default()
        return fonts

    def _fetch_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Fetch scoreboard data from ESPN API for this manager's league.
        
        Args:
            use_cache: Whether to use cached data if available
            
        Returns:
            Dict with 'events' key containing game data, or None on error
        """
        today = datetime.now(pytz.utc).date()
        start_date = (today - timedelta(days=21)).strftime('%Y%m%d')
        end_date = (today + timedelta(days=self.upcoming_fetch_days)).strftime('%Y%m%d')
        date_range = f"{start_date}-{end_date}"
        
        cache_key = f"ncaa_soccer_{self.espn_league}_{start_date}_{end_date}"
        
        # Check cache first
        if use_cache:
            cached_data = self.cache_manager.get(cache_key, max_age=300)
            if cached_data:
                self.logger.debug(f"[{self.league_name}] Using cached data")
                return cached_data
        
        # Fetch from ESPN
        try:
            url = ESPN_SOCCER_SCOREBOARD_URL.format(self.espn_league)
            params = {'dates': date_range, 'limit': 100}
            
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            increment_api_counter('sports', 1)
            self.logger.info(f"[{self.league_name}] Fetched {len(data.get('events', []))} events from ESPN")
            
            # Cache the response
            if use_cache:
                self.cache_manager.set(cache_key, data)
            
            return data
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"[{self.league_name}] API error: {e}")
            return None
        except Exception as e:
            self.logger.error(f"[{self.league_name}] Unexpected error: {e}", exc_info=True)
            return None

    def _extract_game_details(self, event: Dict) -> Optional[Dict]:
        """
        Extract game details from ESPN event data.
        
        Args:
            event: ESPN event dict
            
        Returns:
            Normalized game dict or None if extraction fails
        """
        try:
            competition = event.get("competitions", [{}])[0]
            competitors = competition.get("competitors", [])
            status = competition.get("status", {})
            
            if len(competitors) < 2:
                return None
            
            # Find home and away teams
            home_team = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away_team = next((c for c in competitors if c.get("homeAway") == "away"), None)
            
            if not home_team or not away_team:
                return None
            
            # Extract team info
            home_abbr = home_team.get("team", {}).get("abbreviation", "UNK")
            away_abbr = away_team.get("team", {}).get("abbreviation", "UNK")
            home_name = home_team.get("team", {}).get("shortDisplayName", home_abbr)
            away_name = away_team.get("team", {}).get("shortDisplayName", away_abbr)
            home_score = home_team.get("score", "0")
            away_score = away_team.get("score", "0")
            
            # Extract records
            home_record = ""
            away_record = ""
            if self.show_records:
                home_records = home_team.get("records", [])
                away_records = away_team.get("records", [])
                if home_records:
                    home_record = home_records[0].get("summary", "")
                if away_records:
                    away_record = away_records[0].get("summary", "")
            
            # Status info
            status_type = status.get("type", {})
            state = status_type.get("state", "pre")
            is_live = state == "in"
            is_final = status_type.get("completed", False)
            is_upcoming = state == "pre"
            
            # Game time/clock
            game_clock = status.get("displayClock", "")
            period = status.get("period", 0)
            status_detail = status_type.get("shortDetail", "")
            
            # Parse game date/time
            game_date_str = event.get("date", "")
            start_time_utc = None
            game_time = ""
            game_date = ""
            
            if game_date_str:
                try:
                    start_time_utc = datetime.fromisoformat(game_date_str.replace("Z", "+00:00"))
                    local_time = start_time_utc.astimezone(self._get_timezone())
                    game_time = local_time.strftime("%I:%M%p").lstrip('0')
                    game_date = local_time.strftime("%-m/%-d")
                except ValueError:
                    pass
            
            # Build clock display
            if is_live:
                if period == 1:
                    game_clock_display = f"1H {game_clock}"
                elif period == 2:
                    game_clock_display = f"2H {game_clock}"
                else:
                    game_clock_display = status_detail
            elif is_final:
                game_clock_display = "FINAL"
            else:
                game_clock_display = game_time
            
            # Get league info
            league_info = event.get("league", {})
            league_slug = league_info.get("slug", self.espn_league)
            
            return {
                "id": event.get("id", ""),
                "home_abbr": home_abbr,
                "away_abbr": away_abbr,
                "home_name": home_name,
                "away_name": away_name,
                "home_score": str(home_score) if home_score else "0",
                "away_score": str(away_score) if away_score else "0",
                "home_record": home_record,
                "away_record": away_record,
                "is_live": is_live,
                "is_final": is_final,
                "is_upcoming": is_upcoming,
                "game_clock": game_clock,
                "game_clock_display": game_clock_display,
                "period": period,
                "status_detail": status_detail,
                "game_time": game_time,
                "game_date": game_date,
                "start_time_utc": start_time_utc,
                "league": LEAGUE_DISPLAY_NAMES.get(league_slug, league_slug),
                "league_slug": league_slug,
                "sport_key": self.sport_key
            }
            
        except Exception as e:
            self.logger.error(f"[{self.league_name}] Error extracting game details: {e}")
            return None

    def _is_favorite_game(self, game: Dict) -> bool:
        """Check if game involves a favorite team."""
        if not self.show_favorite_teams_only:
            return True
        if not self.favorite_teams:
            return True
        return (game.get("home_abbr") in self.favorite_teams or 
                game.get("away_abbr") in self.favorite_teams)

    def _should_hide_score(self, game: Dict) -> bool:
        """Check if score should be hidden for anti-spoiler."""
        if not self.anti_spoiler_teams:
            return False
        
        home = game.get("home_abbr", "")
        away = game.get("away_abbr", "")
        
        # Check if either team is in anti-spoiler list
        if home not in self.anti_spoiler_teams and away not in self.anti_spoiler_teams:
            return False
        
        # Check if game is within spoiler window
        start_time = game.get("start_time_utc")
        if not start_time:
            return False
        
        now = datetime.now(pytz.utc)
        spoiler_window = timedelta(hours=self.anti_spoiler_delay_hours)
        
        return now < start_time + spoiler_window

    def _fetch_odds(self, game: Dict) -> None:
        """Fetch and attach odds to game dict."""
        if not self.show_odds:
            return
        try:
            odds_data = self.odds_manager.get_odds(
                sport="soccer",
                league=self.espn_league,
                event_id=game.get("id"),
                update_interval_seconds=3600
            )
            if odds_data:
                game["odds"] = odds_data
        except Exception as e:
            self.logger.debug(f"[{self.league_name}] Could not fetch odds: {e}")

    def _load_and_resize_logo(self, team_abbrev: str) -> Optional[Image.Image]:
        """Load and cache team logo."""
        if team_abbrev in self._logo_cache:
            return self._logo_cache[team_abbrev]
        
        # Try to find logo
        logo_path = os.path.join(self.logo_dir, f"{team_abbrev}.png")
        
        if not os.path.exists(logo_path):
            # Try case-insensitive search
            try:
                for filename in os.listdir(self.logo_dir):
                    if filename.lower() == f"{team_abbrev.lower()}.png":
                        logo_path = os.path.join(self.logo_dir, filename)
                        break
            except FileNotFoundError:
                pass
        
        if not os.path.exists(logo_path):
            # Try to download logo
            try:
                league_key = get_soccer_league_key(self.espn_league)
                download_missing_logo(team_abbrev, self.logo_dir, league_key)
                if os.path.exists(logo_path):
                    self.logger.info(f"[{self.league_name}] Downloaded logo for {team_abbrev}")
            except Exception as e:
                self.logger.debug(f"[{self.league_name}] Could not download logo for {team_abbrev}: {e}")
                return None
        
        if not os.path.exists(logo_path):
            return None
        
        try:
            logo = Image.open(logo_path)
            if logo.mode != 'RGBA':
                logo = logo.convert('RGBA')
            
            # Resize to fit display
            max_size = min(self.display_height - 4, 28)
            logo.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            
            # Cache it
            if len(self._logo_cache) >= 50:
                # Remove oldest entry
                oldest = next(iter(self._logo_cache))
                self._logo_cache.pop(oldest)
            
            self._logo_cache[team_abbrev] = logo
            return logo
            
        except Exception as e:
            self.logger.error(f"[{self.league_name}] Error loading logo {team_abbrev}: {e}")
            return None

    def _draw_text_with_outline(self, draw: ImageDraw.Draw, text: str, position: tuple,
                                 font: ImageFont.FreeTypeFont, fill=(255, 255, 255),
                                 outline_color=(0, 0, 0)):
        """Draw text with outline for readability."""
        x, y = position
        for dx, dy in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        draw.text((x, y), text, font=font, fill=fill)

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """
        Draw the scorebug display for a game.
        
        Layout: [Away Logo] AWAY score - score HOME [Home Logo]
                         status/time display
        """
        try:
            img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            
            # Get game data
            away_abbr = game.get("away_abbr", "???")
            home_abbr = game.get("home_abbr", "???")
            away_score = game.get("away_score", "0")
            home_score = game.get("home_score", "0")
            status_text = game.get("game_clock_display", "")
            
            # Check anti-spoiler
            if self._should_hide_score(game):
                away_score = "-"
                home_score = "-"
            
            # Load logos
            away_logo = self._load_and_resize_logo(away_abbr)
            home_logo = self._load_and_resize_logo(home_abbr)
            
            # Layout calculations
            logo_size = 24
            center_x = self.display_width // 2
            
            # Draw away logo (left side)
            if away_logo:
                img.paste(away_logo, (2, 4), away_logo if away_logo.mode == 'RGBA' else None)
            
            # Draw home logo (right side)
            if home_logo:
                home_x = self.display_width - logo_size - 2
                img.paste(home_logo, (home_x, 4), home_logo if home_logo.mode == 'RGBA' else None)
            
            # Draw team abbreviations
            self._draw_text_with_outline(draw, away_abbr[:4], (2, 0), self.fonts['team'])
            home_abbr_width = len(home_abbr[:4]) * 4
            self._draw_text_with_outline(draw, home_abbr[:4], 
                                         (self.display_width - home_abbr_width - 2, 0),
                                         self.fonts['team'])
            
            # Draw scores in center
            score_text = f"{away_score} - {home_score}"
            score_width = len(score_text) * 6  # Approximate width
            score_x = center_x - score_width // 2
            self._draw_text_with_outline(draw, score_text, (score_x, 10), self.fonts['score'])
            
            # Draw status below scores
            status_width = len(status_text) * 4
            status_x = center_x - status_width // 2
            self._draw_text_with_outline(draw, status_text, (status_x, 24), self.fonts['status'])
            
            # Update display
            self.display_manager.set_image(img)
            
        except Exception as e:
            self.logger.error(f"[{self.league_name}] Error drawing scorebug: {e}", exc_info=True)

    def display(self, force_clear: bool = False) -> None:
        """Display current game."""
        if self.current_game:
            self._draw_scorebug_layout(self.current_game, force_clear)


# =============================================================================
# LIVE MANAGER
# =============================================================================
class NCAAMSoccerLiveManager(BaseNCCASoccerManager):
    """Manager for live NCAA Men's Soccer games."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager, sport_key="ncaam_soccer")
        self.live_games = []
        self.games_list = []  # For compatibility with display controller
        self.current_game_index = 0
        self.last_game_switch = 0
        self.game_display_duration = self.soccer_config.get("live_game_duration", 30)
        self.update_interval = self.soccer_config.get("live_update_interval", 30)
        self.logger.info(f"[{self.league_name}] Live manager initialized (update: {self.update_interval}s)")

    def update(self):
        """Update live games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
        
        self.last_update = current_time
        
        try:
            # Always fetch fresh data for live games
            data = self._fetch_api_data(use_cache=False)
            if not data or 'events' not in data:
                self.live_games = []
                self.games_list = []
                self.current_game = None
                return
            
            # Find live games
            new_live_games = []
            for event in data['events']:
                game = self._extract_game_details(event)
                if game and game['is_live']:
                    if self._is_favorite_game(game) or self.soccer_config.get("show_all_live", False):
                        self._fetch_odds(game)
                        new_live_games.append(game)
            
            # Update game list
            if new_live_games:
                new_live_games.sort(key=lambda x: x['start_time_utc'] or datetime.now(pytz.utc))
                new_game_ids = {g['id'] for g in new_live_games}
                current_game_ids = {g['id'] for g in self.live_games}
                
                if new_game_ids != current_game_ids:
                    self.live_games = new_live_games
                    self.games_list = new_live_games
                    
                    if not self.current_game or self.current_game['id'] not in new_game_ids:
                        self.current_game_index = 0
                        self.current_game = self.live_games[0] if self.live_games else None
                        self.last_game_switch = current_time
                else:
                    # Update existing games with fresh data
                    for i, game in enumerate(self.live_games):
                        updated = next((g for g in new_live_games if g['id'] == game['id']), None)
                        if updated:
                            self.live_games[i] = updated
                            if self.current_game and self.current_game['id'] == updated['id']:
                                self.current_game = updated
                    self.games_list = self.live_games
                
                self.logger.info(f"[{self.league_name}] {len(new_live_games)} live games")
            else:
                if self.live_games:
                    self.logger.info(f"[{self.league_name}] No more live games")
                self.live_games = []
                self.games_list = []
                self.current_game = None
        
        except Exception as e:
            self.logger.error(f"[{self.league_name}] Error updating live games: {e}", exc_info=True)

    def display(self, force_clear: bool = False):
        """Display live game, rotating if multiple."""
        if not self.live_games:
            return
        
        current_time = time.time()
        
        # Rotate through games
        if len(self.live_games) > 1 and (current_time - self.last_game_switch) >= self.game_display_duration:
            self.current_game_index = (self.current_game_index + 1) % len(self.live_games)
            self.current_game = self.live_games[self.current_game_index]
            self.last_game_switch = current_time
            force_clear = True
        
        if not self.current_game and self.live_games:
            self.current_game = self.live_games[0]
        
        if self.current_game:
            self._draw_scorebug_layout(self.current_game, force_clear)


class NCAAWSoccerLiveManager(BaseNCCASoccerManager):
    """Manager for live NCAA Women's Soccer games."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager, sport_key="ncaaw_soccer")
        self.live_games = []
        self.games_list = []
        self.current_game_index = 0
        self.last_game_switch = 0
        self.game_display_duration = self.soccer_config.get("live_game_duration", 30)
        self.update_interval = self.soccer_config.get("live_update_interval", 30)
        self.logger.info(f"[{self.league_name}] Live manager initialized (update: {self.update_interval}s)")

    def update(self):
        """Update live games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
        
        self.last_update = current_time
        
        try:
            data = self._fetch_api_data(use_cache=False)
            if not data or 'events' not in data:
                self.live_games = []
                self.games_list = []
                self.current_game = None
                return
            
            new_live_games = []
            for event in data['events']:
                game = self._extract_game_details(event)
                if game and game['is_live']:
                    if self._is_favorite_game(game) or self.soccer_config.get("show_all_live", False):
                        self._fetch_odds(game)
                        new_live_games.append(game)
            
            if new_live_games:
                new_live_games.sort(key=lambda x: x['start_time_utc'] or datetime.now(pytz.utc))
                new_game_ids = {g['id'] for g in new_live_games}
                current_game_ids = {g['id'] for g in self.live_games}
                
                if new_game_ids != current_game_ids:
                    self.live_games = new_live_games
                    self.games_list = new_live_games
                    
                    if not self.current_game or self.current_game['id'] not in new_game_ids:
                        self.current_game_index = 0
                        self.current_game = self.live_games[0] if self.live_games else None
                        self.last_game_switch = current_time
                else:
                    for i, game in enumerate(self.live_games):
                        updated = next((g for g in new_live_games if g['id'] == game['id']), None)
                        if updated:
                            self.live_games[i] = updated
                            if self.current_game and self.current_game['id'] == updated['id']:
                                self.current_game = updated
                    self.games_list = self.live_games
                
                self.logger.info(f"[{self.league_name}] {len(new_live_games)} live games")
            else:
                if self.live_games:
                    self.logger.info(f"[{self.league_name}] No more live games")
                self.live_games = []
                self.games_list = []
                self.current_game = None
        
        except Exception as e:
            self.logger.error(f"[{self.league_name}] Error updating live games: {e}", exc_info=True)

    def display(self, force_clear: bool = False):
        """Display live game, rotating if multiple."""
        if not self.live_games:
            return
        
        current_time = time.time()
        
        if len(self.live_games) > 1 and (current_time - self.last_game_switch) >= self.game_display_duration:
            self.current_game_index = (self.current_game_index + 1) % len(self.live_games)
            self.current_game = self.live_games[self.current_game_index]
            self.last_game_switch = current_time
            force_clear = True
        
        if not self.current_game and self.live_games:
            self.current_game = self.live_games[0]
        
        if self.current_game:
            self._draw_scorebug_layout(self.current_game, force_clear)


# =============================================================================
# RECENT MANAGER
# =============================================================================
class NCAAMSoccerRecentManager(BaseNCCASoccerManager):
    """Manager for recently completed NCAA Men's Soccer games."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager, sport_key="ncaam_soccer")
        self.recent_games = []
        self.games_list = []
        self.current_game_index = 0
        self.last_game_switch = 0
        self.game_display_duration = 5
        self.update_interval = self.soccer_config.get("recent_update_interval", 3600)
        self.logger.info(f"[{self.league_name}] Recent manager initialized (update: {self.update_interval}s)")

    def update(self):
        """Update recent games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
        
        self.last_update = current_time
        
        try:
            data = self._fetch_api_data(use_cache=True)
            if not data or 'events' not in data:
                self.recent_games = []
                self.games_list = []
                self.current_game = None
                return
            
            new_recent_games = []
            for event in data['events']:
                game = self._extract_game_details(event)
                if game and game['is_final'] and game.get('start_time_utc'):
                    if self._is_favorite_game(game):
                        self._fetch_odds(game)
                        new_recent_games.append(game)
            
            # Sort by time (most recent first) and limit
            new_recent_games.sort(key=lambda x: x['start_time_utc'], reverse=True)
            
            if self.show_favorite_teams_only and self.favorite_teams:
                # One game per favorite team
                team_games = []
                seen_teams = set()
                for game in new_recent_games:
                    for team in [game['home_abbr'], game['away_abbr']]:
                        if team in self.favorite_teams and team not in seen_teams:
                            team_games.append(game)
                            seen_teams.add(team)
                            break
                new_recent_games = team_games[:self.recent_games_to_show]
            else:
                new_recent_games = new_recent_games[:self.recent_games_to_show]
            
            # Update only if changed
            new_ids = {g['id'] for g in new_recent_games}
            current_ids = {g['id'] for g in self.games_list}
            
            if new_ids != current_ids:
                self.recent_games = new_recent_games
                self.games_list = new_recent_games
                
                if not self.current_game or self.current_game['id'] not in new_ids:
                    self.current_game_index = 0
                    self.current_game = self.games_list[0] if self.games_list else None
                    self.last_game_switch = current_time
                
                self.logger.info(f"[{self.league_name}] {len(new_recent_games)} recent games")
        
        except Exception as e:
            self.logger.error(f"[{self.league_name}] Error updating recent games: {e}", exc_info=True)

    def display(self, force_clear: bool = False):
        """Display recent games, rotating through list."""
        if not self.games_list:
            return
        
        current_time = time.time()
        
        if len(self.games_list) > 1 and (current_time - self.last_game_switch) >= self.game_display_duration:
            self.current_game_index = (self.current_game_index + 1) % len(self.games_list)
            self.current_game = self.games_list[self.current_game_index]
            self.last_game_switch = current_time
            force_clear = True
        
        if not self.current_game and self.games_list:
            self.current_game = self.games_list[0]
        
        if self.current_game:
            self._draw_scorebug_layout(self.current_game, force_clear)


class NCAAWSoccerRecentManager(BaseNCCASoccerManager):
    """Manager for recently completed NCAA Women's Soccer games."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager, sport_key="ncaaw_soccer")
        self.recent_games = []
        self.games_list = []
        self.current_game_index = 0
        self.last_game_switch = 0
        self.game_display_duration = 5
        self.update_interval = self.soccer_config.get("recent_update_interval", 3600)
        self.logger.info(f"[{self.league_name}] Recent manager initialized (update: {self.update_interval}s)")

    def update(self):
        """Update recent games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
        
        self.last_update = current_time
        
        try:
            data = self._fetch_api_data(use_cache=True)
            if not data or 'events' not in data:
                self.recent_games = []
                self.games_list = []
                self.current_game = None
                return
            
            new_recent_games = []
            for event in data['events']:
                game = self._extract_game_details(event)
                if game and game['is_final'] and game.get('start_time_utc'):
                    if self._is_favorite_game(game):
                        self._fetch_odds(game)
                        new_recent_games.append(game)
            
            new_recent_games.sort(key=lambda x: x['start_time_utc'], reverse=True)
            
            if self.show_favorite_teams_only and self.favorite_teams:
                team_games = []
                seen_teams = set()
                for game in new_recent_games:
                    for team in [game['home_abbr'], game['away_abbr']]:
                        if team in self.favorite_teams and team not in seen_teams:
                            team_games.append(game)
                            seen_teams.add(team)
                            break
                new_recent_games = team_games[:self.recent_games_to_show]
            else:
                new_recent_games = new_recent_games[:self.recent_games_to_show]
            
            new_ids = {g['id'] for g in new_recent_games}
            current_ids = {g['id'] for g in self.games_list}
            
            if new_ids != current_ids:
                self.recent_games = new_recent_games
                self.games_list = new_recent_games
                
                if not self.current_game or self.current_game['id'] not in new_ids:
                    self.current_game_index = 0
                    self.current_game = self.games_list[0] if self.games_list else None
                    self.last_game_switch = current_time
                
                self.logger.info(f"[{self.league_name}] {len(new_recent_games)} recent games")
        
        except Exception as e:
            self.logger.error(f"[{self.league_name}] Error updating recent games: {e}", exc_info=True)

    def display(self, force_clear: bool = False):
        """Display recent games, rotating through list."""
        if not self.games_list:
            return
        
        current_time = time.time()
        
        if len(self.games_list) > 1 and (current_time - self.last_game_switch) >= self.game_display_duration:
            self.current_game_index = (self.current_game_index + 1) % len(self.games_list)
            self.current_game = self.games_list[self.current_game_index]
            self.last_game_switch = current_time
            force_clear = True
        
        if not self.current_game and self.games_list:
            self.current_game = self.games_list[0]
        
        if self.current_game:
            self._draw_scorebug_layout(self.current_game, force_clear)


# =============================================================================
# UPCOMING MANAGER
# =============================================================================
class NCAAMSoccerUpcomingManager(BaseNCCASoccerManager):
    """Manager for upcoming NCAA Men's Soccer games."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager, sport_key="ncaam_soccer")
        self.upcoming_games = []
        self.games_list = []
        self.current_game_index = 0
        self.last_game_switch = 0
        self.game_display_duration = 5
        self.update_interval = self.soccer_config.get("upcoming_update_interval", 3600)
        self.logger.info(f"[{self.league_name}] Upcoming manager initialized (update: {self.update_interval}s)")

    def update(self):
        """Update upcoming games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
        
        self.last_update = current_time
        
        try:
            data = self._fetch_api_data(use_cache=True)
            if not data or 'events' not in data:
                self.upcoming_games = []
                self.games_list = []
                self.current_game = None
                return
            
            new_upcoming_games = []
            now_utc = datetime.now(pytz.utc)
            
            for event in data['events']:
                game = self._extract_game_details(event)
                if game and game['is_upcoming'] and game.get('start_time_utc'):
                    if game['start_time_utc'] >= now_utc and self._is_favorite_game(game):
                        self._fetch_odds(game)
                        new_upcoming_games.append(game)
            
            # Sort by time (soonest first) and limit
            new_upcoming_games.sort(key=lambda x: x['start_time_utc'])
            
            if self.show_favorite_teams_only and self.favorite_teams:
                team_games = []
                seen_teams = set()
                for game in new_upcoming_games:
                    for team in [game['home_abbr'], game['away_abbr']]:
                        if team in self.favorite_teams and team not in seen_teams:
                            team_games.append(game)
                            seen_teams.add(team)
                            break
                new_upcoming_games = team_games[:self.upcoming_games_to_show]
            else:
                new_upcoming_games = new_upcoming_games[:self.upcoming_games_to_show]
            
            new_ids = {g['id'] for g in new_upcoming_games}
            current_ids = {g['id'] for g in self.games_list}
            
            if new_ids != current_ids:
                self.upcoming_games = new_upcoming_games
                self.games_list = new_upcoming_games
                
                if not self.current_game or self.current_game['id'] not in new_ids:
                    self.current_game_index = 0
                    self.current_game = self.games_list[0] if self.games_list else None
                    self.last_game_switch = current_time
                
                self.logger.info(f"[{self.league_name}] {len(new_upcoming_games)} upcoming games")
        
        except Exception as e:
            self.logger.error(f"[{self.league_name}] Error updating upcoming games: {e}", exc_info=True)

    def display(self, force_clear: bool = False):
        """Display upcoming games, rotating through list."""
        if not self.games_list:
            return
        
        current_time = time.time()
        
        if len(self.games_list) > 1 and (current_time - self.last_game_switch) >= self.game_display_duration:
            self.current_game_index = (self.current_game_index + 1) % len(self.games_list)
            self.current_game = self.games_list[self.current_game_index]
            self.last_game_switch = current_time
            force_clear = True
        
        if not self.current_game and self.games_list:
            self.current_game = self.games_list[0]
        
        if self.current_game:
            self._draw_scorebug_layout(self.current_game, force_clear)


class NCAAWSoccerUpcomingManager(BaseNCCASoccerManager):
    """Manager for upcoming NCAA Women's Soccer games."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager, sport_key="ncaaw_soccer")
        self.upcoming_games = []
        self.games_list = []
        self.current_game_index = 0
        self.last_game_switch = 0
        self.game_display_duration = 5
        self.update_interval = self.soccer_config.get("upcoming_update_interval", 3600)
        self.logger.info(f"[{self.league_name}] Upcoming manager initialized (update: {self.update_interval}s)")

    def update(self):
        """Update upcoming games data."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
        
        self.last_update = current_time
        
        try:
            data = self._fetch_api_data(use_cache=True)
            if not data or 'events' not in data:
                self.upcoming_games = []
                self.games_list = []
                self.current_game = None
                return
            
            new_upcoming_games = []
            now_utc = datetime.now(pytz.utc)
            
            for event in data['events']:
                game = self._extract_game_details(event)
                if game and game['is_upcoming'] and game.get('start_time_utc'):
                    if game['start_time_utc'] >= now_utc and self._is_favorite_game(game):
                        self._fetch_odds(game)
                        new_upcoming_games.append(game)
            
            new_upcoming_games.sort(key=lambda x: x['start_time_utc'])
            
            if self.show_favorite_teams_only and self.favorite_teams:
                team_games = []
                seen_teams = set()
                for game in new_upcoming_games:
                    for team in [game['home_abbr'], game['away_abbr']]:
                        if team in self.favorite_teams and team not in seen_teams:
                            team_games.append(game)
                            seen_teams.add(team)
                            break
                new_upcoming_games = team_games[:self.upcoming_games_to_show]
            else:
                new_upcoming_games = new_upcoming_games[:self.upcoming_games_to_show]
            
            new_ids = {g['id'] for g in new_upcoming_games}
            current_ids = {g['id'] for g in self.games_list}
            
            if new_ids != current_ids:
                self.upcoming_games = new_upcoming_games
                self.games_list = new_upcoming_games
                
                if not self.current_game or self.current_game['id'] not in new_ids:
                    self.current_game_index = 0
                    self.current_game = self.games_list[0] if self.games_list else None
                    self.last_game_switch = current_time
                
                self.logger.info(f"[{self.league_name}] {len(new_upcoming_games)} upcoming games")
        
        except Exception as e:
            self.logger.error(f"[{self.league_name}] Error updating upcoming games: {e}", exc_info=True)

    def display(self, force_clear: bool = False):
        """Display upcoming games, rotating through list."""
        if not self.games_list:
            return
        
        current_time = time.time()
        
        if len(self.games_list) > 1 and (current_time - self.last_game_switch) >= self.game_display_duration:
            self.current_game_index = (self.current_game_index + 1) % len(self.games_list)
            self.current_game = self.games_list[self.current_game_index]
            self.last_game_switch = current_time
            force_clear = True
        
        if not self.current_game and self.games_list:
            self.current_game = self.games_list[0]
        
        if self.current_game:
            self._draw_scorebug_layout(self.current_game, force_clear)


# =============================================================================
# PLUGIN FACTORY FUNCTION
# =============================================================================
def get_managers() -> Dict[str, type]:
    """
    Factory function for plugin registration.
    
    Returns dict mapping mode names to manager classes.
    Used by future plugin system to auto-register managers.
    """
    return {
        "ncaam_soccer_live": NCAAMSoccerLiveManager,
        "ncaam_soccer_recent": NCAAMSoccerRecentManager,
        "ncaam_soccer_upcoming": NCAAMSoccerUpcomingManager,
        "ncaaw_soccer_live": NCAAWSoccerLiveManager,
        "ncaaw_soccer_recent": NCAAWSoccerRecentManager,
        "ncaaw_soccer_upcoming": NCAAWSoccerUpcomingManager,
    }


def get_default_config() -> Dict[str, Any]:
    """
    Get default configuration for NCAA Soccer.
    
    Used by plugin system to merge defaults into main config.
    """
    return DEFAULT_CONFIG


def get_display_durations() -> Dict[str, int]:
    """
    Get default display durations for NCAA Soccer modes.
    
    Used by plugin system to add to display_durations config.
    """
    return DEFAULT_DISPLAY_DURATIONS
