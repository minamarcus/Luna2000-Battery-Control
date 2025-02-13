import logging
import sys
import pytz

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
BATTERY_HOST = "192.168.20.194"
PORT = 502
TOU_REGISTER = 47255
MAX_PERIODS = 14
MAX_MINUTES = 1440
STOCKHOLM_TZ = pytz.timezone('Europe/Stockholm')
API_BASE_URL = "https://www.elprisetjustnu.se/api/v1/prices"

# Schedule configuration
MAX_CHARGING_PERIODS = 3    # Maximum number of charging periods to select
MAX_DISCHARGING_PERIODS = 3 # Maximum number of discharging periods to select