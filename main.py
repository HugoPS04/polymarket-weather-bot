#!/usr/bin/env python3
"""
Polymarket Weather Trading Bot

Entry point for running the bot.

Usage:
    python main.py              # Run continuous trading loop
    python main.py --once       # Run single cycle (for cron/testing)
    python main.py --balance    # Check balance only
    python main.py --scan       # Scan markets only
"""
import argparse
import asyncio
import logging
import sys
import json
from pathlib import Path
from datetime import datetime

from config.settings import get_settings
from src.trading_bot import WeatherTradingBot
from src.polymarket_client import PolymarketClient
from src.market_scanner import MarketScanner


def setup_logging(settings):
    """Configure logging."""
    log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    
    # Ensure log directory exists
    log_path = Path(settings.log_file)
    log_path.parent.mkdir(exist_ok=True, parents=True)
    
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper()),
        format=log_format,
        handlers=[
            logging.FileHandler(settings.log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Reduce noise from external libraries
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("py_clob_client").setLevel(logging.INFO)


async def cmd_run_async(args):
    """Run the trading bot (async version for websocket mode)."""
    import asyncio
    from src.websocket_client import PolymarketWebSocket, PriceUpdate
    
    settings = get_settings()
    setup_logging(settings)
    
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("Polymarket Weather Trading Bot")
    logger.info("=" * 60)
    
    bot = WeatherTradingBot(settings)
    
    if args.once:
        logger.info("Running single cycle...")
        result = bot.run_once()
        print(json.dumps(result, indent=2))
    elif args.websocket:
        logger.info("Starting WebSocket real-time mode...")
        logger.info(f"Live trading: {settings.live_trading}")
        bot.initialize()
        
        # Initial market scan to subscribe to tokens
        markets = bot.market_scanner.get_weather_markets(limit=50)
        bot._subscribe_to_websocket(markets)
        
        # Run WebSocket in background, trading cycle in foreground
        async def run_websocket():
            await bot.ws_monitor.ws.run(list(bot.tracked_tokens))
        
        ws_task = asyncio.create_task(run_websocket())
        
        try:
            while bot.running:
                bot._run_cycle()
                await asyncio.sleep(settings.check_interval)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            bot.stop()
            ws_task.cancel()
        finally:
            await bot.ws_monitor.ws.disconnect()
    else:
        logger.info("Starting continuous trading loop (polling mode)...")
        bot.start()


def cmd_run(args):
    """Run the trading bot."""
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    if args.websocket:
        asyncio.run(cmd_run_async(args))
    else:
        asyncio.run(cmd_run_async(args))


def cmd_balance(args):
    """Check Polymarket balance."""
    settings = get_settings()
    setup_logging(settings)
    
    client = PolymarketClient(settings)
    client.initialize()
    
    balance = client.get_balance()
    print(f"\nUSDC Balance: ${balance.get('usdc', 0):,.2f}")
    print(f"Polygon Address: {client.client.get_address()}")


def cmd_scan(args):
    """Scan for weather markets."""
    settings = get_settings()
    setup_logging(settings)
    
    scanner = MarketScanner(settings)
    markets = scanner.get_weather_markets(limit=args.limit)
    
    print(f"\nFound {len(markets)} weather markets:\n")
    
    for i, m in enumerate(markets[:args.limit], 1):
        print(f"{i}. {m.question}")
        print(f"   Volume: ${m.volume:,.0f} | Liquidity: ${m.liquidity:,.0f}")
        print(f"   Outcomes: {m.outcomes}")
        print(f"   Prices: {[f'{p:.2%}' for p in m.outcome_prices]}")
        print(f"   Ends: {m.end_date}")
        print()


def cmd_weather(args):
    """Test weather API with consensus analysis."""
    settings = get_settings()
    setup_logging(settings)
    
    from src.consensus_engine import ConsensusEngine
    
    engine = ConsensusEngine(settings)
    
    print("\n🌡️ Weather Consensus Analysis")
    print("="*60)
    
    for loc in settings.parsed_locations:
        print(f"\n📍 {loc.name} ({loc.lat}, {loc.lon})")
        print("-"*40)
        
        # Get consensus forecast
        consensus = engine.weather_client.get_consensus_forecast(loc, days=7)
        
        if not consensus:
            print("  No data available")
            continue
        
        # Show next 3 days
        for i, (date, forecast) in enumerate(list(consensus.items())[:3]):
            print(f"\n  📅 {date}:")
            print(f"     Temp: {forecast.min_temp_f:.0f}-{forecast.max_temp_f:.0f}°F "
                  f"(avg: {forecast.avg_temp_f:.0f}°F)")
            print(f"     Precip: {forecast.avg_precip_prob:.0f}% chance")
            print(f"     APIs: {len(forecast.forecasts)} sources "
                  f"(agreement: {forecast.temp_agreement:.0%})")
            print(f"     Sources: {[f.source for f in forecast.forecasts]}")
        
        print()


def cmd_ws(args):
    """Test WebSocket streaming."""
    import asyncio
    from src.websocket_client import PolymarketWebSocket, PriceUpdate
    
    settings = get_settings()
    setup_logging(settings)
    
    ws = PolymarketWebSocket(settings)
    
    def on_price(update: PriceUpdate):
        print(f"\r📊 {update.token_id[:16]}... | "
              f"Bid: {update.best_bid:.3f} | "
              f"Ask: {update.best_ask:.3f} | "
              f"Mid: {update.midpoint:.3f} | "
              f"Spread: {update.spread:.3f}    ", end="")
    
    ws.add_callback(on_price)
    
    # Get some token IDs from markets first
    scanner = MarketScanner(settings)
    markets = scanner.get_weather_markets(limit=5)
    
    if not markets:
        print("No weather markets found to test with")
        return
    
    token_ids = []
    for m in markets[:5]:
        token_ids.extend(m.clob_token_ids[:2])  # YES and NO tokens
    
    print(f"Subscribing to {len(token_ids)} tokens from {len(markets)} markets...")
    print("Press Ctrl+C to stop\n")
    
    async def run_ws():
        await ws.run(token_ids)
    
    try:
        asyncio.run(run_ws())
    except KeyboardInterrupt:
        asyncio.run(ws.disconnect())
        print("\n\nWebSocket test stopped")


def main():
    parser = argparse.ArgumentParser(description="Polymarket Weather Trading Bot")
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Run command
    run_parser = subparsers.add_parser("run", help="Run the trading bot")
    run_parser.add_argument("--once", action="store_true", help="Run single cycle only")
    run_parser.add_argument("--websocket", action="store_true", help="Enable WebSocket real-time mode")
    run_parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    run_parser.set_defaults(func=cmd_run)
    
    # Balance command
    balance_parser = subparsers.add_parser("balance", help="Check Polymarket balance")
    balance_parser.set_defaults(func=cmd_balance)
    
    # Scan command
    scan_parser = subparsers.add_parser("scan", help="Scan weather markets")
    scan_parser.add_argument("--limit", type=int, default=20, help="Max markets to show")
    scan_parser.set_defaults(func=cmd_scan)
    
    # Weather command
    weather_parser = subparsers.add_parser("weather", help="Test weather API with consensus")
    weather_parser.set_defaults(func=cmd_weather)
    
    # Consensus command (alias for weather)
    consensus_parser = subparsers.add_parser("consensus", help="Show weather consensus analysis")
    consensus_parser.set_defaults(func=cmd_weather)
    
    # WebSocket command
    ws_parser = subparsers.add_parser("ws", help="Test WebSocket streaming")
    ws_parser.set_defaults(func=cmd_ws)
    
    args = parser.parse_args()
    
    if args.command is None:
        # Default to run
        args.once = False
        cmd_run(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
