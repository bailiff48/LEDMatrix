"""
Soccer Team Fetcher for LEDMatrix Team Selector
Fetches soccer teams from ESPN API for all configured leagues
"""

import requests
import logging
import json
import time
from typing import Dict, List, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# League slug to display name mapping (matches soccer_managers.py)
LEAGUE_DISPLAY_NAMES = {
    "usa.1": "MLS",
    "usa.nwsl": "NWSL",
    "usa.ncaa.m.1": "Men's NCAA Soccer",
    "usa.ncaa.w.1": "Women's NCAA Soccer",
    "eng.1": "Premier League",
    "esp.1": "La Liga",
    "ger.1": "Bundesliga",
    "ita.1": "Serie A",
    "fra.1": "Ligue 1",
    "uefa.champions": "Champions League",
    "uefa.europa": "Europa League",
    "por.1": "Liga Portugal",
}

# League categorization for UI display
LEAGUE_CATEGORIES = {
    "US Professional": ["usa.1", "usa.nwsl"],
    "NCAA": ["usa.ncaa.m.1", "usa.ncaa.w.1"],
    "Europe - Top 5": ["eng.1", "esp.1", "ger.1", "ita.1", "fra.1"],
    "European Cups": ["uefa.champions", "uefa.europa"],
    "Other European": ["por.1"],
}

class SoccerTeamFetcher:
    """Fetches soccer teams from ESPN API with caching."""
    
    def __init__(self, cache_hours: int = 24):
        """Initialize with cache duration in hours."""
        self.cache_hours = cache_hours
        self.cache = {}
        self.cache_timestamps = {}
        
    def _is_cache_valid(self, league_slug: str) -> bool:
        """Check if cached data is still valid."""
        if league_slug not in self.cache_timestamps:
            return False
        
        cache_time = self.cache_timestamps[league_slug]
        age = time.time() - cache_time
        max_age = self.cache_hours * 3600
        
        return age < max_age
    
    def fetch_league_teams(self, league_slug: str) -> List[Dict[str, Any]]:
        """Fetch teams for a specific league from ESPN API."""
        
        # Check cache first
        if self._is_cache_valid(league_slug):
            logger.info(f"Using cached teams for {league_slug}")
            return self.cache.get(league_slug, [])
        
        # Fetch from ESPN
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_slug}/teams"
        
        try:
            logger.info(f"Fetching soccer teams from ESPN: {league_slug}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            teams = []
            sports_data = data.get('sports', [])
            
            for sport in sports_data:
                leagues = sport.get('leagues', [])
                for league in leagues:
                    league_teams = league.get('teams', [])
                    
                    for team_data in league_teams:
                        team = team_data.get('team', {})
                        
                        # Extract team info
                        team_info = {
                            'id': team.get('id', ''),
                            'abbreviation': team.get('abbreviation', ''),
                            'display_name': team.get('displayName', ''),
                            'short_name': team.get('shortDisplayName', team.get('displayName', '')),
                            'name': team.get('name', ''),
                            'location': team.get('location', ''),
                            'league': league_slug,
                            'league_name': LEAGUE_DISPLAY_NAMES.get(league_slug, league_slug),
                        }
                        
                        # Add logo if available
                        if team.get('logos'):
                            team_info['logo'] = team['logos'][0].get('href', '')
                        
                        teams.append(team_info)
            
            # Cache the results
            self.cache[league_slug] = teams
            self.cache_timestamps[league_slug] = time.time()
            
            logger.info(f"Fetched {len(teams)} teams for {league_slug}")
            return teams
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching teams for {league_slug}: {e}")
            # Return cached data if available, even if expired
            return self.cache.get(league_slug, [])
        except Exception as e:
            logger.error(f"Unexpected error fetching teams for {league_slug}: {e}")
            return self.cache.get(league_slug, [])
    
    def fetch_all_leagues(self, league_slugs: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Fetch teams for all specified leagues.
        Returns data structured by category for UI display.
        """
        all_teams = {}
        
        # Fetch teams for each league
        for league_slug in league_slugs:
            teams = self.fetch_league_teams(league_slug)
            if teams:
                all_teams[league_slug] = {
                    'league_slug': league_slug,
                    'league_name': LEAGUE_DISPLAY_NAMES.get(league_slug, league_slug),
                    'teams': teams,
                    'count': len(teams)
                }
        
        return all_teams
    
    def get_structured_data(self, league_slugs: List[str]) -> Dict[str, Any]:
        """
        Get soccer teams structured by category for team selector UI.
        Returns format compatible with existing team selector.
        """
        all_leagues = self.fetch_all_leagues(league_slugs)
        
        # Structure by category
        structured = {
            '_metadata': {
                'total_leagues': len(all_leagues),
                'total_teams': sum(data['count'] for data in all_leagues.values()),
                'last_updated': datetime.now().isoformat(),
            }
        }
        
        # Add categorized leagues
        for category, slugs in LEAGUE_CATEGORIES.items():
            category_data = {
                'category': category,
                'leagues': {}
            }
            
            for slug in slugs:
                if slug in all_leagues:
                    category_data['leagues'][slug] = all_leagues[slug]
            
            if category_data['leagues']:
                # Use category as key (replace spaces/dashes for JS compatibility)
                category_key = category.lower().replace(' ', '_').replace('-', '_')
                structured[category_key] = category_data
        
        return structured

# Global instance
_fetcher = None

def get_soccer_fetcher() -> SoccerTeamFetcher:
    """Get or create the global soccer team fetcher instance."""
    global _fetcher
    if _fetcher is None:
        _fetcher = SoccerTeamFetcher(cache_hours=24)
    return _fetcher


def fetch_soccer_teams_for_selector(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main function to fetch soccer teams for team selector.
    Uses config to determine which leagues to fetch.
    """
    soccer_config = config.get('soccer_scoreboard', {})
    enabled_leagues = soccer_config.get('leagues', list(LEAGUE_DISPLAY_NAMES.keys()))
    
    fetcher = get_soccer_fetcher()
    return fetcher.get_structured_data(enabled_leagues)


# Example usage for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Test with a few leagues
    test_leagues = ["usa.1", "usa.nwsl", "eng.1"]
    fetcher = SoccerTeamFetcher()
    data = fetcher.get_structured_data(test_leagues)
    
    print(json.dumps(data, indent=2))
