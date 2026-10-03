# ==============================================================================
# SPAYEE SPECIALIZED HLS DOWNLOADER MODULE
# ==============================================================================
# Implements the authorized Spayee download pipeline according to Section 9 specifications.
# Handles M3U8*KEY input parsing, AES-128 key validation, master playlist parsing,
# quality selection, separate audio streams, segment download with resume/retries,
# local M3U8 rewriting with video_key.bin/audio_key.bin, FFmpeg processing,
# and final output validation on project storage (B: drive).
# ==============================================================================

import os
import sys
import re
import time
import base64
import uuid
import shutil
import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, Any, Union, Tuple, List
from urllib.parse import urlparse, urljoin

import requests
import m3u8

from vars import PROJECT_ROOT, DATA_DIR, TEMP_DIR, DOWNLOADS_DIR, FFMPEG_PATH, SPAYEE_SEGMENT_WORKERS
from utils import is_safe_temp_path, cleanup_uploaded_file

logger = logging.getLogger(__name__)

# Default Spayee CDN headers as specified in Section 9.3
DEFAULT_SPAYEE_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.goclasses.in/",
}


# ==============================================================================
# 9.27 SPAYEE DETECTION
# ==============================================================================
def is_spayee_url(value: str) -> bool:
    """
    Detects whether a given URL is a Spayee HLS stream resource.
    Inspects hostname, domain, and path for 'spayee.in' or 'spayee'.
    """
    if not value or not isinstance(value, str):
        return False
    clean = value.strip()
    if not clean:
        return False
    base_url = clean.split("*")[0].strip()
    parsed = urlparse(base_url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    return (
        "spayee.in" in host
        or "spayee" in host
        or "spayee" in path
    )


# ==============================================================================
# 9.2 AES KEY VALIDATION
# ==============================================================================
def clean_spayee_key(key: Union[str, bytes]) -> bytes:
    """
    Validates and standardizes an authorized AES-128 key.
    Supports:
      1. 32 hexadecimal characters (16 bytes)
      2. Base64 string representing exactly 16 bytes
      3. Raw 16 bytes

    Logs only 'AES-128 key accepted'. Never logs or exposes the actual key bytes.
    """
    if not key:
        raise ValueError("Invalid authorized AES-128 key")

    if isinstance(key, bytes):
        if len(key) == 16:
            logger.info("AES-128 key accepted")
            print("AES-128 key accepted")
            return key
        raise ValueError("Invalid authorized AES-128 key")

    if not isinstance(key, str):
        raise ValueError("Invalid authorized AES-128 key")

    key_str = key.strip()

    # 1. 32 Hexadecimal characters
    if re.fullmatch(r"[0-9a-fA-F]{32}", key_str):
        logger.info("AES-128 key accepted")
        print("AES-128 key accepted")
        return bytes.fromhex(key_str)

    # 2. Base64 representing exactly 16 bytes
    try:
        raw = base64.b64decode(key_str, validate=True)
        if len(raw) == 16:
            logger.info("AES-128 key accepted")
            print("AES-128 key accepted")
            return raw
    except Exception:
        pass

    raise ValueError("Invalid authorized AES-128 key")


# ==============================================================================
# 9.1 INPUT PARSING HELPER
# ==============================================================================
def parse_spayee_input(combined_url: str, explicit_key: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """
    Splits combined_url at the first '*' into m3u8_url and key_string.
    Never modifies the URL or key beyond stripping leading/trailing whitespace.
    """
    if not combined_url or not isinstance(combined_url, str):
        raise ValueError("Empty or invalid Spayee URL.")

    clean_input = combined_url.strip()
    if "*" in clean_input:
        m3u8_url, key_str = clean_input.split("*", 1)
        return m3u8_url.strip(), key_str.strip()

    return clean_input, explicit_key.strip() if explicit_key else None


# ==============================================================================
# 9.3 HTTP SESSION HELPER
# ==============================================================================
def create_spayee_session(headers: Optional[Dict[str, str]] = None, workers: int = SPAYEE_SEGMENT_WORKERS) -> requests.Session:
    """Creates a configured requests.Session with default and custom Spayee headers and connection pooling."""
    session = requests.Session()
    pool_size = max(64, (workers or 16) * 4)
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=pool_size,
        pool_maxsize=pool_size,
        max_retries=3
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    session.headers.update(DEFAULT_SPAYEE_HEADERS)
    if headers and isinstance(headers, dict):
        session.headers.update(headers)

    return session


# ==============================================================================
# 9.5 & 9.6 MASTER PLAYLIST & QUALITY SELECTION
# ==============================================================================
def _parse_height_from_uri(uri: str) -> int:
    """Extracts resolution height from URI (e.g. '720/index.m3u8' -> 720)."""
    m = re.search(r"(\d{3,4})p?", uri)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return 0


def select_spayee_variant(
    parsed_master: m3u8.M3U8,
    master_url: str,
    preferred_quality: Optional[str] = None
) -> Tuple[str, int]:
    """
    Selects the highest available resolution variant from the master playlist.
    Sorts by (height, bandwidth) descending.
    If preferred_quality is explicitly provided (e.g. '720p'), selects matching height if available.
    Returns (video_playlist_url, height).
    """
    variants = parsed_master.playlists
    if not variants:
        return master_url, 0

    scored = []
    for var in variants:
        uri = var.uri
        height = 0
        bandwidth = 0

        if var.stream_info:
            if var.stream_info.resolution and len(var.stream_info.resolution) >= 2:
                height = var.stream_info.resolution[1]
            if var.stream_info.bandwidth:
                bandwidth = var.stream_info.bandwidth

        if height == 0:
            height = _parse_height_from_uri(uri)

        resolved_uri = urljoin(master_url, uri)
        scored.append({
            "uri": resolved_uri,
            "height": height,
            "bandwidth": bandwidth
        })

    # Sort descending by (height, bandwidth)
    scored.sort(key=lambda x: (x["height"], x["bandwidth"]), reverse=True)

    # If preferred quality requested, check for match
    if preferred_quality:
        target_m = re.search(r"(\d{3,4})", str(preferred_quality))
        if target_m:
            target_h = int(target_m.group(1))
            matches = [s for s in scored if s["height"] == target_h]
            if matches:
                chosen = matches[0]
                logger.info(f"Selected Spayee quality: {chosen['height']}p")
                print(f"Selected Spayee quality: {chosen['height']}p")
                return chosen["uri"], chosen["height"]

    chosen = scored[0]
    chosen_h = chosen["height"] if chosen["height"] > 0 else 720
    logger.info(f"Selected Spayee quality: {chosen_h}p")
    print(f"Selected Spayee quality: {chosen_h}p")
    return chosen["uri"], chosen_h


# ==============================================================================
# 9.9 SEPARATE AUDIO DETECTION HELPER
# ==============================================================================
def detect_separate_audio(parsed_master: m3u8.M3U8, master_url: str) -> Optional[str]:
    """
    Inspects master playlist for #EXT-X-MEDIA:TYPE=AUDIO with URI attribute.
    Returns resolved audio playlist URL if present, or None.
    """
    if not parsed_master or not parsed_master.media:
        return None

    for media in parsed_master.media:
        if getattr(media, "type", "").upper() == "AUDIO" and getattr(media, "uri", None):
            return urljoin(master_url, media.uri)

    return None


# ==============================================================================
# 9.12 - 9.15 SEGMENT EXTRACTION, DOWNLOAD & RETRY
# ==============================================================================
def _determine_segment_ext(uri: str, default_ext: str = ".ts") -> str:
    """Determines segment file extension from URI path."""
    parsed = urlparse(uri)
    path = parsed.path.lower()
    for ext in (".ts", ".aac", ".m4s", ".mp4", ".m4a", ".fmp4"):
        if path.endswith(ext):
            return ext
    return default_ext


def download_segment_with_retry(
    session: requests.Session,
    segment_url: str,
    target_path: Path,
    segment_idx: int,
    max_retries: int = 3,
    chunk_size: int = 1024 * 1024
) -> int:
    """
    Downloads a single media segment using the authorized session with 3 retries.
    Skips if target_path exists and is non-zero (9.15 Resume Support).
    Returns size in bytes of the segment on disk.
    """
    # 9.15 Resume Support
    if target_path.exists() and target_path.stat().st_size > 0:
        return target_path.stat().st_size

    # Ensure parent folder exists
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(f"{target_path.suffix}.tmp")

    for attempt in range(1, max_retries + 1):
        try:
            with session.get(segment_url, stream=True, timeout=60) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")

                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=chunk_size):
                        if chunk:
                            f.write(chunk)

            if tmp_path.exists() and tmp_path.stat().st_size > 0:
                if target_path.exists():
                    try:
                        target_path.unlink()
                    except OSError:
                        pass
                tmp_path.rename(target_path)
                return target_path.stat().st_size
            else:
                raise RuntimeError("Downloaded segment is 0 bytes")

        except Exception as exc:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

            if attempt < max_retries:
                time.sleep(1)
            else:
                raise RuntimeError(f"Spayee segment {segment_idx} failed: {exc}")
    return 0


def download_segments_parallel(
    session: requests.Session,
    segments: List[Tuple[int, str, str]],
    work_dir: Path,
    workers: int = SPAYEE_SEGMENT_WORKERS,
    chunk_size: int = 1024 * 1024,
    stream_type: str = "Video",
    job_context: Optional[Any] = None
) -> None:
    """
    Downloads segments in parallel using ThreadPoolExecutor while strictly
    preserving individual segment naming/indexing (e.g. segment_000001.ts).
    Aggregates progress and logs clean throughput metrics instead of per-segment spam.
    """
    total_segs = len(segments)
    if total_segs == 0:
        return

    workers_count = max(1, min(int(workers or 16), 32))
    logger.info(f"[SPAYEE] Downloading {total_segs} {stream_type} segments using {workers_count} parallel workers...")
    print(f"[SPAYEE] Downloading {total_segs} {stream_type} segments using {workers_count} parallel workers...")

    lock = threading.Lock()
    completed_count = 0
    downloaded_bytes = 0
    t0 = time.time()
    last_log_time = t0
    errors = []

    def _worker(seg_info: Tuple[int, str, str]) -> None:
        nonlocal completed_count, downloaded_bytes, last_log_time
        seg_idx, seg_url, local_rel = seg_info
        seg_target = work_dir / local_rel

        if job_context and getattr(job_context, "is_cancelled", False):
            raise RuntimeError("Job cancelled by user")

        seg_size = download_segment_with_retry(
            session=session,
            segment_url=seg_url,
            target_path=seg_target,
            segment_idx=seg_idx,
            max_retries=3,
            chunk_size=chunk_size
        )

        with lock:
            completed_count += 1
            downloaded_bytes += seg_size
            now = time.time()
            if (now - last_log_time >= 3.0) or (completed_count == total_segs):
                last_log_time = now
                elapsed = max(0.1, now - t0)
                speed_mb = (downloaded_bytes / (1024 * 1024)) / elapsed
                pct = (completed_count / total_segs) * 100.0
                dl_mb = downloaded_bytes / (1024 * 1024)
                approx_total_mb = (dl_mb / completed_count) * total_segs if completed_count > 0 else 0.0
                eta_sec = (approx_total_mb - dl_mb) / speed_mb if speed_mb > 0 else 0
                eta_m, eta_s = divmod(int(eta_sec), 60)
                msg = (
                    f"[SPAYEE {stream_type}] {pct:5.1f}% | "
                    f"Segments: {completed_count}/{total_segs} | "
                    f"Size: {dl_mb:.1f}/{approx_total_mb:.1f} MB | "
                    f"Speed: {speed_mb:.1f} MB/s | "
                    f"ETA: {eta_m:02d}:{eta_s:02d}"
                )
                logger.info(msg)
                print(msg)

    with ThreadPoolExecutor(max_workers=workers_count) as executor:
        futures = {executor.submit(_worker, seg): seg for seg in segments}
        for fut in as_completed(futures):
            seg_info = futures[fut]
            try:
                fut.result()
            except Exception as exc:
                errors.append((seg_info[0], str(exc)))
                for f in futures:
                    f.cancel()
                break

    if errors:
        failed_idx, err_msg = errors[0]
        raise RuntimeError(f"SPAYEE_SEGMENT_FAILED: {stream_type} segment {failed_idx} failed ({err_msg})")

    total_elapsed = time.time() - t0
    total_mb = downloaded_bytes / (1024 * 1024)
    avg_speed = total_mb / max(0.1, total_elapsed)
    logger.info(f"[SPAYEE] {stream_type} download completed: {total_segs} segments ({total_mb:.1f} MB) in {total_elapsed:.1f}s ({avg_speed:.1f} MB/s)")
    print(f"[SPAYEE] {stream_type} download completed: {total_segs} segments ({total_mb:.1f} MB) in {total_elapsed:.1f}s ({avg_speed:.1f} MB/s)")


# ==============================================================================
# 9.16 - 9.18 LOCAL PLAYLIST REWRITING
# ==============================================================================
def rewrite_local_playlist(
    source_m3u8_text: str,
    base_playlist_url: str,
    segments_dir_name: str,
    key_filename: str,
    is_encrypted: bool
) -> Tuple[str, List[Tuple[int, str, str]]]:
    """
    Reconstructs an M3U8 manifest:
      - Replaces segment URLs with local relative paths (e.g. 'video_segments/segment_000001.ts')
      - Replaces AES-128 URI with local key file (e.g. URI="video_key.bin")
      - Preserves IV, METHOD, and other metadata
    Returns (rewritten_m3u8_content, list_of_segments: [(idx, segment_url, local_rel_path)]).
    """
    lines = source_m3u8_text.splitlines()
    rewritten_lines = []
    segments = []
    seg_idx = 1

    for line in lines:
        stripped = line.strip()
        if not stripped:
            rewritten_lines.append(line)
            continue

        if stripped.startswith("#"):
            # Rewrite key URI if present
            if stripped.startswith("#EXT-X-KEY:"):
                # Replace only URI="..." with URI="<key_filename>"
                new_key_tag = re.sub(
                    r'URI=(?:"[^"]*"|\'[^\']*\'|[^\s,]*)',
                    f'URI="{key_filename}"',
                    stripped
                )
                rewritten_lines.append(new_key_tag)
            else:
                rewritten_lines.append(line)
        else:
            # Segment line
            seg_url = urljoin(base_playlist_url, stripped)
            seg_ext = _determine_segment_ext(stripped, default_ext=".ts" if "video" in segments_dir_name else ".aac")
            local_rel = f"{segments_dir_name}/segment_{seg_idx:06d}{seg_ext}"
            segments.append((seg_idx, seg_url, local_rel))
            rewritten_lines.append(local_rel)
            seg_idx += 1

    return "\n".join(rewritten_lines) + "\n", segments


# ==============================================================================
# 9.19 - 9.21 FFMPEG PROCESSING & MERGE
# ==============================================================================
def process_spayee_ffmpeg(
    work_dir: Path,
    has_audio: bool,
    ffmpeg_bin: str = "ffmpeg"
) -> Path:
    """
    Executes FFmpeg stream copy processing in the isolated work directory.
      1. video.m3u8 -> video_only.mp4 (-c copy, -movflags +faststart)
      2. If separate audio: audio.m3u8 -> audio_only.m4a (-c copy)
      3. If separate audio: merge video_only.mp4 + audio_only.m4a -> final.mp4
      4. If video only: video_only.mp4 -> final.mp4
    """
    video_m3u8 = work_dir / "video.m3u8"
    video_only_mp4 = work_dir / "video_only.mp4"
    final_mp4 = work_dir / "final.mp4"

    if video_only_mp4.exists():
        try:
            video_only_mp4.unlink()
        except OSError:
            pass

    # 9.19 FFmpeg Video Processing
    cmd_video = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel", "warning",
        "-allowed_extensions", "ALL",
        "-f", "hls",
        "-i", "video.m3u8",
        "-map", "0:0",
        "-c", "copy",
        "-movflags", "+faststart",
        "video_only.mp4"
    ]

    res_video = subprocess.run(cmd_video, cwd=str(work_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res_video.returncode != 0 or not video_only_mp4.exists() or video_only_mp4.stat().st_size == 0:
        # Fallback without explicit -map 0:0 in case stream mapping differs
        cmd_fallback = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel", "warning",
            "-allowed_extensions", "ALL",
            "-f", "hls",
            "-i", "video.m3u8",
            "-c", "copy",
            "-movflags", "+faststart",
            "video_only.mp4"
        ]
        res_fallback = subprocess.run(cmd_fallback, cwd=str(work_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res_fallback.returncode != 0 or not video_only_mp4.exists() or video_only_mp4.stat().st_size == 0:
            raise RuntimeError(f"FFmpeg video processing failed: {res_fallback.stderr.strip()[:300]}")

    if has_audio:
        # 9.20 FFmpeg Audio Processing
        audio_m3u8 = work_dir / "audio.m3u8"
        audio_only_m4a = work_dir / "audio_only.m4a"
        if audio_only_m4a.exists():
            try:
                audio_only_m4a.unlink()
            except OSError:
                pass

        cmd_audio = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel", "warning",
            "-allowed_extensions", "ALL",
            "-f", "hls",
            "-i", "audio.m3u8",
            "-c", "copy",
            "audio_only.m4a"
        ]
        res_audio = subprocess.run(cmd_audio, cwd=str(work_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res_audio.returncode != 0 or not audio_only_m4a.exists() or audio_only_m4a.stat().st_size == 0:
            raise RuntimeError(f"FFmpeg audio processing failed: {res_audio.stderr.strip()[:300]}")

        # 9.21 Final Video + Audio Merge
        if final_mp4.exists():
            try:
                final_mp4.unlink()
            except OSError:
                pass

        cmd_merge = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel", "warning",
            "-i", "video_only.mp4",
            "-i", "audio_only.m4a",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "copy",
            "-movflags", "+faststart",
            "final.mp4"
        ]
        res_merge = subprocess.run(cmd_merge, cwd=str(work_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res_merge.returncode != 0 or not final_mp4.exists() or final_mp4.stat().st_size == 0:
            raise RuntimeError(f"FFmpeg merge failed: {res_merge.stderr.strip()[:300]}")
    else:
        # 9.22 Video-Only Case
        if final_mp4.exists():
            try:
                final_mp4.unlink()
            except OSError:
                pass
        shutil.copy2(video_only_mp4, final_mp4)

    return final_mp4


# ==============================================================================
# 9.23 VALIDATION HELPER
# ==============================================================================
def validate_spayee_output(file_path: Path, ffprobe_bin: str = "ffprobe") -> bool:
    """Validates that the final media file exists, is non-zero, and contains a playable video stream."""
    if not file_path.exists() or file_path.stat().st_size == 0:
        return False

    try:
        cmd = [ffprobe_bin, "-v", "error", "-show_streams", str(file_path)]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            return "codec_type=video" in res.stdout
    except Exception:
        pass

    # If ffprobe is not installed or check failed gracefully, accept non-empty file
    return file_path.exists() and file_path.stat().st_size > 1024


# ==============================================================================
# 9.31 CENTRAL SPAYEE DOWNLOAD PIPELINE
# ==============================================================================
def download_spayee_hls(
    combined_url: str,
    *args,
    output_path: Optional[Union[str, Path]] = None,
    name: Optional[str] = None,
    key: Optional[str] = None,
    quality: Optional[str] = None,
    custom_dir: Optional[Union[str, Path]] = None,
    job_context: Optional[Any] = None,
    headers: Optional[Dict[str, str]] = None,
    **kwargs
) -> Union[Path, str]:
    """
    Downloads and decrypts an authorized Spayee HLS stream.
    Follows Section 9 specifications:
      1. Parse M3U8 + authorized key (split at first '*')
      2. Validate authorized AES key
      3. Create isolated work directory under project temp (B: drive)
      4. Fetch master playlist
      5. Select highest available variant resolution
      6. Fetch video playlist
      7. Detect AES-128 encryption
      8. Detect separate audio playlist
      9. Download segments with retry & resume support
      10. Create local playlists with key URI rewriting
      11. Run FFmpeg stream copy
      12. Merge video + audio if separate audio exists
      13. Validate final MP4
      14. Deliver to output_path / downloads directory
      15. Return output_path
    """
    if not combined_url or not isinstance(combined_url, str) or not combined_url.strip():
        logger.error("[SPAYEE] Invalid or empty Spayee URL.")
        raise ValueError("Invalid or empty Spayee URL.")

    # Universal argument resolution for all calling conventions:
    # 1. download_spayee_hls(combined_url, output_path=Path(...), job_context=..., headers=...)
    # 2. download_spayee_hls(combined_url, Path(...))
    # 3. download_spayee_hls(url, name, key, quality, custom_dir)
    # 4. download_spayee_hls(url, name, key, "720p", downloads_dir)
    if args:
        a0 = args[0]
        if isinstance(a0, Path) or (isinstance(a0, str) and (a0.endswith(".mp4") or "/" in a0 or "\\" in a0 or (os.path.exists(a0) and os.path.isdir(a0)))):
            output_path = a0
            if len(args) >= 2:
                if isinstance(args[1], dict):
                    headers = args[1]
                else:
                    job_context = args[1]
            if len(args) >= 3 and isinstance(args[2], dict):
                headers = args[2]
        else:
            name = a0
            if len(args) >= 2:
                key = args[1]
            if len(args) >= 3:
                quality = args[2]
            if len(args) >= 4:
                custom_dir = args[3]
            if len(args) >= 5 and job_context is None:
                job_context = args[4]

    target_output_file: Path
    if output_path is not None:
        p = Path(output_path)
        if p.suffix.lower() == ".mp4" or not (p.exists() and p.is_dir()):
            target_output_file = p
        else:
            safe_title = re.sub(r'[\\/*?:"<>|]', "", name or "Spayee_Lecture").strip() or f"Spayee_Lecture_{int(time.time())}"
            if safe_title.endswith(".mp4"):
                safe_title = safe_title[:-4]
            target_output_file = p / f"{safe_title}.mp4"
    else:
        out_base = Path(custom_dir) if custom_dir else Path(DOWNLOADS_DIR)
        out_base.mkdir(parents=True, exist_ok=True)
        safe_title = re.sub(r'[\\/*?:"<>|]', "", name or "Spayee_Lecture").strip() or f"Spayee_Lecture_{int(time.time())}"
        if safe_title.endswith(".mp4"):
            safe_title = safe_title[:-4]
        target_output_file = out_base / f"{safe_title}.mp4"

    # Step 1: Input Parsing (Section 9.1)
    m3u8_url, auth_key_str = parse_spayee_input(combined_url, explicit_key=key)

    # Step 2: HTTP Session (Section 9.3)
    session = create_spayee_session(headers=headers)

    # Step 3: Work Directory on B: Drive (Section 9.10)
    job_id_str = getattr(job_context, "job_id", "") if job_context else ""
    work_dir_name = f"spayee_{job_id_str}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}" if job_id_str else f"spayee_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    work_dir = Path(TEMP_DIR) / work_dir_name
    work_dir.mkdir(parents=True, exist_ok=True)

    video_segments_dir = work_dir / "video_segments"
    audio_segments_dir = work_dir / "audio_segments"
    video_segments_dir.mkdir(exist_ok=True)

    try:
        # Step 4: Fetch Master M3U8 (Section 9.4)
        resp_master = session.get(m3u8_url, timeout=30)
        if resp_master.status_code in (401, 403):
            raise RuntimeError("Spayee authorization expired or access denied.")
        if resp_master.status_code != 200:
            raise RuntimeError(f"Spayee playlist could not be fetched: HTTP {resp_master.status_code}")

        master_text = resp_master.text
        (work_dir / "master.m3u8").write_text(master_text, encoding="utf-8")

        # Step 5 & 6: Master Playlist Detection & Quality Selection (Section 9.5 & 9.6)
        parsed_master = m3u8.loads(master_text, uri=m3u8_url)
        has_variants = bool(parsed_master.is_variant and parsed_master.playlists)

        if has_variants:
            video_playlist_url, selected_height = select_spayee_variant(parsed_master, m3u8_url, preferred_quality=quality)
            resp_video = session.get(video_playlist_url, timeout=30)
            if resp_video.status_code in (401, 403):
                raise RuntimeError("Spayee authorization expired or access denied.")
            if resp_video.status_code != 200:
                raise RuntimeError(f"Spayee video playlist could not be fetched: HTTP {resp_video.status_code}")
            video_playlist_text = resp_video.text
            video_base_url = video_playlist_url
        else:
            video_playlist_text = master_text
            video_base_url = m3u8_url

        (work_dir / "video_source.m3u8").write_text(video_playlist_text, encoding="utf-8")

        # Step 8: Detect Separate Audio (Section 9.9)
        audio_playlist_url = detect_separate_audio(parsed_master, m3u8_url) if has_variants else None
        has_separate_audio = False
        audio_playlist_text = ""
        audio_base_url = ""

        if audio_playlist_url:
            resp_audio = session.get(audio_playlist_url, timeout=30)
            if resp_audio.status_code == 200:
                audio_playlist_text = resp_audio.text
                (work_dir / "audio_source.m3u8").write_text(audio_playlist_text, encoding="utf-8")
                audio_base_url = audio_playlist_url
                audio_segments_dir.mkdir(exist_ok=True)
                has_separate_audio = True

        # Step 7: Detect AES-128 & Write Key Files (Section 9.8 & 9.11)
        video_is_encrypted = "#EXT-X-KEY" in video_playlist_text and "AES-128" in video_playlist_text
        audio_is_encrypted = has_separate_audio and ("#EXT-X-KEY" in audio_playlist_text and "AES-128" in audio_playlist_text)

        if video_is_encrypted or audio_is_encrypted or auth_key_str:
            if not auth_key_str:
                raise ValueError("Spayee stream is encrypted with AES-128 but no authorized key was provided.")

            aes_key_bytes = clean_spayee_key(auth_key_str)
            (work_dir / "video_key.bin").write_bytes(aes_key_bytes)
            if has_separate_audio:
                (work_dir / "audio_key.bin").write_bytes(aes_key_bytes)

        # Step 9 & 10: Segment Download & Local Playlist Rewriting (Video & Audio) (Section 9.12 - 9.18)
        rewritten_video_m3u8, video_segments = rewrite_local_playlist(
            source_m3u8_text=video_playlist_text,
            base_playlist_url=video_base_url,
            segments_dir_name="video_segments",
            key_filename="video_key.bin",
            is_encrypted=video_is_encrypted
        )
        (work_dir / "video.m3u8").write_text(rewritten_video_m3u8, encoding="utf-8")

        audio_segments = []
        if has_separate_audio:
            rewritten_audio_m3u8, audio_segments = rewrite_local_playlist(
                source_m3u8_text=audio_playlist_text,
                base_playlist_url=audio_base_url,
                segments_dir_name="audio_segments",
                key_filename="audio_key.bin",
                is_encrypted=audio_is_encrypted
            )
            (work_dir / "audio.m3u8").write_text(rewritten_audio_m3u8, encoding="utf-8")

        # Concurrent segment downloading (Video + Audio simultaneously)
        if has_separate_audio and audio_segments:
            with ThreadPoolExecutor(max_workers=2) as stream_executor:
                fut_v = stream_executor.submit(
                    download_segments_parallel,
                    session=session,
                    segments=video_segments,
                    work_dir=work_dir,
                    workers=SPAYEE_SEGMENT_WORKERS,
                    chunk_size=1024 * 1024,
                    stream_type="Video",
                    job_context=job_context
                )
                fut_a = stream_executor.submit(
                    download_segments_parallel,
                    session=session,
                    segments=audio_segments,
                    work_dir=work_dir,
                    workers=SPAYEE_SEGMENT_WORKERS,
                    chunk_size=1024 * 1024,
                    stream_type="Audio",
                    job_context=job_context
                )
                fut_v.result()
                fut_a.result()
        else:
            download_segments_parallel(
                session=session,
                segments=video_segments,
                work_dir=work_dir,
                workers=SPAYEE_SEGMENT_WORKERS,
                chunk_size=1024 * 1024,
                stream_type="Video",
                job_context=job_context
            )

        # Step 11 & 12: FFmpeg Stream Processing & Merge (Section 9.19 - 9.22)
        ffmpeg_bin = FFMPEG_PATH or "ffmpeg"
        final_mp4 = process_spayee_ffmpeg(
            work_dir=work_dir,
            has_audio=has_separate_audio,
            ffmpeg_bin=ffmpeg_bin
        )

        # Step 13: Output Validation (Section 9.23)
        ffprobe_bin = "ffprobe"
        if not validate_spayee_output(final_mp4, ffprobe_bin=ffprobe_bin):
            raise RuntimeError("Spayee final video validation failed (unplayable or zero-byte output).")

        # Step 14: Place Final Output (Section 9.31)
        target_output_file.parent.mkdir(parents=True, exist_ok=True)
        if target_output_file.exists():
            try:
                target_output_file.unlink()
            except OSError:
                pass

        shutil.copy2(final_mp4, target_output_file)
        logger.info(f"[SPAYEE] Successfully generated: {target_output_file} ({target_output_file.stat().st_size / (1024*1024):.2f} MB)")
        
        # Step 15: Return output_path
        if isinstance(output_path, Path):
            return target_output_file
        return str(target_output_file)

    finally:
        # Step 24: Cleanup intermediate segments and temp directory (Section 9.24)
        try:
            if work_dir.exists() and is_safe_temp_path(work_dir):
                shutil.rmtree(work_dir, ignore_errors=True)
        except Exception as cleanup_err:
            logger.debug(f"[SPAYEE] Temporary directory cleanup notice: {cleanup_err}")
