#!/bin/bash

# LED Matrix Cleanup Script - Reversible Archive Approach
# Moves identified cruft to an archive folder instead of deleting
# Can be reverted by moving files back from archive
#
# Usage: ./cleanup_archive.sh [--dry-run] [--execute]
#   --dry-run   Show what would be archived (default)
#   --execute   Actually perform the archive operation

set -e

# Configuration
PROJECT_DIR="${PROJECT_DIR:-$HOME/LEDMatrix}"
ARCHIVE_DIR="$PROJECT_DIR/_archive_$(date +%Y%m%d_%H%M%S)"
MANIFEST_FILE="$ARCHIVE_DIR/MANIFEST.txt"
DRY_RUN=true

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --execute)
            DRY_RUN=false
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--dry-run] [--execute]"
            exit 1
            ;;
    esac
done

echo "========================================"
echo "LED Matrix Cleanup Script"
echo "========================================"
echo "Project directory: $PROJECT_DIR"
echo "Mode: $([ "$DRY_RUN" = true ] && echo 'DRY RUN (no changes)' || echo 'EXECUTE')"
echo ""

if [ ! -d "$PROJECT_DIR" ]; then
    echo "ERROR: Project directory not found: $PROJECT_DIR"
    exit 1
fi

cd "$PROJECT_DIR"

# Initialize counters
FILES_TO_ARCHIVE=0
TOTAL_SIZE=0

# Function to add file to archive list
archive_file() {
    local file="$1"
    local reason="$2"
    
    if [ -e "$file" ]; then
        local size=$(du -sh "$file" 2>/dev/null | cut -f1)
        echo "  [$reason] $file ($size)"
        FILES_TO_ARCHIVE=$((FILES_TO_ARCHIVE + 1))
        
        if [ "$DRY_RUN" = false ]; then
            # Create directory structure in archive
            local dir=$(dirname "$file")
            mkdir -p "$ARCHIVE_DIR/$dir"
            mv "$file" "$ARCHIVE_DIR/$file"
            echo "$file | $reason" >> "$MANIFEST_FILE"
        fi
    fi
}

# Function to archive by pattern
archive_pattern() {
    local pattern="$1"
    local reason="$2"
    
    while IFS= read -r -d '' file; do
        archive_file "$file" "$reason"
    done < <(find . -name "$pattern" -print0 2>/dev/null || true)
}

echo "========================================" 
echo "CATEGORY 1: Backup Files (.backup*)"
echo "========================================"
archive_pattern "*.backup" "backup-file"
archive_pattern "*.backup.*" "backup-file"
archive_pattern "*_backup_*" "backup-file"
archive_pattern "*.bak" "backup-file"

echo ""
echo "========================================"
echo "CATEGORY 2: Old/Superseded Files"
echo "========================================"
# Root level old files
archive_file "web_interface.py" "superseded-by-v2"
archive_file "display_controller.py" "stub-file-real-in-src"

# Old template
archive_file "templates/index.html" "superseded-by-index_v2"

# Empty/stub files
if [ -f "src/web_interface.py" ] && [ ! -s "src/web_interface.py" ]; then
    archive_file "src/web_interface.py" "empty-file"
fi

echo ""
echo "========================================"
echo "CATEGORY 3: Debug/One-Time Scripts"
echo "========================================"
# Diagnostic scripts
archive_pattern "diagnose_*.py" "debug-script"
archive_pattern "debug_*.py" "debug-script"

# Fetch scripts (one-time data fetchers)
archive_pattern "fetch_*.py" "one-time-script"

# Fix scripts (one-time fixes)
archive_pattern "fix_*.py" "one-time-fix"

# Integration scripts (one-time)
archive_pattern "integrate_*.py" "one-time-script"

# Migration scripts
archive_file "migrate_config.py" "migration-script"

# Test debug files in src
archive_file "src/fix_basketball.py" "one-time-fix"
archive_file "src/test_makedirs_debug.py" "debug-script"

echo ""
echo "========================================"
echo "CATEGORY 4: Test Files"
echo "========================================"
# Root level test files
archive_pattern "test_*.py" "test-file"

