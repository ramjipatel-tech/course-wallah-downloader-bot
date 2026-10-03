# ==============================================================================
# KGS (KHAN GLOBAL STUDIES) HLS DOWNLOADER MODULE
# ==============================================================================
import os
import sys
import time
import logging
import subprocess
import re
from pathlib import Path
from urllib.parse import urlparse, urljoin, parse_qs, urlencode, urlunparse
from typing import Optional, Dict, Any, Union, Tuple, List
import requests
import m3u8

from vars import PROJECT_ROOT, DATA_DIR, TEMP_DIR, DOWNLOADS_DIR
from utils import is_safe_temp_path, cleanup_uploaded_file

logger = logging.getLogger(__name__)

DEFAULT_KGS_HEADERS: Dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://khanglobalstudies.com",
    "Referer": "https://khanglobalstudies.com/",
    "Connection": "keep-alive"
}


def is_kgs_url(url: str) -> bool:
    """
    Detects whether a given URL is a KGS (Khan Global Studies / Akamai HLS) resource.
    Inspects hostname, domain, and path for 'kgs', 'khanglobalstudies', or KGS CDN patterns.
    Does NOT match KGS PDF files (PDF files must go to the DIRECT_PDF handler).
    """
    if not url or not isinstance(url, str):
        return False
    clean = url.strip()
    if not clean:
        return False

    # Guard: Any .pdf URL MUST NOT be classified as KGS HLS stream
    base_url = clean.split("*")[0].split("?")[0].strip()
    if base_url.lower().endswith(".pdf") or ".pdf" in url.lower():
        return False

    parsed = urlparse(clean.split("*")[0].strip())
    netloc_lower = (parsed.netloc or "").lower()
    path_lower = (parsed.path or "").lower()

    if any(h in netloc_lower for h in ("kgs", "khanglobalstudies", "kgs-new-v1", "kgs-video")):
        return True
    if "akamaized.net" in netloc_lower and any(p in path_lower for p in ("/kv3/", "/kgs/", "/kv2/")):
        return True
    if "/kgs/" in path_lower or "kgs" in path_lower.split("/"):
        return True
    if "kgs" in netloc_lower or "kgs" in path_lower:
        return True
    return False


def extract_clean_kgs_title(url: str, default_title: Optional[str] = None) -> str:
    """
    Extracts a clean, safe filename/title from URL without exposing tokens, query parameters,
    or generic names like master, index, playlist.
    """
    if default_title and default_title.strip() and default_title.strip().lower() not in (
        "video", "output", "download", "stream", "default title", "master", "playlist", "index"
    ):
        return default_title.strip()

    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p and not p.endswith(".m3u8")]
    if path_parts:
        candidate = path_parts[-1]
        if candidate and candidate.lower() not in ("master", "index", "playlist", "video", "output", "stream", "hls"):
            return candidate

    stem = Path(parsed.path).stem
    if stem and stem.lower() not in ("master", "index", "playlist", "video", "output", "stream", "hls"):
        return stem

    return f"KGS_Lecture_{int(time.time())}"


def _parse_quality_height(quality_str: Optional[str]) -> int:
    """Extracts integer height from quality string (e.g. '720p' -> 720, '1080' -> 1080)."""
    if not quality_str:
        return 720
    m = re.search(r"(\d{3,4})", str(quality_str))
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return 720


def select_kgs_variant(
    playlist_obj: m3u8.M3U8,
    preferred_quality: str = "720p",
    base_url: str = ""
) -> Tuple[str, str]:
    """
    Selects the best variant playlist from an M3U8 master playlist.
    Prefers 720p when available, or highest suitable available quality <= preferred_quality.
    Returns (resolved_variant_url, selected_quality_tag).
    """
    target_height = _parse_quality_height(preferred_quality)
    variants = playlist_obj.playlists
    if not variants:
        return base_url, preferred_quality

    parsed_base = urlparse(base_url)
    base_query = parsed_base.query

    scored_variants = []
    for var in variants:
        uri = var.uri
        height = 0
        bandwidth = 0

        if var.stream_info:
            if var.stream_info.resolution:
                height = var.stream_info.resolution[1]
            elif var.stream_info.bandwidth:
                bandwidth = var.stream_info.bandwidth

        # If resolution not in stream_info, check URI string
        if height == 0:
            m = re.search(r"(\d{3,4})p?", uri)
            if m:
                height = int(m.group(1))

        resolved_uri = urljoin(base_url, uri)
        # Preserve signed query parameters if variant URI lacks them
        parsed_var = urlparse(resolved_uri)
        if not parsed_var.query and base_query:
            resolved_uri = urlunparse((
                parsed_var.scheme,
                parsed_var.netloc,
                parsed_var.path,
                parsed_var.params,
                base_query,
                parsed_var.fragment
            ))

        scored_variants.append({
            "uri": resolved_uri,
            "height": height,
            "bandwidth": bandwidth,
            "raw_var": var
        })

    # Strategy 1: Exact match with target height (e.g. 720)
    exact_matches = [v for v in scored_variants if v["height"] == target_height]
    if exact_matches:
        exact_matches.sort(key=lambda x: x["bandwidth"], reverse=True)
        return exact_matches[0]["uri"], f"{target_height}p"

    # Strategy 2: Highest available quality <= target_height
    under_matches = [v for v in scored_variants if 0 < v["height"] <= target_height]
    if under_matches:
        under_matches.sort(key=lambda x: (x["height"], x["bandwidth"]), reverse=True)
        best = under_matches[0]
        return best["uri"], f"{best['height']}p"

    # Strategy 3: Highest available quality overall
    with_height = [v for v in scored_variants if v["height"] > 0]
    if with_height:
        with_height.sort(key=lambda x: (x["height"], x["bandwidth"]), reverse=True)
        best = with_height[0]
        return best["uri"], f"{best['height']}p"

    # Strategy 4: Highest bandwidth if resolutions not specified
    scored_variants.sort(key=lambda x: x["bandwidth"], reverse=True)
    return scored_variants[0]["uri"], preferred_quality


