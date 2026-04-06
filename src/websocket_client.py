"""
Polymarket WebSocket client for real-time price streaming.
Connects to CLOB WebSocket for live orderbook updates.
"""
import asyncio
import json
import logging
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass
from datetime import datetime

import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

from config.settings import BotSettings, get_settings

logger = logging.getLogger(__name__)


@dataclass
class PriceUpdate:
    """Real-time price update from WebSocket."""
    token_id: str
    best_bid: float
    best_ask: float
    midpoint: float
    spread: float
    volume_24h: float
    timestamp: datetime


class PolymarketWebSocket:
    """
    WebSocket client for Polymarket market data streaming.
    
    Connects to: wss://ws-subscriptions-clob.polymarket.com/ws/market
    
    Features:
    - Subscribe to multiple token IDs
    - Automatic reconnection with backoff
    - Heartbeat/PING handling
    - Callback-based event handling
    """
    
    WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    HEARTBEAT_INTERVAL = 10  # seconds
    
    def __init__(self, settings: Optional[BotSettings] = None):
        self.settings = settings or get_settings()
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self.subscribed_tokens: set = set()
        self.callbacks: List[Callable[[PriceUpdate], None]] = []
        self.reconnect_delay = 5
        self.max_reconnect_delay = 60
        self.last_message_time: Optional[datetime] = None
        
    def add_callback(self, callback: Callable[[PriceUpdate], None]) -> None:
        """
        Add callback for price updates.
        
        Args:
            callback: Function to call with PriceUpdate object
        """
        self.callbacks.append(callback)
        logger.debug(f"Added callback, total: {len(self.callbacks)}")
    
    def remove_callback(self, callback: Callable[[PriceUpdate], None]) -> None:
        """Remove a callback."""
        if callback in self.callbacks:
            self.callbacks.remove(callback)
    
    async def connect(self) -> None:
        """Establish WebSocket connection."""
        try:
            logger.info(f"Connecting to Polymarket WebSocket: {self.WS_URL}")
            self.websocket = await websockets.connect(
                self.WS_URL,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                max_size=10 * 1024 * 1024  # 10MB max message size
            )
            logger.info("WebSocket connected")
            
            # Resubscribe to tokens if reconnecting
            if self.subscribed_tokens:
                await self._resubscribe()
                
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            raise
    
    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        self.running = False
        if self.websocket:
            await self.websocket.close()
            logger.info("WebSocket disconnected")
    
    async def subscribe(self, token_ids: List[str]) -> None:
        """
        Subscribe to price updates for token IDs.
        
        Args:
            token_ids: List of outcome token IDs to track
        """
        if not self.websocket:
            logger.warning("Cannot subscribe: not connected")
            return
        
        subscribe_message = {
            "type": "market",
            "assets_ids": token_ids,
            "custom_feature_enabled": True  # Enable best_bid_ask events
        }
        
        await self.websocket.send(json.dumps(subscribe_message))
        self.subscribed_tokens.update(token_ids)
        
        logger.info(f"Subscribed to {len(token_ids)} tokens (total: {len(self.subscribed_tokens)})")
    
    async def unsubscribe(self, token_ids: List[str]) -> None:
        """Unsubscribe from token IDs."""
        if not self.websocket:
            return
        
        # Note: Polymarket WS doesn't have explicit unsubscribe
        # We track locally and reconnect to reset if needed
        self.subscribed_tokens.difference_update(token_ids)
        logger.info(f"Unsubscribed from {len(token_ids)} tokens")
    
    async def _resubscribe(self) -> None:
        """Resubscribe to all tokens after reconnection."""
        if self.subscribed_tokens and self.websocket:
            await self.subscribe(list(self.subscribed_tokens))
    
    async def _send_heartbeat(self) -> None:
        """Send PING to keep connection alive."""
        while self.running and self.websocket:
            try:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                if self.websocket:
                    await self.websocket.send("PING")
                    logger.debug("Heartbeat PING sent")
            except Exception as e:
                logger.warning(f"Heartbeat failed: {e}")
                break
    
    async def _handle_message(self, message: str) -> None:
        """
        Parse and handle incoming WebSocket message.
        
        Args:
            message: Raw JSON message from WebSocket
        """
        self.last_message_time = datetime.now()
        
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.debug(f"Non-JSON message: {message[:100]}")
            return
        
        # Handle PONG response
        if data == "PONG":
            logger.debug("Received PONG")
            return
        
        # Handle subscription confirmation
        if data.get("type") == "subscribed":
            logger.debug(f"Subscription confirmed: {data}")
            return
        
        # Handle best_bid_ask events (most useful for trading)
        if data.get("type") == "best_bid_ask":
            update = self._parse_best_bid_ask(data)
            if update:
                await self._notify_callbacks(update)
            return
        
        # Handle orderbook updates
        if data.get("type") == "book":
            update = self._parse_book_update(data)
            if update:
                await self._notify_callbacks(update)
            return
        
        # Handle trade events
        if data.get("type") == "trade":
            logger.debug(f"Trade event: {data}")
            return
        
        # Handle new market events
        if data.get("type") == "new_market":
            logger.info(f"New market detected: {data.get('market', 'unknown')}")
            return
        
        # Handle market resolution
        if data.get("type") == "market_resolved":
            logger.info(f"Market resolved: {data}")
            return
        
        logger.debug(f"Unhandled message type: {data.get('type')}")
    
    def _parse_best_bid_ask(self, data: Dict) -> Optional[PriceUpdate]:
        """Parse best_bid_ask event into PriceUpdate."""
        try:
            token_id = data.get("asset_id")
            if not token_id:
                return None
            
            best_bid = float(data.get("best_bid", 0))
            best_ask = float(data.get("best_ask", 0))
            midpoint = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
            spread = best_ask - best_bid if best_bid and best_ask else 0
            
            update = PriceUpdate(
                token_id=token_id,
                best_bid=best_bid,
                best_ask=best_ask,
                midpoint=midpoint,
                spread=spread,
                volume_24h=float(data.get("volume_24h", 0)),
                timestamp=datetime.now()
            )
            
            logger.debug(
                f"Price update: {token_id[:16]}... bid={best_bid:.3f} ask={best_ask:.3f} "
                f"mid={midpoint:.3f} spread={spread:.3f}"
            )
            
            return update
            
        except Exception as e:
            logger.warning(f"Failed to parse best_bid_ask: {e}")
            return None
    
    def _parse_book_update(self, data: Dict) -> Optional[PriceUpdate]:
        """Parse full book update into PriceUpdate."""
        try:
            token_id = data.get("asset_id")
            if not token_id:
                return None
            
            book = data.get("book", {})
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            
            best_bid = float(bids[0]["price"]) if bids else 0
            best_ask = float(asks[0]["price"]) if asks else 0
            midpoint = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
            spread = best_ask - best_bid if best_bid and best_ask else 0
            
            update = PriceUpdate(
                token_id=token_id,
                best_bid=best_bid,
                best_ask=best_ask,
                midpoint=midpoint,
                spread=spread,
                volume_24h=float(data.get("volume_24h", 0)),
                timestamp=datetime.now()
            )
            
            return update
            
        except Exception as e:
            logger.warning(f"Failed to parse book update: {e}")
            return None
    
    async def _notify_callbacks(self, update: PriceUpdate) -> None:
        """Notify all callbacks of price update."""
        for callback in self.callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(update)
                else:
                    callback(update)
            except Exception as e:
                logger.error(f"Callback error: {e}")
    
    async def run(self, token_ids: List[str]) -> None:
        """
        Main run loop with automatic reconnection.
        
        Args:
            token_ids: List of token IDs to subscribe to
        """
        self.running = True
        reconnect_count = 0
        
        while self.running:
            try:
                # Connect
                await self.connect()
                reconnect_count = 0  # Reset on successful connection
                
                # Subscribe
                await self.subscribe(token_ids)
                
                # Start heartbeat task
                heartbeat_task = asyncio.create_task(self._send_heartbeat())
                
                # Message processing loop
                async for message in self.websocket:
                    if not self.running:
                        break
                    await self._handle_message(message)
                
                # Cancel heartbeat
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
                
            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e.code} {e.reason}")
                heartbeat_task.cancel()
                
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            
            # Reconnection logic
            if self.running:
                reconnect_count += 1
                delay = min(
                    self.reconnect_delay * (2 ** (reconnect_count - 1)),
                    self.max_reconnect_delay
                )
                logger.info(f"Reconnecting in {delay:.1f}s (attempt {reconnect_count})")
                await asyncio.sleep(delay)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get connection statistics."""
        return {
            "connected": self.websocket is not None and self.websocket.open,
            "subscribed_tokens": len(self.subscribed_tokens),
            "callbacks": len(self.callbacks),
            "last_message": self.last_message_time.isoformat() if self.last_message_time else None,
            "reconnect_delay": self.reconnect_delay
        }


class WebSocketPriceMonitor:
    """
    High-level price monitor using WebSocket.
    Tracks prices for monitored tokens and triggers alerts.
    """
    
    def __init__(self, settings: Optional[BotSettings] = None):
        self.settings = settings or get_settings()
        self.ws = PolymarketWebSocket(settings)
        self.token_prices: Dict[str, PriceUpdate] = {}
        self.price_alerts: List[Callable[[str, PriceUpdate], None]] = []
        
        # Register callback
        self.ws.add_callback(self._on_price_update)
    
    def _on_price_update(self, update: PriceUpdate) -> None:
        """Handle incoming price update."""
        self.token_prices[update.token_id] = update
        
        # Check for alerts
        for alert_fn in self.price_alerts:
            try:
                alert_fn(update.token_id, update)
            except Exception as e:
                logger.error(f"Price alert error: {e}")
    
    def add_price_alert(
        self, 
        alert_fn: Callable[[str, PriceUpdate], None]
    ) -> None:
        """Add price alert callback."""
        self.price_alerts.append(alert_fn)
    
    def get_price(self, token_id: str) -> Optional[float]:
        """Get current midpoint price for token."""
        if token_id in self.token_prices:
            return self.token_prices[token_id].midpoint
        return None
    
    def get_spread(self, token_id: str) -> Optional[float]:
        """Get current spread for token."""
        if token_id in self.token_prices:
            return self.token_prices[token_id].spread
        return None
    
    def get_all_prices(self) -> Dict[str, float]:
        """Get all current prices."""
        return {
            token_id: update.midpoint
            for token_id, update in self.token_prices.items()
        }
    
    async def start_monitoring(self, token_ids: List[str]) -> None:
        """Start monitoring token prices."""
        logger.info(f"Starting price monitoring for {len(token_ids)} tokens")
        await self.ws.run(token_ids)
    
    def stop_monitoring(self) -> None:
        """Stop monitoring."""
        self.ws.running = False


# Example usage and testing
async def demo():
    """Demo WebSocket client."""
    logging.basicConfig(level=logging.INFO)
    
    ws = PolymarketWebSocket()
    
    def on_price(update: PriceUpdate):
        print(f"📊 {update.token_id[:16]}... | "
              f"Bid: {update.best_bid:.3f} | "
              f"Ask: {update.best_ask:.3f} | "
              f"Mid: {update.midpoint:.3f} | "
              f"Spread: {update.spread:.3f}")
    
    ws.add_callback(on_price)
    
    # Example token IDs (replace with real ones from market scanner)
    test_tokens = [
        "21742633143463906290569050155826241533067272736897614950488156847949938836455",
    ]
    
    print("Starting WebSocket demo (Ctrl+C to stop)...")
    try:
        await ws.run(test_tokens)
    except KeyboardInterrupt:
        await ws.disconnect()
        print("\nDemo stopped")


if __name__ == "__main__":
    asyncio.run(demo())
