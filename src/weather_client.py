"""
Multi-API Weather Client with consensus support.
Fetches forecasts from multiple providers and returns unified data.
"""
import logging
import requests
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

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
    source: str = "unknown"  # API source


@dataclass
class ConsensusForecast:
    """Consensus forecast from multiple APIs."""
    location: str
    timestamp: datetime
    
    # Temperatures (Fahrenheit)
    avg_temp_f: float
    min_temp_f: float
    max_temp_f: float
    temp_sources: List[float]  # Individual API readings
    
    # Precipitation
    avg_precip_prob: float
    max_precip_prob: float
    precip_sources: List[float]
    
    # Precipitation amount
    total_precip_mm: float
    precip_amount_sources: List[float]
    
    # Agreement metrics
    temp_agreement: float  # 0-1, how close are the readings
    precip_agreement: float  # 0-1
    
    # Confidence score (0-1)
    confidence: float
    
    # Individual forecasts for reference
    forecasts: List[WeatherForecast]
    
    @property
    def temp_consensus(self) -> float:
        """Temperature consensus as percentage."""
        return sum(self.temp_sources) / len(self.temp_sources) if self.temp_sources else 0
    
    @property
    def precip_consensus(self) -> float:
        """Precipitation probability consensus."""
        return sum(self.precip_sources) / len(self.precip_sources) if self.precip_sources else 0


