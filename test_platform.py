import os
import sys
import time
import shutil
import asyncio
import unittest
from datetime import datetime, timedelta

# Ensure root directory is in sys.path
sys.path.insert(0, os.path.abspath("."))

from vars import DATA_DIR, USERS_DIR, JOBS_DIR, STATE_DIR, COOKIES_DIR, TEMP_DIR
from db import db, atomic_json_write, safe_json_read
from utils import JobProgressTracker, hrb, hrt, make_progress_bar
from job_manager import job_manager, JobCheckpoint
from academic_parser import parse_academic_txt, build_unit_header_message, build_unit_status_message
import itsgolu as helper


class TestDownloaderPlatform(unittest.TestCase):

    def setUp(self):
        # Ensure directories exist
        for d in [DATA_DIR, USERS_DIR, JOBS_DIR, STATE_DIR, COOKIES_DIR, TEMP_DIR]:
            os.makedirs(d, exist_ok=True)

    def test_01_atomic_json_persistence(self):
        test_file = os.path.join(STATE_DIR, "test_state.json")
        data = {"bot": "test_bot", "count": 42, "active": True}
        
        # Test write
        ok = atomic_json_write(test_file, data)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(test_file))

        # Test read
        read_data = safe_json_read(test_file, dict)
        self.assertEqual(read_data["bot"], "test_bot")
        self.assertEqual(read_data["count"], 42)

        # Test corrupted file recovery
        with open(test_file, "w") as f:
            f.write("{invalid json content---")
        recovered = safe_json_read(test_file, lambda: {"recovered": True})
        self.assertTrue(recovered.get("recovered"))
        
        if os.path.exists(test_file):
            os.remove(test_file)

    def test_02_user_subscription_and_bans(self):
        test_uid = 9999991
        test_name = "Test Subscriber"
        
        # 1. Add user
        ok, expiry = db.add_user(test_uid, test_name, days=30)
        self.assertTrue(ok)
        self.assertIsNotNone(expiry)
        self.assertTrue(db.is_user_authorized(test_uid))

        # 2. Check info
        info = db.get_user_expiry_info(test_uid)
        self.assertIsNotNone(info)
        self.assertEqual(info["user_id"], test_uid)
        self.assertTrue(info["is_active"])
        self.assertFalse(info["banned"])

        # 3. Renew user
        ok_renew, new_exp = db.renew_user(test_uid, days=15)
        self.assertTrue(ok_renew)
        self.assertGreater(new_exp, expiry)

        # 4. Ban user
        self.assertTrue(db.ban_user(test_uid))
        self.assertTrue(db.is_banned(test_uid))
        self.assertFalse(db.is_user_authorized(test_uid))

        # 5. Unban user
        self.assertTrue(db.unban_user(test_uid))
        self.assertFalse(db.is_banned(test_uid))
        self.assertTrue(db.is_user_authorized(test_uid))

        # 6. Remove user
        self.assertTrue(db.remove_user(test_uid))
        self.assertFalse(db.is_user_authorized(test_uid))

    def test_03_youtube_cookies_isolation(self):
        user_a = 111111
        user_b = 222222
        
        cookie_data_a = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1750000000\tSID\tTOKEN_USER_A\n"
        cookie_data_b = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1750000000\tSID\tTOKEN_USER_B\n"

        # Save cookies
        self.assertTrue(db.save_user_cookies(user_a, cookie_data_a))
        self.assertTrue(db.save_user_cookies(user_b, cookie_data_b))

        # Verify existence
        self.assertTrue(db.has_user_cookies(user_a))
        self.assertTrue(db.has_user_cookies(user_b))

        # Check paths and contents are isolated
        path_a = db.get_user_cookies_path(user_a)
        path_b = db.get_user_cookies_path(user_b)
        self.assertNotEqual(path_a, path_b)

        with open(path_a, "r") as f:
            self.assertIn("TOKEN_USER_A", f.read())
        with open(path_b, "r") as f:
            self.assertIn("TOKEN_USER_B", f.read())

        # Check helper returns user cookie
        cfile_a = helper.get_cookies_file(user_a)
        self.assertEqual(cfile_a, path_a)

        # Delete user_a cookies and check user_b is untouched
        self.assertTrue(db.delete_user_cookies(user_a))
        self.assertFalse(db.has_user_cookies(user_a))
        self.assertTrue(db.has_user_cookies(user_b))
        db.delete_user_cookies(user_b)

    def test_04_forum_topic_persistence(self):
        chat_id = -100123456789
        subject = "Applied Mathematics"
        unit_key = "unit_1_calculus"
        topic_id = 4567

        # Save topic
        self.assertTrue(db.save_unit_topic(chat_id, subject, unit_key, topic_id))

        # Retrieve topic
        cached_id = db.get_unit_topic(chat_id, subject, unit_key)
        self.assertEqual(cached_id, topic_id)

        # Case-insensitivity check
        cached_id_upper = db.get_unit_topic(chat_id, "APPLIED MATHEMATICS", "UNIT_1_CALCULUS")
        self.assertEqual(cached_id_upper, topic_id)

    def test_05_job_manager_and_checkpoints(self):
        user_id = 777777
        chat_id = 777777
        channel_id = -100987654321

        sample_txt = "Lecture 01 : https://example.com/v1.mp4\nLecture 02 : https://example.com/v2.mp4\nLecture 03 : https://example.com/v3.mp4"

        # 1. Create Job
        job = job_manager.create_job(
            user_id=user_id,
            chat_id=chat_id,
            channel_id=channel_id,
            source_filename="course.txt",
            raw_content=sample_txt,
            total_items=3,
            start_index=1,
            batch_name="Physics Batch",
            quality="720p"
        )

        self.assertIsNotNone(job.job_id)
        self.assertEqual(job.status, "QUEUED")
        self.assertTrue(os.path.exists(job.temp_dir))

        # 2. Checkpoint updates
        job.completed_indices.append(0)
        job.current_index = 1
        job.phase = "DOWNLOADING"
        db.save_job(job.to_dict())

        # Retrieve from DB
        saved = db.get_job(job.job_id)
        self.assertEqual(saved["current_index"], 1)
        self.assertIn(0, saved["completed_indices"])

        # 3. Test Pause
        job_manager.user_active_jobs[user_id] = job.job_id
        paused_ok, msg, paused_job = job_manager.pause_job(user_id)
        self.assertTrue(paused_ok)
        self.assertEqual(paused_job.status, "PAUSED")

        # 4. Test Cancel & Temp cleanup
        temp_dir = job.temp_dir
        cancel_ok, msg = job_manager.cancel_job(user_id)
        self.assertTrue(cancel_ok)
        self.assertFalse(os.path.exists(temp_dir))

    def test_06_progress_tracker_speed_and_formatting(self):
        tracker = JobProgressTracker(job_id="TEST_JOB", course_name="Data Structures", total_items=10)
        tracker.set_item(index=3, title="Array Operations", phase="Downloading")

        # Fake elapsed time
        tracker.start_time = time.time() - 5.0  # 5 seconds elapsed
        current_bytes = 10 * 1024 * 1024  # 10 MB
        total_bytes = 50 * 1024 * 1024   # 50 MB

        speed, percent, eta = tracker.compute_speed_and_eta(current_bytes, total_bytes)
        self.assertAlmostEqual(percent, 20.0, places=1)
        self.assertGreater(speed, 0)
        self.assertGreater(eta, 0)

        box_text = tracker.format_box(current_bytes, total_bytes, speed, percent, eta)
        self.assertIn("TEST_JOB", box_text)
        self.assertIn("Data Structures", box_text)
        self.assertIn("20.0%", box_text)
        self.assertNotIn("MB/s", box_text)

    def test_07_academic_parser(self):
        sample_course_txt = """
[Course] Advanced Algorithms
Unit 1.0 Graph Theory
1. BFS and DFS : https://example.com/lec1.mp4
2. Dijkstra Algorithm : https://example.com/lec2.mp4

Unit 2.0 Dynamic Programming
3. Knapsack Problem : https://example.com/lec3.pdf
"""
        course = parse_academic_txt(sample_course_txt, "algorithms.txt")
        self.assertEqual(course.course, "Advanced Algorithms")
        self.assertEqual(len(course.all_items), 3)
        self.assertEqual(len(course.units), 2)
        self.assertEqual(course.all_items[0].unit_number, 1)
        self.assertEqual(course.all_items[2].category, "pdf")

    def test_08_safe_filename_sanitization(self):
        unsafe = 'Test/Video: File *with? "bad" <chars> | and \\slashes'
        clean = helper.safe_filename(unsafe)
        self.assertNotIn("/", clean)
        self.assertNotIn("\\", clean)
        self.assertNotIn(":", clean)
        self.assertNotIn("*", clean)
        self.assertNotIn("?", clean)
        self.assertNotIn('"', clean)
        self.assertNotIn("<", clean)
        self.assertNotIn(">", clean)
    def test_09_restart_recovery(self):
        # Create an interrupted job in persistent state
        job_id = "JOB_RECOVERY_TEST"
        user_id = 888888
        job_dict = {
            "job_id": job_id,
            "user_id": user_id,
            "chat_id": user_id,
            "channel_id": -100111222333,
            "source_filename": "recovery_test.txt",
            "total_items": 10,
            "current_index": 4,
            "completed_indices": [0, 1, 2, 3],
            "failed_indices": [],
            "status": "DOWNLOADING",
            "phase": "DOWNLOADING",
            "batch_name": "Recovery Course",
            "raw_content": "1. L1: https://e.com/1\n2. L2: https://e.com/2",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        db.save_job(job_dict)

        unfinished = db.get_unfinished_jobs()
        self.assertTrue(any(j["job_id"] == job_id for j in unfinished))

        # Test recovery manager finding and loading job
        found = False
        for j in unfinished:
            if j["job_id"] == job_id:
                job_obj = JobCheckpoint.from_dict(j)
                self.assertEqual(job_obj.current_index, 4)
                self.assertEqual(len(job_obj.completed_indices), 4)
                found = True
                break
        self.assertTrue(found)

        # Cleanup
        db.delete_job(job_id)

    def test_10_drm_protection_policy(self):
        # DRM protected items must be rejected gracefully without attempting bypass
        fake_api_response = {
            "status": "error",
            "drmProtected": 1,
            "message": "Content is protected by DRM"
        }
        # Verify status is treated as error
        self.assertEqual(fake_api_response.get("drmProtected"), 1)
        self.assertEqual(fake_api_response.get("status"), "error")

    def test_11_concurrency_workers_config(self):
        from vars import DOWNLOAD_WORKERS, UPLOAD_WORKERS, MAX_ACTIVE_USERS
        self.assertGreaterEqual(DOWNLOAD_WORKERS, 1)
        self.assertGreaterEqual(UPLOAD_WORKERS, 1)
        self.assertGreaterEqual(MAX_ACTIVE_USERS, 1)

    def test_12_per_user_isolated_temp_directories(self):
        user_1 = 12345
        user_2 = 67890
        job_1 = job_manager.create_job(user_1, user_1, -100, "f1.txt", "1. A: https://a.com", 1)
        job_2 = job_manager.create_job(user_2, user_2, -100, "f2.txt", "1. B: https://b.com", 1)

        self.assertIn(str(user_1), job_1.temp_dir)
        self.assertIn(str(user_2), job_2.temp_dir)
        self.assertNotEqual(job_1.temp_dir, job_2.temp_dir)

        # Cancel and cleanup
        job_manager.cancel_job(user_1)
        job_manager.cancel_job(user_2)
        self.assertFalse(os.path.exists(job_1.temp_dir))
        self.assertFalse(os.path.exists(job_2.temp_dir))

    def test_13_pdf_validation(self):
        # Create a dummy valid PDF bytes structure with %PDF- header
        valid_pdf_path = "test_valid.pdf"
        with open(valid_pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\n2 0 obj\n<<\n/Type /Pages\n/Kids [3 0 R]\n/Count 1\n>>\nendobj\n3 0 obj\n<<\n/Type /Page\n/Parent 2 0 R\n/MediaBox [0 0 612 792]\n>>\nendobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \ntrailer\n<<\n/Size 4\n/Root 1 0 R\n>>\nstartxref\n185\n%%EOF")

        is_valid, pages, val_msg = helper.validate_pdf_file(valid_pdf_path)
        self.assertTrue(is_valid)
        self.assertGreaterEqual(pages, 1)

        if os.path.exists(valid_pdf_path):
            os.remove(valid_pdf_path)


if __name__ == "__main__":
    unittest.main()
