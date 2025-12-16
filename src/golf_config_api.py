"""
API routes for golf configuration.
Handles GET/POST for golf settings, tours, and favorite golfers.

Endpoints:
    GET  /api/golf-config     - Get current golf configuration
    POST /api/golf-config     - Save golf configuration
    GET  /api/golf-status     - Get current golf manager status
    GET  /api/golf-tours      - Get available tours list
    GET  /api/golf-favorites  - Get favorite golfers list
    POST /api/golf-favorites  - Save favorite golfers list
    GET  /api/golf-athletes/search - Search for golfers by name
"""

from flask import jsonify, request
import requests
import json
import logging

logger = logging.getLogger(__name__)

# Available tours with display names
AVAILABLE_TOURS = {
    'pga': {'name': 'PGA Tour', 'espn_slug': 'pga'},
    'lpga': {'name': 'LPGA Tour', 'espn_slug': 'lpga'},
    'eur': {'name': 'DP World Tour', 'espn_slug': 'eur'},
    'champions-tour': {'name': 'Champions Tour', 'espn_slug': 'champions-tour'},
}


def register_golf_config_routes(app):
    """Register golf configuration routes with the Flask app."""
    
    @app.route('/api/golf-config', methods=['GET'])
    def get_golf_config():
        """Get current golf configuration."""
        try:
            from src.config_manager import ConfigManager
            config_manager = ConfigManager()
            config = config_manager.load_config()
            
            # Get golf config with defaults
            golf_config = config.get('golf', {})
            
            response = {
                'enabled': golf_config.get('enabled', False),
                'tours': golf_config.get('tours', ['pga', 'lpga']),
                'show_top_n': golf_config.get('show_top_n', 5),
                'update_interval': golf_config.get('update_interval', 900),
                'favorite_golfers': golf_config.get('favorite_golfers', []),
                'highlight_favorites': golf_config.get('highlight_favorites', True),
                'show_favorites_section': golf_config.get('show_favorites_section', True),
            }
            
            return jsonify(response)
            
        except Exception as e:
            logger.error(f"Error loading golf config: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/golf-config', methods=['POST'])
    def save_golf_config():
        """Save golf configuration."""
        try:
            from src.config_manager import ConfigManager
            config_manager = ConfigManager()
            
            # Get current config
            config = config_manager.load_config()
            
            # Get posted data
            data = request.json
            
            # Initialize golf section if needed
            if 'golf' not in config:
                config['golf'] = {}
            
            # Update golf settings
            config['golf']['enabled'] = data.get('enabled', False)
            
            # Validate and set tours
            requested_tours = data.get('tours', ['pga', 'lpga'])
            valid_tours = [t for t in requested_tours if t in AVAILABLE_TOURS]
            config['golf']['tours'] = valid_tours if valid_tours else ['pga']
            
            # Validate show_top_n (3-10)
            show_top_n = int(data.get('show_top_n', 5))
            config['golf']['show_top_n'] = max(3, min(10, show_top_n))
            
            # Validate update_interval (300-3600)
            update_interval = int(data.get('update_interval', 900))
            config['golf']['update_interval'] = max(300, min(3600, update_interval))
            
            # Handle favorite golfers if provided
            if 'favorite_golfers' in data:
                config['golf']['favorite_golfers'] = data['favorite_golfers']
            
            # Handle display options
            if 'highlight_favorites' in data:
                config['golf']['highlight_favorites'] = bool(data['highlight_favorites'])
            if 'show_favorites_section' in data:
                config['golf']['show_favorites_section'] = bool(data['show_favorites_section'])
            
            # Also update display duration if provided
            if 'display_duration' in data:
                if 'display' not in config:
                    config['display'] = {}
                if 'display_durations' not in config['display']:
                    config['display']['display_durations'] = {}
                config['display']['display_durations']['golf'] = int(data.get('display_duration', 30))
            
            # Save config
            config_manager.save_config(config)
            
            logger.info(f"Golf config saved: enabled={data.get('enabled')}, tours={valid_tours}")
            
            return jsonify({
                'status': 'success',
                'message': 'Golf configuration saved successfully'
            })
            
        except Exception as e:
            logger.error(f"Error saving golf config: {e}")
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/golf-status', methods=['GET'])
    def get_golf_status():
        """Get current golf manager status."""
        try:
            from src.config_manager import ConfigManager
            
            config_manager = ConfigManager()
            config = config_manager.load_config()
            golf_config = config.get('golf', {})
            
            # Basic status from config
            status = {
                'enabled': golf_config.get('enabled', False),
                'tours': golf_config.get('tours', ['pga', 'lpga']),
                'show_top_n': golf_config.get('show_top_n', 5),
                'update_interval': golf_config.get('update_interval', 900),
                'favorite_golfers_count': len(golf_config.get('favorite_golfers', [])),
                'highlight_favorites': golf_config.get('highlight_favorites', True),
                'show_favorites_section': golf_config.get('show_favorites_section', True),
                'active_tournaments': 0,
                'note': 'Real-time tournament data only available in display process'
            }
            
            return jsonify(status)
            
        except Exception as e:
            logger.error(f"Error getting golf status: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/golf-tours', methods=['GET'])
    def get_golf_tours():
        """Get list of available golf tours."""
        try:
            tours = [
                {
                    'key': key,
                    'name': info['name'],
                    'espn_slug': info['espn_slug']
                }
                for key, info in AVAILABLE_TOURS.items()
            ]
            
            return jsonify({
                'tours': tours,
                'count': len(tours)
            })
            
        except Exception as e:
            logger.error(f"Error getting golf tours: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/golf-favorites', methods=['GET'])
    def get_golf_favorites():
        """Get list of favorite golfers."""
        try:
            from src.config_manager import ConfigManager
            config_manager = ConfigManager()
            config = config_manager.load_config()
            
            golf_config = config.get('golf', {})
            favorites = golf_config.get('favorite_golfers', [])
            
            return jsonify({
                'favorites': favorites,
                'count': len(favorites)
            })
            
        except Exception as e:
            logger.error(f"Error getting golf favorites: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/golf-favorites', methods=['POST'])
    def save_golf_favorites():
        """Save favorite golfers list."""
        try:
            from src.config_manager import ConfigManager
            config_manager = ConfigManager()
            
            # Get current config
            config = config_manager.load_config()
            
            # Get posted data
            data = request.json
            favorites = data.get('favorites', [])
            
            # Validate favorites format
            validated_favorites = []
            for fav in favorites:
                if isinstance(fav, dict) and fav.get('id') and fav.get('name'):
                    validated_favorites.append({
                        'id': str(fav['id']),
                        'name': str(fav['name'])
                    })
            
            # Update config
            if 'golf' not in config:
                config['golf'] = {}
            config['golf']['favorite_golfers'] = validated_favorites
            
            # Save config
            config_manager.save_config(config)
            
            logger.info(f"Golf favorites saved: {len(validated_favorites)} golfers")
            
            return jsonify({
                'status': 'success',
                'message': f'Saved {len(validated_favorites)} favorite golfers',
                'count': len(validated_favorites)
            })
            
        except Exception as e:
            logger.error(f"Error saving golf favorites: {e}")
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/golf-athletes/search', methods=['GET'])
    def search_golf_athletes():
        """
        Search for golfers by name across ESPN's golf athlete database.
        
        Query Parameters:
            q: Search query (required, min 2 characters)
            tour: Tour to search (optional, defaults to 'pga')
            limit: Max results to return (optional, default 20, max 50)
        
        Returns:
            List of matching athletes with id, name, and tour info
        """
        try:
            query = request.args.get('q', '').strip()
            tour = request.args.get('tour', 'pga')
            limit = min(int(request.args.get('limit', 20)), 50)
            
            # Validate query
            if len(query) < 2:
                return jsonify({
                    'error': 'Search query must be at least 2 characters',
                    'athletes': []
                }), 400
            
            # Validate tour
            if tour not in AVAILABLE_TOURS:
                tour = 'pga'
            
            espn_slug = AVAILABLE_TOURS[tour]['espn_slug']
            tour_name = AVAILABLE_TOURS[tour]['name']
            
            # Search ESPN's athlete API
            athletes = _search_espn_athletes(espn_slug, query, limit)
            
            # Add tour context to results
            for athlete in athletes:
                athlete['tour'] = tour
                athlete['tour_name'] = tour_name
            
            return jsonify({
                'athletes': athletes,
                'count': len(athletes),
                'query': query,
                'tour': tour
            })
            
        except Exception as e:
            logger.error(f"Error searching golf athletes: {e}")
            return jsonify({
                'error': str(e),
                'athletes': []
            }), 500
    
    @app.route('/api/golf-athletes/search-all', methods=['GET'])
    def search_golf_athletes_all_tours():
        """
        Search for golfers by name across ALL available tours.
        
        Query Parameters:
            q: Search query (required, min 2 characters)
            limit: Max results per tour (optional, default 10, max 25)
        
        Returns:
            List of matching athletes from all tours, grouped by tour
        """
        try:
            query = request.args.get('q', '').strip()
            limit = min(int(request.args.get('limit', 10)), 25)
            
            # Validate query
            if len(query) < 2:
                return jsonify({
                    'error': 'Search query must be at least 2 characters',
                    'athletes': []
                }), 400
            
            all_athletes = []
            
            # Search each tour
            for tour_key, tour_info in AVAILABLE_TOURS.items():
                try:
                    athletes = _search_espn_athletes(tour_info['espn_slug'], query, limit)
                    for athlete in athletes:
                        athlete['tour'] = tour_key
                        athlete['tour_name'] = tour_info['name']
                    all_athletes.extend(athletes)
                except Exception as e:
                    logger.warning(f"Error searching {tour_key}: {e}")
                    continue
            
            # Remove duplicates (same athlete may appear in multiple tours)
            seen_ids = set()
            unique_athletes = []
            for athlete in all_athletes:
                if athlete['id'] not in seen_ids:
                    seen_ids.add(athlete['id'])
                    unique_athletes.append(athlete)
            
            return jsonify({
                'athletes': unique_athletes,
                'count': len(unique_athletes),
                'query': query
            })
            
        except Exception as e:
            logger.error(f"Error searching all tours: {e}")
            return jsonify({
                'error': str(e),
                'athletes': []
            }), 500
    

    @app.route('/api/golf-athletes/popular', methods=['GET'])
    def get_popular_golfers():
        """
        Get ranked golfers from JSON file for quick selection.
        Source: OWGR (men) and Rolex Rankings (women) with ESPN IDs.
        """
        import os
        rankings_path = '/home/ledpi/LEDMatrix/data/golfer_rankings.json'
        
        try:
            if os.path.exists(rankings_path):
                with open(rankings_path, 'r') as f:
                    rankings_data = json.load(f)
            else:
                # Fallback to empty
                rankings_data = {"pga": [], "lpga": [], "champions-tour": []}
        except Exception as e:
            logger.error(f"Error loading golfer rankings: {e}")
            rankings_data = {"pga": [], "lpga": [], "champions-tour": []}
        
        # Build flat list with tour info
        popular_golfers = []
        tour_names = {
            'pga': 'PGA Tour',
            'lpga': 'LPGA Tour', 
            'champions-tour': 'Champions Tour',
            'eur': 'DP World Tour'
        }
        
        for tour_key in ['pga', 'lpga', 'champions-tour']:
            for golfer in rankings_data.get(tour_key, []):
                popular_golfers.append({
                    'id': str(golfer.get('id', '')),
                    'name': golfer.get('name', ''),
                    'tour': tour_key,
                    'tour_name': tour_names.get(tour_key, tour_key.upper()),
                    'rank': golfer.get('rank', 0)
                })
        
        # Check if grouped view requested
        grouped = request.args.get('grouped', 'false').lower() == 'true'
        
        if grouped:
            by_tour = {}
            for golfer in popular_golfers:
                tour = golfer['tour']
                if tour not in by_tour:
                    by_tour[tour] = {
                        'tour_key': tour,
                        'tour_name': tour_names.get(tour, tour.upper()),
                        'golfers': []
                    }
                by_tour[tour]['golfers'].append(golfer)
            return jsonify({
                'tours': list(by_tour.values()), 
                'total_count': len(popular_golfers),
                'last_updated': rankings_data.get('last_updated', 'unknown')
            })
        
        return jsonify({
            'golfers': popular_golfers, 
            'count': len(popular_golfers),
            'last_updated': rankings_data.get('last_updated', 'unknown')
        })

    @app.route('/api/golf-favorites/add', methods=['POST'])
    def add_golf_favorite():
        """Add a single golfer to favorites."""
        try:
            from src.config_manager import ConfigManager
            config_manager = ConfigManager()
            config = config_manager.load_config()
            
            data = request.json
            if not data.get('id') or not data.get('name'):
                return jsonify({'status': 'error', 'message': 'id and name required'}), 400
            
            if 'golf' not in config:
                config['golf'] = {}
            if 'favorite_golfers' not in config['golf']:
                config['golf']['favorite_golfers'] = []
            
            golfer_id = str(data['id'])
            existing_ids = [str(g.get('id')) for g in config['golf']['favorite_golfers']]
            
            if golfer_id in existing_ids:
                return jsonify({'status': 'exists', 'message': f"{data['name']} already in favorites"})
            
            new_fav = {'id': golfer_id, 'name': data['name'], 'tour': data.get('tour', 'pga')}
            config['golf']['favorite_golfers'].append(new_fav)
            config_manager.save_config(config)
            
            logger.info(f"Added golf favorite: {data['name']}")
            return jsonify({'status': 'success', 'message': f"Added {data['name']}", 'total_count': len(config['golf']['favorite_golfers'])})
        except Exception as e:
            logger.error(f"Error adding favorite: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/golf-favorites/remove', methods=['POST'])
    def remove_golf_favorite():
        """Remove a golfer from favorites."""
        try:
            from src.config_manager import ConfigManager
            config_manager = ConfigManager()
            config = config_manager.load_config()
            
            data = request.json
            golfer_id = str(data.get('id', ''))
            if not golfer_id:
                return jsonify({'status': 'error', 'message': 'id required'}), 400
            
            favorites = config.get('golf', {}).get('favorite_golfers', [])
            original_count = len(favorites)
            favorites = [g for g in favorites if str(g.get('id')) != golfer_id]
            
            if len(favorites) == original_count:
                return jsonify({'status': 'not_found', 'message': 'Golfer not in favorites'})
            
            config['golf']['favorite_golfers'] = favorites
            config_manager.save_config(config)
            
            logger.info(f"Removed golf favorite ID: {golfer_id}")
            return jsonify({'status': 'success', 'message': 'Removed from favorites', 'total_count': len(favorites)})
        except Exception as e:
            logger.error(f"Error removing favorite: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    logger.info("Golf configuration routes registered (with favorites and search)")


def _search_espn_athletes(espn_slug: str, query: str, limit: int = 20) -> list:
    """
    Search ESPN's golf athlete database.
    
    Args:
        espn_slug: ESPN tour slug (pga, lpga, eur, champions-tour)
        query: Search query string
        limit: Maximum results to return
        
    Returns:
        List of athlete dictionaries with id, name, country
    """
    try:
        # ESPN athlete search endpoint
        # Try the athletes endpoint with a large limit and filter locally
        url = f"https://site.api.espn.com/apis/site/v2/sports/golf/{espn_slug}/athletes"
        
        params = {
            'limit': 1000,  # Get a large batch to search through
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        athletes = data.get('athletes', data.get('items', []))
        
        # Filter by query (case-insensitive)
        query_lower = query.lower()
        matches = []
        
        for athlete in athletes:
            # Handle both direct athlete objects and reference objects
            if '$ref' in athlete:
                # This is a reference, skip for now (would need additional fetch)
                continue
            
            name = athlete.get('displayName', athlete.get('fullName', ''))
            short_name = athlete.get('shortName', '')
            
            # Check if query matches name
            if query_lower in name.lower() or query_lower in short_name.lower():
                athlete_info = {
                    'id': str(athlete.get('id', '')),
                    'name': name,
                    'short_name': short_name,
                    'country': athlete.get('flag', {}).get('alt', ''),
                }
                
                # Add headshot if available
                if athlete.get('headshot', {}).get('href'):
                    athlete_info['headshot'] = athlete['headshot']['href']
                
                matches.append(athlete_info)
                
                if len(matches) >= limit:
                    break
        
        return matches
        
    except requests.exceptions.RequestException as e:
        logger.error(f"ESPN API error searching athletes: {e}")
        # Try fallback: search current scoreboard for active players
        return _search_scoreboard_athletes(espn_slug, query, limit)
    except Exception as e:
        logger.error(f"Error searching ESPN athletes: {e}")
        return []


def _search_scoreboard_athletes(espn_slug: str, query: str, limit: int = 20) -> list:
    """
    Fallback: Search current tournament scoreboard for athletes.
    
    This is used when the athletes endpoint fails or returns limited results.
    Only finds players in active tournaments.
    """
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/golf/{espn_slug}/scoreboard"
        
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        events = data.get('events', [])
        
        if not events:
            return []
        
        query_lower = query.lower()
        matches = []
        seen_ids = set()
        
        for event in events:
            competitions = event.get('competitions', [])
            for competition in competitions:
                competitors = competition.get('competitors', [])
                for competitor in competitors:
                    athlete = competitor.get('athlete', {})
                    
                    name = athlete.get('displayName', athlete.get('shortName', ''))
                    athlete_id = str(athlete.get('id', ''))
                    
                    if not athlete_id or athlete_id in seen_ids:
                        continue
                    
                    if query_lower in name.lower():
                        matches.append({
                            'id': athlete_id,
                            'name': name,
                            'short_name': athlete.get('shortName', name),
                            'country': athlete.get('flag', {}).get('alt', ''),
                            'in_tournament': event.get('name', 'Current Tournament'),
                        })
                        seen_ids.add(athlete_id)
                        
                        if len(matches) >= limit:
                            return matches
        
        return matches
        
    except Exception as e:
        logger.error(f"Error searching scoreboard athletes: {e}")
        return []
