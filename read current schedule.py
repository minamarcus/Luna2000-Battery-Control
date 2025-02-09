#!/usr/bin/env python3
import logging
import sys
from pymodbus.client import ModbusTcpClient
from datetime import datetime
import pytz

# Configure logging to console only for this simple script
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

def read_battery_schedule(host: str, port: int = 502):
    """Read and display the current schedule from Luna2000 battery."""
    try:
        # Connect to battery using the correct client initialization
        client = ModbusTcpClient(host)
        if not client.connect():
            logger.error("Failed to connect to battery")
            return False

        # Read register (47255 is the Time of Use register, 43 values)
        response = client.read_holding_registers(
            address=47255,
            count=43,
            slave=1
        )

        print(response)

        if response.isError():
            logger.error(f"Error reading register: {response}")
            return False

        # Get register data
        data = list(response.registers)
        
        # First value is number of periods
        num_periods = data[0]
        logger.info(f"\nNumber of configured periods: {num_periods}")

        if num_periods == 0:
            logger.info("No periods configured.")
            return True

        # Print each period
        weekdays = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
        logger.info("\nSchedule details:")
        logger.info("=" * 50)

        for i in range(num_periods):
            # Each period takes 4 values: start_time, end_time, charge_flag, days
            base_idx = 1 + (i * 4)
            if base_idx + 3 >= len(data):
                break

            start_time = data[base_idx]
            end_time = data[base_idx + 1]
            charge_flag = data[base_idx + 2]  # 0=Charge, 1=Discharge
            days_bits = data[base_idx + 3]

            # Convert minutes to hours:minutes format
            start_hour = start_time // 60
            start_minute = start_time % 60
            end_hour = end_time // 60
            end_minute = end_time % 60

            # Get days this period applies to
            active_days = [weekdays[i] for i in range(7) if days_bits & (1 << i)]
            days_str = ", ".join(active_days)

            # Print period details
            logger.info(f"\nPeriod {i + 1}:")
            logger.info(f"  Time: {start_hour:02d}:{start_minute:02d} - {end_hour:02d}:{end_minute:02d}")
            logger.info(f"  Mode: {'Charging' if charge_flag == 0 else 'Discharging'}")
            logger.info(f"  Active days: {days_str}")

        logger.info("\nRaw register data:")
        logger.info(f"  {data}")

    except Exception as e:
        logger.error(f"Error: {e}")
        return False
    
    finally:
        if 'client' in locals():
            client.close()

    return True

def main():
    # Replace with your battery's IP address
    BATTERY_HOST = "192.168.20.194"
    
    logger.info(f"Reading schedule from Luna2000 battery at {BATTERY_HOST}")
    success = read_battery_schedule(BATTERY_HOST)
    
    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()