"""
Team Database Utility for LEDMatrix
Provides centralized team data access across all sports displays

Created: November 7, 2025
Version: 1.0
Compatible with: Chuck's LEDMatrix system
"""

import json
import os
from typing import Dict, List, Optional


class TeamDatabase:
    """Central team database manager for all sports leagues"""
    
    def __init__(self, db_path: str = None):
        """
        Initialize team database
        
        Args:
            db_path: Path to JSON database file. If None, uses default location.
        """
        if db_path is None:
            # Default to config directory (relative to this file's location)
            current_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(current_dir, '../../config/core_sports_teams.json')
        
        self.db_path = os.path.abspath(db_path)
        self.data = self._load_database()
    
    def _load_database(self) -> Dict:
        """Load team database from JSON file"""
        try:
            with open(self.db_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"Warning: Team database not found at {self.db_path}")
            print("Please ensure core_sports_teams.json is in the config directory")
            return {}
        except json.JSONDecodeError as e:
            print(f"Error parsing team database: {e}")
            return {}
        except Exception as e:
            print(f"Unexpected error loading database: {e}")
            return {}
    
    def get_league_teams(self, league: str) -> List[Dict]:
        """
        Get all teams for a specific league
        
        Args:
            league: League abbreviation (e.g., 'nfl', 'nba', 'mlb')
        
        Returns:
            List of team dictionaries
        """
        league_lower = league.lower()
        if league_lower in self.data:
            return self.data[league_lower].get('teams', [])
        return []
    
    def get_team_by_id(self, league: str, team_id: str) -> Optional[Dict]:
        """
        Get specific team data by ID or abbreviation
        
        Args:
            league: League abbreviation
            team_id: Team ID or abbreviation (e.g., 'KC', 'LAL')
        
        Returns:
            Team dictionary or None if not found
        """
        teams = self.get_league_teams(league)
        team_id_upper = team_id.upper()
        
        for team in teams:
            if (team.get('id', '').upper() == team_id_upper or 
                team.get('abbreviation', '').upper() == team_id_upper):
                return team
        return None
    
    def get_team_by_espn_id(self, league: str, espn_id: str) -> Optional[Dict]:
        """
        Get team by ESPN API ID
        
        Args:
            league: League abbreviation
            espn_id: ESPN team ID (as string or int)
        
        Returns:
            Team dictionary or None if not found
        """
        teams = self.get_league_teams(league)
        for team in teams:
            if str(team.get('espn_team_id', '')) == str(espn_id):
                return team
        return None
    
    def get_team_by_name(self, league: str, name: str) -> Optional[Dict]:
        """
        Get team by full name or display name
        
        Args:
            league: League abbreviation
            name: Team name or display name
        
        Returns:
            Team dictionary or None if not found
        """
        teams = self.get_league_teams(league)
        name_lower = name.lower()
        
        for team in teams:
            if (name_lower in team.get('name', '').lower() or
                name_lower in team.get('display_name', '').lower()):
                return team
        return None
    
    def get_kc_favorites(self) -> List[Dict]:
        """
        Get Kansas City favorite teams across all leagues
        
        Returns:
            List of favorite team references
        """
        return self.data.get('kc_favorites', {}).get('teams', [])
    
    def get_kc_favorite_teams_full(self) -> List[Dict]:
        """
        Get complete team data for KC favorites
        
        Returns:
            List of full team dictionaries for KC favorites
        """
        favorites = self.get_kc_favorites()
        full_teams = []
        
        for fav in favorites:
            league = fav['league'].lower()
            team_id = fav['team_id']
            team = self.get_team_by_id(league, team_id)
            if team:
                team['_favorite_league'] = fav['league']
                full_teams.append(team)
        
        return full_teams
    
    def get_teams_by_conference(self, league: str, conference: str) -> List[Dict]:
        """
        Get teams by conference
        
        Args:
            league: League abbreviation
            conference: Conference name (e.g., 'Eastern', 'AFC', 'Western')
        
        Returns:
            List of team dictionaries in the conference
        """
        teams = self.get_league_teams(league)
        conference_lower = conference.lower()
        return [t for t in teams if t.get('conference', '').lower() == conference_lower]
    
    def get_teams_by_division(self, league: str, division: str, conference: str = None) -> List[Dict]:
        """
        Get teams by division (optionally filtered by conference)
        
        Args:
            league: League abbreviation
            division: Division name (e.g., 'West', 'Atlantic', 'Central')
            conference: Optional conference filter
        
        Returns:
            List of team dictionaries in the division
        """
        teams = self.get_league_teams(league)
        division_lower = division.lower()
        
        filtered = [t for t in teams if t.get('division', '').lower() == division_lower]
        
        if conference:
            conference_lower = conference.lower()
            filtered = [t for t in filtered if t.get('conference', '').lower() == conference_lower]
        
        return filtered
    
    def get_team_color(self, league: str, team_id: str, secondary: bool = False) -> str:
        """
        Get team color hex code
        
        Args:
            league: League abbreviation
            team_id: Team ID
            secondary: If True, return secondary color; else primary
        
        Returns:
            Hex color code (e.g., '#E31837') or '#FFFFFF' if not found
        """
        team = self.get_team_by_id(league, team_id)
        if team:
            color_key = 'secondary_color' if secondary else 'primary_color'
            return team.get(color_key, '#FFFFFF')
        return '#FFFFFF'
    
    def get_team_colors(self, league: str, team_id: str) -> tuple:
        """
        Get both team colors (primary, secondary)
        
        Args:
            league: League abbreviation
            team_id: Team ID
        
        Returns:
            Tuple of (primary_color, secondary_color) hex codes
        """
        team = self.get_team_by_id(league, team_id)
        if team:
            return (
                team.get('primary_color', '#FFFFFF'),
                team.get('secondary_color', '#000000')
            )
        return ('#FFFFFF', '#000000')
    
    def search_teams(self, query: str, league: str = None) -> List[Dict]:
        """
        Search teams by name, location, or abbreviation
        
        Args:
            query: Search string (case-insensitive)
            league: Optional league filter
        
        Returns:
            List of matching teams with league info added
        """
        query_lower = query.lower()
        results = []
        
        leagues_to_search = [league.lower()] if league else list(self.data.keys())
        
        for lg in leagues_to_search:
            if lg in ('_metadata', 'kc_favorites'):
                continue
            
            teams = self.get_league_teams(lg)
            for team in teams:
                if (query_lower in team.get('name', '').lower() or 
                    query_lower in team.get('location', '').lower() or
                    query_lower in team.get('display_name', '').lower() or
                    query_lower in team.get('abbreviation', '').lower()):
                    # Add league info to result
                    team_result = team.copy()
                    team_result['_search_league'] = lg.upper()
                    results.append(team_result)
        
        return results
    
    def get_leagues(self) -> List[str]:
        """
        Get list of available leagues
        
        Returns:
            List of league abbreviations
        """
        return [k.upper() for k in self.data.keys() 
                if k not in ('_metadata', 'kc_favorites')]
    
    def get_league_info(self, league: str) -> Dict:
        """
        Get league metadata
        
        Args:
            league: League abbreviation
        
        Returns:
            Dictionary with league_name, abbreviation, espn_league_id
        """
        league_lower = league.lower()
        if league_lower in self.data:
            league_data = self.data[league_lower]
            return {
                'league_name': league_data.get('league_name', ''),
                'abbreviation': league_data.get('abbreviation', ''),
                'espn_league_id': league_data.get('espn_league_id', ''),
                'team_count': len(league_data.get('teams', []))
            }
        return {}
    
    def get_database_stats(self) -> Dict:
        """
        Get statistics about the database
        
        Returns:
            Dictionary with counts and metadata
        """
        stats = {
            'leagues': [],
            'total_teams': 0,
            'kc_favorites_count': len(self.get_kc_favorites())
        }
        
        for league_key in self.data.keys():
            if league_key in ('_metadata', 'kc_favorites'):
                continue
            
            teams = self.get_league_teams(league_key)
            team_count = len(teams)
            stats['total_teams'] += team_count
            
            stats['leagues'].append({
                'id': league_key.upper(),
                'name': self.data[league_key].get('league_name', ''),
                'team_count': team_count
            })
        
        return stats
    
    def validate_team_id(self, league: str, team_id: str) -> bool:
        """
        Check if a team ID is valid for a league
        
        Args:
            league: League abbreviation
            team_id: Team ID to validate
        
        Returns:
            True if team exists, False otherwise
        """
        return self.get_team_by_id(league, team_id) is not None
    
    def hex_to_rgb(self, hex_color: str) -> tuple:
        """
        Convert hex color to RGB tuple
        
        Args:
            hex_color: Hex color code (e.g., '#E31837' or 'E31837')
        
        Returns:
            Tuple of (r, g, b) values (0-255)
        """
        hex_color = hex_color.lstrip('#')
        if len(hex_color) != 6:
            return (255, 255, 255)  # Default to white on invalid input
        
        try:
            return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        except ValueError:
            return (255, 255, 255)
    
    def get_team_color_rgb(self, league: str, team_id: str, secondary: bool = False) -> tuple:
        """
        Get team color as RGB tuple
        
        Args:
            league: League abbreviation
            team_id: Team ID
            secondary: If True, return secondary color; else primary
        
        Returns:
            Tuple of (r, g, b) values (0-255)
        """
        hex_color = self.get_team_color(league, team_id, secondary)
        return self.hex_to_rgb(hex_color)


# Global instance for easy import across the application
team_db = TeamDatabase()


# Convenience functions for common operations
def get_nfl_teams():
    """Quick access to NFL teams"""
    return team_db.get_league_teams('nfl')


def get_nba_teams():
    """Quick access to NBA teams"""
    return team_db.get_league_teams('nba')


def get_mlb_teams():
    """Quick access to MLB teams"""
    return team_db.get_league_teams('mlb')


def get_nhl_teams():
    """Quick access to NHL teams"""
    return team_db.get_league_teams('nhl')


def get_kc_favorites():
    """Quick access to KC favorite teams"""
    return team_db.get_kc_favorite_teams_full()


if __name__ == "__main__":
    # Self-test when run directly
    print("🧪 Team Database Self-Test\n")
    
    stats = team_db.get_database_stats()
    print(f"Total Teams: {stats['total_teams']}")
    print(f"Leagues: {len(stats['leagues'])}")
    print(f"KC Favorites: {stats['kc_favorites_count']}\n")
    
    for league in stats['leagues']:
        print(f"  {league['id']}: {league['team_count']} teams")
    
    print("\n✅ Database loaded successfully!")
