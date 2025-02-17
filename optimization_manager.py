from typing import Dict, List, Tuple
import pandas as pd
from datetime import datetime, timedelta
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