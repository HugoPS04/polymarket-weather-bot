"""
Exit Strategy Manager for Polymarket Trading Bot.
Handles take-profit ladder and trailing stops.
"""
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from config.settings import BotSettings, get_settings

logger = logging.getLogger(__name__)


class ExitType(Enum):
    """Types of exit strategies."""
    TAKE_PROFIT_LADDER = "take_profit_ladder"
    TRAILING_STOP = "trailing_stop"
    TIME_BASED = "time_based"
    MINIMUM_EDGE = "minimum_edge"


@dataclass
class ExitTier:
    """Single take-profit tier."""
    price_threshold: float  # Market price to trigger exit
    percentage: float  # % of position to exit
    profit_pct: float  # Expected profit at this tier
    
    def __repr__(self):
        return f"ExitTier(p={self.price_threshold:.0%}, exit={self.percentage:.0%}%)"


@dataclass
class ExitConfig:
    """Exit strategy configuration."""
    # Exit type
    exit_type: ExitType = ExitType.TAKE_PROFIT_LADDER
    
    # Take profit ladder tiers (price threshold -> % to exit)
    # Example: 20% market price → exit 25% of position
    take_profit_tiers: List[ExitTier] = field(default_factory=lambda: [
        ExitTier(price_threshold=0.20, percentage=0.25, profit_pct=0.20),
        ExitTier(price_threshold=0.30, percentage=0.25, profit_pct=0.40),
        ExitTier(price_threshold=0.40, percentage=0.25, profit_pct=0.60),
        ExitTier(price_threshold=0.50, percentage=0.25, profit_pct=0.80),
    ])
    
    # Trailing stop settings
    trailing_stop_enabled: bool = False
    trailing_profit_threshold: float = 0.10  # Start trailing after 10% profit
    trailing_distance_pct: float = 0.05  # Stop 5% below peak
    
    # Time-based exit
    time_based_exit_hours: Optional[int] = None  # None = no time exit
    time_based_min_profit_pct: float = 0.05  # Minimum profit before time exit triggers
    
    # Minimum edge exit
    minimum_edge_pct: float = 0.03  # Exit if edge drops below 3%
    
    # Stop loss (optional)
    stop_loss_pct: Optional[float] = None  # e.g., 0.05 = exit at -5%


@dataclass 
class PositionExit:
    """Tracks exit state for a position."""
    market_address: str
    token_id: str
    entry_price: float
    size: float
    entry_time: datetime
    
    # Current state
    current_price: float = 0.0
    peak_price: float = 0.0
    
    # Exit tracking
    exited_tiers: List[int] = field(default_factory=list)
    total_exited_pct: float = 0.0
    realized_profit: float = 0.0
    
    # Trailing stop state
    trailing_activated: bool = False
    
    @property
    def current_pnl_pct(self) -> float:
        """Current P&L percentage."""
        if self.entry_price == 0:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price
    
    @property
    def unrealized_profit(self) -> float:
        """Unrealized profit in USDC."""
        remaining = self.size * (1 - self.total_exited_pct)
        return remaining * self.current_pnl_pct
    
    @property
    def total_profit(self) -> float:
        """Total realized + unrealized profit."""
        return self.realized_profit + self.unrealized_profit
    
    def should_exit(self, config: ExitConfig) -> tuple:
        """
        Check if position should exit based on config.
        
        Returns:
            (should_exit, exit_pct, reason)
        """
        reasons = []
        
        # 1. Check take profit ladder
        if config.exit_type == ExitType.TAKE_PROFIT_LADDER:
            for i, tier in enumerate(config.take_profit_tiers):
                if i not in self.exited_tiers and self.current_price >= tier.price_threshold:
                    return (True, tier.percentage, f"Take profit tier {i+1} @ {tier.price_threshold:.0%}")
        
        # 2. Check trailing stop
        if config.trailing_stop_enabled:
            if self.current_pnl_pct >= config.trailing_profit_threshold:
                self.trailing_activated = True
            
            if self.trailing_activated:
                if self.current_pnl_pct <= (self.peak_price - self.entry_price) / self.entry_price - config.trailing_distance_pct:
                    return (True, 1.0, "Trailing stop triggered")
        
        # 3. Check time-based exit
        if config.time_based_exit_hours:
            elapsed = datetime.now() - self.entry_time
            if elapsed >= timedelta(hours=config.time_based_exit_hours):
                if self.current_pnl_pct >= config.time_based_min_profit_pct:
                    return (True, 1.0, f"Time exit after {config.time_based_exit_hours}h")
        
        # 4. Check minimum edge
        if self.current_pnl_pct <= config.minimum_edge_pct:
            return (True, 1.0, f"Edge below {config.minimum_edge_pct:.0%}")
        
        # 5. Check stop loss
        if config.stop_loss_pct and self.current_pnl_pct <= -config.stop_loss_pct:
            return (True, 1.0, f"Stop loss at -{config.stop_loss_pct:.0%}")
        
        return (False, 0.0, "")
    
    def update_peak(self) -> None:
        """Update peak price for trailing stop."""
        if self.current_price > self.peak_price:
            self.peak_price = self.current_price


