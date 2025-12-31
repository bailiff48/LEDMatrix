"""
Rankings Service for LED Matrix Sports Ticker
==============================================
Fetches and caches AP Top 25 / CFP rankings from ESPN API.

This module is designed to be self-contained for easy plugin extraction later.
It handles its own caching and configuration without depending on core services.

Supported Sports:
- NCAA Football (AP Poll, CFP Rankings)
- NCAA Men's Basketball (AP Poll)
- NCAA Women's Basketball (AP Poll)

Usage:
    from rankings_service import RankingsService
    
    # Get AP Top 25 team abbreviations for football
    top_25 = RankingsService.get_ranked_teams('ncaa_fb', poll='ap', top_n=25)
    # Returns: ['OSU', 'TEX', 'OREG', ...]
    
    # Expand favorite_teams list that may contain "AP_TOP_25"
    expanded = RankingsService.expand_favorite_teams(['UGA', 'AP_TOP_25'], 'ncaa_fb')
    # Returns: ['UGA', 'OSU', 'TEX', 'OREG', ...] (UGA + all Top 25)

Author: Bailey (github.com/bailiff48/LEDMatrix)
Date: December 2024
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set
import requests

logger = logging.getLogger(__name__)

# ESPN Rankings API Endpoints
ESPN_RANKINGS_URLS = {
    'ncaa_fb': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/rankings',
    'ncaam_basketball': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/rankings',
    'ncaaw_basketball': 'https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/rankings',
}

# Map poll names to ESPN ranking types
POLL_TYPE_MAP = {
    'ap': ['AP Top 25', 'AP Poll'],
    'cfp': ['Playoff Committee Rankings', 'CFP Rankings'],
    'coaches': ['Coaches Poll', 'USA Today Coaches Poll'],
}

# Special tokens that can appear in favorite_teams
RANKING_TOKENS = {
    'AP_TOP_25': {'poll': 'ap', 'top_n': 25},
    'AP_TOP_10': {'poll': 'ap', 'top_n': 10},
    'CFP_TOP_12': {'poll': 'cfp', 'top_n': 12},
    'CFP_TOP_25': {'poll': 'cfp', 'top_n': 25},
}

# Cache settings
DEFAULT_CACHE_DURATION = 3600  # 1 hour - rankings don't change often
CACHE_DIR = Path('/var/cache/ledmatrix/rankings')
FALLBACK_CACHE_DIR = Path('/tmp/ledmatrix_rankings_cache')


class RankingsCache:
    """Simple file-based cache for rankings data."""
    
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or self._get_cache_dir()
        self._ensure_cache_dir()
    
    def _get_cache_dir(self) -> Path:
        """Get appropriate cache directory with fallback."""
        if CACHE_DIR.exists() or self._try_create_dir(CACHE_DIR):
            return CACHE_DIR
        logger.warning(f"Cannot use {CACHE_DIR}, falling back to {FALLBACK_CACHE_DIR}")
        self._try_create_dir(FALLBACK_CACHE_DIR)
        return FALLBACK_CACHE_DIR
    
    def _try_create_dir(self, path: Path) -> bool:
        """Try to create directory, return success."""
        try:
            path.mkdir(parents=True, exist_ok=True)
            return True
        except (OSError, PermissionError):
            return False
    
    def _ensure_cache_dir(self):
        """Ensure cache directory exists."""
        self._try_create_dir(self.cache_dir)
    
    def _get_cache_path(self, sport: str, poll: str) -> Path:
        """Get cache file path for a sport/poll combination."""
        return self.cache_dir / f"rankings_{sport}_{poll}.json"
    
    def get(self, sport: str, poll: str, max_age: int = DEFAULT_CACHE_DURATION) -> Optional[Dict]:
        """Get cached rankings if fresh enough."""
        cache_path = self._get_cache_path(sport, poll)
        
        if not cache_path.exists():
            return None
        
        try:
            with open(cache_path, 'r') as f:
                cached = json.load(f)
            
            # Check age
            cached_time = cached.get('timestamp', 0)
            if time.time() - cached_time > max_age:
                logger.debug(f"Rankings cache expired for {sport}/{poll}")
                return None
            
            return cached.get('data')
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error reading rankings cache: {e}")
            return None
    
    def set(self, sport: str, poll: str, data: Dict):
        """Cache rankings data."""
        cache_path = self._get_cache_path(sport, poll)
        
        try:
            cache_data = {
                'timestamp': time.time(),
                'sport': sport,
                'poll': poll,
                'data': data
            }
            with open(cache_path, 'w') as f:
                json.dump(cache_data, f)
            logger.debug(f"Cached rankings for {sport}/{poll}")
        except IOError as e:
            logger.warning(f"Error writing rankings cache: {e}")


class RankingsService:
    """
    Service for fetching and managing sports rankings.
    
    This is implemented as a class with class methods to act as a singleton
    without explicit instantiation. Cache is shared across all calls.
    """
    
    _cache = RankingsCache()
    _rankings_data: Dict[str, Dict] = {}  # In-memory cache for current session
    
    @classmethod
    def fetch_rankings(cls, sport: str, force_refresh: bool = False) -> Optional[Dict]:
        """
        Fetch rankings from ESPN API.
        
        Args:
            sport: One of 'ncaa_fb', 'ncaam_basketball', 'ncaaw_basketball'
            force_refresh: If True, bypass cache and fetch fresh data
            
        Returns:
            Raw rankings data from ESPN or None if fetch fails
        """
        if sport not in ESPN_RANKINGS_URLS:
            logger.error(f"Unknown sport for rankings: {sport}")
            return None
        
        # Check in-memory cache first (fastest)
        cache_key = f"{sport}_raw"
        if not force_refresh and cache_key in cls._rankings_data:
            return cls._rankings_data[cache_key]
        
        # Check file cache
        if not force_refresh:
            cached = cls._cache.get(sport, 'raw')
            if cached:
                cls._rankings_data[cache_key] = cached
                return cached
        
        # Fetch from ESPN
        url = ESPN_RANKINGS_URLS[sport]
        try:
            logger.info(f"Fetching rankings for {sport} from ESPN")
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            # Cache it
            cls._rankings_data[cache_key] = data
            cls._cache.set(sport, 'raw', data)
            
            return data
        except requests.RequestException as e:
            logger.error(f"Error fetching rankings for {sport}: {e}")
            return None
    
    @classmethod
    def get_ranked_teams(
        cls,
        sport: str,
        poll: str = 'ap',
        top_n: int = 25,
        force_refresh: bool = False
    ) -> List[str]:
        """
        Get list of ranked team abbreviations.
        
        Args:
            sport: One of 'ncaa_fb', 'ncaam_basketball', 'ncaaw_basketball'
            poll: Poll type - 'ap', 'cfp', or 'coaches'
            top_n: Number of teams to return (default 25)
            force_refresh: If True, bypass cache
            
        Returns:
            List of team abbreviations in rank order, e.g. ['OSU', 'TEX', 'OREG']
        """
        # Check processed cache first
        cache_key = f"{sport}_{poll}_{top_n}"
        if not force_refresh and cache_key in cls._rankings_data:
            return cls._rankings_data[cache_key]
        
        # Fetch raw data
        raw_data = cls.fetch_rankings(sport, force_refresh)
        if not raw_data:
            logger.warning(f"No rankings data available for {sport}")
            return []
        
        # Find the right poll
        rankings_list = raw_data.get('rankings', [])
        target_poll = None
        poll_names = POLL_TYPE_MAP.get(poll, [poll])
        
        for ranking in rankings_list:
            ranking_name = ranking.get('name', '')
            ranking_short = ranking.get('shortName', '')
            
            for poll_name in poll_names:
                if poll_name.lower() in ranking_name.lower() or poll_name.lower() in ranking_short.lower():
                    target_poll = ranking
                    break
            if target_poll:
                break
        
        if not target_poll:
            # Fallback: use first available ranking
            if rankings_list:
                logger.warning(f"Poll '{poll}' not found for {sport}, using first available: {rankings_list[0].get('name')}")
                target_poll = rankings_list[0]
            else:
                logger.warning(f"No rankings found for {sport}")
                return []
        
        # Extract team abbreviations
        teams = []
        ranks = target_poll.get('ranks', [])
        
        for rank_entry in ranks[:top_n]:
            team = rank_entry.get('team', {})
            abbrev = team.get('abbreviation', '')
            if abbrev:
                teams.append(abbrev)
        
        # Cache processed result
        cls._rankings_data[cache_key] = teams
        logger.info(f"Got {len(teams)} ranked teams for {sport}/{poll}: {teams[:5]}...")
        
        return teams
    
    @classmethod
    def expand_favorite_teams(
        cls,
        favorite_teams: List[str],
        sport: str,
        force_refresh: bool = False
    ) -> List[str]:
        """
        Expand a favorite_teams list, replacing ranking tokens with actual teams.
        
        This is the main method managers should call when processing their
        favorite_teams configuration.
        
        Args:
            favorite_teams: List that may contain team abbreviations and/or
                           special tokens like 'AP_TOP_25', 'CFP_TOP_12'
            sport: Sport identifier for looking up rankings
            force_refresh: If True, fetch fresh rankings
            
        Returns:
            Expanded list with tokens replaced by actual team abbreviations.
            Preserves order, with explicit teams first, then ranked teams.
            Removes duplicates while preserving first occurrence.
            
        Example:
            expand_favorite_teams(['UGA', 'AP_TOP_25'], 'ncaa_fb')
            -> ['UGA', 'OSU', 'TEX', 'OREG', ...] 
               (UGA stays first since explicitly listed, plus all Top 25)
        """
        if not favorite_teams:
            return []
        
        result = []
        seen: Set[str] = set()
        
        for item in favorite_teams:
            item_upper = item.upper() if isinstance(item, str) else str(item).upper()
            
            if item_upper in RANKING_TOKENS:
                # This is a ranking token - expand it
                token_config = RANKING_TOKENS[item_upper]
                ranked_teams = cls.get_ranked_teams(
                    sport,
                    poll=token_config['poll'],
                    top_n=token_config['top_n'],
                    force_refresh=force_refresh
                )
                
                for team in ranked_teams:
                    if team not in seen:
                        seen.add(team)
                        result.append(team)
                        
            else:
                # Regular team abbreviation
                if item_upper not in seen:
                    seen.add(item_upper)
                    result.append(item_upper)
        
        return result
    
    @classmethod
    def is_ranking_token(cls, value: str) -> bool:
        """Check if a value is a ranking token."""
        return value.upper() in RANKING_TOKENS if isinstance(value, str) else False
    
    @classmethod
    def get_available_polls(cls, sport: str) -> List[Dict]:
        """
        Get list of available polls for a sport.
        
        Returns:
            List of dicts with 'name', 'shortName', 'type' for each poll
        """
        raw_data = cls.fetch_rankings(sport)
        if not raw_data:
            return []
        
        polls = []
        for ranking in raw_data.get('rankings', []):
            polls.append({
                'name': ranking.get('name', ''),
                'shortName': ranking.get('shortName', ''),
                'type': ranking.get('type', ''),
            })
        
        return polls
    
    @classmethod
    def clear_cache(cls):
        """Clear all cached rankings data."""
        cls._rankings_data.clear()
        logger.info("Rankings cache cleared")


# Convenience functions for direct import
def get_ranked_teams(sport: str, poll: str = 'ap', top_n: int = 25) -> List[str]:
    """Convenience wrapper for RankingsService.get_ranked_teams()"""
    return RankingsService.get_ranked_teams(sport, poll, top_n)


def expand_favorite_teams(favorite_teams: List[str], sport: str) -> List[str]:
    """Convenience wrapper for RankingsService.expand_favorite_teams()"""
    return RankingsService.expand_favorite_teams(favorite_teams, sport)


# Self-test when run directly
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 60)
    print("Rankings Service Test")
    print("=" * 60)
    
    # Test each sport
    for sport in ESPN_RANKINGS_URLS.keys():
        print(f"\n--- {sport} ---")
        
        # Get available polls
        polls = RankingsService.get_available_polls(sport)
        print(f"Available polls: {[p['shortName'] for p in polls]}")
        
        # Get AP Top 25
        teams = RankingsService.get_ranked_teams(sport, poll='ap', top_n=10)
        print(f"AP Top 10: {teams}")
        
        if sport == 'ncaa_fb':
            # Also test CFP for football
            cfp_teams = RankingsService.get_ranked_teams(sport, poll='cfp', top_n=12)
            print(f"CFP Top 12: {cfp_teams}")
    
    # Test expansion
    print("\n--- Token Expansion Test ---")
    test_favorites = ['UGA', 'AP_TOP_25']
    expanded = RankingsService.expand_favorite_teams(test_favorites, 'ncaa_fb')
    print(f"Input: {test_favorites}")
    print(f"Expanded: {expanded[:10]}... ({len(expanded)} total)")
    
    # Verify UGA is still first if it wasn't in Top 25
    if 'UGA' in expanded:
        print(f"UGA position: {expanded.index('UGA') + 1}")
    
    print("\n✓ Rankings service test complete")
