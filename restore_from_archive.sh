#!/bin/bash

# LED Matrix Restore Script
# Restores files from an archive created by cleanup_archive.sh
#
# Usage: ./restore_from_archive.sh <archive_folder> [--all | <filepath>]
#   --all       Restore all files from archive
#   <filepath>  Restore specific file (relative path)

set -e

PROJECT_DIR="${PROJECT_DIR:-$HOME/LEDMatrix}"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <archive_folder> [--all | <filepath>]"
    echo ""
    echo "Available archives:"
    ls -d "$PROJECT_DIR"/_archive_* 2>/dev/null || echo "  No archives found"
    exit 1
fi

ARCHIVE_DIR="$1"
shift

if [ ! -d "$ARCHIVE_DIR" ]; then
    # Try prepending project dir
    if [ -d "$PROJECT_DIR/$ARCHIVE_DIR" ]; then
        ARCHIVE_DIR="$PROJECT_DIR/$ARCHIVE_DIR"
    else
        echo "ERROR: Archive folder not found: $ARCHIVE_DIR"
        exit 1
    fi
fi

echo "========================================"
echo "LED Matrix Restore Script"
echo "========================================"
echo "Archive: $ARCHIVE_DIR"
echo "Project: $PROJECT_DIR"
echo ""

if [ $# -eq 0 ]; then
    echo "Archive contents:"
    echo "----------------------------------------"
    if [ -f "$ARCHIVE_DIR/MANIFEST.txt" ]; then
        cat "$ARCHIVE_DIR/MANIFEST.txt"
    else
        find "$ARCHIVE_DIR" -type f | sed "s|$ARCHIVE_DIR/||"
    fi
    echo ""
    echo "To restore all: $0 $ARCHIVE_DIR --all"
    echo "To restore one: $0 $ARCHIVE_DIR <filepath>"
    exit 0
fi

if [ "$1" = "--all" ]; then
    echo "Restoring ALL files from archive..."
    
    cd "$ARCHIVE_DIR"
    find . -type f ! -name "MANIFEST.txt" | while read -r file; do
        file="${file#./}"
        dest="$PROJECT_DIR/$file"
        dest_dir=$(dirname "$dest")
        
        echo "  Restoring: $file"
        mkdir -p "$dest_dir"
        cp "$ARCHIVE_DIR/$file" "$dest"
    done
    
    echo ""
    echo "All files restored!"
    echo "Archive kept at: $ARCHIVE_DIR (delete manually if desired)"
else
    FILE="$1"
    
    if [ -f "$ARCHIVE_DIR/$FILE" ]; then
        dest="$PROJECT_DIR/$FILE"
        dest_dir=$(dirname "$dest")
        
        mkdir -p "$dest_dir"
        cp "$ARCHIVE_DIR/$FILE" "$dest"
        echo "Restored: $FILE"
    elif [ -d "$ARCHIVE_DIR/$FILE" ]; then
        dest="$PROJECT_DIR/$FILE"
        cp -r "$ARCHIVE_DIR/$FILE" "$dest"
        echo "Restored directory: $FILE"
    else
        echo "ERROR: File not found in archive: $FILE"
        echo ""
        echo "Available files:"
        find "$ARCHIVE_DIR" -type f ! -name "MANIFEST.txt" | sed "s|$ARCHIVE_DIR/||" | head -20
        exit 1
    fi
fi

echo ""
echo "Done! Test your system:"
echo "  sudo systemctl restart ledmatrix"
echo "  sudo systemctl restart ledmatrix-web"
