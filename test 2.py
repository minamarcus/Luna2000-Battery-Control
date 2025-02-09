#!/usr/bin/env python3
# test_battery_scheduler.py

import unittest
from datetime import datetime, timedelta
import pytz
import logging
from typing import List, Dict, Optional
from battery_schedule import (
    BatteryManager, PriceFetcher, 
    ScheduleManager
)

logger = logging.getLogger(__name__)

class MockBattery:
    """Mock battery for testing purposes."""
    def __init__(self):
        self.register_data = [0] * 43  # Initialize with zeros
        self.connected = False
        
    def connect(self) -> bool:
        """Simulate battery connection."""
        self.connected = True
        logger.info("Connected to mock battery")
        return True
        
    def disconnect(self) -> None:
        """Simulate battery disconnection."""
        self.connected = False
        logger.info("Disconnected from mock battery")
        
    def read_registers(self, address: int, count: int) -> List[int]:
        """Simulate reading registers."""
        return self.register_data
        
    def write_registers(self, address: int, values: List[int]) -> bool:
        """Simulate writing registers."""
        if len(values) == 43:
            self.register_data = values
            return True
        return False

class TestMockBattery(unittest.TestCase):
    def setUp(self):
        self.battery = MockBattery()

    def test_connection(self):
        """Test battery connection and disconnection."""
        self.assertTrue(self.battery.connect())
        self.assertTrue(self.battery.connected)
        self.battery.disconnect()
        self.assertFalse(self.battery.connected)

    def test_register_operations(self):
        """Test reading and writing registers."""
        test_data = [i for i in range(43)]
        self.assertTrue(self.battery.write_registers(0, test_data))
        read_data = self.battery.read_registers(0, 43)
        self.assertEqual(test_data, read_data)

class TestBatteryManager(unittest.TestCase):
    def setUp(self):
        self.manager = BatteryManager()

    def test_schedule_parsing(self):
        """Test parsing of register data into schedule."""
        # Test data: 2 periods
        test_data = [2,  # Number of periods
                    60, 120, 0, 1,  # Period 1: 1am-2am, charging, Sunday
                    180, 240, 1, 2,  # Period 2: 3am-4am, discharging, Monday
                    0] * 4  # Padding
        
        schedule = self.manager._parse_schedule(test_data)
        self.assertEqual(schedule['num_periods'], 2)
        self.assertEqual(len(schedule['periods']), 2)
        
        # Verify first period
        period1 = schedule['periods'][0]
        self.assertEqual(period1['start_time'], 60)
        self.assertEqual(period1['end_time'], 120)
        self.assertTrue(period1['is_charging'])
        self.assertEqual(period1['days'], 1)

    def test_write_schedule(self):
        """Test writing schedule to battery."""
        test_data = [0] * 43
        self.assertTrue(self.manager.write_schedule(test_data))

class TestPriceFetcher(unittest.TestCase):
    def setUp(self):
        self.fetcher = PriceFetcher()

    def test_mock_price_generation(self):
        """Test generation of mock prices."""
        tomorrow = datetime.now(pytz.timezone('Europe/Stockholm')) + timedelta(days=1)
        prices = self.fetcher._generate_mock_prices(tomorrow)
        
        self.assertEqual(len(prices), 24)  # 24 hours
        for price in prices:
            self.assertIn('time_start', price)
            self.assertIn('SEK_per_kWh', price)
            self.assertGreater(price['SEK_per_kWh'], 0)

    def test_price_processing(self):
        """Test processing of price data."""
        prices = self.fetcher.get_prices()
        self.assertIn('tomorrow', prices)
        if prices['tomorrow']:
            for price in prices['tomorrow']:
                self.assertIn('hour', price)
                self.assertIn('time_start', price)
                self.assertIn('sek_per_kwh', price)

class TestScheduleManager(unittest.TestCase):
    def setUp(self):
        self.manager = ScheduleManager()
        self.stockholm_tz = pytz.timezone('Europe/Stockholm')

    def test_day_bit_calculation(self):
        """Test day bit calculation."""
        # Test all days of the week
        test_dates = [
            (datetime(2024, 2, 11, tzinfo=self.stockholm_tz), 1),  # Sunday
            (datetime(2024, 2, 12, tzinfo=self.stockholm_tz), 2),  # Monday
            (datetime(2024, 2, 13, tzinfo=self.stockholm_tz), 4),  # Tuesday
            (datetime(2024, 2, 14, tzinfo=self.stockholm_tz), 8),  # Wednesday
            (datetime(2024, 2, 15, tzinfo=self.stockholm_tz), 16), # Thursday
            (datetime(2024, 2, 16, tzinfo=self.stockholm_tz), 32), # Friday
            (datetime(2024, 2, 17, tzinfo=self.stockholm_tz), 64)  # Saturday
        ]
        
        for date, expected_bit in test_dates:
            self.assertEqual(self.manager.get_day_bit(date), expected_bit)

    def test_period_creation(self):
        """Test period creation with validation."""
        day_bit = 1
        
        # Test valid period
        period = self.manager.create_period(1, 2, True, day_bit)
        self.assertEqual(period['start_time'], 60)
        self.assertEqual(period['end_time'], 120)
        self.assertTrue(period['is_charging'])
        self.assertEqual(period['days'], day_bit)
        
        # Test invalid times
        with self.assertRaises(ValueError):
            self.manager.create_period(-1, 2, True, day_bit)
        with self.assertRaises(ValueError):
            self.manager.create_period(2, 1, True, day_bit)
        with self.assertRaises(ValueError):
            self.manager.create_period(0, 1441, True, day_bit)

    def test_overlap_detection(self):
        """Test period overlap detection."""
        period1 = {'start_time': 60, 'end_time': 120}  # 1am-2am
        period2 = {'start_time': 90, 'end_time': 150}  # 1:30am-2:30am
        period3 = {'start_time': 120, 'end_time': 180} # 2am-3am
        
        self.assertTrue(self.manager.check_overlap(period1, period2))
        self.assertFalse(self.manager.check_overlap(period1, period3))

    def test_schedule_cleaning(self):
        """Test schedule cleaning for current day."""
        now = datetime.now(self.stockholm_tz)
        day_bit = self.manager.get_day_bit(now)
        
        schedule = {
            'periods': [
                {'days': day_bit, 'start_time': 60, 'end_time': 120},     # Current day
                {'days': day_bit << 1, 'start_time': 180, 'end_time': 240} # Next day
            ]
        }
        
        cleaned = self.manager.clean_schedule(schedule, now)
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]['days'], day_bit)

    def test_complete_schedule_update(self):
        """Test complete schedule update process."""
        success = self.manager.update_schedule()
        self.assertTrue(success)

def run_tests():
    """Run all tests with detailed output."""
    suite = unittest.TestLoader().loadTestsFromTestCase(TestMockBattery)
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestBatteryManager))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestPriceFetcher))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestScheduleManager))
    
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)

if __name__ == '__main__':
    run_tests()