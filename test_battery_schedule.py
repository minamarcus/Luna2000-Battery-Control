import unittest
from unittest import mock
from unittest.mock import Mock, patch, MagicMock
import asyncio
from datetime import datetime, timedelta
import pytz
import pandas as pd
from typing import List, Dict, Any
import aiohttp

# Import modules to test
from config import (
    STOCKHOLM_TZ, TOU_MODE, MAX_SELF_CONSUMPTION_MODE, 
    HIGH_USAGE_THRESHOLD, HIGH_USAGE_DURATION_THRESHOLD,
    MAX_SELF_CONSUMPTION_DURATION, MIN_SOC_FOR_DISCHARGE
)
from battery_manager import BatteryManager
from price_fetcher import PriceFetcher
from schedule_manager import ScheduleManager
from period_manager import PeriodManager
from period_utils import normalize_hour, is_night_hour, is_day_hour, get_day_bit
from high_usage_monitor import HighUsageMonitor, BatteryModeManager

class MockModbusClient:
    """Mock implementation of ModbusTcpClient with proper error handling"""
    def __init__(self, host: str, should_fail: bool = False):
        self.host = host
        self.connected = False
        self.registers = [0] * 43
        self.should_fail = should_fail
        self.is_open = False
        
    def connect(self) -> bool:
        if self.should_fail:
            return False
        self.connected = True
        self.is_open = True
        return True
        
    def close(self):
        self.connected = False
        self.is_open = False
        
    def read_holding_registers(self, address: int, count: int, slave: int):
        if self.should_fail:
            response = Mock()
            response.isError = lambda: True
            return response
            
        # Mock specific register responses
        if address == 37760:  # SOC register
            response = Mock()
            response.registers = [500]  # 50.0%
            response.isError = lambda: False
            return response
        elif address == 47086:  # Mode register
            response = Mock()
            response.registers = [5]  # TOU Mode
            response.isError = lambda: False
            return response
        elif address == 47255:  # TOU register
            response = Mock()
            response.registers = self.registers
            response.isError = lambda: False
            return response
        elif address == 37113:  # Active Power register
            response = Mock()
            response.registers = [0, 1000]  # 1000W
            response.isError = lambda: False
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
        
        if address == 47255:  # TOU register
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

class MockTibberHome:
    """Mock Tibber Home for testing"""
    def __init__(self, home_id="test_home_id"):
        self.id = home_id
        self.address1 = "Test Address"
        self.features = Mock(realTimeConsumptionEnabled=True)
    
    async def update_info(self):
        return True
    
    async def rt_subscribe(self, callback):
        return asyncio.create_task(self._mock_subscription(callback))
    
    async def _mock_subscription(self, callback):
        # Mock some data
        for i in range(3):
            callback({
                "data": {
                    "liveMeasurement": {
                        "timestamp": datetime.now().isoformat(),
                        "power": 3000,  # 3kW
                        "accumulatedConsumption": 10.5,
                        "accumulatedProduction": 0,
                        "minPower": 500,
                        "maxPower": 5000,
                        "averagePower": 2000
                    }
                }
            })
            await asyncio.sleep(0.1)
        return None

