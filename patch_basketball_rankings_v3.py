#!/usr/bin/env python3
"""
Precise Basketball Rankings Patch

Based on actual file structure:
- BaseNCAAMBasketballManager has super().__init__() on lines 92-100 (multi-line)
- We only need to patch the BASE class - subclasses inherit automatically

Run from /home/ledpi/LEDMatrix:
    python3 patch_basketball_rankings_v3.py
"""

import sys
import re
from pathlib import Path
from datetime import datetime

DRY_RUN = '--dry-run' in sys.argv

FILES = [
    ('src/ncaam_basketball_managers.py', 'ncaam_basketball'),
    ('src/ncaaw_basketball_managers.py', 'ncaaw_basketball'),
]

BACKUP_SUFFIX = f'.backup_before_rankings_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

IMPORT_BLOCK = '''# Rankings service for AP Top 25 support
try:
    from src.rankings_service import RankingsService
    RANKINGS_AVAILABLE = True
except ImportError:
    RANKINGS_AVAILABLE = False

'''

EXPANSION_CODE_TEMPLATE = '''
        # Expand ranking tokens (AP_TOP_25, AP_TOP_10) if rankings service available
        if RANKINGS_AVAILABLE and hasattr(self, 'favorite_teams') and self.favorite_teams:
            original_count = len(self.favorite_teams)
            self.favorite_teams = RankingsService.expand_favorite_teams(
                self.favorite_teams, 
                '{sport_key}'
            )
            if len(self.favorite_teams) != original_count:
                self.logger.info(f"Expanded favorites from {{original_count}} to {{len(self.favorite_teams)}} teams")
'''


def patch_file(filepath: Path, sport_key: str) -> bool:
    """Patch a single file."""
    
    if not filepath.exists():
        print(f"  ERROR: File not found!")
        return False
    
    content = filepath.read_text()
    lines = content.split('\n')
    
    # Check if already patched
    if 'RANKINGS_AVAILABLE' in content:
        print(f"  Already patched!")
        return True
    
    # Backup
    backup = Path(str(filepath) + BACKUP_SUFFIX)
    if not DRY_RUN:
        backup.write_text(content)
        print(f"  Created backup: {backup.name}")
    
    new_lines = []
    import_added = False
    expansion_added = False
    in_base_class = False
    base_class_super_found = False
    paren_depth = 0
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Add import after the existing imports (look for the from src.base_classes lines)
        if not import_added and 'from src.base_classes' in line:
            # Find the last import line
            j = i
            while j + 1 < len(lines) and (lines[j+1].strip().startswith('from ') or lines[j+1].strip().startswith('import ')):
                j += 1
            
            # Add all lines up to and including last import
            while i <= j:
                new_lines.append(lines[i])
                i += 1
            
            # Add import block
            new_lines.append('')
            for import_line in IMPORT_BLOCK.strip().split('\n'):
                new_lines.append(import_line)
            new_lines.append('')
            import_added = True
            print(f"  ✓ Added import block after line {j+1}")
            continue
        
        # Track when we enter the Base class
        if re.match(r'^class Base\w+Manager\(', line):
            in_base_class = True
        
        # Track when we leave the Base class (next class definition)
        if in_base_class and re.match(r'^class \w+Manager\(', line) and 'Base' not in line:
            in_base_class = False
        
        new_lines.append(line)
        
        # Look for super().__init__ ONLY in the base class
        if in_base_class and not expansion_added and 'super().__init__(' in line:
            base_class_super_found = True
            paren_depth = line.count('(') - line.count(')')
            
            # If multi-line call, collect until balanced
            while paren_depth > 0 and i + 1 < len(lines):
                i += 1
                new_lines.append(lines[i])
                paren_depth += lines[i].count('(') - lines[i].count(')')
            
            # Now add expansion code
            expansion_code = EXPANSION_CODE_TEMPLATE.format(sport_key=sport_key)
            for exp_line in expansion_code.split('\n'):
                new_lines.append(exp_line)
            expansion_added = True
            print(f"  ✓ Added expansion code after base class super().__init__()")
        
        i += 1
    
    if not import_added:
        print(f"  WARNING: Could not find import insertion point")
    
    if not expansion_added:
        print(f"  WARNING: Could not find base class super().__init__()")
    
    # Write result
    new_content = '\n'.join(new_lines)
    
    if not DRY_RUN:
        filepath.write_text(new_content)
        print(f"  ✓ Wrote patched file")
    else:
        print(f"  [DRY RUN] Would write patched file")
        # Show a preview
        if '--preview' in sys.argv:
            print("\n--- Preview of changes ---")
            for j, new_line in enumerate(new_lines[70:130], start=71):
                print(f"{j:4}: {new_line}")
    
    return import_added and expansion_added


def main():
    print("=" * 60)
    print("NCAA Basketball Rankings Patch v3")
    print("=" * 60)
    
    if DRY_RUN:
        print("[DRY RUN MODE]\n")
    
    success = 0
    
    for filepath_str, sport_key in FILES:
        filepath = Path(filepath_str)
        print(f"\n--- {filepath.name} ---")
        
        if patch_file(filepath, sport_key):
            success += 1
    
    print("\n" + "=" * 60)
    print(f"Results: {success}/{len(FILES)} files patched")
    print("=" * 60)
    
    if success > 0 and not DRY_RUN:
        print("\nVerify syntax:")
        print("  python3 -m py_compile src/ncaam_basketball_managers.py")
        print("  python3 -m py_compile src/ncaaw_basketball_managers.py")
        print("\nTest rankings:")
        print('  python3 -c "from src.rankings_service import RankingsService; print(RankingsService.get_ranked_teams(\'ncaam_basketball\', top_n=5))"')
        print("\nRestart service:")
        print("  sudo systemctl restart ledmatrix")
        print("\nCheck logs:")
        print('  sudo journalctl -u ledmatrix -f | grep -i "expanded"')
    
    return 0 if success == len(FILES) else 1


if __name__ == '__main__':
    sys.exit(main())
