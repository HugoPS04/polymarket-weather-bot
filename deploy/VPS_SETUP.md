# VPS Deployment Guide

Complete guide for deploying the Polymarket Weather Trading Bot to a VPS.

## Quick Start (Automated)

```bash
# One-line install (Ubuntu/Debian)
curl -sSL https://raw.githubusercontent.com/yourusername/polymarket-weather-bot/main/deploy/install.sh | bash
```

Then follow the on-screen instructions.

---

## Manual Installation

## Prerequisites

- Ubuntu 22.04+ VPS (or similar Linux)
- SSH access
- Root or sudo privileges
- Polymarket wallet with USDC on Polygon

## 1. Server Setup

### Update System

```bash
sudo apt update && sudo apt upgrade -y
```

### Install Python & Dependencies

```bash
# Install Python 3.10+
sudo apt install -y python3 python3-pip python3-venv git curl

# Install pip for user
curl -sS https://bootstrap.pypa.io/get-pip.py | python3 -
```

### Create User (if needed)

```bash
# Create dedicated user for bot
sudo useradd -m -s /bin/bash botuser
sudo usermod -aG sudo botuser
```

## 2. Clone & Install

### Clone Repository

```bash
# As botuser
git clone <your-repo-url> /home/botuser/polymarket-weather-bot
cd /home/botuser/polymarket-weather-bot
```

### Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Configure Environment

```bash
cp config/config.example.env .env
nano .env  # Edit with your settings
```

**Critical settings to configure:**
```bash
# Polymarket Authentication
POLY_PRIVATE_KEY=your_private_key_here
POLY_FUNDER_ADDRESS=your_wallet_address
POLY_SIGNATURE_TYPE=2

# Weather API (NOAA for US markets - free, no key needed)
WEATHER_API_SOURCE=noaa

# US Cities to monitor (name:lat:lon, comma-separated)
WEATHER_LOCATIONS=Miami:25.7617:-80.1918,NewYork:40.7128:-74.0060,LA:34.0522:-118.2437,Atlanta:33.7490:-84.3880,Dallas:32.7767:-96.7970,Denver:39.7392:-104.9903,Austin:30.2672:-97.7431

# Trading
LIVE_TRADING=false  # Start with false!
MIN_EDGE_THRESHOLD=0.05  # 5% edge minimum
MAX_POSITION_SIZE=100  # USDC per trade
MAX_TOTAL_EXPOSURE=500  # Total USDC exposure

# Bot
CHECK_INTERVAL=60  # Seconds between cycles
```

### Test Installation

```bash
# Test balance check
./venv/bin/python main.py balance

# Test market scan
./venv/bin/python main.py scan --limit 10

# Test weather API
./venv/bin/python main.py weather
```

## 3. Systemd Service Setup

### Create Service File

```bash
sudo nano /etc/systemd/system/polymarket-weather-bot.service
```

Paste the contents of `deploy/systemd.service`, adjusting:
- `User=` to your username
- `WorkingDirectory=` to your path
- `ExecStart=` to your Python path

### Find Python Path

```bash
# Get full path to Python in venv
which python
# Output: /home/botuser/polymarket-weather-bot/venv/bin/python
```

### Enable & Start Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable polymarket-weather-bot
sudo systemctl start polymarket-weather-bot
```

### Check Status

```bash
# Service status
sudo systemctl status polymarket-weather-bot

# View logs
sudo journalctl -u polymarket-weather-bot -f

# Last 50 lines
sudo journalctl -u polymarket-weather-bot -n 50
```

## 4. Monitoring

### Log Files

Bot logs to `logs/bot.log`:

```bash
# Follow logs
tail -f logs/bot.log

# Search for errors
grep ERROR logs/bot.log

# Today's logs
grep "$(date +%Y-%m-%d)" logs/bot.log
```

### Systemd Logs

```bash
# Real-time logs
sudo journalctl -u polymarket-weather-bot -f

# Last hour
sudo journalctl -u polymarket-weather-bot --since "1 hour ago"

# Export logs
sudo journalctl -u polymarket-weather-bot > bot-logs.txt
```

### Position Tracking

```bash
# View current positions
cat data/positions.json | python3 -m json.tool
```

## 5. Going Live

### 1. Start Dry Run

Ensure `LIVE_TRADING=false` in `.env`:

```bash
sudo systemctl restart polymarket-weather-bot
```

Monitor for 24-48 hours. Check logs for:
- Opportunities detected
- Would-have trades
- No errors

### 2. Enable Live Trading

```bash
# Edit config
nano .env

