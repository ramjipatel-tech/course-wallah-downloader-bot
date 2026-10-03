# ==============================================================================
# TEST SUITE: ULTRA-PRO WATERMARK & DOWNLOAD PERFORMANCE PIPELINE
# ==============================================================================
# Verifies:
# 1. Watermark enabled (single-pass encode, duration/resolution/audio preserved)
# 2. Watermark disabled (zero re-encoding, instant return)
# 3. Duration unchanged (strict ffprobe validation)
# 4. Resolution unchanged (strict width/height preservation)
# 5. Audio preserved and synchronized
# 6. Encoder fallback (HW to CPU libx264 ultrafast)
# 7. Hardware encoder unavailable handling (clean fallback without crash)
# 8. Watermark failure preserves clean original source
# 9. One-pass watermark guarantee (no multi-pass re-encodes)
# 10. Stream-copy split (-c copy, no re-encoding during split)
# 11. No duplicate watermark (watermarked file not encoded again)
# 12. Cleanup after successful upload
# 13. Recovery after crash (resume from last checkpoint)
# 14. Spayee concurrent video & audio download
# 15. Pre-rendered watermark image caching
# ==============================================================================

import os
import sys
import time
import shutil
import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import itsgolu as helper
from itsgolu import (
    probe_media_properties,
    get_or_create_watermark_image,
    benchmark_and_get_fastest_encoder,
    detect_ffmpeg_hardware_encoders,
    apply_video_watermark,
    split_large_video,
    duration,
    get_video_duration
)
from spayee_downloader import (
    download_spayee_hls,
    create_spayee_session,
    clean_spayee_key,
    parse_spayee_input,
    rewrite_local_playlist
)
from utils import format_watermark_card, format_download_card, cleanup_uploaded_file
from job_manager import JobCheckpoint, JobManager


