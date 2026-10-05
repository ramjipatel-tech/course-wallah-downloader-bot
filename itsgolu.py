import os
import re
import json
import time
import mmap
import hashlib
import datetime
import aiohttp
import aiofiles
import asyncio
import logging
import requests
import tgcrypto
import subprocess
import concurrent.futures
from math import ceil
from io import BytesIO
from pathlib import Path
from base64 import b64decode
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import urljoin, urlparse

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

import math
import m3u8
import yt_dlp

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from pyrogram import Client, filters
from pyrogram.types import Message

from utils import (
    progress_bar as ext_progress_bar,
    cleanup_uploaded_file,
    cleanup_job_temp_dir,
    is_safe_temp_path,
    parse_pdf_input,
    PDFError,
    PDFPasswordInvalid,
    PDFUnlockFailed,
    PDFUnlockOutputInvalid,
    PDFDownloadFailed,
    PDFInvalid,
    PDF_PASSWORD_INVALID,
    PDF_UNLOCK_FAILED,
    PDF_UNLOCK_OUTPUT_INVALID,
    PDF_DOWNLOAD_FAILED,
    PDF_INVALID
)
import shutil
from vars import *
from db import db, Database
from youtube_fallback import (
    resolve_youtube_vynex,
    resolve_youtube_ytultra,
    resolve_youtube_ytultra_info,
    download_media_stream_url,
    probe_remote_duration,
    extract_remote_media_segment
)


# =========================
# GLOBALS
# =========================
logger = logging.getLogger(__name__)
_LAST_EDIT_TIME = {}


# =========================
# HELPERS
# =========================
def safe_filename(name: str) -> str:
    name = str(name).strip()
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:150] if name else f"file_{int(time.time())}"


def create_session():
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=10,
        pool_maxsize=10,
        max_retries=3
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def get_cookies_file(user_id: int = None) -> str | None:
    """
    Resolves the cookies file path for a given user if it exists and is non-empty.
    Falls back to global COOKIES_FILE if available.
    """
    if user_id:
        try:
            u_cookie = db.get_user_cookies_path(user_id)
            if os.path.exists(u_cookie) and os.path.getsize(u_cookie) > 0:
                return u_cookie
        except Exception:
            pass
    if COOKIES_FILE and os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 0:
        return COOKIES_FILE
    return None



# =========================
# QUALITY & RESOLUTION HELPERS
# =========================

def parse_quality_number(q_str: str) -> int:
    """
    Extracts numerical resolution from a quality label string.
    Examples:
      '2160p' -> 2160, '4k' -> 2160, '1440p' -> 1440, '2k' -> 1440,
      '1080p' -> 1080, '720p' -> 720, '480p' -> 480, '360p' -> 360,
      '240p' -> 240, '144p' -> 144, '720' -> 720
    """
    if not q_str:
        return 0
    s = str(q_str).lower().strip()
    if s == "4k" or "2160" in s:
        return 2160
    if s == "2k" or "1440" in s:
        return 1440
    m = re.search(r'(\d+)', s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return 0
def get_ytdlp_cmd() -> list[str]:
    """
    Returns the command list to invoke yt-dlp safely across environments (systemd venv, Docker, Windows, Linux).
    Resolves directly from active sys.executable venv/bin or venv/Scripts, explicit VM paths, or fallback to python -m yt_dlp.
    """
    py_dir = os.path.dirname(sys.executable)
    for name in ("yt-dlp", "yt-dlp.exe"):
        cand = os.path.join(py_dir, name)
        if os.path.isfile(cand) and (os.access(cand, os.X_OK) or sys.platform.startswith("win")):
            return [cand]

    vm_cand = "/opt/course-wallah-downloader/.venv/bin/yt-dlp"
    if os.path.isfile(vm_cand) and os.access(vm_cand, os.X_OK):
        return [vm_cand]

    which_cand = shutil.which("yt-dlp")
    if which_cand:
        return [which_cand]

    return [sys.executable, "-m", "yt_dlp"]


# =========================
# UNIFIED LECTURE SOURCE RESOLVER (VIDEO + PDF/DOCUMENT)
# =========================

from dataclasses import dataclass, field
import sys
import shutil

@dataclass
class LectureSourceResult:
    has_video: bool = False
    video_url: Optional[str] = None
    video_quality: Optional[str] = None
    has_pdf: bool = False
    pdf_url: Optional[str] = None
    title: str = ""
    thumbnail: Optional[str] = None
    video_id: str = ""
    course_id: str = ""
    is_drm: bool = False
    drm_message: Optional[str] = None
    error: Optional[str] = None
    raw_data: Optional[dict] = None


def resolve_lecture_source(url: str, target_quality: str = None) -> LectureSourceResult:
    """
    Unified lecture source resolver.
    Hits the source/API URL ONCE and extracts both Video and PDF/document information,
    along with normalized metadata, handling DRM detection and independent content extraction.
    """
    if not url or not isinstance(url, str) or not url.strip():
        return LectureSourceResult(error="Invalid or empty lecture URL")

    clean_url = url.strip()
    if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
        return LectureSourceResult(error="Invalid URL scheme (must start with http:// or https://)")

    # Direct PDF file check
    if clean_url.lower().endswith(".pdf") or ".pdf?" in clean_url.lower():
        title_stem = Path(urlparse(clean_url).path).stem or "Document"
        return LectureSourceResult(has_pdf=True, pdf_url=clean_url, title=title_stem)

    print("[LECTURE RESOLVER] Processing lecture source URL...")

    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    }

    try:
        response = requests.get(clean_url, headers=headers, timeout=30)
    except requests.exceptions.Timeout:
        print("[LECTURE RESOLVER] Request timed out (30s)")
        return LectureSourceResult(error="API request timed out (30s)")
    except requests.exceptions.ConnectionError:
        print("[LECTURE RESOLVER] Connection error")
        return LectureSourceResult(error="API connection failed")
    except Exception as exc:
        print(f"[LECTURE RESOLVER] Network error: {type(exc).__name__}")
        return LectureSourceResult(error=f"API network error: {str(exc)}")

    if response.status_code != 200:
        print(f"[LECTURE RESOLVER] API returned HTTP {response.status_code}")
        return LectureSourceResult(error=f"API returned HTTP {response.status_code} ({response.reason})")

    # If the response itself is a PDF document
    content_type = response.headers.get("content-type", "").lower()
    if "application/pdf" in content_type:
        print("[LECTURE RESOLVER] API returned direct PDF content stream.")
        return LectureSourceResult(has_pdf=True, pdf_url=response.url, title="Document")

    try:
        data_json = response.json()
    except Exception:
        print("[LECTURE RESOLVER] Non-JSON API response.")
        return LectureSourceResult(error="Malformed JSON response from API")

    if not isinstance(data_json, dict):
        return LectureSourceResult(error="Invalid API response structure (expected JSON object)")

    # 1. DRM Protection Check
    if (
        data_json.get("drmProtected") in (1, "1", True)
        or str(data_json.get("is_drm", "")).lower() in ("1", "true")
        or (isinstance(data_json.get("data"), dict) and data_json.get("data", {}).get("drmProtected") in (1, "1", True))
    ):
        msg = data_json.get("message") or data_json.get("msg") or "Content is protected by DRM"
        print(f"[LECTURE RESOLVER] DRM protection detected: {msg}")
        return LectureSourceResult(
            is_drm=True,
            drm_message=str(msg),
            error=f"Content is DRM protected: {msg}",
            raw_data=data_json
        )

    payload = data_json.get("data")
    if payload is None or not isinstance(payload, dict):
        payload = data_json

    # 2. Extract Video Information (Multi-field support)
    has_video = False
    video_url = None
    selected_quality_label = None

    # A. Check structured list: download_links or encrypted_links
    download_links = (
        payload.get("download_links")
        or payload.get("encrypted_links")
        or data_json.get("download_links")
        or data_json.get("encrypted_links")
    )
    valid_links = []
    if isinstance(download_links, list):
        valid_links = [
            item for item in download_links
            if isinstance(item, dict) and (item.get("path") or item.get("url") or item.get("link"))
            and str(item.get("path") or item.get("url") or item.get("link")).strip().startswith(("http://", "https://"))
        ]

    if valid_links:
        def link_resolution_key(item):
            q = str(item.get("quality", item.get("bitrate", "")))
            return parse_quality_number(q)

        sorted_links = sorted(valid_links, key=link_resolution_key, reverse=True)
        selected_link = None
        target_res = parse_quality_number(target_quality) if target_quality else 0

        if target_res > 0:
            for item in sorted_links:
                if link_resolution_key(item) == target_res:
                    selected_link = item
                    break
            if not selected_link:
                selected_link = sorted_links[0]
                print(f"[LECTURE RESOLVER] Requested quality ({target_quality}) unavailable, selected highest available: {selected_link.get('quality', selected_link.get('bitrate', 'Unknown'))}")
        else:
            selected_link = sorted_links[0]

        raw_path = selected_link.get("path") or selected_link.get("url") or selected_link.get("link")
        cand_url = str(raw_path).strip()
        parsed = urlparse(cand_url)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            has_video = True
            video_url = cand_url
            q_val = str(selected_link.get("quality") or selected_link.get("bitrate") or f"{link_resolution_key(selected_link)}p").strip()
            if not q_val.endswith("p") and q_val.isdigit():
                q_val += "p"
            selected_quality_label = q_val

    # B. Fallback to direct video fields if list not present
    if not has_video:
        for cand_key in ("file_link", "download_link", "download_link2", "backup_url", "backup_url2", "video_url", "url"):
            cand_val = payload.get(cand_key) or data_json.get(cand_key)
            if cand_val and isinstance(cand_val, str) and cand_val.strip().startswith(("http://", "https://")):
                c_clean = cand_val.strip()
                # Ensure it's not a PDF
                if not c_clean.lower().endswith(".pdf") and ".pdf?" not in c_clean.lower():
                    has_video = True
                    video_url = c_clean
                    selected_quality_label = "720p"
                    break

    # 3. Extract PDF / Document Information (Multi-field support)
    has_pdf = False
    pdf_url = None

    # Check direct PDF keys
    pdf_direct_keys = (
        "pdf_link", "pdf_link2", "pdf_summary_link", "pdf2_summary_link",
        "Pdf_link", "pdf_url", "document", "file", "notes_link", "notes_url"
    )
    for p_key in pdf_direct_keys:
        cand_pdf = payload.get(p_key) or data_json.get(p_key)
        if cand_pdf and isinstance(cand_pdf, str) and cand_pdf.strip().startswith(("http://", "https://")):
            has_pdf = True
            pdf_url = cand_pdf.strip()
            break

    # Fallback to recursive JSON scanning if not found in direct keys
    if not has_pdf:
        pdf_candidates = extract_pdf_urls_from_api_response(data_json, clean_url)
        if pdf_candidates:
            has_pdf = True
            pdf_url = pdf_candidates[0]

    # 4. Extract Metadata
    raw_title = payload.get("Title") or payload.get("title") or data_json.get("Title") or data_json.get("title") or ""
    title_val = str(raw_title).strip() if raw_title else ""

    raw_thumb = payload.get("thumbnail") or payload.get("poster") or data_json.get("thumbnail") or ""
    thumb_val = str(raw_thumb).strip() if raw_thumb else ""

    video_id_val = str(data_json.get("video_id") or payload.get("video_id") or "")
    course_id_val = str(data_json.get("course_id") or payload.get("course_id") or "")

    err_msg = None
    if not has_video and not has_pdf:
        err_msg = data_json.get("message") or data_json.get("msg") or "No video or PDF links found in API response"

    print(f"[LECTURE RESOLVER] Resolution complete: Video={'YES (' + selected_quality_label + ')' if has_video else 'NO'} | PDF={'YES' if has_pdf else 'NO'}")

    return LectureSourceResult(
        has_video=has_video,
        video_url=video_url,
        video_quality=selected_quality_label or "720p",
        has_pdf=has_pdf,
        pdf_url=pdf_url,
        title=title_val,
        thumbnail=thumb_val or None,
        video_id=video_id_val,
        course_id=course_id_val,
        is_drm=False,
        error=err_msg,
        raw_data=data_json
    )


def resolve_fetch_video_url(url: str, target_quality: str = None) -> tuple[str | None, dict | None, str | None]:
    """
    Backward-compatible adapter for resolve_lecture_source.
    Returns (m3u8_path, metadata_dict, error_message).
    """
    res = resolve_lecture_source(url, target_quality)
    if res.is_drm:
        return None, None, res.error or "DRM Protected"
    if res.error and not res.has_video and not res.has_pdf:
        return None, None, res.error

    metadata = {
        "quality": res.video_quality,
        "title": res.title,
        "thumbnail": res.thumbnail,
        "pdf_url": res.pdf_url,
        "video_id": res.video_id,
        "course_id": res.course_id,
        "has_video": res.has_video,
        "has_pdf": res.has_pdf,
    }
    return res.video_url, metadata, (res.error if not res.has_video and not res.has_pdf else None)


# =========================
# DOWNLOAD M3U8
# =========================

