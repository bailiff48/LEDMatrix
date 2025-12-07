"""
Tennis match manager for LED Matrix display.
Fetches ATP and WTA match data from ESPN API with Grand Slam focus.
"""

import requests
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class TennisManager:
    """Manages tennis match data and display."""
    
    # Grand Slam tournaments
    GRAND_SLAMS = {
        'Australian Open': 'AUS OPEN',
        'French Open': 'FRENCH OPEN',
        'Roland Garros': 'FRENCH OPEN',
        'Wimbledon': 'WIMBLEDON',
        'US Open': 'US OPEN',
        'U.S. Open': 'US OPEN'
    }
    
    def __init__(self, config: dict, display_manager):
        """
        Initialize tennis manager.
        
        Args:
            config: Full configuration dictionary
            display_manager: Display manager instance for drawing
        """
        self.config = config
        self.display_manager = display_manager
        self.tennis_config = config.get('tennis', {})
        
        # Match data cache
        self.matches = {
            'atp': [],
            'wta': []
        }
        self.last_update = {}
        self.update_interval = self.tennis_config.get('update_interval', 600)  # 10 min default
        
        # Display settings
        self.majors_only = self.tennis_config.get('majors_only', True)
        self.show_completed = self.tennis_config.get('show_completed_matches', False)
        self.max_matches = self.tennis_config.get('max_matches_display', 5)
        
        # Error tracking
        self.error_count = 0
        self.max_errors = 5
        
        logger.info(f"TennisManager initialized: majors_only={self.majors_only}, "
                   f"show_completed={self.show_completed}")
    
    def update(self):
        """Fetch latest match data if needed."""
        if not self.tennis_config.get('enabled', False):
            return
        
        current_time = time.time()
        
        # Update each tour
        for tour in ['atp', 'wta']:
            last_update = self.last_update.get(tour, 0)
            if current_time - last_update >= self.update_interval:
                self._fetch_matches(tour)
                self.last_update[tour] = current_time
    
    def _fetch_matches(self, tour: str):
        """
        Fetch match data for a specific tour.
        
        Args:
            tour: 'atp' or 'wta'
        """
        try:
            # Get today's date in YYYYMMDD format
            date_str = datetime.now().strftime('%Y%m%d')
            url = f"https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard?dates={date_str}"
            
            logger.debug(f"Fetching {tour.upper()} matches from {url}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            # Parse events (matches)
            events = data.get('events', [])
            if not events:
                logger.debug(f"No {tour.upper()} matches today")
                self.matches[tour] = []
                self.error_count = 0
                return
            
            # Parse and filter matches
            parsed_matches = []
            for event in events:
                match_data = self._parse_match(event, tour)
                if match_data:
                    # Apply filters
                    if self.majors_only and not match_data['is_major']:
                        continue
                    if not self.show_completed and match_data['completed']:
                        continue
                    
                    parsed_matches.append(match_data)
            
            self.matches[tour] = parsed_matches
            self.error_count = 0
            
            if parsed_matches:
                logger.info(f"{tour.upper()}: Found {len(parsed_matches)} matches")
            
        except requests.exceptions.RequestException as e:
            self.error_count += 1
            logger.error(f"Error fetching {tour.upper()} data: {e}")
            if self.error_count >= self.max_errors:
                logger.error(f"Max errors reached for {tour.upper()}, disabling updates")
        except Exception as e:
            logger.error(f"Unexpected error parsing {tour.upper()} data: {e}")
    
    def _parse_match(self, event: dict, tour: str) -> Optional[Dict]:
        """
        Parse match event data.
        
        Args:
            event: Event data from ESPN API
            tour: Tour abbreviation
            
        Returns:
            Parsed match data or None
        """
        try:
            # Get tournament name
            tournament_name = event.get('name', 'Tournament')
            
            # Check if Grand Slam
            is_major = self._is_grand_slam(tournament_name)
            major_abbrev = self._get_major_abbreviation(tournament_name) if is_major else None
            
            # Get competition data
            competitions = event.get('competitions', [])
            if not competitions:
                return None
            
            competition = competitions[0]
            
            # Get status
            status = competition.get('status', {})
            status_type = status.get('type', {})
            state = status_type.get('state', 'pre')
            completed = status_type.get('completed', False)
            
            # Get competitors (players)
            competitors = competition.get('competitors', [])
            if len(competitors) < 2:
                return None
            
            # Parse players
            player1 = self._parse_player(competitors[0])
            player2 = self._parse_player(competitors[1])
            
            if not player1 or not player2:
                return None
            
            # Determine match status
            if state == 'in':
                match_status = 'LIVE'
            elif completed:
                match_status = 'FINAL'
            else:
                match_status = 'UPCOMING'
            
            # Get round info
            round_info = competition.get('notes', [{}])[0].get('headline', '')
            
            return {
                'tour': tour.upper(),
                'tournament': tournament_name,
                'is_major': is_major,
                'major_abbrev': major_abbrev,
                'round': round_info,
                'player1': player1,
                'player2': player2,
                'status': match_status,
                'completed': completed,
                'state': state
            }
            
        except Exception as e:
            logger.error(f"Error parsing match data: {e}")
            return None
    
    def _parse_player(self, competitor: dict) -> Optional[Dict]:
        """
        Parse player data from competitor.
        
        Args:
            competitor: Competitor data from ESPN API
            
        Returns:
            Player info dictionary or None
        """
        try:
            athlete = competitor.get('athlete', {})
            
            # Get player name
            name = athlete.get('shortName', athlete.get('displayName', 'Unknown'))
            
            # Get score/sets
            score = competitor.get('score', {})
            value = score.get('value', '')
            
            # Get individual set scores
            line_scores = competitor.get('linescores', [])
            sets = [ls.get('value', '') for ls in line_scores]
            
            # Check if winner
            winner = competitor.get('winner', False)
            
            return {
                'name': name,
                'score': value,
                'sets': sets,
                'winner': winner
            }
            
        except Exception as e:
            logger.error(f"Error parsing player: {e}")
            return None
    
    def _is_grand_slam(self, tournament_name: str) -> bool:
        """
        Check if tournament is a Grand Slam.
        
        Args:
            tournament_name: Tournament name
            
        Returns:
            True if Grand Slam
        """
        return any(major in tournament_name for major in self.GRAND_SLAMS.keys())
    
    def _get_major_abbreviation(self, tournament_name: str) -> str:
        """
        Get abbreviated name for Grand Slam.
        
        Args:
            tournament_name: Tournament name
            
        Returns:
            Abbreviated name
        """
        for major, abbrev in self.GRAND_SLAMS.items():
            if major in tournament_name:
                return abbrev
        return tournament_name
    
    def has_active_matches(self) -> bool:
        """Check if there are any active matches."""
        return any(len(matches) > 0 for matches in self.matches.values())
    
    def display(self, force_clear: bool = False):
        """Display tennis match data on LED matrix."""
        if not self.has_active_matches():
            return
        
        # Collect all matches
        all_matches = []
        for tour in ['atp', 'wta']:
            all_matches.extend(self.matches.get(tour, []))
        
        # Sort: live first, then upcoming, then completed
        priority = {'LIVE': 0, 'UPCOMING': 1, 'FINAL': 2}
        all_matches.sort(key=lambda m: priority.get(m['status'], 3))
        
        # Limit matches
        max_matches = getattr(self, 'max_matches', 5)
        display_matches = all_matches[:max_matches]
        
        # Build display text
        text_parts = []
        for match in display_matches:
            match_text = self._format_match(match)
            if match_text:
                text_parts.append(match_text)
        
        if not text_parts:
            return
        
        display_text = " | ".join(text_parts)
        
        self.display_manager.clear()
        self.display_manager.draw_text(
            display_text,
            x=2,
            y=12,
            color=(255, 255, 0),
            small_font=True
        )
        self.display_manager.update_display()

    def draw(self, canvas):
        """
        Draw tennis match data on LED matrix.
        
        Args:
            canvas: LED matrix canvas to draw on
        """
        if not self.has_active_matches():
            # Display "no matches" message
            if self.majors_only:
                text = "TENNIS: No majors in progress"
            else:
                text = "TENNIS: No matches today"
            self.display_manager.draw_text(canvas, text, scroll=True)
            return
        
        # Collect all matches
        all_matches = []
        for tour in ['atp', 'wta']:
            all_matches.extend(self.matches.get(tour, []))
        
        # Sort: live first, then upcoming, then completed
        priority = {'LIVE': 0, 'UPCOMING': 1, 'FINAL': 2}
        all_matches.sort(key=lambda m: priority.get(m['status'], 3))
        
        # Limit matches
        display_matches = all_matches[:self.max_matches]
        
        # Build display text
        text_parts = []
        
        for match in display_matches:
            match_text = self._format_match(match)
            if match_text:
                text_parts.append(match_text)
        
        # Join and display
        display_text = " • ".join(text_parts)
        self.display_manager.draw_text(canvas, display_text, scroll=True)
    
    def _format_match(self, match: Dict) -> str:
        """
        Format match for display.
        
        Args:
            match: Match data dictionary
            
        Returns:
            Formatted match string
        """
        try:
            # Use major abbreviation if available
            tournament = match.get('major_abbrev', match.get('tournament', ''))
            
            # Shorten tournament name if too long
            if len(tournament) > 15 and not match['is_major']:
                tournament = tournament[:12] + "..."
            
            player1 = match['player1']
            player2 = match['player2']
            status = match['status']
            
            # Format based on match status
            if status == 'FINAL':
                # Show winner first
                if player1['winner']:
                    winner = player1['name']
                    loser = player2['name']
                    sets = self._format_sets(player1['sets'], player2['sets'])
                else:
                    winner = player2['name']
                    loser = player1['name']
                    sets = self._format_sets(player2['sets'], player1['sets'])
                
                return f"{tournament}: {winner} d. {loser} {sets}"
            
            elif status == 'LIVE':
                # Show current score
                sets = self._format_sets(player1['sets'], player2['sets'])
                return f"{tournament}: {player1['name']} vs {player2['name']} {sets} [LIVE]"
            
            else:  # UPCOMING
                # Just show matchup
                return f"{tournament}: {player1['name']} vs {player2['name']}"
                
        except Exception as e:
            logger.error(f"Error formatting match: {e}")
            return ""
    
    def _format_sets(self, sets1: List, sets2: List) -> str:
        """
        Format set scores for display.
        
        Args:
            sets1: Player 1 set scores
            sets2: Player 2 set scores
            
        Returns:
            Formatted sets string (e.g., "6-4 7-6 6-3")
        """
        if not sets1 or not sets2:
            return ""
        
        formatted = []
        for s1, s2 in zip(sets1, sets2):
            if s1 and s2:
                formatted.append(f"{s1}-{s2}")
        
        return " ".join(formatted)
    
    def get_status(self) -> Dict:
        """
        Get current manager status.
        
        Returns:
            Status dictionary
        """
        total_matches = sum(len(matches) for matches in self.matches.values())
        
        live_matches = []
        for tour, matches in self.matches.items():
            for match in matches:
                if match['status'] == 'LIVE':
                    live_matches.append({
                        'tour': tour.upper(),
                        'tournament': match.get('major_abbrev', match['tournament']),
                        'matchup': f"{match['player1']['name']} vs {match['player2']['name']}"
                    })
        
        return {
            'enabled': self.tennis_config.get('enabled', False),
            'majors_only': self.majors_only,
            'total_matches': total_matches,
            'live_matches': len(live_matches),
            'matches': live_matches,
            'last_update': max(self.last_update.values()) if self.last_update else None,
            'error_count': self.error_count,
            'update_interval': self.update_interval
        }
