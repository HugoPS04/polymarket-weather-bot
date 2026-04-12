"""
Polymarket CLOB API client wrapper.
Handles authentication, order placement, and position management.
"""
import logging
from typing import Dict, List, Optional, Any
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    OrderArgs, 
    OrderType, 
    MarketOrderArgs,
    BookParams,
    DropNotificationParams,
    BalanceAllowanceParams,
    AssetType
)
from py_clob_client.order_builder.constants import BUY, SELL

from config.settings import BotSettings, get_settings

logger = logging.getLogger(__name__)


class PolymarketClient:
    """Wrapper around py_clob_client for weather trading bot."""
    
    def __init__(self, settings: Optional[BotSettings] = None):
        self.settings = settings or get_settings()
        self.client: Optional[ClobClient] = None
        self._initialized = False
        
    def initialize(self) -> None:
        """Initialize the CLOB client with authentication."""
        if self._initialized:
            return
            
        logger.info(f"Initializing Polymarket CLOB client...")
        logger.info(f"Host: {self.settings.poly_host}")
        logger.info(f"Chain ID: {self.settings.poly_chain_id}")
        logger.info(f"Signature Type: {self.settings.poly_signature_type}")
        
        try:
            self.client = ClobClient(
                host=self.settings.poly_host,
                key=self.settings.poly_private_key,
                chain_id=self.settings.poly_chain_id,
                signature_type=self.settings.poly_signature_type,
                funder=self.settings.funder_address
            )
            
            # Derive API credentials
            api_creds = self.client.create_or_derive_api_creds()
            self.client.set_api_creds(api_creds)
            
            # Verify connection by getting user address
            user_addr = self.client.get_address()
            logger.info(f"Authenticated as: {user_addr}")
            
            self._initialized = True
            logger.info("Polymarket client initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize Polymarket client: {e}")
            raise
    
    def get_balance(self) -> Dict[str, Any]:
        """Get USDC balance and allowance."""
        if not self._initialized:
            self.initialize()
        try:
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            result = self.client.get_balance_allowance(params)
            return {
                "usdc": float(result.get("balance", 0)),
                "allowance": float(result.get("allowance", 0)),
                "raw": result
            }
        except Exception as e:
            logger.warning(f"Balance check failed: {e}")
            return {"usdc": 0, "allowance": 0, "error": str(e)}
    
    def get_positions(self, market: Optional[str] = None) -> List[Dict]:
        """
        Get current positions.
        
        Args:
            market: Optional market address to filter positions
            
        Returns:
            List of position dictionaries
        """
        if not self._initialized:
            self.initialize()
        try:
            positions = self.client.get_positions(market=market)
            return positions if positions else []
        except Exception as e:
            logger.warning(f"Positions check failed: {e}")
            return []
    
    def get_order_book(self, token_id: str) -> Dict:
        """
        Get order book for a token.
        
        Args:
            token_id: Outcome token ID
            
        Returns:
            Order book with bids, asks, mid, spread
        """
        if not self._initialized:
            self.initialize()
        return self.client.get_order_book(token_id)
    
    def get_midpoint(self, token_id: str) -> float:
        """Get midpoint price for a token."""
        if not self._initialized:
            self.initialize()
        mid = self.client.get_midpoint(token_id)
        return float(mid.get("mid", 0))
    
    def get_price(self, token_id: str, side: str = "BUY") -> float:
        """Get current price for a token."""
        if not self._initialized:
            self.initialize()
        price = self.client.get_price(token_id, side)
        return float(price.get("price", 0))
    
    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str = BUY,
        order_type: str = "GTC",
        post_only: bool = False
    ) -> Dict:
        """
        Place a limit order.
        
        Args:
            token_id: Outcome token ID
            price: Order price (0.00-1.00)
            size: Number of shares
            side: BUY or SELL
            order_type: GTC, FOK, or FAK
            post_only: If True, order must rest on book (maker)
            
        Returns:
            Order response with orderID and status
        """
        if not self._initialized:
            self.initialize()
            
        logger.info(f"Placing {order_type} order: {side} {size} @ {price} (token: {token_id[:16]}...)")
        
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side
        )
        
        signed_order = self.client.create_order(order_args)
        
        # Map order type string to OrderType enum
        order_type_map = {
            "GTC": OrderType.GTC,
            "FOK": OrderType.FOK,
            "FAK": OrderType.FAK
        }
        ot = order_type_map.get(order_type, OrderType.GTC)
        
        response = self.client.post_order(
            signed_order, 
            ot,
            post_only=post_only
        )
        
        logger.info(f"Order placed: {response.get('orderID', 'N/A')} - Status: {response.get('status', 'N/A')}")
        return response
    
    def place_market_order(
        self,
        token_id: str,
        amount: float,
        side: str = BUY,
        order_type: str = "FOK"
    ) -> Dict:
        """
        Place a market order (Fill or Kill by default).
        
        Args:
            token_id: Outcome token ID
            amount: Dollar amount to spend (USDC)
            side: BUY or SELL
            order_type: FOK or FAK
            
        Returns:
            Order response
        """
        if not self._initialized:
            self.initialize()
            
        logger.info(f"Placing market order: {side} ${amount} (token: {token_id[:16]}...)")
        
        market_order = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=side,
            order_type=OrderType.FOK if order_type == "FOK" else OrderType.FAK
        )
        
        signed_order = self.client.create_market_order(market_order)
        response = self.client.post_order(signed_order, OrderType.FOK)
        
        logger.info(f"Market order placed: {response.get('orderID', 'N/A')}")
        return response
    
    def cancel_order(self, order_id: str) -> Dict:
        """Cancel a single order."""
        if not self._initialized:
            self.initialize()
        return self.client.cancel(order_id)
    
    def cancel_all(self) -> Dict:
        """Cancel all open orders."""
        if not self._initialized:
            self.initialize()
        logger.info("Cancelling all open orders")
        return self.client.cancel_all()
    
    def get_open_orders(self) -> List[Dict]:
        """Get all open orders for the user."""
        if not self._initialized:
            self.initialize()
        return self.client.get_open_orders()
    
    def get_notifications(self) -> List[Dict]:
        """Get user notifications (fills, etc.)."""
        if not self._initialized:
            self.initialize()
        return self.client.get_notifications()
    
    def drop_notification(self, notification_id: str) -> None:
        """Mark a notification as read."""
        if not self._initialized:
            self.initialize()
        self.client.drop_notifications(DropNotificationParams([notification_id]))
    
    def get_market_orders(self, market: str) -> List[Dict]:
        """Get orders for a specific market."""
        if not self._initialized:
            self.initialize()
        return self.client.get_orders(market=market)
    
    def is_initialized(self) -> bool:
        """Check if client is initialized."""
        return self._initialized
