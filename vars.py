import os
import re
from os import environ
from typing import Optional, List, Dict, Any, Union
from dotenv import load_dotenv

# Load .env file at the earliest entry point
load_dotenv()

# ==============================================================================
# 🔑 TELEGRAM & BOT CREDENTIALS
# ==============================================================================
API_ID_RAW = os.environ.get("API_ID", "").strip().strip("'\"")
API_ID = int(API_ID_RAW) if API_ID_RAW.isdigit() else 0

API_HASH = os.environ.get("API_HASH", "").strip().strip("'\"")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip().strip("'\"")

BOT_NAME = os.environ.get("BOT_NAME", "Course Wallah Downloader").strip().strip("'\"")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "downloader_course_wallah_bot").strip().strip("'\"")
BOT_LINK = os.environ.get("BOT_LINK", f"https://t.me/{BOT_USERNAME}").strip().strip("'\"")
SUPPORT_LINK = os.environ.get("SUPPORT_LINK", f"https://t.me/{BOT_USERNAME}").strip().strip("'\"")
SUPPORT_USERNAME = os.environ.get("SUPPORT_USERNAME", BOT_USERNAME).strip().strip("'\"")
SUPPORT_CHAT = os.environ.get("SUPPORT_CHAT", "").strip().strip("'\"")

CREDIT = os.environ.get(
    "CREDIT",
    f'<a href="{BOT_LINK}">𝄟⃝⚡️ 🅲🅾🆄🆁🆂🅴 🆆🅰🅻🅻🅰🅷 💻 𝄟⃝🎓 🔥</a>'
)

# ==============================================================================
# 🤖 MULTI-BOT CENTRAL CONFIGURATION
# ==============================================================================
def get_configured_bots() -> List[Dict[str, Any]]:
    """
    Dynamically loads all configured Telegram bots from environment variables.
    Supports BOT_1_TOKEN, BOT_2_TOKEN, BOT_3_TOKEN, ... BOT_N_TOKEN (dynamic, at least 10+).
    Guarantees unique Pyrogram session names per bot instance and avoids starting duplicate tokens.
    """
    configured = []
    seen_sessions = set()
    seen_tokens = set()

    # Collect all configured bot indices from environment (dynamic scanning)
    indices = set(range(1, 11))
    for k in os.environ.keys():
        m = re.match(r"^BOT_(\d+)_TOKEN$", k)
        if m:
            indices.add(int(m.group(1)))

    for i in sorted(indices):
        token = os.environ.get(f"BOT_{i}_TOKEN", "").strip().strip("'\"")
        # Fallback for bot 1 to legacy BOT_TOKEN if BOT_1_TOKEN is not set
        if i == 1 and not token:
            token = os.environ.get("BOT_TOKEN", "").strip().strip("'\"")

        # Skip empty or placeholder tokens
        if not token or token.lower() in ("your_telegram_bot_token_here", "none", "null", "") or " " in token:
            continue

        # Prevent duplicate tokens (do not start the same bot twice)
        if token in seen_tokens:
            continue
        seen_tokens.add(token)

        b_name = os.environ.get(f"BOT_{i}_NAME", f"coursewallah_{i}" if i > 1 else BOT_NAME).strip().strip("'\"")
        session = os.environ.get(f"BOT_{i}_SESSION", f"coursewallah_bot_{i}").strip().strip("'\"")
        if not session:
            session = f"coursewallah_bot_{i}"

        # Prevent duplicate session names (SQLite lock prevention)
        base_session = session
        dedup_idx = 1
        while session in seen_sessions:
            session = f"{base_session}_{dedup_idx}"
            dedup_idx += 1
        seen_sessions.add(session)

        forum_chat = os.environ.get(f"BOT_{i}_FORUM_CHAT_ID", os.environ.get("FORUM_CHAT_ID", "")).strip().strip("'\"")

        configured.append({
            "id": f"bot_{i}",
            "index": i,
            "name": b_name,
            "token": token,
            "session_name": session,
            "forum_chat_id": forum_chat
        })

    return configured

