# 🔧 Standard Library
import os
import re
import sys
import time
import json
import random
import string
import shutil
import zipfile
import urllib
import subprocess
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse
from typing import Optional, Union, List, Dict, Any, Tuple

# Ensure UTF-8 stdout encoding on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 📦 Third-party Libraries
import aiohttp
import aiofiles
import requests
import yt_dlp
import tgcrypto
from pyrogram import Client, filters, idle, enums
from pyrogram.handlers import MessageHandler, CallbackQueryHandler, EditedMessageHandler
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeChat
)
from pyrogram.errors import (
    FloodWait,
    BadRequest,
    Unauthorized,
    SessionExpired,
    AuthKeyDuplicated,
    AuthKeyUnregistered,
    ChatAdminRequired,
    PeerIdInvalid,
    RPCError,
    MessageNotModified
)

# 🧠 Bot Modules
from vars import (
    API_ID,
    API_HASH,
    BOT_TOKEN,
    BOT_NAME,
    BOT_USERNAME,
    BOT_LINK,
    SUPPORT_LINK,
    CREDIT,
    BOTS,
    get_configured_bots,
    OWNER_ID,
    ADMINS,
    DATA_DIR,
    TEMP_DIR,
    LOGS_DIR,
    WATERMARK_TEXT,
    WATERMARK_FILE,
    AUTH_MESSAGES,
    DOWNLOAD_WORKERS,
    UPLOAD_WORKERS,
    MAX_ACTIVE_USERS,
    MAX_JOBS_PER_USER,
    WEB_SERVER,
    PORT,
    START_IMAGE_PATH,
    START_IMAGE_DIR,
    SESSION_NAME,
    SESSION_DIR,
    SESSIONS_DIR,
    storage,
    FORUM_CHAT_ID,
    RECORDED_TOPIC_ID,
    LIVE_TOPIC_ID,
    PREMIUM_CHANNEL,
    FFMPEG_PATH,
    PW_TOKEN,
    COOKIES_FILE,
    THUMBNAILS,
    MAX_UPLOAD_SIZE_BYTES,
    BOT_STATUS_CHAT_ID
)
import vars
from db import db
from utils import (
    JobProgressTracker,
    hrb,
    hrt,
    MediaRouter,
    MediaType,
    is_direct_image_url,
    is_kgs_url,
    is_youtube_url,
    is_encrypted_stream_url,
    is_direct_pdf_url,
    is_spayee_url,
    is_hls_url,
    is_direct_m3u8_url,
    is_appx_url,
    is_direct_video_url,
    build_failure_card,
    sanitize_error_message,
    format_watermark_card,
    format_download_card,
    format_merging_card,
    format_processing_card,
    format_split_card,
    format_upload_card,
    format_pdf_download_card,
    format_pdf_processing_card,
    format_pdf_upload_card,
    format_success_card,
    format_main_menu,
    format_invalid_input_card,
    format_youtube_quality_menu,
    format_youtube_fallback_card,
    format_4k_available_card,
    format_4k_unavailable_card,
    format_single_quality_card,
    format_universal_input_card,
    format_batch_input_card,
    format_batch_summary_card,
    format_chat_id_card,
    format_api_token_card,
    format_watermark_input_card,
    format_help_menu_card,
    format_contact_card,
    format_status_card,
    format_failure_card,
    format_expired_callback_card,
    format_drm_input_card,
    format_drm_result_card,
    format_bot_online_card,
    check_media_drm_status,
    get_disk_storage_info,
    cleanup_uploaded_file,
    cleanup_job_temp_dir,
    cleanup_stale_temp_dirs,
    parse_pdf_input,
    PDFPasswordInvalid,
    PDF_PASSWORD_INVALID
)
from job_manager import job_manager, JobManager, JobCheckpoint
import auth
import itsgolu as helper
from youtube_fallback import (
    fetch_ytultra_media_data,
    parse_all_ytultra_qualities,
    parse_ytultra_response,
    parse_quality_number,
    resolve_youtube_ytultra_info
)

from academic_parser import (
    parse_academic_txt,
    parse_course_txt,
    detect_txt_format,
    build_unit_header_message,
    build_unit_status_message,
    build_enriched_caption,
    build_pdf_caption,
    normalize_title,
    AcademicCourse,
    AcademicUnit,
    AcademicItem
)
from bracket_topic_parser import (
    parse_bracket_topic_txt,
    parse_bracket_topic_raw,
    parse_first_topic,
    sanitize_topic_name,
    is_bracket_topic_format,
    TxtResource
)
from clean import clean_all, clean_expired_users
from html_handler import html_handler

# Configure structured logging
os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, "bot.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("CourseWallahBot")

# ==============================================================================
# 🤖 BOT CONTEXT & MULTI-BOT INSTANCE MANAGEMENT
# ==============================================================================

@dataclass
class BotContext:
    bot_id: str
    bot_name: str
    session_name: str
    client: Client
    bot_token: str = ""
    bot_username: str = ""
    bot_user_id: int = 0
    bot_first_name: str = ""
    bot_last_name: str = ""
    bot_display_name: str = ""
    bot_link: str = ""
    bot_is_bot: bool = True
    forum_chat_id: Optional[Union[int, str]] = None
    is_online: bool = False
    error: Optional[str] = None
    status_messages: Dict[Tuple[int, str], int] = field(default_factory=dict)
    job_manager: Optional[Any] = None

    def __post_init__(self):
        if self.job_manager is None and self.bot_id:
            self.job_manager = JobManager(bot_id=self.bot_id)

_BOT_CONTEXTS: Dict[Any, BotContext] = {}
_DEFAULT_CLIENT: Optional[Client] = None

def register_bot_context(client: Client, ctx: BotContext):
    """Associate a BotContext with a Pyrogram Client."""
    setattr(client, "ctx", ctx)
    _BOT_CONTEXTS[client] = ctx
    if ctx.bot_id:
        _BOT_CONTEXTS[ctx.bot_id] = ctx

def get_bot_context(client: Client) -> BotContext:
    if client and hasattr(client, "ctx") and client.ctx:
        return client.ctx
    if client in _BOT_CONTEXTS:
        return _BOT_CONTEXTS[client]
    for ctx in _BOT_CONTEXTS.values():
        if ctx.client == client:
            return ctx
    # Dynamic fallback context
    uname = getattr(getattr(client, "me", None), "username", "") or BOT_USERNAME
    fname = getattr(getattr(client, "me", None), "first_name", "") or ""
    lname = getattr(getattr(client, "me", None), "last_name", "") or ""
    disp = f"{fname} {lname}".strip() or uname or BOT_NAME
    blink = f"https://t.me/{uname}" if uname else BOT_LINK
    fallback = BotContext(
        bot_id="bot_1",
        bot_name=BOT_NAME,
        session_name=getattr(client, "name", SESSION_NAME) or SESSION_NAME,
        bot_token=getattr(client, "bot_token", BOT_TOKEN) or BOT_TOKEN,
        client=client,
        bot_username=uname,
        bot_first_name=fname,
        bot_last_name=lname,
        bot_display_name=disp,
        bot_link=blink,
        is_online=getattr(client, "is_connected", True),
        job_manager=job_manager
    )
    return fallback

def get_job_manager(client: Optional[Client] = None, bot_id: Optional[str] = None) -> JobManager:
    """Returns the bot-scoped JobManager for the given client or bot_id, or fallback."""
    if client is not None:
        ctx = get_bot_context(client)
        if ctx and ctx.job_manager:
            return ctx.job_manager
    if bot_id and bot_id in _BOT_CONTEXTS:
        ctx = _BOT_CONTEXTS[bot_id]
        if ctx and ctx.job_manager:
            return ctx.job_manager
    return job_manager

photologo = 'https://i.ibb.co/v6Vr7HCt/1000003297.png'


def get_start_image_path() -> Optional[str]:
    """Resolves the configured start image from assets or skin directories."""
    if START_IMAGE_PATH and os.path.isfile(START_IMAGE_PATH):
        return START_IMAGE_PATH
    if START_IMAGE_DIR and os.path.isdir(START_IMAGE_DIR):
        for name in ("start.jpg", "start.jpeg", "start.png", "start.webp", "welcome.jpg", "welcome.png"):
            p = os.path.join(START_IMAGE_DIR, name)
            if os.path.isfile(p):
                return p
    skin_dir = os.path.join("skin", "start")
    if os.path.isdir(skin_dir):
        for name in ("start.jpg", "start.jpeg", "start.png", "start.webp"):
            p = os.path.join(skin_dir, name)
            if os.path.isfile(p):
                return p
    return None


def get_bot_username(client: Client = None) -> str:
    try:
        if client and hasattr(client, "ctx") and client.ctx and client.ctx.bot_username:
            return client.ctx.bot_username
        if client and getattr(client, "me", None) and getattr(client.me, "username", None):
            return client.me.username
        if _DEFAULT_CLIENT and getattr(_DEFAULT_CLIENT, "me", None) and getattr(_DEFAULT_CLIENT.me, "username", None):
            return _DEFAULT_CLIENT.me.username
    except Exception:
        pass
    return BOT_USERNAME


def get_bot_display_name(client: Client = None) -> str:
    try:
        if client and hasattr(client, "ctx") and client.ctx and client.ctx.bot_display_name:
            return client.ctx.bot_display_name
        if client and getattr(client, "me", None):
            full = f"{getattr(client.me, 'first_name', '') or ''} {getattr(client.me, 'last_name', '') or ''}".strip()
            if full:
                return full
            if getattr(client.me, "username", None):
                return client.me.username
        if client and hasattr(client, "ctx") and client.ctx and client.ctx.bot_name:
            return client.ctx.bot_name
    except Exception:
        pass
    return BOT_NAME


def get_bot_link(client: Client = None) -> str:
    try:
        if client and hasattr(client, "ctx") and client.ctx and client.ctx.bot_link:
            return client.ctx.bot_link
        uname = get_bot_username(client)
        if uname:
            return f"https://t.me/{uname}"
    except Exception:
        pass
    return BOT_LINK


def get_bot_support_link(client: Client = None) -> str:
    try:
        link = get_bot_link(client)
        if link:
            return link
    except Exception:
        pass
    return SUPPORT_LINK


# ==============================================================================
# 🔐 AUTHENTICATION & ACCESS FILTERS
# ==============================================================================

def auth_check_filter(_, client: Client, message: Message):
    try:
        if not message.from_user:
            return True
        uid = message.from_user.id
        if db.is_admin(uid):
            return True
        if db.is_banned(uid):
            return False
        # Check maintenance mode
        if db.get_setting("maintenance_mode", False):
            return False
        bot_uname = get_bot_username(client)
        if message.chat and str(message.chat.type).lower() in ("channel", "chattype.channel"):
            return db.is_channel_authorized(message.chat.id, bot_uname)
        return db.is_user_authorized(uid, bot_uname)
    except Exception:
        return False

auth_filter = filters.create(auth_check_filter)


def admin_check_filter(_, client: Client, message: Message):
    try:
        if not message.from_user:
            return False
        return db.is_admin(message.from_user.id)
    except Exception:
        return False

admin_filter = filters.create(admin_check_filter)


def admin_only(handler_func):
    """Decorator ensuring only authorized admins can execute the command."""
    async def wrapper(client: Client, message: Message, *args, **kwargs):
        user_id = message.from_user.id if message.from_user else 0
        if not db.is_admin(user_id):
            await message.reply_text(
                "⛔ <b>ADMIN ONLY</b>\n\n"
                "This command is available only to bot administrators."
            )
            return
        return await handler_func(client, message, *args, **kwargs)
    return wrapper


# ==============================================================================
# 🏠 USER COMMANDS: /start, /help, /id, /plan & DASHBOARD BUILDERS
# ==============================================================================

def build_welcome_dashboard(user_id: int, first_name: str, is_admin: bool = False, bot_name: str = None) -> tuple[str, InlineKeyboardMarkup]:
    expiry_info = db.get_user_expiry_info(user_id)
    bname = bot_name or BOT_NAME
    if is_admin:
        expiry_display = "Lifetime / Permanent 👑 (Admin Access)"
    elif expiry_info and expiry_info.get("expiry_date"):
        exp_dt = expiry_info.get("expiry_date")
        if isinstance(exp_dt, str):
            try:
                exp_dt = datetime.fromisoformat(exp_dt).strftime("%d %B %Y, %I:%M %p")
            except Exception:
                pass
        elif isinstance(exp_dt, datetime):
            exp_dt = exp_dt.strftime("%d %B %Y, %I:%M %p")
        days = expiry_info.get("days_left", 0)
        expiry_display = f"{exp_dt} ({days} days remaining)"
    else:
        user_data = db.get_user(user_id)
        if user_data and user_data.get("expiry_date"):
            exp_str = user_data.get("expiry_date")
            try:
                exp_dt = datetime.fromisoformat(exp_str).strftime("%d %B %Y, %I:%M %p")
                expiry_display = f"{exp_dt}"
            except Exception:
                expiry_display = str(exp_str)
        else:
            expiry_display = "Active Premium Member"

    return format_main_menu(
        user_id=user_id,
        first_name=first_name,
        is_admin=is_admin,
        bot_name=bname,
        expiry_display=expiry_display
    )


def build_unauthorized_panel(support_link: str = None) -> tuple[str, InlineKeyboardMarkup]:
    s_link = support_link or SUPPORT_LINK
    text = (
        "🔒 <b>SUBSCRIPTION REQUIRED</b>\n\n"
        "Sorry, your account currently doesn't have an active subscription.\n\n"
        "<b>Get access to:</b>\n"
        "🎥 Video Courses\n"
        "📄 PDFs & Notes\n"
        "📝 Questions\n"
        "⚡ Fast Downloads\n"
        "📦 Course Resources"
    )
    buttons = [
        [InlineKeyboardButton("💳 BUY SUBSCRIPTION", url=s_link)],
        [InlineKeyboardButton("ℹ️ HELP", callback_data="menu_help")]
    ]
    return text, InlineKeyboardMarkup(buttons)


def format_job_progress_card(course_name: str, total: int, completed: int, failed: int, current_title: str, phase: str, spinner_char: str = "🔄") -> str:
    remaining = max(0, total - (completed + failed))
    return (
        "📦 <b>PROCESSING COURSE</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>Total:</b> {total}\n"
        f"✅ <b>Success:</b> {completed}\n"
        f"❌ <b>Failed:</b> {failed}\n"
        f"⏳ <b>Remaining:</b> {remaining}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "▶ <b>Current:</b>\n\n"
        f"<code>{current_title}</code>\n\n"
        f"{spinner_char} <b>{phase}...</b>"
    )


async def start_cmd(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    bot_uname = get_bot_username(client)
    b_name = get_bot_display_name(client)
    b_supp = get_bot_support_link(client)

    if message.chat and str(message.chat.type).lower() in ("channel", "chattype.channel"):
        await message.reply_text(
            f"<b>✨ {b_name} is active in this channel</b>\n\n"
            "<b>Available Commands:</b>\n"
            "• /drm - Download batch content\n"
            "• /plan - View channel subscription\n\n"
            "Send commands here to start."
        )
        return

    is_authorized = db.is_user_authorized(user_id, bot_uname)
    is_admin = db.is_admin(user_id)

    # 1. Start loading animation on the same message
    anim_msg = None
    try:
        anim_frames = [
            "🚀 <b>INITIALIZING...</b>\n\n<code>🟩🟩░░░░░░░░░░</code> <b>10%</b>",
            "🚀 <b>INITIALIZING...</b>\n\n<code>🟩🟩🟩🟩░░░░░░</code> <b>25%</b>",
            "🚀 <b>INITIALIZING...</b>\n\n<code>🟩🟩🟩🟩🟩🟩░░</code> <b>40%</b>",
            "🚀 <b>INITIALIZING...</b>\n\n<code>🟩🟩🟩🟩🟩🟩🟩🟩░░</code> <b>60%</b>",
            "🚀 <b>INITIALIZING...</b>\n\n<code>🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩</code> <b>80%</b>",
            "🚀 <b>INITIALIZING...</b>\n\n<code>🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩</code> <b>100%</b>",
            "✨ <b>READY</b>"
        ]
        anim_msg = await message.reply_text(anim_frames[0])
        for frame in anim_frames[1:]:
            await asyncio.sleep(0.18)
            try:
                await anim_msg.edit_text(frame)
            except (MessageNotModified, FloodWait):
                pass
            except Exception:
                break
        await asyncio.sleep(0.15)
    except Exception:
        anim_msg = None

    if not is_authorized:
        caption_unauth, markup_unauth = build_unauthorized_panel(support_link=b_supp)
        start_img = get_start_image_path()
        if anim_msg:
            try:
                await anim_msg.delete()
            except Exception:
                pass
        if start_img:
            try:
                await message.reply_photo(photo=start_img, caption=caption_unauth, reply_markup=markup_unauth)
                return
            except Exception:
                pass
        await message.reply_text(caption_unauth, reply_markup=markup_unauth)
        return

    user_name = message.from_user.first_name if message.from_user else "User"
    dashboard_text, dashboard_markup = build_welcome_dashboard(user_id, user_name, is_admin, bot_name=b_name)

    if anim_msg:
        try:
            await anim_msg.delete()
        except Exception:
            pass

    start_img = get_start_image_path()
    if start_img:
        try:
            await message.reply_photo(photo=start_img, caption=dashboard_text, reply_markup=dashboard_markup)
            return
        except Exception:
            pass
    await message.reply_text(dashboard_text, reply_markup=dashboard_markup)


async def help_cmd(client: Client, message: Message):
    b_name = get_bot_display_name(client)
    text, markup = format_help_menu_card(b_name)
    await message.reply_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)


async def id_cmd(client: Client, message: Message):
    if not message or not message.chat:
        return

    chat = message.chat
    chat_id = chat.id
    raw_type = getattr(chat.type, "value", str(chat.type)) if hasattr(chat.type, "value") else str(chat.type)
    chat_type = raw_type.lower().replace("chattype.", "")

    thread_id = getattr(message, "message_thread_id", None)
    if thread_id and chat_type not in ["supergroup", "group"]:
        thread_id = None

    title = chat.title or (f"{chat.first_name or ''} {chat.last_name or ''}".strip() if chat.first_name else None)
    user_id = message.from_user.id if message.from_user and chat_type != "private" else None

    res = format_chat_id_card(
        chat_id=chat_id,
        chat_type=chat_type,
        title=title,
        username=chat.username,
        thread_id=int(thread_id) if thread_id else None,
        user_id=user_id
    )

    reply_kwargs = {}
    if isinstance(res, tuple):
        card_text, card_markup = res
        reply_kwargs["reply_markup"] = card_markup
    else:
        card_text = res

    if thread_id and chat_type in ["supergroup", "group"]:
        try:
            reply_kwargs["message_thread_id"] = int(thread_id)
        except (ValueError, TypeError):
            pass

    try:
        await message.reply_text(card_text, parse_mode=enums.ParseMode.HTML, **reply_kwargs)
    except Exception as exc:
        logger.warning(f"Failed to reply to /id command: {exc}")


# ==============================================================================
# 🍪 PER-USER YOUTUBE COOKIES: /cookies, /getcookies, /deletecookies
# ==============================================================================

