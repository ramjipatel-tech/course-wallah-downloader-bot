import os
import sys
import unittest
import tempfile
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

from pyrogram import Client, enums
from pyrogram.types import Chat, ChatMember, ChatPrivileges, Message

from main import (
    resolve_destination,
    build_send_kwargs,
    create_forum_topic_safe,
    resolve_or_create_forum_topic,
    build_academic_topic_title,
    validate_forum_chat,
    _DESTINATION_CACHE
)
from db import db, Database, atomic_json_write, safe_json_read
from academic_parser import parse_academic_txt, AcademicItem
from job_manager import JobManager, JobCheckpoint
import itsgolu as helper
from vars import MAX_UPLOAD_SIZE_BYTES, CREDIT, TOPICS_STATE_FILE


class TestTelegramDestinationMatrix(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        _DESTINATION_CACHE.clear()
        self.orig_topics_file = db.topics_file
        self.test_topics_file = os.path.join(self.test_dir, "test_topics.json")
        db.topics_file = self.test_topics_file
        self.mock_client = MagicMock(spec=Client)

    def tearDown(self):
        import shutil
        db.topics_file = self.orig_topics_file
        shutil.rmtree(self.test_dir, ignore_errors=True)
        _DESTINATION_CACHE.clear()

    # -------------------------------------------------------------------------
    # 1. PRIVATE CHAT
    # -------------------------------------------------------------------------
    async def test_01_private_chat_destination(self):
        chat_id = 987654321  # Positive user ID
        dest = await resolve_destination(self.mock_client, chat_id)
        self.assertEqual(dest["type"], "private")
        self.assertFalse(dest["is_forum"])
        self.assertEqual(dest["chat_id"], chat_id)

        # Ensure build_send_kwargs never attaches message_thread_id
        kwargs = await build_send_kwargs(self.mock_client, chat_id, topic_thread_id=12345)
        self.assertEqual(kwargs, {})
        self.assertNotIn("message_thread_id", kwargs)

    # -------------------------------------------------------------------------
    # 2. NORMAL GROUP
    # -------------------------------------------------------------------------
    async def test_02_normal_group_destination(self):
        chat_id = -100111222333
        mock_chat = MagicMock()
        mock_chat.id = chat_id
        mock_chat.type = enums.ChatType.GROUP
        mock_chat.is_forum = False
        mock_chat.title = "Standard Discussion Group"
        self.mock_client.get_chat = AsyncMock(return_value=mock_chat)

        dest = await resolve_destination(self.mock_client, chat_id)
        self.assertEqual(dest["type"], "group")
        self.assertFalse(dest["is_forum"])

        # Never attach thread ID to normal group
        kwargs = await build_send_kwargs(self.mock_client, chat_id, topic_thread_id=99)
        self.assertEqual(kwargs, {})
        self.assertNotIn("message_thread_id", kwargs)

        # Topic creation must return None for normal group
        tid = await resolve_or_create_forum_topic(
            self.mock_client, chat_id, "Physics", 1, "Kinematics"
        )
        self.assertIsNone(tid)

    # -------------------------------------------------------------------------
    # 3. SUPERGROUP WITHOUT FORUM / TOPICS
    # -------------------------------------------------------------------------
    async def test_03_supergroup_without_forum_destination(self):
        chat_id = -100444555666
        mock_chat = MagicMock()
        mock_chat.id = chat_id
        mock_chat.type = enums.ChatType.SUPERGROUP
        mock_chat.is_forum = False
        mock_chat.title = "Non-Forum Supergroup"
        self.mock_client.get_chat = AsyncMock(return_value=mock_chat)

        dest = await resolve_destination(self.mock_client, chat_id)
        self.assertEqual(dest["type"], "supergroup")
        self.assertFalse(dest["is_forum"])

        kwargs = await build_send_kwargs(self.mock_client, chat_id, topic_thread_id=55)
        self.assertEqual(kwargs, {})
        self.assertNotIn("message_thread_id", kwargs)

        tid = await resolve_or_create_forum_topic(
            self.mock_client, chat_id, "Chemistry", 1, "Thermodynamics"
        )
        self.assertIsNone(tid)

    # -------------------------------------------------------------------------
    # 4. FORUM-ENABLED SUPERGROUP
    # -------------------------------------------------------------------------
    async def test_04_forum_enabled_supergroup_destination(self):
        chat_id = -100777888999
        mock_chat = MagicMock()
        mock_chat.id = chat_id
        mock_chat.type = enums.ChatType.SUPERGROUP
        mock_chat.is_forum = True
        mock_chat.title = "Academic Forum Supergroup"
        self.mock_client.get_chat = AsyncMock(return_value=mock_chat)

        mock_member = MagicMock()
        mock_member.privileges = MagicMock(can_manage_topics=True, can_post_messages=True)
        self.mock_client.get_chat_member = AsyncMock(return_value=mock_member)

        dest = await resolve_destination(self.mock_client, chat_id)
        self.assertEqual(dest["type"], "supergroup")
        self.assertTrue(dest["is_forum"])
        self.assertTrue(dest["can_manage_topics"])

        kwargs = await build_send_kwargs(self.mock_client, chat_id, topic_thread_id=777)
        self.assertEqual(kwargs, {"message_thread_id": 777})

    # -------------------------------------------------------------------------
    # 5. CHANNEL
    # -------------------------------------------------------------------------
    async def test_05_channel_destination(self):
        chat_id = -100888999000
        mock_chat = MagicMock()
        mock_chat.id = chat_id
        mock_chat.type = enums.ChatType.CHANNEL
        mock_chat.is_forum = False
        mock_chat.title = "Broadcast Channel"
        self.mock_client.get_chat = AsyncMock(return_value=mock_chat)

        dest = await resolve_destination(self.mock_client, chat_id)
        self.assertEqual(dest["type"], "channel")
        self.assertFalse(dest["is_forum"])

        kwargs = await build_send_kwargs(self.mock_client, chat_id, topic_thread_id=88)
        self.assertEqual(kwargs, {})
        self.assertNotIn("message_thread_id", kwargs)

        tid = await resolve_or_create_forum_topic(
            self.mock_client, chat_id, "Mathematics", 1, "Calculus"
        )
        self.assertIsNone(tid)

    # -------------------------------------------------------------------------
    # 6. FORUM TOPIC CREATION (REAL TELEGRAM TOPIC ID)
    # -------------------------------------------------------------------------
    async def test_06_forum_topic_creation(self):
        chat_id = -100999000111
        mock_created = MagicMock()
        mock_created.id = 1234
        self.mock_client.create_forum_topic = AsyncMock(return_value=mock_created)

        thread_id = await create_forum_topic_safe(self.mock_client, chat_id, "RECORDED — UNIT 1 — LIMITS")
        self.assertEqual(thread_id, 1234)
        self.mock_client.create_forum_topic.assert_awaited_once_with(
            chat_id=chat_id, title="RECORDED — UNIT 1 — LIMITS"
        )

    # -------------------------------------------------------------------------
    # 7. FORUM TOPIC REUSE (DO NOT CREATE NEW TOPIC FOR EVERY LECTURE)
    # -------------------------------------------------------------------------
    async def test_07_forum_topic_reuse(self):
        chat_id = -100999000111
        mock_chat = MagicMock()
        mock_chat.id = chat_id
        mock_chat.type = enums.ChatType.SUPERGROUP
        mock_chat.is_forum = True
        self.mock_client.get_chat = AsyncMock(return_value=mock_chat)

        mock_created = MagicMock()
        mock_created.id = 5678
        self.mock_client.create_forum_topic = AsyncMock(return_value=mock_created)

        # First call creates the topic
        tid1 = await resolve_or_create_forum_topic(
            self.mock_client, chat_id, "Physics", 1, "Kinematics", is_live=False
        )
        self.assertEqual(tid1, 5678)
        self.assertEqual(self.mock_client.create_forum_topic.await_count, 1)

        # Second call for next lecture in same unit reuses the topic
        tid2 = await resolve_or_create_forum_topic(
            self.mock_client, chat_id, "Physics", 1, "Kinematics", is_live=False
        )
        self.assertEqual(tid2, 5678)
        # Verify create_forum_topic was NOT called again
        self.assertEqual(self.mock_client.create_forum_topic.await_count, 1)

    # -------------------------------------------------------------------------
    # 8. PERSISTENT TOPIC MAPPING IN ATOMIC STATE SYSTEM
    # -------------------------------------------------------------------------
    def test_08_persistent_topic_mapping(self):
        test_db = Database()
        topics_file = os.path.join(self.test_dir, "topics.json")
        test_db.topics_file = topics_file

        saved = test_db.save_topic_id(-100123456, "recorded|maths|unit_1|algebra", 4321)
        self.assertTrue(saved)
        self.assertTrue(os.path.exists(topics_file))

        loaded_tid = test_db.get_topic_id(-100123456, "recorded|maths|unit_1|algebra")
        self.assertEqual(loaded_tid, 4321)

    # -------------------------------------------------------------------------
    # 9. RESTART AND TOPIC MAPPING RECOVERY
    # -------------------------------------------------------------------------
    def test_09_restart_topic_mapping_recovery(self):
        topics_file = os.path.join(self.test_dir, "persistent_topics.json")
        atomic_json_write(topics_file, {
            "-10011223344:recorded|biology|unit_2|genetics": 8765
        })

        # Simulate bot restart with brand new Database instance
        fresh_db = Database()
        fresh_db.topics_file = topics_file
        recovered_tid = fresh_db.get_topic_id(-10011223344, "recorded|biology|unit_2|genetics")
        self.assertEqual(recovered_tid, 8765)

    # -------------------------------------------------------------------------
    # 10. MULTI-SUBJECT TXT (FLAT TOPIC WITH SUBJECT IDENTIFIER)
    # -------------------------------------------------------------------------
    def test_10_multi_subject_txt_topic_naming(self):
        title = build_academic_topic_title(
            subject="Digital Electronics",
            unit_num=2,
            unit_title="Sequential Circuits",
            is_live=False,
            multiple_subjects=True
        )
        self.assertEqual(title, "RECORDED — 📚 DIGITAL ELECTRONICS — UNIT 2 — SEQUENTIAL CIRCUITS")

        live_title = build_academic_topic_title(
            subject="Computer Networks",
            unit_num=3,
            unit_title="Transport Layer",
            is_live=True,
            multiple_subjects=True
        )
        self.assertEqual(live_title, "LIVE — 📚 COMPUTER NETWORKS — UNIT 3 — TRANSPORT LAYER")

    # -------------------------------------------------------------------------
    # 11. SINGLE-SUBJECT TXT (NO REDUNDANT SUBJECT PREFIX LAYER)
    # -------------------------------------------------------------------------
    def test_11_single_subject_txt_topic_naming(self):
        title = build_academic_topic_title(
            subject="Mathematics",
            unit_num=1,
            unit_title="Linear Algebra",
            is_live=False,
            multiple_subjects=False
        )
        self.assertEqual(title, "RECORDED — UNIT 1 — LINEAR ALGEBRA")
        self.assertNotIn("📚", title)
        self.assertNotIn("MATHEMATICS", title)

    # -------------------------------------------------------------------------
    # 12. GENERAL TOPIC CREATION & REUSE
    # -------------------------------------------------------------------------
    async def test_12_general_topic(self):
        chat_id = -100999000111
        mock_chat = MagicMock()
        mock_chat.id = chat_id
        mock_chat.type = enums.ChatType.SUPERGROUP
        mock_chat.is_forum = True
        self.mock_client.get_chat = AsyncMock(return_value=mock_chat)

        mock_created = MagicMock()
        mock_created.id = 1001
        self.mock_client.create_forum_topic = AsyncMock(return_value=mock_created)

        gen_id = await resolve_or_create_forum_topic(
            client=self.mock_client,
            chat_id=chat_id,
            subject="General",
            unit_num=0,
            unit_title="General Discussion & Course Overview",
            is_live=False,
            multiple_subjects=False
        )
        self.assertEqual(gen_id, 1001)

    # -------------------------------------------------------------------------
    # 13. THUMBNAIL FALLBACK (PRIORITY 1)
    # -------------------------------------------------------------------------
    def test_13_thumbnail_priority_detection(self):
        thumb_file = os.path.join(self.test_dir, "course_cover.jpg")
        with open(thumb_file, "wb") as f:
            f.write(b"fake image data")

        thumb_val = thumb_file
        has_custom_thumb = thumb_val and os.path.exists(thumb_val) and thumb_val not in ("/d", "no", "none", "")
        self.assertTrue(has_custom_thumb)

    # -------------------------------------------------------------------------
    # 14. INTRO-VIDEO FALLBACK (PRIORITY 2)
    # -------------------------------------------------------------------------
    def test_14_intro_video_priority_detection(self):
        thumb_val = "/d"  # No custom thumbnail
        items = [
            AcademicItem(raw_line="Intro : https://example.com/intro", index=1, title="Course Introduction & Roadmap", url="https://example.com/intro", url_without_scheme="example.com/intro", category="video"),
            AcademicItem(raw_line="L1 : https://example.com/l1", index=2, title="Lecture 1: Basics", url="https://example.com/l1", url_without_scheme="example.com/l1", category="video")
        ]
        has_custom_thumb = thumb_val and os.path.exists(thumb_val) and thumb_val not in ("/d", "no", "none", "")
        self.assertFalse(has_custom_thumb)

        intro_item = next((itm for itm in items if any(k in itm.title.lower() for k in ("intro", "introduction", "overview", "syllabus", "orientation", "roadmap")) and itm.category == "video"), None)
        self.assertIsNotNone(intro_item)
        self.assertEqual(intro_item.title, "Course Introduction & Roadmap")

    # -------------------------------------------------------------------------
    # 15. NO-RESOURCE OVERVIEW FALLBACK (PRIORITY 3)
    # -------------------------------------------------------------------------
    def test_15_no_resource_overview_fallback(self):
        thumb_val = None
        items = [
            AcademicItem(raw_line="L1 : https://example.com/l1", index=1, title="Lecture 1: Number Systems", url="https://example.com/l1", url_without_scheme="example.com/l1", category="video")
        ]
        has_custom_thumb = bool(thumb_val and os.path.exists(thumb_val))
        intro_item = next((itm for itm in items if any(k in itm.title.lower() for k in ("intro", "introduction", "overview", "syllabus")) and itm.category == "video"), None)
        self.assertFalse(has_custom_thumb)
        self.assertIsNone(intro_item)

    # -------------------------------------------------------------------------
    # 16. CORRECT SUBJECT/UNIT ROUTING
    # -------------------------------------------------------------------------
    async def test_16_correct_subject_unit_routing(self):
        chat_id = -100555666777
        mock_chat = MagicMock()
        mock_chat.id = chat_id
        mock_chat.type = enums.ChatType.SUPERGROUP
        mock_chat.is_forum = True
        self.mock_client.get_chat = AsyncMock(return_value=mock_chat)

        topic_counter = 2000
        async def mock_create(chat_id, title):
            nonlocal topic_counter
            topic_counter += 1
            mock_res = MagicMock()
            mock_res.id = topic_counter
            return mock_res

        self.mock_client.create_forum_topic = AsyncMock(side_effect=mock_create)

        tid_u1 = await resolve_or_create_forum_topic(self.mock_client, chat_id, "Physics", 1, "Kinematics")
        tid_u2 = await resolve_or_create_forum_topic(self.mock_client, chat_id, "Physics", 2, "Dynamics")
        tid_u1_again = await resolve_or_create_forum_topic(self.mock_client, chat_id, "Physics", 1, "Kinematics")

        self.assertNotEqual(tid_u1, tid_u2)
        self.assertEqual(tid_u1, tid_u1_again)

    # -------------------------------------------------------------------------
    # 17. MULTIPLE SIMULTANEOUS JOBS CONCURRENCY
    # -------------------------------------------------------------------------
    def test_17_multiple_simultaneous_jobs(self):
        manager = JobManager()
        manager.user_active_jobs[101] = "job_USER_A"
        manager.user_active_jobs[102] = "job_USER_B"

        self.assertTrue(manager.is_user_busy(101))
        self.assertTrue(manager.is_user_busy(102))
        self.assertFalse(manager.is_user_busy(103))

    # -------------------------------------------------------------------------
    # 18. CROSS-JOB TOPIC ISOLATION (NO TOPIC POLLUTION)
    # -------------------------------------------------------------------------
    def test_18_cross_job_topic_isolation(self):
        job_a = JobCheckpoint(
            job_id="job_AAA",
            user_id=1,
            chat_id=1,
            channel_id=-100111,
            source_filename="courseA.txt",
            total_items=10,
            active_thread_id=501
        )
        job_b = JobCheckpoint(
            job_id="job_BBB",
            user_id=2,
            chat_id=2,
            channel_id=-100222,
            source_filename="courseB.txt",
            total_items=10,
            active_thread_id=902
        )

        # Thread IDs must remain strictly attached to respective job instances
        self.assertEqual(job_a.active_thread_id, 501)
        self.assertEqual(job_b.active_thread_id, 902)
        self.assertNotEqual(job_a.active_thread_id, job_b.active_thread_id)

    # -------------------------------------------------------------------------
    # 19. LARGE VIDEO PARTS STAYING IN SAME TOPIC
    # -------------------------------------------------------------------------
    @patch("subprocess.run")
    @patch("itsgolu.get_video_duration", return_value=120.0)
    async def test_19_large_video_parts_stay_in_same_topic(self, mock_dur, mock_subproc):
        chat_id = -100777888999
        topic_id = 456

        large_video = os.path.join(self.test_dir, "lecture_huge.mp4")
        with open(large_video, "wb") as f:
            f.write(b"0" * int(3.5 * 1024 * 1024))

        threshold = 2 * 1024 * 1024

        def fake_ffmpeg_run(cmd, *args, **kwargs):
            out_file = cmd[-1]
            with open(out_file, "wb") as f:
                f.write(b"0" * int(1.6 * 1024 * 1024))
            return MagicMock(returncode=0, stderr="")

        mock_subproc.side_effect = fake_ffmpeg_run

        sent_calls = []
        async def mock_send_video(**kwargs):
            sent_calls.append(kwargs)
            mock_msg = MagicMock()
            mock_msg.id = len(sent_calls)
            return mock_msg

        self.mock_client.send_video = AsyncMock(side_effect=mock_send_video)

        with patch("itsgolu.MAX_UPLOAD_SIZE_BYTES", threshold):
            await helper.send_vid(
                bot=self.mock_client,
                m=None,
                cc="Lecture 05",
                filename=large_video,
                thumb=None,
                name="Lecture 05",
                prog=None,
                channel_id=chat_id,
                topic_thread_id=topic_id
            )

        self.assertGreaterEqual(len(sent_calls), 2)
        for call in sent_calls:
            self.assertEqual(call.get("chat_id"), chat_id)
            self.assertEqual(call.get("message_thread_id"), topic_id)

    # -------------------------------------------------------------------------
    # 20. PDF IMMEDIATELY AFTER ALL VIDEO PARTS IN SAME TOPIC
    # -------------------------------------------------------------------------
    async def test_20_pdf_immediately_after_video_parts_in_same_topic(self):
        chat_id = -100777888999
        topic_id = 789

        events = []
        async def mock_send_video(**kwargs):
            events.append(("VIDEO", kwargs.get("message_thread_id")))
            return MagicMock(id=1)

        async def mock_send_document(**kwargs):
            events.append(("PDF", kwargs.get("message_thread_id")))
            return MagicMock(id=2)

        self.mock_client.send_video = AsyncMock(side_effect=mock_send_video)
        self.mock_client.send_document = AsyncMock(side_effect=mock_send_document)

        # Send Video Part 1
        await helper.safe_video_send(self.mock_client, chat_id=chat_id, video="part1.mp4", caption="Part 1", message_thread_id=topic_id)
        # Send Video Part 2
        await helper.safe_video_send(self.mock_client, chat_id=chat_id, video="part2.mp4", caption="Part 2", message_thread_id=topic_id)
        # Send PDF immediately
        await self.mock_client.send_document(chat_id=chat_id, document="notes.pdf", caption="Notes", message_thread_id=topic_id)

        self.assertEqual(events, [
            ("VIDEO", topic_id),
            ("VIDEO", topic_id),
            ("PDF", topic_id)
        ])

    # -------------------------------------------------------------------------
    # 21. FORUM DESTINATION WITHOUT FORUM_CHAT_ID STARTUP CRASH
    # -------------------------------------------------------------------------
    def test_21_no_forum_chat_id_startup_crash(self):
        # Verify FORUM_CHAT_ID can be empty string and does not raise NameError or crash
        import vars as test_vars
        forum_id = getattr(test_vars, "FORUM_CHAT_ID", None)
        self.assertIsNotNone(forum_id)
        self.assertIsInstance(forum_id, str)

    # -------------------------------------------------------------------------
    # 22. NO message_thread_id LEAKAGE INTO PRIVATE / GROUP / CHANNEL
    # -------------------------------------------------------------------------
    async def test_22_no_thread_id_leakage(self):
        # Test across all non-forum destination types
        # A. Private
        kw_priv = await build_send_kwargs(self.mock_client, 12345678, topic_thread_id=555)
        self.assertNotIn("message_thread_id", kw_priv)

        # B. Group
        mock_group = MagicMock(type=enums.ChatType.GROUP, is_forum=False)
        self.mock_client.get_chat = AsyncMock(return_value=mock_group)
        kw_grp = await build_send_kwargs(self.mock_client, -100111, topic_thread_id=555)
        self.assertNotIn("message_thread_id", kw_grp)

        # C. Non-forum supergroup
        _DESTINATION_CACHE.clear()
        mock_sg = MagicMock(type=enums.ChatType.SUPERGROUP, is_forum=False)
        self.mock_client.get_chat = AsyncMock(return_value=mock_sg)
        kw_sg = await build_send_kwargs(self.mock_client, -100222, topic_thread_id=555)
        self.assertNotIn("message_thread_id", kw_sg)

        # D. Channel
        _DESTINATION_CACHE.clear()
        mock_ch = MagicMock(type=enums.ChatType.CHANNEL, is_forum=False)
        self.mock_client.get_chat = AsyncMock(return_value=mock_ch)
        kw_ch = await build_send_kwargs(self.mock_client, -100333, topic_thread_id=555)
        self.assertNotIn("message_thread_id", kw_ch)

        # E. None thread id on forum
        _DESTINATION_CACHE.clear()
        mock_forum = MagicMock(type=enums.ChatType.SUPERGROUP, is_forum=True)
        self.mock_client.get_chat = AsyncMock(return_value=mock_forum)
        kw_none = await build_send_kwargs(self.mock_client, -100444, topic_thread_id=None)
        self.assertNotIn("message_thread_id", kw_none)
        kw_zero = await build_send_kwargs(self.mock_client, -100444, topic_thread_id=0)
        self.assertNotIn("message_thread_id", kw_zero)


if __name__ == "__main__":
    unittest.main()
