#!/usr/bin/env python3
"""
Centralized logo downloader utility for automatically fetching team logos from ESPN API.
This module provides functionality to download missing team logos for various sports leagues,
with special support for FCS teams and other NCAA divisions.

FIXED VERSION: Changed all LOGO_DIRECTORIES paths from relative to absolute paths
to fix permission denied errors when running as systemd service.
"""

import os
import time
import logging
import requests
import json
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Cache to prevent repeated download attempts for the same logo
_failed_download_cache = {}
_FAILED_CACHE_DURATION = 300  # Don't retry failed downloads for 5 minutes

class LogoDownloader:
    """Centralized logo downloader for team logos from ESPN API."""
    
    # ESPN API endpoints for different sports/leagues
    API_ENDPOINTS = {
        'nfl': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams',
        'nba': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams',
        'mlb': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams',
        'nhl': 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams',
        'ncaa_fb': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams',
        'ncaa_fb_all': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams',  # Includes FCS
        'fcs': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams',  # FCS teams from same endpoint
        'ncaam_basketball': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams',
        'ncaa_baseball': 'https://site.api.espn.com/apis/site/v2/sports/baseball/college-baseball/teams',
        'ncaam_hockey': 'https://site.api.espn.com/apis/site/v2/sports/hockey/mens-college-hockey/teams',
        # Soccer leagues
        'soccer_eng.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/teams',
        'soccer_esp.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/teams',
        'soccer_ger.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/ger.1/teams',
        'soccer_ita.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1/teams',
        'soccer_fra.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/fra.1/teams',
        'soccer_por.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/por.1/teams',
        'soccer_uefa.champions': 'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/teams',
        'soccer_uefa.europa': 'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.europa/teams',
        'soccer_usa.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/teams',
        # NEW: US Professional and NCAA Soccer (3 new leagues)
        'soccer_usa.nwsl': 'https://site.api.espn.com/apis/site/v2/sports/soccer/usa.nwsl/teams',
        'soccer_usa.ncaa.m.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/usa.ncaa.m.1/teams',
        'soccer_usa.ncaa.w.1': 'https://site.api.espn.com/apis/site/v2/sports/soccer/usa.ncaa.w.1/teams'
    }
    
    # Directory mappings for different leagues
    # FIXED: Changed from relative paths to absolute paths to fix systemd service permission issues
    LOGO_DIRECTORIES = {
        'nfl': '/home/ledpi/LEDMatrix/assets/sports/nfl_logos',
        'nba': '/home/ledpi/LEDMatrix/assets/sports/nba_logos', 
        'wnba': '/home/ledpi/LEDMatrix/assets/sports/wnba_logos', 
        'mlb': '/home/ledpi/LEDMatrix/assets/sports/mlb_logos',
        'nhl': '/home/ledpi/LEDMatrix/assets/sports/nhl_logos',
        # NCAA sports use same directory
        'ncaa_fb': '/home/ledpi/LEDMatrix/assets/sports/ncaa_logos',
        'ncaa_fb_all': '/home/ledpi/LEDMatrix/assets/sports/ncaa_logos',
        'fcs': '/home/ledpi/LEDMatrix/assets/sports/ncaa_logos',
        'ncaam_basketball': '/home/ledpi/LEDMatrix/assets/sports/ncaa_logos',
        'ncaaw_basketball': '/home/ledpi/LEDMatrix/assets/sports/ncaa_logos',
        'ncaa_baseball': '/home/ledpi/LEDMatrix/assets/sports/ncaa_logos',
        'ncaam_hockey': '/home/ledpi/LEDMatrix/assets/sports/ncaa_logos',
        'ncaaw_hockey': '/home/ledpi/LEDMatrix/assets/sports/ncaa_logos',
        # Soccer leagues - all use the same soccer_logos directory
        'soccer_eng.1': '/home/ledpi/LEDMatrix/assets/sports/soccer_logos',
        'soccer_esp.1': '/home/ledpi/LEDMatrix/assets/sports/soccer_logos',
        'soccer_ger.1': '/home/ledpi/LEDMatrix/assets/sports/soccer_logos',
        'soccer_ita.1': '/home/ledpi/LEDMatrix/assets/sports/soccer_logos',
        'soccer_fra.1': '/home/ledpi/LEDMatrix/assets/sports/soccer_logos',
        'soccer_por.1': '/home/ledpi/LEDMatrix/assets/sports/soccer_logos',
        'soccer_uefa.champions': '/home/ledpi/LEDMatrix/assets/sports/soccer_logos',
        'soccer_uefa.europa': '/home/ledpi/LEDMatrix/assets/sports/soccer_logos',
        'soccer_usa.1': '/home/ledpi/LEDMatrix/assets/sports/soccer_logos',
        # NEW: US Professional and NCAA Soccer (3 new leagues)
        'soccer_usa.nwsl': '/home/ledpi/LEDMatrix/assets/sports/soccer_logos',
        'soccer_usa.ncaa.m.1': '/home/ledpi/LEDMatrix/assets/sports/soccer_logos',
        'soccer_usa.ncaa.w.1': '/home/ledpi/LEDMatrix/assets/sports/soccer_logos'
    }
    
    def __init__(self, request_timeout: int = 30, retry_attempts: int = 3):
        """Initialize the logo downloader with configurable request settings."""
        self.request_timeout = request_timeout
        
        # Configure session with retry logic
        self.session = requests.Session()
        retry_strategy = Retry(
            total=retry_attempts,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
    
    @staticmethod
    def normalize_abbreviation(abbreviation: str) -> str:
        """Normalize team abbreviation for filename."""
        # Handle special characters that can cause filesystem issues
        normalized = abbreviation.upper()
        
        # Replace problematic characters with safe alternatives
        normalized = normalized.replace('&', 'AND')
        normalized = normalized.replace('/', '_')
        normalized = normalized.replace('\\', '_')
        normalized = normalized.replace(':', '_')
        normalized = normalized.replace('*', '_')
        normalized = normalized.replace('?', '_')
        normalized = normalized.replace('"', '_')
        normalized = normalized.replace('<', '_')
        normalized = normalized.replace('>', '_')
        normalized = normalized.replace('|', '_')
        return normalized
    
    @staticmethod
    def get_logo_filename_variations(abbreviation: str) -> list:
        """Get possible filename variations for a team abbreviation."""
        variations = []
        original = abbreviation.upper()
        normalized = LogoDownloader.normalize_abbreviation(abbreviation)
        
        # Add original and normalized versions
        variations.extend([f"{original}.png", f"{normalized}.png"])
        
        # Special handling for known cases
        if original == 'TA&M':
            # TA&M has a file named TA&M.png, but normalize creates TAANDM.png
            variations = [f"{original}.png", f"{normalized}.png"]
        
        return variations
    
    def get_logo_directory(self, league: str) -> str:
        """Get the logo directory for a given league."""
        return self.LOGO_DIRECTORIES.get(league, f'/home/ledpi/LEDMatrix/assets/sports/{league}_logos')
    
    def ensure_logo_directory(self, logo_dir: str) -> bool:
        """Ensure the logo directory exists, create if necessary."""
        logger.info(f"DEBUG ensure_logo_directory called with: {logo_dir}, cwd={os.getcwd()}")
        try:
            os.makedirs(logo_dir, exist_ok=True)
            
            # Check if we can actually write to the directory
            test_file = os.path.join(logo_dir, '.write_test')
            try:
                with open(test_file, 'w') as f:
                    f.write('test')
                os.remove(test_file)
                logger.debug(f"Directory {logo_dir} is writable")
                return True
            except PermissionError:
                logger.error(f"Permission denied: Cannot write to directory {logo_dir}")
                logger.error(f"Please run: sudo ./fix_assets_permissions.sh")
                return False
            except Exception as e:
                logger.error(f"Failed to test write access to directory {logo_dir}: {e}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to create logo directory {logo_dir}: {e}")
            return False
    
    def download_logo(self, logo_url: str, filepath: Path, team_abbreviation: str) -> bool:
        """Download a single logo from URL and save to filepath."""
        try:
            response = self.session.get(logo_url, timeout=self.request_timeout)
            response.raise_for_status()
            
            # Save the logo
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            logger.info(f"Downloaded logo for {team_abbreviation} to {filepath}")
            return True
            
        except requests.RequestException as e:
            logger.error(f"Failed to download logo for {team_abbreviation} from {logo_url}: {e}")
            return False
        except IOError as e:
            logger.error(f"Failed to save logo for {team_abbreviation} to {filepath}: {e}")
            return False
    
    def fetch_teams_data(self, league: str) -> Optional[Dict]:
        """Fetch team data from ESPN API for a given league."""
        endpoint = self.API_ENDPOINTS.get(league)
        if not endpoint:
            logger.error(f"No API endpoint configured for league: {league}")
            return None
        
        try:
            # Add limit parameter to get all teams
            params = {'limit': 1000}
            response = self.session.get(endpoint, params=params, timeout=self.request_timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch teams data for {league}: {e}")
            return None
    
    def fetch_single_team(self, league: str, team_id: str) -> Optional[Dict]:
        """Fetch a single team's data by ID."""
        endpoint = self.API_ENDPOINTS.get(league)
        if not endpoint:
            logger.error(f"No API endpoint configured for league: {league}")
            return None
        
        # Construct team-specific endpoint
        team_endpoint = f"{endpoint}/{team_id}"
        
        try:
            response = self.session.get(team_endpoint, timeout=self.request_timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch team {team_id} data for {league}: {e}")
            return None
    
    def extract_teams_from_data(self, data: Dict, league: str) -> List[Dict[str, str]]:
        """Extract team information from API response."""
        teams = []
        
        try:
            sports_data = data.get('sports', [])
            if not sports_data:
                logger.warning(f"No sports data found for {league}")
                return teams
            
            for sport in sports_data:
                leagues_data = sport.get('leagues', [])
                for league_data in leagues_data:
                    teams_data = league_data.get('teams', [])
                    for team_data in teams_data:
                        team = team_data.get('team', {})
                        
                        # Extract logo URL
                        logos = team.get('logos', [])
                        if not logos:
                            logger.warning(f"No logos found for team: {team.get('displayName', 'Unknown')}")
                            continue
                        
                        logo_url = logos[0].get('href', '')
                        if not logo_url:
                            logger.warning(f"No logo URL found for team: {team.get('displayName', 'Unknown')}")
                            continue
                        
                        teams.append({
                            'abbreviation': team.get('abbreviation', ''),
                            'display_name': team.get('displayName', ''),
                            'logo_url': logo_url
                        })
            
            logger.debug(f"Extracted {len(teams)} teams from {league}")
            return teams
            
        except (KeyError, TypeError) as e:
            logger.error(f"Failed to extract teams from data for {league}: {e}")
            return teams
    
    def download_missing_logos_for_league(self, league: str, force_download: bool = False) -> Tuple[int, int]:
        """Download missing logos for all teams in a league."""
        logger.info(f"Starting logo download for league: {league}")
        
        # Get logo directory
        logo_dir = self.get_logo_directory(league)
        if not self.ensure_logo_directory(logo_dir):
            logger.error(f"Failed to create logo directory for {league}")
            return 0, 0
        
        # Fetch team data
        data = self.fetch_teams_data(league)
        if not data:
            logger.error(f"Failed to fetch team data for {league}")
            return 0, 0
        
        # Extract teams
        teams = self.extract_teams_from_data(data, league)
        if not teams:
            logger.warning(f"No teams found for {league}")
            return 0, 0
        
        # Download missing logos
        downloaded_count = 0
        failed_count = 0
        
        for team in teams:
            abbreviation = team['abbreviation']
            display_name = team['display_name']
            logo_url = team['logo_url']
            
            # Create filename
            filename = f"{self.normalize_abbreviation(abbreviation)}.png"
            filepath = Path(logo_dir) / filename
            
            # Skip if already exists and not forcing download
            if filepath.exists() and not force_download:
                logger.debug(f"Skipping {display_name}: {filename} already exists")
                continue
            
            # Download logo
            if self.download_logo(logo_url, filepath, display_name):
                downloaded_count += 1
            else:
                failed_count += 1
            
            # Small delay to be respectful to the API
            time.sleep(0.1)
        
        logger.info(f"Logo download complete for {league}: {downloaded_count} downloaded, {failed_count} failed")
        return downloaded_count, failed_count
    
    def download_all_ncaa_football_logos(self, include_fcs: bool = True, force_download: bool = False) -> Tuple[int, int]:
        """Download all NCAA football team logos including FCS teams."""
        if include_fcs:
            return self.download_missing_logos_for_league('ncaa_fb_all', force_download)
        else:
            return self.download_missing_logos_for_league('ncaa_fb', force_download)
    
    def download_missing_logo_for_team(self, league: str, team_id: str, team_abbreviation: str, logo_path: Path) -> bool:
        """Download a missing logo for a specific team."""
        # Get logo directory from the path
        logo_dir = str(logo_path.parent)
        
        # Ensure the logo directory exists and is writable
        logo_dir = str(logo_path.parent)
        if not self.ensure_logo_directory(logo_dir):
            logger.error(f"Cannot download logo for {team_abbreviation}: directory {logo_dir} is not writable")
            return False
        
        # Fetch team data to find the logo URL
        data = self.fetch_single_team(league, team_id)
        if not data:
            return False
        try:
            logo_url = data["team"]["logos"][0]["href"]
        except KeyError:
            return False
        # Download the logo
        success = self.download_logo(logo_url, logo_path, team_abbreviation)
        if success:
            time.sleep(0.1)  # Small delay
        return success
    
    def download_all_missing_logos(self, leagues: List[str] | None = None, force_download: bool = False) -> Dict[str, Tuple[int, int]]:
        """Download missing logos for all specified leagues."""
        if leagues is None:
            leagues = list(self.API_ENDPOINTS.keys())
        
        results = {}
        total_downloaded = 0
        total_failed = 0
        
        for league in leagues:
            if league not in self.API_ENDPOINTS:
                logger.warning(f"Skipping unknown league: {league}")
                continue
            
            downloaded, failed = self.download_missing_logos_for_league(league, force_download)
            results[league] = (downloaded, failed)
            total_downloaded += downloaded
            total_failed += failed
        
        logger.info(f"Overall logo download results: {total_downloaded} downloaded, {total_failed} failed")
        return results
    
    def create_placeholder_logo(self, team_abbreviation: str, logo_dir: str) -> bool:
        """Create a placeholder logo when real logo cannot be downloaded."""
        try:
            # Ensure the logo directory exists
            if not self.ensure_logo_directory(logo_dir):
                logger.error(f"Failed to create logo directory: {logo_dir}")
                return False
            
            filename = f"{self.normalize_abbreviation(team_abbreviation)}.png"
            filepath = Path(logo_dir) / filename
            
            # Check if we can write to the directory
            try:
                # Test write permissions by creating a temporary file
                test_file = filepath.parent / "test_write.tmp"
                test_file.touch()
                test_file.unlink()  # Remove the test file
            except PermissionError:
                logger.error(f"Permission denied: Cannot write to directory {logo_dir}")
                return False
            except Exception as e:
                logger.error(f"Directory access error for {logo_dir}: {e}")
                return False
            
            # Create a simple placeholder logo
            logo = Image.new('RGBA', (64, 64), (100, 100, 100, 255))  # Gray background
            draw = ImageDraw.Draw(logo)
            
            # Try to load a font, fallback to default
            try:
                font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 12)
            except:
                try:
                    font = ImageFont.load_default()
                except:
                    font = None
            
            # Draw team abbreviation
            text = team_abbreviation
            if font:
                # Center the text
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                x = (64 - text_width) // 2
                y = (64 - text_height) // 2
                draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
            else:
                # Fallback without font
                draw.text((16, 24), text, fill=(255, 255, 255, 255))
            
            logo.save(filepath)
            logger.info(f"Created placeholder logo for {team_abbreviation} at {filepath}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create placeholder logo for {team_abbreviation}: {e}")
            return False
    
    def convert_image_to_rgba(self, filepath: Path) -> bool:
        """Convert an image file to RGBA format to avoid PIL warnings."""
        try:
            with Image.open(filepath) as img:
                if img.mode != 'RGBA':
                    # Convert to RGBA
                    converted_img = img.convert('RGBA')
                    converted_img.save(filepath, 'PNG')
                    logger.debug(f"Converted {filepath.name} from {img.mode} to RGBA")
                    return True
                else:
                    logger.debug(f"{filepath.name} is already in RGBA format")
                    return True
        except Exception as e:
            logger.error(f"Failed to convert {filepath.name} to RGBA: {e}")
            return False
    
    def convert_all_logos_to_rgba(self, league: str) -> Tuple[int, int]:
        """Convert all logos in a league directory to RGBA format."""
        logo_dir = Path(self.get_logo_directory(league))
        if not logo_dir.exists():
            logger.warning(f"Logo directory does not exist: {logo_dir}")
            return 0, 0
        
        converted_count = 0
        failed_count = 0
        
        for logo_file in logo_dir.glob("*.png"):
            if self.convert_image_to_rgba(logo_file):
                converted_count += 1
            else:
                failed_count += 1
        
        logger.info(f"Converted {converted_count} logos to RGBA format for {league}, {failed_count} failed")
        return converted_count, failed_count


# Helper function to map soccer league codes to logo downloader format
def get_soccer_league_key(league_code: str) -> str:
    """
    Map soccer league codes to logo downloader format.
    
    Args:
        league_code: Soccer league code (e.g., 'eng.1', 'por.1')
        
    Returns:
        Logo downloader league key (e.g., 'soccer_eng.1', 'soccer_por.1')
    """
    return f"soccer_{league_code}"


# Convenience function for easy integration
def download_missing_logo(league: str, team_id: str, team_abbreviation: str, logo_path: Path, logo_url: str | None = None, create_placeholder: bool = True) -> bool:
    """
    Convenience function to download a missing team logo.
    
    Args:
        team_abbreviation: Team abbreviation (e.g., 'UGA', 'BAMA', 'TA&M')
        league: League identifier (e.g., 'ncaa_fb', 'nfl')
        team_name: Optional team name for logging
        create_placeholder: Whether to create a placeholder if download fails
        
    Returns:
        True if logo exists or was successfully downloaded, False otherwise
    """
    downloader = LogoDownloader()
    # Check if we recently failed to download this logo (prevent spam loops)
    cache_key = f"{league}_{team_abbreviation}"
    if cache_key in _failed_download_cache:
        if time.time() - _failed_download_cache[cache_key] < _FAILED_CACHE_DURATION:
            return False  # Skip - recently failed
    
    # Check if logo already exists
    logo_dir = downloader.get_logo_directory(league)
    if not downloader.ensure_logo_directory(logo_dir):
        logger.error(f"Cannot download logo for {team_abbreviation}: directory {logo_dir} is not writable")
        return False
    filename = f"{downloader.normalize_abbreviation(team_abbreviation)}.png"
    filepath = Path(logo_dir) / filename
    
    if filepath.exists():
        logger.debug(f"Logo already exists for {team_abbreviation} ({league})")
        return True
    
    # Try to download the real logo first
    logger.info(f"Attempting to download logo for {team_abbreviation}  from {league}")
    if logo_url:
        success = downloader.download_logo(logo_url, filepath, team_abbreviation)
        if success:
            time.sleep(0.1)  # Small delay
        return success

    success = downloader.download_missing_logo_for_team(league, team_id, team_abbreviation, filepath)
    
    if not success and create_placeholder:
        logger.info(f"Creating placeholder logo for {team_abbreviation}")
        # Create placeholder as fallback
        success = downloader.create_placeholder_logo(team_abbreviation, logo_dir)
    
    if success:
        logger.info(f"Successfully handled logo for {team_abbreviation}")
    else:
        logger.warning(f"Failed to download or create logo for {team_abbreviation}")
        # Cache this failure to prevent spam loops
        _failed_download_cache[f"{league}_{team_abbreviation}"] = time.time()
    
    return success


def download_all_logos_for_league(league: str, force_download: bool = False) -> Tuple[int, int]:
    """
    Convenience function to download all missing logos for a league.
    
    Args:
        league: League identifier (e.g., 'ncaa_fb', 'nfl')
        force_download: Whether to re-download existing logos
        
    Returns:
        Tuple of (downloaded_count, failed_count)
    """
    downloader = LogoDownloader()
    return downloader.download_missing_logos_for_league(league, force_download)


if __name__ == "__main__":
    # Example usage
    logging.basicConfig(level=logging.INFO)
    
    downloader = LogoDownloader()
    
    # Download NCAA football logos (including FCS)
    print("Downloading NCAA football logos...")
    downloaded, failed = downloader.download_all_ncaa_football_logos(include_fcs=True)
    print(f"Downloaded {downloaded} logos, {failed} failed")
    
    # Download NFL logos
    print("\nDownloading NFL logos...")
    downloaded, failed = downloader.download_missing_logos_for_league('nfl')
    print(f"Downloaded {downloaded} logos, {failed} failed")
