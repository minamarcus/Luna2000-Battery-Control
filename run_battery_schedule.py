#!/usr/bin/env python3
import schedule
import time
import subprocess
import sys
import os
import argparse
from datetime import datetime
import pytz
import signal
from config import logger

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BATTERY_SCRIPT = os.path.join(SCRIPT_DIR, 'set_battery_schedule.py')

# Flag to track if shutdown is requested
shutdown_requested = False

def run_battery_schedule(mode):
    """Run the battery schedule script with the specified mode."""
    start_time = datetime.now()
    logger.info(f"Starting battery schedule in {mode} mode at {start_time}")
    
    try:
        # Build the command
        cmd = [sys.executable, BATTERY_SCRIPT, '--mode', mode]
        
        # Log the command being run
        logger.info(f"Executing: {' '.join(cmd)}")
        
        # Run the command and capture output
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False  # Don't raise exception on non-zero return code
        )
        
        # Log the result
        if result.returncode == 0:
            logger.info(f"{mode.capitalize()} schedule completed successfully")
        else:
            logger.error(f"{mode.capitalize()} schedule failed with return code {result.returncode}")
            logger.error(f"Error output: {result.stderr}")
        
        # Log stdout for debugging
        for line in result.stdout.splitlines():
            logger.debug(f"STDOUT: {line}")
        
        # Calculate execution time
        execution_time = (datetime.now() - start_time).total_seconds()
        logger.info(f"{mode.capitalize()} schedule execution completed in {execution_time:.2f} seconds")
        
        return result.returncode == 0
    
    except Exception as e:
        logger.error(f"Error executing {mode} schedule: {e}")
        return False

def setup_schedule():
    """Set up the daily schedule."""
    logger.info("Setting up battery schedule automation")
    
    # Schedule regular mode at 14:00
    schedule.every().day.at("14:00").do(run_battery_schedule, mode="regular")
    logger.info("Scheduled regular battery update at 14:00 daily")
    
    # Schedule evening mode at 17:00
    schedule.every().day.at("17:00").do(run_battery_schedule, mode="evening")
    logger.info("Scheduled evening battery update at 17:00 daily")
    
    # Log next scheduled runs
    for job in schedule.get_jobs():
        next_run = job.next_run
        if next_run:
            logger.info(f"Next scheduled run: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")

def run_now(mode):
    """Run the specified mode immediately."""
    logger.info(f"Manual execution of {mode} mode requested")
    return run_battery_schedule(mode)

def signal_handler(signum, frame):
    """Handle interrupt signals."""
    global shutdown_requested
    
    if shutdown_requested:
        logger.info("Forced shutdown requested. Exiting immediately.")
        sys.exit(1)
    
    logger.info(f"Signal {signum} received. Preparing for graceful shutdown...")
    shutdown_requested = True

def main():
    """Main function."""
    parser = argparse.ArgumentParser(description='Battery Schedule Automation')
    parser.add_argument('--run-now', choices=['regular', 'evening', 'both'], 
                        help='Run specified mode immediately and exit')
    parser.add_argument('--daemon', action='store_true', 
                        help='Run as a daemon process in the background')
    
    args = parser.parse_args()
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Check if we're running in immediate mode
    if args.run_now:
        if args.run_now == 'both':
            logger.info("Running both regular and evening modes immediately")
            regular_success = run_battery_schedule('regular')
            evening_success = run_battery_schedule('evening')
            success = regular_success and evening_success
        else:
            logger.info(f"Running {args.run_now} mode immediately")
            success = run_battery_schedule(args.run_now)
        
        sys.exit(0 if success else 1)
    
    # Running in scheduler mode
    logger.info("Starting Battery Schedule Automation")
    
    # Configure timezone
    try:
        from config import STOCKHOLM_TZ
        logger.info(f"Using timezone from config: {STOCKHOLM_TZ}")
    except ImportError:
        logger.info("Could not import timezone from config, using system timezone")
    
    # Set up the schedule
    setup_schedule()
    
    # Main loop to run pending tasks
    logger.info("Entering main scheduler loop. Press Ctrl+C to exit.")
    try:
        while not shutdown_requested:
            schedule.run_pending()
            time.sleep(1)
    except Exception as e:
        logger.error(f"Error in main loop: {e}")
    finally:
        logger.info("Battery scheduler shutting down")

if __name__ == "__main__":
    main()