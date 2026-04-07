# VPS Deployment - Beginner's Guide

## What is a VPS?

A VPS (Virtual Private Server) is a computer somewhere on the internet that runs 24/7. You'll rent one to run your trading bot so it works even when your laptop is off.

## What You Need

1. **A VPS provider** (recommended: DigitalOcean, Vultr, or Hetzner)
2. **A domain name** (optional but recommended)
3. **10-15 minutes** to set up

---

## Step 1: Buy a VPS

### Recommended Providers (~$5-10/month)

**DigitalOcean** (easiest for beginners)
- Go to https://www.digitalocean.com
- Sign up
- Create a "Droplet" (VPS)
- Choose: Ubuntu 22.04 LTS, Basic plan, $6/month
- Choose a datacenter close to you
- Add your SSH key (or password - easier for beginners)

**Vultr** (cheaper, good performance)
- https://www.vultr.com
- Similar process to DigitalOcean

### What is SSH?

SSH is how you connect to your VPS from your terminal. Like opening a terminal window on a remote computer.

---

## Step 2: Connect to Your VPS

### On Mac/Linux:
Open Terminal and type:
```bash
ssh root@YOUR_VPS_IP
```
Replace `YOUR_VPS_IP` with the IP address shown in your VPS dashboard.

### On Windows:
Download **PuTTY** or use **Windows Terminal** (Windows 10/11).

### First Login:
```
root@your-vps:~#
```
You're now logged in as root (admin).

---

## Step 3: Create a User (Don't use root!)

```bash
# Create a new user
adduser bot

# Give it sudo powers
usermod -aG sudo bot

# Switch to that user
su - bot

# You should see: bot@your-vps:~$
```

---

## Step 4: Install the Bot (One Command)

Still in your VPS terminal (as `bot` user):

```bash
# Download and run the deploy script
curl -sSL https://raw.githubusercontent.com/HugoPS04/polymarket-weather-bot/main/deploy/deploy_simple.sh | bash
```

This will:
- Install Python
- Create a virtual environment
- Install dependencies
- Set up the bot
- Configure auto-start

---

## Step 5: Configure Your Bot

```bash
# Edit the settings file
nano ~/.polymarket-weather-bot/.env
```

**Fill in these values:**

```bash
# Polymarket API keys (REQUIRED - get from polymarket.com)
POLY_PRIVATE_KEY=your_private_key_here
POLY_FUNDER_ADDRESS=your_funder_address_here
POLY_SIGNATURE_TYPE=2

# Weather locations (already set)
WEATHER_LOCATIONS=Miami:25.7617:-80.1918,NewYork:40.7128:-74.0060,...

# Trading mode (start with FALSE!)
LIVE_TRADING=false
```

**To save in nano:** Press `Ctrl+X`, then `Y`, then `Enter`

---

## Step 6: Test It Works

```bash
# Go to bot directory
cd ~/.polymarket-weather-bot

# Test with a simple command
./venv/bin/python main.py scan --limit 5
```

You should see market data. If yes, the bot works!

---

## Step 7: Start the Bot

```bash
# Start the bot service
sudo systemctl start polymarket-weather-bot

# Check if it's running
sudo systemctl status polymarket-weather-bot
```

**Stop the bot:**
```bash
sudo systemctl stop polymarket-weather-bot
```

**View logs:**
```bash
sudo journalctl -u polymarket-weather-bot -f
```

---

## Step 8: Enable Live Trading (When Ready)

```bash
# Stop the bot first
sudo systemctl stop polymarket-weather-bot

# Edit .env
nano ~/.polymarket-weather-bot/.env
```

Change:
```bash
LIVE_TRADING=true
```

```bash
# Save and restart
sudo systemctl restart polymarket-weather-bot
```

---

## Common VPS Commands

| Command | What it does |
|---------|--------------|
| `sudo systemctl start polymarket-weather-bot` | Start bot |
| `sudo systemctl stop polymarket-weather-polymarket-weather-bot` | Stop bot |
| `sudo systemctl restart polymarket-weather-bot` | Restart bot |
| `sudo systemctl status polymarket-weather-bot` | Check if running |
| `sudo journalctl -u polymarket-weather-bot -f` | View live logs |
| `tail -f ~/.polymarket-weather-bot/logs/bot.log` | View bot logs |

---

## Troubleshooting

### Bot won't start
```bash
# Check logs
sudo journalctl -u polymarket-weather-bot -e

# Common fixes:
cd ~/.polymarket-weather-bot
./venv/bin/pip install -r requirements.txt
```

### Python errors
```bash
# Update code from GitHub
cd ~/.polymarket-weather-bot
git pull origin main
./venv/bin/pip install -r requirements.txt
```

### VPS is slow
```bash
# Check resources
htop
```

---

## Quick Reference Card

```
YOUR VPS IP: _______________
SSH command: ssh bot@_______________

Bot location: ~/.polymarket-weather-bot
Start bot: sudo systemctl start polymarket-weather-bot
Stop bot: sudo systemctl stop polymarket-weather-bot
View logs: sudo journalctl -u polymarket-weather-bot -f
Edit settings: nano ~/.polymarket-weather-bot/.env
```

---

## That's It!

Your bot will now run 24/7 on the VPS. It will:
- Scan markets every few minutes
- Calculate probabilities using NOAA data
- Generate signals when edge is found
- Trade automatically when LIVE_TRADING=true

**Start with `LIVE_TRADING=false` for a few days to verify it works!**