def resolve_kgs_playlist(
    url: str,
    preferred_quality: str = "720p",
    custom_headers: Optional[Dict[str, str]] = None,
    timeout: int = 15
) -> Tuple[str, Dict[str, Any]]:
    """
    Inspects a KGS HLS URL.
    If it is a master playlist, selects the best variant matching preferred_quality (default 720p)
    or highest available quality, and resolves relative paths while strictly preserving signed tokens.
    If it is a media playlist or direct stream, returns the URL as-is.
    """
    if not url or not isinstance(url, str):
        return url, {"is_master": False, "selected_quality": preferred_quality, "error": "Invalid URL"}

    clean_url = url.strip()
    headers = dict(DEFAULT_KGS_HEADERS)
    if custom_headers:
        headers.update(custom_headers)

    try:
        resp = requests.get(clean_url, headers=headers, timeout=timeout)
        if resp.status_code in (401, 403):
            logger.warning(f"[KGS RESOLVER] HTTP {resp.status_code} Forbidden (authorization expired or rejected).")
            return clean_url, {
                "is_master": False,
                "selected_quality": preferred_quality,
                "http_status": resp.status_code,
                "error": "KGS_AUTH_403",
                "auth_error": True
            }
        if resp.status_code != 200:
            logger.warning(f"[KGS RESOLVER] HTTP {resp.status_code} fetching playlist. Falling back to direct URL.")
            return clean_url, {
                "is_master": False,
                "selected_quality": preferred_quality,
                "http_status": resp.status_code,
                "fallback": True
            }

        content = resp.text
        if "#EXTM3U" not in content:
            # Not an M3U8 manifest, use direct URL
            return clean_url, {"is_master": False, "selected_quality": preferred_quality, "fallback": True}

        parsed_playlist = m3u8.loads(content, uri=clean_url)
        if parsed_playlist.is_variant and parsed_playlist.playlists:
            # Master playlist with variants
            variant_url, selected_q = select_kgs_variant(parsed_playlist, preferred_quality, base_url=clean_url)
            logger.info(f"[KGS RESOLVER] Master playlist resolved -> Variant {selected_q}: {variant_url[:60]}...")
            return variant_url, {
                "is_master": True,
                "selected_quality": selected_q,
                "variants_count": len(parsed_playlist.playlists)
            }
        else:
            # Media / Segment playlist
            return clean_url, {
                "is_master": False,
                "selected_quality": preferred_quality,
                "segments_count": len(parsed_playlist.segments) if parsed_playlist.segments else 0
            }

    except Exception as exc:
        logger.warning(f"[KGS RESOLVER] Failed to resolve playlist ({exc}). Falling back to direct URL.")
        return clean_url, {
            "is_master": False,
            "selected_quality": preferred_quality,
            "fallback": True,
            "error": str(exc)
        }


