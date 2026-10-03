# ==============================================================================
# TEST SUITE: SPAYEE SPECIALIZED HLS DOWNLOADER MODULE (SECTION 9)
# ==============================================================================
import os
import sys
import shutil
import tempfile
import unittest
import base64
from pathlib import Path
from unittest.mock import patch, MagicMock

import m3u8

from spayee_downloader import (
    is_spayee_url,
    clean_spayee_key,
    parse_spayee_input,
    create_spayee_session,
    select_spayee_variant,
    detect_separate_audio,
    rewrite_local_playlist,
    download_segment_with_retry,
    process_spayee_ffmpeg,
    validate_spayee_output,
    download_spayee_hls,
    DEFAULT_SPAYEE_HEADERS
)
from bracket_topic_parser import parse_bracket_topic_raw, parse_bracket_topic_txt
from utils import MediaRouter, MediaType, is_safe_temp_path
from vars import TEMP_DIR, DOWNLOADS_DIR


class TestSpayeeSpecialPipeline(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="spayee_test_"))
        self.dummy_hex_key = "648dea11fe203008b24f492175fe674d"
        self.dummy_raw_bytes = bytes.fromhex(self.dummy_hex_key)
        self.dummy_b64_key = base64.b64encode(self.dummy_raw_bytes).decode("ascii")

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 9.1 INPUT PARSING TESTS
    # -------------------------------------------------------------------------
    def test_01_input_parsing_splits_at_first_asterisk_only(self):
        combined = "https://qcdn.spayee.in/courses/math/index.m3u8*648dea11fe203008b24f492175fe674d"
        url, key = parse_spayee_input(combined)
        self.assertEqual(url, "https://qcdn.spayee.in/courses/math/index.m3u8")
        self.assertEqual(key, "648dea11fe203008b24f492175fe674d")

    def test_02_input_parsing_url_without_key(self):
        url_only = "https://qcdn.spayee.in/courses/math/index.m3u8"
        url, key = parse_spayee_input(url_only)
        self.assertEqual(url, "https://qcdn.spayee.in/courses/math/index.m3u8")
        self.assertIsNone(key)

    def test_03_input_parsing_with_explicit_key(self):
        url_only = "https://qcdn.spayee.in/courses/math/index.m3u8"
        url, key = parse_spayee_input(url_only, explicit_key="my_explicit_key_123")
        self.assertEqual(url, "https://qcdn.spayee.in/courses/math/index.m3u8")
        self.assertEqual(key, "my_explicit_key_123")

    def test_04_input_parsing_never_modifies_url_params(self):
        combined = "https://qcdn.spayee.in/index.m3u8?token=xyz123&exp=999*648dea11fe203008b24f492175fe674d"
        url, key = parse_spayee_input(combined)
        self.assertEqual(url, "https://qcdn.spayee.in/index.m3u8?token=xyz123&exp=999")
        self.assertEqual(key, "648dea11fe203008b24f492175fe674d")

    # -------------------------------------------------------------------------
    # 9.2 AES KEY VALIDATION TESTS
    # -------------------------------------------------------------------------
    def test_05_clean_spayee_key_hex_format(self):
        key_bytes = clean_spayee_key(self.dummy_hex_key)
        self.assertEqual(len(key_bytes), 16)
        self.assertEqual(key_bytes, self.dummy_raw_bytes)

    def test_06_clean_spayee_key_base64_format(self):
        key_bytes = clean_spayee_key(self.dummy_b64_key)
        self.assertEqual(len(key_bytes), 16)
        self.assertEqual(key_bytes, self.dummy_raw_bytes)

    def test_07_clean_spayee_key_raw_bytes(self):
        key_bytes = clean_spayee_key(self.dummy_raw_bytes)
        self.assertEqual(len(key_bytes), 16)
        self.assertEqual(key_bytes, self.dummy_raw_bytes)

    def test_08_clean_spayee_key_rejects_invalid_lengths(self):
        # 30 hex characters instead of 32
        with self.assertRaises(ValueError):
            clean_spayee_key("648dea11fe203008b24f492175fe67")

        # 34 hex characters
        with self.assertRaises(ValueError):
            clean_spayee_key("648dea11fe203008b24f492175fe674daa")

        # Invalid base64 representing non-16 bytes
        bad_b64 = base64.b64encode(b"short").decode("ascii")
        with self.assertRaises(ValueError):
            clean_spayee_key(bad_b64)

        # Empty key
        with self.assertRaises(ValueError):
            clean_spayee_key("")

    # -------------------------------------------------------------------------
    # 9.3 HTTP SESSION & HEADERS TESTS
    # -------------------------------------------------------------------------
    def test_09_create_spayee_session_default_headers(self):
        session = create_spayee_session()
        self.assertIn("User-Agent", session.headers)
        self.assertEqual(session.headers["Referer"], "https://www.goclasses.in/")

    def test_10_create_spayee_session_custom_headers_override(self):
        custom = {"Referer": "https://custom.coursewallah.com/", "X-Custom-Auth": "token123"}
        session = create_spayee_session(headers=custom)
        self.assertEqual(session.headers["Referer"], "https://custom.coursewallah.com/")
        self.assertEqual(session.headers["X-Custom-Auth"], "token123")
        self.assertIn("User-Agent", session.headers)

    # -------------------------------------------------------------------------
    # 9.5 & 9.6 MASTER PLAYLIST & QUALITY SELECTION TESTS
    # -------------------------------------------------------------------------
    def test_11_select_highest_quality_automatically(self):
        master_m3u8 = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
