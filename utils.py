import os
import re
import time
import math
import asyncio
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional, Dict, Any, Union, List, Tuple, Set
import shutil
import logging
import tempfile
import requests
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, MessageNotModified
from vars import CREDIT, PROJECT_ROOT, DATA_DIR, TEMP_DIR, DOWNLOADS_DIR

from urllib.parse import urlparse, unquote

logger = logging.getLogger(__name__)

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class MediaType(str, Enum):
    DIRECT_IMAGE = "DIRECT_IMAGE"
    DIRECT_PDF = "DIRECT_PDF"
    KGS_HLS = "KGS_HLS"
    SPAYEE_HLS = "SPAYEE_HLS"
    GO_CLASSES = "GO_CLASSES"
    YOUTUBE = "YOUTUBE"
    ENCRYPTED_STREAM = "ENCRYPTED_STREAM"
    DIRECT_M3U8 = "DIRECT_M3U8"
    APPX_LECTURE = "APPX_LECTURE"
    DIRECT_VIDEO = "DIRECT_VIDEO"
    UNKNOWN = "UNKNOWN"


class PDFError(Exception):
    """Base exception for PDF operations."""
    pass


class PDFPasswordInvalid(PDFError):
    """PDF password is invalid or missing when required."""
    pass


class PDFUnlockFailed(PDFError):
    """PDF unlocking failed."""
    pass


class PDFUnlockOutputInvalid(PDFError):
    """Unlocked PDF output failed validation."""
    pass


class PDFDownloadFailed(PDFError):
    """PDF download failed."""
    pass


class PDFInvalid(PDFError):
    """PDF format or structure is invalid / corrupted."""
    pass


PDF_PASSWORD_INVALID = "PDF_PASSWORD_INVALID"
PDF_UNLOCK_FAILED = "PDF_UNLOCK_FAILED"
PDF_UNLOCK_OUTPUT_INVALID = "PDF_UNLOCK_OUTPUT_INVALID"
PDF_DOWNLOAD_FAILED = "PDF_DOWNLOAD_FAILED"
PDF_INVALID = "PDF_INVALID"


def parse_pdf_input(value: Optional[str]) -> Dict[str, Any]:
    """
    Parse a PDF resource input that may contain an authorized password.
    Format: PDF_URL*PASSWORD
    Strictly splits only on the first '*'. Everything after belongs to password.
    Returns:
        {
            "url": "...",
            "password": "...",
            "has_password": True/False
        }
    """
    if not value or not isinstance(value, str):
        return {
            "url": "",
            "password": None,
            "has_password": False
        }

    clean = value.strip()
    if not clean:
        return {
            "url": "",
            "password": None,
            "has_password": False
        }

    if "*" in clean:
        parts = clean.split("*", 1)
        url_part = parts[0].strip()
        pwd_part = parts[1].strip() if len(parts) > 1 else ""
        return {
            "url": url_part,
            "password": pwd_part if pwd_part else None,
            "has_password": bool(pwd_part)
        }

    return {
        "url": clean,
        "password": None,
        "has_password": False
    }


def is_direct_image_url(url: str, category_hint: Optional[str] = None) -> bool:
    """Detects whether a given URL is a direct image resource (.jpg, .jpeg, .png, .webp, etc.)."""
    if not url or not isinstance(url, str):
        return False
    clean = url.strip()
    if not clean:
        return False
    if category_hint and str(category_hint).strip().lower() in ("image", "photo", "img", "thumb", "thumbnail"):
        return True

    base_url = clean.split("*")[0].strip()
    parsed = urlparse(base_url)
    path = (parsed.path or "").strip()
    path_lower = path.lower().rstrip('/')

    img_exts = (r'\.jpg', r'\.jpeg', r'\.png', r'\.webp', r'\.gif', r'\.bmp', r'\.svg')
    ext_pattern = rf"({'|'.join(img_exts)})($|[?&#/])"
    if re.search(ext_pattern, clean, re.IGNORECASE):
        return True

    query_lower = (parsed.query or "").lower()
    if any(e in query_lower for e in (".jpg", ".jpeg", ".png", ".webp")) or "format=jpg" in query_lower or "format=png" in query_lower or "type=image" in query_lower:
        return True

    return False


