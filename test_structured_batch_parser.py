import os
import sys
import unittest
import tempfile
import shutil

# Ensure root directory is in sys.path
sys.path.insert(0, os.path.abspath("."))

from vars import TEMP_DIR, PROJECT_ROOT
from utils import MediaRouter, MediaType, is_safe_temp_path
from job_manager import JobManager
from academic_parser import (
    parse_academic_txt,
    parse_course_txt,
    detect_txt_format,
    AcademicCourse,
    AcademicUnit,
    AcademicItem
)
from structured_batch_parser import (
    parse_structured_batch_raw,
    parse_structured_batch_txt,
    StructuredBatch,
    StructuredContentItem,
    sanitize_folder_name,
    extract_hierarchy_and_title
)

SAMPLE_CAREERWILL_TXT = """=======================================================
🎯 Careerwill App
=======================================================

───── BATCH DETAILS ──────

🌟 Batch      : Maths (Foundation Batch)
🪪 ID         : 2558
👨🏫 Instructor : Rakesh Yadav Sir
📸 Thumbnail  : https://cwmediabkt99.crwilladmin.com/thumbnails/maths_2558.jpg
💰 Price       : ₹1999
📅 Start Date  : 01-Jan-2024
📅 End Date    : 31-Dec-2024
🕒 Generated On: 02-Oct-2026 15:30:00

─────TOPIC SUMMARY─────

📁 Practice Class||SSC GD Practice : 13 videos
📁 Practice Class||UP SI Practice : 20 videos
📁 Practice Class||Railway Practice : 12 videos

📁 Rakesh Sir (Revision Class)||Percentage : 9 videos
📁 Rakesh Sir (Revision Class)||Compound Interest : 3 videos
📁 Rakesh Sir (Revision Class)||Simple Interest : 4 videos

📁 Calculation Capsule|| : 5 videos
📁 Basic Concept Of Maths|| : 8 videos

─────LINK SUMMARY─────

🔗 Total Number of Links : 827
┠🎬 Total Videos         : 214
┃  ┠🎦 .m3u8             : 63
┃  ┠🎦 .mpd              : 149
┃  ┠🎦 Youtube           : 2
┠📄 Total PDFs           : 613

CONTENT:

[Practice Class] (SSC GD Practice) Class - 01 | Maths Practice | Percentage: https://d3vlg4qjb80h8n.cloudfront.net/videos/ssc_gd_01.m3u8?Key-Pair-Id=APKA1234&Signature=XYZ%2B987&Expires=1790000000

[Practice Class] (UP SI Practice) Class - 01 | Maths Practice | SI: https://d3vlg4qjb80h8n.cloudfront.net/videos/upsi_01.mpd*12345:token:authorized_token_xyz

[Rakesh Sir (Revision Class)] (Percentage) Class-01 | Percentage Revision (Class Notes): https://cwmediabkt99.crwilladmin.com/class-attachment/percentage_notes_01.pdf?auth=123

[Calculation Capsule] Class 01 | Fast Multiplication Tricks: https://www.youtube.com/watch?v=dQw4w9WgXcQ

[Basic Concept Of Maths] Class 01 | Number System Basics: https://d3vlg4qjb80h8n.cloudfront.net/vod/basics_01.mp4
"""

SAMPLE_LEGACY_APPX_TXT = """[Subject] Mathematics
[Course] Discrete Structures
[Home] Discrete Structure Syllabus : https://example.com/syllabus.pdf

Unit-1.0 Logic and Proof Techniques ▶

35. DS : https://example.com/video35.m3u8
36. DS : https://example.com/video36.m3u8

Unit-2.0 Set Theory and Relation ✔

DS U-2 Notes by TC : https://example.com/notes2.pdf
1. DS : https://example.com/video1.m3u8
"""