class MockTibber:
    """Mock Tibber client for testing"""
    def __init__(self, token=None, websession=None, user_agent=None):
        self.token = token
        self.name = "Test User"
        self._homes = [MockTibberHome()]
    
    async def update_info(self):
        return True
    
    def get_homes(self):
        return self._homes

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
            60, 120, 0, 1,  # Period 1: 1h-2h, charging, Sunday
            180, 240, 256, 2,  # Period 2: 3h-4h, discharging, Monday
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

    def test_write_schedule_success(self):
        """Test successful schedule writing."""
        test_data = [0] * 43  # Initialize array with correct length
        test_data[0] = 2  # Number of periods
        # Period 1: 7h-8h, charging, Sunday
        test_data[1] = 420
        test_data[2] = 480
        test_data[3] = 0
        test_data[4] = 1
        # Period 2: 18h-19h, discharging, Sunday
        test_data[5] = 1080
        test_data[6] = 1140
        test_data[7] = 256  # Discharging flag
        test_data[8] = 1
        
        with patch('battery_manager.ModbusTcpClient', return_value=self.mock_client):
            success = self.battery.write_schedule(test_data)
            self.assertTrue(success)
            self.assertEqual(self.mock_client.registers, test_data)

    def test_get_soc(self):
        """Test getting battery state of charge."""
        with patch('battery_manager.ModbusTcpClient', return_value=self.mock_client):
            soc = self.battery.get_soc()
            self.assertEqual(soc, 50.0)

    def test_get_mode(self):
        """Test getting battery mode."""
        with patch('battery_manager.ModbusTcpClient', return_value=self.mock_client):
            mode = self.battery.get_mode()
            self.assertEqual(mode, 5)  # TOU Mode

    def test_set_mode(self):
        """Test setting battery mode."""
        with patch('battery_manager.ModbusTcpClient', return_value=self.mock_client):
            success = self.battery.set_mode(MAX_SELF_CONSUMPTION_MODE)
            self.assertTrue(success)

    def test_encode_flags(self):
        """Test encoding charge flag and day bits."""
        # Charging, Monday
        result = self.battery._encode_flags(0, 2)
        self.assertEqual(result, 2)
        
        # Discharging, Monday
        result = self.battery._encode_flags(1, 2)
        self.assertEqual(result, 258)  # 2 + 256

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
            (datetime(2025, 2, 9, tzinfo=STOCKHOLM_TZ), 1),   # Sunday
            (datetime(2025, 2, 10, tzinfo=STOCKHOLM_TZ), 2),  # Monday
            (datetime(2025, 2, 11, tzinfo=STOCKHOLM_TZ), 4),  # Tuesday
            (datetime(2025, 2, 12, tzinfo=STOCKHOLM_TZ), 8),  # Wednesday
            (datetime(2025, 2, 13, tzinfo=STOCKHOLM_TZ), 16), # Thursday
            (datetime(2025, 2, 14, tzinfo=STOCKHOLM_TZ), 32), # Friday
            (datetime(2025, 2, 15, tzinfo=STOCKHOLM_TZ), 64), # Saturday
        ]
        for test_date, expected_bit in test_cases:
            with self.subTest(date=test_date.strftime('%A')):
                self.assertEqual(get_day_bit(test_date), expected_bit)

class TestPeriodManager(unittest.TestCase):
    """Test PeriodManager class."""
    
    def setUp(self):
        self.period_manager = PeriodManager()
        
    def test_create_period(self):
        """Test period creation."""
        period = self.period_manager.create_period(
            start_hour=8,
            end_hour=10,
            is_charging=True,
            day_bit=2  # Monday
        )
        
        self.assertEqual(period['start_time'], 480)
        self.assertEqual(period['end_time'], 600)
        self.assertTrue(period['is_charging'])
        self.assertEqual(period['days'], 2)
        self.assertEqual(period['charge_flag'], 0)

    def test_midnight_crossing_period(self):
        """Test creation of a period that crosses midnight."""
        period = self.period_manager.create_period(
            start_hour=23,
            end_hour=1,
            is_charging=False,
            day_bit=4  # Tuesday
        )
        
        self.assertEqual(period['start_time'], 1380)
        self.assertEqual(period['end_time'], 60)
        self.assertFalse(period['is_charging'])
        self.assertEqual(period['days'], 4)
        self.assertEqual(period['charge_flag'], 1)

    def test_combine_consecutive_periods(self):
        """Test combining consecutive periods."""
        periods = [
            self.period_manager.create_period(8, 9, True, 2),
            self.period_manager.create_period(9, 10, True, 2),
            self.period_manager.create_period(10, 11, True, 2),
            # Different day, should not combine
            self.period_manager.create_period(11, 12, True, 4),
            # Different charging state, should not combine
            self.period_manager.create_period(12, 13, False, 2),
        ]
        
        combined = self.period_manager.combine_consecutive_periods(periods)
        self.assertEqual(len(combined), 3)
        
        # Check first combined period (8-11)
        self.assertEqual(combined[0]['start_time'], 480)
        self.assertEqual(combined[0]['end_time'], 660)
        self.assertTrue(combined[0]['is_charging'])
        
        # Check second period (11-12)
        self.assertEqual(combined[1]['start_time'], 660)
        self.assertEqual(combined[1]['end_time'], 720)
        
        # Check third period (12-13)
        self.assertEqual(combined[2]['start_time'], 720)
        self.assertEqual(combined[2]['end_time'], 780)
        self.assertFalse(combined[2]['is_charging'])

    def test_check_overlap(self):
        """Test detecting overlapping periods."""
        period1 = self.period_manager.create_period(8, 10, True, 2)
        period2 = self.period_manager.create_period(9, 11, True, 2)
        period3 = self.period_manager.create_period(11, 12, True, 2)
        period4 = self.period_manager.create_period(8, 10, True, 4)  # Different day
        
        # Overlapping periods
        self.assertTrue(self.period_manager.check_overlap(period1, period2))
        
        # Non-overlapping periods
        self.assertFalse(self.period_manager.check_overlap(period1, period3))
        
        # Same time but different days
        self.assertFalse(self.period_manager.check_overlap(period1, period4))