async def cookies_upload_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    prompt = await message.reply_text(
        "<b>🍪 YouTube Cookies Configuration</b>\n\n"
        "<blockquote>Please upload your Netscape-format <code>cookies.txt</code> file within 60 seconds.\n"
        "Your cookie file will be stored securely and isolated to your account.</blockquote>",
        quote=True
    )

    try:
        doc_msg: Message = await client.listen(chat_id=message.chat.id, user_id=user_id, timeout=60)
        if not doc_msg.document or not doc_msg.document.file_name.endswith(".txt"):
            await message.reply_text("❌ Invalid file format! Please upload a valid <code>.txt</code> Netscape cookies file.")
            return

        downloaded_path = await doc_msg.download()
        with open(downloaded_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        try:
            os.remove(downloaded_path)
        except Exception:
            pass

        # Validate Netscape cookie content
        if not content.strip() or ("# Netscape HTTP Cookie File" not in content and "# HTTP Cookie File" not in content and "\t" not in content):
            await message.reply_text("❌ Invalid cookie format! The file must be a standard Netscape/HTTP cookie file exported from your browser.")
            return

        # Save to per-user isolated directory
        if db.save_user_cookies(user_id, content):
            await message.reply_text(
                "✅ <b>YouTube Cookies Saved Successfully!</b>\n\n"
                "<blockquote>Your cookies are now configured and will be used automatically for your YouTube downloads.</blockquote>"
            )
        else:
            await message.reply_text("❌ Failed to save cookies. Please try again.")

    except asyncio.TimeoutError:
        await message.reply_text("⏱ Timed out waiting for cookie file. Send /cookies again when ready.")
    except Exception as e:
        await message.reply_text(f"❌ Error setting cookies: {str(e)}")


async def getcookies_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    info = db.get_user_cookies_info(user_id)

    if info["configured"]:
        await message.reply_text(
            "🍪 <b>YouTube Cookies Status</b>\n\n"
            "• <b>Status:</b> ✅ Configured\n"
            f"• <b>Updated:</b> <code>{info['updated_at']}</code>\n"
            f"• <b>File Size:</b> <code>{hrb(info['size_bytes'])}</code>\n\n"
            "Use /deletecookies to remove or /cookies to update."
        )
    else:
        await message.reply_text(
            "🍪 <b>YouTube Cookies Status</b>\n\n"
            "• <b>Status:</b> ❌ Not Configured\n\n"
            "Send /cookies to upload your <code>cookies.txt</code> for YouTube downloads."
        )


async def deletecookies_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if db.delete_user_cookies(user_id):
        await message.reply_text("✅ Your YouTube cookies have been deleted successfully.")
    else:
        await message.reply_text("ℹ️ No cookies found to delete.")


# ==============================================================================
# 🚦 JOB CONTROL: /status, /stop, /resume, /cancel, /jobs
# ==============================================================================

async def job_status_cmd(client: Client, message: Message):
    bot_ctx = get_bot_context(client)
    bot_id = bot_ctx.bot_id if bot_ctx else None
    user_id = message.from_user.id
    active_job = db.get_user_active_job(user_id, bot_id=bot_id)
    paused_job = db.get_user_paused_job(user_id, bot_id=bot_id)

    target_job = active_job or paused_job
    if not target_job:
        await message.reply_text("ℹ️ You have no active or paused downloads running.")
        return

    job = JobCheckpoint.from_dict(target_job)
    completed = len(job.completed_indices)
    pct = (completed / job.total_items * 100.0) if job.total_items > 0 else 0.0

    await message.reply_text(
        f"╭─── 📊 <b>JOB STATUS</b> ───╮\n"
        f"│ 🆔 <b>Job ID:</b> <code>{job.job_id}</code>\n"
        f"│ 📚 <b>Course:</b> {job.batch_name or 'Course'}\n"
        f"│ 🎬 <b>Item:</b> {job.current_index + 1}/{job.total_items}\n"
        f"│ 📌 <b>Current:</b> <code>{job.current_item_title or 'Processing...'}</code>\n"
        f"│\n"
        f"│ 📊 <b>Progress:</b> {completed}/{job.total_items} ({pct:.1f}%)\n"
        f"│ 🔄 <b>Phase:</b> {job.phase}\n"
        f"│ 🚦 <b>Status:</b> <code>{job.status}</code>\n"
        f"╰──────────────────╯\n\n"
        "Controls: /stop | /resume | /cancel"
    )


async def job_stop_cmd(client: Client, message: Message):
    bot_ctx = get_bot_context(client)
    bot_id = bot_ctx.bot_id if bot_ctx else None
    user_id = message.from_user.id
    jm = get_job_manager(client, bot_id=bot_id)
    success, msg, job = jm.pause_job(user_id, bot_id=bot_id)
    if success and job:
        completed = len(job.completed_indices)
        await message.reply_text(
            f"⏸️ <b>Download Paused</b>\n\n"
            f"• <b>Job ID:</b> <code>{job.job_id}</code>\n"
            f"• <b>Progress:</b> {completed}/{job.total_items}\n"
            f"• <b>Checkpoint Saved:</b> Yes\n\n"
            "Use <code>/resume</code> to continue from this exact point."
        )
    else:
        await message.reply_text(f"⚠️ {msg}")


async def job_resume_cmd(client: Client, message: Message):
    bot_ctx = get_bot_context(client)
    bot_id = bot_ctx.bot_id if bot_ctx else None
    user_id = message.from_user.id
    jm = get_job_manager(client, bot_id=bot_id)
    if jm.is_user_busy(user_id, bot_id=bot_id):
        await message.reply_text("⚠️ You already have an active download running. Use /status to check.")
        return

    paused_dict = db.get_user_paused_job(user_id, bot_id=bot_id)
    if not paused_dict:
        await message.reply_text("ℹ️ No paused download found for your account.")
        return

    job = JobCheckpoint.from_dict(paused_dict)
    completed = len(job.completed_indices)
    await message.reply_text(
        f"▶️ <b>Resuming Download</b>\n\n"
        f"• <b>Job ID:</b> <code>{job.job_id}</code>\n"
        f"• <b>Resuming from item:</b> {completed + 1}/{job.total_items}\n\n"
        "Continuing execution in background..."
    )
    await jm.launch_job(job, client, execute_job_pipeline)


async def job_cancel_cmd(client: Client, message: Message):
    bot_ctx = get_bot_context(client)
    bot_id = bot_ctx.bot_id if bot_ctx else None
    user_id = message.from_user.id
    jm = get_job_manager(client, bot_id=bot_id)
    success, msg = jm.cancel_job(user_id, bot_id=bot_id)
    if success:
        await message.reply_text("🛑 <b>Download Cancelled</b>\n\n<blockquote>Your job has been cancelled and temporary files have been cleaned up.</blockquote>")
    else:
        await message.reply_text(f"⚠️ {msg}")


async def list_user_jobs_cmd(client: Client, message: Message):
    bot_ctx = get_bot_context(client)
    bot_id = bot_ctx.bot_id if bot_ctx else None
    user_id = message.from_user.id
    user_jobs = db.list_jobs(user_id=user_id, bot_id=bot_id)
    if not user_jobs:
        await message.reply_text("📝 You have no recent jobs recorded.")
        return

    lines = ["<b>📋 Your Recent Jobs:</b>\n"]
    for j in user_jobs[-10:]:
        jid = j.get("job_id")
        st = j.get("status", "UNKNOWN")
        comp = j.get("completed_count", 0)
        tot = j.get("total_items", 0)
        lines.append(f"• <code>{jid}</code> — <b>{st}</b> ({comp}/{tot})")

    await message.reply_text("\n".join(lines))


# ==============================================================================
# 🛠️ TOOLS: /t2t, /t2h
# ==============================================================================

async def text_to_txt_cmd(client: Client, message: Message):
    editable = await message.reply_text("<b>📝 Text to .txt Converter</b>\n\n<blockquote>Please send the text content to convert:</blockquote>")
    input_msg: Message = await client.listen(chat_id=message.chat.id, user_id=message.from_user.id, timeout=120)
    if not input_msg.text:
        await message.reply_text("❌ Invalid text received.")
        return

    text_data = input_msg.text.strip()
    await input_msg.delete()

    await editable.edit("<b>🏷️ Send custom filename or send /d for default:</b>")
    fname_msg: Message = await client.listen(chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
    fname = "course_links" if fname_msg.text.strip() == "/d" else helper.safe_filename(fname_msg.text.strip())
    await fname_msg.delete()
    await editable.delete()

    os.makedirs("downloads", exist_ok=True)
    txt_path = f"downloads/{fname}.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text_data)

    await message.reply_document(document=txt_path, caption=f"📄 <code>{fname}.txt</code>\n\n<blockquote>Ready for /drm downloader!</blockquote>")
    try:
        os.remove(txt_path)
    except Exception:
        pass


async def t2h_cmd(client: Client, message: Message):
    await html_handler(client, message)


# ==============================================================================
# 📥 CORE DOWNLOADER WORKFLOW: /drm
# ==============================================================================

async def drm_cmd(client: Client, message: Message, doc_message: Message = None):
    user_id = message.from_user.id if message.from_user else 0
    bot_ctx = get_bot_context(client)
    bot_id = bot_ctx.bot_id if bot_ctx else "bot_1"
    bot_uname = bot_ctx.bot_username or get_bot_username(client)

    jm = get_job_manager(client, bot_id=bot_id)
    if jm.is_user_busy(user_id, bot_id=bot_id):
        await message.reply_text("⚠️ You already have an active download running! Use /status to check or /stop to pause it.")
        return

    # Check for direct URL in message arguments (e.g. /drm https://...)
    cmd_text = (message.text or message.caption or "").strip()
    cmd_parts = cmd_text.split(maxsplit=1)
    if len(cmd_parts) > 1 and cmd_parts[1].startswith(("http://", "https://")):
        target_url = cmd_parts[1].strip()
        status_info = check_media_drm_status(target_url)
        res_card = format_drm_result_card(
            title=status_info.get("title", "Media Stream"),
            is_drm=status_info.get("is_drm", False),
            media_type=status_info.get("type", "Stream"),
            details=status_info.get("details")
        )
        await message.reply_text(res_card)
        return

    direct_doc = doc_message or (message if message.document and message.document.file_name and message.document.file_name.lower().endswith(".txt") else None)

    if direct_doc and direct_doc.document:
        editable = await message.reply_text("📥 <b>Reading batch text file...</b>")
        input_doc = direct_doc
        doc_path = await input_doc.download()
    else:
        prompt_text, prompt_markup = format_drm_input_card(user_id=user_id)
        editable = await message.reply_text(prompt_text, reply_markup=prompt_markup)

        try:
            input_doc: Message = await client.listen(chat_id=message.chat.id, user_id=user_id, timeout=120)
        except asyncio.TimeoutError:
            await editable.edit("⏱ Timed out waiting for file or URL. Send /drm when ready.")
            return

        # Check for cancel or URL input
        if input_doc.text:
            in_text = input_doc.text.strip()
            if in_text.lower() in ("/cancel", "cancel"):
                try:
                    await input_doc.delete(True)
                except Exception:
                    pass
                await editable.edit("❌ <b>Operation cancelled.</b>")
                return

            if in_text.startswith(("http://", "https://")):
                try:
                    await input_doc.delete(True)
                except Exception:
                    pass
                status_info = check_media_drm_status(in_text)
                res_card = format_drm_result_card(
                    title=status_info.get("title", "Media Stream"),
                    is_drm=status_info.get("is_drm", False),
                    media_type=status_info.get("type", "Stream"),
                    details=status_info.get("details")
                )
                await editable.edit(res_card)
                return

        if not input_doc.document or not input_doc.document.file_name.endswith('.txt'):
            await editable.edit("❌ Please send a valid <code>.txt</code> file or media URL!")
            return

        doc_path = await input_doc.download()
        try:
            await input_doc.delete(True)
        except Exception:
            pass

    try:
        with open(doc_path, "r", encoding="utf-8", errors="ignore") as f:
            content_str = f.read()
    except Exception as e:
        await editable.edit(f"❌ Error reading file: {e}")
        if os.path.exists(doc_path):
            os.remove(doc_path)
        return

    orig_filename = input_doc.document.file_name or "course.txt"
    course_data = parse_course_txt(content_str, orig_filename)
    total_links = len(course_data.all_items)

    if total_links == 0:
        await editable.edit("❌ No valid links found in the text file.")
        if os.path.exists(doc_path):
            os.remove(doc_path)
        return

    # Category stats
    pdf_count = sum(1 for itm in course_data.all_items if itm.category == "pdf")
    img_count = sum(1 for itm in course_data.all_items if itm.category == "image")
    zip_count = sum(1 for itm in course_data.all_items if itm.category == "zip")
    vid_count = total_links - (pdf_count + img_count + zip_count)

    units_count = len([u for u in course_data.units if u.number != 0])
    card_1, mark_1 = format_universal_input_card(
        "BATCH START INDEX",
        f"Subject: {course_data.subject}\nCourse: {course_data.course}\nUnits: {units_count} | Items: {total_links}\n🎥 Videos: {vid_count} | 📑 PDFs: {pdf_count}\n\nSend Start Index (1 - {total_links}) or send /d for 1:",
        example="1 or /d",
        cancel_callback="input_cancel",
        user_id=user_id
    )
    await editable.edit(card_1, reply_markup=mark_1)

    # 1. Start Index
    try:
        idx_msg: Message = await client.listen(chat_id=message.chat.id, user_id=user_id, timeout=30)
        idx_text = idx_msg.text.strip() if idx_msg.text else "/d"
        await idx_msg.delete(True)
        if idx_text.lower() in ("/cancel", "cancel"):
            await editable.edit("❌ <b>Batch operation cancelled.</b>")
            if os.path.exists(doc_path):
                os.remove(doc_path)
            return
        start_idx = 1 if idx_text == "/d" else int(idx_text)
    except Exception:
        start_idx = 1
    start_idx = max(1, min(start_idx, total_links))

    # 2. Batch Name
    card_2, mark_2 = format_universal_input_card(
        "BATCH NAME",
        f"Send Batch Name or send /d for default ('{course_data.course}'):",
        example=course_data.course,
        cancel_callback="input_cancel",
        user_id=user_id
    )
    await editable.edit(card_2, reply_markup=mark_2)
    try:
        b_msg: Message = await client.listen(chat_id=message.chat.id, user_id=user_id, timeout=30)
        b_text = b_msg.text.strip() if b_msg.text else "/d"
        await b_msg.delete(True)
        if b_text.lower() in ("/cancel", "cancel"):
            await editable.edit("❌ <b>Batch operation cancelled.</b>")
            if os.path.exists(doc_path):
                os.remove(doc_path)
            return
        batch_name = course_data.course if b_text == "/d" else b_text
    except Exception:
        batch_name = course_data.course

    # 3. Resolution
    card_3, mark_3 = format_universal_input_card(
        "VIDEO RESOLUTION",
        "Select video resolution:\n• 360p\n• 480p (Recommended)\n• 720p\n• 1080p\n• 2160p (4K UHD)\n\nSend resolution or /d for 480p:",
        example="720 or /d",
        cancel_callback="input_cancel",
        user_id=user_id
    )
    await editable.edit(card_3, reply_markup=mark_3)
    try:
        res_msg: Message = await client.listen(chat_id=message.chat.id, user_id=user_id, timeout=30)
        res_text = res_msg.text.strip() if res_msg.text else "/d"
        await res_msg.delete(True)
        if res_text.lower() in ("/cancel", "cancel"):
            await editable.edit("❌ <b>Batch operation cancelled.</b>")
            if os.path.exists(doc_path):
                os.remove(doc_path)
            return
        quality = "480p" if res_text in ("/d", "") else (f"{res_text}p" if not res_text.endswith("p") else res_text)
    except Exception:
        quality = "480p"

    # 4. Watermark
    card_4, mark_4 = format_universal_input_card(
        "WATERMARK SETTINGS",
        f"Send custom watermark text, /d for '{WATERMARK_TEXT}', or 'no' to disable:",
        example=f"/d or {WATERMARK_TEXT}",
        cancel_callback="input_cancel",
        user_id=user_id
    )
    await editable.edit(card_4, reply_markup=mark_4)
    try:
        wm_msg: Message = await client.listen(chat_id=message.chat.id, user_id=user_id, timeout=30)
        wm_text = wm_msg.text.strip() if wm_msg.text else "/d"
        await wm_msg.delete(True)
        if wm_text.lower() in ("/cancel", "cancel"):
            await editable.edit("❌ <b>Batch operation cancelled.</b>")
            if os.path.exists(doc_path):
                os.remove(doc_path)
            return
        wm_val = WATERMARK_TEXT if wm_text == "/d" else wm_text
    except Exception:
        wm_val = WATERMARK_TEXT

    # 5. Credit
    card_5, mark_5 = format_universal_input_card(
        "CAPTION CREDIT",
        "Send caption credit or send /d for default:",
        example="/d",
        cancel_callback="input_cancel",
        user_id=user_id
    )
    await editable.edit(card_5, reply_markup=mark_5)
    try:
        cr_msg: Message = await client.listen(chat_id=message.chat.id, user_id=user_id, timeout=30)
        cr_text = cr_msg.text.strip() if cr_msg.text else "/d"
        await cr_msg.delete(True)
        if cr_text.lower() in ("/cancel", "cancel"):
            await editable.edit("❌ <b>Batch operation cancelled.</b>")
            if os.path.exists(doc_path):
                os.remove(doc_path)
            return
        credit_val = CREDIT if cr_text == "/d" else cr_text
    except Exception:
        credit_val = CREDIT

    # 6. PW Token (optional)
    card_6, mark_6 = format_universal_input_card(
        "PW TOKEN (OPTIONAL)",
        "Send PW Token or send /d for none:",
        example="/d",
        cancel_callback="input_cancel",
        user_id=user_id
    )
    await editable.edit(card_6, reply_markup=mark_6)
    try:
        pw_msg: Message = await client.listen(chat_id=message.chat.id, user_id=user_id, timeout=30)
        pw_text = pw_msg.text.strip() if pw_msg.text else "/d"
        await pw_msg.delete(True)
        if pw_text.lower() in ("/cancel", "cancel"):
            await editable.edit("❌ <b>Batch operation cancelled.</b>")
            if os.path.exists(doc_path):
                os.remove(doc_path)
            return
        pw_token = "/d" if pw_text == "/d" else pw_text
    except Exception:
        pw_token = "/d"

    # 7. Thumbnail
    batch_thumb_url = getattr(course_data, "structured_batch", None).thumbnail if getattr(course_data, "structured_batch", None) else None
    card_7, mark_7 = format_universal_input_card(
        "THUMBNAIL SETUP",
        "Send a photo for custom thumbnail, /d for default, or /skip to skip:",
        example="/d or send a photo",
        cancel_callback="input_cancel",
        user_id=user_id
    )
    await editable.edit(card_7, reply_markup=mark_7)
    thumb_val = batch_thumb_url if batch_thumb_url else "/d"
    try:
        th_msg: Message = await client.listen(chat_id=message.chat.id, user_id=user_id, timeout=30)
        if th_msg.text and th_msg.text.strip().lower() in ("/cancel", "cancel"):
            await th_msg.delete(True)
            await editable.edit("❌ <b>Batch operation cancelled.</b>")
            if os.path.exists(doc_path):
                os.remove(doc_path)
            return

        if th_msg.photo:
            os.makedirs("downloads", exist_ok=True)
            custom_th = f"downloads/thumb_{user_id}.jpg"
            await client.download_media(message=th_msg.photo, file_name=custom_th)
            thumb_val = custom_th
        elif th_msg.text:
            if th_msg.text.strip() in ("/skip", "no"):
                thumb_val = "no"
            elif th_msg.text.strip() == "/d":
                thumb_val = batch_thumb_url if batch_thumb_url else "/d"
            else:
                thumb_val = "/d"
        await th_msg.delete(True)
    except Exception:
        thumb_val = batch_thumb_url if batch_thumb_url else "/d"

    # 8. Channel ID
    card_8, mark_8 = format_universal_input_card(
        "DESTINATION CHANNEL",
        "Send Channel ID (e.g. -1001234567890) or /d to upload right here:",
        example="/d or -1001234567890",
        cancel_callback="input_cancel",
        user_id=user_id
    )
    await editable.edit(card_8, reply_markup=mark_8)
    try:
        ch_msg: Message = await client.listen(chat_id=message.chat.id, user_id=user_id, timeout=30)
        ch_text = ch_msg.text.strip() if ch_msg.text else "/d"
        await ch_msg.delete(True)
        if ch_text.lower() in ("/cancel", "cancel"):
            await editable.edit("❌ <b>Batch operation cancelled.</b>")
            if os.path.exists(doc_path):
                os.remove(doc_path)
            return
        channel_id = message.chat.id if ch_text == "/d" else int(ch_text)
    except Exception:
        channel_id = message.chat.id

    await editable.delete()

    # Create persistent job checkpoint
    job = jm.create_job(
        user_id=user_id,
        chat_id=message.chat.id,
        channel_id=channel_id,
        source_filename=orig_filename,
        raw_content=content_str,
        total_items=total_links,
        start_index=start_idx,
        batch_name=batch_name,
        quality=quality,
        watermark=wm_val,
        credit=credit_val,
        pw_token=pw_token,
        thumb=thumb_val,
        bot_id=bot_id,
        bot_username=bot_uname
    )

    if os.path.exists(doc_path):
        try:
            os.remove(doc_path)
        except Exception:
            pass

    # Launch background job pipeline
    await message.reply_text(
        f"🚀 <b>Task Created Successfully!</b>\n\n"
        f"• <b>Job ID:</b> <code>{job.job_id}</code>\n"
        f"• <b>Course:</b> {batch_name}\n"
        f"• <b>Items:</b> {total_links}\n"
        f"• <b>Quality:</b> {quality}\n\n"
        "Download started in background. Use /status to check progress."
    )
    await jm.launch_job(job, client, execute_job_pipeline)


# ==============================================================================
# ⚙️ DESTINATION & TOPIC MANAGEMENT (Multi-Type Telegram Dispatcher)
# ==============================================================================

# Destination cache
_DESTINATION_CACHE: Dict[Any, Dict[str, Any]] = {}

async def resolve_destination(client: Client, chat_id: Union[int, str]) -> Dict[str, Any]:
    """
    Resolves Telegram destination type cleanly:
    A) PRIVATE CHAT (id > 0 or type == 'private') -> is_forum: False, thread_id: None
    B) NORMAL GROUP (type == 'group') -> is_forum: False, thread_id: None
    C) SUPERGROUP WITHOUT FORUM (type == 'supergroup', is_forum == False) -> is_forum: False, thread_id: None
    D) FORUM-ENABLED SUPERGROUP (type == 'supergroup', is_forum == True) -> is_forum: True
    E) CHANNEL (type == 'channel') -> is_forum: False, thread_id: None
    """
    try:
        cid = int(chat_id)
    except (ValueError, TypeError):
        cid = chat_id

    bot_ctx = get_bot_context(client)
    bot_id = bot_ctx.bot_id if bot_ctx else "bot"
    cache_key = f"{bot_id}:{cid}"

    if cache_key in _DESTINATION_CACHE:
        return _DESTINATION_CACHE[cache_key]

    # Quick check for private user ID
    if isinstance(cid, int) and cid > 0:
        res = {
            "chat_id": cid,
            "type": "private",
            "is_forum": False,
            "title": "Private Chat",
            "can_manage_topics": False
        }
        _DESTINATION_CACHE[cache_key] = res
        return res

    try:
        chat = await client.get_chat(cid)
        raw_type = getattr(chat, "type", "")
        type_str = str(getattr(raw_type, "value", raw_type)).lower()
        if "supergroup" in type_str:
            c_type = "supergroup"
        elif "channel" in type_str:
            c_type = "channel"
        elif "group" in type_str:
            c_type = "group"
        elif "private" in type_str:
            c_type = "private"
        else:
            c_type = type_str

        is_forum = bool(getattr(chat, "is_forum", False)) and (c_type == "supergroup")
        title = getattr(chat, "title", str(cid))
        
        can_manage_topics = False
        if is_forum:
            try:
                member = await client.get_chat_member(cid, "me")
                privileges = getattr(member, "privileges", None)
                can_manage_topics = bool(getattr(privileges, "can_manage_topics", False)) if privileges else False
            except Exception:
                can_manage_topics = False

        res = {
            "chat_id": cid,
            "type": c_type,
            "is_forum": is_forum,
            "title": title,
            "can_manage_topics": can_manage_topics
        }
        _DESTINATION_CACHE[cache_key] = res
        return res
    except Exception as exc:
        logger.debug(f"[DESTINATION] Could not inspect chat {cid}: {exc}")
        res = {
            "chat_id": cid,
            "type": "unknown",
            "is_forum": False,
            "title": str(cid),
            "can_manage_topics": False
        }
        _DESTINATION_CACHE[cache_key] = res
        _DESTINATION_CACHE[cid] = res
        return res


async def build_send_kwargs(client: Client, chat_id: Union[int, str], topic_thread_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Returns kwargs for Pyrogram send functions.
    Crucially: message_thread_id is ONLY attached when destination is a verified forum-enabled supergroup
    and topic_thread_id is a valid positive integer.
    Never returns message_thread_id=None or forum parameters for private/group/channel.
    """
    if topic_thread_id is None:
        return {}
    try:
        tid = int(topic_thread_id)
        if tid <= 0:
            return {}
    except (ValueError, TypeError):
        return {}

    dest = await resolve_destination(client, chat_id)
    if dest.get("is_forum") and dest.get("type") == "supergroup":
        return {"message_thread_id": tid}
    return {}


async def send_text_to_destination(
    client: Client,
    chat_id: Union[int, str],
    text: str,
    topic_thread_id: Optional[int] = None,
    parse_mode=enums.ParseMode.HTML,
    disable_web_page_preview: bool = True,
    reply_markup=None
) -> Message:
    """Centralized text dispatcher with destination-aware thread ID management."""
    kwargs = await build_send_kwargs(client, chat_id, topic_thread_id)
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    kwargs["disable_web_page_preview"] = disable_web_page_preview
    return await client.send_message(chat_id=chat_id, text=text, **kwargs)


async def send_document_to_destination(
    client: Client,
    chat_id: Union[int, str],
    document: str,
    caption: str = "",
    topic_thread_id: Optional[int] = None,
    parse_mode=enums.ParseMode.HTML,
    thumb: Optional[str] = None,
    progress=None,
    progress_args=None
) -> Message:
    """Centralized document dispatcher with destination-aware thread ID management."""
    kwargs = await build_send_kwargs(client, chat_id, topic_thread_id)
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    if thumb and os.path.exists(thumb):
        kwargs["thumb"] = thumb
    if progress:
        kwargs["progress"] = progress
        if progress_args:
            kwargs["progress_args"] = progress_args
    return await client.send_document(chat_id=chat_id, document=document, caption=caption, **kwargs)


def build_academic_topic_title(subject: str, unit_num: Any, unit_title: str, topic_title: Optional[str] = None, is_live: bool = False, multiple_subjects: bool = False) -> str:
    """Builds clean, flat Telegram Forum Topic title according to academic organization rules."""
    subj_str = (subject or "General").strip()
    u_title = (unit_title or "General").strip()
    prefix = "LIVE — " if is_live else "RECORDED — "

    if multiple_subjects and subj_str.lower() != "general":
        if unit_num not in (0, "0", "General", None, ""):
            title = f"{prefix}📚 {subj_str.upper()} — UNIT {unit_num} — {u_title.upper()}"
        else:
            title = f"{prefix}📚 {subj_str.upper()} — {u_title.upper()}"
    else:
        if unit_num not in (0, "0", "General", None, ""):
            title = f"{prefix}UNIT {unit_num} — {u_title.upper()}"
        else:
            title = f"{prefix}{u_title.upper()}"

    if topic_title and topic_title.upper() != u_title.upper():
        title = f"{title} — {topic_title.upper()}"

    return title[:128]


async def validate_forum_chat(client: Client, chat_id: Union[int, str]) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Validates forum destination chat access, forum supergroup status, and bot admin permissions.
    Returns (is_valid, error_or_success_message, details_dict).
    """
    try:
        chat = await client.get_chat(chat_id)
    except BadRequest as br:
        err_str = str(br)
        if "CHANNEL_INVALID" in err_str or "CHAT_INVALID" in err_str:
            return False, f"Chat ID {chat_id} is invalid or bot cannot access it (CHANNEL_INVALID).", {}
        if "CHAT_ADMIN_REQUIRED" in err_str or "USER_NOT_PARTICIPANT" in err_str:
            return False, f"Bot is not a member or administrator of chat {chat_id}.", {}
        return False, f"Telegram API error accessing chat {chat_id}: {br}", {}
    except Exception as exc:
        return False, f"Failed to access chat {chat_id}: {exc}", {}

    chat_type = str(getattr(chat, "type", "")).lower()
    is_forum = getattr(chat, "is_forum", False)
    title = getattr(chat, "title", str(chat_id))

    try:
        member = await client.get_chat_member(chat_id, "me")
        privileges = getattr(member, "privileges", None)
        can_manage_topics = getattr(privileges, "can_manage_topics", False) if privileges else False
        can_post_messages = getattr(privileges, "can_post_messages", True) if privileges else True
    except Exception:
        member = None
        can_manage_topics = False
        can_post_messages = True

    details = {
        "title": title,
        "chat_id": chat_id,
        "type": chat_type,
        "is_forum": is_forum,
        "can_manage_topics": can_manage_topics,
        "can_post_messages": can_post_messages
    }

    if not is_forum:
        return True, f"Chat '{title}' ({chat_id}) is a standard {chat_type} (Non-forum mode).", details

    if not can_manage_topics:
        return True, f"Chat '{title}' is a Forum Supergroup, but bot lacks 'Manage Topics' admin permission. Ensure bot has admin rights.", details

    return True, f"Chat '{title}' is a verified Forum Supergroup with topic management permissions.", details


async def create_forum_topic_safe(client: Client, chat_id: Union[int, str], title: str) -> Optional[int]:
    """
    Creates a Telegram Forum Topic safely in supergroups,
    handling Pyrofork / Pyrogram layer compatibility.
    Returns the integer message_thread_id (topic ID).
    """
    # 1. High-level client method
    try:
        if hasattr(client, "create_forum_topic"):
            res = await client.create_forum_topic(chat_id=chat_id, title=title[:128])
            if res:
                tid = getattr(res, "id", None) or getattr(res, "message_thread_id", None)
                if isinstance(tid, int) and tid > 0:
                    return tid
    except Exception as e1:
        logger.debug(f"[FORUM TOPIC] High-level create_forum_topic: {e1}")

    # 2. Fallback to raw invoke CreateForumTopic
    try:
        from pyrogram import raw
        peer = await client.resolve_peer(chat_id)
        r = await client.invoke(
            raw.functions.messages.CreateForumTopic(
                peer=peer,
                title=title[:128],
                random_id=client.rnd_id()
            )
        )
        if hasattr(r, "updates"):
            for u in r.updates:
                msg = getattr(u, "message", None)
                if msg and getattr(msg, "id", None):
                    return int(msg.id)
                if getattr(u, "id", None) and getattr(u, "id", None) > 1:
                    return int(u.id)
        elif hasattr(r, "id"):
            return int(r.id)
    except Exception as e2:
        logger.warning(f"[FORUM TOPIC] Raw CreateForumTopic failed for chat {chat_id}: {e2}")

    return None


async def resolve_or_create_forum_topic(
    client: Client,
    chat_id: Union[int, str],
    subject: str,
    unit_num: Any = 0,
    unit_title: str = "General",
    topic_title: Optional[str] = None,
    is_live: bool = False,
    multiple_subjects: bool = False,
    bot_id: Optional[str] = None,
    custom_title: Optional[str] = None,
    topic_key: Optional[str] = None
) -> Optional[int]:
    """
    Checks if destination is a real forum supergroup.
    If yes, checks persistent storage for existing message_thread_id.
    If missing, creates a REAL Telegram Forum Topic in the supergroup and saves the ID.
    Returns message_thread_id or None if chat is not a forum supergroup.
    """
    # Guard: Only forum-enabled supergroups have topics
    dest = await resolve_destination(client, chat_id)
    if not dest.get("is_forum") or dest.get("type") != "supergroup":
        return None

    bot_ctx = get_bot_context(client)
    effective_bot_id = bot_id or (bot_ctx.bot_id if bot_ctx else None)

    if not topic_key:
        norm_subj = normalize_title(subject or "general")
        norm_unit_title = normalize_title(unit_title or "general")
        u_num_str = str(unit_num) if unit_num not in (0, "0", "General", None, "") else "0"
        topic_key = f"{'live' if is_live else 'recorded'}|{norm_subj}|unit_{u_num_str}|{norm_unit_title}"
        if topic_title:
            topic_key += f"|{normalize_title(topic_title)}"

    cached_id = db.get_topic_id(chat_id, topic_key, bot_id=effective_bot_id)
    if cached_id:
        return cached_id

    # Create Real Forum Topic in Supergroup
    topic_header = custom_title or build_academic_topic_title(subject, unit_num, unit_title, topic_title, is_live, multiple_subjects=multiple_subjects)
    try:
        thread_id = await create_forum_topic_safe(client=client, chat_id=chat_id, title=topic_header)
        if thread_id:
            db.save_topic_id(chat_id, topic_key, thread_id, bot_id=effective_bot_id)
            logger.info(f"[{effective_bot_id or 'FORUM'}] Created REAL Forum Topic '{topic_header}' -> ID {thread_id} in group {chat_id}")
            return thread_id
    except BadRequest as br:
        logger.warning(f"[{effective_bot_id or 'FORUM'}] Cannot create forum topic in chat {chat_id}: {br}")
    except Exception as e:
        logger.warning(f"[{effective_bot_id or 'FORUM'}] Forum topic creation exception: {e}")

    return None


# ==============================================================================
# ⚙️ CORE PIPELINE EXECUTOR (Restart-Safe, Checkpoint-Driven, DRM-Safe)
# ==============================================================================

async def execute_job_pipeline(job: JobCheckpoint, bot_client: Client, manager):
    """
    Executes a download job item-by-item with real-time checkpoints,
    dynamic progress tracking, forum topic support, and restart safety.
    """
    course_data = parse_course_txt(job.raw_content, job.source_filename)
    all_items = course_data.all_items
    total_items = len(all_items)

    channel_id = job.channel_id
    b_name = job.batch_name or course_data.course or "Course"
    quality = job.quality
    watermark = job.watermark
    credit = job.credit
    thumb = job.thumb
    user_id = job.user_id
    job_id = job.job_id

    # Per-job isolated directory
    job_temp = job.temp_dir or os.path.join(TEMP_DIR, str(user_id), job_id)
    downloads_dir = os.path.join(job_temp, "downloads")
    os.makedirs(downloads_dir, exist_ok=True)

    active_unit = None
    active_thread_id = job.active_thread_id
    active_unit_status_msg = None
    unit_items_processed = 0
    unit_items_failed = 0

    progress_tracker = JobProgressTracker(job_id=job_id, course_name=b_name, total_items=total_items)
    prog_msg = None

    # Initialize GENERAL batch area in forum-enabled destination or send batch header
    if job.current_index == 0 and len(job.completed_indices) == 0:
        dest_info = await resolve_destination(bot_client, channel_id)
        gen_thread_id = None
        if dest_info.get("is_forum") and dest_info.get("type") == "supergroup":
            gen_thread_id = await resolve_or_create_forum_topic(
                client=bot_client,
                chat_id=channel_id,
                subject="General",
                unit_num=0,
                unit_title="General Discussion & Course Overview",
                is_live=False,
                multiple_subjects=False
            )

        gen_send_kwargs = await build_send_kwargs(bot_client, channel_id, gen_thread_id)
        batch_caption = (
            f"🎓 <b>COURSE WALLAH</b>\n\n"
            f"📚 <b>Course:</b> {b_name}\n"
            f"📝 <b>Subject:</b> {course_data.subject}\n"
            f"📦 <b>Total Items:</b> {total_items}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 <b>Downloaded via:</b> {credit or CREDIT}"
        )
        if thumb and thumb not in ("/d", "no", "none", ""):
            if os.path.exists(thumb) or thumb.startswith("http://") or thumb.startswith("https://"):
                try:
                    await bot_client.send_photo(chat_id=channel_id, photo=thumb, caption=batch_caption, parse_mode=enums.ParseMode.HTML, **gen_send_kwargs)
                except Exception as e:
                    logger.debug(f"[GENERAL] Failed to send custom batch thumbnail: {e}")
        else:
            # Check if intro video exists among items (e.g. title contains "intro" or "overview")
            intro_item = next((itm for itm in all_items if any(k in itm.title.lower() for k in ("intro", "introduction", "overview", "syllabus", "orientation")) and itm.category == "video"), None)
            if intro_item:
                intro_caption = f"{batch_caption}\n\n🎬 <b>Intro Video:</b> {intro_item.title}"
                try:
                    await bot_client.send_message(chat_id=channel_id, text=intro_caption, parse_mode=enums.ParseMode.HTML, **gen_send_kwargs)
                except Exception:
                    pass
            else:
                try:
                    await bot_client.send_message(chat_id=channel_id, text=batch_caption, parse_mode=enums.ParseMode.HTML, **gen_send_kwargs)
                except Exception:
                    pass

    multiple_subjs = len({itm.subject_name for itm in all_items if itm.subject_name and itm.subject_name != "General"}) > 1

    for i in range(job.current_index, total_items):
        # 1. Check for cancellation / pause request
        if manager.cancel_flags.get(job_id, False):
            logger.info(f"Job {job_id} paused/cancelled at index {i}.")
            break

        # 2. Skip already completed items (Restart Safety)
        if i in job.completed_indices:
            continue

        item: AcademicItem = all_items[i]
        job.current_index = i
        job.current_item_title = item.title
        job.phase = "DOWNLOADING"
        db.save_job(job.to_dict())

        # 3. Unit / Topic transition handling
        target_unit = None
        if course_data.units:
            for u in course_data.units:
                if item in u.items or (item.unit_title == u.title and item.unit_number == u.number):
                    target_unit = u
                    break
        if target_unit is None and course_data.units:
            target_unit = course_data.units[0]

        if target_unit is not None and target_unit != active_unit:
            # Finalize previous unit status message
            if active_unit is not None and active_unit_status_msg is not None:
                try:
                    done_text = build_unit_status_message(
                        course_data.subject, b_name, active_unit,
                        total_unit_items=len(active_unit.items),
                        failed_unit_items=unit_items_failed, is_done=True
                    )
                    await active_unit_status_msg.edit_text(done_text, parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass

            active_unit = target_unit
            unit_items_processed = 0
            unit_items_failed = 0
            active_thread_id = None

            # Resolve or create REAL Telegram Forum topic
            if getattr(course_data, "format_type", None) == "bracket_topic":
                b_ctx = get_bot_context(bot_client)
                eff_bid = b_ctx.bot_id if b_ctx else None
                active_thread_id = await resolve_or_create_forum_topic(
                    client=bot_client,
                    chat_id=channel_id,
                    subject=active_unit.title,
                    unit_num=active_unit.number,
                    unit_title=active_unit.title,
                    topic_title=None,
                    is_live=False,
                    multiple_subjects=False,
                    bot_id=eff_bid,
                    custom_title=active_unit.display_header,
                    topic_key=f"bracket_topic:{normalize_title(active_unit.title)}"
                )
            else:
                active_thread_id = await resolve_or_create_forum_topic(
                    client=bot_client,
                    chat_id=channel_id,
                    subject=course_data.subject,
                    unit_num=active_unit.number,
                    unit_title=active_unit.title,
                    topic_title=item.topic_title if hasattr(item, "topic_title") else None,
                    is_live=False,
                    multiple_subjects=multiple_subjs
                )

            job.active_thread_id = active_thread_id

            # If normal channel fallback (not a forum group or cannot create topic), send Unit Header
            if active_thread_id is None:
                try:
                    hdr_text = build_unit_header_message(course_data.subject, b_name, active_unit)
                    hdr_msg = await bot_client.send_message(chat_id=channel_id, text=hdr_text, parse_mode=enums.ParseMode.HTML)
                    try:
                        await bot_client.pin_chat_message(chat_id=channel_id, message_id=hdr_msg.id)
                    except Exception:
                        pass
                except Exception:
                    pass

        send_kwargs = await build_send_kwargs(bot_client, channel_id, active_thread_id)
        name_clean = helper.safe_filename(item.title)
        url = item.url
        raw_url = url
        temp_files_to_clean = []

        try:
            # 4. Progress message
            prog_text = format_download_card(
                course_name=b_name,
                title=item.title,
                unit_title=active_unit.title if active_unit else None,
                subject_name=course_data.subject if course_data.subject != "General" else None,
                frame=0
            )
            try:
                prog_msg = await bot_client.send_message(job.chat_id, prog_text, disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
                b_ctx = get_bot_context(bot_client)
                eff_bot_id = b_ctx.bot_id if b_ctx else getattr(job, "bot_id", "bot_1")
                if b_ctx and prog_msg:
                    b_ctx.status_messages[(job.chat_id, job.job_id)] = prog_msg.id
            except Exception:
                b_ctx = get_bot_context(bot_client)
                eff_bot_id = b_ctx.bot_id if b_ctx else getattr(job, "bot_id", "bot_1")
                prog_msg = None

            # =================================================================
            # 5. ITEM ROUTING, DOWNLOAD & MULTI-STREAM UPLOAD
            # =================================================================
            active_wm = watermark if watermark not in ("/d", "no", "none", "") else None
            custom_headers = {
                "User-Agent": "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
                "Referer": "https://player.akamai.net.in/",
                "Origin": "https://akstechnicalclasses.classx.co.in",
                "Accept": "*/*",
                "Connection": "keep-alive"
            }

            media_type = MediaRouter.classify_url(url, getattr(item, 'category', None))
            v_status = "NOT_AVAILABLE"
            pdf_status = "NOT_AVAILABLE"
            v_error = None
            pdf_error = None

            def _on_part_done(part_idx, item_idx=i+1):
                key = str(item_idx)
                if key not in job.uploaded_parts:
                    job.uploaded_parts[key] = []
                if part_idx not in job.uploaded_parts[key]:
                    job.uploaded_parts[key].append(part_idx)
                db.save_job(job.to_dict())

            # --- A. AppX / ClassX / Lecture API Source URL (Video + PDF Unified) ---
            if media_type == MediaType.APPX_LECTURE:
                target_q = quality if quality in ["144p", "240p", "360p", "480p", "720p", "1080p"] else None
                lec_res = await asyncio.to_thread(helper.resolve_lecture_source, url, target_q)

                if lec_res.is_drm:
                    fail_card = build_failure_card(
                        resource_type="DRM PROTECTED",
                        lecture_title=item.title,
                        course_name=b_name,
                        subject_name=course_data.subject if course_data.subject != "General" else None,
                        unit_title=active_unit.title if active_unit else None,
                        lecture_index=i + 1,
                        reason="Content is DRM encrypted and protected by the provider.",
                        technical_error=lec_res.drm_message or "Widevine DRM Protected",
                        video_status="FAILED",
                        pdf_status="FAILED",
                        credit=credit or CREDIT
                    )
                    try:
                        await bot_client.send_message(channel_id, fail_card, parse_mode=enums.ParseMode.HTML, **send_kwargs)
                    except Exception:
                        pass
                    raise RuntimeError(f"Content is DRM protected: {lec_res.drm_message or 'DRM Protected'}")

                if not lec_res.has_video and not lec_res.has_pdf:
                    raise RuntimeError(f"Lecture resolve error: {lec_res.error or 'No playable video or PDF found'}")

                # 1. Process Video Stream if available
                if lec_res.has_video and lec_res.video_url:
                    try:
                        if manager.cancel_flags.get(job_id, False):
                            break

                        job.phase = "DOWNLOADING"
                        db.save_job(job.to_dict())
                        if prog_msg:
                            try:
                                await prog_msg.edit_text(format_download_card(b_name, item.title, active_unit.title if active_unit else None, course_data.subject if course_data.subject != "General" else None, frame=1), parse_mode=enums.ParseMode.HTML)
                            except Exception:
                                pass

                        print(f"[APPX {job_id}] M3U8 DOWNLOAD START")
                        logging.info(f"[APPX {job_id}] M3U8 DOWNLOAD START")
                        v_file = await asyncio.to_thread(helper.download_appx_m3u8, lec_res.video_url, name_clean, None, downloads_dir)
                        if not v_file or not os.path.exists(v_file) or os.path.getsize(v_file) == 0:
                            raise RuntimeError("M3U8 video download produced empty or missing file.")
                        print(f"[APPX {job_id}] M3U8 DOWNLOAD SUCCESS")
                        logging.info(f"[APPX {job_id}] M3U8 DOWNLOAD SUCCESS")
                        temp_files_to_clean.append(v_file)

                        if manager.cancel_flags.get(job_id, False):
                            break

                        job.phase = "PROCESSING"
                        db.save_job(job.to_dict())
                        final_v_file = v_file
                        if active_wm and WATERMARK_TEXT:
                            job.phase = "WATERMARKING"
                            db.save_job(job.to_dict())
                            if prog_msg:
                                try:
                                    await prog_msg.edit_text(format_watermark_card(item.title, frame=0), parse_mode=enums.ParseMode.HTML)
                                except Exception:
                                    pass
                            print(f"[APPX {job_id}] WATERMARK START")
                            logging.info(f"[APPX {job_id}] WATERMARK START")
                            wm_res = await asyncio.to_thread(helper.apply_video_watermark, v_file, active_wm, WATERMARK_FILE)
                            if wm_res and os.path.exists(wm_res):
                                temp_files_to_clean.append(wm_res)
                                final_v_file = wm_res
                            print(f"[APPX {job_id}] WATERMARK SUCCESS")
                            logging.info(f"[APPX {job_id}] WATERMARK SUCCESS")

                        v_thumb = await asyncio.to_thread(helper.extract_or_download_thumbnail, final_v_file, lec_res.thumbnail, thumb)
                        if v_thumb and os.path.exists(v_thumb):
                            temp_files_to_clean.append(v_thumb)

                        caption_text = build_enriched_caption(
                            course_data.subject,
                            b_name,
                            item,
                            item_index=i + 1,
                            credit=credit
                        )

                        if manager.cancel_flags.get(job_id, False):
                            break

                        job.phase = "UPLOADING"
                        db.save_job(job.to_dict())
                        print(f"[APPX {job_id}] UPLOAD START")
                        logging.info(f"[APPX {job_id}] UPLOAD START")
                        async with manager.upload_semaphore:
                            await helper.send_vid(
                                bot_client,
                                prog_msg,
                                caption_text,
                                final_v_file,
                                v_thumb,
                                item.title,
                                prog_msg,
                                channel_id,
                                watermark=watermark,
                                topic_thread_id=active_thread_id,
                                parse_mode=enums.ParseMode.HTML,
                                user_id=user_id,
                                bot_id=eff_bot_id,
                                uploaded_parts=job.uploaded_parts.get(str(i + 1), []),
                                on_part_uploaded=_on_part_done
                            )
                        print(f"[APPX {job_id}] UPLOAD SUCCESS")
                        logging.info(f"[APPX {job_id}] UPLOAD SUCCESS")
                        v_status = "SUCCESS"
                    except Exception as ve:
                        v_status = "FAILED"
                        v_error = str(ve)
                        logger.error(f"[APPX {job_id}] Video processing error: {ve}")

                # 2. Process PDF Document if available immediately after video
                if lec_res.has_pdf and lec_res.pdf_url:
                    try:
                        if manager.cancel_flags.get(job_id, False):
                            break

                        if prog_msg:
                            try:
                                await prog_msg.edit_text(format_pdf_download_card(b_name, item.title, frame=0), parse_mode=enums.ParseMode.HTML)
                            except Exception:
                                pass

                        print(f"[APPX {job_id}] PDF DOWNLOAD START")
                        logging.info(f"[APPX {job_id}] PDF DOWNLOAD START")
                        pdf_doc = await helper.download_pdf(
                            url=lec_res.pdf_url,
                            name=f"{name_clean}_notes",
                            custom_headers=custom_headers,
                            watermark_text=active_wm,
                            watermark_file=WATERMARK_FILE,
                            custom_dir=downloads_dir
                        )
                        if not pdf_doc or not os.path.exists(pdf_doc) or os.path.getsize(pdf_doc) == 0:
                            raise RuntimeError("PDF download produced empty or invalid file.")
                        temp_files_to_clean.append(pdf_doc)
                        print(f"[APPX {job_id}] PDF DOWNLOAD SUCCESS")
                        logging.info(f"[APPX {job_id}] PDF DOWNLOAD SUCCESS")

                        pdf_cap = build_pdf_caption(
                            course_name=b_name,
                            lecture_title=item.title,
                            subject_name=course_data.subject if course_data.subject != "General" else None,
                            unit_title=active_unit.title if active_unit else None,
                            item_index=i + 1,
                            credit=credit or CREDIT
                        )

                        if manager.cancel_flags.get(job_id, False):
                            break

                        if prog_msg:
                            try:
                                await prog_msg.edit_text(format_pdf_upload_card(b_name, item.title, frame=0), parse_mode=enums.ParseMode.HTML)
                            except Exception:
                                pass

                        print(f"[APPX {job_id}] PDF UPLOAD START")
                        logging.info(f"[APPX {job_id}] PDF UPLOAD START")
                        async with manager.upload_semaphore:
                            await bot_client.send_document(
                                chat_id=channel_id,
                                document=pdf_doc,
                                caption=pdf_cap,
                                parse_mode=enums.ParseMode.HTML,
                                **send_kwargs
                            )
                        print(f"[APPX {job_id}] PDF UPLOAD SUCCESS")
                        logging.info(f"[APPX {job_id}] PDF UPLOAD SUCCESS")
                        pdf_status = "SUCCESS"
                    except Exception as pe:
                        pdf_status = "FAILED"
                        pdf_error = str(pe)
                        logger.error(f"[APPX {job_id}] PDF processing error: {pe}")

            # --- KGS HLS Stream (Khan Global Studies) ---
            elif media_type == MediaType.KGS_HLS:
                if manager.cancel_flags.get(job_id, False):
                    break

                job.phase = "DOWNLOADING"
                db.save_job(job.to_dict())
                if prog_msg:
                    try:
                        await prog_msg.edit_text(format_download_card(b_name, item.title, active_unit.title if active_unit else None, course_data.subject if course_data.subject != "General" else None, frame=1), parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass

                print(f"[KGS {job_id}] HLS DOWNLOAD START")
                logging.info(f"[KGS {job_id}] HLS DOWNLOAD START")
                v_file = await asyncio.to_thread(helper.download_kgs, url, name_clean, quality, None, downloads_dir, user_id, job_id)
                if not v_file or not os.path.exists(v_file) or os.path.getsize(v_file) == 0:
                    raise RuntimeError("KGS HLS video download produced empty or missing file.")
                print(f"[KGS {job_id}] HLS DOWNLOAD SUCCESS")
                logging.info(f"[KGS {job_id}] HLS DOWNLOAD SUCCESS")
                temp_files_to_clean.append(v_file)

                if manager.cancel_flags.get(job_id, False):
                    break

                job.phase = "PROCESSING"
                db.save_job(job.to_dict())
                final_v_file = v_file
                if active_wm and WATERMARK_TEXT:
                    job.phase = "WATERMARKING"
                    db.save_job(job.to_dict())
                    if prog_msg:
                        try:
                            await prog_msg.edit_text(format_watermark_card(item.title, frame=0), parse_mode=enums.ParseMode.HTML)
                        except Exception:
                            pass
                    wm_res = await asyncio.to_thread(helper.apply_video_watermark, v_file, active_wm, WATERMARK_FILE)
                    if wm_res and os.path.exists(wm_res):
                        temp_files_to_clean.append(wm_res)
                        final_v_file = wm_res

                v_thumb = await asyncio.to_thread(helper.extract_or_download_thumbnail, final_v_file, None, thumb)
                if v_thumb and os.path.exists(v_thumb):
                    temp_files_to_clean.append(v_thumb)

                caption_text = build_enriched_caption(
                    course_data.subject,
                    b_name,
                    item,
                    item_index=i + 1,
                    credit=credit
                )

                if manager.cancel_flags.get(job_id, False):
                    break

                job.phase = "UPLOADING"
                db.save_job(job.to_dict())
                async with manager.upload_semaphore:
                    await helper.send_vid(
                        bot_client,
                        prog_msg,
                        caption_text,
                        final_v_file,
                        v_thumb,
                        item.title,
                        prog_msg,
                        channel_id,
                        watermark=watermark,
                        topic_thread_id=active_thread_id,
                        parse_mode=enums.ParseMode.HTML,
                        user_id=user_id,
                        bot_id=eff_bot_id,
                        uploaded_parts=job.uploaded_parts.get(str(i + 1), []),
                        on_part_uploaded=_on_part_done
                    )
                v_status = "SUCCESS"

            # --- B. Direct M3U8 Stream URL ---
            elif media_type == MediaType.DIRECT_M3U8:
                if manager.cancel_flags.get(job_id, False):
                    break

                job.phase = "DOWNLOADING"
                db.save_job(job.to_dict())
                if prog_msg:
                    try:
                        await prog_msg.edit_text(format_download_card(b_name, item.title, active_unit.title if active_unit else None, course_data.subject if course_data.subject != "General" else None, frame=1), parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass

                print(f"[M3U8 {job_id}] DOWNLOAD START")
                logging.info(f"[M3U8 {job_id}] DOWNLOAD START")
                v_file = await asyncio.to_thread(helper.download_appx_m3u8, url, name_clean, None, downloads_dir)
                if not v_file or not os.path.exists(v_file) or os.path.getsize(v_file) == 0:
                    raise RuntimeError("Direct M3U8 download produced empty or missing file.")
                print(f"[M3U8 {job_id}] DOWNLOAD SUCCESS")
                logging.info(f"[M3U8 {job_id}] DOWNLOAD SUCCESS")
                temp_files_to_clean.append(v_file)

                if manager.cancel_flags.get(job_id, False):
                    break

                job.phase = "PROCESSING"
                db.save_job(job.to_dict())
                final_v_file = v_file
                if active_wm and WATERMARK_TEXT:
                    job.phase = "WATERMARKING"
                    db.save_job(job.to_dict())
                    if prog_msg:
                        try:
                            await prog_msg.edit_text(format_watermark_card(item.title, frame=0), parse_mode=enums.ParseMode.HTML)
                        except Exception:
                            pass
                    wm_res = await asyncio.to_thread(helper.apply_video_watermark, v_file, active_wm, WATERMARK_FILE)
                    if wm_res and os.path.exists(wm_res):
                        temp_files_to_clean.append(wm_res)
                        final_v_file = wm_res

                v_thumb = await asyncio.to_thread(helper.extract_or_download_thumbnail, final_v_file, None, thumb)
                if v_thumb and os.path.exists(v_thumb):
                    temp_files_to_clean.append(v_thumb)

                caption_text = build_enriched_caption(
                    course_data.subject,
                    b_name,
                    item,
                    item_index=i + 1,
                    credit=credit
                )

                if manager.cancel_flags.get(job_id, False):
                    break

                job.phase = "UPLOADING"
                db.save_job(job.to_dict())
                async with manager.upload_semaphore:
                    await helper.send_vid(
                        bot_client,
                        prog_msg,
                        caption_text,
                        final_v_file,
                        v_thumb,
                        item.title,
                        prog_msg,
                        channel_id,
                        watermark=watermark,
                        topic_thread_id=active_thread_id,
                        parse_mode=enums.ParseMode.HTML,
                        user_id=user_id,
                        bot_id=eff_bot_id,
                        uploaded_parts=job.uploaded_parts.get(str(i + 1), []),
                        on_part_uploaded=_on_part_done
                    )
                v_status = "SUCCESS"

            # --- C. Direct PDF Document URL ---
            elif media_type == MediaType.DIRECT_PDF:
                job.phase = "DOWNLOADING"
                db.save_job(job.to_dict())
                if prog_msg:
                    try:
                        await prog_msg.edit_text(format_pdf_download_card(b_name, item.title, frame=0), parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass

                pdf_file = await helper.download_pdf(
                    url=url,
                    name=name_clean,
                    custom_headers=custom_headers,
                    watermark_text=active_wm,
                    watermark_file=WATERMARK_FILE,
                    custom_dir=downloads_dir
                )
                if not pdf_file or not os.path.exists(pdf_file) or os.path.getsize(pdf_file) == 0:
                    raise RuntimeError("PDF download produced empty or invalid file.")
                temp_files_to_clean.append(pdf_file)

                caption_text = build_pdf_caption(
                    course_name=b_name,
                    lecture_title=item.title,
                    subject_name=course_data.subject if course_data.subject != "General" else None,
                    unit_title=active_unit.title if active_unit else None,
                    item_index=i + 1,
                    credit=credit or CREDIT
                )

                job.phase = "UPLOADING"
                db.save_job(job.to_dict())
                if prog_msg:
                    try:
                        await prog_msg.edit_text(format_pdf_upload_card(b_name, item.title, frame=0), parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass

                async with manager.upload_semaphore:
                    await bot_client.send_document(
                        chat_id=channel_id,
                        document=pdf_file,
                        caption=caption_text,
                        parse_mode=enums.ParseMode.HTML,
                        **send_kwargs
                    )
                pdf_status = "SUCCESS"

            # --- D. YouTube Video ---
            elif media_type == MediaType.YOUTUBE:
                job.phase = "DOWNLOADING"
                db.save_job(job.to_dict())
                if prog_msg:
                    try:
                        await prog_msg.edit_text(format_download_card(b_name, item.title, active_unit.title if active_unit else None, course_data.subject if course_data.subject != "General" else None, frame=1), parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass

                caption_text = build_enriched_caption(
                    course_data.subject,
                    b_name,
                    item,
                    item_index=i + 1,
                    credit=credit
                )

                yt_info = await asyncio.to_thread(helper.resolve_youtube_ytultra_info, url, quality)
                yt_uploaded_msg = None

                if yt_info and yt_info.get("url"):
                    job.phase = "UPLOADING"
                    db.save_job(job.to_dict())
                    try:
                        async with manager.upload_semaphore:
                            yt_uploaded_msg = await helper.process_and_upload_remote_youtube_parts(
                                bot_client,
                                prog_msg,
                                caption_text,
                                item.title,
                                url,
                                yt_info,
                                channel_id,
                                user_id=user_id,
                                quality=quality,
                                custom_dir=downloads_dir,
                                thumbnail=thumb,
                                watermark=watermark,
                                topic_thread_id=active_thread_id,
                                parse_mode=enums.ParseMode.HTML,
                                uploaded_parts=job.uploaded_parts.get(str(i + 1), []),
                                on_part_uploaded=_on_part_done
                            )
                    except Exception as exc:
                        logger.warning(f"[YOUTUBE] Remote part-by-part processing failed: {exc}")
                        if "insufficient for the requested ~1800 MB part architecture" in str(exc):
                            raise

                if yt_uploaded_msg:
                    v_status = "SUCCESS"
                else:
                    yt_file = await helper.download_video(url, name_clean, quality, user_id=user_id, custom_dir=downloads_dir)
                    if not yt_file or not os.path.exists(yt_file) or os.path.getsize(yt_file) == 0:
                        raise RuntimeError("YouTube download produced empty or missing file.")
                    temp_files_to_clean.append(yt_file)

                    job.phase = "PROCESSING"
                    db.save_job(job.to_dict())
                    final_yt = yt_file
                    if active_wm and WATERMARK_TEXT:
                        if prog_msg:
                            try:
                                await prog_msg.edit_text(format_watermark_card(item.title, frame=0), parse_mode=enums.ParseMode.HTML)
                            except Exception:
                                pass
                        wm_res = await asyncio.to_thread(helper.apply_video_watermark, yt_file, active_wm, WATERMARK_FILE)
                        if wm_res and os.path.exists(wm_res):
                            temp_files_to_clean.append(wm_res)
                            final_yt = wm_res

                    yt_thumb = await asyncio.to_thread(helper.extract_or_download_thumbnail, final_yt, None, thumb)
                    if yt_thumb and os.path.exists(yt_thumb):
                        temp_files_to_clean.append(yt_thumb)

                    job.phase = "UPLOADING"
                    db.save_job(job.to_dict())
                    async with manager.upload_semaphore:
                        await helper.send_vid(
                            bot_client,
                            prog_msg,
                            caption_text,
                            final_yt,
                            yt_thumb,
                            item.title,
                            prog_msg,
                            channel_id,
                            watermark=watermark,
                            topic_thread_id=active_thread_id,
                            parse_mode=enums.ParseMode.HTML,
                            user_id=user_id,
                            bot_id=eff_bot_id,
                            uploaded_parts=job.uploaded_parts.get(str(i + 1), []),
                            on_part_uploaded=_on_part_done
                        )
                    v_status = "SUCCESS"

            # --- E. Encrypted / AES Protected Video ---
            elif media_type == MediaType.ENCRYPTED_STREAM:
                key = None
                if "*" in url:
                    url_parts = url.split("*", 1)
                    url = url_parts[0].strip()
                    key = url_parts[1].strip()

                job.phase = "DOWNLOADING"
                db.save_job(job.to_dict())
                if prog_msg:
                    try:
                        await prog_msg.edit_text(format_download_card(b_name, item.title, active_unit.title if active_unit else None, course_data.subject if course_data.subject != "General" else None, frame=1), parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass

                enc_file = await asyncio.to_thread(helper.download_and_decrypt_video, url, name_clean, key)
                if not enc_file or not os.path.exists(enc_file) or os.path.getsize(enc_file) == 0:
                    raise RuntimeError("Encrypted video download or decryption failed.")
                temp_files_to_clean.append(enc_file)

                job.phase = "PROCESSING"
                db.save_job(job.to_dict())
                final_enc = enc_file
                if active_wm and WATERMARK_TEXT:
                    if prog_msg:
                        try:
                            await prog_msg.edit_text(format_watermark_card(item.title, frame=0), parse_mode=enums.ParseMode.HTML)
                        except Exception:
                            pass
                    wm_res = await asyncio.to_thread(helper.apply_video_watermark, enc_file, active_wm, WATERMARK_FILE)
                    if wm_res and os.path.exists(wm_res):
                        temp_files_to_clean.append(wm_res)
                        final_enc = wm_res

                enc_thumb = await asyncio.to_thread(helper.extract_or_download_thumbnail, final_enc, None, thumb)
                if enc_thumb and os.path.exists(enc_thumb):
                    temp_files_to_clean.append(enc_thumb)

                caption_text = build_enriched_caption(
                    course_data.subject,
                    b_name,
                    item,
                    item_index=i + 1,
                    credit=credit
                )

                job.phase = "UPLOADING"
                db.save_job(job.to_dict())
                async with manager.upload_semaphore:
                    await helper.send_vid(
                        bot_client,
                        prog_msg,
                        caption_text,
                        final_enc,
                        enc_thumb,
                        item.title,
                        prog_msg,
                        channel_id,
                        watermark=watermark,
                        topic_thread_id=active_thread_id,
                        parse_mode=enums.ParseMode.HTML,
                        user_id=user_id,
                        bot_id=eff_bot_id,
                        uploaded_parts=job.uploaded_parts.get(str(i + 1), []),
                        on_part_uploaded=_on_part_done
                    )
                v_status = "SUCCESS"

            # --- F. Spayee / Go Classes Specialized HLS Stream ---
            elif media_type in (MediaType.SPAYEE_HLS, MediaType.GO_CLASSES):
                if manager.cancel_flags.get(job_id, False):
                    break

                job.phase = "DOWNLOADING"
                db.save_job(job.to_dict())
                if prog_msg:
                    try:
                        await prog_msg.edit_text(format_download_card(b_name, item.title, active_unit.title if active_unit else None, course_data.subject if course_data.subject != "General" else None, frame=1), parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass

                key = getattr(item, "authorized_key", None)
                if not key and "*" in url:
                    url_parts = url.split("*", 1)
                    url = url_parts[0].strip()
                    key = url_parts[1].strip()

                print(f"[SPAYEE {job_id}] HLS DOWNLOAD START")
                logging.info(f"[SPAYEE {job_id}] HLS DOWNLOAD START")
                v_file = await asyncio.to_thread(helper.download_spayee_hls, url, name_clean, key, quality, downloads_dir)
                if not v_file or not os.path.exists(v_file) or os.path.getsize(v_file) == 0:
                    raise RuntimeError("Spayee HLS video download produced empty or missing file.")
                print(f"[SPAYEE {job_id}] HLS DOWNLOAD SUCCESS")
                logging.info(f"[SPAYEE {job_id}] HLS DOWNLOAD SUCCESS")
                temp_files_to_clean.append(v_file)

                if manager.cancel_flags.get(job_id, False):
                    break

                job.phase = "PROCESSING"
                db.save_job(job.to_dict())
                final_v_file = v_file
                if active_wm and WATERMARK_TEXT:
                    job.phase = "WATERMARKING"
                    db.save_job(job.to_dict())
                    if prog_msg:
                        try:
                            await prog_msg.edit_text(format_watermark_card(item.title, frame=0), parse_mode=enums.ParseMode.HTML)
                        except Exception:
                            pass
                    wm_res = await asyncio.to_thread(helper.apply_video_watermark, v_file, active_wm, WATERMARK_FILE)
                    if wm_res and os.path.exists(wm_res):
                        temp_files_to_clean.append(wm_res)
                        final_v_file = wm_res

                v_thumb = await asyncio.to_thread(helper.extract_or_download_thumbnail, final_v_file, None, thumb)
                if v_thumb and os.path.exists(v_thumb):
                    temp_files_to_clean.append(v_thumb)

                caption_text = build_enriched_caption(
                    course_data.subject,
                    b_name,
                    item,
                    item_index=i + 1,
                    credit=credit
                )

                if manager.cancel_flags.get(job_id, False):
                    break

                job.phase = "UPLOADING"
                db.save_job(job.to_dict())
                async with manager.upload_semaphore:
                    await helper.send_vid(
                        bot_client,
                        prog_msg,
                        caption_text,
                        final_v_file,
                        v_thumb,
                        item.title,
                        prog_msg,
                        channel_id,
                        watermark=watermark,
                        topic_thread_id=active_thread_id,
                        parse_mode=enums.ParseMode.HTML,
                        user_id=user_id,
                        bot_id=eff_bot_id,
                        uploaded_parts=job.uploaded_parts.get(str(i + 1), []),
                        on_part_uploaded=_on_part_done
                    )
                v_status = "SUCCESS"

            # --- G. Direct Image Resource (.jpg, .jpeg, .png, .webp, etc.) ---
            elif media_type == MediaType.DIRECT_IMAGE:
                job.phase = "DOWNLOADING"
                db.save_job(job.to_dict())
                if prog_msg:
                    try:
                        await prog_msg.edit_text(format_download_card(b_name, item.title, active_unit.title if active_unit else None, course_data.subject if course_data.subject != "General" else None, frame=1), parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass

                img_file = await asyncio.to_thread(helper.download_image, url, name_clean, None, downloads_dir)
                if not img_file or not os.path.exists(img_file) or os.path.getsize(img_file) == 0:
                    raise RuntimeError("Direct image download produced empty or missing file.")
                temp_files_to_clean.append(img_file)

                caption_text = build_enriched_caption(
                    course_data.subject,
                    b_name,
                    item,
                    item_index=i + 1,
                    credit=credit
                )

                job.phase = "UPLOADING"
                db.save_job(job.to_dict())
                if prog_msg:
                    try:
                        await prog_msg.edit_text(format_upload_card(item.title, frame=0), parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass

                async with manager.upload_semaphore:
                    try:
                        await bot_client.send_photo(
                            chat_id=channel_id,
                            photo=img_file,
                            caption=caption_text,
                            parse_mode=enums.ParseMode.HTML,
                            **send_kwargs
                        )
                    except Exception:
                        await bot_client.send_document(
                            chat_id=channel_id,
                            document=img_file,
                            caption=caption_text,
                            parse_mode=enums.ParseMode.HTML,
                            **send_kwargs
                        )
                v_status = "SUCCESS"

            # --- H. Generic Direct Video URL (.mp4, .mkv, .webm, etc.) ---
            elif media_type == MediaType.DIRECT_VIDEO:
                job.phase = "DOWNLOADING"
                db.save_job(job.to_dict())
                if prog_msg:
                    try:
                        await prog_msg.edit_text(format_download_card(b_name, item.title, active_unit.title if active_unit else None, course_data.subject if course_data.subject != "General" else None, frame=1), parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass

                gen_file = await asyncio.to_thread(helper.download_direct_video, url, name_clean, None, downloads_dir)
                if not gen_file or not os.path.exists(gen_file) or os.path.getsize(gen_file) == 0:
                    raise RuntimeError("Direct video download produced empty or missing file.")
                temp_files_to_clean.append(gen_file)

                job.phase = "PROCESSING"
                db.save_job(job.to_dict())
                final_gen = gen_file
                if active_wm and WATERMARK_TEXT:
                    if prog_msg:
                        try:
                            await prog_msg.edit_text(format_watermark_card(item.title, frame=0), parse_mode=enums.ParseMode.HTML)
                        except Exception:
                            pass
                    wm_res = await asyncio.to_thread(helper.apply_video_watermark, gen_file, active_wm, WATERMARK_FILE)
                    if wm_res and os.path.exists(wm_res):
                        temp_files_to_clean.append(wm_res)
                        final_gen = wm_res

                gen_thumb = await asyncio.to_thread(helper.extract_or_download_thumbnail, final_gen, None, thumb)
                if gen_thumb and os.path.exists(gen_thumb):
                    temp_files_to_clean.append(gen_thumb)

                caption_text = build_enriched_caption(
                    course_data.subject,
                    b_name,
                    item,
                    item_index=i + 1,
                    credit=credit
                )

                job.phase = "UPLOADING"
                db.save_job(job.to_dict())
                async with manager.upload_semaphore:
                    await helper.send_vid(
                        bot_client,
                        prog_msg,
                        caption_text,
                        final_gen,
                        gen_thumb,
                        item.title,
                        prog_msg,
                        channel_id,
                        watermark=watermark,
                        topic_thread_id=active_thread_id,
                        parse_mode=enums.ParseMode.HTML,
                        user_id=user_id,
                        bot_id=eff_bot_id,
                        uploaded_parts=job.uploaded_parts.get(str(i + 1), []),
                        on_part_uploaded=_on_part_done
                    )
                v_status = "SUCCESS"

            # --- I. Unsupported / Unknown URL ---
            else:
                raise RuntimeError("Unsupported video source: The provided link could not be identified as a supported source.")

            if prog_msg:
                try:
                    await prog_msg.delete()
                except Exception:
                    pass

            # 8. Mark completed & update checkpoint
            clean_res_url = parse_pdf_input(item.url)["url"] if is_direct_pdf_url(item.url) else item.url
            raw_l = getattr(item, "raw_line", f"{item.title} : {item.url}")
            if "*" in raw_l and is_direct_pdf_url(item.url):
                raw_l = raw_l.split("*")[0].strip()

            if v_status == "SUCCESS" or pdf_status == "SUCCESS":
                job.completed_indices.append(i)
                unit_items_processed += 1
                lec_status = "SUCCESS" if (v_status in ("SUCCESS", "NOT_AVAILABLE") and pdf_status in ("SUCCESS", "NOT_AVAILABLE")) else "PARTIAL"
                job.item_results.append({
                    "index": i + 1,
                    "title": item.title,
                    "url": clean_res_url,
                    "raw_line": raw_l,
                    "video_status": v_status,
                    "pdf_status": pdf_status,
                    "lecture_status": lec_status,
                    "failure_reason": None if lec_status == "SUCCESS" else sanitize_error_message(f"Video: {v_error} | PDF: {pdf_error}")
                })
                db.save_job(job.to_dict())
                logger.info(f"[JOB {job_id}] Completed item {i+1}/{total_items}: {item.title} (Video={v_status}, PDF={pdf_status})")
            else:
                job.failed_indices.append(i)
                unit_items_failed += 1
                err_msg_full = sanitize_error_message(f"Video: {v_error or 'Not Available'}, PDF: {pdf_error or 'Not Available'}")
                job.item_results.append({
                    "index": i + 1,
                    "title": item.title,
                    "url": clean_res_url,
                    "raw_line": raw_l,
                    "video_status": v_status,
                    "pdf_status": pdf_status,
                    "lecture_status": "FAILED",
                    "failure_reason": err_msg_full
                })
                db.save_job(job.to_dict())
                logger.error(f"[JOB {job_id}] Item {i+1} failed: {err_msg_full}")

        except FloodWait as fw:
            logger.warning(f"FloodWait hit: sleeping {fw.value}s")
            await asyncio.sleep(fw.value if hasattr(fw, "value") else getattr(fw, "x", 10))
            continue

        except Exception as item_err:
            logger.error(f"[JOB {job_id}] Item {i+1} failed: {item_err}")
            job.failed_indices.append(i)
            unit_items_failed += 1

            if media_type == MediaType.DIRECT_PDF:
                err_v_status = "NOT_AVAILABLE"
                err_pdf_status = "FAILED"
            elif media_type in (MediaType.KGS_HLS, MediaType.DIRECT_M3U8, MediaType.YOUTUBE, MediaType.ENCRYPTED_STREAM, MediaType.SPAYEE_HLS, MediaType.GO_CLASSES, MediaType.DIRECT_VIDEO, MediaType.DIRECT_IMAGE):
                err_v_status = "FAILED"
                err_pdf_status = "NOT_AVAILABLE"
            else:
                err_v_status = v_status if v_status != "NOT_AVAILABLE" else "FAILED"
                err_pdf_status = pdf_status if pdf_status != "NOT_AVAILABLE" else "NOT_AVAILABLE"

            clean_res_url = parse_pdf_input(item.url)["url"] if is_direct_pdf_url(item.url) else item.url
            raw_l = getattr(item, "raw_line", f"{item.title} : {item.url}")
            if "*" in raw_l and is_direct_pdf_url(item.url):
                raw_l = raw_l.split("*")[0].strip()

            job.item_results.append({
                "index": i + 1,
                "title": item.title,
                "url": clean_res_url,
                "raw_line": raw_l,
                "video_status": err_v_status,
                "pdf_status": err_pdf_status,
                "lecture_status": "FAILED",
                "failure_reason": sanitize_error_message(str(item_err))
            })
            db.save_job(job.to_dict())
            fail_card = build_failure_card(
                resource_type="LECTURE",
                lecture_title=item.title,
                course_name=b_name,
                subject_name=course_data.subject if course_data.subject != "General" else None,
                unit_title=active_unit.title if active_unit else None,
                lecture_index=i + 1,
                reason="Media resource download/processing failed",
                technical_error=str(item_err),
                video_status=err_v_status,
                pdf_status=err_pdf_status,
                credit=credit or CREDIT
            )
            try:
                await bot_client.send_message(
                    channel_id,
                    fail_card,
                    parse_mode=enums.ParseMode.HTML,
                    **send_kwargs
                )
            except Exception:
                pass

        finally:
            if prog_msg:
                try:
                    await prog_msg.delete(True)
                except Exception:
                    pass
            # Clean temporary files for this item safely
            for fpath in temp_files_to_clean:
                if fpath:
                    cleanup_uploaded_file(fpath)

    # Finalize job status
    if len(job.completed_indices) + len(job.failed_indices) >= total_items:
        job.status = "COMPLETED"
        job.phase = "DONE"
        db.save_job(job.to_dict())

        success_count = len(job.completed_indices)
        failed_count = len(job.failed_indices)

        # Generate successful.txt and failed.txt
        successful_lines = []
        failed_lines = []
        for res in job.item_results:
            if res.get("video_status") == "SUCCESS" or res.get("pdf_status") == "SUCCESS":
                successful_lines.append(res.get("raw_line", ""))
            else:
                failed_lines.append(res.get("raw_line", ""))

        if not successful_lines and job.completed_indices:
            for idx in job.completed_indices:
                if idx < len(all_items):
                    successful_lines.append(all_items[idx].raw_line)
        if not failed_lines and job.failed_indices:
            for idx in job.failed_indices:
                if idx < len(all_items):
                    failed_lines.append(all_items[idx].raw_line)

        succ_file = os.path.join(job_temp, "successful.txt")
        try:
            with open(succ_file, "w", encoding="utf-8") as f:
                f.write("\n".join(filter(None, successful_lines)))
        except Exception:
            pass

        fail_file = os.path.join(job_temp, "failed.txt")
        try:
            with open(fail_file, "w", encoding="utf-8") as f:
                f.write("\n".join(filter(None, failed_lines)))
        except Exception:
            pass

        bot_disp = get_bot_display_name(bot_client)
        summary_text = (
            "🎉 <b>DOWNLOAD COMPLETED</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📦 <b>Total:</b> {total_items}\n"
            f"✅ <b>Successful:</b> {success_count}\n"
            f"❌ <b>Failed:</b> {failed_count}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"𝄟⃝⚡️ <b>{bot_disp}</b>"
        )
        buttons = [
            [InlineKeyboardButton("📄 SUCCESS FILE", callback_data=f"res_succ_{job_id}"),
             InlineKeyboardButton("❌ FAILED FILE", callback_data=f"res_fail_{job_id}")]
        ]
        if failed_count > 0:
            buttons.append([InlineKeyboardButton("🔄 RETRY FAILED", callback_data=f"retry_fail_{job_id}")])

        markup = InlineKeyboardMarkup(buttons)
        try:
            await bot_client.send_message(job.chat_id, summary_text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
            if channel_id != job.chat_id:
                await bot_client.send_message(channel_id, summary_text, parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            logger.warning(f"Failed to send completion summary: {e}")

        # Cleanup job temporary download directory safely (keep result files)
        cleanup_job_temp_dir(downloads_dir)


# ==============================================================================
# 👑 ADMIN COMMANDS & INTERACTIVE INLINE PANEL
# ==============================================================================

async def admin_panel_cmd(client: Client, message: Message):
    b_name = get_bot_display_name(client)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users", callback_data="adm_users"), InlineKeyboardButton("📥 Downloads", callback_data="adm_downloads")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast"), InlineKeyboardButton("⚙️ System", callback_data="adm_system")],
        [InlineKeyboardButton("📚 Topics", callback_data="adm_topics"), InlineKeyboardButton("🎨 Settings", callback_data="adm_settings")],
        [InlineKeyboardButton("📜 Logs", callback_data="adm_logs"), InlineKeyboardButton("🔄 Refresh", callback_data="adm_refresh")]
    ])
    await message.reply_text(
        f"<b>👑 {b_name} — Admin Control Panel</b>\n\n"
        "Select a section below to manage the bot:",
        reply_markup=keyboard
    )


async def admin_callbacks(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    if not db.is_admin(user_id):
        await query.answer("⛔ Admin Only!", show_alert=True)
        return

    data = query.data
    await query.answer()

    if data in ("adm_refresh", "adm_main"):
        b_name = get_bot_display_name(client)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 Users", callback_data="adm_users"), InlineKeyboardButton("📥 Downloads", callback_data="adm_downloads")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast"), InlineKeyboardButton("⚙️ System", callback_data="adm_system")],
            [InlineKeyboardButton("📚 Topics", callback_data="adm_topics"), InlineKeyboardButton("🎨 Settings", callback_data="adm_settings")],
            [InlineKeyboardButton("📜 Logs", callback_data="adm_logs"), InlineKeyboardButton("🔄 Refresh", callback_data="adm_refresh")]
        ])
        await query.message.edit_text(
            f"<b>👑 {b_name} — Admin Control Panel</b>\n\nSelect a section below:",
            reply_markup=keyboard
        )

    elif data == "adm_users":
        users = db.list_users()
        active = sum(1 for u in users if not u.get("banned", False))
        banned = sum(1 for u in users if u.get("banned", False))
        text = (
            f"<b>👥 Users Management</b>\n\n"
            f"• <b>Total Registered:</b> <code>{len(users)}</code>\n"
            f"• <b>Active:</b> <code>{active}</code>\n"
            f"• <b>Banned:</b> <code>{banned}</code>\n\n"
            "Commands:\n"
            "• <code>/add &lt;id&gt; &lt;days&gt;</code>\n"
            "• <code>/remove &lt;id&gt;</code>\n"
            "• <code>/renew &lt;id&gt; &lt;days&gt;</code>\n"
            "• <code>/ban &lt;id&gt;</code> | <code>/unban &lt;id&gt;</code>\n"
            "• <code>/users</code> (view list)"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 View All Users", callback_data="adm_list_users")],
            [InlineKeyboardButton("🔙 Back to Admin", callback_data="adm_main")]
        ])
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "adm_list_users":
        users = db.list_users()
        if not users:
            text = "ℹ️ No users registered yet."
        else:
            lines = ["<b>👥 Registered Users:</b>\n"]
            for u in users[:25]:
                status = "🚫 Banned" if u.get("banned") else "✅ Active"
                lines.append(f"• <code>{u.get('user_id')}</code> ({u.get('name')}) — {status}")
            text = "\n".join(lines)
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_users")]])
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "adm_downloads":
        bot_ctx = get_bot_context(client)
        bot_id = bot_ctx.bot_id if bot_ctx else "bot_1"
        jobs = db.list_jobs(bot_id=bot_id)
        active = [j for j in jobs if j.get("status") in ("QUEUED", "DOWNLOADING", "PROCESSING", "UPLOADING", "RECOVERING")]
        paused = [j for j in jobs if j.get("status") == "PAUSED"]
        text = (
            f"<b>📥 Downloads Coordinator</b>\n\n"
            f"• <b>Active Jobs:</b> <code>{len(active)}</code>\n"
            f"• <b>Paused Jobs:</b> <code>{len(paused)}</code>\n"
            f"• <b>Total Recorded:</b> <code>{len(jobs)}</code>\n\n"
            f"• Download Workers: <code>{DOWNLOAD_WORKERS}</code>\n"
            f"• Upload Workers: <code>{UPLOAD_WORKERS}</code>\n\n"
            "Commands: /stopall | /resumeall | /jobs"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏸️ Stop All", callback_data="adm_stopall"), InlineKeyboardButton("▶️ Resume All", callback_data="adm_resumeall")],
            [InlineKeyboardButton("🔙 Back", callback_data="adm_main")]
        ])
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "adm_stopall":
        # Pause all active jobs for this bot
        bot_ctx = get_bot_context(client)
        bot_id = bot_ctx.bot_id if bot_ctx else "bot_1"
        jm = get_job_manager(client, bot_id=bot_id)
        jm.pause_all(bot_id=bot_id)
        await query.answer("All active downloads paused!", show_alert=True)
        await query.message.edit_text("⏸️ <b>All active downloads have been paused.</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_downloads")]]))

    elif data == "adm_resumeall":
        await query.answer("Resuming paused jobs...", show_alert=True)
        bot_ctx = get_bot_context(client)
        bot_id = bot_ctx.bot_id if bot_ctx else "bot_1"
        jm = get_job_manager(client, bot_id=bot_id)
        unfinished = db.get_unfinished_jobs(bot_id=bot_id)
        for j in unfinished:
            job = JobCheckpoint.from_dict(j)
            if not jm.is_user_busy(job.user_id, bot_id=bot_id):
                asyncio.create_task(jm.launch_job(job, client, execute_job_pipeline))
        await query.message.edit_text("▶️ <b>Resuming all unfinished jobs.</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_downloads")]]))

    elif data == "adm_system":
        import platform
        uname = platform.uname()
        text = (
            f"<b>⚙️ System Metrics</b>\n\n"
            f"• <b>Python:</b> <code>{platform.python_version()}</code>\n"
            f"• <b>OS:</b> <code>{uname.system} {uname.release}</code>\n"
            f"• <b>Workers:</b> Down={DOWNLOAD_WORKERS} | Up={UPLOAD_WORKERS}\n"
            f"• <b>Max Active Users:</b> {MAX_ACTIVE_USERS}\n"
            f"• <b>State Storage:</b> JSON Atomic (No Database)\n"
            f"• <b>Maintenance Mode:</b> {'ON' if db.get_setting('maintenance_mode', False) else 'OFF'}"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_main")]])
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "adm_topics":
        topics = db.list_all_topics()
        lines = ["<b>📚 Persistent Forum Topics:</b>\n"]
        if not topics:
            lines.append("No topic mappings saved yet.")
        else:
            for k, tid in list(topics.items())[:20]:
                lines.append(f"• <code>{k}</code> ➔ ID: <code>{tid}</code>")
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_main")]])
        await query.message.edit_text("\n".join(lines), reply_markup=markup)

    elif data == "adm_settings":
        wm = db.get_setting("watermark", WATERMARK_TEXT)
        def_q = db.get_setting("default_quality", "720p")
        text = (
            f"<b>🎨 Bot Settings</b>\n\n"
            f"• <b>Watermark:</b> <code>{wm}</code>\n"
            f"• <b>Default Quality:</b> <code>{def_q}</code>\n\n"
            "To change:\n"
            "• <code>/setwatermark &lt;text&gt;</code>\n"
            "• <code>/setquality &lt;quality&gt;</code>"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_main")]])
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "adm_logs":
        log_file = os.path.join(LOGS_DIR, "bot.log")
        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            recent = "".join(lines[-20:])
            text = f"<b>📜 Recent Logs (Last 20 lines):</b>\n<pre>{recent[:3500]}</pre>"
        else:
            text = "📝 No logs available yet."
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Download Log File", callback_data="adm_getlog_file")],
            [InlineKeyboardButton("🗑 Clear Logs", callback_data="adm_clearlogs")],
            [InlineKeyboardButton("🔙 Back", callback_data="adm_main")]
        ])
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "adm_getlog_file":
        log_file = os.path.join(LOGS_DIR, "bot.log")
        if os.path.exists(log_file):
            await client.send_document(chat_id=query.message.chat.id, document=log_file, caption="📜 Complete Bot Logs")
        else:
            await query.answer("No log file found.", show_alert=True)

    elif data == "adm_clearlogs":
        log_file = os.path.join(LOGS_DIR, "bot.log")
        if os.path.exists(log_file):
            with open(log_file, "w", encoding="utf-8") as f:
                f.write("")
        await query.answer("Logs cleared successfully!", show_alert=True)
        await query.message.edit_text("✅ <b>Logs have been cleared.</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_logs")]]))


