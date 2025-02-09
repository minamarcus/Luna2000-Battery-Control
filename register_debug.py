# register_debug.py
from typing import List, Dict
from datetime import datetime
import pytz
from config import STOCKHOLM_TZ

def print_register_data(register_data: List[int], title: str = "Register Data") -> None:
    """
    Print register data in a human-readable format.
    
    Args:
        register_data: List of 43 integers representing the battery schedule
        title: Optional title for the output
    """
    print(f"\n{'='*50}")
    print(f"=== {title} ===")
    print(f"{'='*50}")
    
    num_periods = int(register_data[0])
    print(f"\nNumber of periods: {num_periods}")
    
    if num_periods > 0:
        print("\nPeriod details:")
        print("-" * 50)
        
        weekdays = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 
                   'Thursday', 'Friday', 'Saturday']
        
        for i in range(num_periods):
            base_idx = 1 + (i * 4)
            if base_idx + 3 >= len(register_data):
                break
                
            start_minutes = int(register_data[base_idx])
            end_minutes = int(register_data[base_idx + 1])
            charge_flag = int(register_data[base_idx + 2])
            days_bits = int(register_data[base_idx + 3])
            
            # Convert minutes to hours for display
            start_hour = int(start_minutes // 60)
            end_hour = int(end_minutes // 60)
            if end_hour <= start_hour:  # Handle midnight crossing
                end_hour += 24
                
            # Get active days
            active_days = [weekdays[j] for j in range(7) if days_bits & (1 << j)]
            days_str = ", ".join(active_days)
            
            # Determine if charging or discharging
            mode = "Charging" if charge_flag == 0 else "Discharging"
            
            print(f"Period {i+1}:")
            print(f"  Mode: {mode}")
            print(f"  Time: {start_hour%24:02d}:00-{end_hour%24:02d}:00")
            print(f"  Days: {days_str}")
            print(f"  Raw values: start={start_minutes}, end={end_minutes}, "
                  f"charge_flag={charge_flag}, days_bits={days_bits}")
            print()
    
    print("\nComplete register data:")
    print("-" * 50)
    print(f"[{', '.join(str(x) for x in register_data)}]")
    print("=" * 50)

def format_time_range(start_minutes: int, end_minutes: int) -> str:
    """Format a time range in a human-readable format."""
    start_hour = start_minutes // 60
    end_hour = end_minutes // 60
    
    if end_hour <= start_hour:  # Handle midnight crossing
        end_hour += 24
        
    return f"{start_hour%24:02d}:00-{end_hour%24:02d}:00"

def verify_register_data(register_data: List[int]) -> bool:
    """
    Verify that register data is valid.
    
    Args:
        register_data: List of integers representing the battery schedule
        
    Returns:
        bool: True if data is valid, False otherwise
        
    Prints detailed verification results
    """
    print("\nVerifying register data...")
    valid = True
    
    # Check length
    if len(register_data) != 43:
        print(f"❌ Invalid length: {len(register_data)} (should be 43)")
        return False
        
    num_periods = register_data[0]
    print(f"\nNumber of periods: {num_periods}")
    
    if num_periods < 0 or num_periods > 14:
        print(f"❌ Invalid number of periods: {num_periods} (should be 0-14)")
        valid = False
        
    # Check each period
    for i in range(num_periods):
        base_idx = 1 + (i * 4)
        if base_idx + 3 >= len(register_data):
            print(f"❌ Period {i+1} data extends beyond register length")
            valid = False
            continue
            
        start_minutes = register_data[base_idx]
        end_minutes = register_data[base_idx + 1]
        charge_flag = register_data[base_idx + 2]
        days_bits = register_data[base_idx + 3]
        
        print(f"\nPeriod {i+1}:")
        
        # Verify time values
        if not (0 <= start_minutes < 1440):
            print(f"❌ Invalid start time: {start_minutes} minutes")
            valid = False
        else:
            print(f"✓ Start time valid: {start_minutes} minutes "
                  f"({start_minutes//60:02d}:00)")
            
        if not (0 < end_minutes <= 1440):
            print(f"❌ Invalid end time: {end_minutes} minutes")
            valid = False
        else:
            print(f"✓ End time valid: {end_minutes} minutes "
                  f"({end_minutes//60:02d}:00)")
            
        # Verify charge flag
        if charge_flag not in [0, 1]:
            print(f"❌ Invalid charge flag: {charge_flag}")
            valid = False
        else:
            print(f"✓ Charge flag valid: {charge_flag} "
                  f"({'Charging' if charge_flag == 0 else 'Discharging'})")
            
        # Verify day bits
        if not (0 < days_bits < 128):
            print(f"❌ Invalid days bits: {days_bits}")
            valid = False
        else:
            weekdays = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 
                       'Thursday', 'Friday', 'Saturday']
            active_days = [weekdays[j] for j in range(7) if days_bits & (1 << j)]
            print(f"✓ Days bits valid: {days_bits} ({', '.join(active_days)})")
    
    # Check padding
    remaining_values = register_data[1 + num_periods*4:]
    if not all(x == 0 for x in remaining_values):
        print("\n❌ Non-zero values in padding area")
        valid = False
    else:
        print("\n✓ Padding area correctly zeroed")
    
    print(f"\nVerification {'passed' if valid else 'failed'} ✓" if valid else "❌")
    return valid

if __name__ == "__main__":
    # Example usage
    example_data = [
        2,  # Number of periods
        360, 420, 0, 2,  # Period 1: 6:00-7:00, charging, Monday
        1080, 1140, 1, 2,  # Period 2: 18:00-19:00, discharging, Monday
    ] + [0] * 35  # Padding to reach 43 values
    
    print_register_data(example_data, "Example Schedule")
    verify_register_data(example_data)