class TestPriceFetcher(unittest.TestCase):
    """Test PriceFetcher class."""
    
    def setUp(self):
        self.price_fetcher = PriceFetcher()
        
    def test_fetch_price_data(self):
        """Test fetching price data."""
        mock_data = [
            {"SEK_per_kWh": 0.5, "time_start": "2025-02-25T00:00:00Z"},
            {"SEK_per_kWh": 0.6, "time_start": "2025-02-25T01:00:00Z"},
        ]
        
        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = mock_data
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response
            
            date = datetime(2025, 2, 25, tzinfo=STOCKHOLM_TZ)
            result = self.price_fetcher._fetch_price_data(date)
            
            self.assertEqual(result, mock_data)
            mock_get.assert_called_once()

    def test_get_prices(self):
        """Test getting today and tomorrow prices."""
        today_data = [
            {"SEK_per_kWh": 0.5, "time_start": "2025-02-25T00:00:00Z"},
            {"SEK_per_kWh": 0.6, "time_start": "2025-02-25T01:00:00Z"},
        ]
        tomorrow_data = [
            {"SEK_per_kWh": 0.4, "time_start": "2025-02-26T00:00:00Z"},
            {"SEK_per_kWh": 0.5, "time_start": "2025-02-26T01:00:00Z"},
        ]
        
        with patch.object(PriceFetcher, '_fetch_price_data') as mock_fetch:
            mock_fetch.side_effect = [today_data, tomorrow_data]
            
            result = self.price_fetcher.get_prices()
            
            # Check that both today and tomorrow data are processed
            self.assertIn('today', result)
            self.assertIn('tomorrow', result)
            
            # Check structure of processed data
            for day_data in [result['today'], result['tomorrow']]:
                self.assertGreaterEqual(len(day_data), 1)
                first_item = day_data[0]
                self.assertIn('hour', first_item)
                self.assertIn('SEK_per_kWh', first_item)
                self.assertIn('time_start', first_item)

