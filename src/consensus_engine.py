"""
Consensus Trading Engine for Weather Betting.
Analyzes multiple weather APIs to find high-confidence trading opportunities.
"""
import logging
import re
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

from config.settings import BotSettings, get_settings, LocationConfig
from src.weather_client import WeatherClient, WeatherForecast, ConsensusForecast

logger = logging.getLogger(__name__)


@dataclass
class TradingSignal:
    """High-confidence trading signal from consensus analysis."""
    market_question: str
    market_address: str
    outcome: str
    token_id: str
    
    # Probabilities
    model_probability: float  # Our model's calculated probability
    market_price: float  # Current market price
    edge: float  # model_probability - market_price
    
    # Confidence metrics
    confidence: float  # 0-1, based on API agreement
    api_count: int  # Number of APIs that agree
    api_sources: List[str]  # Which APIs provided data
    
    # Reasoning
    reasoning: str
    temperature_readings: List[float]  # In Fahrenheit
    precipitation_readings: List[float]  # In percent
    
    # Trading recommendation
    recommended_action: str  # "BUY", "SELL", "HOLD"
    bet_size_recommendation: float  # Suggested bet size as % of max
    risk_level: str  # "LOW", "MEDIUM", "HIGH"
    
    # Market data
    liquidity: float
    volume: float
    market_end_date: datetime
    
    def __str__(self):
        return (
            f"Signal: {self.outcome} on '{self.market_question[:40]}...'\n"
            f"  Model: {self.model_probability:.1%} | Market: {self.market_price:.1%} | Edge: {self.edge:.1%}\n"
            f"  Confidence: {self.confidence:.1%} ({self.api_count} APIs)\n"
            f"  Action: {self.recommended_action} ({self.risk_level} risk)\n"
            f"  Reasoning: {self.reasoning}"
        )


