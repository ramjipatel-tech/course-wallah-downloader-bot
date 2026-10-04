import os
import shutil
import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from vars import MAX_UPLOAD_SIZE_BYTES
from youtube_fallback import (
    parse_bytes_from_label,
    parse_ytultra_response,
    probe_remote_duration,
    extract_remote_media_segment
)
import itsgolu as helper
from utils import MediaRouter, MediaType


class TestRemotePartDownloader(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.test_dir = os.path.join(os.getcwd(), "test_temp_remote_parts")
        os.makedirs(self.test_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            try:
                shutil.rmtree(self.test_dir, ignore_errors=True)
            except Exception:
                pass

    def test_01_parse_bytes_from_label(self):
        """Test extraction of exact byte sizes from string labels."""
        self.assertEqual(parse_bytes_from_label("76.18 GB"), int(76.18 * 1024 * 1024 * 1024))
        self.assertEqual(parse_bytes_from_label("397.72 MB"), int(397.72 * 1024 * 1024))
        self.assertEqual(parse_bytes_from_label("12.22 GB"), int(12.22 * 1024 * 1024 * 1024))
        self.assertEqual(parse_bytes_from_label("500 KB"), 500 * 1024)
        self.assertEqual(parse_bytes_from_label(""), 0)
        self.assertEqual(parse_bytes_from_label(None), 0)

    def test_02_ytultra_4k_and_audio_parsing(self):
        """Test that YTUltra parser correctly extracts 4K VP9 stream and AAC audio."""
        sample_response = {
            "title": "4K Ultra HD Nature Video",
            "duration": 8154,
            "formats": [
                {
                    "url": "https://rr2---sn-4g5ednss.googlevideo.com/videoplayback?itag=315&mime=video/webm",
                    "format": "4K (2160p) - 76.18 GB - webm",
                    "height": 2160,
                    "has_audio": False
                },
                {
                    "url": "https://rr2---sn-4g5ednss.googlevideo.com/videoplayback?itag=137&mime=video/mp4",
                    "format": "1080p - 12.22 GB - mp4",
                    "height": 1080,
                    "has_audio": False
                },
                {
                    "url": "https://rr2---sn-4g5ednss.googlevideo.com/videoplayback?itag=18&mime=video/mp4",
                    "format": "360p - 1.2 GB - mp4",
                    "height": 360,
                    "has_audio": True
                },
                {
                    "url": "https://rr2---sn-4g5ednss.googlevideo.com/videoplayback?itag=140&mime=audio/mp4",
                    "format": "128kbps - 397.72 MB - m4a",
                    "type": "audio",
                    "has_audio": True
                }
            ]
        }

        # Request 4K / 2160p
        parsed = parse_ytultra_response(sample_response, target_height=2160)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["height"], 2160)
        self.assertFalse(parsed["is_progressive"])
        self.assertIn("itag=315", parsed["video_url"])
        self.assertIn("itag=140", parsed["audio_url"])
        self.assertEqual(parsed["container_ext"], ".mkv")
        self.assertGreater(parsed["video_bytes"], 70 * 1024 * 1024 * 1024)
        self.assertGreater(parsed["audio_bytes"], 300 * 1024 * 1024)
        self.assertEqual(parsed["total_bytes"], parsed["video_bytes"] + parsed["audio_bytes"])

    def test_03_time_based_part_duration_calculation(self):
        """Verify part duration math: target_bytes / combined_bitrate for ~1800MB parts."""
        # 4K 76.18 GB (~81,797,652,152 bytes) + 397.72 MB (~417,039,646 bytes) = 82,214,691,798 bytes
        total_bytes = int(76.18 * 1024 * 1024 * 1024 + 397.72 * 1024 * 1024)
        duration_sec = 8154.0
        target_part_bytes = 1800 * 1024 * 1024

        combined_bitrate = total_bytes / duration_sec
        part_duration = target_part_bytes / combined_bitrate
        import math
        total_parts = math.ceil(total_bytes / target_part_bytes)

        self.assertAlmostEqual(combined_bitrate / (1024 * 1024), 9.615, delta=0.5)
        self.assertAlmostEqual(part_duration, 187.19, delta=5.0)
        self.assertEqual(total_parts, 44)

    @patch("subprocess.run")
    def test_04_extract_remote_media_segment_ffmpeg_args(self, mock_sub):
        """Verify FFmpeg receives exact matching start and duration for video and audio."""
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_sub.return_value = mock_res

        v_url = "https://example.com/video.webm"
        a_url = "https://example.com/audio.m4a"
        out_file = os.path.join(self.test_dir, "test_part_1.mkv")

        with patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=1800 * 1024 * 1024), \
             patch("os.replace"):
            ok = extract_remote_media_segment(v_url, a_url, start_sec=187.5, dur_sec=187.5, output_path=out_file)

        self.assertTrue(ok)
        self.assertTrue(mock_sub.called)
        cmd = mock_sub.call_args[0][0]
        # Check command has matching timestamps
        self.assertIn("-ss", cmd)
        self.assertIn("187.500", cmd)
        self.assertIn("-t", cmd)
        self.assertIn("-c:v", cmd)
        self.assertIn("copy", cmd)
        self.assertIn("-c:a", cmd)

    async def test_05_insufficient_storage_protection(self):
        """Verify that process_and_upload_remote_youtube_parts aborts cleanly if disk is low."""
        mock_bot = MagicMock()
        mock_prog = AsyncMock()

        yt_info = {
            "url": "https://example.com/video.webm",
            "audio_url": "https://example.com/audio.m4a",
            "height": 2160,
            "duration": 8154,
            "video_bytes": int(76.18 * 1024 * 1024 * 1024),
            "audio_bytes": int(397.72 * 1024 * 1024),
            "total_bytes": int(76.18 * 1024 * 1024 * 1024 + 397.72 * 1024 * 1024),
            "container_ext": ".mkv"
        }

        # Mock shutil.disk_usage to return only 200 MB free
        mock_usage = MagicMock()
        mock_usage.free = 200 * 1024 * 1024

        with patch("shutil.disk_usage", return_value=mock_usage):
            with self.assertRaises(RuntimeError) as ctx:
                await helper.process_and_upload_remote_youtube_parts(
                    bot=mock_bot,
                    prog=mock_prog,
                    caption="Test Caption",
                    raw_title="4K Nature Video",
                    url="https://youtu.be/MdG0Vw9f1A4",
                    yt_info=yt_info,
                    channel_id=123456,
                    custom_dir=self.test_dir,
                    target_part_bytes=1800 * 1024 * 1024
                )

            self.assertIn("Current Railway storage is insufficient for the requested ~1800 MB part architecture.", str(ctx.exception))

    async def test_06_one_part_at_a_time_lifecycle_and_checkpoint(self):
        """Verify that parts are generated, uploaded, checkpointed, and deleted one by one."""
        mock_bot = MagicMock()
        mock_prog = AsyncMock()

        # Setup 2-part video metadata
        yt_info = {
            "url": "https://example.com/video.webm",
            "audio_url": "https://example.com/audio.m4a",
            "height": 2160,
            "duration": 200,
            "video_bytes": 3000 * 1024 * 1024,
            "audio_bytes": 100 * 1024 * 1024,
            "total_bytes": 3100 * 1024 * 1024,
            "container_ext": ".mkv"
        }

        mock_usage = MagicMock()
        mock_usage.free = 10 * 1024 * 1024 * 1024  # 10 GB free

        checkpointed_parts = []
        def on_part_done(idx):
            checkpointed_parts.append(idx)

        deleted_files = []
        def fake_cleanup(p):
            deleted_files.append(p)

        with patch("shutil.disk_usage", return_value=mock_usage), \
             patch("itsgolu.extract_remote_media_segment", return_value=True), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=1500 * 1024 * 1024), \
             patch("itsgolu.probe_media_properties", return_value={"valid": True, "video_codec": "vp9", "duration": 100}), \
             patch("itsgolu.safe_video_send", AsyncMock(return_value=MagicMock(message_id=999))), \
             patch("itsgolu.cleanup_uploaded_file", side_effect=fake_cleanup):

            res = await helper.process_and_upload_remote_youtube_parts(
                bot=mock_bot,
                prog=mock_prog,
                caption="Test 4K Video",
                raw_title="Lecture_4K",
                url="https://youtu.be/MdG0Vw9f1A4",
                yt_info=yt_info,
                channel_id=123456,
                custom_dir=self.test_dir,
                on_part_uploaded=on_part_done,
                target_part_bytes=1800 * 1024 * 1024
            )

        self.assertIsNotNone(res)
        self.assertEqual(checkpointed_parts, [1, 2])
        self.assertTrue(any("Part_1" in f for f in deleted_files))
        self.assertTrue(any("Part_2" in f for f in deleted_files))

    async def test_07_checkpoint_resume_skips_uploaded_parts(self):
        """Verify that when uploaded_parts=[1], Part 1 is skipped and only Part 2 is processed."""
        mock_bot = MagicMock()
        mock_prog = AsyncMock()

        yt_info = {
            "url": "https://example.com/video.webm",
            "audio_url": "https://example.com/audio.m4a",
            "height": 2160,
            "duration": 200,
            "video_bytes": 3000 * 1024 * 1024,
            "audio_bytes": 100 * 1024 * 1024,
            "total_bytes": 3100 * 1024 * 1024,
            "container_ext": ".mkv"
        }

        mock_usage = MagicMock()
        mock_usage.free = 10 * 1024 * 1024 * 1024

        checkpointed_parts = []
        def on_part_done(idx):
            checkpointed_parts.append(idx)

        extracted_parts = []
        def fake_extract(v, a, s, d, out):
            extracted_parts.append(out)
            return True

        with patch("shutil.disk_usage", return_value=mock_usage), \
             patch("itsgolu.extract_remote_media_segment", side_effect=fake_extract), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=1500 * 1024 * 1024), \
             patch("itsgolu.probe_media_properties", return_value={"valid": True, "video_codec": "vp9", "duration": 100}), \
             patch("itsgolu.safe_video_send", AsyncMock(return_value=MagicMock(message_id=1001))), \
             patch("itsgolu.cleanup_uploaded_file"):

            res = await helper.process_and_upload_remote_youtube_parts(
                bot=mock_bot,
                prog=mock_prog,
                caption="Test 4K Video",
                raw_title="Lecture_4K",
                url="https://youtu.be/MdG0Vw9f1A4",
                yt_info=yt_info,
                channel_id=123456,
                custom_dir=self.test_dir,
                uploaded_parts=[1],  # Part 1 already uploaded!
                on_part_uploaded=on_part_done,
                target_part_bytes=1800 * 1024 * 1024
            )

        self.assertIsNotNone(res)
        self.assertEqual(checkpointed_parts, [2])
        self.assertEqual(len(extracted_parts), 1)
        self.assertIn("Part_2", extracted_parts[0])

    async def test_08_url_token_refresh_on_403(self):
        """Verify that when extraction fails, fresh URLs are queried from YTUltra and extraction is retried."""
        mock_bot = MagicMock()
        mock_prog = AsyncMock()

        yt_info = {
            "url": "https://expired.example.com/video.webm",
            "audio_url": "https://expired.example.com/audio.m4a",
            "height": 2160,
            "duration": 100,
            "video_bytes": 1000 * 1024 * 1024,
            "audio_bytes": 50 * 1024 * 1024,
            "total_bytes": 1050 * 1024 * 1024,
            "container_ext": ".mkv"
        }

        fresh_info = {
            "url": "https://fresh.example.com/video.webm",
            "audio_url": "https://fresh.example.com/audio.m4a",
            "height": 2160,
            "duration": 100,
            "video_bytes": 1000 * 1024 * 1024,
            "audio_bytes": 50 * 1024 * 1024,
            "total_bytes": 1050 * 1024 * 1024,
            "container_ext": ".mkv"
        }

        mock_usage = MagicMock()
        mock_usage.free = 10 * 1024 * 1024 * 1024

        call_count = 0
        used_urls = []
        def fake_extract(v, a, s, d, out):
            nonlocal call_count
            call_count += 1
            used_urls.append(v)
            if call_count == 1:
                return False  # first attempt fails with expired token
            return True       # second attempt with refreshed token succeeds

        with patch("shutil.disk_usage", return_value=mock_usage), \
             patch("itsgolu.extract_remote_media_segment", side_effect=fake_extract), \
             patch("itsgolu.resolve_youtube_ytultra_info", return_value=fresh_info), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=1000 * 1024 * 1024), \
             patch("itsgolu.probe_media_properties", return_value={"valid": True, "video_codec": "vp9", "duration": 100}), \
             patch("itsgolu.safe_video_send", AsyncMock(return_value=MagicMock(message_id=1002))), \
             patch("itsgolu.cleanup_uploaded_file"):

            res = await helper.process_and_upload_remote_youtube_parts(
                bot=mock_bot,
                prog=mock_prog,
                caption="Test Refresh",
                raw_title="Lecture_Refresh",
                url="https://youtu.be/MdG0Vw9f1A4",
                yt_info=yt_info,
                channel_id=123456,
                custom_dir=self.test_dir
            )

        self.assertIsNotNone(res)
        self.assertEqual(call_count, 2)
        self.assertIn("expired.example.com", used_urls[0])
        self.assertIn("fresh.example.com", used_urls[1])

    def test_09_provider_isolation_regression(self):
        """Verify non-YouTube providers remain completely isolated and functional."""
        self.assertEqual(MediaRouter.classify_url("https://onlinex.appx.co.in/stream/123"), MediaType.APPX_LECTURE)
        self.assertEqual(MediaRouter.classify_url("https://spayee.com/courses/video.m3u8"), MediaType.SPAYEE_HLS)
        self.assertEqual(MediaRouter.classify_url("https://khanglobalstudies.com/video/master.m3u8"), MediaType.KGS_HLS)
        self.assertEqual(MediaRouter.classify_url("https://example.com/lecture.pdf"), MediaType.DIRECT_PDF)
        self.assertEqual(MediaRouter.classify_url("https://example.com/photo.jpg"), MediaType.DIRECT_IMAGE)
        self.assertEqual(MediaRouter.classify_url("https://youtu.be/MdG0Vw9f1A4"), MediaType.YOUTUBE)


if __name__ == "__main__":
    unittest.main()