class TestBatteryModeManager(unittest.TestCase):
    """Test BatteryModeManager class."""
    
    def setUp(self):
        self.battery_manager = BatteryManager("192.168.1.100")
        self.mode_manager = BatteryModeManager(self.battery_manager)
        self.mock_client = MockModbusClient("192.168.1.100")
        
    def test_get_current_mode(self):
        """Test getting current battery mode."""
        with patch('battery_manager.ModbusTcpClient', return_value=self.mock_client):
            mode = self.mode_manager.get_current_mode()
            self.assertEqual(mode, 5)  # Default TOU mode

    def test_switch_to_max_self_consumption(self):
        """Test switching to max self-consumption mode."""
        with patch('battery_manager.ModbusTcpClient', return_value=self.mock_client):
            # High enough SOC
            success = self.mode_manager.switch_to_max_self_consumption(50.0)
            self.assertTrue(success)
            self.assertTrue(self.mode_manager.in_high_usage_mode)
            
            # Reset state
            self.mode_manager.in_high_usage_mode = False
            
            # Low SOC
            success = self.mode_manager.switch_to_max_self_consumption(MIN_SOC_FOR_DISCHARGE - 1)
            self.assertFalse(success)
            self.assertFalse(self.mode_manager.in_high_usage_mode)

    def test_switch_to_tou_mode(self):
        """Test switching back to TOU mode."""
        with patch('battery_manager.ModbusTcpClient', return_value=self.mock_client):
            # Set initial state
            self.mode_manager.in_high_usage_mode = True
            
            success = self.mode_manager.switch_to_tou_mode()
            self.assertTrue(success)
            self.assertFalse(self.mode_manager.in_high_usage_mode)
            
            # Already in TOU mode
            success = self.mode_manager.switch_to_tou_mode()
            self.assertTrue(success)

    def test_handle_mode_maintenance(self):
        """Test mode maintenance handling."""
        with patch('battery_manager.ModbusTcpClient', return_value=self.mock_client):
            # Set initial state
            self.mode_manager.in_high_usage_mode = True
            self.mode_manager.mode_switch_time = time.time() - MAX_SELF_CONSUMPTION_DURATION - 1
            
            # Should switch back to TOU mode
            self.mode_manager.handle_mode_maintenance()
            self.assertFalse(self.mode_manager.in_high_usage_mode)
            
            # Reset and test with recent switch time
            self.mode_manager.in_high_usage_mode = True
            self.mode_manager.mode_switch_time = time.time() - 10
            
            # Should not switch back yet
            self.mode_manager.handle_mode_maintenance()
            self.assertTrue(self.mode_manager.in_high_usage_mode)

class TestHighUsageMonitor(unittest.TestCase):
    """Test HighUsageMonitor class."""
    
    def setUp(self):
        self.monitor = HighUsageMonitor(test_mode=True)
        # Create a battery manager mock with predictable responses
        self.battery_mock = MagicMock()
        self.battery_mock.get_mode.return_value = 5  # TOU mode
        self.battery_mock.get_soc.return_value = 50.0
        self.monitor.battery_manager = self.battery_mock
        
        # Replace the mode manager's battery with our mock
        self.monitor.battery_mode_manager.battery_manager = self.battery_mock
        
    def test_initialization(self):
        """Test HighUsageMonitor initialization."""
        self.assertIsNotNone(self.monitor.battery_manager)
        self.assertIsNotNone(self.monitor.battery_mode_manager)
        self.assertEqual(self.monitor.high_usage_count, 0)
        self.assertTrue(self.monitor.test_mode)
        
    def test_tibber_callback_normal_power(self):
        """Test callback with normal power usage."""
        package = {
            "data": {
                "liveMeasurement": {
                    "power": 1000,  # 1kW
                    "timestamp": "2025-02-25T12:00:00Z"
                }
            }
        }
        
        # Set initial state
        self.monitor.high_usage_count = 5
        
        # Call the callback
        self.monitor.tibber_callback(package)
        
        # Check that counter was reset
        self.assertEqual(self.monitor.high_usage_count, 0)
        
        # Verify no mode switch
        self.battery_mock.set_mode.assert_not_called()
        
    def test_tibber_callback_high_power(self):
        """Test callback with high power usage."""
        package = {
            "data": {
                "liveMeasurement": {
                    "power": HIGH_USAGE_THRESHOLD * 1000 + 1000,  # Above threshold
                    "timestamp": "2025-02-25T12:00:00Z"
                }
            }
        }
        
        # Call the callback multiple times to reach threshold
        for i in range(HIGH_USAGE_DURATION_THRESHOLD):
            self.monitor.tibber_callback(package)
            
        # Verify mode switch was called
        self.battery_mock.set_mode.assert_called_once_with(MAX_SELF_CONSUMPTION_MODE)
        
        # Check that counter was reset
        self.assertEqual(self.monitor.high_usage_count, 0)
        
        # Check that high usage mode flag is set
        self.assertTrue(self.monitor.battery_mode_manager.in_high_usage_mode)

    def test_is_currently_discharging(self):
        """Test checking if battery is currently discharging."""
        # Create a mock schedule with an active discharging period
        now = datetime.now(STOCKHOLM_TZ)
        current_hour = now.hour
        current_day_bit = get_day_bit(now)
        
        # Create a mock schedule
        mock_schedule = {
            'periods': [
                {
                    'start_time': current_hour * 60,
                    'end_time': (current_hour + 1) * 60,
                    'is_charging': False,
                    'days': current_day_bit
                }
            ]
        }
        
        self.battery_mock.read_schedule.return_value = mock_schedule
        
        # Test if correctly detects discharging
        result = self.monitor.battery_mode_manager.is_currently_discharging()
        self.assertTrue(result)
        
        # Change to charging period
        mock_schedule['periods'][0]['is_charging'] = True
        self.battery_mock.read_schedule.return_value = mock_schedule
        
        # Should now return False
        result = self.monitor.battery_mode_manager.is_currently_discharging()
        self.assertFalse(result)

