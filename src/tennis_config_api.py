"""
API routes for tennis configuration.
Handles GET/POST for tennis settings, favorites management, and player search.
"""

from flask import jsonify, request
import logging
import json
import os

logger = logging.getLogger(__name__)


def register_tennis_config_routes(app):
    """Register tennis configuration routes with the Flask app."""
    
    @app.route('/api/tennis-config', methods=['GET'])
    def get_tennis_config():
        """Get current tennis configuration."""
        try:
            from src.config_manager import ConfigManager
            config_manager = ConfigManager()
            config = config_manager.load_config()
            
            # Get tennis config with defaults
            tennis_config = config.get('tennis', {})
            
            response = {
                'enabled': tennis_config.get('enabled', False),
                'tours': tennis_config.get('tours', ['atp', 'wta']),
                'majors_only': tennis_config.get('majors_only', True),
                'show_completed_matches': tennis_config.get('show_completed_matches', False),
                'max_matches_display': tennis_config.get('max_matches_display', 5),
                'update_interval': tennis_config.get('update_interval', 600),
                'favorite_players': tennis_config.get('favorite_players', []),
                'highlight_favorites': tennis_config.get('highlight_favorites', True),
                'show_favorites_section': tennis_config.get('show_favorites_section', True)
            }
            
            return jsonify(response)
            
        except Exception as e:
            logger.error(f"Error loading tennis config: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/tennis-config', methods=['POST'])
    def save_tennis_config():
        """Save tennis configuration."""
        try:
            from src.config_manager import ConfigManager
            config_manager = ConfigManager()
            
            # Get current config
            config = config_manager.load_config()
            
            # Get posted data
            data = request.json
            
            # Update tennis section
            if 'tennis' not in config:
                config['tennis'] = {}
            
            config['tennis']['enabled'] = data.get('enabled', False)
            config['tennis']['tours'] = data.get('tours', ['atp', 'wta'])
            config['tennis']['majors_only'] = data.get('majors_only', True)
            config['tennis']['show_completed_matches'] = data.get('show_completed_matches', False)
            config['tennis']['max_matches_display'] = int(data.get('max_matches_display', 5))
            config['tennis']['update_interval'] = int(data.get('update_interval', 600))
            
            # Favorites settings
            if 'highlight_favorites' in data:
                config['tennis']['highlight_favorites'] = data.get('highlight_favorites', True)
            if 'show_favorites_section' in data:
                config['tennis']['show_favorites_section'] = data.get('show_favorites_section', True)
            
            # Also update display duration if provided
            if 'display_duration' in data:
                if 'display' not in config:
                    config['display'] = {}
                if 'display_durations' not in config['display']:
                    config['display']['display_durations'] = {}
                config['display']['display_durations']['tennis'] = int(data.get('display_duration', 25))
            
            # Save config
            config_manager.save_config(config)
            
            logger.info(f"Tennis config saved: enabled={data.get('enabled')}, tours={data.get('tours')}, majors_only={data.get('majors_only')}")
            
            return jsonify({
                'status': 'success',
                'message': 'Tennis configuration saved successfully'
            })
            
        except Exception as e:
            logger.error(f"Error saving tennis config: {e}")
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/tennis-status', methods=['GET'])
    def get_tennis_status():
        """Get current tennis manager status."""
        try:
            from src.config_manager import ConfigManager
            
            config_manager = ConfigManager()
            config = config_manager.load_config()
            tennis_config = config.get('tennis', {})
            
            # Basic status from config
            status = {
                'enabled': tennis_config.get('enabled', False),
                'tours': tennis_config.get('tours', ['atp', 'wta']),
                'majors_only': tennis_config.get('majors_only', True),
                'show_completed_matches': tennis_config.get('show_completed_matches', False),
                'update_interval': tennis_config.get('update_interval', 600),
                'total_matches': 0,
                'live_matches': 0,
                'favorites_count': len(tennis_config.get('favorite_players', [])),
                'note': 'Real-time match data only available in display process'
            }
            
            return jsonify(status)
            
        except Exception as e:
            logger.error(f"Error getting tennis status: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/tennis-tours', methods=['GET'])
    def get_tennis_tours():
        """Get available tennis tours."""
        tours = [
            {'id': 'atp', 'name': 'ATP Tour', 'description': "Men's professional tennis"},
            {'id': 'wta', 'name': 'WTA Tour', 'description': "Women's professional tennis"}
        ]
        return jsonify({'tours': tours})
    
    # ==================== FAVORITES MANAGEMENT ====================
    
    @app.route('/api/tennis-favorites', methods=['GET'])
    def get_tennis_favorites():
        """Get list of favorite tennis players."""
        try:
            from src.config_manager import ConfigManager
            config_manager = ConfigManager()
            config = config_manager.load_config()
            
            tennis_config = config.get('tennis', {})
            favorites = tennis_config.get('favorite_players', [])
            
            return jsonify({
                'favorites': favorites,
                'count': len(favorites)
            })
            
        except Exception as e:
            logger.error(f"Error getting tennis favorites: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/tennis-favorites', methods=['POST'])
    def set_tennis_favorites():
        """Set the complete list of favorite tennis players."""
        try:
            from src.config_manager import ConfigManager
            config_manager = ConfigManager()
            config = config_manager.load_config()
            
            data = request.json
            favorites = data.get('favorites', [])
            
            if 'tennis' not in config:
                config['tennis'] = {}
            
            config['tennis']['favorite_players'] = favorites
            config_manager.save_config(config)
            
            logger.info(f"Tennis favorites updated: {len(favorites)} players")
            
            return jsonify({
                'status': 'success',
                'count': len(favorites)
            })
            
        except Exception as e:
            logger.error(f"Error setting tennis favorites: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/tennis-favorites/add', methods=['POST'])
    def add_tennis_favorite():
        """Add a single player to favorites."""
        try:
            from src.config_manager import ConfigManager
            config_manager = ConfigManager()
            config = config_manager.load_config()
            
            data = request.json
            player = data.get('player', {})
            
            if not player.get('id') or not player.get('name'):
                return jsonify({'error': 'Player id and name required'}), 400
            
            if 'tennis' not in config:
                config['tennis'] = {}
            if 'favorite_players' not in config['tennis']:
                config['tennis']['favorite_players'] = []
            
            # Check for duplicate
            existing_ids = [p.get('id') for p in config['tennis']['favorite_players']]
            if player['id'] in existing_ids:
                return jsonify({
                    'status': 'exists',
                    'message': f"{player['name']} is already a favorite"
                })
            
            # Add player
            config['tennis']['favorite_players'].append(player)
            config_manager.save_config(config)
            
            logger.info(f"Added tennis favorite: {player['name']} (ID: {player['id']})")
            
            return jsonify({
                'status': 'success',
                'message': f"Added {player['name']} to favorites",
                'count': len(config['tennis']['favorite_players'])
            })
            
        except Exception as e:
            logger.error(f"Error adding tennis favorite: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/tennis-favorites/remove', methods=['POST'])
    def remove_tennis_favorite():
        """Remove a player from favorites."""
        try:
            from src.config_manager import ConfigManager
            config_manager = ConfigManager()
            config = config_manager.load_config()
            
            data = request.json
            player_id = data.get('player_id') or data.get('id')
            
            if not player_id:
                return jsonify({'error': 'Player ID required'}), 400
            
            tennis_config = config.get('tennis', {})
            favorites = tennis_config.get('favorite_players', [])
            
            # Find and remove
            original_count = len(favorites)
            favorites = [p for p in favorites if str(p.get('id')) != str(player_id)]
            
            if len(favorites) == original_count:
                return jsonify({
                    'status': 'not_found',
                    'message': 'Player not in favorites'
                })
            
            config['tennis']['favorite_players'] = favorites
            config_manager.save_config(config)
            
            logger.info(f"Removed tennis favorite ID: {player_id}")
            
            return jsonify({
                'status': 'success',
                'message': 'Removed from favorites',
                'count': len(favorites)
            })
            
        except Exception as e:
            logger.error(f"Error removing tennis favorite: {e}")
            return jsonify({'error': str(e)}), 500
    
    # ==================== PLAYER SEARCH ====================
    
    @app.route('/api/tennis-athletes/search', methods=['GET'])
    def search_tennis_athletes():
        """Search for tennis players in active tournaments."""
        try:
            import requests
            from datetime import datetime
            
            query = request.args.get('q', '').lower().strip()
            tour_filter = request.args.get('tour', 'all').lower()
            
            if len(query) < 2:
                return jsonify({'athletes': [], 'message': 'Query too short'})
            
            athletes = []
            seen_ids = set()
            
            # Search each tour
            tours_to_search = ['atp', 'wta'] if tour_filter == 'all' else [tour_filter]
            date_str = datetime.now().strftime('%Y%m%d')
            
            for tour in tours_to_search:
                try:
                    url = f"https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard?dates={date_str}"
                    response = requests.get(url, timeout=10)
                    
                    if response.status_code != 200:
                        continue
                    
                    data = response.json()
                    events = data.get('events', [])
                    
                    for event in events:
                        tournament = event.get('name', 'Unknown Tournament')
                        competitions = event.get('competitions', [])
                        
                        for comp in competitions:
                            competitors = comp.get('competitors', [])
                            for competitor in competitors:
                                athlete = competitor.get('athlete', {})
                                athlete_id = str(athlete.get('id', ''))
                                name = athlete.get('displayName', athlete.get('shortName', ''))
                                
                                if not athlete_id or athlete_id in seen_ids:
                                    continue
                                
                                if query in name.lower():
                                    seen_ids.add(athlete_id)
                                    athletes.append({
                                        'id': athlete_id,
                                        'name': name,
                                        'tour': tour.upper(),
                                        'tournament': tournament,
                                        'country': athlete.get('flag', {}).get('alt', '')
                                    })
                
                except Exception as e:
                    logger.warning(f"Error searching {tour}: {e}")
                    continue
            
            # Sort by name
            athletes.sort(key=lambda x: x['name'])
            
            return jsonify({
                'athletes': athletes[:20],
                'count': len(athletes),
                'source': 'active_tournaments'
            })
            
        except Exception as e:
            logger.error(f"Error searching tennis athletes: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/tennis-athletes/search-all', methods=['GET'])
    def search_tennis_athletes_all():
        """Search for tennis players using ESPN global search."""
        try:
            import requests
            
            query = request.args.get('q', '').strip()
            
            if len(query) < 2:
                return jsonify({'athletes': [], 'message': 'Query too short'})
            
            # Use ESPN's global search API
            url = f"https://site.web.api.espn.com/apis/common/v3/search?query={query}&limit=20&type=player"
            
            response = requests.get(url, timeout=10)
            
            if response.status_code != 200:
                return jsonify({'athletes': [], 'message': 'Search API error'})
            
            data = response.json()
            items = data.get('items', [])
            
            # Filter for tennis players only
            athletes = []
            for item in items:
                sport = item.get('sport', '')
                if sport.lower() == 'tennis':
                    # Determine tour from league info
                    league = item.get('league', '').lower()
                    if 'wta' in league:
                        tour = 'WTA'
                    elif 'atp' in league:
                        tour = 'ATP'
                    else:
                        tour = 'ATP/WTA'  # Unknown, could be either
                    
                    athletes.append({
                        'id': str(item.get('id', '')),
                        'name': item.get('displayName', ''),
                        'tour': tour,
                        'country': ''
                    })
            
            return jsonify({
                'athletes': athletes,
                'count': len(athletes),
                'source': 'espn_search'
            })
            
        except Exception as e:
            logger.error(f"Error in tennis search-all: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/tennis-athletes/popular', methods=['GET'])
    def get_popular_tennis_athletes():
        """Get popular/ranked tennis players from JSON file."""
        try:
            # Try to load rankings file
            rankings_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'tennis_rankings.json')
            
            if not os.path.exists(rankings_path):
                # Try alternate path
                rankings_path = '/home/ledpi/LEDMatrix/data/tennis_rankings.json'
            
            if os.path.exists(rankings_path):
                with open(rankings_path, 'r') as f:
                    rankings_data = json.load(f)
                
                athletes = rankings_data.get('players', [])
                
                # Apply tour filter if specified
                tour_filter = request.args.get('tour', 'all').lower()
                if tour_filter != 'all':
                    athletes = [a for a in athletes if a.get('tour', '').lower() == tour_filter]
                
                return jsonify({
                    'athletes': athletes,
                    'count': len(athletes),
                    'source': 'rankings_file',
                    'last_updated': rankings_data.get('last_updated', 'Unknown')
                })
            else:
                # Return empty if no rankings file
                return jsonify({
                    'athletes': [],
                    'count': 0,
                    'source': 'none',
                    'message': 'Rankings file not found. Run build_tennis_rankings.py to generate.'
                })
                
        except Exception as e:
            logger.error(f"Error loading popular tennis athletes: {e}")
            return jsonify({'error': str(e)}), 500
    
    logger.info("Tennis configuration routes registered (with favorites support)")
