from datetime import datetime, timedelta
import pandas as pd
from typing import List, Dict, Tuple, Optional
import time
from config import (
    logger, MAX_PERIODS, MAX_MINUTES, STOCKHOLM_TZ,
    MAX_CHARGING_PERIODS, MAX_DISCHARGING_PERIODS,
    MAX_RETRIES, RETRY_DELAY
)
from battery_manager import BatteryManager
from price_fetcher import PriceFetcher
from period_utils import *

class ScheduleManager:
    def __init__(self, battery_host: str):
        self.battery = BatteryManager(battery_host)
        self.price_fetcher = PriceFetcher()
        self.MAX_PERIODS = MAX_PERIODS
        self.MAX_MINUTES = MAX_MINUTES
        self.stockholm_tz = STOCKHOLM_TZ
        self.max_charging_periods = MAX_CHARGING_PERIODS
        self.max_discharging_periods = MAX_DISCHARGING_PERIODS

    def _get_night_prices(self, today_prices: List[Dict], tomorrow_prices: List[Dict]) -> List[Dict]:
        """
        Get prices for night hours (22:00-06:00) spanning current evening to next morning.
        Returns list of prices sorted by SEK_per_kWh.
        """
        night_prices = []
        
        # Add today's late evening hours (22:00-23:59)
        for price in today_prices:
            if price['hour'] >= 22:
                night_prices.append(price)
                
        # Add tomorrow's early morning hours (00:00-06:00)
        for price in tomorrow_prices:
            if price['hour'] <= 6:
                night_prices.append(price)
                
        return sorted(night_prices, key=lambda x: x['SEK_per_kWh'])

    def create_period(self, start_hour: int, end_hour: int, 
                     is_charging: bool, day_bit: int) -> Dict:
        """Create a valid period entry."""
        start_hour = start_hour % 24
        end_hour = end_hour % 24
        
        start_minutes = start_hour * 60
        end_minutes = end_hour * 60 if end_hour > start_hour else (end_hour + 24) * 60
                
        if not (0 <= start_minutes < self.MAX_MINUTES and 
                0 < end_minutes <= self.MAX_MINUTES * 2):
            raise ValueError(f"Invalid time range: {start_hour}:00-{end_hour}:00")
        
        # Normalize end time: 1440 should be 0
        if end_minutes == self.MAX_MINUTES:
            end_minutes = 0
        
        return {
            'start_time': start_minutes,
            'end_time': end_minutes,
            'charge_flag': 0 if is_charging else 1,
            'days': day_bit,
            'is_charging': is_charging
        }

    def _process_charging_periods(self, night_prices: List[Dict], target_date: datetime) -> List[Dict]:
        """Process and create charging periods for night hours."""
        # First sort by price to get the cheapest hours
        selected_prices = sorted(night_prices[:self.max_charging_periods], key=lambda x: x['hour'])
        
        # Create periods for selected hours
        periods = []
        for price in selected_prices:
            # Use previous day's bit for hours 22-23, next day's bit for hours 0-6
            period_date = target_date - timedelta(days=1) if price['hour'] >= 22 else target_date
            day_bit = get_day_bit(period_date)
            
            periods.append(
                self.create_period(
                    start_hour=price['hour'],
                    end_hour=(price['hour'] + 1) % 24,
                    is_charging=True,
                    day_bit=day_bit
                )
            )
        
        # Combine consecutive periods
        return self._combine_consecutive_periods(periods)

    def _process_discharging_periods(self, df: pd.DataFrame, day_bit: int) -> List[Dict]:
        """Process and create periods for discharging during daytime."""
        # Filter for day hours first
        day_df = df[df['hour'].apply(is_day_hour)]
        
        # Select the best hours (up to max_discharging_periods)
        best_hours_df = day_df.nlargest(self.max_discharging_periods, 'SEK_per_kWh')
        
        # Sort chronologically to create periods
        selected_hours = sorted(best_hours_df['hour'].tolist())
        
        # Create initial periods (one hour each)
        periods = []
        for hour in selected_hours:
            periods.append(
                self.create_period(
                    start_hour=hour,
                    end_hour=(hour + 1) % 24,
                    is_charging=False,
                    day_bit=day_bit
                )
            )
        
        # Combine consecutive periods
        return self._combine_consecutive_periods(periods)

    def _combine_consecutive_periods(self, periods: List[Dict]) -> List[Dict]:
        """Combine consecutive periods with the same charging state."""
        if not periods:
            return []
            
        sorted_periods = sorted(periods, key=lambda x: x['start_time'])
        combined = []
        current_period = sorted_periods[0].copy()
        
        for next_period in sorted_periods[1:]:
            current_end_hour = current_period['end_time'] // 60
            next_start_hour = next_period['start_time'] // 60
            
            if (current_end_hour % 24 == next_start_hour % 24 and 
                current_period['is_charging'] == next_period['is_charging'] and
                current_period['days'] == next_period['days']):
                current_period['end_time'] = next_period['end_time']
            else:
                combined.append(current_period)
                current_period = next_period.copy()
        
        combined.append(current_period)
        return combined

    def check_overlap(self, period1: Dict, period2: Dict) -> bool:
        """
        Check if two periods overlap in both time and day.
        Periods only overlap if they share at least one common day and their times overlap.
        """
        # First check if the periods share any days
        common_days = period1['days'] & period2['days']  # Bitwise AND of day bits
        if not common_days:
            return False  # No overlap if no common days
        
        def normalize_times(period):
            start = period['start_time']
            end = period['end_time']
            if end <= start:
                end += self.MAX_MINUTES
            return start, end
        
        start1, end1 = normalize_times(period1)
        start2, end2 = normalize_times(period2)
        
        if start2 < start1 and start2 < end2:
            start2 += self.MAX_MINUTES
            end2 += self.MAX_MINUTES
        
        return not (end1 <= start2 or end2 <= start1)

    def _is_period_in_future(self, period: Dict, current_time: datetime) -> bool:
        """Check if a period starts after the current time."""
        period_start_hour = period['start_time'] // 60
        current_hour = current_time.hour
        current_minute = current_time.minute
        current_minutes = current_hour * 60 + current_minute
        
        return period['start_time'] > current_minutes

    def clean_schedule(self, schedule: Dict, current_date: datetime) -> List[Dict]:
        """Remove periods that aren't for the current day or have already passed."""
        if not schedule or 'periods' not in schedule:
            return []
            
        current_day_bit = get_day_bit(current_date)
        current_time = current_date
        
        return [p for p in schedule['periods'] 
                if (p['days'] & current_day_bit) and 
                   self._is_period_in_future(p, current_time)]
    
    def create_register_data(self, periods: List[Dict]) -> List[int]:
        """Create register data format from periods."""
        if len(periods) > self.MAX_PERIODS:
            raise ValueError(f"Maximum {self.MAX_PERIODS} periods allowed")
            
        data = [len(periods)]  # Number of periods
        
        for period in sorted(periods, key=lambda x: x['start_time']):
            combined_flags = self._combine_flags(
                charge_flag=0 if period['is_charging'] else 1,
                days_bits=period['days']
            )
            
            data.extend([
                period['start_time'],
                period['end_time'],
                combined_flags
            ])
            
        # Pad with zeros to reach 43 values
        data.extend([0] * (43 - len(data)))
        return data

    def log_schedule(self, periods: List[Dict], title: str = "Schedule"):
        """Log schedule in human-readable format."""
        weekdays = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 
                   'Thursday', 'Friday', 'Saturday']
        logger.info(f"\n=== {title} ===")
        
        for i, period in enumerate(periods, 1):
            active_days = [weekdays[i] for i in range(7) 
                         if period['days'] & (1 << i)]
            days_str = ", ".join(active_days)
            start_hour = period['start_time'] // 60
            end_hour = period['end_time'] // 60
            mode = "Charging" if period['is_charging'] else "Discharging"
            
            if end_hour <= start_hour:
                end_hour += 24
            
            logger.info(
                f"Period {i}: {mode} on {days_str} "
                f"at {start_hour%24:02d}:00-{end_hour%24:02d}:00"
            )

    def _combine_flags(self, charge_flag: int, days_bits: int) -> int:
        """Combine charge flag and day bits into single value."""
        return days_bits + (256 if charge_flag == 1 else 0)

    def find_optimal_periods(self, today_prices: List[Dict], 
                           tomorrow_prices: List[Dict],
                           target_date: datetime) -> Tuple[List[Dict], List[Dict]]:
        """Find optimal charging and discharging periods."""
        # Get night prices spanning evening to next morning
        night_prices = self._get_night_prices(today_prices, tomorrow_prices)
        charging_periods = self._process_charging_periods(night_prices, target_date)

        # Process day periods (discharging)
        day_df = pd.DataFrame(tomorrow_prices)
        day_mask = day_df['hour'].apply(is_day_hour)
        day_df = day_df[day_mask]
        discharging_periods = self._process_discharging_periods(day_df, get_day_bit(target_date))

        # Sort all periods chronologically
        return charging_periods, discharging_periods

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
                current_periods = self.clean_schedule(current_schedule, now)
                self.log_schedule(current_periods, "Current Schedule")
                
                # Get prices for today and tomorrow
                prices = self.price_fetcher.get_prices()
                if not prices.get('today') or not prices.get('tomorrow'):
                    raise RuntimeError("Failed to fetch prices")
                
                # Keep future periods if SOC > 10%
                if current_soc > 10:
                    logger.info(f"Checking for future periods to preserve (SOC: {current_soc}%)")
                    if current_periods:
                        logger.info(f"Preserving {len(current_periods)} future periods")
                    else:
                        logger.info("No future periods found in current schedule")
                else:
                    logger.info(f"Clearing current schedule due to low SOC ({current_soc}%)")
                    current_periods = []
                
                # Find optimal periods for tomorrow
                charging_periods, discharging_periods = self.find_optimal_periods(
                    prices['today'],
                    prices['tomorrow'], 
                    tomorrow
                )
                new_periods = charging_periods + discharging_periods
                self.log_schedule(new_periods, "New Periods for Tomorrow")
                
                # Merge and check for overlaps
                all_periods = sorted(current_periods + new_periods, 
                                   key=lambda x: x['start_time'])
                final_periods = []
                
                for period in all_periods:
                    overlap = False
                    for existing in final_periods:
                        if self.check_overlap(period, existing):
                            overlap = True
                            break
                    if not overlap:
                        final_periods.append(period)
                
                # Create and write new register data
                new_register_data = self.create_register_data(final_periods)
                self.log_schedule(final_periods, "Final Schedule")

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