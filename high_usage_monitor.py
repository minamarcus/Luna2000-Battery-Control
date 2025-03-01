import asyncio
import time
import logging
import math
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import aiohttp
import tibber

from config import (
    logger, BATTERY_HOST, HIGH_USAGE_THRESHOLD, HIGH_USAGE_DURATION_THRESHOLD,
    MAX_SELF_CONSUMPTION_DURATION, MONITORING_START_HOUR, MONITORING_END_HOUR, 
    MIN_SOC_FOR_DISCHARGE, TOU_MODE, MAX_SELF_CONSUMPTION_MODE, 
    STOCKHOLM_TZ, TIBBER_TOKEN, MAX_RETRIES, RETRY_DELAY
)
from battery_manager import BatteryManager

class BatteryModeManager:
    """Class to manage battery mode changes and track state."""
    
    def __init__(self, battery_manager: BatteryManager):
        self.battery_manager = battery_manager
        self.in_high_usage_mode = False
        self.mode_switch_time = 0
        
    def get_current_mode(self) -> Optional[int]:
        """Get the current battery mode."""
        try:
            mode = self.battery_manager.get_mode()
            return mode
        except Exception as e:
            logger.error(f"Error reading battery mode: {e}")
            return None
    
    def is_currently_discharging(self) -> bool:
        """
        Check if the battery is currently in an active discharging period.
        
        Returns:
            bool: True if there's an active discharging period, False otherwise
        """
        try:
            # Get current schedule
            schedule = self.battery_manager.read_schedule()
            if not schedule or 'periods' not in schedule or not schedule['periods']:
                return False
                
            # Get current time and day
            now = datetime.now(STOCKHOLM_TZ)
            current_minutes = now.hour * 60 + now.minute
            current_day_bit = 1 << ((now.weekday() + 1) % 7)  # Sunday=0, Monday=1, etc.
            
            # Check each period
            for period in schedule['periods']:
                # Check if period is for today
                if not (period['days'] & current_day_bit):
                    continue
                    
                # Check if period is active now
                start_time = period['start_time']
                end_time = period['end_time']
                
                # Handle midnight crossing
                if end_time < start_time:
                    end_time += 1440  # Add 24 hours in minutes
                
                if start_time <= current_minutes < end_time:
                    # Period is active now, check if it's discharging
                    if not period['is_charging']:
                        logger.info(f"Currently in an active discharging period: {start_time//60:02d}:{start_time%60:02d}-{end_time//60:02d}:{end_time%60:02d}")
                        return True
            
            return False
        except Exception as e:
            logger.error(f"Error checking if currently discharging: {e}")
            return False
            
    def switch_to_max_self_consumption(self, soc: float) -> bool:
        """
        Switch the battery to Max Self-Consumption mode.
        
        Args:
            soc: Current battery state of charge (%)
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if self.in_high_usage_mode:
                return True  # Already in high usage mode
                
            logger.info(f"Switching to max self-consumption mode (SOC: {soc}%)")
            
            if soc < MIN_SOC_FOR_DISCHARGE:
                logger.warning(f"SOC too low for discharge: {soc}%, minimum required: {MIN_SOC_FOR_DISCHARGE}%")
                return False
                
            # Get current mode
            current_mode = self.get_current_mode()
            if current_mode is None:
                logger.error("Unable to get current battery mode")
                return False
                
            if current_mode != TOU_MODE:
                logger.info(f"Battery not in TOU mode (current mode: {current_mode}), not switching")
                return False
            
            # Switch to Max Self-Consumption mode
            success = self.battery_manager.set_mode(MAX_SELF_CONSUMPTION_MODE)
            if success:
                self.in_high_usage_mode = True
                self.mode_switch_time = time.time()
                logger.info("Successfully switched to max self-consumption mode")
                return True
            else:
                logger.error("Failed to switch to max self-consumption mode")
                return False
        except Exception as e:
            logger.error(f"Error switching to max self-consumption mode: {e}")
            return False
            
    def switch_to_tou_mode(self) -> bool:
        """Switch the battery back to TOU mode."""
        try:
            if not self.in_high_usage_mode:
                return True  # Already in normal mode
                
            logger.info("Switching back to TOU mode")
            
            # Switch to TOU mode
            success = self.battery_manager.set_mode(TOU_MODE)
            if success:
                self.in_high_usage_mode = False
                logger.info("Successfully switched back to TOU mode")
                return True
            else:
                logger.error("Failed to switch back to TOU mode")
                # We'll retry later
                return False
        except Exception as e:
            logger.error(f"Error switching to TOU mode: {e}")
            return False
            
    def handle_mode_maintenance(self) -> None:
        """
        Periodically check and fix battery mode if necessary.
        This should be called regularly to ensure the battery
        returns to TOU mode after the high usage period.
        """
        try:
            if not self.in_high_usage_mode:
                return
                
            # Check if we need to switch back to TOU mode
            elapsed_time = time.time() - self.mode_switch_time
            if elapsed_time >= MAX_SELF_CONSUMPTION_DURATION:
                logger.info(f"Maximum self-consumption duration reached ({MAX_SELF_CONSUMPTION_DURATION} seconds)")
                self.switch_to_tou_mode()
        except Exception as e:
            logger.error(f"Error in mode maintenance: {e}")

class HighUsageMonitor:
    """Monitor for high power usage and trigger battery mode changes."""
    
    def __init__(self, test_mode: bool = False, websession = None, live_display: bool = True):
        self.battery_manager = BatteryManager(BATTERY_HOST)
        self.battery_mode_manager = BatteryModeManager(self.battery_manager)
        self.high_usage_count = 0
        self.tibber_connection = None
        self.home = None
        self.stopped = False
        self.test_mode = test_mode
        self.websession = websession
        self._subscription_task = None
        self._reconnect_task = None
        self._last_data_time = None
        self._connection_active = False
        self._reconnect_attempt = 0
        self._max_reconnect_delay = 300  # 5 minutes max delay
        self.live_display = live_display
        self.current_power_kw = 0.0
        self.display_active = False
        
        if self.test_mode:
            logger.info("Test mode enabled - battery connections will be simulated")
        
    async def initialize_tibber(self, websession=None) -> bool:
        """Initialize the Tibber connection and home."""
        try:
            logger.info("Initializing Tibber connection...")
            self.tibber_connection = tibber.Tibber(
                TIBBER_TOKEN, 
                websession=websession,
                user_agent="BatteryManagementSystem"
            )
            await self.tibber_connection.update_info()
            
            logger.info(f"Connected to Tibber as: {self.tibber_connection.name}")
            
            # Get the first home (assuming there's only one)
            homes = self.tibber_connection.get_homes()
            if not homes:
                logger.error("No homes found in Tibber account")
                return False
                
            self.home = homes[0]
            await self.home.update_info()
            
            # Check if real-time consumption is enabled
            features = getattr(self.home, 'features', None)
            if features:
                real_time_enabled = getattr(features, 'realTimeConsumptionEnabled', False)
                if not real_time_enabled:
                    logger.error("Real-time consumption is not enabled for this home")
                    return False
            
            logger.info(f"Monitoring home: {self.home.address1}")
            return True
        except Exception as e:
            logger.error(f"Error initializing Tibber: {e}")
            return False
            
    def _update_live_display(self, power_kw: float):
        """Update the live display on the terminal with just power usage."""
        if not self.live_display:
            return
            
        now = datetime.now().strftime("%H:%M:%S")
        status = "HIGH" if power_kw >= HIGH_USAGE_THRESHOLD else "Normal"
        
        # Store current power reading
        self.current_power_kw = power_kw
        
        # Create the display string - use carriage return to stay on same line
        display = f"\r[{now}] Power: {power_kw:.2f} kW | Status: {status}"
        
        # Pad with spaces to clear any previous longer output
        display = display.ljust(60)
        
        # Print without newline and flush to ensure immediate display
        print(display, end='', flush=True)
        self.display_active = True
        
    def _print_newline_if_needed(self):
        """Print a newline if the live display is active to avoid overwriting."""
        if self.display_active and self.live_display:
            print()  # Add a newline
            self.display_active = False
    
    def tibber_callback(self, package: Dict[str, Any]) -> None:
        """Callback function for real-time Tibber data."""
        try:
            # Update last data timestamp
            self._last_data_time = datetime.now()
            self._connection_active = True
            self._reconnect_attempt = 0  # Reset reconnection attempts on successful data
            
            data = package.get("data")
            if data is None:
                return

            live_measurement = data.get("liveMeasurement")
            if live_measurement is None:
                return

            power = live_measurement.get("power")
            if power is None:
                return

            # Convert to kW for easier reading
            power_kw = power / 1000
            
            # Update the live display with just power reading
            self._update_live_display(power_kw)
            
            now = datetime.now(STOCKHOLM_TZ)
            current_hour = now.hour
            
            # Check if we're within monitoring hours
            if not (MONITORING_START_HOUR <= current_hour < MONITORING_END_HOUR):
                return
                
            # Check if we're already in high usage mode
            if self.battery_mode_manager.in_high_usage_mode:
                # Maintenance will handle switching back to TOU mode
                self.battery_mode_manager.handle_mode_maintenance()
                return
            
            # Check for high usage
            if power_kw >= HIGH_USAGE_THRESHOLD:
                if self.high_usage_count == 0:
                    self._print_newline_if_needed()
                    logger.info(f"Detected high power usage: {power_kw:.2f} kW")
                
                self.high_usage_count += 1
                
                # Only log every few counts to reduce spam
                if self.high_usage_count % 3 == 0 or self.high_usage_count == HIGH_USAGE_DURATION_THRESHOLD:
                    self._print_newline_if_needed()
                    logger.info(f"High power usage continues: {power_kw:.2f} kW (count: {self.high_usage_count}/{HIGH_USAGE_DURATION_THRESHOLD})")
                
                if self.high_usage_count >= HIGH_USAGE_DURATION_THRESHOLD:
                    self._print_newline_if_needed()
                    logger.info(f"Sustained high power usage detected: {power_kw:.2f} kW for {HIGH_USAGE_DURATION_THRESHOLD} seconds")
                    
                    # Check if already in a discharging period
                    if self.battery_mode_manager.is_currently_discharging():
                        logger.info("Battery is already in a scheduled discharging period, not switching modes")
                        self.high_usage_count = 0
                        return
                    
                    # Get battery SOC - only query the battery when actually needed
                    soc = self.battery_manager.get_soc()
                    if soc is not None and soc >= MIN_SOC_FOR_DISCHARGE:
                        self.battery_mode_manager.switch_to_max_self_consumption(soc)
                    else:
                        logger.warning(f"Cannot switch to max self-consumption: SOC too low or unknown")
                    self.high_usage_count = 0
            else:
                if self.high_usage_count > 0:
                    self._print_newline_if_needed()
                    logger.info(f"Power usage returned to normal: {power_kw:.2f} kW")
                    self.high_usage_count = 0
        except Exception as e:
            self._print_newline_if_needed()
            logger.error(f"Error in Tibber callback: {e}")
            # Reset counter on error to avoid getting stuck
            self.high_usage_count = 0
    
    async def _monitor_connection(self):
        """Monitor the connection and reconnect if needed."""
        while not self.stopped:
            try:
                # Skip connection monitoring if we're in test mode
                if self.test_mode:
                    await asyncio.sleep(10)
                    continue
                
                # Check if we have a recent data point
                now = datetime.now()
                if (self._last_data_time is not None and 
                    self._connection_active and 
                    (now - self._last_data_time) > timedelta(minutes=5)):
                    
                    logger.warning(f"No data received for over 5 minutes, connection may be stale")
                    self._connection_active = False
                    
                    # Trigger reconnect
                    if self._subscription_task:
                        # Cancel existing subscription
                        if hasattr(self._subscription_task, 'unsubscribe'):
                            try:
                                await self._subscription_task.unsubscribe()
                            except Exception as e:
                                logger.error(f"Error unsubscribing: {e}")
                        
                        # Close websocket if available
                        if self.home and hasattr(self.home, '_ws') and self.home._ws:
                            try:
                                self._print_newline_if_needed()
                                logger.info("Connection lost, attempting to close and reconnect...")
                                await self.home._ws.close()
                            except Exception as e:
                                self._print_newline_if_needed()
                                logger.error(f"Error closing websocket: {e}")
                    
                    # Schedule reconnection with backoff
                    if not self._reconnect_task or self._reconnect_task.done():
                        self._reconnect_task = asyncio.create_task(self._reconnect_with_backoff())
                
                # Check every 30 seconds
                await asyncio.sleep(30)
                
            except asyncio.CancelledError:
                logger.info("Connection monitor cancelled")
                break
            except Exception as e:
                logger.error(f"Error in connection monitoring: {e}")
                await asyncio.sleep(30)  # Wait before next check
    
    async def _reconnect_with_backoff(self):
        """Attempt to reconnect with exponential backoff."""
        # Calculate backoff delay
        self._reconnect_attempt += 1
        delay = min(RETRY_DELAY * (2 ** (self._reconnect_attempt - 1)), self._max_reconnect_delay)
        
        logger.info(f"Scheduling reconnection attempt {self._reconnect_attempt} in {delay} seconds")
        await asyncio.sleep(delay)
        
        try:
            logger.info(f"Attempting to reconnect (attempt {self._reconnect_attempt})")
            
            # Re-initialize Tibber if needed
            if not self.tibber_connection or not self.home:
                success = await self.initialize_tibber(self.websession)
                if not success:
                    logger.error("Failed to re-initialize Tibber connection")
                    return
            
            # Start a new subscription
            logger.info("Starting new Tibber subscription")
            
            # Define wrapper callback
            def wrapped_callback(pkg):
                if self.stopped:
                    return
                try:
                    self.tibber_callback(pkg)
                except Exception as e:
                    logger.error(f"Error in tibber callback: {e}")
            
            # Create new subscription
            self._subscription_task = await self.home.rt_subscribe(wrapped_callback)
            logger.info("Successfully reconnected to Tibber")
            self._connection_active = True
            self._last_data_time = datetime.now()
            
        except Exception as e:
            logger.error(f"Error during reconnection: {e}")
            # No need to reschedule here, the monitor will do it if needed
            
    async def _run_test_mode(self) -> None:
        """Run in test mode with simulated power data."""
        self._print_newline_if_needed()
        logger.info("Running in test mode with simulated power data")
        
        # Simulate alternating normal and high usage patterns
        test_start_time = time.time()
        
        while not self.stopped:
            # Simulate varying power levels
            elapsed = time.time() - test_start_time
            # Create a sine wave pattern between 2.0 and 12.0 kW
            base_power = 7.0  # Average power
            amplitude = 5.0  # How much it varies by
            period = 60.0  # Complete cycle in seconds
            
            # Calculate simulated power using sine wave
            power_kw = base_power + amplitude * math.sin(2 * math.pi * elapsed / period)
            
            # Update live display with just power
            self._update_live_display(power_kw)
            
            # Check for high usage
            if power_kw >= HIGH_USAGE_THRESHOLD:
                if self.high_usage_count == 0:
                    self._print_newline_if_needed()
                    logger.info(f"Detected high power usage: {power_kw:.2f} kW")
                
                self.high_usage_count += 1
                
                # Log periodically
                if self.high_usage_count % 3 == 0:
                    self._print_newline_if_needed()
                    logger.info(f"High power usage continues: {power_kw:.2f} kW (count: {self.high_usage_count}/{HIGH_USAGE_DURATION_THRESHOLD})")
                
                if self.high_usage_count >= HIGH_USAGE_DURATION_THRESHOLD:
                    self._print_newline_if_needed()
                    logger.info(f"Sustained high power usage detected: {power_kw:.2f} kW for {HIGH_USAGE_DURATION_THRESHOLD} seconds")
                    soc = 50.0  # Simulate SOC in test mode
                    logger.info(f"Switching to max self-consumption mode (SOC: {soc}%)")
                    logger.info("TEST MODE: Simulating battery mode switch")
                    self.battery_mode_manager.in_high_usage_mode = True
                    self.battery_mode_manager.mode_switch_time = time.time()
                    logger.info("Successfully switched to max self-consumption mode")
                    self.high_usage_count = 0
            else:
                if self.high_usage_count > 0:
                    self._print_newline_if_needed()
                    logger.info(f"Power usage returned to normal: {power_kw:.2f} kW")
                    self.high_usage_count = 0
            
            # Handle mode maintenance
            self.battery_mode_manager.handle_mode_maintenance()
            
            # Wait before updating again
            await asyncio.sleep(1)
            
    async def start_monitoring(self) -> None:
        """Start the real-time monitoring."""
        try:
            logger.info("Starting high usage monitor")
            
            if self.test_mode:
                await self._run_test_mode()
                return
            
            # Check if battery is accessible
            current_mode = self.battery_mode_manager.get_current_mode()
            if current_mode is not None:
                logger.info(f"Successfully connected to battery. Current mode: {current_mode}")
            else:
                logger.warning("Could not get battery mode - check battery connection")
                
            logger.info("Starting Tibber power monitoring")
            
            # Subscribe to real-time measurements
            logger.info("Subscribing to real-time measurements...")
            
            # Define a wrapper callback that checks if we're stopped
            def wrapped_callback(pkg):
                if self.stopped:
                    return
                try:
                    self.tibber_callback(pkg)
                except Exception as e:
                    logger.error(f"Error in tibber callback: {e}")
            
            # Start the subscription
            try:
                self._subscription_task = await self.home.rt_subscribe(wrapped_callback)
                logger.info("Successfully subscribed to real-time measurements")
                self._connection_active = True
                self._last_data_time = datetime.now()
            except Exception as e:
                logger.error(f"Error subscribing to real-time measurements: {e}")
                raise
            
            # Start the connection monitor
            connection_monitor = asyncio.create_task(self._monitor_connection())
            
            # Keep the monitor running
            while not self.stopped:
                try:
                    await asyncio.sleep(10)
                    
                    # Ensure mode maintenance happens even without Tibber updates
                    self.battery_mode_manager.handle_mode_maintenance()
                except asyncio.CancelledError:
                    logger.info("Monitoring loop cancelled")
                    break
                
            # Cancel connection monitor if we're exiting
            if not connection_monitor.done():
                connection_monitor.cancel()
                
        except asyncio.CancelledError:
            logger.info("Real-time subscription cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in rt_subscribe: {e}")
        finally:
            # Attempt to close any ongoing subscription
            if hasattr(self, '_subscription_task') and self._subscription_task:
                logger.info("Cleaning up subscription task")
                
            logger.info("Monitoring stopped")
            
    def stop(self) -> None:
        """Stop the monitoring."""
        self.stopped = True
        logger.info("Stopping high usage monitor")
        
    async def cleanup(self) -> None:
        """Clean up resources before shutdown."""
        logger.info("Cleaning up high usage monitor resources")
        
        # Set stopped flag
        self.stopped = True
        
        # Cancel reconnect task if running
        if hasattr(self, '_reconnect_task') and self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            
        # Try to unsubscribe from Tibber
        if hasattr(self, '_subscription_task') and self._subscription_task:
            try:
                if hasattr(self._subscription_task, 'unsubscribe'):
                    logger.info("Unsubscribing from Tibber")
                    await self._subscription_task.unsubscribe()
            except Exception as e:
                logger.error(f"Error unsubscribing from Tibber: {e}")
        
        # Close any open websocket
        if self.home:
            try:
                if hasattr(self.home, '_ws') and self.home._ws:
                    logger.info("Closing Tibber websocket connection")
                    await self.home._ws.close()
                    logger.info("Tibber websocket connection closed")
                    
                # Force cleanup the subscription if available
                if hasattr(self.home, '_subscription'):
                    self.home._subscription = None
            except Exception as e:
                logger.error(f"Error closing Tibber websocket: {e}")
                
        # If battery is in high usage mode, switch back to TOU
        if self.battery_mode_manager and self.battery_mode_manager.in_high_usage_mode:
            try:
                logger.info("Switching battery back to TOU mode before exit")
                self.battery_mode_manager.switch_to_tou_mode()
            except Exception as e:
                logger.error(f"Error switching battery mode: {e}")

async def run_monitor(test_mode: bool = False, live_display: bool = True) -> None:
    """Run the high usage monitor with retry logic."""
    monitor = None
    
    max_retries = MAX_RETRIES
    retry_delay = RETRY_DELAY
    
    try:
        monitor = HighUsageMonitor(test_mode=test_mode, live_display=live_display)
        
        for attempt in range(1, max_retries + 1):
            try:
                # Initialize Tibber connection
                success = await monitor.initialize_tibber()
                if success:
                    # Start monitoring
                    await monitor.start_monitoring()
                    break
                else:
                    logger.warning(f"Failed to initialize Tibber, retrying... (attempt {attempt}/{max_retries})")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
            except asyncio.CancelledError:
                logger.info("Monitor cancelled during execution")
                raise
            except Exception as e:
                logger.error(f"Error running high usage monitor: {e}")
                if attempt < max_retries:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logger.error(f"Failed to run high usage monitor after {max_retries} attempts")
                    break
    except asyncio.CancelledError:
        logger.info("Monitor task cancelled")
        raise
    finally:
        # Clean up resources if monitor was created
        if monitor:
            try:
                await monitor.cleanup()
            except Exception as e:
                logger.error(f"Error during cleanup: {e}")

# For testing this module directly
if __name__ == "__main__":
    import argparse
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Run high usage monitor')
    parser.add_argument('--test', action='store_true', help='Run in test mode with simulated data')
    parser.add_argument('--no-display', action='store_true', help='Disable live power display')
    args = parser.parse_args()
    
    # Setup logging for standalone testing
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )
    
    # Run the monitor
    asyncio.run(run_monitor(test_mode=args.test, live_display=not args.no_display))