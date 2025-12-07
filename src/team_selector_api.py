"""
Team Selector API Routes for LEDMatrix Enhanced Sports Ticker
Provides Flask routes for the visual team selection interface

FIXED VERSION - Saves teams in Chuck's format:
- Teams organized by sport into each scoreboard's favorite_teams array
- Anti-spoiler teams saved to top-level anti_spoiler_teams
- Team IDs are just abbreviations (no league prefix)
"""

from flask import jsonify, request, send_file
import os
import json

def register_team_selector_routes(app):
    """Register team selector routes with the Flask app"""
    
    # Import team_database here to avoid circular imports
    from src.utils.team_database import team_db
    
    # Map league codes to config sections and sport keys
    LEAGUE_TO_CONFIG = {
        'NFL': {'config_key': 'nfl_scoreboard', 'sport_key': 'nfl'},
        'NBA': {'config_key': 'nba_scoreboard', 'sport_key': 'nba'},
        'MLB': {'config_key': 'mlb_scoreboard', 'sport_key': 'mlb'},
        'MILB': {'config_key': 'milb_scoreboard', 'sport_key': 'milb'},
        'NHL': {'config_key': 'nhl_scoreboard', 'sport_key': 'nhl'},
        'NCAAF': {'config_key': 'ncaa_fb_scoreboard', 'sport_key': 'ncaa_fb'},
        'NCAA_FB': {'config_key': 'ncaa_fb_scoreboard', 'sport_key': 'ncaa_fb'},  # Alias for team_db format
        'NCAAMB': {'config_key': 'ncaam_basketball_scoreboard', 'sport_key': 'ncaam_basketball'},
        'NCAAWB': {'config_key': 'ncaaw_basketball_scoreboard', 'sport_key': 'ncaaw_basketball'},
        'NCAAB': {'config_key': 'ncaa_baseball_scoreboard', 'sport_key': 'ncaa_baseball'},
        'WNBA': {'config_key': 'wnba_scoreboard', 'sport_key': 'wnba'},
        'SOCCER': {'config_key': 'soccer_scoreboard', 'sport_key': 'soccer'},
        'MLS': {'config_key': 'soccer_scoreboard', 'sport_key': 'soccer'},
    }

    # Reverse mapping: config_key -> team_db league format (for loading saved teams)
    CONFIG_TO_TEAMDB_LEAGUE = {
        'nfl_scoreboard': 'NFL',
        'nba_scoreboard': 'NBA',
        'mlb_scoreboard': 'MLB',
        'milb_scoreboard': 'MILB',
        'nhl_scoreboard': 'NHL',
        'ncaa_fb_scoreboard': 'NCAA_FB',
        'ncaam_basketball_scoreboard': 'NCAAMB',
        'ncaaw_basketball_scoreboard': 'NCAAWB',
        'ncaa_baseball_scoreboard': 'NCAAB',
        'wnba_scoreboard': 'WNBA',
        'soccer_scoreboard': 'SOCCER',
    }
    
    @app.route('/team-selector')
    def team_selector_page():
        """Serve the team selector HTML page using send_file"""
        file_path = '/home/ledpi/LEDMatrix/static/team_selector.html'
        if not os.path.exists(file_path):
            return f"Error: team_selector.html not found at {file_path}", 404
        return send_file(file_path)
    
    @app.route('/api/teams/all', methods=['GET'])
    def get_all_teams():
        """Get all teams from the database"""
        try:
            # Get all teams organized by league
            all_leagues = team_db.get_leagues()
            teams_by_league = {}
            
            for league in all_leagues:
                league_teams = team_db.get_league_teams(league.lower())
                teams_by_league[league.lower()] = {
                    'league_name': team_db.get_league_info(league).get('league_name', league),
                    'teams': league_teams
                }
            
            # Add KC favorites
            teams_by_league['kc_favorites'] = {
                'teams': team_db.get_kc_favorites()
            }
            
            return jsonify(teams_by_league)
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/teams/saved', methods=['GET'])
    def get_saved_teams():
        """Get currently saved teams from Chuck's config format"""
        try:
            config_path = '/home/ledpi/LEDMatrix/config/config.json'
            
            if not os.path.exists(config_path):
                return jsonify({
                    'teams': [],
                    'anti_spoiler_teams': []
                })
            
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            # Collect all favorite teams from all scoreboard configs
            all_teams = []
            processed_configs = {}  # Track which configs we've seen

            for config_key, teamdb_league in CONFIG_TO_TEAMDB_LEAGUE.items():
                # Skip if we already processed this config section
                if config_key in processed_configs:
                    continue

                processed_configs[config_key] = teamdb_league

                if config_key in config:
                    favorite_teams = config[config_key].get('favorite_teams', [])
                    # Prefix with team_db league format so frontend can match
                    for team_id in favorite_teams:
                        all_teams.append(f"{teamdb_league}_{team_id}")
            
            # Get anti-spoiler teams from top level
            anti_spoiler_teams = config.get('anti_spoiler_teams', [])
            
            return jsonify({
                'teams': all_teams,
                'anti_spoiler_teams': anti_spoiler_teams
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/teams/save', methods=['POST'])
    def save_teams():
        """Save teams in Chuck's format - organized by sport"""
        try:
            data = request.get_json()
            if not data:
                return jsonify({
                    'status': 'error',
                    'message': 'No data provided'
                }), 400
            
            selected_teams = data.get('teams', [])
            anti_spoiler_teams = data.get('anti_spoiler_teams', [])
            
            config_path = '/home/ledpi/LEDMatrix/config/config.json'
            
            # Load existing config
            if not os.path.exists(config_path):
                return jsonify({
                    'status': 'error',
                    'message': 'Config file not found'
                }), 404
            
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            # Organize teams by league
            teams_by_league = {}
            for team_id in selected_teams:
                # Team ID format: "NFL_KC", "NCAA_FB_IOWA", "NCAAMB_DUKE", etc.
                # Try to match against known league codes (handles underscores in league names)
                matched = False
                for known_league in sorted(LEAGUE_TO_CONFIG.keys(), key=len, reverse=True):
                    if team_id.startswith(known_league + '_'):
                        league_code = known_league
                        team_abbr = team_id[len(known_league) + 1:]
                        if league_code not in teams_by_league:
                            teams_by_league[league_code] = []
                        teams_by_league[league_code].append(team_abbr)
                        matched = True
                        break
                # Fallback to simple split if no known league matched
                if not matched and '_' in team_id:
                    league_code, team_abbr = team_id.split('_', 1)
                    if league_code not in teams_by_league:
                        teams_by_league[league_code] = []
                    teams_by_league[league_code].append(team_abbr)
            
            # Update each scoreboard's favorite_teams
            for league_code, team_list in teams_by_league.items():
                if league_code in LEAGUE_TO_CONFIG:
                    config_key = LEAGUE_TO_CONFIG[league_code]['config_key']
                    if config_key in config:
                        config[config_key]['favorite_teams'] = team_list
                    else:
                        # Create the scoreboard config if it doesn't exist
                        config[config_key] = {'favorite_teams': team_list}
            
            # Clear favorite_teams for leagues that have no selections
            # BUT: Don't clear if another league code shares the same config_key and has teams
            cleared_configs = set()
            for league_code, mapping in LEAGUE_TO_CONFIG.items():
                config_key = mapping['config_key']

                # Skip if this config was already processed
                if config_key in cleared_configs:
                    continue

                # Check if ANY league code that maps to this config_key has teams selected
                has_teams_for_this_config = any(
                    lc in teams_by_league 
                    for lc, m in LEAGUE_TO_CONFIG.items() 
                    if m['config_key'] == config_key
                )

                # Only clear if NO league codes for this config have teams
                if not has_teams_for_this_config and config_key in config:
                    config[config_key]['favorite_teams'] = []

                cleared_configs.add(config_key)
            
            # Save anti-spoiler teams to top level
            config['anti_spoiler_teams'] = anti_spoiler_teams
            
            # Write config back
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=4)
            
            return jsonify({
                'status': 'success',
                'message': 'Configuration saved successfully',
                'selected_count': len(selected_teams),
                'anti_spoiler_count': len(anti_spoiler_teams)
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/teams/league/<league_code>', methods=['GET'])
    def get_league_teams(league_code):
        """Get teams for a specific league"""
        try:
            teams = team_db.get_league_teams(league_code.upper())
            return jsonify({
                'status': 'success',
                'league': league_code.upper(),
                'teams': teams,
                'count': len(teams)
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/teams/search', methods=['GET'])
    def search_teams():
        """Search teams by query string"""
        query = request.args.get('q', '')
        if not query:
            return jsonify({
                'status': 'error',
                'message': 'No search query provided'
            }), 400
        
        try:
            results = team_db.search_teams(query)
            return jsonify({
                'status': 'success',
                'query': query,
                'teams': results,
                'count': len(results)
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/teams/favorites', methods=['GET'])
    def get_favorites():
        """Get Kansas City favorites"""
        try:
            favorites = team_db.get_kc_favorites()
            return jsonify({
                'status': 'success',
                'teams': favorites,
                'count': len(favorites)
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/teams/stats', methods=['GET'])
    def get_team_stats():
        """Get database statistics"""
        try:
            stats = team_db.get_database_stats()
            return jsonify({
                'status': 'success',
                'stats': stats
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/teams/by-code/<team_code>', methods=['GET'])
    def get_team_by_code(team_code):
        """Get a specific team by its code"""
        try:
            # Since we don't know the league, search all
            for league in team_db.get_leagues():
                team = team_db.get_team_by_id(league.lower(), team_code.upper())
                if team:
                    return jsonify({
                        'status': 'success',
                        'team': team
                    })
            
            return jsonify({
                'status': 'error',
                'message': f'Team not found: {team_code}'
            }), 404
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/teams/conferences/<league_code>', methods=['GET'])
    def get_league_conferences(league_code):
        """Get all conferences/divisions for a league"""
        try:
            teams = team_db.get_league_teams(league_code.upper())
            
            # Extract unique conferences/divisions
            conferences = set()
            for team in teams:
                if 'conference' in team and team['conference']:
                    conferences.add(team['conference'])
            
            return jsonify({
                'status': 'success',
                'league': league_code.upper(),
                'conferences': sorted(list(conferences)),
                'count': len(conferences)
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/teams/soccer', methods=['GET'])
    def get_soccer_teams():
        """Get all soccer teams organized by category"""
        try:
            from src.soccer_team_fetcher import fetch_soccer_teams_for_selector
            from src.config_manager import ConfigManager
            
            config_manager = ConfigManager()
            config = config_manager.load_config()
            soccer_data = fetch_soccer_teams_for_selector(config)
            
            return jsonify({
                'status': 'success',
                'data': soccer_data,
                'message': 'Soccer teams fetched successfully'
            })
        
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': f'Error fetching soccer teams: {str(e)}'
            }), 500

    # Health check endpoint
    @app.route('/api/teams/health', methods=['GET'])
    def health_check():
        """Health check endpoint"""
        return jsonify({
            'status': 'healthy',
            'service': 'team-selector-api',
            'version': '2.0.0-fixed'
        })
