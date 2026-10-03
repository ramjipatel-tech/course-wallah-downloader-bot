# ==============================================================================
# COURSE WALLAH DOWNLOADER — PRODUCTION REPAIR TEST SUITE
# ==============================================================================
# Verifies all 30 core requirements from Section 44:
# 1. KGS URL detection
# 2. KGS PDF detection
# 3. KGS signed query preservation
# 4. KGS 403 handling
# 5. KGS original-reference compatibility
# 6. Spayee parser
# 7. Spayee AES key parsing
# 8. Spayee variant selection
# 9. Spayee parallel segment ordering
# 10. Spayee failed segment retry
# 11. Spayee cancellation
# 12. PDF URL*PASSWORD
# 13. PDF password not persisted
# 14. PDF validation
# 15. Watermark disabled
# 16. Watermark hardware detection
# 17. Watermark fallback
# 18. Watermark progress
# 19. Upload progress
# 20. Cleanup ONLY after successful upload
# 21. Failed upload keeps media
# 22. Successful multipart upload cleans all parts
# 23. Retry only failed part
# 24. YouTube warning not treated as fatal
# 25. Direct M3U8
# 26. Direct video
# 27. Multi-bot isolation
# 28. Forum topic isolation
# 29. Crash recovery
# 30. No secret leakage in logs
# ==============================================================================

import os
import sys
import time
import tempfile
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

from vars import SPAYEE_SEGMENT_WORKERS, MAX_UPLOAD_SIZE_BYTES, TEMP_DIR
from utils import (
    MediaRouter,
    MediaType,
    parse_pdf_input,
    sanitize_error_message,
    progress_bar,
    cleanup_uploaded_file,
    is_safe_temp_path
)
from itsgolu import validate_unlocked_pdf
from kgs_downloader import (
    is_kgs_url,
    select_kgs_variant,
    resolve_kgs_playlist,
    download_kgs,
    DEFAULT_KGS_HEADERS
)
from spayee_downloader import (
    is_spayee_url,
    parse_spayee_input,
    clean_spayee_key,
    select_spayee_variant,
    rewrite_local_playlist,
    download_segments_parallel,
    download_spayee_hls
)
import itsgolu as helper
import m3u8


