import asyncio
import time
import logging
from datetime import datetime
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
    
    def __init__(self, test_mode: bool = False):
        self.battery_manager = BatteryManager(BATTERY_HOST)
        self.battery_mode_manager = BatteryModeManager(self.battery_manager)
        self.high_usage_count = 0
        self.tibber_connection = None
        self.home = None
        self.stopped = False
        self.test_mode = test_mode
        
        if self.test_mode:
            logger.info("Test mode enabled - battery connections will be simulated")
        
    async def initialize_tibber(self) -> bool:
        """Initialize the Tibber connection and home."""
        try:
            logger.info("Initializing Tibber connection...")
            self.tibber_connection = tibber.Tibber(
                TIBBER_TOKEN, 
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
            if hasattr(self.home, 'features') and hasattr(self.home.features, 'realTimeConsumptionEnabled'):
                if not self.home.features.realTimeConsumptionEnabled:
                    logger.error("Real-time consumption is not enabled for this home")
                    return False
            
            logger.info(f"Monitoring home: {self.home.address1}")
            return True
        except Exception as e:
            logger.error(f"Error initializing Tibber: {e}")
            return False
            
    def tibber_callback(self, package: Dict[str, Any]) -> None:
        """Callback function for real-time Tibber data."""
        try:
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
                    logger.info(f"Detected high power usage: {power_kw:.2f} kW")
                
                self.high_usage_count += 1
                logger.info(f"High power usage continues: {power_kw:.2f} kW (count: {self.high_usage_count}/{HIGH_USAGE_DURATION_THRESHOLD})")
                
                if self.high_usage_count >= HIGH_USAGE_DURATION_THRESHOLD:
                    logger.info(f"Sustained high power usage detected: {power_kw:.2f} kW for {HIGH_USAGE_DURATION_THRESHOLD} seconds")
                    # Get battery SOC
                    soc = self.battery_manager.get_soc()
                    if soc is not None and soc >= MIN_SOC_FOR_DISCHARGE:
                        self.battery_mode_manager.switch_to_max_self_consumption(soc)
                    else:
                        logger.warning(f"Cannot switch to max self-consumption: SOC too low or unknown")
                    self.high_usage_count = 0
            else:
                if self.high_usage_count > 0:
                    logger.info(f"Power usage returned to normal: {power_kw:.2f} kW")
                    self.high_usage_count = 0
        except Exception as e:
            logger.error(f"Error in Tibber callback: {e}")
            
    async def _run_test_mode(self) -> None:
        """Run in test mode with simulated power data."""
        logger.info("Running in test mode with simulated power data")
        
        # Simulate alternating normal and high usage patterns
        while not self.stopped:
            # Simulate high usage
            logger.info("Detected high power usage: 10.00 kW")
            for i in range(HIGH_USAGE_DURATION_THRESHOLD):
                if self.stopped:
                    break
                    
                self.high_usage_count += 1
                logger.info(f"High power usage continues: 10.00 kW (count: {self.high_usage_count}/{HIGH_USAGE_DURATION_THRESHOLD})")
                
                if self.high_usage_count >= HIGH_USAGE_DURATION_THRESHOLD:
                    logger.info(f"Sustained high power usage detected: 10.00 kW for {HIGH_USAGE_DURATION_THRESHOLD} seconds")
                    soc = 50.0  # Simulate SOC in test mode
                    logger.info(f"Switching to max self-consumption mode (SOC: {soc}%)")
                    logger.info("TEST MODE: Simulating battery mode switch")
                    self.battery_mode_manager.in_high_usage_mode = True
                    self.battery_mode_manager.mode_switch_time = time.time()
                    logger.info("Successfully switched to max self-consumption mode")
                    self.high_usage_count = 0
                    break
                    
                await asyncio.sleep(1)
                
            # Wait a bit before next cycle
            await asyncio.sleep(10)
            
            # Simulate normal usage
            logger.info("Power usage returned to normal: 4.00 kW")
            
            # Handle mode maintenance
            self.battery_mode_manager.handle_mode_maintenance()
            
            # Wait a bit before next cycle
            await asyncio.sleep(10)
            
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
            await self.home.rt_subscribe(self.tibber_callback)
            logger.info("Successfully subscribed to real-time measurements")
            
            # Keep the monitor running
            while not self.stopped:
                await asyncio.sleep(10)
                
                # Ensure mode maintenance happens even without Tibber updates
                self.battery_mode_manager.handle_mode_maintenance()
                
        except Exception as e:
            logger.error(f"Error in rt_subscribe: {e}")
        finally:
            logger.info("Monitoring stopped")
            
    def stop(self) -> None:
        """Stop the monitoring."""
        self.stopped = True
        logger.info("Stopping high usage monitor")

async def run_monitor(test_mode: bool = False) -> None:
    """Run the high usage monitor with retry logic."""
    monitor = HighUsageMonitor(test_mode=test_mode)
    
    max_retries = MAX_RETRIES
    retry_delay = RETRY_DELAY
    
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
        except Exception as e:
            logger.error(f"Error running high usage monitor: {e}")
            if attempt < max_retries:
                logger.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                logger.error(f"Failed to run high usage monitor after {max_retries} attempts")
                break

# For testing this module directly
if __name__ == "__main__":
    # Setup logging for standalone testing
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )
    
    # Run in test mode
    asyncio.run(run_monitor(test_mode=True))