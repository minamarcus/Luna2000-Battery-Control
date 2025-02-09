import unittest
from unittest.mock import Mock, patch
from datetime import datetime
import pytz
import pandas as pd
from typing import List, Dict

from battery_manager import BatteryManager
from price_fetcher import PriceFetcher
from schedule_manager import ScheduleManager
from period_utils import normalize_hour, is_night_hour, is_day_hour, get_day_bit
from config import STOCKHOLM_TZ
from register_debug import print_register_data, verify_register_data

class MockModbusClient:
    """Mock implementation of ModbusTcpClient with proper error handling"""
    def __init__(self, host: str, should_fail: bool = False):
        self.host = host
        self.connected = False
        self.registers = [0] * 43
        self.should_fail = should_fail
        
    def connect(self) -> bool:
        if self.should_fail:
            return False
        self.connected = True
        return True
        
    def close(self):
        self.connected = False
        
    def read_holding_registers(self, address: int, count: int, slave: int):
        if self.should_fail:
            response = Mock()
            response.isError = lambda: True
            return response
            
        response = Mock()
        response.registers = self.registers
        response.isError = lambda: False
        return response
        
    def write_registers(self, address: int, values: List[int], slave: int):
        if self.should_fail:
            response = Mock()
            response.isError = lambda: True
            return response
            
        response = Mock()
        response.isError = lambda: False
        self.registers = values.copy()
        return response

class MockResponse:
    """Mock API response with error simulation"""
    def __init__(self, json_data: Dict, should_fail: bool = False):
        self.json_data = json_data
        self.should_fail = should_fail
        
    def json(self):
        if self.should_fail:
            raise ValueError("JSON decode error")
        return self.json_data
        
    def raise_for_status(self):
        if self.should_fail:
            raise Exception("HTTP error")