360/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=854x480
480/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720
720/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080
1080/index.m3u8
"""
        parsed = m3u8.loads(master_m3u8, uri="https://qcdn.spayee.in/video/master.m3u8")
        chosen_url, height = select_spayee_variant(parsed, "https://qcdn.spayee.in/video/master.m3u8")
        self.assertEqual(height, 1080)
        self.assertEqual(chosen_url, "https://qcdn.spayee.in/video/1080/index.m3u8")

    def test_12_select_fallback_720p_when_1080p_absent(self):
        master_m3u8 = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
360/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=854x480
480/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720
720/index.m3u8
"""
        parsed = m3u8.loads(master_m3u8, uri="https://qcdn.spayee.in/video/master.m3u8")
        chosen_url, height = select_spayee_variant(parsed, "https://qcdn.spayee.in/video/master.m3u8")
        self.assertEqual(height, 720)
        self.assertEqual(chosen_url, "https://qcdn.spayee.in/video/720/index.m3u8")

    def test_13_select_preferred_quality_when_explicitly_requested(self):
        master_m3u8 = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=854x480
480/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720
720/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080
1080/index.m3u8
"""
        parsed = m3u8.loads(master_m3u8, uri="https://qcdn.spayee.in/video/master.m3u8")
        chosen_url, height = select_spayee_variant(parsed, "https://qcdn.spayee.in/video/master.m3u8", preferred_quality="480p")
        self.assertEqual(height, 480)
        self.assertEqual(chosen_url, "https://qcdn.spayee.in/video/480/index.m3u8")

    # -------------------------------------------------------------------------
    # 9.9 SEPARATE AUDIO DETECTION TESTS
    # -------------------------------------------------------------------------
    def test_14_detect_separate_audio_stream(self):
        master_with_audio = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="English",DEFAULT=YES,AUTOSELECT=YES,URI="audio/audio_master.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720,AUDIO="audio"
720/index.m3u8
"""
        parsed = m3u8.loads(master_with_audio, uri="https://qcdn.spayee.in/vod/master.m3u8")
        audio_url = detect_separate_audio(parsed, "https://qcdn.spayee.in/vod/master.m3u8")
        self.assertEqual(audio_url, "https://qcdn.spayee.in/vod/audio/audio_master.m3u8")

    def test_15_detect_separate_audio_absent(self):
        master_no_audio = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720