class TestStructuredBatchParserAndDualWorkflows(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # TEST 01: Detect Structured Batch TXT
    # -------------------------------------------------------------------------
    def test_01_detect_structured_batch_txt(self):
        fmt = detect_txt_format(SAMPLE_CAREERWILL_TXT)
        self.assertEqual(fmt, "structured_batch")

    # -------------------------------------------------------------------------
    # TEST 02: Detect Legacy APPX TXT
    # -------------------------------------------------------------------------
    def test_02_detect_legacy_appx_txt(self):
        fmt = detect_txt_format(SAMPLE_LEGACY_APPX_TXT)
        self.assertEqual(fmt, "legacy_appx")

    # -------------------------------------------------------------------------
    # TEST 03: Ensure Both Parsers Remain Independent
    # -------------------------------------------------------------------------
    def test_03_parsers_remain_independent(self):
        # Legacy parser handles legacy txt
        legacy_course = parse_academic_txt(SAMPLE_LEGACY_APPX_TXT, "math.txt")
        self.assertEqual(legacy_course.course, "Discrete Structures")
        self.assertEqual(len(legacy_course.units), 3)

        # Structured parser handles structured batch txt
        structured_course = parse_structured_batch_txt(SAMPLE_CAREERWILL_TXT, "careerwill.txt")
        self.assertEqual(structured_course.course, "Maths (Foundation Batch)")
        self.assertEqual(structured_course.format_type, "structured_batch")

        # parse_course_txt auto-routes correctly
        routed_a = parse_course_txt(SAMPLE_LEGACY_APPX_TXT, "legacy.txt")
        routed_b = parse_course_txt(SAMPLE_CAREERWILL_TXT, "structured.txt")
        self.assertEqual(routed_a.course, "Discrete Structures")
        self.assertEqual(routed_b.course, "Maths (Foundation Batch)")

    # -------------------------------------------------------------------------
    # TEST 04: Parse Batch Name
    # -------------------------------------------------------------------------
    def test_04_parse_batch_name(self):
        raw_batch = parse_structured_batch_raw(SAMPLE_CAREERWILL_TXT)
        self.assertEqual(raw_batch.batch_name, "Maths (Foundation Batch)")

    # -------------------------------------------------------------------------
    # TEST 05: Parse Batch ID
    # -------------------------------------------------------------------------
    def test_05_parse_batch_id(self):
        raw_batch = parse_structured_batch_raw(SAMPLE_CAREERWILL_TXT)
        self.assertEqual(raw_batch.batch_id, "2558")

    # -------------------------------------------------------------------------
    # TEST 06: Parse Instructor
    # -------------------------------------------------------------------------
    def test_06_parse_instructor(self):
        raw_batch = parse_structured_batch_raw(SAMPLE_CAREERWILL_TXT)
        self.assertEqual(raw_batch.instructor, "Rakesh Yadav Sir")

    # -------------------------------------------------------------------------
    # TEST 07: Parse Thumbnail
    # -------------------------------------------------------------------------
    def test_07_parse_thumbnail(self):
        raw_batch = parse_structured_batch_raw(SAMPLE_CAREERWILL_TXT)
        self.assertEqual(raw_batch.thumbnail, "https://cwmediabkt99.crwilladmin.com/thumbnails/maths_2558.jpg")

    # -------------------------------------------------------------------------
    # TEST 08: Parse Price
    # -------------------------------------------------------------------------
    def test_08_parse_price(self):
        raw_batch = parse_structured_batch_raw(SAMPLE_CAREERWILL_TXT)
        self.assertEqual(raw_batch.price, "₹1999")

    # -------------------------------------------------------------------------
    # TEST 09: Parse Dates
    # -------------------------------------------------------------------------
    def test_09_parse_dates(self):
        raw_batch = parse_structured_batch_raw(SAMPLE_CAREERWILL_TXT)
        self.assertEqual(raw_batch.start_date, "01-Jan-2024")
        self.assertEqual(raw_batch.end_date, "31-Dec-2024")
        self.assertEqual(raw_batch.generated_on, "02-Oct-2026 15:30:00")

    # -------------------------------------------------------------------------
    # TEST 10: Parse Topic Summary
    # -------------------------------------------------------------------------
    def test_10_parse_topic_summary(self):
        raw_batch = parse_structured_batch_raw(SAMPLE_CAREERWILL_TXT)
        self.assertGreaterEqual(len(raw_batch.topic_summary), 8)
        first_topic = raw_batch.topic_summary[0]
        self.assertEqual(first_topic["parent"], "Practice Class")
        self.assertEqual(first_topic["topic"], "SSC GD Practice")
        self.assertEqual(first_topic["count"], 13)

    # -------------------------------------------------------------------------
    # TEST 11: Parse A||B Hierarchy
    # -------------------------------------------------------------------------
    def test_11_parse_a_double_pipe_b_hierarchy(self):
        raw_batch = parse_structured_batch_raw(SAMPLE_CAREERWILL_TXT)
        rev_topics = [t for t in raw_batch.topic_summary if t["parent"] == "Rakesh Sir (Revision Class)"]
        self.assertEqual(len(rev_topics), 3)
        self.assertEqual(rev_topics[0]["topic"], "Percentage")
        self.assertEqual(rev_topics[0]["count"], 9)
        self.assertEqual(rev_topics[1]["topic"], "Compound Interest")
        self.assertEqual(rev_topics[2]["topic"], "Simple Interest")

    # -------------------------------------------------------------------------
    # TEST 12: Handle A|| Empty Child (No Fake Child Folder)
    # -------------------------------------------------------------------------
    def test_12_handle_empty_child_topic(self):
        raw_batch = parse_structured_batch_raw(SAMPLE_CAREERWILL_TXT)
        calc_topic = next((t for t in raw_batch.topic_summary if t["parent"] == "Calculation Capsule"), None)
        self.assertIsNotNone(calc_topic)
        self.assertEqual(calc_topic["topic"], "")
        self.assertEqual(calc_topic["count"], 5)

    # -------------------------------------------------------------------------
    # TEST 13: Parse CONTENT Records
    # -------------------------------------------------------------------------
    def test_13_parse_content_records(self):
        course = parse_structured_batch_txt(SAMPLE_CAREERWILL_TXT)
        self.assertEqual(len(course.all_items), 5)
        item0 = course.all_items[0]
        self.assertEqual(item0.title, "Class - 01 | Maths Practice | Percentage")
        self.assertEqual(item0.parent_folder, "Practice Class")
        self.assertEqual(item0.subfolder, "SSC GD Practice")

    # -------------------------------------------------------------------------
    # TEST 14: Extract M3U8
    # -------------------------------------------------------------------------
    def test_14_extract_m3u8(self):
        course = parse_structured_batch_txt(SAMPLE_CAREERWILL_TXT)
        item_m3u8 = course.all_items[0]
        self.assertEqual(item_m3u8.category, "video")
        self.assertIn(".m3u8", item_m3u8.url)
        self.assertEqual(MediaRouter.classify_url(item_m3u8.url), MediaType.DIRECT_M3U8)

    # -------------------------------------------------------------------------
    # TEST 15: Extract MPD
    # -------------------------------------------------------------------------
    def test_15_extract_mpd(self):
        course = parse_structured_batch_txt(SAMPLE_CAREERWILL_TXT)
        item_mpd = course.all_items[1]
        self.assertEqual(item_mpd.category, "video")
        self.assertIn(".mpd*", item_mpd.url)
        self.assertEqual(MediaRouter.classify_url(item_mpd.url), MediaType.ENCRYPTED_STREAM)

    # -------------------------------------------------------------------------
    # TEST 16: Extract YouTube
    # -------------------------------------------------------------------------
    def test_16_extract_youtube(self):
        course = parse_structured_batch_txt(SAMPLE_CAREERWILL_TXT)
        item_yt = course.all_items[3]
        self.assertEqual(item_yt.category, "video")
        self.assertIn("youtube.com", item_yt.url)
        self.assertEqual(MediaRouter.classify_url(item_yt.url), MediaType.YOUTUBE)

    # -------------------------------------------------------------------------
    # TEST 17: Extract PDF
    # -------------------------------------------------------------------------
    def test_17_extract_pdf(self):
        course = parse_structured_batch_txt(SAMPLE_CAREERWILL_TXT)
        item_pdf = course.all_items[2]
        self.assertEqual(item_pdf.category, "pdf")
        self.assertIn(".pdf", item_pdf.url)
        self.assertEqual(MediaRouter.classify_url(item_pdf.url), MediaType.DIRECT_PDF)

    # -------------------------------------------------------------------------
    # TEST 18: Preserve Signed URLs
    # -------------------------------------------------------------------------
    def test_18_preserve_signed_urls(self):
        course = parse_structured_batch_txt(SAMPLE_CAREERWILL_TXT)
        item_signed = course.all_items[0]
        self.assertIn("Key-Pair-Id=APKA1234", item_signed.url)
        self.assertIn("Signature=XYZ%2B987", item_signed.url)
        self.assertIn("Expires=1790000000", item_signed.url)

    # -------------------------------------------------------------------------
    # TEST 19: Preserve MPD Authorization Suffix
    # -------------------------------------------------------------------------
    def test_19_preserve_mpd_suffix(self):
        course = parse_structured_batch_txt(SAMPLE_CAREERWILL_TXT)
        item_mpd = course.all_items[1]
        self.assertEqual(item_mpd.url, "https://d3vlg4qjb80h8n.cloudfront.net/videos/upsi_01.mpd*12345:token:authorized_token_xyz")

    # -------------------------------------------------------------------------
    # TEST 20: Preserve Content Order
    # -------------------------------------------------------------------------
    def test_20_preserve_content_order(self):
        course = parse_structured_batch_txt(SAMPLE_CAREERWILL_TXT)
        indices = [itm.index for itm in course.all_items]
        self.assertEqual(indices, [1, 2, 3, 4, 5])
        titles = [itm.title for itm in course.all_items]
        self.assertIn("Maths Practice", titles[0])
        self.assertEqual(course.all_items[0].subfolder, "SSC GD Practice")
        self.assertIn("Fast Multiplication Tricks", titles[3])
        self.assertIn("Number System Basics", titles[4])

    # -------------------------------------------------------------------------
    # TEST 21: Folder-Name Sanitization
    # -------------------------------------------------------------------------
    def test_21_folder_name_sanitization(self):
        # Unsafe filesystem characters stripped
        unsafe_name = "Rakesh Sir (Revision Class): Chapter 1 <Special>? *|"
        sanitized = sanitize_folder_name(unsafe_name)
        self.assertEqual(sanitized, "Rakesh Sir (Revision Class) Chapter 1 Special")
        for bad_char in '<>:"/\\|?*':
            self.assertNotIn(bad_char, sanitized)

        # Path traversal prevented
        traversal = "../../etc/passwd"
        self.assertEqual(sanitize_folder_name(traversal), "etc passwd")

        # Hindi / Unicode characters preserved
        hindi_name = "गणित (फाउंडेशन बैच) - भाग 01"
        self.assertEqual(sanitize_folder_name(hindi_name), "गणित (फाउंडेशन बैच) - भाग 01")

    # -------------------------------------------------------------------------
    # TEST 22: Duplicate Prevention
    # -------------------------------------------------------------------------
    def test_22_duplicate_prevention(self):
        txt_with_dups = SAMPLE_CAREERWILL_TXT + "\n[Calculation Capsule] Class 01 | Fast Multiplication Tricks: https://www.youtube.com/watch?v=dQw4w9WgXcQ\n"
        course = parse_structured_batch_txt(txt_with_dups)
        # The duplicate line must not be added twice
        self.assertEqual(len(course.all_items), 5)

    # -------------------------------------------------------------------------
    # TEST 23: Missing TOPIC SUMMARY Fallback
    # -------------------------------------------------------------------------
    def test_23_missing_topic_summary_fallback(self):
        txt_no_summary = """───── BATCH DETAILS ──────
🌟 Batch : Reasoning Special
🪪 ID    : 1001

CONTENT:
[Logical Reasoning] (Syllogism) Class - 01 | Syllogism Basics: https://example.com/syl1.mp4
[Logical Reasoning] (Syllogism) Class - 02 | Syllogism Practice: https://example.com/syl2.mp4
[Verbal Reasoning] Class - 01 | Analogy: https://example.com/ana1.mp4
"""
        course = parse_structured_batch_txt(txt_no_summary)
        self.assertEqual(len(course.all_items), 3)
        self.assertEqual(course.all_items[0].parent_folder, "Logical Reasoning")
        self.assertEqual(course.all_items[0].subfolder, "Syllogism")
        self.assertEqual(course.all_items[2].parent_folder, "Verbal Reasoning")
        self.assertEqual(course.all_items[2].subfolder, "")

    # -------------------------------------------------------------------------
    # TEST 24: Telegram Forum Topic Mapping
    # -------------------------------------------------------------------------
    def test_24_telegram_topic_mapping(self):
        course = parse_structured_batch_txt(SAMPLE_CAREERWILL_TXT)
        # Verify units created represent distinct parent-subfolder combinations
        unit_headers = [u.display_header for u in course.units]
        self.assertIn("📌 PRACTICE CLASS — SSC GD PRACTICE", unit_headers)
        self.assertIn("📌 PRACTICE CLASS — UP SI PRACTICE", unit_headers)
        self.assertIn("📌 RAKESH SIR (REVISION CLASS) — PERCENTAGE", unit_headers)
        self.assertIn("📌 CALCULATION CAPSULE", unit_headers)
        self.assertIn("📌 BASIC CONCEPT OF MATHS", unit_headers)

    # -------------------------------------------------------------------------
    # TEST 25: Multi-Bot Isolation
    # -------------------------------------------------------------------------
    def test_25_multibot_isolation(self):
        jm = JobManager()
        # Parse courses in isolated bot contexts
        course_bot1 = parse_course_txt(SAMPLE_CAREERWILL_TXT, "bot1.txt")
        course_bot2 = parse_course_txt(SAMPLE_LEGACY_APPX_TXT, "bot2.txt")

        jm.set_user_active_job(101, "job_CW_1", bot_id="bot_1")
        jm.set_user_active_job(101, "job_APPX_2", bot_id="bot_2")

        self.assertEqual(jm.get_active_job_id(101, bot_id="bot_1"), "job_CW_1")
        self.assertEqual(jm.get_active_job_id(101, bot_id="bot_2"), "job_APPX_2")

        # Bot 1 has structured batch, Bot 2 has legacy appx
        self.assertEqual(course_bot1.format_type, "structured_batch")
        self.assertEqual(course_bot2.format_type, "legacy_appx")

    # -------------------------------------------------------------------------
    # TEST 26: B-Drive Temporary Storage Safety
    # -------------------------------------------------------------------------
    def test_26_bdrive_storage_safety(self):
        job_temp = os.path.join(TEMP_DIR, "user_12345", "job_CW_BATCH")
        os.makedirs(job_temp, exist_ok=True)
        self.assertTrue(is_safe_temp_path(job_temp))
        shutil.rmtree(job_temp, ignore_errors=True)

    # -------------------------------------------------------------------------
    # TEST 27: Existing APPX Regression Test
    # -------------------------------------------------------------------------
    def test_27_existing_appx_regression(self):
        course = parse_academic_txt(SAMPLE_LEGACY_APPX_TXT, "240_Discrete_Structures(Math).txt")
        self.assertEqual(course.subject, "Mathematics")
        self.assertEqual(course.course, "Discrete Structures")
        self.assertEqual(len(course.all_items), 5)
        # Unit 1 logic
        unit1 = next(u for u in course.units if u.number == 1)
        self.assertEqual(unit1.title, "Logic and Proof Techniques")
        self.assertEqual(len(unit1.items), 2)
        # General syllabus item
        gen_unit = next(u for u in course.units if u.number == 0)
        self.assertEqual(len(gen_unit.items), 1)
        self.assertEqual(gen_unit.items[0].category, "pdf")


if __name__ == "__main__":
    unittest.main()