BOTS: List[Dict[str, Any]] = get_configured_bots()

# ==============================================================================
# 👥 OWNER & ADMINS
# ==============================================================================
OWNER_ID_RAW = os.environ.get("OWNER_ID", "").strip().strip("'\"")
OWNER_ID = int(OWNER_ID_RAW) if OWNER_ID_RAW.isdigit() else 0

ADMINS_RAW = os.environ.get("ADMINS", str(OWNER_ID) if OWNER_ID else "").strip().strip("'\"")
ADMINS = [int(x.strip()) for x in ADMINS_RAW.split(",") if x.strip().isdigit()]
if OWNER_ID and OWNER_ID not in ADMINS:
    ADMINS.append(OWNER_ID)

from pathlib import Path
from storage import get_storage_manager, APP_ROOT

# Absolute Project Root
PROJECT_ROOT = APP_ROOT

# ==============================================================================
# 📂 DIRECTORY STRUCTURE (Zero Database Persistence, Centralized Storage)
# ==============================================================================
storage = get_storage_manager()

DATA_DIR = str(storage.get_root_dir())
USERS_DIR = str(storage.get_dir("users"))
JOBS_DIR = str(storage.get_dir("jobs"))
STATE_DIR = str(storage.get_dir("state"))
COOKIES_DIR = str(storage.get_dir("cookies"))
LOGS_DIR = str(storage.get_dir("logs"))
TEMP_DIR = str(storage.get_dir("temp"))
DOWNLOADS_DIR = str(storage.get_dir("downloads"))
OUTPUT_DIR = str(storage.get_dir("output"))
SESSIONS_DIR = str(storage.get_dir("sessions"))
THUMBNAILS_DIR = str(storage.get_dir("thumbnails"))
CACHE_DIR = str(storage.get_dir("cache"))
CONFIG_DIR = str(storage.get_dir("config"))
TOPICS_DIR = str(storage.get_dir("topics"))

# Asset directories
ASSETS_DIR = os.environ.get("ASSETS_DIR", str(PROJECT_ROOT / "assets"))
START_IMAGE_DIR = os.path.join(ASSETS_DIR, "start")
START_IMAGE_PATH = os.environ.get("START_IMAGE_PATH", os.path.join(START_IMAGE_DIR, "start.jpg"))

# Persistent session config
SESSION_NAME = os.environ.get("SESSION_NAME", "bot_session")
SESSION_DIR = SESSIONS_DIR if os.path.exists(SESSIONS_DIR) else STATE_DIR

# State files
USERS_STATE_FILE = os.path.join(STATE_DIR, "users.json")
JOBS_STATE_FILE = os.path.join(STATE_DIR, "jobs.json")
CONFIG_STATE_FILE = os.path.join(STATE_DIR, "config.json")
TOPICS_STATE_FILE = os.path.join(STATE_DIR, "topics.json")

# Ensure all essential directories exist safely
for d in [DATA_DIR, USERS_DIR, JOBS_DIR, STATE_DIR, COOKIES_DIR, LOGS_DIR, TEMP_DIR, DOWNLOADS_DIR, OUTPUT_DIR, SESSIONS_DIR, THUMBNAILS_DIR, CACHE_DIR, CONFIG_DIR, TOPICS_DIR, ASSETS_DIR, START_IMAGE_DIR]:
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass

# Legacy / Global compatibility
DATABASE_ENABLED = False  # Local JSON persistence is always active
DATABASE_NAME = "botdb"
DATABASE_URL = ""
MONGO_URL = ""

