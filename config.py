import logging
import sys
import pytz
import os

from dotenv import load_dotenv
load_dotenv()

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

# Constants
BATTERY_HOST = os.getenv('BATTERY_HOST')
PORT = 502
TOU_REGISTER = 47255
MAX_PERIODS = 14
MAX_MINUTES = 1440
STOCKHOLM_TZ = pytz.timezone('Europe/Stockholm')
API_BASE_URL = "https://www.elprisetjustnu.se/api/v1/prices"

# Schedule configuration
MAX_CHARGING_PERIODS = 3    # Maximum number of charging periods to select
MAX_DISCHARGING_PERIODS = 4 # Maximum number of discharging periods to select
PRICE_THRESHOLD_FACTOR = 1.5 # Price must be this times higher to replace current periods

# Evening optimization configuration
EVENING_PRICE_THRESHOLD = 1.2  # Next day prices must be 20% less than current evening prices
BATTERY_DISCHARGE_RATE = 25.0  # Percentage of battery used per hour (configurable)
EVENING_START_HOUR = 18  # Start hour for evening optimization (18:00)
EVENING_END_HOUR = 22    # End hour for evening optimization (22:00)
NEXT_DAY_START_HOUR = 6  # Start hour for next day price comparison (6:00)
NEXT_DAY_END_HOUR = 22   # End hour for next day price comparison (22:00)

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 20  # seconds

# High usage monitor configuration
HIGH_USAGE_THRESHOLD = 8.0  # kW - switch to max self-consumption above this threshold
HIGH_USAGE_DURATION_THRESHOLD = 10  # seconds - must exceed threshold for this duration
MAX_SELF_CONSUMPTION_DURATION = 600  # seconds (10 minutes) - how long to stay in self-consumption mode
MONITORING_START_HOUR = 7  # Only monitor between these hours
MONITORING_END_HOUR = 22
MIN_SOC_FOR_DISCHARGE = 10  # Minimum battery % to allow discharge

# Battery mode registers
MODE_REGISTER = 47086
TOU_MODE = 5
MAX_SELF_CONSUMPTION_MODE = 2

TIBBER_TOKEN = os.getenv('TIBBER_TOKEN')