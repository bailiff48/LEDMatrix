#!/usr/bin/env python3
"""
Build and verify tennis player rankings with ESPN IDs.
Similar to golf rankings builder - fetches current rankings and verifies IDs.

Usage:
    python3 build_tennis_rankings.py           # Build/update rankings
    python3 build_tennis_rankings.py --verify  # Verify existing IDs only
"""

import json
import requests
import time
import os
import sys
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Output path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(SCRIPT_DIR, '..', 'data', 'tennis_rankings.json')


def search_espn_player(name: str) -> dict:
    """
    Search ESPN for a tennis player by name.
    Returns player info with verified ESPN ID.
    """
    try:
        # Clean name for search
        search_name = name.replace("'", "").strip()
        url = f"https://site.web.api.espn.com/apis/common/v3/search?query={search_name}&limit=5&type=player"
        
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            logger.warning(f"Search API returned {response.status_code} for '{name}'")
            return None
        
        data = response.json()
        items = data.get('items', [])
        
        # Find tennis player
        for item in items:
            if item.get('sport', '').lower() == 'tennis':
                return {
                    'id': str(item.get('id', '')),
                    'name': item.get('displayName', name),
                    'found': True
                }
        
        return None
        
    except Exception as e:
        logger.error(f"Error searching for '{name}': {e}")
        return None


def verify_player_id(player_id: str, expected_name: str) -> bool:
    """
    Verify a player ID is valid by checking ESPN athlete endpoint.
    """
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/tennis/athletes/{player_id}"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            actual_name = data.get('athlete', {}).get('displayName', '')
            return expected_name.lower() in actual_name.lower() or actual_name.lower() in expected_name.lower()
        
        return False
        
    except:
        return False


