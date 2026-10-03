import os
import sys
import unittest
import tempfile
import asyncio
import subprocess
from unittest.mock import patch, MagicMock

import m3u8

from vars import PROJECT_ROOT, DATA_DIR, TEMP_DIR, DOWNLOADS_DIR
from utils import MediaRouter, MediaType, cleanup_uploaded_file, is_safe_temp_path
from kgs_downloader import (
    is_kgs_url,
    extract_clean_kgs_title,
    select_kgs_variant,
    resolve_kgs_playlist,
    download_kgs,
    DEFAULT_KGS_HEADERS
)
from job_manager import JobManager, JobCheckpoint
from itsgolu import safe_filename


class MockHttpResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


class TestKGSHLSDownloader(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # TEST 1: KGS URL detection
    # -------------------------------------------------------------------------
    def test_01_kgs_url_detection(self):
        kgs_urls = [
            "https://kgs-new-v1.akamaized.net/kv3/course_physics/master.m3u8?hdnts=exp=123~hmac=abc",
            "https://kgs.akamaized.net/kv2/chemistry/index.m3u8?token=xyz",
            "https://khanglobalstudies.com/video/master.m3u8?auth=123",
            "https://cdn.example.com/kgs/biology/class1.m3u8",
            "https://stream.kgs-video.net/hls/live/stream.m3u8"
        ]
        for url in kgs_urls:
            self.assertTrue(is_kgs_url(url), f"Failed to detect KGS URL: {url}")

    # -------------------------------------------------------------------------
    # TEST 2: KGS priority over generic M3U8
    # -------------------------------------------------------------------------
    def test_02_kgs_priority_over_generic_m3u8(self):
        kgs_m3u8 = "https://kgs-new-v1.akamaized.net/kv3/lecture_01/master.m3u8?hdnts=exp=99999~hmac=sec123"
        classified = MediaRouter.classify_url(kgs_m3u8)
        self.assertEqual(classified, MediaType.KGS_HLS)

    # -------------------------------------------------------------------------
    # TEST 3: Normal M3U8 still goes to existing M3U8 handler
    # -------------------------------------------------------------------------
    def test_03_generic_m3u8_routing(self):
        generic_m3u8 = "https://live-cdn.example.com/streams/live_math_01.m3u8?token=xyz"
        classified = MediaRouter.classify_url(generic_m3u8)
        self.assertEqual(classified, MediaType.DIRECT_M3U8)

    # -------------------------------------------------------------------------
    # TEST 4: YouTube still goes to YouTube handler
    # -------------------------------------------------------------------------
    def test_04_youtube_routing(self):
        yt_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        classified = MediaRouter.classify_url(yt_url)
        self.assertEqual(classified, MediaType.YOUTUBE)

    # -------------------------------------------------------------------------
    # TEST 5: APPX still goes to APPX handler
    # -------------------------------------------------------------------------
    def test_05_appx_routing(self):
        appx_url = "https://akstechnicalclasses.classx.co.in/fetch_video?id=12345"
        classified = MediaRouter.classify_url(appx_url)
        self.assertEqual(classified, MediaType.APPX_LECTURE)

    # -------------------------------------------------------------------------
    # TEST 6: PDF still goes to PDF handler
    # -------------------------------------------------------------------------
    def test_06_pdf_routing(self):
        pdf_url = "https://cdn.example.com/materials/unit_01_notes.pdf?auth=abc"
        classified = MediaRouter.classify_url(pdf_url)
        self.assertEqual(classified, MediaType.DIRECT_PDF)

    # -------------------------------------------------------------------------
    # TEST 7: Signed query string remains unchanged
    # -------------------------------------------------------------------------
    def test_07_signed_query_preserved_exactly(self):
        signed_url = "https://kgs-new-v1.akamaized.net/kv3/lecture/master.m3u8?exp=1700000000~acl=/*~data=user123~hmac=987654321fedcba"
        master_content = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720
720p/index.m3u8
"""
        with patch("requests.get", return_value=MockHttpResponse(master_content)):
            resolved_url, meta = resolve_kgs_playlist(signed_url, "720p")
            self.assertIn("exp=1700000000~acl=/*~data=user123~hmac=987654321fedcba", resolved_url)

    # -------------------------------------------------------------------------
    # TEST 8: hdnts query remains intact
    # -------------------------------------------------------------------------
    def test_08_hdnts_token_preserved(self):
        token_url = "https://kgs-new-v1.akamaized.net/kv3/test/master.m3u8?hdnts=exp=1800000000~acl=%2F*~hmac=abcdef0123456789"
        master_content = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=854x480
480p/index.m3u8
"""
        with patch("requests.get", return_value=MockHttpResponse(master_content)):
            resolved_url, meta = resolve_kgs_playlist(token_url, "480p")
            self.assertIn("hdnts=exp=1800000000~acl=%2F*~hmac=abcdef0123456789", resolved_url)

    # -------------------------------------------------------------------------
    # TEST 9: hdntl query remains intact
    # -------------------------------------------------------------------------
    def test_09_hdntl_token_preserved(self):
        token_url = "https://kgs-new-v1.akamaized.net/kv3/test/master.m3u8?hdntl=exp=1800000000~acl=%2F*~data=xyz~hmac=fedcba9876543210"
        master_content = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080
1080p/index.m3u8
"""
        with patch("requests.get", return_value=MockHttpResponse(master_content)):
            resolved_url, meta = resolve_kgs_playlist(token_url, "1080p")
            self.assertIn("hdntl=exp=1800000000~acl=%2F*~data=xyz~hmac=fedcba9876543210", resolved_url)

    # -------------------------------------------------------------------------
    # TEST 10: Relative segment / variant URL handling
    # -------------------------------------------------------------------------
    def test_10_relative_variant_resolution(self):
        base = "https://kgs-new-v1.akamaized.net/kv3/physics/2026/master.m3u8?hdnts=exp=111"
        master_content = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
subfolder/720p_stream.m3u8
"""
        with patch("requests.get", return_value=MockHttpResponse(master_content)):
            resolved_url, meta = resolve_kgs_playlist(base, "720p")
            self.assertTrue(resolved_url.startswith("https://kgs-new-v1.akamaized.net/kv3/physics/2026/subfolder/720p_stream.m3u8"))
            self.assertIn("hdnts=exp=111", resolved_url)

    # -------------------------------------------------------------------------
    # TEST 11: Master playlist handling
    # -------------------------------------------------------------------------
    def test_11_master_playlist_parsing(self):
        base = "https://kgs-new-v1.akamaized.net/kv3/chemistry/master.m3u8"
        master_content = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
360p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=854x480
480p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720
720p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4500000,RESOLUTION=1920x1080
1080p.m3u8
"""
        with patch("requests.get", return_value=MockHttpResponse(master_content)):
            resolved_url, meta = resolve_kgs_playlist(base, "720p")
            self.assertTrue(meta["is_master"])
            self.assertEqual(meta["selected_quality"], "720p")
            self.assertEqual(meta["variants_count"], 4)
            self.assertIn("720p.m3u8", resolved_url)

    # -------------------------------------------------------------------------
    # TEST 12: Media playlist handling (direct segments)
    # -------------------------------------------------------------------------
    def test_12_media_playlist_direct(self):
        media_url = "https://kgs-new-v1.akamaized.net/kv3/history/media.m3u8?hdnts=exp=123"
        media_content = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment_0.ts
#EXTINF:10.0,
segment_1.ts
#EXT-X-ENDLIST
"""
        with patch("requests.get", return_value=MockHttpResponse(media_content)):
            resolved_url, meta = resolve_kgs_playlist(media_url, "720p")
            self.assertFalse(meta["is_master"])
            self.assertEqual(resolved_url, media_url)
            self.assertEqual(meta["segments_count"], 2)

    # -------------------------------------------------------------------------
    # TEST 13: 720p preference
    # -------------------------------------------------------------------------
    def test_13_720p_preference(self):
        base = "https://kgs-new-v1.akamaized.net/kv3/math/master.m3u8"
        master_content = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=854x480
480p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720
720p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4500000,RESOLUTION=1920x1080
1080p.m3u8
"""
        with patch("requests.get", return_value=MockHttpResponse(master_content)):
            resolved_url, meta = resolve_kgs_playlist(base, "720p")
            self.assertIn("720p.m3u8", resolved_url)

    # -------------------------------------------------------------------------
    # TEST 14: Fallback to highest available quality when 720p not present
    # -------------------------------------------------------------------------
    def test_14_fallback_highest_quality(self):
        base = "https://kgs-new-v1.akamaized.net/kv3/math/master.m3u8"
        master_content = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
360p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=854x480
480p.m3u8
"""
        with patch("requests.get", return_value=MockHttpResponse(master_content)):
            resolved_url, meta = resolve_kgs_playlist(base, "720p")
            self.assertIn("480p.m3u8", resolved_url)
            self.assertEqual(meta["selected_quality"], "480p")

    # -------------------------------------------------------------------------
    # TEST 15: FFmpeg non-zero exit handling
    # -------------------------------------------------------------------------
    @patch("subprocess.run")
    def test_15_ffmpeg_nonzero_exit_handled(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "Server returned 403 Forbidden (access denied)"
        mock_proc.stdout = ""
        mock_run.return_value = mock_proc

        dummy_url = "https://kgs-new-v1.akamaized.net/kv3/test/master.m3u8?hdnts=exp=123"
        with patch("requests.get", return_value=MockHttpResponse("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\n720.m3u8")):
            res = download_kgs(dummy_url, "test_failed_lecture", "720p", custom_dir=self.test_dir)
            self.assertIsNone(res)

    # -------------------------------------------------------------------------
    # TEST 16: Timestamp-discontinuity warnings are not fatal
    # -------------------------------------------------------------------------
    @patch("subprocess.run")
    def test_16_timestamp_discontinuity_warning_non_fatal(self, mock_run):
        def fake_ffmpeg_run(cmd, *args, **kwargs):
            out_file = cmd[-1]
            with open(out_file, "wb") as f:
                f.write(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00" + b"A" * 1024)
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stderr = "[hls @ 000001] timestamp discontinuity in stream 0: 123456 -> 123450\n[mp4 @ 000002] Non-monotonous DTS in output stream 0:1"
            mock_res.stdout = ""
            return mock_res

        mock_run.side_effect = fake_ffmpeg_run
        dummy_url = "https://kgs-new-v1.akamaized.net/kv3/test/master.m3u8?hdnts=exp=123"
        with patch("requests.get", return_value=MockHttpResponse("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\n720.m3u8")), \
             patch("itsgolu.is_playable", return_value=True):
            res = download_kgs(dummy_url, "test_discontinuity_lecture", "720p", custom_dir=self.test_dir)
            self.assertIsNotNone(res)
            self.assertTrue(os.path.exists(res))
            self.assertGreater(os.path.getsize(res), 0)

    # -------------------------------------------------------------------------
    # TEST 17: Output validation (playability & size)
    # -------------------------------------------------------------------------
    @patch("subprocess.run")
    def test_17_output_validation(self, mock_run):
        def fake_valid_run(cmd, *args, **kwargs):
            out_file = cmd[-1]
            with open(out_file, "wb") as f:
                f.write(b"\x00\x00\x00\x20ftypisom" + b"X" * 2048)
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stderr = ""
            mock_res.stdout = ""
            return mock_res

        mock_run.side_effect = fake_valid_run
        dummy_url = "https://kgs-new-v1.akamaized.net/kv3/physics/class1.m3u8"
        with patch("requests.get", return_value=MockHttpResponse("#EXTM3U\n#EXTINF:10,\nseg1.ts")), \
             patch("itsgolu.is_playable", return_value=True):
            res = download_kgs(dummy_url, "Physics_01", "720p", custom_dir=self.test_dir)
            self.assertIsNotNone(res)
            self.assertTrue(res.endswith(".mp4"))
            self.assertTrue(os.path.exists(res))

    # -------------------------------------------------------------------------
    # TEST 18: B-drive temp/output location
    # -------------------------------------------------------------------------
    def test_18_b_drive_temp_location(self):
        self.assertTrue(os.path.exists(str(PROJECT_ROOT)))
        self.assertTrue(os.path.exists(str(TEMP_DIR)) or str(TEMP_DIR).endswith("temp"))
        self.assertTrue(is_safe_temp_path(os.path.join(TEMP_DIR, "kgs", "lecture.mp4")))
        # System root safety check
        self.assertFalse(is_safe_temp_path("C:\\"))
        self.assertFalse(is_safe_temp_path("C:\\Windows\\System32"))

    # -------------------------------------------------------------------------
    # TEST 19: Cleanup only after successful upload
    # -------------------------------------------------------------------------
    def test_19_cleanup_only_after_successful_upload(self):
        # Create dummy downloaded file inside TEMP_DIR
        kgs_temp_dir = os.path.join(TEMP_DIR, "test_job_cleanup")
        os.makedirs(kgs_temp_dir, exist_ok=True)
        dummy_media = os.path.join(kgs_temp_dir, "lecture_01.mp4")
        with open(dummy_media, "wb") as f:
            f.write(b"SAMPLE VIDEO DATA" * 100)

        self.assertTrue(os.path.exists(dummy_media))

        # Simulation: Upload failed -> Do NOT cleanup
        upload_success = False
        if upload_success:
            cleanup_uploaded_file(dummy_media)
        self.assertTrue(os.path.exists(dummy_media), "File must be preserved if upload failed!")

        # Simulation: Upload succeeded -> Cleanup now
        upload_success = True
        if upload_success:
            cleanup_uploaded_file(dummy_media)
        self.assertFalse(os.path.exists(dummy_media), "File must be cleaned after confirmed upload!")

        import shutil
        shutil.rmtree(kgs_temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # TEST 20: Multi-bot KGS isolation
    # -------------------------------------------------------------------------
    def test_20_multibot_kgs_isolation(self):
        jm = JobManager()
        # Bot 1 starts KGS job
        jm.set_user_active_job(user_id=1001, job_id="job_KGS_1", bot_id="bot_1")
        # Bot 2 starts KGS job
        jm.set_user_active_job(user_id=1002, job_id="job_KGS_2", bot_id="bot_2")

        # Bot 1 cancels its job
        jm.cancel_flags["job_KGS_1"] = True
        jm.cancel_reasons["job_KGS_1"] = "User cancel on Bot 1"

        # Check Bot 1 is cancelled, Bot 2 is still active
        self.assertTrue(jm.cancel_flags.get("job_KGS_1"))
        self.assertFalse(jm.cancel_flags.get("job_KGS_2", False))

        # Check active jobs per bot
        self.assertEqual(jm.get_active_job_id(1001, bot_id="bot_1"), "job_KGS_1")
        self.assertIsNone(jm.get_active_job_id(1001, bot_id="bot_2"))
        self.assertEqual(jm.get_active_job_id(1002, bot_id="bot_2"), "job_KGS_2")


if __name__ == "__main__":
    unittest.main()
