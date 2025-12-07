"""
Config Migration Script
=======================

Safely adds dynamic duration configuration to existing config.json
Preserves all existing settings and adds defaults for new fields.
"""

import json
import os
import sys
from pathlib import Path


def migrate_config(config_path: str = "/home/ledpi/LEDMatrix/config/config.json"):
    """
    Migrate config.json to add dynamic duration support.
    
    Args:
        config_path: Path to config.json file
    """
    print(f"Loading config from: {config_path}")
    
    # Load existing config
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: Config file not found at {config_path}")
        return False
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in config file: {e}")
        return False
    
    # Check if already migrated
    if 'use_dynamic_durations' in config.get('display', {}):
        print("Config already has dynamic duration settings - skipping migration")
        print(f"Current setting: use_dynamic_durations = {config['display']['use_dynamic_durations']}")
        return True
    
    # Create backup
    backup_path = f"{config_path}.backup_before_dynamic_durations"
    print(f"Creating backup at: {backup_path}")
    with open(backup_path, 'w') as f:
        json.dump(config, f, indent=4)
    
    # Add dynamic duration configuration
    print("Adding dynamic duration configuration...")
    
    # Ensure display section exists
    if 'display' not in config:
        config['display'] = {}
    
    # Add use_dynamic_durations toggle (default: False for backward compatibility)
    config['display']['use_dynamic_durations'] = False
    
    # Add dynamic_duration_config section
    config['display']['dynamic_duration_config'] = {
        "clock": {
            "fixed": 10
        },
        "weather": {
            "per_screen": 15
        },
        "sports": {
            "base_per_item": 8,
            "min_per_item": 4,
            "max_total": 90,
            "scale_factor": 0.4
        }
    }
    
    # Save updated config
    print("Saving updated config...")
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=4)
    
    print("\n✅ Migration complete!")
    print("\nNew configuration added:")
    print(f"  - use_dynamic_durations: {config['display']['use_dynamic_durations']}")
    print(f"  - dynamic_duration_config: Added with defaults")
    print(f"\nBackup saved at: {backup_path}")
    print("\nTo enable dynamic durations, set 'use_dynamic_durations' to true in the web interface")
    
    return True


def verify_migration(config_path: str = "/home/ledpi/LEDMatrix/config/config.json"):
    """
    Verify the migration was successful.
    
    Args:
        config_path: Path to config.json file
    """
    print("\n=== Verification ===")
    
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    display = config.get('display', {})
    
    # Check required fields
    checks = [
        ('use_dynamic_durations', display.get('use_dynamic_durations') is not None),
        ('dynamic_duration_config', 'dynamic_duration_config' in display),
        ('clock config', 'clock' in display.get('dynamic_duration_config', {})),
        ('weather config', 'weather' in display.get('dynamic_duration_config', {})),
        ('sports config', 'sports' in display.get('dynamic_duration_config', {}))
    ]
    
    all_passed = True
    for check_name, passed in checks:
        status = "✅" if passed else "❌"
        print(f"{status} {check_name}")
        all_passed = all_passed and passed
    
    if all_passed:
        print("\n✅ All checks passed!")
    else:
        print("\n❌ Some checks failed - please review the config")
    
    return all_passed


if __name__ == "__main__":
    # Allow custom config path
    config_path = sys.argv[1] if len(sys.argv) > 1 else "/home/ledpi/LEDMatrix/config/config.json"
    
    print("=" * 60)
    print("LED Matrix Dynamic Duration Config Migration")
    print("=" * 60)
    print()
    
    # Run migration
    success = migrate_config(config_path)
    
    if success:
        # Verify migration
        verify_migration(config_path)
    else:
        print("\n❌ Migration failed")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("Next steps:")
    print("1. Restart the LED Matrix services")
    print("2. Access web interface at http://ledpi.local:5001")
    print("3. Navigate to Duration Configuration page")
    print("4. Enable 'Smart Durations' toggle")
    print("=" * 60)
