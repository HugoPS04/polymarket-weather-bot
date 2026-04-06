"""
Main trading bot logic.
Analyzes weather data, finds market opportunities, and executes trades.
"""
import logging
import time
import json
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from pathlib import Path

from config.settings import BotSettings, get_settings
from src.polymarket_client import PolymarketClient
from src.weather_client import WeatherClient, LocationConfig
from src.market_scanner import MarketScanner, Market
from src.websocket_client import PolymarketWebSocket, PriceUpdate, WebSocketPriceMonitor

logger = logging.getLogger(__name__)


class TradingOpportunity:
    """Represents a trading opportunity with edge calculation."""
    
    def __init__(
        self,
        market: Market,
        outcome: str,
        token_id: str,
        model_probability: float,
        market_price: float,
        edge: float,
        reasoning: str
    ):
        self.market = market
        self.outcome = outcome
        self.token_id = token_id
        self.model_probability = model_probability  # What our model says
        self.market_price = market_price  # What market says
        self.edge = edge  # model_probability - market_price
        self.reasoning = reasoning
    
    def __repr__(self):
        return f"Opportunity({self.market.question[:30]}... {self.outcome} edge={self.edge:.2%})"


class WeatherTradingBot:
    """
    Autonomous weather trading bot.
    
    Workflow:
    1. Scan Polymarket for weather markets
    2. Fetch weather forecasts for relevant locations
    3. Compare forecast probabilities to market prices
    4. Place bets when edge exceeds threshold
    5. Manage positions (take profit, stop loss, hedge)
    """
    
    def __init__(self, settings: Optional[BotSettings] = None):
        self.settings = settings or get_settings()
        
        # Initialize clients
        self.poly_client = PolymarketClient(self.settings)
        self.weather_client = WeatherClient(self.settings)
        self.market_scanner = MarketScanner(self.settings)
        self.ws_monitor: Optional[WebSocketPriceMonitor] = None
        
        # State
        self.running = False
        self.positions: Dict[str, Dict] = {}  # market_address -> position info
        self.opportunities: List[TradingOpportunity] = []
        self.tracked_tokens: set = set()
        self.live_prices: Dict[str, float] = {}  # token_id -> current price
        
        # Paths
        self.data_dir = Path("data")
        self.data_dir.mkdir(exist_ok=True)
        
    def initialize(self) -> None:
        """Initialize all components."""
        logger.info("Initializing Weather Trading Bot...")
        
        # Initialize Polymarket client
        self.poly_client.initialize()
        
        # Check balance
        balance = self.poly_client.get_balance()
        logger.info(f"USDC Balance: {balance.get('usdc', 0)}")
        
        # Initialize WebSocket monitor
        self.ws_monitor = WebSocketPriceMonitor(self.settings)
        self.ws_monitor.add_price_alert(self._on_price_update)
        
        # Load existing positions
        self._load_positions()
        
        logger.info("Bot initialized successfully")
    
    def start(self) -> None:
        """Start the trading loop."""
        logger.info("Starting trading bot...")
        logger.info(f"Live trading: {self.settings.live_trading}")
        logger.info(f"Check interval: {self.settings.check_interval}s")
        logger.info(f"Min edge threshold: {self.settings.min_edge_threshold:.2%}")
        
        self.running = True
        
        try:
            while self.running:
                self._run_cycle()
                time.sleep(self.settings.check_interval)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            self.stop()
        except Exception as e:
            logger.error(f"Bot error: {e}")
            self.stop()
            raise
    
    def stop(self) -> None:
        """Stop the trading loop."""
        logger.info("Stopping trading bot...")
        self.running = False
        if self.ws_monitor:
            self.ws_monitor.stop_monitoring()
        self._save_positions()
    
    def _on_price_update(self, token_id: str, update: PriceUpdate) -> None:
        """
        Handle real-time price update from WebSocket.
        Called automatically when WebSocket receives price data.
        """
        self.live_prices[token_id] = update.midpoint
        
        # Check if this token is part of any opportunity
        for opp in self.opportunities:
            if opp.token_id == token_id:
                # Update opportunity with live price
                old_price = opp.market_price
                opp.market_price = update.midpoint
                opp.edge = opp.model_probability - update.midpoint
                
                # Log significant price movements
                price_change = update.midpoint - old_price
                if abs(price_change) >= 0.02:  # 2% move
                    direction = "▲" if price_change > 0 else "▼"
                    logger.info(
                        f"Price alert {direction}: {opp.outcome} on '{opp.market.question[:30]}...' "
                        f"{old_price:.2%} → {update.midpoint:.2%} ({price_change:+.2%}) "
                        f"Edge: {opp.edge:.2%}"
                    )
                
                # Execute trade if edge threshold crossed
                if self.settings.live_trading and abs(opp.edge) >= self.settings.min_edge_threshold:
                    if opp.edge > 0 and update.midpoint < old_price:  # Price dropped, good time to buy
                        logger.info(f"WebSocket trigger: Buying {opp.outcome} @ {update.midpoint:.2%}")
                        self._execute_single_trade(opp)
                    elif opp.edge < 0 and update.midpoint > old_price:  # Price rose, good time to sell
                        logger.info(f"WebSocket trigger: Selling {opp.outcome} @ {update.midpoint:.2%}")
                        self._execute_single_trade(opp)
    
    def _run_cycle(self) -> None:
        """Execute one trading cycle."""
        cycle_start = datetime.now()
        logger.info(f"=== Trading Cycle {cycle_start.isoformat()} ===")
        
        try:
            # Step 1: Scan for weather markets
            logger.info("Step 1: Scanning for weather markets...")
            markets = self.market_scanner.get_weather_markets(limit=50)
            logger.info(f"Found {len(markets)} weather markets")
            
            # Subscribe to WebSocket updates for these markets
            self._subscribe_to_websocket(markets)
            
            # Step 2: Get weather forecasts
            logger.info("Step 2: Fetching weather forecasts...")
            locations = self.settings.parsed_locations
            forecasts = {}
            for loc in locations:
                analysis = self.weather_client.analyze_precipitation_chance(loc)
                forecasts[loc.name] = analysis
                logger.info(f"  {loc.name}: avg precip {analysis.get('avg_precipitation_prob', 0):.1f}%")
            
            # Step 3: Find trading opportunities
            logger.info("Step 3: Analyzing opportunities...")
            self.opportunities = self._find_opportunities(markets, forecasts)
            logger.info(f"Found {len(self.opportunities)} opportunities")
            
            # Step 4: Execute trades
            if self.settings.live_trading:
                logger.info("Step 4: Executing trades...")
                self._execute_trades(self.opportunities)
            else:
                logger.info("Step 4: [DRY RUN] Would execute trades")
                for opp in self.opportunities:
                    logger.info(f"  Would bet on {opp.outcome} @ {opp.market_price:.2%} (edge: {opp.edge:.2%})")
            
            # Step 5: Manage existing positions
            logger.info("Step 5: Managing positions...")
            self._manage_positions()
            
            # Save state
            self._save_positions()
            
            cycle_duration = (datetime.now() - cycle_start).total_seconds()
            logger.info(f"Cycle completed in {cycle_duration:.1f}s")
            
        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)
    
    def _find_opportunities(
        self, 
        markets: List[Market], 
        forecasts: Dict[str, Any]
    ) -> List[TradingOpportunity]:
        """
        Find trading opportunities by comparing weather model to market prices.
        
        This is the core alpha-generation logic.
        """
        opportunities = []
        
        for market in markets:
            # Skip markets with no liquidity
            if market.liquidity < 100:
                continue
            
            # Try to match market to a location
            location_match = self._match_market_to_location(market, forecasts)
            if not location_match:
                continue
            
            location_name, forecast = location_match
            
            # Calculate model probability based on forecast
            model_prob, reasoning = self._calculate_market_probability(market, forecast)
            
            # Compare to market prices
            for i, outcome in enumerate(market.outcomes):
                if i >= len(market.outcome_prices):
                    continue
                    
                market_price = market.outcome_prices[i]
                edge = model_prob - market_price
                
                if abs(edge) >= self.settings.min_edge_threshold:
                    opp = TradingOpportunity(
                        market=market,
                        outcome=outcome,
                        token_id=market.clob_token_ids[i] if i < len(market.clob_token_ids) else None,
                        model_probability=model_prob,
                        market_price=market_price,
                        edge=edge,
                        reasoning=reasoning
                    )
                    opportunities.append(opp)
                    logger.info(f"  OPPORTUNITY: {outcome} on '{market.question[:40]}...' - Model: {model_prob:.1%}, Market: {market_price:.1%}, Edge: {edge:.1%}")
        
        # Sort by edge (highest first)
        opportunities.sort(key=lambda x: x.edge, reverse=True)
        return opportunities
    
    def _match_market_to_location(
        self, 
        market: Market, 
        forecasts: Dict[str, Any]
    ) -> Optional[tuple]:
        """Match a market to a monitored location."""
        import re
        question_lower = market.question.lower()
        
        # Direct match
        for loc_name, forecast in forecasts.items():
            if loc_name.lower() in question_lower:
                return (loc_name, forecast)
        
        # Extract city from pattern "in [City Name] be/on"
        match = re.search(r'in ([A-Za-z\s]+?) (?:be|on|will)', market.question, re.IGNORECASE)
        if match:
            extracted_city = match.group(1).strip().lower()
            
            # Check against our locations
            for loc_name, forecast in forecasts.items():
                if loc_name.lower() == extracted_city:
                    return (loc_name, forecast)
            
            # Check common aliases
            city_aliases = {
                'new york city': 'newyork',
                'nyc': 'newyork',
                'new york': 'newyork',
                'los angeles': 'la',
            }
            
            normalized = city_aliases.get(extracted_city, extracted_city)
            for loc_name, forecast in forecasts.items():
                if loc_name.lower() == normalized:
                    return (loc_name, forecast)
        
        return None
    
    def _calculate_market_probability(
        self, 
        market: Market, 
        forecast: Dict[str, Any]
    ) -> tuple:
        """
        Calculate probability for a market outcome based on weather forecast.
        
        Returns:
            (probability, reasoning)
        """
        import re
        question_lower = market.question.lower()
        
        # Rain/Precipitation markets
        if any(word in question_lower for word in ["rain", "precipitation", "shower"]):
            avg_prob = forecast.get("avg_precipitation_prob", 0)
            rainy_pct = forecast.get("rainy_percentage", 0)
            
            # Simple model: weighted average
            model_prob = (avg_prob * 0.6 + rainy_pct * 0.4) / 100
            
            reasoning = f"Avg precip: {avg_prob:.1f}%, Rainy hours: {rainy_pct:.1f}%"
            return (min(model_prob, 0.99), reasoning)
        
        # Temperature markets - enhanced logic
        elif any(word in question_lower for word in ["temperature", "degree", "fahrenheit", "celsius", "hottest", "coldest"]):
            avg_temp_f = forecast.get("avg_temp_f", 70)
            max_temp_f = forecast.get("max_temp_f", 80)
            min_temp_f = forecast.get("min_temp_f", 60)
            
            # Parse temperature range from question (e.g., "82-83°F" or "42°C")
            temp_range = self._extract_temp_range(market.question)
            
            if temp_range:
                low, high = temp_range
                unit = 'F' if 'fahrenheit' in question_lower or '°f' in question_lower.lower() or 'f' in question_lower[-3:].lower() else 'C'
                
                # Convert to Fahrenheit if needed
                if unit == 'C':
                    low = low * 9/5 + 32
                    high = high * 9/5 + 32
                
                # Calculate probability based on forecast vs target range
                model_prob, reasoning = self._calculate_temp_probability(
                    max_temp_f, min_temp_f, low, high
                )
                return (model_prob, reasoning)
            
            # Fallback: simple threshold check
            temp_match = re.search(r'(\d{2,3})\s*[°fF]?', question_lower)
            if temp_match:
                threshold = int(temp_match.group(1))
                # Assume Fahrenheit for US markets
                if threshold < 50:  # Likely Celsius
                    threshold = threshold * 9/5 + 32
                
                if "above" in question_lower or "over" in question_lower or "highest" in question_lower:
                    prob = max_temp_f >= threshold
                    model_prob = 0.85 if prob else 0.15
                elif "below" in question_lower or "under" in question_lower or "lowest" in question_lower:
                    prob = min_temp_f <= threshold
                    model_prob = 0.85 if prob else 0.15
                else:
                    model_prob = 0.5
                    
                reasoning = f"Avg temp: {avg_temp_f:.1f}°F, Max: {max_temp_f:.1f}°F, Threshold: {threshold:.0f}°F"
                return (model_prob, reasoning)
            
            return (0.5, "No temperature threshold found")
        
        # Storm/Thunder markets
        elif any(word in question_lower for word in ["storm", "thunder", "lightning"]):
            # Check for severe weather codes in forecast
            severe_hours = sum(1 for f in forecast.get("forecasts", []) 
                             if "thunder" in f.get("condition", "").lower() 
                             or "storm" in f.get("condition", "").lower())
            
            model_prob = min(severe_hours / 24, 0.9)
            reasoning = f"Severe weather hours: {severe_hours}"
            return (model_prob, reasoning)
        
        return (0.5, "Unknown market type")
    
    def _extract_temp_range(self, question: str) -> Optional[tuple]:
        """Extract temperature range from market question."""
        import re
        
        # Fahrenheit pattern: 82-83°F or 82-83
        match = re.search(r'(\d+)-(\d+)\s*°?F', question, re.IGNORECASE)
        if match:
            return (int(match.group(1)), int(match.group(2)))
        
        # Celsius pattern: 42°C
        match = re.search(r'(\d+)\s*°C', question, re.IGNORECASE)
        if match:
            temp_c = int(match.group(1))
            return (temp_c, temp_c + 1)  # Assume ±1°C range
        
        return None
    
    def _calculate_temp_probability(
        self,
        forecast_max: float,
        forecast_min: float,
        target_low: float,
        target_high: float
    ) -> tuple:
        """
        Calculate probability that temperature will be in the target range.
        
        Returns:
            (probability, reasoning)
        """
        target_mid = (target_low + target_high) / 2
        diff = abs(forecast_max - target_mid)
        
        # If forecast max is within the range, high probability
        if target_low <= forecast_max <= target_high:
            prob = 0.85
            reasoning = f"Forecast max ({forecast_max:.0f}°F) is IN range {target_low:.0f}-{target_high:.0f}°F"
        # If forecast is close (within 3 degrees), moderate probability
        elif diff <= 3:
            prob = 0.65 if forecast_max < target_low else 0.60
            reasoning = f"Forecast max ({forecast_max:.0f}°F) is CLOSE to range {target_low:.0f}-{target_high:.0f}°F"
        # If forecast is far, low probability
        elif forecast_max < target_low:
            prob = max(0.05, 0.30 - (target_low - forecast_max) * 0.05)
            reasoning = f"Forecast max ({forecast_max:.0f}°F) is BELOW range {target_low:.0f}-{target_high:.0f}°F"
        else:
            prob = max(0.05, 0.30 - (forecast_max - target_high) * 0.05)
            reasoning = f"Forecast max ({forecast_max:.0f}°F) is ABOVE range {target_low:.0f}-{target_high:.0f}°F"
        
        return (prob, reasoning)
        
        # Default: no strong signal
        return (0.5, "No matching market type")
    
    def _execute_trades(self, opportunities: List[TradingOpportunity]) -> None:
        """Execute trades for opportunities."""
        # Calculate current exposure
        current_exposure = self._get_total_exposure()
        
        for opp in opportunities:
            # Check exposure limits
            if current_exposure >= self.settings.max_total_exposure:
                logger.warning(f"Max exposure reached (${current_exposure}), skipping trades")
                break
            
            # Calculate bet size
            bet_size = min(
                self.settings.max_position_size,
                self.settings.max_total_exposure - current_exposure
            )
            
            if bet_size < 10:
                logger.warning("Insufficient remaining exposure for trade")
                break
            
            # Determine side (BUY if edge positive, SELL if negative)
            if opp.edge > 0:
                # Market is undervalued - BUY
                side = "BUY"
                logger.info(f"Buying {opp.outcome} @ {opp.market_price:.2%} (edge: {opp.edge:.2%})")
            else:
                # Market is overvalued - could SELL if we have position, otherwise skip
                if opp.market.question not in self.positions:
                    continue
                side = "SELL"
                logger.info(f"Selling {opp.outcome} @ {opp.market_price:.2%} (edge: {opp.edge:.2%})")
            
            try:
                # Place order
                response = self.poly_client.place_limit_order(
                    token_id=opp.token_id,
                    price=opp.market_price,
                    size=bet_size,
                    side=side,
                    order_type=self.settings.order_type
                )
                
                # Track position
                self._track_position(opp.market, opp.outcome, bet_size, response)
                
                current_exposure += bet_size
                
            except Exception as e:
                logger.error(f"Trade execution failed: {e}")
    
    def _execute_single_trade(self, opportunity: TradingOpportunity) -> None:
        """
        Execute a single trade triggered by WebSocket price update.
        
        Args:
            opportunity: Trading opportunity with updated price
        """
        # Check exposure
        current_exposure = self._get_total_exposure()
        if current_exposure >= self.settings.max_total_exposure:
            logger.warning("Max exposure reached, skipping WebSocket-triggered trade")
            return
        
        # Calculate bet size
        bet_size = min(
            self.settings.max_position_size,
            self.settings.max_total_exposure - current_exposure
        )
        
        if bet_size < 10:
            return
        
        # Determine side
        side = "BUY" if opportunity.edge > 0 else "SELL"
        
        try:
            response = self.poly_client.place_limit_order(
                token_id=opportunity.token_id,
                price=opportunity.market_price,
                size=bet_size,
                side=side,
                order_type=self.settings.order_type
            )
            
            self._track_position(opportunity.market, opportunity.outcome, bet_size, response)
            logger.info(f"WebSocket trade executed: {side} {bet_size} @ {opportunity.market_price:.2%}")
            
        except Exception as e:
            logger.error(f"WebSocket trade failed: {e}")
    
    def _subscribe_to_websocket(self, markets: List[Market]) -> None:
        """
        Subscribe to WebSocket updates for market tokens.
        
        Args:
            markets: List of markets to track
        """
        if not self.ws_monitor:
            return
        
        # Collect all token IDs from markets
        new_tokens = set()
        for market in markets:
            for token_id in market.clob_token_ids:
                if token_id and token_id not in self.tracked_tokens:
                    new_tokens.add(token_id)
        
        if new_tokens:
            self.tracked_tokens.update(new_tokens)
            logger.info(f"Subscribing to {len(new_tokens)} new tokens (total: {len(self.tracked_tokens)})")
    
    def _manage_positions(self) -> None:
        """Manage existing positions (take profit, stop loss, hedge)."""
        for market_addr, position in list(self.positions.items()):
            # Check if market is resolved
            # TODO: Fetch market status and handle resolution
            
            # Check for take profit opportunities
            # TODO: Implement take profit logic
            
            # Check for stop loss
            # TODO: Implement stop loss logic
            
            pass
    
    def _get_total_exposure(self) -> float:
        """Calculate total USDC exposure across all positions."""
        try:
            positions = self.poly_client.get_positions()
            total = 0
            for pos in positions:
                total += float(pos.get("position", 0))
            return total
        except Exception as e:
            logger.error(f"Error calculating exposure: {e}")
            return 0
    
    def _track_position(
        self, 
        market: Market, 
        outcome: str, 
        size: float, 
        order_response: Dict
    ) -> None:
        """Track a new position."""
        self.positions[market.market_address] = {
            "market": market.question,
            "market_address": market.market_address,
            "outcome": outcome,
            "size": size,
            "entry_price": order_response.get("price", 0),
            "order_id": order_response.get("orderID", ""),
            "timestamp": datetime.now().isoformat()
        }
        logger.info(f"Tracked position: {outcome} on '{market.question[:30]}...'")
    
    def _load_positions(self) -> None:
        """Load positions from disk."""
        positions_file = self.data_dir / "positions.json"
        if positions_file.exists():
            try:
                with open(positions_file) as f:
                    self.positions = json.load(f)
                logger.info(f"Loaded {len(self.positions)} positions from disk")
            except Exception as e:
                logger.warning(f"Failed to load positions: {e}")
    
    def _save_positions(self) -> None:
        """Save positions to disk."""
        positions_file = self.data_dir / "positions.json"
        try:
            with open(positions_file, "w") as f:
                json.dump(self.positions, f, indent=2)
            logger.debug("Saved positions to disk")
        except Exception as e:
            logger.error(f"Failed to save positions: {e}")
    
    def run_once(self) -> Dict[str, Any]:
        """Run a single trading cycle (for testing/cron)."""
        if not self.poly_client.is_initialized():
            self.initialize()
        
        self._run_cycle()
        
        return {
            "opportunities": len(self.opportunities),
            "positions": len(self.positions),
            "exposure": self._get_total_exposure()
        }
