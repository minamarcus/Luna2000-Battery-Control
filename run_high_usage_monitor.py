#!/usr/bin/env python3
import asyncio
import signal
import sys
import time
from datetime import datetime
import logging

from config import logger
from high_usage_monitor import run_monitor

# Flag to track if we should stop
stop_event = asyncio.Event()

async def main():
    """Main function to run the high usage monitor service."""
    logger.info("Starting Battery Management System")
    logger.info("Starting high usage monitor service")
    
    try:
        # Run the high usage monitor until stopped
        await run_monitor(test_mode=False)  # Set to True for testing
    except asyncio.CancelledError:
        logger.info("High usage monitor service stopped")
    except Exception as e:
        logger.error(f"Unexpected error in high usage monitor: {e}")
        return 1
    
    return 0

def signal_handler(sig, frame):
    """Handle interrupt signals."""
    logger.info("Services stopped by user")
    stop_event.set()
    
    # Give a short grace period for cleanup
    time.sleep(0.5)
    
    # If still running after grace period, exit forcefully
    sys.exit(0)

if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run the main function
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        sys.exit(0)