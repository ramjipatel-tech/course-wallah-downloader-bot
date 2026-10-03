#!/usr/bin/env bash
# ==============================================================================
# Course Wallah Downloader - Safe Production Update Script
# Safely pulls code from GitHub and restarts the bot without touching
# .env, Telegram sessions, cookies, or persistent database state.
# ==============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

INSTALL_DIR="/opt/course-wallah-downloader"
SERVICE_NAME="course-wallah"

echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}🔄 Course Wallah Downloader - Safe Production Update${NC}"
echo -e "${CYAN}============================================================${NC}"

# Navigate to application directory
cd "$INSTALL_DIR" || cd "$(dirname "$0")/.."

# 1. Pull latest changes safely
echo -e "\n${YELLOW}[1/4] Pulling latest updates from GitHub (Fast-Forward Only)...${NC}"
git pull --ff-only origin main

# 2. Update Python dependencies if requirements changed
echo -e "\n${YELLOW}[2/4] Updating Python dependencies...${NC}"
if [ -d ".venv" ]; then
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
else
    echo -e "${RED}⚠️ Virtual environment (.venv) not found. Creating one...${NC}"
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
fi

# 3. Ensure runtime directories exist
echo -e "\n${YELLOW}[3/4] Checking runtime directories...${NC}"
mkdir -p data/state data/logs data/temp data/cookies data/jobs data/users data/topics data/config downloads assets/start

# 4. Restart systemd service
echo -e "\n${YELLOW}[4/4] Restarting systemd service (${SERVICE_NAME})...${NC}"
if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl daemon-reload || true
    sudo systemctl restart "$SERVICE_NAME"
    echo -e "${GREEN}✅ Service restarted successfully.${NC}"
    echo -e "\nCurrent service status:"
    sudo systemctl status "$SERVICE_NAME" --no-pager -n 10 || true
else
    echo -e "${YELLOW}ℹ️ systemctl not available (non-systemd environment).${NC}"
fi

echo -e "\n${GREEN}🎉 Update complete! Check live logs with: sudo journalctl -u ${SERVICE_NAME} -f -n 50${NC}"
