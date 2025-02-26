#!/usr/bin/env python3
import sys
import argparse
from datetime import datetime
from config import logger, BATTERY_HOST, STOCKHOLM_TZ
from schedule_manager import ScheduleManager

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Update battery schedule with optimization')
    parser.add_argument('--mode', choices=['regular', 'evening'], default='regular',
                        help='Operation mode: regular (14:00) or evening (17:00)')
    args = parser.parse_args()
    
    # Log the mode we're running in
    logger.info(f"Running battery schedule update in {args.mode} mode")
    
    try:
        scheduler = ScheduleManager(BATTERY_HOST)
        
        # Get current time for logging
        now = datetime.now(STOCKHOLM_TZ)
        logger.info(f"Starting schedule update at {now.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Execute the appropriate update function based on mode
        if args.mode == 'regular':
            success = scheduler.update_schedule()
        else:  # evening mode
            success = scheduler.update_evening_schedule()
        
        if success:
            logger.info(f"{args.mode.capitalize()} schedule update completed successfully")
        else:
            logger.error(f"{args.mode.capitalize()} schedule update failed")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()