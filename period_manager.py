from typing import Dict, List
from datetime import datetime
from config import (
    MAX_MINUTES, MAX_PERIODS, PRICE_THRESHOLD_FACTOR, 
    EVENING_START_HOUR, EVENING_END_HOUR
)
from period_utils import get_day_bit, is_day_hour

class PeriodManager:
    def __init__(self):
        self.MAX_MINUTES = MAX_MINUTES
        self.MAX_PERIODS = MAX_PERIODS
        self.price_threshold_factor = PRICE_THRESHOLD_FACTOR

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
    
    def compare_period_prices(self, current_periods: List[Dict], 
                             new_discharging_periods: List[Dict], 
                             prices: Dict[str, List[Dict]]) -> bool:
        """
        Compare prices between current periods and new discharging periods.
        Returns True if new periods are more profitable by the configured threshold factor.
        
        Args:
            current_periods: List of current schedule periods
            new_discharging_periods: List of new discharging periods for tomorrow
            prices: Dictionary containing today's and tomorrow's prices
        """
        # Filter out charging periods from current schedule
        current_discharge_periods = [p for p in current_periods if not p['is_charging']]
        if not current_discharge_periods:
            return True  # No current discharge periods to compare

        # Calculate average price for current discharge periods
        current_prices = []
        for period in current_discharge_periods:
            start_hour = period['start_time'] // 60
            end_hour = period['end_time'] // 60
            if end_hour <= start_hour:
                end_hour += 24
            for hour in range(start_hour, end_hour):
                hour_price = next(
                    (p['SEK_per_kWh'] for p in prices['today'] if p['hour'] == hour % 24),
                    None
                )
                if hour_price:
                    current_prices.append(hour_price)
        
        if not current_prices:
            return True  # No valid prices found for current periods
        
        current_avg_price = sum(current_prices) / len(current_prices)

        # Calculate average price for new discharge periods
        new_prices = []
        for period in new_discharging_periods:
            start_hour = period['start_time'] // 60
            end_hour = period['end_time'] // 60
            if end_hour <= start_hour:
                end_hour += 24
            for hour in range(start_hour, end_hour):
                hour_price = next(
                    (p['SEK_per_kWh'] for p in prices['tomorrow'] if p['hour'] == hour % 24),
                    None
                )
                if hour_price:
                    new_prices.append(hour_price)
        
        if not new_prices:
            return False  # No valid prices found for new periods
            
        new_avg_price = sum(new_prices) / len(new_prices)

        # Check if new prices exceed the threshold factor
        return new_avg_price >= (current_avg_price * self.price_threshold_factor)
        
    def create_evening_periods(
        self, 
        current_time: datetime, 
        hours_to_add: int, 
        current_periods: List[Dict],
        evening_prices: List[Dict],
        hours_already_covered: float
    ) -> List[Dict]:
        """
        Create new periods for evening optimization. Groups consecutive hours into single periods.
        
        Args:
            current_time: Current datetime
            hours_to_add: Number of hours to add
            current_periods: Existing periods
            evening_prices: Evening price data
            hours_already_covered: Hours already covered in evening
            
        Returns:
            List of new periods to add
        """
        if hours_to_add <= 0:
            return []
            
        # Get current day bit
        current_day_bit = 1 << ((current_time.weekday() + 1) % 7)  # Sunday=0 convention
        
        # Get current hour coverage status
        hours_coverage = {hour: False for hour in range(EVENING_START_HOUR, EVENING_END_HOUR)}
        
        for period in current_periods:
            # Skip if not for today
            if not (period['days'] & current_day_bit):
                continue
                
            # Skip charging periods
            if period['is_charging']:
                continue
                
            # Convert to hours for comparison
            start_hour = period['start_time'] // 60
            end_hour = period['end_time'] // 60
            
            # Handle midnight crossing
            if end_hour <= start_hour:
                end_hour += 24
                
            # Mark which evening hours are covered
            for hour in range(EVENING_START_HOUR, EVENING_END_HOUR):
                if start_hour <= hour < end_hour:
                    hours_coverage[hour] = True
        
        # Sort evening hours by price (highest first)
        sorted_hours = sorted(
            [(hour, next((p['SEK_per_kWh'] for p in evening_prices if p['hour'] == hour), 0)) 
             for hour in range(EVENING_START_HOUR, EVENING_END_HOUR) if not hours_coverage[hour]],
            key=lambda x: x[1],
            reverse=True
        )
        
        # Take the top N hours based on hours_to_add
        best_hours = [h[0] for h in sorted_hours[:hours_to_add]]
        
        if not best_hours:
            return []
            
        # Sort by hour for grouping
        best_hours.sort()
        
        # Group consecutive hours
        groups = []
        current_group = [best_hours[0]]
        
        for i in range(1, len(best_hours)):
            if best_hours[i] == best_hours[i-1] + 1:
                current_group.append(best_hours[i])
            else:
                groups.append(current_group)
                current_group = [best_hours[i]]
        
        groups.append(current_group)
        
        # Create ONE period per group
        new_periods = []
        for group in groups:
            start_hour = group[0]
            end_hour = group[-1] + 1
            
            new_periods.append(
                self.create_period(
                    start_hour=start_hour,
                    end_hour=end_hour,
                    is_charging=False,
                    day_bit=current_day_bit
                )
            )
        
        return new_periods