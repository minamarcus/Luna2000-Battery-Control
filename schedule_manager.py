from datetime import datetime, timedelta
import pandas as pd
from typing import List, Dict, Tuple
from config import logger, MAX_PERIODS, MAX_MINUTES, STOCKHOLM_TZ
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

    def create_period(self, start_hour: int, end_hour: int, 
                     is_charging: bool, day_bit: int) -> Dict:
        """Create a valid period entry."""
        start_hour = start_hour % 24
        end_hour = end_hour % 24
        
        start_minutes = start_hour * 60
        
        if end_hour < start_hour:
            end_minutes = (end_hour + 24) * 60
        else:
            end_minutes = end_hour * 60
                
        if not (0 <= start_minutes < self.MAX_MINUTES and 
                0 < end_minutes <= self.MAX_MINUTES * 2):
            raise ValueError(f"Invalid time range: {start_hour}:00-{end_hour}:00")
                
        return {
            'start_time': start_minutes,
            'end_time': end_minutes,
            'charge_flag': 0 if is_charging else 1,
            'days': day_bit,
            'is_charging': is_charging
        }

    def check_overlap(self, period1: Dict, period2: Dict) -> bool:
        """Check if two periods overlap."""
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

    def find_optimal_periods(self, prices_data: List[Dict], 
                           target_date: datetime) -> Tuple[List[Dict], List[Dict]]:
        """Find optimal charging and discharging periods."""
        df = pd.DataFrame(prices_data)
        day_bit = get_day_bit(target_date)

        # Process night periods (charging)
        night_mask = df['hour'].apply(is_night_hour)
        night_df = df[night_mask].sort_values('SEK_per_kWh')
        charging_periods = self._process_periods(night_df, True, day_bit)

        # Process day periods (discharging)
        day_mask = df['hour'].apply(is_day_hour)
        day_df = df[day_mask].sort_values('SEK_per_kWh', ascending=False)
        discharging_periods = self._process_periods(day_df, False, day_bit)

        return charging_periods, discharging_periods

    def _combine_consecutive_periods(self, periods: List[Dict]) -> List[Dict]:
        """Combine consecutive periods with the same charging state."""
        if not periods:
            return []
            
        # Sort periods by start time
        sorted_periods = sorted(periods, key=lambda x: x['start_time'])
        combined = []
        current_period = sorted_periods[0].copy()
        
        for next_period in sorted_periods[1:]:
            current_end_hour = current_period['end_time'] // 60
            next_start_hour = next_period['start_time'] // 60
            
            # Check if periods are consecutive and have the same charging state
            if (current_end_hour == next_start_hour and 
                current_period['is_charging'] == next_period['is_charging'] and
                current_period['days'] == next_period['days']):
                # Extend current period
                current_period['end_time'] = next_period['end_time']
            else:
                # Add current period to combined list and start a new one
                combined.append(current_period)
                current_period = next_period.copy()
        
        # Add the last period
        combined.append(current_period)
        return combined

    def _process_periods(self, df: pd.DataFrame, is_charging: bool, 
                        day_bit: int) -> List[Dict]:
        """Process and create periods for either charging or discharging."""
        # Sort hours by price (ascending for charging, descending for discharging)
        sorted_df = df.sort_values('SEK_per_kWh', 
                                 ascending=is_charging)
        
        # Select the best hours (up to 4)
        selected_hours = []
        for _, row in sorted_df.iterrows():
            hour = normalize_hour(row['hour'])
            if len(selected_hours) >= 4:
                break
            if ((is_charging and is_night_hour(hour)) or 
                (not is_charging and is_day_hour(hour))):
                selected_hours.append(hour)
        
        # Sort hours chronologically
        selected_hours.sort()
        
        # Create initial periods (one hour each)
        periods = []
        for hour in selected_hours:
            periods.append(
                self.create_period(
                    start_hour=hour,
                    end_hour=(hour + 1) % 24,
                    is_charging=is_charging,
                    day_bit=day_bit
                )
            )
        
        # Combine consecutive periods
        return self._combine_consecutive_periods(periods)

    def clean_schedule(self, schedule: Dict, current_date: datetime) -> List[Dict]:
        """Remove periods that aren't for the current day."""
        if not schedule or 'periods' not in schedule:
            return []
        current_day_bit = get_day_bit(current_date)
        return [p for p in schedule['periods'] if p['days'] & current_day_bit]

    def create_register_data(self, periods: List[Dict]) -> List[int]:
        """Create register data format from periods."""
        if len(periods) > self.MAX_PERIODS:
            raise ValueError(f"Maximum {self.MAX_PERIODS} periods allowed")
            
        data = [len(periods)]  # Number of periods
        
        for period in sorted(periods, key=lambda x: x['start_time']):
            # Combine charge flag and days into single value
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
        """Combine charge flag and day bits into single value.
        
        Args:
            charge_flag: 0 for charge, 1 for discharge
            days_bits: Bitmap of active days (1=Sunday, 2=Monday, 4=Tuesday, etc.)
            
        Returns:
            For charging: just the day_bits (e.g., 4 for Tuesday)
            For discharging: day_bits + 256 (e.g., 260 for Tuesday)
        """
        return days_bits + (256 if charge_flag == 1 else 0)

    def should_preserve_today_schedule(self, today_periods: List[Dict], 
                                    today_prices: List[Dict], 
                                    tomorrow_prices: List[Dict]) -> bool:
        """Determine if today's discharge periods should be preserved based on price comparison."""
        now = datetime.now(self.stockholm_tz)
        current_hour = now.hour
        
        # Get remaining discharge periods for today
        remaining_periods = [p for p in today_periods 
                           if not p['is_charging'] and 
                           p['start_time'] // 60 >= current_hour]
        
        if not remaining_periods:
            return True
            
        # Calculate average price for remaining discharge periods today
        today_discharge_hours = set()
        for period in remaining_periods:
            start_hour = period['start_time'] // 60
            end_hour = period['end_time'] // 60
            if end_hour <= start_hour:
                end_hour += 24
            today_discharge_hours.update(range(start_hour, end_hour))
        
        today_prices_dict = {p['hour']: p['SEK_per_kWh'] for p in today_prices}
        today_discharge_prices = [today_prices_dict[h % 24] 
                                for h in today_discharge_hours 
                                if h % 24 in today_prices_dict]
        
        if not today_discharge_prices:
            return True
            
        avg_today_price = sum(today_discharge_prices) / len(today_discharge_prices)
        
        # Find top 4 most expensive hours for tomorrow
        tomorrow_prices_sorted = sorted(tomorrow_prices, 
                                      key=lambda x: x['SEK_per_kWh'], 
                                      reverse=True)
        top_tomorrow_prices = tomorrow_prices_sorted[:4]
        avg_tomorrow_top_price = sum(p['SEK_per_kWh'] 
                                   for p in top_tomorrow_prices) / len(top_tomorrow_prices)
        
        # If tomorrow's prices are significantly higher, don't preserve today's schedule
        return avg_tomorrow_top_price < (avg_today_price * 1.5)

    def update_schedule(self) -> bool:
        """Main function to update the schedule."""
        try:
            current_schedule = self.battery.read_schedule()
            if not current_schedule:
                logger.error("Failed to read current schedule")
                return False

            now = datetime.now(self.stockholm_tz)
            tomorrow = now + timedelta(days=1)
            
            logger.info(f"Updating schedule at {now}")
            
            # Get current schedule
            current_periods = self.clean_schedule(current_schedule, now)
            self.log_schedule(current_periods, "Current Schedule")
            
            # Get prices for today and tomorrow
            prices = self.price_fetcher.get_prices()
            if not prices.get('today') or not prices.get('tomorrow'):
                logger.error("Failed to fetch prices")
                return False
            
            # Determine if we should keep today's schedule
            preserve_today = self.should_preserve_today_schedule(
                current_periods, 
                prices['today'], 
                prices['tomorrow']
            )
            
            if preserve_today:
                logger.info("Preserving today's schedule based on price comparison")
            else:
                logger.info("Clearing today's schedule due to better prices tomorrow")
                current_periods = []
            
            # Find optimal periods for tomorrow
            charging_periods, discharging_periods = self.find_optimal_periods(
                prices['tomorrow'], tomorrow)
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
            return success

        except Exception as e:
            logger.error(f"Unexpected error in schedule update: {e}")
            return False