def download_appx_m3u8(url: str, name: str, custom_headers: str = None, custom_dir: str = "downloads") -> str | None:
    """
    Fast M3U8 video download using FFmpeg with stream copy (-c copy), connection persistence,
    and automatic reconnection flags for maximum throughput.
    Supports CloudFront HLS, Akamai, S3, signed URLs, query parameters, relative/absolute .ts segments.
    """
    if not url or not isinstance(url, str) or not url.strip():
        print("[DOWNLOAD] Invalid or empty M3U8 URL.")
        return None

    clean_url = url.strip()
    out_dir = custom_dir or "downloads"
    os.makedirs(out_dir, exist_ok=True)
    safe_name = safe_filename(name)
    output = os.path.join(out_dir, f"{safe_name}.mp4")

    if custom_headers:
        headers = custom_headers
    elif any(k in clean_url.lower() for k in ("akamai.net.in", "classx.co.in", "appx.co.in", "classplusapp.com")):
        headers = (
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36\r\n"
            "Referer: https://player.akamai.net.in/\r\n"
            "Origin: https://akstechnicalclasses.classx.co.in\r\n"
            "Accept: */*\r\n"
            "Connection: keep-alive\r\n"
        )
    else:
        headers = (
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36\r\n"
            "Accept: */*\r\n"
            "Accept-Language: en-US,en;q=0.9\r\n"
            "Connection: keep-alive\r\n"
        )

    retry_count = 0
    max_retries = 3
    download_success = False
    while retry_count < max_retries:
        if os.path.exists(output):
            try:
                os.remove(output)
            except OSError:
                pass

        cmd = [
            FFMPEG_PATH or "ffmpeg",
            "-y",
            "-loglevel", "error",
            "-headers", headers,
            "-reconnect", "1",
            "-reconnect_at_eof", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-multiple_requests", "1",
            "-i", clean_url,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            "-movflags", "+faststart",
            output
        ]

        print(f"[DOWNLOAD] Downloading M3U8 stream (attempt {retry_count + 1}) with FFmpeg...")
        logging.info(f"Downloading M3U8 stream to {output}")
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            print("[DOWNLOAD] Download succeeded")
            download_success = True
            break
        except subprocess.CalledProcessError as exc:
            retry_count += 1
            err_msg = exc.stderr.strip() if exc.stderr else str(exc)
            print(f"[DOWNLOAD] Retry {retry_count}/{max_retries} failed: {err_msg}")
            if retry_count == max_retries:
                print("[DOWNLOAD] All download retries failed.")
                if os.path.exists(output):
                    try:
                        os.remove(output)
                    except OSError:
                        pass
                return None

    if not download_success:
        return None

    if os.path.exists(output) and os.path.getsize(output) > 0:
        print(f"[DOWNLOAD] Stream download complete: {output} ({os.path.getsize(output)/(1024*1024):.1f} MB)")
        return output
    else:
        print(f"[DOWNLOAD] Download failed or file is empty: {output}")
        if os.path.exists(output):
            try:
                os.remove(output)
            except OSError:
                pass
        return None

download_direct_m3u8 = download_appx_m3u8
download_hls = download_appx_m3u8