class ConsensusEngine:
    """
    Analyzes weather data from multiple APIs to generate high-confidence trading signals.
    
    Strategy:
    - Only trade when APIs agree (consensus)
    - Minimum confidence threshold
    - Time-based rules: No new bets after 10am local on event day
    - Calculate edge based on consensus vs market price
    """
    
    # Confidence thresholds
    MIN_CONFIDENCE = 0.60  # Minimum 60% confidence
    MIN_API_AGREEMENT = 2  # Minimum 2 APIs must agree
    
    # Probability thresholds
    HIGH_PROB_THRESHOLD = 0.70  # Trade on 70%+ predictions
    LOW_PROB_THRESHOLD = 0.30  # Trade on <30% predictions
    
    # Minimum edge
    MIN_EDGE = 0.05  # 5% minimum edge
    
    # Time-based strategy
    LOCAL_CUTOFF_HOUR = 10  # No new bets after 10am local time
    MAX_DAYS_AHEAD = 2  # Allow betting up to 2 days ahead
    
    # US timezone mapping (simplified)
    US_TIMEZONES = {
        "miami": "America/New_York",
        "newyork": "America/New_York",
        "losangeles": "America/Los_Angeles",
        "la": "America/Los_Angeles",
        "seattle": "America/Los_Angeles",
        "austin": "America/Chicago",
        "houston": "America/Chicago",
        "dallas": "America/Chicago",
        "denver": "America/Denver",
        "chicago": "America/Chicago",
        "atlanta": "America/New_York",
        "boston": "America/New_York",
        "phoenix": "America/Phoenix",
    }
    
    def __init__(self, settings: Optional[BotSettings] = None):
        self.settings = settings or get_settings()
        self.weather_client = WeatherClient(settings)
        self.locations = settings.parsed_locations if settings else []
    
    def analyze_market(
        self, 
        market_question: str,
        market_address: str,
        outcomes: List[str],
        outcome_prices: List[float],
        clob_token_ids: List[str],
        liquidity: float,
        volume: float,
        end_date: datetime,
        debug: bool = False
    ) -> Optional[TradingSignal]:
        """
        Analyze a market and generate a trading signal if confidence is high enough.
        
        Time-based strategy:
        - If market date is TODAY and local time > 10am: Skip new positions
        - If market date is tomorrow or later: OK to enter
        - Can close/exit positions anytime
        
        Returns:
            TradingSignal if high confidence, None otherwise
        """
        question_lower = market_question.lower()
        
        # Try to match market to a location
        location = self._match_location(question_lower)
        if not location:
            if debug:
                logger.debug(f"No location match for: {market_question[:50]}...")
            return None
        
        # Check time-based strategy
        target_date = self._extract_date(market_question)
        if target_date:
            time_check = self._check_time_strategy(location, target_date)
            if time_check["skip_reason"]:
                if debug:
                    logger.debug(f"Skipping {location.name}: {time_check['skip_reason']}")
                return None
        
        # Get consensus forecast
        consensus = self.weather_client.get_consensus_forecast(location, days=7)
        if not consensus:
            return None
        
        # Analyze based on market type
        if any(word in question_lower for word in ["rain", "precipitation", "shower", "drizzle"]):
            return self._analyze_precipitation_market(
                market_question, market_address, outcomes, outcome_prices,
                clob_token_ids, liquidity, volume, end_date, location, consensus
            )
        
        elif any(word in question_lower for word in ["temperature", "degree", "fahrenheit", "celsius", "hottest", "coldest"]):
            return self._analyze_temperature_market(
                market_question, market_address, outcomes, outcome_prices,
                clob_token_ids, liquidity, volume, end_date, location, consensus
            )
        
        return None
    
    def _check_time_strategy(
        self, 
        location: LocationConfig, 
        target_date
    ) -> Dict[str, Any]:
        """
        Check if we should enter new positions based on time.
        
        Strategy:
        - Event day = day 0
        - No new bets after 10am local on event day
        - Can bet on day+1 and day+2 (NOAA has great accuracy up to 48h)
        - Can always close/exit positions anytime
        
        Returns:
            Dict with 'can_enter', 'skip_reason', 'hours_remaining', 'days_ahead'
        """
        from datetime import date
        
        result = {
            "can_enter": True,
            "skip_reason": None,
            "hours_remaining": 24,
            "market_date": target_date,
            "is_today": False,
            "days_ahead": 0
        }
        
        # Get timezone for location
        tz = self._get_timezone(location.name)
        
        # Get current time in that timezone
        try:
            from zoneinfo import ZoneInfo
            now_utc = datetime.now(ZoneInfo("UTC"))
            now_local = now_utc.astimezone(ZoneInfo(tz))
        except:
            # Fallback if timezone not available
            now_local = datetime.now()
        
        # Convert target_date to date if needed
        if isinstance(target_date, datetime):
            target_date_only = target_date.date()
        else:
            target_date_only = target_date
        
        # Check if market is for today
        today = now_local.date()
        result["is_today"] = (target_date_only == today)
        
        # Calculate days ahead
        delta = (target_date_only - today).days
        result["days_ahead"] = delta
        
        # Check if too far in future
        if delta > self.MAX_DAYS_AHEAD:
            result["can_enter"] = False
            result["skip_reason"] = f"Market too far ahead ({delta} days > {self.MAX_DAYS_AHEAD})"
        elif result["is_today"]:
            # Event is TODAY - apply 10am cutoff
            current_hour = now_local.hour
            result["hours_remaining"] = max(0, 24 - current_hour)
            
            if current_hour >= self.LOCAL_CUTOFF_HOUR:
                result["can_enter"] = False
                result["skip_reason"] = (
                    f"Past {self.LOCAL_CUTOFF_HOUR}am local time "
                    f"({current_hour}:00), only {result['hours_remaining']}h remaining"
                )
        elif target_date_only < today:
            result["can_enter"] = False
            result["skip_reason"] = "Market date has passed"
        
        return result
    
    def _get_timezone(self, location_name: str) -> str:
        """Get timezone for a location."""
        name_lower = location_name.lower().replace(" ", "")
        return self.US_TIMEZONES.get(name_lower, "America/New_York")
    
    def _analyze_precipitation_market(
        self,
        market_question: str,
        market_address: str,
        outcomes: List[str],
        outcome_prices: List[float],
        clob_token_ids: List[str],
        liquidity: float,
        volume: float,
        end_date: datetime,
        location: LocationConfig,
        consensus: Dict[str, ConsensusForecast]
    ) -> Optional[TradingSignal]:
        """Analyze precipitation market."""
        
        # Parse target date from question
        target_date = self._extract_date(market_question)
        if not target_date:
            # Use tomorrow as default
            target_date = (datetime.now() + timedelta(days=1)).date()
        
        date_key = str(target_date)
        if date_key not in consensus:
            return None
        
        day_forecast = consensus[date_key]
        
        # Calculate consensus precipitation probability
        precip_prob = day_forecast.avg_precip_prob
        precip_sources = day_forecast.precip_sources
        confidence = day_forecast.precip_agreement * min(1.0, len(precip_sources) / 3)
        
        # Check if this is a "will it rain" market
        will_rain = "will" in market_question.lower() and "rain" in market_question.lower()
        
        # Determine outcome and probability
        if will_rain:
            # Market: "Will it rain in [location] on [date]?"
            model_prob = precip_prob / 100  # Convert to 0-1
            yes_idx = self._find_outcome_index(outcomes, "yes")
            if yes_idx is None:
                return None
            
            outcome = outcomes[yes_idx]
            market_price = outcome_prices[yes_idx] if yes_idx < len(outcome_prices) else 0.5
            token_id = clob_token_ids[yes_idx] if yes_idx < len(clob_token_ids) else None
            
            edge = model_prob - market_price
            api_sources = [f.source for f in day_forecast.forecasts]
            
            # Determine action
            if model_prob >= self.HIGH_PROB_THRESHOLD and confidence >= self.MIN_CONFIDENCE:
                action = "BUY" if edge > 0.05 else "HOLD"
                risk = "LOW" if confidence >= 0.95 else "MEDIUM"
            elif model_prob <= self.LOW_PROB_THRESHOLD and confidence >= self.MIN_CONFIDENCE:
                action = "SELL" if edge < -0.05 else "HOLD"
                risk = "LOW" if confidence >= 0.95 else "MEDIUM"
            else:
                return None
            
            return TradingSignal(
                market_question=market_question,
                market_address=market_address,
                outcome=outcome,
                token_id=token_id or "",
                model_probability=model_prob,
                market_price=market_price,
                edge=edge,
                confidence=confidence,
                api_count=len(precip_sources),
                api_sources=api_sources,
                reasoning=f"Consensus: {precip_prob:.1f}% precipitation probability (APIs agree: {day_forecast.precip_agreement:.1%})",
                temperature_readings=day_forecast.temp_sources,
                precipitation_readings=precip_sources,
                recommended_action=action,
                bet_size_recommendation=min(1.0, confidence),
                risk_level=risk,
                liquidity=liquidity,
                volume=volume,
                market_end_date=end_date
            )
        
        return None
    
    def _analyze_temperature_market(
        self,
        market_question: str,
        market_address: str,
        outcomes: List[str],
        outcome_prices: List[float],
        clob_token_ids: List[str],
        liquidity: float,
        volume: float,
        end_date: datetime,
        location: LocationConfig,
        consensus: Dict[str, ConsensusForecast]
    ) -> Optional[TradingSignal]:
        """Analyze temperature market."""
        
        # Parse target date
        target_date = self._extract_date(market_question)
        if not target_date:
            target_date = (datetime.now() + timedelta(days=1)).date()
        
        date_key = str(target_date)
        if date_key not in consensus:
            return None
        
        day_forecast = consensus[date_key]
        
        # Parse temperature target
        temp_info = self._parse_temperature_target(market_question)
        if not temp_info:
            return None
        
        target_temp, comparison, is_fahrenheit = temp_info
        
        # Calculate what our models predict
        avg_temp = day_forecast.avg_temp_f
        max_temp = day_forecast.max_temp_f
        min_temp = day_forecast.min_temp_f
        temp_sources = day_forecast.temp_sources
        
        # Temperature agreement
        temp_agreement = day_forecast.temp_agreement
        # Use the confidence from the forecast (already calculated correctly)
        confidence = day_forecast.confidence
        temp_agreement = day_forecast.temp_agreement
        
        # Calculate probability using proper model
        # Calculate days ahead for uncertainty adjustment
        today = datetime.now().date()
        if isinstance(target_date, datetime):
            target_date_only = target_date.date()
        else:
            target_date_only = target_date
        days_ahead = max(0, (target_date_only - today).days)
        
        # Calculate probability using proper model
        model_prob = self._calculate_temperature_probability(
            avg_temp=avg_temp,
            max_temp=max_temp,
            min_temp=min_temp,
            target_temp=target_temp,
            comparison=comparison,
            confidence=confidence,
            temp_agreement=temp_agreement,
            days_ahead=days_ahead
        )
        
        # Find YES outcome (condition being true)
        yes_idx = self._find_outcome_index(outcomes, "yes")
        if yes_idx is None:
            return None
        
        outcome = outcomes[yes_idx]
        market_price = outcome_prices[yes_idx] if yes_idx < len(outcome_prices) else 0.5
        token_id = clob_token_ids[yes_idx] if yes_idx < len(clob_token_ids) else None
        
        edge = model_prob - market_price
        api_sources = [f.source for f in day_forecast.forecasts]
        
        # Only trade if acceptable confidence and meaningful edge
        if confidence < self.MIN_CONFIDENCE:
            return None
        
        # For very low probability events, require larger edge
        min_edge_for_low_prob = self.MIN_EDGE
        if model_prob < 0.20:
            min_edge_for_low_prob = 0.10  # Require 10% edge for low prob events
        elif model_prob < 0.30:
            min_edge_for_low_prob = 0.08  # Require 8% edge
        
        # Increase edge requirement for further-ahead forecasts (more uncertainty)
        # Day 0: 1x, Day 1: 1.25x, Day 2: 1.5x
        days_ahead_factor = {0: 1.0, 1: 1.25, 2: 1.5}.get(days_ahead, 1.5)
        min_edge_for_low_prob *= days_ahead_factor
        
        if abs(edge) < min_edge_for_low_prob:
            return None
        
        # Determine action based on model probability and edge direction
        if edge > 0:
            action = "BUY"
            risk = "LOW" if confidence >= 0.90 else "MEDIUM"
        elif edge < 0:
            action = "SELL"  # Selling "yes" when we think it won't happen
            risk = "LOW" if confidence >= 0.90 else "MEDIUM"
        else:
            return None
        
        reasoning = (
            f"Forecast: avg {avg_temp:.0f}°F, max {max_temp:.0f}°F, min {min_temp:.0f}°F. "
            f"APIs agree: {temp_agreement:.1%}. Prob: {model_prob:.0%}. Target: {comparison} {target_temp if isinstance(target_temp, (int, float)) else target_temp[0] if isinstance(target_temp, tuple) else target_temp:.0f}°F"
        )
        
        return TradingSignal(
            market_question=market_question,
            market_address=market_address,
            outcome=outcome,
            token_id=token_id or "",
            model_probability=model_prob,
            market_price=market_price,
            edge=edge,
            confidence=confidence,
            api_count=len(temp_sources),
            api_sources=api_sources,
            reasoning=reasoning,
            temperature_readings=temp_sources,
            precipitation_readings=day_forecast.precip_sources,
            recommended_action=action,
            bet_size_recommendation=min(1.0, confidence),
            risk_level=risk,
            liquidity=liquidity,
            volume=volume,
            market_end_date=end_date
        )
    
    def _match_location(self, question: str) -> Optional[LocationConfig]:
        """Match market question to a monitored location."""
        question_lower = question.lower()
        
        # Normalize question (remove spaces, lower case)
        question_normalized = question_lower.replace(" ", "")
        
        for loc in self.locations:
            # Direct match (case insensitive)
            if loc.name.lower() in question_lower:
                return loc
            # Match without spaces (handles "LosAngeles" vs "Los Angeles")
            if loc.name.lower().replace(" ", "") in question_normalized:
                return loc
        
        # Common aliases (handle both with and without spaces)
        aliases = {
            "miami": ["miami", "miami"],
            "new york": ["newyork", "new york", "nyc"],
            "los angeles": ["losangeles", "los angeles", "la"],
            "chicago": ["chicago"],
            "houston": ["houston"],
            "seattle": ["seattle"],
            "austin": ["austin"]
        }
        
        for loc in self.locations:
            loc_lower = loc.name.lower().replace(" ", "")
            
            # Check if location name matches any alias
            for city_name, alias_list in aliases.items():
                if loc_lower in alias_list or city_name.replace(" ", "") in alias_list:
                    # Check if any form of this city is in the question
                    for alias in alias_list:
                        if alias in question_lower or alias.replace(" ", "") in question_normalized:
                            return loc
        
        return None
    
    def _extract_date(self, question: str) -> Optional[Any]:
        """Extract date from market question."""
        # Look for "April X" pattern
        match = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})', question)
        if match:
            month_str, day_str = match.groups()
            month_map = {
                "January": 1, "February": 2, "March": 3, "April": 4,
                "May": 5, "June": 6, "July": 7, "August": 8,
                "September": 9, "October": 10, "November": 11, "December": 12
            }
            month = month_map.get(month_str, 1)
            day = int(day_str)
            year = datetime.now().year
            try:
                from datetime import date
                return date(year, month, day)
            except:
                return None
        
        # Look for "tomorrow"
        if "tomorrow" in question.lower():
            return (datetime.now() + timedelta(days=1)).date()
        
        # Look for "today"
        if "today" in question.lower():
            return datetime.now().date()
        
        return None
    
    def _parse_temperature_target(self, question: str) -> Optional[Tuple]:
        """
        Parse temperature target from question.
        
        Returns:
            Tuple of (target_temp, comparison, is_fahrenheit)
            comparison: ">", ">=", "<", "<=", "range"
        """
        question_lower = question.lower()
        
        # Check if Fahrenheit or Celsius - be more inclusive
        is_fahrenheit = any(x in question_lower for x in ["fahrenheit", "°f", "°f", "fahreinheit"])
        
        # Look for range like "between 82-83°F"
        range_match = re.search(r'between\s+(\d+)[-–](\d+)\s*°?f', question, re.IGNORECASE)
        if range_match:
            low = int(range_match.group(1))
            high = int(range_match.group(2))
            # Check if it has °F or just F at the end
            has_f_marker = re.search(r'\d+[-–]\d+\s*°?[Ff]', question)
            is_range_fahrenheit = has_f_marker and ("°f" in question.lower() or question.lower().endswith("f"))
            
            if is_range_fahrenheit or "fahrenheit" in question_lower:
                return ((low, high), "range", True)
            else:
                # Celsius - convert to Fahrenheit for consistency
                return ((low * 9/5 + 32, high * 9/5 + 32), "range", False)
        
        # Look for threshold like "> 82°F" or "82°F or higher"
        if ">=" in question or "or higher" in question or "at least" in question:
            match = re.search(r'(\d+)\s*°?f', question, re.IGNORECASE)
            if match:
                temp = int(match.group(1))
                # Check if Fahrenheit
                is_f = "°f" in question.lower() or question.lower().endswith("f") or "fahrenheit" in question_lower
                if not is_f:
                    temp = temp * 9/5 + 32
                    is_fahrenheit = False
                else:
                    is_fahrenheit = True
                return (temp, ">=", is_fahrenheit)
        
        if ">" in question or "above" in question or "over" in question:
            match = re.search(r'(\d+)\s*°?f', question, re.IGNORECASE)
            if match:
                temp = int(match.group(1))
                is_f = "°f" in question.lower() or question.lower().endswith("f") or "fahrenheit" in question_lower
                if not is_f:
                    temp = temp * 9/5 + 32
                    is_fahrenheit = False
                else:
                    is_fahrenheit = True
                return (temp, ">", is_fahrenheit)
        
        if "<=" in question or "or lower" in question or "at most" in question:
            match = re.search(r'(\d+)\s*°?f', question, re.IGNORECASE)
            if match:
                temp = int(match.group(1))
                is_f = "°f" in question.lower() or question.lower().endswith("f") or "fahrenheit" in question_lower
                if not is_f:
                    temp = temp * 9/5 + 32
                    is_fahrenheit = False
                else:
                    is_fahrenheit = True
                return (temp, "<=", is_fahrenheit)
        
        if "<" in question or "below" in question or "under" in question:
            match = re.search(r'(\d+)\s*°?f', question, re.IGNORECASE)
            if match:
                temp = int(match.group(1))
                is_f = "°f" in question.lower() or question.lower().endswith("f") or "fahrenheit" in question_lower
                if not is_f:
                    temp = temp * 9/5 + 32
                    is_fahrenheit = False
                else:
                    is_fahrenheit = True
                return (temp, "<", is_fahrenheit)
        
        return None
    
    def _calculate_temperature_probability(
        self,
        avg_temp: float,
        max_temp: float,
        min_temp: float,
        target_temp: Any,
        comparison: str,
        confidence: float,
        temp_agreement: float,
        days_ahead: int = 0
    ) -> float:
        """
        Calculate temperature probability using proper model.
        
        This uses a more nuanced approach than binary 15%/85%:
        - Factors in where the forecast falls in the range
        - Accounts for forecast uncertainty based on API agreement
        - Accounts for NOAA's decreasing accuracy further ahead
        - Uses gradual probability curves, not binary outcomes
        
        Args:
            avg_temp: Average temperature across sources
            max_temp: Maximum temperature across sources
            min_temp: Minimum temperature across sources
            target_temp: Target temperature (int for thresholds, tuple for range)
            comparison: Type of comparison (">", ">=", "<", "<=", "range")
            confidence: Overall confidence in the forecast (0-1)
            temp_agreement: How much APIs agree (0-1)
            days_ahead: Days until event (affects uncertainty)
            
        Returns:
            Probability (0-1)
        """
        import math
        
        # Base uncertainty - weather forecasts typically have ±2-3°F error
        # This decreases as API agreement increases
        base_uncertainty = 2.0  # degrees Fahrenheit
        
        # NOAA accuracy degrades with distance:
        # Day 0: ±2°F, Day 1: ±3°F, Day 2: ±4-5°F
        noaa_uncertainty_by_day = {0: 2.0, 1: 3.0, 2: 4.5}
        noaa_uncertainty = noaa_uncertainty_by_day.get(days_ahead, 5.0)
        
        # Combine API agreement uncertainty with NOAA forecast uncertainty
        api_uncertainty = base_uncertainty * (1 - temp_agreement * 0.5)  # 1.0 to 2.0 degrees
        uncertainty = max(api_uncertainty, noaa_uncertainty)  # Use the larger
        
        if comparison == "range":
            low, high = target_temp
            range_center = (low + high) / 2
            range_half_width = (high - low) / 2
            
            # The forecast high is our best estimate of the daily high
            forecast_high = max_temp
            
            # Calculate where the forecast falls relative to the range
            # Distance from forecast to range center
            distance_from_center = abs(forecast_high - range_center)
            
            # If forecast is inside range, probability is high
            # If forecast is outside range, probability decreases with distance
            
            if low <= forecast_high <= high:
                # Inside range - high probability
                # But reduce if forecast is near boundary (less margin for error)
                distance_to_nearest_boundary = min(forecast_high - low, high - forecast_high)
                
                # Base probability: 0.75-0.90 depending on how centered the forecast is
                center_score = 1 - (distance_from_center / range_half_width) if range_half_width > 0 else 1
                base_prob = 0.75 + 0.15 * center_score
                
                # Reduce probability if near boundary (less margin for error)
                boundary_margin = distance_to_nearest_boundary / uncertainty
                boundary_factor = min(1.0, boundary_margin)
                
                model_prob = base_prob * (0.7 + 0.3 * boundary_factor)
                
            else:
                # Outside range - calculate probability based on distance
                # If forecast is just outside, there's still some chance
                if forecast_high < low:
                    distance_outside = low - forecast_high
                else:
                    distance_outside = forecast_high - high
                
                # Probability decreases as distance outside range increases
                # Using a gaussian-like falloff
                sigma = uncertainty  # Standard deviation based on forecast uncertainty
                probability_falloff = math.exp(-(distance_outside ** 2) / (2 * sigma ** 2))
                model_prob = 0.15 * probability_falloff
            
            return max(0.05, min(0.95, model_prob))
        
        elif comparison == ">=":
            # Will temperature be >= threshold?
            threshold = target_temp
            forecast_high = max_temp
            
            if forecast_high >= threshold:
                # Forecast is above threshold
                distance_above = forecast_high - threshold
                # Higher = more confident
                confidence_boost = min(0.15, distance_above / 10)
                model_prob = 0.80 + confidence_boost
            else:
                # Forecast is below threshold
                distance_below = threshold - forecast_high
                # Probability decreases with distance
                sigma = uncertainty
                probability_falloff = math.exp(-(distance_below ** 2) / (2 * sigma ** 2))
                model_prob = 0.20 * probability_falloff
            
            return max(0.05, min(0.95, model_prob))
        
        elif comparison == ">":
            # Will temperature be > threshold? (strictly greater)
            threshold = target_temp
            forecast_high = max_temp
            
            if forecast_high > threshold:
                distance_above = forecast_high - threshold
                confidence_boost = min(0.15, distance_above / 10)
                model_prob = 0.75 + confidence_boost
            else:
                distance_below = threshold - forecast_high + 0.1  # Small buffer for strict >
                sigma = uncertainty
                probability_falloff = math.exp(-(distance_below ** 2) / (2 * sigma ** 2))
                model_prob = 0.15 * probability_falloff
            
            return max(0.05, min(0.95, model_prob))
        
        elif comparison == "<=":
            # Will temperature be <= threshold?
            threshold = target_temp
            forecast_high = max_temp
            
            if forecast_high <= threshold:
                distance_below = threshold - forecast_high
                confidence_boost = min(0.15, distance_below / 10)
                model_prob = 0.80 + confidence_boost
            else:
                distance_above = forecast_high - threshold
                sigma = uncertainty
                probability_falloff = math.exp(-(distance_above ** 2) / (2 * sigma ** 2))
                model_prob = 0.20 * probability_falloff
            
            return max(0.05, min(0.95, model_prob))
        
        elif comparison == "<":
            # Will temperature be < threshold? (strictly less)
            threshold = target_temp
            forecast_high = max_temp
            
            if forecast_high < threshold:
                distance_below = threshold - forecast_high
                confidence_boost = min(0.15, distance_below / 10)
                model_prob = 0.75 + confidence_boost
            else:
                distance_above = forecast_high - threshold + 0.1
                sigma = uncertainty
                probability_falloff = math.exp(-(distance_above ** 2) / (2 * sigma ** 2))
                model_prob = 0.15 * probability_falloff
            
            return max(0.05, min(0.95, model_prob))
        
        else:
            return 0.50  # Default to 50% if unknown comparison
    
    def _find_outcome_index(self, outcomes: List[str], target: str) -> Optional[int]:
        """Find index of outcome (case-insensitive)."""
        target_lower = target.lower()
        for i, outcome in enumerate(outcomes):
            if outcome.lower() == target_lower:
                return i
            if target_lower in outcome.lower():
                return i
        return None
    
    def get_high_confidence_signals(
        self, 
        markets: List[Any],
        debug: bool = False
    ) -> List[TradingSignal]:
        """
        Analyze multiple markets and return only high-confidence signals.
        
        Args:
            markets: List of market objects from market scanner
            debug: If True, log all market analyses
            
        Returns:
            List of TradingSignals with confidence >= MIN_CONFIDENCE
        """
        signals = []
        analyzed = 0
        skipped_no_location = 0
        skipped_low_confidence = 0
        skipped_no_edge = 0
        
        for market in markets:
            # Skip low liquidity markets
            if market.liquidity < 100:
                continue
            
            signal = self.analyze_market(
                market_question=market.question,
                market_address=market.market_address,
                outcomes=market.outcomes,
                outcome_prices=market.outcome_prices,
                clob_token_ids=market.clob_token_ids,
                liquidity=market.liquidity,
                volume=market.volume,
                end_date=market.end_date,
                debug=debug
            )
            
            analyzed += 1
            
            if signal:
                if signal.recommended_action != "HOLD":
                    signals.append(signal)
                    if debug:
                        logger.info(f"✅ SIGNAL: {signal.outcome} on '{signal.market_question[:40]}...' "
                                  f"Model: {signal.model_probability:.0%} | Market: {signal.market_price:.0%} | "
                                  f"Edge: {signal.edge:.0%} | Confidence: {signal.confidence:.0%}")
                else:
                    skipped_no_edge += 1
                    if debug:
                        logger.info(f"⚠️  NO EDGE: {signal.market_question[:50]}... "
                                  f"Model: {signal.model_probability:.0%} | Market: {signal.market_price:.0%} | Edge: {signal.edge:.0%}")
            else:
                skipped_low_confidence += 1
                if debug:
                    logger.info(f"❌ LOW CONF: {market.question[:50]}... (low confidence or no location match)")
        
        if debug:
            logger.info(f"\n📊 Debug Summary:")
            logger.info(f"   Markets analyzed: {analyzed}")
            logger.info(f"   No location match: {skipped_no_location}")
            logger.info(f"   Low confidence: {skipped_low_confidence}")
            logger.info(f"   No edge: {skipped_no_edge}")
            logger.info(f"   Signals generated: {len(signals)}")
        
        # Sort by edge (highest first)
        signals.sort(key=lambda x: abs(x.edge), reverse=True)
        
        return signals
