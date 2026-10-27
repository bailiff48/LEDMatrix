#!/usr/bin/env python3
"""
Dynamic Team Resolver for LEDMatrix

This module provides functionality to resolve dynamic team names like "AP_TOP_25"
into actual team abbreviations that update automatically with rankings.

Supported dynamic teams:
- AP_TOP_25 / AP_TOP_10 / AP_TOP_5: current AP poll for the calling sport
  (NCAA football, men's basketball or women's basketball)
- CFP_TOP_12 / CFP_TOP_25: College Football Playoff rankings (football only)

Usage:
    resolver = DynamicTeamResolver()
    resolved_teams = resolver.resolve_teams(["UGA", "AP_TOP_25", "AUB"])
    # Returns: ["UGA", "UGA", "AUB", "MICH", "OSU", ...] (AP_TOP_25 teams)
"""

import logging
import time
import requests
from typing import Dict, List, Set, Optional, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

try:
    from src.rankings_service import RankingsService
except ImportError:  # running from inside src/
    try:
        from rankings_service import RankingsService
    except ImportError:
        RankingsService = None

# Sports that have an ESPN poll. Tokens in any other sport's favorites are ignored.
RANKED_SPORTS = {'ncaa_fb', 'ncaam_basketball', 'ncaaw_basketball'}