class WeatherClient:
    """Multi-provider weather API client with consensus support."""
    
    # NOAA API endpoints
    NOAA_API = "https://api.weather.gov/points/{lat},{lon}"
    NOAA_FORECAST = "https://api.weather.gov/gridpoints/{wfo}/{x},{y}/forecast"
    
    # Open-Meteo API (free, no key)
    OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
    
    # Visual Crossing (free tier, good for Google-adjacent data)
    VISUAL_CROSSING_API = "https://weather.visualcrossing.com/WeatherAPI/timeline"
    
    def __init__(self, settings: Optional[BotSettings] = None):
        self.settings = settings or get_settings()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PolymarketWeatherBot/1.0",
            "Accept": "application/json"
        })
    
    def get_consensus_forecast(
        self, 
        location: LocationConfig, 
        days: int = 7
    ) -> Dict[str, ConsensusForecast]:
        """
        Get consensus forecast from all available APIs.
        
        Returns:
            Dict mapping date -> ConsensusForecast
        """
        results = {}
        
        # Fetch from all APIs in parallel
        forecasts = self._fetch_all_apis(location, days)
        
        if not forecasts:
            return results
        
        # Group by date
        by_date = {}
        for f in forecasts:
            date_key = f.timestamp.date()
            if date_key not in by_date:
                by_date[date_key] = []
            by_date[date_key].append(f)
        
        # Build consensus for each date
        for date, day_forecasts in by_date.items():
            consensus = self._build_consensus(location.name, date, day_forecasts)
            results[str(date)] = consensus
        
        return results
    
    def _fetch_all_apis(
        self, 
        location: LocationConfig, 
        days: int
    ) -> List[WeatherForecast]:
        """Fetch forecasts from all available APIs."""
        all_forecasts = []
        
        # Open-Meteo (always available, free)
        openmeteo = self._get_openmeteo_forecast(location, days)
        all_forecasts.extend(openmeteo)
        
        # Visual Crossing (free tier)
        try:
            visual_crossing = self._get_visual_crossing_forecast(location, days)
            all_forecasts.extend(visual_crossing)
        except Exception as e:
            logger.warning(f"Visual Crossing failed: {e}")
        
        # NOAA (US only, try if lat/lon looks like US)
        if self._is_us_location(location):
            try:
                noaa = self._get_noaa_forecast(location, days)
                all_forecasts.extend(noaa)
            except Exception as e:
                logger.warning(f"NOAA failed: {e}")
        
        return all_forecasts
    
    def _build_consensus(
        self, 
        location_name: str,
        date: Any,
        forecasts: List[WeatherForecast]
    ) -> ConsensusForecast:
        """Build consensus forecast from multiple API results."""
        temps_f = [f.temperature_f for f in forecasts]
        precip_probs = [f.precipitation_prob for f in forecasts]
        precip_amounts = [f.precipitation_mm for f in forecasts]
        
        # Calculate agreements
        temp_agreement = self._calculate_agreement(temps_f)
        precip_agreement = self._calculate_agreement(precip_probs)
        
        # Count unique API sources (not individual readings)
        unique_apis = set(f.source for f in forecasts)
        api_count = len(unique_apis)
        
        # Use the higher agreement (temp or precip) for confidence
        # This prevents low precip agreement from hurting temp market confidence
        best_agreement = max(temp_agreement, precip_agreement)
        
        # With 2+ APIs agreeing: full weight, with 1 API: reduced confidence
        api_factor = min(1.0, api_count / 2)
        confidence = best_agreement * api_factor
        
        return ConsensusForecast(
            location=location_name,
            timestamp=datetime.combine(date, datetime.min.time()),
            avg_temp_f=sum(temps_f) / len(temps_f) if temps_f else 70,
            min_temp_f=min(temps_f) if temps_f else 60,
            max_temp_f=max(temps_f) if temps_f else 80,
            temp_sources=temps_f,
            avg_precip_prob=sum(precip_probs) / len(precip_probs) if precip_probs else 0,
            max_precip_prob=max(precip_probs) if precip_probs else 0,
            precip_sources=precip_probs,
            total_precip_mm=sum(precip_amounts) if precip_amounts else 0,
            precip_amount_sources=precip_amounts,
            temp_agreement=temp_agreement,
            precip_agreement=precip_agreement,
            confidence=confidence,
            forecasts=forecasts
        )
    
    def _calculate_agreement(self, values: List[float]) -> float:
        """Calculate how close values are. 1.0 = perfect agreement."""
        if not values:
            return 0.0
        if len(values) < 2:
            return 1.0
        
        avg = sum(values) / len(values)
        if avg == 0:
            return 1.0
        
        # Calculate coefficient of variation (lower = more agreement)
        variance = sum((v - avg) ** 2 for v in values) / len(values)
        std_dev = variance ** 0.5
        cv = std_dev / avg if avg != 0 else 0
        
        # Convert to agreement score (1 - CV, clamped to 0-1)
        agreement = max(0, 1 - cv)
        return min(1.0, agreement)
    
    def _is_us_location(self, location: LocationConfig) -> bool:
        """Check if location is in US (NOAA only covers US)."""
        lat, lon = location.lat, location.lon
        return (24 <= lat <= 50) and (-130 <= lon <= -65)
    
    def _get_openmeteo_forecast(
        self, 
        location: LocationConfig, 
        days: int
    ) -> List[WeatherForecast]:
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
            ],
            "forecast_days": days,
            "timezone": "auto"
        }
        
        try:
            response = self.session.get(
                self.OPEN_METEO_API, 
                params=params, 
                timeout=10
            )
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
                    visibility=hourly.get("visibility", [10] * len(timestamps))[i],
                    source="open-meteo"
                )
                forecasts.append(forecast)
            
            logger.debug(f"Open-Meteo: fetched {len(forecasts)} forecasts for {location.name}")
            return forecasts
            
        except Exception as e:
            logger.error(f"Open-Meteo error for {location.name}: {e}")
            return []
    
    def _get_visual_crossing_forecast(
        self, 
        location: LocationConfig, 
        days: int
    ) -> List[WeatherForecast]:
        """Fetch forecast from Visual Crossing (free tier, good data)."""
        # Visual Crossing free tier: 1000 days per month
        url = f"{self.VISUAL_CROSSING_API}/{location.lat},{location.lon}"
        params = {
            "unitGroup": "us",  # Fahrenheit
            "include": "hours",
            "key": "demo",  # Limited free key for testing
            "elements": "temp,humidity,precip,precipprob,windspeed,conditions,visibility",
            "days": days
        }
        
        try:
            response = self.session.get(url, params=params, timeout=10)
            # If demo key fails, try without (some endpoints are free)
            if response.status_code == 400:
                return []
            
            response.raise_for_status()
            data = response.json()
            
            forecasts = []
            for day in data.get("days", []):
                for hour_data in day.get("hours", []):
                    ts = hour_data.get("datetime")
                    if not ts:
                        continue
                    
                    # Parse timestamp
                    dt = datetime.strptime(f"{day.get('date')} {ts}", "%Y-%m-%d %H:%M:%S")
                    
                    forecast = WeatherForecast(
                        location=location.name,
                        timestamp=dt,
                        temperature=hour_data.get("temp", 70),
                        temperature_f=hour_data.get("temp", 70),  # Already in Fahrenheit
                        condition=hour_data.get("conditions", "Clear"),
                        precipitation_prob=hour_data.get("precipprob", 0),
                        precipitation_mm=hour_data.get("precip", 0) * 25.4,  # inches to mm
                        wind_speed=hour_data.get("windspeed", 0) * 1.609,  # mph to km/h
                        humidity=hour_data.get("humidity", 50),
                        pressure=1013,  # Not provided by VC
                        cloud_cover=100 - hour_data.get("visibility", 10) * 10,  # Rough estimate
                        visibility=hour_data.get("visibility", 10),
                        source="visual-crossing"
                    )
                    forecasts.append(forecast)
            
            logger.debug(f"Visual Crossing: fetched {len(forecasts)} forecasts for {location.name}")
            return forecasts
            
        except Exception as e:
            logger.warning(f"Visual Crossing error for {location.name}: {e}")
            return []
    
    def _get_noaa_forecast(
        self, 
        location: LocationConfig, 
        days: int
    ) -> List[WeatherForecast]:
        """Fetch forecast from NOAA (US only)."""
        # Get gridpoint from coordinates
        points_url = self.NOAA_API.format(lat=location.lat, lon=location.lon)
        
        try:
            # Get gridpoint
            points_resp = self.session.get(points_url, timeout=10)
            points_resp.raise_for_status()
            points_data = points_resp.json()
            
            # Get forecast URL
            gridpoint = points_data.get("properties", {}).get("forecastGridData", "")
            if not gridpoint:
                return []
            
            # Get hourly forecast
            forecast_resp = self.session.get(gridpoint, timeout=10)
            forecast_resp.raise_for_status()
            forecast_data = forecast_resp.json()
            
            forecasts = []
            periods = forecast_data.get("properties", {}).get("temperature", {}).get("values", [])
            
            for period in periods[:days * 24]:  # Limit to requested days
                ts = period.get("validTime", "")
                if not ts:
                    continue
                
                dt = datetime.fromisoformat(ts.split("/")[0])
                temp = period.get("value")
                if temp is None:
                    continue
                
                # NOAA returns Celsius
                forecast = WeatherForecast(
                    location=location.name,
                    timestamp=dt,
                    temperature=temp,
                    temperature_f=temp * 9/5 + 32,
                    condition="Forecast",
                    precipitation_prob=0,
                    precipitation_mm=0,
                    wind_speed=0,
                    humidity=50,
                    pressure=1013,
                    cloud_cover=50,
                    visibility=10,
                    source="noaa"
                )
                forecasts.append(forecast)
            
            logger.debug(f"NOAA: fetched {len(forecasts)} forecasts for {location.name}")
            return forecasts
            
        except Exception as e:
            logger.warning(f"NOAA error for {location.name}: {e}")
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
    
    def get_single_api_forecast(
        self, 
        location: LocationConfig, 
        api: str = "open-meteo"
    ) -> Optional[WeatherForecast]:
        """Get current conditions from a single API."""
        forecasts = []
        if api == "open-meteo":
            forecasts = self._get_openmeteo_forecast(location, days=1)
        elif api == "noaa" and self._is_us_location(location):
            forecasts = self._get_noaa_forecast(location, days=1)
        
        if forecasts:
            return forecasts[0]
        return None
