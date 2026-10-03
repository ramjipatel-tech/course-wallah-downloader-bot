import os
import sys
import unittest
import tempfile
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

from utils import MediaRouter, MediaType, is_direct_image_url, is_direct_pdf_url, is_kgs_url, is_spayee_url, is_youtube_url, build_failure_card
from job_manager import JobManager, JobCheckpoint
from itsgolu import download_image, download_direct_video, apply_pdf_watermark, send_vid
import main
from main import BotContext, get_bot_context, register_bot_context


class TestFinalRestoration(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. BOT CONTEXT REGRESSION TEST (Requirement 13)
    # -------------------------------------------------------------------------
    def test_handler_has_valid_bot_context(self):
        """Ensures every bot instance has a valid BotContext and no undefined bot_idx exists."""
        mock_client1 = MagicMock()
        mock_client1.name = "coursewallah_bot_1"
        b_ctx1 = BotContext(bot_id="bot_1", bot_name="Bot 1", session_name="session1", client=mock_client1, bot_username="bot1")
        register_bot_context(mock_client1, b_ctx1)

        mock_client2 = MagicMock()
        mock_client2.name = "coursewallah_bot_2"
        b_ctx2 = BotContext(bot_id="bot_2", bot_name="Bot 2", session_name="session2", client=mock_client2, bot_username="bot2")
        register_bot_context(mock_client2, b_ctx2)

        self.assertEqual(get_bot_context(mock_client1).bot_id, "bot_1")
        self.assertEqual(get_bot_context(mock_client2).bot_id, "bot_2")

    # -------------------------------------------------------------------------
    # 2. MEDIA ROUTING PRIORITY TESTS (Requirement 3 & 8)
    # -------------------------------------------------------------------------
    def test_routing_priorities(self):
        # 1. Direct Image
        img_url = "https://example.com/assets/poster.png"
        self.assertEqual(MediaRouter.classify_url(img_url), MediaType.DIRECT_IMAGE)

        # 2. Direct PDF
        pdf_url = "https://example.com/notes/lecture1.pdf"
        self.assertEqual(MediaRouter.classify_url(pdf_url), MediaType.DIRECT_PDF)

        # 2b. KGS PDF MUST route to DIRECT_PDF (Requirement 8)
        kgs_pdf = "https://kgs-v2.akamaized.net/kgs/kgs/pdfs/6438015949cf944888bebb34.pdf"
        self.assertEqual(MediaRouter.classify_url(kgs_pdf), MediaType.DIRECT_PDF)
        self.assertFalse(is_kgs_url(kgs_pdf))

        # 3. KGS HLS Stream
        kgs_hls = "https://kgs-new-v1.akamaized.net/kv3/sample/master.m3u8?hdnts=exp=123"
        self.assertEqual(MediaRouter.classify_url(kgs_hls), MediaType.KGS_HLS)

        # 4. Spayee HLS Stream
        spayee_url = "https://qcdn.spayee.in/videos/648dea/index.m3u8*648dea11fe203008b24f492175fe674d"
        self.assertEqual(MediaRouter.classify_url(spayee_url), MediaType.SPAYEE_HLS)

        # 5. YouTube
        yt_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(MediaRouter.classify_url(yt_url), MediaType.YOUTUBE)

        # 6. Encrypted Stream
        enc_url = "https://dragoapi.vercel.app/video.mpd*keys"
        self.assertEqual(MediaRouter.classify_url(enc_url), MediaType.ENCRYPTED_STREAM)

        # 7. Generic Direct M3U8
        generic_m3u8 = "https://example.com/stream/master.m3u8"
        self.assertEqual(MediaRouter.classify_url(generic_m3u8), MediaType.DIRECT_M3U8)

        # 8. APPX Lecture
        appx_url = "https://akstechnicalclasses.classx.co.in/fetch_video?id=123"
        self.assertEqual(MediaRouter.classify_url(appx_url), MediaType.APPX_LECTURE)

        # 9. Direct Video
        mp4_url = "https://example.com/video/lecture01.mp4"
        self.assertEqual(MediaRouter.classify_url(mp4_url), MediaType.DIRECT_VIDEO)

    # -------------------------------------------------------------------------
    # 3. INDEPENDENT STATUS REPORTING (Requirement 12 & 31)
    # -------------------------------------------------------------------------
    def test_independent_status_reporting(self):
        # Case A: Video success, PDF not available
        card_a = build_failure_card("LECTURE", "Lecture 01", "Math", video_status="SUCCESS", pdf_status="NOT_AVAILABLE")
        self.assertIn("Video:</b> ✅ Success", card_a)
        self.assertIn("PDF:</b> ⚪ Not Available", card_a)

        # Case B: Video failed, PDF success
        card_b = build_failure_card("LECTURE", "Lecture 01", "Math", video_status="FAILED", pdf_status="SUCCESS")
        self.assertIn("Video:</b> ❌ Download failed", card_b)
        self.assertIn("PDF:</b> ✅ Success", card_b)

        # Case C: Video failed with 403, PDF not available
        card_c = build_failure_card("LECTURE", "Lecture 01", "Math", technical_error="KGS_AUTH_403 Server returned 403 Forbidden", video_status="FAILED", pdf_status="NOT_AVAILABLE")
        self.assertIn("Video:</b> ❌ Authorization rejected or expired", card_c)
        self.assertIn("PDF:</b> ⚪ Not Available", card_c)

    # -------------------------------------------------------------------------
    # 4. KGS 403 FAST-FAIL (Requirement 9)
    # -------------------------------------------------------------------------
    def test_kgs_403_fast_fail(self):
        from kgs_downloader import resolve_kgs_playlist, download_kgs
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        with patch("requests.get", return_value=mock_resp):
            stream_url, meta = resolve_kgs_playlist("https://kgs-new-v1.akamaized.net/kv3/test/master.m3u8")
            self.assertEqual(meta.get("error"), "KGS_AUTH_403")
            self.assertTrue(meta.get("auth_error"))

            # download_kgs aborts immediately and returns None without 3 retries
            res = download_kgs("https://kgs-new-v1.akamaized.net/kv3/test/master.m3u8", "test_lec", custom_dir=self.test_dir)
            self.assertIsNone(res)

    # -------------------------------------------------------------------------
    # 5. SEND_VID KWARGS SAFETY (Requirement 13)
    # -------------------------------------------------------------------------
    def test_send_vid_kwargs_signature(self):
        import inspect
        sig = inspect.signature(send_vid)
        self.assertIn("user_id", sig.parameters)
        self.assertIn("bot_id", sig.parameters)


if __name__ == "__main__":
    unittest.main()