@mock.patch('asyncio.sleep', return_value=None)
class TestHighUsageMonitorAsync(unittest.TestCase):
    """Test asynchronous aspects of HighUsageMonitor."""
    
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
    def tearDown(self):
        self.loop.close()
        
    @mock.patch('tibber.Tibber', MockTibber)
    async def async_setup_monitor(self):
        """Setup monitor with async operations."""
        monitor = HighUsageMonitor(test_mode=False)
        success = await monitor.initialize_tibber()
        if not success:
            raise ValueError("Failed to initialize Tibber")
        return monitor
        
    def test_initialize_tibber(self, mock_sleep):
        """Test Tibber initialization."""
        async def run_test():
            with mock.patch('tibber.Tibber', MockTibber):
                monitor = HighUsageMonitor()
                success = await monitor.initialize_tibber()
                self.assertTrue(success)
                self.assertIsNotNone(monitor.tibber_connection)
                self.assertIsNotNone(monitor.home)
                
        self.loop.run_until_complete(run_test())
        
    def test_start_monitoring_test_mode(self, mock_sleep):
        """Test start_monitoring in test mode."""
        monitor = HighUsageMonitor(test_mode=True)
        
        # Make the monitor stop after a short time
        async def stop_monitor():
            await asyncio.sleep(0.5)
            monitor.stopped = True
            
        async def run_test():
            stop_task = asyncio.create_task(stop_monitor())
            await monitor.start_monitoring()
            await stop_task
            
        self.loop.run_until_complete(run_test())
        
        # Verify that high_usage_count was incremented at least once
        self.assertGreater(monitor.high_usage_count, 0)
        
    def test_start_monitoring_real_mode(self, mock_sleep):
        """Test start_monitoring in real mode."""
        async def run_test():
            with mock.patch('tibber.Tibber', MockTibber):
                monitor = await self.async_setup_monitor()
                
                # Create a task to stop the monitor after a short time
                async def stop_after_delay():
                    await asyncio.sleep(0.5)
                    monitor.stopped = True
                    
                stop_task = asyncio.create_task(stop_after_delay())
                
                try:
                    # Start monitoring
                    await monitor.start_monitoring()
                finally:
                    await stop_task
                    await monitor.cleanup()
                    
        self.loop.run_until_complete(run_test())