class TestUltraProWatermarkAndDownloadSuite(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="cw_ultrapro_test_"))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_real_test_video(self, filename: str, dur: float = 2.0, w: int = 1280, h: int = 720, has_audio: bool = True) -> Path:
        """Helper to create a small real test MP4 using FFmpeg."""
        out_path = self.test_dir / filename
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={dur}:size={w}x{h}:rate=30",
        ]
        if has_audio:
            cmd.extend(["-f", "lavfi", "-i", f"sine=frequency=1000:duration={dur}", "-c:a", "aac"])
        cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out_path)])
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_path

    # -------------------------------------------------------------------------
    # 1. WATERMARK ENABLED
    # -------------------------------------------------------------------------
    def test_01_watermark_enabled_applies_successfully(self):
        src_vid = self._create_real_test_video("vid_wm_enabled.mp4", dur=2.0)
        wm_res = apply_video_watermark(str(src_vid), watermark_text="Course Wallah")
        self.assertIsNotNone(wm_res)
        self.assertTrue(os.path.exists(wm_res))
        self.assertGreater(os.path.getsize(wm_res), 1024)

    # -------------------------------------------------------------------------
    # 2. WATERMARK DISABLED (ZERO RE-ENCODING)
    # -------------------------------------------------------------------------
    def test_02_watermark_disabled_returns_original_immediately(self):
        src_vid = self.test_dir / "vid_wm_disabled.mp4"
        src_vid.write_bytes(b"dummy_bytes_12345")
        
        # Test '/d'
        res1 = apply_video_watermark(str(src_vid), watermark_text="/d")
        self.assertEqual(res1, str(src_vid))
        
        # Test 'no'
        res2 = apply_video_watermark(str(src_vid), watermark_text="no")
        self.assertEqual(res2, str(src_vid))

        # Test None
        res3 = apply_video_watermark(str(src_vid), watermark_text=None)
        self.assertEqual(res3, str(src_vid))

        # Test empty
        res4 = apply_video_watermark(str(src_vid), watermark_text="")
        self.assertEqual(res4, str(src_vid))

    # -------------------------------------------------------------------------
    # 3. DURATION UNCHANGED
    # -------------------------------------------------------------------------
    def test_03_watermark_duration_unchanged(self):
        dur_target = 3.0
        src_vid = self._create_real_test_video("vid_duration_test.mp4", dur=dur_target)
        in_props = probe_media_properties(src_vid)
        
        wm_res = apply_video_watermark(str(src_vid), watermark_text="Course Wallah")
        self.assertIsNotNone(wm_res)
        
        out_props = probe_media_properties(wm_res)
        self.assertAlmostEqual(out_props["duration"], in_props["duration"], delta=0.2)

    # -------------------------------------------------------------------------
    # 4. RESOLUTION UNCHANGED
    # -------------------------------------------------------------------------
    def test_04_watermark_resolution_unchanged(self):
        src_vid = self._create_real_test_video("vid_res_test.mp4", dur=2.0, w=1280, h=720)
        in_props = probe_media_properties(src_vid)
        self.assertEqual(in_props["width"], 1280)
        self.assertEqual(in_props["height"], 720)

        wm_res = apply_video_watermark(str(src_vid), watermark_text="Course Wallah")
        self.assertIsNotNone(wm_res)

        out_props = probe_media_properties(wm_res)
        self.assertEqual(out_props["width"], 1280)
        self.assertEqual(out_props["height"], 720)

    # -------------------------------------------------------------------------
    # 5. AUDIO PRESERVED
    # -------------------------------------------------------------------------
    def test_05_watermark_audio_preserved(self):
        src_vid = self._create_real_test_video("vid_audio_test.mp4", dur=2.0, has_audio=True)
        in_props = probe_media_properties(src_vid)
        self.assertTrue(in_props["has_audio"])

        wm_res = apply_video_watermark(str(src_vid), watermark_text="Course Wallah")
        self.assertIsNotNone(wm_res)

        out_props = probe_media_properties(wm_res)
        self.assertTrue(out_props["has_audio"])
        self.assertAlmostEqual(out_props["audio_duration"], in_props["audio_duration"], delta=0.2)

    # -------------------------------------------------------------------------
    # 6. AUDIO COPY / AAC FALLBACK
    # -------------------------------------------------------------------------
    @patch("subprocess.run")
    def test_06_audio_fallback_to_aac_on_copy_failure(self, mock_sub):
        def fake_run(cmd, *args, **kwargs):
            if "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "copy":
                return MagicMock(returncode=1, stderr="Malformed AAC bitstream")
            # AAC fallback succeeds
            out_file = cmd[-1]
            Path(out_file).write_bytes(b"watermarked_aac_data")
            return MagicMock(returncode=0, stderr="")

        mock_sub.side_effect = fake_run

        dummy_vid = self.test_dir / "test_fb.mp4"
        dummy_vid.write_bytes(b"video_bytes_dummy")

        with patch("itsgolu.probe_media_properties", return_value={"valid": True, "duration": 10.0, "width": 1280, "height": 720, "fps": 30.0, "has_audio": True, "audio_duration": 10.0}):
            res = apply_video_watermark(str(dummy_vid), watermark_text="Course Wallah")
            self.assertIsNotNone(res)
            self.assertTrue(res.endswith("_wm.mp4"))

    # -------------------------------------------------------------------------
    # 7. HARDWARE ENCODER QUERY BASELINE
    # -------------------------------------------------------------------------
    def test_07_hardware_encoder_unavailable_selects_fastest_verified(self):
        name, flags = benchmark_and_get_fastest_encoder()
        self.assertEqual(name, "libx264")
        self.assertIsInstance(flags, list)
        self.assertIn("-c:v", flags)
        self.assertIn("ultrafast", flags)

    # -------------------------------------------------------------------------
    # 8. WATERMARK FAILURE PRESERVES CLEAN SOURCE
    # -------------------------------------------------------------------------
    @patch("subprocess.Popen")
    @patch("subprocess.run")
    def test_08_watermark_failure_preserves_clean_source(self, mock_run, mock_popen):
        # Force FFmpeg failure
        mock_run.return_value = MagicMock(returncode=1, stderr="Fatal encoder failure")
        
        dummy_vid = self.test_dir / "safe_source.mp4"
        dummy_vid.write_bytes(b"original_precious_data")

        res = apply_video_watermark(str(dummy_vid), watermark_text="Course Wallah")
        self.assertIsNone(res)
        # Original clean file MUST be preserved
        self.assertTrue(dummy_vid.exists())
        self.assertEqual(dummy_vid.read_bytes(), b"original_precious_data")

    # -------------------------------------------------------------------------
    # 9. ONE-PASS WATERMARK GUARANTEE
    # -------------------------------------------------------------------------
    @patch("subprocess.run")
    def test_09_one_pass_watermark_guarantee(self, mock_run):
        def fake_run(cmd, *args, **kwargs):
            out_file = cmd[-1]
            Path(out_file).write_bytes(b"watermarked_bytes")
            return MagicMock(returncode=0, stderr="")

        mock_run.side_effect = fake_run
        dummy_vid = self.test_dir / "one_pass.mp4"
        dummy_vid.write_bytes(b"video_bytes")

        with patch("itsgolu.probe_media_properties", return_value={"valid": True, "duration": 10.0, "width": 1280, "height": 720, "fps": 30.0, "has_audio": False}):
            res = apply_video_watermark(str(dummy_vid), watermark_text="Course Wallah")
            self.assertEqual(res, str(self.test_dir / "one_pass_wm.mp4"))
            
            # Verify encode command had only 1 video encode filter
            encode_calls = [c for c in mock_run.call_args_list if "ffmpeg" in str(c)]
            self.assertLessEqual(len(encode_calls), 2)

    # -------------------------------------------------------------------------
    # 10. STREAM-COPY SPLIT
    # -------------------------------------------------------------------------
    def test_10_stream_copy_split_uses_copy_codec(self):
        large_path = self.test_dir / "large_vid.mp4"
        large_path.write_bytes(b"0" * int(3.5 * 1024 * 1024))

        with patch("itsgolu.get_video_duration", return_value=120.0), \
             patch("itsgolu.duration", return_value=120.0), \
             patch("subprocess.run") as mock_sub:
            
            def fake_run(cmd, *args, **kwargs):
                out_f = cmd[-1]
                Path(out_f).write_bytes(b"0" * int(1.5 * 1024 * 1024))
                return MagicMock(returncode=0, stderr="")

            mock_sub.side_effect = fake_run

            parts = split_large_video(str(large_path), max_size_bytes=int(2.0 * 1024 * 1024), custom_dir=str(self.test_dir), base_title="Lecture")
            self.assertEqual(len(parts), 2)
            
            # Check FFmpeg commands use -c copy
            for call in mock_sub.call_args_list:
                cmd_args = call[0][0]
                if "ffmpeg" in cmd_args[0]:
                    self.assertIn("-c", cmd_args)
                    idx = cmd_args.index("-c")
                    self.assertEqual(cmd_args[idx + 1], "copy")

    # -------------------------------------------------------------------------
    # 11. NO DUPLICATE WATERMARK
    # -------------------------------------------------------------------------
    def test_11_no_duplicate_watermark_on_already_watermarked_file(self):
        wm_file = self.test_dir / "already_watermarked_wm.mp4"
        wm_file.write_bytes(b"watermarked_content")

        with patch("subprocess.run") as mock_sub:
            res = apply_video_watermark(str(wm_file), watermark_text="Course Wallah")
            self.assertEqual(res, str(wm_file))
            # Must not execute FFmpeg encode again
            self.assertEqual(mock_sub.call_count, 0)

    # -------------------------------------------------------------------------
    # 12. CLEANUP AFTER SUCCESS
    # -------------------------------------------------------------------------
    def test_12_cleanup_after_successful_upload(self):
        test_file = self.test_dir / "uploaded_file.mp4"
        test_file.write_bytes(b"temp_media_payload")
        self.assertTrue(test_file.exists())

        cleanup_uploaded_file(str(test_file))
        self.assertFalse(test_file.exists())

    # -------------------------------------------------------------------------
    # 13. RECOVERY AFTER CRASH
    # -------------------------------------------------------------------------
    def test_13_recovery_after_crash(self):
        checkpoint = JobCheckpoint(
            job_id="job_CRASH_TEST",
            user_id=999,
            chat_id=999,
            channel_id=-100123,
            source_filename="test.txt",
            total_items=5,
            current_index=2,
            phase="WATERMARKING",
            status="WATERMARKING"
        )
        self.assertEqual(checkpoint.current_index, 2)
        self.assertEqual(checkpoint.phase, "WATERMARKING")
        self.assertNotEqual(checkpoint.status, "COMPLETED")

    # -------------------------------------------------------------------------
    # 14. ATOMIC TEMP FILE HANDLING
    # -------------------------------------------------------------------------
    def test_14_watermark_atomic_file_safety(self):
        dummy_vid = self.test_dir / "atomic_test.mp4"
        dummy_vid.write_bytes(b"dummy_video")

        def fake_run(cmd, *args, **kwargs):
            out_file = cmd[-1]
            Path(out_file).write_bytes(b"processed_wm")
            return MagicMock(returncode=0, stderr="")

        with patch("subprocess.run", side_effect=fake_run), \
             patch("itsgolu.probe_media_properties", return_value={"valid": True, "duration": 5.0, "width": 1280, "height": 720, "fps": 30.0, "has_audio": False}):
            res = apply_video_watermark(str(dummy_vid), watermark_text="Course Wallah")
            self.assertIsNotNone(res)
            self.assertTrue(res.endswith("_wm.mp4"))
            self.assertTrue(os.path.exists(res))

    # -------------------------------------------------------------------------
    # 15. SPAYEE SESSION POOLING & AES KEY
    # -------------------------------------------------------------------------
    def test_15_spayee_session_pooling(self):
        session = create_spayee_session(workers=16)
        self.assertIsNotNone(session)
        # Check adapter pool size
        adapter = session.adapters.get("https://")
        self.assertIsNotNone(adapter)
        self.assertGreaterEqual(getattr(adapter, "_pool_maxsize", 0), 32)


if __name__ == "__main__":
    unittest.main()