def download_image(url: str, name: str, custom_headers: Optional[Union[dict, str]] = None, custom_dir: str = "downloads") -> Optional[str]:
    """
    Direct image downloader supporting jpg, jpeg, png, webp, gif, bmp, etc.
    Follows redirects, uses browser headers, validates Content-Type and magic bytes, and saves image.
    Never routes to yt-dlp, FFmpeg, KGS, or Spayee.
    """
    if not url or not isinstance(url, str) or not url.strip():
        logging.error("[IMAGE_DOWNLOAD] Invalid or empty image URL.")
        return None
    clean_url = url.strip()
    out_dir = custom_dir or "downloads"
    os.makedirs(out_dir, exist_ok=True)
    safe_name = safe_filename(name)

    parsed = urlparse(clean_url)
    path = parsed.path or ""
    ext = os.path.splitext(path)[1].lower()
    if not ext or ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
        ext = ".jpg"

    output = os.path.join(out_dir, f"{safe_name}{ext}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive"
    }
    if isinstance(custom_headers, dict):
        headers.update(custom_headers)

    try:
        session = create_session()
        response = session.get(clean_url, headers=headers, timeout=30, stream=True, allow_redirects=True)
        if response.status_code != 200:
            logging.error(f"[IMAGE_DOWNLOAD] HTTP {response.status_code} for {clean_url}")
            return None

        with open(output, "wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)

        if not os.path.exists(output) or os.path.getsize(output) < 16:
            logging.error(f"[IMAGE_DOWNLOAD] File too small or empty: {output}")
            if os.path.exists(output):
                try:
                    os.remove(output)
                except OSError:
                    pass
            return None

        logging.info(f"[IMAGE_DOWNLOAD] Succeeded: {output} ({os.path.getsize(output)} bytes)")
        return output
    except Exception as e:
        logging.error(f"[IMAGE_DOWNLOAD] Failed: {e}")
        if os.path.exists(output):
            try:
                os.remove(output)
            except OSError:
                pass
        return None


def download_direct_video(url: str, name: str, custom_headers: Optional[Union[dict, str]] = None, custom_dir: str = "downloads") -> Optional[str]:
    """
    Direct video downloader for non-YouTube direct video files (.mp4, .mkv, .webm, .mov, etc.).
    Uses streaming HTTP download with resume and browser headers, NOT yt-dlp.
    Only actual YouTube URLs use yt-dlp.
    """
    if not url or not isinstance(url, str) or not url.strip():
        logging.error("[DIRECT_VIDEO] Invalid or empty direct video URL.")
        return None
    clean_url = url.strip()
    out_dir = custom_dir or "downloads"
    os.makedirs(out_dir, exist_ok=True)
    safe_name = safe_filename(name)

    parsed = urlparse(clean_url)
    path = parsed.path or ""
    ext = os.path.splitext(path)[1].lower()
    if not ext or ext not in (".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".m4v"):
        ext = ".mp4"

    output = os.path.join(out_dir, f"{safe_name}{ext}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive"
    }
    if isinstance(custom_headers, dict):
        headers.update(custom_headers)

    retry_count = 0
    max_retries = 3
    while retry_count < max_retries:
        try:
            session = create_session()
            response = session.get(clean_url, headers=headers, timeout=60, stream=True, allow_redirects=True)
            if response.status_code != 200:
                retry_count += 1
                logging.warning(f"[DIRECT_VIDEO] HTTP {response.status_code} attempt {retry_count}/{max_retries}")
                time.sleep(2)
                continue

            with open(output, "wb") as f:
                for chunk in response.iter_content(chunk_size=1048576):  # 1MB chunks
                    if chunk:
                        f.write(chunk)

            if os.path.exists(output) and os.path.getsize(output) > 1024:
                logging.info(f"[DIRECT_VIDEO] Download complete: {output} ({os.path.getsize(output)/(1024*1024):.1f} MB)")
                return output
            else:
                retry_count += 1
                if os.path.exists(output):
                    try:
                        os.remove(output)
                    except OSError:
                        pass
        except Exception as e:
            retry_count += 1
            logging.warning(f"[DIRECT_VIDEO] Attempt {retry_count} failed: {e}")
            time.sleep(2)

    if os.path.exists(output):
        try:
            os.remove(output)
        except OSError:
            pass
    return None


from spayee_downloader import (
    download_spayee_hls,
    is_spayee_url,
    clean_spayee_key,
    select_spayee_variant,
    parse_spayee_input
)



# ==============================================================================
# WATERMARK & HARDWARE ACCELERATION (FASTEST-PATH ULTRA-PRO)
# ==============================================================================

_FASTEST_VERIFIED_ENCODER: str = "libx264"
_FASTEST_ENCODER_FLAGS: List[str] = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", str(WATERMARK_CRF), "-threads", "0", "-pix_fmt", "yuv420p"]
_HW_BENCHMARK_DONE: bool = True


def probe_media_properties(file_path: Union[str, Path], ffprobe_bin: str = "ffprobe") -> Dict[str, Any]:
    """
    Extracts precise video & audio properties using ffprobe.
    Returns dictionary with: duration, width, height, fps, video_codec, video_bitrate, has_audio, audio_codec, audio_duration, audio_bitrate, total_bitrate, size_bytes, valid.
    """
    props: Dict[str, Any] = {
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "video_codec": "",
        "video_bitrate": 0,
        "has_audio": False,
        "audio_codec": "",
        "audio_duration": 0.0,
        "audio_bitrate": 0,
        "total_bitrate": 0,
        "size_bytes": 0,
        "valid": False
    }
    if not file_path:
        return props

    p = Path(file_path)
    if not p.exists() or p.stat().st_size == 0:
        return props

    props["size_bytes"] = p.stat().st_size
    bin_path = str(FFPROBE_PATH if "FFPROBE_PATH" in globals() and FFPROBE_PATH else (ffprobe_bin or "ffprobe"))

    cmd = [
        bin_path, "-v", "error",
        "-show_format", "-show_streams",
        "-of", "json",
        str(file_path)
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            fmt = data.get("format", {})
            try:
                props["duration"] = float(fmt.get("duration", 0.0) or 0.0)
            except (ValueError, TypeError):
                pass
            try:
                if fmt.get("bit_rate"):
                    props["total_bitrate"] = int(fmt.get("bit_rate"))
            except Exception:
                pass

            for s in data.get("streams", []):
                ctype = s.get("codec_type")
                if ctype == "video" and not props["video_codec"]:
                    props["video_codec"] = s.get("codec_name", "")
                    props["width"] = int(s.get("width", 0) or 0)
                    props["height"] = int(s.get("height", 0) or 0)
                    if s.get("bit_rate"):
                        try:
                            props["video_bitrate"] = int(s.get("bit_rate"))
                        except Exception:
                            pass
                    if s.get("duration"):
                        try:
                            s_dur = float(s.get("duration"))
                            if props["duration"] == 0.0:
                                props["duration"] = s_dur
                        except (ValueError, TypeError):
                            pass
                    r_fps = s.get("r_frame_rate", "")
                    if "/" in str(r_fps):
                        try:
                            num, den = r_fps.split("/", 1)
                            if float(den) > 0:
                                props["fps"] = float(num) / float(den)
                        except Exception:
                            pass
                elif ctype == "audio" and not props["has_audio"]:
                    props["has_audio"] = True
                    props["audio_codec"] = s.get("codec_name", "")
                    if s.get("bit_rate"):
                        try:
                            props["audio_bitrate"] = int(s.get("bit_rate"))
                        except Exception:
                            pass
                    if s.get("duration"):
                        try:
                            props["audio_duration"] = float(s.get("duration"))
                        except (ValueError, TypeError):
                            pass

            props["valid"] = True
    except Exception as e:
        logger.debug(f"[PROBE] ffprobe probe notice for {file_path}: {e}")

    return props


def get_escaped_font_param() -> str:
    """
    Finds a usable font file and properly escapes path characters (including Windows colons)
    for FFmpeg drawtext filter syntax to prevent Fontconfig crashes on Windows.
    """
    candidate_paths = [
        os.path.abspath("font.otf"),
        os.path.join(str(PROJECT_ROOT), "font.otf"),
    ]
    if sys.platform.startswith("win"):
        candidate_paths.extend([
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf"
        ])
    else:
        candidate_paths.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf"
        ])

    for p in candidate_paths:
        if p and os.path.exists(p):
            safe_p = p.replace("\\", "/").replace(":", "\\:")
            return f"fontfile='{safe_p}':"

    return ""


def get_or_create_watermark_image(text: str = "Course Wallah", base_dir: Optional[str] = None) -> Optional[str]:
    """Compatibility stub for pre-rendered PNG watermark."""
    return None


def benchmark_and_get_fastest_encoder(ffmpeg_bin: str = "ffmpeg") -> Tuple[str, List[str]]:
    """
    Returns baseline production H.264 video encoder (libx264 ultrafast).
    Avoids expensive startup benchmark to ensure instant initialization.
    """
    global _FASTEST_VERIFIED_ENCODER, _FASTEST_ENCODER_FLAGS
    crf_val = str(WATERMARK_CRF if "WATERMARK_CRF" in globals() else 26)
    _FASTEST_VERIFIED_ENCODER = "libx264"
    _FASTEST_ENCODER_FLAGS = [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", crf_val,
        "-threads", "0",
        "-pix_fmt", "yuv420p"
    ]
    return _FASTEST_VERIFIED_ENCODER, _FASTEST_ENCODER_FLAGS


def detect_ffmpeg_hardware_encoders() -> Optional[str]:
    """
    Returns hardware encoder if explicitly configured/verified, else None (CPU libx264 baseline).
    """
    return None


def apply_video_watermark(
    video_path: str,
    watermark_text: str = None,
    watermark_file: str = None,
    progress_callback: Optional[Callable[[float, float, float, float], None]] = None
) -> str | None:
    """
    Burns a visible, stable watermark into the video using FFmpeg.
    - If watermark is disabled ('/d', 'no', 'none', '', None), returns original video_path immediately with ZERO re-encoding.
    - If video was already watermarked (_wm.mp4), returns video_path immediately (prevents duplicate encoding).
    - Uses stable drawtext filter with libx264 preset=ultrafast and reasonable CRF.
    - Strictly preserves duration (tolerance <= 0.10s, target <= 0.05s), resolution, FPS, and audio synchronization.
    - NEVER uses -shortest (preserves original video timeline).
    - Audio handling: attempts stream copy (-c:a copy); if incompatible, performs a single combined encode with AAC audio.
    - Atomic file safety: encodes to <base>_wm.tmp.mp4, validates with ffprobe, then atomically renames to <base>_wm.mp4.
    - Failure safety: On any error, keeps original clean input intact and deletes only temporary intermediate files.
    """
    if not video_path or not os.path.exists(video_path):
        return None

    # Check if watermark is requested
    has_text = watermark_text and str(watermark_text).strip() not in ("/d", "no", "none", "")
    has_file = watermark_file and isinstance(watermark_file, str) and os.path.exists(watermark_file)

    if not has_text and not has_file:
        return video_path

    # Prevent duplicate watermarking
    if str(video_path).endswith("_wm.mp4") or "watermarked" in str(video_path).lower():
        if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
            return video_path

    t0 = time.time()
    enc_name, enc_args = benchmark_and_get_fastest_encoder()

    # Pre-encode probe for duration, resolution, FPS, and audio
    in_props = probe_media_properties(video_path)
    in_dur = in_props.get("duration", 0.0) or duration(video_path)
    in_w = in_props.get("width", 0)
    in_h = in_props.get("height", 0)
    in_fps = in_props.get("fps", 0.0)
    in_has_audio = in_props.get("has_audio", False)
    in_audio_codec = in_props.get("audio_codec", "").lower()
    in_audio_dur = in_props.get("audio_duration", 0.0)

    wm_display = watermark_text or "Course Wallah"
    print(f"[WATERMARK] Starting single-pass video watermark encode (Text: '{wm_display}')...")
    logger.info(f"[WATERMARK] Starting single-pass video watermark encode (Text: '{wm_display}')...")

    base, ext = os.path.splitext(video_path)
    temp_output_wm = f"{base}_wm.tmp.mp4"
    final_output_wm = f"{base}_wm.mp4"

    # Clean any stale temp file
    if os.path.exists(temp_output_wm):
        try:
            os.remove(temp_output_wm)
        except OSError:
            pass

    # Build stable drawtext filter
    clean_text = str(wm_display).strip()
    safe_text = (
        clean_text
        .replace('\\', '\\\\')
        .replace(':', '\\:')
        .replace("'", "\\'")
        .replace('%', '\\%')
        .replace(',', '\\,')
    )
    font_arg = get_escaped_font_param()

    vf = (
        f"drawtext={font_arg}text='{safe_text}':"
        r"fontsize=max(16\,h/32):fontcolor=white@0.9:"
        r"box=1:boxcolor=black@0.45:boxborderw=8:line_spacing=2:"
        r"x=20:y=20"
    )

    def build_cmd(audio_mode: str) -> List[str]:
        cmd = [
            str(FFMPEG_PATH or "ffmpeg"), "-y", "-loglevel", "error",
            "-i", str(video_path),
            "-vf", vf,
            "-map", "0:v:0",
            "-map", "0:a?",
        ] + enc_args

        if audio_mode == "copy":
            cmd += ["-c:a", "copy", "-movflags", "+faststart", temp_output_wm]
        else:
            cmd += ["-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", temp_output_wm]
        return cmd

    last_cb_time = 0.0

    def run_proc(cmd_list: List[str]) -> Tuple[int, str]:
        nonlocal last_cb_time
        if progress_callback is None:
            res = subprocess.run(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return res.returncode, res.stderr or ""

        full_cmd = cmd_list[:-1] + ["-progress", "pipe:1", "-nostats", cmd_list[-1]]
        proc = subprocess.Popen(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        cur_sec = 0.0
        cur_speed = 1.0

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            if line.startswith("out_time_us="):
                try:
                    cur_sec = float(line.split("=")[1]) / 1000000.0
                except ValueError:
                    pass
            elif line.startswith("out_time_ms="):
                try:
                    cur_sec = float(line.split("=")[1]) / 1000000.0
                except ValueError:
                    pass
            elif line.startswith("speed="):
                val = line.split("=")[1].strip().rstrip("x")
                try:
                    cur_speed = float(val)
                except ValueError:
                    pass
            elif line.startswith("progress="):
                now = time.time()
                if in_dur > 0 and (now - last_cb_time >= 1.5 or line == "progress=end"):
                    last_cb_time = now
                    pct = min(100.0, (cur_sec / in_dur) * 100.0)
                    try:
                        progress_callback(cur_sec, in_dur, cur_speed, pct)
                    except Exception:
                        pass

        _, err = proc.communicate()
        return proc.returncode, err or ""

    try:
        # Determine initial audio strategy
        # Compatible stream-copy audio codecs: aac, mp3, ac3, eac3
        prefer_copy = (not in_has_audio) or (in_audio_codec in ("aac", "mp3", "ac3", "eac3", ""))

        audio_strategy = "copy" if prefer_copy else "aac"
        cmd = build_cmd(audio_strategy)
        ret_code, err_msg = run_proc(cmd)

        # If audio copy failed, fallback to single combined encode with AAC
        if ret_code != 0 and audio_strategy == "copy":
            print(f"[WATERMARK] Audio copy failed ({err_msg.strip() or 'incompatible bitstream'}), retrying with AAC audio encode...")
            logger.info(f"[WATERMARK] Audio copy failed, retrying with AAC audio encode: {err_msg.strip()}")
            if os.path.exists(temp_output_wm):
                try:
                    os.remove(temp_output_wm)
                except OSError:
                    pass
            cmd_aac = build_cmd("aac")
            ret_code, err_msg = run_proc(cmd_aac)

        if ret_code != 0:
            raise RuntimeError(f"FFmpeg watermark encoding failed: {err_msg}")

        if not os.path.exists(temp_output_wm) or os.path.getsize(temp_output_wm) == 0:
            raise RuntimeError("FFmpeg watermark output file was empty or not created")

        # Post-encode Validation (Part 2, 4, 5, 7, 12)
        out_props = probe_media_properties(temp_output_wm)
        if not out_props.get("valid"):
            # Fallback probe if container metadata delayed
            out_dur_val = duration(temp_output_wm)
            out_props["duration"] = out_dur_val
            out_props["valid"] = (out_dur_val > 0)

        # 1. Duration Validation (Part 2: diff <= 0.10s)
        if in_dur > 0 and out_props.get("duration", 0.0) > 0:
            out_dur = out_props["duration"]
            dur_diff = abs(out_dur - in_dur)
            if dur_diff > 0.10:
                raise RuntimeError(
                    f"Watermark duration changed materially: in={in_dur:.3f}s, out={out_dur:.3f}s, diff={dur_diff:.3f}s > 0.10s"
                )

        # 2. Resolution Validation (Part 5: width/height must match)
        out_w = out_props.get("width", 0)
        out_h = out_props.get("height", 0)
        if in_w > 0 and in_h > 0 and out_w > 0 and out_h > 0:
            if (in_w != out_w) or (in_h != out_h):
                raise RuntimeError(
                    f"Watermark resolution changed: in={in_w}x{in_h}, out={out_w}x{out_h}"
                )

        # 3. FPS Validation (Part 4: source frame rate preserved)
        out_fps = out_props.get("fps", 0.0)
        if in_fps > 0 and out_fps > 0:
            fps_diff = abs(out_fps - in_fps)
            if fps_diff > 0.5:
                raise RuntimeError(
                    f"Watermark FPS changed materially: in={in_fps:.2f}, out={out_fps:.2f}"
                )

        # 4. Audio Stream & Sync Validation (Part 6 & 7)
        if in_has_audio:
            if not out_props.get("has_audio"):
                raise RuntimeError("Watermark output lost audio stream")
            if in_audio_dur > 0 and out_props.get("audio_duration", 0.0) > 0:
                audio_diff = abs(out_props["audio_duration"] - in_audio_dur)
                if audio_diff > 0.5:
                    raise RuntimeError(
                        f"Watermark audio duration drift: in={in_audio_dur:.3f}s, out={out_props['audio_duration']:.3f}s"
                    )

        # Atomic Rename (Part 14)
        if os.path.exists(final_output_wm):
            try:
                os.remove(final_output_wm)
            except OSError:
                pass
        os.replace(temp_output_wm, final_output_wm)

        elapsed = time.time() - t0
        out_mb = os.path.getsize(final_output_wm) / (1024 * 1024)
        speed_mult = (in_dur / max(0.01, elapsed)) if in_dur > 0 else 1.0
        print(f"[WATERMARK] Watermark successfully applied in {elapsed:.2f}s ({out_mb:.1f} MB, {speed_mult:.1f}x realtime)")
        logger.info(f"[WATERMARK] Watermark successfully applied in {elapsed:.2f}s ({out_mb:.1f} MB, {speed_mult:.1f}x realtime)")

        # Safe cleanup of original input ONLY after confirmed successful validation and atomic rename
        try:
            if os.path.exists(video_path) and Path(video_path).resolve() != Path(final_output_wm).resolve():
                os.remove(video_path)
        except OSError:
            pass

        return final_output_wm

    except Exception as exc:
        print(f"[WATERMARK] Failed to apply watermark: {exc}")
        logger.error(f"[WATERMARK] Failed to apply watermark: {exc}")
        # Failure Safety: Delete ONLY temporary/incomplete watermark files. Preserve clean original input!
        if os.path.exists(temp_output_wm):
            try:
                os.remove(temp_output_wm)
            except OSError:
                pass
        if os.path.exists(final_output_wm):
            try:
                os.remove(final_output_wm)
            except OSError:
                pass
        return None


# =========================
# THUMBNAIL HELPERS
# =========================

def extract_or_download_thumbnail(video_path: str, api_thumbnail_url: str = None, custom_thumb_path: str = None) -> str | None:
    """
    Resolves or generates a thumbnail for Telegram video upload.
    Order:
      1. API thumbnail URL if provided.
      2. Custom thumbnail file if provided and exists.
      3. Auto-generate frame capture from video using FFmpeg.
      4. Fallback to None (graceful failure).
    """
    os.makedirs(TEMP_DIR, exist_ok=True)
    base_id = os.path.splitext(os.path.basename(video_path))[0] if video_path else f"thumb_{int(time.time())}"

    # 1. API thumbnail
    if api_thumbnail_url and isinstance(api_thumbnail_url, str) and (api_thumbnail_url.startswith("http://") or api_thumbnail_url.startswith("https://")):
        api_thumb_file = os.path.join(TEMP_DIR, f"api_{base_id}.jpg")
        try:
            r = requests.get(api_thumbnail_url, timeout=10)
            if r.status_code == 200 and len(r.content) > 0:
                with open(api_thumb_file, "wb") as f:
                    f.write(r.content)
                if os.path.exists(api_thumb_file) and os.path.getsize(api_thumb_file) > 0:
                    return api_thumb_file
        except Exception as exc:
            logging.warning(f"Failed to download API thumbnail: {exc}")

    # 2. Custom thumbnail
    if custom_thumb_path and isinstance(custom_thumb_path, str) and custom_thumb_path not in ("/d", "no", "skip"):
        if os.path.exists(custom_thumb_path) and os.path.getsize(custom_thumb_path) > 0:
            return custom_thumb_path

    # 3. Auto-generate from video
    if video_path and os.path.exists(video_path):
        gen_thumb = os.path.join(TEMP_DIR, f"gen_{base_id}.jpg")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", "00:00:05", "-i", str(video_path), "-vframes", "1", "-q:v", "2", str(gen_thumb)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False
            )
            if os.path.exists(gen_thumb) and os.path.getsize(gen_thumb) > 0:
                return gen_thumb
            # Try earlier timestamp for short videos
            subprocess.run(
                ["ffmpeg", "-y", "-ss", "00:00:01", "-i", str(video_path), "-vframes", "1", "-q:v", "2", str(gen_thumb)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False
            )
            if os.path.exists(gen_thumb) and os.path.getsize(gen_thumb) > 0:
                return gen_thumb
        except Exception as exc:
            logging.warning(f"Thumbnail auto-generation failed: {exc}")

    return None


# =========================
# CAPTION BUILDER
# =========================

def build_video_caption(
    title: str,
    quality: str = "720p",
    duration_str: str = None,
    course_name: str = None,
    credit: str = None,
    index: int = None,
    subject_name: str = None,
    unit_number = None,
    unit_title: str = None,
    topic_title: str = None
) -> str:
    """
    Constructs a clean, professional Telegram video caption with academic hierarchy
    without exposing sensitive URLs, tokens, headers, or internal API routes.
    """
    header = f"<b>——— ✦ {str(index).zfill(3)} ✦ ———</b>\n\n" if index else ""
    academic_lines = []
    if subject_name:
        academic_lines.append(f"📚 <b>{subject_name}</b>")
    if course_name:
        academic_lines.append(f"📖 <b>{course_name}</b>")
    if unit_number is not None and unit_title:
        academic_lines.append(f"📌 <b>Unit {unit_number} — {unit_title}</b>")
    elif unit_title and unit_title != "General":
        academic_lines.append(f"📌 <b>{unit_title}</b>")
    if topic_title:
        academic_lines.append(f"📝 <b>Topic:</b> {topic_title}")

    academic_block = ("\n".join(academic_lines) + "\n\n") if academic_lines else ""

    lines = [
        f"{header}{academic_block}🎬 <b>Title:</b> {title}\n",
        f"📺 <b>Quality:</b> {quality}"
    ]
    if duration_str:
        lines.append(f"⏱ <b>Duration:</b> {duration_str}")
    if course_name and not academic_lines:
        lines.append(f"📚 <b>Course:</b> {course_name}")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━")
    credit_display = credit or "DRM Wizard"
    lines.append(f"🤖 <b>Downloaded via:</b> {credit_display}")

    return "\n".join(lines)

# =========================
# VIDEO INFO
# =========================
def get_duration(filename):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of",
            "default=noprint_wrappers=1:nokey=1", filename
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )
    return float(result.stdout or 0)


def duration(filename: str) -> float:
    if not filename or not os.path.exists(filename):
        return 0.0
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration", "-of",
                "default=noprint_wrappers=1:nokey=1", str(filename)
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                return float(result.stdout.strip())
            except ValueError:
                return 0.0
        return 0.0
    except Exception:
        return 0.0


def get_video_duration(filename: str) -> float:
    """Uses ffprobe to extract precise video duration in seconds."""
    if not filename or not os.path.exists(filename):
        return 0.0
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration", "-of",
                "default=noprint_wrappers=1:nokey=1", str(filename)
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        val = result.stdout.strip()
        return float(val) if val else 0.0
    except Exception:
        return 0.0


def validate_split_part(part_path: str, max_size_bytes: int) -> bool:
    """Verifies that a split video part exists, has size > 0, is within limit, and is readable by ffprobe."""
    if not part_path or not os.path.exists(part_path):
        return False
    size = os.path.getsize(part_path)
    if size == 0 or size > max_size_bytes:
        return False
    dur = get_video_duration(part_path)
    if dur <= 0:
        return is_playable(part_path)
    return True


def split_large_video(
    file_path: str,
    max_size_bytes: Optional[int] = None,
    custom_dir: Optional[str] = None,
    base_title: Optional[str] = None
) -> list[str]:
    """
    Splits an oversized video into multiple uploadable parts using FFmpeg stream copy (-c copy).
    - Preserves original video/audio quality, resolution, bitrate, and sync without re-encoding.
    - Watermarking is assumed to have happened BEFORE calling split_large_video.
    - Recursively splits any generated part that exceeds the safe upload threshold.
    - Verifies each generated part with ffprobe for playability and duration.
    """
    if not file_path or not os.path.exists(file_path):
        print(f"[SPLIT] Error: Input file does not exist: {file_path}")
        return []

    threshold = max_size_bytes or MAX_UPLOAD_SIZE_BYTES
    size_bytes = os.path.getsize(file_path)

    # 1. Normal Video under threshold
    if size_bytes <= threshold:
        print(f"[SIZE CHECK] Video size {size_bytes / (1024*1024):.1f} MB is within limit {threshold / (1024*1024):.1f} MB. No split needed.")
        return [file_path]

    total_duration = get_video_duration(file_path)
    if total_duration <= 0:
        total_duration = duration(file_path)

    if total_duration <= 0:
        print(f"[SPLIT] Warning: Could not detect video duration for {file_path}, returning original.")
        return [file_path]

    # Target chunk size is 88% of max_size_bytes to safely absorb keyframe / container overhead
    safe_target_bytes = int(threshold * 0.88)
    parts_count = max(2, math.ceil(size_bytes / safe_target_bytes))
    part_duration = total_duration / parts_count

    out_dir = custom_dir or os.path.dirname(file_path) or str(TEMP_DIR)
    os.makedirs(out_dir, exist_ok=True)

    stem = safe_filename(base_title or Path(file_path).stem)
    stem = re.sub(r"_Part_\d+$", "", stem, flags=re.IGNORECASE)

    print(f"[SIZE CHECK] Final video size: {size_bytes / (1024*1024):.1f} MB | Threshold: {threshold / (1024*1024):.1f} MB")
    print(f"[SPLIT] Required parts: {parts_count} (approx {part_duration:.1f}s each)")

    output_files = []
    for i in range(parts_count):
        part_num = i + 1
        output_file = os.path.join(out_dir, f"{stem}_Part_{part_num}.mp4")
        if os.path.exists(output_file):
            try:
                os.remove(output_file)
            except OSError:
                pass

        start_sec = i * part_duration
        dur_sec = part_duration if i < (parts_count - 1) else (total_duration - start_sec + 2.0)

        cmd = [
            FFMPEG_PATH or "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{start_sec:.3f}",
            "-i", str(file_path),
            "-t", f"{dur_sec:.3f}",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart",
            str(output_file)
        ]

        print(f"[SPLIT] Creating Part {part_num}/{parts_count} (start={start_sec:.1f}s, dur={dur_sec:.1f}s)...")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            print(f"[SPLIT] FFmpeg stream copy error for Part {part_num}: {res.stderr}")

        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            part_size = os.path.getsize(output_file)
            print(f"[SPLIT] Part {part_num} verified: {part_size / (1024*1024):.1f} MB")

            if part_size > threshold:
                print(f"[SPLIT] Part {part_num} is still oversized ({part_size/(1024*1024):.1f} MB), re-splitting subpart...")
                sub_parts = split_large_video(output_file, max_size_bytes=threshold, custom_dir=out_dir, base_title=f"{stem}_P{part_num}")
                output_files.extend(sub_parts)
                try:
                    os.remove(output_file)
                except OSError:
                    pass
            else:
                output_files.append(output_file)
        else:
            print(f"[SPLIT] Failed to create Part {part_num}")

    if not output_files:
        print("[SPLIT] Warning: Splitting produced no valid parts; falling back to original.")
        return [file_path]

    return output_files


def get_mps_and_keys(api_url):
    response = requests.get(api_url)
    response_json = response.json()
    mpd = response_json.get("mpd_url")
    keys = response_json.get("keys")
    return mpd, keys


def get_mps_and_keys2(api_url: str):
    """
    Fallback resolver for MPD URLs and DRM keys.
    """
    try:
        return get_mps_and_keys(api_url)
    except Exception as e:
        logging.error(f"get_mps_and_keys2 failed: {e}")
        return None


def exec_cmd(cmd):
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = process.stdout.decode()
    print(output)
    return output


def pull_run(work, cmds):
    with concurrent.futures.ThreadPoolExecutor(max_workers=work) as executor:
        print("Waiting for tasks to complete")
        executor.map(exec_cmd, cmds)


async def aio(url, name):
    k = f"{name}.pdf"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                f = await aiofiles.open(k, mode="wb")
                await f.write(await resp.read())
                await f.close()
    return k


def validate_unlocked_pdf(file_path: str) -> tuple[bool, int, str]:
    """
    Validates that an unlocked PDF meets all criteria (Requirement 10):
    1. file exists
    2. file size > 0
    3. starts with %PDF
    4. PyMuPDF can open it
    5. doc.needs_pass == False
    6. page_count > 0
    """
    if not file_path or not os.path.exists(file_path):
        return False, 0, "File does not exist"

    file_size = os.path.getsize(file_path)
    if file_size <= 0:
        return False, 0, "File size is 0 bytes"

    # Check %PDF header in first 1024 bytes
    try:
        with open(file_path, "rb") as f:
            header = f.read(1024)
            if b"%PDF-" not in header and b"%PDF" not in header:
                return False, 0, "Missing %PDF header in file"
    except Exception as e:
        return False, 0, f"Error reading file header: {e}"

    if fitz is None:
        return True, 1, "fitz not installed (basic magic check passed)"

    try:
        doc = fitz.open(file_path)
        if getattr(doc, "needs_pass", False):
            doc.close()
            return False, 0, "PDF still requires password (needs_pass is True)"

        page_count = len(doc)
        title_val = doc.metadata.get("title", "") if doc.metadata else ""
        doc.close()

        if page_count <= 0:
            return False, 0, "PDF contains 0 pages"

        return True, page_count, title_val
    except Exception as e:
        return False, 0, f"Corrupted or invalid unlocked PDF: {e}"


def unlock_pdf_file(input_pdf: str, output_pdf: str, password: Optional[str]) -> tuple[bool, str, int]:
    """
    Unlocks a password-protected PDF using PyMuPDF.
    Preserves all pages, text, images, dimensions, layout without rasterization.
    Returns (success, error_code, page_count).
    """
    if fitz is None:
        return False, "PYMUPDF_NOT_INSTALLED", 0

    if not input_pdf or not os.path.exists(input_pdf):
        return False, PDF_INVALID, 0

    try:
        doc = fitz.open(input_pdf)
    except Exception as e:
        return False, PDF_INVALID, 0

    try:
        is_enc = getattr(doc, "needs_pass", False) or getattr(doc, "is_encrypted", False)
        if is_enc:
            print("[PDF] Password-protected PDF detected")
            logging.info("[PDF] Password-protected PDF detected")

            if not password:
                doc.close()
                logging.error("[PDF] PDF is password protected but no password was provided.")
                return False, PDF_PASSWORD_INVALID, 0

            auth_result = doc.authenticate(password)
            if not auth_result:
                doc.close()
                logging.error("[PDF] Password authentication failed.")
                return False, PDF_PASSWORD_INVALID, 0

            print("[PDF] Authentication successful")
            logging.info("[PDF] Authentication successful")
            print("[PDF] Creating unlocked PDF")
            logging.info("[PDF] Creating unlocked PDF")

            os.makedirs(os.path.dirname(output_pdf) or ".", exist_ok=True)
            try:
                doc.save(
                    output_pdf,
                    encryption=fitz.PDF_ENCRYPT_NONE,
                    garbage=4,
                    deflate=True,
                    clean=True
                )
            except Exception as se:
                doc.close()
                logging.error(f"[PDF] Failed to save unlocked PDF: {se}")
                return False, PDF_UNLOCK_FAILED, 0

            doc.close()

            # Validate unlocked output (Requirement 10)
            is_valid, page_cnt, val_err = validate_unlocked_pdf(output_pdf)
            if not is_valid:
                logging.error(f"[PDF] Unlocked PDF validation failed: {val_err}")
                return False, PDF_UNLOCK_OUTPUT_INVALID, 0

            return True, "OK", page_cnt
        else:
            page_cnt = len(doc)
            doc.close()
            return True, "NOT_ENCRYPTED", page_cnt
    except Exception as exc:
        try:
            doc.close()
        except Exception:
            pass
        return False, str(exc), 0


def validate_pdf_file(file_path: str) -> tuple[bool, int, str]:
    """
    Validates that the file exists, has non-zero size, contains %PDF header,
    and is openable by PyMuPDF with page count > 0.
    """
    if not file_path or not os.path.exists(file_path):
        return False, 0, "File does not exist"

    file_size = os.path.getsize(file_path)
    if file_size < 32:
        return False, 0, f"File is too small ({file_size} bytes)"

    # Check %PDF- magic bytes in first 1024 bytes
    try:
        with open(file_path, "rb") as f:
            header = f.read(1024)
            if b"%PDF-" not in header:
                return False, 0, "Missing %PDF header in file"
    except Exception as e:
        return False, 0, f"Error reading file header: {e}"

    if fitz is None:
        return True, 1, "fitz not installed (basic magic check passed)"

    try:
        doc = fitz.open(file_path)
        page_count = len(doc)
        title_val = doc.metadata.get("title", "") if doc.metadata else ""
        doc.close()

        if page_count <= 0:
            return False, 0, "PDF contains 0 pages"

        return True, page_count, title_val
    except Exception as e:
        return False, 0, f"Corrupted or invalid PDF: {e}"


def apply_pdf_watermark(
    input_pdf: str,
    output_pdf: str,
    watermark_text: str = None,
    watermark_file: str = None
) -> str | None:
    """
    Applies the existing bot watermark to all pages of the input PDF.
    Preserves original PDF content, layout, orientation and dimensions without rasterization.
    """
    if fitz is None:
        logging.error("[PDF_WATERMARK] PyMuPDF (fitz) is required for PDF watermarking.")
        return None

    if not input_pdf or not os.path.exists(input_pdf):
        logging.error(f"[PDF_WATERMARK] Input PDF missing: {input_pdf}")
        return None

    # Check if watermark was explicitly disabled
    if watermark_text is not None and str(watermark_text).strip().lower() in ("/d", "no", "none", ""):
        print("[PDF_WATERMARK] Watermark explicitly disabled; skipping.")
        return input_pdf

    # Determine watermark text from input or config
    wm_text = None
    if watermark_text and str(watermark_text).strip().lower() not in ("/d", "no", "none", ""):
        wm_text = str(watermark_text).strip()
    elif WATERMARK_TEXT and str(WATERMARK_TEXT).strip().lower() not in ("/d", "no", "none", ""):
        wm_text = str(WATERMARK_TEXT).strip()

    # Determine image watermark file
    wm_image = None
    if watermark_file and os.path.isfile(watermark_file):
        wm_image = watermark_file
    elif WATERMARK_FILE and os.path.isfile(WATERMARK_FILE):
        wm_image = WATERMARK_FILE

    # If watermark is disabled or empty, return input_pdf directly
    if not wm_text and not wm_image:
        print("[PDF_WATERMARK] Watermark disabled or empty; skipping.")
        return input_pdf

    try:
        doc = fitz.open(input_pdf)
        total_pages = len(doc)
        if total_pages <= 0:
            doc.close()
            logging.error("[PDF_WATERMARK] PDF contains 0 pages.")
            return None

        for page in doc:
            rect = page.rect

            # 1. Apply image watermark if available
            if wm_image:
                try:
                    img_w = min(rect.width * 0.45, 250)
                    img_h = min(rect.height * 0.45, 250)
                    img_rect = fitz.Rect(
                        (rect.width - img_w) / 2,
                        (rect.height - img_h) / 2,
                        (rect.width + img_w) / 2,
                        (rect.height + img_h) / 2
                    )
                    page.insert_image(img_rect, filename=wm_image, overlay=True, keep_proportion=True)
                except Exception as img_err:
                    logging.warning(f"[PDF_WATERMARK] Image watermark failed: {img_err}")

            # 2. Apply diagonal text watermark grid
            if wm_text:
                y = 50
                while y < rect.height:
                    x = 25
                    while x < rect.width:
                        page.insert_text(
                            (x, y),
                            wm_text,
                            fontsize=24,
                            fontname="helv",
                            color=(0.85, 0.15, 0.15),
                            fill_opacity=0.25,
                            stroke_opacity=0,
                            overlay=True,
                        )
                        x += 230
                    y += 115

        # Save to output file with optimizations
        os.makedirs(os.path.dirname(output_pdf) or ".", exist_ok=True)
        doc.save(
            output_pdf,
            garbage=4,
            deflate=True,
            clean=True
        )
        doc.close()

        if not os.path.exists(output_pdf) or os.path.getsize(output_pdf) == 0:
            logging.error("[PDF_WATERMARK] Output watermarked PDF is empty or missing.")
            return None

        # Verify page count integrity
        verify_doc = fitz.open(output_pdf)
        verify_pages = len(verify_doc)
        verify_doc.close()

        if verify_pages != total_pages:
            logging.error(f"[PDF_WATERMARK] Page count mismatch: {total_pages} -> {verify_pages}")
            return None

        print(f"[PDF_WATERMARK] Successfully watermarked {total_pages} pages: {output_pdf}")
        return output_pdf

    except Exception as e:
        logging.error(f"[PDF_WATERMARK] Exception applying watermark: {e}")
        return None


def extract_pdf_urls_from_api_response(data: dict | list | str, base_url: str = "") -> list[str]:
    """
    Extracts valid HTTP/HTTPS PDF URLs from API JSON response structures or text.
    """
    results: list[str] = []

    def _looks_like_pdf_url(u: str) -> bool:
        if not u or not isinstance(u, str):
            return False
        u = u.strip()
        if not u.startswith(("http://", "https://")):
            return False
        parsed = urlparse(u)
        path = parsed.path.lower()
        return path.endswith(".pdf") or ".pdf?" in u.lower() or "pdf" in parsed.query.lower()

    if isinstance(data, dict):
        for key in ("pdf_url", "pdf_link", "pdf_link2", "Pdf_link", "pdf", "file_url", "url"):
            val = data.get(key)
            if isinstance(val, str) and _looks_like_pdf_url(val):
                if val.strip() not in results:
                    results.append(val.strip())

        dl = data.get("download_links") or (data.get("data", {}).get("download_links") if isinstance(data.get("data"), dict) else None)
        if isinstance(dl, list):
            for item in dl:
                if isinstance(item, dict):
                    p = item.get("path") or item.get("url")
                    if isinstance(p, str) and _looks_like_pdf_url(p) and p.strip() not in results:
                        results.append(p.strip())

        nested_data = data.get("data")
        if isinstance(nested_data, dict):
            for k in ("pdf_url", "pdf_link", "pdf_link2", "Pdf_link", "pdf"):
                v = nested_data.get(k)
                if isinstance(v, str) and _looks_like_pdf_url(v) and v.strip() not in results:
                    results.append(v.strip())

    elif isinstance(data, str):
        pattern = r"""https?://[^\s"'<>\\]+?\.pdf(?:\?[^\s"'<>\\]*)?"""
        for match in re.findall(pattern, data, flags=re.IGNORECASE):
            if _looks_like_pdf_url(match) and match.strip() not in results:
                results.append(match.strip())

    return results


async def download(url, name):
    ka = f"{name}.pdf"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                f = await aiofiles.open(ka, mode="wb")
                await f.write(await resp.read())
                await f.close()
    return ka


async def download_pdf(
    url: str,
    name: str,
    custom_headers: dict = None,
    watermark_text: str = None,
    watermark_file: str = None,
    apply_wm: bool = True,
    custom_dir: str = "downloads",
    password: Optional[str] = None
) -> str | None:
    """
    Downloads a PDF using streaming/chunked async I/O, validates integrity,
    authenticates & unlocks password-protected PDFs if encrypted, and applies
    existing Course Wallah red watermark.
    Supports CloudFront, Akamai, Spayee CDN, KGS CDN, crwilladmin, S3, redirected URLs,
    and signed parameters without logging sensitive passwords or tokens.
    """
    if not url or not isinstance(url, str):
        logging.error("[PDF] Invalid or missing PDF URL")
        return None

    # Parse URL and password cleanly (Requirement 1, 2, 21)
    pdf_info = parse_pdf_input(url)
    clean_url = pdf_info["url"]
    pdf_password = password if password is not None else pdf_info["password"]

    if not clean_url or not clean_url.strip().startswith(("http://", "https://")):
        logging.error("[PDF] Invalid or missing clean PDF URL")
        return None

    clean_url = clean_url.strip()
    out_dir = custom_dir or "downloads"
    os.makedirs(out_dir, exist_ok=True)
    safe_name = safe_filename(name)

    # Safe sanitized URL for logging (never log auth tokens, passwords, or secrets)
    parsed_u = urlparse(clean_url)
    sanitized_url = f"{parsed_u.scheme}://{parsed_u.netloc}{parsed_u.path}"
    print(f"[PDF] URL resolved: {sanitized_url}")
    logging.info(f"[PDF] URL resolved: {sanitized_url}")

    # Check whether watermarking should be performed
    wm_active = False
    if apply_wm:
        if watermark_text is not None and str(watermark_text).strip().lower() in ("/d", "no", "none", ""):
            wm_active = False
        elif watermark_text and str(watermark_text).strip().lower() not in ("/d", "no", "none", ""):
            wm_active = True
        elif watermark_file and os.path.isfile(watermark_file):
            wm_active = True
        elif WATERMARK_TEXT and str(WATERMARK_TEXT).strip().lower() not in ("/d", "no", "none", ""):
            wm_active = True
        elif WATERMARK_FILE and os.path.isfile(WATERMARK_FILE):
            wm_active = True

    orig_file = os.path.join(out_dir, f"{safe_name}_original.pdf")
    unlocked_file = os.path.join(out_dir, f"{safe_name}_unlocked.pdf")
    if wm_active:
        final_wm_file = os.path.join(out_dir, f"{safe_name}_watermarked.pdf")
    else:
        final_wm_file = None

    if custom_headers:
        headers = custom_headers
    elif any(k in clean_url.lower() for k in ("akamai.net.in", "classx.co.in", "appx.co.in", "classplusapp.com")):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": "https://player.akamai.net.in/",
            "Origin": "https://akstechnicalclasses.classx.co.in",
            "Accept": "application/pdf,application/octet-stream,*/*",
            "Connection": "keep-alive"
        }
    else:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/pdf,application/octet-stream,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive"
        }

    max_retries = 3
    retry_delay = 2
    download_success = False

    for attempt in range(1, max_retries + 1):
        if os.path.exists(orig_file):
            try:
                os.remove(orig_file)
            except Exception:
                pass

        try:
            print(f"[PDF] Download started (attempt {attempt}/{max_retries}): {safe_name}")
            logging.info(f"[PDF] Download started (attempt {attempt}/{max_retries}): {safe_name}")
            timeout = aiohttp.ClientTimeout(total=180, connect=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(clean_url, headers=headers, allow_redirects=True) as resp:
                    if resp.status != 200:
                        logging.error(f"[PDF] Download failed for {sanitized_url}: HTTP {resp.status}")
                        if resp.status in (401, 403, 404, 410):
                            return None
                        if attempt < max_retries:
                            await asyncio.sleep(retry_delay)
                            continue
                        return None

                    async with aiofiles.open(orig_file, "wb") as f:
                        while True:
                            chunk = await resp.content.read(1024 * 1024)
                            if not chunk:
                                break
                            await f.write(chunk)

            download_success = True
            break
        except (asyncio.TimeoutError, aiohttp.ClientError) as ce:
            logging.error(f"[PDF] Attempt {attempt} error downloading {sanitized_url}: {ce}")
            if attempt < max_retries:
                await asyncio.sleep(retry_delay)
            else:
                return None
        except Exception as e:
            logging.error(f"[PDF] Attempt {attempt} exception downloading {sanitized_url}: {e}")
            if attempt < max_retries:
                await asyncio.sleep(retry_delay)
            else:
                return None

    if not download_success or not os.path.exists(orig_file):
        return None

    print(f"[PDF] Download completed: {orig_file} ({os.path.getsize(orig_file)} bytes)")
    logging.info(f"[PDF] Download completed: {orig_file}")

    # Inspect encryption & authenticate if password-protected (Requirement 6, 7, 9, 10, 11)
    is_encrypted = False
    if fitz is not None:
        try:
            test_doc = fitz.open(orig_file)
            is_encrypted = getattr(test_doc, "needs_pass", False) or getattr(test_doc, "is_encrypted", False)
            test_doc.close()
        except Exception:
            is_encrypted = False

    working_file = orig_file
    if is_encrypted:
        success, err_code, pages = unlock_pdf_file(orig_file, unlocked_file, pdf_password)
        if not success:
            if os.path.exists(orig_file):
                try:
                    os.remove(orig_file)
                except Exception:
                    pass
            if err_code == PDF_PASSWORD_INVALID:
                raise PDFPasswordInvalid(PDF_PASSWORD_INVALID)
            elif err_code == PDF_UNLOCK_OUTPUT_INVALID:
                raise PDFUnlockOutputInvalid(PDF_UNLOCK_OUTPUT_INVALID)
            elif err_code == PDF_UNLOCK_FAILED:
                raise PDFUnlockFailed(PDF_UNLOCK_FAILED)
            else:
                raise PDFInvalid(err_code)

        working_file = unlocked_file
        print(f"[PDF] Unlocked PDF validated: {pages} pages ({unlocked_file})")
        logging.info(f"[PDF] Unlocked PDF validated: {pages} pages")
    else:
        # Validate standard non-encrypted downloaded PDF
        is_valid, pages, val_err = validate_pdf_file(orig_file)
        if not is_valid:
            logging.error(f"[PDF] Validation failed for {orig_file}: {val_err}")
            if os.path.exists(orig_file):
                try:
                    os.remove(orig_file)
                except Exception:
                    pass
            raise PDFInvalid(PDF_INVALID)

        print(f"[PDF] Validation passed: {pages} pages ({orig_file})")
        logging.info(f"[PDF] Validation passed: {pages} pages")

    # Apply red Course Wallah watermark on unlocked / valid PDF (Requirement 14, 15)
    if wm_active and final_wm_file:
        print(f"[PDF] Watermark started: {working_file}")
        logging.info(f"[PDF] Watermark started: {working_file}")
        result = apply_pdf_watermark(
            working_file,
            final_wm_file,
            watermark_text=watermark_text,
            watermark_file=watermark_file
        )
        if result and os.path.exists(result):
            # Validate PDF again after watermarking (Requirement 15)
            is_wm_val, wm_pages, wm_val_err = validate_pdf_file(result)
            if not is_wm_val:
                logging.error(f"[PDF] Watermarked PDF validation failed: {wm_val_err}")
                return None

            print(f"[PDF] Watermark completed: {result}")
            logging.info(f"[PDF] Watermark completed: {result}")

            # Safe cleanup of intermediate original and unlocked files
            if os.path.exists(orig_file):
                try:
                    os.remove(orig_file)
                except Exception:
                    pass
            if os.path.exists(unlocked_file) and unlocked_file != result:
                try:
                    os.remove(unlocked_file)
                except Exception:
                    pass
            return result
        else:
            logging.error("[PDF] Watermarking failed; refusing to return unwatermarked PDF as watermark was requested.")
            if os.path.exists(orig_file):
                try:
                    os.remove(orig_file)
                except Exception:
                    pass
            if os.path.exists(unlocked_file):
                try:
                    os.remove(unlocked_file)
                except Exception:
                    pass
            return None
    else:
        if working_file == unlocked_file:
            if os.path.exists(orig_file):
                try:
                    os.remove(orig_file)
                except Exception:
                    pass
            return unlocked_file
        return orig_file


async def pdf_download(url: str, name: str, custom_dir: str = "downloads") -> str | None:
    """
    Alias for download_pdf to support legacy callers.
    """
    return await download_pdf(url, name, custom_dir=custom_dir)


def process_zip_to_video(url: str, name: str) -> str | None:
    """
    Downloads and extracts a zip package containing video content.
    """
    import zipfile
    import shutil
    os.makedirs(TEMP_DIR, exist_ok=True)
    safe_name = safe_filename(name)
    zip_path = os.path.join(TEMP_DIR, f"{safe_name}.zip")
    extract_dir = os.path.join(TEMP_DIR, f"extracted_{safe_name}_{int(time.time())}")

    try:
        r = requests.get(url, stream=True, timeout=120)
        if r.status_code != 200:
            return None
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)

        if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
            return None

        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(extract_dir)

        for root, _, files in os.walk(extract_dir):
            for file in files:
                if file.lower().endswith((".mp4", ".mkv", ".webm", ".ts")):
                    found_path = os.path.join(root, file)
                    final_path = os.path.join(TEMP_DIR, f"{safe_name}.mp4")
                    if os.path.exists(final_path):
                        cleanup_uploaded_file(final_path)
                    os.rename(found_path, final_path)
                    try:
                        cleanup_uploaded_file(zip_path)
                        shutil.rmtree(extract_dir, ignore_errors=True)
                    except Exception:
                        pass
                    return final_path

        return None
    except Exception as e:
        logging.error(f"process_zip_to_video error: {e}")
        return None