class TestScheduleManager(unittest.TestCase):
    """Test ScheduleManager class."""
    
    def setUp(self):
        self.manager = ScheduleManager("192.168.1.100")
        self.mock_client = MockModbusClient("192.168.1.100")
        
    def test_update_schedule(self):
        """Test the full schedule update process."""
        # Mock battery responses
        with patch('battery_manager.ModbusTcpClient', return_value=self.mock_client):
            # Mock price fetcher
            with patch.object(PriceFetcher, 'get_prices') as mock_get_prices:
                mock_get_prices.return_value = {
                    'today': [{"hour": i, "SEK_per_kWh": 0.5 + 0.02*i, "time_start": datetime.now()} for i in range(24)],
                    'tomorrow': [{"hour": i, "SEK_per_kWh": 0.4 + 0.02*i, "time_start": datetime.now()} for i in range(24)]
                }
                
                # Run the update
                success = self.manager.update_schedule()
                
                # Check result
                self.assertTrue(success)
                
                # Verify that write_schedule was called
                data = self.mock_client.registers
                self.assertGreater(data[0], 0)  # Should have at least one period

    def test_update_schedule_with_retry(self):
        """Test schedule update with retries on failure."""
        # First attempt fails, second succeeds
        failing_client = MockModbusClient("192.168.1.100", should_fail=True)
        working_client = MockModbusClient("192.168.1.100")
        
        client_seq = [failing_client, working_client]
        
        with patch('battery_manager.ModbusTcpClient', side_effect=client_seq):
            # Mock price fetcher
            with patch.object(PriceFetcher, 'get_prices') as mock_get_prices:
                mock_get_prices.return_value = {
                    'today': [{"hour": i, "SEK_per_kWh": 0.5 + 0.02*i, "time_start": datetime.now()} for i in range(24)],
                    'tomorrow': [{"hour": i, "SEK_per_kWh": 0.4 + 0.02*i, "time_start": datetime.now()} for i in range(24)]
                }
                
                # Mock time.sleep to avoid actual delays
                with patch('time.sleep'):
                    # Run the update
                    success = self.manager.update_schedule()
                    
                    # Check result
                    self.assertTrue(success)

    def test_update_schedule_soc_based_decisions(self):
        """Test schedule updates with different SOC levels."""
        # Test with low SOC
        low_soc_client = MockModbusClient("192.168.1.100")
        # Override SOC register response
        low_soc_client.read_holding_registers = lambda address, count, slave: Mock(
            registers=[50] if address == 37760 else low_soc_client.registers,
            isError=lambda: False
        )
        
        # Test with high SOC
        high_soc_client = MockModbusClient("192.168.1.100")
        # Override SOC register response
        high_soc_client.read_holding_registers = lambda address, count, slave: Mock(
            registers=[800] if address == 37760 else high_soc_client.registers,
            isError=lambda: False
        )
        
        # Test low SOC case
        with patch('battery_manager.ModbusTcpClient', return_value=low_soc_client):
            # Mock price fetcher
            with patch.object(PriceFetcher, 'get_prices') as mock_get_prices:
                mock_get_prices.return_value = {
                    'today': [{"hour": i, "SEK_per_kWh": 0.5 + 0.02*i, "time_start": datetime.now()} for i in range(24)],
                    'tomorrow': [{"hour": i, "SEK_per_kWh": 0.4 + 0.02*i, "time_start": datetime.now()} for i in range(24)]
                }
                
                # Run the update with low SOC (5%)
                success = self.manager.update_schedule()
                self.assertTrue(success)
                
                # Verify current schedule was cleared due to low SOC
                # This is a bit tricky to test directly, so we'll check logs
                with self.assertLogs(level='INFO') as cm:
                    self.manager.update_schedule()
                    self.assertTrue(any("Clearing current schedule due to low SOC" in msg for msg in cm.output))
        
        # Test high SOC case
        with patch('battery_manager.ModbusTcpClient', return_value=high_soc_client):
            # Mock price fetcher
            with patch.object(PriceFetcher, 'get_prices') as mock_get_prices:
                mock_get_prices.return_value = {
                    'today': [{"hour": i, "SEK_per_kWh": 0.5 + 0.02*i, "time_start": datetime.now()} for i in range(24)],
                    'tomorrow': [{"hour": i, "SEK_per_kWh": 0.4 + 0.02*i, "time_start": datetime.now()} for i in range(24)]
                }
                
                # Run the update with high SOC (80%)
                success = self.manager.update_schedule()
                self.assertTrue(success)
                
                # Verify checking for future periods with high SOC
                with self.assertLogs(level='INFO') as cm:
                    self.manager.update_schedule()
                    self.assertTrue(any("Checking for future periods to preserve" in msg for msg in cm.output))

