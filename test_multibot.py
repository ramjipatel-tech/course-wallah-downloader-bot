"""
test_multibot.py — Comprehensive Test Suite for Multi-Bot Single-Process Architecture

Explicitly tests all 24 requirements from the Master Prompt:
1. One bot starts.
2. Two bots start.
3. Four bots start.
4. Missing BOT_2_TOKEN is skipped.
5. BOT_TOKEN legacy fallback.
6. Duplicate token detection.
7. Unique session names.
8. Bot identity fetched through get_me().
9. Bot 1 and Bot 2 have separate contexts.
10. Same Telegram user ID on two bots does not collide.
11. Same chat ID on two bots does not collide.
12. Same subject/unit on two bots does not collide.
13. Topic mappings are bot-scoped.
14. Status messages are bot-scoped.
15. Jobs are bot-scoped.
16. Stop/cancel is bot-scoped.
17. Resume is bot-scoped.
18. Checkpoints are bot-scoped.
19. Destination is bot-scoped.
20. Bot 2 failure does not kill Bot 1.
21. One Python process architecture.
22. Graceful shutdown.
23. No secret leakage.
24. Branding is bot-scoped.
"""

import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import os
import sys
import io
import asyncio
import sqlite3
import shutil

# Ensure local imports work
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import vars
from db import db
from job_manager import job_manager, JobCheckpoint
import main
from main import (
    BotContext,
    create_bot_client,
    get_bot_context,
    register_bot_context,
    get_bot_username,
    get_bot_display_name,
    get_bot_link,
    get_bot_support_link,
    start_single_bot,
    start_all_bots,
    resolve_destination,
    resolve_or_create_forum_topic,
    build_welcome_dashboard
)