class TestBatteryManager(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures before each test method."""
        self.battery = BatteryManager("192.168.1.100")
        self.mock_client = MockModbusClient("192.168.1.100")

    def test_connect_success(self):
        """Test successful connection to battery."""
        with patch('battery_manager.ModbusTcpClient', return_value=self.mock_client):
            client = self.battery.connect()
            self.assertIsNotNone(client)
            self.assertTrue(self.mock_client.connected)

    def test_connect_failure(self):
        """Test failed connection to battery."""
        failed_client = MockModbusClient("192.168.1.100", should_fail=True)
        with patch('battery_manager.ModbusTcpClient', return_value=failed_client):
            client = self.battery.connect()
            self.assertIsNone(client)

    def test_read_schedule_success(self):
        """Test successful schedule reading with valid data."""
        test_registers = [
            2,  # Number of periods
            60, 120, 0, 1,  # Period 1: 1h-2h, charging, Monday
            180, 240, 1, 2,  # Period 2: 3h-4h, discharging, Tuesday
        ] + [0] * 35  # Padding
        
        self.mock_client.registers = test_registers
        with patch('battery_manager.ModbusTcpClient', return_value=self.mock_client):
            schedule = self.battery.read_schedule()
            
            self.assertIsNotNone(schedule)
            self.assertEqual(schedule['num_periods'], 2)
            self.assertEqual(len(schedule['periods']), 2)
            
            # Verify first period
            period1 = schedule['periods'][0]
            self.assertEqual(period1['start_time'], 60)
            self.assertEqual(period1['end_time'], 120)
            self.assertTrue(period1['is_charging'])
            self.assertEqual(period1['days'], 1)
            
            # Verify second period
            period2 = schedule['periods'][1]
            self.assertEqual(period2['start_time'], 180)
            self.assertEqual(period2['end_time'], 240)
            self.assertFalse(period2['is_charging'])
            self.assertEqual(period2['days'], 2)

    def test_read_schedule_failure(self):
        """Test schedule reading with connection failure."""
        failed_client = MockModbusClient("192.168.1.100", should_fail=True)
        with patch('battery_manager.ModbusTcpClient', return_value=failed_client):
            schedule = self.battery.read_schedule()
            self.assertIsNone(schedule)

    def test_write_schedule_success(self):
        """Test successful schedule writing."""
        test_data = [0] * 43  # Initialize array with correct length
        test_data[0] = 2  # Number of periods
        # Period 1: 7h-8h, charging, Monday
        test_data[1:5] = [420, 480, 0, 1]
        # Period 2: 18h-19h, discharging, Monday
        test_data[5:9] = [1080, 1140, 1, 1]
        
        print("\nTesting write_schedule with register data:")
        print("\nRegister data to be written:")
        print_register_data(test_data, "Test Schedule")
        verify_register_data(test_data)
        
        with patch('battery_manager.ModbusTcpClient', return_value=self.mock_client):
            success = self.battery.write_schedule(test_data)
            self.assertTrue(success)
            self.assertEqual(self.mock_client.registers, test_data)
            
            print("\nVerifying written register data:")
            print_register_data(self.mock_client.registers, "Written Data")
            verify_register_data(self.mock_client.registers)

    def test_write_schedule_invalid_length(self):
        """Test schedule writing with invalid data length."""
        invalid_data = [1, 60, 120, 0, 1]  # Too short
        
        print("\nTesting write_schedule with invalid data length:")
        print(f"\nAttempting to write {len(invalid_data)} values:")
        print(f"[{', '.join(str(x) for x in invalid_data)}]")
        print("\nExpected: Should fail due to invalid length (not 43 values)")
        
        with patch('battery_manager.ModbusTcpClient', return_value=self.mock_client):
            success = self.battery.write_schedule(invalid_data)
            self.assertFalse(success)  # Should return False for invalid data length
            
        print(f"\nResult: {'Failed as expected' if not success else 'Unexpectedly succeeded'}")

class TestPeriodUtils(unittest.TestCase):
    """Test period utility functions."""
    
    def test_normalize_hour(self):
        """Test hour normalization."""
        test_cases = [
            (24, 0),
            (25, 1),
            (-1, 23),
            (48, 0),
            (-24, 0),
            (12, 12)
        ]
        for input_hour, expected in test_cases:
            with self.subTest(input_hour=input_hour):
                self.assertEqual(normalize_hour(input_hour), expected)
        
    def test_is_night_hour(self):
        """Test night hour detection."""
        night_hours = [22, 23, 0, 1, 2, 3, 4, 5, 6]
        day_hours = [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
        
        for hour in night_hours:
            with self.subTest(hour=hour):
                self.assertTrue(is_night_hour(hour))
                
        for hour in day_hours:
            with self.subTest(hour=hour):
                self.assertFalse(is_night_hour(hour))
        
    def test_is_day_hour(self):
        """Test day hour detection."""
        day_hours = [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
        night_hours = [22, 23, 0, 1, 2, 3, 4, 5, 6]
        
        for hour in day_hours:
            with self.subTest(hour=hour):
                self.assertTrue(is_day_hour(hour))
                
        for hour in night_hours:
            with self.subTest(hour=hour):
                self.assertFalse(is_day_hour(hour))
        
    def test_get_day_bit(self):
        """Test day bit calculation."""
        test_cases = [
            (datetime(2025, 2, 10, tzinfo=STOCKHOLM_TZ), 2),  # Monday
            (datetime(2025, 2, 11, tzinfo=STOCKHOLM_TZ), 4),  # Tuesday
            (datetime(2025, 2, 12, tzinfo=STOCKHOLM_TZ), 8),  # Wednesday
            (datetime(2025, 2, 13, tzinfo=STOCKHOLM_TZ), 16), # Thursday
            (datetime(2025, 2, 14, tzinfo=STOCKHOLM_TZ), 32), # Friday
            (datetime(2025, 2, 15, tzinfo=STOCKHOLM_TZ), 64), # Saturday
            (datetime(2025, 2, 9, tzinfo=STOCKHOLM_TZ), 1),   # Sunday
        ]
        for test_date, expected_bit in test_cases:
            with self.subTest(date=test_date.strftime('%A')):
                self.assertEqual(get_day_bit(test_date), expected_bit)

class TestScheduleManager(unittest.TestCase):
    """Test schedule management functionality."""
    
    def setUp(self):
        self.schedule_manager = ScheduleManager("192.168.1.100")
        self.test_date = datetime(2025, 2, 9, tzinfo=STOCKHOLM_TZ)

    def test_consecutive_days(self):
        """Test scheduling with real consecutive days price data (January 24-25, 2025)"""
        print("\nTesting consecutive days schedule optimization:")

        # January 24th price data
        day1_prices = [
            {"SEK_per_kWh": 0.27729, "hour": 0}, {"SEK_per_kWh": 0.27258, "hour": 1},
            {"SEK_per_kWh": 0.27086, "hour": 2}, {"SEK_per_kWh": 0.27006, "hour": 3},
            {"SEK_per_kWh": 0.26868, "hour": 4}, {"SEK_per_kWh": 0.28348, "hour": 5},
            {"SEK_per_kWh": 0.32111, "hour": 6}, {"SEK_per_kWh": 0.65014, "hour": 7},
            {"SEK_per_kWh": 0.79079, "hour": 8}, {"SEK_per_kWh": 0.62651, "hour": 9},
            {"SEK_per_kWh": 0.54127, "hour": 10}, {"SEK_per_kWh": 0.48379, "hour": 11},
            {"SEK_per_kWh": 0.42195, "hour": 12}, {"SEK_per_kWh": 0.41071, "hour": 13},
            {"SEK_per_kWh": 0.35587, "hour": 14}, {"SEK_per_kWh": 0.32926, "hour": 15},
            {"SEK_per_kWh": 0.32604, "hour": 16}, {"SEK_per_kWh": 0.29690, "hour": 17},
            {"SEK_per_kWh": 0.27924, "hour": 18}, {"SEK_per_kWh": 0.25480, "hour": 19},
            {"SEK_per_kWh": 0.24413, "hour": 20}, {"SEK_per_kWh": 0.19847, "hour": 21},
            {"SEK_per_kWh": 0.08214, "hour": 22}, {"SEK_per_kWh": 0.04463, "hour": 23}
        ]

        # January 25th price data
        day2_prices = [
            {"SEK_per_kWh": 0.03232, "hour": 0}, {"SEK_per_kWh": 0.02555, "hour": 1},
            {"SEK_per_kWh": 0.01169, "hour": 2}, {"SEK_per_kWh": 0.00848, "hour": 3},
            {"SEK_per_kWh": 0.00779, "hour": 4}, {"SEK_per_kWh": 0.00470, "hour": 5},
            {"SEK_per_kWh": 0.01192, "hour": 6}, {"SEK_per_kWh": 0.02441, "hour": 7},
            {"SEK_per_kWh": 0.03266, "hour": 8}, {"SEK_per_kWh": 0.03759, "hour": 9},
            {"SEK_per_kWh": 0.11562, "hour": 10}, {"SEK_per_kWh": 0.19298, "hour": 11},
            {"SEK_per_kWh": 0.21383, "hour": 12}, {"SEK_per_kWh": 0.21028, "hour": 13},
            {"SEK_per_kWh": 0.20982, "hour": 14}, {"SEK_per_kWh": 0.21039, "hour": 15},
            {"SEK_per_kWh": 0.21864, "hour": 16}, {"SEK_per_kWh": 0.22907, "hour": 17},
            {"SEK_per_kWh": 0.21658, "hour": 18}, {"SEK_per_kWh": 0.20890, "hour": 19},
            {"SEK_per_kWh": 0.16192, "hour": 20}, {"SEK_per_kWh": 0.04011, "hour": 21},
            {"SEK_per_kWh": 0.01536, "hour": 22}, {"SEK_per_kWh": 0.00057, "hour": 23}
        ]

        # Day 1 - January 24th (Friday)
        day1_date = datetime(2025, 1, 24, tzinfo=STOCKHOLM_TZ)
        print(f"\nOptimizing schedule for {day1_date.date()} (Friday)")
        
        print("\nDay 1 Price Summary:")
        day1_df = pd.DataFrame(day1_prices)
        print("\nHighest prices:")
        print(day1_df.nlargest(5, 'SEK_per_kWh')[['hour', 'SEK_per_kWh']])
        print("\nLowest prices:")
        print(day1_df.nsmallest(5, 'SEK_per_kWh')[['hour', 'SEK_per_kWh']])
        
        # Get schedule for day 1
        charging_periods1, discharging_periods1 = self.schedule_manager.find_optimal_periods(
            day1_prices, day1_date)
        
        # Create and validate register data for day 1
        all_periods1 = sorted(charging_periods1 + discharging_periods1, 
                            key=lambda x: x['start_time'])
        register_data1 = [int(x) for x in self.schedule_manager.create_register_data(all_periods1)]
        
        print("\nDay 1 Optimized Schedule:")
        print_register_data(register_data1, "Friday Schedule")
        verify_register_data(register_data1)

        # Day 2 - January 25th (Saturday)
        day2_date = datetime(2025, 1, 25, tzinfo=STOCKHOLM_TZ)
        print(f"\nOptimizing schedule for {day2_date.date()} (Saturday)")
        
        print("\nDay 2 Price Summary:")
        day2_df = pd.DataFrame(day2_prices)
        print("\nHighest prices:")
        print(day2_df.nlargest(5, 'SEK_per_kWh')[['hour', 'SEK_per_kWh']])
        print("\nLowest prices:")
        print(day2_df.nsmallest(5, 'SEK_per_kWh')[['hour', 'SEK_per_kWh']])
        
        # Get schedule for day 2
        charging_periods2, discharging_periods2 = self.schedule_manager.find_optimal_periods(
            day2_prices, day2_date)
        
        # Create and validate register data for day 2
        all_periods2 = sorted(charging_periods2 + discharging_periods2, 
                            key=lambda x: x['start_time'])
        register_data2 = [int(x) for x in self.schedule_manager.create_register_data(all_periods2)]
        
        print("\nDay 2 Optimized Schedule:")
        print_register_data(register_data2, "Saturday Schedule")
        verify_register_data(register_data2)

        # Verify charging periods are during cheapest hours
        for day, periods, prices in [(1, charging_periods1, day1_prices), 
                                   (2, charging_periods2, day2_prices)]:
            charging_hours = set()
            for period in periods:
                start_hour = int(period['start_time'] // 60)
                end_hour = int(period['end_time'] // 60)
                if end_hour <= start_hour:
                    end_hour += 24
                charging_hours.update(h % 24 for h in range(start_hour, end_hour))
            
            # Get the 4 cheapest night hours
            night_prices = [(p['hour'], p['SEK_per_kWh']) for p in prices 
                          if is_night_hour(p['hour'])]
            cheapest_hours = sorted(night_prices, key=lambda x: x[1])[:4]
            cheapest_hours = {h[0] for h in cheapest_hours}
            
            print(f"\nDay {day} charging hours: {sorted(charging_hours)}")
            print(f"Day {day} cheapest night hours: {sorted(cheapest_hours)}")
            self.assertTrue(any(h in charging_hours for h in cheapest_hours))

        # Verify discharging periods are during most expensive hours
        for day, periods, prices in [(1, discharging_periods1, day1_prices), 
                                   (2, discharging_periods2, day2_prices)]:
            discharging_hours = set()
            for period in periods:
                start_hour = int(period['start_time'] // 60)
                end_hour = int(period['end_time'] // 60)
                if end_hour <= start_hour:
                    end_hour += 24
                discharging_hours.update(h % 24 for h in range(start_hour, end_hour))
            
            # Get the 4 most expensive day hours
            day_prices = [(p['hour'], p['SEK_per_kWh']) for p in prices 
                         if is_day_hour(p['hour'])]
            expensive_hours = sorted(day_prices, key=lambda x: -x[1])[:4]
            expensive_hours = {h[0] for h in expensive_hours}
            
            print(f"\nDay {day} discharging hours: {sorted(discharging_hours)}")
            print(f"Day {day} most expensive day hours: {sorted(expensive_hours)}")
            self.assertTrue(any(h in discharging_hours for h in expensive_hours))

    def test_create_period(self):
        """Test period creation with various scenarios."""
        test_cases = [
            # Regular period
            {
                'start': 1, 'end': 2, 'charging': True, 'day': 1,
                'expected': {'start_time': 60, 'end_time': 120, 'charge_flag': 0, 'days': 1}
            },
            # Midnight crossing period
            {
                'start': 23, 'end': 1, 'charging': True, 'day': 1,
                'expected': {'start_time': 1380, 'end_time': 1500, 'charge_flag': 0, 'days': 1}
            },
            # Full hour period
            {
                'start': 0, 'end': 1, 'charging': False, 'day': 2,
                'expected': {'start_time': 0, 'end_time': 60, 'charge_flag': 1, 'days': 2}
            }
        ]
        
        for case in test_cases:
            with self.subTest(case=case):
                period = self.schedule_manager.create_period(
                    case['start'], case['end'], case['charging'], case['day']
                )
                for key, value in case['expected'].items():
                    self.assertEqual(period[key], value)

    def test_check_overlap(self):
        """Test period overlap detection."""
        test_cases = [
            # Overlapping periods
            {
                'period1': {'start_time': 60, 'end_time': 120},
                'period2': {'start_time': 90, 'end_time': 150},
                'expected': True
            },
            # Non-overlapping periods
            {
                'period1': {'start_time': 60, 'end_time': 120},
                'period2': {'start_time': 180, 'end_time': 240},
                'expected': False
            },
            # Midnight crossing overlap
            {
                'period1': {'start_time': 1380, 'end_time': 60},
                'period2': {'start_time': 0, 'end_time': 120},
                'expected': True
            }
        ]
        
        for case in test_cases:
            with self.subTest(case=case):
                result = self.schedule_manager.check_overlap(
                    case['period1'], case['period2']
                )
                self.assertEqual(result, case['expected'])

    def test_find_optimal_periods(self):
        """Test optimal period selection with realistic price data."""
        prices_data = [
            {"hour": h, "SEK_per_kWh": 0.5 + (0.1 * h)} for h in range(24)
        ]
        
        charging_periods, discharging_periods = self.schedule_manager.find_optimal_periods(
            prices_data, self.test_date
        )
        
        # Verify number of periods
        self.assertEqual(len(charging_periods), 4)
        self.assertEqual(len(discharging_periods), 4)
        
        # Verify charging periods are during night hours
        for period in charging_periods:
            start_hour = period['start_time'] // 60
            self.assertTrue(is_night_hour(start_hour))
            
        # Verify discharging periods are during day hours
        for period in discharging_periods:
            start_hour = period['start_time'] // 60
            self.assertTrue(is_day_hour(start_hour))

    def test_clean_schedule(self):
        """Test schedule cleaning functionality."""
        test_schedule = {
            'periods': [
                {'days': 1, 'start_time': 60, 'end_time': 120},  # Sunday
                {'days': 2, 'start_time': 180, 'end_time': 240}, # Monday
                {'days': 4, 'start_time': 300, 'end_time': 360}  # Tuesday
            ]
        }
        
        # Test for Sunday (day_bit = 1)
        sunday_date = datetime(2025, 2, 9, tzinfo=STOCKHOLM_TZ)
        sunday_periods = self.schedule_manager.clean_schedule(test_schedule, sunday_date)
        self.assertEqual(len(sunday_periods), 1)
        self.assertEqual(sunday_periods[0]['days'], 1)
        
        # Test for Monday (day_bit = 2)
        monday_date = datetime(2025, 2, 10, tzinfo=STOCKHOLM_TZ)
        monday_periods = self.schedule_manager.clean_schedule(test_schedule, monday_date)
        self.assertEqual(len(monday_periods), 1)
        self.assertEqual(monday_periods[0]['days'], 2)

if __name__ == '__main__':
    unittest.main()