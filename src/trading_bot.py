"""
Refined Weather Trading Bot with Consensus Engine.
Uses multi-API weather data to find high-confidence trading opportunities.
"""
import logging
import time
import json
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from pathlib import Path

from config.settings import BotSettings, get_settings
from src.polymarket_client import PolymarketClient
from src.weather_client import WeatherClient, ConsensusForecast
from src.market_scanner import MarketScanner, Market
from src.consensus_engine import ConsensusEngine, TradingSignal
from src.safety_manager import IntelligentLogger
from src.exit_manager import ExitManager
from src.websocket_client import PolymarketWebSocket, PriceUpdate, WebSocketPriceMonitor

logger = logging.getLogger(__name__)


class WeatherTradingBot:
    """
    High-confidence weather trading bot using consensus weather data.
    
    Strategy:
    - Fetch weather from 2-3 APIs (Open-Meteo, Visual Crossing, NOAA)
    - Only trade when APIs agree (consensus)
    - Minimum 85% confidence threshold
    - Focus on high-probability scenarios (85%+)
    - Place limit orders at favorable prices
    """
    
    def __init__(self, settings: Optional[BotSettings] = None):
        self.settings = settings or get_settings()
        
        # Initialize clients
        self.poly_client = PolymarketClient(self.settings)
        self.market_scanner = MarketScanner(self.settings)
        self.consensus_engine = ConsensusEngine(settings)
        self.ws_monitor: Optional[WebSocketPriceMonitor] = None
        
        # State
        self.running = False
        self.positions: Dict[str, Dict] = {}
        self.signals: List[TradingSignal] = []
        self.tracked_tokens: set = set()
        self.live_prices: Dict[str, float] = {}
        
        # Paths
        self.data_dir = Path("data")
        self.data_dir.mkdir(exist_ok=True)
        
        # Logging
        self.log_dir = Path("logs")
        self.log_dir.mkdir(exist_ok=True)
        
        # Live logger for console output
        self.logger_console = IntelligentLogger("TradeLog")
        
        # Initialize
        self.initialize()
        
        return self
    
    def initialize(self) -> None:
        """Initialize all components."""
        logger.info("Initializing Weather Trading Bot (Consensus Edition)...")
        logger.info(f"Weather locations: {[loc.name for loc in self.settings.parsed_locations]}")
        logger.info(f"Min confidence threshold: {self.consensus_engine.MIN_CONFIDENCE:.0%}")
        logger.info(f"Min API agreement: {self.consensus_engine.MIN_API_AGREEMENT}")
        
        # Initialize Polymarket client
        self.poly_client.initialize()
        
        # Check balance
        balance = self.poly_client.get_balance()
        logger.info(f"USDC Balance: {balance.get('usdc', 0)}")
        
        # Initialize WebSocket monitor
        self.ws_monitor = WebSocketPriceMonitor(self.settings)
        self.ws_monitor.add_price_alert(self._on_price_update)
        
        # Initialize exit manager
        self.exit_manager = ExitManager(self.settings)
        
        # Load existing positions
        self._load_positions()
        
        logger.info("Bot initialized successfully")
    
    def start(self) -> None:
        """Start the trading loop."""
        logger.info("Starting trading bot...")
        logger.info(f"Live trading: {self.settings.live_trading}")
        logger.info(f"Check interval: {self.settings.check_interval}s")
        logger.info(f"Min edge threshold: {self.settings.min_edge_threshold:.0%}")
        
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
        """Handle real-time price update from WebSocket."""
        self.live_prices[token_id] = update.midpoint
        
        # Check if this token is part of any signal
        for signal in self.signals:
            if signal.token_id == token_id:
                # Check for significant price movement
                if signal.market_price != update.midpoint:
                    old_edge = signal.edge
                    signal.market_price = update.midpoint
                    signal.edge = signal.model_probability - update.midpoint
                    
                    if abs(signal.edge - old_edge) >= 0.02:
                        logger.info(
                            f"Price update: {signal.outcome} {old_edge:.1%} → {signal.edge:.1%} "
                            f"(market now at {update.midpoint:.1%})"
                        )
    
    def _run_cycle(self) -> None:
        """Execute one trading cycle."""
        cycle_start = datetime.now()
        logger.info(f"\n{'='*60}")
        logger.info(f"Trading Cycle {cycle_start.isoformat()}")
        logger.info(f"{'='*60}")
        
        try:
            # Step 1: Scan for weather markets
            logger.info("Step 1: Scanning for weather markets...")
            markets = self.market_scanner.get_weather_markets(limit=50)
            logger.info(f"Found {len(markets)} weather markets")
            
            # Subscribe to WebSocket tokens
            self._subscribe_to_websocket(markets)
            
            # Step 2: Analyze markets with consensus engine
            logger.info("Step 2: Analyzing with consensus engine...")
            self.signals = self.consensus_engine.get_high_confidence_signals(markets, debug=True)
            logger.info(f"Found {len(self.signals)} high-confidence signals")
            
            # Print signals
            for i, signal in enumerate(self.signals, 1):
                print(f"\n  📊 Signal #{i}:")
                print(f"  Market: {signal.market_question[:50]}...")
                print(f"  Model: {signal.model_probability:.0%} | Market: {signal.market_price:.0%} | Edge: {signal.edge:.0%}")
                print(f"  Confidence: {signal.confidence:.0%} ({signal.api_count} APIs)")
                print(f"  Action: {signal.recommended_action} ({signal.risk_level} risk)")
                print(f"  Reason: {signal.reasoning[:80]}...")
            
            # Step 3: Execute trades
            if self.settings.live_trading:
                logger.info("\nStep 3: Executing trades...")
                self._execute_signals(self.signals)
            else:
                logger.info("\nStep 3: [DRY RUN] Would execute trades:")
                for signal in self.signals:
                    logger.info(
                        f"  Would {signal.recommended_action} ${signal.bet_size_recommendation * self.settings.max_position_size:.0f} "
                        f"on {signal.outcome} @ {signal.market_price:.0%}"
                    )
            
            # Step 4: Manage positions
            logger.info("\nStep 4: Managing positions...")
            self._manage_positions()
            
            # Save state
            self._save_positions()
            
            cycle_duration = (datetime.now() - cycle_start).total_seconds()
            logger.info(f"\nCycle completed in {cycle_duration:.1f}s")
            
        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)
    
    def _execute_signals(self, signals: List[TradingSignal]) -> None:
        """Execute trades for high-confidence signals."""
        current_exposure = self._get_total_exposure()
        
        for signal in signals:
            # Check exposure limits
            if current_exposure >= self.settings.max_total_exposure:
                logger.warning(f"Max exposure reached (${current_exposure}), skipping trades")
                break
            
            # Calculate bet size based on confidence
            bet_size = (
                self.settings.max_position_size * signal.bet_size_recommendation
            )
            bet_size = min(bet_size, self.settings.max_total_exposure - current_exposure)
            
            if bet_size < 10:
                continue
            
            # Determine side
            if signal.recommended_action == "BUY":
                side = "BUY"
            elif signal.recommended_action == "SELL":
                side = "SELL"
            else:
                continue
            
            try:
                logger.info(
                    f"Executing: {side} ${bet_size:.0f} on {signal.outcome} "
                    f"@ {signal.market_price:.1%} (edge: {signal.edge:.1%})"
                )
                
                response = self.poly_client.place_limit_order(
                    token_id=signal.token_id,
                    price=signal.market_price,
                    size=bet_size,
                    side=side,
                    order_type=self.settings.order_type
                )
                
                self._track_signal_position(signal, bet_size, response)
                current_exposure += bet_size
                
            except Exception as e:
                logger.error(f"Trade execution failed: {e}")
    
    def _check_exits(self) -> None:
        """Check for exit signals on open positions."""
        # Get current prices from live_prices
        prices = {
            token_id: price 
            for token_id, price in self.live_prices.items()
        }
        
        # Also get prices from market scanner
        for signal in self.signals:
            prices[signal.market_address] = signal.market_price
        
        # Check for exits
        exit_signals = self.exit_manager.check_positions(prices)
        
        for signal in exit_signals:
            logger.info(f"EXIT SIGNAL: {signal['reason']}")
            logger.info(f"  Market: {signal['market_address'][:30]}...")
            logger.info(f"  Exit amount: ${signal['exit_amount']:.2f}")
            logger.info(f"  Current price: ${signal['current_price']:.2f} (entry: ${signal['entry_price']:.2f})")
            logger.info(f"  P&L: {signal['pnl_pct']:.1%}")
            
            # Live log exit
            self._log_exit(signal)
            
            if self.settings.live_trading:
                # Execute exit
                self._execute_exit(signal)
    
    def _execute_exit(self, exit_signal: Dict) -> None:
        """Execute an exit order."""
        try:
            response = self.poly_client.place_limit_order(
                token_id=exit_signal['token_id'],
                price=exit_signal['current_price'],
                size=exit_signal['exit_amount'],
                side="SELL",
                order_type="FOK"
            )
            logger.info(f"Exit order placed: {response.get('orderID', 'N/A')}")
        except Exception as e:
            logger.error(f"Exit order failed: {e}")
        
        # Live log exit execution (always runs)
        self._log_exit_executed(exit_signal)
    
    def _log_exit(self, signal) -> None:
        """Log exit signal to console."""
        try:
            pnl = signal['pnl_pct']
            emoji = "💰" if pnl > 0 else "📉"
            self.logger_console.banner(f"{emoji} EXIT SIGNAL")
            self.logger_console.item("Reason", signal['reason'])
            self.logger_console.item("Exit amount", f"${signal['exit_amount']:.2f}")
            self.logger_console.item("Entry", f"${signal['entry_price']:.2f}")
            self.logger_console.item("Current", f"${signal['current_price']:.2f}")
            self.logger_console.item("P&L", f"{pnl:.1%}")
        except:
            pass
    
    def _log_exit_executed(self, signal) -> None:
        """Log exit executed to console."""
        try:
            self.logger_console.success(f"Exit executed: {signal.get('order_id', 'N/A')[:20]}...")
        except:
            pass

    def _calculate_position_size(self, signal: TradingSignal) -> float:
        """Calculate position size using Kelly Criterion.
        
        Kelly Formula: f* = (bp - q) / b
        Where:
        - b = odds received (price / (1 - price))
        - p = probability of winning (our model probability)
        - q = probability of losing (1 - p)
        
        We apply a safety fraction (typically 0.25) to reduce volatility.
        """
        # Get parameters
        kelly_enabled = getattr(self.settings, 'kelly_enabled', True)
        kelly_fraction = getattr(self.settings, 'kelly_fraction', 0.25)
        kelly_min = getattr(self.settings, 'kelly_min_bet', 0.5)
        kelly_max = getattr(self.settings, 'kelly_max_bet', 10)
        
        if not kelly_enabled:
            # Fallback to simple confidence-based sizing
            return self.settings.max_position_size * signal.bet_size_recommendation
        
        # Our probability estimate
        p = signal.model_probability
        
        # Market implied probability (1 / odds)
        # Price is in format like 0.10 = 10% = odds of 9x
        if signal.market_price <= 0:
            return kelly_min
        
        # Calculate b (odds received)
        b = (1 / signal.market_price) - 1  # If price=0.10, b=9 (bet 1 win 9)
        
        # q = probability of losing
        q = 1 - p
        
        # Full Kelly
        if b <= 0:
            kelly_bet = kelly_min
        else:
            kelly = (b * p - q) / b
            kelly_bet = kelly * kelly_fraction
        
        # Convert to dollar amount based on max position
        dollar_bet = kelly_bet * self.settings.max_position_size
        
        # Apply bounds
        dollar_bet = max(kelly_min, min(kelly_max, dollar_bet))
        
        # Ensure we don't exceed remaining exposure
        current_exposure = self._get_total_exposure()
        max_allowed = self.settings.max_total_exposure - current_exposure
        dollar_bet = min(dollar_bet, max_allowed)
        
        logger.info(f"Kelly calculation: p={p:.0%}, b={b:.2f}, Kelly={kelly:.2%}, "
                    f"fraction={kelly_fraction}, bet=${dollar_bet:.2f}")
        
        return dollar_bet
    
    def _track_signal_position(
        self, 
        signal: TradingSignal, 
        size: float, 
        order_response: Dict
    ) -> None:
        """Track a new position from a signal."""
        self.positions[signal.market_address] = {
            "market": signal.market_question,
            "market_address": signal.market_address,
            "outcome": signal.outcome,
            "size": size,
            "entry_price": order_response.get("price", signal.market_price),
            "model_probability": signal.model_probability,
            "confidence": signal.confidence,
            "order_id": order_response.get("orderID", ""),
            "signal_reasoning": signal.reasoning,
            "timestamp": datetime.now().isoformat()
        }
    
    def _log_position_opened(self, signal, size, response) -> None:
        """Log position opened to console."""
        try:
            self.logger_console.banner("📌 POSITION OPENED")
            self.logger_console.item("Market", signal.market_question[:50])
            self.logger_console.item("Outcome", signal.outcome)
            self.logger_console.item("Entry price", f"${signal.market_price:.2f}")
            self.logger_console.item("Size", f"${size:.2f}")
            self.logger_console.item("Model prob", f"{signal.model_probability:.0%}")
            self.logger_console.item("Edge", f"{signal.edge:.0%}")
            self.logger_console.item("Confidence", f"{signal.confidence:.0%}")
            self.logger_console.item("Order ID", response.get("orderID", "N/A")[:20])
            self.logger_console.item("Time", datetime.now().strftime("%H:%M:%S"))
        except Exception as e:
            logger.debug(f"Console log failed: {e}")
    
    def _log_position_opened_simple(self, opportunity, size) -> None:
        """Log position opened (simple version for websocket)."""
        try:
            self.logger_console.banner("📌 POSITION OPENED (WebSocket)")
            self.logger_console.item("Market", opportunity.market.question[:50])
            self.logger_console.item("Price", f"${opportunity.market_price:.2f}")
            self.logger_console.item("Size", f"${size:.2f}")
            self.logger_console.item("Edge", f"{opportunity.edge:.0%}")
        except:
            pass
    
    def _subscribe_to_websocket(self, markets: List[Market]) -> None:
        """Subscribe to WebSocket updates for market tokens."""
        if not self.ws_monitor:
            return
        
        new_tokens = set()
        for market in markets:
            for token_id in market.clob_token_ids:
                if token_id and token_id not in self.tracked_tokens:
                    new_tokens.add(token_id)
        
        if new_tokens:
            self.tracked_tokens.update(new_tokens)
            logger.debug(f"Tracking {len(new_tokens)} new tokens (total: {len(self.tracked_tokens)})")
    
    def _get_total_exposure(self) -> float:
        """Calculate total USDC exposure across all positions."""
        return sum(pos.get("size", 0) for pos in self.positions.values())
    
    def _manage_positions(self) -> None:
        """Manage existing positions - check settlements and claims."""
        if not self.positions:
            return
        
        logger.info(f"Checking {len(self.positions)} positions for settlement...")
        
        # Get market addresses to check
        market_addresses = list(self.positions.keys())
        
        try:
            # Check market resolution statuses
            statuses = self.poly_client.get_markets_status(market_addresses)
            
            for market_addr, status in statuses.items():
                if status.get("error"):
                    continue
                
                if status.get("resolved"):
                    logger.info(f"Market resolved: {market_addr}")
                    logger.info(f"  Winner: {status.get('answer', 'Unknown')}")
                    
                    # Get our position on this market
                    position = self.positions.get(market_addr, {})
                    our_outcome = position.get("outcome", "")
                    
                    # Check if we won
                    if our_outcome.lower() == status.get("answer", "").lower():
                        logger.info(f"  We WON! Claiming rewards...")
                        result = self.poly_client.claim_rewards(market_addr)
                        logger.info(f"  Claim result: {result}")
                    else:
                        logger.info(f"  We lost this one")
                    
                    # Settle the market
                    settle_result = self.poly_client.settle_market(market_addr)
                    logger.info(f"  Settlement: {settle_result}")
                    
                    # Remove from active positions
                    del self.positions[market_addr]
                    self._save_positions()
                    
        except Exception as e:
            logger.error(f"Position management error: {e}")
    
    def check_and_settle_all(self) -> Dict[str, Any]:
        """
        Check all positions and settle any resolved markets.
        Run this periodically or on startup.
        """
        results = {
            "positions_checked": len(self.positions),
            "settled": [],
            "claimed": [],
            "errors": []
        }
        
        if not self.positions:
            logger.info("No positions to check")
            return results
        
        # Get market statuses
        market_addresses = list(self.positions.keys())
        
        try:
            statuses = self.poly_client.get_markets_status(market_addresses)
            
            for market_addr, status in statuses.items():
                if status.get("error"):
                    results["errors"].append({"market": market_addr, "error": status["error"]})
                    continue
                
                if status.get("resolved"):
                    position = self.positions.get(market_addr, {})
                    
                    # Claim rewards
                    if position:
                        claim_result = self.poly_client.claim_rewards(market_addr)
                        if not claim_result.get("error"):
                            results["claimed"].append({
                                "market": market_addr,
                                "outcome": position.get("outcome"),
                                "size": position.get("size")
                            })
                    
                    # Settle
                    settle_result = self.poly_client.settle_market(market_addr)
                    results["settled"].append({"market": market_addr})
                    
                    # Remove from tracking
                    if market_addr in self.positions:
                        del self.positions[market_addr]
                    
        except Exception as e:
            logger.error(f"Settle all error: {e}")
            results["errors"].append(str(e))
        
        self._save_positions()
        return results
    
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
    
    def run_once(self, debug: bool = False) -> Dict[str, Any]:
        """Run a single trading cycle (for testing/cron)."""
        if not self.poly_client.is_initialized():
            self.initialize()
        
        if debug:
            # Enable debug logging temporarily
            logging.getLogger().setLevel(logging.DEBUG)
        
        self._run_cycle()
        
        return {
            "signals": len(self.signals),
            "positions": len(self.positions),
            "exposure": self._get_total_exposure(),
            "high_confidence_trades": [
                {
                    "market": s.market_question[:50],
                    "outcome": s.outcome,
                    "model_prob": s.model_probability,
                    "market_price": s.market_price,
                    "edge": s.edge,
                    "confidence": s.confidence,
                    "action": s.recommended_action
                }
                for s in self.signals[:5]  # Top 5
            ]
        }
    
    def get_consensus_status(self) -> Dict[str, Any]:
        """Get status of consensus weather analysis."""
        status = {
            "locations": [],
            "apis": ["open-meteo", "visual-crossing", "noaa"],
            "consensus_available": False
        }
        
        for loc in self.settings.parsed_locations:
            try:
                consensus = self.consensus_engine.weather_client.get_consensus_forecast(loc, days=1)
                status["locations"].append({
                    "name": loc.name,
                    "has_data": len(consensus) > 0,
                    "dates": list(consensus.keys())[:3]
                })
                status["consensus_available"] = True
            except Exception as e:
                status["locations"].append({
                    "name": loc.name,
                    "has_data": False,
                    "error": str(e)
                })
        
        return status
