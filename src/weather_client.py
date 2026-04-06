"""
Weather API client supporting multiple providers (NOAA, OpenWeatherMap).
Fetches forecasts and historical data for opportunity analysis.
"""
import logging
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass

from config.settings import BotSettings, get_settings, LocationConfig

logger = logging.getLogger(__name__)


@dataclass
class WeatherForecast:
    """Parsed weather forecast data."""
    location: str
    timestamp: datetime
    temperature: float  # Celsius
    temperature_f: float  # Fahrenheit
    condition: str
    precipitation_prob: float  # 0-100%
    precipitation_mm: float
    wind_speed: float  # km/h
    humidity: float  # %
    pressure: float  # hPa
    cloud_cover: float  # %
    visibility: float  # km


class WeatherClient:
    """Multi-provider weather API client."""
    
    # NOAA API endpoints
    NOAA_POINTS_API = "https://api.weather.gov/points/{lat},{lon}"
    NOAA_FORECAST_API = "https://api.weather.gov/gridpoints/{wfo},{x},{y}/forecast"
    
    # Open-Meteo API (free, no key required - recommended alternative to NOAA)
    OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
    
    def __init__(self, settings: Optional[BotSettings] = None):
        self.settings = settings or get_settings()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PolymarketWeatherBot/1.0 (hugo@example.com)",
            "Accept": "application/geo+json"
        })
        # Cache for grid points to avoid repeated lookups
        self._grid_cache: Dict[str, Dict] = {}
        
    def get_forecast(self, location: LocationConfig, days: int = 7) -> List[WeatherForecast]:
        """
        Get weather forecast for a location.
        
        Args:
            location: Location config with lat/lon
            days: Number of forecast days
            
        Returns:
            List of WeatherForecast objects
        """
        if self.settings.weather_api_source == "openweathermap":
            return self._get_openweathermap_forecast(location, days)
        elif self.settings.weather_api_source == "noaa":
            return self._get_noaa_forecast(location, days)
        else:
            # Default to Open-Meteo (global fallback)
            return self._get_openmeteo_forecast(location, days)
    
    def _get_noaa_grid_point(self, lat: float, lon: float) -> Optional[Dict]:
        """
        Get NOAA grid point info for coordinates.
        
        Args:
            lat: Latitude
            lon: Longitude
            
        Returns:
            Dict with wfo (office), gridX, gridY, and forecast URLs
        """
        cache_key = f"{lat},{lon}"
        if cache_key in self._grid_cache:
            return self._grid_cache[cache_key]
        
        url = self.NOAA_POINTS_API.format(lat=lat, lon=lon)
        
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            properties = data.get("properties", {})
            grid_info = {
                "wfo": properties.get("cwa"),
                "gridX": properties.get("gridX"),
                "gridY": properties.get("gridY"),
                "forecast": properties.get("forecast"),
                "forecastHourly": properties.get("forecastHourly"),
                "forecastGridData": properties.get("forecastGridData")
            }
            
            # Cache for 1 hour (grids rarely change)
            self._grid_cache[cache_key] = grid_info
            logger.debug(f"NOAA grid point for {lat},{lon}: {grid_info['wfo']}/{grid_info['gridX']},{grid_info['gridY']}")
            return grid_info
            
        except Exception as e:
            logger.error(f"NOAA grid point API error for {lat},{lon}: {e}")
            return None
    
    def _get_noaa_forecast(self, location: LocationConfig, days: int) -> List[WeatherForecast]:
        """
        Fetch forecast from NOAA NWS API (free, US only).
        
        Args:
            location: Location config
            days: Number of forecast days (NOAA provides 7 days)
            
        Returns:
            List of WeatherForecast objects
        """
        # Get grid point info
        grid_info = self._get_noaa_grid_point(location.lat, location.lon)
        if not grid_info:
            logger.warning(f"NOAA grid lookup failed for {location.name}, falling back to Open-Meteo")
            return self._get_openmeteo_forecast(location, days)
        
        # Use hourly forecast for detailed data
        url = grid_info.get("forecastHourly")
        if not url:
            logger.warning(f"NOAA hourly forecast URL not available for {location.name}")
            return self._get_openmeteo_forecast(location, days)
        
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            forecasts = []
            periods = data.get("properties", {}).get("periods", [])
            
            for period in periods:
                # Parse temperature
                temp_f = period.get("temperature", 0)
                temp_c = (temp_f - 32) * 5/9
                
                # Parse wind speed (NOAA gives mph, convert to km/h)
                wind_speed = period.get("windSpeed", "0 mph")
                if isinstance(wind_speed, str):
                    wind_speed = float(wind_speed.split()[0]) * 1.60934
                
                # Parse precipitation probability
                precip_prob = period.get("probabilityOfPrecipitation", {})
                if isinstance(precip_prob, dict):
                    precip_prob = precip_prob.get("value", 0) or 0
                else:
                    precip_prob = precip_prob or 0
                
                # Map NOAA short forecast to condition
                condition = period.get("shortForecast", "Unknown")
                
                # Estimate other fields from condition
                humidity = self._estimate_humidity_from_condition(condition)
                cloud_cover = self._estimate_cloud_cover_from_condition(condition)
                
                forecast = WeatherForecast(
                    location=location.name,
                    timestamp=datetime.fromisoformat(period.get("startTime", datetime.now().isoformat())),
                    temperature=temp_c,
                    temperature_f=temp_f,
                    condition=condition,
                    precipitation_prob=precip_prob,
                    precipitation_mm=0,  # NOAA hourly doesn't include precip amount
                    wind_speed=wind_speed,
                    humidity=humidity,
                    pressure=1013.25,  # Default sea level pressure
                    cloud_cover=cloud_cover,
                    visibility=10.0  # Default 10km visibility
                )
                forecasts.append(forecast)
            
            logger.debug(f"Fetched {len(forecasts)} hourly forecasts from NOAA for {location.name}")
            return forecasts
            
        except Exception as e:
            logger.error(f"NOAA forecast API error for {location.name}: {e}")
            return self._get_openmeteo_forecast(location, days)
    
    def _estimate_humidity_from_condition(self, condition: str) -> float:
        """Estimate humidity from NOAA condition description."""
        condition_lower = condition.lower()
        if "rain" in condition_lower or "storm" in condition_lower or "showers" in condition_lower:
            return 85.0
        elif "cloudy" in condition_lower or "overcast" in condition_lower:
            return 70.0
        elif "partly" in condition_lower:
            return 55.0
        elif "clear" in condition_lower or "sunny" in condition_lower:
            return 40.0
        else:
            return 60.0
    
    def _estimate_cloud_cover_from_condition(self, condition: str) -> float:
        """Estimate cloud cover from NOAA condition description."""
        condition_lower = condition.lower()
        if "clear" in condition_lower or "sunny" in condition_lower:
            return 10.0
        elif "partly" in condition_lower:
            return 45.0
        elif "mostly" in condition_lower and "cloud" in condition_lower:
            return 70.0
        elif "cloudy" in condition_lower or "overcast" in condition_lower:
            return 90.0
        else:
            return 50.0
    
    def _get_openmeteo_forecast(self, location: LocationConfig, days: int) -> List[WeatherForecast]:
        """Fetch forecast from Open-Meteo API (free, no key)."""
        
        params = {
            "latitude": location.lat,
            "longitude": location.lon,
            "hourly": [
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation_probability",
                "precipitation",
                "weather_code",
                "wind_speed_10m",
                "surface_pressure",
                "cloud_cover",
                "visibility"
            ].join(","),
            "forecast_days": days,
            "timezone": "auto"
        }
        
        try:
            response = self.session.get(self.OPEN_METEO_API, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            forecasts = []
            hourly = data.get("hourly", {})
            timestamps = hourly.get("time", [])
            
            for i, ts in enumerate(timestamps):
                forecast = WeatherForecast(
                    location=location.name,
                    timestamp=datetime.fromisoformat(ts.replace("Z", "+00:00")),
                    temperature=hourly.get("temperature_2m", [0] * len(timestamps))[i],
                    temperature_f=hourly.get("temperature_2m", [0] * len(timestamps))[i] * 9/5 + 32,
                    condition=self._openmeteo_code_to_condition(
                        hourly.get("weather_code", [0] * len(timestamps))[i]
                    ),
                    precipitation_prob=hourly.get("precipitation_probability", [0] * len(timestamps))[i],
                    precipitation_mm=hourly.get("precipitation", [0] * len(timestamps))[i],
                    wind_speed=hourly.get("wind_speed_10m", [0] * len(timestamps))[i],
                    humidity=hourly.get("relative_humidity_2m", [0] * len(timestamps))[i],
                    pressure=hourly.get("surface_pressure", [0] * len(timestamps))[i],
                    cloud_cover=hourly.get("cloud_cover", [0] * len(timestamps))[i],
                    visibility=hourly.get("visibility", [10] * len(timestamps))[i]
                )
                forecasts.append(forecast)
            
            logger.debug(f"Fetched {len(forecasts)} hourly forecasts for {location.name}")
            return forecasts
            
        except Exception as e:
            logger.error(f"Open-Meteo API error for {location.name}: {e}")
            return []
    
    def _get_openweathermap_forecast(self, location: LocationConfig, days: int) -> List[WeatherForecast]:
        """Fetch forecast from OpenWeatherMap API (requires API key)."""
        if not self.settings.openweather_api_key:
            logger.warning("OpenWeatherMap API key not configured, falling back to Open-Meteo")
            return self._get_openmeteo_forecast(location, days)
        
        url = "https://api.openweathermap.org/data/2.5/forecast"
        params = {
            "lat": location.lat,
            "lon": location.lon,
            "appid": self.settings.openweather_api_key,
            "units": "metric"
        }
        
        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            forecasts = []
            for item in data.get("list", []):
                forecast = WeatherForecast(
                    location=location.name,
                    timestamp=datetime.fromtimestamp(item.get("dt", 0)),
                    temperature=item.get("main", {}).get("temp", 0),
                    temperature_f=item.get("main", {}).get("temp", 0) * 9/5 + 32,
                    condition=item.get("weather", [{}])[0].get("description", "unknown"),
                    precipitation_prob=item.get("pop", 0) * 100,
                    precipitation_mm=item.get("rain", {}).get("3h", 0),
                    wind_speed=item.get("wind", {}).get("speed", 0) * 3.6,  # m/s to km/h
                    humidity=item.get("main", {}).get("humidity", 0),
                    pressure=item.get("main", {}).get("pressure", 0),
                    cloud_cover=item.get("clouds", {}).get("all", 0),
                    visibility=item.get("visibility", 10000) / 1000  # m to km
                )
                forecasts.append(forecast)
            
            logger.debug(f"Fetched {len(forecasts)} forecasts for {location.name} from OpenWeatherMap")
            return forecasts
            
        except Exception as e:
            logger.error(f"OpenWeatherMap API error for {location.name}: {e}")
            return []
    
    def _openmeteo_code_to_condition(self, code: int) -> str:
        """Convert WMO weather code to human-readable condition."""
        codes = {
            0: "Clear sky",
            1: "Mainly clear",
            2: "Partly cloudy",
            3: "Overcast",
            45: "Foggy",
            48: "Depositing rime fog",
            51: "Light drizzle",
            53: "Moderate drizzle",
            55: "Dense drizzle",
            61: "Slight rain",
            63: "Moderate rain",
            65: "Heavy rain",
            71: "Slight snow",
            73: "Moderate snow",
            75: "Heavy snow",
            77: "Snow grains",
            80: "Slight rain showers",
            81: "Moderate rain showers",
            82: "Violent rain showers",
            85: "Slight snow showers",
            86: "Heavy snow showers",
            95: "Thunderstorm",
            96: "Thunderstorm with slight hail",
            99: "Thunderstorm with heavy hail"
        }
        return codes.get(code, "Unknown")
    
    def get_current_conditions(self, location: LocationConfig) -> Optional[WeatherForecast]:
        """Get current weather conditions."""
        forecasts = self.get_forecast(location, days=1)
        if forecasts:
            return forecasts[0]
        return None
    
    def analyze_precipitation_chance(
        self, 
        location: LocationConfig, 
        date_range: tuple = None
    ) -> Dict[str, Any]:
        """
        Analyze precipitation probability for a date range.
        
        Args:
            location: Location to analyze
            date_range: (start_date, end_date) tuple, defaults to next 7 days
            
        Returns:
            Analysis dict with avg_prob, max_prob, rainy_hours, etc.
        """
        forecasts = self.get_forecast(location, days=7)
        
        if not forecasts:
            return {"error": "No forecast data"}
        
        # Filter by date range if provided
        if date_range:
            start, end = date_range
            forecasts = [
                f for f in forecasts 
                if start <= f.timestamp.date() <= end
            ]
        
        if not forecasts:
            return {"error": "No forecasts in date range"}
        
        precip_probs = [f.precipitation_prob for f in forecasts]
        rainy_hours = [f for f in forecasts if f.precipitation_prob > 50]
        
        return {
            "location": location.name,
            "hours_analyzed": len(forecasts),
            "avg_precipitation_prob": sum(precip_probs) / len(precip_probs),
            "max_precipitation_prob": max(precip_probs),
            "rainy_hours": len(rainy_hours),
            "rainy_percentage": len(rainy_hours) / len(forecasts) * 100,
            "total_precipitation_mm": sum(f.precipitation_mm for f in forecasts),
            "forecasts": forecasts
        }
    
    def analyze_temperature_range(
        self,
        location: LocationConfig,
        date_range: tuple = None
    ) -> Dict[str, Any]:
        """Analyze temperature forecasts."""
        forecasts = self.get_forecast(location, days=7)
        
        if not forecasts:
            return {"error": "No forecast data"}
        
        temps = [f.temperature for f in forecasts]
        temps_f = [f.temperature_f for f in forecasts]
        
        return {
            "location": location.name,
            "avg_temp_c": sum(temps) / len(temps),
            "min_temp_c": min(temps),
            "max_temp_c": max(temps),
            "avg_temp_f": sum(temps_f) / len(temps_f),
            "min_temp_f": min(temps_f),
            "max_temp_f": max(temps_f),
        }