# Change:
LIVE_TRADING=true

# Restart
sudo systemctl restart polymarket-weather-bot
```

### 3. Monitor First Trades

```bash
# Watch for trade execution
tail -f logs/bot.log | grep -E "(Order placed|Trade|OPPORTUNITY)"

# Check Polymarket directly
# https://polymarket.com/portfolio
```

## 6. Maintenance

### Update Bot

```bash
cd /home/botuser/polymarket-weather-bot
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart polymarket-weather-bot
```

### Rotate Logs

Create `/etc/logrotate.d/polymarket-bot`:

```
/home/botuser/polymarket-weather-bot/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 botuser botuser
}
```

### Backup Positions

```bash
# Cron job to backup positions
0 0 * * * cp /home/botuser/polymarket-weather-bot/data/positions.json /backup/positions-$(date +\%Y-\%m-\%d).json
```

## 7. Troubleshooting

### Service Won't Start

```bash
# Check service file syntax
sudo systemd-analyze verify /etc/systemd/system/polymarket-weather-bot.service

# Check Python path
ls -la /home/botuser/polymarket-weather-bot/venv/bin/python

# Test manually
cd /home/botuser/polymarket-weather-bot
./venv/bin/python main.py balance
```

### "401 Unauthorized" Errors

1. Verify `POLY_PRIVATE_KEY` in `.env`
2. Check `POLY_SIGNATURE_TYPE` matches wallet type
3. Restart service to re-derive credentials

```bash
sudo systemctl restart polymarket-weather-bot
```

### High Memory Usage

```bash
# Check memory
ps aux | grep python

# Add to systemd service:
MemoryLimit=512M
```

### Network Issues

```bash
# Test connectivity
curl https://clob.polymarket.com
curl https://api.open-meteo.com

# Check DNS
dig clob.polymarket.com
```

## 8. Security Hardening

### Firewall

```bash
# Only allow SSH
sudo ufw default deny incoming
sudo ufw allow ssh
sudo ufw enable
```

### Private Key Security

```bash
# Restrict .env permissions
chmod 600 .env
chown botuser:botuser .env
```

### Automatic Security Updates

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

## 9. Recommended VPS Specs

| Spec | Minimum | Recommended |
|------|---------|-------------|
| CPU | 1 core | 2 cores |
| RAM | 512 MB | 1 GB |
| Storage | 5 GB | 10 GB |
| Network | 1 Gbps | 1 Gbps |

**Providers:**
- DigitalOcean Droplet ($6/mo)
- Linode Nanode ($5/mo)
- AWS t3.micro (free tier eligible)
- Hetzner CX11 (€5/mo)

## 10. How It Works

### Trading Logic

1. **Scan Markets**: Bot scans Polymarket for weather-related markets
2. **Fetch Forecasts**: Gets NOAA weather forecasts for configured US cities
3. **Calculate Edge**: Compares NOAA forecast probability to market prices
4. **Execute Trades**: Buys YES/NO when edge exceeds threshold (default 5%)

### Example

```
Market: "Will highest temp in Atlanta be 82-83°F on April 5?"
- Market Price: YES @ 0.1%
- NOAA Forecast: Max 80°F (close to range)
- Model Probability: 85%
- Edge: 84.9% ✅ BET YES
```

### Weather API

- **NOAA NWS** (default): Free, official US National Weather Service data
- No API key required
- Provides hourly forecasts up to 7 days
- Automatic fallback to Open-Meteo if NOAA fails

## 11. Cost Estimate

| Item | Monthly Cost |
|------|--------------|
| VPS (DigitalOcean) | $6 |
| Domain (optional) | $1 |
| **Total** | **~$7/mo** |

## Quick Commands Reference

```bash
# Service management
sudo systemctl start polymarket-weather-bot
sudo systemctl stop polymarket-weather-bot
sudo systemctl restart polymarket-weather-bot
sudo systemctl status polymarket-weather-bot

# Logs
sudo journalctl -u polymarket-weather-bot -f
tail -f logs/bot.log

# Manual test
cd /home/botuser/polymarket-weather-bot
./venv/bin/python main.py balance
./venv/bin/python main.py scan
```

---

**Need help?** Check `logs/bot.log` first, then `journalctl` for system errors.