# Extra Admin command handlers
# ==============================================================================
# 👑 ADMIN COMMAND HANDLERS (With Strict admin_only Guard)
# ==============================================================================

@admin_only
async def admin_stats_cmd(client: Client, message: Message):
    users = db.list_users()
    jobs = db.list_jobs()
    completed = sum(1 for j in jobs if j.get("status") == "COMPLETED")
    await message.reply_text(
        f"📊 <b>System Statistics</b>\n\n"
        f"• <b>Subscribed Users:</b> <code>{len(users)}</code>\n"
        f"• <b>Total Jobs:</b> <code>{len(jobs)}</code>\n"
        f"• <b>Completed Jobs:</b> <code>{completed}</code>\n"
        f"• <b>Active Workers:</b> Down={DOWNLOAD_WORKERS} | Up={UPLOAD_WORKERS}"
    )


@admin_only
async def admin_system_cmd(client: Client, message: Message):
    import platform
    uname = platform.uname()
    st = db.get_storage_stats()
    text = (
        f"<b>⚙️ System Diagnostics</b>\n\n"
        f"• <b>Python:</b> <code>{platform.python_version()}</code>\n"
        f"• <b>OS:</b> <code>{uname.system} {uname.release}</code>\n"
        f"• <b>Workers:</b> Down={DOWNLOAD_WORKERS} | Up={UPLOAD_WORKERS}\n"
        f"• <b>Active Users Limit:</b> {MAX_ACTIVE_USERS}\n"
        f"• <b>Maintenance Mode:</b> {'ON' if db.get_setting('maintenance_mode', False) else 'OFF'}\n"
        f"• <b>Temp Storage:</b> <code>{hrb(st['temp_bytes'])}</code> ({st['temp_files']} files)\n"
        f"• <b>Log Storage:</b> <code>{hrb(st['logs_bytes'])}</code>"
    )
    await message.reply_text(text)