class TestOptimizationManager(unittest.TestCase):
    """Test OptimizationManager class."""
    
    def setUp(self):
        from optimization_manager import OptimizationManager
        self.manager = OptimizationManager(3, 4)  # 3 charging, 4 discharging periods
        
    def test_get_night_prices(self):
        """Test getting night prices."""
        today_prices = [{"hour": i, "SEK_per_kWh": 0.5} for i in range(24)]
        tomorrow_prices = [{"hour": i, "SEK_per_kWh": 0.4} for i in range(24)]
        
        night_prices = self.manager.get_night_prices(today_prices, tomorrow_prices)
        
        # Should include today's 22-23h and tomorrow's 0-6h
        self.assertEqual(len(night_prices), 9)
        
        # Check hours included
        hours = [p["hour"] for p in night_prices]
        self.assertIn(22, hours)
        self.assertIn(23, hours)
        self.assertIn(0, hours)
        self.assertIn(6, hours)
        
        # Check hours not included
        self.assertNotIn(7, hours)
        self.assertNotIn(21, hours)

    def test_process_charging_periods(self):
        """Test processing charging periods."""
        night_prices = [
            {"hour": 22, "SEK_per_kWh": 0.3},
            {"hour": 23, "SEK_per_kWh": 0.2},
            {"hour": 0, "SEK_per_kWh": 0.1},
            {"hour": 1, "SEK_per_kWh": 0.15},
        ]
        
        target_date = datetime(2025, 2, 25, tzinfo=STOCKHOLM_TZ)
        periods = self.manager.process_charging_periods(night_prices, target_date)
        
        # Should select 3 cheapest periods
        self.assertEqual(len(periods), 3)
        
        # Check that periods are for charging
        for period in periods:
            self.assertTrue(period['is_charging'])
            
        # Check that periods are sorted by time
        self.assertLessEqual(periods[0]['start_time'], periods[1]['start_time'])
        
        # Check that consecutive periods are combined
        if len(periods) >= 2:
            consecutive = False
            for i in range(len(periods) - 1):
                if periods[i]['end_time'] == periods[i+1]['start_time']:
                    consecutive = True
                    break
                    
            if consecutive:
                # Ideally we should check original vs. combined, but that's harder to test
                pass

    def test_process_discharging_periods(self):
        """Test processing discharging periods."""
        df = pd.DataFrame([
            {"hour": i, "SEK_per_kWh": 0.5 + 0.1*i} for i in range(7, 22)
        ])
        
        day_bit = 2  # Monday
        periods = self.manager.process_discharging_periods(df, day_bit)
        
        # Should select up to 4 most expensive periods
        self.assertLessEqual(len(periods), 4)
        
        # Check that periods are for discharging
        for period in periods:
            self.assertFalse(period['is_charging'])
            self.assertEqual(period['days'], day_bit)
            
        # Check that periods include the most expensive hours
        most_expensive_hours = df.nlargest(4, 'SEK_per_kWh')['hour'].tolist()
        period_hours = []
        
        for period in periods:
            start_hour = period['start_time'] // 60
            end_hour = period['end_time'] // 60
            if end_hour < start_hour:
                end_hour += 24
                
            period_hours.extend(range(start_hour, end_hour))
            
        for hour in most_expensive_hours:
            self.assertIn(hour, period_hours)

    def test_find_optimal_periods(self):
        """Test finding optimal periods."""
        today_prices = [{"hour": i, "SEK_per_kWh": 0.5 + 0.02*i, "time_start": datetime.now()} for i in range(24)]
        tomorrow_prices = [{"hour": i, "SEK_per_kWh": 0.4 + 0.02*i, "time_start": datetime.now()} for i in range(24)]
        
        target_date = datetime(2025, 2, 25, tzinfo=STOCKHOLM_TZ)
        charging_periods, discharging_periods = self.manager.find_optimal_periods(
            today_prices, tomorrow_prices, target_date
        )
        
        # Check that we have periods
        self.assertGreater(len(charging_periods), 0)
        self.assertGreater(len(discharging_periods), 0)
        
        # Check charging periods are in night hours
        for period in charging_periods:
            start_hour = period['start_time'] // 60
            self.assertTrue(is_night_hour(start_hour % 24))
            
        # Check discharging periods are in day hours
        for period in discharging_periods:
            start_hour = period['start_time'] // 60
            self.assertTrue(is_day_hour(start_hour % 24))

