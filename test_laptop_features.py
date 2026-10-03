import os
import unittest
import tempfile
import shutil
import json
from unittest.mock import MagicMock, patch

from academic_parser import (
    parse_academic_txt,
    build_pdf_caption,
    build_enriched_caption,
    detect_subject_and_course
)
import itsgolu as helper
from main import (
    build_welcome_dashboard,
    build_unauthorized_panel,
    format_job_progress_card
)
from job_manager import JobCheckpoint, job_manager
from db import db


import asyncio


class TestLaptopBotFeatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.loop = asyncio.get_event_loop()
        except RuntimeError:
            cls.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(cls.loop)

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_start_dashboard_active_subscriber(self):
        """Verify Welcome Dashboard generates beautiful Course Wallah panel with active status."""
        test_uid = 99912345
        db.add_user(test_uid, "TestSubscriber", days=30)
        
        text, markup = build_welcome_dashboard(test_uid, "TestSubscriber", is_admin=False)
        self.assertIn("WELCOME TO COURSE WALLAH", text)
        self.assertIn("TestSubscriber", text)
        self.assertIn("ACTIVE", text)
        self.assertIn("Available Benefits", text)
        self.assertIn("Video Downloads", text)
        self.assertIn("PDF / Notes", text)
        
        # Verify button callbacks
        btn_data = [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]
        self.assertIn("menu_courses", btn_data)
        self.assertIn("menu_download", btn_data)
        self.assertIn("menu_subscription", btn_data)
        self.assertIn("menu_help", btn_data)
        
        db.remove_user(test_uid)

    def test_start_dashboard_unauthorized(self):
        """Verify non-subscriber panel shows subscription required card."""
        text, markup = build_unauthorized_panel()
        self.assertIn("SUBSCRIPTION REQUIRED", text)
        self.assertIn("Get access to", text)
        self.assertIn("Video Courses", text)
        self.assertIn("PDFs & Notes", text)
        
        btn_urls = [btn.url for row in markup.inline_keyboard for btn in row if btn.url]
        self.assertTrue(len(btn_urls) > 0)

    def test_job_progress_card(self):
        """Verify progress status message formatting has real counts and no fake percentages."""
        card = format_job_progress_card(
            course_name="Operating Systems",
            total=145,
            completed=82,
            failed=4,
            current_title="Lecture 83 — Virtual Memory",
            phase="DOWNLOADING"
        )
        self.assertIn("PROCESSING COURSE", card)
        self.assertIn("Total:</b> 145", card)
        self.assertIn("Success:</b> 82", card)
        self.assertIn("Failed:</b> 4", card)
        self.assertIn("Remaining:</b> 59", card)
        self.assertIn("Lecture 83 — Virtual Memory", card)
        self.assertIn("DOWNLOADING", card)

    def test_single_api_response_video_and_pdf(self):
        """Verify one API response independently resolves both Video and PDF."""
        mock_api_response = {
            "status": "success",
            "video_id": "vid_101",
            "course_id": "course_202",
            "data": {
                "id": "lec_01",
                "Title": "Lecture 01 — Introduction to Logic Gates",
                "thumbnail": "https://example.com/thumb.jpg",
                "download_links": [
                    {"quality": "720p", "path": "https://example.com/stream_720.m3u8"},
                    {"quality": "480p", "path": "https://example.com/stream_480.m3u8"}
                ],
                "pdf_link": "https://example.com/notes/lecture_01.pdf"
            }
        }
        
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {"content-type": "application/json"}
            mock_resp.json.return_value = mock_api_response
            mock_get.return_value = mock_resp
            
            res = helper.resolve_lecture_source("https://example.com/fetch_video?id=101", target_quality="720p")
            
            self.assertTrue(res.has_video)
            self.assertEqual(res.video_url, "https://example.com/stream_720.m3u8")
            self.assertEqual(res.video_quality, "720p")
            self.assertTrue(res.has_pdf)
            self.assertEqual(res.pdf_url, "https://example.com/notes/lecture_01.pdf")
            self.assertEqual(res.title, "Lecture 01 — Introduction to Logic Gates")
            self.assertEqual(res.thumbnail, "https://example.com/thumb.jpg")

    def test_single_api_response_video_only_missing_pdf_is_not_failure(self):
        """Verify when pdf_link is empty, video still succeeds and PDF is marked not available without failing."""
        mock_api_response = {
            "status": "success",
            "data": {
                "Title": "Lecture 02 — Boolean Algebra",
                "download_links": [
                    {"quality": "720p", "path": "https://example.com/stream_720.m3u8"}
                ],
                "pdf_link": "",
                "pdf_link2": "",
                "pdf_summary_link": ""
            }
        }
        
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {"content-type": "application/json"}
            mock_resp.json.return_value = mock_api_response
            mock_get.return_value = mock_resp
            
            res = helper.resolve_lecture_source("https://example.com/fetch_video?id=102")
            
            self.assertTrue(res.has_video)
            self.assertEqual(res.video_url, "https://example.com/stream_720.m3u8")
            self.assertFalse(res.has_pdf)
            self.assertIsNone(res.pdf_url)
            self.assertIsNone(res.error)

    def test_empty_download_links_fallback(self):
        """Verify empty download_links falls back to file_link, backup_url, etc."""
        mock_api_response = {
            "status": "success",
            "data": {
                "Title": "Lecture 03 — Karnaugh Maps",
                "download_links": [],
                "file_link": "https://example.com/backup_stream.mp4",
                "pdf_link": "https://example.com/kmap.pdf"
            }
        }
        
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {"content-type": "application/json"}
            mock_resp.json.return_value = mock_api_response
            mock_get.return_value = mock_resp
            
            res = helper.resolve_lecture_source("https://example.com/fetch_video?id=103")
            
            self.assertTrue(res.has_video)
            self.assertEqual(res.video_url, "https://example.com/backup_stream.mp4")
            self.assertTrue(res.has_pdf)

    def test_pdf_caption_design(self):
        """Verify styled PDF caption matches Course Wallah aesthetic."""
        cap = build_pdf_caption(
            course_name="Digital Electronics",
            lecture_title="Unit 2.0 Boolean Algebra",
            subject_name="Electronics",
            unit_title="Unit 2",
            item_index=17,
            credit="Course Wallah"
        )
        self.assertIn("STUDY MATERIAL", cap)
        self.assertIn("Digital Electronics", cap)
        self.assertIn("Unit 2.0 Boolean Algebra", cap)
        self.assertIn("017", cap)
        self.assertIn("Course Wallah", cap)

    def test_result_files_generation_and_retry(self):
        """Verify generation of successful.txt and failed.txt and creation of retry job."""
        raw_txt = (
            "Lecture 1 : https://example.com/lec1\n"
            "Lecture 2 : https://example.com/lec2\n"
            "Lecture 3 : https://example.com/lec3\n"
        )
        course = parse_academic_txt(raw_txt, "test_course.txt")
        self.assertEqual(len(course.all_items), 3)

        job = job_manager.create_job(
            user_id=8888,
            chat_id=8888,
            channel_id=8888,
            source_filename="test_course.txt",
            raw_content=raw_txt,
            total_items=3,
            start_index=1,
            batch_name="Test Batch"
        )

        # Simulate items 1 & 3 succeeded, item 2 failed
        job.completed_indices = [0, 2]
        job.failed_indices = [1]
        job.item_results = [
            {"index": 1, "title": "Lecture 1", "raw_line": course.all_items[0].raw_line, "video_status": "SUCCESS", "pdf_status": "NOT_AVAILABLE"},
            {"index": 2, "title": "Lecture 2", "raw_line": course.all_items[1].raw_line, "video_status": "FAILED", "pdf_status": "FAILED", "failure_reason": "NETWORK_TIMEOUT"},
            {"index": 3, "title": "Lecture 3", "raw_line": course.all_items[2].raw_line, "video_status": "SUCCESS", "pdf_status": "SUCCESS"}
        ]
        db.save_job(job.to_dict())

        # Test successful lines
        succ_lines = [res["raw_line"] for res in job.item_results if res["video_status"] == "SUCCESS"]
        self.assertEqual(len(succ_lines), 2)
        self.assertIn("Lecture 1 : https://example.com/lec1", succ_lines)
        self.assertIn("Lecture 3 : https://example.com/lec3", succ_lines)

        # Test failed lines
        fail_lines = [res["raw_line"] for res in job.item_results if res["video_status"] == "FAILED"]
        self.assertEqual(len(fail_lines), 1)
        self.assertEqual(fail_lines[0], "Lecture 2 : https://example.com/lec2")

        # Create retry job
        retry_content = "\n".join(fail_lines)
        retry_job = job_manager.create_job(
            user_id=job.user_id,
            chat_id=job.chat_id,
            channel_id=job.channel_id,
            source_filename=f"retry_{job.source_filename}",
            raw_content=retry_content,
            total_items=len(fail_lines),
            start_index=1,
            batch_name=f"Retry — {job.batch_name}"
        )
        self.assertEqual(retry_job.total_items, 1)
        self.assertIn("Lecture 2", retry_job.raw_content)
        
        # Cleanup
        db.delete_job(job.job_id)
        db.delete_job(retry_job.job_id)

    def test_destination_resolution_and_send_kwargs(self):
        """Verify all 5 Telegram destination types: Private, Group, Supergroup, Forum, Channel."""
        import asyncio
        from main import resolve_destination, build_send_kwargs, _DESTINATION_CACHE
        _DESTINATION_CACHE.clear()

        # 1. Private Chat (Positive integer user ID)
        mock_client = MagicMock()
        dest_pvt = self.run_async(resolve_destination(mock_client, 12345678))
        self.assertEqual(dest_pvt["type"], "private")
        self.assertFalse(dest_pvt["is_forum"])

        kwargs_pvt = self.run_async(build_send_kwargs(mock_client, 12345678, topic_thread_id=99))
        self.assertEqual(kwargs_pvt, {})  # Thread ID must NOT be passed to private chat

        # 2. Normal Group
        mock_group_chat = MagicMock()
        mock_group_chat.id = -1001111111111
        mock_group_chat.type = "group"
        mock_group_chat.is_forum = False
        mock_group_chat.title = "Standard Group"
        fut_grp = self.loop.create_future()
        fut_grp.set_result(mock_group_chat)
        mock_client.get_chat = MagicMock(return_value=fut_grp)

        _DESTINATION_CACHE.clear()
        dest_grp = self.run_async(resolve_destination(mock_client, -1001111111111))
        self.assertEqual(dest_grp["type"], "group")
        self.assertFalse(dest_grp["is_forum"])

        kwargs_grp = self.run_async(build_send_kwargs(mock_client, -1001111111111, topic_thread_id=99))
        self.assertEqual(kwargs_grp, {})  # Thread ID must NOT be passed to standard group

        # 3. Supergroup Without Forum
        mock_sg_chat = MagicMock()
        mock_sg_chat.id = -1002222222222
        mock_sg_chat.type = "supergroup"
        mock_sg_chat.is_forum = False
        mock_sg_chat.title = "Standard Supergroup"
        fut_sg = self.loop.create_future()
        fut_sg.set_result(mock_sg_chat)
        mock_client.get_chat = MagicMock(return_value=fut_sg)

        _DESTINATION_CACHE.clear()
        dest_sg = self.run_async(resolve_destination(mock_client, -1002222222222))
        self.assertEqual(dest_sg["type"], "supergroup")
        self.assertFalse(dest_sg["is_forum"])

        kwargs_sg = self.run_async(build_send_kwargs(mock_client, -1002222222222, topic_thread_id=99))
        self.assertEqual(kwargs_sg, {})  # Thread ID must NOT be passed to non-forum supergroup

        # 4. Forum-Enabled Supergroup
        mock_forum_chat = MagicMock()
        mock_forum_chat.id = -1003333333333
        mock_forum_chat.type = "supergroup"
        mock_forum_chat.is_forum = True
        mock_forum_chat.title = "Forum Supergroup"
        fut_forum = self.loop.create_future()
        fut_forum.set_result(mock_forum_chat)
        mock_client.get_chat = MagicMock(return_value=fut_forum)
        mock_member = MagicMock()
        mock_member.privileges = MagicMock(can_manage_topics=True, can_post_messages=True)
        fut_mem = self.loop.create_future()
        fut_mem.set_result(mock_member)
        mock_client.get_chat_member = MagicMock(return_value=fut_mem)

        _DESTINATION_CACHE.clear()
        dest_forum = self.run_async(resolve_destination(mock_client, -1003333333333))
        self.assertEqual(dest_forum["type"], "supergroup")
        self.assertTrue(dest_forum["is_forum"])

        kwargs_forum = self.run_async(build_send_kwargs(mock_client, -1003333333333, topic_thread_id=456))
        self.assertEqual(kwargs_forum, {"message_thread_id": 456})  # Thread ID passed ONLY here

        kwargs_forum_none = self.run_async(build_send_kwargs(mock_client, -1003333333333, topic_thread_id=None))
        self.assertEqual(kwargs_forum_none, {})

        # 5. Channel
        mock_chan_chat = MagicMock()
        mock_chan_chat.id = -1004444444444
        mock_chan_chat.type = "channel"
        mock_chan_chat.is_forum = False
        mock_chan_chat.title = "Broadcast Channel"
        fut_chan = self.loop.create_future()
        fut_chan.set_result(mock_chan_chat)
        mock_client.get_chat = MagicMock(return_value=fut_chan)

        _DESTINATION_CACHE.clear()
        dest_chan = self.run_async(resolve_destination(mock_client, -1004444444444))
        self.assertEqual(dest_chan["type"], "channel")
        self.assertFalse(dest_chan["is_forum"])

        kwargs_chan = self.run_async(build_send_kwargs(mock_client, -1004444444444, topic_thread_id=99))
        self.assertEqual(kwargs_chan, {})  # Thread ID must NOT be passed to broadcast channel

    def test_multi_job_cancellation_isolation(self):
        """
        Verify that cancelling or pausing Job A does NOT affect or cancel Job B.
        Each job has its own cancel flag, cancel reason, and asyncio task.
        """
        user_1 = 1110001
        user_2 = 2220002

        job_A = job_manager.create_job(
            user_id=user_1,
            chat_id=user_1,
            channel_id=user_1,
            source_filename="course_a.txt",
            raw_content="L1 : https://example.com/a1\nL2 : https://example.com/a2",
            total_items=2,
            batch_name="Course A"
        )
        job_B = job_manager.create_job(
            user_id=user_2,
            chat_id=user_2,
            channel_id=user_2,
            source_filename="course_b.txt",
            raw_content="L1 : https://example.com/b1\nL2 : https://example.com/b2",
            total_items=2,
            batch_name="Course B"
        )

        job_B_completed = False

        async def worker_A(job, client, manager):
            for _ in range(50):
                if manager.cancel_flags.get(job.job_id, False):
                    raise asyncio.CancelledError()
                await asyncio.sleep(0.05)

        async def worker_B(job, client, manager):
            nonlocal job_B_completed
            for _ in range(5):
                if manager.cancel_flags.get(job.job_id, False):
                    raise asyncio.CancelledError()
                await asyncio.sleep(0.02)
            job_B_completed = True

        async def run_scenario():
            mock_client = MagicMock()
            task_A = await job_manager.launch_job(job_A, mock_client, worker_A)
            task_B = await job_manager.launch_job(job_B, mock_client, worker_B)

            # Allow both jobs to start running
            await asyncio.sleep(0.03)

            # Explicitly pause Job A
            success, msg, paused_job = job_manager.pause_job(user_1, job_id=job_A.job_id, reason="User /stop command on Job A")
            self.assertTrue(success)

            # Verify Job B cancel flag is FALSE
            self.assertFalse(job_manager.cancel_flags.get(job_B.job_id, False))
            self.assertNotIn(job_B.job_id, job_manager.cancel_reasons)

            # Wait for tasks
            await asyncio.gather(task_A, task_B, return_exceptions=True)

        self.run_async(run_scenario())

        # Job B MUST have completed successfully
        self.assertTrue(job_B_completed, "Job B should have completed uninterrupted!")

        # Verify DB states
        db_job_A = db.get_job(job_A.job_id)
        db_job_B = db.get_job(job_B.job_id)
        self.assertEqual(db_job_A["status"], "PAUSED")
        self.assertEqual(db_job_B["status"], "COMPLETED")

        # Cleanup
        db.delete_job(job_A.job_id)
        db.delete_job(job_B.job_id)


if __name__ == "__main__":
    unittest.main()
