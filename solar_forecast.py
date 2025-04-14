import requests
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from config import logger, STOCKHOLM_TZ, SOLAR_FORECAST_THRESHOLD, SOLCAST_API_KEY, SOLCAST_RESOURCE_ID

class SolarForecast:
    """Handles fetching and processing solar forecast data from Solcast API."""
    
    def __init__(self):
        self.api_key = SOLCAST_API_KEY
        self.resource_id = SOLCAST_RESOURCE_ID
        self.base_url = "https://api.solcast.com.au"
    
    def get_forecast(self) -> Optional[Dict]:
        """
        Fetch the solar forecast for the next 7 days from Solcast.
        
        Returns:
            Dict: Forecast data or None if there was an error
        """
        try:
            logger.info("Fetching solar forecast from Solcast API")
            
            # Build request URL for forecasts endpoint
            url = f"{self.base_url}/rooftop_sites/{self.resource_id}/forecasts"
            
            # Set up parameters
            params = {
                "format": "json",
                "api_key": self.api_key
            }
            
            # Make the request
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            
            # Parse the response
            forecast_data = response.json()
            logger.info(f"Successfully fetched solar forecast. Got {len(forecast_data.get('forecasts', []))} forecast periods.")
            
            return forecast_data
        except requests.RequestException as e:
            logger.error(f"Error fetching solar forecast: {e}")
            return None
    
    def get_next_day_forecast(self) -> Tuple[float, List[Dict]]:
        """
        Process forecast data to get tomorrow's expected production.
        
        Returns:
            Tuple: (
                float: Total expected kWh for tomorrow,
                List[Dict]: Hourly forecast entries for tomorrow
            )
        """
        forecast_data = self.get_forecast()
        if not forecast_data or "forecasts" not in forecast_data:
            logger.warning("No forecast data available")
            return 0.0, []
        
        # Get tomorrow's date
        now = datetime.now(STOCKHOLM_TZ)
        tomorrow = (now + timedelta(days=1)).date()
        tomorrow_start = datetime.combine(tomorrow, datetime.min.time(), tzinfo=STOCKHOLM_TZ)
        tomorrow_end = tomorrow_start + timedelta(days=1)
        
        # Filter and aggregate tomorrow's forecast data
        tomorrow_forecast = []
        total_kwh = 0.0
        
        for entry in forecast_data["forecasts"]:
            # Convert period_end to datetime with timezone
            period_end = datetime.fromisoformat(entry["period_end"].replace("Z", "+00:00"))
            period_end = period_end.astimezone(STOCKHOLM_TZ)
            
            # For 30-minute forecasts, convert to hourly by taking the first entry per hour
            hour = period_end.hour
            
            # Check if period is for tomorrow
            if tomorrow_start <= period_end < tomorrow_end:
                # Calculate kWh for this period
                # Solcast provides pv_estimate in kW, and each period is typically 30 minutes (0.5 hours)
                # So kWh = kW * 0.5
                kwh_for_period = entry["pv_estimate"] * 0.5
                total_kwh += kwh_for_period
                
                tomorrow_forecast.append({
                    "hour": hour,
                    "period_end": period_end,
                    "pv_estimate": entry["pv_estimate"],
                    "kwh": kwh_for_period
                })
        
        logger.info(f"Forecast for {tomorrow}: Total expected production: {total_kwh:.2f} kWh")
        return total_kwh, tomorrow_forecast
    
    def should_skip_night_charging(self) -> bool:
        """
        Determine if night charging should be skipped based on solar forecast.
        
        Returns:
            bool: True if night charging should be skipped, False otherwise
        """
        total_kwh, _ = self.get_next_day_forecast()
        
        # Check if forecast exceeds the threshold
        should_skip = total_kwh >= SOLAR_FORECAST_THRESHOLD
        
        if should_skip:
            logger.info(f"Expected solar production ({total_kwh:.2f} kWh) exceeds threshold ({SOLAR_FORECAST_THRESHOLD} kWh). Skipping night charging.")
        else:
            logger.info(f"Expected solar production ({total_kwh:.2f} kWh) below threshold ({SOLAR_FORECAST_THRESHOLD} kWh). Night charging will proceed normally.")
        
        return should_skip