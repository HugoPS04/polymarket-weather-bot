# Deployment Checklist

Use this checklist to ensure your VPS deployment is production-ready.

## Pre-Deployment

- [ ] VPS provisioned (Ubuntu 22.04+, 1GB+ RAM recommended)
- [ ] SSH access configured (key-based, no password)
- [ ] Firewall configured (only SSH port 22 open)
- [ ] Polymarket wallet created with USDC on Polygon
- [ ] Private key secured (never commit to git)

## Installation

- [ ] System dependencies installed (`python3`, `pip`, `git`)
- [ ] Repository cloned to `/home/username/polymarket-weather-bot`
- [ ] Virtual environment created and activated
- [ ] Python dependencies installed (`pip install -r requirements.txt`)
- [ ] `.env` file created from `config/config.example.env`
- [ ] `.env` permissions set to `600` (owner read/write only)

## Configuration

- [ ] `POLY_PRIVATE_KEY` set (your Polygon wallet private key)
- [ ] `POLY_FUNDER_ADDRESS` set (your wallet address)
- [ ] `POLY_SIGNATURE_TYPE` set (usually `2` for browser/safe)
- [ ] `WEATHER_API_SOURCE=noaa` (for US markets)
- [ ] `WEATHER_LOCATIONS` configured with US cities
- [ ] `LIVE_TRADING=false` (start in dry-run mode!)
- [ ] `MIN_EDGE_THRESHOLD=0.05` (5% minimum edge)
- [ ] `MAX_POSITION_SIZE=100` (USDC per trade)
- [ ] `MAX_TOTAL_EXPOSURE=500` (total USDC at risk)

## Testing (Dry Run)

- [ ] Balance check works: `./venv/bin/python main.py balance`
- [ ] Market scan works: `./venv/bin/python main.py scan --limit 10`
- [ ] Weather API works: `./venv/bin/python main.py weather`
- [ ] Systemd service installed: `sudo systemctl status polymarket-weather-bot`
- [ ] Logs visible: `tail -f logs/bot.log`
- [ ] Bot detects opportunities (check logs for "OPPORTUNITY")
- [ ] No errors in logs after 24 hours

## Going Live

- [ ] Reviewed all dry-run trades (would you actually make these bets?)
- [ ] Verified edge calculations match your expectations
- [ ] Confirmed you understand the risks
- [ ] Started with small position sizes
- [ ] Changed `LIVE_TRADING=true` in `.env`
- [ ] Restarted service: `sudo systemctl restart polymarket-weather-bot`
- [ ] Monitored first live trades on Polymarket portfolio

## Monitoring Setup

- [ ] Log rotation configured (`/etc/logrotate.d/polymarket-bot`)
- [ ] Monitoring dashboard or alerts set up (optional)
- [ ] Backup strategy for positions.json
- [ ] Emergency stop procedure documented

## Security Hardening

- [ ] `.env` file permissions: `chmod 600 .env`
- [ ] Firewall active: `sudo ufw status` (only SSH open)
- [ ] Automatic security updates enabled
- [ ] No unnecessary services running
- [ ] Regular system updates scheduled

## Maintenance

- [ ] Weekly: Check logs for errors
- [ ] Weekly: Review P&L and adjust strategy if needed
- [ ] Monthly: Update bot (`git pull && pip install -r requirements.txt`)
- [ ] Monthly: Review and rotate private keys (optional but recommended)

## Emergency Procedures

### Stop Bot Immediately
```bash
sudo systemctl stop polymarket-weather-bot
```

### Withdraw Funds
1. Stop bot
2. Go to Polymarket.com
3. Withdraw USDC to your wallet

### Check Current Positions
```bash
cat data/positions.json | python3 -m json.tool
```

### View Recent Trades
```bash
grep "Order placed" logs/bot.log | tail -20
```

## Troubleshooting Quick Reference

| Issue | Solution |
|-------|----------|
| Service won't start | `sudo journalctl -u polymarket-weather-bot -n 50` |
| 401 Unauthorized | Check `POLY_PRIVATE_KEY` and `POLY_SIGNATURE_TYPE` |
| No weather markets | Check Polymarket has active weather markets |
| High memory usage | Add `MemoryMax=512M` to systemd service |
| API rate limited | Increase `CHECK_INTERVAL` in `.env` |

## Success Criteria

Your deployment is successful when:

- ✅ Bot runs continuously without crashes
- ✅ Logs show regular trading cycles
- ✅ Opportunities are detected and logged
- ✅ (Live mode) Trades execute on Polymarket
- ✅ No unauthorized transactions
- ✅ P&L tracking works correctly

---

**Remember**: Start with `LIVE_TRADING=false` and monitor for at least 24-48 hours before going live!
