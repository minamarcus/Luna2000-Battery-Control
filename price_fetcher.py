from datetime import datetime, timedelta
import requests
from typing import List, Dict, Optional
from config import logger, STOCKHOLM_TZ, API_BASE_URL

class PriceFetcher:
    def __init__(self):
        self.base_url = API_BASE_URL
        self.stockholm_tz = STOCKHOLM_TZ

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
        tomorrow = now + timedelta(days=0)
        
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