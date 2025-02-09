from datetime import datetime
import sys
from config import logger, BATTERY_HOST
from schedule_manager import ScheduleManager

def main():
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