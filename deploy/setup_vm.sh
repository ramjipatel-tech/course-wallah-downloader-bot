#!/usr/bin/env bash
# ==============================================================================
# Course Wallah Downloader - Linux VM Automated Provisioning Script
# Target OS: Ubuntu 20.04/22.04/24.04 / Debian 11/12
# ==============================================================================

set -euo pipefail

# ANSI Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

INSTALL_DIR="/opt/course-wallah-downloader"
SERVICE_NAME="course-wallah"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}🚀 Course Wallah Downloader - Automated VM Setup Script${NC}"
echo -e "${CYAN}============================================================${NC}"

# Check root privileges
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}❌ Please run this script with sudo or as root.${NC}"
    exit 1
fi

# 1. Update system package index
echo -e "\n${YELLOW}[1/7] Updating system package index...${NC}"
apt-get update -y

# 2. Install required system packages & FFmpeg
echo -e "\n${YELLOW}[2/7] Installing system dependencies, FFmpeg, and Python 3...${NC}"
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    ffmpeg \
    aria2 \
    git \
    curl \
    wget \
    jq \
    build-essential \
    libffi-dev \
    libssl-dev

# Verify FFmpeg installation
if command -v ffmpeg >/dev/null 2>&1; then
    echo -e "${GREEN}✅ FFmpeg installed: $(ffmpeg -version | head -n 1)${NC}"
else
    echo -e "${RED}❌ FFmpeg installation failed!${NC}"
    exit 1
fi

# 3. Determine and create deployment directory
CURRENT_DIR="$(pwd)"
if [ "$CURRENT_DIR" != "$INSTALL_DIR" ]; then
    echo -e "\n${YELLOW}[3/7] Setting up installation directory at ${INSTALL_DIR}...${NC}"
    mkdir -p "$INSTALL_DIR"
    if [ -f "main.py" ]; then
        echo "Copying files from current directory to ${INSTALL_DIR}..."
        cp -r ./* "$INSTALL_DIR/" 2>/dev/null || true
        cp -r ./.??* "$INSTALL_DIR/" 2>/dev/null || true
    fi
    cd "$INSTALL_DIR"
else
    echo -e "\n${YELLOW}[3/7] Already in installation directory: ${INSTALL_DIR}${NC}"
fi

# 4. Create persistent data & runtime directories
echo -e "\n${YELLOW}[4/7] Ensuring runtime directories exist...${NC}"
mkdir -p "${INSTALL_DIR}/data/state" \
         "${INSTALL_DIR}/data/logs" \
         "${INSTALL_DIR}/data/temp" \
         "${INSTALL_DIR}/data/cookies" \
         "${INSTALL_DIR}/data/jobs" \
         "${INSTALL_DIR}/data/users" \
         "${INSTALL_DIR}/data/topics" \
         "${INSTALL_DIR}/data/config" \
         "${INSTALL_DIR}/downloads" \
         "${INSTALL_DIR}/assets/start"

# 5. Create Python Virtual Environment & Install Dependencies
echo -e "\n${YELLOW}[5/7] Setting up Python virtual environment (.venv)...${NC}"
if [ ! -d "${INSTALL_DIR}/.venv" ]; then
    python3 -m venv "${INSTALL_DIR}/.venv"
fi

"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip setuptools wheel
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo -e "${GREEN}✅ Python dependencies installed successfully.${NC}"

# 6. Install systemd service
echo -e "\n${YELLOW}[6/7] Configuring systemd service (${SERVICE_NAME})...${NC}"
if [ -f "${INSTALL_DIR}/deploy/course-wallah.service" ]; then
    cp "${INSTALL_DIR}/deploy/course-wallah.service" "$SERVICE_FILE"
elif [ -f "${INSTALL_DIR}/deploy/course-wallah.service.example" ]; then
    cp "${INSTALL_DIR}/deploy/course-wallah.service.example" "$SERVICE_FILE"
fi

chmod 644 "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
echo -e "${GREEN}✅ systemd service enabled.${NC}"

# 7. Environment File (.env) check
echo -e "\n${YELLOW}[7/7] Checking environment configuration (.env)...${NC}"
if [ ! -f "${INSTALL_DIR}/.env" ]; then
    if [ -f "${INSTALL_DIR}/.env.example" ]; then
        cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
        chmod 600 "${INSTALL_DIR}/.env"
        echo -e "${YELLOW}⚠️ Created ${INSTALL_DIR}/.env from .env.example.${NC}"
        echo -e "${YELLOW}👉 IMPORTANT: Edit ${INSTALL_DIR}/.env to configure your BOT_TOKEN, API_ID, API_HASH, and OWNER_ID before starting.${NC}"
        echo -e "\nCommand to edit:"
        echo -e "   ${CYAN}nano ${INSTALL_DIR}/.env${NC}"
    fi
else
    echo -e "${GREEN}✅ Existing .env found.${NC}"
    echo -e "Starting / restarting service..."
    systemctl restart "$SERVICE_NAME" || true
fi

echo -e "\n${CYAN}============================================================${NC}"
echo -e "${GREEN}🎉 VM SETUP COMPLETE!${NC}"
echo -e "${CYAN}============================================================${NC}"
echo -e "Helpful systemd commands:"
echo -e "  • Start:    ${CYAN}sudo systemctl start ${SERVICE_NAME}${NC}"
echo -e "  • Status:   ${CYAN}sudo systemctl status ${SERVICE_NAME}${NC}"
echo -e "  • Logs:     ${CYAN}sudo journalctl -u ${SERVICE_NAME} -f -n 100${NC}"
echo -e "  • Restart:  ${CYAN}sudo systemctl restart ${SERVICE_NAME}${NC}"
echo -e "  • Stop:     ${CYAN}sudo systemctl stop ${SERVICE_NAME}${NC}"
echo -e "${CYAN}============================================================${NC}"
