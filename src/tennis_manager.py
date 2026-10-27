"""
Tennis match manager for LED Matrix display.
Fetches ATP and WTA match data from ESPN API with Grand Slam focus.

Enhanced with:
- Favorite player tracking and priority
- Round-aware sorting (later rounds = higher priority)
- Live match detection with separate favorite/all live priority
- Dynamic update intervals (faster when live)
- Start time display for upcoming matches
- Match rotation to ensure all matches get shown

Plugin Architecture Ready:
- Accepts cache_manager (optional for backward compatibility)
- Standard interface methods: update(), display(), has_content(), get_status()
- Live priority hook: should_interrupt_rotation()
- Config reload support: reload_config()
- Plugin metadata: PLUGIN_KEY, PLUGIN_TYPE
"""

import requests
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Any

logger = logging.getLogger(__name__)


class TennisManager:
    """
    Manages tennis match data and display.
    
    Plugin-ready content manager for LED Matrix system.
    Can be converted to full plugin by inheriting from BasePlugin
    when plugin architecture is implemented.
    """

    # Plugin metadata (for future plugin system)
    PLUGIN_KEY = "tennis"
    PLUGIN_TYPE = "content"  # Options: "sport", "ticker", "content", "static"
    PLUGIN_VERSION = "2.0.0"

    # Grand Slam tournaments
    GRAND_SLAMS = {
        'Australian Open': 'AUS OPEN',
        'French Open': 'FRENCH OPEN',
        'Roland Garros': 'FRENCH OPEN',
        'Wimbledon': 'WIMBLEDON',
        'US Open': 'US OPEN',
        'U.S. Open': 'US OPEN'
    }

    # Round priority scoring (higher = more important)
    ROUND_PRIORITY = {
        '7': 100,   # Final
        '6': 90,    # Semifinal
        '5': 80,    # Quarterfinal
        '4': 70,    # Round 4 (Round of 16)
        '3': 60,    # Round 3
        '2': 50,    # Round 2
        '1': 40,    # Round 1
        '14': 30,   # Qualifying Final
        '12': 20,   # Qualifying 2nd Round
        '11': 10,   # Qualifying 1st Round
    }

    # Short round names for display
    ROUND_SHORT_NAMES = {
        '7': 'F',
        '6': 'SF',
        '5': 'QF',
        '4': 'R16',
        '3': 'R3',
        '2': 'R2',
        '1': 'R1',
        '14': 'QF',
        '12': 'Q2',
        '11': 'Q1',
    }

    def __init__(self, config: dict, display_manager, cache_manager=None):
        """
        Initialize tennis manager.

        Args:
            config: Full configuration dictionary
            display_manager: Display manager instance for drawing
            cache_manager: Optional cache manager for data persistence (plugin-ready)
        """
        self.config = config
        self.display_manager = display_manager
        self.cache_manager = cache_manager  # Plugin-ready: use for persistent caching
        self.tennis_config = config.get(self.PLUGIN_KEY, {})  # Use PLUGIN_KEY for consistency

        # Match data cache
        self.matches = {
            'atp': [],
            'wta': []
        }
        self.last_update = {}
        
        # Update intervals
        self.update_interval = self.tennis_config.get('update_interval', 600)  # 10 min default
        self.live_update_interval = self.tennis_config.get('live_update_interval', 60)  # 1 min when live

        # Display settings
        self.majors_only = self.tennis_config.get('majors_only', False)
        self.show_completed = self.tennis_config.get('show_completed_matches', True)
        self.max_matches = self.tennis_config.get('max_matches_display', 5)
        self.completed_max_age_hours = self.tennis_config.get('completed_match_max_age_hours', 24)

        # Favorite players - list of dicts with 'id', 'name', 'tour'
        self.favorite_players = self.tennis_config.get('favorite_players', [])
        self.favorite_player_ids = set(str(p.get('id', '')) for p in self.favorite_players if p.get('id'))
        self.highlight_favorites = self.tennis_config.get('highlight_favorites', True)

        # Live priority settings
        self.live_priority_favorites = self.tennis_config.get('live_priority_favorites', True)
        self.live_priority_all = self.tennis_config.get('live_priority_all', False)

        # Error tracking
        self.error_count = 0
        self.max_errors = 5

        # Scrolling display state
        self._scroll_image = None
        self._scroll_position = 0
        self._scroll_speed = 2
        self._last_scroll_build = 0

        # Match rotation tracking (for fairness in display)
        self._shown_match_ids: Set[str] = set()
        self._last_rotation_reset = time.time()
        self._rotation_reset_interval = 300  # Reset shown tracking every 5 minutes

        logger.info(f"TennisManager initialized: majors_only={self.majors_only}, "
                   f"favorites={len(self.favorite_players)}, "
                   f"live_priority_favorites={self.live_priority_favorites}, "
                   f"live_priority_all={self.live_priority_all}")

    def update(self):
        """Fetch latest match data if needed."""
        if not self.tennis_config.get('enabled', False):
            return

        current_time = time.time()

        # Use faster update interval if we have live matches
        effective_interval = self.live_update_interval if self.has_live_matches() else self.update_interval

        # Update each tour
        for tour in ['atp', 'wta']:
            last_update = self.last_update.get(tour, 0)
            if current_time - last_update >= effective_interval:
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
            cache_key = f"tennis_{tour}_{date_str}"
            
            # Try cache first if cache_manager available
            if self.cache_manager:
                cached_data = self.cache_manager.get(cache_key)
                if cached_data and isinstance(cached_data, list):
                    logger.debug(f"Using cached {tour.upper()} matches")
                    self.matches[tour] = cached_data
                    self.error_count = 0
                    return
            
            url = f"https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard?dates={date_str}"

            logger.debug(f"Fetching {tour.upper()} matches from {url}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            data = response.json()

            # Parse events (tournaments)
            events = data.get('events', [])
            if not events:
                logger.debug(f"No {tour.upper()} tournaments today")
                self.matches[tour] = []
                self.error_count = 0
                return

            # Parse and filter matches from all tournaments
            parsed_matches = []

            for event in events:
                tournament_name = event.get('name', 'Tournament')
                is_major = self._is_grand_slam(tournament_name)
                major_abbrev = self._get_major_abbreviation(tournament_name) if is_major else None

                # Skip non-majors if majors_only is set
                if self.majors_only and not is_major:
                    continue

                # Tennis API uses groupings (Men's Singles, Women's Singles, etc.)
                groupings = event.get('groupings', [])

                for grouping in groupings:
                    group_info = grouping.get('grouping', {})
                    group_name = group_info.get('displayName', '')

                    # Only process singles matches (skip doubles/mixed)
                    if 'Singles' not in group_name:
                        continue

                    competitions = grouping.get('competitions', [])

                    for competition in competitions:
                        match_data = self._parse_match(
                            competition,
                            tour,
                            tournament_name,
                            is_major,
                            major_abbrev
                        )

                        if match_data:
                            # Apply completed filter (but always keep favorites)
                            if not self.show_completed and match_data['completed']:
                                if not match_data['has_favorite']:
                                    continue
                            parsed_matches.append(match_data)

            self.matches[tour] = parsed_matches
            self.error_count = 0
            
            # Store in cache if cache_manager available
            if self.cache_manager and parsed_matches:
                cache_ttl = self.live_update_interval if self.has_live_matches() else self.update_interval
                self.cache_manager.set(f"tennis_{tour}_{datetime.now().strftime('%Y%m%d')}", 
                                       parsed_matches, ttl=cache_ttl)

            # Log summary
            total = len(parsed_matches)
            live = sum(1 for m in parsed_matches if m['status'] == 'LIVE')
            favorites = sum(1 for m in parsed_matches if m['has_favorite'])
            if parsed_matches:
                logger.info(f"{tour.upper()}: Found {total} matches ({live} live, {favorites} with favorites)")
            else:
                logger.debug(f"{tour.upper()}: No matches after filtering")

        except requests.exceptions.RequestException as e:
            self.error_count += 1
            logger.error(f"Error fetching {tour.upper()} data: {e}")
            if self.error_count >= self.max_errors:
                logger.error(f"Max errors reached for {tour.upper()}, disabling updates")
        except Exception as e:
            logger.error(f"Unexpected error parsing {tour.upper()} data: {e}", exc_info=True)

    def _parse_match(self, competition: dict, tour: str, tournament_name: str,
                     is_major: bool, major_abbrev: Optional[str]) -> Optional[Dict]:
        """
        Parse individual match data from a competition.

        Args:
            competition: Competition/match data from ESPN API
            tour: Tour abbreviation
            tournament_name: Name of the tournament
            is_major: Whether this is a Grand Slam
            major_abbrev: Abbreviated name for Grand Slam

        Returns:
            Parsed match data or None
        """
        try:
            # Get unique match ID
            match_id = competition.get('id', '')

            # Get status
            status = competition.get('status', {})
            status_type = status.get('type', {})
            status_name = status_type.get('name', 'STATUS_SCHEDULED')
            state = status_type.get('state', 'pre')
            completed = status_type.get('completed', False)

            # Get start time
            start_date_str = competition.get('date', '')
            start_time = None
            if start_date_str:
                try:
                    start_time = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                except:
                    pass

            # Get round info
            round_data = competition.get('round', {})
            round_id = str(round_data.get('id', '0'))
            round_name = round_data.get('displayName', '')
            round_short = self.ROUND_SHORT_NAMES.get(round_id, round_name[:3] if round_name else '')
            round_priority = self.ROUND_PRIORITY.get(round_id, 0)

            # Get competitors (players)
            competitors = competition.get('competitors', [])
            if len(competitors) < 2:
                return None

            # Parse players with ID for favorite matching
            player1 = self._parse_player(competitors[0])
            player2 = self._parse_player(competitors[1])

            if not player1 or not player2:
                return None
            
            # Skip matches where both players are TBD/Unknown (undetermined bracket matches)
            p1_name = player1.get('name', '').lower()
            p2_name = player2.get('name', '').lower()
            if p1_name in ('unknown', 'tbd', '') and p2_name in ('unknown', 'tbd', ''):
                logger.debug(f"Skipping TBD vs TBD match in {tournament_name}")
                return None

            # Check if either player is a favorite
            has_favorite = (
                player1.get('id') in self.favorite_player_ids or
                player2.get('id') in self.favorite_player_ids
            )

            # Determine match status
            if state == 'in' or status_name == 'STATUS_IN_PROGRESS':
                match_status = 'LIVE'
            elif completed or status_name in ['STATUS_FINAL', 'STATUS_RETIRED', 'STATUS_WALKOVER']:
                match_status = 'FINAL'
            else:
                match_status = 'UPCOMING'

            # Get venue/court info
            venue = competition.get('venue', {})
            court = venue.get('fullName', venue.get('court', ''))

            return {
                'id': match_id,
                'tour': tour.upper(),
                'tournament': tournament_name,
                'is_major': is_major,
                'major_abbrev': major_abbrev,
                'round_id': round_id,
                'round_name': round_name,
                'round_short': round_short,
                'round_priority': round_priority,
                'court': court,
                'player1': player1,
                'player2': player2,
                'status': match_status,
                'completed': completed,
                'state': state,
                'start_time': start_time,
                'has_favorite': has_favorite,
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

            # Get player ID for favorite matching
            player_id = str(athlete.get('id', ''))

            # Get player name
            name = athlete.get('shortName', athlete.get('displayName', ''))
            
            # Check for TBD/placeholder names
            if not name or name.lower() in ('tbd', 'to be determined', 'winner', ''):
                name = 'TBD'

            # Get country/flag
            country = athlete.get('flag', {}).get('alt', '')

            # Get score/sets
            score = competitor.get('score', {})
            value = score.get('value', '')

            # Get individual set scores
            line_scores = competitor.get('linescores', [])
            sets = [str(ls.get('value', '')) for ls in line_scores]

            # Check if winner
            winner = competitor.get('winner', False)

            return {
                'id': player_id,
                'name': name,
                'country': country,
                'score': value,
                'sets': sets,
                'winner': winner,
                'is_favorite': player_id in self.favorite_player_ids
            }

        except Exception as e:
            logger.error(f"Error parsing player: {e}")
            return None

    def _is_grand_slam(self, tournament_name: str) -> bool:
        """Check if tournament is a Grand Slam."""
        return any(major in tournament_name for major in self.GRAND_SLAMS.keys())

    def _get_major_abbreviation(self, tournament_name: str) -> str:
        """Get abbreviated name for Grand Slam."""
        for major, abbrev in self.GRAND_SLAMS.items():
            if major in tournament_name:
                return abbrev
        return tournament_name

    # =========================================================================
    # PLUGIN INTERFACE METHODS (standardized for future plugin system)
    # =========================================================================

    def has_content(self) -> bool:
        """
        Plugin interface: Check if manager has content to display.
        Alias for has_active_matches() for plugin compatibility.
        """
        return self.has_active_matches()

    def should_interrupt_rotation(self) -> bool:
        """
        Plugin interface: Check if this content should interrupt normal rotation.
        Alias for should_interrupt_for_live() for plugin compatibility.
        """
        return self.should_interrupt_for_live()

    def get_display_duration(self) -> int:
        """
        Plugin interface: Return display duration based on scroll content.
        
        Calculates time needed for one full scroll cycle plus buffer.
        Formula: (scroll_width / scroll_speed) * frame_time * 1.5
        
        Returns:
            Duration in seconds (minimum 15, default 30 if no scroll image)
        """
        # Check for config override first
        config_duration = self.tennis_config.get('display_duration')
        if config_duration:
            return config_duration
        
        # Calculate based on scroll image if available
        if self._scroll_image and self._scroll_image.width > 0:
            scroll_width = self._scroll_image.width
            scroll_speed = getattr(self, '_scroll_speed', 2)
            frame_time = 0.03  # matches time.sleep in display()
            
            # Time for one full scroll cycle
            frames_needed = scroll_width / scroll_speed
            scroll_time = frames_needed * frame_time
            
            # Add 50% buffer for comfortable viewing, minimum 15 seconds
            duration = max(15, int(scroll_time * 1.5))
            
            # Cap at reasonable maximum
            return min(duration, 90)
        
        # Fallback: estimate based on match count
        total_matches = sum(len(m) for m in self.matches.values())
        if total_matches > 0:
            # ~5 seconds per match, minimum 15
            return max(15, min(total_matches * 5, 60))
        
        return 15  # Default minimum

    # =========================================================================
    # LIVE MATCH DETECTION METHODS (for display_controller integration)
    # =========================================================================

    @property
    def games_list(self) -> List[Dict]:
        """
        Property for DynamicDurationManager compatibility.
        Returns flattened list of all matches for duration calculation.
        """
        all_matches = []
        for tour in ['atp', 'wta']:
            all_matches.extend(self.matches.get(tour, []))
        return all_matches

    def has_active_matches(self) -> bool:
        """Check if there are any active matches (for mode availability)."""
        return any(len(matches) > 0 for matches in self.matches.values())

    def has_live_matches(self) -> bool:
        """Check if there are any LIVE matches right now."""
        for matches in self.matches.values():
            for match in matches:
                if match.get('status') == 'LIVE':
                    return True
        return False

    def has_live_favorite_matches(self) -> bool:
        """Check if any favorite players have LIVE matches."""
        for matches in self.matches.values():
            for match in matches:
                if match.get('status') == 'LIVE' and match.get('has_favorite'):
                    return True
        return False

    def should_interrupt_for_live(self) -> bool:
        """
        Determine if tennis should interrupt normal rotation for live content.
        
        Returns:
            True if live priority conditions are met
        """
        if self.live_priority_favorites and self.has_live_favorite_matches():
            return True
        if self.live_priority_all and self.has_live_matches():
            return True
        return False

    # =========================================================================
    # SMART SORTING
    # =========================================================================

    def _sort_matches_by_priority(self, matches: List[Dict]) -> List[Dict]:
        """
        Sort matches by priority:
        1a. LIVE + Favorite
        1b. LIVE + Non-favorite  
        2a. Favorite UPCOMING
        2b. Favorite COMPLETED (recent)
        3. Current/high round non-favorites
        4. Earlier round non-favorites
        
        Within each tier, sort by round priority (later rounds first)
        """
        def priority_key(match):
            status = match.get('status', 'UPCOMING')
            has_fav = match.get('has_favorite', False)
            round_pri = match.get('round_priority', 0)
            
            # Primary tier (lower = higher priority)
            if status == 'LIVE' and has_fav:
                tier = 0  # 1a: LIVE + Favorite
            elif status == 'LIVE':
                tier = 1  # 1b: LIVE + Non-favorite
            elif has_fav and status == 'UPCOMING':
                tier = 2  # 2a: Favorite UPCOMING
            elif has_fav and status == 'FINAL':
                tier = 3  # 2b: Favorite COMPLETED
            elif status == 'UPCOMING':
                tier = 4  # 3: Non-favorite upcoming
            else:
                tier = 5  # 4: Non-favorite completed
            
            # Secondary: round priority (negate so higher round = lower key = higher priority)
            # Tertiary: start time for upcoming matches
            start_time = match.get('start_time')
            time_key = start_time.timestamp() if start_time else 0
            
            return (tier, -round_pri, time_key)
        
        return sorted(matches, key=priority_key)

    def _get_display_matches(self) -> List[Dict]:
        """
        Get matches to display with smart selection.
        
        Ensures favorites always shown, rotates through others.
        """
        # Collect all matches
        all_matches = []
        for tour in ['atp', 'wta']:
            all_matches.extend(self.matches.get(tour, []))

        if not all_matches:
            return []

        # Sort by priority
        sorted_matches = self._sort_matches_by_priority(all_matches)

        # Separate favorites and non-favorites
        favorite_matches = [m for m in sorted_matches if m.get('has_favorite')]
        other_matches = [m for m in sorted_matches if not m.get('has_favorite')]

        # Reset rotation tracking periodically
        current_time = time.time()
        if current_time - self._last_rotation_reset > self._rotation_reset_interval:
            self._shown_match_ids.clear()
            self._last_rotation_reset = current_time

        # Always include all favorite matches
        display_matches = list(favorite_matches)

        # Fill remaining slots with other matches, preferring unseen ones
        remaining_slots = max(0, self.max_matches - len(display_matches))
        
        if remaining_slots > 0 and other_matches:
            # Prefer matches we haven't shown recently
            unseen = [m for m in other_matches if m.get('id') not in self._shown_match_ids]
            seen = [m for m in other_matches if m.get('id') in self._shown_match_ids]
            
            # Take from unseen first, then seen
            fill_matches = (unseen + seen)[:remaining_slots]
            display_matches.extend(fill_matches)
            
            # Track what we're showing
            for m in fill_matches:
                self._shown_match_ids.add(m.get('id', ''))

        return display_matches

    # =========================================================================
    # DISPLAY METHODS
    # =========================================================================

    def display(self, force_clear: bool = False):
        """Display tennis match data with scrolling text."""
        from PIL import Image, ImageDraw, ImageFont

        if not self.has_active_matches():
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

        # If image fits on screen, just display it centered
        if total_width <= width:
            self.display_manager.clear()
            x_offset = (width - total_width) // 2
            self.display_manager.image.paste(self._scroll_image, (x_offset, 0))
            self.display_manager.update_display()
            return

        # Scrolling display
        scroll_speed = getattr(self, '_scroll_speed', 2)
        self._scroll_position = (self._scroll_position + scroll_speed) % total_width

        # Create visible window with wrap-around
        visible = Image.new('RGB', (width, height), (0, 0, 0))
        pos = self._scroll_position

        # First part
        first_chunk_width = min(width, total_width - pos)
        if first_chunk_width > 0:
            chunk = self._scroll_image.crop((pos, 0, pos + first_chunk_width, height))
            visible.paste(chunk, (0, 0))

        # Wrap-around part
        if first_chunk_width < width:
            remaining = width - first_chunk_width
            chunk = self._scroll_image.crop((0, 0, remaining, height))
            visible.paste(chunk, (first_chunk_width, 0))

        self.display_manager.image.paste(visible, (0, 0))
        self.display_manager.update_display()
        time.sleep(0.03)

    def _needs_rebuild(self) -> bool:
        """Check if scroll image needs rebuilding."""
        current_time = time.time()
        if not hasattr(self, '_last_scroll_build') or self._last_scroll_build == 0:
            return True
        # Rebuild more frequently if we have live matches
        rebuild_interval = 30 if self.has_live_matches() else 60
        return current_time - self._last_scroll_build > rebuild_interval

    def _build_scroll_image(self):
        """Build the wide scrolling image with match information."""
        from PIL import Image, ImageDraw, ImageFont

        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height

        # Get prioritized matches
        display_matches = self._get_display_matches()

        if not display_matches:
            self._scroll_image = None
            return

        # Build segments with colors
        segments = []

        # Header
        segments.append({
            'text': 'TENNIS: ',
            'color': (0, 255, 0)
        })

        for i, match in enumerate(display_matches):
            match_text = self._format_match_display(match)
            if match_text:
                # Color coding
                status = match.get('status', 'UPCOMING')
                has_fav = match.get('has_favorite', False)

                if status == 'LIVE' and has_fav:
                    color = (255, 0, 255)    # Magenta for LIVE favorite
                elif status == 'LIVE':
                    color = (255, 255, 0)    # Yellow for LIVE
                elif has_fav:
                    color = (0, 255, 255)    # Cyan for favorite
                elif status == 'FINAL':
                    color = (150, 150, 150)  # Gray for completed
                else:
                    color = (255, 255, 255)  # White for upcoming

                # Add star prefix for favorites
                if has_fav and self.highlight_favorites:
                    segments.append({'text': '* ', 'color': (255, 215, 0)})  # Gold star

                segments.append({
                    'text': match_text,
                    'color': color
                })

                # Separator
                if i < len(display_matches) - 1:
                    segments.append({'text': ' | ', 'color': (100, 100, 100)})

        if len(segments) <= 1:  # Only header
            self._scroll_image = None
            return

        # Load font
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 12)
        except:
            font = ImageFont.load_default()

        # Calculate total width
        temp_img = Image.new('RGB', (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)

        total_text_width = 0
        for seg in segments:
            bbox = temp_draw.textbbox((0, 0), seg['text'], font=font)
            total_text_width += bbox[2] - bbox[0]

        total_width = total_text_width + width

        # Create scroll image
        scroll_img = Image.new('RGB', (total_width, height), (0, 0, 0))
        draw = ImageDraw.Draw(scroll_img)

        x = width // 2
        y = (height - 12) // 2

        for seg in segments:
            text = seg['text']
            color = seg['color']
            draw.text((x, y), text, font=font, fill=color)
            bbox = draw.textbbox((0, 0), text, font=font)
            x += bbox[2] - bbox[0]

        self._scroll_image = scroll_img
        self._scroll_position = 0
        self._last_scroll_build = time.time()

        logger.debug(f"Built tennis scroll image: {total_width}x{height} pixels, {len(display_matches)} matches")

    def _format_match_display(self, match: Dict) -> str:
        """
        Format match for scrolling display with round info and times.
        """
        try:
            # Tournament name
            tournament = match.get('major_abbrev') or match.get('tournament', '')
            if len(tournament) > 12 and not match.get('is_major'):
                tournament = tournament[:10] + ".."

            # Round info
            round_short = match.get('round_short', '')
            
            player1 = match['player1']
            player2 = match['player2']
            status = match['status']

            # Build display based on status
            if status == 'FINAL':
                if player1.get('winner'):
                    winner, loser = player1['name'], player2['name']
                    sets = self._format_sets(player1['sets'], player2['sets'])
                else:
                    winner, loser = player2['name'], player1['name']
                    sets = self._format_sets(player2['sets'], player1['sets'])
                
                round_str = f"[{round_short}] " if round_short else ""
                return f"{tournament} {round_str}{winner} d. {loser} {sets}"

            elif status == 'LIVE':
                sets = self._format_sets(player1['sets'], player2['sets'])
                round_str = f"[{round_short}] " if round_short else ""
                return f"{tournament} {round_str}{player1['name']} vs {player2['name']} {sets} [LIVE]"

            else:  # UPCOMING
                round_str = f"[{round_short}] " if round_short else ""
                time_str = ""
                if match.get('start_time'):
                    try:
                        local_time = match['start_time'].strftime('%I:%M%p').lstrip('0').lower()
                        time_str = f" @{local_time}"
                    except:
                        pass
                return f"{tournament} {round_str}{player1['name']} vs {player2['name']}{time_str}"

        except Exception as e:
            logger.error(f"Error formatting match: {e}")
            return ""

    def _format_match(self, match: Dict) -> str:
        """Legacy format match method - calls new method."""
        return self._format_match_display(match)

    def _format_sets(self, sets1: List, sets2: List) -> str:
        """Format set scores for display."""
        if not sets1 or not sets2:
            return ""
        formatted = []
        for s1, s2 in zip(sets1, sets2):
            if s1 and s2:
                formatted.append(f"{s1}-{s2}")
        return " ".join(formatted)

    # =========================================================================
    # LEGACY DRAW METHOD (for canvas-based display if needed)
    # =========================================================================

    def draw(self, canvas):
        """Draw tennis match data on LED matrix (legacy canvas method)."""
        if not self.has_active_matches():
            text = "TENNIS: No matches" if not self.majors_only else "TENNIS: No majors"
            self.display_manager.draw_text(canvas, text, scroll=True)
            return

        display_matches = self._get_display_matches()
        text_parts = [self._format_match_display(m) for m in display_matches if m]
        text_parts = [t for t in text_parts if t]
        
        display_text = " | ".join(text_parts)
        self.display_manager.draw_text(canvas, display_text, scroll=True)

    # =========================================================================
    # STATUS / API METHODS
    # =========================================================================

    def get_status(self) -> Dict:
        """Get current manager status for web API."""
        total_matches = sum(len(matches) for matches in self.matches.values())

        live_matches = []
        favorite_matches = []
        
        for tour, matches in self.matches.items():
            for match in matches:
                if match['status'] == 'LIVE':
                    live_matches.append({
                        'tour': tour.upper(),
                        'tournament': match.get('major_abbrev', match['tournament']),
                        'matchup': f"{match['player1']['name']} vs {match['player2']['name']}",
                        'has_favorite': match.get('has_favorite', False),
                        'round': match.get('round_short', '')
                    })
                if match.get('has_favorite'):
                    favorite_matches.append({
                        'tour': tour.upper(),
                        'status': match['status'],
                        'matchup': f"{match['player1']['name']} vs {match['player2']['name']}",
                        'round': match.get('round_short', '')
                    })

        return {
            'enabled': self.tennis_config.get('enabled', False),
            'majors_only': self.majors_only,
            'total_matches': total_matches,
            'live_matches': len(live_matches),
            'live_match_details': live_matches,
            'favorite_matches': len(favorite_matches),
            'favorite_match_details': favorite_matches,
            'has_live_favorites': self.has_live_favorite_matches(),
            'live_priority_favorites': self.live_priority_favorites,
            'live_priority_all': self.live_priority_all,
            'last_update': max(self.last_update.values()) if self.last_update else None,
            'error_count': self.error_count,
            'update_interval': self.update_interval,
            'live_update_interval': self.live_update_interval,
            'effective_interval': self.live_update_interval if self.has_live_matches() else self.update_interval
        }

    def reload_config(self, config: dict = None):
        """
        Reload configuration (called when config changes via web UI).
        
        Args:
            config: Optional new config dict. If None, uses existing self.config.
        """
        if config is not None:
            self.config = config
        self.tennis_config = self.config.get(self.PLUGIN_KEY, {})
        
        self.majors_only = self.tennis_config.get('majors_only', False)
        self.show_completed = self.tennis_config.get('show_completed_matches', True)
        self.max_matches = self.tennis_config.get('max_matches_display', 5)
        self.update_interval = self.tennis_config.get('update_interval', 600)
        self.live_update_interval = self.tennis_config.get('live_update_interval', 60)
        
        self.favorite_players = self.tennis_config.get('favorite_players', [])
        self.favorite_player_ids = set(str(p.get('id', '')) for p in self.favorite_players if p.get('id'))
        self.highlight_favorites = self.tennis_config.get('highlight_favorites', True)
        
        self.live_priority_favorites = self.tennis_config.get('live_priority_favorites', True)
        self.live_priority_all = self.tennis_config.get('live_priority_all', False)
        
        # Force rebuild on next display
        self._scroll_image = None
        self._last_scroll_build = 0
        
        logger.info(f"TennisManager config reloaded: favorites={len(self.favorite_players)}, "
                   f"live_priority_favorites={self.live_priority_favorites}")
