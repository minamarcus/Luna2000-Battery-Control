from datetime import datetime, timedelta
import time
from typing import List, Dict, Optional, Tuple
from config import (
    logger, MAX_PERIODS, STOCKHOLM_TZ,
    MAX_CHARGING_PERIODS, MAX_DISCHARGING_PERIODS,
    MAX_RETRIES, RETRY_DELAY, EVENING_PRICE_THRESHOLD,
    BATTERY_DISCHARGE_RATE, EVENING_START_HOUR, EVENING_END_HOUR,
    NEXT_DAY_START_HOUR, NEXT_DAY_END_HOUR, MIN_SOC_FOR_DISCHARGE
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
                if current_soc > MIN_SOC_FOR_DISCHARGE:
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

    def update_evening_schedule(self) -> bool:
        """
        Evening optimization function to run at 18:00.
        
        This function:
        1. Checks if SOC is above MIN_SOC_FOR_DISCHARGE
        2. Checks for existing periods between 18:00-22:00
        3. Calculates how many hours battery will run during that timeframe
        4. Compares today's evening prices with next day prices
        5. Creates new periods between 18:00-22:00 based on price and SOC
        """
        for attempt in range(MAX_RETRIES):
            try:
                logger.info(f"Evening schedule update attempt {attempt + 1}/{MAX_RETRIES}")
                
                # Get current battery state
                current_soc = self.battery.get_soc()
                if current_soc is None:
                    raise RuntimeError("Failed to read battery SOC")
                
                logger.info(f"Current battery SOC: {current_soc}%")
                
                # Check if SOC is above minimum threshold
                if current_soc <= MIN_SOC_FOR_DISCHARGE:
                    logger.info(f"SOC too low for evening optimization: {current_soc}%. Minimum required: {MIN_SOC_FOR_DISCHARGE}%")
                    return True  # Not an error, just no action needed
                
                # Get current schedule
                current_schedule = self.battery.read_schedule()
                if not current_schedule:
                    raise RuntimeError("Failed to read current schedule")
                
                now = datetime.now(self.stockholm_tz)
                today = now
                tomorrow = now + timedelta(days=1)
                
                # Get all periods from current schedule (both today and tomorrow)
                # We DON'T want to filter for just the current day because we need to preserve
                # tomorrow's periods that were created during the 14:00 run
                current_periods = current_schedule.get('periods', [])
                self.schedule_data_manager.log_schedule(current_periods, "Current Schedule")
                
                # Get prices for today and tomorrow
                prices = self.price_fetcher.get_prices()
                if not prices.get('today') or not prices.get('tomorrow'):
                    raise RuntimeError("Failed to fetch prices")
                
                # Calculate evening price information for only today
                # We only care about today's evening periods when deciding what to add
                now_day_bit = 1 << ((now.weekday() + 1) % 7)  # Sunday=0 convention
                today_periods = [p for p in current_periods if p['days'] & now_day_bit]
                evening_prices, evening_hours_covered = self.optimization_manager.calculate_evening_coverage(
                    today_periods, prices['today']
                )
                
                if evening_hours_covered >= (EVENING_END_HOUR - EVENING_START_HOUR):
                    logger.info(f"Evening period already fully covered by existing periods: {evening_hours_covered} hours")
                    return True  # No need to add more periods
                
                # Calculate next day prices for comparison
                next_day_avg_price = self.optimization_manager.calculate_next_day_avg_price(prices['tomorrow'])
                evening_avg_price = sum(p['SEK_per_kWh'] for p in evening_prices) / len(evening_prices) if evening_prices else 0
                
                logger.info(f"Today's evening avg price: {evening_avg_price:.4f} SEK/kWh")
                logger.info(f"Next day avg price: {next_day_avg_price:.4f} SEK/kWh")
                
                # If next day prices are significantly higher than evening prices, do nothing
                if next_day_avg_price > evening_avg_price * EVENING_PRICE_THRESHOLD:
                    logger.info(
                        f"Next day prices {next_day_avg_price:.4f} are more than {EVENING_PRICE_THRESHOLD * 100}% of "
                        f"evening prices {evening_avg_price:.4f}. No changes needed."
                    )
                    return True  # Not an error, just no action needed
                
                # Calculate additional hours we can add based on SOC
                hours_to_add = self.optimization_manager.calculate_additional_hours(current_soc, evening_hours_covered)
                
                if hours_to_add <= 0:
                    logger.info(f"No additional hours can be added with current SOC: {current_soc}%")
                    return True  # Not an error, just no action needed
                
                # Create new evening periods (only for today)
                new_periods = self.period_manager.create_evening_periods(
                    now, hours_to_add, today_periods, evening_prices, evening_hours_covered
                )
                
                if not new_periods:
                    logger.info("No new periods created for evening optimization")
                    return True  # Not an error, just no action needed
                
                # Merge with existing periods
                all_periods = sorted(current_periods + new_periods, key=lambda x: x['start_time'])
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
                    logger.info("Successfully updated evening schedule")
                    return True
                else:
                    raise RuntimeError("Failed to write schedule to battery")
                
            except Exception as e:
                logger.error(f"Error on attempt {attempt + 1}/{MAX_RETRIES}: {e}")
                if attempt < MAX_RETRIES - 1:
                    logger.info(f"Waiting {RETRY_DELAY} seconds before retry...")
                    time.sleep(RETRY_DELAY)
                continue
        
        logger.error(f"Failed to update evening schedule after {MAX_RETRIES} attempts")
        return False