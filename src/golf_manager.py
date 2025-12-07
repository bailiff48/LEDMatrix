"""
Golf tournament manager for LED Matrix display.
Fetches PGA and LPGA tournament data from ESPN API.
"""

import requests
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class GolfManager:
    """Manages golf tournament data and display."""
    
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
        
        # Tournament data cache
        self.tournaments = {
            'pga': None,
            'lpga': None
        }
        self.last_update = {}
        self.update_interval = self.golf_config.get('update_interval', 900)  # 15 min default
        
        # Display settings
        self.show_top_n = self.golf_config.get('show_top_n', 5)
        self.enabled_tours = self.golf_config.get('tours', ['pga', 'lpga'])
        
        # Error tracking
        self.error_count = 0
        self.max_errors = 5
        
        logger.info(f"GolfManager initialized: tours={self.enabled_tours}, top_n={self.show_top_n}")
        # Scrolling display state
        self._scroll_image = None
        self._scroll_position = 0
        self._scroll_speed = 2
        self._last_scroll_build = 0

    
    def update(self):
        """Fetch latest tournament data if needed."""
        if not self.golf_config.get('enabled', False):
            return
        
        current_time = time.time()
        
        # Update each enabled tour
        for tour in self.enabled_tours:
            last_update = self.last_update.get(tour, 0)
            if current_time - last_update >= self.update_interval:
                self._fetch_tournament(tour)
                self.last_update[tour] = current_time
    
    def _fetch_tournament(self, tour: str):
        """
        Fetch tournament data for a specific tour.
        
        Args:
            tour: 'pga' or 'lpga'
        """
        try:
            url = f"https://site.api.espn.com/apis/site/v2/sports/golf/{tour}/scoreboard"
            
            logger.debug(f"Fetching {tour.upper()} tournament data from {url}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            # Check for active events
            events = data.get('events', [])
            if not events:
                logger.debug(f"No active {tour.upper()} tournaments")
                self.tournaments[tour] = None
                self.error_count = 0
                return
            
            # Get the first (current) event
            event = events[0]
            tournament_data = self._parse_tournament(event, tour)
            
            if tournament_data:
                # Skip completed LPGA tournaments (off-season shows stale "FINAL" events)
                if tour == 'lpga' and tournament_data.get('completed', False):
                    logger.info(f"LPGA: Skipping completed tournament '{tournament_data['name']}' (status: {tournament_data['status']})")
                    self.tournaments[tour] = None
                else:
                    self.tournaments[tour] = tournament_data
                    self.error_count = 0
                    logger.info(f"{tour.upper()}: {tournament_data['name']} - {tournament_data['status']}")
            else:
                self.tournaments[tour] = None
                
        except requests.exceptions.RequestException as e:
            self.error_count += 1
            logger.error(f"Error fetching {tour.upper()} data: {e}")
            if self.error_count >= self.max_errors:
                logger.error(f"Max errors reached for {tour.upper()}, disabling updates")
        except Exception as e:
            logger.error(f"Unexpected error parsing {tour.upper()} data: {e}")
    
    def _parse_tournament(self, event: dict, tour: str) -> Optional[Dict]:
        """
        Parse tournament event data.
        
        Args:
            event: Event data from ESPN API
            tour: Tour abbreviation
            
        Returns:
            Parsed tournament data or None
        """
        try:
            tournament = {
                'tour': tour.upper(),
                'id': event.get('id'),
                'name': event.get('name', event.get('shortName', 'Tournament')),
                'status': 'In Progress',
                'leaders': []
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
            
            # Get top N players
            for player in sorted_players[:self.show_top_n]:
                player_info = self._parse_player(player)
                if player_info:
                    tournament['leaders'].append(player_info)
            
            return tournament  # Return even without leaders for preview mode
            
        except Exception as e:
            logger.error(f"Error parsing tournament data: {e}")
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
            status = competitor.get('status', {})
            
            # Get player name
            name = athlete.get('shortName', athlete.get('displayName', 'Unknown'))
            
            # Get score
            score = competitor.get('score', 'E')
            
            # Position will be calculated after sorting (ESPN doesn't provide it)
            position_data = status.get('position', {})
            position = position_data.get('displayName', None)  # Usually None from ESPN
            
            # Get thru indicator (holes completed)
            thru = status.get('thru', '')
            
            return {
                'name': name,
                'score': score,
                'position': position,
                'thru': thru
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
            Integer score value
        """
        try:
            if score_str == 'E':
                return 0
            return int(score_str)
        except (ValueError, TypeError):
            return 0
    
    def has_active_tournaments(self) -> bool:
        """Check if there are any active tournaments."""
        # If we haven't fetched yet, return True to allow first display attempt
        if not self.last_update:
            return True
        return any(t is not None for t in self.tournaments.values())

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
        Get display duration based on tournament mode and content.
        Preview mode (Tue-Wed): 8 seconds per upcoming tournament
        Active mode (Thu-Mon): 5 seconds base + 3 seconds per player shown
        """
        if self._is_tournament_active_day():
            # Dynamic: 10s base + 3s per player
            base_time = 10
            per_player_time = 3
            return base_time + (self.show_top_n * per_player_time)
        else:
            # Preview mode: count upcoming (non-completed) tournaments
            upcoming_count = 0
            for tour in self.enabled_tours:
                tournament = self.tournaments.get(tour)
                if tournament and not tournament.get('completed', False):
                    upcoming_count += 1
            # 8 seconds per upcoming tournament, minimum 8
            return max(10, upcoming_count * 10)
    
    def display(self, force_clear: bool = False):
        """Display golf tournament data with scrolling text."""
        from PIL import Image, ImageDraw, ImageFont
        import time
        
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
        """Build the wide scrolling image with all tournament data."""
        from PIL import Image, ImageDraw, ImageFont
        import time
        
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        
        # Check if we're in preview mode (Tue-Wed) or active mode (Thu-Mon)
        is_active_day = self._is_tournament_active_day()

        # Build text segments
        segments = []
        
        for tour in ['pga', 'lpga']:
            if tour not in self.enabled_tours:
                continue
            tournament = self.tournaments.get(tour)
            if not tournament:
                continue
            
            # Tournament header
            tour_label = tournament['tour'].upper()
            tournament_name = self._shorten_tournament_name(tournament['name'])
            segments.append({
                'text': f"{tour_label}: {tournament_name}",
                'color': (0, 255, 0),  # Green
                'bold': True
            })
            
            # On active days (Thu-Mon), show players. On preview days (Tue-Wed), show status
            if is_active_day:
                # Add separator
                segments.append({'text': ' | ', 'color': (100, 100, 100)})
                # Add players
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

                    # Format position (may include T for ties)
                    pos_display = position if position else str(i + 1)
                    segments.append({
                        'text': f"{pos_display}. {short_name} ({score_display})",
                        'color': (255, 255, 255)
                    })
                    # Add separator between players
                    if i < len(leaders) - 1:
                        segments.append({'text': ' | ', 'color': (100, 100, 100)})
            else:
                # Preview mode - skip completed tournaments, only show upcoming
                if tournament.get('completed', False):
                    # Remove the tournament header we just added
                    segments.pop()  # Remove the tournament name
                    continue
                # Show tournament status (start time)
                status = tournament.get('status', '')
                if status:
                    segments.append({'text': f' - {status}', 'color': (200, 200, 200)})
            # Add tour separator
            segments.append({'text': '  ●  ', 'color': (0, 255, 0)})

        # Remove trailing separator
        if segments and segments[-1]['text'].strip() in ['|', '●', '']:
            segments.pop()
        
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
        
        logger.debug(f"Built golf scroll image: {total_width}x{height} pixels")


    def draw(self, canvas):
        """
        Draw golf tournament data on LED matrix.
        
        Args:
            canvas: LED matrix canvas to draw on
        """
        if not self.has_active_tournaments():
            # Display "no tournaments" message
            text = "GOLF: No tournaments in progress"
            self.display_manager.draw_text(canvas, text, scroll=True)
            return
        
        # Build display text
        text_parts = []
        
        for tour in ['pga', 'lpga']:
            if tour not in self.enabled_tours:
                continue
                
            tournament = self.tournaments.get(tour)
            if not tournament:
                continue
            
            # Tournament header
            tour_name = tournament['tour']
            tournament_name = self._shorten_tournament_name(tournament['name'])
            
            text_parts.append(f"{tour_name}: {tournament_name}")
            
            # Add top 3 leaders
            for i, player in enumerate(tournament['leaders'][:3]):
                score_display = self._format_score(player['score'])
                text_parts.append(f"{player['name']} ({score_display})")
            
            # Separator between tours
            text_parts.append("•")
        
        # Remove trailing separator
        if text_parts and text_parts[-1] == "•":
            text_parts.pop()
        
        # Join and display
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
            'International': 'Intl'
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
        Get current manager status.
        
        Returns:
            Status dictionary
        """
        active_tournaments = []
        for tour, tournament in self.tournaments.items():
            if tournament:
                active_tournaments.append({
                    'tour': tour.upper(),
                    'name': tournament['name'],
                    'status': tournament['status'],
                    'leaders': len(tournament['leaders'])
                })
        
        return {
            'enabled': self.golf_config.get('enabled', False),
            'enabled_tours': self.enabled_tours,
            'active_tournaments': len(active_tournaments),
            'tournaments': active_tournaments,
            'last_update': max(self.last_update.values()) if self.last_update else None,
            'error_count': self.error_count,
            'update_interval': self.update_interval
        }
