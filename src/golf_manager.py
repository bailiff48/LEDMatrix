"""
Golf tournament manager for LED Matrix display.
Fetches tournament data from ESPN API for multiple tours.
Supports favorite golfer tracking with highlighted display.

Supported Tours:
- PGA Tour (pga)
- LPGA Tour (lpga)  
- DP World Tour (eur)
- PGA Champions Tour (champions-tour)

Config Schema (config.json golf section):
{
    "golf": {
        "enabled": true,
        "tours": ["pga", "lpga", "eur", "champions-tour"],
        "show_top_n": 5,
        "update_interval": 900,
        "favorite_golfers": [
            {"id": "3448", "name": "Tiger Woods"},
            {"id": "1225", "name": "Rory McIlroy"}
        ],
        "highlight_favorites": true,
        "show_favorites_section": true
    }
}
"""

import requests
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Tour configuration with display names and ESPN API slugs
AVAILABLE_TOURS = {
    'pga': {
        'name': 'PGA Tour',
        'espn_slug': 'pga',
        'color': (0, 255, 0),  # Green
    },
    'lpga': {
        'name': 'LPGA Tour',
        'espn_slug': 'lpga',
        'color': (255, 105, 180),  # Pink
    },
    'eur': {
        'name': 'DP World Tour',
        'espn_slug': 'eur',
        'color': (0, 191, 255),  # Deep sky blue
    },
    'champions-tour': {
        'name': 'Champions Tour',
        'espn_slug': 'champions-tour',
        'color': (255, 215, 0),  # Gold
    },
}

# Highlight color for favorite golfers
FAVORITE_HIGHLIGHT_COLOR = (255, 255, 0)  # Yellow
FAVORITE_SECTION_COLOR = (255, 165, 0)    # Orange for "YOUR GOLFERS" header


