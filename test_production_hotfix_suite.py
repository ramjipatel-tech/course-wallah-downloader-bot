import os
import unittest
import asyncio
import tempfile
import shutil
from unittest.mock import patch, MagicMock, AsyncMock

from pyrogram import enums
from pyrogram.types import CallbackQuery, Message, User
from youtube_fallback import extract_remote_media_segment, download_media_stream_url
from utils import (
    format_universal_input_card,
    format_drm_input_card,
    format_drm_result_card,
    format_bot_online_card,
    check_media_drm_status
)
from main import BotContext, bot_health_watchdog, input_cancel_callback


class TestProductionHotfixSuite(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("subprocess.run")
    def test_01_ffmpeg_mkv_segment_output_format_and_extension(self, mock_sub):
        """Verify FFmpeg receives -f matroska and .seg.tmp.mkv for MKV container segments."""
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_sub.return_value = mock_res

        v_url = "https://example.com/video.webm"
        a_url = "https://example.com/audio.m4a"
        out_file = os.path.join(self.test_dir, "Course_Part_1.mkv")

        with patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=1024 * 1024), \
             patch("os.replace"):
            ok = extract_remote_media_segment(v_url, a_url, start_sec=0.0, dur_sec=187.5, output_path=out_file)

        self.assertTrue(ok)
        self.assertTrue(mock_sub.called)
        cmd = mock_sub.call_args[0][0]
        self.assertIn("-f", cmd)
        self.assertIn("matroska", cmd)
        out_arg = cmd[-1]
        self.assertTrue(out_arg.endswith(".seg.tmp.mkv"), f"Expected .seg.tmp.mkv, got: {out_arg}")

    @patch("subprocess.run")
    def test_02_ffmpeg_mp4_segment_output_format_and_faststart(self, mock_sub):
        """Verify FFmpeg receives -f mp4, -movflags +faststart, and .seg.tmp.mp4 for MP4 segments."""
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_sub.return_value = mock_res

        v_url = "https://example.com/video.mp4"
        a_url = "https://example.com/audio.m4a"
        out_file = os.path.join(self.test_dir, "Course_Part_1.mp4")

        with patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=1024 * 1024), \
             patch("os.replace"):
            ok = extract_remote_media_segment(v_url, a_url, start_sec=0.0, dur_sec=187.5, output_path=out_file)

        self.assertTrue(ok)
        self.assertTrue(mock_sub.called)
        cmd = mock_sub.call_args[0][0]
        self.assertIn("-f", cmd)
        self.assertIn("mp4", cmd)
        self.assertIn("-movflags", cmd)
        self.assertIn("+faststart", cmd)
        out_arg = cmd[-1]
        self.assertTrue(out_arg.endswith(".seg.tmp.mp4"), f"Expected .seg.tmp.mp4, got: {out_arg}")

    @patch("subprocess.run")
    def test_03_download_media_stream_explicit_format(self, mock_sub):
        """Verify download_media_stream_url passes explicit container formats to FFmpeg."""
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_sub.return_value = mock_res

        stream_url = "https://example.com/video.webm"
        audio_url = "https://example.com/audio.m4a"
        out_file = os.path.join(self.test_dir, "Lecture.mkv")

        with patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=1024 * 1024), \
             patch("os.replace"):
            ok = download_media_stream_url(stream_url, out_file, audio_url=audio_url)

        self.assertTrue(ok)
        self.assertTrue(mock_sub.called)
        cmd = mock_sub.call_args[0][0]
        self.assertIn("-f", cmd)
        self.assertIn("matroska", cmd)

    def test_04_universal_input_cards_format(self):
        """Verify universal input cards generate clean markup and titles."""
        text, markup = format_universal_input_card(
            action_title="WATERMARK SETTINGS",
            instruction="Send watermark text or /d for default:",
            example="Course Wallah",
            cancel_callback="input_cancel",
            user_id=12345
        )
        self.assertIn("WATERMARK SETTINGS", text)
        self.assertIn("Course Wallah", text)
        self.assertIsNotNone(markup)
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "input_cancel:12345")

    def test_05_drm_input_and_result_cards(self):
        """Verify DRM prompt and result cards."""
        text, markup = format_drm_input_card(user_id=9876)
        self.assertIn("DRM CHECKER & BATCH", text)
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "input_cancel:9876")

        clean_res = format_drm_result_card("Calculus Lecture 1", is_drm=False, media_type="HLS Stream")
        self.assertIn("No DRM Detected", clean_res)
        self.assertIn("Calculus Lecture 1", clean_res)

        drm_res = format_drm_result_card("Physics Lecture", is_drm=True, media_type="MPEG-DASH / MPD")
        self.assertIn("DRM Protected", drm_res)
        self.assertIn("Widevine", drm_res)

    @patch("utils.requests.get")
    def test_06_check_media_drm_status_detection(self, mock_get):
        """Verify check_media_drm_status identifies Widevine MPD and clear streams."""
        # 1. Widevine MPD
        mock_resp_drm = MagicMock()
        mock_resp_drm.text = '<MPD><ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/></MPD>'
        mock_get.return_value = mock_resp_drm

        res_drm = check_media_drm_status("https://example.com/stream.mpd")
        self.assertTrue(res_drm["is_drm"])
        self.assertIn("Widevine", res_drm["details"])

        # 2. Clear HLS
        mock_resp_clear = MagicMock()
        mock_resp_clear.text = '#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:10.0,\nseg1.ts'
        mock_get.return_value = mock_resp_clear

        res_clear = check_media_drm_status("https://example.com/stream.m3u8")
        self.assertFalse(res_clear["is_drm"])
        self.assertIn("Clear HLS", res_clear["details"])

    def test_07_bot_online_card(self):
        """Verify startup announcement card."""
        text, markup = format_bot_online_card(
            bot_name="Course Wallah Bot",
            bot_username="cw_bot",
            active_bots=3,
            total_bots=3,
            recovered_jobs=1
        )
        self.assertIn("Bot is now ONLINE & LIVE", text)
        self.assertIn("Course Wallah Bot", text)
        self.assertIn("3/3 active", text)
        self.assertIn("1 job(s) restored", text)
        self.assertIsNotNone(markup)

    async def test_08_input_cancel_callback_isolation(self):
        """Verify input_cancel_callback rejects cross-user cancellation."""
        mock_client = MagicMock()
        mock_query = AsyncMock()
        mock_query.data = "input_cancel:12345"
        mock_user = MagicMock()
        mock_user.id = 99999  # Different user
        mock_query.from_user = mock_user

        await input_cancel_callback(mock_client, mock_query)
        mock_query.answer.assert_called_with("⚠️ This input prompt belongs to another user.", show_alert=True)

        # Authorized user
        mock_user.id = 12345
        await input_cancel_callback(mock_client, mock_query)
        mock_query.message.edit_text.assert_called_with("❌ <b>Operation cancelled.</b>", parse_mode=enums.ParseMode.HTML)

    async def test_09_bot_health_watchdog_reconnect(self):
        """Verify watchdog detects disconnected client and attempts automatic reconnect."""
        mock_client = AsyncMock()
        mock_client.is_connected = False
        mock_client.is_initialized = True
        mock_client.get_me = AsyncMock(return_value=MagicMock(id=111, username="testbot", first_name="Test", last_name=""))

        ctx = BotContext(
            bot_id="bot_1",
            bot_name="Bot 1",
            session_name="sess_1",
            client=mock_client,
            is_online=False
        )

        with patch("main.start_single_bot", new=AsyncMock(return_value=True)) as mock_start, \
             patch("asyncio.sleep", new=AsyncMock()) as mock_sleep:
            # Let the loop execute once and break
            async def run_one_tick():
                for c in [ctx]:
                    if not c.client.is_connected:
                        await mock_start(c)
            
            await run_one_tick()
            self.assertTrue(mock_start.called)


if __name__ == "__main__":
    unittest.main()
