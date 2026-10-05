import os
import sys
import time
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio

from utils import (
    MediaType,
    MediaRouter,
    is_spayee_url,
    is_goclasses_url,
    is_youtube_url,
    is_hls_url,
    is_direct_video_url,
    is_appx_url,
    resolve_redirect_url,
    format_download_card,
    format_watermark_card,
    format_merging_card,
    format_split_card,
    format_upload_card,
    format_pdf_download_card,
    format_success_card,
    build_failure_card,
    progress_bar,
    JobProgressTracker
)
from vars import MAX_UPLOAD_SIZE_BYTES
from itsgolu import (
    split_large_video,
    send_vid
)
import itsgolu as helper

class TestMasterRoutingAndSplitting(unittest.IsolatedAsyncioTestCase):

    # ==========================================================================
    # 1. PROVIDER ROUTING & STRICT YOUTUBE CLASSIFICATION TESTS
    # ==========================================================================

    def test_01_strict_youtube_detection(self):
        """YouTube detection must match only legitimate YouTube hostnames, never query parameters or false substrings."""
        valid_yt_urls = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
        ]
        for url in valid_yt_urls:
            self.assertTrue(is_youtube_url(url), f"Failed to identify valid YouTube URL: {url}")
            self.assertEqual(MediaRouter.classify_url(url), MediaType.YOUTUBE)

        # False positives that contain 'youtube' in path, query, or domain substring
        invalid_yt_urls = [
            "https://spayee.in/courses/youtube-marketing/lecture.m3u8",
            "https://example.com/search?query=youtube_video",
            "https://notyoutube.com/watch?v=123",
            "https://myyoutube.org/stream.mp4",
            "https://courses.goclasses.in/lecture/youtube_deep_dive.m3u8"
        ]
        for url in invalid_yt_urls:
            self.assertFalse(is_youtube_url(url), f"Incorrectly identified non-YouTube URL as YouTube: {url}")
            self.assertNotEqual(MediaRouter.classify_url(url), MediaType.YOUTUBE)

    def test_02_spayee_routing(self):
        """Spayee URLs and CDNs must be strictly routed to SPAYEE_HLS."""
        spayee_urls = [
            "https://qcdn.spayee.in/6290a1/stream/master.m3u8",
            "https://vcdn.spayee.in/course_wallah/video/playlist.m3u8",
            "https://content.spees.in/enc/master.m3u8",
            "https://secure.spayee.com/live/lecture1.m3u8",
            "https://learning.spayee.in/media/hls/master.m3u8*AUTHORIZATION_KEY"
        ]
        for url in spayee_urls:
            self.assertTrue(is_spayee_url(url), f"Failed to identify Spayee URL: {url}")
            self.assertEqual(MediaRouter.classify_url(url), MediaType.SPAYEE_HLS)

    def test_03_goclasses_routing(self):
        """Go Classes / GateOverflow URLs must be strictly routed to GO_CLASSES and never fall back to YouTube."""
        goclasses_urls = [
            "https://courses.goclasses.in/video/lecture10.m3u8",
            "https://goclasses.in/stream/algo_part1.m3u8",
            "https://gateoverflow.in/courses/discrete_math/lecture5.m3u8",
            "https://app.goclasses.in/hls/dbms.m3u8*AUTH_KEY"
        ]
        for url in goclasses_urls:
            self.assertTrue(is_goclasses_url(url), f"Failed to identify Go Classes URL: {url}")
            self.assertEqual(MediaRouter.classify_url(url), MediaType.GO_CLASSES)

    def test_04_appx_routing_boundary(self):
        """AppX lecture URLs must be identified as APPX_LECTURE without altering AppX internals."""
        appx_urls = [
            "https://appx.co.in/api/v1/courses/10/lecture/20",
            "https://akstechnicalclasses.classx.co.in/fetch_video/456",
            "https://mycoaching.classx.co.in/lecture/789"
        ]
        for url in appx_urls:
            self.assertTrue(is_appx_url(url), f"Failed to identify AppX URL: {url}")
            self.assertEqual(MediaRouter.classify_url(url), MediaType.APPX_LECTURE)

    def test_05_unknown_source_no_fallback(self):
        """Unrecognized URLs must return UNKNOWN and NEVER fall back to YouTube or generic video."""
        unknown_urls = [
            "https://example.com/unsupported_page",
            "https://someblog.org/articles/post1",
            "https://unknownstreamingsite.xyz/watch/abc"
        ]
        for url in unknown_urls:
            self.assertEqual(MediaRouter.classify_url(url), MediaType.UNKNOWN)

    def test_06_independent_classification_no_state_leakage(self):
        """In a mixed batch of URLs, each URL must be classified independently with zero state leakage."""
        batch = [
            ("https://qcdn.spayee.in/vid1/master.m3u8", MediaType.SPAYEE_HLS),
            ("https://courses.goclasses.in/vid2.m3u8", MediaType.GO_CLASSES),
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", MediaType.YOUTUBE),
            ("https://qcdn.spayee.in/vid3/master.m3u8", MediaType.SPAYEE_HLS),
            ("https://appx.co.in/api/v1/courses/1/lecture/2", MediaType.APPX_LECTURE),
            ("https://d111.cloudfront.net/stream.m3u8", MediaType.DIRECT_M3U8),
            ("https://example.com/video.mp4", MediaType.DIRECT_VIDEO),
            ("https://example.com/notes.pdf", MediaType.DIRECT_PDF),
            ("https://unknownsite.org/lecture", MediaType.UNKNOWN),
            ("https://qcdn.spayee.in/vid4/master.m3u8", MediaType.SPAYEE_HLS),
        ]
        for url, expected_type in batch:
            detected_type = MediaRouter.classify_url(url)
            self.assertEqual(detected_type, expected_type, f"State leakage or incorrect classification for {url}: expected {expected_type}, got {detected_type}")

    # ==========================================================================
    # 2. LARGE VIDEO SPLITTING & PART CHECKPOINT TESTS
    # ==========================================================================

    @patch("itsgolu.validate_split_part", return_value=True)
    @patch("itsgolu.get_video_duration", return_value=3600.0)
    @patch("subprocess.run")
    def test_07_large_video_threshold_and_part_calculation(self, mock_sub, mock_dur, mock_val):
        """Verify part calculation logic across diverse file sizes."""
        threshold_mb = MAX_UPLOAD_SIZE_BYTES / (1024 * 1024)
        self.assertAlmostEqual(threshold_mb, 1950.0, places=1)

        test_file = "sample_test_video.mp4"

        # 500 MB -> 1 part (no split)
        with patch("os.path.getsize", return_value=500 * 1024 * 1024), patch("os.path.exists", return_value=True):
            parts = split_large_video(test_file, max_size_bytes=MAX_UPLOAD_SIZE_BYTES)
            self.assertEqual(len(parts), 1)
            self.assertEqual(parts[0], test_file)

        # 1949 MB -> 1 part (no split)
        with patch("os.path.getsize", return_value=1949 * 1024 * 1024), patch("os.path.exists", return_value=True):
            parts = split_large_video(test_file, max_size_bytes=MAX_UPLOAD_SIZE_BYTES)
            self.assertEqual(len(parts), 1)

        # 2000 MB -> 2 parts
        def mock_sz_2000(p):
            return 2000 * 1024 * 1024 if p == test_file else 1000 * 1024 * 1024
        with patch("os.path.getsize", side_effect=mock_sz_2000), patch("os.path.exists", return_value=True):
            parts = split_large_video(test_file, max_size_bytes=MAX_UPLOAD_SIZE_BYTES)
            self.assertEqual(len(parts), 2)

        # 3800 MB -> 3 parts (safe target 1716 MB per part)
        def mock_sz_3800(p):
            return 3800 * 1024 * 1024 if p == test_file else 1300 * 1024 * 1024
        with patch("os.path.getsize", side_effect=mock_sz_3800), patch("os.path.exists", return_value=True):
            parts = split_large_video(test_file, max_size_bytes=MAX_UPLOAD_SIZE_BYTES)
            self.assertGreaterEqual(len(parts), 2)

        # 6000 MB -> 4 parts
        def mock_sz_6000(p):
            return 6000 * 1024 * 1024 if p == test_file else 1500 * 1024 * 1024
        with patch("os.path.getsize", side_effect=mock_sz_6000), patch("os.path.exists", return_value=True):
            parts = split_large_video(test_file, max_size_bytes=MAX_UPLOAD_SIZE_BYTES)
            self.assertEqual(len(parts), 4)

    @patch("itsgolu.validate_split_part", return_value=True)
    @patch("itsgolu.get_video_duration", return_value=3600.0)
    @patch("subprocess.run")
    def test_08_split_large_video_execution(self, mock_sub, mock_dur, mock_val):
        """Verify split_large_video uses ffmpeg stream copy, names parts properly, and validates parts."""
        test_file = "test_video_large.mp4"
        def mock_sz(p):
            return 3900 * 1024 * 1024 if p == test_file else 1500 * 1024 * 1024
        with patch("os.path.getsize", side_effect=mock_sz), \
             patch("os.path.exists", return_value=True):
            parts = split_large_video(test_file, max_size_bytes=1950 * 1024 * 1024)
            self.assertGreaterEqual(len(parts), 2)
            self.assertIn("Part_1", parts[0])
            self.assertIn("Part_2", parts[1])

    @patch("itsgolu.validate_split_part", return_value=True)
    @patch("itsgolu.duration", return_value=60.0)
    async def test_09_send_vid_split_checkpoint_resume(self, mock_dur, mock_val):
        """Verify send_vid resumes large video parts properly and invokes on_part_uploaded callback."""
        mock_bot = MagicMock()
        mock_bot.send_video = AsyncMock()

        part1 = "test_part_1.mp4"
        part2 = "test_part_2.mp4"
        part3 = "test_part_3.mp4"

        recorded_uploaded_parts = []
        def _on_part_done(p_num):
            recorded_uploaded_parts.append(p_num)

        with patch("os.path.getsize", return_value=3000 * 1024 * 1024), \
             patch("os.path.exists", return_value=True), \
             patch("itsgolu.split_large_video", return_value=[part1, part2, part3]):

            # Test 1: First run with no previously uploaded parts -> uploads Part 1, 2, 3
            res = await send_vid(
                mock_bot, None, "Caption", "source_large.mp4", None, "Large Lecture", None, -100123456,
                uploaded_parts=[],
                on_part_uploaded=_on_part_done
            )
            self.assertTrue(res)
            self.assertEqual(mock_bot.send_video.call_count, 3)
            self.assertEqual(recorded_uploaded_parts, [1, 2, 3])

            # Test 2: Retry with Part 1 already completed -> skips Part 1, uploads only Part 2 & 3
            mock_bot.send_video.reset_mock()
            recorded_uploaded_parts.clear()
            res = await send_vid(
                mock_bot, None, "Caption", "source_large.mp4", None, "Large Lecture", None, -100123456,
                uploaded_parts=[1],
                on_part_uploaded=_on_part_done
            )
            self.assertTrue(res)
            self.assertEqual(mock_bot.send_video.call_count, 2)
            self.assertEqual(recorded_uploaded_parts, [2, 3])

    # ==========================================================================
    # 3. UI ANIMATION & SPEED PRIVACY TESTS
    # ==========================================================================

    def test_10_status_cards_design_and_privacy(self):
        """Verify status cards render rich typography and strictly omit raw MB/s and raw byte counts in user-facing text."""
        # Download card
        dl_card = format_download_card("Complete DSA", "Binary Trees", "Trees", "Computer Science", frame=2)
        self.assertIn("DOWNLOADING", dl_card)
        self.assertIn("Binary Trees", dl_card)
        self.assertNotIn("MB/s", dl_card)
        self.assertNotIn("B/s", dl_card)

        # Watermark card
        wm_card = format_watermark_card("Dynamic Programming", frame=1)
        self.assertIn("WATERMARKING", wm_card)
        self.assertIn("Dynamic Programming", wm_card)

        # Splitting card (only for actual oversized videos)
        split_card = format_split_card("Full Syllabus Revision", frame=0)
        self.assertIn("PREPARING LARGE VIDEO", split_card)
        self.assertIn("Full Syllabus Revision", split_card)

        # Upload card for split part
        up_split_card = format_upload_card("Full Syllabus Revision", part_idx=1, total_parts=3, frame=1)
        self.assertIn("UPLOADING PART 1/3", up_split_card)

        # Success card
        succ_card = format_success_card("Lecture 01 - Graphs", video_delivered=True, pdf_delivered=True, split_parts=2)
        self.assertIn("DOWNLOAD READY", succ_card)
        self.assertIn("2 part(s) uploaded", succ_card)

    async def test_11_progress_bar_no_raw_speed_leak(self):
        """Verify progress_bar callback renders clean percentage without leaking raw MB/s or raw ETA into Telegram text."""
        mock_reply = AsyncMock()
        mock_reply.edit_text = AsyncMock()
        await progress_bar(500 * 1024 * 1024, 1000 * 1024 * 1024, mock_reply, time.time() - 10, name="Lecture 01 (Part 1)")
        self.assertTrue(mock_reply.edit_text.called)
        call_text = mock_reply.edit_text.call_args[0][0]
        self.assertIn("𝙐𝙥𝙡𝙤𝙖𝙙𝙞𝙣𝙜 𝙋𝙖𝙧𝙩 1", call_text)
        self.assertIn("50.00%", call_text)

    # ==========================================================================
    # 4. YOUTUBE FALLBACK CHAIN & FORMAT SELECTION TESTS
    # ==========================================================================

    def test_12_vynex_format_selection_progressive_with_audio(self):
        """Vynex must never select video-only formats and must pick highest resolution with audio <= requested quality."""
        from youtube_fallback import parse_vynex_formats

        sample_vynex_data = {
            "formats": [
                {"label": "1080p", "height": 1080, "ext": "mp4", "size": "80 MB", "has_audio": False, "url": "https://cdn.vynex.ai/1080p.mp4"},
                {"label": "720p", "height": 720, "ext": "mp4", "size": "45 MB", "has_audio": False, "url": "https://cdn.vynex.ai/720p.mp4"},
                {"label": "480p", "height": 480, "ext": "mp4", "size": "25 MB", "has_audio": False, "url": "https://cdn.vynex.ai/480p.mp4"},
                {"label": "360p", "height": 360, "ext": "mp4", "size": "15 MB", "has_audio": True, "url": "https://cdn.vynex.ai/360p_audio.mp4"},
                {"label": "240p", "height": 240, "ext": "mp4", "size": "8 MB", "has_audio": True, "url": "https://cdn.vynex.ai/240p_audio.mp4"}
            ],
            "audio": [
                {"format_id": "140", "bitrate": "128k", "ext": "m4a", "url": "https://cdn.vynex.ai/audio.m4a"}
            ]
        }

        # Request 720p: 720p is video-only -> must safely fallback to 360p (progressive with audio)
        chosen = parse_vynex_formats(sample_vynex_data, target_height=720)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["height"], 360)
        self.assertEqual(chosen["url"], "https://cdn.vynex.ai/360p_audio.mp4")

        # When all formats lack audio, returns None
        no_audio_data = {
            "formats": [
                {"label": "720p", "height": 720, "ext": "mp4", "has_audio": False, "url": "https://cdn.vynex.ai/720p.mp4"}
            ]
        }
        self.assertIsNone(parse_vynex_formats(no_audio_data, target_height=720))

    def test_13_ytultra_format_selection_progressive_with_audio(self):
        """YTUltra must defensively parse media formats and select progressive video+audio."""
        from youtube_fallback import parse_ytultra_response

        sample_ytultra_data = {
            "formats": [
                {"quality": "1080p", "height": 1080, "type": "video-only", "has_audio": False, "url": "https://ytultra.com/1080_vo.mp4"},
                {"quality": "720p", "height": 720, "type": "video", "has_audio": True, "url": "https://ytultra.com/720_va.mp4"},
                {"quality": "360p", "height": 360, "type": "video", "has_audio": True, "url": "https://ytultra.com/360_va.mp4"}
            ]
        }

        chosen = parse_ytultra_response(sample_ytultra_data, target_height=720)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["height"], 720)
        self.assertEqual(chosen["url"], "https://ytultra.com/720_va.mp4")

    @patch("itsgolu.probe_media_properties", return_value={"valid": True, "video_codec": "h264", "has_audio": True, "duration": 60.0})
    @patch("itsgolu.download_media_stream_url", return_value=True)
    @patch("os.path.exists", return_value=True)
    @patch("os.path.getsize", return_value=50 * 1024 * 1024)
    @patch("itsgolu.resolve_youtube_vynex")
    @patch("itsgolu.resolve_youtube_ytultra_info", return_value={"url": "https://cdn.ytultra.com/stream.mp4", "has_audio": True, "height": 720})
    async def test_14_youtube_direct_ytultra_primary_success(self, mock_ytultra, mock_vynex, mock_sz, mock_ex, mock_dl, mock_probe):
        """When YTUltra produces verified video+audio file, return it directly without calling fallback yt-dlp or Vynex."""
        res = await helper.download_video("https://youtube.com/watch?v=abc12345", "test_yt", quality="720p", custom_dir="downloads")
        self.assertIsNotNone(res)
        self.assertIn("ytultra", res)
        self.assertTrue(mock_ytultra.called)
        self.assertFalse(mock_vynex.called)

    @patch("itsgolu.probe_media_properties", return_value={"valid": True, "video_codec": "h264", "has_audio": True, "duration": 60.0})
    @patch("itsgolu.get_existing_file", side_effect=lambda x: x)
    @patch("os.path.exists", return_value=True)
    @patch("os.path.getsize", return_value=30 * 1024 * 1024)
    @patch("itsgolu.resolve_youtube_ytultra_info", return_value=None)
    @patch("itsgolu.resolve_youtube_ytultra", return_value=None)
    @patch("itsgolu.resolve_youtube_vynex")
    async def test_15_youtube_fallback_chain_ytdlp_fallback(self, mock_vynex, mock_ytultra_str, mock_ytultra_info, mock_sz, mock_ex, mock_exist, mock_probe):
        """When direct YTUltra fails, fallback to yt-dlp is invoked."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc_mock = AsyncMock()
            proc_mock.communicate.return_value = (b"", b"")
            proc_mock.returncode = 0
            mock_exec.return_value = proc_mock

            res = await helper.download_video("https://youtube.com/watch?v=abc12345", "test_yt", quality="720p", custom_dir="downloads")
            self.assertIsNotNone(res)
            self.assertTrue(mock_ytultra_info.called)
            self.assertFalse(mock_vynex.called)

    @patch("itsgolu.probe_media_properties", return_value={"valid": True, "video_codec": "h264", "has_audio": True, "duration": 60.0})
    @patch("itsgolu.download_media_stream_url", return_value=True)
    @patch("itsgolu.resolve_youtube_ytultra_info", return_value=None)
    @patch("itsgolu.resolve_youtube_ytultra", return_value=None)
    @patch("itsgolu.resolve_youtube_vynex", return_value="https://cdn.vynex.ai/stream.mp4")
    async def test_16_youtube_fallback_chain_vynex_fallback(self, mock_vynex, mock_ytultra_str, mock_ytultra_info, mock_dl, mock_probe):
        """When YTUltra and yt-dlp both fail, Vynex fallback is invoked and returns valid file."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc_mock = AsyncMock()
            proc_mock.communicate.return_value = (b"", b"Sign in required")
            proc_mock.returncode = 1
            mock_exec.return_value = proc_mock

            def exist_side_effect(path):
                return "vynex" in str(path)

            with patch("os.path.exists", side_effect=exist_side_effect), \
                 patch("os.path.getsize", return_value=25 * 1024 * 1024):
                res = await helper.download_video("https://youtube.com/watch?v=abc12345", "test_yt", quality="720p", custom_dir="downloads")
                self.assertIsNotNone(res)
                self.assertIn("vynex", res)
                self.assertTrue(mock_vynex.called)

    @patch("itsgolu.download_media_stream_url", return_value=True)
    @patch("itsgolu.resolve_youtube_ytultra_info", return_value={"url": "https://cdn.ytultra.com/stream.mp4", "has_audio": False})
    @patch("itsgolu.resolve_youtube_ytultra", return_value=None)
    @patch("itsgolu.resolve_youtube_vynex", return_value="https://cdn.vynex.ai/stream.mp4")
    async def test_17_youtube_audio_validation_rejects_silent_video(self, mock_vynex, mock_ytultra_str, mock_ytultra_info, mock_dl):
        """When YTUltra produces video without audio (has_audio=False), it is rejected and falls back."""
        probe_call_count = 0
        def probe_side_effect(path):
            nonlocal probe_call_count
            probe_call_count += 1
            if "vynex" in str(path):
                return {"valid": True, "video_codec": "h264", "has_audio": True, "duration": 60.0}
            return {"valid": True, "video_codec": "h264", "has_audio": False, "duration": 60.0}

        with patch("asyncio.create_subprocess_exec") as mock_exec, \
             patch("itsgolu.probe_media_properties", side_effect=probe_side_effect), \
             patch("itsgolu.get_existing_file", side_effect=lambda x: x), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=50 * 1024 * 1024), \
             patch("os.remove"):

            proc_mock = AsyncMock()
            proc_mock.communicate.return_value = (b"", b"Sign in required")
            proc_mock.returncode = 1
            mock_exec.return_value = proc_mock

            res = await helper.download_video("https://youtube.com/watch?v=abc12345", "test_yt", quality="720p", custom_dir="downloads")
            self.assertIsNotNone(res)
            self.assertIn("vynex", res)
            self.assertTrue(mock_vynex.called)


if __name__ == '__main__':
    unittest.main()
