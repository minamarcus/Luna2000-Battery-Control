from datetime import datetime
from typing import Dict, Set
from config import MAX_MINUTES

def normalize_hour(hour: int) -> int:
    """Normalize hour to 0-23 range and handle midnight crossing"""
    return hour % 24

def is_night_hour(hour: int) -> bool:
    """Check if hour is within night time (22:00-06:00)"""
    hour = normalize_hour(hour)
    return hour >= 22 or hour <= 6

def is_day_hour(hour: int) -> bool:
    """Check if hour is within day time (07:00-21:00)"""
    hour = normalize_hour(hour)
    return 7 <= hour <= 21

def get_day_bit(date: datetime) -> int:
    """Convert date to day bit (Sunday=0 convention)."""
    weekday = (date.weekday() + 1) % 7
    return 1 << weekday

def collect_period_hours(period: Dict) -> Set[int]:
    """Collect hours from a period, handling midnight crossing."""
    start_hour = int(period['start_time'] // 60)
    end_hour = int(period['end_time'] // 60)
    
    if end_hour <= start_hour:  # Midnight crossing
        end_hour += 24
        
    return {h % 24 for h in range(start_hour, end_hour)}

def validate_time(minutes: int) -> int:
    """Validate time is within bounds."""
    if not isinstance(minutes, int):
        raise ValueError("Time must be an integer number of minutes")
        
    minutes = minutes % MAX_MINUTES
    
    if minutes < 0:
        raise ValueError(f"Time cannot be negative")
        
    return minutes