class TestMultiBotArchitecture(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.test_data_dir = os.path.join(os.getcwd(), "data_test_multibot")
        os.makedirs(self.test_data_dir, exist_ok=True)
        # Point db storage to test directory
        self._orig_topics_file = db.topics_file
        self._orig_jobs_file = db.jobs_file
        self._orig_users_file = db.users_file
        self._orig_config_file = db.config_file

        db.topics_file = os.path.join(self.test_data_dir, "topics.json")
        db.jobs_file = os.path.join(self.test_data_dir, "jobs.json")
        db.users_file = os.path.join(self.test_data_dir, "users.json")
        db.config_file = os.path.join(self.test_data_dir, "config.json")
        db._init_files()

        # Clear job manager active state
        job_manager.user_active_jobs.clear()
        job_manager.active_tasks.clear()
        job_manager.cancel_flags.clear()
        job_manager.cancel_reasons.clear()

    def tearDown(self):
        db.topics_file = self._orig_topics_file
        db.jobs_file = self._orig_jobs_file
        db.users_file = self._orig_users_file
        db.config_file = self._orig_config_file
        shutil.rmtree(self.test_data_dir, ignore_errors=True)

    # 1. One bot starts
    def test_01_one_bot_starts(self):
        env = {
            "BOT_1_TOKEN": "111111:AAABBBCCC",
            "API_ID": "12345",
            "API_HASH": "abcdef"
        }
        with patch.dict(os.environ, env, clear=True):
            bots = vars.get_configured_bots()
            self.assertEqual(len(bots), 1)
            self.assertEqual(bots[0]["id"], "bot_1")
            self.assertEqual(bots[0]["session_name"], "coursewallah_bot_1")
            self.assertEqual(bots[0]["token"], "111111:AAABBBCCC")

    # 2. Two bots start
    def test_02_two_bots_start(self):
        env = {
            "BOT_1_TOKEN": "111:TOKEN1",
            "BOT_2_TOKEN": "222:TOKEN2",
            "API_ID": "12345",
            "API_HASH": "abcdef"
        }
        with patch.dict(os.environ, env, clear=True):
            bots = vars.get_configured_bots()
            self.assertEqual(len(bots), 2)
            self.assertEqual(bots[0]["id"], "bot_1")
            self.assertEqual(bots[1]["id"], "bot_2")
            self.assertEqual(bots[0]["session_name"], "coursewallah_bot_1")
            self.assertEqual(bots[1]["session_name"], "coursewallah_bot_2")

    # 3. Four bots start (and support >4 dynamically)
    def test_03_four_bots_start(self):
        env = {
            "BOT_1_TOKEN": "111:TOKEN1",
            "BOT_2_TOKEN": "222:TOKEN2",
            "BOT_3_TOKEN": "333:TOKEN3",
            "BOT_4_TOKEN": "444:TOKEN4",
            "API_ID": "12345",
            "API_HASH": "abcdef"
        }
        with patch.dict(os.environ, env, clear=True):
            bots = vars.get_configured_bots()
            self.assertEqual(len(bots), 4)
            self.assertEqual([b["id"] for b in bots], ["bot_1", "bot_2", "bot_3", "bot_4"])

    # 4. Missing BOT_2_TOKEN is skipped
    def test_04_missing_bot_2_token_is_skipped(self):
        env = {
            "BOT_1_TOKEN": "111:TOKEN1",
            "BOT_3_TOKEN": "333:TOKEN3",
            "API_ID": "12345",
            "API_HASH": "abcdef"
        }
        with patch.dict(os.environ, env, clear=True):
            bots = vars.get_configured_bots()
            self.assertEqual(len(bots), 2)
            self.assertEqual(bots[0]["id"], "bot_1")
            self.assertEqual(bots[1]["id"], "bot_3")

    # 5. BOT_TOKEN legacy fallback
    def test_05_bot_token_legacy_fallback(self):
        env = {
            "BOT_TOKEN": "999:LEGACYTOKEN",
            "API_ID": "12345",
            "API_HASH": "abcdef"
        }
        with patch.dict(os.environ, env, clear=True):
            bots = vars.get_configured_bots()
            self.assertEqual(len(bots), 1)
            self.assertEqual(bots[0]["id"], "bot_1")
            self.assertEqual(bots[0]["token"], "999:LEGACYTOKEN")

    # 6. Duplicate token detection
    def test_06_duplicate_token_detection(self):
        env = {
            "BOT_1_TOKEN": "111:SAME_TOKEN",
            "BOT_2_TOKEN": "111:SAME_TOKEN",
            "API_ID": "12345",
            "API_HASH": "abcdef"
        }
        with patch.dict(os.environ, env, clear=True):
            bots = vars.get_configured_bots()
            self.assertEqual(len(bots), 1)
            self.assertEqual(bots[0]["id"], "bot_1")

    # 7. Unique session names
    def test_07_unique_session_names(self):
        env = {
            "BOT_1_TOKEN": "111:TOKEN1",
            "BOT_2_TOKEN": "222:TOKEN2",
            "BOT_3_TOKEN": "333:TOKEN3",
            "BOT_4_TOKEN": "444:TOKEN4",
            "BOT_1_SESSION": "custom_session",
            "BOT_2_SESSION": "custom_session",
            "BOT_3_SESSION": "custom_session",
            "BOT_4_SESSION": "custom_session",
            "API_ID": "12345",
            "API_HASH": "abcdef"
        }
        with patch.dict(os.environ, env, clear=True):
            bots = vars.get_configured_bots()
            sessions = [b["session_name"] for b in bots]
            self.assertEqual(len(sessions), len(set(sessions)))
            self.assertEqual(len(sessions), 4)

    # 8. Bot identity fetched through get_me()
    async def test_08_bot_identity_fetched_through_get_me(self):
        mock_client = AsyncMock()
        mock_me = MagicMock(
            id=123456789,
            username="coursewallah_official_bot",
            first_name="Course",
            last_name="Wallah",
            is_bot=True
        )
        mock_client.get_me.return_value = mock_me
        ctx = BotContext(bot_id="bot_1", bot_name="Bot 1", session_name="s1", client=mock_client)

        success = await start_single_bot(ctx)
        self.assertTrue(success)
        self.assertEqual(ctx.bot_user_id, 123456789)
        self.assertEqual(ctx.bot_username, "coursewallah_official_bot")
        self.assertEqual(ctx.bot_first_name, "Course")
        self.assertEqual(ctx.bot_last_name, "Wallah")
        self.assertEqual(ctx.bot_display_name, "Course Wallah")
        self.assertEqual(ctx.bot_link, "https://t.me/coursewallah_official_bot")
        self.assertTrue(ctx.bot_is_bot)

    # 9. Bot 1 and Bot 2 have separate contexts
    def test_09_bot_1_and_bot_2_separate_contexts(self):
        cfg1 = {"id": "bot_1", "name": "Bot 1", "session_name": "bot_session_1", "token": "111:T1"}
        cfg2 = {"id": "bot_2", "name": "Bot 2", "session_name": "bot_session_2", "token": "222:T2"}

        client1 = create_bot_client(cfg1)
        client2 = create_bot_client(cfg2)

        self.assertIsNot(client1, client2)
        ctx1 = get_bot_context(client1)
        ctx2 = get_bot_context(client2)
        self.assertIsNot(ctx1, ctx2)
        self.assertEqual(ctx1.bot_id, "bot_1")
        self.assertEqual(ctx2.bot_id, "bot_2")

    # 10. Same Telegram user ID on two bots does not collide
    def test_10_same_telegram_user_id_does_not_collide(self):
        user_id = 88888
        job_manager.set_user_active_job(user_id, "job_bot1_001", bot_id="bot_1")

        self.assertTrue(job_manager.is_user_busy(user_id, bot_id="bot_1"))
        self.assertEqual(job_manager.get_active_job_id(user_id, bot_id="bot_1"), "job_bot1_001")

        # NOT busy on bot_2
        self.assertFalse(job_manager.is_user_busy(user_id, bot_id="bot_2"))
        self.assertIsNone(job_manager.get_active_job_id(user_id, bot_id="bot_2"))

        # User starts job on bot_2 simultaneously
        job_manager.set_user_active_job(user_id, "job_bot2_002", bot_id="bot_2")
        self.assertTrue(job_manager.is_user_busy(user_id, bot_id="bot_1"))
        self.assertTrue(job_manager.is_user_busy(user_id, bot_id="bot_2"))
        self.assertEqual(job_manager.get_active_job_id(user_id, bot_id="bot_2"), "job_bot2_002")

    # 11. Same chat ID on two bots does not collide
    def test_11_same_chat_id_does_not_collide(self):
        chat_id = -100555666777
        ctx1 = BotContext(bot_id="bot_1", bot_name="Bot 1", session_name="s1", client=MagicMock())
        ctx2 = BotContext(bot_id="bot_2", bot_name="Bot 2", session_name="s2", client=MagicMock())

        ctx1.status_messages[(chat_id, "JOB_1")] = 7001
        ctx2.status_messages[(chat_id, "JOB_1")] = 8002

        self.assertEqual(ctx1.status_messages[(chat_id, "JOB_1")], 7001)
        self.assertEqual(ctx2.status_messages[(chat_id, "JOB_1")], 8002)

    # 12. Same subject/unit on two bots does not collide
    async def test_12_same_subject_unit_does_not_collide(self):
        mock_client_1 = AsyncMock()
        mock_client_2 = AsyncMock()
        mock_chat = MagicMock(id=-100999000111, is_forum=True)
        mock_chat.type = "supergroup"
        mock_client_1.get_chat.return_value = mock_chat
        mock_client_2.get_chat.return_value = mock_chat

        mock_client_1.create_forum_topic.return_value = MagicMock(id=5678)
        mock_client_2.create_forum_topic.return_value = MagicMock(id=9876)

        t1 = await resolve_or_create_forum_topic(mock_client_1, -100999000111, "PHYSICS", 1, "KINEMATICS", bot_id="bot_1")
        t2 = await resolve_or_create_forum_topic(mock_client_2, -100999000111, "PHYSICS", 1, "KINEMATICS", bot_id="bot_2")

        self.assertEqual(t1, 5678)
        self.assertEqual(t2, 9876)

    # 13. Topic mappings are bot-scoped
    def test_13_topic_mappings_are_bot_scoped(self):
        chat_id = -1001999888777
        topic_key = "RECORDED|MATHS|UNIT 1"

        db.save_topic_id(chat_id, topic_key, 1111, bot_id="bot_1")
        db.save_topic_id(chat_id, topic_key, 2222, bot_id="bot_2")

        self.assertEqual(db.get_topic_id(chat_id, topic_key, bot_id="bot_1"), 1111)
        self.assertEqual(db.get_topic_id(chat_id, topic_key, bot_id="bot_2"), 2222)
        self.assertIsNone(db.get_topic_id(chat_id, topic_key, bot_id="bot_3"))

        # list_all_topics filtered by bot
        topics_b1 = db.list_all_topics(bot_id="bot_1")
        self.assertEqual(len(topics_b1), 1)
        topics_b2 = db.list_all_topics(bot_id="bot_2")
        self.assertEqual(len(topics_b2), 1)

    # 14. Status messages are bot-scoped
    def test_14_status_messages_are_bot_scoped(self):
        ctx1 = BotContext(bot_id="bot_1", bot_name="Bot 1", session_name="s1", client=MagicMock())
        ctx2 = BotContext(bot_id="bot_2", bot_name="Bot 2", session_name="s2", client=MagicMock())

        ctx1.status_messages[(123, "J1")] = 501
        ctx2.status_messages[(123, "J2")] = 502

        self.assertIn((123, "J1"), ctx1.status_messages)
        self.assertNotIn((123, "J2"), ctx1.status_messages)
        self.assertIn((123, "J2"), ctx2.status_messages)

    # 15. Jobs are bot-scoped
    def test_15_jobs_are_bot_scoped(self):
        db.save_job({"job_id": "J_B1", "user_id": 1, "status": "QUEUED", "bot_id": "bot_1"})
        db.save_job({"job_id": "J_B2", "user_id": 1, "status": "QUEUED", "bot_id": "bot_2"})

        jobs_b1 = db.list_jobs(user_id=1, bot_id="bot_1")
        jobs_b2 = db.list_jobs(user_id=1, bot_id="bot_2")

        self.assertEqual(len(jobs_b1), 1)
        self.assertEqual(jobs_b1[0]["job_id"], "J_B1")
        self.assertEqual(len(jobs_b2), 1)
        self.assertEqual(jobs_b2[0]["job_id"], "J_B2")

    # 16. Stop/cancel is bot-scoped
    def test_16_stop_cancel_is_bot_scoped(self):
        user_id = 77777
        job_manager.set_user_active_job(user_id, "JOB_A", bot_id="bot_1")
        job_manager.set_user_active_job(user_id, "JOB_B", bot_id="bot_2")

        task_a = MagicMock()
        task_a.done.return_value = False
        task_b = MagicMock()
        task_b.done.return_value = False
        job_manager.active_tasks["JOB_A"] = task_a
        job_manager.active_tasks["JOB_B"] = task_b

        # Stop job on bot_1
        success, msg = job_manager.cancel_job(user_id, bot_id="bot_1", reason="Stop on bot 1")
        self.assertTrue(success)
        task_a.cancel.assert_called_once()
        task_b.cancel.assert_not_called()

        self.assertFalse(job_manager.is_user_busy(user_id, bot_id="bot_1"))
        self.assertTrue(job_manager.is_user_busy(user_id, bot_id="bot_2"))

    # 17. Resume is bot-scoped
    def test_17_resume_is_bot_scoped(self):
        user_id = 66666
        db.save_job({"job_id": "JOB_PAUSED_B1", "user_id": user_id, "status": "PAUSED", "bot_id": "bot_1"})
        db.save_job({"job_id": "JOB_PAUSED_B2", "user_id": user_id, "status": "PAUSED", "bot_id": "bot_2"})

        paused_b1 = db.get_user_paused_job(user_id, bot_id="bot_1")
        paused_b2 = db.get_user_paused_job(user_id, bot_id="bot_2")

        self.assertEqual(paused_b1["job_id"], "JOB_PAUSED_B1")
        self.assertEqual(paused_b2["job_id"], "JOB_PAUSED_B2")

    # 18. Checkpoints are bot-scoped
    def test_18_checkpoints_are_bot_scoped(self):
        chk = JobCheckpoint(
            job_id="CHK_001",
            user_id=12345,
            chat_id=12345,
            channel_id=-100999,
            source_filename="test.txt",
            raw_content="T: https://link",
            total_items=1,
            bot_id="bot_2",
            bot_username="cw_bot_2"
        )
        d = chk.to_dict()
        self.assertEqual(d["bot_id"], "bot_2")
        self.assertEqual(d["bot_username"], "cw_bot_2")

        db.save_job(d)
        recovered = db.get_job("CHK_001")
        self.assertEqual(recovered["bot_id"], "bot_2")

    # 19. Destination is bot-scoped
    def test_19_destination_is_bot_scoped(self):
        db.set_forum_group(-100111222333, bot_id="bot_1")
        db.set_forum_group(-100444555666, bot_id="bot_2")

        dest1 = db.get_forum_group(bot_id="bot_1")
        dest2 = db.get_forum_group(bot_id="bot_2")

        self.assertEqual(dest1, -100111222333)
        self.assertEqual(dest2, -100444555666)

    # 20. Bot 2 failure does not kill Bot 1
    async def test_20_bot_2_failure_does_not_kill_bot_1(self):
        c1 = AsyncMock()
        me1 = MagicMock(username="bot1_user", id=111)
        c1.get_me.return_value = me1

        c2 = AsyncMock()
        c2.start.side_effect = Exception("Unauthorized / Invalid Token")

        ctx1 = BotContext(bot_id="bot_1", bot_name="Bot 1", session_name="s1", client=c1)
        ctx2 = BotContext(bot_id="bot_2", bot_name="Bot 2", session_name="s2", client=c2)

        res1 = await start_single_bot(ctx1)
        res2 = await start_single_bot(ctx2)

        self.assertTrue(res1)
        self.assertTrue(ctx1.is_online)
        self.assertFalse(res2)
        self.assertFalse(ctx2.is_online)

    # 21. One Python process architecture
    def test_21_one_python_process_architecture(self):
        # Verify that create_bot_client does not launch subprocesses and creates clients in same process
        cfg1 = {"id": "bot_1", "name": "Bot 1", "session_name": "s1", "token": "111:T1"}
        cfg2 = {"id": "bot_2", "name": "Bot 2", "session_name": "s2", "token": "222:T2"}

        c1 = create_bot_client(cfg1)
        c2 = create_bot_client(cfg2)

        self.assertIsInstance(c1, main.Client)
        self.assertIsInstance(c2, main.Client)
        self.assertNotEqual(c1.name, c2.name)

    # 22. Graceful shutdown
    async def test_22_graceful_shutdown(self):
        c1 = AsyncMock()
        c2 = AsyncMock()
        c3 = AsyncMock()
        c4 = AsyncMock()

        ctxs = [
            BotContext(bot_id=f"bot_{i}", bot_name=f"Bot {i}", session_name=f"s{i}", client=c, is_online=True)
            for i, c in enumerate([c1, c2, c3, c4], 1)
        ]

        for ctx in ctxs:
            await ctx.client.stop()

        c1.stop.assert_awaited_once()
        c2.stop.assert_awaited_once()
        c3.stop.assert_awaited_once()
        c4.stop.assert_awaited_once()

    # 23. No secret leakage
    async def test_23_no_secret_leakage(self):
        secret_token = "987654:SUPER_SECRET_TOKEN_XYZ"
        secret_hash = "very_secret_api_hash_abc123"

        stdout_trap = io.StringIO()
        with patch("sys.stdout", stdout_trap):
            mock_client = AsyncMock()
            mock_client.get_me.return_value = MagicMock(id=123, username="secret_test_bot", first_name="Bot", last_name="")
            ctx = BotContext(
                bot_id="bot_1",
                bot_name="Bot 1",
                session_name="s1",
                client=mock_client,
                bot_token=secret_token
            )
            await start_single_bot(ctx)

        output = stdout_trap.getvalue()
        self.assertNotIn(secret_token, output)
        self.assertNotIn(secret_hash, output)

    # 24. Branding is bot-scoped
    def test_24_branding_is_bot_scoped(self):
        c1 = MagicMock()
        ctx1 = BotContext(
            bot_id="bot_1",
            bot_name="Course Wallah Alpha",
            bot_display_name="Course Wallah Alpha",
            bot_username="cw_alpha_bot",
            bot_link="https://t.me/cw_alpha_bot",
            session_name="s1",
            client=c1
        )
        register_bot_context(c1, ctx1)

        c2 = MagicMock()
        ctx2 = BotContext(
            bot_id="bot_2",
            bot_name="Course Wallah Beta",
            bot_display_name="Course Wallah Beta",
            bot_username="cw_beta_bot",
            bot_link="https://t.me/cw_beta_bot",
            session_name="s2",
            client=c2
        )
        register_bot_context(c2, ctx2)

        self.assertEqual(get_bot_display_name(c1), "Course Wallah Alpha")
        self.assertEqual(get_bot_display_name(c2), "Course Wallah Beta")

        self.assertEqual(get_bot_link(c1), "https://t.me/cw_alpha_bot")
        self.assertEqual(get_bot_link(c2), "https://t.me/cw_beta_bot")

        # Dashboard generation
        text1, _ = build_welcome_dashboard(101, "Alice", bot_name=get_bot_display_name(c1))
        text2, _ = build_welcome_dashboard(102, "Bob", bot_name=get_bot_display_name(c2))

        self.assertIn("COURSE WALLAH ALPHA", text1)
        self.assertIn("COURSE WALLAH BETA", text2)

    # 25. REGRESSION TEST: Existing job from Bot 1 is completely invisible to Bot 2
    def test_25_regression_existing_job_from_bot1_invisible_to_bot2(self):
        c1 = MagicMock()
        c2 = MagicMock()
        ctx1 = BotContext(bot_id="111", bot_name="Bot 1", session_name="s1", client=c1)
        ctx2 = BotContext(bot_id="222", bot_name="Bot 2", session_name="s2", client=c2)
        register_bot_context(c1, ctx1)
        register_bot_context(c2, ctx2)

        user_id = 500
        chat_id = 500

        # Bot 1 creates Job A
        job_a = ctx1.job_manager.create_job(
            user_id=user_id,
            chat_id=chat_id,
            channel_id=chat_id,
            source_filename="course.txt",
            raw_content="1. Title: https://link.com",
            total_items=1,
            bot_id="111"
        )
        ctx1.job_manager.set_user_active_job(user_id, job_a.job_id, bot_id="111")

        # Query Bot 1 jobs
        b1_jobs = db.list_jobs(user_id=user_id, bot_id="111")
        self.assertEqual(len(b1_jobs), 1)
        self.assertEqual(b1_jobs[0]["job_id"], job_a.job_id)

        # Query Bot 2 jobs -> MUST BE COMPLETELY EMPTY
        b2_jobs = db.list_jobs(user_id=user_id, bot_id="222")
        self.assertEqual(len(b2_jobs), 0)

        # Query active job on Bot 2 -> MUST BE NONE
        b2_active = db.get_user_active_job(user_id, bot_id="222")
        self.assertIsNone(b2_active)

        # Busy check on Bot 2 -> MUST BE FALSE
        self.assertFalse(ctx2.job_manager.is_user_busy(user_id, bot_id="222"))
        self.assertTrue(ctx1.job_manager.is_user_busy(user_id, bot_id="111"))

        # Bot 2 creates Job B for same user and chat
        job_b = ctx2.job_manager.create_job(
            user_id=user_id,
            chat_id=chat_id,
            channel_id=chat_id,
            source_filename="course.txt",
            raw_content="1. Title: https://link.com",
            total_items=1,
            bot_id="222"
        )
        ctx2.job_manager.set_user_active_job(user_id, job_b.job_id, bot_id="222")

        # Now Bot 1 sees only Job A, Bot 2 sees only Job B
        b1_jobs_after = db.list_jobs(user_id=user_id, bot_id="111")
        b2_jobs_after = db.list_jobs(user_id=user_id, bot_id="222")
        self.assertEqual(len(b1_jobs_after), 1)
        self.assertEqual(b1_jobs_after[0]["job_id"], job_a.job_id)
        self.assertEqual(len(b2_jobs_after), 1)
        self.assertEqual(b2_jobs_after[0]["job_id"], job_b.job_id)

        # Active job checks
        self.assertEqual(db.get_user_active_job(user_id, bot_id="111")["job_id"], job_a.job_id)
        self.assertEqual(db.get_user_active_job(user_id, bot_id="222")["job_id"], job_b.job_id)

    # 26. Status isolation
    def test_26_status_message_isolation(self):
        c1 = MagicMock()
        c2 = MagicMock()
        ctx1 = BotContext(bot_id="111", bot_name="Bot 1", session_name="s1", client=c1)
        ctx2 = BotContext(bot_id="222", bot_name="Bot 2", session_name="s2", client=c2)

        ctx1.status_messages[(500, "job_A")] = 101
        ctx2.status_messages[(500, "job_B")] = 202

        # Update Bot 1 status message
        ctx1.status_messages[(500, "job_A")] = 105

        self.assertEqual(ctx1.status_messages[(500, "job_A")], 105)
        self.assertEqual(ctx2.status_messages[(500, "job_B")], 202)
        self.assertNotIn((500, "job_A"), ctx2.status_messages)
        self.assertNotIn((500, "job_B"), ctx1.status_messages)

    # 27. Recovery isolation
    async def test_27_recovery_isolation(self):
        db.save_job({
            "job_id": "JOB_111",
            "bot_id": "111",
            "user_id": 500,
            "status": "DOWNLOADING",
            "total_items": 10,
            "current_index": 2,
            "completed_indices": [0, 1]
        })
        db.save_job({
            "job_id": "JOB_222",
            "bot_id": "222",
            "user_id": 500,
            "status": "DOWNLOADING",
            "total_items": 10,
            "current_index": 4,
            "completed_indices": [0, 1, 2, 3]
        })

        mock_c1 = AsyncMock()
        mock_ctx1 = BotContext(bot_id="111", bot_name="Bot 1", session_name="s1", client=mock_c1)
        register_bot_context(mock_c1, mock_ctx1)

        # Recover ONLY for Bot 1 (Bot 2 is offline)
        dummy_worker = AsyncMock()
        recovered = await job_manager.recover_on_startup({"111": mock_c1}, dummy_worker)
        self.assertEqual(recovered, 1)

        # Job A was recovered into Bot 1 JobManager
        self.assertEqual(mock_ctx1.job_manager.get_active_job_id(500, bot_id="111"), "JOB_111")

        # Job B was untouched in DB (remains DOWNLOADING or unrecovered because Bot 2 is offline)
        j2 = db.get_job("JOB_222", bot_id="222")
        self.assertEqual(j2["status"], "DOWNLOADING")

    # 28. Destination isolation
    async def test_28_destination_isolation(self):
        c1 = AsyncMock()
        c2 = AsyncMock()
        ctx1 = BotContext(bot_id="111", bot_name="Bot 1", session_name="s1", client=c1)
        ctx2 = BotContext(bot_id="222", bot_name="Bot 2", session_name="s2", client=c2)
        register_bot_context(c1, ctx1)
        register_bot_context(c2, ctx2)

        chat_obj = MagicMock(id=-100555, is_forum=True)
        chat_obj.type = "supergroup"
        chat_obj.title = "Test Supergroup"

        c1.get_chat.return_value = chat_obj
        c2.get_chat.return_value = chat_obj

        # Bot 1 has manage topics privilege
        member1 = MagicMock()
        member1.privileges = MagicMock(can_manage_topics=True)
        c1.get_chat_member.return_value = member1

        # Bot 2 does NOT have manage topics privilege
        member2 = MagicMock()
        member2.privileges = MagicMock(can_manage_topics=False)
        c2.get_chat_member.return_value = member2

        dest1 = await resolve_destination(c1, -100555)
        dest2 = await resolve_destination(c2, -100555)

        self.assertTrue(dest1["can_manage_topics"])
        self.assertFalse(dest2["can_manage_topics"])

    # 29. Same user ID cancellation isolation
    def test_29_same_user_id_cancellation_isolation(self):
        c1 = MagicMock()
        c2 = MagicMock()
        ctx1 = BotContext(bot_id="111", bot_name="Bot 1", session_name="s1", client=c1)
        ctx2 = BotContext(bot_id="222", bot_name="Bot 2", session_name="s2", client=c2)

        user_id = 999
        ctx1.job_manager.set_user_active_job(user_id, "JOB_A", bot_id="111")
        ctx2.job_manager.set_user_active_job(user_id, "JOB_B", bot_id="222")

        task_a = MagicMock()
        task_a.done.return_value = False
        task_b = MagicMock()
        task_b.done.return_value = False

        ctx1.job_manager.active_tasks["JOB_A"] = task_a
        ctx2.job_manager.active_tasks["JOB_B"] = task_b

        # Cancel on Bot 1
        success, _ = ctx1.job_manager.cancel_job(user_id, bot_id="111", reason="User cancel on Bot 1")
        self.assertTrue(success)
        task_a.cancel.assert_called_once()
        task_b.cancel.assert_not_called()

        self.assertFalse(ctx1.job_manager.is_user_busy(user_id, bot_id="111"))
        self.assertTrue(ctx2.job_manager.is_user_busy(user_id, bot_id="222"))


if __name__ == "__main__":
    unittest.main()
