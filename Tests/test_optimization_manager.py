import unittest
from unittest.mock import patch
from datetime import datetime
import pandas as pd
import sys
import os

# Add the parent directory to the path so we can import the modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optimization_manager import OptimizationManager
from period_manager import PeriodManager
from config import (
    EVENING_START_HOUR, EVENING_END_HOUR, 
    NEXT_DAY_START_HOUR, NEXT_DAY_END_HOUR,
    BATTERY_DISCHARGE_RATE, MIN_SOC_FOR_DISCHARGE
)

class TestOptimizationManager(unittest.TestCase):
    
    def setUp(self):
        """Set up common test fixtures."""
        self.manager = OptimizationManager(max_charging_periods=3, max_discharging_periods=4)
        self.period_manager = PeriodManager()
        
        # Test data
        self.test_time = datetime(2023, 5, 15, 18, 0, 0)  # Monday, 18:00
        self.monday_bit = 1 << 1  # Monday = bit 1
        
        # Sample price data
        self.today_prices = [
            {'hour': h, 'SEK_per_kWh': 1.0 + h/10, 'time_start': None} for h in range(24)
        ]
        self.tomorrow_prices = [
            {'hour': h, 'SEK_per_kWh': 0.8 + h/10, 'time_start': None} for h in range(24)
        ]
        
        # Sample period for a Monday
        self.sample_period = self.period_manager.create_period(
            start_hour=19, 
            end_hour=20, 
            is_charging=False, 
            day_bit=self.monday_bit
        )
    
    def test_get_night_prices(self):
        """Test extracting night prices from today and tomorrow."""
        night_prices = self.manager.get_night_prices(self.today_prices, self.tomorrow_prices)
        
        # Should include 22:00-23:00 from today and 00:00-06:00 from tomorrow
        expected_hours = list(range(22, 24)) + list(range(0, 7))
        actual_hours = [p['hour'] for p in night_prices]
        
        # Check that all expected hours are included
        for h in expected_hours:
            self.assertIn(h % 24, actual_hours)
        
        # Check that no other hours are included
        for h in actual_hours:
            self.assertIn(h, [h % 24 for h in expected_hours])
        
        # Check that prices are sorted from lowest to highest
        self.assertEqual(night_prices, sorted(night_prices, key=lambda x: x['SEK_per_kWh']))
    
    def test_process_charging_periods(self):
        """Test creating charging periods for night hours."""
        night_prices = self.manager.get_night_prices(self.today_prices, self.tomorrow_prices)
        
        charging_periods = self.manager.process_charging_periods(night_prices, self.test_time)
        
        # Should create up to max_charging_periods periods
        self.assertLessEqual(len(charging_periods), self.manager.max_charging_periods)
        
        # All periods should be charging periods
        for period in charging_periods:
            self.assertTrue(period['is_charging'])
    
    def test_process_discharging_periods(self):
        """Test creating discharging periods for daytime."""
        df = pd.DataFrame(self.tomorrow_prices)
        
        discharging_periods = self.manager.process_discharging_periods(df, self.monday_bit)
        
        # Should create up to max_discharging_periods periods
        self.assertLessEqual(len(discharging_periods), self.manager.max_discharging_periods)
        
        # All periods should be discharging periods
        for period in discharging_periods:
            self.assertFalse(period['is_charging'])
    
    def test_find_optimal_periods(self):
        """Test finding optimal charging and discharging periods."""
        charging_periods, discharging_periods = self.manager.find_optimal_periods(
            self.today_prices, 
            self.tomorrow_prices, 
            self.test_time
        )
        
        # Should return charging and discharging periods
        self.assertIsInstance(charging_periods, list)
        self.assertIsInstance(discharging_periods, list)
        
        # Charging periods should be for night, discharging for day
        for period in charging_periods:
            self.assertTrue(period['is_charging'])
            
        for period in discharging_periods:
            self.assertFalse(period['is_charging'])
    
    @patch('optimization_manager.datetime')
    def test_calculate_evening_coverage(self, mock_datetime):
        """Test calculating evening coverage."""
        mock_datetime.now.return_value = self.test_time
        
        # Test with no periods
        evening_prices, hours_covered = self.manager.calculate_evening_coverage(
            [], self.today_prices
        )
        self.assertEqual(hours_covered, 0)
        self.assertEqual(len(evening_prices), EVENING_END_HOUR - EVENING_START_HOUR)
        
        # Test with one period covering one hour
        evening_prices, hours_covered = self.manager.calculate_evening_coverage(
            [self.sample_period], self.today_prices
        )
        self.assertEqual(hours_covered, 1)
        
        # Test with one period covering multiple hours
        multi_hour_period = self.period_manager.create_period(
            start_hour=18, 
            end_hour=21, 
            is_charging=False, 
            day_bit=self.monday_bit
        )
        
        evening_prices, hours_covered = self.manager.calculate_evening_coverage(
            [multi_hour_period], self.today_prices
        )
        self.assertEqual(hours_covered, 3)
        
        # Test with charging period (should be ignored)
        charging_period = self.period_manager.create_period(
            start_hour=18, 
            end_hour=21, 
            is_charging=True,  # Charging period 
            day_bit=self.monday_bit
        )
        
        evening_prices, hours_covered = self.manager.calculate_evening_coverage(
            [charging_period], self.today_prices
        )
        self.assertEqual(hours_covered, 0)  # Charging periods don't count
        
        # Test with wrong day (should be ignored)
        wrong_day_period = self.period_manager.create_period(
            start_hour=18, 
            end_hour=21, 
            is_charging=False, 
            day_bit=1 << 2  # Tuesday, not Monday
        )
        
        evening_prices, hours_covered = self.manager.calculate_evening_coverage(
            [wrong_day_period], self.today_prices
        )
        self.assertEqual(hours_covered, 0)  # Different day doesn't count
    
    def test_calculate_next_day_avg_price(self):
        """Test calculating next day average price."""
        avg_price = self.manager.calculate_next_day_avg_price(self.tomorrow_prices)
        
        # Calculate expected average manually for hours 6:00-22:00
        relevant_prices = [
            p['SEK_per_kWh'] for p in self.tomorrow_prices 
            if NEXT_DAY_START_HOUR <= p['hour'] < NEXT_DAY_END_HOUR
        ]
        expected_avg = sum(relevant_prices) / len(relevant_prices)
        
        self.assertAlmostEqual(avg_price, expected_avg)
        
        # Test with empty data
        empty_prices = []
        avg_price = self.manager.calculate_next_day_avg_price(empty_prices)
        self.assertEqual(avg_price, 0)
    
    def test_calculate_additional_hours(self):
        """Test calculating additional discharge hours based on SOC."""
        # Test with high SOC
        high_soc = 85.0
        available_hours = int((high_soc - MIN_SOC_FOR_DISCHARGE) / BATTERY_DISCHARGE_RATE)
        expected_hours = min(available_hours, EVENING_END_HOUR - EVENING_START_HOUR)
        
        hours_to_add = self.manager.calculate_additional_hours(high_soc, 0)
        self.assertEqual(hours_to_add, expected_hours)
        
        # Test with partially covered evening
        hours_already_covered = 2
        expected_hours = min(available_hours, EVENING_END_HOUR - EVENING_START_HOUR - hours_already_covered)
        
        hours_to_add = self.manager.calculate_additional_hours(high_soc, hours_already_covered)
        self.assertEqual(hours_to_add, expected_hours)
        
        # Test with low SOC
        low_soc = MIN_SOC_FOR_DISCHARGE + 10  # Just enough for less than 1 hour
        expected_hours = int((low_soc - MIN_SOC_FOR_DISCHARGE) / BATTERY_DISCHARGE_RATE)
        
        hours_to_add = self.manager.calculate_additional_hours(low_soc, 0)
        self.assertEqual(hours_to_add, expected_hours)
        
        # Test with minimum SOC
        min_soc = MIN_SOC_FOR_DISCHARGE
        hours_to_add = self.manager.calculate_additional_hours(min_soc, 0)
        self.assertEqual(hours_to_add, 0)  # No hours available
        
        # Test with fully covered evening
        full_coverage = EVENING_END_HOUR - EVENING_START_HOUR
        hours_to_add = self.manager.calculate_additional_hours(high_soc, full_coverage)
        self.assertEqual(hours_to_add, 0)  # Evening already covered

if __name__ == '__main__':
    unittest.main()