def download_kgs(
    url: str,
    name: str,
    quality: str = "720p",
    custom_headers: Optional[Union[str, Dict[str, str]]] = None,
    custom_dir: Optional[str] = None,
    user_id: Optional[int] = None,
    job_id: Optional[str] = None
) -> Optional[str]:
    """
    Downloads a KGS HLS video stream using FFmpeg with stream copy (-c copy),
    AAC ADTS to ASC bitstream filter (-bsf:a aac_adtstoasc), faststart flag,
    and automatic reconnection persistence.

    Preserves authorized signed tokens and query parameters exactly.
    Handles timestamp discontinuity warnings without failing.
    Validates the generated MP4 on the B: project storage.
    """
    if not url or not isinstance(url, str) or not url.strip():
        logger.error("[KGS DOWNLOAD] Invalid or empty URL.")
        return None

    import itsgolu as helper

    out_dir = custom_dir or os.path.join(TEMP_DIR, "kgs")
    os.makedirs(out_dir, exist_ok=True)

    safe_title = helper.safe_filename(name) if hasattr(helper, "safe_filename") else extract_clean_kgs_title(url, name)
    if not safe_title or safe_title == ".mp4":
        safe_title = f"KGS_Lecture_{int(time.time())}"
    if safe_title.endswith(".mp4"):
        safe_title = safe_title[:-4]

    output_path = os.path.join(out_dir, f"{safe_title}.mp4")

    # Step 1: Resolve master/variant playlist
    resolved_headers = dict(DEFAULT_KGS_HEADERS)
    if isinstance(custom_headers, dict):
        resolved_headers.update(custom_headers)

    stream_url, meta = resolve_kgs_playlist(url, preferred_quality=quality, custom_headers=resolved_headers)

    # If authorization is rejected (403/401), stop immediately without blind retries
    if meta.get("auth_error") or meta.get("error") == "KGS_AUTH_403":
        logger.error("[KGS DOWNLOAD] KGS authorization rejected or expired (403). Stopping immediately.")
        return None

    # Step 2: Format FFmpeg headers string
    if isinstance(custom_headers, str) and "\r\n" in custom_headers:
        formatted_headers = custom_headers
    else:
        hdr_lines = [f"{k}: {v}" for k, v in resolved_headers.items()]
        formatted_headers = "\r\n".join(hdr_lines) + "\r\n"

    ffmpeg_bin = getattr(helper, "FFMPEG_PATH", None) or "ffmpeg"

    cmd = [
        ffmpeg_bin,
        "-y",
        "-loglevel", "warning",
        "-headers", formatted_headers,
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-multiple_requests", "1",
        "-i", stream_url.strip(),
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        output_path
    ]

    max_retries = 3
    retry_count = 0
    download_success = False

    while retry_count < max_retries:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass

        logger.info(f"[KGS DOWNLOAD] Attempt {retry_count + 1}/{max_retries} -> {output_path}")
        print(f"[KGS DOWNLOAD] Downloading KGS HLS stream (attempt {retry_count + 1}) to {safe_title}.mp4...")

        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            stderr_output = proc.stderr or ""
            # Inspect stderr for non-fatal timestamp discontinuity warnings
            if "timestamp discontinuity" in stderr_output.lower() or "non-monotonous" in stderr_output.lower():
                logger.info("[KGS DOWNLOAD] Timestamp discontinuity warnings present in stream (non-fatal).")

            # Check for immediate 403 Forbidden rejection -> abort immediately
            if "403 forbidden" in stderr_output.lower() or "server returned 403" in stderr_output.lower() or "server returned 401" in stderr_output.lower():
                logger.error("[KGS DOWNLOAD] Server returned 403 Forbidden (authorization expired or invalid). Stopping immediately.")
                return None

            if proc.returncode == 0:
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    logger.info(f"[KGS DOWNLOAD] FFmpeg finished successfully. Size: {os.path.getsize(output_path)/(1024*1024):.2f} MB")
                    download_success = True
                    break
                else:
                    logger.warning("[KGS DOWNLOAD] FFmpeg returned 0 but output file is missing or 0 bytes.")
            else:
                retry_count += 1
                sanitized_err = re.sub(r"(hdnts|hdntl|hmac|token|key|auth|sig)=[^&\s]+", r"\1=[REDACTED]", stderr_output)
                logger.warning(f"[KGS DOWNLOAD] FFmpeg failed (code {proc.returncode}): {sanitized_err.strip()[:300]}")
                if retry_count < max_retries:
                    time.sleep(1)

        except RuntimeError:
            raise
        except Exception as exc:
            retry_count += 1
            logger.error(f"[KGS DOWNLOAD] Exception running FFmpeg: {exc}")
            if retry_count < max_retries:
                time.sleep(1)

    if download_success and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        # Step 3: Validate playability via ffprobe / is_playable
        if hasattr(helper, "is_playable"):
            try:
                if not helper.is_playable(output_path):
                    logger.warning("[KGS DOWNLOAD] File validation: not directly playable, attempting repair.")
                    if hasattr(helper, "repair_video"):
                        repaired = helper.repair_video(output_path)
                        if repaired and os.path.exists(repaired) and os.path.getsize(repaired) > 0:
                            output_path = repaired
            except Exception as e:
                logger.warning(f"[KGS DOWNLOAD] Playability check notice: {e}")

        print(f"[KGS DOWNLOAD] Stream download complete: {output_path} ({os.path.getsize(output_path)/(1024*1024):.1f} MB)")
        return output_path

    logger.error(f"[KGS DOWNLOAD] All download attempts failed for {safe_title}.")
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except OSError:
            pass
    return None
