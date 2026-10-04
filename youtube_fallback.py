import os
import re
import json
import logging
import asyncio
import subprocess
from typing import Optional, Dict, Any, List, Tuple, Union
from urllib.parse import urlparse, quote, parse_qs
from pathlib import Path

import requests

from vars import FFMPEG_PATH, FFPROBE_PATH

def parse_quality_number(q_str: str) -> int:
    if not q_str:
        return 720
    q_lower = str(q_str).lower()
    if "4k" in q_lower or "2160" in q_lower:
        return 2160
    if "2k" in q_lower or "1440" in q_lower:
        return 1440
    m = re.search(r"(\d{3,4})", str(q_str))
    return int(m.group(1)) if m else 720

def parse_bytes_from_label(label: str) -> int:
    """
    Extracts byte count from format string such as '76.18 GB', '397.72 MB', '12.22 GB', '963.40 MB'.
    Ignores bitrate indicators like '128kbps'.
    """
    if not label:
        return 0
    matches = re.findall(r"([\d.]+)\s*(GB|GiB|MB|MiB|KB|KiB|B)\b(?![kKmMgG]?bps|/s)", str(label), re.IGNORECASE)
    if not matches:
        return 0
    # Prefer the last or largest size unit match (e.g. in '128kbps - 397.72 MB', matches is [('397.72', 'MB')])
    val_str, unit_raw = matches[-1]
    val = float(val_str)
    unit = unit_raw.upper()
    if unit in ("GB", "GIB"):
        return int(val * 1024 * 1024 * 1024)
    elif unit in ("MB", "MIB"):
        return int(val * 1024 * 1024)
    elif unit in ("KB", "KIB"):
        return int(val * 1024)
    return int(val)

logger = logging.getLogger(__name__)

DEFAULT_YOUTUBE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Connection": "keep-alive"
}

YTULTRA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://www.ytultra.com",
    "Referer": "https://www.ytultra.com/",
    "Connection": "keep-alive"
}


# ==============================================================================
# 1. VYNEX API ADAPTER & RESOLVER
# ==============================================================================

def parse_vynex_formats(data: Dict[str, Any], target_height: Any = 720) -> Optional[Dict[str, Any]]:
    """
    Parses Vynex YouTube API response to select a format with both video AND audio.
    Prioritizes formats where has_audio == True and resolution is <= target_height.
    If no format <= target_height has audio, selects the highest available resolution with audio.
    Never selects a video-only format.
    """
    if not data or not isinstance(data, dict):
        return None

    if isinstance(target_height, str):
        target_height = parse_quality_number(target_height)
    elif not isinstance(target_height, (int, float)) or target_height <= 0:
        target_height = 720
    else:
        target_height = int(target_height)

    formats = data.get("formats")
    if not isinstance(formats, list) and isinstance(data.get("data"), dict):
        formats = data["data"].get("formats")

    if not isinstance(formats, list) or not formats:
        return None

    audio_capable_formats: List[Tuple[int, Dict[str, Any]]] = []

    for fmt in formats:
        if not isinstance(fmt, dict):
            continue

        raw_url = fmt.get("url")
        if not raw_url or not isinstance(raw_url, str) or not raw_url.startswith(("http://", "https://")):
            continue

        has_audio = fmt.get("has_audio")
        if has_audio is None:
            has_audio = fmt.get("hasAudio")
        if has_audio is None:
            has_audio = fmt.get("audio")

        is_audio_valid = bool(has_audio is True or str(has_audio).lower() == "true")
        if not is_audio_valid:
            continue

        h = fmt.get("height")
        if h is None or not isinstance(h, (int, float)) or h <= 0:
            lbl = str(fmt.get("label") or fmt.get("quality") or "")
            match = re.search(r"(\d{3,4})p?", lbl)
            h = int(match.group(1)) if match else 360
        else:
            h = int(h)

        audio_capable_formats.append((h, fmt))

    if not audio_capable_formats:
        logger.warning("[VYNEX] No format with both video and audio found in response.")
        return None

    audio_capable_formats.sort(key=lambda x: x[0], reverse=True)

    for h, fmt in audio_capable_formats:
        if h <= target_height:
            logger.info(f"[VYNEX] Selected progressive format: {h}p (target <= {target_height}p)")
            return fmt

    chosen = audio_capable_formats[-1][1]
    logger.info(f"[VYNEX] Selected fallback progressive format: {audio_capable_formats[-1][0]}p")
    return chosen