class GolfManager:
    """Manages golf tournament data and display with favorite golfer support."""
    
    def __init__(self, config: dict, display_manager):
        """
        Initialize golf manager.
        
        Args:
            config: Full configuration dictionary
            display_manager: Display manager instance for drawing
        """
        self.config = config
        self.display_manager = display_manager
        self.golf_config = config.get('golf', {})
        
        # Tournament data cache - initialize for all available tours
        self.tournaments = {tour: None for tour in AVAILABLE_TOURS.keys()}
        self.last_update = {}
        self.update_interval = self.golf_config.get('update_interval', 900)  # 15 min default
        
        # Display settings
        self.show_top_n = self.golf_config.get('show_top_n', 5)
        self.enabled_tours = self.golf_config.get('tours', ['pga', 'lpga'])
        
        # Favorite golfers - list of {id, name} dicts
        self.favorite_golfers = self.golf_config.get('favorite_golfers', [])
        self.highlight_favorites = self.golf_config.get('highlight_favorites', True)
        self.show_favorites_section = self.golf_config.get('show_favorites_section', True)
        
        # Build a set of favorite IDs for fast lookup
        self.favorite_ids = {str(g.get('id')) for g in self.favorite_golfers if g.get('id')}
        
        # Error tracking per tour
        self.error_counts = {tour: 0 for tour in AVAILABLE_TOURS.keys()}
        self.max_errors = 5
        
        # Scrolling display state
        self._scroll_image = None
        self._scroll_position = 0
        self._scroll_speed = 2
        self._last_scroll_build = 0
        
        # Cache for favorites found in tournaments
        self._favorites_in_tournaments = []
        
        logger.info(f"GolfManager initialized: tours={self.enabled_tours}, "
                   f"top_n={self.show_top_n}, favorites={len(self.favorite_golfers)}")

    def reload_config(self):
        """Reload configuration (called when config changes via web UI)."""
        self.golf_config = self.config.get('golf', {})
        self.show_top_n = self.golf_config.get('show_top_n', 5)
        self.enabled_tours = self.golf_config.get('tours', ['pga', 'lpga'])
        self.favorite_golfers = self.golf_config.get('favorite_golfers', [])
        self.highlight_favorites = self.golf_config.get('highlight_favorites', True)
        self.show_favorites_section = self.golf_config.get('show_favorites_section', True)
        self.favorite_ids = {str(g.get('id')) for g in self.favorite_golfers if g.get('id')}
        
        # Force scroll image rebuild
        self._scroll_image = None
        
        logger.info(f"GolfManager config reloaded: tours={self.enabled_tours}, "
                   f"favorites={len(self.favorite_golfers)}")

    def update(self):
        """Fetch latest tournament data if needed."""
        if not self.golf_config.get('enabled', False):
            return
        
        current_time = time.time()
        
        # Update each enabled tour
        for tour in self.enabled_tours:
            if tour not in AVAILABLE_TOURS:
                logger.warning(f"Unknown tour '{tour}' in enabled_tours, skipping")
                continue
                
            last_update = self.last_update.get(tour, 0)
            if current_time - last_update >= self.update_interval:
                self._fetch_tournament(tour)
                self.last_update[tour] = current_time
        
        # Update favorites cache after fetching
        self._update_favorites_cache()
    
    def _fetch_tournament(self, tour: str):
        """
        Fetch tournament data for a specific tour.
        
        Args:
            tour: Tour key (e.g., 'pga', 'lpga', 'eur', 'champions-tour')
        """
        if tour not in AVAILABLE_TOURS:
            logger.error(f"Unknown tour: {tour}")
            return
            
        tour_info = AVAILABLE_TOURS[tour]
        espn_slug = tour_info['espn_slug']
        
        try:
            url = f"https://site.api.espn.com/apis/site/v2/sports/golf/{espn_slug}/scoreboard"
            
            logger.debug(f"Fetching {tour_info['name']} data from {url}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            # Check for active events
            events = data.get('events', [])
            if not events:
                logger.debug(f"No active {tour_info['name']} tournaments")
                self.tournaments[tour] = None
                self.error_counts[tour] = 0
                return
            
            # Get the first (current) event
            event = events[0]
            tournament_data = self._parse_tournament(event, tour)
            
            if tournament_data:
                # Skip completed tournaments for certain tours (off-season shows stale events)
                if tournament_data.get('completed', False) and tour in ['lpga', 'eur']:
                    logger.info(f"{tour_info['name']}: Skipping completed tournament "
                               f"'{tournament_data['name']}' (status: {tournament_data['status']})")
                    self.tournaments[tour] = None
                else:
                    self.tournaments[tour] = tournament_data
                    self.error_counts[tour] = 0
                    logger.info(f"{tour_info['name']}: {tournament_data['name']} - "
                               f"{tournament_data['status']} ({len(tournament_data['all_players'])} players)")
            else:
                self.tournaments[tour] = None
                
        except requests.exceptions.RequestException as e:
            self.error_counts[tour] = self.error_counts.get(tour, 0) + 1
            logger.error(f"Error fetching {tour_info['name']} data: {e}")
            if self.error_counts[tour] >= self.max_errors:
                logger.error(f"Max errors reached for {tour_info['name']}, disabling updates")
        except Exception as e:
            logger.error(f"Unexpected error parsing {tour_info['name']} data: {e}")
    
    def _parse_tournament(self, event: dict, tour: str) -> Optional[Dict]:
        """
        Parse tournament event data, storing all players for favorite matching.
        
        Args:
            event: Event data from ESPN API
            tour: Tour key
            
        Returns:
            Parsed tournament data or None
        """
        try:
            tour_info = AVAILABLE_TOURS.get(tour, {})
            
            tournament = {
                'tour': tour,
                'tour_name': tour_info.get('name', tour.upper()),
                'tour_color': tour_info.get('color', (0, 255, 0)),
                'id': event.get('id'),
                'name': event.get('name', event.get('shortName', 'Tournament')),
                'status': 'In Progress',
                'completed': False,
                'leaders': [],      # Top N players for leaderboard
                'all_players': [],  # All players for favorite matching
            }
            
            # Get competition data
            competitions = event.get('competitions', [])
            if not competitions:
                return None
            
            competition = competitions[0]
            
            # Get status
            status = competition.get('status', {})
            status_type = status.get('type', {})
            tournament['status'] = status_type.get('detail', 'In Progress')
            tournament['completed'] = status_type.get('completed', False)
            
            # Get competitors (players)
            competitors = competition.get('competitors', [])
            
            # Sort by position/score
            sorted_players = sorted(
                competitors,
                key=lambda x: self._parse_score(x.get('score', 'E'))
            )
            
            # Parse ALL players (for favorite matching)
            for idx, player in enumerate(sorted_players):
                player_info = self._parse_player(player, idx + 1)
                if player_info:
                    tournament['all_players'].append(player_info)
                    # Also add to leaders if in top N
                    if idx < self.show_top_n:
                        tournament['leaders'].append(player_info)
            
            return tournament
            
        except Exception as e:
            logger.error(f"Error parsing tournament data: {e}")
            return None
    
    def _parse_player(self, competitor: dict, position: int) -> Optional[Dict]:
        """
        Parse player data from competitor.
        
        Args:
            competitor: Competitor data from ESPN API
            position: Calculated position (1-based)
            
        Returns:
            Player info dictionary or None
        """
        try:
            athlete = competitor.get('athlete', {})
            status = competitor.get('status', {})
            
            # Get player ID (crucial for favorite matching)
            player_id = str(athlete.get('id', ''))
            
            # Get player name
            name = athlete.get('shortName', athlete.get('displayName', 'Unknown'))
            
            # Get score
            score = competitor.get('score', 'E')
            
            # Get position from status if available, otherwise use calculated
            position_data = status.get('position', {})
            display_position = position_data.get('displayName')
            if not display_position:
                # Check for tied position indicator
                if position_data.get('isTie'):
                    display_position = f"T{position}"
                else:
                    display_position = str(position)
            
            # Get thru indicator (holes completed)
            thru = status.get('thru', '')
            if thru == 18:
                thru = 'F'  # Finished round
            elif thru:
                thru = str(thru)
            
            # Get today's round score if available
            today_score = None
            linescores = competitor.get('linescores', [])
            if linescores:
                # Last linescore is current/today's round
                current_round = linescores[-1]
                today_score = current_round.get('value')
            
            return {
                'id': player_id,
                'name': name,
                'score': score,
                'position': display_position,
                'thru': thru,
                'today': today_score,
            }
            
        except Exception as e:
            logger.error(f"Error parsing player: {e}")
            return None
    
    def _parse_score(self, score_str: str) -> int:
        """
        Parse score string to integer for sorting.
        
        Args:
            score_str: Score string (e.g., '-12', 'E', '+3')
            
        Returns:
            Integer score value (lower is better)
        """
        try:
            if score_str == 'E':
                return 0
            # Handle 'CUT', 'WD', 'DQ' etc - put at end
            if not score_str or not score_str.lstrip('+-').isdigit():
                return 999
            return int(score_str)
        except (ValueError, TypeError):
            return 999
    
    def _update_favorites_cache(self):
        """Update cache of favorite golfers found in current tournaments."""
        self._favorites_in_tournaments = []
        
        if not self.favorite_ids:
            return
        
        for tour in self.enabled_tours:
            tournament = self.tournaments.get(tour)
            if not tournament:
                continue
            
            for player in tournament.get('all_players', []):
                if player.get('id') in self.favorite_ids:
                    # Add tournament context to player
                    player_with_context = player.copy()
                    player_with_context['tour'] = tour
                    player_with_context['tour_name'] = tournament['tour_name']
                    player_with_context['tournament_name'] = tournament['name']
                    self._favorites_in_tournaments.append(player_with_context)
        
        if self._favorites_in_tournaments:
            logger.debug(f"Found {len(self._favorites_in_tournaments)} favorites in tournaments")
    
    def _is_favorite(self, player_id: str) -> bool:
        """Check if a player ID is in favorites list."""
        return str(player_id) in self.favorite_ids
    
    def has_active_tournaments(self) -> bool:
        """Check if there are any active tournaments."""
        # If we haven't fetched yet, return True to allow first display attempt
        if not self.last_update:
            return True
        return any(self.tournaments.get(tour) is not None for tour in self.enabled_tours)

    def _is_tournament_active_day(self) -> bool:
        """
        Check if today is a tournament day (Thu-Mon).
        Golf tournaments typically run Thursday through Sunday/Monday.
        Tue-Wed are preview days showing upcoming tournament info.
        """
        # 0=Monday, 1=Tuesday, 2=Wednesday, 3=Thursday, 4=Friday, 5=Saturday, 6=Sunday
        day_of_week = datetime.now().weekday()
        # Active days: Thursday(3), Friday(4), Saturday(5), Sunday(6), Monday(0)
        return day_of_week in [0, 3, 4, 5, 6]

    def get_display_duration(self) -> int:
        """
        Get dynamic display duration based on content.
        
        Returns:
            Duration in seconds
        """
        if not self.has_active_tournaments():
            return 10  # Short duration for "no tournaments" message
        
        base_duration = 10
        
        if self._is_tournament_active_day():
            # Count active tournaments
            active_count = sum(1 for tour in self.enabled_tours 
                             if self.tournaments.get(tour) is not None)
            
            # Base time per tournament + time per player shown
            per_tournament = 8
            per_player = 2
            leaderboard_time = active_count * (per_tournament + (self.show_top_n * per_player))
            
            # Add time for favorites section if enabled and has favorites
            favorites_time = 0
            if self.show_favorites_section and self._favorites_in_tournaments:
                favorites_time = 5 + (len(self._favorites_in_tournaments) * 3)
            
            return base_duration + leaderboard_time + favorites_time
        else:
            # Preview mode - shorter duration
            upcoming_count = sum(1 for tour in self.enabled_tours 
                               if self.tournaments.get(tour) and 
                               not self.tournaments[tour].get('completed', False))
            return max(10, upcoming_count * 10)
    
    def display(self, force_clear: bool = False):
        """Display golf tournament data with scrolling text."""
        from PIL import Image, ImageDraw, ImageFont
        
        if not self.has_active_tournaments():
            # Reset scroll state when no content
            self._scroll_image = None
            self._scroll_position = 0
            return
        
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        
        # Build or rebuild the scroll image if needed
        if self._scroll_image is None or force_clear or self._needs_rebuild():
            self._build_scroll_image()
        
        if self._scroll_image is None:
            return
        
        # Get total width for wrapping
        total_width = self._scroll_image.width
        
        # If image fits on screen, just display it centered (no scroll needed)
        if total_width <= width:
            self.display_manager.clear()
            x_offset = (width - total_width) // 2
            self.display_manager.image.paste(self._scroll_image, (x_offset, 0))
            self.display_manager.update_display()
            return
        
        # Scrolling display
        scroll_speed = getattr(self, '_scroll_speed', 2)
        
        # Update scroll position
        self._scroll_position = (self._scroll_position + scroll_speed) % total_width
        
        # Create the visible window with wrap-around
        visible = Image.new('RGB', (width, height), (0, 0, 0))
        
        # Calculate how much of the image to show
        pos = self._scroll_position
        
        # First part: from current position to end (or screen width)
        first_chunk_width = min(width, total_width - pos)
        if first_chunk_width > 0:
            chunk = self._scroll_image.crop((pos, 0, pos + first_chunk_width, height))
            visible.paste(chunk, (0, 0))
        
        # Second part: wrap around from beginning if needed
        if first_chunk_width < width:
            remaining = width - first_chunk_width
            chunk = self._scroll_image.crop((0, 0, remaining, height))
            visible.paste(chunk, (first_chunk_width, 0))
        
        # Update display
        self.display_manager.image.paste(visible, (0, 0))
        self.display_manager.update_display()
        
        # Small delay for smooth scrolling
        time.sleep(0.03)
    
    def _needs_rebuild(self) -> bool:
        """Check if scroll image needs rebuilding (data changed)."""
        current_time = time.time()
        # Rebuild every 60 seconds to catch updates
        if not hasattr(self, '_last_scroll_build'):
            return True
        return current_time - self._last_scroll_build > 60
    
    def _build_scroll_image(self):
        """Build the wide scrolling image with leaderboard and favorites sections."""
        from PIL import Image, ImageDraw, ImageFont
        
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        
        # Check if we're in preview mode (Tue-Wed) or active mode (Thu-Mon)
        is_active_day = self._is_tournament_active_day()

        # Build text segments
        segments = []
        
        # === SECTION 1: LEADERBOARD ===
        for tour in self.enabled_tours:
            if tour not in AVAILABLE_TOURS:
                continue
            tournament = self.tournaments.get(tour)
            if not tournament:
                continue
            
            tour_info = AVAILABLE_TOURS[tour]
            tour_color = tour_info['color']
            
            # Tournament header
            tournament_name = self._shorten_tournament_name(tournament['name'])
            segments.append({
                'text': f"{tour_info['name']}: {tournament_name}",
                'color': tour_color,
                'bold': True
            })
            
            # On active days (Thu-Mon), show players. On preview days (Tue-Wed), show status
            if is_active_day:
                # Add separator
                segments.append({'text': ' | ', 'color': (100, 100, 100)})
                
                # Add leaders with favorite highlighting
                leaders = tournament.get('leaders', [])[:self.show_top_n]
                for i, player in enumerate(leaders):
                    score_display = self._format_score(player.get('score', 'E'))
                    position = player.get('position', str(i + 1))
                    name = player.get('name', 'Unknown')
                    
                    # Shorten first name to initial
                    name_parts = name.split()
                    if len(name_parts) > 1:
                        short_name = f"{name_parts[0][0]}. {' '.join(name_parts[1:])}"
                    else:
                        short_name = name

                    # Check if this is a favorite golfer
                    is_favorite = self._is_favorite(player.get('id', ''))
                    
                    # Choose color based on favorite status
                    if is_favorite and self.highlight_favorites:
                        text_color = FAVORITE_HIGHLIGHT_COLOR
                        # Add star indicator for favorites
                        player_text = f"★{position}. {short_name} ({score_display})"
                    else:
                        text_color = (255, 255, 255)
                        player_text = f"{position}. {short_name} ({score_display})"
                    
                    segments.append({
                        'text': player_text,
                        'color': text_color
                    })
                    
                    # Add separator between players
                    if i < len(leaders) - 1:
                        segments.append({'text': ' | ', 'color': (100, 100, 100)})
            else:
                # Preview mode - skip completed tournaments, only show upcoming
                if tournament.get('completed', False):
                    # Remove the tournament header we just added
                    segments.pop()
                    continue
                # Show tournament status (start time)
                status = tournament.get('status', '')
                if status:
                    segments.append({'text': f' - {status}', 'color': (200, 200, 200)})
            
            # Add tour separator
            segments.append({'text': '  ◆  ', 'color': tour_color})

        # Remove trailing separator if present
        if segments and '◆' in segments[-1].get('text', ''):
            segments.pop()
        
        # === SECTION 2: FAVORITES (if enabled and has favorites not in leaderboard) ===
        if is_active_day and self.show_favorites_section and self._favorites_in_tournaments:
            # Find favorites NOT already shown in leaderboard
            leaderboard_ids = set()
            for tour in self.enabled_tours:
                tournament = self.tournaments.get(tour)
                if tournament:
                    for player in tournament.get('leaders', [])[:self.show_top_n]:
                        leaderboard_ids.add(player.get('id', ''))
            
            # Filter to favorites not in leaderboard
            favorites_to_show = [f for f in self._favorites_in_tournaments 
                               if f.get('id') not in leaderboard_ids]
            
            if favorites_to_show:
                # Add section separator
                segments.append({'text': '  ║  ', 'color': (150, 150, 150)})
                
                # Section header
                segments.append({
                    'text': '⛳ YOUR GOLFERS: ',
                    'color': FAVORITE_SECTION_COLOR,
                    'bold': True
                })
                
                # Add each favorite
                for i, player in enumerate(favorites_to_show):
                    name = player.get('name', 'Unknown')
                    score_display = self._format_score(player.get('score', 'E'))
                    position = player.get('position', '?')
                    tour_name = player.get('tour_name', '')
                    
                    # Shorten name
                    name_parts = name.split()
                    if len(name_parts) > 1:
                        short_name = f"{name_parts[0][0]}. {' '.join(name_parts[1:])}"
                    else:
                        short_name = name
                    
                    # Format: "T. Woods T42 (-2) [PGA]"
                    player_text = f"{short_name} {position} ({score_display})"
                    
                    segments.append({
                        'text': player_text,
                        'color': FAVORITE_HIGHLIGHT_COLOR
                    })
                    
                    # Add tour indicator in smaller text
                    if tour_name:
                        # Abbreviate tour name
                        tour_abbrev = tour_name.replace(' Tour', '').replace('DP World', 'DPWT')
                        segments.append({
                            'text': f' [{tour_abbrev}]',
                            'color': (150, 150, 150)
                        })
                    
                    # Add separator between favorites
                    if i < len(favorites_to_show) - 1:
                        segments.append({'text': ' • ', 'color': (100, 100, 100)})
        
        # If no segments, show nothing
        if not segments:
            self._scroll_image = None
            return
        
        # Load font
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 12)
        except:
            font = ImageFont.load_default()
        
        # Calculate total width needed
        temp_img = Image.new('RGB', (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)
        
        total_text_width = 0
        for seg in segments:
            bbox = temp_draw.textbbox((0, 0), seg['text'], font=font)
            total_text_width += bbox[2] - bbox[0]
        
        # Add padding for smooth wrap-around (one screen width)
        total_width = total_text_width + width
        
        # Create the scroll image
        scroll_img = Image.new('RGB', (total_width, height), (0, 0, 0))
        draw = ImageDraw.Draw(scroll_img)
        
        # Draw all segments
        x = width // 2  # Start in middle for initial view
        y = (height - 12) // 2  # Vertically center
        
        for seg in segments:
            text = seg['text']
            color = seg['color']
            draw.text((x, y), text, font=font, fill=color)
            bbox = draw.textbbox((0, 0), text, font=font)
            x += bbox[2] - bbox[0]
        
        self._scroll_image = scroll_img
        self._scroll_position = 0
        self._last_scroll_build = time.time()
        
        logger.debug(f"Built golf scroll image: {total_width}x{height} pixels, "
                    f"{len(segments)} segments")


    def draw(self, canvas):
        """
        Draw golf tournament data on LED matrix (legacy method).
        
        Args:
            canvas: LED matrix canvas to draw on
        """
        if not self.has_active_tournaments():
            text = "GOLF: No tournaments in progress"
            self.display_manager.draw_text(canvas, text, scroll=True)
            return
        
        # Build display text
        text_parts = []
        
        for tour in self.enabled_tours:
            if tour not in AVAILABLE_TOURS:
                continue
                
            tournament = self.tournaments.get(tour)
            if not tournament:
                continue
            
            tour_info = AVAILABLE_TOURS[tour]
            tournament_name = self._shorten_tournament_name(tournament['name'])
            
            text_parts.append(f"{tour_info['name']}: {tournament_name}")
            
            # Add top leaders
            for i, player in enumerate(tournament['leaders'][:3]):
                score_display = self._format_score(player['score'])
                text_parts.append(f"{player['name']} ({score_display})")
            
            text_parts.append("•")
        
        # Remove trailing separator
        if text_parts and text_parts[-1] == "•":
            text_parts.pop()
        
        display_text = " • ".join(text_parts)
        self.display_manager.draw_text(canvas, display_text, scroll=True)
    
    def _shorten_tournament_name(self, name: str) -> str:
        """
        Shorten tournament name for display.
        
        Args:
            name: Full tournament name
            
        Returns:
            Shortened name
        """
        # Common abbreviations
        replacements = {
            'Championship': 'Champ',
            'Tournament': 'Tourn',
            'presented by': 'pres.',
            'Presented by': 'pres.',
            'Open': 'Open',
            'Classic': 'Classic',
            'Invitational': 'Inv',
            'International': "Int'l",
            'DP World Tour': 'DPWT',
        }
        
        short_name = name
        for old, new in replacements.items():
            short_name = short_name.replace(old, new)
        
        # Limit length
        if len(short_name) > 30:
            short_name = short_name[:27] + "..."
        
        return short_name
    
    def _format_score(self, score: str) -> str:
        """
        Format score for display.
        
        Args:
            score: Raw score string
            
        Returns:
            Formatted score
        """
        if score == 'E':
            return 'E'
        
        try:
            score_int = int(score)
            if score_int > 0:
                return f"+{score_int}"
            return str(score_int)
        except (ValueError, TypeError):
            return score
    
    def get_status(self) -> Dict:
        """
        Get current manager status for web UI.
        
        Returns:
            Status dictionary
        """
        active_tournaments = []
        for tour in self.enabled_tours:
            tournament = self.tournaments.get(tour)
            if tournament:
                # Count favorites in this tournament
                favorites_count = sum(1 for p in tournament.get('all_players', [])
                                    if self._is_favorite(p.get('id', '')))
                
                active_tournaments.append({
                    'tour': tour,
                    'tour_name': tournament['tour_name'],
                    'name': tournament['name'],
                    'status': tournament['status'],
                    'player_count': len(tournament.get('all_players', [])),
                    'leaders_shown': len(tournament.get('leaders', [])),
                    'favorites_found': favorites_count,
                })
        
        return {
            'enabled': self.golf_config.get('enabled', False),
            'enabled_tours': self.enabled_tours,
            'available_tours': list(AVAILABLE_TOURS.keys()),
            'active_tournaments': len(active_tournaments),
            'tournaments': active_tournaments,
            'favorite_golfers': len(self.favorite_golfers),
            'favorites_in_play': len(self._favorites_in_tournaments),
            'highlight_favorites': self.highlight_favorites,
            'show_favorites_section': self.show_favorites_section,
            'last_update': max(self.last_update.values()) if self.last_update else None,
            'error_counts': {k: v for k, v in self.error_counts.items() if v > 0},
            'update_interval': self.update_interval,
            'display_duration': self.get_display_duration(),
        }

    @staticmethod
    def get_available_tours() -> Dict:
        """
        Get list of available tours for configuration UI.
        
        Returns:
            Dictionary of tour info
        """
        return {
            tour_key: {
                'name': info['name'],
                'espn_slug': info['espn_slug'],
            }
            for tour_key, info in AVAILABLE_TOURS.items()
        }
