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

    def _process_periods(self, df: pd.DataFrame, is_charging: bool, 
                        day_bit: int) -> List[Dict]:
        """Process and create periods for either charging or discharging."""
        periods = []
        used_hours = set()
        
        for _, row in df.iterrows():
            if len(periods) >= 4:
                break
                
            hour = normalize_hour(row['hour'])
            if hour in used_hours:
                continue
                
            # Find contiguous block
            potential_block = [hour]
            for offset in [-1, 1]:
                check_hour = normalize_hour(hour + offset)
                if (check_hour in df['hour'].values and 
                    check_hour not in used_hours):
                    if (is_charging and is_night_hour(check_hour)) or \
                       (not is_charging and is_day_hour(check_hour)):
                        potential_block.append(check_hour)
                    
            potential_block.sort()
            max_hours = min(len(potential_block), 4 - len(periods))
            
            for block_hour in potential_block[:max_hours]:
                if block_hour not in used_hours:
                    end_hour = (block_hour + 1) % 24
                    periods.append(
                        self.create_period(
                            start_hour=block_hour,
                            end_hour=end_hour,
                            is_charging=is_charging,
                            day_bit=day_bit
                        )
                    )
                    used_hours.add(block_hour)
        
        return periods

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
            
        data = [len(periods)]
        
        for period in sorted(periods, key=lambda x: x['start_time']):
            data.extend([
                period['start_time'],
                period['end_time'],
                period['charge_flag'],
                period['days']
            ])
            
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
            
            cleaned_periods = self.clean_schedule(current_schedule, now)
            self.log_schedule(cleaned_periods, "Current Schedule")
            
            # Get prices for tomorrow
            prices = self.price_fetcher.get_prices()
            if not prices.get('tomorrow'):
                logger.error("Failed to fetch tomorrow's prices")
                return False
            
            # Find optimal periods
            charging_periods, discharging_periods = self.find_optimal_periods(
                prices['tomorrow'], tomorrow)
            new_periods = charging_periods + discharging_periods
            self.log_schedule(new_periods, "New Periods for Tomorrow")
            
            # Merge and check for overlaps
            all_periods = sorted(cleaned_periods + new_periods, 
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