class ExitManager:
    """
    Manages exit strategies for open positions.
    
    Usage:
        exit_mgr = ExitManager(settings)
        exit_mgr.add_position(market_addr, token_id, entry_price, size)
        
        # In trading loop:
        exits = exit_mgr.check_positions(current_prices)
        for exit_signal in exits:
            # Execute exit orders
    """
    
    def __init__(self, settings: Optional[BotSettings] = None):
        self.settings = settings or get_settings()
        self.exit_config = self._load_exit_config()
        self.positions: Dict[str, PositionExit] = {}
    
    def _load_exit_config(self) -> ExitConfig:
        """Load exit config from settings."""
        # Load from .env with defaults
        return ExitConfig(
            exit_type=ExitType.TAKE_PROFIT_LADDER,
            trailing_stop_enabled=self.settings.exit_trailing_stop if hasattr(self.settings, 'exit_trailing_stop') else False,
            trailing_profit_threshold=0.10,
            trailing_distance_pct=0.05,
            time_based_exit_hours=None,
            time_based_min_profit_pct=0.05,
            minimum_edge_pct=0.03,
            stop_loss_pct=None
        )
    
    def add_position(
        self, 
        market_address: str, 
        token_id: str, 
        entry_price: float,
        size: float,
        entry_time: Optional[datetime] = None
    ) -> None:
        """Track a new position for exit management."""
        self.positions[market_address] = PositionExit(
            market_address=market_address,
            token_id=token_id,
            entry_price=entry_price,
            size=size,
            entry_time=entry_time or datetime.now(),
            current_price=entry_price,
            peak_price=entry_price
        )
        logger.info(f"Tracking position for exit: {market_address[:20]}... @ ${entry_price:.2f}")
    
    def update_price(self, market_address: str, current_price: float) -> None:
        """Update current price for a position."""
        if market_address in self.positions:
            pos = self.positions[market_address]
            pos.current_price = current_price
            pos.update_peak()
    
    def check_positions(self, prices: Dict[str, float]) -> List[Dict[str, Any]]:
        """
        Check all positions for exit signals.
        
        Args:
            prices: Dict mapping market_address -> current price
            
        Returns:
            List of exit signals with instructions
        """
        exit_signals = []
        
        for market_addr, pos in list(self.positions.items()):
            # Update price
            if market_addr in prices:
                pos.current_price = prices[market_addr]
                pos.update_peak()
            
            # Check exit conditions
            should_exit, exit_pct, reason = pos.should_exit(self.exit_config)
            
            if should_exit:
                # Calculate exit amount
                exit_amount = pos.size * exit_pct * (1 - pos.total_exited_pct)
                
                signal = {
                    "market_address": market_addr,
                    "token_id": pos.token_id,
                    "reason": reason,
                    "exit_pct": exit_pct,
                    "exit_amount": exit_amount,
                    "current_price": pos.current_price,
                    "entry_price": pos.entry_price,
                    "pnl_pct": pos.current_pnl_pct,
                    "realized_profit": pos.realized_profit
                }
                
                exit_signals.append(signal)
                
                # Update tracking
                pos.exited_tiers.append(len(pos.exited_tiers))
                pos.total_exited_pct += exit_pct
                
                if pos.total_exited_pct >= 0.99:  # Fully exited
                    del self.positions[market_addr]
        
        return exit_signals
    
    def execute_exit(self, signal: Dict) -> Dict:
        """
        Execute an exit signal.
        Call this after check_positions returns signals.
        
        Returns:
            Execution result
        """
        logger.info(f"EXIT SIGNAL: {signal['reason']}")
        logger.info(f"  Exit amount: ${signal['exit_amount']:.2f}")
        logger.info(f"  Current price: ${signal['current_price']:.2f}")
        logger.info(f"  P&L: {signal['pnl_pct']:.1%}")
        
        # Return signal dict for trading bot to execute
        return {
            "action": "SELL" if signal['current_price'] > signal['entry_price'] else "SELL",
            "token_id": signal['token_id'],
            "size": signal['exit_amount'],
            "price": signal['current_price'],
            "reason": signal['reason']
        }
    
    def get_status(self) -> Dict[str, Any]:
        """Get current exit manager status."""
        return {
            "tracked_positions": len(self.positions),
            "exit_config": {
                "type": self.exit_config.exit_type.value,
                "trailing_enabled": self.exit_config.trailing_stop_enabled,
                "tiers_count": len(self.exit_config.take_profit_tiers)
            },
            "positions": [
                {
                    "market": p.market_address[:20],
                    "entry": p.entry_price,
                    "current": p.current_price,
                    "pnl_pct": p.current_pnl_pct,
                    "exited_pct": p.total_exited_pct
                }
                for p in self.positions.values()
            ]
        }
    
    def remove_position(self, market_address: str) -> None:
        """Remove a position from tracking."""
        if market_address in self.positions:
            del self.positions[market_address]
    
    def update_config(self, **kwargs) -> None:
        """Update exit configuration at runtime."""
        for key, value in kwargs.items():
            if hasattr(self.exit_config, key):
                setattr(self.exit_config, key, value)
        logger.info(f"Exit config updated: {kwargs}")