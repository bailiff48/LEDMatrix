"""
API routes for tennis configuration.
Handles GET/POST for tennis settings.
"""

from flask import jsonify, request
import logging

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
                'majors_only': tennis_config.get('majors_only', True),
                'show_completed_matches': tennis_config.get('show_completed_matches', False),
                'max_matches_display': tennis_config.get('max_matches_display', 5),
                'update_interval': tennis_config.get('update_interval', 600)
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
            config['tennis']['majors_only'] = data.get('majors_only', True)
            config['tennis']['show_completed_matches'] = data.get('show_completed_matches', False)
            config['tennis']['max_matches_display'] = int(data.get('max_matches_display', 5))
            config['tennis']['update_interval'] = int(data.get('update_interval', 600))
            
            # Also update display duration if provided
            if 'display_duration' in data:
                if 'display' not in config:
                    config['display'] = {}
                if 'display_durations' not in config['display']:
                    config['display']['display_durations'] = {}
                config['display']['display_durations']['tennis'] = int(data.get('display_duration', 25))
            
            # Save config
            config_manager.save_config(config)
            
            logger.info(f"Tennis config saved: enabled={data.get('enabled')}, majors_only={data.get('majors_only')}")
            
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
            import datetime
            
            config_manager = ConfigManager()
            config = config_manager.load_config()
            tennis_config = config.get('tennis', {})
            
            # Basic status from config
            status = {
                'enabled': tennis_config.get('enabled', False),
                'majors_only': tennis_config.get('majors_only', True),
                'show_completed_matches': tennis_config.get('show_completed_matches', False),
                'update_interval': tennis_config.get('update_interval', 600),
                'total_matches': 0,
                'live_matches': 0,
                'note': 'Real-time match data only available in display process'
            }
            
            return jsonify(status)
            
        except Exception as e:
            logger.error(f"Error getting tennis status: {e}")
            return jsonify({'error': str(e)}), 500
    
    logger.info("Tennis configuration routes registered")
