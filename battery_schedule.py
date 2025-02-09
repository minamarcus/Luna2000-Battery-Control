#!/usr/bin/env python3
import logging
import sys
from datetime import datetime, timedelta
import pytz
from typing import List, Dict, Optional
import pandas as pd
import requests
from pymodbus.client import ModbusTcpClient

# Configure logging to both file and console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('battery_schedule.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class BatteryManager:
    def __init__(self, host: str, port: int = 502):
        self.host = host
        self.port = port
        self.TOU_REGISTER = 47255
        self.MAX_PERIODS = 14
        self.MAX_MINUTES = 1440
        self.stockholm_tz = pytz.timezone('Europe/Stockholm')

    def connect(self) -> Optional[ModbusTcpClient]:
        """Establish connection to the battery."""
        try:
            client = ModbusTcpClient(self.host)
            if not client.connect():
                raise ConnectionError("Failed to connect to battery")
            logger.info(f"Successfully connected to battery at {self.host}:{self.port}")
            return client
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return None

    def read_schedule(self) -> Optional[Dict]:
        """Read and parse the battery schedule."""
        client = None
        try:
            client = self.connect()
            if not client:
                return None

            response = client.read_holding_registers(
                address=self.TOU_REGISTER,
                count=43,
                slave=1
            )

            if response.isError():
                logger.error(f"Error reading register: {response}")
                return None

            data = list(response.registers)
            return self._parse_schedule(data)

        except Exception as e:
            logger.error(f"Error reading schedule: {e}")
            return None
        finally:
            if client:
                client.close()

    def _parse_schedule(self, data: List[int]) -> Dict:
        """Parse raw register data into a structured format."""
        num_periods = data[0]
        periods = []

        for i in range(num_periods):
            base_idx = 1 + (i * 4)
            if base_idx + 3 >= len(data):
                break

            start_time = data[base_idx]
            end_time = data[base_idx + 1]
            charge_flag = data[base_idx + 2]
            days_bits = data[base_idx + 3]

            periods.append({
                'start_time': start_time,
                'end_time': end_time,
                'charge_flag': charge_flag,
                'days': days_bits,
                'is_charging': charge_flag == 0
            })

        return {
            'num_periods': num_periods,
            'periods': periods,
            'raw_data': data
        }

    def write_schedule(self, data: List[int]) -> bool:
        """Write schedule to battery."""
        client = None
        try:
            client = self.connect()
            if not client:
                return False

            if len(data) != 43:
                raise ValueError(f"Data must be exactly 43 values, got {len(data)}")

            response = client.write_registers(
                address=self.TOU_REGISTER,
                values=data,
                slave=1
            )

            if response.isError():
                raise Exception(f"Error writing to register: {response}")

            logger.info("Successfully wrote schedule to battery")
            return True

        except Exception as e:
            logger.error(f"Error writing schedule: {e}")
            return False
        finally:
            if client:
                client.close()

class PriceFetcher:
    def __init__(self):
        self.base_url = "https://www.elprisetjustnu.se/api/v1/prices"
        self.stockholm_tz = pytz.timezone('Europe/Stockholm')

    def _fetch_price_data(self, date: datetime) -> Optional[List[Dict]]:
        """Fetch price data for a specific date."""
        try:
            url = f"{self.base_url}/{date.year}/{date.month:02d}-{date.day:02d}_SE3.json"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Error fetching prices for {date.date()}: {e}")
            return None

    def get_prices(self) -> Dict[str, List[Dict]]:
        """Get electricity prices for today and tomorrow."""
        now = datetime.now(self.stockholm_tz)
        tomorrow = now + timedelta(days=1)
        
        tomorrow_data = self._fetch_price_data(tomorrow)
        if not tomorrow_data:
            return {'tomorrow': None}

        processed_data = []
        for item in tomorrow_data:
            time_start = datetime.fromisoformat(item['time_start'].replace('Z', '+00:00'))
            time_start = time_start.astimezone(self.stockholm_tz)
            
            processed_data.append({
                'hour': time_start.hour,
                'time_start': time_start,
                'SEK_per_kWh': item['SEK_per_kWh']
            })

        return {'tomorrow': sorted(processed_data, key=lambda x: x['hour'])}