def resolve_youtube_vynex(url: str, quality: str = "720p", timeout: int = 15) -> Optional[str]:
    """
    Queries Vynex YouTube resolver API and returns direct stream URL with video+audio.
    GET https://vynex.ai/api/tools/youtube-video?url=<YOUTUBE_URL>&will_retry=1
    """
    if not url or not isinstance(url, str):
        return None

    target_height = parse_quality_number(quality)
    if target_height <= 0:
        target_height = 720

    api_url = f"https://vynex.ai/api/tools/youtube-video?url={quote(url.strip())}&will_retry=1"
    try:
        resp = requests.get(api_url, headers=DEFAULT_YOUTUBE_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            logger.warning(f"[VYNEX] API returned HTTP {resp.status_code}")
            return None

        data = resp.json()
        chosen_fmt = parse_vynex_formats(data, target_height=target_height)
        if chosen_fmt and chosen_fmt.get("url"):
            return str(chosen_fmt["url"]).strip()
        return None
    except Exception as exc:
        logger.warning(f"[VYNEX] API resolution error: {exc}")
        return None


# ==============================================================================
# 2. YTULTRA API ADAPTER & RESOLVER (PRIMARY YOUTUBE PROVIDER)
# ==============================================================================

def parse_ytultra_response(data: Dict[str, Any], target_height: Any = 720) -> Optional[Dict[str, Any]]:
    """
    Defensively parses YTUltra API response to select formats ensuring VIDEO + AUDIO > RESOLUTION.
    POST https://api.ytultra.com/ikool/youtube/download
    - Tolerates missing/extra/reordered fields.
    - Selects progressive (video+audio) formats or separate video + audio for FFmpeg remuxing.
    - Rejects video-only formats when no audio stream is available.
    - Never fakes resolution.
    """
    if not data or not isinstance(data, dict):
        return None

    if isinstance(target_height, str):
        target_height = parse_quality_number(target_height)
    elif not isinstance(target_height, (int, float)) or target_height <= 0:
        target_height = 720
    else:
        target_height = int(target_height)

    # Search in potential response format fields
    formats = (
        data.get("medias")
        or data.get("formats")
        or data.get("streams")
        or data.get("links")
        or (data.get("data", {}).get("medias") if isinstance(data.get("data"), dict) else None)
        or (data.get("data", {}).get("formats") if isinstance(data.get("data"), dict) else None)
        or (data.get("data", {}).get("streams") if isinstance(data.get("data"), dict) else None)
    )

    # Metadata extraction
    data_dict = data.get("data") if isinstance(data.get("data"), dict) else data
    title = data_dict.get("title") or data.get("title")
    duration_val = data_dict.get("duration") or data.get("duration")
    thumb_url = data_dict.get("thumbnail") or data.get("thumbnail")

    # Check for direct single video URL if progressive
    if not isinstance(formats, list) or not formats:
        direct_url = data.get("url") or data.get("download_url") or data.get("video_url")
        if direct_url and isinstance(direct_url, str) and direct_url.startswith(("http://", "https://")):
            return {
                "url": direct_url,
                "video_url": direct_url,
                "audio_url": None,
                "height": target_height,
                "has_audio": True,
                "is_progressive": True,
                "title": title,
                "duration": duration_val,
                "thumbnail": thumb_url,
                "video_bytes": 0,
                "audio_bytes": 0,
                "total_bytes": 0,
                "container_ext": ".mp4",
                "video_format_label": "",
                "audio_format_label": ""
            }
        return None

    progressive_formats: List[Tuple[int, Dict[str, Any]]] = []
    video_only_formats: List[Tuple[int, Dict[str, Any]]] = []
    audio_only_formats: List[Tuple[int, Dict[str, Any]]] = []

    for fmt in formats:
        if not isinstance(fmt, dict):
            continue

        raw_url = fmt.get("url") or fmt.get("download_url") or fmt.get("link")
        if not raw_url or not isinstance(raw_url, str) or not raw_url.startswith(("http://", "https://")):
            continue

        lbl = str(fmt.get("format") or fmt.get("quality") or fmt.get("label") or fmt.get("resolution") or "")
        ftype = str(fmt.get("type") or fmt.get("format_type") or "").lower()

        # Parse query params for YouTube itag / mime if present
        try:
            parsed_u = urlparse(raw_url)
            qs = parse_qs(parsed_u.query)
            itag = qs.get("itag", [None])[0]
            mime = qs.get("mime", [None])[0] or ""
        except Exception:
            itag = None
            mime = ""

        # Check for Pure Audio Stream
        is_audio_only = (
            mime.startswith("audio/")
            or itag in ["140", "141", "139", "251", "250", "249", "171", "172"]
            or any(ext in lbl.lower() for ext in [".m4a", ".weba", ".mp3", ".aac", ".opus", ".ogg"])
            or ftype == "audio"
            or ftype == "audio_only"
        )

        if is_audio_only:
            pref = 2 if (itag == "140" or ".m4a" in lbl.lower() or "audio/mp4" in mime) else 1
            audio_only_formats.append((pref, fmt))
            continue

        # Extract height
        h = fmt.get("height")
        if h is None or not isinstance(h, (int, float)) or h <= 0:
            if "4k" in lbl.lower() or "2160" in lbl:
                h = 2160
            elif "2k" in lbl.lower() or "1440" in lbl:
                h = 1440
            else:
                m = re.search(r"(\d{3,4})p?", lbl)
                if m:
                    h = int(m.group(1))
                elif itag == "18":
                    h = 360
                elif itag == "22":
                    h = 720
                else:
                    h = 360
        else:
            h = int(h)

        # Check if Progressive (contains both video and audio)
        has_audio = fmt.get("has_audio")
        if has_audio is None:
            has_audio = fmt.get("hasAudio")
        if has_audio is None:
            has_audio = fmt.get("audio")

        is_prog = False
        if itag in ["18", "22", "34", "35", "36", "37", "38", "43", "44", "45", "46", "59", "78", "82", "83", "84", "85"]:
            is_prog = True
        elif has_audio is True or str(has_audio).lower() == "true":
            is_prog = True
        elif has_audio is False or str(has_audio).lower() == "false" or "video_only" in ftype or "video-only" in ftype:
            is_prog = False
        elif itag in ["160", "133", "134", "135", "136", "137", "298", "299", "302", "303", "308", "315"]:
            is_prog = False
        elif not mime.startswith("video/") and has_audio is not False:
            is_prog = True

        if is_prog:
            progressive_formats.append((h, fmt))
        else:
            video_only_formats.append((h, fmt))

    progressive_formats.sort(key=lambda x: x[0], reverse=True)
    video_only_formats.sort(key=lambda x: x[0], reverse=True)
    audio_only_formats.sort(key=lambda x: x[0], reverse=True)

    best_audio = audio_only_formats[0][1] if audio_only_formats else None
    audio_bytes = parse_bytes_from_label(str(best_audio.get("format") or "")) if best_audio else 0

    valid_prog = [p for p in progressive_formats if p[0] <= target_height]
    valid_video = [v for v in video_only_formats if v[0] <= target_height] if best_audio else []

    chosen_entry = None
    is_progressive = False
    chosen_video_fmt = None

    if valid_video and valid_prog:
        if valid_video[0][0] > valid_prog[0][0]:
            chosen_entry = valid_video[0]
            chosen_video_fmt = chosen_entry[1]
            is_progressive = False
            logger.info(f"[YTULTRA] Selected separate {chosen_entry[0]}p video + audio for remux (target <= {target_height}p)")
        else:
            chosen_entry = valid_prog[0]
            chosen_video_fmt = chosen_entry[1]
            is_progressive = True
            logger.info(f"[YTULTRA] Selected progressive format: {chosen_entry[0]}p (target <= {target_height}p)")
    elif valid_video:
        chosen_entry = valid_video[0]
        chosen_video_fmt = chosen_entry[1]
        is_progressive = False
        logger.info(f"[YTULTRA] Selected separate {chosen_entry[0]}p video + audio (target <= {target_height}p)")
    elif valid_prog:
        chosen_entry = valid_prog[0]
        chosen_video_fmt = chosen_entry[1]
        is_progressive = True
        logger.info(f"[YTULTRA] Selected progressive format: {chosen_entry[0]}p (target <= {target_height}p)")
    elif progressive_formats:
        chosen_entry = progressive_formats[-1]
        chosen_video_fmt = chosen_entry[1]
        is_progressive = True
        logger.info(f"[YTULTRA] Selected fallback progressive format: {chosen_entry[0]}p")
    elif video_only_formats and best_audio:
        chosen_entry = video_only_formats[-1]
        chosen_video_fmt = chosen_entry[1]
        is_progressive = False
        logger.info(f"[YTULTRA] Selected fallback separate {chosen_entry[0]}p video + audio")

    if not chosen_entry or not chosen_video_fmt:
        logger.warning("[YTULTRA] No playable format with audio found in response.")
        return None

    video_lbl = str(chosen_video_fmt.get("format") or chosen_video_fmt.get("quality") or "")
    audio_lbl = str(best_audio.get("format") or "") if best_audio else ""
    video_bytes = parse_bytes_from_label(video_lbl)
    total_bytes = video_bytes + (audio_bytes if not is_progressive else 0)

    # Determine container format
    v_raw_url = chosen_video_fmt.get("url") or chosen_video_fmt.get("download_url") or ""
    container_ext = ".mp4"
    if "webm" in video_lbl.lower() or "video/webm" in v_raw_url:
        container_ext = ".mkv"

    return {
        "url": chosen_video_fmt.get("url") or chosen_video_fmt.get("download_url") or chosen_video_fmt.get("link"),
        "video_url": chosen_video_fmt.get("url") or chosen_video_fmt.get("download_url") or chosen_video_fmt.get("link"),
        "audio_url": best_audio.get("url") if (best_audio and not is_progressive) else None,
        "height": chosen_entry[0],
        "has_audio": True,
        "is_progressive": is_progressive,
        "title": title,
        "duration": duration_val,
        "thumbnail": thumb_url,
        "video_bytes": video_bytes,
        "audio_bytes": audio_bytes if not is_progressive else 0,
        "total_bytes": total_bytes,
        "container_ext": container_ext,
        "video_format_label": video_lbl,
        "audio_format_label": audio_lbl
    }


def parse_all_ytultra_qualities(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parses full YTUltra response into a structured list of ALL REAL available playable qualities.
    Enforces VIDEO + AUDIO > RESOLUTION:
    - Progressive formats have built-in audio.
    - Video-only formats are ONLY included if compatible audio (AAC / opus) is present in the response.
    - Formats without audio are strictly excluded.
    - Returns qualities sorted from highest resolution to lowest, with badges and metadata.
    """
    if not data or not isinstance(data, dict):
        return {"title": "", "duration": 0, "thumbnail": None, "qualities": [], "raw_data": {}}

    formats = (
        data.get("medias")
        or data.get("formats")
        or data.get("streams")
        or data.get("links")
        or (data.get("data", {}).get("medias") if isinstance(data.get("data"), dict) else None)
        or (data.get("data", {}).get("formats") if isinstance(data.get("data"), dict) else None)
        or (data.get("data", {}).get("streams") if isinstance(data.get("data"), dict) else None)
    )

    data_dict = data.get("data") if isinstance(data.get("data"), dict) else data
    title = data_dict.get("title") or data.get("title") or "YouTube Video"
    duration_val = data_dict.get("duration") or data.get("duration") or 0
    thumb_url = data_dict.get("thumbnail") or data.get("thumbnail")

    if not isinstance(formats, list) or not formats:
        direct_url = data.get("url") or data.get("download_url") or data.get("video_url")
        if direct_url and isinstance(direct_url, str) and direct_url.startswith(("http://", "https://")):
            return {
                "title": title,
                "duration": duration_val,
                "thumbnail": thumb_url,
                "raw_data": data,
                "qualities": [
                    {
                        "height": 720,
                        "label": "720p HD",
                        "short_label": "720p",
                        "badge": "⚡",
                        "codec": "H264",
                        "audio_codec": "AAC",
                        "container_ext": ".mp4",
                        "video_bytes": 0,
                        "audio_bytes": 0,
                        "total_bytes": 0,
                        "formatted_size": "",
                        "is_progressive": True,
                        "video_url": direct_url,
                        "audio_url": None,
                        "url": direct_url
                    }
                ]
            }
        return {"title": title, "duration": duration_val, "thumbnail": thumb_url, "qualities": [], "raw_data": data}

    audio_only_formats: List[Tuple[int, Dict[str, Any]]] = []
    video_entries: List[Tuple[int, bool, Dict[str, Any]]] = []

    for fmt in formats:
        if not isinstance(fmt, dict):
            continue

        raw_url = fmt.get("url") or fmt.get("download_url") or fmt.get("link")
        if not raw_url or not isinstance(raw_url, str) or not raw_url.startswith(("http://", "https://")):
            continue

        lbl = str(fmt.get("format") or fmt.get("quality") or fmt.get("label") or fmt.get("resolution") or "")
        ftype = str(fmt.get("type") or fmt.get("format_type") or "").lower()

        try:
            parsed_u = urlparse(raw_url)
            qs = parse_qs(parsed_u.query)
            itag = qs.get("itag", [None])[0]
            mime = qs.get("mime", [None])[0] or ""
        except Exception:
            itag = None
            mime = ""

        is_audio_only = (
            mime.startswith("audio/")
            or itag in ["140", "141", "139", "251", "250", "249", "171", "172"]
            or any(ext in lbl.lower() for ext in [".m4a", ".weba", ".mp3", ".aac", ".opus", ".ogg"])
            or ftype == "audio"
            or ftype == "audio_only"
        )

        if is_audio_only:
            pref = 2 if (itag == "140" or ".m4a" in lbl.lower() or "audio/mp4" in mime) else 1
            audio_only_formats.append((pref, fmt))
            continue

        h = fmt.get("height")
        if h is None or not isinstance(h, (int, float)) or h <= 0:
            if "4k" in lbl.lower() or "2160" in lbl:
                h = 2160
            elif "2k" in lbl.lower() or "1440" in lbl:
                h = 1440
            else:
                m = re.search(r"(\d{3,4})p?", lbl)
                if m:
                    h = int(m.group(1))
                elif itag == "18":
                    h = 360
                elif itag == "22":
                    h = 720
                else:
                    h = 360
        else:
            h = int(h)

        has_audio = fmt.get("has_audio")
        if has_audio is None:
            has_audio = fmt.get("hasAudio")
        if has_audio is None:
            has_audio = fmt.get("audio")

        is_prog = False
        if itag in ["18", "22", "34", "35", "36", "37", "38", "43", "44", "45", "46", "59", "78", "82", "83", "84", "85"]:
            is_prog = True
        elif has_audio is True or str(has_audio).lower() == "true":
            is_prog = True
        elif has_audio is False or str(has_audio).lower() == "false" or "video_only" in ftype or "video-only" in ftype:
            is_prog = False
        elif itag in ["160", "133", "134", "135", "136", "137", "298", "299", "302", "303", "308", "315"]:
            is_prog = False
        elif not mime.startswith("video/") and has_audio is not False:
            is_prog = True

        video_entries.append((h, is_prog, fmt))

    audio_only_formats.sort(key=lambda x: x[0], reverse=True)
    best_audio = audio_only_formats[0][1] if audio_only_formats else None
    audio_bytes = parse_bytes_from_label(str(best_audio.get("format") or "")) if best_audio else 0

    seen_heights: Set[int] = set()
    qualities: List[Dict[str, Any]] = []

    video_entries.sort(key=lambda x: (x[0], not x[1] if best_audio else x[1]), reverse=True)

    for h, is_prog, fmt in video_entries:
        if h in seen_heights:
            continue
        if not is_prog and not best_audio:
            continue

        seen_heights.add(h)
        v_url = fmt.get("url") or fmt.get("download_url") or fmt.get("link")
        a_url = best_audio.get("url") if (best_audio and not is_prog) else None
        lbl = str(fmt.get("format") or fmt.get("quality") or fmt.get("label") or "")
        v_bytes = parse_bytes_from_label(lbl)
        tot_bytes = v_bytes + (audio_bytes if not is_prog else 0)

        if h >= 2160:
            badge = "🔥"
            label = "4K UHD • 2160p"
            short_lbl = "4K UHD"
        elif h >= 1440:
            badge = "💎"
            label = "1440p • 2K"
            short_lbl = "1440p"
        elif h >= 1080:
            badge = "🎬"
            label = "1080p • FHD"
            short_lbl = "1080p"
        elif h >= 720:
            badge = "⚡"
            label = "720p • HD"
            short_lbl = "720p"
        elif h >= 480:
            badge = "📱"
            label = "480p • SD"
            short_lbl = "480p"
        elif h >= 360:
            badge = "🎥"
            label = "360p"
            short_lbl = "360p"
        else:
            badge = "📦"
            label = f"{h}p"
            short_lbl = f"{h}p"

        codec = "VP9" if ("webm" in lbl.lower() or "vp9" in lbl.lower()) else "H264"
        container_ext = ".mkv" if ("webm" in lbl.lower() or "vp9" in lbl.lower() or "video/webm" in v_url) else ".mp4"

        formatted_size = ""
        if tot_bytes > 0:
            if tot_bytes >= 1024 * 1024 * 1024:
                formatted_size = f"{tot_bytes / (1024*1024*1024):.2f} GB"
            else:
                formatted_size = f"{tot_bytes / (1024*1024):.1f} MB"

        qualities.append({
            "height": h,
            "label": label,
            "short_label": short_lbl,
            "badge": badge,
            "codec": codec,
            "audio_codec": "AAC" if (best_audio or is_prog) else "None",
            "container_ext": container_ext,
            "video_bytes": v_bytes,
            "audio_bytes": audio_bytes if not is_prog else 0,
            "total_bytes": tot_bytes,
            "formatted_size": formatted_size,
            "is_progressive": is_prog,
            "video_url": v_url,
            "audio_url": a_url,
            "url": v_url
        })

    qualities.sort(key=lambda q: q["height"], reverse=True)

    return {
        "title": title,
        "duration": duration_val,
        "thumbnail": thumb_url,
        "raw_data": data,
        "qualities": qualities
    }


def fetch_ytultra_media_data(url: str, timeout: int = 15) -> Optional[Dict[str, Any]]:
    """
    Queries YTUltra download API and returns full parsed media information including
    all playable qualities, metadata, and raw response.
    POST https://api.ytultra.com/ikool/youtube/download
    """
    if not url or not isinstance(url, str):
        return None

    api_url = "https://api.ytultra.com/ikool/youtube/download"
    payload = {"url": url.strip()}

    try:
        resp = requests.post(api_url, headers=YTULTRA_HEADERS, json=payload, timeout=timeout)
        if resp.status_code != 200:
            logger.warning(f"[YTULTRA] API returned HTTP {resp.status_code}")
            return None

        data = resp.json()
        return parse_all_ytultra_qualities(data)
    except Exception as exc:
        logger.warning(f"[YTULTRA] API resolution error: {exc}")
        return None


def resolve_youtube_ytultra_info(url: str, quality: str = "720p", timeout: int = 15) -> Optional[Dict[str, Any]]:
    """
    Directly queries the YTUltra YouTube download API and returns media info dictionary.
    POST https://api.ytultra.com/ikool/youtube/download
    """
    if not url or not isinstance(url, str):
        return None

    target_height = parse_quality_number(quality)
    if target_height <= 0:
        target_height = 720

    api_url = "https://api.ytultra.com/ikool/youtube/download"
    payload = {"url": url.strip()}

    try:
        resp = requests.post(api_url, headers=YTULTRA_HEADERS, json=payload, timeout=timeout)
        if resp.status_code != 200:
            logger.warning(f"[YTULTRA] API returned HTTP {resp.status_code}")
            return None

        data = resp.json()
        chosen_fmt = parse_ytultra_response(data, target_height=target_height)
        if chosen_fmt:
            parsed_all = parse_all_ytultra_qualities(data)
            chosen_fmt["all_qualities"] = parsed_all.get("qualities", [])
            chosen_fmt["raw_data"] = data
        return chosen_fmt
    except Exception as exc:
        logger.warning(f"[YTULTRA] API resolution error: {exc}")
        return None


def resolve_youtube_ytultra(url: str, quality: str = "720p", timeout: int = 15) -> Optional[str]:
    """
    Queries YTUltra YouTube download API and returns direct stream URL with video+audio.
    POST https://api.ytultra.com/ikool/youtube/download
    """
    info = resolve_youtube_ytultra_info(url, quality, timeout=timeout)
    if info and info.get("url"):
        return str(info["url"]).strip()
    return None


# ==============================================================================
# 3. REMOTE STREAM PROBING, SLICING & MUXING
# ==============================================================================

def probe_remote_duration(url: str, timeout: int = 15) -> float:
    """
    Probes duration in seconds directly from remote media URL using ffprobe over HTTP.
    """
    if not url:
        return 0.0
    bin_path = str(FFPROBE_PATH or "ffprobe")
    cmd = [
        bin_path, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(url)
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        if res.returncode == 0 and res.stdout.strip():
            return float(res.stdout.strip())
    except Exception as e:
        logger.warning(f"[PROBE DURATION] Error probing remote duration: {e}")
    return 0.0


def extract_remote_media_segment(
    video_url: str,
    audio_url: Optional[str],
    start_sec: float,
    dur_sec: float,
    output_path: str,
    timeout: Optional[int] = None
) -> bool:
    """
    Extracts a time-aligned segment from remote video and audio streams using FFmpeg stream copy.
    - Slices matching exact time ranges (-ss start_sec -t dur_sec) for both video and audio.
    - Explicitly supplies container format (-f matroska or -f mp4) to ensure FFmpeg never fails on temporary file paths.
    - Preserves container validity and faststart if mp4.
    - Uses dynamic timeout scaled to part duration to support large ~1800 MB 4K parts.
    """
    if not video_url or not output_path:
        return False

    if timeout is None or timeout <= 0:
        timeout = max(600, int(dur_sec * 3.0))

    ext = os.path.splitext(output_path)[1].lower()
    is_mkv = ext in [".mkv", ".webm"]
    fmt = "matroska" if is_mkv else "mp4"
    valid_ext = ".mkv" if is_mkv else ".mp4"
    temp_path = f"{output_path}.seg.tmp{valid_ext}"

    if os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except OSError:
            pass

    ffmpeg_bin = FFMPEG_PATH or "ffmpeg"

    # Slicing from separate video and audio streams with identical time range
    if audio_url and isinstance(audio_url, str) and audio_url.startswith(("http://", "https://")):
        cmd = [
            ffmpeg_bin, "-y", "-loglevel", "error",
            "-ss", f"{start_sec:.3f}",
            "-i", str(video_url),
            "-ss", f"{start_sec:.3f}",
            "-i", str(audio_url),
            "-t", f"{dur_sec:.3f}",
            "-c:v", "copy",
            "-c:a", "copy",
            "-avoid_negative_ts", "make_zero",
            "-f", fmt
        ]
        if fmt == "mp4":
            cmd.extend(["-movflags", "+faststart"])
        cmd.append(str(temp_path))
    else:
        # Slicing from progressive stream
        cmd = [
            ffmpeg_bin, "-y", "-loglevel", "error",
            "-ss", f"{start_sec:.3f}",
            "-i", str(video_url),
            "-t", f"{dur_sec:.3f}",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-f", fmt
        ]
        if fmt == "mp4":
            cmd.extend(["-movflags", "+faststart"])
        cmd.append(str(temp_path))

    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        if res.returncode != 0:
            logger.warning(f"[FFMPEG SEGMENT] Error extracting segment: {res.stderr}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            return False

        if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            return False

        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        os.replace(temp_path, output_path)
        return True

    except Exception as exc:
        logger.error(f"[FFMPEG SEGMENT] Exception extracting segment: {exc}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return False


def download_media_stream_url(
    stream_url: str,
    output_path: str,
    audio_url: Optional[str] = None,
    timeout: int = 30,
    chunk_size: int = 1024 * 1024
) -> bool:
    """
    Downloads media from remote stream URL(s) with minimal disk usage.
    If audio_url is provided, performs on-the-fly FFmpeg stream muxing (-c copy).
    If audio_url is None, stream copies the progressive format with faststart.
    """
    if not stream_url or not output_path:
        return False

    ext = os.path.splitext(output_path)[1].lower()
    is_mkv = ext in [".mkv", ".webm"]
    fmt = "matroska" if is_mkv else "mp4"
    valid_ext = ".mkv" if is_mkv else ".mp4"
    temp_dest = f"{output_path}.dl.tmp{valid_ext}"
    ffmpeg_bin = FFMPEG_PATH or "ffmpeg"

    # Case 1: Separate video + audio streams -> direct FFmpeg remux
    if audio_url and isinstance(audio_url, str) and audio_url.startswith(("http://", "https://")):
        try:
            cmd = [
                ffmpeg_bin, "-y", "-loglevel", "error",
                "-i", str(stream_url),
                "-i", str(audio_url),
                "-c:v", "copy",
                "-c:a", "copy",
                "-avoid_negative_ts", "make_zero",
                "-f", fmt
            ]
            if fmt == "mp4":
                cmd.extend(["-movflags", "+faststart"])
            cmd.append(str(temp_dest))

            res = subprocess.run(cmd, check=False)
            if res.returncode == 0 and os.path.exists(temp_dest) and os.path.getsize(temp_dest) > 0:
                if os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except OSError:
                        pass
                os.replace(temp_dest, output_path)
                return True
        except Exception as e:
            logger.warning(f"[STREAM DOWNLOAD] FFmpeg remote mux failed, falling back: {e}")

    # Case 2: Progressive stream -> try FFmpeg stream copy directly from remote URL
    try:
        cmd = [
            ffmpeg_bin, "-y", "-loglevel", "error",
            "-i", str(stream_url),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-f", fmt
        ]
        if fmt == "mp4":
            cmd.extend(["-movflags", "+faststart"])
        cmd.append(str(temp_dest))

        res = subprocess.run(cmd, check=False)
        if res.returncode == 0 and os.path.exists(temp_dest) and os.path.getsize(temp_dest) > 0:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            os.replace(temp_dest, output_path)
            return True
    except Exception:
        pass

    # Case 3: Chunked HTTP streaming fallback
    try:
        with requests.get(stream_url, headers=DEFAULT_YOUTUBE_HEADERS, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            with open(temp_dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)

        if not os.path.exists(temp_dest) or os.path.getsize(temp_dest) == 0:
            if os.path.exists(temp_dest):
                os.remove(temp_dest)
            return False

        # Apply FFmpeg faststart
        cmd = [
            ffmpeg_bin, "-y", "-loglevel", "error",
            "-i", str(temp_dest),
            "-c", "copy",
            "-f", fmt
        ]
        if fmt == "mp4":
            cmd.extend(["-movflags", "+faststart"])
        cmd.append(str(output_path))

        res = subprocess.run(cmd, check=False)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            try:
                os.remove(temp_dest)
            except OSError:
                pass
            return True

        # Fallback rename
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        os.replace(temp_dest, output_path)
        return True

    except Exception as e:
        logger.error(f"[STREAM DOWNLOAD] Failed to download media stream: {e}")
        if os.path.exists(temp_dest):
            try:
                os.remove(temp_dest)
            except OSError:
                pass
        return False
