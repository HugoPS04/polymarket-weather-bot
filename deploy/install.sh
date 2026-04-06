#!/bin/bash
#
# Polymarket Weather Bot - VPS Installation Script
# Ubuntu 22.04+ / Debian 11+
#
# Usage: curl -sSL https://raw.githubusercontent.com/yourusername/polymarket-weather-bot/main/deploy/install.sh | bash
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Polymarket Weather Bot - Installer${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}Error: Do not run as root. Run as regular user with sudo access.${NC}"
    exit 1
fi

# Check OS
if [ ! -f /etc/os-release ]; then
    echo -e "${RED}Error: Cannot detect OS. This script supports Ubuntu/Debian.${NC}"
    exit 1
fi

source /etc/os-release
if [[ ! "$ID" =~ ^(ubuntu|debian)$ ]]; then
    echo -e "${YELLOW}Warning: Unsupported OS ($ID). Proceeding anyway...${NC}"
fi

# Step 1: Install system dependencies
echo -e "${YELLOW}[1/6] Installing system dependencies...${NC}"
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git curl wget

# Step 2: Create project directory
echo -e "${YELLOW}[2/6] Setting up project directory...${NC}"
PROJECT_DIR="$HOME/polymarket-weather-bot"

if [ -d "$PROJECT_DIR" ]; then
    echo -e "${YELLOW}Directory exists. Updating...${NC}"
    cd "$PROJECT_DIR"
    git pull || true
else
    echo -e "${GREEN}Cloning repository...${NC}"
    # If you have a repo, uncomment and set URL:
    # git clone <YOUR_REPO_URL> "$PROJECT_DIR"
    # cd "$PROJECT_DIR"
    
    # For now, create directory structure manually
    mkdir -p "$PROJECT_DIR"
    cd "$PROJECT_DIR"
    echo -e "${YELLOW}Note: Clone your repository to $PROJECT_DIR${NC}"
fi

# Step 3: Create virtual environment
echo -e "${YELLOW}[3/6] Creating Python virtual environment...${NC}"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip

# Step 4: Install Python dependencies
echo -e "${YELLOW}[4/6] Installing Python dependencies...${NC}"
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    echo -e "${GREEN}Dependencies installed.${NC}"
else
    echo -e "${RED}Error: requirements.txt not found.${NC}"
    echo "Make sure you're in the project directory."
    exit 1
fi

# Step 5: Configure environment
echo -e "${YELLOW}[5/6] Configuring environment...${NC}"
if [ ! -f ".env" ]; then
    if [ -f "config/config.example.env" ]; then
        cp config/config.example.env .env
        echo -e "${GREEN}Created .env from template.${NC}"
        echo -e "${YELLOW}IMPORTANT: Edit .env with your settings!${NC}"
        echo -e "${YELLOW}  nano .env${NC}"
    else
        echo -e "${RED}Error: config/config.example.env not found.${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}.env already exists.${NC}"
fi

# Set secure permissions on .env
chmod 600 .env

# Step 6: Setup systemd service
echo -e "${YELLOW}[6/6] Setting up systemd service...${NC}"

if [ -f "deploy/systemd.service" ]; then
    # Copy service file with correct paths
    SERVICE_FILE="/tmp/polymarket-weather-bot.service"
    
    # Replace USER and paths in service file
    sed "s|User=ubuntu|User=$USER|g" deploy/systemd.service > "$SERVICE_FILE"
    sed -i "s|Group=ubuntu|Group=$USER|g" "$SERVICE_FILE"
    sed -i "s|/home/ubuntu/polymarket-weather-bot|$PROJECT_DIR|g" "$SERVICE_FILE"
    
    # Install service
    sudo cp "$SERVICE_FILE" /etc/systemd/system/polymarket-weather-bot.service
    sudo systemctl daemon-reload
    sudo systemctl enable polymarket-weather-bot
    
    echo -e "${GREEN}Systemd service installed.${NC}"
    echo -e "${YELLOW}To start the bot: sudo systemctl start polymarket-weather-bot${NC}"
else
    echo -e "${YELLOW}Skipping systemd setup (deploy/systemd.service not found)${NC}"
fi

# Create necessary directories
mkdir -p logs data

# Summary
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Installation Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo ""
echo "1. Edit configuration:"
echo -e "   ${GREEN}nano .env${NC}"
echo ""
echo "   Required settings:"
echo "   - POLY_PRIVATE_KEY"
echo "   - POLY_FUNDER_ADDRESS"
echo "   - WEATHER_LOCATIONS"
echo ""
echo "2. Test the installation:"
echo -e "   ${GREEN}./venv/bin/python main.py balance${NC}"
echo -e "   ${GREEN}./venv/bin/python main.py scan --limit 10${NC}"
echo -e "   ${GREEN}./venv/bin/python main.py weather${NC}"
echo ""
echo "3. Start in dry-run mode (LIVE_TRADING=false):"
echo -e "   ${GREEN}sudo systemctl start polymarket-weather-bot${NC}"
echo ""
echo "4. Monitor logs:"
echo -e "   ${GREEN}sudo journalctl -u polymarket-weather-bot -f${NC}"
echo -e "   ${GREEN}tail -f logs/bot.log${NC}"
echo ""
echo "5. When ready, enable live trading:"
echo "   - Edit .env: LIVE_TRADING=true"
echo "   - Restart: sudo systemctl restart polymarket-weather-bot"
echo ""
echo -e "${GREEN}Documentation: deploy/VPS_SETUP.md${NC}"
echo ""
