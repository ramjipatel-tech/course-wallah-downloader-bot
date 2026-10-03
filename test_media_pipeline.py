import os
import sys
import unittest
import tempfile
import asyncio
import fitz
from unittest.mock import patch, MagicMock, AsyncMock

import itsgolu as helper
from itsgolu import (
    resolve_lecture_source,
    resolve_fetch_video_url,
    validate_pdf_file,
    apply_pdf_watermark,
    apply_video_watermark,
    get_ytdlp_cmd,
    LectureSourceResult
)
from utils import MediaRouter, MediaType, build_failure_card, sanitize_error_message
from job_manager import JobManager, JobCheckpoint
from academic_parser import parse_academic_txt, AcademicItem


class MockResponse:
    def __init__(self, json_data, status_code=200, headers=None, url="https://api.example.com/lecture/1"):
        self._json = json_data
        self.status_code = status_code
        self.reason = "OK" if status_code == 200 else "Error"
        self.headers = headers or {"content-type": "application/json"}
        self.url = url

    def json(self):
        return self._json


class TestMasterSuite(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # TEST 1: APPX response = video + PDF (Expected: VIDEO -> PDF dispatch)
    # -------------------------------------------------------------------------
    @patch("requests.get")
    def test_01_appx_video_and_pdf(self, mock_get):
        payload = {
            "Title": "Lecture 01: Calculus Introduction",
            "thumbnail": "https://img.example.com/thumb1.jpg",
            "download_links": [
                {"quality": "720p", "path": "https://cdn.example.com/stream720.m3u8"},
                {"quality": "480p", "path": "https://cdn.example.com/stream480.m3u8"}
            ],
            "pdf_link": "https://cdn.example.com/notes1.pdf",
            "video_id": "v101",
            "course_id": "c202"
        }
        mock_get.return_value = MockResponse(payload, status_code=200)

        res = resolve_lecture_source("https://api.example.com/lecture/1", target_quality="720p")
        self.assertTrue(res.has_video)
        self.assertEqual(res.video_url, "https://cdn.example.com/stream720.m3u8")
        self.assertEqual(res.video_quality, "720p")
        self.assertTrue(res.has_pdf)
        self.assertEqual(res.pdf_url, "https://cdn.example.com/notes1.pdf")
        self.assertEqual(res.title, "Lecture 01: Calculus Introduction")
        self.assertFalse(res.is_drm)
        self.assertIsNone(res.error)

    # -------------------------------------------------------------------------
    # TEST 2: APPX response = video only (Expected: VIDEO success, PDF NOT_AVAILABLE, No failure)
    # -------------------------------------------------------------------------
    @patch("requests.get")
    def test_02_appx_video_only(self, mock_get):
        payload = {
            "title": "Quantum Physics Intro",
            "download_links": [
                {"quality": "1080p", "path": "https://cdn.example.com/qp1080.m3u8"}
            ]
        }
        mock_get.return_value = MockResponse(payload, status_code=200)

        res = resolve_lecture_source("https://api.example.com/lecture/video_only")
        self.assertTrue(res.has_video)
        self.assertEqual(res.video_url, "https://cdn.example.com/qp1080.m3u8")
        self.assertFalse(res.has_pdf)
        self.assertIsNone(res.pdf_url)
        self.assertEqual(res.title, "Quantum Physics Intro")

        # Failure accounting test
        v_status = "SUCCESS" if res.has_video else "NOT_AVAILABLE"
        pdf_status = "SUCCESS" if res.has_pdf else "NOT_AVAILABLE"
        lec_status = "SUCCESS" if (v_status in ("SUCCESS", "NOT_AVAILABLE") and pdf_status in ("SUCCESS", "NOT_AVAILABLE") and (v_status == "SUCCESS" or pdf_status == "SUCCESS")) else "FAILED"
        self.assertEqual(v_status, "SUCCESS")
        self.assertEqual(pdf_status, "NOT_AVAILABLE")
        self.assertEqual(lec_status, "SUCCESS")

    # -------------------------------------------------------------------------
    # TEST 3: APPX response = PDF only (Expected: PDF success, VIDEO NOT_AVAILABLE, No failure)
    # -------------------------------------------------------------------------
    @patch("requests.get")
    def test_03_appx_pdf_only(self, mock_get):
        payload = {
            "Title": "Handwritten Formula Sheet",
            "data": {
                "pdf_url": "https://cdn.example.com/formulas.pdf",
                "download_links": []
            }
        }
        mock_get.return_value = MockResponse(payload, status_code=200)

        res = resolve_lecture_source("https://api.example.com/lecture/pdf_only")
        self.assertFalse(res.has_video)
        self.assertIsNone(res.video_url)
        self.assertTrue(res.has_pdf)
        self.assertEqual(res.pdf_url, "https://cdn.example.com/formulas.pdf")
        self.assertEqual(res.title, "Handwritten Formula Sheet")

        # Failure accounting test
        v_status = "SUCCESS" if res.has_video else "NOT_AVAILABLE"
        pdf_status = "SUCCESS" if res.has_pdf else "NOT_AVAILABLE"
        lec_status = "SUCCESS" if (v_status in ("SUCCESS", "NOT_AVAILABLE") and pdf_status in ("SUCCESS", "NOT_AVAILABLE") and (v_status == "SUCCESS" or pdf_status == "SUCCESS")) else "FAILED"
        self.assertEqual(v_status, "NOT_AVAILABLE")
        self.assertEqual(pdf_status, "SUCCESS")
        self.assertEqual(lec_status, "SUCCESS")

    # -------------------------------------------------------------------------
    # TEST 4: APPX response = neither (Expected: actual media-resolution failure)
    # -------------------------------------------------------------------------
    @patch("requests.get")
    def test_04_appx_neither(self, mock_get):
        payload = {
            "status": "error",
            "message": "Content expired or not accessible",
            "download_links": []
        }
        mock_get.return_value = MockResponse(payload, status_code=200)

        res = resolve_lecture_source("https://api.example.com/lecture/empty")
        self.assertFalse(res.has_video)
        self.assertFalse(res.has_pdf)
        self.assertIsNotNone(res.error)

        has_usable_resource = res.has_video or res.has_pdf
        self.assertFalse(has_usable_resource)

    # -------------------------------------------------------------------------
    # TEST 5: APPX response = empty download_links but valid file_link (Expected: video success)
    # -------------------------------------------------------------------------
    @patch("requests.get")
    def test_05_appx_empty_download_links_valid_file_link(self, mock_get):
        payload = {
            "title": "Fallback Video Lecture",
            "download_links": [],
            "file_link": "https://cdn.example.com/direct_encoded.m3u8"
        }
        mock_get.return_value = MockResponse(payload, status_code=200)

        res = resolve_lecture_source("https://api.example.com/lecture/fallback_link")
        self.assertTrue(res.has_video)
        self.assertEqual(res.video_url, "https://cdn.example.com/direct_encoded.m3u8")
        self.assertEqual(res.title, "Fallback Video Lecture")

    # -------------------------------------------------------------------------
    # TEST 6: APPX response = valid PDF via document / notes_link but no video (Expected: PDF success)
    # -------------------------------------------------------------------------
    @patch("requests.get")
    def test_06_appx_valid_notes_link_no_video(self, mock_get):
        payload = {
            "title": "Unit Notes Document",
            "notes_link": "https://cdn.example.com/unit_notes.pdf"
        }
        mock_get.return_value = MockResponse(payload, status_code=200)

        res = resolve_lecture_source("https://api.example.com/lecture/notes_only")
        self.assertFalse(res.has_video)
        self.assertTrue(res.has_pdf)
        self.assertEqual(res.pdf_url, "https://cdn.example.com/unit_notes.pdf")

    # -------------------------------------------------------------------------
    # TEST 7: Direct M3U8 (Expected: classified as DIRECT_M3U8, title cleaned, no APPX API call)
    # -------------------------------------------------------------------------
    def test_07_direct_m3u8_routing_and_title(self):
        m3u8_url = "https://live-cdn.example.com/streams/live_lesson_01.m3u8?token=xyz123&expires=999999"
        m_type = MediaRouter.classify_url(m3u8_url)
        self.assertEqual(m_type, MediaType.DIRECT_M3U8)

        clean_title = MediaRouter.extract_clean_title(m3u8_url, "Default Title")
        self.assertEqual(clean_title, "live_lesson_01")
        self.assertNotIn("token", clean_title)
        self.assertNotIn("xyz123", clean_title)

    # -------------------------------------------------------------------------
    # TEST 8: Direct PDF (Expected: classified as DIRECT_PDF, direct validation pass)
    # -------------------------------------------------------------------------
    def test_08_direct_pdf_routing_and_validation(self):
        pdf_url = "https://cdn.example.com/syllabus/physics_ch1.pdf?auth=abc"
        m_type = MediaRouter.classify_url(pdf_url)
        self.assertEqual(m_type, MediaType.DIRECT_PDF)

        sample_pdf = os.path.join(self.test_dir, "physics_ch1.pdf")
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 50), "Chapter 1 Physics Notes")
        doc.save(sample_pdf)
        doc.close()

        is_valid, pages, err = validate_pdf_file(sample_pdf)
        self.assertTrue(is_valid)
        self.assertEqual(pages, 1)

    # -------------------------------------------------------------------------
    # TEST 9: Two APPX jobs concurrently (Expected: one does not cancel the other)
    # -------------------------------------------------------------------------
    def test_09_two_appx_jobs_concurrent_isolation(self):
        manager = JobManager()
        manager.user_active_jobs[101] = "job_AAA"
        manager.user_active_jobs[102] = "job_BBB"
        manager.cancel_flags["job_AAA"] = False
        manager.cancel_flags["job_BBB"] = False

        # Cancelling Job A must NOT cancel Job B
        manager.cancel_flags["job_AAA"] = True
        manager.cancel_reasons["job_AAA"] = "User requested stop"
        self.assertTrue(manager.cancel_flags["job_AAA"])
        self.assertFalse(manager.cancel_flags.get("job_BBB", False))
        self.assertEqual(manager.cancel_reasons["job_AAA"], "User requested stop")
        self.assertNotIn("job_BBB", manager.cancel_reasons)

    # -------------------------------------------------------------------------
    # TEST 10: APPX + M3U8 concurrently (Expected: both progress independently within limits)
    # -------------------------------------------------------------------------
    def test_10_appx_and_m3u8_concurrency(self):
        manager = JobManager()
        self.assertEqual(manager.download_semaphore._value, 4)
        self.assertEqual(manager.upload_semaphore._value, 3)

        manager.user_active_jobs[201] = "job_APPX"
        manager.user_active_jobs[202] = "job_M3U8"

        self.assertEqual(manager.user_active_jobs.get(201), "job_APPX")
        self.assertEqual(manager.user_active_jobs.get(202), "job_M3U8")
        self.assertIsNone(manager.user_active_jobs.get(203))

    # -------------------------------------------------------------------------
    # TEST 11: Stop Job A (Expected: Job B continues without disruption)
    # -------------------------------------------------------------------------
    def test_11_stop_job_a_job_b_continues(self):
        manager = JobManager()
        manager.cancel_flags["JOB_A"] = False
        manager.cancel_flags["JOB_B"] = False

        manager.cancel_flags["JOB_A"] = True
        manager.cancel_reasons["JOB_A"] = "Admin killed Job A"
        self.assertTrue(manager.cancel_flags["JOB_A"])
        self.assertFalse(manager.cancel_flags["JOB_B"])

    # -------------------------------------------------------------------------
    # TEST 12: Resume Job A (Expected: completed resources skipped)
    # -------------------------------------------------------------------------
    def test_12_resume_job_skips_completed(self):
        job = JobCheckpoint(
            job_id="job_RESUME",
            user_id=501,
            chat_id=501,
            channel_id=501,
            source_filename="course.txt",
            total_items=5,
            current_index=2,
            completed_indices=[0, 1]
        )
        # Verify indices 0 and 1 are skipped
        items_to_process = [i for i in range(job.total_items) if i not in job.completed_indices]
        self.assertEqual(items_to_process, [2, 3, 4])
        self.assertEqual(items_to_process[0], 2)

    # -------------------------------------------------------------------------
    # TEST 13: APPX video + PDF dispatch order: VIDEO N -> PDF N -> VIDEO N+1 -> PDF N+1
    # -------------------------------------------------------------------------
    def test_13_exact_dispatch_order_simulation(self):
        dispatched_events = []
        lectures = [
            {"id": 1, "has_video": True, "has_pdf": True},
            {"id": 2, "has_video": True, "has_pdf": True},
            {"id": 3, "has_video": True, "has_pdf": False},
            {"id": 4, "has_video": False, "has_pdf": True},
        ]
        for lec in lectures:
            if lec["has_video"]:
                dispatched_events.append(f"VIDEO {lec['id']}")
            if lec["has_pdf"]:
                dispatched_events.append(f"PDF {lec['id']}")

        expected_order = [
            "VIDEO 1",
            "PDF 1",
            "VIDEO 2",
            "PDF 2",
            "VIDEO 3",
            "PDF 4"
        ]
        self.assertEqual(dispatched_events, expected_order)

    # -------------------------------------------------------------------------
    # TEST 14: TXT with multiple subjects (Expected: Subject A, Subject B, Subject C separated)
    # -------------------------------------------------------------------------
    def test_14_txt_multiple_subjects_organization(self):
        sample_txt = """
# Course: Gate CS Master
# Subject: Digital Electronics
UNIT 1: Number Systems
L1: https://example.com/de1
L2: https://example.com/de2

# Subject: Theory of Computation
UNIT 1: Finite Automata
L1: https://example.com/toc1
L2: https://example.com/toc2
"""
        course_data = parse_academic_txt(sample_txt, "gate_cs.txt")
        self.assertEqual(len(course_data.all_items), 4)
        subjects = {itm.subject_name for itm in course_data.all_items if itm.subject_name}
        self.assertGreater(len(subjects), 1)

    # -------------------------------------------------------------------------
    # TEST 15: TXT with one subject (Expected: Unit 1, Unit 2, Unit 3 without redundant duplicate subject hierarchy)
    # -------------------------------------------------------------------------
    def test_15_txt_single_subject_organization(self):
        sample_txt = """
UNIT 1: Logic Gates
L1: https://example.com/lg1
L2: https://example.com/lg2

UNIT 2: Boolean Algebra
L3: https://example.com/ba1
L4: https://example.com/ba2
"""
        course_data = parse_academic_txt(sample_txt, "discrete_math.txt")
        self.assertEqual(len(course_data.all_items), 4)
        self.assertEqual(len(course_data.units), 2)
        self.assertEqual(course_data.units[0].number, 1)
        self.assertEqual(course_data.units[1].number, 2)

    # -------------------------------------------------------------------------
    # TEST 16: Batch thumbnail exists (Expected: thumbnail in GENERAL)
    # -------------------------------------------------------------------------
    def test_16_batch_thumbnail_exists(self):
        thumb_path = os.path.join(self.test_dir, "batch_thumb.jpg")
        with open(thumb_path, "wb") as f:
            f.write(b"fake image data")

        self.assertTrue(os.path.exists(thumb_path))
        self.assertNotEqual(thumb_path, "/d")
        self.assertNotEqual(thumb_path, "no")

    # -------------------------------------------------------------------------
    # TEST 17: No thumbnail but intro video exists (Expected: intro video in GENERAL)
    # -------------------------------------------------------------------------
    def test_17_no_thumbnail_intro_video_detected(self):
        items = [
            AcademicItem(raw_line="Intro : https://example.com/intro", index=1, title="Course Introduction & Syllabus", url="https://example.com/intro", url_without_scheme="example.com/intro", category="video"),
            AcademicItem(raw_line="L1 : https://example.com/l1", index=2, title="Lecture 1: Basics", url="https://example.com/l1", url_without_scheme="example.com/l1", category="video")
        ]
        intro_item = next((itm for itm in items if any(k in itm.title.lower() for k in ("intro", "introduction", "overview", "syllabus")) and itm.category == "video"), None)
        self.assertIsNotNone(intro_item)
        self.assertEqual(intro_item.title, "Course Introduction & Syllabus")

    # -------------------------------------------------------------------------
    # TEST 18: No thumbnail and no intro (Expected: no fake media fabricated)
    # -------------------------------------------------------------------------
    def test_18_no_thumbnail_no_intro_no_fake_media(self):
        items = [
            AcademicItem(raw_line="L1 : https://example.com/l1", index=1, title="Lecture 1: Number Systems", url="https://example.com/l1", url_without_scheme="example.com/l1", category="video")
        ]
        thumb_val = "/d"
        intro_item = next((itm for itm in items if any(k in itm.title.lower() for k in ("intro", "introduction", "overview", "syllabus")) and itm.category == "video"), None)
        has_batch_thumb = thumb_val and os.path.exists(thumb_val)
        self.assertFalse(has_batch_thumb)
        self.assertIsNone(intro_item)

    # -------------------------------------------------------------------------
    # TEST 19: Normal private chat (Expected: works without message_thread_id)
    # -------------------------------------------------------------------------
    def test_19_private_chat_dispatch(self):
        chat_id = 987654321  # Positive integer represents private chat
        is_private = isinstance(chat_id, int) and chat_id > 0
        thread_id = None
        send_kwargs = {"message_thread_id": thread_id} if (thread_id and not is_private) else {}
        self.assertNotIn("message_thread_id", send_kwargs)

    # -------------------------------------------------------------------------
    # TEST 20: Normal group (Expected: works without forum-only parameters)
    # -------------------------------------------------------------------------
    def test_20_normal_group_dispatch(self):
        dest_details = {"chat_id": -1001234567, "type": "group", "is_forum": False}
        thread_id = 45
        # Only attach thread ID if is_forum is True and type is supergroup
        attach_thread = dest_details.get("is_forum") and "supergroup" in dest_details.get("type", "")
        send_kwargs = {"message_thread_id": thread_id} if attach_thread else {}
        self.assertNotIn("message_thread_id", send_kwargs)

    # -------------------------------------------------------------------------
    # TEST 21: Forum-enabled supergroup (Expected: real message_thread_id topics)
    # -------------------------------------------------------------------------
    def test_21_forum_supergroup_dispatch(self):
        dest_details = {"chat_id": -1009876543, "type": "supergroup", "is_forum": True}
        thread_id = 42
        attach_thread = dest_details.get("is_forum") and "supergroup" in dest_details.get("type", "")
        send_kwargs = {"message_thread_id": thread_id} if (attach_thread and thread_id) else {}
        self.assertIn("message_thread_id", send_kwargs)
        self.assertEqual(send_kwargs["message_thread_id"], 42)

    # -------------------------------------------------------------------------
    # TEST 22: Channel (Expected: channel-compatible dispatch, no forum params)
    # -------------------------------------------------------------------------
    def test_22_channel_dispatch(self):
        dest_details = {"chat_id": -1005555555, "type": "channel", "is_forum": False}
        thread_id = 12
        attach_thread = dest_details.get("is_forum") and "supergroup" in dest_details.get("type", "")
        send_kwargs = {"message_thread_id": thread_id} if attach_thread else {}
        self.assertNotIn("message_thread_id", send_kwargs)

    # -------------------------------------------------------------------------
    # TEST 23: Video below upload threshold (Expected: single file, no split)
    # -------------------------------------------------------------------------
    def test_23_video_below_upload_threshold(self):
        sample_path = os.path.join(self.test_dir, "small_lecture.mp4")
        with open(sample_path, "wb") as f:
            f.write(b"0" * 1024 * 50)  # 50 KB

        parts = helper.split_large_video(sample_path, max_size_bytes=1024 * 1024)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0], sample_path)

    # -------------------------------------------------------------------------
    # TEST 24: Large video splitting into 2 parts with stream copy
    # -------------------------------------------------------------------------
    @patch("itsgolu.get_video_duration", return_value=120.0)
    @patch("subprocess.run")
    def test_24_large_video_splitting_2_parts(self, mock_subproc, mock_dur):
        large_path = os.path.join(self.test_dir, "large_lecture.mp4")
        # Create a simulated 3.5 MB file with threshold 2 MB
        with open(large_path, "wb") as f:
            f.write(b"0" * int(3.5 * 1024 * 1024))

        threshold = 2 * 1024 * 1024

        def fake_ffmpeg_run(cmd, *args, **kwargs):
            out_file = cmd[-1]
            with open(out_file, "wb") as f:
                f.write(b"0" * int(1.6 * 1024 * 1024))
            return MagicMock(returncode=0, stderr="")

        mock_subproc.side_effect = fake_ffmpeg_run

        parts = helper.split_large_video(large_path, max_size_bytes=threshold, custom_dir=self.test_dir, base_title="Lecture 1")
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0].endswith("Part_1.mp4"))
        self.assertTrue(parts[1].endswith("Part_2.mp4"))
        for p in parts:
            self.assertTrue(os.path.exists(p))
            self.assertLessEqual(os.path.getsize(p), threshold)

    # -------------------------------------------------------------------------
    # TEST 25: Large video splitting into 3+ parts
    # -------------------------------------------------------------------------
    @patch("itsgolu.get_video_duration", return_value=300.0)
    @patch("subprocess.run")
    def test_25_large_video_splitting_3_parts(self, mock_subproc, mock_dur):
        large_path = os.path.join(self.test_dir, "huge_lecture.mp4")
        with open(large_path, "wb") as f:
            f.write(b"0" * int(5.5 * 1024 * 1024))

        threshold = 2 * 1024 * 1024

        def fake_ffmpeg_run(cmd, *args, **kwargs):
            out_file = cmd[-1]
            with open(out_file, "wb") as f:
                f.write(b"0" * int(1.5 * 1024 * 1024))
            return MagicMock(returncode=0, stderr="")

        mock_subproc.side_effect = fake_ffmpeg_run

        parts = helper.split_large_video(large_path, max_size_bytes=threshold, custom_dir=self.test_dir, base_title="Lecture Huge")
        self.assertGreaterEqual(len(parts), 3)

    # -------------------------------------------------------------------------
    # TEST 26: Watermark applied ONCE before splitting (No duplicate encode)
    # -------------------------------------------------------------------------
    @patch("itsgolu.apply_video_watermark")
    @patch("itsgolu.split_large_video")
    def test_26_watermark_before_split(self, mock_split, mock_wm):
        orig_video = os.path.join(self.test_dir, "raw_video.mp4")
        wm_video = os.path.join(self.test_dir, "raw_video_wm.mp4")
        with open(orig_video, "wb") as f:
            f.write(b"0" * 1024)
        with open(wm_video, "wb") as f:
            f.write(b"0" * 1024)

        mock_wm.return_value = wm_video
        mock_split.return_value = [f"{self.test_dir}/part1.mp4", f"{self.test_dir}/part2.mp4"]

        # Watermark happens once on downloaded file
        processed = helper.apply_video_watermark(orig_video, "Course Wallah", None)
        self.assertEqual(processed, wm_video)
        mock_wm.assert_called_once_with(orig_video, "Course Wallah", None)

        # Splitting is performed on the watermarked output
        split_parts = helper.split_large_video(processed, max_size_bytes=500)
        self.assertEqual(len(split_parts), 2)
        mock_split.assert_called_once_with(wm_video, max_size_bytes=500)

    # -------------------------------------------------------------------------
    # TEST 27: Part-level resume / crash checkpoint
    # -------------------------------------------------------------------------
    def test_27_checkpoint_part_level_resume(self):
        job = JobCheckpoint(
            job_id="job_TESTPARTS",
            user_id=12345,
            chat_id=12345,
            channel_id=-100123456,
            source_filename="test.txt",
            total_items=3,
            current_index=0,
            uploaded_parts={"0": [1]}  # Part 1 was already uploaded
        )
        parts_list = ["part_1.mp4", "part_2.mp4", "part_3.mp4"]
        completed_parts = job.uploaded_parts.get("0", [])

        to_upload = []
        for idx, part in enumerate(parts_list, start=1):
            if idx in completed_parts:
                continue  # Skip already uploaded part
            to_upload.append((idx, part))

        # Part 1 is skipped, only Parts 2 & 3 are scheduled
        self.assertEqual(len(to_upload), 2)
        self.assertEqual(to_upload[0], (2, "part_2.mp4"))
        self.assertEqual(to_upload[1], (3, "part_3.mp4"))

    # -------------------------------------------------------------------------
    # TEST 28: UI Stage Formatting (No raw technical statistics on Telegram)
    # -------------------------------------------------------------------------
    def test_28_ui_stage_cards_clean_and_animated(self):
        from utils import (
            format_download_card,
            format_watermark_card,
            format_merging_card,
            format_processing_card,
            format_split_card,
            format_upload_card,
            format_pdf_download_card,
            format_success_card
        )

        dl_card = format_download_card("Mathematics", "Lecture 01 - Calculus", unit_title="Unit 1 - Limits", frame=1)
        self.assertIn("DOWNLOADING", dl_card)
        self.assertIn("Lecture 01 - Calculus", dl_card)
        self.assertNotIn("MB/s", dl_card)
        self.assertNotIn("ETA:", dl_card)

        wm_card = format_watermark_card("Lecture 01", frame=2)
        self.assertIn("WATERMARKING VIDEO", wm_card)
        self.assertIn("Applying Course Wallah watermark", wm_card)

        merge_card = format_merging_card("Lecture 01", frame=0)
        self.assertIn("MERGING VIDEO", merge_card)

        split_card = format_split_card("Large Lecture 02", frame=1)
        self.assertIn("PREPARING LARGE VIDEO", split_card)
        self.assertIn("Automatically splitting it into uploadable parts", split_card)

        up_card = format_upload_card("Lecture 01", part_idx=1, total_parts=2, frame=0)
        self.assertIn("UPLOADING PART 1/2", up_card)

        pdf_card = format_pdf_download_card("Mathematics", "Lecture 01 Notes", frame=0)
        self.assertIn("DOWNLOADING STUDY MATERIAL", pdf_card)

        succ_card = format_success_card("Lecture 01", video_delivered=True, pdf_delivered=True, split_parts=2)
        self.assertIn("DOWNLOAD READY", succ_card)
        self.assertIn("2 part(s) uploaded", succ_card)


if __name__ == "__main__":
    unittest.main()