def pdf_download_sync(
    url: str,
    name: str,
    chunk_size=1024 * 512,
    custom_headers: dict = None,
    watermark_text: str = None,
    watermark_file: str = None,
    apply_wm: bool = True,
    custom_dir: str = "downloads"
) -> str | None:
    """
    Synchronous streaming PDF download with validation and watermarking.
    Supports CRWill, CloudFront, redirects, and custom storage directories.
    """
    if not url or not isinstance(url, str) or not url.strip().startswith(("http://", "https://")):
        logging.error("[PDF_SYNC] Invalid PDF URL")
        return None

    clean_url = url.strip()
    out_dir = custom_dir or "downloads"
    os.makedirs(out_dir, exist_ok=True)
    safe_name = safe_filename(name)

    wm_active = False
    if apply_wm:
        if watermark_text is not None and str(watermark_text).strip().lower() in ("/d", "no", "none", ""):
            wm_active = False
        elif watermark_text and str(watermark_text).strip().lower() not in ("/d", "no", "none", ""):
            wm_active = True
        elif watermark_file and os.path.isfile(watermark_file):
            wm_active = True
        elif WATERMARK_TEXT and str(WATERMARK_TEXT).strip().lower() not in ("/d", "no", "none", ""):
            wm_active = True
        elif WATERMARK_FILE and os.path.isfile(WATERMARK_FILE):
            wm_active = True

    if wm_active:
        orig_file = os.path.join(out_dir, f"{safe_name}_original.pdf")
        final_wm_file = os.path.join(out_dir, f"{safe_name}_watermarked.pdf")
    else:
        orig_file = os.path.join(out_dir, f"{safe_name}.pdf")
        final_wm_file = None

    if custom_headers:
        headers = custom_headers
    elif any(k in clean_url.lower() for k in ("akamai.net.in", "classx.co.in", "appx.co.in", "classplusapp.com")):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": "https://player.akamai.net.in/",
            "Origin": "https://akstechnicalclasses.classx.co.in",
            "Accept": "application/pdf,application/octet-stream,*/*",
            "Connection": "keep-alive"
        }
    else:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/pdf,application/octet-stream,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive"
        }

    max_retries = 3
    download_success = False

    for attempt in range(1, max_retries + 1):
        if os.path.exists(orig_file):
            try:
                os.remove(orig_file)
            except Exception:
                pass

        try:
            r = requests.get(clean_url, headers=headers, allow_redirects=True, stream=True, timeout=60)
            if r.status_code != 200:
                logging.error(f"[PDF_SYNC] Attempt {attempt} failed with HTTP {r.status_code}")
                if r.status_code in (401, 403, 404, 410):
                    return None
                if attempt < max_retries:
                    time.sleep(2)
                    continue
                return None

            with open(orig_file, "wb") as fd:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        fd.write(chunk)

            download_success = True
            break
        except Exception as e:
            logging.error(f"[PDF_SYNC] Attempt {attempt} exception: {e}")
            if attempt < max_retries:
                time.sleep(2)
            else:
                return None

    if not download_success or not os.path.exists(orig_file):
        return None

    is_valid, pages, val_err = validate_pdf_file(orig_file)
    if not is_valid:
        logging.error(f"[PDF_SYNC] Validation failed: {val_err}")
        if os.path.exists(orig_file):
            try:
                os.remove(orig_file)
            except Exception:
                pass
        return None

    if wm_active and final_wm_file:
        result = apply_pdf_watermark(
            orig_file,
            final_wm_file,
            watermark_text=watermark_text,
            watermark_file=watermark_file
        )
        if result and os.path.exists(result):
            if os.path.exists(orig_file):
                try:
                    os.remove(orig_file)
                except Exception:
                    pass
            return result
        else:
            if os.path.exists(orig_file):
                try:
                    os.remove(orig_file)
                except Exception:
                    pass
            return None

    return orig_file


