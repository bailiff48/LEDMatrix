#!/usr/bin/env python3
"""
Patch script to add MIL_HELO and STAR icon support to flight_manager.py
Run on Pi: python3 patch_flight_manager.py
"""

import re
import shutil
from pathlib import Path

FLIGHT_MANAGER = Path.home() / "LEDMatrix" / "src" / "flight_manager.py"

def main():
    if not FLIGHT_MANAGER.exists():
        print(f"ERROR: {FLIGHT_MANAGER} not found!")
        return False
    
    # Backup original
    backup = FLIGHT_MANAGER.with_suffix('.py.backup_icons')
    shutil.copy(FLIGHT_MANAGER, backup)
    print(f"Created backup: {backup}")
    
    content = FLIGHT_MANAGER.read_text()
    
    # === PATCH 1: Add MIL_HELO and STAR to icon_files ===
    old_icons = """icon_files = {
            'JET': 'jet.png',
            'MIL': 'military.png',
            'HELO': 'helicopter.png',
            'GA': 'ga.png',
            'UNK': 'unknown.png'
        }"""
    
    new_icons = """icon_files = {
            'JET': 'jet.png',
            'MIL': 'military.png',
            'HELO': 'helicopter.png',
            'GA': 'ga.png',
            'UNK': 'unknown.png',
            'MIL_HELO': 'military_helicopter.png',
            'STAR': 'star.png'
        }"""
    
    if old_icons in content:
        content = content.replace(old_icons, new_icons)
        print("✓ Patched icon_files to add MIL_HELO and STAR")
    elif 'MIL_HELO' in content:
        print("⊘ icon_files already has MIL_HELO")
    else:
        print("✗ Could not find icon_files to patch")
    
    # === PATCH 2: Update _infer_aircraft_type for MIL_HELO ===
    old_infer = '''    def _infer_aircraft_type(self, callsign: str, altitude_ft: int, speed_knots: int) -> str:
        """
        Infer aircraft type from callsign patterns and flight characteristics.
        Returns type_code: MIL, JET, HELO, GA, or UNK
        """
        callsign = (callsign or '').upper().strip()
        
        # Military patterns
        military_patterns = ['REACH', 'TETON', 'EVAC', 'RESCUE', 'ARMY', 'NAVY', 
                           'GUARD', 'DUKE', 'HAWK', 'VIPER', 'RCH', 'CNV', 'PAT',
                           'IRON', 'STEEL', 'BLADE', 'SABER', 'TOPCAT', 'BOXER',
                           'KARMA', 'RAID', 'SKULL', 'BONE', 'DEATH']
        for pattern in military_patterns:
            if callsign.startswith(pattern):
                return 'MIL'
        
        # Helicopter patterns
        heli_patterns = ['LIFE', 'MEDEVAC', 'HELI', 'COPTER', 'AIR1', 'MERCY']
        for pattern in heli_patterns:
            if pattern in callsign:
                return 'HELO'
        
        # Low & slow = likely helicopter
        if altitude_ft and speed_knots and altitude_ft < 3000 and speed_knots < 120:
            return 'HELO'
        
        # Commercial airlines
        airlines = ['AAL', 'UAL', 'DAL', 'SWA', 'JBU', 'ASA', 'FFT', 'NKS', 
                   'SKW', 'ENY', 'RPA', 'EDV', 'FDX', 'UPS', 'GTI', 'ABX',
                   'EJA', 'LXJ', 'XOJ', 'TVS', 'XAJ', 'LEA', 'WWI']
        for code in airlines:
            if callsign.startswith(code):
                return 'JET'
        
        # High altitude = jet
        if altitude_ft and altitude_ft > 25000:
            return 'JET'
        
        # N-numbers = general aviation
        if callsign.startswith('N') and len(callsign) <= 6:
            return 'GA'
        
        return 'UNK\''''
    
    new_infer = '''    def _infer_aircraft_type(self, callsign: str, altitude_ft: int, speed_knots: int) -> str:
        """
        Infer aircraft type from callsign patterns and flight characteristics.
        Returns type_code: MIL, MIL_HELO, JET, HELO, GA, or UNK
        """
        callsign = (callsign or '').upper().strip()
        
        # Military patterns
        military_patterns = ['REACH', 'TETON', 'EVAC', 'RESCUE', 'ARMY', 'NAVY', 
                           'GUARD', 'DUKE', 'HAWK', 'VIPER', 'RCH', 'CNV', 'PAT',
                           'IRON', 'STEEL', 'BLADE', 'SABER', 'TOPCAT', 'BOXER',
                           'KARMA', 'RAID', 'SKULL', 'BONE', 'DEATH', 'DUSTOFF']
        
        # Helicopter patterns
        heli_patterns = ['LIFE', 'MEDEVAC', 'HELI', 'COPTER', 'AIR1', 'MERCY', 'DUSTOFF']
        
        # Check military first
        is_military = False
        for pattern in military_patterns:
            if callsign.startswith(pattern):
                is_military = True
                break
        
        # Check helicopter patterns
        is_helo = False
        for pattern in heli_patterns:
            if pattern in callsign:
                is_helo = True
                break
        
        # Military helicopter (military callsign + helo indicator OR low/slow military)
        if is_military:
            if is_helo:
                return 'MIL_HELO'
            # Low and slow military = likely helicopter
            if altitude_ft and speed_knots and altitude_ft < 5000 and speed_knots < 180:
                return 'MIL_HELO'
            return 'MIL'
        
        # Civilian helicopter
        if is_helo:
            return 'HELO'
        
        # Low & slow = likely helicopter
        if altitude_ft and speed_knots and altitude_ft < 3000 and speed_knots < 120:
            return 'HELO'
        
        # Commercial airlines
        airlines = ['AAL', 'UAL', 'DAL', 'SWA', 'JBU', 'ASA', 'FFT', 'NKS', 
                   'SKW', 'ENY', 'RPA', 'EDV', 'FDX', 'UPS', 'GTI', 'ABX',
                   'EJA', 'LXJ', 'XOJ', 'TVS', 'XAJ', 'LEA', 'WWI']
        for code in airlines:
            if callsign.startswith(code):
                return 'JET'
        
        # High altitude = jet
        if altitude_ft and altitude_ft > 25000:
            return 'JET'
        
        # N-numbers = general aviation
        if callsign.startswith('N') and len(callsign) <= 6:
            return 'GA'
        
        return 'UNK\''''
    
    if old_infer in content:
        content = content.replace(old_infer, new_infer)
        print("✓ Patched _infer_aircraft_type for MIL_HELO detection")
    elif 'MIL_HELO' in content and '_infer_aircraft_type' in content:
        print("⊘ _infer_aircraft_type may already have MIL_HELO")
    else:
        print("✗ Could not find _infer_aircraft_type to patch (may need manual edit)")
    
    # === PATCH 3: Update _create_flight_display for star prefix ===
    old_display_icon = """        # Get aircraft icon (will be resized to 12x12)
        icon = self.aircraft_icons.get(aircraft_type)
        icon_width = 12 if icon else 0
        icon_spacing = 2 if icon else 0
        
        # === LINE 1: Icon + Callsign (y=0) ===
        bbox = draw.textbbox((0, 0), callsign, font=font_callsign)
        text_width = bbox[2] - bbox[0]
        total_width = icon_width + icon_spacing + text_width
        start_x = (self.display_width - total_width) // 2
        
        # Draw icon if available (resize to 12x12)
        if icon:
            small_icon = icon.resize((12, 12), Image.LANCZOS) if icon.size != (12, 12) else icon
            img.paste(small_icon, (start_x, 1), small_icon if small_icon.mode == 'RGBA' else None)
        
        # Draw callsign
        text_x = start_x + icon_width + icon_spacing
        draw.text((text_x, 0), callsign, fill=self.COLORS['orange'], font=font_callsign)"""
    
    new_display_icon = """        # Check if military aircraft (show star prefix)
        is_military = aircraft_type in ('MIL', 'MIL_HELO')
        
        # Get aircraft icon (will be resized to 12x12)
        icon = self.aircraft_icons.get(aircraft_type)
        star_icon = self.aircraft_icons.get('STAR') if is_military else None
        
        icon_width = 12 if icon else 0
        star_width = 12 if star_icon else 0
        icon_spacing = 2 if icon else 0
        star_spacing = 2 if star_icon else 0
        
        # === LINE 1: [Star] + Icon + Callsign (y=0) ===
        bbox = draw.textbbox((0, 0), callsign, font=font_callsign)
        text_width = bbox[2] - bbox[0]
        total_width = star_width + star_spacing + icon_width + icon_spacing + text_width
        start_x = (self.display_width - total_width) // 2
        
        current_x = start_x
        
        # Draw star icon first if military
        if star_icon:
            small_star = star_icon.resize((12, 12), Image.LANCZOS) if star_icon.size != (12, 12) else star_icon
            img.paste(small_star, (current_x, 1), small_star if small_star.mode == 'RGBA' else None)
            current_x += star_width + star_spacing
        
        # Draw aircraft icon if available (resize to 12x12)
        if icon:
            small_icon = icon.resize((12, 12), Image.LANCZOS) if icon.size != (12, 12) else icon
            img.paste(small_icon, (current_x, 1), small_icon if small_icon.mode == 'RGBA' else None)
            current_x += icon_width + icon_spacing
        
        # Draw callsign
        text_x = current_x
        draw.text((text_x, 0), callsign, fill=self.COLORS['orange'], font=font_callsign)"""
    
    if old_display_icon in content:
        content = content.replace(old_display_icon, new_display_icon)
        print("✓ Patched _create_flight_display for star prefix on military")
    elif 'is_military = aircraft_type' in content:
        print("⊘ _create_flight_display may already have star prefix")
    else:
        print("✗ Could not find _create_flight_display icon section to patch (may need manual edit)")
    
    # Write patched content
    FLIGHT_MANAGER.write_text(content)
    print(f"\n✓ Saved patched file: {FLIGHT_MANAGER}")
    print("\nRestart service to apply: sudo systemctl restart ledmatrix")
    return True

if __name__ == "__main__":
    main()