def is_spayee_url(url: str) -> bool:
    """Detects whether a given URL is a Spayee HLS stream resource (spayee.in, qcdn.spayee.in, vcdn.spayee.in, media.spayee.com, spees.in)."""
    if not url or not isinstance(url, str):
        return False
    clean = url.strip()
    if not clean:
        return False
    base_url = clean.split("*")[0].strip()
    parsed = urlparse(base_url)
    host = (parsed.hostname or parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    return (
        any(d in host for d in ("spayee.in", "spayee.com", "spees.in", "spees", "spayee"))
        or "spayee" in host
        or "spayee" in path
        or "spees" in host
        or "spees" in path
    )


def is_goclasses_url(url: str) -> bool:
    """Detects whether a given URL is a Go Classes / GateOverflow stream or video resource."""
    if not url or not isinstance(url, str):
        return False
    clean = url.strip()
    if not clean:
        return False
    base_url = clean.split("*")[0].strip()
    parsed = urlparse(base_url)
    host = (parsed.hostname or parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    return (
        any(d in host for d in ("goclasses.in", "goclasses", "gateoverflow.in", "gateoverflow"))
        or "goclasses" in host
        or "goclasses" in path
        or "gateoverflow" in host
        or "gateoverflow" in path
    )


def is_kgs_url(url: str) -> bool:
    """
    Detects whether a given URL is a KGS (Khan Global Studies / Akamai HLS) resource.
    Inspects hostname, domain, and path for 'kgs', 'khanglobalstudies', or KGS CDN patterns.
    Does NOT hardcode specific video IDs, courses, or tokens.
    """
    if not url or not isinstance(url, str):
        return False
    clean = url.strip()
    if not clean:
        return False
    try:
        from kgs_downloader import is_kgs_url as _kgs_check
        return _kgs_check(clean)
    except Exception:
        parsed = urlparse(clean)
        netloc_lower = (parsed.netloc or "").lower()
        path_lower = (parsed.path or "").lower()
        if any(h in netloc_lower for h in ("kgs", "khanglobalstudies", "kgs-new-v1", "kgs-video")):
            return True
        if "akamaized.net" in netloc_lower and any(p in path_lower for p in ("/kv3/", "/kgs/", "/kv2/")):
            return True
        if "/kgs/" in path_lower or "kgs" in path_lower.split("/"):
            return True
        return False


def is_youtube_url(url: str) -> bool:
    """
    Detects whether a given URL is strictly a YouTube video/playlist/short/live.
    Uses strict domain and hostname parsing without loose substring checks.
    """
    if not url or not isinstance(url, str):
        return False
    clean = url.strip().split("*")[0].strip()
    if not clean:
        return False
    try:
        parsed = urlparse(clean)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False
        
        strict_yt_hosts = {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "youtu.be",
            "youtube-nocookie.com",
            "www.youtube-nocookie.com",
            "music.youtube.com"
        }
        if hostname in strict_yt_hosts:
            return True
        if hostname.endswith(".youtube.com") or hostname == "youtu.be":
            return True
        return False
    except Exception:
        return False


def is_encrypted_stream_url(url: str) -> bool:
    """Detects whether a given URL is an encrypted AES stream (dragoapi, encrypted.m, or URL containing * AES key)."""
    if not url or not isinstance(url, str):
        return False
    clean = url.strip()
    lower = clean.lower()
    if is_spayee_url(clean) or is_goclasses_url(clean):
        return False
    if is_direct_pdf_url(clean):
        return False
    return "dragoapi.vercel.app" in lower or "encrypted.m" in lower or "*" in clean


def is_direct_pdf_url(url: str, category_hint: Optional[str] = None) -> bool:
    """
    Detects whether a given URL is a direct PDF document resource.
    Supports query parameters, signed tokens, uppercase .PDF, and CDN routing paths
    without relying solely on exact trailing extensions or specific whitelisted domains.
    Supports password-protected PDF URLs (PDF_URL*PASSWORD).
    """
    if not url or not isinstance(url, str):
        return False
    clean = url.strip()
    if not clean:
        return False

    if category_hint and str(category_hint).strip().lower() in ("pdf", "document", "doc", "notes"):
        return True

    parsed_info = parse_pdf_input(clean)
    base_url = parsed_info["url"]
    if not base_url:
        return False

    parsed = urlparse(base_url)
    path = (parsed.path or "").strip()
    path_lower = path.lower().rstrip('/')

    # Check path extension (.pdf, .PDF)
    if path_lower.endswith(".pdf"):
        return True

    # Regex check: .pdf followed by end-of-string, query param (?), hash (#), or slash (/)
    if re.search(r'\.pdf($|[?&#/])', base_url, re.IGNORECASE):
        return True

    # Query string indicators (e.g. ?file=doc.pdf or format=pdf)
    query_lower = (parsed.query or "").lower()
    if ".pdf" in query_lower or "type=pdf" in query_lower or "format=pdf" in query_lower or "mime=application/pdf" in query_lower:
        return True

    return False


def is_appx_url(url: str) -> bool:
    """Detects whether a given URL is an APPX / Classplus / ClassX / authorized lecture API endpoint."""
    if not url or not isinstance(url, str):
        return False
    clean = url.strip()
    lower = clean.lower()
    return any(k in lower for k in (
        "fetch_video", "/fetch_video", "get_video", "/get_video",
        "classx.co.in", "appx.co.in", "classplusapp.com", "testpress.in",
        "akamai.net.in", "penpencil"
    ))


def is_hls_url(url: str) -> bool:
    """
    Detects whether a given URL is an HLS (M3U8) stream playlist.
    Supports master playlists, media playlists, CloudFront URLs, signed parameters,
    and relative/absolute .ts segments. Excludes APPX API endpoints.
    """
    if not url or not isinstance(url, str):
        return False
    clean = url.strip()
    if not clean:
        return False

    if is_appx_url(clean):
        return False

    base_url = clean.split("*")[0].strip()
    parsed = urlparse(base_url)
    path = (parsed.path or "").strip()
    path_lower = path.lower().rstrip('/')

    # Path extension (.m3u8, .M3U8)
    if path_lower.endswith(".m3u8"):
        return True

    # Regex check: .m3u8 followed by end-of-string, query param (?), hash (#), or slash (/)
    if re.search(r'\.m3u8($|[?&#/])', clean, re.IGNORECASE):
        return True

    # Common HLS path keywords
    if "m3u8" in path_lower or "/hls/" in path_lower or "/vod/" in path_lower or "channel_vod_non_drm_hls" in path_lower:
        return True

    query_lower = (parsed.query or "").lower()
    if ".m3u8" in query_lower:
        return True

    return False

is_direct_m3u8_url = is_hls_url


def is_direct_video_url(url: str) -> bool:
    """Detects whether a given URL is a direct video file container (.mp4, .mkv, .webm, .avi, etc.)."""
    if not url or not isinstance(url, str):
        return False
    clean = url.strip()
    if not clean:
        return False

    parsed = urlparse(clean.split("*")[0].strip())
    path_lower = (parsed.path or "").lower().rstrip('/')
    video_exts = (r'\.mp4', r'\.mkv', r'\.webm', r'\.avi', r'\.mov', r'\.ts', r'\.flv', r'\.m4v', r'\.3gp')
    ext_pattern = rf"({'|'.join(video_exts)})($|[?&#])"
    if re.search(ext_pattern, clean, re.IGNORECASE):
        return True
    return False


def resolve_redirect_url(url: str, timeout: int = 5) -> str:
    """
    Safely resolves referral / redirect / shortlink URLs to their canonical destination.
    Preserves signed URL parameters and authorized keys (*KEY).
    """
    if not url or not isinstance(url, str):
        return url
    clean = url.strip()
    if not clean:
        return url

    key_part = None
    target_url = clean
    if "*" in clean:
        parts = clean.split("*", 1)
        target_url = parts[0].strip()
        key_part = parts[1].strip() if len(parts) > 1 else None

    try:
        parsed = urlparse(target_url)
        host = (parsed.hostname or "").lower()
        shortener_hosts = {
            "bit.ly", "tinyurl.com", "t.co", "goo.gl", "rotf.lol",
            "shorturl.at", "ow.ly", "buff.ly", "is.gd", "cutt.ly"
        }
        if host in shortener_hosts or "/r/" in parsed.path or "/referral/" in parsed.path:
            import requests
            resp = requests.head(target_url, allow_redirects=True, timeout=timeout)
            resolved = resp.url
            if resolved and resolved != target_url:
                return f"{resolved}*{key_part}" if key_part else resolved
    except Exception:
        pass

    return clean


class MediaRouter:
    """
    Independent media URL classifier and router.
    Accurately classifies input URLs before download so each media type
    is routed to its proper specialized pipeline without cross-contamination.

    Priority Order:
    1. DIRECT_IMAGE
    2. DIRECT_PDF
    3. APPX_LECTURE (Authorized endpoints)
    4. KGS_HLS (Khan Global Studies)
    5. SPAYEE_HLS (Spayee CDN)
    6. GO_CLASSES (Go Classes / GateOverflow)
    7. ENCRYPTED_STREAM (AES streams)
    8. DIRECT_M3U8 / HLS
    9. DIRECT_VIDEO (mp4, mkv, webm, etc.)
    10. YOUTUBE (Strict youtube.com, youtu.be)
    11. UNKNOWN (Unsupported source)
    """
    is_direct_image_url = staticmethod(is_direct_image_url)
    is_direct_pdf_url = staticmethod(is_direct_pdf_url)
    is_kgs_url = staticmethod(is_kgs_url)
    is_spayee_url = staticmethod(is_spayee_url)
    is_goclasses_url = staticmethod(is_goclasses_url)
    is_youtube_url = staticmethod(is_youtube_url)
    is_encrypted_stream_url = staticmethod(is_encrypted_stream_url)
    is_hls_url = staticmethod(is_hls_url)
    is_direct_m3u8_url = staticmethod(is_direct_m3u8_url)
    is_appx_url = staticmethod(is_appx_url)
    is_direct_video_url = staticmethod(is_direct_video_url)
    resolve_redirect_url = staticmethod(resolve_redirect_url)

    @staticmethod
    def classify_url(url: str, category_hint: Optional[str] = None) -> MediaType:
        if not url or not isinstance(url, str):
            return MediaType.UNKNOWN

        clean = url.strip()
        if not clean:
            return MediaType.UNKNOWN

        # 1. DIRECT_IMAGE (jpg, jpeg, png, webp, gif, bmp, svg)
        if is_direct_image_url(clean, category_hint=category_hint):
            return MediaType.DIRECT_IMAGE

        # 2. DIRECT_PDF (CRWill CDN, CloudFront, Google Drive direct, signed PDFs, uppercase .PDF, KGS PDF)
        if is_direct_pdf_url(clean, category_hint=category_hint):
            return MediaType.DIRECT_PDF

        # 3. APPX / CLASSPLUS (ClassX / Classplus / Authorized Lecture API URLs)
        if is_appx_url(clean):
            return MediaType.APPX_LECTURE

        # 4. KGS_HLS (Khan Global Studies / Akamaized signed HLS)
        if is_kgs_url(clean):
            return MediaType.KGS_HLS

        # 5. SPAYEE_HLS (qcdn.spayee.in, vcdn.spayee.in, spayee.in, spees)
        if is_spayee_url(clean):
            return MediaType.SPAYEE_HLS

        # 6. GO_CLASSES (goclasses.in, gateoverflow.in)
        if is_goclasses_url(clean):
            return MediaType.GO_CLASSES

        # 7. ENCRYPTED_STREAM (dragoapi, encrypted.m, etc. - excluding Spayee/GoClasses/PDF)
        if is_encrypted_stream_url(clean):
            return MediaType.ENCRYPTED_STREAM

        # 8. DIRECT_M3U8 / HLS (CloudFront HLS, Akamai, generic HLS)
        if is_hls_url(clean):
            return MediaType.DIRECT_M3U8

        # 9. DIRECT_VIDEO (.mp4, .mkv, .webm, .avi, .mov, .ts, .flv)
        if is_direct_video_url(clean):
            return MediaType.DIRECT_VIDEO

        # 10. YOUTUBE (Strict youtube.com, youtu.be, youtube-nocookie.com)
        if is_youtube_url(clean):
            return MediaType.YOUTUBE

        # 11. Check if path/URL explicitly indicates APPX lecture endpoint
        lower = clean.lower()
        if "/lecture/" in lower or "/api/" in lower or "/fetch_video" in lower:
            return MediaType.APPX_LECTURE

        # 12. Check if clean URL contains common video extensions in query/path
        if re.search(r'\.(mp4|mkv|webm|avi|mov|ts|flv)($|[?&#])', clean, re.I):
            return MediaType.DIRECT_VIDEO

        # If not safely identified as any supported source, return UNKNOWN (never generic fallback to YouTube)
        return MediaType.UNKNOWN

    @staticmethod
    def extract_clean_title(url: str, default_title: Optional[str] = None) -> str:
        """Extract a clean, safe filename/title from URL without exposing tokens or query parameters."""
        if default_title and default_title.strip() and default_title.strip().lower() not in (
            "video", "output", "download", "stream", "default title", "master", "playlist", "index", "file", "document", "notes"
        ):
            return default_title.strip()

        parsed = urlparse(url)
        path_parts = [p for p in (parsed.path or "").split("/") if p]
        for candidate in reversed(path_parts):
            cand_unquoted = unquote(candidate)
            stem = Path(cand_unquoted).stem
            if stem and stem.lower() not in ("master", "index", "playlist", "video", "output", "download", "stream", "file", "doc", "document"):
                return stem

        stem = Path(unquote(parsed.path or "")).stem
        if stem and stem.lower() not in ("master", "index", "playlist", "video", "output", "download", "stream", "file"):
            return stem

        return f"Media_{int(time.time())}"


def hrb(value: Optional[float | int], digits: int = 2, delim: str = " ", postfix: str = "") -> str:
    """Formats bytes to human readable string (KB, MB, GB, TB)."""
    if value is None:
        return "0 B"
    try:
        val = float(value)
    except (ValueError, TypeError):
        return "0 B"

    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(val) < 1024.0:
            return f"{val:.{digits}f}{delim}{unit}{postfix}" if unit != "B" else f"{int(val)}{delim}{unit}{postfix}"
        val /= 1024.0
    return f"{val:.{digits}f}{delim}PB{postfix}"


def hrt(seconds: Optional[float | int], precision: int = 2) -> str:
    """Formats seconds to human readable time string (e.g. 02m 45s, 01h 12m)."""
    if seconds is None or seconds < 0:
        return "00s"
    try:
        sec = int(seconds)
    except (ValueError, TypeError):
        return "00s"

    days = sec // 86400
    hours = (sec % 86400) // 3600
    minutes = (sec % 3600) // 60
    rem_sec = sec % 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes:02d}m")
    if rem_sec > 0 or not parts:
        parts.append(f"{rem_sec:02d}s")

    return " ".join(parts[:precision])


def make_progress_bar(percent: float, bar_length: int = 14) -> str:
    """Generates clean ASCII progress bar with filled and empty characters."""
    pct = max(0.0, min(100.0, float(percent)))
    filled = int(round(bar_length * (pct / 100.0)))
    unfilled = bar_length - filled
    return "█" * filled + "░" * unfilled


class JobProgressTracker:
    """
    Per-job progress tracking instance with real speed computation,
    throttling, animated frames, and FloodWait protection.
    """

    def __init__(
        self,
        job_id: str,
        course_name: str = "Download",
        total_items: int = 1,
        update_interval: float = 1.8
    ):
        self.job_id = job_id
        self.course_name = course_name[:35]
        self.total_items = max(1, total_items)
        self.current_item_index = 1
        self.current_item_title = "Starting..."
        self.phase = "Downloading"

        self.start_time = time.time()
        self.last_update_time = 0.0
        self.last_bytes = 0
        self.current_speed = 0.0
        self.spinner_idx = 0
        self.update_interval = update_interval

    def set_item(self, index: int, title: str, phase: str = "Downloading"):
        self.current_item_index = index
        self.current_item_title = title[:40]
        self.phase = phase
        self.start_time = time.time()
        self.last_bytes = 0
        self.current_speed = 0.0

    def compute_speed_and_eta(self, current_bytes: int, total_bytes: int) -> tuple[float, float, float]:
        now = time.time()
        elapsed = now - self.start_time
        if elapsed < 0.5:
            return 0.0, 0.0, 0.0

        # Calculate actual real speed
        real_speed = current_bytes / elapsed if elapsed > 0 else 0.0
        self.current_speed = real_speed

        percent = (current_bytes / total_bytes * 100.0) if total_bytes > 0 else 0.0
        eta = ((total_bytes - current_bytes) / real_speed) if (real_speed > 0 and total_bytes > 0) else 0.0
        return real_speed, percent, eta

    def format_box(
        self,
        current_bytes: int,
        total_bytes: int,
        speed: float,
        percent: float,
        eta: float,
        phase_override: str = None
    ) -> str:
        phase_str = phase_override or self.phase
        self.spinner_idx += 1
        dots = get_dot_animation(self.spinner_idx)
        bullets = get_bullet_animation(self.spinner_idx)

        # Internal technical logging only
        logger.debug(f"[TRACKER] {self.job_id} | {self.current_item_title}: {percent:.1f}% ({current_bytes}/{total_bytes} bytes, {speed:.1f} B/s, ETA {eta:.1f}s)")

        bar = make_progress_bar(percent)
        return (
            "╭────────────────────────────╮\n"
            "│       🎓 <b>COURSE WALLAH</b>     │\n"
            "│                            │\n"
            f"│    🔄 <b>{phase_str.upper()} {dots}</b>    │\n"
            "│                            │\n"
            f"│ 📚 <b>{self.course_name[:35]}</b>\n"
            f"│ 🎬 <b>{self.current_item_index}/{self.total_items}: {self.current_item_title[:35]}</b>\n"
            f"│ <code>{bar}</code> <b>{percent:5.1f}%</b>\n"
            f"│ 🆔 <code>{self.job_id}</code>\n"
            "│                            │\n"
            f"│        {bullets}           │\n"
            f"│   <i>Processing item {self.current_item_index} of {self.total_items}</i>   │\n"
            "╰────────────────────────────╯"
        )

    async def update_message(
        self,
        message,
        current_bytes: int,
        total_bytes: int,
        force: bool = False,
        phase_override: str = None
    ) -> bool:
        if not message:
            return False

        now = time.time()
        if not force and (now - self.last_update_time < self.update_interval):
            return False

        speed, percent, eta = self.compute_speed_and_eta(current_bytes, total_bytes)
        text = self.format_box(current_bytes, total_bytes, speed, percent, eta, phase_override=phase_override)

        try:
            await message.edit_text(text)
            self.last_update_time = now
            return True
        except FloodWait as fw:
            await asyncio.sleep(fw.value if hasattr(fw, "value") else getattr(fw, "x", 5))
            return False
        except MessageNotModified:
            return False
        except Exception:
            return False


# Legacy support helper
async def progress_bar(current, total, reply, start_time, name="{VIDEO}", watermark="{CREDIT}"):
    """
    Backwards-compatible progress callback used by pyrogram send_video / send_document.
    Non-blocking, logs technical speeds and bytes internally, while providing a clean
    animated Telegram status card without raw technical speed/bytes.
    """
    if not reply or total <= 0:
        return

    now = time.time()
    # Check last edit timestamp stored on reply message object
    last_edit = getattr(reply, "_last_edit_ts", 0.0)
    if isinstance(last_edit, (int, float)) and (now - float(last_edit) < 1.8):
        return

    elapsed = now - start_time
    if elapsed < 0.5:
        return

    speed = current / elapsed if elapsed > 0 else 0.0
    percent = (current / total) * 100.0

    # Internal technical logging only
    logger.debug(f"[UPLOAD PROGRESS] {str(name)[:30]}: {percent:.1f}% ({current/(1024*1024):.1f}/{total/(1024*1024):.1f} MB, {speed/(1024*1024):.2f} MB/s)")

    frame = int(elapsed / 1.5)
    dots = get_dot_animation(frame)
    bullets = get_bullet_animation(frame)
    clean_name = str(name)[:45]

    part_match = re.search(r"\(Part\s+(\d+)\)", clean_name, re.IGNORECASE)
    if part_match:
        hdr = f"📤 <b>UPLOADING PART {part_match.group(1)} {dots}</b>"
        sub_text = f"Uploading Part {part_match.group(1)}..."
    else:
        hdr = f"📤 <b>UPLOADING VIDEO {dots}</b>"
        sub_text = "Uploading to Telegram..."

    bar = make_progress_bar(percent)
    msg = (
        "╭────────────────────────────╮\n"
        "│       🎓 <b>COURSE WALLAH</b>     │\n"
        "│                            │\n"
        f"│    {hdr}    │\n"
        "│                            │\n"
        f"│ 🎬 <b>{clean_name}</b>\n"
        f"│ <code>{bar}</code> <b>{percent:5.1f}%</b>\n"
        "│                            │\n"
        f"│        {bullets}           │\n"
        f"│   <i>{sub_text}</i>   │\n"
        "╰────────────────────────────╯"
    )

    try:
        reply._last_edit_ts = now
        await reply.edit_text(msg)
    except FloodWait as e:
        await asyncio.sleep(e.value if hasattr(e, "value") else getattr(e, "x", 5))
    except (MessageNotModified, Exception):
        pass


def sanitize_error_message(error: Optional[str]) -> str:
    """Sanitizes technical errors to avoid exposing bot tokens, authorization headers, or signed tokens."""
    if not error:
        return "Unknown error"
    err = str(error)
    # Strip URL tokens / query params
    err = re.sub(r"([?&][a-zA-Z0-9_\-]+)=[^&\s]+", r"\1=[REDACTED]", err)
    # Strip sensitive key-values (token=, key=, secret=, etc.)
    err = re.sub(r"(?i)\b(token|key|secret|auth|signature|sig|password|pass|cookie)=([^\s&]+)", r"\1=[REDACTED]", err)
    # Strip Telegram bot tokens
    err = re.sub(r"\d{8,12}:[a-zA-Z0-9_-]{35}", "[REDACTED_BOT_TOKEN]", err)
    # Strip bearer tokens
    err = re.sub(r"Bearer\s+[a-zA-Z0-9_\-\.]+", "Bearer [REDACTED]", err, flags=re.IGNORECASE)
    # Truncate if excessively long
    return err[:300]


def build_failure_card(
    resource_type: str,
    lecture_title: str,
    course_name: str,
    subject_name: Optional[str] = None,
    unit_title: Optional[str] = None,
    lecture_index: Optional[int] = None,
    reason: str = "Media could not be downloaded",
    technical_error: Optional[str] = None,
    video_status: str = "NOT_AVAILABLE",
    pdf_status: str = "NOT_AVAILABLE",
    credit: str = CREDIT
) -> str:
    """Builds a clean Course Wallah designed failure report card without exposing technical errors."""
    def get_status_str(status: str) -> str:
        if status == "SUCCESS":
            return "✅ Success"
        elif status == "FAILED":
            if technical_error:
                err_str = str(technical_error)
                if "PDF_PASSWORD_INVALID" in err_str or "Invalid PDF password" in err_str or ("password" in err_str.lower() and "pdf" in err_str.lower()):
                    return "❌ Invalid PDF password"
                if "403" in err_str or "rejected or expired" in err_str:
                    return "❌ Authorization rejected or expired"
            return "❌ Download failed"
        elif status in ("PDF_PASSWORD_INVALID", "PASSWORD_INVALID"):
            return "❌ Invalid PDF password"
        elif status == "CANCELLED":
            return "⏹ Cancelled"
        else:
            return "⚪ Not Available"

    v_icon = get_status_str(video_status)
    p_icon = get_status_str(pdf_status)

    idx_str = f" {lecture_index}" if lecture_index else ""

    lines = [
        f"❌ <b>{resource_type.upper()} DOWNLOAD FAILED</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🎬 <b>Lecture:</b> {lecture_title}{idx_str}",
        f"📚 <b>Course:</b> {course_name}",
    ]
    if subject_name and subject_name != "General":
        lines.append(f"📝 <b>Subject:</b> {subject_name}")
    if unit_title and unit_title != "General":
        lines.append(f"📌 <b>Unit:</b> {unit_title}")

    lines.extend([
        "",
        f"🎬 <b>Video:</b> {v_icon}",
        f"📄 <b>PDF:</b> {p_icon}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🤖 <b>{credit or 'Course Wallah'}</b>"
    ])
    return "\n".join(lines)


def get_bullet_animation(frame: int = 0) -> str:
    """Generates a looping 5-bullet smooth animation."""
    bullets = ["○", "○", "○", "○", "○"]
    idx = frame % len(bullets)
    bullets[idx] = "◉"
    return " ".join(bullets)


def get_dot_animation(frame: int = 0) -> str:
    """Generates cycling dots (·, ··, ···)."""
    count = (frame % 3) + 1
    return "·" * count


def format_download_card(
    course_name: str,
    title: str,
    unit_title: Optional[str] = None,
    subject_name: Optional[str] = None,
    frame: int = 0
) -> str:
    """
    Builds a premium animated Course Wallah download card.
    Strictly avoids exposing raw technical speeds, bytes, or FFmpeg statistics to Telegram users.
    """
    dots = get_dot_animation(frame)
    bullets = get_bullet_animation(frame)
    clean_title = (title or "Lecture")[:45]
    clean_course = (course_name or "Course Wallah")[:35]

    unit_line = f"│ 📌 {unit_title[:35]}\n" if unit_title and unit_title != "General" else ""
    subj_line = f"│ 📝 {subject_name[:35]}\n" if subject_name and subject_name != "General" else ""

    return (
        "╭────────────────────────────╮\n"
        "│       🎓 <b>COURSE WALLAH</b>     │\n"
        "│                            │\n"
        f"│      ⬇️ <b>DOWNLOADING {dots}</b>      │\n"
        "│                            │\n"
        f"│ 🎬 <b>{clean_title}</b>\n"
        f"│ 📚 {clean_course}\n"
        f"{subj_line}{unit_line}"
        "│                            │\n"
        f"│        {bullets}           │\n"
        "│   <i>Preparing your lecture</i>    │\n"
        "╰────────────────────────────╯"
    )


def format_watermark_card(
    title: str,
    frame: int = 0,
    subtext: str = "Applying Course Wallah watermark",
    processed_sec: Optional[float] = None,
    total_sec: Optional[float] = None,
    speed_x: Optional[float] = None,
    percent: Optional[float] = None
) -> str:
    """Builds a dedicated Course Wallah video watermarking status card with real progress."""
    if percent is not None and total_sec and total_sec > 0:
        bar = make_progress_bar(percent)
        proc_str = hrt(processed_sec) if processed_sec is not None else "00s"
        tot_str = hrt(total_sec)
        spd_str = f"{speed_x:.1f}x" if speed_x else "1.0x"
        rem_sec = max(0, (total_sec - (processed_sec or 0)) / (speed_x or 1.0)) if speed_x and speed_x > 0 else 0
        rem_str = hrt(rem_sec)
        return (
            "🎨 <b>WATERMARKING VIDEO</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🎬 <b>{title[:45]}</b>\n\n"
            f"<code>{bar}</code> <b>{percent:5.1f}%</b>\n\n"
            f"⏱ <b>Processed:</b> {proc_str} / {tot_str}\n"
            f"⚡ <b>Speed:</b> {spd_str}\n"
            f"⏳ <b>Remaining:</b> {rem_str}"
        )
    dots = get_dot_animation(frame)
    bullets = get_bullet_animation(frame)
    return (
        "🎨 <b>WATERMARKING VIDEO</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎬 <b>{title[:45]}</b>\n\n"
        f"🎨 {subtext} {dots}\n"
        f"{bullets}"
    )


def format_merging_card(title: str, frame: int = 0) -> str:
    """
    Builds an actual merge/remux stage card.
    Must only be called when an actual merge/remux operation is running.
    """
    dots = get_dot_animation(frame)
    bullets = get_bullet_animation(frame)
    return (
        "⚙️ <b>MERGING VIDEO</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎬 <b>{title[:45]}</b>\n\n"
        f"⚙️ Merging {dots}\n"
        f"{bullets}"
    )


def format_processing_card(title: str, frame: int = 0, stage_name: str = "Processing") -> str:
    """Builds a general non-merge processing stage card."""
    dots = get_dot_animation(frame)
    bullets = get_bullet_animation(frame)
    return (
        "⚙️ <b>PROCESSING VIDEO</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎬 <b>{title[:45]}</b>\n\n"
        f"⚙️ {stage_name} {dots}\n"
        f"{bullets}"
    )


def format_split_card(title: str, frame: int = 0) -> str:
    """Builds a large video splitting status card."""
    dots = get_dot_animation(frame)
    bullets = get_bullet_animation(frame)
    return (
        "✂️ <b>PREPARING LARGE VIDEO</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎬 <b>{title[:45]}</b>\n\n"
        "This video is larger than the upload limit.\n"
        "Automatically splitting it into uploadable parts...\n\n"
        f"✂️ Splitting {dots}\n"
        f"{bullets}"
    )


def format_upload_card(
    title: str,
    part_idx: Optional[int] = None,
    total_parts: Optional[int] = None,
    frame: int = 0
) -> str:
    """Builds an animated Telegram video upload status card."""
    dots = get_dot_animation(frame)
    bullets = get_bullet_animation(frame)
    clean_title = (title or "Lecture")[:45]

    if part_idx is not None and total_parts is not None and total_parts > 1:
        header = f"📤 <b>UPLOADING PART {part_idx}/{total_parts}</b>"
        action = f"📤 Uploading Part {part_idx}/{total_parts} {dots}"
    else:
        header = "📤 <b>UPLOADING VIDEO</b>"
        action = f"📤 Uploading {dots}"

    return (
        f"{header}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎬 <b>{clean_title}</b>\n\n"
        f"{action}\n"
        f"{bullets}"
    )


def format_pdf_download_card(course_name: str, title: str, frame: int = 0) -> str:
    """Builds a PDF study material download status card."""
    dots = get_dot_animation(frame)
    bullets = get_bullet_animation(frame)
    return (
        "📄 <b>DOWNLOADING STUDY MATERIAL</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📚 <b>{(course_name or 'Course Wallah')[:35]}</b>\n"
        f"🎓 <b>{(title or 'Notes')[:45]}</b>\n\n"
        f"📄 Downloading {dots}\n"
        f"{bullets}"
    )


def format_pdf_processing_card(course_name: str, title: str, frame: int = 0) -> str:
    """Builds a PDF study material processing status card."""
    dots = get_dot_animation(frame)
    bullets = get_bullet_animation(frame)
    return (
        "📄 <b>PROCESSING STUDY MATERIAL</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📚 <b>{(course_name or 'Course Wallah')[:35]}</b>\n"
        f"🎓 <b>{(title or 'Notes')[:45]}</b>\n\n"
        f"📄 Processing {dots}\n"
        f"{bullets}"
    )


def format_pdf_upload_card(course_name: str, title: str, frame: int = 0) -> str:
    """Builds a PDF study material upload status card."""
    dots = get_dot_animation(frame)
    bullets = get_bullet_animation(frame)
    return (
        "📤 <b>UPLOADING STUDY MATERIAL</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📚 <b>{(course_name or 'Course Wallah')[:35]}</b>\n"
        f"🎓 <b>{(title or 'Notes')[:45]}</b>\n\n"
        f"📤 Uploading {dots}\n"
        f"{bullets}"
    )


def format_success_card(
    lecture_title: str,
    video_delivered: bool = True,
    pdf_delivered: bool = False,
    split_parts: int = 1,
    credit: str = CREDIT
) -> str:
    """Builds a designed Course Wallah completion status card."""
    v_icon = "✅" if video_delivered else "ℹ️ Not Available"
    p_icon = "✅" if pdf_delivered else "ℹ️ Not Available"
    part_str = f"\n📦 <b>{split_parts} part(s) uploaded</b>" if split_parts > 1 else ""

    return (
        "╭────────────────────────────╮\n"
        "│      🎓 <b>COURSE WALLAH</b>      │\n"
        "│                            │\n"
        "│     ✅ <b>DOWNLOAD READY</b>      │\n"
        "╰────────────────────────────╯\n"
        f"🎬 <b>{lecture_title[:45]}</b>\n\n"
        f"🎬 Video: {v_icon}\n"
        f"📄 Study Material: {p_icon}"
        f"{part_str}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>{credit or 'Course Wallah'}</b>"
    )


def format_youtube_quality_menu(
    title: str,
    duration_sec: float,
    qualities: List[Dict[str, Any]],
    job_id: str,
    user_id: int
) -> Tuple[str, InlineKeyboardMarkup]:
    """
    Builds a premium Telegram card with inline keyboard buttons for each REAL available quality.
    """
    dur_str = hrt(int(duration_sec)) if duration_sec > 0 else "N/A"
    clean_title = (title or "YouTube Video")[:60]

    text = (
        "╭────────────────────────────╮\n"
        "│      🎬 <b>VIDEO READY</b>       │\n"
        "│    ⚡ <b>COURSE WALLAH</b>        │\n"
        "╰────────────────────────────╯\n\n"
        f"📌 <b>Title:</b> <code>{clean_title}</code>\n"
        f"⏱ <b>Duration:</b> <code>{dur_str}</code>\n"
        f"📺 <b>Available Streams:</b> {len(qualities)} options\n\n"
        "👇 <b>Select Download Quality:</b>"
    )

    buttons = []
    # 4K gets prominent full-width button if available
    q_4k = [q for q in qualities if q.get("height", 0) >= 2160]
    q_other = [q for q in qualities if q.get("height", 0) < 2160]

    if q_4k:
        q = q_4k[0]
        size_lbl = f" • {q.get('formatted_size')}" if q.get("formatted_size") else ""
        buttons.append([
            InlineKeyboardButton(
                f"🔥 4K UHD • 2160p{size_lbl}",
                callback_data=f"ytq:{job_id}:2160:{user_id}"
            )
        ])

    row = []
    for q in q_other:
        h = q.get("height", 720)
        badge = q.get("badge", "🎥")
        short_lbl = q.get("short_label", f"{h}p")
        row.append(
            InlineKeyboardButton(
                f"{badge} {short_lbl}",
                callback_data=f"ytq:{job_id}:{h}:{user_id}"
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton("❌ Cancel", callback_data=f"ytcancel:{job_id}:{user_id}")
    ])

    return text, InlineKeyboardMarkup(buttons)


def format_youtube_fallback_card(
    title: str,
    duration_sec: float,
    requested_height: int,
    highest_quality: Dict[str, Any],
    job_id: str,
    user_id: int
) -> Tuple[str, InlineKeyboardMarkup]:
    """
    Builds fallback card when requested quality (e.g. 4K) is unavailable.
    """
    dur_str = hrt(int(duration_sec)) if duration_sec > 0 else "N/A"
    clean_title = (title or "YouTube Video")[:60]
    target_h = highest_quality.get("height", 720)
    target_lbl = highest_quality.get("short_label", f"{target_h}p")
    target_badge = highest_quality.get("badge", "🎬")

    req_lbl = "4K UHD (2160p)" if requested_height >= 2160 else f"{requested_height}p"

    text = (
        "╭────────────────────────────╮\n"
        "│      ⚠️ <b>QUALITY NOTICE</b>     │\n"
        "│    ⚡ <b>COURSE WALLAH</b>        │\n"
        "╰────────────────────────────╯\n\n"
        f"📌 <b>Title:</b> <code>{clean_title}</code>\n"
        f"⏱ <b>Duration:</b> <code>{dur_str}</code>\n\n"
        f"⚠️ <b>{req_lbl} is not available for this video.</b>\n"
        f"🎯 <b>Highest available quality:</b> <b>{target_badge} {target_lbl}</b>\n\n"
        f"<i>Would you like to download in {target_lbl}?</i>"
    )

    buttons = [
        [InlineKeyboardButton(f"✅ Download {target_lbl}", callback_data=f"ytq:{job_id}:{target_h}:{user_id}")],
        [InlineKeyboardButton("🎚 Choose Quality", callback_data=f"ytmenu:{job_id}:{user_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"ytcancel:{job_id}:{user_id}")]
    ]

    return text, InlineKeyboardMarkup(buttons)


def format_universal_input_card(
    action_title: str,
    instruction: str,
    example: Optional[str] = None,
    cancel_callback: Optional[str] = None,
    user_id: Optional[int] = None
) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    Constructs a universal, clean, premium input prompt card with action title,
    expected input format, safe example, and cancel action.
    """
    clean_title = (action_title or "INPUT").upper()
    lines = [
        "╭━━━━━━━━━━━━━━━━━━━━━━╮",
        f"│ 🎓 <b>COURSE WALLAH</b>      │",
        f"│     <b>{clean_title}</b>",
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n",
        f"📝 <b>{instruction}</b>"
    ]
    if example:
        lines.append(f"\n━━━━━━━━━━━━━━━━━━━━━━\n💡 <b>Example:</b> <code>{example}</code>")

    buttons = None
    if cancel_callback:
        cb = cancel_callback if user_id is None else f"{cancel_callback}:{user_id}"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data=cb)]
        ])

    return "\n".join(lines), buttons


def format_drm_input_card(user_id: int = 0) -> Tuple[str, InlineKeyboardMarkup]:
    """
    Constructs clean, professional prompt card for /drm command.
    """
    text = (
        "╭────────────────────────╮\n"
        "│ 🔐 <b>DRM CHECKER & BATCH</b> │\n"
        "╰────────────────────────╯\n\n"
        "📝 <b>Send the media URL you want to check</b>\n"
        "<i>or send a <code>.txt</code> file containing course links.</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <i>Only send authorized media or course links.</i>\n"
        "💡 <b>Example:</b> <code>https://...</code> or upload <code>links.txt</code>"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data=f"input_cancel:{user_id}")]
    ])
    return text, buttons


def format_drm_result_card(
    title: str,
    is_drm: bool,
    media_type: str = "Stream",
    details: Optional[str] = None
) -> str:
    """
    Constructs clean DRM check result card without exposing secrets/headers/keys.
    """
    clean_title = (title or "Media Stream")[:60]
    drm_status = "🔴 <b>DRM Protected</b> (Encrypted / Widevine / PlayReady)" if is_drm else "🟢 <b>No DRM Detected</b> (Directly Playable & Downloadable)"
    lines = [
        "╭────────────────────────╮",
        "│ 🔐 <b>DRM CHECK RESULT</b>    │",
        "╰────────────────────────╯\n",
        f"🎬 <b>Media:</b> <code>{clean_title}</code>",
        f"📦 <b>Type:</b> <code>{media_type}</code>",
        f"🛡️ <b>DRM Status:</b> {drm_status}"
    ]
    if details:
        lines.append(f"ℹ️ <b>Details:</b> <i>{details}</i>")
    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚡ <i>Powered by Course Wallah</i>")
    return "\n".join(lines)


def check_media_drm_status(url: str) -> Dict[str, Any]:
    """
    Safely probes media URL for DRM encryption without exposing tokens/keys/headers.
    """
    clean_title = MediaRouter.extract_clean_title(url, "Media Stream")
    m_type = MediaRouter.classify_url(url)
    
    is_drm = False
    details = "Clean playable media stream."
    type_lbl = m_type.value.upper()
    
    # Check for DASH/MPD or Widevine
    url_lower = url.lower()
    if ".mpd" in url_lower or "mpd" in url_lower:
        type_lbl = "MPEG-DASH / MPD"
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if "ContentProtection" in r.text or "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed" in r.text or "cenc" in r.text:
                is_drm = True
                details = "Widevine / Common Encryption (CENC) DRM detected."
            else:
                details = "Clear/unencrypted DASH manifest."
        except Exception:
            details = "DASH manifest (status unverified)."
    elif ".m3u8" in url_lower or "m3u8" in url_lower:
        type_lbl = "HLS / M3U8"
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if "#EXT-X-KEY:METHOD=SAMPLE-AES" in r.text or "SAMPLE-AES-CTR" in r.text:
                is_drm = True
                details = "FairPlay / Sample-AES DRM detected."
            elif "#EXT-X-KEY:METHOD=AES-128" in r.text:
                details = "AES-128 HLS standard encrypted (Supported via Course Wallah)."
            else:
                details = "Clear HLS stream."
        except Exception:
            details = "HLS playlist."
    elif "youtube" in url_lower or "youtu.be" in url_lower:
        type_lbl = "YouTube Video"
        details = "YouTube stream (Supported via YTUltra 4K pipeline)."
    else:
        type_lbl = f"Direct {m_type.value.title()}"
        details = "Standard media URL."

    return {
        "title": clean_title,
        "is_drm": is_drm,
        "type": type_lbl,
        "details": details
    }


def format_bot_online_card(
    bot_name: str,
    bot_username: str = "",
    active_bots: int = 1,
    total_bots: int = 1,
    recovered_jobs: int = 0
) -> Tuple[str, InlineKeyboardMarkup]:
    """
    Builds the official startup & online status announcement card for Telegram.
    """
    uname_str = f"@{bot_username}" if bot_username else bot_name
    now_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    
    rec_line = f"♻️ <b>Recovery scan completed:</b> {recovered_jobs} job(s) restored\n" if recovered_jobs > 0 else "♻️ <b>Recovery scan completed:</b> No unfinished jobs found\n"

    text = (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "│ 🎓 <b>COURSE WALLAH</b>      │\n"
        "│      <b>BOT ONLINE</b>       │\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "🟢 <b>Bot is now ONLINE & LIVE</b>\n\n"
        "⚡ <b>Downloader:</b> READY\n"
        "🎬 <b>YouTube:</b> READY\n"
        "🔥 <b>4K Remote Pipeline:</b> READY\n"
        "📺 <b>Quality Selector:</b> READY\n"
        "📤 <b>Telegram Upload:</b> READY\n"
        "🔄 <b>Auto Recovery:</b> ENABLED\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🤖 <b>{bot_name}</b> (<code>{uname_str}</code>)\n"
        f"🟢 <b>Status:</b> ONLINE ({active_bots}/{total_bots} active)\n"
        f"🕐 <b>Time:</b> <code>{now_str}</code>\n\n"
        f"{rec_line}\n"
        "🚀 <b>Course Wallah is ready!</b>\n"
        "Use /start to begin."
    )
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚀 Start", callback_data="help_menu"),
            InlineKeyboardButton("❓ Help", callback_data="help_menu")
        ]
    ])
    return text, buttons


# ==============================================================================
# 🧹 SAFE STORAGE & AUTO-CLEANUP UTILITIES
# ==============================================================================

def get_disk_storage_info(target_dir: Optional[str] = None) -> Dict[str, Any]:
    """Returns safe disk space metrics (total, used, free, drive letter)."""
    check_dir = target_dir or str(PROJECT_ROOT)
    try:
        usage = shutil.disk_usage(check_dir)
        drive_letter = Path(check_dir).resolve().drive or check_dir[:2]
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        used_gb = usage.used / (1024 ** 3)
        pct_used = (usage.used / usage.total) * 100.0 if usage.total > 0 else 0.0
        return {
            "drive": drive_letter,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "total_str": hrb(usage.total),
            "used_str": hrb(usage.used),
            "free_str": hrb(usage.free),
            "total_human": hrb(usage.total),
            "used_human": hrb(usage.used),
            "free_human": hrb(usage.free),
            "free_gb": free_gb,
            "total_gb": total_gb,
            "used_gb": used_gb,
            "percent_used": pct_used,
            "is_low_space": free_gb < 1.0  # Alert if less than 1GB free
        }
    except Exception as exc:
        logger.debug(f"[STORAGE] Failed to get disk usage for {check_dir}: {exc}")
        return {
            "drive": "Unknown",
            "total_bytes": 0,
            "used_bytes": 0,
            "free_bytes": 0,
            "total_str": "0 B",
            "used_str": "0 B",
            "free_str": "0 B",
            "total_human": "0 B",
            "used_human": "0 B",
            "free_human": "0 B",
            "free_gb": 0.0,
            "total_gb": 0.0,
            "used_gb": 0.0,
            "percent_used": 0.0,
            "is_low_space": False
        }


def is_safe_temp_path(target_path: Optional[Union[str, Path]]) -> bool:
    """
    Strict safety check: Verifies that target_path is within project's TEMP_DIR,
    DOWNLOADS_DIR, or DATA_DIR, and NEVER a root or parent system directory.
    """
    if not target_path:
        return False
    try:
        resolved = Path(target_path).resolve()
        # Disallow root or drive letter paths (e.g. C:\, B:\, /)
        if str(resolved) == str(resolved.anchor) or len(resolved.parts) <= 1:
            return False

        # Disallow system and user roots
        proj_root = Path(PROJECT_ROOT).resolve()
        temp_root = Path(TEMP_DIR).resolve()
        down_root = Path(DOWNLOADS_DIR).resolve()
        data_root = Path(DATA_DIR).resolve()
        sys_temp = Path(tempfile.gettempdir()).resolve()

        # Must not be the root directories themselves
        if resolved in (proj_root, data_root, temp_root, down_root, sys_temp):
            return False

        # Path must be a sub-path of temp_root, down_root, data_root, or system temp
        for allowed in (temp_root, down_root, data_root, sys_temp):
            try:
                resolved.relative_to(allowed)
                return True
            except ValueError:
                continue

        return False
    except Exception:
        return False


def cleanup_uploaded_file(filepath: Optional[Union[str, Path]]) -> bool:
    """
    Safely deletes an uploaded media or intermediate file after confirmed Telegram upload.
    Strictly verifies containment within TEMP_DIR/DOWNLOADS_DIR before unlinking.
    """
    if not filepath:
        return False
    try:
        p = Path(filepath)
        if not p.exists():
            return False
        if is_safe_temp_path(p):
            if p.is_file():
                p.unlink(missing_ok=True)
                logger.debug(f"[CLEANUP] Deleted uploaded file: {p}")
                return True
        else:
            logger.warning(f"[CLEANUP REJECTED] Path {p} failed safety containment check.")
    except Exception as exc:
        logger.debug(f"[CLEANUP] Error removing {filepath}: {exc}")
    return False


def cleanup_job_temp_dir(dir_path: Optional[Union[str, Path]]) -> bool:
    """
    Safely removes an entire job-specific temporary directory upon job completion/cancel.
    Strictly verifies containment within TEMP_DIR before removing.
    """
    if not dir_path:
        return False
    try:
        p = Path(dir_path)
        if not p.exists():
            return False
        if is_safe_temp_path(p):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
                logger.debug(f"[CLEANUP] Purged job temp directory: {p}")
                return True
        else:
            logger.warning(f"[CLEANUP REJECTED] Dir {p} failed safety containment check.")
    except Exception as exc:
        logger.debug(f"[CLEANUP] Error removing job temp dir {dir_path}: {exc}")
    return False


def cleanup_stale_temp_dirs(active_job_ids: Optional[set] = None) -> int:
    """
    Scans TEMP_DIR for stale/orphaned job temp folders and safely cleans them.
    Keeps directories belonging to active or recovering jobs.
    """
    cleaned_count = 0
    temp_p = Path(TEMP_DIR)
    if not temp_p.exists() or not temp_p.is_dir():
        return 0

    active_ids = active_job_ids or set()
    try:
        for item in temp_p.glob("*"):
            if item.is_dir():
                # Check user level dir or direct job dir
                if item.name.isdigit():
                    for sub in item.glob("job_*"):
                        if sub.name not in active_ids:
                            if cleanup_job_temp_dir(sub):
                                cleaned_count += 1
                elif item.name.startswith("job_") and item.name not in active_ids:
                    if cleanup_job_temp_dir(item):
                        cleaned_count += 1
    except Exception as exc:
        logger.debug(f"[CLEANUP] Stale temp scan error: {exc}")
    return cleaned_count



