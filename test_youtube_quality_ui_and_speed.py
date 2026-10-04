import os
import shutil
import unittest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from youtube_fallback import (
    parse_all_ytultra_qualities,
    fetch_ytultra_media_data,
    resolve_youtube_ytultra_info
)
from utils import (
    format_youtube_quality_menu,
    format_youtube_fallback_card,
    MediaRouter,
    MediaType
)
import main
import itsgolu as helper


class TestYouTubeQualityUIAndSpeed(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.test_dir = os.path.join(os.getcwd(), "test_temp_ui")
        os.makedirs(self.test_dir, exist_ok=True)
        main._PENDING_YT_JOBS.clear()

    def tearDown(self):
        if os.path.exists(self.test_dir):
            try:
                shutil.rmtree(self.test_dir, ignore_errors=True)
            except Exception:
                pass
        main._PENDING_YT_JOBS.clear()

    def test_01_4k_available_menu(self):
        """Test that when 4K is available, 4K button is prominently displayed."""
        raw_response = {
            "title": "4K Ultra HD Nature Video",
            "duration": 8154,
            "formats": [
                {
                    "url": "https://googlevideo.com/videoplayback?itag=315",
                    "format": "4K (2160p) - 76.18 GB - webm",
                    "height": 2160,
                    "has_audio": False
                },
                {
                    "url": "https://googlevideo.com/videoplayback?itag=137",
                    "format": "1080p - 12.22 GB - mp4",
                    "height": 1080,
                    "has_audio": False
                },
                {
                    "url": "https://googlevideo.com/videoplayback?itag=22",
                    "format": "720p - 2.1 GB - mp4",
                    "height": 720,
                    "has_audio": True
                },
                {
                    "url": "https://googlevideo.com/videoplayback?itag=140",
                    "format": "128kbps - 397.72 MB - m4a",
                    "type": "audio",
                    "has_audio": True
                }
            ]
        }

        parsed = parse_all_ytultra_qualities(raw_response)
        qualities = parsed["qualities"]

        # Check heights present
        heights = [q["height"] for q in qualities]
        self.assertIn(2160, heights)
        self.assertIn(1080, heights)
        self.assertIn(720, heights)

        # Build menu
        text, markup = format_youtube_quality_menu(
            title=parsed["title"],
            duration_sec=parsed["duration"],
            qualities=qualities,
            job_id="job_4k_test",
            user_id=12345
        )

        self.assertIn("VIDEO READY", text)
        self.assertIn("4K Ultra HD Nature Video", text)

        # Check buttons
        all_buttons = [b for row in markup.inline_keyboard for b in row]
        button_texts = [b.text for b in all_buttons]
        callback_datas = [b.callback_data for b in all_buttons]

        self.assertTrue(any("4K UHD" in t for t in button_texts))
        self.assertTrue(any("ytq:job_4k_test:2160:12345" in c for c in callback_datas))

    def test_02_4k_unavailable_fallback_card(self):
        """Test that when 4K is unavailable and requested, highest real quality (1080p) is offered."""
        raw_response = {
            "title": "1080p Documentary",
            "duration": 3600,
            "formats": [
                {
                    "url": "https://googlevideo.com/videoplayback?itag=137",
                    "format": "1080p - 2.5 GB - mp4",
                    "height": 1080,
                    "has_audio": False
                },
                {
                    "url": "https://googlevideo.com/videoplayback?itag=22",
                    "format": "720p - 1.1 GB - mp4",
                    "height": 720,
                    "has_audio": True
                },
                {
                    "url": "https://googlevideo.com/videoplayback?itag=140",
                    "format": "128kbps - 50 MB - m4a",
                    "type": "audio",
                    "has_audio": True
                }
            ]
        }

        parsed = parse_all_ytultra_qualities(raw_response)
        qualities = parsed["qualities"]
        heights = [q["height"] for q in qualities]
        self.assertNotIn(2160, heights)
        self.assertEqual(qualities[0]["height"], 1080)

        # Build fallback card
        text, markup = format_youtube_fallback_card(
            title=parsed["title"],
            duration_sec=parsed["duration"],
            requested_height=2160,
            highest_quality=qualities[0],
            job_id="job_fallback_test",
            user_id=12345
        )

        self.assertIn("QUALITY NOTICE", text)
        self.assertIn("4K UHD (2160p) is not available", text)
        self.assertIn("1080p", text)

        all_buttons = [b for row in markup.inline_keyboard for b in row]
        button_texts = [b.text for b in all_buttons]
        callback_datas = [b.callback_data for b in all_buttons]

        self.assertTrue(any("Download 1080p" in t for t in button_texts))
        self.assertTrue(any("Choose Quality" in t for t in button_texts))
        self.assertTrue(any("ytq:job_fallback_test:1080:12345" in c for c in callback_datas))

    def test_03_video_only_without_audio_rejected(self):
        """Test that video-only formats are rejected when no audio stream exists."""
        raw_response = {
            "title": "Silent Video",
            "duration": 60,
            "formats": [
                {
                    "url": "https://googlevideo.com/videoplayback?itag=315",
                    "format": "4K - 500 MB - webm",
                    "height": 2160,
                    "has_audio": False
                }
            ]
        }

        parsed = parse_all_ytultra_qualities(raw_response)
        self.assertEqual(len(parsed["qualities"]), 0)

    async def test_04_callback_user_isolation(self):
        """Test that user B clicking user A's callback is rejected."""
        mock_client = MagicMock()
        mock_query = AsyncMock()
        mock_query.data = "ytq:job_iso_1:1080:12345"
        mock_query.from_user.id = 99999  # Attacker / Different user

        await main.youtube_quality_callback(mock_client, mock_query)

        mock_query.answer.assert_called_with("⚠️ This download menu belongs to another user.", show_alert=True)

    async def test_05_callback_successful_trigger(self):
        """Test that legitimate user tapping quality triggers download."""
        mock_client = MagicMock()
        mock_query = AsyncMock()
        mock_query.data = "ytq:job_legit_1:1080:12345"
        mock_query.from_user.id = 12345
        mock_query.message = AsyncMock()

        main._PENDING_YT_JOBS["job_legit_1"] = {
            "user_id": 12345,
            "chat_id": 55555,
            "url": "https://youtu.be/MdG0Vw9f1A4",
            "title": "Test Lecture",
            "media_data": {
                "title": "Test Lecture",
                "duration": 100,
                "raw_data": {
                    "formats": [
                        {"url": "https://googlevideo.com/v1", "height": 1080, "has_audio": True}
                    ]
                }
            }
        }

        with patch("main._execute_youtube_download", new_callable=AsyncMock) as mock_exec:
            await main.youtube_quality_callback(mock_client, mock_query)
            await asyncio.sleep(0.05)  # Allow create_task to execute
            mock_exec.assert_called_once()
            self.assertEqual(mock_exec.call_args[1]["quality"], "1080p")
            self.assertEqual(mock_exec.call_args[1]["user_id"], 12345)

    async def test_06_cancel_callback_cleanup(self):
        """Test that cancelling removes the pending job entry."""
        mock_client = MagicMock()
        mock_query = AsyncMock()
        mock_query.data = "ytcancel:job_cancel_1:12345"
        mock_query.from_user.id = 12345
        mock_query.message = AsyncMock()

        main._PENDING_YT_JOBS["job_cancel_1"] = {
            "user_id": 12345,
            "url": "https://youtu.be/test"
        }

        await main.youtube_cancel_callback(mock_client, mock_query)

        self.assertNotIn("job_cancel_1", main._PENDING_YT_JOBS)
        mock_query.message.edit_text.assert_called_once()
        self.assertIn("Download cancelled", mock_query.message.edit_text.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
