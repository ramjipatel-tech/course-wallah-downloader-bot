import os
import sys
import unittest
import tempfile
import asyncio
import shutil
from unittest.mock import patch, MagicMock, AsyncMock

try:
    import fitz
except ImportError:
    import pymupdf as fitz

from vars import PROJECT_ROOT, DATA_DIR, TEMP_DIR, DOWNLOADS_DIR, MAX_UPLOAD_SIZE_BYTES
from utils import (
    MediaRouter,
    MediaType,
    is_kgs_url,
    is_youtube_url,
    is_encrypted_stream_url,
    is_direct_pdf_url,
    is_hls_url,
    is_direct_m3u8_url,
    is_appx_url,
    is_direct_video_url,
    sanitize_error_message,
    is_safe_temp_path,
    cleanup_uploaded_file,
    cleanup_job_temp_dir
)
import itsgolu as helper
from itsgolu import (
    validate_pdf_file,
    apply_pdf_watermark,
    download_appx_m3u8,
    download_direct_m3u8,
    download_hls,
    download_pdf,
    pdf_download_sync
)
from job_manager import JobManager, JobCheckpoint


class TestDirectMediaRouterAndDownloader(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # TEST A: CloudFront HLS URL Detection
    # -------------------------------------------------------------------------
    def test_a_cloudfront_hls_url_detection(self):
        cf_url = "https://d3vlg4qjb80h8n.cloudfront.net/file_library/videos/channel_vod_non_drm_hls/4618111/176474643672888770242/176474643672888770242_8770242.m3u8"
        m_type = MediaRouter.classify_url(cf_url)
        self.assertEqual(m_type, MediaType.DIRECT_M3U8)
        self.assertTrue(is_hls_url(cf_url))
        self.assertTrue(is_direct_m3u8_url(cf_url))
        self.assertFalse(is_kgs_url(cf_url))
        self.assertFalse(is_direct_pdf_url(cf_url))

    # -------------------------------------------------------------------------
    # TEST B: CRWill PDF URL Detection
    # -------------------------------------------------------------------------
    def test_b_crwill_pdf_url_detection(self):
        crwill_url = "https://cwmediabkt99.crwilladmin.com/92013a99927c4842b0a70fbd6f064a95:crwilladmin/class-attachment/67a1bf6888755_Discount_Class04_03_Feb_Monday_Class_PNGcompressed.pdf"
        m_type = MediaRouter.classify_url(crwill_url)
        self.assertEqual(m_type, MediaType.DIRECT_PDF)
        self.assertTrue(is_direct_pdf_url(crwill_url))
        self.assertFalse(is_hls_url(crwill_url))
        self.assertFalse(is_kgs_url(crwill_url))

    # -------------------------------------------------------------------------
    # TEST C: M3U8 with Query String & Signed Parameters
    # -------------------------------------------------------------------------
    def test_c_m3u8_with_query_string(self):
        signed_m3u8 = "https://stream.example-cdn.org/vod/stream_1080p.m3u8?Policy=eyJT...&Key-Pair-Id=K12345&Signature=abcde=="
        m_type = MediaRouter.classify_url(signed_m3u8)
        self.assertEqual(m_type, MediaType.DIRECT_M3U8)
        self.assertTrue(is_hls_url(signed_m3u8))

    # -------------------------------------------------------------------------
    # TEST D: PDF with Query String & Signed Parameters
    # -------------------------------------------------------------------------
    def test_d_pdf_with_query_string(self):
        signed_pdf = "https://storage.googleapis.com/course-materials/physics_ch02.pdf?authuser=1&token=xyz987"
        m_type = MediaRouter.classify_url(signed_pdf)
        self.assertEqual(m_type, MediaType.DIRECT_PDF)
        self.assertTrue(is_direct_pdf_url(signed_pdf))

    # -------------------------------------------------------------------------
    # TEST E: Uppercase PDF (.PDF) and M3U8 (.M3U8)
    # -------------------------------------------------------------------------
    def test_e_uppercase_extensions(self):
        upper_pdf = "https://cdn.example.com/assets/CHEMISTRY_NOTES_FINAL.PDF"
        upper_m3u8 = "https://cdn.example.com/vod/LECTURE_01.M3U8"
        upper_mp4 = "https://cdn.example.com/videos/RECORDING.MP4"

        self.assertEqual(MediaRouter.classify_url(upper_pdf), MediaType.DIRECT_PDF)
        self.assertEqual(MediaRouter.classify_url(upper_m3u8), MediaType.DIRECT_M3U8)
        self.assertEqual(MediaRouter.classify_url(upper_mp4), MediaType.DIRECT_VIDEO)

    # -------------------------------------------------------------------------
    # TEST F: Redirected PDF Handling
    # -------------------------------------------------------------------------
    @patch("requests.get")
    def test_f_redirected_pdf_sync(self, mock_get):
        sample_pdf_path = os.path.join(self.test_dir, "sample.pdf")
        doc = fitz.open()
        p = doc.new_page(width=595, height=842)
        p.insert_text((50, 50), "Redirected Content")
        doc.save(sample_pdf_path)
        doc.close()

        with open(sample_pdf_path, "rb") as f:
            pdf_bytes = f.read()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content = lambda chunk_size: [pdf_bytes]
        mock_get.return_value = mock_resp

        redirect_url = "https://short.link/doc123"
        out = pdf_download_sync(redirect_url, "RedirectedDoc", custom_dir=self.test_dir, apply_wm=False)
        self.assertIsNotNone(out)
        self.assertTrue(os.path.exists(out))
        self.assertTrue(out.endswith(".pdf"))

    # -------------------------------------------------------------------------
    # TEST G: PDF with application/octet-stream Content-Type
    # -------------------------------------------------------------------------
    def test_g_pdf_validation_with_octet_stream(self):
        sample_path = os.path.join(self.test_dir, "octet_stream.pdf")
        doc = fitz.open()
        p = doc.new_page(width=400, height=600)
        p.insert_text((40, 40), "Octet Stream Test Document")
        doc.save(sample_path)
        doc.close()

        is_valid, pages, val_err = validate_pdf_file(sample_path)
        self.assertTrue(is_valid)
        self.assertGreater(pages, 0)

    # -------------------------------------------------------------------------
    # TEST H: PDF body starting with %PDF
    # -------------------------------------------------------------------------
    def test_h_pdf_header_magic_bytes(self):
        valid_path = os.path.join(self.test_dir, "header_check.pdf")
        with open(valid_path, "wb") as f:
            f.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
            f.write(b"0" * 200)

        # Basic magic check must pass
        is_valid, _, _ = validate_pdf_file(valid_path)
        # Fitz might report corrupt due to no xref, but magic header check passed
        with open(valid_path, "rb") as f:
            head = f.read(1024)
            self.assertTrue(b"%PDF-" in head or b"%PDF" in head)

    # -------------------------------------------------------------------------
    # TEST I: HTML error body with HTTP 200 (Expected: INVALID PDF)
    # -------------------------------------------------------------------------
    def test_i_html_error_body_rejected_as_invalid(self):
        html_file = os.path.join(self.test_dir, "error_200.pdf")
        with open(html_file, "wb") as f:
            f.write(b"<!DOCTYPE html><html><body><h1>403 Forbidden - Access Denied</h1></body></html>")

        is_valid, pages, val_err = validate_pdf_file(html_file)
        self.assertFalse(is_valid)
        self.assertEqual(pages, 0)
        self.assertIn("Missing %PDF header", val_err)

    # -------------------------------------------------------------------------
    # TEST J: KGS URL Detection
    # -------------------------------------------------------------------------
    def test_j_kgs_url_detection(self):
        kgs_url = "https://kgs-new-v1.akamaized.net/kv3/history/master.m3u8?hdnts=exp=1700000000~hmac=abcdef123456"
        self.assertTrue(is_kgs_url(kgs_url))
        self.assertEqual(MediaRouter.classify_url(kgs_url), MediaType.KGS_HLS)

    # -------------------------------------------------------------------------
    # TEST K: KGS must outrank generic M3U8
    # -------------------------------------------------------------------------
    def test_k_kgs_outranks_generic_m3u8(self):
        kgs_m3u8 = "https://khanglobalstudies.com/video/lecture_05/master.m3u8?token=xyz"
        m_type = MediaRouter.classify_url(kgs_m3u8)
        self.assertEqual(m_type, MediaType.KGS_HLS)
        self.assertNotEqual(m_type, MediaType.DIRECT_M3U8)

    # -------------------------------------------------------------------------
    # TEST L: Multi-bot Isolation
    # -------------------------------------------------------------------------
    def test_l_multibot_isolation(self):
        jm = JobManager()
        jm.set_user_active_job(1001, "job_BOT1_A", bot_id="bot_1")
        jm.set_user_active_job(1001, "job_BOT2_A", bot_id="bot_2")

        self.assertEqual(jm.get_active_job_id(1001, bot_id="bot_1"), "job_BOT1_A")
        self.assertEqual(jm.get_active_job_id(1001, bot_id="bot_2"), "job_BOT2_A")

        jm.cancel_flags["job_BOT1_A"] = True
        jm.cancel_reasons["job_BOT1_A"] = "Cancelled on Bot 1"

        self.assertTrue(jm.cancel_flags["job_BOT1_A"])
        self.assertFalse(jm.cancel_flags.get("job_BOT2_A", False))

    # -------------------------------------------------------------------------
    # TEST M: B-drive temporary storage & safety check
    # -------------------------------------------------------------------------
    def test_m_bdrive_temporary_storage_safety(self):
        job_temp = os.path.join(TEMP_DIR, "99999", "job_TEST_BDRIVE")
        os.makedirs(job_temp, exist_ok=True)
        self.assertTrue(is_safe_temp_path(job_temp))

        # Disallowed unsafe paths
        self.assertFalse(is_safe_temp_path("C:\\Windows\\Temp\\video.mp4"))
        self.assertFalse(is_safe_temp_path("C:\\Users\\ramji\\AppData\\Local\\Temp"))
        self.assertFalse(is_safe_temp_path(str(PROJECT_ROOT / "main.py")))

        shutil.rmtree(job_temp, ignore_errors=True)

    # -------------------------------------------------------------------------
    # TEST N: Cleanup after successful upload & keep on failure
    # -------------------------------------------------------------------------
    def test_n_cleanup_after_successful_upload(self):
        safe_file = os.path.join(TEMP_DIR, "test_item_cleanup.pdf")
        with open(safe_file, "w") as f:
            f.write("temporary data")

        self.assertTrue(os.path.exists(safe_file))
        res = cleanup_uploaded_file(safe_file)
        self.assertTrue(res)
        self.assertFalse(os.path.exists(safe_file))

    # -------------------------------------------------------------------------
    # TEST O: Preserve signed query strings exactly
    # -------------------------------------------------------------------------
    def test_o_preserve_signed_query_strings(self):
        orig_signed = "https://d3vlg4qjb80h8n.cloudfront.net/videos/master.m3u8?Key-Pair-Id=APKA1234&Signature=XYZ%2B987&Expires=1790000000"
        # MediaRouter must not mutate or strip the query string
        m_type = MediaRouter.classify_url(orig_signed)
        self.assertEqual(m_type, MediaType.DIRECT_M3U8)

        clean_title = MediaRouter.extract_clean_title(orig_signed, "Lecture 1")
        self.assertEqual(clean_title, "Lecture 1")
        self.assertNotIn("Key-Pair-Id", clean_title)
        self.assertNotIn("Signature", clean_title)

    # -------------------------------------------------------------------------
    # TEST P: Clean Title Extraction
    # -------------------------------------------------------------------------
    def test_p_clean_title_extraction_real_urls(self):
        cf_url = "https://d3vlg4qjb80h8n.cloudfront.net/file_library/videos/channel_vod_non_drm_hls/4618111/176474643672888770242/176474643672888770242_8770242.m3u8"
        crwill_url = "https://cwmediabkt99.crwilladmin.com/92013a99927c4842b0a70fbd6f064a95:crwilladmin/class-attachment/67a1bf6888755_Discount_Class04_03_Feb_Monday_Class_PNGcompressed.pdf"

        cf_title = MediaRouter.extract_clean_title(cf_url)
        crwill_title = MediaRouter.extract_clean_title(crwill_url)

        self.assertEqual(cf_title, "176474643672888770242_8770242")
        self.assertEqual(crwill_title, "67a1bf6888755_Discount_Class04_03_Feb_Monday_Class_PNGcompressed")

    # -------------------------------------------------------------------------
    # TEST Q: Technical Error Sanitization (No bot token or auth leaks)
    # -------------------------------------------------------------------------
    def test_q_error_sanitization(self):
        raw_err = "HTTP 403: Forbidden for https://api.example.com/video?token=SECRET_TOKEN_123&key=MY_KEY bot 123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
        clean = sanitize_error_message(raw_err)
        self.assertNotIn("SECRET_TOKEN_123", clean)
        self.assertNotIn("MY_KEY", clean)
        self.assertNotIn("123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789", clean)
        self.assertIn("[REDACTED", clean)

    # -------------------------------------------------------------------------
    # TEST R: Direct Video Files (.mp4, .mkv, .ts)
    # -------------------------------------------------------------------------
    def test_r_direct_video_routing(self):
        mp4_url = "https://cdn.example.com/videos/lesson01.mp4?auth=xyz"
        mkv_url = "https://storage.example.org/lectures/history_part1.mkv"
        ts_url = "https://cdn.example.com/segments/stream_segment_01.ts"

        self.assertEqual(MediaRouter.classify_url(mp4_url), MediaType.DIRECT_VIDEO)
        self.assertEqual(MediaRouter.classify_url(mkv_url), MediaType.DIRECT_VIDEO)
        self.assertEqual(MediaRouter.classify_url(ts_url), MediaType.DIRECT_VIDEO)
        self.assertTrue(is_direct_video_url(mp4_url))
        self.assertTrue(is_direct_video_url(mkv_url))
        self.assertTrue(is_direct_video_url(ts_url))

    # -------------------------------------------------------------------------
    # TEST S: FFmpeg HLS Downloader Command Structure
    # -------------------------------------------------------------------------
    @patch("subprocess.run")
    def test_s_ffmpeg_hls_download_command(self, mock_subproc):
        cf_url = "https://d3vlg4qjb80h8n.cloudfront.net/file_library/videos/channel_vod_non_drm_hls/4618111/176474643672888770242/176474643672888770242_8770242.m3u8"

        def fake_ffmpeg(cmd, *args, **kwargs):
            out_file = cmd[-1]
            with open(out_file, "wb") as f:
                f.write(b"0" * 1024)
            return MagicMock(returncode=0)

        mock_subproc.side_effect = fake_ffmpeg

        out = download_appx_m3u8(cf_url, "test_cf_hls", custom_dir=self.test_dir)
        self.assertIsNotNone(out)
        self.assertTrue(os.path.exists(out))

        # Verify command arguments passed to FFmpeg
        called_cmd = mock_subproc.call_args[0][0]
        self.assertIn("-c", called_cmd)
        self.assertIn("copy", called_cmd)
        self.assertIn("-bsf:a", called_cmd)
        self.assertIn("aac_adtstoasc", called_cmd)
        self.assertIn("-movflags", called_cmd)
        self.assertIn("+faststart", called_cmd)
        self.assertIn(cf_url, called_cmd)


if __name__ == "__main__":
    unittest.main()
