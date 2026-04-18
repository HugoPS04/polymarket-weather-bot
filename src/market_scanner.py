"""
Polymarket market scanner for weather-related markets.
Discovers active weather markets and extracts token IDs for trading.
"""
import logging
import requests
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

from config.settings import BotSettings, get_settings
from src.consensus_engine import LocationConfig

logger = logging.getLogger(__name__)


@dataclass
class Market:
    """Polymarket weather market data."""
    question: str
    slug: str
    market_address: str
    condition_id: str
    clob_token_ids: List[str]  # [YES_token, NO_token] or multi-outcome
    outcomes: List[str]
    outcome_prices: List[float]
    volume: float
    liquidity: float
    end_date: datetime
    category: str
    status: str  # open, closed, resolved
    neg_risk: bool


class MarketScanner:
    """Scan Polymarket for weather-related markets."""
    
    GAMMA_API = "https://gamma-api.polymarket.com"
    
    def __init__(self, settings: Optional[BotSettings] = None):
        self.settings = settings or get_settings()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PolymarketWeatherBot/1.0",
            "Accept": "application/json"
        })
        self.locations: List[LocationConfig] = getattr(self.settings, 'parsed_locations', [])
    
    def _location_in_question(self, question: str) -> bool:
        """Check if any configured location is mentioned in the market question."""
        if not self.locations:
            logger.debug(f"[DEBUG] No locations configured, accepting all markets")
            return True

        question_lower = question.lower()

        # Common city name aliases
        aliases = {
            "miami": ["miami", "miami fl"],
            "seattle": ["seattle", "seattle wa"],
            "new york": ["new york", "newyork", "nyc"],
            "los angeles": ["los angeles", "losangeles", "la"],
            "chicago": ["chicago"],
            "houston": ["houston"],
            "austin": ["austin", "austin tx"],
        }

        for loc in self.locations:
            loc_name = loc.name.lower()
            # Direct match (handles "Miami", "Seattle", etc.)
            if loc_name in question_lower:
                return True
            # Alias match
            if loc_name in aliases:
                for alias in aliases[loc_name]:
                    if alias in question_lower:
                        return True

        return False

    def get_weather_markets(self, limit: int = 50) -> List[Market]:
        """
        Fetch active weather-related markets matching configured locations.

        Returns:
            List of Market objects (location-filtered)
        """
        markets = []
        location_names = [loc.name for loc in self.locations] if self.locations else []
        logger.info(f"[DEBUG] MarketScanner locations: {location_names}")

        # Search weather category (API already filters by tag)
        weather_markets = self._fetch_markets(tag="weather", limit=limit)
        logger.info(f"[DEBUG] Weather tag API returned {len(weather_markets)} markets")

        # Also search for weather keywords in all markets
        all_markets = self._fetch_markets(limit=limit * 2)
        logger.info(f"[DEBUG] General API returned {len(all_markets)} markets")

        # Log sample questions to understand what's being checked
        logger.info(f"[DEBUG]   Locations to match: {[loc.name for loc in self.locations]}")
        for m in (weather_markets + all_markets)[:5]:
            loc_match = self._location_in_question(m.question)
            logger.info(f"[DEBUG]   Q={m.question[:100]}")
            logger.info(f"[DEBUG]   loc_match={loc_match}")

        # Add all weather tag markets that match location
        for m in weather_markets:
            if self._location_in_question(m.question):
                markets.append(m)

        # Add from general API: weather keywords AND location match
        weather_kw = ["weather", "rain", "snow", "temperature", "degree", "fahrenheit", "celsius",
                       "precipitation", "storm", "hurricane", "thunder", "heat", "cold", "freeze"]
        for market in all_markets:
            if market in markets:
                continue
            q_lower = market.question.lower()
            matched_kw = any(k in q_lower for k in weather_kw)
            if matched_kw and self._location_in_question(market.question):
                markets.append(market)

        logger.info(f"Found {len(markets)} weather-related markets (filtered by location)")
        return markets


    def _fetch_markets(self, tag: str = None, limit: int = 50) -> List[Market]:
        """Fetch markets from Gamma API."""
        params = {
            "closed": False,
            "limit": limit,
            "order": "volume",
            "ascending": False
        }
        
        if tag:
            params["tag"] = tag
        
        try:
            response = self.session.get(
                f"{self.GAMMA_API}/markets",
                params=params,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            markets = []
            for m in data:
                market = self._parse_market(m)
                if market:
                    markets.append(market)
            
            return markets
            
        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
            return []
    
    def _parse_market(self, data: Dict) -> Optional[Market]:
        """Parse raw market data into Market object."""
        try:
            # Parse outcome prices - handle both list and string formats
            outcome_prices_data = data.get("outcomePrices", [])
            outcome_prices = []
            
            if isinstance(outcome_prices_data, list):
                # Already a list
                outcome_prices = [float(p) if p else 0.0 for p in outcome_prices_data]
            elif isinstance(outcome_prices_data, str):
                # String format like "[0.5,0.5]" or "[\"0.5\",\"0.5\"]"
                outcome_prices_str = outcome_prices_data.strip("[]")
                if outcome_prices_str:
                    for p in outcome_prices_str.split(","):
                        p = p.strip().strip('"')  # Remove quotes if present
                        if p:
                            outcome_prices.append(float(p))
            
            # Parse end date
            end_date = None
            if data.get("endDate"):
                end_date = datetime.fromisoformat(data["endDate"].replace("Z", "+00:00"))
            
            # Parse clobTokenIds - can be string or list
            clob_token_ids = data.get("clobTokenIds", [])
            if isinstance(clob_token_ids, str):
                # Parse string like "[\"id1\",\"id2\"]"
                import json
                try:
                    clob_token_ids = json.loads(clob_token_ids)
                except:
                    clob_token_ids = []
            
            # Parse outcomes - can be string or list
            outcomes_data = data.get("outcomes", [])
            if isinstance(outcomes_data, list):
                outcomes = outcomes_data
            elif isinstance(outcomes_data, str):
                # Parse string like "[\"Yes\",\"No\"]"
                import json
                try:
                    outcomes = json.loads(outcomes_data)
                except:
                    outcomes = []
            else:
                outcomes = []
            
            return Market(
                question=data.get("question", "Unknown"),
                slug=data.get("slug", ""),
                market_address=data.get("address", ""),
                condition_id=data.get("conditionId", ""),
                clob_token_ids=clob_token_ids,
                outcomes=outcomes,
                outcome_prices=outcome_prices,
                volume=float(data.get("volume", 0)),
                liquidity=float(data.get("liquidity", 0)),
                end_date=end_date,
                category=data.get("category", ""),
                status="open",  # We filtered closed=False
                neg_risk=data.get("negRisk", False)
            )
        except Exception as e:
            logger.warning(f"Failed to parse market: {e}")
            return None
    
    def _is_weather_market(self, market: Market) -> bool:
        """Check if a market is weather-related."""
        weather_keywords = [
            "weather", "rain", "snow", "temperature", "degree", 
            "fahrenheit", "celsius", "precipitation", "storm",
            "hurricane", "thunder", "heat", "cold", "freeze"
        ]
        
        question_lower = market.question.lower()
        return any(keyword in question_lower for keyword in weather_keywords)
    
    def get_market_by_slug(self, slug: str) -> Optional[Market]:
        """Fetch a specific market by its slug."""
        try:
            response = self.session.get(
                f"{self.GAMMA_API}/markets",
                params={"slug": slug},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            if data and len(data) > 0:
                return self._parse_market(data[0])
            return None
            
        except Exception as e:
            logger.error(f"Error fetching market {slug}: {e}")
            return None
    
    def get_market_prices(self, market: Market) -> Dict[str, float]:
        """
        Get current prices for all outcomes in a market.
        
        Returns:
            Dict mapping outcome name to price
        """
        prices = {}
        for i, outcome in enumerate(market.outcomes):
            if i < len(market.outcome_prices):
                prices[outcome] = market.outcome_prices[i]
        return prices
    
    def find_arbitrage_opportunities(
        self, 
        markets: List[Market], 
        threshold: float = 0.02
    ) -> List[Dict]:
        """
        Find markets where YES + NO prices don't sum to ~1.0 (arb opportunities).
        
        Args:
            markets: List of markets to check
            threshold: Minimum deviation from 1.0 to flag
            
        Returns:
            List of arbitrage opportunities
        """
        opportunities = []
        
        for market in markets:
            if len(market.outcome_prices) >= 2:
                price_sum = sum(market.outcome_prices[:2])  # YES + NO
                deviation = abs(1.0 - price_sum)
                
                if deviation >= threshold:
                    opportunities.append({
                        "market": market,
                        "price_sum": price_sum,
                        "deviation": deviation,
                        "yes_price": market.outcome_prices[0],
                        "no_price": market.outcome_prices[1] if len(market.outcome_prices) > 1 else 0
                    })
        
        return opportunities
    
    def get_market_liquidity(self, market: Market) -> Dict[str, Any]:
        """Get detailed liquidity info for a market."""
        return {
            "market": market.question,
            "volume_24h": market.volume,
            "available_liquidity": market.liquidity,
            "yes_price": market.outcome_prices[0] if market.outcome_prices else None,
            "no_price": market.outcome_prices[1] if len(market.outcome_prices) > 1 else None,
            "spread": (
                market.outcome_prices[1] - market.outcome_prices[0] 
                if len(market.outcome_prices) >= 2 else None
            )
        }