@admin_only
async def admin_workers_cmd(client: Client, message: Message):
    bot_ctx = get_bot_context(client)
    jm = get_job_manager(client, bot_id=bot_ctx.bot_id if bot_ctx else None)
    active_count = len(jm.user_active_jobs)
    await message.reply_text(
        f"⚡ <b>Concurrency & Worker Status</b>\n\n"
        f"• <b>Download Workers:</b> <code>{DOWNLOAD_WORKERS}</code>\n"
        f"• <b>Upload Workers:</b> <code>{UPLOAD_WORKERS}</code>\n"
        f"• <b>Active Job Slots:</b> <code>{active_count}/{MAX_ACTIVE_USERS}</code>\n\n"
        "Use <code>/setworkers &lt;down&gt; &lt;up&gt;</code> to adjust."
    )


@admin_only
async def admin_setworkers_cmd(client: Client, message: Message):
    args = message.text.split()
    if len(args) < 3 or not args[1].isdigit() or not args[2].isdigit():
        await message.reply_text("❌ Format: <code>/setworkers &lt;download_workers&gt; &lt;upload_workers&gt;</code>")
        return
    dw, uw = int(args[1]), int(args[2])
    db.set_setting("download_workers", dw)
    db.set_setting("upload_workers", uw)
    await message.reply_text(f"✅ Concurrency settings saved: Download={dw}, Upload={uw}")


