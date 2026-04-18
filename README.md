# Polymarket Weather Trading Bot

Autonomous trading bot that analyzes weather forecasts and trades on Polymarket prediction markets.

## Features

- **Market Scanning**: Automatically discovers weather-related markets on Polymarket
- **Weather Analysis**: 
  - **NOAA NWS** (default for US markets) - Free, no API key, official US National Weather Service data
  - **Open-Meteo** (global fallback) - Free, no API key
  - **OpenWeatherMap** (optional) - Requires API key
- **Edge Detection**: Compares weather model probabilities to market prices
- **Kelly Criterion Sizing**: Mathematical position sizing for optimal capital allocation
- **Automated Trading**: Places limit orders when edge exceeds threshold
- **Real-Time Prices**: WebSocket streaming for instant price updates and trade triggers
- **Position Management**: Tracks positions, P&L, and exposure limits
- **VPS Ready**: Designed for 24/7 deployment with systemd

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Weather APIs   │────▶│  Trading Bot     │────▶│  Polymarket     │
│  (Open-Meteo,   │     │  (Python)        │     │  (CLOB API)     │
│   OpenWeather)  │     │                  │     │                 │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                              │
                              ▼
                       ┌──────────────────┐
                       │  Position Store  │
                       │  (JSON/SQLite)   │
                       └──────────────────┘
```

## VPS Quickstart

Assuming the bot is already installed and running as a systemd service:

```bash
# Check if the bot is running
sudo systemctl status polymarket-weather-bot

# View live logs
sudo journalctl -u polymarket-weather-bot -f

# Or view the bot log file
tail -f logs/bot.log

# Stop the bot
sudo systemctl stop polymarket-weather-bot

# Close all positions before stopping
python main.py sell-all --execute
sudo systemctl stop polymarket-weather-bot
```

---

## Quick Start

### Option A: Automated VPS Install

```bash
# One-line install on Ubuntu/Debian VPS
curl -sSL https://raw.githubusercontent.com/yourusername/polymarket-weather-bot/main/deploy/install.sh | bash
```

### Option B: Manual Install

### 1. Install Dependencies

```bash
cd polymarket-weather-bot
pip install -r requirements.txt
```

### 2. Configure

```bash
cp config/config.example.env .env
# Edit .env with your settings
```

Required settings:
- `POLY_PRIVATE_KEY`: Your Polygon wallet private key
- `POLY_FUNDER_ADDRESS`: Your wallet address (for signature_type=2)
- `WEATHER_LOCATIONS`: Comma-separated list of `name:lat:lon`

Weather API (US markets):
- `WEATHER_API_SOURCE=noaa` (default) - Uses NOAA National Weather Service (US only, free)
- Falls back to Open-Meteo automatically if NOAA fails
- For global markets: `WEATHER_API_SOURCE=openweathermap` (requires API key)

### 3. Test Connection

```bash
# Check Polymarket balance
python main.py balance

# Scan weather markets
python main.py scan

# Test weather API
python main.py weather
```

### 4. Run (Dry Mode)

By default, `LIVE_TRADING=false` - bot will analyze but not place real trades.

```bash
python main.py run
```

### 5. Enable Live Trading

Set `LIVE_TRADING=true` in `.env`:

```bash
python main.py run
```

## Commands

```bash
# Continuous trading loop (polling mode)
python main.py run

# Single cycle (for cron/testing)
python main.py run --once

# WebSocket mode (real-time price streaming + instant trade triggers)
python main.py run --websocket

# Check balance
python main.py balance

# Scan markets
python main.py scan --limit 50

# Test weather API
python main.py weather

# Test WebSocket streaming standalone
python main.py ws

# Close ALL positions and cancel all orders
python main.py sell-all --execute

# Check current positions
python main.py positions