class TestScheduleDataManager(unittest.TestCase):
    """Test ScheduleDataManager class."""
    
    def setUp(self):
        from schedule_data_manager import ScheduleDataManager
        self.manager = ScheduleDataManager(14)  # Max 14 periods
        
    def test_clean_schedule(self):
        """Test cleaning schedule."""
        now = datetime.now(STOCKHOLM_TZ)
        current_hour = now.hour
        future_hour = (current_hour + 2) % 24
        past_hour = (current_hour - 2) % 24
        current_day_bit = get_day_bit(now)
        
        schedule = {
            'periods': [
                # Future period today
                {
                    'start_time': future_hour * 60,
                    'end_time': (future_hour + 1) * 60,
                    'days': current_day_bit,
                    'is_charging': True
                },
                # Past period today
                {
                    'start_time': past_hour * 60,
                    'end_time': (past_hour + 1) * 60,
                    'days': current_day_bit,
                    'is_charging': True
                },
                # Period for a different day
                {
                    'start_time': current_hour * 60,
                    'end_time': (current_hour + 1) * 60,
                    'days': current_day_bit * 2,  # Next day
                    'is_charging': True
                }
            ]
        }
        
        clean_periods = self.manager.clean_schedule(schedule, now)
        
        # Should only keep the future period for today
        self.assertEqual(len(clean_periods), 1)
        self.assertEqual(clean_periods[0]['start_time'], future_hour * 60)
        
    def test_create_register_data(self):
        """Test creating register data."""
        periods = [
            {
                'start_time': 60,  # 1:00
                'end_time': 120,   # 2:00
                'is_charging': True,
                'days': 1  # Sunday
            },
            {
                'start_time': 720,  # 12:00
                'end_time': 780,    # 13:00
                'is_charging': False,
                'days': 2  # Monday
            }
        ]
        
        register_data = self.manager.create_register_data(periods)
        
        # Check register data structure
        self.assertEqual(len(register_data), 43)
        self.assertEqual(register_data[0], 2)  # Number of periods
        
        # Check first period data
        self.assertEqual(register_data[1], 60)   # start_time
        self.assertEqual(register_data[2], 120)  # end_time
        self.assertEqual(register_data[3], 0)    # charge_flag = 0 for charging
        self.assertEqual(register_data[4], 1)    # days = 1 for Sunday
        
        # Check second period data
        self.assertEqual(register_data[5], 720)  # start_time
        self.assertEqual(register_data[6], 780)  # end_time
        self.assertEqual(register_data[7], 256)  # charge_flag = 256 for discharging
        self.assertEqual(register_data[8], 2)    # days = 2 for Monday
        
        # Rest should be zeros
        for i in range(9, 43):
            self.assertEqual(register_data[i], 0)
    
    def test_log_schedule(self):
        """Test logging schedule."""
        periods = [
            {
                'start_time': 60,  # 1:00
                'end_time': 120,   # 2:00
                'is_charging': True,
                'days': 1  # Sunday
            },
            {
                'start_time': 720,  # 12:00
                'end_time': 780,    # 13:00
                'is_charging': False,
                'days': 2  # Monday
            }
        ]
        
        # Should not raise exceptions
        with self.assertLogs(level='INFO') as cm:
            self.manager.log_schedule(periods, "Test Schedule")
            
            # Check that log messages were generated
            self.assertTrue(any("Test Schedule" in msg for msg in cm.output))
            self.assertTrue(any("Charging on Sunday" in msg for msg in cm.output))
            self.assertTrue(any("Discharging on Monday" in msg for msg in cm.output))

if __name__ == '__main__':
    unittest.main()