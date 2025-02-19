from datetime import datetime, timedelta
import time
from typing import List, Dict
from config import (
    logger, MAX_PERIODS, STOCKHOLM_TZ,
    MAX_CHARGING_PERIODS, MAX_DISCHARGING_PERIODS,
    MAX_RETRIES, RETRY_DELAY
)
from battery_manager import BatteryManager
from optimization_manager import OptimizationManager
from period_manager import PeriodManager
from price_fetcher import PriceFetcher
from schedule_data_manager import ScheduleDataManager

class ScheduleManager:
    def __init__(self, battery_host: str):
        self.battery = BatteryManager(battery_host)
        self.price_fetcher = PriceFetcher()
        self.period_manager = PeriodManager()
        self.schedule_data_manager = ScheduleDataManager(MAX_PERIODS)
        self.optimization_manager = OptimizationManager(
            MAX_CHARGING_PERIODS,
            MAX_DISCHARGING_PERIODS
        )
        self.stockholm_tz = STOCKHOLM_TZ

    def update_schedule(self) -> bool:
        """Main function to update the schedule with retries."""
        for attempt in range(MAX_RETRIES):
            try:
                logger.info(f"Schedule update attempt {attempt + 1}/{MAX_RETRIES}")
                
                # Get current battery state
                current_soc = self.battery.get_soc()
                if current_soc is None:
                    raise RuntimeError("Failed to read battery SOC")

                current_schedule = self.battery.read_schedule()
                if not current_schedule:
                    raise RuntimeError("Failed to read current schedule")

                now = datetime.now(self.stockholm_tz)
                tomorrow = now + timedelta(days=1)
                
                logger.info(f"Updating schedule at {now} (Current SOC: {current_soc}%)")
                
                # Get current schedule
                current_periods = self.schedule_data_manager.clean_schedule(current_schedule, now)
                self.schedule_data_manager.log_schedule(current_periods, "Current Schedule")
                
                # Get prices for today and tomorrow
                prices = self.price_fetcher.get_prices()
                if not prices.get('today') or not prices.get('tomorrow'):
                    raise RuntimeError("Failed to fetch prices")

                # Find optimal periods for tomorrow first to compare prices
                charging_periods, discharging_periods = self.optimization_manager.find_optimal_periods(
                    prices['today'],
                    prices['tomorrow'], 
                    tomorrow
                )

                # Keep future periods if SOC > 10% and price comparison is favorable
                if current_soc > 10:
                    logger.info(f"Checking for future periods to preserve (SOC: {current_soc}%)")
                    if current_periods:
                        # Compare prices between current and new periods
                        keep_current = not self.period_manager.compare_period_prices(
                            current_periods,
                            discharging_periods,
                            prices
                        )
                        
                        if keep_current:
                            logger.info(
                                f"Preserving {len(current_periods)} future periods - new prices "
                                f"not {self.period_manager.price_threshold_factor*100}% higher"
                            )
                        else:
                            logger.info(
                                f"Clearing current periods - new prices are at least "
                                f"{self.period_manager.price_threshold_factor*100}% higher"
                            )
                            current_periods = []
                    else:
                        logger.info("No future periods found in current schedule")
                else:
                    logger.info(f"Clearing current schedule due to low SOC ({current_soc}%)")
                    current_periods = []
                
                new_periods = charging_periods + discharging_periods
                self.schedule_data_manager.log_schedule(new_periods, "New Periods for Tomorrow")
                
                # Merge and check for overlaps
                all_periods = sorted(current_periods + new_periods, 
                                   key=lambda x: x['start_time'])
                final_periods = []
                
                for period in all_periods:
                    overlap = False
                    for existing in final_periods:
                        if self.period_manager.check_overlap(period, existing):
                            overlap = True
                            break
                    if not overlap:
                        final_periods.append(period)
                
                # Create and write new register data
                new_register_data = self.schedule_data_manager.create_register_data(final_periods)
                self.schedule_data_manager.log_schedule(final_periods, "Final Schedule")
                
                # Write to battery
                success = self.battery.write_schedule(new_register_data)
                if success:
                    logger.info("Successfully updated battery schedule")
                    return True
                else:
                    raise RuntimeError("Failed to write schedule to battery")

            except Exception as e:
                logger.error(f"Error on attempt {attempt + 1}/{MAX_RETRIES}: {e}")
                if attempt < MAX_RETRIES - 1:
                    logger.info(f"Waiting {RETRY_DELAY} seconds before retry...")
                    time.sleep(RETRY_DELAY)
                continue

        logger.error(f"Failed to update schedule after {MAX_RETRIES} attempts")
        return False