# Check bot status
python main.py status
```

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `POLY_PRIVATE_KEY` | - | Polygon wallet private key |
| `POLY_SIGNATURE_TYPE` | 2 | 0=EOA, 1=Email, 2=Browser/Safe |
| `MAX_POSITION_SIZE` | 100 | Max USDC per position |
| `MAX_TOTAL_EXPOSURE` | 500 | Max total USDC exposure |
| `MIN_EDGE_THRESHOLD` | 0.05 | Minimum edge (5%) to trade |
| `ORDER_TYPE` | GTC | Order type: GTC, FOK, or FAK |
| `KELLY_ENABLED` | true | Use Kelly Criterion for position sizing |
| `KELLY_FRACTION` | 0.25 | Fraction of Kelly to bet (0.25 = quarter Kelly) |
| `KELLY_MIN_BET` | 0.5 | Minimum bet size (USDC) |
| `KELLY_MAX_BET` | 10 | Maximum bet size (USDC) |
| `WEATHER_API_SOURCE` | noaa | `noaa` (US), `openweathermap`, or `openmeteo` |
| `WEATHER_LOCATIONS` | Miami | Locations to monitor (name:lat:lon) |
| `CHECK_INTERVAL` | 60 | Seconds between cycles |
| `LIVE_TRADING` | false | Enable real trading |

## Kelly Criterion Position Sizing

The bot uses the Kelly Criterion to calculate optimal bet sizes:

**Formula:** `f* = (bp - q) / b`

Where:
- **b** = odds received (payout ratio)
- **p** = probability of winning (our model probability)
- **q** = probability of losing (1 - p)

**Example:**
```
Model probability: 65%
Market price: 10% (implies 9x odds)

b = 9, p = 0.65, q = 0.35
Kelly = (9 × 0.65 - 0.35) / 9 = 0.622

With 0.25 fraction: 0.155 × max_position = ~$15.50 bet
```

The Kelly fraction (default 0.25) reduces volatility risk. A fraction of 0.25 means you bet only 25% of what full Kelly would suggest.

## VPS Deployment

### Systemd Service

Create `/etc/systemd/system/polymarket-weather-bot.service`:

```ini
[Unit]
Description=Polymarket Weather Trading Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/polymarket-weather-bot
Environment="PATH=/home/ubuntu/.local/bin:/usr/bin:/bin"
ExecStart=/home/ubuntu/.local/bin/python main.py run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable polymarket-weather-bot
sudo systemctl start polymarket-weather-bot
sudo systemctl status polymarket-weather-bot
```

### Cron Mode (Alternative)

For periodic execution instead of continuous loop:

```bash
# Run every hour
0 * * * * cd /home/ubuntu/polymarket-weather-bot && /home/ubuntu/.local/bin/python main.py run --once >> logs/cron.log 2>&1
```

## Project Structure

```
polymarket-weather-bot/
├── main.py                 # Entry point + CLI commands
├── requirements.txt        # Python dependencies
├── config/
│   ├── settings.py         # Configuration management
│   └── config.example.env  # Example environment file
├── src/
│   ├── __init__.py
│   ├── polymarket_client.py   # Polymarket API wrapper
│   ├── weather_client.py      # Weather API client
│   ├── market_scanner.py     # Market discovery
│   ├── trading_bot.py        # Main bot logic + Kelly sizing
│   └── intelligent_logger.py # Formatted console output
├── data/                   # Position storage
├── logs/                   # Log files
└── deploy/
    └── systemd.service     # Systemd service template
```

## Trading Logic

The bot finds opportunities by:

1. **Scanning** Polymarket for weather markets (rain, temperature, storms)
2. **Fetching** weather forecasts for monitored locations
3. **Calculating** model probability from forecast data
4. **Comparing** model probability to market price
5. **Trading** when edge exceeds threshold using Kelly sizing

### WebSocket Trading

The bot supports two modes:

**Polling Mode** (default):
- Checks prices every `CHECK_INTERVAL` seconds (default: 60s)
- Good for low-frequency strategies
- Lower resource usage

**WebSocket Mode** (real-time):
- Streams live price updates from Polymarket
- Triggers trades instantly on price movements
- Auto-subscribes to tokens from scanned markets
- Detects 2%+ price movements with alerts

Enable WebSocket trading by running:
```bash
python main.py run --websocket
```

Or test WebSocket streaming standalone:
```bash
python main.py ws
```

### Console Output

The bot uses formatted banners for important events:

```
╔══════════════════════════════════════╗
║  📌 POSITION OPENED                  ║
╠══════════════════════════════════════╣
║  Market:  Will it rain in Miami t...  ║
║  Outcome:  YES                       ║
║  Entry price:  $0.35                 ║
║  Size:  $12.50                       ║
║  Model prob:  65%                    ║
║  Edge:  30%                          ║
╚══════════════════════════════════════╝