# Test directory (archive entire folder)
if [ -d "test" ]; then
    echo "  [test-directory] test/ (entire folder)"
    FILES_TO_ARCHIVE=$((FILES_TO_ARCHIVE + 1))
    if [ "$DRY_RUN" = false ]; then
        mkdir -p "$ARCHIVE_DIR"
        mv "test" "$ARCHIVE_DIR/test"
        echo "test/ | test-directory" >> "$MANIFEST_FILE"
    fi
fi

echo ""
echo "========================================"
echo "CATEGORY 5: Temporary/Cache Files"
echo "========================================"
archive_pattern "*.pyc" "compiled-python"
archive_pattern "*.pyo" "compiled-python"
archive_pattern "__pycache__" "python-cache"
archive_pattern "*.log" "log-file"
archive_pattern "*.tmp" "temp-file"

echo ""
echo "========================================"
echo "SUMMARY"
echo "========================================"
echo "Files to archive: $FILES_TO_ARCHIVE"

if [ "$DRY_RUN" = true ]; then
    echo ""
    echo "This was a DRY RUN - no files were moved."
    echo "To actually archive these files, run:"
    echo "  ./cleanup_archive.sh --execute"
    echo ""
    echo "After archiving, you can:"
    echo "1. Test that everything still works"
    echo "2. Commit changes to git (archive folder will be included)"
    echo "3. If something breaks, restore from: $ARCHIVE_DIR"
else
    echo ""
    echo "Archive created at: $ARCHIVE_DIR"
    echo "Manifest saved to: $MANIFEST_FILE"
    echo ""
    echo "To restore a file:"
    echo "  mv $ARCHIVE_DIR/<filepath> $PROJECT_DIR/<filepath>"
    echo ""
    echo "To restore everything:"
    echo "  cp -r $ARCHIVE_DIR/* $PROJECT_DIR/"
    echo ""
    echo "NEXT STEPS:"
    echo "1. Test the system: sudo systemctl restart ledmatrix"
    echo "2. Check web UI: http://ledpi.local:5001"
    echo "3. If all good, commit to git:"
    echo "   cd $PROJECT_DIR"
    echo "   git add -A"
    echo "   git status  # Review changes"
    echo "   git commit -m 'Archive unused files for cleanup'"
    echo "   git push"
fi

echo ""
echo "========================================"
echo "FILES TO KEEP (Active System)"
echo "========================================"
echo "
Core (in src/):
  - display_controller.py, display_manager.py
  - config_manager.py, cache_manager.py
  - dynamic_duration_manager.py, clock.py

Sports Managers (in src/):
  - nhl_managers.py, nba_managers.py, wnba_managers.py
  - mlb_manager.py, milb_manager.py, soccer_managers.py
  - nfl_managers.py, ncaa_fb_managers.py
  - ncaa_baseball_managers.py
  - ncaam_basketball_managers.py, ncaaw_basketball_managers.py
  - ncaam_hockey_managers.py, ncaaw_hockey_managers.py
  - logo_downloader.py

Base Classes (in src/base_classes/):
  - sports.py, football.py, basketball.py
  - hockey.py, baseball.py, api_extractors.py

Content Managers (in src/):
  - weather_manager.py, weather_icons.py
  - stock_manager.py, stock_news_manager.py
  - flight_manager.py, golf_manager.py, tennis_manager.py
  - music_manager.py, spotify_client.py, ytm_client.py
  - news_manager.py, calendar_manager.py
  - odds_ticker_manager.py, leaderboard_manager.py
  - of_the_day_manager.py

Web Interface:
  - web_interface_v2.py (root)
  - src/team_selector_api.py, flight_config_api.py
  - src/golf_config_api.py, tennis_config_api.py, wifi_api.py
  - templates/index_v2.html
  - static/* (all static files)

Config:
  - config/config.json, config_secrets.json
  - config/core_sports_teams.json
  - config/*.template.json

Install Scripts:
  - first_time_install.sh, install_service.sh
  - install_web_service.sh, fix_*.sh (permission fixers)
  - start_web_conditionally.py
  - ledmatrix.service, ledmatrix-web.service

Assets:
  - assets/* (fonts, logos, icons)
"
