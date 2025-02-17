from typing import Dict, List
from datetime import datetime
from config import MAX_MINUTES, MAX_PERIODS
from period_utils import get_day_bit, is_day_hour

class PeriodManager:
    def __init__(self):
        self.MAX_MINUTES = MAX_MINUTES
        self.MAX_PERIODS = MAX_PERIODS

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

    def combine_consecutive_periods(self, periods: List[Dict]) -> List[Dict]:
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
        """Check if two periods overlap in both time and day."""
        common_days = period1['days'] & period2['days']
        if not common_days:
            return False
        
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

    def is_period_in_future(self, period: Dict, current_time: datetime) -> bool:
        """Check if a period starts after the current time."""
        current_minutes = current_time.hour * 60 + current_time.minute
        return period['start_time'] > current_minutes