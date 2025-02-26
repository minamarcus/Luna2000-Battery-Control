import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import pytz
import sys
import os

# Add the parent directory to the path so we can import the modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    EVENING_START_HOUR, EVENING_END_HOUR, NEXT_DAY_START_HOUR, NEXT_DAY_END_HOUR,
    BATTERY_DISCHARGE_RATE, MIN_SOC_FOR_DISCHARGE, EVENING_PRICE_THRESHOLD,
    STOCKHOLM_TZ
)
from battery_manager import BatteryManager
from schedule_manager import ScheduleManager

class TestScheduleManagerIntegration(unittest.TestCase):
    
    def setUp(self):
        """Set up common test fixtures."""
        # Create a fixed datetime for testing
        self.test_time = datetime(2023, 5, 15, 18, 0, 0, tzinfo=STOCKHOLM_TZ)  # Monday, 18:00
        
        # Sample price data
        self.today_prices = [
            {'hour': h, 'SEK_per_kWh': 1.0 + h/10} for h in range(24)
        ]
        self.tomorrow_prices = [
            {'hour': h, 'SEK_per_kWh': 0.8 + h/10} for h in range(24)
        ]
        
        # Sample schedule data
        self.empty_register_data = [0] * 43
        self.empty_schedule = {'num_periods': 0, 'periods': [], 'raw_data': self.empty_register_data}
        
        # Configure mocks
        self.setup_mocks()
    
    def setup_mocks(self):
        """Set up mock objects."""
        # Mock the battery manager
        self.mock_battery = MagicMock(spec=BatteryManager)
        self.mock_battery.get_soc.return_value = 80.0
        self.mock_battery.read_schedule.return_value = self.empty_schedule
        self.mock_battery.write_schedule.return_value = True
        
        # Mock the price fetcher
        self.mock_price_fetcher = MagicMock()
        self.mock_price_fetcher.get_prices.return_value = {
            'today': self.today_prices,
            'tomorrow': self.tomorrow_prices
        }
        
        # Patches for datetime
        self.datetime_patcher = patch('schedule_manager.datetime')
        self.mock_datetime = self.datetime_patcher.start()
        self.mock_datetime.now.return_value = self.test_time
        
        # Patch the BatteryManager to return our mock
        self.battery_patcher = patch('schedule_manager.BatteryManager')
        self.mock_battery_class = self.battery_patcher.start()
        self.mock_battery_class.return_value = self.mock_battery
        
        # Patch the PriceFetcher to return our mock
        self.price_patcher = patch('schedule_manager.PriceFetcher')
        self.mock_price_class = self.price_patcher.start()
        self.mock_price_class.return_value = self.mock_price_fetcher
    
    def tearDown(self):
        """Clean up patches."""
        self.datetime_patcher.stop()
        self.battery_patcher.stop()
        self.price_patcher.stop()
    
    def test_full_evening_flow_empty_schedule(self):
        """Test the full evening optimization flow with an empty schedule."""
        # Create the schedule manager
        scheduler = ScheduleManager("test_host")
        
        # Run the evening update
        result = scheduler.update_evening_schedule()
        
        # Verify success
        self.assertTrue(result)
        
        # Verify that battery.write_schedule was called
        self.mock_battery.write_schedule.assert_called_once()
        
        # Get the created periods from the write_schedule call
        args, _ = self.mock_battery.write_schedule.call_args
        register_data = args[0]
        
        # There should be at least one period (for evening optimization)
        self.assertGreater(register_data[0], 0)  # First value is number of periods
    
    def test_full_evening_flow_with_existing_periods(self):
        """Test the full evening optimization flow with existing periods."""
        # Create a schedule with some existing periods
        monday_bit = 1 << 1  # Monday
        tuesday_bit = 1 << 2  # Tuesday
        
        # Create a period for Monday 18:00-19:00
        existing_period_data = [
            1,  # One period
            1080, 1140, 1, 2,  # 18:00-19:00, discharging, Monday
        ] + [0] * 38
        
        existing_schedule = {
            'num_periods': 1,
            'periods': [
                {
                    'start_time': 1080,  # 18:00
                    'end_time': 1140,    # 19:00
                    'charge_flag': 1,    # Discharging
                    'days': monday_bit,
                    'is_charging': False
                }
            ],
            'raw_data': existing_period_data
        }
        
        # Update the mock to return our existing schedule
        self.mock_battery.read_schedule.return_value = existing_schedule
        
        # Create the schedule manager
        scheduler = ScheduleManager("test_host")
        
        # Run the evening update
        result = scheduler.update_evening_schedule()
        
        # Verify success
        self.assertTrue(result)
        
        # Verify that battery.write_schedule was called
        self.mock_battery.write_schedule.assert_called_once()
        
        # Get the created periods from the write_schedule call
        args, _ = self.mock_battery.write_schedule.call_args
        register_data = args[0]
        
        # There should be more than one period now
        self.assertGreater(register_data[0], 1)
    
    def test_evening_flow_low_soc(self):
        """Test the evening optimization with SOC below threshold."""
        # Set SOC below threshold
        self.mock_battery.get_soc.return_value = MIN_SOC_FOR_DISCHARGE - 1
        
        # Create the schedule manager
        scheduler = ScheduleManager("test_host")
        
        # Run the evening update
        result = scheduler.update_evening_schedule()
        
        # Should return True (success) but not call write_schedule
        self.assertTrue(result)
        self.mock_battery.write_schedule.assert_not_called()
    
    def test_evening_flow_full_coverage(self):
        """Test when evening is already fully covered by existing periods."""
        # Create a schedule with periods covering the entire evening
        monday_bit = 1 << 1  # Monday
        
        # Create periods covering 18:00-22:00
        existing_schedule = {
            'num_periods': 1,
            'periods': [
                {
                    'start_time': 1080,  # 18:00
                    'end_time': 1320,    # 22:00
                    'charge_flag': 1,    # Discharging
                    'days': monday_bit,
                    'is_charging': False
                }
            ],
            'raw_data': [1, 1080, 1320, 1, 2] + [0] * 38
        }
        
        # Update the mock to return our existing schedule
        self.mock_battery.read_schedule.return_value = existing_schedule
        
        # Create the schedule manager
        scheduler = ScheduleManager("test_host")
        
        # Run the evening update
        result = scheduler.update_evening_schedule()
        
        # Should return True but not call write_schedule (no changes needed)
        self.assertTrue(result)
        self.mock_battery.write_schedule.assert_not_called()
    
    def test_evening_flow_high_next_day_prices(self):
        """Test when next day prices are too high compared to evening."""
        # Create price data where tomorrow is much more expensive
        tomorrow_prices = [
            {'hour': h, 'SEK_per_kWh': (1.0 + h/10) * EVENING_PRICE_THRESHOLD * 1.5} 
            for h in range(24)
        ]
        
        self.mock_price_fetcher.get_prices.return_value = {
            'today': self.today_prices,
            'tomorrow': tomorrow_prices
        }
        
        # Create the schedule manager
        scheduler = ScheduleManager("test_host")
        
        # Run the evening update
        result = scheduler.update_evening_schedule()
        
        # Should return True but not call write_schedule (next day prices too high)
        self.assertTrue(result)
        self.mock_battery.write_schedule.assert_not_called()

    def test_both_updates_in_sequence(self):
        """Test running both regular and evening updates in sequence."""
        # First, simulate the regular update
        scheduler = ScheduleManager("test_host")
        result_regular = scheduler.update_schedule()
        self.assertTrue(result_regular)
        
        # Reset mocks for the evening update
        self.mock_battery.reset_mock()
        self.mock_price_fetcher.reset_mock()
        
        # Create a schedule with tomorrow's periods (as if created by regular update)
        tuesday_bit = 1 << 2  # Tuesday
        tomorrow_schedule = {
            'num_periods': 1,
            'periods': [
                {
                    'start_time': 600,   # 10:00
                    'end_time': 660,     # 11:00
                    'charge_flag': 1,    # Discharging
                    'days': tuesday_bit,
                    'is_charging': False
                }
            ],
            'raw_data': [1, 600, 660, 1, 4] + [0] * 38
        }
        
        # Update the mock to return our tomorrow schedule
        self.mock_battery.read_schedule.return_value = tomorrow_schedule
        
        # Now run the evening update
        result_evening = scheduler.update_evening_schedule()
        self.assertTrue(result_evening)
        
        # Verify that battery.write_schedule was called
        self.mock_battery.write_schedule.assert_called_once()
        
        # Get what was written to the battery
        args, _ = self.mock_battery.write_schedule.call_args
        register_data = args[0]
        
        # There should be more than one period now (tomorrow's + evening periods)
        self.assertGreater(register_data[0], 1)

if __name__ == '__main__':
    unittest.main()