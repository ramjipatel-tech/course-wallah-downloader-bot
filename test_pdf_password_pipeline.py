import os
import io
import sys
import shutil
import tempfile
import unittest
import logging
from unittest.mock import patch

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from utils import (
    parse_pdf_input,
    MediaRouter,
    MediaType,
    is_direct_pdf_url,
    build_failure_card,
    sanitize_error_message,
    PDF_PASSWORD_INVALID,
    PDF_UNLOCK_FAILED,
    PDF_UNLOCK_OUTPUT_INVALID,
    PDF_DOWNLOAD_FAILED,
    PDF_INVALID,
    PDFPasswordInvalid,
    PDFUnlockFailed,
    PDFUnlockOutputInvalid
)
from itsgolu import (
    validate_unlocked_pdf,
    unlock_pdf_file,
    validate_pdf_file,
    apply_pdf_watermark
)
from bracket_topic_parser import parse_bracket_topic_txt
from academic_parser import parse_academic_txt


class TestPdfPasswordPipeline(unittest.TestCase):
    """
    Test suite for password-protected PDF support (PDF_URL*PASSWORD),
    PyMuPDF unlocking, safe watermark sequencing, routing, and independent status cards.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="cw_pdf_test_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_encrypted_pdf(self, filename: str, password: str, num_pages: int = 1) -> str:
        """Helper to create a temporary test encrypted PDF."""
        file_path = os.path.join(self.test_dir, filename)
        doc = fitz.open()
        for i in range(num_pages):
            page = doc.new_page(width=595, height=842)
            page.insert_text((50, 50 + (i * 20)), f"Page {i+1} Confidential Content for Course Wallah")
        doc.save(
            file_path,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            user_pw=password,
            owner_pw=password + "_owner"
        )
        doc.close()
        return file_path

    # 1. Parse PDF URL without password
    def test_parse_pdf_url_without_password(self):
        url = "https://example.com/notes/lecture1.pdf"
        result = parse_pdf_input(url)
        self.assertEqual(result["url"], url)
        self.assertIsNone(result["password"])
        self.assertFalse(result["has_password"])

    # 2. Parse PDF URL with password
    def test_parse_pdf_url_with_password(self):
        combined = "https://d2a5xnk4s7n8a6.cloudfront.net/assets/file.pdf*0f4ad22a-c620-4b44-9903-30937420b12f"
        result = parse_pdf_input(combined)
        self.assertEqual(result["url"], "https://d2a5xnk4s7n8a6.cloudfront.net/assets/file.pdf")
        self.assertEqual(result["password"], "0f4ad22a-c620-4b44-9903-30937420b12f")
        self.assertTrue(result["has_password"])

    # 3. Parse PDF splitting ONLY at the first '*'
    def test_parse_pdf_split_only_first_star(self):
        combined = "https://example.com/file.pdf*pass*word*with*stars*and-123_#special"
        result = parse_pdf_input(combined)
        self.assertEqual(result["url"], "https://example.com/file.pdf")
        self.assertEqual(result["password"], "pass*word*with*stars*and-123_#special")
        self.assertTrue(result["has_password"])

    # 4. Ensure PDF password is never logged or exposed
    def test_pdf_password_not_logged(self):
        secret_pw = "TOP_SECRET_PASSWORD_998877"
        enc_pdf = self._create_encrypted_pdf("secret.pdf", secret_pw)
        unlocked_pdf = os.path.join(self.test_dir, "secret_unlocked.pdf")

        # Capture log records and stdout
        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        logger = logging.getLogger()
        logger.addHandler(handler)
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()

        try:
            success, err, pages = unlock_pdf_file(enc_pdf, unlocked_pdf, secret_pw)
            self.assertTrue(success)

            stdout_val = sys.stdout.getvalue()
            log_val = log_stream.getvalue()

            self.assertNotIn(secret_pw, stdout_val)
            self.assertNotIn(secret_pw, log_val)
            self.assertIn("[PDF] Password-protected PDF detected", stdout_val + log_val)
            self.assertIn("[PDF] Authentication successful", stdout_val + log_val)

            # Check sanitize_error_message
            err_with_secret = f"Error during auth: password={secret_pw}"
            sanitized = sanitize_error_message(err_with_secret)
            self.assertNotIn(secret_pw, sanitized)
        finally:
            sys.stdout = old_stdout
            logger.removeHandler(handler)

    # 5. MediaRouter routes PDF_URL*PASSWORD to DIRECT_PDF
    def test_pdf_password_routes_to_direct_pdf(self):
        url = "https://example.com/notes.pdf*0f4ad22a-c620-4b44-9903-30937420b12f"
        classified = MediaRouter.classify_url(url)
        self.assertEqual(classified, MediaType.DIRECT_PDF)
        self.assertTrue(is_direct_pdf_url(url))

    # 6. KGS PDF URL with password routes to DIRECT_PDF (never KGS_HLS)
    def test_kgs_pdf_password_routes_to_direct_pdf(self):
        kgs_pdf = "https://kgs-v2.akamaized.net/kgs/kgs/pdfs/abc.pdf*0f4ad22a-c620-4b44-9903-30937420b12f"
        classified = MediaRouter.classify_url(kgs_pdf)
        self.assertEqual(classified, MediaType.DIRECT_PDF)
        self.assertNotEqual(classified, MediaType.KGS_HLS)

    # 7. Successful PDF authentication
    def test_pdf_authentication_success(self):
        password = "my_auth_password_456"
        enc_pdf = self._create_encrypted_pdf("doc_enc.pdf", password, num_pages=2)
        unlocked_pdf = os.path.join(self.test_dir, "doc_unlocked.pdf")

        success, err, pages = unlock_pdf_file(enc_pdf, unlocked_pdf, password)
        self.assertTrue(success)
        self.assertEqual(err, "OK")
        self.assertEqual(pages, 2)
        self.assertTrue(os.path.exists(unlocked_pdf))

    # 8. Invalid PDF password handling (fail-fast without hanging)
    def test_pdf_invalid_password(self):
        password = "correct_password"
        enc_pdf = self._create_encrypted_pdf("doc_enc.pdf", password)
        unlocked_pdf = os.path.join(self.test_dir, "doc_unlocked.pdf")

        success, err, pages = unlock_pdf_file(enc_pdf, unlocked_pdf, "wrong_password_attempt")
        self.assertFalse(success)
        self.assertEqual(err, PDF_PASSWORD_INVALID)
        self.assertFalse(os.path.exists(unlocked_pdf))

    # 9. Unlocked PDF does NOT require password
    def test_unlocked_pdf_does_not_require_password(self):
        password = "auth_pass_789"
        enc_pdf = self._create_encrypted_pdf("enc_test.pdf", password, num_pages=3)
        unlocked_pdf = os.path.join(self.test_dir, "unlocked_test.pdf")

        success, err, pages = unlock_pdf_file(enc_pdf, unlocked_pdf, password)
        self.assertTrue(success)

        is_val, val_pages, val_msg = validate_unlocked_pdf(unlocked_pdf)
        self.assertTrue(is_val)
        self.assertEqual(val_pages, 3)

        doc = fitz.open(unlocked_pdf)
        self.assertFalse(bool(doc.needs_pass))
        doc.close()

    # 10. Unlocked PDF preserves exact page count
    def test_unlocked_pdf_preserves_page_count(self):
        password = "preserve_pages_pw"
        enc_pdf = self._create_encrypted_pdf("multi_page.pdf", password, num_pages=5)
        unlocked_pdf = os.path.join(self.test_dir, "multi_page_unlocked.pdf")

        success, err, pages = unlock_pdf_file(enc_pdf, unlocked_pdf, password)
        self.assertTrue(success)
        self.assertEqual(pages, 5)

        doc = fitz.open(unlocked_pdf)
        self.assertEqual(len(doc), 5)
        doc.close()

    # 11. Unlocked PDF flows cleanly to red Course Wallah watermark
    def test_unlocked_pdf_goes_to_red_watermark(self):
        password = "watermark_flow_pw"
        enc_pdf = self._create_encrypted_pdf("for_wm.pdf", password, num_pages=2)
        unlocked_pdf = os.path.join(self.test_dir, "for_wm_unlocked.pdf")
        wm_output = os.path.join(self.test_dir, "for_wm_watermarked.pdf")

        success, err, pages = unlock_pdf_file(enc_pdf, unlocked_pdf, password)
        self.assertTrue(success)

        wm_res = apply_pdf_watermark(unlocked_pdf, wm_output, watermark_text="Course Wallah")
        self.assertIsNotNone(wm_res)
        self.assertTrue(os.path.exists(wm_output))

        is_val, wm_pages, val_err = validate_pdf_file(wm_output)
        self.assertTrue(is_val)
        self.assertEqual(wm_pages, 2)

    # 12. PDF status is independent from video status
    def test_pdf_status_independent_from_video(self):
        # Case A: Video succeeded, PDF password failed
        card_a = build_failure_card(
            resource_type="LECTURE",
            lecture_title="Linear Algebra 01",
            course_name="GATE Math",
            video_status="SUCCESS",
            pdf_status="FAILED",
            technical_error="PDF_PASSWORD_INVALID"
        )
        self.assertIn("🎬 <b>Video:</b> ✅ Success", card_a)
        self.assertIn("📄 <b>PDF:</b> ❌ Invalid PDF password", card_a)

        # Case B: Video not available, PDF succeeded
        card_b = build_failure_card(
            resource_type="LECTURE",
            lecture_title="Annotated Notes",
            course_name="GATE Math",
            video_status="NOT_AVAILABLE",
            pdf_status="SUCCESS"
        )
        self.assertIn("🎬 <b>Video:</b> ⚪ Not Available", card_b)
        self.assertIn("📄 <b>PDF:</b> ✅ Success", card_b)

    # 13. Direct single link handling
    def test_single_password_pdf_link(self):
        single_input = "https://cdn.example.com/course/notes.pdf*0f4ad22a-c620-4b44-9903-30937420b12f"
        parsed = parse_pdf_input(single_input)
        self.assertEqual(parsed["url"], "https://cdn.example.com/course/notes.pdf")
        self.assertEqual(parsed["password"], "0f4ad22a-c620-4b44-9903-30937420b12f")
        self.assertTrue(parsed["has_password"])

        media_type = MediaRouter.classify_url(single_input)
        self.assertEqual(media_type, MediaType.DIRECT_PDF)

        clean_title = MediaRouter.extract_clean_title(parsed["url"])
        self.assertEqual(clean_title, "notes")

    # 14. Academic batch TXT password PDF link parsing
    def test_txt_password_pdf_link(self):
        txt_content = (
            "[Linear Algebra] Annotated Notes : "
            "https://d2a5xnk4s7n8a6.cloudfront.net/assets/file.pdf*0f4ad22a-c620-4b44-9903-30937420b12f\n"
        )
        course = parse_bracket_topic_txt(txt_content, filename="01_Linear_Algebra(Math).txt")
        self.assertEqual(len(course.all_items), 1)

        item = course.all_items[0]
        self.assertEqual(item.topic_title, "Linear Algebra")
        self.assertEqual(item.title, "Annotated Notes")
        self.assertEqual(item.category, "pdf")
        self.assertEqual(item.url, "https://d2a5xnk4s7n8a6.cloudfront.net/assets/file.pdf*0f4ad22a-c620-4b44-9903-30937420b12f")


if __name__ == "__main__":
    unittest.main()
