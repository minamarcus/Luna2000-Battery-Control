import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import pytz
import sys
import os

# Add the parent directory to the path so we can import the modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    EVENING_START_HOUR, EVENING_END_HOUR, 
    BATTERY_DISCHARGE_RATE, MIN_SOC_FOR_DISCHARGE, 
    EVENING_PRICE_THRESHOLD
)
from optimization_manager import OptimizationManager
from period_manager import PeriodManager
from schedule_manager import ScheduleManager
from battery_manager import BatteryManager

class TestEveningOptimization(unittest.TestCase):
    
    def setUp(self):
        """Set up common test fixtures."""
        # Create a fixed datetime for testing
        self.test_time = datetime(2023, 5, 15, 18, 0, 0)  # Monday, 18:00
        self.test_day_bit = 1 << 1  # Monday = bit 1
        
        # Mock the battery manager
        self.mock_battery = MagicMock(spec=BatteryManager)
        self.mock_battery.get_soc.return_value = 80.0  # Good SOC level
        
        # Create instances for testing
        self.period_manager = PeriodManager()
        self.optimization_manager = OptimizationManager(max_charging_periods=3, max_discharging_periods=4)
        
        # Sample price data
        self.today_prices = [
            {'hour': h, 'SEK_per_kWh': 1.0 + h/10} for h in range(24)
        ]
        self.tomorrow_prices = [
            {'hour': h, 'SEK_per_kWh': 0.8 + h/10} for h in range(24)
        ]
        
        # Mock schedule data
        self.empty_schedule = {'num_periods': 0, 'periods': [], 'raw_data': [0] * 43}
        
        # Sample periods for testing
        self.sample_period = self.period_manager.create_period(
            start_hour=19, 
            end_hour=20, 
            is_charging=False, 
            day_bit=self.test_day_bit
        )

    @patch('optimization_manager.datetime')
    def test_calculate_evening_coverage(self, mock_datetime):
        """Test the function that calculates evening hour coverage."""
        # Configure mock to return fixed datetime
        mock_datetime.now.return_value = self.test_time
        
        # Test with no periods
        evening_prices, hours_covered = self.optimization_manager.calculate_evening_coverage(
            [], self.today_prices
        )
        self.assertEqual(hours_covered, 0)
        self.assertEqual(len(evening_prices), EVENING_END_HOUR - EVENING_START_HOUR)
        
        # Test with one period covering one hour
        with patch('optimization_manager.datetime') as mock_datetime:
            mock_datetime.now.return_value = self.test_time
            single_period = [self.sample_period]  # Covers 19:00-20:00
            evening_prices, hours_covered = self.optimization_manager.calculate_evening_coverage(
                single_period, self.today_prices
            )
            self.assertEqual(hours_covered, 1)
        
        # Test with one period covering multiple hours
        with patch('optimization_manager.datetime') as mock_datetime:
            mock_datetime.now.return_value = self.test_time
            multi_hour_period = [self.period_manager.create_period(
                start_hour=18, end_hour=21, is_charging=False, day_bit=self.test_day_bit
            )]
            evening_prices, hours_covered = self.optimization_manager.calculate_evening_coverage(
                multi_hour_period, self.today_prices
            )
            self.assertEqual(hours_covered, 3)
    
    def test_calculate_next_day_avg_price(self):
        """Test the function for calculating next day average prices."""
        avg_price = self.optimization_manager.calculate_next_day_avg_price(self.tomorrow_prices)
        
        # Calculate expected average manually
        expected_prices = [p['SEK_per_kWh'] for p in self.tomorrow_prices 
                         if 6 <= p['hour'] < 22]
        expected_avg = sum(expected_prices) / len(expected_prices)
        
        self.assertAlmostEqual(avg_price, expected_avg)
    
    def test_calculate_additional_hours(self):
        """Test the function that calculates how many additional hours to add."""
        # Test with high SOC
        high_soc = 80.0
        hours_already_covered = 1
        
        # Calculate expected result - max is constrained by SOC
        # With 80% SOC, MIN_SOC=10%, and RATE=25%, we can discharge for 2 hours, not 3
        expected_hours = 2  # (80-10)/25 = 2.8, but as an int it's 2
        
        hours_to_add = self.optimization_manager.calculate_additional_hours(high_soc, hours_already_covered)
        
        # We should get what the function calculates, which is 2 hours based on SOC limitation
        self.assertEqual(hours_to_add, expected_hours)
        
        # Test with low SOC
        low_soc = MIN_SOC_FOR_DISCHARGE + BATTERY_DISCHARGE_RATE * 1.5  # Enough for 1.5 hours
        hours_to_add = self.optimization_manager.calculate_additional_hours(low_soc, hours_already_covered)
        
        # We should be able to add only 1 hour
        self.assertEqual(hours_to_add, 1)
        
        # Test with SOC at minimum threshold
        min_soc = MIN_SOC_FOR_DISCHARGE
        hours_to_add = self.optimization_manager.calculate_additional_hours(min_soc, hours_already_covered)
        
        # We should not be able to add any hours
        self.assertEqual(hours_to_add, 0)
    
    def test_create_evening_periods(self):
        """Test the function that creates evening periods."""
        with patch('period_manager.datetime') as mock_datetime:
            mock_datetime.now.return_value = self.test_time
            
            # Test with no existing periods, adding 2 hours
            new_periods = self.period_manager.create_evening_periods(
                self.test_time, 
                2, 
                [], 
                self.today_prices[EVENING_START_HOUR:EVENING_END_HOUR], 
                0
            )
            
            # Should create periods for the 2 most expensive hours
            self.assertEqual(len(new_periods), 1)  # Should combine consecutive hours
            
            # Test with existing periods, avoid overlapping
            existing_period = self.sample_period  # 19:00-20:00
            new_periods = self.period_manager.create_evening_periods(
                self.test_time, 
                2, 
                [existing_period], 
                self.today_prices[EVENING_START_HOUR:EVENING_END_HOUR], 
                1
            )
            
            # Should create periods avoiding 19:00-20:00
            for period in new_periods:
                start_hour = period['start_time'] // 60
                end_hour = period['end_time'] // 60
                self.assertFalse(
                    (start_hour <= 19 < end_hour) or 
                    (start_hour < 20 <= end_hour)
                )

    @patch('schedule_manager.datetime')
    @patch('optimization_manager.datetime')
    @patch('period_manager.datetime')
    def test_update_evening_schedule_success(self, mock_pm_datetime, mock_om_datetime, mock_sm_datetime):
        """Test a successful evening schedule update."""
        # Set up all datetime mocks to return the same time
        test_time = self.test_time
        mock_pm_datetime.now.return_value = test_time
        mock_om_datetime.now.return_value = test_time
        mock_sm_datetime.now.return_value = test_time
        
        # Mock battery manager
        mock_battery = MagicMock()
        mock_battery.get_soc.return_value = 80.0
        mock_battery.read_schedule.return_value = self.empty_schedule
        mock_battery.write_schedule.return_value = True
        
        # Mock price fetcher
        mock_price_fetcher = MagicMock()
        mock_price_fetcher.get_prices.return_value = {
            'today': self.today_prices,
            'tomorrow': self.tomorrow_prices
        }
        
        # Create ScheduleManager with mocks
        with patch('schedule_manager.BatteryManager', return_value=mock_battery), \
             patch('schedule_manager.PriceFetcher', return_value=mock_price_fetcher):
            
            schedule_manager = ScheduleManager("test_host")
            
            # Test evening update
            result = schedule_manager.update_evening_schedule()
            
            # Verify result
            self.assertTrue(result)
            
            # Check that battery.write_schedule was called
            mock_battery.write_schedule.assert_called_once()
    
    @patch('schedule_manager.datetime')
    def test_update_evening_schedule_low_soc(self, mock_datetime):
        """Test evening schedule update with low SOC."""
        mock_datetime.now.return_value = self.test_time
        
        # Mock battery manager
        mock_battery = MagicMock()
        mock_battery.get_soc.return_value = MIN_SOC_FOR_DISCHARGE - 1  # Below threshold
        
        # Create ScheduleManager with mocks
        with patch('schedule_manager.BatteryManager', return_value=mock_battery):
            schedule_manager = ScheduleManager("test_host")
            
            # Test evening update
            result = schedule_manager.update_evening_schedule()
            
            # Should return True but not do anything
            self.assertTrue(result)
            
            # Check that battery.write_schedule was not called
            mock_battery.write_schedule.assert_not_called()
    
    @patch('schedule_manager.datetime')
    def test_update_evening_schedule_next_day_price_too_high(self, mock_datetime):
        """Test when next day prices are too high compared to evening."""
        mock_datetime.now.return_value = self.test_time
        
        # Mock battery manager
        mock_battery = MagicMock()
        mock_battery.get_soc.return_value = 80.0
        mock_battery.read_schedule.return_value = self.empty_schedule
        
        # Create price data where tomorrow is much more expensive
        today_prices = [{'hour': h, 'SEK_per_kWh': 1.0} for h in range(24)]
        tomorrow_prices = [{'hour': h, 'SEK_per_kWh': 1.0 * EVENING_PRICE_THRESHOLD * 2} for h in range(24)]
        
        # Mock price fetcher
        mock_price_fetcher = MagicMock()
        mock_price_fetcher.get_prices.return_value = {
            'today': today_prices,
            'tomorrow': tomorrow_prices
        }
        
        # Create ScheduleManager with mocks
        with patch('schedule_manager.BatteryManager', return_value=mock_battery), \
             patch('schedule_manager.PriceFetcher', return_value=mock_price_fetcher):
            
            schedule_manager = ScheduleManager("test_host")
            
            # Test evening update
            result = schedule_manager.update_evening_schedule()
            
            # Should return True but not do anything
            self.assertTrue(result)
            
            # Check that battery.write_schedule was not called
            mock_battery.write_schedule.assert_not_called()
    
    @patch('schedule_manager.datetime')
    def test_preserve_tomorrow_periods(self, mock_datetime):
        """Test that tomorrow's periods are preserved during evening optimization."""
        mock_datetime.now.return_value = self.test_time
        
        # Create a schedule with tomorrow's periods
        today_bit = 1 << 1  # Monday
        tomorrow_bit = 1 << 2  # Tuesday
        
        # Today period (19:00-20:00 Monday)
        today_period = self.period_manager.create_period(
            start_hour=19, end_hour=20, is_charging=False, day_bit=today_bit
        )
        
        # Tomorrow period (10:00-11:00 Tuesday)
        tomorrow_period = self.period_manager.create_period(
            start_hour=10, end_hour=11, is_charging=False, day_bit=tomorrow_bit
        )
        
        # Create schedule with both periods
        schedule_with_tomorrow = {
            'num_periods': 2,
            'periods': [today_period, tomorrow_period],
            'raw_data': [2, 1140, 1200, 1, 2, 600, 660, 1, 4] + [0] * 34
        }
        
        # Mock battery manager
        mock_battery = MagicMock()
        mock_battery.get_soc.return_value = 80.0
        mock_battery.read_schedule.return_value = schedule_with_tomorrow
        mock_battery.write_schedule.return_value = True
        
        # Mock price fetcher
        mock_price_fetcher = MagicMock()
        mock_price_fetcher.get_prices.return_value = {
            'today': self.today_prices,
            'tomorrow': self.tomorrow_prices
        }
        
        # Create ScheduleManager with mocks
        with patch('schedule_manager.BatteryManager', return_value=mock_battery), \
             patch('schedule_manager.PriceFetcher', return_value=mock_price_fetcher), \
             patch('optimization_manager.datetime') as mock_om_datetime, \
             patch('period_manager.datetime') as mock_pm_datetime, \
             patch('schedule_manager.ScheduleDataManager') as mock_sdm_class:
            
            # Set up datetime mocks
            mock_om_datetime.now.return_value = self.test_time
            mock_pm_datetime.now.return_value = self.test_time
            
            # Set up mock for schedule_data_manager
            mock_sdm = MagicMock()
            mock_sdm_class.return_value = mock_sdm
            
            schedule_manager = ScheduleManager("test_host")
            
            # Override the schedule_data_manager with our mock
            schedule_manager.schedule_data_manager = mock_sdm
            
            # Make the mock return the input data unmodified for create_register_data
            mock_sdm.create_register_data.side_effect = lambda periods: [len(periods)] + [0] * 42
            
            # Test evening update
            result = schedule_manager.update_evening_schedule()
            
            # Verify result
            self.assertTrue(result)
            
            # Verify that write_schedule was called
            mock_battery.write_schedule.assert_called_once()
            
            # Verify that create_register_data was called
            mock_sdm.create_register_data.assert_called_once()
            
            # Extract the periods that were passed to create_register_data
            args, _ = mock_sdm.create_register_data.call_args
            periods = args[0]
            
            # Check if tomorrow's period is in the list
            tomorrow_periods = [p for p in periods if p['days'] == tomorrow_bit]
            self.assertEqual(len(tomorrow_periods), 1)
            self.assertEqual(tomorrow_periods[0]['start_time'], 600)  # 10:00

if __name__ == '__main__':
    unittest.main()