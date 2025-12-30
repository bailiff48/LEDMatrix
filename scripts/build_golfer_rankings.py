#!/usr/bin/env python3
"""
Build golfer rankings JSON by searching ESPN for each name.
Uses OWGR top 100 (men) and Rolex Rankings (women).
"""

import json
import urllib.request
import urllib.parse
import time
import sys

# OWGR Top 50 Men (as of Dec 2025 approximate)
MEN_GOLFERS = [
    "Scottie Scheffler", "Xander Schauffele", "Rory McIlroy", "Jon Rahm",
    "Collin Morikawa", "Ludvig Aberg", "Wyndham Clark", "Viktor Hovland",
    "Patrick Cantlay", "Tommy Fleetwood", "Hideki Matsuyama", "Sahith Theegala",
    "Tony Finau", "Shane Lowry", "Sungjae Im", "Russell Henley",
    "Matt Fitzpatrick", "Tom Kim", "Brian Harman", "Keegan Bradley",
    "Corey Conners", "Max Homa", "Akshay Bhatia", "Robert MacIntyre",
    "Cameron Young", "Sepp Straka", "Jason Day", "Si Woo Kim",
    "Adam Scott", "Byeong Hun An", "Justin Thomas", "Denny McCarthy",
    "Billy Horschel", "Davis Thompson", "Maverick McNealy", "Cameron Smith",
    "Taylor Moore", "Aaron Rai", "Christiaan Bezuidenhout", "Jordan Spieth",
    "Dustin Johnson", "Tiger Woods", "Brooks Koepka", "Bryson DeChambeau",
    "Phil Mickelson", "Rickie Fowler", "Justin Rose", "Sergio Garcia",
    "Adam Hadwin", "Min Woo Lee"
]

# Rolex Women's Rankings Top 30
WOMEN_GOLFERS = [
    "Nelly Korda", "Lydia Ko", "Hannah Green", "Lilia Vu", "Jin Young Ko",
    "Ruoning Yin", "Ayaka Furue", "Charley Hull", "Celine Boutier", "Rose Zhang",
    "Minjee Lee", "Amy Yang", "Nasa Hataoka", "Haeran Ryu", "Brooke Henderson",
    "Jeeno Thitikul", "Lexi Thompson", "Georgia Hall", "Yuka Saso", "Ally Ewing",
    "Atthaya Thitikul", "Lauren Coughlin", "Megan Khang", "Andrea Lee", "Leona Maguire"
]

# Champions Tour notable players
CHAMPIONS_GOLFERS = [
    "Bernhard Langer", "Ernie Els", "Fred Couples", "Vijay Singh", 
    "Padraig Harrington", "Steve Stricker", "Jim Furyk", "Stewart Cink",
    "Retief Goosen", "David Duval", "Y.E. Yang", "Richard Bland"
]

def search_espn(name):
    """Search ESPN for a golfer and return their ID."""
    try:
        query = urllib.parse.quote(name)
        url = f"https://site.web.api.espn.com/apis/common/v3/search?query={query}&limit=3&type=player"
        
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
        
        items = data.get('items', [])
        # Find golf result
        for item in items:
            if item.get('sport') == 'golf':
                return {
                    'id': item['id'],
                    'name': item['displayName'],
                    'league': item.get('league', 'pga')
                }
        return None
    except Exception as e:
        print(f"  Error searching {name}: {e}", file=sys.stderr)
        return None

def build_rankings():
    """Build the complete rankings JSON."""
    rankings = {
        "last_updated": "2025-12-16",
        "source": "OWGR/Rolex Rankings (ESPN IDs)",
        "note": "Run build_golfer_rankings.py to refresh",
        "pga": [],
        "lpga": [],
        "champions-tour": []
    }
    
    print("Building PGA Tour rankings...")
    for i, name in enumerate(MEN_GOLFERS, 1):
        result = search_espn(name)
        if result:
            rankings["pga"].append({
                "rank": i,
                "id": result['id'],
                "name": result['name']
            })
            print(f"  {i}. {result['name']} (ID: {result['id']})")
        else:
            print(f"  {i}. {name} - NOT FOUND")
        time.sleep(0.3)  # Rate limiting
    
    print("\nBuilding LPGA Tour rankings...")
    for i, name in enumerate(WOMEN_GOLFERS, 1):
        result = search_espn(name)
        if result:
            rankings["lpga"].append({
                "rank": i,
                "id": result['id'],
                "name": result['name']
            })
            print(f"  {i}. {result['name']} (ID: {result['id']})")
        else:
            print(f"  {i}. {name} - NOT FOUND")
        time.sleep(0.3)
    
    print("\nBuilding Champions Tour rankings...")
    for i, name in enumerate(CHAMPIONS_GOLFERS, 1):
        result = search_espn(name)
        if result:
            rankings["champions-tour"].append({
                "rank": i,
                "id": result['id'],
                "name": result['name']
            })
            print(f"  {i}. {result['name']} (ID: {result['id']})")
        else:
            print(f"  {i}. {name} - NOT FOUND")
        time.sleep(0.3)
    
    return rankings

if __name__ == '__main__':
    print("=" * 50)
    print("Building Golfer Rankings JSON")
    print("=" * 50)
    
    rankings = build_rankings()
    
    # Save to file
    output_path = '/home/ledpi/LEDMatrix/data/golfer_rankings.json'
    
    # Ensure directory exists
    import os
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, 'w') as f:
        json.dump(rankings, f, indent=2)
    
    print(f"\n{'=' * 50}")
    print(f"Saved to {output_path}")
    print(f"PGA: {len(rankings['pga'])} golfers")
    print(f"LPGA: {len(rankings['lpga'])} golfers")
    print(f"Champions: {len(rankings['champions-tour'])} golfers")