@admin_only
async def admin_setratelimit_cmd(client: Client, message: Message):
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.reply_text("❌ Format: <code>/setratelimit &lt;delay_seconds&gt;</code>")
        return
    delay = int(args[1])
    db.set_setting("retry_delay", delay)
    await message.reply_text(f"✅ Rate limit retry delay set to: <code>{delay}s</code>")


@admin_only
async def admin_queue_cmd(client: Client, message: Message):
    jobs = db.list_jobs()
    active = [j for j in jobs if j.get("status") in ("QUEUED", "DOWNLOADING", "PROCESSING", "UPLOADING", "RECOVERING")]
    if not active:
        await message.reply_text("ℹ️ The job queue is currently empty.")
        return
    lines = ["<b>🚦 Active Download Queue:</b>\n"]
    for j in active:
        lines.append(f"• <code>{j.get('job_id')}</code> | User: <code>{j.get('user_id')}</code> | <b>{j.get('status')}</b> ({j.get('completed_count', 0)}/{j.get('total_items', 0)})")
    await message.reply_text("\n".join(lines))


@admin_only
async def admin_storage_cmd(client: Client, message: Message):
    st = db.get_storage_stats()
    await message.reply_text(
        f"💾 <b>Storage Breakdown (Database-Free)</b>\n\n"
        f"• <b>Temporary Files:</b> <code>{hrb(st['temp_bytes'])}</code> ({st['temp_files']} files)\n"
        f"• <b>State & Metadata:</b> <code>{hrb(st['state_bytes'])}</code>\n"
        f"• <b>Log Files:</b> <code>{hrb(st['logs_bytes'])}</code>\n"
        f"• <b>Stored Jobs:</b> <code>{st['jobs_count']}</code>\n"
        f"• <b>Stored Users:</b> <code>{st['users_count']}</code>\n"
        f"• <b>Topic Mappings:</b> <code>{st['topics_count']}</code>"
    )


@admin_only
async def admin_cleanup_cmd(client: Client, message: Message):
    status_msg = await message.reply_text("🧹 Performing system cleanup...")
    clean_all()
    removed_exp = await clean_expired_users(client)
    await status_msg.edit_text(f"✅ Cleanup complete! Removed {removed_exp} expired subscriptions.")


@admin_only
async def broadcast_cmd(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply_text("❌ Format: <code>/broadcast &lt;message&gt;</code>")
        return

    text_to_send = args[1]
    users = db.list_users()
    sent = 0
    failed = 0

    prog = await message.reply_text(f"📢 Broadcasting to {len(users)} users...")
    for u in users:
        uid = u.get("user_id")
        try:
            await client.send_message(uid, text_to_send)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await prog.edit_text(f"✅ <b>Broadcast Completed!</b>\n\n• Sent: <code>{sent}</code>\n• Failed: <code>{failed}</code>")


@admin_only
async def set_watermark_cmd(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply_text("❌ Format: <code>/setwatermark &lt;text&gt;</code>")
        return
    db.set_setting("watermark", args[1].strip())
    await message.reply_text(f"✅ Watermark updated to: <code>{args[1].strip()}</code>")


@admin_only
async def view_watermark_cmd(client: Client, message: Message):
    wm = db.get_setting("watermark", WATERMARK_TEXT)
    await message.reply_text(f"🎨 <b>Current Watermark:</b> <code>{wm}</code>")


@admin_only
async def set_quality_cmd(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply_text("❌ Format: <code>/setquality &lt;144|240|360|480|720|1080&gt;</code>")
        return
    q = args[1].strip()
    if not q.endswith("p"):
        q += "p"
    db.set_setting("default_quality", q)
    await message.reply_text(f"✅ Default quality set to: <code>{q}</code>")


@admin_only
async def setgroup_cmd(client: Client, message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("❌ Format: <code>/setgroup &lt;chat_id&gt;</code>\nExample: <code>/setgroup -1001234567890</code>")
        return
    try:
        gid = int(args[1])
        valid, msg, details = await validate_forum_chat(client, gid)
        if not valid:
            await message.reply_text(f"❌ <b>Invalid Group / Channel:</b>\n\n{msg}\n\n<i>Ensure the bot is added as an Administrator with full permissions.</i>")
            return

        db.set_forum_group(gid)
        forum_status = "✅ Forum Supergroup Enabled" if details.get("is_forum") else "ℹ️ Standard Supergroup / Channel"
        perm_status = "✅ Manage Topics Allowed" if details.get("can_manage_topics") else "⚠️ Lacks 'Manage Topics' privilege"

        await message.reply_text(
            f"✅ <b>Destination Group Configured!</b>\n\n"
            f"• <b>Title:</b> {details.get('title')}\n"
            f"• <b>Chat ID:</b> <code>{gid}</code>\n"
            f"• <b>Type:</b> <code>{details.get('type')}</code>\n"
            f"• <b>Forum Status:</b> {forum_status}\n"
            f"• <b>Topic Permissions:</b> {perm_status}\n\n"
            "Academic units will now be routed to this destination."
        )
    except ValueError:
        await message.reply_text("❌ Invalid Chat ID format. Must be an integer (e.g. <code>-1001234567890</code>).")
    except Exception as e:
        await message.reply_text(f"❌ Failed to configure group: {e}\nEnsure the bot is added as admin to the group.")


@admin_only
async def list_topics_cmd(client: Client, message: Message):
    topics = db.list_all_topics()
    if not topics:
        await message.reply_text("ℹ️ No persistent forum topics mapped yet.")
        return
    lines = ["<b>📚 Persistent Forum Topics Mapping:</b>\n"]
    for k, tid in list(topics.items())[:30]:
        lines.append(f"• <code>{k}</code> ➔ ID: <code>{tid}</code>")
    await message.reply_text("\n".join(lines))


@admin_only
async def createtopics_cmd(client: Client, message: Message):
    fg = db.get_forum_group()
    if not fg:
        await message.reply_text("⚠️ No forum group configured. Use <code>/setgroup &lt;chat_id&gt;</code> first.")
        return
    await message.reply_text(
        f"📌 <b>Forum Topic Manager</b>\n\n"
        f"• Target Group: <code>{fg}</code>\n\n"
        "Topics are created automatically when batch courses are downloaded.\n"
        "To view existing topic mappings, use <code>/topics</code>."
    )


@admin_only
async def setlog_cmd(client: Client, message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("❌ Format: <code>/setlog &lt;channel_id&gt;</code>")
        return
    try:
        cid = int(args[1])
        db.set_log_channel(get_bot_username(client), cid)
        await message.reply_text(f"✅ Log channel set to: <code>{cid}</code>")
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")


@admin_only
async def getlog_cmd(client: Client, message: Message):
    log_file = os.path.join(LOGS_DIR, "bot.log")
    if os.path.exists(log_file):
        try:
            await message.reply_document(document=log_file, caption="📜 <b>Complete Bot Log File</b>")
        except Exception:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            recent = "".join(lines[-25:])
            await message.reply_text(f"<b>📜 Recent Logs:</b>\n<pre>{recent[:3500]}</pre>")
    else:
        await message.reply_text("📝 No log file available.")


@admin_only
async def clearlogs_cmd(client: Client, message: Message):
    log_file = os.path.join(LOGS_DIR, "bot.log")
    if os.path.exists(log_file):
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("")
    await message.reply_text("✅ <b>Bot logs cleared.</b>")


@admin_only
async def stopall_cmd(client: Client, message: Message):
    bot_ctx = get_bot_context(client)
    bot_id = bot_ctx.bot_id if bot_ctx else "bot_1"
    jm = get_job_manager(client, bot_id=bot_id)
    jm.pause_all(bot_id=bot_id)
    await message.reply_text(f"⏸️ Paused active download job(s) on this bot. Checkpoints saved.")


@admin_only
async def resumeall_cmd(client: Client, message: Message):
    bot_ctx = get_bot_context(client)
    bot_id = bot_ctx.bot_id if bot_ctx else "bot_1"
    jm = get_job_manager(client, bot_id=bot_id)
    unfinished = db.get_unfinished_jobs(bot_id=bot_id)
    resumed_count = 0
    for j in unfinished:
        job = JobCheckpoint.from_dict(j)
        if not jm.is_user_busy(job.user_id, bot_id=bot_id):
            asyncio.create_task(jm.launch_job(job, client, execute_job_pipeline))
            resumed_count += 1
    await message.reply_text(f"▶️ Resuming {resumed_count} unfinished job(s) in background.")


@admin_only
async def user_info_cmd(client: Client, message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("❌ Format: <code>/user &lt;user_id&gt;</code>")
        return
    try:
        uid = int(args[1])
        u_info = db.get_user(uid)
        if not u_info:
            await message.reply_text(f"ℹ️ User <code>{uid}</code> is not registered.")
            return
        status = "🚫 Banned" if u_info.get("banned") else "✅ Active"
        await message.reply_text(
            f"👤 <b>User Info:</b>\n\n"
            f"• <b>User ID:</b> <code>{uid}</code>\n"
            f"• <b>Name:</b> {u_info.get('name')}\n"
            f"• <b>Status:</b> {status}\n"
            f"• <b>Expiry:</b> {u_info.get('expiry_date') or 'N/A'}"
        )
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")


@admin_only
async def job_info_cmd(client: Client, message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("❌ Format: <code>/job &lt;job_id&gt;</code>")
        return
    jid = args[1].strip()
    j_info = db.get_job(jid)
    if not j_info:
        await message.reply_text(f"ℹ️ Job <code>{jid}</code> not found.")
        return
    await message.reply_text(
        f"📋 <b>Job Details:</b>\n\n"
        f"• <b>Job ID:</b> <code>{jid}</code>\n"
        f"• <b>User ID:</b> <code>{j_info.get('user_id')}</code>\n"
        f"• <b>Status:</b> <code>{j_info.get('status')}</code>\n"
        f"• <b>Phase:</b> <code>{j_info.get('phase')}</code>\n"
        f"• <b>Progress:</b> {len(j_info.get('completed_indices', []))}/{j_info.get('total_items', 0)}"
    )


@admin_only
async def maintenance_on_cmd(client: Client, message: Message):
    db.set_setting("maintenance_mode", True)
    await message.reply_text("🛠 <b>Maintenance Mode is now ON.</b> Non-admin access is disabled.")


@admin_only
async def maintenance_off_cmd(client: Client, message: Message):
    db.set_setting("maintenance_mode", False)
    await message.reply_text("✅ <b>Maintenance Mode is now OFF.</b> Bot is open to subscribers.")


@admin_only
async def restart_cmd(client: Client, message: Message):
    await message.reply_text("♻️ <b>Restarting bot process...</b> Checkpoints will resume automatically.")
    os.execl(sys.executable, sys.executable, *sys.argv)


# Unauthorized fallback handler for non-subscribers
async def unauthorized_handler(client: Client, message: Message):
    b_name = get_bot_display_name(client)
    b_link = get_bot_link(client)
    s_link = get_bot_support_link(client)
    await message.reply(
        f"<b>Mʏ Nᴀᴍᴇ [{b_name}]({b_link})</b>\n\n"
        "<blockquote>You need an active subscription to use this command.\n"
        "Please contact admin to get premium access.</blockquote>",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("💫 Get Premium Access", url=s_link)
        ]])
    )


# ==============================================================================
# 🎮 INTERACTIVE MENU & DASHBOARD CALLBACKS
# ==============================================================================

async def menu_callbacks(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    data = query.data
    await query.answer()

    b_name = get_bot_display_name(client)
    s_link = get_bot_support_link(client)
    s_uname = getattr(vars, "SUPPORT_USERNAME", "") or get_bot_username(client)

    if data == "menu_main":
        user_name = query.from_user.first_name if query.from_user else "User"
        is_admin = db.is_admin(user_id)
        text, markup = build_welcome_dashboard(user_id, user_name, is_admin, bot_name=b_name)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    elif data == "menu_download":
        text = (
            "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
            "│ 🎓 <b>COURSE WALLAH</b>      │\n"
            "│   🎬 <b>DOWNLOAD</b>         │\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "🔗 <b>Send your media URL</b>\n\n"
            "You can send:\n"
            "• YouTube URL (up to 4K UHD)\n"
            "• Course / Lecture URL\n"
            "• Direct M3U8 or MP4 Stream\n"
            "• PDF Study Material link\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💡 <b>Example:</b>\n"
            "<code>https://youtu.be/...</code>"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 BATCH DOWNLOAD", callback_data="menu_batch")],
            [InlineKeyboardButton("🏠 HOME", callback_data="menu_main")]
        ])
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    elif data == "menu_courses":
        text = (
            "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
            "│ 📚 <b>MY COURSES</b>        │\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "Centralized learning downloader.\n\n"
            "<b>Supported Features:</b>\n"
            "• 🎥 <b>Lecture Videos:</b> Multi-quality M3U8, KGS & Spayee\n"
            "• 📺 <b>YouTube 4K:</b> Fast remote slicing up to 2160p\n"
            "• 📄 <b>PDF Notes:</b> Automatic extraction with branding\n"
            "• 📁 <b>Batch Hierarchy:</b> Subject → Unit → Topic structure\n"
            "• 💬 <b>Forum Topics:</b> Direct supergroup routing\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💡 <i>Tip: Send any lecture link or .txt file directly!</i>"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 START DOWNLOAD", callback_data="menu_download")],
            [InlineKeyboardButton("🏠 HOME", callback_data="menu_main")]
        ])
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    elif data == "menu_quality":
        text = (
            "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
            "│ 🎯 <b>QUALITY SELECTOR</b>  │\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "Course Wallah provides smart stream quality selection:\n\n"
            "• 🔥 <b>4K UHD • 2160p</b> (VP9 + AAC Remote Part Slicing)\n"
            "• 💎 <b>1440p Quad HD</b> (2K High Resolution)\n"
            "• 🎬 <b>1080p Full HD</b> (Crystal Clear Playback)\n"
            "• ⚡ <b>720p HD</b> (Fast Standard Streaming)\n"
            "• 📱 <b>480p / 360p SD</b> (Data-Saver Streams)\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🎯 <i>Only REAL available qualities are presented per stream.</i>"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 START DOWNLOAD", callback_data="menu_download")],
            [InlineKeyboardButton("🏠 HOME", callback_data="menu_main")]
        ])
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    elif data == "menu_batch":
        text, markup = format_batch_input_card(user_id=user_id)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    elif data == "menu_pdf":
        text = (
            "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
            "│ 📄 <b>PDF DOWNLOADER</b>    │\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "High-speed PDF notes & study material extraction.\n\n"
            "<b>Capabilities:</b>\n"
            "• Direct PDF downloads\n"
            "• Password-protected PDF unlocking (`URL*password`)\n"
            "• Clean watermarking & metadata tagging\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💡 <b>Usage:</b> Simply paste a PDF link or batch file."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 START DOWNLOAD", callback_data="menu_download")],
            [InlineKeyboardButton("🏠 HOME", callback_data="menu_main")]
        ])
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    elif data == "menu_drm":
        text, markup = format_drm_input_card(user_id=user_id)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    elif data == "menu_help":
        text, markup = format_help_menu_card(b_name)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    elif data == "menu_help_how":
        text = (
            "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
            "│ 📖 <b>HOW TO USE</b>        │\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "<b>1. Downloading Individual Videos:</b>\n"
            "• Paste any YouTube, Lecture, or M3U8 link in this chat.\n"
            "• Choose your desired quality from the popup menu.\n\n"
            "<b>2. Downloading Batch Courses:</b>\n"
            "• Send <code>/drm</code> and upload your course <code>.txt</code> file.\n"
            "• Choose start index and custom parameters.\n\n"
            "<b>3. Controlling Downloads:</b>\n"
            "• <code>/status</code>: Real-time progress, speed, and ETA\n"
            "• <code>/stop</code>: Pause download & save checkpoint\n"
            "• <code>/resume</code>: Resume from last completed item\n"
            "• <code>/cancel</code>: Cancel active download"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 HELP MENU", callback_data="menu_help")],
            [InlineKeyboardButton("🏠 HOME", callback_data="menu_main")]
        ])
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    elif data == "menu_help_dl":
        text = (
            "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
            "│ 🎬 <b>DOWNLOAD HELP</b>     │\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "<b>Supported Media Providers:</b>\n"
            "• YouTube (up to 4K UHD 2160p)\n"
            "• HLS / M3U8 streams (AES-128 decrypted)\n"
            "• KGS & Spayee stream pipelines\n"
            "• APPX / Classplus lecture streams\n"
            "• Direct MP4, MKV, WebM, and PDF links\n\n"
            "⚡ <i>All downloads are split into ~1800 MB safe parts automatically.</i>"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 HELP MENU", callback_data="menu_help")],
            [InlineKeyboardButton("🏠 HOME", callback_data="menu_main")]
        ])
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    elif data == "menu_help_quality":
        text = (
            "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
            "│ 📺 <b>QUALITY GUIDE</b>     │\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "<b>How Quality Selection Works:</b>\n"
            "• Whenever you send a YouTube link, available streams are inspected live.\n"
            "• If 4K UHD is available, a dedicated 4K button is displayed.\n"
            "• If a requested quality is missing, the highest available quality is offered.\n"
            "• You can change quality anytime before download starts."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 HELP MENU", callback_data="menu_help")],
            [InlineKeyboardButton("🏠 HOME", callback_data="menu_main")]
        ])
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    elif data == "menu_help_drm":
        text = (
            "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
            "│ 🔐 <b>DRM INFORMATION</b>   │\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "• Course Wallah includes a built-in DRM checker (<code>/drm</code>).\n"
            "• It detects Widevine, FairPlay, and encrypted media manifests.\n"
            "• Standard AES-128 HLS streams are fully supported.\n"
            "• Protected Widevine DRM streams cannot be bypassed and will be safely flagged."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 HELP MENU", callback_data="menu_help")],
            [InlineKeyboardButton("🏠 HOME", callback_data="menu_main")]
        ])
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    elif data == "menu_contact":
        text, markup = format_contact_card(
            bot_name=b_name,
            support_link=s_link,
            support_username=s_uname
        )
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    elif data == "menu_subscription":
        expiry_info = db.get_user_expiry_info(user_id)
        is_admin = db.is_admin(user_id)
        if is_admin:
            status_str = "🟢 ACTIVE (ADMIN)"
            valid_till = "Lifetime / Permanent 👑"
            days_left = "Unlimited"
        elif expiry_info and expiry_info.get("expiry_date"):
            status_str = "🟢 ACTIVE"
            exp_dt = expiry_info.get("expiry_date")
            if isinstance(exp_dt, str):
                try:
                    exp_dt = datetime.fromisoformat(exp_dt).strftime("%d %B %Y, %I:%M %p")
                except Exception:
                    pass
            elif isinstance(exp_dt, datetime):
                exp_dt = exp_dt.strftime("%d %B %Y, %I:%M %p")
            valid_till = str(exp_dt)
            days_left = f"{expiry_info.get('days_left', 0)} days remaining"
        else:
            status_str = "🟢 ACTIVE"
            valid_till = "Configured"
            days_left = "Active"

        text = (
            "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
            "│ 👑 <b>MY SUBSCRIPTION</b>   │\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            f"• <b>User ID:</b> <code>{user_id}</code>\n"
            f"• <b>Status:</b> {status_str}\n"
            f"• <b>Valid Till:</b> <code>{valid_till}</code>\n"
            f"• <b>Time Remaining:</b> <code>{days_left}</code>\n\n"
            "<b>Available Benefits:</b>\n"
            "✅ High Speed Video Streams\n"
            "✅ 4K UHD Remote Slicing\n"
            "✅ Automatic PDF Extraction\n"
            "✅ Custom Moving Watermarks\n"
            "✅ YouTube Cookies Isolation\n"
            "✅ Unlimited Batch Downloads"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 HOME", callback_data="menu_main")]
        ])
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    elif data == "menu_help":
        b_name = get_bot_display_name(client)
        text = (
            f"<b>ℹ️ {b_name} — USER MANUAL</b>\n\n"
            "<b>📥 Downloading Content:</b>\n"
            "• <b>Direct Links:</b> Send any lecture or YouTube link directly.\n"
            "• <b>Batch TXT:</b> Send a <code>.txt</code> file or use <code>/drm</code>.\n\n"
            "<b>🍪 YouTube Cookies:</b>\n"
            "• Use <code>/cookies</code> to upload browser <code>cookies.txt</code>.\n"
            "• Check status with <code>/getcookies</code>.\n\n"
            "<b>🛠️ Tools:</b>\n"
            "• <code>/t2t</code> — Convert raw text into .txt course file\n"
            "• <code>/t2h</code> — Web HTML converter\n"
            "• <code>/id</code> — Get your Telegram User ID"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="menu_main")]
        ])
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)


