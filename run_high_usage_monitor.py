#!/usr/bin/env python3
import asyncio
import signal
import sys
import time
import os
import logging
from datetime import datetime
import atexit
import tibber

from config import logger, TIBBER_TOKEN

# Global variables
monitor = None
session = None
websession = None
shutdown_in_progress = False

async def create_monitor(test_mode=False):
    """Create and initialize the high usage monitor."""
    from high_usage_monitor import HighUsageMonitor
    global monitor, session, websession
    
    try:
        # We'll manage our own aiohttp session for better control
        import aiohttp
        websession = aiohttp.ClientSession()
        
        # Create the monitor with our managed session
        monitor = HighUsageMonitor(test_mode=test_mode, websession=websession)
        
        # Initialize Tibber connection
        logger.info("Initializing Tibber connection...")
        success = await monitor.initialize_tibber(websession)
        if not success:
            logger.error("Failed to initialize Tibber connection")
            return False
            
        return True
    except Exception as e:
        logger.error(f"Error creating monitor: {e}")
        return False

async def cleanup_resources():
    """Clean up all resources properly."""
    global monitor, session, websession, shutdown_in_progress
    
    if shutdown_in_progress:
        return
        
    shutdown_in_progress = True
    logger.info("Cleaning up resources...")
    
    cleanup_tasks = []
    
    # 1. Stop monitor and clean up its resources
    if monitor:
        try:
            logger.info("Stopping monitor...")
            monitor.stop()
            
            # Add monitor cleanup to tasks
            if hasattr(monitor, 'cleanup'):
                cleanup_tasks.append(monitor.cleanup())
        except Exception as e:
            logger.error(f"Error stopping monitor: {e}")
    
    # 2. Try to ensure websocket connections are closed
    if hasattr(tibber, 'ACTIVE_SUBSCRIPTIONS'):
        for subscription in tibber.ACTIVE_SUBSCRIPTIONS:
            try:
                logger.info(f"Closing subscription: {subscription}")
                await subscription.unsubscribe()
            except Exception as e:
                logger.error(f"Error closing subscription: {e}")
    
    # 3. Close our client session
    if websession and not websession.closed:
        try:
            logger.info("Closing aiohttp session...")
            cleanup_tasks.append(websession.close())
        except Exception as e:
            logger.error(f"Error closing websession: {e}")
    
    # Run all cleanup tasks concurrently with a timeout
    if cleanup_tasks:
        try:
            logger.info(f"Running {len(cleanup_tasks)} cleanup tasks...")
            await asyncio.wait_for(asyncio.gather(*cleanup_tasks, return_exceptions=True), timeout=5.0)
            logger.info("Cleanup tasks completed")
        except asyncio.TimeoutError:
            logger.warning("Cleanup tasks timed out")
        except Exception as e:
            logger.error(f"Error during cleanup tasks: {e}")
    
    logger.info("Resource cleanup complete")

def sync_cleanup():
    """Synchronous cleanup for atexit."""
    # This is a fallback for regular exit
    if not shutdown_in_progress:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If the loop is still running, we can schedule the cleanup
            asyncio.create_task(cleanup_resources())
        else:
            # Otherwise create a new loop for cleanup
            logger.info("Creating new event loop for cleanup")
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                new_loop.run_until_complete(cleanup_resources())
            finally:
                new_loop.close()

async def run_monitoring():
    """Run the monitoring loop."""
    global monitor
    
    if not monitor:
        return
        
    try:
        logger.info("Starting monitoring...")
        await monitor.start_monitoring()
    except asyncio.CancelledError:
        logger.info("Monitoring cancelled")
        raise
    except Exception as e:
        logger.error(f"Monitoring error: {e}")

async def main():
    """Main function to run the high usage monitor service."""
    global monitor
    
    logger.info("Starting Battery Management System")
    logger.info("Starting high usage monitor service")
    
    try:
        # Initialize the monitor
        success = await create_monitor(test_mode=False)
        if not success:
            logger.error("Failed to create monitor")
            return 1
            
        # Register exit handler
        atexit.register(sync_cleanup)
        
        # Run the monitoring loop
        await run_monitoring()
            
    except asyncio.CancelledError:
        logger.info("Main task cancelled")
    except Exception as e:
        logger.error(f"Unexpected error in main: {e}")
        return 1
    finally:
        # Ensure cleanup happens
        await cleanup_resources()
    
    return 0

def signal_handler(sig, frame):
    """Handle interrupt signals."""
    global shutdown_in_progress
    
    if shutdown_in_progress:
        logger.info("Shutdown already in progress, forcing exit...")
        os._exit(1)  # Force exit if called twice
        
    logger.info("Shutdown signal received")
    
    # Get the current event loop
    loop = asyncio.get_event_loop()
    
    # Schedule the cleanup
    for task in asyncio.all_tasks(loop):
        if task != asyncio.current_task():
            task.cancel()
    
    # Schedule cleanup
    loop.create_task(cleanup_resources())

if __name__ == "__main__":
    # Configure default exception handler
    def handle_exception(loop, context):
        logger.error(f"Unhandled exception: {context}")
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run the main function
    try:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(handle_exception)
        exit_code = loop.run_until_complete(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
        # Let the signal handler deal with cleanup