╔══════════════════════════════════════╗
║  💰 EXIT SIGNAL                      ║
╠══════════════════════════════════════╣
║  Reason:  Weather condition change    ║
║  P&L:  +23.5%                        ║
╚══════════════════════════════════════╝
```

Example trade:
- Market: "Will it rain in Miami tomorrow?" 
- Market price: YES @ 40%
- Weather model: 65% chance of rain
- Edge: 25% → **BUY YES** (Kelly suggests ~$15.50 with 0.25 fraction)

## Risk Management

- **Kelly Criterion**: Mathematical position sizing for long-term growth
- **Position limits**: Max $100 per position, $500 total
- **Edge threshold**: Only trade when model has >5% edge
- **Dry run mode**: Test without real money
- **Logging**: All trades logged for audit

## API Rate Limits

| API | Limit |
|-----|-------|
| Polymarket CLOB | 9,000 req/10s |
| Polymarket Gamma | 4,000 req/10s |
| NOAA NWS (api.weather.gov) | Free, generous limit (~5s retry on exceed) |
| Open-Meteo | Free, no limit |

Bot respects rate limits with built-in retry logic. NOAA recommends including contact info in User-Agent header.

## Security

⚠️ **Never commit your `.env` file!**

- Private keys stored only in `.env` (gitignored)
- API credentials derived at runtime
- No keys logged or transmitted

## Development

### Add New Weather Signal

Edit `src/trading_bot.py` → `_calculate_market_probability()`:

```python
elif "humidity" in question_lower:
    avg_humidity = forecast.get("avg_humidity", 50)
    model_prob = avg_humidity / 100
    reasoning = f"Avg humidity: {avg_humidity}%"
    return (model_prob, reasoning)
```

### Add New Market Type

Edit `src/market_scanner.py` → `_is_weather_market()`:

```python
weather_keywords.extend(["humidity", "wind", "pressure"])
```

## Troubleshooting

### "401 Unauthorized"
- Check `POLY_PRIVATE_KEY` is correct
- Verify `POLY_SIGNATURE_TYPE` matches your wallet
- Re-run to derive fresh API credentials

### "No weather markets found"
- Check Polymarket has active weather markets
- Increase scan limit: `python main.py scan --limit 100`

### "API rate limited"
- Bot has built-in retry logic
- Increase `CHECK_INTERVAL` in config

## VPS Deployment

See `deploy/VPS_SETUP.md` for complete deployment guide.

**Quick deploy:**
```bash
# Automated install
curl -sSL https://raw.githubusercontent.com/yourusername/polymarket-weather-bot/main/deploy/install.sh | bash

# Or manual setup
sudo systemctl enable polymarket-weather-bot
sudo systemctl start polymarket-weather-bot
```

**Monitor:**
```bash
sudo journalctl -u polymarket-weather-bot -f
tail -f logs/bot.log
```

### Stopping the Bot

```bash
# Stop the service
sudo systemctl stop polymarket-weather-bot

# Disable auto-start (optional)
sudo systemctl disable polymarket-weather-bot
```

If you need to close all positions before stopping:
```bash
# Close all positions and cancel orders
python main.py sell-all --execute

# Then stop the service
sudo systemctl stop polymarket-weather-bot
```

## License

MIT

## Disclaimer

Not financial advice. Trade at your own risk. Test thoroughly before using real funds.

**Always start with `LIVE_TRADING=false` and monitor for 24-48 hours before enabling real trading.**