# ==============================================================================
# ⚡ CONCURRENCY & WORKER LIMITS
# ==============================================================================
DOWNLOAD_WORKERS = int(os.environ.get("DOWNLOAD_WORKERS", "4"))
UPLOAD_WORKERS = int(os.environ.get("UPLOAD_WORKERS", "3"))
MAX_ACTIVE_USERS = int(os.environ.get("MAX_ACTIVE_USERS", "6"))
MAX_JOBS_PER_USER = int(os.environ.get("MAX_JOBS_PER_USER", "1"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
RETRY_DELAY = int(os.environ.get("RETRY_DELAY", "5"))
SPAYEE_SEGMENT_WORKERS = int(os.environ.get("SPAYEE_SEGMENT_WORKERS", "16"))

# ==============================================================================
# 📦 UPLOAD THRESHOLD & VIDEO SPLITTING (Telegram Limit Safety Margin)
# ==============================================================================
# 1950 MB safe threshold to account for Telegram 2000 MB limit & container headers
MAX_UPLOAD_SIZE_BYTES = int(os.environ.get("MAX_UPLOAD_SIZE_BYTES", str(1950 * 1024 * 1024)))

# ==============================================================================
# 🎨 WATERMARK & BRANDING
# ==============================================================================
WATERMARK_TEXT = os.environ.get("WATERMARK_TEXT", "Course Wallah")
WATERMARK_FILE = os.environ.get("WATERMARK_FILE", "Course Wallah")
WATERMARK_CRF = int(os.environ.get("WATERMARK_CRF", "26"))

# Default thumbnails
THUMBNAILS = list(map(str, os.environ.get("THUMBNAILS", "").split()))

# Cookies
COOKIES_FILE = os.environ.get("COOKIES_FILE", "cookies.txt")

# ==============================================================================
# 📢 TELEGRAM FORUM / SUPERGROUP SETTINGS
# ==============================================================================
FORUM_CHAT_ID = os.environ.get("FORUM_CHAT_ID", "")
RECORDED_TOPIC_ID = int(os.environ.get("RECORDED_TOPIC_ID", "0")) if os.environ.get("RECORDED_TOPIC_ID", "0").isdigit() else None
LIVE_TOPIC_ID = int(os.environ.get("LIVE_TOPIC_ID", "0")) if os.environ.get("LIVE_TOPIC_ID", "0").isdigit() else None
PREMIUM_CHANNEL = os.environ.get("PREMIUM_CHANNEL", "")
BOT_STATUS_CHAT_ID = os.environ.get("BOT_STATUS_CHAT_ID", "").strip().strip("'\"")

# ==============================================================================
# 🔧 TOOLS & BINARIES
# ==============================================================================
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")
FFPROBE_PATH = os.environ.get("FFPROBE_PATH", "ffprobe")
PW_TOKEN = os.environ.get("PW_TOKEN", "")

# ==============================================================================
# 🌐 WEB SERVER (Optional)
# ==============================================================================
WEB_SERVER = os.environ.get("WEB_SERVER", "False").lower() == "true"
WEBHOOK = True
PORT = int(os.environ.get("PORT", "8000"))

# ==============================================================================
# 💬 MESSAGE TEMPLATES
# ==============================================================================
AUTH_MESSAGES = {
    "subscription_active": """<b>🎉 Subscription Activated!</b>

<blockquote>Your subscription has been activated and will expire on {expiry_date}.
You can now use the bot!</blockquote>\n\n Type /start to start uploading """,

    "subscription_expired": """<b>⚠️ Your Subscription Has Ended</b>

<blockquote>Your access to the bot has been revoked as your subscription period has expired.
Please contact the admin to renew your subscription.</blockquote>""",

    "user_added": """<b>✅ User Added Successfully!</b>

<blockquote>👤 Name: {name}
🆔 User ID: {user_id}
📅 Expiry: {expiry_date}</blockquote>""",

    "user_removed": """<b>✅ User Removed Successfully!</b>

<blockquote>User ID {user_id} has been removed from authorized users.</blockquote>""",

    "access_denied": """<b>⚠️ Access Denied!</b>

<blockquote>You are not authorized to use this bot.
Please contact the admin to get access.</blockquote>""",

    "not_admin": "⚠️ You are not authorized to use this command!",
    
    "invalid_format": """❌ <b>Invalid Format!</b>

<blockquote>Use format: {format}</blockquote>"""
}
