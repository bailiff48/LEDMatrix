#!/usr/bin/env python3
import logging
import sys

# Configure logging before importing any other modules
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s.%(msecs)03d - %(levelname)s:%(name)s:%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout  # Explicitly set to stdout
)

# Now import the display controller
from src.display_controller import main

if __name__ == "__main__":
    main() 