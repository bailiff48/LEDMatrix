#!/usr/bin/env python3
"""
Patch script to add AP Top 25 support to NCAA Basketball managers.

This adds rankings expansion to both Men's and Women's basketball managers.
Run from /home/ledpi/LEDMatrix directory.

Usage:
    python3 patch_ncaa_basketball_rankings.py [--dry-run]
"""

import sys
import re
import shutil
from pathlib import Path
from datetime import datetime

DRY_RUN = '--dry-run' in sys.argv

# Target files
TARGET_FILES = {
    'ncaam_basketball_managers.py': 'ncaam_basketball',
    'ncaaw_basketball_managers.py': 'ncaaw_basketball',
}

# Backup suffix
BACKUP_SUFFIX = f'.backup_before_rankings_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

# The import to add (after other imports)
IMPORT_BLOCK = '''
# Rankings service for AP Top 25 support
try:
    from rankings_service import RankingsService
    RANKINGS_AVAILABLE = True
except ImportError:
    RANKINGS_AVAILABLE = False
    import logging
    logging.getLogger(__name__).warning("rankings_service not available - AP_TOP_25 tokens won't expand")
'''

# The expansion code template (sport_id will be substituted)
EXPANSION_CODE_TEMPLATE = '''
        # Expand ranking tokens (AP_TOP_25, AP_TOP_10, etc.) if service available
        if RANKINGS_AVAILABLE and self.favorite_teams:
            original_count = len(self.favorite_teams)
            self.favorite_teams = RankingsService.expand_favorite_teams(
                self.favorite_teams, 
                '{sport_id}'
            )
            if len(self.favorite_teams) != original_count:
                self.logger.info(f"Expanded favorites from {{original_count}} to {{len(self.favorite_teams)}} teams")
'''


def patch_file(filename, sport_id):
    """Apply the patch to a basketball manager file."""
    
    target_path = Path('src') / filename
    
    if not target_path.exists():
        print(f"  ERROR: {target_path} not found!")
        return False
    
    # Read current content
    content = target_path.read_text()
    
    # Check if already patched
    if 'RankingsService' in content:
        print(f"  ✓ {filename} already patched (RankingsService found)")
        return True
    
    # Create backup
    if not DRY_RUN:
        backup_path = target_path.with_suffix(target_path.suffix + BACKUP_SUFFIX)
        shutil.copy(target_path, backup_path)
        print(f"  Created backup: {backup_path.name}")
    
    # Add import after existing imports
    # Look for the last import line before class/def/logger
    import_pattern = r'((?:from|import)\s+[\w.]+.*\n)(?=\s*(?:class|def|#\s*-{3,}|logger\s*=))'
    
    match = re.search(import_pattern, content, re.MULTILINE)
    if match:
        insert_pos = match.end()
        content = content[:insert_pos] + IMPORT_BLOCK + content[insert_pos:]
        print(f"  ✓ Added import block")
    else:
        # Fallback: add after module docstring
        docstring_end = content.find('"""', content.find('"""') + 3) + 3
        if docstring_end > 3:
            content = content[:docstring_end] + '\n' + IMPORT_BLOCK + content[docstring_end:]
            print(f"  ✓ Added import block (after docstring)")
        else:
            content = IMPORT_BLOCK + '\n' + content
            print(f"  ✓ Added import block (at top)")
    
    # Find where to add expansion code
    # Look for where self.favorite_teams is set
    favorites_pattern = r"(self\.favorite_teams\s*=\s*self\.mode_config\.get\(['\"]favorite_teams['\"],\s*\[\]\))"
    
    match = re.search(favorites_pattern, content)
    if match:
        insert_pos = match.end()
        expansion_code = EXPANSION_CODE_TEMPLATE.format(sport_id=sport_id)
        content = content[:insert_pos] + expansion_code + content[insert_pos:]
        print(f"  ✓ Added favorites expansion code for '{sport_id}'")
    else:
        print(f"  WARNING: Could not find favorite_teams assignment in {filename}")
        print(f"  You may need to manually add expansion code")
        return False
    
    # Write patched content
    if DRY_RUN:
        print(f"  DRY RUN - would write {len(content)} bytes")
    else:
        target_path.write_text(content)
        print(f"  ✓ Wrote patched file")
    
    return True


def main():
    print("=" * 60)
    print("NCAA Basketball Rankings Patch")
    print("=" * 60)
    
    if DRY_RUN:
        print("DRY RUN MODE - no files will be modified\n")
    
    success_count = 0
    
    for filename, sport_id in TARGET_FILES.items():
        print(f"\n--- {filename} ---")
        if patch_file(filename, sport_id):
            success_count += 1
    
    print("\n" + "=" * 60)
    print(f"Results: {success_count}/{len(TARGET_FILES)} files patched")
    
    if success_count > 0:
        print("\nNEXT STEPS:")
        print("1. Ensure rankings_service.py is in src/")
        print("2. Restart the service: sudo systemctl restart ledmatrix")
        print("3. Add 'AP_TOP_25' to basketball favorite_teams in config.json")
    print("=" * 60)
    
    return 0 if success_count == len(TARGET_FILES) else 1


if __name__ == '__main__':
    sys.exit(main())