# ==============================================================================
# 📄 RESULT FILES & RETRY FAILED CALLBACKS
# ==============================================================================

async def result_file_callback(client: Client, query: CallbackQuery):
    await query.answer()
    data = query.data
    parts = data.split("_", 2)
    file_type = parts[1]
    job_id = parts[2]

    job_data = db.get_job(job_id)
    if not job_data:
        await query.message.reply_text("❌ Job details not found in history.")
        return

    job = JobCheckpoint.from_dict(job_data)
    filename = "successful.txt" if file_type == "succ" else "failed.txt"
    filepath = os.path.join(job.temp_dir, filename) if job.temp_dir else None

    # Recreate if missing on disk
    if not filepath or not os.path.exists(filepath):
        course_data = parse_course_txt(job.raw_content, job.source_filename)
        indices = job.completed_indices if file_type == "succ" else job.failed_indices
        lines = [course_data.all_items[idx].raw_line for idx in indices if idx < len(course_data.all_items)]
        os.makedirs("downloads", exist_ok=True)
        filepath = os.path.join("downloads", f"{job_id}_{filename}")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    caption = f"📄 <b>{filename.upper()}</b> for Job <code>{job_id}</code>\n\n𝄟⃝⚡️ <b>Course Wallah</b>"
    try:
        await query.message.reply_document(document=filepath, caption=caption, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await query.message.reply_text(f"❌ Failed to send file: {e}")


async def retry_failed_callback(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    bot_ctx = get_bot_context(client)
    bot_id = bot_ctx.bot_id if bot_ctx else "bot_1"
    jm = get_job_manager(client, bot_id=bot_id)

    if jm.is_user_busy(user_id, bot_id=bot_id):
        await query.answer("⚠️ You already have an active download running! Use /status to check.", show_alert=True)
        return

    await query.answer("🔄 Preparing retry for failed items...", show_alert=True)
    job_id = query.data.replace("retry_fail_", "")
    job_data = db.get_job(job_id, bot_id=bot_id)
    if not job_data:
        await query.message.reply_text("❌ Job data not found.")
        return

    job = JobCheckpoint.from_dict(job_data)
    course_data = parse_course_txt(job.raw_content, job.source_filename)
    failed_lines = [course_data.all_items[idx].raw_line for idx in job.failed_indices if idx < len(course_data.all_items)]

    if not failed_lines:
        await query.message.reply_text("ℹ️ No failed items found to retry!")
        return

    retry_content = "\n".join(failed_lines)
    retry_job = jm.create_job(
        user_id=user_id,
        chat_id=job.chat_id,
        channel_id=job.channel_id,
        source_filename=f"retry_{job.source_filename}",
        raw_content=retry_content,
        total_items=len(failed_lines),
        start_index=1,
        batch_name=f"Retry — {job.batch_name}",
        quality=job.quality,
        watermark=job.watermark,
        credit=job.credit,
        pw_token=job.pw_token,
        thumb=job.thumb,
        active_thread_id=job.active_thread_id,
        bot_id=bot_id,
        bot_username=get_bot_username(client)
    )

    await query.message.reply_text(
        f"🔄 <b>Retry Job Started!</b>\n\n"
        f"• <b>New Job ID:</b> <code>{retry_job.job_id}</code>\n"
        f"• <b>Items to Retry:</b> {len(failed_lines)}\n"
        f"• <b>Original Job:</b> <code>{job_id}</code>\n\n"
        "Processing failed items in background..."
    )
    await jm.launch_job(retry_job, client, execute_job_pipeline)


# ==============================================================================
# 🎯 DIRECT LINK & DOCUMENT INPUT HANDLERS
# ==============================================================================

_PENDING_YT_JOBS: Dict[str, Dict[str, Any]] = {}


async def _execute_youtube_download(
    client: Client,
    job_id: str,
    user_id: int,
    url: str,
    title: str,
    quality: str,
    media_data: Optional[Dict[str, Any]],
    status_msg: Message,
    chat_id: Union[int, str],
    topic_thread_id: Optional[int] = None,
    credit: str = CREDIT
):
    """
    Executes YouTube video download and upload with the selected quality.
    """
    temp_files = []
    job_temp = os.path.join(TEMP_DIR, str(user_id), job_id)
    os.makedirs(job_temp, exist_ok=True)
    clean_title = helper.safe_filename(title) or f"YouTube_Lecture_{int(time.time())}"
    caption = (
        f"🎬 <b>Title:</b> {clean_title}\n"
        f"📺 <b>Quality:</b> {quality}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>Downloaded via:</b> {credit}"
    )

    try:
        await status_msg.edit_text(
            format_download_card("Course Wallah", clean_title, frame=0),
            parse_mode=enums.ParseMode.HTML
        )
    except Exception:
        pass

    target_height = parse_quality_number(quality)
    yt_info = None
    if media_data and media_data.get("raw_data"):
        yt_info = parse_ytultra_response(media_data["raw_data"], target_height=target_height)

    if not yt_info or not yt_info.get("url"):
        yt_info = await asyncio.to_thread(resolve_youtube_ytultra_info, url, quality)

    yt_done = False
    if yt_info and yt_info.get("url"):
        try:
            res_msg = await helper.process_and_upload_remote_youtube_parts(
                client,
                status_msg,
                caption,
                clean_title,
                url,
                yt_info,
                chat_id,
                user_id=user_id,
                quality=quality,
                custom_dir=job_temp,
                thumbnail=None,
                watermark=WATERMARK_TEXT if WATERMARK_TEXT not in ("/d", "no", "none", "") else None,
                topic_thread_id=topic_thread_id,
                parse_mode=enums.ParseMode.HTML
            )
            if res_msg:
                yt_done = True
        except Exception as exc:
            logger.warning(f"[YOUTUBE] Remote part processing failed: {exc}")
            if "insufficient for the requested ~1800 MB part architecture" in str(exc):
                try:
                    await status_msg.edit_text(
                        "⚠️ <b>Temporary storage is insufficient for this video. The download was stopped safely.</b>",
                        parse_mode=enums.ParseMode.HTML
                    )
                except Exception:
                    pass
                return

    if not yt_done:
        yt_file = await helper.download_video(url, clean_title, quality, user_id=user_id, custom_dir=job_temp)
        if not yt_file or not os.path.exists(yt_file) or os.path.getsize(yt_file) == 0:
            raise RuntimeError("YouTube download produced empty or missing file.")
        temp_files.append(yt_file)

        final_v_file = yt_file
        if WATERMARK_TEXT and WATERMARK_TEXT not in ("/d", "no", "none", ""):
            try:
                await status_msg.edit_text(format_watermark_card(clean_title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
            wm_res = await asyncio.to_thread(helper.apply_video_watermark, yt_file, WATERMARK_TEXT, WATERMARK_FILE)
            if wm_res and os.path.exists(wm_res):
                temp_files.append(wm_res)
                final_v_file = wm_res

        v_thumb = await asyncio.to_thread(helper.extract_or_download_thumbnail, final_v_file, None, None)
        if v_thumb and os.path.exists(v_thumb):
            temp_files.append(v_thumb)

        try:
            await status_msg.edit_text(format_upload_card(clean_title, frame=0), parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass

        await helper.send_vid(
            client,
            status_msg,
            caption,
            final_v_file,
            v_thumb,
            clean_title,
            status_msg,
            chat_id,
            topic_thread_id=topic_thread_id,
            parse_mode=enums.ParseMode.HTML
        )

    try:
        await status_msg.delete()
    except Exception:
        pass
    finally:
        for tf in temp_files:
            cleanup_uploaded_file(tf)
        cleanup_job_temp_dir(job_temp)


async def _run_direct_link_job(
    job_id: str,
    client: Client,
    message: Message,
    url: str,
    text: str,
    user_id: int,
    status_msg: Message
):
    bot_ctx = get_bot_context(client)
    bot_id = bot_ctx.bot_id if bot_ctx else "bot_1"
    jm = get_job_manager(client, bot_id=bot_id)
    temp_files = []
    job_temp = os.path.join(TEMP_DIR, str(user_id), job_id)
    os.makedirs(job_temp, exist_ok=True)
    media_type = MediaRouter.classify_url(url)

    try:
        # --- 1. YouTube Pipeline ---
        if media_type == MediaType.YOUTUBE:
            raw_title = text.split("\n")[0].replace(url, "").strip()
            title = helper.safe_filename(raw_title) or f"YouTube_Lecture_{int(time.time())}"

            try:
                await status_msg.edit_text(format_download_card("Course Wallah", title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

            # Query all available formats from YTUltra
            media_data = await asyncio.to_thread(fetch_ytultra_media_data, url)
            qualities = media_data.get("qualities", []) if media_data else []

            if media_data and media_data.get("title") and not raw_title:
                title = helper.safe_filename(media_data["title"])

            duration_sec = float(media_data.get("duration") or 0) if media_data else 0.0

            # Check if user has an explicit configured quality
            configured_q = db.get_user_quality(user_id) if hasattr(db, "get_user_quality") else None
            if configured_q and configured_q.lower() not in ("auto", "ask", "none", ""):
                req_h = parse_quality_number(configured_q)
                has_req = any(q.get("height") == req_h for q in qualities)
                if has_req:
                    await _execute_youtube_download(
                        client=client,
                        job_id=job_id,
                        user_id=user_id,
                        url=url,
                        title=title,
                        quality=f"{req_h}p",
                        media_data=media_data,
                        status_msg=status_msg,
                        chat_id=message.chat.id,
                        topic_thread_id=getattr(message, "message_thread_id", None),
                        credit=CREDIT
                    )
                    return
                elif qualities:
                    highest_q = qualities[0]
                    card_text, markup = format_youtube_fallback_card(
                        title=title,
                        duration_sec=duration_sec,
                        requested_height=req_h,
                        highest_quality=highest_q,
                        job_id=job_id,
                        user_id=user_id
                    )
                    _PENDING_YT_JOBS[job_id] = {
                        "user_id": user_id,
                        "chat_id": message.chat.id,
                        "url": url,
                        "title": title,
                        "media_data": media_data,
                        "created_at": time.time(),
                        "topic_thread_id": getattr(message, "message_thread_id", None)
                    }
                    await status_msg.edit_text(card_text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
                    return

            # Interactive Selection: If multiple playable qualities exist, show quality menu
            if len(qualities) > 1:
                card_text, markup = format_youtube_quality_menu(
                    title=title,
                    duration_sec=duration_sec,
                    qualities=qualities,
                    job_id=job_id,
                    user_id=user_id
                )
                _PENDING_YT_JOBS[job_id] = {
                    "user_id": user_id,
                    "chat_id": message.chat.id,
                    "url": url,
                    "title": title,
                    "media_data": media_data,
                    "created_at": time.time(),
                    "topic_thread_id": getattr(message, "message_thread_id", None)
                }
                await status_msg.edit_text(card_text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
                return

            # Default / Single Quality Fallback
            chosen_q = f"{qualities[0]['height']}p" if qualities else "720p"
            await _execute_youtube_download(
                client=client,
                job_id=job_id,
                user_id=user_id,
                url=url,
                title=title,
                quality=chosen_q,
                media_data=media_data,
                status_msg=status_msg,
                chat_id=message.chat.id,
                topic_thread_id=getattr(message, "message_thread_id", None),
                credit=CREDIT
            )
            return


        # --- 2. Direct PDF Pipeline ---
        elif media_type == MediaType.DIRECT_PDF:
            clean_pdf_url = parse_pdf_input(url)["url"]
            title = MediaRouter.extract_clean_title(clean_pdf_url, text.split("\n")[0].replace(url, "").strip()) or f"Document_{int(time.time())}"
            try:
                await status_msg.edit_text(format_pdf_download_card("Course Wallah", title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

            name_clean = helper.safe_filename(title)
            pdf_file = await helper.download_pdf(
                url=url,
                name=name_clean,
                watermark_text=WATERMARK_TEXT,
                watermark_file=WATERMARK_FILE,
                custom_dir=job_temp
            )
            if not pdf_file or not os.path.exists(pdf_file) or os.path.getsize(pdf_file) == 0:
                raise RuntimeError("PDF download produced empty or invalid file.")
            temp_files.append(pdf_file)

            pdf_cap = build_pdf_caption(
                course_name="Course Wallah",
                lecture_title=title,
                credit=CREDIT
            )

            try:
                await status_msg.edit_text(format_pdf_upload_card("Course Wallah", title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

            await client.send_document(
                chat_id=message.chat.id,
                document=pdf_file,
                caption=pdf_cap,
                parse_mode=enums.ParseMode.HTML
            )
            try:
                await status_msg.delete()
            except Exception:
                pass

        # --- KGS HLS Stream Pipeline ---
        elif media_type == MediaType.KGS_HLS:
            title = MediaRouter.extract_clean_title(url, text.split("\n")[0].replace(url, "").strip()) or f"KGS_Lecture_{int(time.time())}"
            name_clean = helper.safe_filename(title)

            try:
                await status_msg.edit_text(format_download_card("Course Wallah", title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

            print(f"[KGS {job_id}] DOWNLOAD START")
            logging.info(f"[KGS {job_id}] DOWNLOAD START")
            v_file = await asyncio.to_thread(helper.download_kgs, url, name_clean, "720p", None, job_temp, user_id, job_id)
            if not v_file or not os.path.exists(v_file) or os.path.getsize(v_file) == 0:
                raise RuntimeError("KGS HLS video download produced empty or missing file.")
            print(f"[KGS {job_id}] DOWNLOAD SUCCESS")
            logging.info(f"[KGS {job_id}] DOWNLOAD SUCCESS")
            temp_files.append(v_file)

            if jm.cancel_flags.get(job_id, False):
                return

            final_v_file = v_file
            if WATERMARK_TEXT and WATERMARK_TEXT not in ("/d", "no", "none", ""):
                try:
                    await status_msg.edit_text(format_watermark_card(title, frame=0), parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
                print(f"[KGS {job_id}] WATERMARK START")
                logging.info(f"[KGS {job_id}] WATERMARK START")
                wm_res = await asyncio.to_thread(helper.apply_video_watermark, v_file, WATERMARK_TEXT, WATERMARK_FILE)
                if wm_res and os.path.exists(wm_res):
                    temp_files.append(wm_res)
                    final_v_file = wm_res
                print(f"[KGS {job_id}] WATERMARK SUCCESS")
                logging.info(f"[KGS {job_id}] WATERMARK SUCCESS")

            v_thumb = await asyncio.to_thread(helper.extract_or_download_thumbnail, final_v_file, None, None)
            if v_thumb and os.path.exists(v_thumb):
                temp_files.append(v_thumb)

            caption = (
                f"🎥 <b>Title:</b> {title}\n"
                f"📺 <b>Format:</b> KGS HLS Stream\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ <b>Downloaded via:</b> {CREDIT}"
            )

            if jm.cancel_flags.get(job_id, False):
                return

            try:
                await status_msg.edit_text(format_upload_card(title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

            print(f"[KGS {job_id}] UPLOAD START")
            logging.info(f"[KGS {job_id}] UPLOAD START")
            await helper.send_vid(
                client,
                status_msg,
                caption,
                final_v_file,
                v_thumb,
                title,
                status_msg,
                message.chat.id,
                parse_mode=enums.ParseMode.HTML,
                user_id=user_id,
                bot_id=bot_ctx.bot_id if bot_ctx else None
            )
            print(f"[KGS {job_id}] UPLOAD SUCCESS")
            logging.info(f"[KGS {job_id}] UPLOAD SUCCESS")
            try:
                await status_msg.delete()
            except Exception:
                pass

        # --- 3. Direct M3U8 Stream Pipeline ---
        elif media_type == MediaType.DIRECT_M3U8:
            title = MediaRouter.extract_clean_title(url, text.split("\n")[0].replace(url, "").strip()) or f"Stream_{int(time.time())}"
            name_clean = helper.safe_filename(title)

            try:
                await status_msg.edit_text(format_download_card("Course Wallah", title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

            print(f"[DIRECT M3U8 {job_id}] DOWNLOAD START")
            logging.info(f"[DIRECT M3U8 {job_id}] DOWNLOAD START")
            v_file = await asyncio.to_thread(helper.download_appx_m3u8, url, name_clean, None, job_temp)
            if not v_file or not os.path.exists(v_file) or os.path.getsize(v_file) == 0:
                raise RuntimeError("Direct M3U8 stream download produced empty or missing file.")
            print(f"[DIRECT M3U8 {job_id}] DOWNLOAD SUCCESS")
            logging.info(f"[DIRECT M3U8 {job_id}] DOWNLOAD SUCCESS")
            temp_files.append(v_file)

            if jm.cancel_flags.get(job_id, False):
                return

            final_v_file = v_file
            if WATERMARK_TEXT and WATERMARK_TEXT not in ("/d", "no", "none", ""):
                try:
                    await status_msg.edit_text(format_watermark_card(title, frame=0), parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
                print(f"[DIRECT M3U8 {job_id}] WATERMARK START")
                logging.info(f"[DIRECT M3U8 {job_id}] WATERMARK START")
                wm_res = await asyncio.to_thread(helper.apply_video_watermark, v_file, WATERMARK_TEXT, WATERMARK_FILE)
                if wm_res and os.path.exists(wm_res):
                    temp_files.append(wm_res)
                    final_v_file = wm_res
                print(f"[DIRECT M3U8 {job_id}] WATERMARK SUCCESS")
                logging.info(f"[DIRECT M3U8 {job_id}] WATERMARK SUCCESS")

            v_thumb = await asyncio.to_thread(helper.extract_or_download_thumbnail, final_v_file, None, None)
            if v_thumb and os.path.exists(v_thumb):
                temp_files.append(v_thumb)

            caption = (
                f"🎬 <b>Title:</b> {title}\n"
                f"📺 <b>Format:</b> M3U8 Stream\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 <b>Downloaded via:</b> {CREDIT}"
            )

            if jm.cancel_flags.get(job_id, False):
                return

            try:
                await status_msg.edit_text(format_upload_card(title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

            print(f"[DIRECT M3U8 {job_id}] UPLOAD START")
            logging.info(f"[DIRECT M3U8 {job_id}] UPLOAD START")
            await helper.send_vid(
                client,
                status_msg,
                caption,
                final_v_file,
                v_thumb,
                title,
                status_msg,
                message.chat.id,
                parse_mode=enums.ParseMode.HTML
            )
            print(f"[DIRECT M3U8 {job_id}] UPLOAD SUCCESS")
            logging.info(f"[DIRECT M3U8 {job_id}] UPLOAD SUCCESS")
            try:
                await status_msg.delete()
            except Exception:
                pass

        # --- 4. Encrypted Stream Pipeline ---
        elif media_type == MediaType.ENCRYPTED_STREAM:
            key = None
            raw_url = url
            if "*" in raw_url:
                parts = raw_url.split("*", 1)
                raw_url = parts[0].strip()
                key = parts[1].strip()

            title = helper.safe_filename(text.split("\n")[0].replace(url, "").strip()) or f"Encrypted_Lecture_{int(time.time())}"
            name_clean = helper.safe_filename(title)

            try:
                await status_msg.edit_text(format_download_card("Course Wallah", title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

            enc_file = await asyncio.to_thread(helper.download_and_decrypt_video, raw_url, name_clean, key)
            if not enc_file or not os.path.exists(enc_file) or os.path.getsize(enc_file) == 0:
                raise RuntimeError("Encrypted video download or decryption failed.")
            temp_files.append(enc_file)

            final_enc = enc_file
            if WATERMARK_TEXT and WATERMARK_TEXT not in ("/d", "no", "none", ""):
                try:
                    await status_msg.edit_text(format_watermark_card(title, frame=0), parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
                wm_res = await asyncio.to_thread(helper.apply_video_watermark, enc_file, WATERMARK_TEXT, WATERMARK_FILE)
                if wm_res and os.path.exists(wm_res):
                    temp_files.append(wm_res)
                    final_enc = wm_res

            enc_thumb = await asyncio.to_thread(helper.extract_or_download_thumbnail, final_enc, None, None)
            if enc_thumb and os.path.exists(enc_thumb):
                temp_files.append(enc_thumb)

            caption = (
                f"🎬 <b>Title:</b> {title}\n"
                f"📺 <b>Type:</b> Decrypted Media\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 <b>Downloaded via:</b> {CREDIT}"
            )

            try:
                await status_msg.edit_text(format_upload_card(title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

            await helper.send_vid(
                client,
                status_msg,
                caption,
                final_enc,
                enc_thumb,
                title,
                status_msg,
                message.chat.id,
                parse_mode=enums.ParseMode.HTML
            )
            try:
                await status_msg.delete()
            except Exception:
                pass

        # --- 5. Spayee / Go Classes Specialized HLS Pipeline ---
        elif media_type in (MediaType.SPAYEE_HLS, MediaType.GO_CLASSES):
            key = None
            raw_url = url
            if "*" in raw_url:
                parts = raw_url.split("*", 1)
                raw_url = parts[0].strip()
                key = parts[1].strip()

            stream_label = "Go Classes Stream" if media_type == MediaType.GO_CLASSES else "Spayee HLS Stream"
            title = MediaRouter.extract_clean_title(raw_url, text.split("\n")[0].replace(url, "").strip()) or f"Lecture_{int(time.time())}"
            name_clean = helper.safe_filename(title)

            try:
                await status_msg.edit_text(format_download_card("Course Wallah", title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

            v_file = await asyncio.to_thread(helper.download_spayee_hls, raw_url, name_clean, key, "720p", job_temp)
            if not v_file or not os.path.exists(v_file) or os.path.getsize(v_file) == 0:
                raise RuntimeError(f"{stream_label} video download produced empty or missing file.")
            temp_files.append(v_file)

            final_v_file = v_file
            if WATERMARK_TEXT and WATERMARK_TEXT not in ("/d", "no", "none", ""):
                try:
                    await status_msg.edit_text(format_watermark_card(title, frame=0), parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
                wm_res = await asyncio.to_thread(helper.apply_video_watermark, v_file, WATERMARK_TEXT, WATERMARK_FILE)
                if wm_res and os.path.exists(wm_res):
                    temp_files.append(wm_res)
                    final_v_file = wm_res

            v_thumb = await asyncio.to_thread(helper.extract_or_download_thumbnail, final_v_file, None, None)
            if v_thumb and os.path.exists(v_thumb):
                temp_files.append(v_thumb)

            caption = (
                f"🎬 <b>Title:</b> {title}\n"
                f"📺 <b>Type:</b> {stream_label}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 <b>Downloaded via:</b> {CREDIT}"
            )

            try:
                await status_msg.edit_text(format_upload_card(title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

            await helper.send_vid(
                client,
                status_msg,
                caption,
                final_v_file,
                v_thumb,
                title,
                status_msg,
                message.chat.id,
                parse_mode=enums.ParseMode.HTML,
                user_id=user_id,
                bot_id=bot_ctx.bot_id if bot_ctx else "bot_1"
            )
            try:
                await status_msg.delete()
            except Exception:
                pass

        # --- 6. Direct Image Pipeline (.jpg, .jpeg, .png, .webp, etc.) ---
        elif media_type == MediaType.DIRECT_IMAGE:
            title = MediaRouter.extract_clean_title(url, text.split("\n")[0].replace(url, "").strip()) or f"Image_{int(time.time())}"
            name_clean = helper.safe_filename(title)

            try:
                await status_msg.edit_text(format_download_card("Course Wallah", title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

            print(f"[IMAGE {job_id}] DOWNLOAD START")
            logging.info(f"[IMAGE {job_id}] DOWNLOAD START")
            img_file = await asyncio.to_thread(helper.download_image, url, name_clean, None, job_temp)
            if not img_file or not os.path.exists(img_file) or os.path.getsize(img_file) == 0:
                raise RuntimeError("Direct image download produced empty or invalid file.")
            print(f"[IMAGE {job_id}] DOWNLOAD SUCCESS")
            logging.info(f"[IMAGE {job_id}] DOWNLOAD SUCCESS")
            temp_files.append(img_file)

            if jm.cancel_flags.get(job_id, False):
                return

            caption = (
                f"🖼 <b>Title:</b> {title}\n"
                f"📷 <b>Format:</b> Image\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 <b>Downloaded via:</b> {CREDIT}"
            )

            try:
                await status_msg.edit_text(format_upload_card(title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

            print(f"[IMAGE {job_id}] UPLOAD START")
            logging.info(f"[IMAGE {job_id}] UPLOAD START")
            try:
                await client.send_photo(
                    chat_id=message.chat.id,
                    photo=img_file,
                    caption=caption,
                    parse_mode=enums.ParseMode.HTML
                )
            except Exception:
                await client.send_document(
                    chat_id=message.chat.id,
                    document=img_file,
                    caption=caption,
                    parse_mode=enums.ParseMode.HTML
                )
            print(f"[IMAGE {job_id}] UPLOAD SUCCESS")
            logging.info(f"[IMAGE {job_id}] UPLOAD SUCCESS")
            try:
                await status_msg.delete()
            except Exception:
                pass

        # --- 7. Direct Video Pipeline (.mp4, .mkv, .webm, etc.) ---
        elif media_type == MediaType.DIRECT_VIDEO:
            title = MediaRouter.extract_clean_title(url, text.split("\n")[0].replace(url, "").strip()) or f"Video_{int(time.time())}"
            name_clean = helper.safe_filename(title)

            try:
                await status_msg.edit_text(format_download_card("Course Wallah", title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

            print(f"[DIRECT VIDEO {job_id}] DOWNLOAD START")
            logging.info(f"[DIRECT VIDEO {job_id}] DOWNLOAD START")
            v_file = await asyncio.to_thread(helper.download_direct_video, url, name_clean, None, job_temp)
            if not v_file or not os.path.exists(v_file) or os.path.getsize(v_file) == 0:
                raise RuntimeError("Direct video download produced empty or missing file.")
            print(f"[DIRECT VIDEO {job_id}] DOWNLOAD SUCCESS")
            logging.info(f"[DIRECT VIDEO {job_id}] DOWNLOAD SUCCESS")
            temp_files.append(v_file)

            if jm.cancel_flags.get(job_id, False):
                return

            final_v_file = v_file
            if WATERMARK_TEXT and WATERMARK_TEXT not in ("/d", "no", "none", ""):
                try:
                    await status_msg.edit_text(format_watermark_card(title, frame=0), parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
                print(f"[DIRECT VIDEO {job_id}] WATERMARK START")
                logging.info(f"[DIRECT VIDEO {job_id}] WATERMARK START")
                wm_res = await asyncio.to_thread(helper.apply_video_watermark, v_file, WATERMARK_TEXT, WATERMARK_FILE)
                if wm_res and os.path.exists(wm_res):
                    temp_files.append(wm_res)
                    final_v_file = wm_res
                print(f"[DIRECT VIDEO {job_id}] WATERMARK SUCCESS")
                logging.info(f"[DIRECT VIDEO {job_id}] WATERMARK SUCCESS")

            v_thumb = await asyncio.to_thread(helper.extract_or_download_thumbnail, final_v_file, None, None)
            if v_thumb and os.path.exists(v_thumb):
                temp_files.append(v_thumb)

            caption = (
                f"🎬 <b>Title:</b> {title}\n"
                f"📺 <b>Format:</b> Direct Video\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 <b>Downloaded via:</b> {CREDIT}"
            )

            if jm.cancel_flags.get(job_id, False):
                return

            try:
                await status_msg.edit_text(format_upload_card(title, frame=0), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

            print(f"[DIRECT VIDEO {job_id}] UPLOAD START")
            logging.info(f"[DIRECT VIDEO {job_id}] UPLOAD START")
            await helper.send_vid(
                client,
                status_msg,
                caption,
                final_v_file,
                v_thumb,
                title,
                status_msg,
                message.chat.id,
                parse_mode=enums.ParseMode.HTML,
                user_id=user_id,
                bot_id=bot_ctx.bot_id if bot_ctx else None
            )
            print(f"[DIRECT VIDEO {job_id}] UPLOAD SUCCESS")
            logging.info(f"[DIRECT VIDEO {job_id}] UPLOAD SUCCESS")
            try:
                await status_msg.delete()
            except Exception:
                pass

        # --- 6. AppX / ClassX Lecture API Pipeline ---
        elif media_type == MediaType.APPX_LECTURE:
            try:
                await status_msg.edit_text(
                    "╭────────────────────────────╮\n"
                    "│       🎓 <b>COURSE WALLAH</b>     │\n"
                    "│                            │\n"
                    "│      🔎 <b>RESOLVING ···</b>        │\n"
                    "│                            │\n"
                    "│ 🔍 Querying authorized source\n"
                    "│                            │\n"
                    "│        ◉ ○ ○ ○ ○           │\n"
                    "│   <i>Preparing your lecture</i>    │\n"
                    "╰────────────────────────────╯",
                    parse_mode=enums.ParseMode.HTML
                )
            except Exception:
                pass

            lec_res = await asyncio.to_thread(helper.resolve_lecture_source, url, "720p")
            if lec_res.is_drm:
                await status_msg.edit_text(f"⛔ <b>DRM Protected Content</b>\n\n<blockquote>{lec_res.drm_message or 'This lecture is DRM protected.'}</blockquote>")
                return

            if not lec_res.has_video and not lec_res.has_pdf:
                await status_msg.edit_text(f"❌ <b>Lecture Resolution Failed</b>\n\n<blockquote>{lec_res.error or 'No playable video or PDF found in source.'}</blockquote>")
                return

            item_title = lec_res.title or helper.safe_filename(text.split("\n")[0].replace(url, "").strip()) or f"Lecture_{int(time.time())}"
            name_clean = helper.safe_filename(item_title)

            # Order: VIDEO -> PDF
            if lec_res.has_video and lec_res.video_url:
                if jm.cancel_flags.get(job_id, False):
                    return

                try:
                    await status_msg.edit_text(format_download_card("Course Wallah", item_title, frame=0), parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass

                print(f"[APPX {job_id}] M3U8 DOWNLOAD START")
                logging.info(f"[APPX {job_id}] M3U8 DOWNLOAD START")
                v_file = await asyncio.to_thread(helper.download_appx_m3u8, lec_res.video_url, name_clean, None, job_temp)
                if not v_file or not os.path.exists(v_file) or os.path.getsize(v_file) == 0:
                    raise RuntimeError("Video download produced empty or missing file.")
                print(f"[APPX {job_id}] M3U8 DOWNLOAD SUCCESS")
                logging.info(f"[APPX {job_id}] M3U8 DOWNLOAD SUCCESS")
                temp_files.append(v_file)

                if jm.cancel_flags.get(job_id, False):
                    return

                final_v_file = v_file
                if WATERMARK_TEXT and WATERMARK_TEXT not in ("/d", "no", "none", ""):
                    try:
                        await status_msg.edit_text(format_watermark_card(item_title, frame=0), parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass
                    print(f"[APPX {job_id}] WATERMARK START")
                    logging.info(f"[APPX {job_id}] WATERMARK START")
                    wm_res = await asyncio.to_thread(helper.apply_video_watermark, v_file, WATERMARK_TEXT, WATERMARK_FILE)
                    if wm_res and os.path.exists(wm_res):
                        temp_files.append(wm_res)
                        final_v_file = wm_res
                    print(f"[APPX {job_id}] WATERMARK SUCCESS")
                    logging.info(f"[APPX {job_id}] WATERMARK SUCCESS")

                v_thumb = await asyncio.to_thread(helper.extract_or_download_thumbnail, final_v_file, lec_res.thumbnail, None)
                if v_thumb and os.path.exists(v_thumb):
                    temp_files.append(v_thumb)

                caption = (
                    f"🎬 <b>Title:</b> {item_title}\n"
                    f"📺 <b>Quality:</b> {lec_res.video_quality or '720p'}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🤖 <b>Downloaded via:</b> {CREDIT}"
                )

                if jm.cancel_flags.get(job_id, False):
                    return

                try:
                    await status_msg.edit_text(format_upload_card(item_title, frame=0), parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass

                print(f"[APPX {job_id}] UPLOAD START")
                logging.info(f"[APPX {job_id}] UPLOAD START")
                await helper.send_vid(
                    client,
                    status_msg,
                    caption,
                    final_v_file,
                    v_thumb,
                    item_title,
                    status_msg,
                    message.chat.id,
                    parse_mode=enums.ParseMode.HTML
                )
                print(f"[APPX {job_id}] UPLOAD SUCCESS")
                logging.info(f"[APPX {job_id}] UPLOAD SUCCESS")

            # Process PDF if available immediately after video
            if lec_res.has_pdf and lec_res.pdf_url:
                if jm.cancel_flags.get(job_id, False):
                    return

                try:
                    await status_msg.edit_text(format_pdf_download_card("Course Wallah", item_title, frame=0), parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass

                print(f"[APPX {job_id}] PDF DOWNLOAD START")
                logging.info(f"[APPX {job_id}] PDF DOWNLOAD START")
                pdf_doc = await helper.download_pdf(
                    url=lec_res.pdf_url,
                    name=f"{name_clean}_notes",
                    watermark_text=WATERMARK_TEXT,
                    watermark_file=WATERMARK_FILE,
                    custom_dir=job_temp
                )
                if pdf_doc and os.path.exists(pdf_doc) and os.path.getsize(pdf_doc) > 0:
                    temp_files.append(pdf_doc)
                    print(f"[APPX {job_id}] PDF DOWNLOAD SUCCESS")
                    logging.info(f"[APPX {job_id}] PDF DOWNLOAD SUCCESS")
                    pdf_cap = build_pdf_caption(
                        course_name="Course Wallah",
                        lecture_title=item_title,
                        credit=CREDIT
                    )

                    try:
                        await status_msg.edit_text(format_pdf_upload_card("Course Wallah", item_title, frame=0), parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass

                    print(f"[APPX {job_id}] PDF UPLOAD START")
                    logging.info(f"[APPX {job_id}] PDF UPLOAD START")
                    await client.send_document(
                        chat_id=message.chat.id,
                        document=pdf_doc,
                        caption=pdf_cap,
                        parse_mode=enums.ParseMode.HTML
                    )
                    print(f"[APPX {job_id}] PDF UPLOAD SUCCESS")
                    logging.info(f"[APPX {job_id}] PDF UPLOAD SUCCESS")

            try:
                await status_msg.delete()
            except Exception:
                pass

        # --- 7. Unsupported / Unknown URL ---
        else:
            await status_msg.edit_text(
                "❌ <b>Unsupported URL Format</b>\n\n"
                "<blockquote>The provided link could not be classified into any supported video, HLS stream, PDF, or course format.</blockquote>",
                parse_mode=enums.ParseMode.HTML
            )
            return

    except asyncio.CancelledError:
        if jm.cancel_flags.get(job_id, False) and job_id in jm.cancel_reasons:
            reason = jm.cancel_reasons[job_id]
            print(f"[{job_id}] CANCEL REQUESTED: {reason}")
            logging.info(f"[{job_id}] CANCEL REQUESTED: {reason}")
        else:
            print(f"[{job_id}] UNEXPECTED TASK CANCELLATION source=Direct link task cancellation")
            logging.warning(f"[{job_id}] UNEXPECTED TASK CANCELLATION source=Direct link task cancellation")
    except Exception as err:
        logger.error(f"[{job_id}] Direct link processing error: {err}")
        if isinstance(err, PDFPasswordInvalid) or "PDF_PASSWORD_INVALID" in str(err):
            card_text = (
                "❌ <b>PDF DOWNLOAD FAILED</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "📄 <b>PDF:</b> ❌ Invalid PDF password\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 <b>{CREDIT or 'Course Wallah'}</b>"
            )
            try:
                await status_msg.edit_text(card_text, parse_mode=enums.ParseMode.HTML)
            except Exception:
                try:
                    await message.reply_text(card_text, parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
            return

        clean_err = sanitize_error_message(str(err))
        try:
            await status_msg.edit_text(f"❌ <b>Download Failed:</b>\n\n<blockquote>{clean_err[:250]}</blockquote>", parse_mode=enums.ParseMode.HTML)
        except Exception:
            try:
                await message.reply_text(f"❌ <b>Download Failed:</b>\n\n<blockquote>{clean_err[:250]}</blockquote>", parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
    finally:
        for f in temp_files:
            if f:
                cleanup_uploaded_file(f)
        cleanup_job_temp_dir(job_temp)
        jm.clear_user_active_job(user_id, bot_id=bot_id)
        jm.active_tasks.pop(job_id, None)
        jm.cancel_flags.pop(job_id, None)
        jm.cancel_reasons.pop(job_id, None)


async def direct_message_handler(client: Client, message: Message):
    text = message.text.strip()
    url_match = re.search(r"https?://[^\s]+", text)
    if not url_match:
        return

    url = url_match.group(0)
    user_id = message.from_user.id
    user_name = message.from_user.first_name or "User"
    bot_ctx = get_bot_context(client)
    bot_id = bot_ctx.bot_id if bot_ctx else "bot_1"
    jm = get_job_manager(client, bot_id=bot_id)

    if jm.is_user_busy(user_id, bot_id=bot_id):
        await message.reply_text("⚠️ You already have an active download running! Use /status to check.")
        return

    job_id = f"job_{uuid.uuid4().hex[:8].upper()}"
    jm.set_user_active_job(user_id, job_id, bot_id=bot_id)
    jm.cancel_flags[job_id] = False
    jm.cancel_reasons.pop(job_id, None)

    status_msg = await message.reply_text(
        "⚡ <b>PROCESSING YOUR REQUEST</b>\n\n"
        "⠋ Connecting...\n\n"
        "<i>Please wait...</i>"
    )

    # Launch in independent task so Pyrogram reconnects or message timeouts never cancel the media task
    task = asyncio.create_task(_run_direct_link_job(job_id, client, message, url, text, user_id, status_msg))
    jm.active_tasks[job_id] = task


async def direct_document_handler(client: Client, message: Message):
    if not message.document or not message.document.file_name or not message.document.file_name.lower().endswith(".txt"):
        return
    await drm_cmd(client, message, doc_message=message)


# Callbacks for features & details
async def features_callback(client: Client, query: CallbackQuery):
    await query.answer()
    b_name = get_bot_display_name(client)
    features_text = (
        f"<b>🔥 {b_name} Features</b>\n\n"
        "• 📥 Fast M3U8, YouTube & Direct Video Downloader\n"
        "• 📑 PDF Download & Watermarking\n"
        "• 📚 Academic Course Structure (Subject → Unit → Topic)\n"
        "• 💬 Telegram Forum Topics Supergroup Integration\n"
        "• 🍪 Per-User YouTube Cookies Isolation\n"
        "• 🚦 Pause (/stop), Resume (/resume), Cancel (/cancel)\n"
        "• ♻️ Automatic Crash Recovery & Restart-Safe Checkpoints\n"
        "• ⚡ Real Speed Calculation & Dynamic Progress Cards\n"
        "• 🎨 Dynamic Moving Video Watermark Support"
    )
    await query.message.edit_text(
        features_text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_main")]])
    )


async def details_callback(client: Client, query: CallbackQuery):
    await query.answer()
    b_name = get_bot_display_name(client)
    s_link = get_bot_support_link(client)
    b_user = get_bot_username(client)
    details_text = (
        f"<b>📋 {b_name} Details</b>\n\n"
        f"• 🤖 <b>Bot Name:</b> {b_name}\n"
        f"• 🏷 <b>Username:</b> @{b_user}\n"
        f"• 📱 <b>Support:</b> {s_link}\n"
        "• ⚡ <b>Persistence:</b> Database-Free Atomic JSON\n"
        "• 🛠 <b>Framework:</b> Pyrogram 2.x + Asyncio\n\n"
        "<b>🔐 Privacy & Security:</b>\n"
        "• Safe isolated directories for every job\n"
        "• User cookies stored separately and never logged"
    )
    await query.message.edit_text(
        details_text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_main")]])
    )


async def back_to_start_callback(client: Client, query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    user_name = query.from_user.first_name if query.from_user else "User"
    is_admin = db.is_admin(user_id)
    text, markup = build_welcome_dashboard(user_id, user_name, is_admin)
    await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)


async def youtube_quality_callback(client: Client, query: CallbackQuery):
    """
    Handles user tapping a specific quality button (e.g. ytq:job123:2160:user456).
    """
    data = query.data or ""
    parts = data.split(":")
    if len(parts) < 4:
        await query.answer("⚠️ Invalid quality selection.", show_alert=True)
        return

    job_id = parts[1]
    try:
        height = int(parts[2])
        expected_user = int(parts[3])
    except (ValueError, TypeError):
        await query.answer("⚠️ Invalid callback parameters.", show_alert=True)
        return

    if query.from_user and query.from_user.id != expected_user:
        await query.answer("⚠️ This download menu belongs to another user.", show_alert=True)
        return

    await query.answer(f"Selected {height}p quality")

    pending = _PENDING_YT_JOBS.pop(job_id, None)
    if not pending:
        text, markup = format_expired_callback_card()
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        return

    asyncio.create_task(
        _execute_youtube_download(
            client=client,
            job_id=job_id,
            user_id=expected_user,
            url=pending["url"],
            title=pending["title"],
            quality=f"{height}p",
            media_data=pending.get("media_data"),
            status_msg=query.message,
            chat_id=pending["chat_id"],
            topic_thread_id=pending.get("topic_thread_id"),
            credit=pending.get("credit", CREDIT)
        )
    )


async def youtube_menu_callback(client: Client, query: CallbackQuery):
    """
    Handles user clicking 'Choose Quality' button to view full quality selection menu.
    """
    data = query.data or ""
    parts = data.split(":")
    if len(parts) < 3:
        await query.answer("⚠️ Invalid action.", show_alert=True)
        return

    job_id = parts[1]
    try:
        expected_user = int(parts[2])
    except (ValueError, TypeError):
        await query.answer("⚠️ Invalid callback parameters.", show_alert=True)
        return

    if query.from_user and query.from_user.id != expected_user:
        await query.answer("⚠️ This download menu belongs to another user.", show_alert=True)
        return

    await query.answer()
    pending = _PENDING_YT_JOBS.get(job_id)
    if not pending or not pending.get("media_data"):
        text, markup = format_expired_callback_card()
        await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        return

    media_data = pending["media_data"]
    qualities = media_data.get("qualities", [])
    if not qualities:
        await query.message.edit_text("❌ <i>No playable video qualities available for this URL.</i>", parse_mode=enums.ParseMode.HTML)
        return

    text, markup = format_youtube_quality_menu(
        title=pending["title"],
        duration_sec=float(media_data.get("duration") or 0),
        qualities=qualities,
        job_id=job_id,
        user_id=expected_user
    )
    await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)


async def youtube_cancel_callback(client: Client, query: CallbackQuery):
    """
    Handles user cancelling the YouTube download.
    """
    data = query.data or ""
    parts = data.split(":")
    if len(parts) < 3:
        await query.answer("Cancelled")
        return

    job_id = parts[1]
    try:
        expected_user = int(parts[2])
    except (ValueError, TypeError):
        await query.answer("Cancelled")
        return

    if query.from_user and query.from_user.id != expected_user:
        await query.answer("⚠️ This download menu belongs to another user.", show_alert=True)
        return

    await query.answer("Download cancelled")
    _PENDING_YT_JOBS.pop(job_id, None)
    await query.message.edit_text("❌ <b>Download cancelled.</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 HOME", callback_data="menu_main")]]), parse_mode=enums.ParseMode.HTML)


async def input_cancel_callback(client: Client, query: CallbackQuery):
    """
    Handles user cancelling an active input prompt via universal input cards.
    """
    data = query.data or ""
    parts = data.split(":")
    if len(parts) >= 2 and parts[1].isdigit():
        expected_uid = int(parts[1])
        if expected_uid != 0 and query.from_user and query.from_user.id != expected_uid:
            await query.answer("⚠️ This input prompt belongs to another user.", show_alert=True)
            return

    try:
        await query.answer("Cancelled")
        await query.message.edit_text("❌ <b>Operation cancelled.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass


async def input_retry_callback(client: Client, query: CallbackQuery):
    """
    Handles user clicking 'TRY AGAIN' on invalid input card.
    """
    await query.answer()
    text = (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "│ 🎓 <b>COURSE WALLAH</b>      │\n"
        "│   🎬 <b>DOWNLOAD</b>         │\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "🔗 <b>Send your media URL</b>\n\n"
        "You can send:\n"
        "• YouTube URL (up to 4K UHD)\n"
        "• Course / Lecture URL\n"
        "• Direct M3U8 or MP4 Stream\n"
        "• PDF Study Material link\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💡 <b>Example:</b>\n"
        "<code>https://youtu.be/...</code>"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 BATCH DOWNLOAD", callback_data="menu_batch")],
        [InlineKeyboardButton("🏠 HOME", callback_data="menu_main")]
    ])
    await query.message.edit_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)


def add_handler_to_client(client: Client, handler, group: int = 0):
    """Safely register handler synchronously on Pyrogram dispatcher without floating loop tasks."""
    if hasattr(client, "dispatcher") and client.dispatcher:
        disp = client.dispatcher
        if group not in disp.groups:
            disp.groups[group] = []
            from collections import OrderedDict
            disp.groups = OrderedDict(sorted(disp.groups.items()))
        disp.groups[group].append(handler)
    else:
        try:
            client.add_handler(handler, group=group)
        except Exception:
            pass


def register_all_handlers(client: Client):
    """Register all message and callback handlers on an independent Pyrogram Client instance."""
    # User Commands
    add_handler_to_client(client, MessageHandler(start_cmd, filters.command("start") & filters.private & auth_filter))
    add_handler_to_client(client, MessageHandler(help_cmd, filters.command("help") & filters.private & auth_filter))
    add_handler_to_client(client, MessageHandler(id_cmd, filters.command("id")))
    add_handler_to_client(client, EditedMessageHandler(id_cmd, filters.command("id")))
    add_handler_to_client(client, MessageHandler(drm_cmd, filters.command("drm") & filters.private & auth_filter))
    add_handler_to_client(client, MessageHandler(job_status_cmd, filters.command("status") & filters.private & auth_filter))
    add_handler_to_client(client, MessageHandler(job_stop_cmd, filters.command(["stop", "pause"]) & filters.private & auth_filter))
    add_handler_to_client(client, MessageHandler(job_resume_cmd, filters.command(["resume", "retry"]) & filters.private & auth_filter))
    add_handler_to_client(client, MessageHandler(job_cancel_cmd, filters.command("cancel") & filters.private & auth_filter))
    add_handler_to_client(client, MessageHandler(list_user_jobs_cmd, filters.command(["jobs", "myjobs"]) & filters.private & auth_filter))
    add_handler_to_client(client, MessageHandler(cookies_upload_cmd, filters.command("cookies") & filters.private & auth_filter))
    add_handler_to_client(client, MessageHandler(getcookies_cmd, filters.command("getcookies") & filters.private & auth_filter))
    add_handler_to_client(client, MessageHandler(deletecookies_cmd, filters.command("deletecookies") & filters.private & auth_filter))
    add_handler_to_client(client, MessageHandler(text_to_txt_cmd, filters.command(["t2t", "txt"]) & filters.private & auth_filter))
    add_handler_to_client(client, MessageHandler(t2h_cmd, filters.command("t2h") & filters.private & auth_filter))

    # Admin Commands
    add_handler_to_client(client, MessageHandler(admin_panel_cmd, filters.command("admin") & filters.private & admin_filter))
    add_handler_to_client(client, MessageHandler(admin_stats_cmd, filters.command("stats") & filters.private))
    add_handler_to_client(client, MessageHandler(admin_system_cmd, filters.command("system") & filters.private))
    add_handler_to_client(client, MessageHandler(admin_workers_cmd, filters.command("workers") & filters.private))
    add_handler_to_client(client, MessageHandler(admin_setworkers_cmd, filters.command("setworkers") & filters.private))
    add_handler_to_client(client, MessageHandler(admin_setratelimit_cmd, filters.command("setratelimit") & filters.private))
    add_handler_to_client(client, MessageHandler(admin_queue_cmd, filters.command("queue") & filters.private))
    add_handler_to_client(client, MessageHandler(admin_storage_cmd, filters.command("storage") & filters.private))
    add_handler_to_client(client, MessageHandler(admin_cleanup_cmd, filters.command("cleanup") & filters.private))
    add_handler_to_client(client, MessageHandler(broadcast_cmd, filters.command(["broadcast", "broadcast_users"]) & filters.private))
    add_handler_to_client(client, MessageHandler(set_watermark_cmd, filters.command("setwatermark") & filters.private))
    add_handler_to_client(client, MessageHandler(view_watermark_cmd, filters.command("watermark") & filters.private))
    add_handler_to_client(client, MessageHandler(set_quality_cmd, filters.command("setquality") & filters.private))
    add_handler_to_client(client, MessageHandler(setgroup_cmd, filters.command("setgroup") & filters.private))
    add_handler_to_client(client, MessageHandler(list_topics_cmd, filters.command(["topics", "sync_topics"]) & filters.private))
    add_handler_to_client(client, MessageHandler(createtopics_cmd, filters.command("createtopics") & filters.private))
    add_handler_to_client(client, MessageHandler(setlog_cmd, filters.command("setlog") & filters.private))
    add_handler_to_client(client, MessageHandler(getlog_cmd, filters.command(["logs", "getlog"]) & filters.private))
    add_handler_to_client(client, MessageHandler(clearlogs_cmd, filters.command("clearlogs") & filters.private))
    add_handler_to_client(client, MessageHandler(stopall_cmd, filters.command(["stopall", "pauseall"]) & filters.private))
    add_handler_to_client(client, MessageHandler(resumeall_cmd, filters.command(["resumeall", "retryall"]) & filters.private))
    add_handler_to_client(client, MessageHandler(user_info_cmd, filters.command("user") & filters.private))
    add_handler_to_client(client, MessageHandler(job_info_cmd, filters.command("job") & filters.private))
    add_handler_to_client(client, MessageHandler(maintenance_on_cmd, filters.command("maintenance") & filters.private))
    add_handler_to_client(client, MessageHandler(maintenance_off_cmd, filters.command("maintenance_off") & filters.private))
    add_handler_to_client(client, MessageHandler(restart_cmd, filters.command("restart") & filters.private))

    # Auth Commands
    add_handler_to_client(client, MessageHandler(auth.add_user_cmd, filters.command("add") & filters.private))
    add_handler_to_client(client, MessageHandler(auth.renew_user_cmd, filters.command("renew") & filters.private))
    add_handler_to_client(client, MessageHandler(auth.remove_user_cmd, filters.command("remove") & filters.private))
    add_handler_to_client(client, MessageHandler(auth.ban_user_cmd, filters.command("ban") & filters.private))
    add_handler_to_client(client, MessageHandler(auth.unban_user_cmd, filters.command("unban") & filters.private))
    add_handler_to_client(client, MessageHandler(auth.list_users_cmd, filters.command("users") & filters.private))
    add_handler_to_client(client, MessageHandler(auth.my_plan_cmd, filters.command("plan") & filters.private))

    # Unauthorized & Direct Inputs
    add_handler_to_client(client, MessageHandler(unauthorized_handler, ~auth_filter & filters.private & filters.regex(r"^/") & ~filters.command(["start", "id", "help"])))
    add_handler_to_client(client, MessageHandler(direct_document_handler, filters.document & filters.private & auth_filter))
    add_handler_to_client(client, MessageHandler(direct_message_handler, filters.text & filters.private & ~filters.regex(r"^/") & auth_filter))

    # Callback Query Handlers
    add_handler_to_client(client, CallbackQueryHandler(admin_callbacks, filters.regex(r"^adm_")))
    add_handler_to_client(client, CallbackQueryHandler(menu_callbacks, filters.regex(r"^menu_")))
    add_handler_to_client(client, CallbackQueryHandler(result_file_callback, filters.regex(r"^res_(succ|fail)_")))
    add_handler_to_client(client, CallbackQueryHandler(retry_failed_callback, filters.regex(r"^retry_fail_")))
    add_handler_to_client(client, CallbackQueryHandler(youtube_quality_callback, filters.regex(r"^ytq:")))
    add_handler_to_client(client, CallbackQueryHandler(youtube_menu_callback, filters.regex(r"^ytmenu:")))
    add_handler_to_client(client, CallbackQueryHandler(youtube_cancel_callback, filters.regex(r"^ytcancel:")))
    add_handler_to_client(client, CallbackQueryHandler(input_cancel_callback, filters.regex(r"^input_cancel:")))
    add_handler_to_client(client, CallbackQueryHandler(input_retry_callback, filters.regex(r"^input_retry:")))
    add_handler_to_client(client, CallbackQueryHandler(features_callback, filters.regex("features")))
    add_handler_to_client(client, CallbackQueryHandler(details_callback, filters.regex("details")))
    add_handler_to_client(client, CallbackQueryHandler(back_to_start_callback, filters.regex("back_to_start")))



_CLIENTS_BY_ID: Dict[str, Client] = {}

def create_bot_client(config: Dict[str, Any]) -> Client:
    """Create and configure an independent Pyrogram Client instance."""
    bot_id = config.get("id") or f"bot_{config.get('index', 1)}"
    if bot_id in _CLIENTS_BY_ID:
        return _CLIENTS_BY_ID[bot_id]

    session_name = config.get("session_name") or f"coursewallah_bot_{config.get('index', 1)}"
    token = config.get("token") or BOT_TOKEN

    # Ensure sessions directory exists safely
    try:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
    except Exception:
        pass

    client = Client(
        name=session_name,
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=token,
        workdir=SESSIONS_DIR
    )
    ctx = BotContext(
        bot_id=bot_id,
        bot_name=config.get("name") or f"Bot {config.get('index', 1)}",
        session_name=session_name,
        client=client,
        job_manager=JobManager(bot_id=bot_id)
    )
    register_bot_context(client, ctx)
    register_all_handlers(client)
    _CLIENTS_BY_ID[bot_id] = client
    return client


_default_client: Optional[Client] = None

def get_client() -> Client:
    """Provide a default bot client instance for backwards compatibility."""
    global _default_client
    if _default_client is None:
        configured = get_configured_bots()
        cfg = configured[0] if configured else {
            "id": "bot_1",
            "name": BOT_NAME,
            "session_name": "coursewallah_bot_1",
            "token": BOT_TOKEN
        }
        _default_client = create_bot_client(cfg)
    return _default_client

bot = get_client()


# ==============================================================================
# 🚀 BOT STARTUP & LIFECYCLE MANAGEMENT
# ==============================================================================

async def setup_bot_commands(client: Client):
    """Register scoped bot commands for normal users vs administrators."""
    try:
        user_commands = [
            BotCommand("start", "Start the bot"),
            BotCommand("drm", "Download course/batch from TXT"),
            BotCommand("status", "Check active download status"),
            BotCommand("stop", "Pause current download"),
            BotCommand("resume", "Resume paused download"),
            BotCommand("cancel", "Cancel active download"),
            BotCommand("cookies", "Upload YouTube cookies"),
            BotCommand("getcookies", "Check cookies status"),
            BotCommand("deletecookies", "Delete YouTube cookies"),
            BotCommand("plan", "Check your subscription plan"),
            BotCommand("t2t", "Convert text to .txt file"),
            BotCommand("t2h", "Web HTML converter"),
            BotCommand("id", "Get your Telegram & Chat ID"),
            BotCommand("help", "View user manual")
        ]
        await client.set_bot_commands(user_commands, scope=BotCommandScopeDefault())

        admin_commands = user_commands + [
            BotCommand("admin", "Open admin control panel"),
            BotCommand("users", "List all subscribed users"),
            BotCommand("add", "Add a new subscriber"),
            BotCommand("remove", "Remove a subscriber"),
            BotCommand("renew", "Renew user subscription"),
            BotCommand("ban", "Ban a user"),
            BotCommand("unban", "Unban a user"),
            BotCommand("jobs", "View all active/recent jobs"),
            BotCommand("stopall", "Pause all running downloads"),
            BotCommand("resumeall", "Resume all unfinished downloads"),
            BotCommand("broadcast", "Broadcast message to all users"),
            BotCommand("stats", "System & download statistics"),
            BotCommand("system", "System diagnostics & metrics"),
            BotCommand("workers", "Concurrency and worker slots"),
            BotCommand("storage", "Storage breakdown"),
            BotCommand("cleanup", "Clean temp files & expired users"),
            BotCommand("setgroup", "Configure Telegram forum supergroup"),
            BotCommand("topics", "List persistent forum topics"),
            BotCommand("setwatermark", "Set video watermark text"),
            BotCommand("restart", "Restart bot safely")
        ]

        for admin_id in ADMINS:
            try:
                await client.set_bot_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
            except Exception:
                pass

        logger.info("[TELEGRAM] Bot command scopes registered successfully.")
    except Exception as e:
        logger.warning(f"[TELEGRAM] Could not register bot commands: {e}")


async def start_single_bot(bot_ctx: BotContext) -> bool:
    """Start or reconnect an individual bot client, handling session locks gracefully."""
    client = bot_ctx.client
    bot_name = bot_ctx.bot_name
    print(f"\n[{bot_name}] Connecting...")
    logger.info(f"[{bot_name}] Connecting session: {bot_ctx.session_name} (workdir: {getattr(client, 'workdir', SESSIONS_DIR)})")

    try:
        is_conn = (getattr(client, "is_connected", False) is True)
        is_init = (getattr(client, "is_initialized", False) is True)
        if not is_conn:
            if not is_init:
                await client.start()
            else:
                try:
                    await client.connect()
                except Exception:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    await client.start()

        me = await client.get_me()
        bot_ctx.bot_user_id = me.id
        bot_ctx.bot_username = me.username or ""
        bot_ctx.bot_first_name = me.first_name or ""
        bot_ctx.bot_last_name = me.last_name or ""
        full_name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        bot_ctx.bot_display_name = full_name or (f"@{me.username}" if me.username else bot_name)
        bot_ctx.bot_link = f"https://t.me/{me.username}" if me.username else ""
        bot_ctx.bot_is_bot = getattr(me, "is_bot", True)
        bot_ctx.is_online = True
        bot_ctx.error = None

        if me.username:
            print(f"[{bot_name}] Connected as @{me.username}")
            logger.info(f"[{bot_name}] Connected: @{me.username} (ID: {me.id})")
        else:
            print(f"[{bot_name}] Connected as {bot_ctx.bot_display_name} (ID: {me.id})")
            logger.info(f"[{bot_name}] Connected: {bot_ctx.bot_display_name} (ID: {me.id})")

        await setup_bot_commands(client)
        return True
    except sqlite3.OperationalError as e:
        err_msg = str(e)
        print(f"[{bot_name}] SQLite session error ({bot_ctx.session_name}): {err_msg}", file=sys.stderr)
        logger.error(f"[{bot_name}] SQLite session error: {bot_ctx.session_name} -> {err_msg}", exc_info=True)
        bot_ctx.error = f"Session database error: {err_msg}"
        bot_ctx.is_online = False
        return False
    except Exception as e:
        err_msg = str(e)
        print(f"[{bot_name}] Connection failed: {err_msg}", file=sys.stderr)
        logger.error(f"[{bot_name}] Connection failed: {err_msg}")
        bot_ctx.error = err_msg
        bot_ctx.is_online = False
        return False


async def bot_health_watchdog(bot_contexts: List[BotContext], interval_sec: float = 15.0):
    """
    Background health watchdog that continuously monitors each configured bot client.
    Automatically reconnects dropped sessions with bounded exponential backoff.
    Guarantees per-bot isolation so that one client reconnecting does not disrupt other bots.
    """
    backoff_delays = [1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
    bot_attempts: Dict[str, int] = {ctx.bot_id: 0 for ctx in bot_contexts}

    while True:
        try:
            await asyncio.sleep(interval_sec)
            for ctx in bot_contexts:
                client = ctx.client
                is_conn = getattr(client, "is_connected", False)
                if not is_conn:
                    ctx.is_online = False
                    attempt = bot_attempts.get(ctx.bot_id, 0)
                    delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                    logger.warning(
                        f"[WATCHDOG] Bot {ctx.bot_name} session disconnected (attempt {attempt + 1}). "
                        f"Attempting automatic reconnect in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                    try:
                        ok = await start_single_bot(ctx)
                        if ok:
                            bot_attempts[ctx.bot_id] = 0
                            logger.info(f"[WATCHDOG] Bot {ctx.bot_name} reconnected and restored ONLINE successfully.")
                        else:
                            bot_attempts[ctx.bot_id] = attempt + 1
                    except Exception as rec_err:
                        bot_attempts[ctx.bot_id] = attempt + 1
                        logger.error(f"[WATCHDOG] Failed to reconnect {ctx.bot_name}: {rec_err}")
                else:
                    bot_attempts[ctx.bot_id] = 0
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"[WATCHDOG] Loop exception: {e}")


def start_health_server(port: Optional[int] = None):
    """Optional background HTTP health check server for Render/Railway/Heroku web deployments."""
    if port is None:
        port_env = os.environ.get("PORT", "").strip().strip("'\"")
        if port_env and port_env.isdigit():
            port = int(port_env)
        elif WEB_SERVER:
            port = PORT or 8080
        else:
            return

    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/health", "/status", "/ping"):
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok","service":"Course Wallah Telegram Downloader Bot"}\n')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"Health Server: OK (Port {port})")
        logger.info(f"[HEALTH SERVER] Background HTTP health server listening on port {port}")
    except Exception as e:
        logger.warning(f"[HEALTH SERVER] Could not start health server on port {port}: {e}")


async def start_all_bots(configs: Optional[List[Dict[str, Any]]] = None):
    """Start all configured bot clients concurrently inside ONE Python process."""
    if configs is None:
        configs = get_configured_bots()

    # Start optional background HTTP health server if PORT or WEB_SERVER is configured
    start_health_server()

    py_ver = sys.version.split()[0]
    cpu_cnt = os.cpu_count() or 1

    # FFmpeg & FFprobe Verification
    ffmpeg_ok = False
    try:
        r_ff = subprocess.run([FFMPEG_PATH or "ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ffmpeg_ok = (r_ff.returncode == 0)
    except Exception:
        ffmpeg_ok = False

    ffprobe_ok = False
    try:
        r_fp = subprocess.run(["ffprobe", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ffprobe_ok = (r_fp.returncode == 0)
    except Exception:
        ffprobe_ok = False

    storage_root = str(storage.get_root_dir())
    storage_writable = storage.is_writable()
    is_persistent = storage.is_persistent_volume()

    print("==================================================")
    print("Course Wallah Downloader")
    print("==================================================")
    print(f"Python: {py_ver}")
    print(f"FFmpeg: {'OK' if ffmpeg_ok else 'NOT FOUND'}")
    print(f"FFprobe: {'OK' if ffprobe_ok else 'NOT FOUND'}")
    print(f"Storage: {storage_root}")
    print(f"Storage writable: {'YES' if storage_writable else 'NO'}")
    print(f"Persistent volume: {'detected' if is_persistent else 'not detected'}")
    print(f"Bots configured: {len(configs)}")

    storage_info = get_disk_storage_info(storage_root)
    print(f"Disk Free Space: {storage_info.get('free_human', 'Unknown')}")
    print(f"Temp Directory: {TEMP_DIR}")
    print(f"Sessions Directory: {SESSIONS_DIR}")

    # Safe stale temp cleanup on startup (preserving active checkpoints)
    stale_cleaned = cleanup_stale_temp_dirs()
    if stale_cleaned > 0:
        logger.info(f"Cleaned {stale_cleaned} stale temporary directories on startup.")

    import pyrogram
    print(f"Pyrogram: {getattr(pyrogram, '__version__', 'unknown')}")

    # yt-dlp version & executable resolution
    ytdlp_cmd = helper.get_ytdlp_cmd()
    try:
        r_yt = subprocess.run(list(ytdlp_cmd) + ["--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        yt_ver = r_yt.stdout.strip() if r_yt.returncode == 0 else "unknown"
    except Exception:
        yt_ver = "unknown"
    print(f"yt-dlp: {yt_ver}")

    # Credential Validation
    if not configs:
        msg = (
            "❌ Fatal Configuration Error: No valid Telegram bot tokens configured!\n"
            "Please configure BOT_TOKEN or BOT_1_TOKEN in environment variables / Railway Variables.\n"
        )
        print(msg, file=sys.stderr)
        logger.error(msg)
        if os.environ.get("PORT"):
            print("[INFO] Keeping container alive for HTTP health checks. Please set bot tokens in Railway dashboard.")
            while True:
                await asyncio.sleep(3600)
        return

    # 4. Resource Tier Detection & Worker Limits
    if cpu_cnt <= 1:
        res_tier = "Low Resource Instance (1 Core)"
        rec_down, rec_up = 2, 2
    elif cpu_cnt <= 2:
        res_tier = "Standard Resource Instance (2 Cores)"
        rec_down, rec_up = 3, 2
    else:
        res_tier = "High Performance Instance (4+ Cores)"
        rec_down, rec_up = 4, 3

    print(f"Platform Tier: {res_tier}")
    print(f"Workers: Configured (Download={DOWNLOAD_WORKERS}, Upload={UPLOAD_WORKERS}) | Recommended (Download={rec_down}, Upload={rec_up})")
    print(f"Watermark: READY (Text: {WATERMARK_TEXT})")

    # 5. Initialize bot clients
    bot_contexts: List[BotContext] = []
    for cfg in configs:
        client = create_bot_client(cfg)
        ctx = get_bot_context(client)
        if ctx:
            bot_contexts.append(ctx)

    # 6. Start all bot clients
    for ctx in bot_contexts:
        print(f"Starting {ctx.bot_name}...")
        await start_single_bot(ctx)

    online_contexts = [ctx for ctx in bot_contexts if ctx.is_online]

    print("\n" + "=" * 60)
    print("BOT STATUS")
    print("=" * 60)
    for ctx in bot_contexts:
        if ctx.is_online:
            disp = f"@{ctx.bot_username}" if ctx.bot_username else ctx.bot_display_name
            print(f"🟢 {ctx.bot_name} ({disp}) — ONLINE")
        else:
            reason = f" ({ctx.error})" if ctx.error else ""
            print(f"🔴 {ctx.bot_name} — FAILED{reason}")

    if not online_contexts:
        print("\n❌ No bot instances could be started.", file=sys.stderr)
        logger.error("No bot instances online.")
        if os.environ.get("PORT"):
            print("[INFO] Keeping container alive for HTTP health checks. Check credentials and Railway logs.")
            while True:
                await asyncio.sleep(3600)
        return

    # 7. Proactive Forum Group Validation on first online client
    primary_client = online_contexts[0].client
    configured_fg = FORUM_CHAT_ID or db.get_forum_group()
    if configured_fg:
        try:
            fg_id = int(configured_fg)
            valid, f_msg, details = await validate_forum_chat(primary_client, fg_id)
            if valid:
                print(f"[FORUM] Target Destination: {details.get('title')} ({fg_id}) | Forum: {'YES' if details.get('is_forum') else 'NO'}")
                logger.info(f"[FORUM] Validated destination {fg_id}: {details.get('title')}")
            else:
                print(f"⚠️ [FORUM WARNING] Configured forum chat {fg_id} is inaccessible: {f_msg}")
                logger.warning(f"[FORUM WARNING] Configured forum chat {fg_id} is inaccessible: {f_msg}")
        except ValueError:
            print(f"⚠️ [FORUM WARNING] Invalid FORUM_CHAT_ID value: {configured_fg}")

    # 8. Automatic Crash Recovery & Restart Resumption across online clients
    online_clients_map = {ctx.bot_id: ctx.client for ctx in online_contexts}
    print("Checking for interrupted jobs to recover...")
    recovered = await job_manager.recover_on_startup(online_clients_map, execute_job_pipeline)
    if recovered > 0:
        print(f"♻️ Successfully recovered and resumed {recovered} job(s) from previous session!")

    # 9. Send official Online & Ready announcement if BOT_STATUS_CHAT_ID is configured
    if BOT_STATUS_CHAT_ID:
        primary_ctx = online_contexts[0]
        try:
            target_chat = int(BOT_STATUS_CHAT_ID) if BOT_STATUS_CHAT_ID.lstrip("-").isdigit() else BOT_STATUS_CHAT_ID
            note_text, note_markup = format_bot_online_card(
                bot_name=primary_ctx.bot_display_name,
                bot_username=primary_ctx.bot_username,
                active_bots=len(online_contexts),
                total_bots=len(bot_contexts),
                recovered_jobs=recovered
            )
            await primary_ctx.client.send_message(
                chat_id=target_chat,
                text=note_text,
                reply_markup=note_markup,
                parse_mode=enums.ParseMode.HTML
            )
            logger.info(f"[STARTUP NOTIFY] Sent startup online card to status chat {BOT_STATUS_CHAT_ID}")
        except Exception as notify_err:
            logger.warning(f"[STARTUP NOTIFY] Could not send startup notification to {BOT_STATUS_CHAT_ID}: {notify_err}")

    # 10. Start background health watchdog
    watchdog_task = asyncio.create_task(bot_health_watchdog(bot_contexts))

    print("=" * 60)
    print(f"✅ COURSE WALLAH IS ONLINE AND READY ({len(online_contexts)}/{len(bot_contexts)} bots active)", flush=True)
    print("=" * 60, flush=True)
    logger.info(f"✅ COURSE WALLAH IS ONLINE AND READY ({len(online_contexts)}/{len(bot_contexts)} bots active)")

    # 11. Idle until shutdown signal
    try:
        await idle()
    finally:
        print("\n" + "=" * 60)
        print("🛑 Shutting down bot clients...")
        print("=" * 60)
        if watchdog_task and not watchdog_task.done():
            watchdog_task.cancel()
        for ctx in online_contexts:
            print(f"Stopping {ctx.bot_name}...")
            logger.info(f"Stopping {ctx.bot_name} cleanly...")
            try:
                await ctx.client.stop()
                print(f"✅ {ctx.bot_name} stopped cleanly.")
            except Exception as stop_err:
                print(f"⚠️ {ctx.bot_name} stop error: {stop_err}")


async def start_main():
    await start_all_bots()


def start_bot():
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(start_main())
    except (KeyboardInterrupt, SystemExit):
        print("\nBot process exited cleanly.")
    except Exception as e:
        print(f"[Fatal Startup Error] {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    start_bot()

