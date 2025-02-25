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

def parse_period_flags(flag_value: int) -> tuple:
    """
    Parse the combined charging/day flags.
    Returns (is_charging: bool, active_days: list[int])
    """
    # Bit 8 (256) determines charging (0) or discharging (1)
    is_charging = (flag_value & 256) == 0
    
    # Lower 7 bits (0-6) represent days (Sunday to Saturday)
    days_bits = flag_value & 0x7F  # Get only the lower 7 bits
    
    active_days = []
    for i in range(7):
        if days_bits & (1 << i):
            active_days.append(i)
            
    return is_charging, active_days

def read_active_power(host: str, port: int = 502):
    """
    Read active power from Luna2000 battery.
    
    Reads the active power value (INT32) from register 37113.
    Positive values indicate feed-in to the power grid.
    Negative values indicate supply from the power grid.
    """
    try:
        # Connect to battery
        client = ModbusTcpClient(host)
        if not client.connect():
            logger.error("Failed to connect to battery")
            return False

        # Read register (37113 is the Active Power register)
        # Quantity is 2 because it's a 32-bit value
        response = client.read_holding_registers(
            address=37113,
            count=2,
            slave=1
        )

        if response.isError():
            logger.error(f"Error reading active power register: {response}")
            return False

        # Get register data
        data = list(response.registers)
        
        # Convert two 16-bit registers to one 32-bit signed integer
        # First register is high word, second register is low word
        active_power = (data[0] << 16) | data[1]
        
        # Convert to signed value if necessary (two's complement)
        if active_power >= 2**31:
            active_power = active_power - 2**32
            
        # Apply gain (which is 1 in this case)
        # If gain were different, we'd multiply here
        
        # Display the result
        if active_power > 0:
            status = "feeding into the power grid"
        else:
            status = "drawing from the power grid"
            
        logger.info("\nActive Power:")
        logger.info(f"  Value: {active_power} W")
        logger.info(f"  Status: {status}")
        
        # Log raw data for debugging
        logger.info("\nRaw register data:")
        logger.info(f"  {data}")

    except Exception as e:
        logger.error(f"Error reading active power: {e}")
        return False
    
    finally:
        if 'client' in locals():
            client.close()

    return True

def read_battery_schedule(host: str, port: int = 502):
    """Read and display the current schedule from Luna2000 battery."""
    try:
        # Connect to battery
        client = ModbusTcpClient(host)
        if not client.connect():
            logger.error("Failed to connect to battery")
            return False

        # Read register (47255 is the Time of Use register)
        response = client.read_holding_registers(
            address=47255,
            count=43,
            slave=1
        )

        if response.isError():
            logger.error(f"Error reading register: {response}")
            return False

        # Get register data
        data = list(response.registers)
        
        # First value is number of periods (0-14)
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
            # Each period takes 3 values: start_time, end_time, flags
            base_idx = 1 + (i * 3)
            if base_idx + 2 >= len(data):
                break

            start_time = data[base_idx]     # Minutes since midnight
            end_time = data[base_idx + 1]   # Minutes since midnight
            period_flags = data[base_idx + 2]  # Combined charge/discharge and days flags

            # Validate time range
            if not (0 <= start_time <= 1440 and 0 <= end_time <= 1440):
                logger.warning(f"Invalid time range in period {i + 1}")
                continue

            # Parse the combined flags
            is_charging, active_day_indices = parse_period_flags(period_flags)

            # Convert minutes to hours:minutes format
            start_hour = start_time // 60
            start_minute = start_time % 60
            end_hour = end_time // 60
            end_minute = end_time % 60

            # Get days this period applies to
            active_days = [weekdays[idx] for idx in active_day_indices]
            days_str = ", ".join(active_days)

            # Print period details
            logger.info(f"\nPeriod {i + 1}:")
            logger.info(f"  Time: {start_hour:02d}:{start_minute:02d} - {end_hour:02d}:{end_minute:02d}")
            logger.info(f"  Mode: {'Charging' if is_charging else 'Discharging'}")
            logger.info(f"  Active days: {days_str}")
            logger.info(f"  Raw flags value: {period_flags}")

        logger.info("\nRaw register data:")
        logger.info(f"  {data}")

    except Exception as e:
        logger.error(f"Error: {e}")
        return False
    
    finally:
        if 'client' in locals():
            client.close()

    return True

def read_battery_soc(host: str, port: int = 502):
    """Read and display the soc from Luna2000 battery."""
    try:
        # Connect to battery
        client = ModbusTcpClient(host)
        if not client.connect():
            logger.error("Failed to connect to battery")
            return False

        # Read register (47255 is the Time of Use register)
        response = client.read_holding_registers(
            address=37760,
            count=1,
            slave=1
        )

        if response.isError():
            logger.error(f"Error reading register: {response}")
            return False

        # Get register data
        data = list(response.registers)

        logger.info("\nRaw register data:")
        logger.info(f"  {data}")

    except Exception as e:
        logger.error(f"Error: {e}")
        return False
    
    finally:
        if 'client' in locals():
            client.close()

    return True

def read_working_mode(host: str, port: int = 502):
    """Read and display working mode from Luna2000 battery."""
    try:
        # Connect to battery
        client = ModbusTcpClient(host)
        if not client.connect():
            logger.error("Failed to connect to battery")
            return False

        response = client.read_holding_registers(
            address=47086,
            count=1,
            slave=1
        )

        if response.isError():
            logger.error(f"Error reading register: {response}")
            return False

        mode = response.registers[0]
        # Get register data
        data = list(response.registers)

        logger.info("\nRaw register data:")
        logger.info(f"  {mode}")

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
    
    # logger.info(f"Reading schedule from Luna2000 battery at {BATTERY_HOST}")
    # success = read_battery_schedule(BATTERY_HOST)

    # if not success:
    #     sys.exit(1)

    # logger.info(f"Reading SOC from Luna2000 battery at {BATTERY_HOST}")
    # success = read_battery_soc(BATTERY_HOST)

    # if not success:
    #     sys.exit(1)
    
    # logger.info(f"Reading active power from Luna2000 battery at {BATTERY_HOST}")
    # success = read_active_power(BATTERY_HOST)

    # if not success:
    #     sys.exit(1)

    logger.info(f"Reading working mode from Luna2000 battery at {BATTERY_HOST}")
    success = read_working_mode(BATTERY_HOST)

    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()