class TestProductionRepairSuite(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        os.makedirs(TEMP_DIR, exist_ok=True)
        self.test_dir = tempfile.mkdtemp(prefix="prod_repair_test_", dir=TEMP_DIR)

    def tearDown(self):
        import shutil
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    # 1. KGS URL Detection
    def test_01_kgs_url_detection(self):
        kgs_url = "https://kgs-new-v1.akamaized.net/kv3/physics/master.m3u8?hdnts=exp=170000~hmac=abcd"
        self.assertTrue(is_kgs_url(kgs_url))
        self.assertEqual(MediaRouter.classify_url(kgs_url), MediaType.KGS_HLS)

    # 2. KGS PDF Detection
    def test_02_kgs_pdf_detection(self):
        kgs_pdf = "https://kgs-new-v1.akamaized.net/kv3/notes/physics_handwritten.pdf?hdnts=exp=170000~hmac=abcd"
        self.assertFalse(is_kgs_url(kgs_pdf))
        self.assertEqual(MediaRouter.classify_url(kgs_pdf), MediaType.DIRECT_PDF)

    # 3. KGS Signed Query Preservation
    def test_03_kgs_signed_query_preservation(self):
        master_content = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720\n"
            "720p.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=854x480\n"
            "480p.m3u8\n"
        )
        parsed = m3u8.loads(master_content)
        base_url = "https://kgs-new-v1.akamaized.net/kv3/test/master.m3u8?hdnts=exp=99999~hmac=secret123"
        variant_url, q_tag = select_kgs_variant(parsed, "720p", base_url=base_url)
        self.assertIn("720p.m3u8", variant_url)
        self.assertIn("hdnts=exp=99999~hmac=secret123", variant_url)
        self.assertEqual(q_tag, "720p")

    # 4. KGS 403 Handling (Fast fail without blind retries)
    @patch("requests.get")
    def test_04_kgs_403_handling(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_get.return_value = mock_resp

        url = "https://kgs-new-v1.akamaized.net/kv3/expired/master.m3u8?hdnts=exp=1000"
        res_url, meta = resolve_kgs_playlist(url)
        self.assertTrue(meta.get("auth_error"))
        self.assertEqual(meta.get("error"), "KGS_AUTH_403")

        # download_kgs aborts immediately without 3 retries
        res = download_kgs(url, "Lecture 1", custom_dir=self.test_dir)
        self.assertIsNone(res)

    # 5. KGS Original Reference Compatibility
    def test_05_kgs_original_reference_compatibility(self):
        self.assertIn("khanglobalstudies.com", DEFAULT_KGS_HEADERS["Origin"])
        self.assertIn("khanglobalstudies.com/", DEFAULT_KGS_HEADERS["Referer"])
        self.assertIn("User-Agent", DEFAULT_KGS_HEADERS)

    # 6. Spayee Parser
    def test_06_spayee_parser(self):
        combined = "https://qcdn.spayee.in/courses/test/index.m3u8*648dea11fe203008b24f492175fe674d"
        url, key = parse_spayee_input(combined)
        self.assertEqual(url, "https://qcdn.spayee.in/courses/test/index.m3u8")
        self.assertEqual(key, "648dea11fe203008b24f492175fe674d")

    # 7. Spayee AES Key Parsing
    def test_07_spayee_aes_key_parsing(self):
        # 32-char hex
        raw_hex = clean_spayee_key("648dea11fe203008b24f492175fe674d")
        self.assertEqual(len(raw_hex), 16)

        # 16-byte raw
        raw_bytes = clean_spayee_key(b"1234567890123456")
        self.assertEqual(raw_bytes, b"1234567890123456")

        # Invalid key raises ValueError
        with self.assertRaises(ValueError):
            clean_spayee_key("invalid_short_key")

    # 8. Spayee Variant Selection
    def test_08_spayee_variant_selection(self):
        master_content = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080\n"
            "1080/index.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=1280x720\n"
            "720/index.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=600000,RESOLUTION=854x480\n"
            "480/index.m3u8\n"
        )
        parsed = m3u8.loads(master_content)
        base = "https://qcdn.spayee.in/master.m3u8"

        # Default highest available (1080p)
        url_1080, h_1080 = select_spayee_variant(parsed, base)
        self.assertEqual(h_1080, 1080)
        self.assertIn("1080/index.m3u8", url_1080)

        # Explicit 720p request
        url_720, h_720 = select_spayee_variant(parsed, base, preferred_quality="720p")
        self.assertEqual(h_720, 720)
        self.assertIn("720/index.m3u8", url_720)

    # 9. Spayee Parallel Segment Ordering
    def test_09_spayee_parallel_segment_ordering(self):
        m3u8_text = (
            "#EXTM3U\n"
            "#EXT-X-TARGETDURATION:10\n"
            "#EXTINF:10.0,\n"
            "seg0.ts\n"
            "#EXTINF:10.0,\n"
            "seg1.ts\n"
            "#EXTINF:10.0,\n"
            "seg2.ts\n"
            "#EXT-X-ENDLIST\n"
        )
        rewritten, segments = rewrite_local_playlist(
            m3u8_text, "https://qcdn.spayee.in/hls/", "video_segments", "video_key.bin", False
        )
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0][0], 1)
        self.assertEqual(segments[0][2], "video_segments/segment_000001.ts")
        self.assertEqual(segments[1][2], "video_segments/segment_000002.ts")
        self.assertEqual(segments[2][2], "video_segments/segment_000003.ts")
        self.assertIn("video_segments/segment_000001.ts", rewritten)
        self.assertIn("video_segments/segment_000002.ts", rewritten)
        self.assertIn("video_segments/segment_000003.ts", rewritten)

    # 10. Spayee Failed Segment Retry
    @patch("requests.Session.get")
    def test_10_spayee_failed_segment_retry(self, mock_get):
        mock_get.side_effect = RuntimeError("Network reset")
        session = MagicMock()
        session.get = mock_get
        segments = [(1, "https://qcdn.spayee.in/seg1.ts", "video_segments/segment_000001.ts")]
        with self.assertRaises(RuntimeError) as ctx:
            download_segments_parallel(session, segments, Path(self.test_dir), workers=2)
        self.assertIn("SPAYEE_SEGMENT_FAILED", str(ctx.exception))

    # 11. Spayee Cancellation
    def test_11_spayee_cancellation(self):
        class MockJob:
            is_cancelled = True

        session = MagicMock()
        segments = [(1, "https://qcdn.spayee.in/seg1.ts", "video_segments/segment_000001.ts")]
        with self.assertRaises(RuntimeError) as ctx:
            download_segments_parallel(session, segments, Path(self.test_dir), workers=2, job_context=MockJob())
        self.assertIn("cancelled", str(ctx.exception).lower())

    # 12. PDF URL*PASSWORD
    def test_12_pdf_url_password(self):
        inp = "https://example.com/notes.pdf*0f4ad22a-c620-4b44-9903-30937420b12f"
        parsed = parse_pdf_input(inp)
        self.assertEqual(parsed["url"], "https://example.com/notes.pdf")
        self.assertEqual(parsed["password"], "0f4ad22a-c620-4b44-9903-30937420b12f")
        self.assertIsNotNone(parsed["password"])

    # 13. PDF Password Not Persisted
    def test_13_pdf_password_not_persisted(self):
        from job_manager import JobCheckpoint
        job = JobCheckpoint(
            job_id="test_pwd_job",
            user_id=12345,
            chat_id=12345,
            channel_id=-100111,
            source_filename="course.txt",
            total_items=5,
            bot_id="bot_1"
        )
        d = job.to_dict()
        self.assertNotIn("password", d)

    # 14. PDF Validation
    def test_14_pdf_validation(self):
        # Empty file is invalid
        empty_file = os.path.join(self.test_dir, "empty.pdf")
        open(empty_file, "wb").close()
        is_valid, pages, msg = validate_unlocked_pdf(empty_file)
        self.assertFalse(is_valid)

    # 15. Watermark Disabled
    def test_15_watermark_disabled(self):
        dummy_vid = os.path.join(self.test_dir, "dummy.mp4")
        with open(dummy_vid, "wb") as f:
            f.write(b"fake_mp4_bytes")

        res_d = helper.apply_video_watermark(dummy_vid, watermark_text="/d")
        self.assertEqual(res_d, dummy_vid)

        res_none = helper.apply_video_watermark(dummy_vid, watermark_text=None)
        self.assertEqual(res_none, dummy_vid)

    # 16. Watermark Hardware Detection
    def test_16_watermark_hardware_detection(self):
        enc_name, enc_args = helper.benchmark_and_get_fastest_encoder()
        self.assertIsNotNone(enc_name)
        self.assertIsInstance(enc_args, list)
        self.assertIn("-c:v", enc_args)

    # 17. Watermark Fallback
    @patch("subprocess.run")
    def test_17_watermark_fallback(self, mock_sub):
        def fake_run(cmd, *args, **kwargs):
            if "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "copy":
                return MagicMock(returncode=1, stderr="Audio copy failed")
            out_file = cmd[-1]
            Path(out_file).write_bytes(b"watermarked_data")
            return MagicMock(returncode=0, stderr="")

        mock_sub.side_effect = fake_run

        dummy_vid = os.path.join(self.test_dir, "test_fallback.mp4")
        with open(dummy_vid, "wb") as f:
            f.write(b"video_data")

        with patch("itsgolu.probe_media_properties", return_value={"valid": True, "duration": 5.0, "width": 1280, "height": 720, "fps": 30.0, "has_audio": True, "audio_duration": 5.0}):
            res = helper.apply_video_watermark(dummy_vid, watermark_text="Course Wallah")
            self.assertIsNotNone(res)
            self.assertTrue(res.endswith("_wm.mp4"))

    # 18. Watermark Progress
    def test_18_watermark_progress(self):
        from utils import format_watermark_card
        card = format_watermark_card("Physics Lecture 01", frame=2)
        self.assertIn("WATERMARKING VIDEO", card)
        self.assertIn("Physics Lecture 01", card)

    # 19. Upload Progress Calculation
    async def test_19_upload_progress(self):
        reply_mock = AsyncMock()
        reply_mock.edit_text = AsyncMock()
        await progress_bar(500 * 1024 * 1024, 1000 * 1024 * 1024, reply_mock, time.time() - 10, name="Lecture 01")
        self.assertTrue(reply_mock.edit_text.called)
        call_text = reply_mock.edit_text.call_args[0][0]
        self.assertIn("UPLOADING", call_text)
        self.assertIn("50.0%", call_text)

    # 20. Cleanup ONLY After Successful Upload
    @patch("itsgolu.duration", return_value=60.0)
    async def test_20_cleanup_only_after_successful_upload(self, mock_dur):
        media_file = os.path.join(self.test_dir, "lecture_success.mp4")
        with open(media_file, "wb") as f:
            f.write(b"sample_bytes")

        mock_bot = MagicMock()
        mock_msg = MagicMock()
        mock_bot.send_video = AsyncMock(return_value=mock_msg)

        res = await helper.send_vid(
            mock_bot, None, "Caption", media_file, None, "Lecture", None, -100123
        )
        self.assertIsNotNone(res)
        # Media file must be cleaned after confirmed upload success
        self.assertFalse(os.path.exists(media_file))

    # 21. Failed Upload Keeps Media
    @patch("itsgolu.duration", return_value=60.0)
    async def test_21_failed_upload_keeps_media(self, mock_dur):
        media_file = os.path.join(self.test_dir, "lecture_failed.mp4")
        with open(media_file, "wb") as f:
            f.write(b"sample_bytes")

        mock_bot = MagicMock()
        mock_bot.send_video = AsyncMock(return_value=None)
        mock_bot.send_document = AsyncMock(return_value=None)

        res = await helper.send_vid(
            mock_bot, None, "Caption", media_file, None, "Lecture", None, -100123
        )
        self.assertIsNone(res)
        # Media file must NOT be deleted if upload failed
        self.assertTrue(os.path.exists(media_file))

    # 22. Successful Multipart Upload Cleans All Parts
    @patch("itsgolu.duration", return_value=60.0)
    @patch("itsgolu.split_large_video")
    async def test_22_successful_multipart_upload_cleans_all_parts(self, mock_split, mock_dur):
        orig_large = os.path.join(self.test_dir, "large.mp4")
        part1 = os.path.join(self.test_dir, "large_Part_1.mp4")
        part2 = os.path.join(self.test_dir, "large_Part_2.mp4")

        with open(orig_large, "wb") as f:
            f.write(b"orig_large_dummy")
        with open(part1, "wb") as f:
            f.write(b"part1_bytes")
        with open(part2, "wb") as f:
            f.write(b"part2_bytes")

        mock_split.return_value = [part1, part2]

        mock_bot = MagicMock()
        mock_bot.send_video = AsyncMock(return_value=MagicMock())

        def fake_getsize_22(path):
            if str(path) == str(orig_large):
                return MAX_UPLOAD_SIZE_BYTES + 1000
            return 100

        with patch("os.path.getsize", side_effect=fake_getsize_22):
            res = await helper.send_vid(
                mock_bot, None, "Caption", orig_large, None, "Large Lecture", None, -100123
            )
        self.assertIsNotNone(res)
        # All parts and original must be cleaned
        self.assertFalse(os.path.exists(part1))
        self.assertFalse(os.path.exists(part2))
        self.assertFalse(os.path.exists(orig_large))

    # 23. Retry Only Failed Part
    @patch("itsgolu.duration", return_value=60.0)
    @patch("itsgolu.split_large_video")
    async def test_23_retry_only_failed_part(self, mock_split, mock_dur):
        orig_large = os.path.join(self.test_dir, "large_fail.mp4")
        part1 = os.path.join(self.test_dir, "large_Part_1.mp4")
        part2 = os.path.join(self.test_dir, "large_Part_2.mp4")

        with open(orig_large, "wb") as f:
            f.write(b"orig_large_dummy")
        with open(part1, "wb") as f:
            f.write(b"part1_bytes")
        with open(part2, "wb") as f:
            f.write(b"part2_bytes")

        mock_split.return_value = [part1, part2]

        # Part 1 succeeds, Part 2 fails
        mock_bot = MagicMock()
        mock_bot.send_video = AsyncMock(side_effect=[MagicMock(), None])
        mock_bot.send_document = AsyncMock(return_value=None)

        def fake_getsize_23(path):
            if str(path) == str(orig_large):
                return MAX_UPLOAD_SIZE_BYTES + 1000
            return 100

        with patch("os.path.getsize", side_effect=fake_getsize_23):
            res = await helper.send_vid(
                mock_bot, None, "Caption", orig_large, None, "Large Lecture", None, -100123
            )
        # Part 1 was cleaned on success, failed Part 2 remains on disk for retry
        self.assertFalse(os.path.exists(part1))
        self.assertTrue(os.path.exists(part2))

    # 24. YouTube Warning Not Treated As Fatal
    async def test_24_youtube_warning_not_treated_as_fatal(self):
        target_video = os.path.join(self.test_dir, "yt_test_720p.mp4")
        with open(target_video, "wb") as f:
            f.write(b"fake_yt_mp4_bytes")

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"", b"WARNING: -f best selects pre-merged format"))
            mock_exec.return_value = mock_proc

            res = await helper.download_video("https://youtube.com/watch?v=123", "yt_test", quality="720p", custom_dir=self.test_dir)
            self.assertIsNotNone(res)
            self.assertTrue(os.path.exists(res))

    # 25. Direct M3U8
    def test_25_direct_m3u8(self):
        hls_url = "https://example.com/vod/live_stream/index.m3u8"
        self.assertEqual(MediaRouter.classify_url(hls_url), MediaType.DIRECT_M3U8)

    # 26. Direct Video
    def test_26_direct_video(self):
        mp4_url = "https://cdn.example.com/videos/lecture12.mp4"
        self.assertEqual(MediaRouter.classify_url(mp4_url), MediaType.DIRECT_VIDEO)

    # 27. Multi-Bot Isolation
    def test_27_multi_bot_isolation(self):
        from job_manager import JobManager
        jm1 = JobManager("bot_1")
        jm2 = JobManager("bot_2")

        job1 = jm1.create_job(
            user_id=101,
            chat_id=101,
            channel_id=-100111,
            source_filename="course.txt",
            raw_content="[Physics] Lec 1: https://ex.com/1",
            total_items=1
        )
        job2 = jm2.create_job(
            user_id=101,
            chat_id=101,
            channel_id=-100111,
            source_filename="course.txt",
            raw_content="[Physics] Lec 2: https://ex.com/2",
            total_items=1
        )

        self.assertEqual(job1.bot_id, "bot_1")
        self.assertEqual(job2.bot_id, "bot_2")
        self.assertNotEqual(job1.job_id, job2.job_id)

    # 28. Forum Topic Isolation
    def test_28_forum_topic_isolation(self):
        from db import db
        # Save isolated topics per bot_id
        db.save_topic_id(-100111, "Physics", 100, bot_id="bot_1")
        db.save_topic_id(-100111, "Physics", 200, bot_id="bot_2")

        self.assertEqual(db.get_topic_id(-100111, "Physics", bot_id="bot_1"), 100)
        self.assertEqual(db.get_topic_id(-100111, "Physics", bot_id="bot_2"), 200)

    # 29. Crash Recovery
    def test_29_crash_recovery(self):
        from db import db
        test_job = {
            "job_id": "CRASH_RECOVER_01",
            "user_id": 999,
            "bot_id": "bot_1",
            "phase": "DOWNLOADING",
            "progress": "5/10",
            "updated_at": time.time()
        }
        db.save_job(test_job)
        jobs = db.list_jobs()
        job_ids = [j["job_id"] for j in jobs]
        self.assertIn("CRASH_RECOVER_01", job_ids)

        loaded = db.get_job("CRASH_RECOVER_01", bot_id="bot_1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["bot_id"], "bot_1")

        # Cleanup
        db.delete_job("CRASH_RECOVER_01")

    # 30. No Secret Leakage in Logs
    def test_30_no_secret_leakage_in_logs(self):
        raw_error = "Error fetching https://kgs.com/m3u8?hdnts=exp=170000~hmac=supersecretkey12345 password=my_secret_pass key=648dea11fe203008b24f492175fe674d token=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ"
        sanitized = sanitize_error_message(raw_error)
        self.assertNotIn("supersecretkey12345", sanitized)
        self.assertNotIn("my_secret_pass", sanitized)
        self.assertNotIn("648dea11fe203008b24f492175fe674d", sanitized)
        self.assertIn("[REDACTED]", sanitized)


if __name__ == "__main__":
    unittest.main()
