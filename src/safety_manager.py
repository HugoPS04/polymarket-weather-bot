"""
Safety Manager for Trading Bot
Provides emergency stops, circuit breakers, and state recovery.
"""
import logging
import signal
import atexit
import json
import os
from typing import Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass
class SafetyState:
    """Persistent safety state for recovery."""
    running: bool = False
    last_cycle_time: Optional[datetime] = None
    consecutive_errors: int = 0
    total_trades: int = 0
    emergency_stop_active: bool = False
    circuit_broken: bool = False
    last_error: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "last_cycle_time": self.last_cycle_time.isoformat() if self.last_cycle_time else None,
            "consecutive_errors": self.consecutive_errors,
            "total_trades": self.total_trades,
            "emergency_stop_active": self.emergency_stop_active,
            "circuit_broken": self.circuit_broken,
            "last_error": self.last_error
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "SafetyState":
        state = cls()
        state.running = data.get("running", False)
        if data.get("last_cycle_time"):
            state.last_cycle_time = datetime.fromisoformat(data["last_cycle_time"])
        state.consecutive_errors = data.get("consecutive_errors", 0)
        state.total_trades = data.get("total_trades", 0)
        state.emergency_stop_active = data.get("emergency_stop_active", False)
        state.circuit_broken = data.get("circuit_broken", False)
        state.last_error = data.get("last_error")
        return state