720/index.m3u8
"""
        parsed = m3u8.loads(master_no_audio, uri="https://qcdn.spayee.in/vod/master.m3u8")
        audio_url = detect_separate_audio(parsed, "https://qcdn.spayee.in/vod/master.m3u8")
        self.assertIsNone(audio_url)

    # -------------------------------------------------------------------------
    # 9.16 - 9.18 LOCAL PLAYLIST REWRITING & AES KEY URI TESTS
    # -------------------------------------------------------------------------
    def test_16_rewrite_local_playlist_replaces_segments_and_key_uri(self):
        source_playlist = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXT-X-KEY:METHOD=AES-128,URI="https://qcdn.spayee.in/auth/key?id=123",IV=0x1234567890abcdef1234567890abcdef
#EXTINF:6.0,
segment_01.ts?token=signed_token_1
#EXTINF:6.0,
segment_02.ts?token=signed_token_2
#EXT-X-ENDLIST
"""
        rewritten, segments = rewrite_local_playlist(
            source_m3u8_text=source_playlist,
            base_playlist_url="https://qcdn.spayee.in/video/720/index.m3u8",
            segments_dir_name="video_segments",
            key_filename="video_key.bin",
            is_encrypted=True
        )

        # Check key URI rewrite
        self.assertIn('URI="video_key.bin"', rewritten)
        self.assertIn("IV=0x1234567890abcdef1234567890abcdef", rewritten)
        self.assertNotIn("https://qcdn.spayee.in/auth/key", rewritten)

        # Check local relative segment paths
        self.assertIn("video_segments/segment_000001.ts", rewritten)
        self.assertIn("video_segments/segment_000002.ts", rewritten)

        # Check segments list extracted
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0][0], 1)
        self.assertEqual(segments[0][1], "https://qcdn.spayee.in/video/720/segment_01.ts?token=signed_token_1")
        self.assertEqual(segments[0][2], "video_segments/segment_000001.ts")

    # -------------------------------------------------------------------------
    # 9.13 - 9.15 SEGMENT DOWNLOAD WITH RETRY & RESUME TESTS
    # -------------------------------------------------------------------------
    def test_17_segment_download_skips_existing_nonzero_file(self):
        seg_file = self.test_dir / "segment_000001.ts"
        seg_file.write_bytes(b"EXISTING_TS_DATA")

        session_mock = MagicMock()
        download_segment_with_retry(
            session=session_mock,
            segment_url="https://qcdn.spayee.in/seg1.ts",
            target_path=seg_file,
            segment_idx=1
        )
        # Session get should NOT have been called due to resume support
        session_mock.get.assert_not_called()
        self.assertEqual(seg_file.read_bytes(), b"EXISTING_TS_DATA")

    def test_18_segment_download_retries_transient_failure_then_succeeds(self):
        seg_file = self.test_dir / "segment_000002.ts"

        # Mock response with iter_content
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        resp_mock.iter_content.return_value = [b"MOCK_TS_CONTENT"]
        resp_mock.__enter__.return_value = resp_mock

        session_mock = MagicMock()
        # Fail first attempt, succeed second attempt
        session_mock.get.side_effect = [
            RuntimeError("Network blip"),
            resp_mock
        ]

        download_segment_with_retry(
            session=session_mock,
            segment_url="https://qcdn.spayee.in/seg2.ts",
            target_path=seg_file,
            segment_idx=2,
            max_retries=3
        )

        self.assertTrue(seg_file.exists())
        self.assertEqual(seg_file.read_bytes(), b"MOCK_TS_CONTENT")
        self.assertEqual(session_mock.get.call_count, 2)

    def test_19_segment_download_raises_after_max_retries(self):
        seg_file = self.test_dir / "segment_000003.ts"

        session_mock = MagicMock()
        session_mock.get.side_effect = RuntimeError("Persistent connection error")

        with self.assertRaises(RuntimeError) as ctx:
            download_segment_with_retry(
                session=session_mock,
                segment_url="https://qcdn.spayee.in/seg3.ts",
                target_path=seg_file,
                segment_idx=3,
                max_retries=3
            )
        self.assertIn("Spayee segment 3 failed", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 9.27 SPAYEE URL DETECTION TESTS
    # -------------------------------------------------------------------------
    def test_20_is_spayee_url_classification(self):
        valid_spayee_urls = [
            "https://qcdn.spayee.in/courses/math/index.m3u8*648dea11fe203008b24f492175fe674d",
            "https://vcdn.spayee.in/vod/index.m3u8",
            "https://subdomain.spayee.com/video/master.m3u8",
            "https://spayee.in/media/playlist.m3u8",
            "https://d123.cloudfront.net/spayee/lecture1.m3u8"
        ]
        for u in valid_spayee_urls:
            self.assertTrue(is_spayee_url(u), f"Failed to identify: {u}")
            self.assertEqual(MediaRouter.classify_url(u), MediaType.SPAYEE_HLS)

        invalid_urls = [
            "https://d3vlg4qjb80h8n.cloudfront.net/videos/master.m3u8",
            "https://kgs-new-v1.akamaized.net/kv3/lecture/master.m3u8",
            "https://youtube.com/watch?v=12345",
            "https://example.com/notes.pdf"
        ]
        for u in invalid_urls:
            self.assertFalse(is_spayee_url(u), f"Incorrectly matched: {u}")
            self.assertNotEqual(MediaRouter.classify_url(u), MediaType.SPAYEE_HLS)

    # -------------------------------------------------------------------------
    # 9.23 VALIDATION TESTS
    # -------------------------------------------------------------------------
    def test_21_validate_spayee_output(self):
        empty_file = self.test_dir / "empty.mp4"
        empty_file.touch()
        self.assertFalse(validate_spayee_output(empty_file))

        valid_dummy = self.test_dir / "valid.mp4"
        valid_dummy.write_bytes(b"X" * 2048)
        # Without ffprobe output mocking, fallback accepts > 1024 bytes
        with patch("subprocess.run") as mock_sub:
            mock_sub.return_value = MagicMock(returncode=0, stdout="codec_type=video\n")
            self.assertTrue(validate_spayee_output(valid_dummy))

    # -------------------------------------------------------------------------
    # 9.28 & 9.31 END-TO-END PIPELINE TESTS
    # -------------------------------------------------------------------------
    @patch("spayee_downloader.validate_spayee_output", return_value=True)
    @patch("spayee_downloader.process_spayee_ffmpeg")
    @patch("requests.Session.get")
    def test_22_full_download_spayee_hls_pipeline_success(
        self,
        mock_get,
        mock_ffmpeg,
        mock_validate
    ):
        combined_url = "https://qcdn.spayee.in/vod/master.m3u8*648dea11fe203008b24f492175fe674d"
        out_mp4 = self.test_dir / "Lecture_1A.mp4"

        master_content = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720
720/index.m3u8
"""
        video_content = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXT-X-KEY:METHOD=AES-128,URI="https://qcdn.spayee.in/key.bin"
#EXTINF:6.0,
seg1.ts
#EXT-X-ENDLIST
"""

        # Mock HTTP responses
        def mock_get_handler(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.__enter__.return_value = m
            if "master.m3u8" in url:
                m.text = master_content
            elif "720/index.m3u8" in url:
                m.text = video_content
            else:
                m.iter_content.return_value = [b"DUMMY_SEG_DATA"]
            return m

        mock_get.side_effect = mock_get_handler

        # Mock FFmpeg process to create a dummy final.mp4
        def mock_ffmpeg_handler(work_dir, has_audio, ffmpeg_bin):
            final = work_dir / "final.mp4"
            final.write_bytes(b"FINAL_VIDEO_BYTES_12345")
            return final

        mock_ffmpeg.side_effect = mock_ffmpeg_handler

        # Execute
        result_path = download_spayee_hls(
            combined_url=combined_url,
            output_path=out_mp4
        )

        self.assertTrue(out_mp4.exists())
        self.assertEqual(out_mp4.read_bytes(), b"FINAL_VIDEO_BYTES_12345")
        self.assertTrue(str(result_path).endswith("Lecture_1A.mp4"))

    # -------------------------------------------------------------------------
    # 9.30 SECURITY / AUTHORIZATION FAILURE TEST
    # -------------------------------------------------------------------------
    @patch("requests.Session.get")
    def test_23_expired_authorization_returns_clean_error(self, mock_get):
        combined_url = "https://qcdn.spayee.in/vod/master.m3u8*648dea11fe203008b24f492175fe674d"
        out_mp4 = self.test_dir / "Lecture_1A.mp4"

        resp_403 = MagicMock()
        resp_403.status_code = 403
        mock_get.return_value = resp_403

        with self.assertRaises(RuntimeError) as ctx:
            download_spayee_hls(combined_url=combined_url, output_path=out_mp4)

        self.assertIn("Spayee authorization expired or access denied", str(ctx.exception))
        # Ensure secret key is NEVER leaked in error
        self.assertNotIn("648dea11fe203008b24f492175fe674d", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 9.32 TEST CASE FROM SPECIFICATION
    # -------------------------------------------------------------------------
    def test_24_bracket_topic_txt_spayee_test_case(self):
        txt_input = "[Linear Algebra] Lecture 1A - Why Study Linear Algebra : https://qcdn.spayee.in/vod/index.m3u8*648dea11fe203008b24f492175fe674d"
        resources = parse_bracket_topic_raw(txt_input)

        self.assertEqual(len(resources), 1)
        res = resources[0]
        self.assertEqual(res.topic, "Linear Algebra")
        self.assertEqual(res.title, "Lecture 1A - Why Study Linear Algebra")
        self.assertEqual(res.media_type, "SPAYEE_HLS")
        self.assertEqual(res.url, "https://qcdn.spayee.in/vod/index.m3u8")
        self.assertEqual(res.authorized_key, "648dea11fe203008b24f492175fe674d")

        # Check AcademicCourse generation
        course = parse_bracket_topic_txt(txt_input)
        self.assertEqual(course.subject, "Linear Algebra")
        self.assertEqual(len(course.all_items), 1)
        item = course.all_items[0]
        self.assertEqual(item.title, "Lecture 1A - Why Study Linear Algebra")
        self.assertEqual(item.unit_title, "Linear Algebra")


if __name__ == "__main__":
    unittest.main()