class DynamicTeamResolver:
    """
    Resolves dynamic team names to actual team abbreviations.
    
    This class handles special team names that represent dynamic groups
    like AP Top 25 rankings, which update automatically.
    """
    
    # Cache for rankings data
    _rankings_cache: Dict[str, List[str]] = {}
    _cache_timestamp: float = 0
    _cache_duration: int = 3600  # 1 hour cache
    
    # Supported dynamic team patterns
    # 'poll' is matched against ESPN's ranking names by RankingsService.
    # The sport comes from the manager calling resolve_teams(). Previously
    # every sport (including basketball) got the FOOTBALL poll.
    DYNAMIC_PATTERNS = {
        'AP_TOP_25': {'poll': 'ap', 'limit': 25},
        'AP_TOP_10': {'poll': 'ap', 'limit': 10},
        'AP_TOP_5': {'poll': 'ap', 'limit': 5},
        'CFP_TOP_12': {'poll': 'cfp', 'limit': 12},
        'CFP_TOP_25': {'poll': 'cfp', 'limit': 25},
    }
    
    def __init__(self, request_timeout: int = 30):
        """Initialize the dynamic team resolver."""
        self.request_timeout = request_timeout
        self.logger = logger
        
    def resolve_teams(self, team_list: List[str], sport: str = 'ncaa_fb') -> List[str]:
        """
        Resolve a list of team names, expanding dynamic team names.
        
        Args:
            team_list: List of team names (can include dynamic names like "AP_TOP_25")
            sport: Sport type for context (default: 'ncaa_fb')
            
        Returns:
            List of resolved team abbreviations
        """
        if not team_list:
            return []
            
        resolved_teams = []
        
        for team in team_list:
            if team in self.DYNAMIC_PATTERNS:
                # Resolve dynamic team
                dynamic_teams = self._resolve_dynamic_team(team, sport)
                resolved_teams.extend(dynamic_teams)
                self.logger.info(f"Resolved {team} to {len(dynamic_teams)} teams: {dynamic_teams[:5]}{'...' if len(dynamic_teams) > 5 else ''}")
            elif self._is_potential_dynamic_team(team):
                # Unknown dynamic team, skip it
                self.logger.warning(f"Unknown dynamic team '{team}' - skipping")
            else:
                # Regular team name, add as-is
                resolved_teams.append(team)
                
        # Remove duplicates while preserving order
        seen = set()
        unique_teams = []
        for team in resolved_teams:
            if team not in seen:
                seen.add(team)
                unique_teams.append(team)
                
        return unique_teams
    
    def _resolve_dynamic_team(self, dynamic_team: str, sport: str) -> List[str]:
        """
        Resolve a dynamic team name to actual team abbreviations.
        
        Args:
            dynamic_team: Dynamic team name (e.g., "AP_TOP_25")
            sport: Sport type for context
            
        Returns:
            List of team abbreviations
        """
        if dynamic_team not in self.DYNAMIC_PATTERNS:
            self.logger.warning(f"Unknown dynamic team: {dynamic_team}")
            return []
            
        pattern_config = self.DYNAMIC_PATTERNS[dynamic_team]
        poll = pattern_config['poll']
        limit = pattern_config['limit']

        if sport not in RANKED_SPORTS:
            self.logger.warning(f"Dynamic team {dynamic_team} not supported for sport {sport}")
            return []

        if RankingsService is not None:
            top_teams = RankingsService.get_ranked_teams(sport, poll=poll, top_n=limit)
        elif sport == 'ncaa_fb':
            # Legacy fallback (football only)
            top_teams = list(self._fetch_ncaa_fb_rankings().keys())[:limit]
        else:
            top_teams = []

        if not top_teams:
            self.logger.warning(f"Could not fetch rankings for {dynamic_team} ({sport})")
            return []

        self.logger.info(f"Resolved {dynamic_team} for {sport} to top {len(top_teams)} teams: {top_teams}")
        return top_teams
    
    def _fetch_ncaa_fb_rankings(self) -> Dict[str, int]:
        """
        Fetch current NCAA Football rankings from ESPN API.
        
        Returns:
            Dictionary mapping team abbreviations to rankings
        """
        current_time = time.time()
        
        # Check cache first
        if (self._rankings_cache and 
            current_time - self._cache_timestamp < self._cache_duration):
            return self._rankings_cache
            
        try:
            self.logger.info("Fetching fresh NCAA Football rankings from ESPN API")
            rankings_url = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/rankings"
            
            response = requests.get(rankings_url, timeout=self.request_timeout)
            response.raise_for_status()
            data = response.json()
            
            rankings = {}
            rankings_data = data.get('rankings', [])
            
            if rankings_data:
                # Use the first ranking (usually AP Top 25)
                first_ranking = rankings_data[0]
                ranking_name = first_ranking.get('name', 'Unknown')
                teams = first_ranking.get('ranks', [])
                
                self.logger.info(f"Using ranking: {ranking_name}")
                self.logger.info(f"Found {len(teams)} teams in ranking")
                
                for team_data in teams:
                    team_info = team_data.get('team', {})
                    team_abbr = team_info.get('abbreviation', '')
                    current_rank = team_data.get('current', 0)
                    
                    if team_abbr and current_rank > 0:
                        rankings[team_abbr] = current_rank
                
                # Sort by ranking (1, 2, 3, etc.)
                sorted_rankings = dict(sorted(rankings.items(), key=lambda x: x[1]))
                
                # Cache the results
                self._rankings_cache = sorted_rankings
                self._cache_timestamp = current_time
                
                self.logger.info(f"Fetched rankings for {len(sorted_rankings)} teams")
                return sorted_rankings
                
        except Exception as e:
            self.logger.error(f"Error fetching NCAA Football rankings: {e}")
            
        return {}
    
    def get_available_dynamic_teams(self) -> List[str]:
        """
        Get list of available dynamic team names.
        
        Returns:
            List of supported dynamic team names
        """
        return list(self.DYNAMIC_PATTERNS.keys())
    
    def is_dynamic_team(self, team_name: str) -> bool:
        """
        Check if a team name is a dynamic team.
        
        Args:
            team_name: Team name to check
            
        Returns:
            True if the team name is dynamic
        """
        return team_name in self.DYNAMIC_PATTERNS
    
    def _is_potential_dynamic_team(self, team_name: str) -> bool:
        """
        Check if a team name looks like it might be a dynamic team but isn't recognized.
        
        Args:
            team_name: Team name to check
            
        Returns:
            True if the team name looks like a dynamic team pattern
        """
        # Check for common dynamic team patterns
        dynamic_patterns = ['AP_TOP_', 'TOP_', 'RANKED_', 'PLAYOFF_']
        return any(pattern in team_name.upper() for pattern in dynamic_patterns)
    
    def clear_cache(self):
        """Clear the rankings cache to force fresh data on next request."""
        self._rankings_cache = {}
        self._cache_timestamp = 0
        self.logger.info("Cleared dynamic team rankings cache")


# Convenience function for easy integration
def resolve_dynamic_teams(team_list: List[str], sport: str = 'ncaa_fb') -> List[str]:
    """
    Convenience function to resolve dynamic teams in a team list.
    
    Args:
        team_list: List of team names (can include dynamic names)
        sport: Sport type for context
        
    Returns:
        List of resolved team abbreviations
    """
    resolver = DynamicTeamResolver()
    return resolver.resolve_teams(team_list, sport)