class ScheduleManager:
    def __init__(self, battery_host: str):
        self.battery = BatteryManager(battery_host)
        self.price_fetcher = PriceFetcher()
        self.MAX_PERIODS = 14
        self.MAX_MINUTES = 1440
        self.stockholm_tz = pytz.timezone('Europe/Stockholm')

    def normalize_hour(self, hour: int) -> int:
        """Normalize hour to 0-23 range and handle midnight crossing"""
        return hour % 24

    def is_night_hour(self, hour: int) -> bool:
        """Check if hour is within night time (22:00-06:00)"""
        hour = self.normalize_hour(hour)
        return hour >= 22 or hour <= 6

    def is_day_hour(self, hour: int) -> bool:
        """Check if hour is within day time (07:00-21:00)"""
        hour = self.normalize_hour(hour)
        return 7 <= hour <= 21

    def get_day_bit(self, date: datetime) -> int:
        """Convert date to day bit (Sunday=0 convention)."""
        weekday = (date.weekday() + 1) % 7
        return 1 << weekday

    def collect_period_hours(self, period: Dict) -> set:
        """Collect hours from a period, handling midnight crossing."""
        start_hour = int(period['start_time'] // 60)
        end_hour = int(period['end_time'] // 60)
        
        if end_hour <= start_hour:  # Midnight crossing
            end_hour += 24
            
        # Create range and apply modulo 24 to each hour
        return {h % 24 for h in range(start_hour, end_hour)}

    def validate_time(self, minutes: int) -> int:
        """Validate time is within bounds."""
        if not isinstance(minutes, int):
            raise ValueError("Time must be an integer number of minutes")
            
        # Normalize to 24-hour period
        minutes = minutes % self.MAX_MINUTES
        
        if minutes < 0:
            raise ValueError(f"Time cannot be negative")
            
        return minutes

    def create_period(self, start_hour: int, end_hour: int, is_charging: bool, day_bit: int) -> Dict:
        """Create a valid period entry."""
        # Normalize hours to 0-23 range
        start_hour = start_hour % 24
        end_hour = end_hour % 24
        
        # Convert to minutes
        start_minutes = start_hour * 60
        
        # Handle midnight crossing properly
        if end_hour < start_hour:
            end_minutes = (end_hour + 24) * 60
        else:
            end_minutes = end_hour * 60
                
        # Validate
        if not (0 <= start_minutes < self.MAX_MINUTES and 0 < end_minutes <= self.MAX_MINUTES * 2):
            raise ValueError(f"Invalid time range: {start_hour}:00-{end_hour}:00")
                
        return {
            'start_time': start_minutes,
            'end_time': end_minutes,
            'charge_flag': 0 if is_charging else 1,  # 0=Charge, 1=Discharge
            'days': day_bit,
            'is_charging': is_charging
        }

    def check_overlap(self, period1: Dict, period2: Dict) -> bool:
        """Check if two periods overlap."""
        def normalize_times(period):
            start = period['start_time']
            end = period['end_time']
            # If period crosses midnight, add 24 hours to end time
            if end <= start:
                end += self.MAX_MINUTES
            return start, end
        
        # Get normalized times for both periods
        start1, end1 = normalize_times(period1)
        start2, end2 = normalize_times(period2)
        
        # Normalize start2 if it's before start1 (for midnight crossing comparison)
        if start2 < start1 and start2 < end2:
            start2 += self.MAX_MINUTES
            end2 += self.MAX_MINUTES
        
        # Check for overlap
        return not (end1 <= start2 or end2 <= start1)

    def find_optimal_periods(self, prices_data: List[Dict], target_date: datetime) -> tuple:
        """Find optimal charging and discharging periods, prioritizing contiguous blocks."""
        df = pd.DataFrame(prices_data)
        day_bit = self.get_day_bit(target_date)

        # Find charging periods (night time)
        night_mask = df['hour'].apply(self.is_night_hour)
        night_df = df[night_mask].copy()
        night_df = night_df.sort_values('SEK_per_kWh')
        
        # Find cheapest contiguous blocks for charging
        charging_periods = []
        used_hours = set()
        
        # Process each hour from cheapest to most expensive
        for _, row in night_df.iterrows():
            if len(charging_periods) >= 4:
                break
                
            hour = self.normalize_hour(row['hour'])
            if hour in used_hours:
                continue
                
            # Check for contiguous hours
            potential_block = [hour]
            for offset in [-1, 1]:
                check_hour = self.normalize_hour(hour + offset)
                if check_hour in night_df['hour'].values and check_hour not in used_hours:
                    if self.is_night_hour(check_hour):
                        potential_block.append(check_hour)
                    
            # Sort and select hours
            potential_block.sort()
            for block_hour in potential_block[:min(len(potential_block), 4 - len(charging_periods))]:
                if block_hour not in used_hours:
                    end_hour = (block_hour + 1) % 24
                    charging_periods.append(
                        self.create_period(
                            start_hour=block_hour,
                            end_hour=end_hour,
                            is_charging=True,
                            day_bit=day_bit
                        )
                    )
                    used_hours.add(block_hour)

        # Find discharging periods (day time)
        day_mask = df['hour'].apply(self.is_day_hour)
        day_df = df[day_mask].copy()
        day_df = day_df.sort_values('SEK_per_kWh', ascending=False)
        
        # Find most expensive contiguous blocks for discharging
        discharging_periods = []
        used_hours = set()
        
        # Process each hour from most expensive to least expensive
        for _, row in day_df.iterrows():
            if len(discharging_periods) >= 4:
                break
                
            hour = self.normalize_hour(row['hour'])
            if hour in used_hours:
                continue
                
            # Check for contiguous hours
            potential_block = [hour]
            for offset in [-1, 1]:
                check_hour = self.normalize_hour(hour + offset)
                if check_hour in day_df['hour'].values and check_hour not in used_hours:
                    if self.is_day_hour(check_hour):
                        potential_block.append(check_hour)
                    
            # Sort and select hours
            potential_block.sort()
            for block_hour in potential_block[:min(len(potential_block), 4 - len(discharging_periods))]:
                if block_hour not in used_hours:
                    end_hour = (block_hour + 1) % 24
                    discharging_periods.append(
                        self.create_period(
                            start_hour=block_hour,
                            end_hour=end_hour,
                            is_charging=False,
                            day_bit=day_bit
                        )
                    )
                    used_hours.add(block_hour)

        return charging_periods, discharging_periods

    def clean_schedule(self, schedule: Dict, current_date: datetime) -> List[Dict]:
        """Remove periods that aren't for the current day."""
        if not schedule or 'periods' not in schedule:
            return []
        current_day_bit = self.get_day_bit(current_date)
        return [p for p in schedule['periods'] if p['days'] & current_day_bit]

    def create_register_data(self, periods: List[Dict]) -> List[int]:
        """Create register data format from periods."""
        if len(periods) > self.MAX_PERIODS:
            raise ValueError(f"Maximum {self.MAX_PERIODS} periods allowed")
            
        data = [len(periods)]  # Number of periods
        
        for period in sorted(periods, key=lambda x: x['start_time']):
            data.extend([
                period['start_time'],
                period['end_time'],
                period['charge_flag'],
                period['days']
            ])
            
        # Pad with zeros to reach 43 values
        data.extend([0] * (43 - len(data)))
        return data

    def log_schedule(self, periods: List[Dict], title: str = "Schedule"):
        """Log schedule in human-readable format."""
        weekdays = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 
                   'Thursday', 'Friday', 'Saturday']
        logger.info(f"\n=== {title} ===")
        
        for i, period in enumerate(periods, 1):
            active_days = [weekdays[i] for i in range(7) if period['days'] & (1 << i)]
            days_str = ", ".join(active_days)
            start_hour = period['start_time'] // 60
            end_hour = period['end_time'] // 60
            mode = "Charging" if period['is_charging'] else "Discharging"
        if end_hour <= start_hour:  # Handle midnight crossing
                end_hour += 24
            
        logger.info(
            f"Period {i}: {mode} on {days_str} "
            f"at {start_hour%24:02d}:00-{end_hour%24:02d}:00"
        )

    def update_schedule(self) -> bool:
        """Main function to update the schedule."""
        try:
            # Read current schedule
            current_schedule = self.battery.read_schedule()
            if not current_schedule:
                logger.error("Failed to read current schedule")
                return False

            now = datetime.now(self.stockholm_tz)
            tomorrow = now + timedelta(days=1)
            
            logger.info(f"Updating schedule at {now}")
            
            # Clean existing schedule
            cleaned_periods = self.clean_schedule(current_schedule, now)
            self.log_schedule(cleaned_periods, "Current Schedule")
            
            # Get prices for tomorrow
            prices = self.price_fetcher.get_prices()
            if not prices.get('tomorrow'):
                logger.error("Failed to fetch tomorrow's prices")
                return False
            
            # Find optimal periods
            charging_periods, discharging_periods = self.find_optimal_periods(
                prices['tomorrow'], tomorrow)
            new_periods = charging_periods + discharging_periods
            self.log_schedule(new_periods, "New Periods for Tomorrow")
            
            # Merge and check for overlaps
            all_periods = sorted(cleaned_periods + new_periods, 
                               key=lambda x: x['start_time'])
            final_periods = []
            
            for period in all_periods:
                overlap = False
                for existing in final_periods:
                    if self.check_overlap(period, existing):
                        overlap = True
                        break
                if not overlap:
                    final_periods.append(period)
            
            # Create and write new register data
            new_register_data = self.create_register_data(final_periods)
            self.log_schedule(final_periods, "Final Schedule")
            
            # Write to battery
            success = self.battery.write_schedule(new_register_data)
            if success:
                logger.info("Successfully updated battery schedule")
            return success

        except Exception as e:
            logger.error(f"Unexpected error in schedule update: {e}")
            return False

def main():
    # Battery IP address - replace with actual battery IP
    BATTERY_HOST = "192.168.1.100"
    
    try:
        scheduler = ScheduleManager(BATTERY_HOST)
        success = scheduler.update_schedule()
        
        if success:
            logger.info("Schedule update completed successfully")
        else:
            logger.error("Schedule update failed")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()