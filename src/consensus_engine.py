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
    - Only trade when 2+ APIs agree (consensus)
    - Minimum confidence threshold: 85%
    - Focus on high-probability scenarios (85-99%)
    - Calculate edge based on consensus vs market price
    """
    
    # Confidence thresholds
    MIN_CONFIDENCE = 0.60  # Minimum 60% confidence to consider (lowered)
    MIN_API_AGREEMENT = 2  # Minimum 2 APIs must agree
    
    # Probability thresholds for trading (lowered for more opportunities)
    HIGH_PROB_THRESHOLD = 0.70  # Trade on 70%+ predictions
    LOW_PROB_THRESHOLD = 0.30  # Trade on <30% predictions
    
    # Minimum edge to consider a trade
    MIN_EDGE = 0.05  # 5% minimum edge
    
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
        
        # Calculate if condition is met
        if comparison == ">":
            condition_met = avg_temp > target_temp or max_temp > target_temp
            model_prob = 0.85 if condition_met else 0.15
        elif comparison == ">=":
            condition_met = avg_temp >= target_temp or max_temp >= target_temp
            model_prob = 0.85 if condition_met else 0.15
        elif comparison == "<":
            condition_met = avg_temp < target_temp or min_temp < target_temp
            model_prob = 0.85 if condition_met else 0.15
        elif comparison == "<=":
            condition_met = avg_temp <= target_temp or min_temp <= target_temp
            model_prob = 0.85 if condition_met else 0.15
        elif comparison == "range":
            low, high = target_temp
            in_range = low <= avg_temp <= high or low <= max_temp <= high
            model_prob = 0.85 if in_range else 0.15
            target_temp = (low + high) / 2
        else:
            return None
        
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
        
        if abs(edge) < self.MIN_EDGE:  # Need at least MIN_EDGE% edge
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
            f"APIs agree: {temp_agreement:.1%}. Target: {comparison} {target_temp:.0f}°F"
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
        for loc in self.locations:
            if loc.name.lower() in question:
                return loc
        
        # Common aliases
        aliases = {
            "miami": "Miami",
            "new york": "NewYork",
            "nyc": "NewYork",
            "los angeles": "LA",
            "la": "LA",
            "chicago": "Chicago",
            "houston": "Houston"
        }
        
        for alias, loc_name in aliases.items():
            if alias in question:
                for loc in self.locations:
                    if loc.name == loc_name:
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
        
        # Check if Fahrenheit or Celsius
        is_fahrenheit = any(x in question_lower for x in ["fahrenheit", "°f", "f "])
        
        # Look for range like "between 82-83°F"
        range_match = re.search(r'between\s+(\d+)[-–](\d+)\s*°?[FfC]', question)
        if range_match:
            low = int(range_match.group(1))
            high = int(range_match.group(2))
            if is_fahrenheit:
                return ((low, high), "range", True)
            else:
                return ((low * 9/5 + 32, high * 9/5 + 32), "range", False)
        
        # Look for threshold like "> 82°F" or "82°F or higher"
        if ">=" in question or "or higher" in question or "at least" in question:
            match = re.search(r'(\d+)\s*°?[FfC]', question)
            if match:
                temp = int(match.group(1))
                if not is_fahrenheit:
                    temp = temp * 9/5 + 32
                return (temp, ">=", is_fahrenheit)
        
        if ">" in question or "above" in question or "over" in question:
            match = re.search(r'(\d+)\s*°?[FfC]', question)
            if match:
                temp = int(match.group(1))
                if not is_fahrenheit:
                    temp = temp * 9/5 + 32
                return (temp, ">", is_fahrenheit)
        
        if "<=" in question or "or lower" in question or "at most" in question:
            match = re.search(r'(\d+)\s*°?[FfC]', question)
            if match:
                temp = int(match.group(1))
                if not is_fahrenheit:
                    temp = temp * 9/5 + 32
                return (temp, "<=", is_fahrenheit)
        
        if "<" in question or "below" in question or "under" in question:
            match = re.search(r'(\d+)\s*°?[FfC]', question)
            if match:
                temp = int(match.group(1))
                if not is_fahrenheit:
                    temp = temp * 9/5 + 32
                return (temp, "<", is_fahrenheit)
        
        return None
    
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