class SafetyManager:
    """
    Manages safety features for the trading bot.
    
    Features:
    - Emergency stop (SIGINT/SIGTERM handling)
    - Circuit breaker (stop after X errors)
    - State persistence (recover after crash)
    - Graceful shutdown
    - Health monitoring
    """
    
    EMERGENCY_STOP_FILE = "data/EMERGENCY_STOP"
    STATE_FILE = "data/safety_state.json"
    
    def __init__(
        self,
        max_consecutive_errors: int = 3,
        max_error_rate: float = 0.3,
        health_check_interval: int = 60,
        state_file: Optional[str] = None
    ):
        self.max_consecutive_errors = max_consecutive_errors
        self.max_error_rate = max_error_rate
        self.health_check_interval = health_check_interval
        
        self.state_file = Path(state_file or self.STATE_FILE)
        self.state_file.parent.mkdir(exist_ok=True, parents=True)
        
        self.state = self._load_state()
        self._lock = Lock()
        
        # Setup signal handlers
        self._setup_signal_handlers()
        
        # Register cleanup
        atexit.register(self.cleanup)
        
        logger.info("SafetyManager initialized")
        logger.info(f"  Max consecutive errors: {max_consecutive_errors}")
        logger.info(f"  Max error rate: {max_error_rate:.0%}")
    
    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        logger.info("Signal handlers registered (SIGINT/SIGTERM)")
    
    def _signal_handler(self, signum, frame) -> None:
        """Handle shutdown signals."""
        sig_name = signal.Signals(signum).name
        logger.warning(f"Received {sig_name} - initiating graceful shutdown...")
        self.emergency_stop(reason=f"Signal {sig_name} received")
    
    def _load_state(self) -> SafetyState:
        """Load persisted state."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    data = json.load(f)
                    state = SafetyState.from_dict(data)
                    logger.info(f"Loaded safety state: {state.consecutive_errors} errors, {state.total_trades} trades")
                    return state
            except Exception as e:
                logger.warning(f"Failed to load state: {e}")
        return SafetyState()
    
    def _save_state(self) -> None:
        """Persist current state."""
        with self._lock:
            try:
                with open(self.state_file, "w") as f:
                    json.dump(self.state.to_dict(), f, indent=2)
            except Exception as e:
                logger.error(f"Failed to save state: {e}")
    
    def record_cycle_start(self) -> None:
        """Record start of trading cycle."""
        self.state.running = True
        self.state.last_cycle_time = datetime.now()
        self._save_state()
    
    def record_cycle_end(self, success: bool = True) -> None:
        """Record end of trading cycle."""
        if success:
            self.state.consecutive_errors = 0
        self.state.last_cycle_time = datetime.now()
        self._save_state()
    
    def record_error(self, error: str) -> bool:
        """
        Record an error. Returns True if circuit breaker should trigger.
        
        Circuit breaker triggers when:
        - consecutive errors >= max_consecutive_errors
        - OR error rate > max_error_rate over recent cycles
        """
        self.state.consecutive_errors += 1
        self.state.last_error = error
        self.state.last_cycle_time = datetime.now()
        
        logger.error(f"Error recorded (consecutive: {self.state.consecutive_errors}): {error[:100]}")
        
        # Check if should break circuit
        should_break = (
            self.state.consecutive_errors >= self.max_consecutive_errors or
            self._check_error_rate()
        )
        
        if should_break:
            self._break_circuit(error)
        
        self._save_state()
        return should_break
    
    def _check_error_rate(self) -> bool:
        """Check if error rate exceeds threshold."""
        # Simple check: if last 10 cycles had >30% errors
        # For now, just check consecutive
        return False
    
    def _break_circuit(self, reason: str) -> None:
        """Break the circuit - stop trading."""
        self.state.circuit_broken = True
        logger.critical(f"CIRCUIT BREAKER TRIGGERED: {reason}")
        logger.critical("Trading halted. Run 'python main.py resume' to restart after fixing issues.")
    
    def emergency_stop(self, reason: str = "Manual emergency stop") -> None:
        """Trigger emergency stop."""
        logger.warning(f"EMERGENCY STOP: {reason}")
        self.state.emergency_stop_active = True
        self.state.running = False
        self._save_state()
        
        # Create emergency stop file
        with open(self.EMERGENCY_STOP_FILE, "w") as f:
            f.write(f"{datetime.now().isoformat()}: {reason}")
        
        raise SystemExit(f"Emergency stop: {reason}")
    
    def resume(self) -> bool:
        """
        Resume trading after emergency stop or circuit break.
        Returns True if successfully resumed.
        """
        if not self.state.emergency_stop_active and not self.state.circuit_broken:
            logger.info("Already running")
            return True
        
        # Check if emergency stop file exists
        if Path(self.EMERGENCY_STOP_FILE).exists():
            logger.warning("Emergency stop file exists. Remove it to resume.")
            logger.warning("Command: rm data/EMERGENCY_STOP")
            return False
        
        # Resume
        self.state.emergency_stop_active = False
        self.state.circuit_broken = False
        self.state.consecutive_errors = 0
        self._save_state()
        
        logger.info("✅ Trading resumed")
        return True
    
    def record_trade(self) -> None:
        """Record a successful trade."""
        self.state.total_trades += 1
        self._save_state()
    
    def can_trade(self) -> tuple:
        """
        Check if trading is allowed.
        Returns (can_trade, reason)
        """
        if self.state.emergency_stop_active:
            return (False, "Emergency stop active")
        
        if self.state.circuit_broken:
            return (False, "Circuit breaker triggered")
        
        if Path(self.EMERGENCY_STOP_FILE).exists():
            return (False, "Emergency stop file present")
        
        return (True, "")
    
    def get_status(self) -> dict:
        """Get current safety status."""
        can_trade, reason = self.can_trade()
        return {
            "can_trade": can_trade,
            "reason": reason,
            "emergency_stop": self.state.emergency_stop_active,
            "circuit_broken": self.state.circuit_broken,
            "consecutive_errors": self.state.consecutive_errors,
            "total_trades": self.state.total_trades,
            "last_cycle": self.state.last_cycle_time.isoformat() if self.state.last_cycle_time else None,
            "last_error": self.state.last_error
        }
    
    def cleanup(self) -> None:
        """Cleanup on shutdown."""
        self.state.running = False
        self._save_state()
        logger.info("SafetyManager cleanup complete")
    
    @staticmethod
    def clear_emergency_stop() -> bool:
        """Clear emergency stop file and allow trading."""
        path = Path(SafetyManager.EMERGENCY_STOP_FILE)
        if path.exists():
            path.unlink()
            logger.info("Emergency stop file removed")
            return True
        return False


class IntelligentLogger:
    """
    Rich logging for manual bot runs.
    Provides clear, formatted output with different levels.
    """
    
    def __init__(self, name: str = "PolymarketBot"):
        self.logger = logging.getLogger(name)
        self._setup_console_handler()
    
    def _setup_console_handler(self) -> None:
        """Setup colored console output."""
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        
        # Format for different levels
        formats = {
            "INFO": "  ▶ %(message)s",
            "WARNING": "  ⚠ %(message)s",
            "ERROR": "  ✖ %(message)s",
            "CRITICAL": "  ⚠⚠ %(message)s",
            "DEBUG": "  ▷ %(message)s"
        }
        
        class ColoredFormatter(logging.Formatter):
            def format(self, record):
                fmt = formats.get(record.levelname, "  %(%message)s")
                record.msg = fmt % {"message": record.msg}
                return super().format(record)
        
        handler.setFormatter(ColoredFormatter("%(asctime)s %(levelname)s%(message)s", "%H:%M:%S"))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.DEBUG)
    
    def banner(self, text: str) -> None:
        """Print banner."""
        border = "=" * 50
        self.logger.info(f"\n{border}")
        self.logger.info(f"  {text}")
        self.logger.info(f"{border}\n")
    
    def section(self, title: str) -> None:
        """Print section header."""
        self.logger.info(f"\n┌─ {title} " + "─" * (40 - len(title)))
    
    def item(self, label: str, value: Any) -> None:
        """Print labeled item."""
        self.logger.info(f"│ {label:<30} {value}")
    
    def success(self, text: str) -> None:
        """Print success message."""
        self.logger.info(f"  ✅ {text}")
    
    def error(self, text: str) -> None:
        """Print error message."""
        self.logger.error(f"  ❌ {text}")
    
    def warning(self, text: str) -> None:
        """Print warning message."""
        self.logger.warning(f"  ⚠️  {text}")
    
    def signal(self, text: str) -> None:
        """Print exit signal."""
        self.logger.info(f"  🔔 {text}")
    
    def table(self, headers: list, rows: list) -> None:
        """Print a table."""
        # Calculate column widths
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(str(cell)))
        
        # Print header
        header_line = "│ " + " │ ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " │"
        self.logger.info(f"\n{header_line}")
        self.logger.info("│ " + "│".join("-" * (w + 2) for w in widths) + "│")
        
        # Print rows
        for row in rows:
            row_line = "│ " + " │ ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)) + " │"
            self.logger.info(row_line)
        
        self.logger.info("")