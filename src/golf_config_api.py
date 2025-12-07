"""
API routes for golf configuration.
Handles GET/POST for golf settings.
"""

from flask import jsonify, request
import logging

logger = logging.getLogger(__name__)


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
                'update_interval': golf_config.get('update_interval', 900)
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
            
            # Update golf section
            if 'golf' not in config:
                config['golf'] = {}
            
            config['golf']['enabled'] = data.get('enabled', False)
            config['golf']['tours'] = data.get('tours', ['pga', 'lpga'])
            config['golf']['show_top_n'] = int(data.get('show_top_n', 5))
            config['golf']['update_interval'] = int(data.get('update_interval', 900))
            
            # Also update display duration if provided
            if 'display_duration' in data:
                if 'display' not in config:
                    config['display'] = {}
                if 'display_durations' not in config['display']:
                    config['display']['display_durations'] = {}
                config['display']['display_durations']['golf'] = int(data.get('display_duration', 30))
            
            # Save config
            config_manager.save_config(config)
            
            logger.info(f"Golf config saved: enabled={data.get('enabled')}, tours={data.get('tours')}")
            
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
            import datetime
            
            config_manager = ConfigManager()
            config = config_manager.load_config()
            golf_config = config.get('golf', {})
            
            # Basic status from config
            status = {
                'enabled': golf_config.get('enabled', False),
                'tours': golf_config.get('tours', ['pga', 'lpga']),
                'show_top_n': golf_config.get('show_top_n', 5),
                'update_interval': golf_config.get('update_interval', 900),
                'active_tournaments': 0,
                'note': 'Real-time tournament data only available in display process'
            }
            
            return jsonify(status)
            
        except Exception as e:
            logger.error(f"Error getting golf status: {e}")
            return jsonify({'error': str(e)}), 500
    
    logger.info("Golf configuration routes registered")