def build_rankings():
    """
    Build tennis rankings file with verified ESPN IDs.
    """
    logger.info("Building tennis rankings...")
    
    # Load existing rankings if available
    existing_players = {}
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, 'r') as f:
                data = json.load(f)
                for p in data.get('players', []):
                    existing_players[p['name']] = p
            logger.info(f"Loaded {len(existing_players)} existing players")
        except:
            pass
    
    # Current top players (manually curated list)
    players_to_add = [
        # ATP Top 25 + Notable
        {"name": "Jannik Sinner", "tour": "ATP", "rank": 1, "country": "ITA"},
        {"name": "Alexander Zverev", "tour": "ATP", "rank": 2, "country": "GER"},
        {"name": "Carlos Alcaraz", "tour": "ATP", "rank": 3, "country": "ESP"},
        {"name": "Taylor Fritz", "tour": "ATP", "rank": 4, "country": "USA"},
        {"name": "Daniil Medvedev", "tour": "ATP", "rank": 5, "country": "RUS"},
        {"name": "Casper Ruud", "tour": "ATP", "rank": 6, "country": "NOR"},
        {"name": "Novak Djokovic", "tour": "ATP", "rank": 7, "country": "SRB"},
        {"name": "Alex de Minaur", "tour": "ATP", "rank": 8, "country": "AUS"},
        {"name": "Andrey Rublev", "tour": "ATP", "rank": 9, "country": "RUS"},
        {"name": "Grigor Dimitrov", "tour": "ATP", "rank": 10, "country": "BUL"},
        {"name": "Tommy Paul", "tour": "ATP", "rank": 11, "country": "USA"},
        {"name": "Stefanos Tsitsipas", "tour": "ATP", "rank": 12, "country": "GRE"},
        {"name": "Holger Rune", "tour": "ATP", "rank": 13, "country": "DEN"},
        {"name": "Hubert Hurkacz", "tour": "ATP", "rank": 14, "country": "POL"},
        {"name": "Frances Tiafoe", "tour": "ATP", "rank": 15, "country": "USA"},
        {"name": "Jack Draper", "tour": "ATP", "rank": 16, "country": "GBR"},
        {"name": "Ugo Humbert", "tour": "ATP", "rank": 17, "country": "FRA"},
        {"name": "Lorenzo Musetti", "tour": "ATP", "rank": 18, "country": "ITA"},
        {"name": "Karen Khachanov", "tour": "ATP", "rank": 19, "country": "RUS"},
        {"name": "Sebastian Korda", "tour": "ATP", "rank": 20, "country": "USA"},
        {"name": "Ben Shelton", "tour": "ATP", "rank": 21, "country": "USA"},
        {"name": "Felix Auger-Aliassime", "tour": "ATP", "rank": 22, "country": "CAN"},
        {"name": "Arthur Fils", "tour": "ATP", "rank": 23, "country": "FRA"},
        {"name": "Alejandro Tabilo", "tour": "ATP", "rank": 24, "country": "CHI"},
        {"name": "Tomas Machac", "tour": "ATP", "rank": 25, "country": "CZE"},
        # ATP Notable/Legends
        {"name": "Nick Kyrgios", "tour": "ATP", "rank": 99, "country": "AUS"},
        {"name": "Rafael Nadal", "tour": "ATP", "rank": 99, "country": "ESP"},
        {"name": "Roger Federer", "tour": "ATP", "rank": 99, "country": "SUI"},
        {"name": "Andy Murray", "tour": "ATP", "rank": 99, "country": "GBR"},
        {"name": "Stan Wawrinka", "tour": "ATP", "rank": 99, "country": "SUI"},
        {"name": "Denis Shapovalov", "tour": "ATP", "rank": 50, "country": "CAN"},
        {"name": "Gael Monfils", "tour": "ATP", "rank": 40, "country": "FRA"},
        {"name": "Matteo Berrettini", "tour": "ATP", "rank": 35, "country": "ITA"},
        {"name": "Cameron Norrie", "tour": "ATP", "rank": 45, "country": "GBR"},
        {"name": "Francisco Cerundolo", "tour": "ATP", "rank": 26, "country": "ARG"},
        
        # WTA Top 25 + Notable
        {"name": "Aryna Sabalenka", "tour": "WTA", "rank": 1, "country": "BLR"},
        {"name": "Iga Swiatek", "tour": "WTA", "rank": 2, "country": "POL"},
        {"name": "Coco Gauff", "tour": "WTA", "rank": 3, "country": "USA"},
        {"name": "Jasmine Paolini", "tour": "WTA", "rank": 4, "country": "ITA"},
        {"name": "Qinwen Zheng", "tour": "WTA", "rank": 5, "country": "CHN"},
        {"name": "Elena Rybakina", "tour": "WTA", "rank": 6, "country": "KAZ"},
        {"name": "Jessica Pegula", "tour": "WTA", "rank": 7, "country": "USA"},
        {"name": "Emma Navarro", "tour": "WTA", "rank": 8, "country": "USA"},
        {"name": "Daria Kasatkina", "tour": "WTA", "rank": 9, "country": "RUS"},
        {"name": "Barbora Krejcikova", "tour": "WTA", "rank": 10, "country": "CZE"},
        {"name": "Danielle Collins", "tour": "WTA", "rank": 11, "country": "USA"},
        {"name": "Paula Badosa", "tour": "WTA", "rank": 12, "country": "ESP"},
        {"name": "Anna Kalinskaya", "tour": "WTA", "rank": 13, "country": "RUS"},
        {"name": "Madison Keys", "tour": "WTA", "rank": 14, "country": "USA"},
        {"name": "Mirra Andreeva", "tour": "WTA", "rank": 15, "country": "RUS"},
        {"name": "Marta Kostyuk", "tour": "WTA", "rank": 16, "country": "UKR"},
        {"name": "Beatriz Haddad Maia", "tour": "WTA", "rank": 17, "country": "BRA"},
        {"name": "Diana Shnaider", "tour": "WTA", "rank": 18, "country": "RUS"},
        {"name": "Donna Vekic", "tour": "WTA", "rank": 19, "country": "CRO"},
        {"name": "Karolina Muchova", "tour": "WTA", "rank": 20, "country": "CZE"},
        {"name": "Victoria Azarenka", "tour": "WTA", "rank": 21, "country": "BLR"},
        {"name": "Maria Sakkari", "tour": "WTA", "rank": 22, "country": "GRE"},
        {"name": "Leylah Fernandez", "tour": "WTA", "rank": 23, "country": "CAN"},
        {"name": "Liudmila Samsonova", "tour": "WTA", "rank": 24, "country": "RUS"},
        {"name": "Katie Boulter", "tour": "WTA", "rank": 25, "country": "GBR"},
        # WTA Notable/Legends
        {"name": "Serena Williams", "tour": "WTA", "rank": 99, "country": "USA"},
        {"name": "Venus Williams", "tour": "WTA", "rank": 99, "country": "USA"},
        {"name": "Naomi Osaka", "tour": "WTA", "rank": 50, "country": "JPN"},
        {"name": "Emma Raducanu", "tour": "WTA", "rank": 55, "country": "GBR"},
        {"name": "Caroline Wozniacki", "tour": "WTA", "rank": 99, "country": "DEN"},
        {"name": "Bianca Andreescu", "tour": "WTA", "rank": 60, "country": "CAN"},
        {"name": "Sloane Stephens", "tour": "WTA", "rank": 65, "country": "USA"},
        {"name": "Petra Kvitova", "tour": "WTA", "rank": 70, "country": "CZE"},
        {"name": "Elina Svitolina", "tour": "WTA", "rank": 30, "country": "UKR"},
        {"name": "Caroline Garcia", "tour": "WTA", "rank": 35, "country": "FRA"},
    ]
    
    verified_players = []
    failed = []
    
    for player in players_to_add:
        name = player['name']
        
        # Check if we already have a verified ID
        if name in existing_players and existing_players[name].get('id'):
            existing = existing_players[name]
            logger.info(f"Using existing ID for {name}: {existing['id']}")
            verified_players.append({
                'id': existing['id'],
                'name': name,
                'tour': player['tour'],
                'rank': player['rank'],
                'country': player['country']
            })
            continue
        
        # Search ESPN for player
        logger.info(f"Searching ESPN for {name}...")
        result = search_espn_player(name)
        
        if result and result.get('id'):
            verified_players.append({
                'id': result['id'],
                'name': name,
                'tour': player['tour'],
                'rank': player['rank'],
                'country': player['country']
            })
            logger.info(f"  Found: ID {result['id']}")
        else:
            failed.append(name)
            logger.warning(f"  NOT FOUND: {name}")
        
        # Rate limiting
        time.sleep(0.3)
    
    # Save results
    output_data = {
        'last_updated': datetime.now().strftime('%Y-%m-%d'),
        'version': '1.0',
        'description': 'Tennis player rankings with verified ESPN IDs',
        'players': verified_players,
        'stats': {
            'total': len(verified_players),
            'atp': len([p for p in verified_players if p['tour'] == 'ATP']),
            'wta': len([p for p in verified_players if p['tour'] == 'WTA']),
            'failed': len(failed)
        }
    }
    
    # Ensure data directory exists
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(output_data, f, indent=2)
    
    logger.info(f"\nResults saved to {OUTPUT_PATH}")
    logger.info(f"Total verified: {len(verified_players)} (ATP: {output_data['stats']['atp']}, WTA: {output_data['stats']['wta']})")
    
    if failed:
        logger.warning(f"Failed to find: {', '.join(failed)}")
    
    return output_data


def verify_existing():
    """
    Verify IDs in existing rankings file.
    """
    logger.info("Verifying existing tennis rankings...")
    
    if not os.path.exists(OUTPUT_PATH):
        logger.error(f"Rankings file not found: {OUTPUT_PATH}")
        return
    
    with open(OUTPUT_PATH, 'r') as f:
        data = json.load(f)
    
    players = data.get('players', [])
    valid = 0
    invalid = []
    
    for player in players:
        pid = player.get('id', '')
        name = player.get('name', '')
        
        if verify_player_id(pid, name):
            valid += 1
            logger.info(f"✓ {name} (ID: {pid})")
        else:
            invalid.append(name)
            logger.warning(f"✗ {name} (ID: {pid}) - INVALID")
        
        time.sleep(0.2)
    
    logger.info(f"\nVerification complete: {valid}/{len(players)} valid")
    if invalid:
        logger.warning(f"Invalid IDs for: {', '.join(invalid)}")


if __name__ == '__main__':
    if '--verify' in sys.argv:
        verify_existing()
    else:
        build_rankings()
