#!/usr/bin/env bash
# ==============================================================================
# Course Wallah Downloader - Production Healthcheck Script
# Validates process state, FFmpeg availability, runtime directories, and logs.
# ==============================================================================

set -uo pipefail

SERVICE_NAME="course-wallah"
APP_DIR="/opt/course-wallah-downloader"

echo "=== COURSE WALLAH HEALTHCHECK ==="
echo "Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"

HEALTHY=0

# 1. Check systemd service status if on Linux
if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "[✅ PASS] systemd service '${SERVICE_NAME}' is active (running)."
    else
        echo "[❌ FAIL] systemd service '${SERVICE_NAME}' is NOT active."
        HEALTHY=1
    fi
fi

# 2. Check Python process presence
PID=$(pgrep -f "main.py" || true)
if [ -n "$PID" ]; then
    echo "[✅ PASS] Application process running with PID(s): $PID"
else
    echo "[⚠️ WARN] No main.py process found in process table."
fi

# 3. Check FFmpeg binary
if command -v ffmpeg >/dev/null 2>&1; then
    FF_VER=$(ffmpeg -version | head -n 1)
    echo "[✅ PASS] FFmpeg available: ${FF_VER}"
else
    echo "[❌ FAIL] FFmpeg NOT found in PATH."
    HEALTHY=1
fi

# 4. Check runtime storage directories
cd "$APP_DIR" 2>/dev/null || cd "$(dirname "$0")/.."
MISSING_DIRS=0
for dir in data/state data/logs data/temp data/cookies data/jobs data/users data/topics data/config; do
    if [ ! -d "$dir" ]; then
        echo "[❌ FAIL] Missing required directory: $dir"
        MISSING_DIRS=1
        HEALTHY=1
    fi
done
if [ "$MISSING_DIRS" -eq 0 ]; then
    echo "[✅ PASS] All persistent data and state directories exist."
fi

# 5. Check log file recency
LOG_FILE="data/logs/bot.log"
if [ -f "$LOG_FILE" ]; then
    SIZE=$(stat -c%s "$LOG_FILE" 2>/dev/null || stat -f%z "$LOG_FILE" 2>/dev/null || echo "0")
    echo "[✅ PASS] Log file '${LOG_FILE}' exists (Size: ${SIZE} bytes)."
else
    echo "[ℹ️ INFO] Log file '${LOG_FILE}' not yet generated."
fi

if [ "$HEALTHY" -eq 0 ]; then
    echo "=== HEALTH STATUS: HEALTHY ==="
    exit 0
else
    echo "=== HEALTH STATUS: DEGRADED / UNHEALTHY ==="
    exit 1
fi
