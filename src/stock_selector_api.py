"""
Stock Selector API - Routes for stock/crypto selection interface with Yahoo Finance search

Add to web_interface_v2.py:
    from src.stock_selector_api import register_stock_selector_routes
    register_stock_selector_routes(app)
"""

from flask import jsonify, request, send_from_directory
import os
import json
import logging
import requests

logger = logging.getLogger(__name__)

def register_stock_selector_routes(app):
    """Register stock selector routes with Flask app."""
    
    # Get the base directory
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    static_dir = os.path.join(base_dir, 'static')
    config_dir = os.path.join(base_dir, 'config')
    
    @app.route('/stock-selector')
    def stock_selector_page():
        """Serve the stock selector HTML page."""
        return send_from_directory(static_dir, 'stock_selector.html')
    
    @app.route('/api/stocks/search')
    def search_stocks():
        """
        Search for stocks/crypto using Yahoo Finance API.
        Query param: q (search term)
        Returns matching symbols with names and types.
        """
        query = request.args.get('q', '').strip()
        
        if not query or len(query) < 1:
            return jsonify({
                'status': 'error',
                'message': 'Search query required'
            }), 400
        
        try:
            # Yahoo Finance search API
            url = f"https://query1.finance.yahoo.com/v1/finance/search"
            params = {
                'q': query,
                'quotesCount': 15,
                'newsCount': 0,
                'enableFuzzyQuery': True,
                'quotesQueryId': 'tss_match_phrase_query'
            }
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                quotes = data.get('quotes', [])
                
                # Format results
                results = []
                for quote in quotes:
                    symbol = quote.get('symbol', '')
                    if not symbol:
                        continue
                        
                    results.append({
                        'symbol': symbol,
                        'name': quote.get('longname') or quote.get('shortname') or symbol,
                        'shortname': quote.get('shortname', ''),
                        'quoteType': quote.get('quoteType', 'EQUITY'),
                        'exchange': quote.get('exchange', '')
                    })
                
                return jsonify({
                    'status': 'success',
                    'results': results,
                    'count': len(results)
                })
            else:
                logger.warning(f"Yahoo Finance search returned {response.status_code}")
                return jsonify({
                    'status': 'error',
                    'message': f'Search service returned {response.status_code}'
                }), 500
                
        except requests.Timeout:
            logger.warning("Yahoo Finance search timed out")
            return jsonify({
                'status': 'error',
                'message': 'Search timed out'
            }), 504
        except Exception as e:
            logger.error(f"Error searching stocks: {e}")
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/stocks/config', methods=['GET'])
    def get_stocks_config():
        """Get current stock and crypto configuration."""
        try:
            config_path = os.path.join(config_dir, 'config.json')
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            return jsonify({
                'status': 'success',
                'stocks': config.get('stocks', {}),
                'crypto': config.get('crypto', {})
            })
        except Exception as e:
            logger.error(f"Error loading stocks config: {e}")
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/stocks/config', methods=['POST'])
    def save_stocks_config():
        """Save stock and crypto configuration."""
        try:
            data = request.get_json()
            
            config_path = os.path.join(config_dir, 'config.json')
            
            # Load current config
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            # Update stocks section
            if 'stocks' in data:
                if 'stocks' not in config:
                    config['stocks'] = {}
                if 'symbols' in data['stocks']:
                    config['stocks']['symbols'] = data['stocks']['symbols']
            
            # Update crypto section
            if 'crypto' in data:
                if 'crypto' not in config:
                    config['crypto'] = {}
                if 'symbols' in data['crypto']:
                    config['crypto']['symbols'] = data['crypto']['symbols']
            
            # Save updated config
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=4)
            
            logger.info(f"Saved stocks config: {len(data.get('stocks', {}).get('symbols', []))} stocks, "
                       f"{len(data.get('crypto', {}).get('symbols', []))} crypto")
            
            return jsonify({
                'status': 'success',
                'message': 'Configuration saved successfully'
            })
        except Exception as e:
            logger.error(f"Error saving stocks config: {e}")
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    logger.info("Stock selector routes registered: /stock-selector, /api/stocks/search, /api/stocks/config")
