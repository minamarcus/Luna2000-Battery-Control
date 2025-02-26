from typing import Dict, List, Tuple
import pandas as pd
from datetime import datetime, timedelta
from config import (
    logger, EVENING_START_HOUR, EVENING_END_HOUR, 
    NEXT_DAY_START_HOUR, NEXT_DAY_END_HOUR,
    BATTERY_DISCHARGE_RATE, MIN_SOC_FOR_DISCHARGE
)
from period_utils import is_day_hour, get_day_bit
from period_manager import PeriodManager

class OptimizationManager:
    def __init__(self, max_charging_periods: int, max_discharging_periods: int):
        self.max_charging_periods = max_charging_periods
        self.max_discharging_periods = max_discharging_periods
        self.period_manager = PeriodManager()

    def get_night_prices(self, today_prices: List[Dict], tomorrow_prices: List[Dict]) -> List[Dict]:
        """Get prices for night hours (22:00-06:00)."""
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

    def process_charging_periods(self, night_prices: List[Dict], target_date: datetime) -> List[Dict]:
        """Process and create charging periods for night hours."""
        selected_prices = sorted(night_prices[:self.max_charging_periods], key=lambda x: x['hour'])
        
        periods = []
        for price in selected_prices:
            period_date = target_date - timedelta(days=1) if price['hour'] >= 22 else target_date
            day_bit = get_day_bit(period_date)
            
            periods.append(
                self.period_manager.create_period(
                    start_hour=price['hour'],
                    end_hour=(price['hour'] + 1) % 24,
                    is_charging=True,
                    day_bit=day_bit
                )
            )
        
        return self.period_manager.combine_consecutive_periods(periods)

    def process_discharging_periods(self, df: pd.DataFrame, day_bit: int) -> List[Dict]:
        """Process and create periods for discharging during daytime."""
        day_df = df[df['hour'].apply(is_day_hour)]
        best_hours_df = day_df.nlargest(self.max_discharging_periods, 'SEK_per_kWh')
        selected_hours = sorted(best_hours_df['hour'].tolist())
        
        periods = []
        for hour in selected_hours:
            periods.append(
                self.period_manager.create_period(
                    start_hour=hour,
                    end_hour=(hour + 1) % 24,
                    is_charging=False,
                    day_bit=day_bit
                )
            )
        
        return self.period_manager.combine_consecutive_periods(periods)

    def find_optimal_periods(self, today_prices: List[Dict], 
                           tomorrow_prices: List[Dict],
                           target_date: datetime) -> Tuple[List[Dict], List[Dict]]:
        """Find optimal charging and discharging periods."""
        night_prices = self.get_night_prices(today_prices, tomorrow_prices)
        charging_periods = self.process_charging_periods(night_prices, target_date)

        day_df = pd.DataFrame(tomorrow_prices)
        discharging_periods = self.process_discharging_periods(
            day_df, 
            get_day_bit(target_date)
        )

        return charging_periods, discharging_periods
    
    def calculate_evening_coverage(self, current_periods: List[Dict], today_prices: List[Dict]) -> Tuple[List[Dict], float]:
        """
        Calculate how many hours are covered in the evening period by existing schedules.
        
        Args:
            current_periods: List of current schedule periods
            today_prices: List of today's price data
            
        Returns:
            Tuple of (evening price data, hours already covered)
        """
        # Get current day bit
        now = datetime.now()
        current_day_bit = 1 << ((now.weekday() + 1) % 7)  # Sunday=0 convention
        
        # Filter prices for evening hours
        evening_prices = [p for p in today_prices if EVENING_START_HOUR <= p['hour'] < EVENING_END_HOUR]
        
        # Track coverage by hour
        hours_coverage = {hour: False for hour in range(EVENING_START_HOUR, EVENING_END_HOUR)}
        
        # Check each period if it covers evening hours
        for period in current_periods:
            # Skip if not for today
            if not (period['days'] & current_day_bit):
                continue
                
            # Skip charging periods (we only care about discharging for evening optimization)
            if period['is_charging']:
                continue
                
            # Convert to hours for comparison
            start_hour = period['start_time'] // 60
            end_hour = period['end_time'] // 60
            
            # Handle midnight crossing
            if end_hour <= start_hour:
                end_hour += 24
                
            # Check which evening hours are covered
            for hour in range(EVENING_START_HOUR, EVENING_END_HOUR):
                if start_hour <= hour < end_hour:
                    hours_coverage[hour] = True
        
        # Calculate total coverage
        covered_hours = sum(1 for covered in hours_coverage.values() if covered)
        
        logger.info(f"Evening hours already covered: {covered_hours} of {EVENING_END_HOUR - EVENING_START_HOUR}")
        for hour, covered in hours_coverage.items():
            logger.info(f"  Hour {hour:02d}:00: {'Covered' if covered else 'Not covered'}")
        
        return evening_prices, covered_hours
    
    def calculate_next_day_avg_price(self, tomorrow_prices: List[Dict]) -> float:
        """
        Calculate average price for next day within the specified hours.
        
        Args:
            tomorrow_prices: List of tomorrow's price data
            
        Returns:
            Average price for the next day
        """
        relevant_prices = [
            p['SEK_per_kWh'] for p in tomorrow_prices 
            if NEXT_DAY_START_HOUR <= p['hour'] < NEXT_DAY_END_HOUR
        ]
        
        if not relevant_prices:
            return 0
            
        return sum(relevant_prices) / len(relevant_prices)
    
    def calculate_additional_hours(self, current_soc: float, hours_already_covered: float) -> int:
        """
        Calculate how many additional hours we can add based on SOC.
        
        Args:
            current_soc: Current battery state of charge (%)
            hours_already_covered: Hours already covered in the evening period
            
        Returns:
            Number of additional hours we can add
        """
        # Calculate available SOC for discharge
        available_soc = current_soc - MIN_SOC_FOR_DISCHARGE
        
        # Calculate how many hours of discharge we can support
        total_possible_hours = int(available_soc / BATTERY_DISCHARGE_RATE)
        
        # Calculate how many more hours we can add
        additional_hours = min(
            total_possible_hours,
            (EVENING_END_HOUR - EVENING_START_HOUR) - hours_already_covered
        )
        
        logger.info(f"Available SOC for discharge: {available_soc:.1f}%")
        logger.info(f"Total possible discharge hours with current SOC: {total_possible_hours}")
        logger.info(f"Additional hours that can be added: {additional_hours}")
        
        return max(0, additional_hours)