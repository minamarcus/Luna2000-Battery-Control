from typing import Dict, List
from datetime import datetime
from config import logger
from period_utils import get_day_bit
from period_manager import PeriodManager

class ScheduleDataManager:
    def __init__(self, max_periods: int):
        self.MAX_PERIODS = max_periods
        self.period_manager = PeriodManager()

    def clean_schedule(self, schedule: Dict, current_date: datetime) -> List[Dict]:
        """Remove periods that aren't for the current day or have already passed."""
        if not schedule or 'periods' not in schedule:
            return []
            
        current_day_bit = get_day_bit(current_date)
        
        return [p for p in schedule['periods'] 
                if (p['days'] & current_day_bit) and 
                   PeriodManager().is_period_in_future(p, current_date)]

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

    def _combine_flags(self, charge_flag: int, days_bits: int) -> int:
        """Combine charge flag and day bits into single value."""
        return days_bits + (256 if charge_flag == 1 else 0)

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