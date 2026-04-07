#!/bin/bash
#
# SIMPLE DEPLOY - One command on VPS
# Run on your VPS as: bash <(curl -sSL <this-script-url>)
#
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Polymarket Bot - Simple Deploy${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Config
BOT_DIR="$HOME/polymarket-weather-bot"
VENV="$BOT_DIR/venv"

# Step 1: System deps
echo -e "${YELLOW}[1/5] System deps...${NC}"
sudo apt update -qq
sudo apt install -y -qq python3 python3-pip python3-venv git curl

# Step 2: Create dir
echo -e "${YELLOW}[2/5] Creating bot directory...${NC}"
mkdir -p "$BOT_DIR"
cd "$BOT_DIR"

# Step 3: Copy code from current location (assumes you're in the project dir)
# If running remote, use: git clone or rsync
if [ -f "main.py" ]; then
    echo "Copying bot files..."
    rsync -av --exclude='venv' --exclude='__pycache__' --exclude='.git' --exclude='*.pyc' . "$BOT_DIR/" 2>/dev/null || cp -r . "$BOT_DIR/"
else
    echo -e "${RED}Error: Run this from your bot directory${NC}"
    exit 1
fi

# Step 4: Setup venv
echo -e "${YELLOW}[3/5] Python venv...${NC}"
cd "$BOT_DIR"
python3 -m venv venv
source venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

# Step 5: Env
echo -e "${YELLOW}[4/5] Configuration...${NC}"
if [ ! -f ".env" ]; then
    cp config/config.example.env .env
    echo -e "${YELLOW}Created .env - EDIT IT: nano .env${NC}"
else
    echo ".env exists"
fi
chmod 600 .env

# Step 6: Service
echo -e "${YELLOW}[5/5] Systemd service...${NC}"
sudo tee /etc/systemd/system/polymarket-weather-bot.service > /dev/null <<EOF
[Unit]
Description=Polymarket Weather Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$BOT_DIR
Environment=PYTHONPATH=$BOT_DIR
ExecStart=$BOT_DIR/venv/bin/python main.py run --loop
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable polymarket-weather-bot

mkdir -p logs data

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}DONE!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "1. Edit .env: nano $BOT_DIR/.env"
echo "2. Test: $VENV/bin/python main.py scan --limit 5"
echo "3. Start: sudo systemctl start polymarket-weather-bot"
echo "4. Logs: sudo journalctl -u polymarket-weather-bot -f"
