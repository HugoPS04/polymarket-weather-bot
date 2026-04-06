"""
Bot configuration using Pydantic settings.
Loads from .env file or environment variables.
"""
import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List, Optional
from functools import lru_cache


# Find .env file - look in project root and current directory
def find_env_file() -> str:
    """Find the .env file path."""
    # Check current directory first
    env_path = Path(".env")
    if env_path.exists():
        return str(env_path.absolute())
    # Check project root
    project_root = Path(__file__).parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        return str(env_path.absolute())
    return ".env"


class LocationConfig(BaseSettings):
    """Single weather location configuration."""
    name: str
    lat: float
    lon: float


class BotSettings(BaseSettings):
    """Main bot configuration."""
    
    class Config:
        env_file = find_env_file()
        env_file_encoding = "utf-8"
        case_sensitive = False
    
    # === Polymarket ===
    poly_private_key: str = Field(..., description="Polygon wallet private key")
    poly_signature_type: int = Field(default=2, description="0=EOA, 1=Email, 2=Browser/Safe")
    poly_funder_address: Optional[str] = Field(default=None, description="Funder address for proxy wallets")
    poly_host: str = Field(default="https://clob.polymarket.com", description="CLOB API host")
    poly_chain_id: int = Field(default=137, description="Polygon chain ID")
    
    # === Trading ===
    max_position_size: float = Field(default=100.0, description="Max position per market (USDC)")
    max_total_exposure: float = Field(default=500.0, description="Max total exposure (USDC)")
    min_edge_threshold: float = Field(default=0.05, description="Minimum edge to bet (e.g., 0.05 = 5%)")
    order_type: str = Field(default="GTC", description="GTC, FOK, or FAK")
    
    # === Weather ===
    weather_api_source: str = Field(default="noaa", description="noaa or openweathermap")
    openweather_api_key: Optional[str] = Field(default=None, description="OpenWeatherMap API key")
    weather_locations: str = Field(
        default="Miami:25.7617:-80.1918",
        description="Locations as name:lat:lon (comma-separated)"
    )
    
    # === Bot ===
    check_interval: int = Field(default=60, description="Check interval in seconds")
    live_trading: bool = Field(default=False, description="Enable live trading")
    
    # === Logging ===
    log_level: str = Field(default="INFO", description="Logging level")
    log_file: str = Field(default="logs/bot.log", description="Log file path")
    
    @property
    def parsed_locations(self) -> List[LocationConfig]:
        """Parse weather_locations string into LocationConfig list."""
        locations = []
        for loc in self.weather_locations.split(","):
            parts = loc.strip().split(":")
            if len(parts) == 3:
                locations.append(LocationConfig(
                    name=parts[0],
                    lat=float(parts[1]),
                    lon=float(parts[2])
                ))
        return locations
    
    @property
    def funder_address(self) -> str:
        """Return funder address (defaults to derived address from private key)."""
        return self.poly_funder_address or ""


@lru_cache()
def get_settings() -> BotSettings:
    """Get cached settings instance."""
    return BotSettings()