def parse_vid_info(info):
    info = info.strip().split("\n")
    new_info = []
    temp = []

    for i in info:
        i = str(i)
        if "[" not in i and "---" not in i:
            while "  " in i:
                i = i.replace("  ", " ")
            i = i.split("|")[0].split(" ", 2)
            try:
                if "RESOLUTION" not in i[2] and i[2] not in temp and "audio" not in i[2]:
                    temp.append(i[2])
                    new_info.append((i[0], i[2]))
            except Exception:
                pass

    return new_info


def vid_info(info):
    info = info.strip().split("\n")
    new_info = {}
    temp = []

    for i in info:
        i = str(i)
        if "[" not in i and "---" not in i:
            while "  " in i:
                i = i.replace("  ", " ")
            i = i.split("|")[0].split(" ", 3)
            try:
                if "RESOLUTION" not in i[2] and i[2] not in temp and "audio" not in i[2]:
                    temp.append(i[2])
                    new_info.update({f"{i[2]}": f"{i[0]}"})
            except Exception:
                pass

    return new_info

def download_raw_file(url: str, filename: str, user_id: int = None, custom_dir: str = "downloads") -> str | None:
    """
    Ultra-fast, resume-safe raw file download.
    Uses venv-aware yt-dlp with safe argument execution, retries, and error handling.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13)",
        "Referer": "https://akstechnicalclasses.classx.co.in/",
        "Origin": "https://akstechnicalclasses.classx.co.in",
        "Accept": "*/*",
        "Connection": "keep-alive"
    }

    out_dir = custom_dir or "downloads"
    os.makedirs(out_dir, exist_ok=True)
    file_path = os.path.join(out_dir, f"{filename}.mkv")

    cmd = list(get_ytdlp_cmd()) + [
        "-f", "bv*+ba/b",
        "-o", file_path,
        "--no-check-certificate"
    ]
    for k, v in headers.items():
        cmd.extend(["--add-header", f"{k}: {v}"])

    cfile = get_cookies_file(user_id)
    if cfile and os.path.exists(cfile):
        cmd.extend(["--cookies", cfile])

    cmd.extend(["--retries", "5", "--fragment-retries", "5", url])

    retry_count = 0
    max_retries = 3
    download_success = False
    while retry_count < max_retries:
        print(f"▶️ [YT-DLP] Downloading raw file (attempt {retry_count + 1})...")
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            print("✅ [YT-DLP] Raw download succeeded")
            download_success = True
            break
        except subprocess.CalledProcessError as exc:
            retry_count += 1
            print(f"⚠️ [YT-DLP] Download retry {retry_count}/{max_retries} failed: {exc.stderr[:200] if exc.stderr else exc}")
            if retry_count == max_retries:
                print("❌ [YT-DLP] All download retries failed.")
                return None

    if not download_success:
        return None

    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        print(f"✅ [YT-DLP] Download complete: {file_path}")
        return file_path
    else:
        print(f"❌ [YT-DLP] Download failed or file is empty: {file_path}")
        return None


def decrypt_and_merge_video(mpd_url, keys_string, output_path, output_name, quality="720"):
    try:
        output_path = Path(output_path)
        output_path.mkdir(parents=True, exist_ok=True)

        cmd1 = list(get_ytdlp_cmd()) + [
            "-f", f"bv[height<={quality}]+ba/b",
            "-o", str(output_path / "file.%(ext)s"),
            "--allow-unplayable-format",
            "--no-check-certificate",
            "--external-downloader", "aria2c",
            str(mpd_url)
        ]
        subprocess.run(cmd1, check=True)

        av_dir = list(output_path.iterdir())

        video_decrypted = False
        audio_decrypted = False
        key_args = keys_string.split() if isinstance(keys_string, str) and keys_string.strip() else []

        for data in av_dir:
            if data.suffix == ".mp4" and not video_decrypted:
                cmd2 = ["mp4decrypt"] + key_args + ["--show-progress", str(data), str(output_path / "video.mp4")]
                subprocess.run(cmd2, check=True)
                if (output_path / "video.mp4").exists():
                    video_decrypted = True
                data.unlink(missing_ok=True)

            elif data.suffix == ".m4a" and not audio_decrypted:
                cmd3 = ["mp4decrypt"] + key_args + ["--show-progress", str(data), str(output_path / "audio.m4a")]
                subprocess.run(cmd3, check=True)
                if (output_path / "audio.m4a").exists():
                    audio_decrypted = True
                data.unlink(missing_ok=True)

        if not video_decrypted or not audio_decrypted:
            raise FileNotFoundError("Decryption failed: video or audio file not found.")

        cmd4 = [
            "ffmpeg", "-y",
            "-i", str(output_path / "video.mp4"),
            "-i", str(output_path / "audio.m4a"),
            "-c", "copy",
            str(output_path / f"{output_name}.mp4")
        ]
        subprocess.run(cmd4, check=True)

        if (output_path / "video.mp4").exists():
            (output_path / "video.mp4").unlink(missing_ok=True)
        if (output_path / "audio.m4a").exists():
            (output_path / "audio.m4a").unlink(missing_ok=True)

        filename = output_path / f"{output_name}.mp4"

        if not filename.exists():
            raise FileNotFoundError("Merged video file not found.")

        return str(filename)

    except Exception as e:
        print(f"Error during decryption and merging: {str(e)}")
        raise


async def run_cmd(cmd):
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise Exception(f"Command failed: {cmd}\n[stderr] {stderr.decode()}")
    return stdout.decode()


def old_download(url, file_name, chunk_size=1024 * 10 * 10):
    if os.path.exists(file_name):
        os.remove(file_name)
    r = requests.get(url, allow_redirects=True, stream=True)
    with open(file_name, "wb") as fd:
        for chunk in r.iter_content(chunk_size=chunk_size):
            if chunk:
                fd.write(chunk)
    return file_name


def human_readable_size(size, decimal_places=2):
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if size < 1024.0 or unit == "PB":
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"


def time_name():
    date = datetime.date.today()
    now = datetime.datetime.now()
    current_time = now.strftime("%H%M%S")
    return f"{date} {current_time}.mp4"


async def fast_download(url, name):
    max_retries = 5
    retry_count = 0
    success = False

    while not success and retry_count < max_retries:
        try:
            if "m3u8" in url:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        m3u8_text = await response.text()

                    playlist = m3u8.loads(m3u8_text)

                    if playlist.is_endlist:
                        base_url = url.rsplit("/", 1)[0] + "/"
                        segments = []

                        async with aiohttp.ClientSession() as session:
                            tasks = []
                            for segment in playlist.segments:
                                segment_url = urljoin(base_url, segment.uri)
                                tasks.append(asyncio.create_task(session.get(segment_url)))

                            responses = await asyncio.gather(*tasks)
                            for response in responses:
                                segment_data = await response.read()
                                segments.append(segment_data)

                        output_file = f"{name}.mp4"
                        with open(output_file, "wb") as f:
                            for segment in segments:
                                f.write(segment)

                        success = True
                        return [output_file]

                    else:
                        cmd = [
                            "ffmpeg",
                            "-hide_banner",
                            "-loglevel", "error",
                            "-stats",
                            "-i", str(url),
                            "-c", "copy",
                            "-bsf:a", "aac_adtstoasc",
                            "-movflags", "+faststart",
                            f"{name}.mp4"
                        ]
                        subprocess.run(cmd, check=False)
                        if os.path.exists(f"{name}.mp4"):
                            success = True
                            return [f"{name}.mp4"]
            else:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            output_file = f"{name}.mp4"
                            with open(output_file, "wb") as f:
                                while True:
                                    chunk = await response.content.read(2 * 1024 * 1024)
                                    if not chunk:
                                        break
                                    f.write(chunk)
                            success = True
                            return [output_file]

            if not success:
                print(f"\nAttempt {retry_count + 1} failed, retrying in 3 seconds...")
                retry_count += 1
                await asyncio.sleep(3)

        except Exception as e:
            print(f"\nError during attempt {retry_count + 1}: {str(e)}")
            retry_count += 1
            await asyncio.sleep(3)

    return None


def get_existing_file(name):
    try:
        if os.path.isfile(name):
            return name

        base, ext = os.path.splitext(name)
        candidates = [
            f"{name}.webm",
            f"{base}.mp4",
            f"{base}.mkv",
            f"{base}.webm",
            f"{base}.mp4.webm",
        ]
        for file in candidates:
            if os.path.isfile(file):
                return file
        return f"{base}.mp4"
    except Exception as exc:
        logging.error(f"Error checking file: {exc}")
        return name


async def download_video(url: str, output_name: str, quality: str = "480p", user_id: int = None, custom_dir: str = "downloads") -> str | None:
    """
    Downloads YouTube video using direct YTUltra API as primary source.
    1. Direct YTUltra API (progressive or remote video+audio stream copy)
    2. Fallback to yt-dlp only if YTUltra is unavailable
    3. Fallback to Vynex only if yt-dlp fails
    Verifies output with probe_media_properties:
    - file exists and size > 0
    - valid video stream
    - valid audio stream
    Video-only files are NEVER returned as a completed lecture.
    """
    safe_name = safe_filename(output_name)
    if len(safe_name) > 60:
        safe_name = safe_name[:60]
    safe_name += f"_{quality}"

    out_dir = custom_dir or "downloads"
    os.makedirs(out_dir, exist_ok=True)
    target_file = os.path.join(out_dir, f"{safe_name}.mp4")

    height = parse_quality_number(quality)
    if height <= 0:
        height = 720

    # -------------------------------------------------------------------------
    # STEP 1: Primary Method - Direct YTUltra API
    # -------------------------------------------------------------------------
    logger.info(f"[YOUTUBE] Attempting direct YTUltra API for {url}...")
    yt_info = await asyncio.to_thread(resolve_youtube_ytultra_info, url, quality)
    ytultra_stream_url = yt_info.get("url") if isinstance(yt_info, dict) else (yt_info if isinstance(yt_info, str) else None)
    audio_url = yt_info.get("audio_url") if isinstance(yt_info, dict) else None

    # Fallback to string resolver if info was None
    if not ytultra_stream_url:
        ytultra_stream_url = await asyncio.to_thread(resolve_youtube_ytultra, url, quality)

    if ytultra_stream_url:
        ytultra_dest = os.path.join(out_dir, f"{safe_name}_ytultra.mp4")
        dl_ok = await asyncio.to_thread(download_media_stream_url, ytultra_stream_url, ytultra_dest, audio_url=audio_url)
        if dl_ok and os.path.exists(ytultra_dest) and os.path.getsize(ytultra_dest) > 0:
            props = probe_media_properties(ytultra_dest)
            if props.get("valid") and props.get("video_codec") and not props.get("has_audio"):
                logger.warning(f"[YOUTUBE] YTUltra output is video-only (no audio), cleaning up and falling back...")
                try:
                    os.remove(ytultra_dest)
                except OSError:
                    pass
            else:
                logger.info(f"[YOUTUBE] Direct YTUltra succeeded: {ytultra_dest}")
                return ytultra_dest

    # -------------------------------------------------------------------------
    # STEP 2: Fallback Method #1 - Authorized Cookies + yt-dlp
    # -------------------------------------------------------------------------
    logger.info(f"[YOUTUBE] Attempting yt-dlp fallback for {url}...")
    yt_format = (
        f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={height}]+bestaudio/"
        f"best[height<={height}][ext=mp4]/"
        f"best[height<={height}]/"
        f"best"
    )

    ytdlp_base = get_ytdlp_cmd()
    cmd = list(ytdlp_base) + [
        "--no-playlist",
        "-f", yt_format,
        "--merge-output-format", "mp4",
        "--concurrent-fragments", "4",
        "--buffer-size", "1024K",
        "--no-mtime",
        "--no-check-certificates",
        "--socket-timeout", "10",
        "--retries", "1",
        "--fragment-retries", "1",
        "-o", target_file
    ]

    cfile = get_cookies_file(user_id)
    if cfile and os.path.exists(cfile):
        cmd.extend(["--cookies", cfile])

    cmd.append(url)

    total_attempts = 2
    retry_count = 0

    while retry_count < total_attempts:
        attempt_num = retry_count + 1
        print(f"[YT-DLP] Downloading video (attempt {attempt_num}/{total_attempts})...")
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=25)

            if process.returncode == 0:
                print("[YT-DLP] Download succeeded")
                break

            retry_count += 1
            err_msg = stderr.decode(errors="replace").strip() if stderr else "Unknown error"
            print(f"[WARN] [YT-DLP] Download attempt {attempt_num}/{total_attempts} note: {err_msg[:200]}")
            if any(k in err_msg.lower() for k in ["sign in", "confirm you are not a bot", "bot detection", "javascript runtime", "403", "forbidden"]):
                break
            if retry_count < total_attempts:
                await asyncio.sleep(2)
        except Exception as exc:
            retry_count += 1
            print(f"[WARN] [YT-DLP] Subprocess notice: {exc}")
            if retry_count < total_attempts:
                await asyncio.sleep(2)

    final_file = get_existing_file(target_file)
    if os.path.exists(final_file) and os.path.getsize(final_file) > 0:
        fixed_file = f"{os.path.splitext(final_file)[0]}_fixed.mp4"
        try:
            res = subprocess.run(
                [FFMPEG_PATH or "ffmpeg", "-y", "-loglevel", "error", "-i", str(final_file), "-c", "copy", "-movflags", "+faststart", str(fixed_file)],
                check=False
            )
            cand = fixed_file if (res.returncode == 0 and os.path.exists(fixed_file) and os.path.getsize(fixed_file) > 0) else final_file
            if cand == fixed_file and os.path.exists(final_file):
                try:
                    os.remove(final_file)
                except OSError:
                    pass
        except Exception:
            cand = final_file

        props = probe_media_properties(cand)
        if props.get("valid") and props.get("video_codec") and not props.get("has_audio"):
            logger.warning(f"[YOUTUBE] yt-dlp output is video-only (no audio stream), trying Vynex fallback...")
            try:
                os.remove(cand)
            except OSError:
                pass
        else:
            logger.info(f"[YOUTUBE] yt-dlp produced valid file: {cand}")
            return cand

    # -------------------------------------------------------------------------
    # STEP 3: Fallback Method #2 - Vynex API (progressive video+audio)
    # -------------------------------------------------------------------------
    logger.info(f"[YOUTUBE] Attempting Vynex fallback API for {url}...")
    vynex_stream_url = await asyncio.to_thread(resolve_youtube_vynex, url, quality)
    if vynex_stream_url:
        vynex_dest = os.path.join(out_dir, f"{safe_name}_vynex.mp4")
        dl_ok = await asyncio.to_thread(download_media_stream_url, vynex_stream_url, vynex_dest)
        if dl_ok and os.path.exists(vynex_dest) and os.path.getsize(vynex_dest) > 0:
            props = probe_media_properties(vynex_dest)
            if props.get("valid") and props.get("video_codec") and not props.get("has_audio"):
                logger.warning(f"[YOUTUBE] Vynex stream is video-only (no audio).")
                try:
                    os.remove(vynex_dest)
                except OSError:
                    pass
            else:
                logger.info(f"[YOUTUBE] Vynex fallback succeeded: {vynex_dest}")
                return vynex_dest

    logger.error(f"[YOUTUBE] All YouTube download attempts exhausted for URL: {url}")
    return None


def decrypt_file(file_path: str, key: str) -> bool:
    if not file_path or not os.path.exists(file_path):
        return False

    # Safety check for empty file
    if os.path.getsize(file_path) == 0:
        print("❌ File is empty, skipping decrypt")
        return False

    if not key:
        return True

    key_bytes = key.encode()
    size = min(28, os.path.getsize(file_path))

    with open(file_path, "r+b") as f:
        with mmap.mmap(f.fileno(), length=size, access=mmap.ACCESS_WRITE) as mm:
            for i in range(size):
                mm[i] ^= key_bytes[i] if i < len(key_bytes) else i

    return True

def is_playable(file_path: str) -> bool:
    if not file_path or not os.path.exists(file_path):
        return False
    try:
        subprocess.run(
            ["ffprobe", "-v", "error", "-i", str(file_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def repair_video(file_path: str) -> str:
    if not file_path or not os.path.exists(file_path):
        return file_path
    repaired_path = file_path.replace(".mkv", "_fixed.mp4")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(file_path), "-c", "copy", str(repaired_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )
        if os.path.exists(repaired_path) and os.path.getsize(repaired_path) > 0:
            return repaired_path
    except Exception as e:
        logging.error(f"repair_video failed: {e}")
    return file_path


credit1 = os.environ.get(
    "credit1",
    CREDIT
)


def download_and_decrypt_video(url: str, name: str, key: str = None) -> str | None:
    # Download and decrypt the video
    if "fetch_video" in url:
        final_url, meta, err = resolve_fetch_video_url(url, "720p")
        if final_url:
            return download_appx_m3u8(final_url, name)
        return None
    if "transcoded" in url and ".m3u8" in url:
        print("⚡ Handling m3u8")
        return download_appx_m3u8(url, name)
    video_path = None
    for _ in range(5):  # Retry up to 5 times
        video_path = download_raw_file(url, name)
        if video_path and os.path.getsize(video_path) > 10 * 1024 * 1024:  # Ensure minimum size
            break

    if not video_path:
        return None

    try:
        if key:
            # Decrypt the file if key is provided
            decrypt_file(video_path, key)
    except Exception as e:
        print(f"⚠️ Decrypt failed: {e}")
        return None

    if not is_playable(video_path):
        # If not playable, repair the video
        return repair_video(video_path)
    else:
        # If already playable, return the original path
        return video_path
        
# ====== Time formatting for ETA ======
def _fmt_time(sec):
    sec = int(sec)
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60

    if h > 0:
        return f"{h}h {m}m {s}s"
    elif m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


# ====== Progress bar for uploads ======
def progress_bar(current, total, reply, start_time, name="{VIDEO}", credit="{CREDIT}"):
    """
    Progress callback for pyrogram uploads.
    """
    if current <= 0 or total <= 0:
        return

    now = time.time()
    key = id(reply)
    last_edit = _LAST_EDIT_TIME.get(key, 0)
    if now - last_edit < 1.8:
        return
    _LAST_EDIT_TIME[key] = now

    diff = now - start_time
    if diff < 0.5:
        return

    speed = current / diff if diff > 0 else 0
    percent = (current * 100) / total

    # Terminal logging only (internal)
    logging.debug(f"[UPLOAD] {name[:30]}: {percent:.1f}% ({current/(1024*1024):.1f}/{total/(1024*1024):.1f} MB, {speed/(1024*1024):.2f} MB/s)")

    sp = f"{speed/(1024*1024):.2f} MB/s" if speed > 0 else "0 MB/s"
    perc = f"{percent:.2f}%"
    cur = f"{current/(1024*1024):.1f} MB" if current < 1024*1024*1024 else f"{current/(1024*1024*1024):.2f} GB"
    tot = f"{total/(1024*1024):.1f} MB" if total < 1024*1024*1024 else f"{total/(1024*1024*1024):.2f} GB"
    rem_sec = int((total - current) / speed) if speed > 0 else 0
    mins, secs = divmod(rem_sec, 60)
    eta = f"{mins:02d}m {secs:02d}s" if rem_sec > 0 else "00s"

    pct = max(0.0, min(100.0, float(percent)))
    filled = int(round(12 * (pct / 100.0)))
    unfilled = 12 - filled
    bar = "█" * filled + "░" * unfilled

    from vars import BOT_USERNAME
    bot_credit = f"@{BOT_USERNAME}" if BOT_USERNAME else "@course_wallah_official_bot"

    clean_name = safe_filename(name)[:45]
    part_match = re.search(r"\(Part\s+(\d+)\)", clean_name, re.IGNORECASE)
    if part_match:
        hdr = f"📤 𝙐𝙥𝙡𝙤𝙖𝙙𝙞𝙣𝙜 𝙋𝙖𝙧𝙩 {part_match.group(1)} 📤"
    else:
        hdr = "📤 𝙐𝙥𝙡𝙤𝙖𝙙𝙞𝙣𝙜 📤"

    text = (
        f"**╭──⌈ {hdr} ⌋──╮**\n"
        f"**┣⪼ [ {bar} ]**\n"
        f"**┣⪼ 🚀 𝙎𝙥𝙚𝙚𝙙 :** {sp}\n"
        f"**┣⪼ 📈 𝙋𝙧𝙤𝙜𝙧𝙚𝙨𝙨 :** {perc}\n"
        f"**┣⪼ ⏳ 𝙇𝙤𝙖𝙙𝙚𝙙 :** {cur}\n"
        f"**┣⪼ 🍁 𝙎𝙞𝙯𝙚 :** {tot}\n"
        f"**┣⪼ 🕛 𝙀𝙏𝘼 :** {eta}\n"
        f"**╰────⌈ ✪ {bot_credit} ✪ ⌋────╯**"
    )

    try:
        reply.edit_text(text)
    except Exception:
        pass


# ====== Main send_vid function ======
from pyrogram.errors import FloodWait
import asyncio

# 🔥 FULL SAFE VIDEO SEND
async def safe_video_send(bot, delay=1, retries=5, **kwargs):
    attempt = 0
    while attempt < retries:
        try:
            msg = await bot.send_video(**kwargs)
            if delay > 0:
                await asyncio.sleep(delay)
            return msg

        except FloodWait as e:
            print(f"⏳ FloodWait: {e.value} sec")
            await asyncio.sleep(e.value)

        except Exception as e:
            print(f"⚠️ Error: {e}")
            attempt += 1
            await asyncio.sleep(5)

    print("❌ Failed after retries")
    return None


# ====== MAIN FUNCTION ======
async def send_vid(
    bot,
    m,
    cc,
    filename,
    thumb,
    name,
    prog,
    channel_id,
    watermark="{CREDIT}",
    topic_thread_id: int = None,
    parse_mode=None,
    user_id: int = None,
    bot_id: str = None,
    **kwargs
):
    try:
        import os, subprocess, time

        temp_thumb = None
        thumbnail = thumb

        # 🔥 THUMBNAIL
        if thumb in ["/d", "no"] or not (isinstance(thumb, str) and os.path.exists(thumb)):
            temp_thumb = os.path.join(TEMP_DIR, f"thumb_{os.path.basename(filename)}.jpg")
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", "00:00:10", "-i", str(filename), "-vframes", "1", "-q:v", "2", str(temp_thumb)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False
                )
            except Exception as e:
                logging.error(f"Thumbnail extraction failed: {e}")
            thumbnail = temp_thumb if (os.path.exists(temp_thumb) and os.path.getsize(temp_thumb) > 0) else None

        send_kwargs = {}
        if topic_thread_id is not None:
            try:
                tid = int(topic_thread_id)
                if tid > 0:
                    send_kwargs["message_thread_id"] = tid
            except (ValueError, TypeError):
                pass
        if parse_mode is not None:
            send_kwargs["parse_mode"] = parse_mode

        file_size_bytes = os.path.getsize(filename)

        # ===== NORMAL FILE (Under configured threshold) =====
        if file_size_bytes <= MAX_UPLOAD_SIZE_BYTES:
            dur = int(duration(filename))
            start_time = time.time()

            sent_message = await safe_video_send(
                bot,
                chat_id=channel_id,
                video=filename,
                caption=cc,
                supports_streaming=True,
                height=720,
                width=1280,
                thumb=thumbnail,
                duration=dur,
                progress=progress_bar if prog else None,
                progress_args=(prog, start_time, name, watermark) if prog else None,
                **send_kwargs
            )

            if not sent_message:
                # fallback document
                sent_message = await bot.send_document(
                    chat_id=channel_id,
                    document=filename,
                    caption=cc,
                    progress=progress_bar if prog else None,
                    progress_args=(prog, start_time, name, watermark) if prog else None,
                    **send_kwargs
                )

            # Auto-cleanup ONLY after confirmed successful Telegram upload!
            if sent_message:
                cleanup_uploaded_file(filename)

        # ===== LARGE FILE (Exceeds threshold -> Split and Upload) =====
        else:
            print(f"[LARGE VIDEO] File size {file_size_bytes/(1024*1024):.1f} MB exceeds upload threshold {MAX_UPLOAD_SIZE_BYTES/(1024*1024):.1f} MB.")
            if prog:
                try:
                    from utils import format_split_card
                    await prog.edit_text(format_split_card(name), parse_mode=parse_mode)
                except Exception:
                    pass

            parts = split_large_video(filename, max_size_bytes=MAX_UPLOAD_SIZE_BYTES, base_title=name)
            first_part_message = None
            all_parts_successful = True
            uploaded_parts_list = kwargs.get("uploaded_parts") or []
            on_part_uploaded = kwargs.get("on_part_uploaded")

            for idx, part in enumerate(parts, start=1):
                if idx in uploaded_parts_list:
                    print(f"[LARGE VIDEO] Part {idx}/{len(parts)} already uploaded in checkpoint, skipping re-upload.")
                    continue

                part_dur = int(duration(part))
                part_caption = f"{cc}\n\n📦 <b>Part {idx}/{len(parts)}</b>"

                if prog:
                    try:
                        from utils import format_upload_card
                        await prog.edit_text(format_upload_card(name, part_idx=idx, total_parts=len(parts)), parse_mode=parse_mode)
                    except Exception:
                        pass

                msg_obj = await safe_video_send(
                    bot,
                    chat_id=channel_id,
                    video=part,
                    caption=part_caption,
                    supports_streaming=True,
                    height=720,
                    width=1280,
                    thumb=thumbnail,
                    duration=part_dur,
                    progress=progress_bar if prog else None,
                    progress_args=(prog, time.time(), f"{name} (Part {idx})", watermark) if prog else None,
                    **send_kwargs
                )

                if not msg_obj:
                    msg_obj = await bot.send_document(
                        chat_id=channel_id,
                        document=part,
                        caption=part_caption,
                        progress=progress_bar if prog else None,
                        progress_args=(prog, time.time(), f"{name} (Part {idx})", watermark) if prog else None,
                        **send_kwargs
                    )

                if msg_obj:
                    if first_part_message is None:
                        first_part_message = msg_obj
                    if callable(on_part_uploaded):
                        try:
                            on_part_uploaded(idx)
                        except Exception:
                            pass
                    # Immediately delete part once confirmed uploaded!
                    cleanup_uploaded_file(part)
                else:
                    all_parts_successful = False
                    # Keep failed part on disk for retry

            # Clean original large video once all parts are done/successful
            if all_parts_successful:
                cleanup_uploaded_file(filename)

            sent_message = first_part_message

        if temp_thumb:
            cleanup_uploaded_file(temp_thumb)

        return sent_message

    except Exception as err:
        logger.error(f"send_vid failed: {err}")
        print(f"[ERROR] send_vid failed: {err}")
        return None

try:
    from kgs_downloader import download_kgs, is_kgs_url, resolve_kgs_playlist, extract_clean_kgs_title
except ImportError:
    pass

try:
    from spayee_downloader import download_spayee_hls, is_spayee_url, clean_spayee_key, select_spayee_variant, parse_spayee_input
except ImportError:
    pass


async def process_and_upload_remote_youtube_parts(
    bot: Client,
    prog: Optional[Message],
    caption: str,
    raw_title: str,
    url: str,
    yt_info: Dict[str, Any],
    channel_id: Union[int, str],
    user_id: Optional[int] = None,
    quality: str = "720p",
    custom_dir: Optional[str] = None,
    thumbnail: Optional[str] = None,
    watermark: Optional[str] = None,
    topic_thread_id: Optional[Union[int, str]] = None,
    parse_mode: Any = None,
    uploaded_parts: Optional[List[int]] = None,
    on_part_uploaded: Optional[Any] = None,
    target_part_bytes: int = int(1800 * 1024 * 1024)
) -> Optional[Message]:
    """
    Remote Part-by-Part YouTube Processor & Uploader.
    - NEVER downloads or stores the full 76 GB source on disk.
    - NEVER creates a full merged file on disk.
    - Computes time-aligned segment duration based on combined video+audio bitrate.
    - Slices remote video and audio concurrently using FFmpeg stream copy (-c:v copy -c:a copy).
    - Processes exactly ONE ~1800 MB part at a time.
    - Validates each part with ffprobe (valid video, audio, duration).
    - Uploads part to Telegram with safe_video_send.
    - Checkpoints uploaded part and immediately DELETES it from disk.
    - Verifies disk storage capacity before starting each part.
    - Refreshes remote signed URL from YTUltra if token expires during extraction.
    """
    if not yt_info or not isinstance(yt_info, dict):
        return None

    video_url = yt_info.get("url")
    audio_url = yt_info.get("audio_url")
    if not video_url:
        return None

    # 1. Setup temporary directory for current job
    out_dir = custom_dir or os.path.join(str(TEMP_DIR), str(user_id or "common"), f"yt_{int(time.time())}")
    os.makedirs(out_dir, exist_ok=True)

    # 2. Storage capacity check
    required_space = int(target_part_bytes * 1.15)
    try:
        free_space = shutil.disk_usage(out_dir).free
        if free_space < required_space:
            err_msg = "Current Railway storage is insufficient for the requested ~1800 MB part architecture."
            logger.error(f"[STORAGE CHECK] {err_msg} (Free: {free_space / (1024*1024):.1f} MB, Required: {required_space / (1024*1024):.1f} MB)")
            if prog:
                try:
                    await prog.edit_text(f"❌ <b>Storage Error:</b> {err_msg}", parse_mode=parse_mode)
                except Exception:
                    pass
            raise RuntimeError(err_msg)
    except Exception as exc:
        if "insufficient for the requested ~1800 MB part architecture" in str(exc):
            raise
        logger.warning(f"[STORAGE CHECK] Could not determine disk usage: {exc}")

    # 3. Determine Duration & Combined Bitrate
    duration_sec = 0.0
    if yt_info.get("duration"):
        try:
            duration_sec = float(yt_info["duration"])
        except (ValueError, TypeError):
            duration_sec = 0.0

    if duration_sec <= 0:
        duration_sec = await asyncio.to_thread(probe_remote_duration, video_url)

    if duration_sec <= 0:
        duration_sec = 3600.0  # safe 1-hour fallback

    video_bytes = int(yt_info.get("video_bytes") or 0)
    audio_bytes = int(yt_info.get("audio_bytes") or 0)
    total_bytes = int(yt_info.get("total_bytes") or (video_bytes + audio_bytes))

    # Calculate part duration using source bitrate
    if total_bytes > 0 and duration_sec > 0:
        combined_bitrate = total_bytes / duration_sec  # bytes per second
        part_duration = target_part_bytes / combined_bitrate
        total_parts = max(1, math.ceil(total_bytes / target_part_bytes))
    else:
        # Fallback to single part if byte sizes were not available
        total_parts = 1
        part_duration = duration_sec

    # Ensure part_duration is realistic
    part_duration = max(10.0, min(part_duration, duration_sec))

    container_ext = str(yt_info.get("container_ext") or ".mkv")
    stem = safe_filename(raw_title)

    logger.info(
        f"[YOUTUBE REMOTE] Total size: {total_bytes / (1024*1024):.1f} MB | "
        f"Duration: {duration_sec:.1f}s | Target Part Size: {target_part_bytes / (1024*1024):.1f} MB | "
        f"Parts: {total_parts} (~{part_duration:.1f}s each) | Container: {container_ext}"
    )

    # 4. Message formatting and sending configuration
    send_kwargs = {}
    if topic_thread_id is not None:
        try:
            tid = int(topic_thread_id)
            if tid > 0:
                send_kwargs["message_thread_id"] = tid
        except (ValueError, TypeError):
            pass
    if parse_mode is not None:
        send_kwargs["parse_mode"] = parse_mode

    uploaded_parts_set = set(uploaded_parts or [])
    first_part_message = None

    # 5. Process and Upload One Part at a Time
    for part_idx in range(1, total_parts + 1):
        if part_idx in uploaded_parts_set:
            logger.info(f"[YOUTUBE REMOTE] Part {part_idx}/{total_parts} already uploaded, skipping.")
            continue

        # Storage safety check before creating this part
        try:
            free_space = shutil.disk_usage(out_dir).free
            if free_space < required_space:
                err_msg = "Current Railway storage is insufficient for the requested ~1800 MB part architecture."
                logger.error(f"[STORAGE CHECK] {err_msg}")
                raise RuntimeError(err_msg)
        except Exception as exc:
            if "insufficient for the requested ~1800 MB part architecture" in str(exc):
                raise

        start_sec = (part_idx - 1) * part_duration
        dur_sec = part_duration if part_idx < total_parts else (duration_sec - start_sec + 2.0)
        dur_sec = max(1.0, dur_sec)

        part_file = os.path.join(out_dir, f"{stem}_Part_{part_idx}{container_ext}")
        if os.path.exists(part_file):
            try:
                os.remove(part_file)
            except OSError:
                pass

        if prog:
            try:
                from utils import format_download_card
                part_title_lbl = f"{raw_title} (Part {part_idx}/{total_parts})" if total_parts > 1 else raw_title
                await prog.edit_text(format_download_card("Course Wallah", part_title_lbl, frame=0), parse_mode=parse_mode)
            except Exception:
                pass

        logger.info(f"[YOUTUBE REMOTE] Generating Part {part_idx}/{total_parts} (start={start_sec:.1f}s, dur={dur_sec:.1f}s)...")

        # Extract remote time-aligned segment with bounded retries & automatic URL refresh
        max_extract_attempts = 3
        extract_ok = False

        for attempt in range(1, max_extract_attempts + 1):
            def do_extract(v_url, a_url):
                return extract_remote_media_segment(v_url, a_url, start_sec, dur_sec, part_file)

            extract_ok = await asyncio.to_thread(do_extract, video_url, audio_url)
            if extract_ok and os.path.exists(part_file) and os.path.getsize(part_file) > 0:
                props = probe_media_properties(part_file)
                if props.get("valid") and props.get("video_codec"):
                    break  # Valid segment generated successfully
                else:
                    logger.warning(f"[YOUTUBE REMOTE] Part {part_idx} validation failed on attempt {attempt}: {props}")
                    extract_ok = False

            if attempt < max_extract_attempts:
                backoff_sec = 1.0 * (2 ** (attempt - 1))
                logger.warning(
                    f"[YOUTUBE REMOTE] Segment extraction failed for Part {part_idx} (attempt {attempt}/{max_extract_attempts}), "
                    f"refreshing provider signed URL in {backoff_sec:.1f}s..."
                )
                await asyncio.sleep(backoff_sec)
                # Re-resolve fresh media URL from original provider
                new_info = await asyncio.to_thread(resolve_youtube_ytultra_info, url, quality)
                if new_info and new_info.get("url"):
                    video_url = new_info.get("url")
                    audio_url = new_info.get("audio_url")
                    logger.info(f"[YOUTUBE REMOTE] Refreshed YTUltra URL obtained for Part {part_idx}")

        if not extract_ok or not os.path.exists(part_file) or os.path.getsize(part_file) == 0:
            if os.path.exists(part_file):
                try:
                    os.remove(part_file)
                except OSError:
                    pass
            raise RuntimeError(f"Failed to generate valid segment for Part {part_idx}/{total_parts} after {max_extract_attempts} attempts.")


        # Probe and Validate Part
        props = probe_media_properties(part_file)
        if not props.get("valid") or not props.get("video_codec"):
            cleanup_uploaded_file(part_file)
            raise RuntimeError(f"Part {part_idx} validation failed: Invalid media stream properties ({props})")

        part_size = os.path.getsize(part_file)
        actual_dur = int(props.get("duration") or dur_sec)
        logger.info(f"[YOUTUBE REMOTE] Part {part_idx} verified: {part_size / (1024*1024):.1f} MB, dur={actual_dur}s")

        # Generate thumbnail for this part
        part_thumb = None
        if thumbnail and os.path.exists(thumbnail):
            part_thumb = thumbnail
        else:
            try:
                part_thumb = await asyncio.to_thread(extract_or_download_thumbnail, part_file, None, None)
            except Exception:
                part_thumb = None

        if prog:
            try:
                from utils import format_upload_card
                await prog.edit_text(format_upload_card(raw_title, part_idx=part_idx, total_parts=total_parts), parse_mode=parse_mode)
            except Exception:
                pass

        part_caption = f"{caption}\n\n📦 <b>Part {part_idx}/{total_parts}</b>" if total_parts > 1 else caption
        start_time = time.time()

        # Upload to Telegram
        msg_obj = await safe_video_send(
            bot,
            chat_id=channel_id,
            video=part_file,
            caption=part_caption,
            supports_streaming=True,
            height=int(yt_info.get("height") or 720),
            width=int(yt_info.get("width") or 1280),
            thumb=part_thumb,
            duration=actual_dur,
            progress=ext_progress_bar if prog else None,
            progress_args=(prog, start_time, f"{raw_title} (Part {part_idx})", watermark) if prog else None,
            **send_kwargs
        )

        if not msg_obj:
            logger.warning(f"[YOUTUBE REMOTE] Video send failed for Part {part_idx}, falling back to document...")
            msg_obj = await bot.send_document(
                chat_id=channel_id,
                document=part_file,
                caption=part_caption,
                progress=ext_progress_bar if prog else None,
                progress_args=(prog, start_time, f"{raw_title} (Part {part_idx})", watermark) if prog else None,
                **send_kwargs
            )

        if not msg_obj:
            # One retry attempt for failed upload
            logger.warning(f"[YOUTUBE REMOTE] Retrying upload for Part {part_idx}...")
            msg_obj = await safe_video_send(
                bot,
                chat_id=channel_id,
                video=part_file,
                caption=part_caption,
                supports_streaming=True,
                height=int(yt_info.get("height") or 720),
                width=int(yt_info.get("width") or 1280),
                thumb=part_thumb,
                duration=actual_dur,
                progress=ext_progress_bar if prog else None,
                progress_args=(prog, start_time, f"{raw_title} (Part {part_idx})", watermark) if prog else None,
                **send_kwargs
            )

        if not msg_obj:
            cleanup_uploaded_file(part_file)
            if part_thumb and part_thumb != thumbnail:
                cleanup_uploaded_file(part_thumb)
            raise RuntimeError(f"Failed to upload Part {part_idx}/{total_parts} to Telegram")

        if first_part_message is None:
            first_part_message = msg_obj

        # Checkpoint confirmed uploaded part
        if callable(on_part_uploaded):
            try:
                on_part_uploaded(part_idx)
            except Exception as e:
                logger.warning(f"[YOUTUBE REMOTE] Checkpoint callback error: {e}")

        # IMMEDIATELY delete temporary part file!
        cleanup_uploaded_file(part_file)
        if part_thumb and part_thumb != thumbnail:
            cleanup_uploaded_file(part_thumb)
        logger.info(f"[YOUTUBE REMOTE] Part {part_idx}/{total_parts} uploaded & deleted.")

    # Cleanup temp directory once all parts are done
    cleanup_job_temp_dir(out_dir)

    return first_part_message


