"""
Dynamic Duration Manager for LED Matrix Display
Intelligently calculates display durations based on content volume
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class DynamicDurationManager:
    """
    Manages dynamic display durations for various content types.
    
    Calculates appropriate display times based on:
    - Content volume (1 game vs 16 games)
    - Content type (static vs scrolling vs sports)
    - User-configured bounds (min/max)
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the dynamic duration manager.
        
        Args:
            config: Full system configuration dictionary
        """
        self.config = config
        self.display_config = config.get('display', {})
        
        # Check if dynamic durations are enabled
        self.enabled = self.display_config.get('use_dynamic_durations', False)
        
        # Get dynamic duration configuration
        self.dynamic_config = self.display_config.get('dynamic_duration_config', {})
        
        # Get fallback fixed durations
        self.fixed_durations = self.display_config.get('display_durations', {})
        
        # Load sport-specific configuration
        self.sports_config = self.dynamic_config.get('sports', {
            'base_per_item': 8,      # Seconds per item for single item
            'min_per_item': 4,       # Minimum seconds per item when many items
            'max_total': 180,         # Maximum total duration for any sport
            'scale_factor': 0.4      # How quickly to reduce per-item time (0-1)
        })
        
        # Static content configuration
        self.static_config = {
            'clock': self.dynamic_config.get('clock', {'fixed': 10}),
            'weather': self.dynamic_config.get('weather', {'per_screen': 10})
        }
        
        logger.info(f"DynamicDurationManager initialized - Enabled: {self.enabled}")
        if self.enabled:
            logger.info(f"Sports config: {self.sports_config}")
            logger.info(f"Static config: {self.static_config}")
    
    def get_duration(self, mode_key: str, manager: Any = None, item_count: Optional[int] = None) -> int:
        """
        Get the appropriate duration for a display mode.
        
        Args:
            mode_key: The display mode key (e.g., 'nfl_live', 'weather', 'clock')
            manager: The manager object (optional, for querying state)
            item_count: Number of items to display (optional override)
            
        Returns:
            Duration in seconds
        """

        # If dynamic durations disabled, return fixed duration
        if not self.enabled:
            return self.fixed_durations.get(mode_key, 30)
        
        # Determine item count if not provided
        if item_count is None and manager is not None:
            item_count = self._get_item_count(mode_key, manager)
        
        # Calculate duration based on mode type
        if mode_key == 'clock':
            return self._get_clock_duration()
        
        elif mode_key.startswith('weather'):
            return self._get_weather_duration(mode_key, item_count or 1)
        
        elif self._is_sports_mode(mode_key):
            return self._get_sports_duration(mode_key, item_count or 0)
        
        else:
            # Default to fixed duration for unknown modes
            if 'nfl' in mode_key.lower():
                logger.info(f"DEBUG: Not recognized as sports mode, returning default")
            return self.fixed_durations.get(mode_key, 30)
    
    def _get_item_count(self, mode_key: str, manager: Any) -> int:
        """
        logger.info(f"DEBUG _get_item_count: mode_key={mode_key}, manager={type(manager).__name__ if manager else None}, has_games_list={hasattr(manager, 'games_list') if manager else False}")
        Determine the number of items from a manager.
        
        Args:
            mode_key: The display mode key
            manager: The manager object
            
        Returns:
            Number of items to display
        """


        # Sports managers - check for games
        if hasattr(manager, 'games_list') and len(manager.games_list) > 0:
            count = len(manager.games_list)
            return count
        elif hasattr(manager, 'live_games'):
            return len(manager.live_games)
        elif hasattr(manager, 'recent_games'):
            return len(manager.recent_games)
        elif hasattr(manager, 'upcoming_games'):
            return len(manager.upcoming_games)
        # Weather managers - multiple screens
        elif mode_key.startswith('weather'):
            # Weather typically has 1-3 screens (current, hourly, daily)
            return 1
        
        # Default
        return 1
    
    def _is_sports_mode(self, mode_key: str) -> bool:
        """Check if a mode is a sports mode."""
        sports_keywords = [
            'nfl', 'nba', 'mlb', 'nhl', 'wnba', 'milb', 'soccer',
            'ncaa_fb', 'ncaa_baseball', 'ncaam_basketball', 'ncaaw_basketball',
            'ncaam_hockey', 'ncaaw_hockey', 'golf', 'tennis'
        ]
        return any(keyword in mode_key for keyword in sports_keywords)
    
    def _get_clock_duration(self) -> int:
        """Get duration for clock display."""
        return self.static_config['clock'].get('fixed', 10)
    
    def _get_weather_duration(self, mode_key: str, screen_count: int = 1) -> int:
        """
        Get duration for weather display.
        
        Args:
            mode_key: Weather mode key
            screen_count: Number of weather screens
            
        Returns:
            Duration in seconds
        """
        per_screen = self.static_config['weather'].get('per_screen', 15)
        return per_screen * screen_count
    
    def _get_sports_duration(self, mode_key: str, item_count: int) -> int:
        """
        Calculate duration for sports modes using smart scaling.
        
        Formula:
        - 1 game: base_per_item seconds (8s default)
        - Multiple games: Scales down per-item time
        - Max total: Capped at max_total (90s default)
        
        Args:
            mode_key: Sports mode key
            item_count: Number of games/items
            
        Returns:
            Duration in seconds
        """


        # If no items, return minimum duration
        if item_count == 0:
            return 5
        
        # Get config values
        base_per_item = self.sports_config['base_per_item']
        min_per_item = self.sports_config['min_per_item']
        max_total = self.sports_config['max_total']
        scale_factor = self.sports_config['scale_factor']
        
        # Single item - use base duration
        if item_count == 1:
            return base_per_item
        
        # Multiple items - calculate scaled duration
        # Linear scaling: per_item = base - (count - 1) * reduction_rate
        # reduction_rate = (base - min) * scale_factor / 10
        # This means it takes ~10-15 items to reach min_per_item
        reduction_rate = (base_per_item - min_per_item) * scale_factor / 10
        per_item_duration = max(min_per_item, base_per_item - (item_count - 1) * reduction_rate)
        
        # Calculate total duration
        total_duration = int(per_item_duration * item_count)
        
        # Apply max bound
        final_duration = min(total_duration, max_total)
        
        logger.debug(
            f"Sports duration calculated for {mode_key}: "
            f"{item_count} items, {per_item_duration:.1f}s each = {final_duration}s total"
        )
        
        return final_duration
    
    def get_config_summary(self) -> Dict[str, Any]:
        """
        Get a summary of current configuration.
        
        Returns:
            Dictionary with configuration summary
        """
        return {
            'enabled': self.enabled,
            'sports': self.sports_config,
            'static': self.static_config
        }
    
    def update_config(self, new_config: Dict[str, Any]):
        """
        Update configuration dynamically.
        
        Args:
            new_config: New configuration dictionary
        """
        self.dynamic_config = new_config.get('dynamic_duration_config', self.dynamic_config)
        self.sports_config = self.dynamic_config.get('sports', self.sports_config)
        self.static_config['clock'] = self.dynamic_config.get('clock', self.static_config['clock'])
        self.static_config['weather'] = self.dynamic_config.get('weather', self.static_config['weather'])
        
        logger.info("DynamicDurationManager configuration updated")


# Example usage and testing
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.DEBUG)
    
    # Test configuration
    test_config = {
        'display': {
            'use_dynamic_durations': True,
            'display_durations': {
                'nfl_live': 30,
                'clock': 15
            },
            'dynamic_duration_config': {
                'clock': {'fixed': 10},
                'weather': {'per_screen': 15},
                'sports': {
                    'base_per_item': 8,
                    'min_per_item': 4,
                    'max_total': 90,
                    'scale_factor': 0.4
                }
            }
        }
    }
    
    ddm = DynamicDurationManager(test_config)
    
    # Test scenarios
    print("\n=== Dynamic Duration Test Cases ===\n")
    
    # Clock - always fixed
    print(f"Clock (always same): {ddm.get_duration('clock', item_count=1)}s")
    
    # Weather - per screen
    print(f"Weather (3 screens): {ddm.get_duration('weather_current', item_count=3)}s")
    
    # Sports - various game counts
    for count in [0, 1, 3, 5, 10, 16]:
        duration = ddm.get_duration('nfl_live', item_count=count)
        per_game = duration / count if count > 0 else 0
        print(f"NFL ({count:2d} games): {duration:3d}s total ({per_game:.1f}s per game)")
    
    print("\n=== Config Summary ===\n")
    import json
    print(json.dumps(ddm.get_config_summary(), indent=2))
