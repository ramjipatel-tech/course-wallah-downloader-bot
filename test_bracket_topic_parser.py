import os
import sys
import unittest
import tempfile
import shutil
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from pyrogram import enums

# Ensure root directory is in sys.path
sys.path.insert(0, os.path.abspath("."))

from vars import TEMP_DIR, PROJECT_ROOT, DATA_DIR
from utils import (
    MediaRouter,
    MediaType,
    is_spayee_url,
    is_direct_pdf_url,
    is_hls_url,
    is_youtube_url,
    is_encrypted_stream_url,
    is_direct_video_url
)
from db import Database, db
from job_manager import JobManager, JobCheckpoint
from academic_parser import (
    parse_academic_txt,
    parse_course_txt,
    detect_txt_format,
    normalize_title,
    AcademicCourse,
    AcademicUnit,
    AcademicItem
)
from structured_batch_parser import (
    parse_structured_batch_txt
)
from bracket_topic_parser import (
    parse_first_topic,
    sanitize_topic_name,
    is_bracket_topic_format,
    parse_bracket_topic_raw,
    parse_bracket_topic_txt,
    TxtResource
)
from main import (
    resolve_or_create_forum_topic,
    build_send_kwargs,
    create_forum_topic_safe,
    _DESTINATION_CACHE
)

SAMPLE_BRACKET_TXT = """[Linear Algebra] Lecture 1A - Why Study Linear Algebra : https://qcdn.spayee.in/vod/index.m3u8*648dea11fe203008b24f492175fe674d
[Linear Algebra] Lecture 1B - Linear Algebra for GATE & Interviews : https://example.com/video1b.m3u8
[Linear Algebra] Annotated Notes - Lecture 1A-1B : https://example.com/notes.pdf*AUTH_TOKEN_999
[Linear Algebra] Lecture 1C - Linearly Independent and Linearly Dependent : https://example.com/video1c.mp4
[Probability] Lecture 1A. Probability Definition, Sample Space and Events : https://youtu.be/dQw4w9WgXcQ
[Probability] Lecture 1B. Inclusion Exclusion Principle : https://example.com/prob1b.m3u8
[Probability] Annotated Notes Lecture 1 : https://cwmediabkt99.crwilladmin.com/notes_prob.pdf
[Linear Algebra] Lecture 2A - Matrix Operations : https://example.com/video2a.mp4
"""

SAMPLE_CAREERWILL_TXT = """───── BATCH DETAILS ──────
🌟 Batch : Maths Special
CONTENT:
[Practice Class] Class - 01 | Maths Practice: https://d3vlg4qjb80h8n.cloudfront.net/videos/ssc_gd_01.m3u8
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


class TestBracketTopicParserAndMasterPatch(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.orig_topics_file = db.topics_file
        self.test_topics_file = os.path.join(self.test_dir, "test_topics.json")
        db.topics_file = self.test_topics_file
        self.mock_client = MagicMock()
        self.mock_client.rnd_id = MagicMock(return_value=12345)
        _DESTINATION_CACHE.clear()

    def tearDown(self):
        db.topics_file = self.orig_topics_file
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)
        _DESTINATION_CACHE.clear()

    # -------------------------------------------------------------------------
    # 1. EXTRACT FIRST [] CORRECTLY
    # -------------------------------------------------------------------------
    def test_01_extract_first_bracket_correctly(self):
        line = "[Linear Algebra] Lecture 1A - Why Study Linear Algebra : https://qcdn.spayee.in/vod/index.m3u8"
        parsed = parse_first_topic(line)
        self.assertIsNotNone(parsed)
        topic, remainder = parsed
        self.assertEqual(topic, "Linear Algebra")
        self.assertEqual(remainder, "Lecture 1A - Why Study Linear Algebra : https://qcdn.spayee.in/vod/index.m3u8")

    # -------------------------------------------------------------------------
    # 2. EMPTY [] REJECTED
    # -------------------------------------------------------------------------
    def test_02_empty_bracket_rejected(self):
        self.assertIsNone(parse_first_topic("[] Lecture 1A : https://example.com/v.mp4"))
        self.assertIsNone(parse_first_topic("[   ] Lecture 1A : https://example.com/v.mp4"))
        self.assertIsNone(parse_first_topic("Lecture without bracket : https://example.com/v.mp4"))

        raw = parse_bracket_topic_raw("[] Empty Topic Lecture : https://example.com/v.mp4")
        self.assertEqual(len(raw), 0)

    # -------------------------------------------------------------------------
    # 3. WHITESPACE NORMALIZED
    # -------------------------------------------------------------------------
    def test_03_whitespace_normalized(self):
        line = "[   Linear    Algebra   ] Lecture 1A : https://example.com/v.mp4"
        parsed = parse_first_topic(line)
        self.assertIsNotNone(parsed)
        topic, _ = parsed
        self.assertEqual(topic, "Linear Algebra")

        sanitized = sanitize_topic_name("  Computer   Networks  \n\t  ")
        self.assertEqual(sanitized, "Computer Networks")

    # -------------------------------------------------------------------------
    # 4. URL CONTAINING ':' IS NOT BROKEN
    # -------------------------------------------------------------------------
    def test_04_url_containing_colon_not_broken(self):
        line = "[Operating Systems] Memory Management Part 1 : https://cdn.example.com:8443/vod/stream:1080p.m3u8?time=12:30:00"
        course = parse_bracket_topic_txt(line)
        self.assertEqual(len(course.all_items), 1)
        item = course.all_items[0]
        self.assertEqual(item.title, "Memory Management Part 1")
        self.assertEqual(item.url, "https://cdn.example.com:8443/vod/stream:1080p.m3u8?time=12:30:00")

    # -------------------------------------------------------------------------
    # 5. URL CONTAINING QUERY PARAMETERS IS PRESERVED
    # -------------------------------------------------------------------------
    def test_05_url_query_parameters_preserved(self):
        line = "[Theory of Computation] Turing Machines : https://d3vlg4qjb80h8n.cloudfront.net/master.m3u8?Key-Pair-Id=APKA1234&Signature=XYZ%2B987&Expires=1790000000"
        resources = parse_bracket_topic_raw(line)
        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0].url, "https://d3vlg4qjb80h8n.cloudfront.net/master.m3u8?Key-Pair-Id=APKA1234&Signature=XYZ%2B987&Expires=1790000000")

    # -------------------------------------------------------------------------
    # 6. M3U8*KEY IS CORRECTLY SEPARATED
    # -------------------------------------------------------------------------
    def test_06_m3u8_star_key_separated(self):
        line = "[Linear Algebra] Lecture 1A - Why Study Linear Algebra : https://qcdn.spayee.in/vod/index.m3u8*648dea11fe203008b24f492175fe674d"
        resources = parse_bracket_topic_raw(line)
        self.assertEqual(len(resources), 1)
        res = resources[0]
        self.assertEqual(res.topic, "Linear Algebra")
        self.assertEqual(res.title, "Lecture 1A - Why Study Linear Algebra")
        self.assertEqual(res.url, "https://qcdn.spayee.in/vod/index.m3u8")
        self.assertEqual(res.authorized_key, "648dea11fe203008b24f492175fe674d")
        self.assertEqual(res.media_type, "SPAYEE_HLS")

    # -------------------------------------------------------------------------
    # 7. PDF*TOKEN IS CORRECTLY HANDLED
    # -------------------------------------------------------------------------
    def test_07_pdf_star_token_handled(self):
        line = "[Linear Algebra] Annotated Notes - Lecture 1A-1B : https://example.com/notes.pdf*AUTH_TOKEN_XYZ"
        resources = parse_bracket_topic_raw(line)
        self.assertEqual(len(resources), 1)
        res = resources[0]
        self.assertEqual(res.topic, "Linear Algebra")
        self.assertEqual(res.title, "Annotated Notes - Lecture 1A-1B")
        self.assertEqual(res.url, "https://example.com/notes.pdf")
        self.assertEqual(res.authorized_key, "AUTH_TOKEN_XYZ")
        self.assertEqual(res.media_type, "DIRECT_PDF")

        course = parse_bracket_topic_txt(line)
        self.assertEqual(course.all_items[0].category, "pdf")

    # -------------------------------------------------------------------------
    # 8. SAME TOPIC CREATES ONLY ONE FORUM TOPIC
    # -------------------------------------------------------------------------
    async def test_08_same_topic_creates_only_one_forum_topic(self):
        chat_id = -100999888111
        mock_chat = MagicMock()
        mock_chat.id = chat_id
        mock_chat.type = enums.ChatType.SUPERGROUP
        mock_chat.is_forum = True
        self.mock_client.get_chat = AsyncMock(return_value=mock_chat)

        create_topic_call_count = 0
        async def mock_create(chat_id, title):
            nonlocal create_topic_call_count
            create_topic_call_count += 1
            mock_res = MagicMock()
            mock_res.id = 7001
            return mock_res

        self.mock_client.create_forum_topic = AsyncMock(side_effect=mock_create)

        # 100 entries for Linear Algebra
        hundred_entries = "\n".join([f"[Linear Algebra] Lecture {i} : https://example.com/v{i}.mp4" for i in range(1, 101)])
        course = parse_bracket_topic_txt(hundred_entries)

        # Only 1 AcademicUnit created for Linear Algebra
        self.assertEqual(len(course.units), 1)
        self.assertEqual(len(course.all_items), 100)

        # Simulate resolving topic for all 100 items
        unit = course.units[0]
        for item in course.all_items:
            tid = await resolve_or_create_forum_topic(
                client=self.mock_client,
                chat_id=chat_id,
                subject=unit.title,
                unit_num=unit.number,
                unit_title=unit.title,
                bot_id="bot_1",
                custom_title=unit.display_header,
                topic_key=f"bracket_topic:{normalize_title(unit.title)}"
            )
            self.assertEqual(tid, 7001)

        # Telegram create_forum_topic must be called EXACTLY ONCE
        self.assertEqual(create_topic_call_count, 1)

    # -------------------------------------------------------------------------
    # 9. TOPIC SWITCHING WORKS
    # -------------------------------------------------------------------------
    async def test_09_topic_switching_works(self):
        chat_id = -100999888222
        mock_chat = MagicMock()
        mock_chat.id = chat_id
        mock_chat.type = enums.ChatType.SUPERGROUP
        mock_chat.is_forum = True
        self.mock_client.get_chat = AsyncMock(return_value=mock_chat)

        topic_counter = 8000
        async def mock_create(chat_id, title):
            nonlocal topic_counter
            topic_counter += 1
            mock_res = MagicMock()
            mock_res.id = topic_counter
            return mock_res

        self.mock_client.create_forum_topic = AsyncMock(side_effect=mock_create)

        txt = """[Linear Algebra] Lecture 1A : https://example.com/la1.mp4
[Probability] Lecture 1A : https://example.com/pr1.mp4
"""
        course = parse_bracket_topic_txt(txt)
        self.assertEqual(len(course.units), 2)
        self.assertEqual(course.units[0].title, "Linear Algebra")
        self.assertEqual(course.units[1].title, "Probability")

        t1 = await resolve_or_create_forum_topic(
            client=self.mock_client, chat_id=chat_id, subject="Linear Algebra",
            bot_id="bot_1", custom_title="📌 Linear Algebra", topic_key="bracket_topic:linear_algebra"
        )
        t2 = await resolve_or_create_forum_topic(
            client=self.mock_client, chat_id=chat_id, subject="Probability",
            bot_id="bot_1", custom_title="📌 Probability", topic_key="bracket_topic:probability"
        )

        self.assertEqual(t1, 8001)
        self.assertEqual(t2, 8002)
        self.assertNotEqual(t1, t2)

    # -------------------------------------------------------------------------
    # 10. RETURNING TO AN EARLIER TOPIC REUSES IT
    # -------------------------------------------------------------------------
    async def test_10_returning_to_earlier_topic_reuses_it(self):
        chat_id = -100999888333
        mock_chat = MagicMock()
        mock_chat.id = chat_id
        mock_chat.type = enums.ChatType.SUPERGROUP
        mock_chat.is_forum = True
        self.mock_client.get_chat = AsyncMock(return_value=mock_chat)

        created_topics = {}
        counter = 9000
        async def mock_create(chat_id, title):
            nonlocal counter
            counter += 1
            created_topics[title] = counter
            mock_res = MagicMock()
            mock_res.id = counter
            return mock_res

        self.mock_client.create_forum_topic = AsyncMock(side_effect=mock_create)

        txt = """[Linear Algebra] Lecture 1A : https://example.com/la1.mp4
[Linear Algebra] Lecture 1B : https://example.com/la2.mp4
[Probability] Lecture 1A : https://example.com/pr1.mp4
[Probability] Lecture 1B : https://example.com/pr2.mp4
[Linear Algebra] Lecture 2A : https://example.com/la3.mp4
"""
        course = parse_bracket_topic_txt(txt)
        # Exactly 2 distinct topic units
        self.assertEqual(len(course.units), 2)
        self.assertEqual(len(course.all_items), 5)

        # Process all items sequentially
        resolved_thread_ids = []
        for itm in course.all_items:
            tid = await resolve_or_create_forum_topic(
                client=self.mock_client,
                chat_id=chat_id,
                subject=itm.topic_title,
                bot_id="bot_1",
                custom_title=f"📌 {itm.topic_title}",
                topic_key=f"bracket_topic:{normalize_title(itm.topic_title)}"
            )
            resolved_thread_ids.append(tid)

        # Expected: item 0, 1, 4 have same thread_id (Linear Algebra), 2, 3 have Probability
        self.assertEqual(resolved_thread_ids[0], resolved_thread_ids[1])
        self.assertEqual(resolved_thread_ids[0], resolved_thread_ids[4])
        self.assertEqual(resolved_thread_ids[2], resolved_thread_ids[3])
        self.assertNotEqual(resolved_thread_ids[0], resolved_thread_ids[2])
        # Only 2 Telegram forum topics created in total
        self.assertEqual(len(created_topics), 2)

    # -------------------------------------------------------------------------
    # 11. DIFFERENT BOTS CANNOT SHARE TOPICS
    # -------------------------------------------------------------------------
    async def test_11_different_bots_cannot_share_topics(self):
        chat_id = -100999888444
        mock_chat = MagicMock()
        mock_chat.id = chat_id
        mock_chat.type = enums.ChatType.SUPERGROUP
        mock_chat.is_forum = True
        self.mock_client.get_chat = AsyncMock(return_value=mock_chat)

        counter = 5000
        async def mock_create(chat_id, title):
            nonlocal counter
            counter += 1
            mock_res = MagicMock()
            mock_res.id = counter
            return mock_res

        self.mock_client.create_forum_topic = AsyncMock(side_effect=mock_create)

        t_bot1 = await resolve_or_create_forum_topic(
            client=self.mock_client, chat_id=chat_id, subject="Linear Algebra",
            bot_id="bot_1", custom_title="📌 Linear Algebra", topic_key="bracket_topic:linear_algebra"
        )
        t_bot2 = await resolve_or_create_forum_topic(
            client=self.mock_client, chat_id=chat_id, subject="Linear Algebra",
            bot_id="bot_2", custom_title="📌 Linear Algebra", topic_key="bracket_topic:linear_algebra"
        )

        self.assertEqual(t_bot1, 5001)
        self.assertEqual(t_bot2, 5002)
        self.assertNotEqual(t_bot1, t_bot2)

        # Cached lookups remain isolated
        self.assertEqual(db.get_topic_id(chat_id, "bracket_topic:linear_algebra", bot_id="bot_1"), 5001)
        self.assertEqual(db.get_topic_id(chat_id, "bracket_topic:linear_algebra", bot_id="bot_2"), 5002)

    # -------------------------------------------------------------------------
    # 12. EXISTING APPX TXT PARSER STILL WORKS
    # -------------------------------------------------------------------------
    def test_12_existing_appx_parser_still_works(self):
        fmt_legacy = detect_txt_format(SAMPLE_LEGACY_APPX_TXT)
        self.assertEqual(fmt_legacy, "legacy_appx")

        fmt_cw = detect_txt_format(SAMPLE_CAREERWILL_TXT)
        self.assertEqual(fmt_cw, "structured_batch")

        fmt_bracket = detect_txt_format(SAMPLE_BRACKET_TXT)
        self.assertEqual(fmt_bracket, "bracket_topic")

        course_legacy = parse_course_txt(SAMPLE_LEGACY_APPX_TXT, "legacy.txt")
        self.assertEqual(course_legacy.course, "Discrete Structures")
        self.assertEqual(len(course_legacy.all_items), 5)

        course_cw = parse_course_txt(SAMPLE_CAREERWILL_TXT, "cw.txt")
        self.assertEqual(course_cw.format_type, "structured_batch")

    # -------------------------------------------------------------------------
    # 13. YOUTUBE INSIDE BRACKET TXT WORKS
    # -------------------------------------------------------------------------
    def test_13_youtube_inside_bracket_txt(self):
        line = "[Linear Algebra] Lecture 10G : https://youtu.be/dQw4w9WgXcQ"
        resources = parse_bracket_topic_raw(line)
        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0].media_type, "YOUTUBE")
        self.assertEqual(MediaRouter.classify_url(resources[0].url), MediaType.YOUTUBE)

    # -------------------------------------------------------------------------
    # 14. SPAYEE HLS ROUTES TO SPAYEE HANDLER
    # -------------------------------------------------------------------------
    def test_14_spayee_hls_routing(self):
        spayee_urls = [
            "https://qcdn.spayee.in/vod/index.m3u8*648dea11fe203008b24f492175fe674d",
            "https://vcdn.spayee.in/hls/master.m3u8",
            "https://spayee.in/media/stream.m3u8*key123"
        ]
        for url in spayee_urls:
            self.assertTrue(is_spayee_url(url))
            self.assertEqual(MediaRouter.classify_url(url), MediaType.SPAYEE_HLS)

    # -------------------------------------------------------------------------
    # 15. GENERIC M3U8 ROUTES TO GENERIC HLS HANDLER
    # -------------------------------------------------------------------------
    def test_15_generic_m3u8_routing(self):
        generic_url = "https://d3vlg4qjb80h8n.cloudfront.net/videos/master.m3u8?auth=123"
        self.assertFalse(is_spayee_url(generic_url))
        self.assertEqual(MediaRouter.classify_url(generic_url), MediaType.DIRECT_M3U8)

    # -------------------------------------------------------------------------
    # 16. PDF ROUTES TO PDF HANDLER
    # -------------------------------------------------------------------------
    def test_16_pdf_routing(self):
        pdf_url = "https://example.com/files/lecture_notes.pdf"
        pdf_token_url = "https://example.com/files/lecture_notes.pdf*SECRET_TOKEN"
        self.assertEqual(MediaRouter.classify_url(pdf_url), MediaType.DIRECT_PDF)
        self.assertEqual(MediaRouter.classify_url(pdf_token_url), MediaType.DIRECT_PDF)
        self.assertTrue(is_direct_pdf_url(pdf_token_url))

    # -------------------------------------------------------------------------
    # 17. ORIGINAL TXT ORDER IS PRESERVED
    # -------------------------------------------------------------------------
    def test_17_original_txt_order_preserved(self):
        course = parse_bracket_topic_txt(SAMPLE_BRACKET_TXT)
        expected_titles = [
            "Lecture 1A - Why Study Linear Algebra",
            "Lecture 1B - Linear Algebra for GATE & Interviews",
            "Annotated Notes - Lecture 1A-1B",
            "Lecture 1C - Linearly Independent and Linearly Dependent",
            "Lecture 1A. Probability Definition, Sample Space and Events",
            "Lecture 1B. Inclusion Exclusion Principle",
            "Annotated Notes Lecture 1",
            "Lecture 2A - Matrix Operations"
        ]
        actual_titles = [itm.title for itm in course.all_items]
        self.assertEqual(actual_titles, expected_titles)
        self.assertEqual([itm.index for itm in course.all_items], list(range(1, 9)))

    # -------------------------------------------------------------------------
    # 18. DUPLICATE UPLOAD DOES NOT CREATE DUPLICATE TOPICS
    # -------------------------------------------------------------------------
    async def test_18_duplicate_upload_does_not_create_duplicate_topics(self):
        chat_id = -100999888555
        mock_chat = MagicMock()
        mock_chat.id = chat_id
        mock_chat.type = enums.ChatType.SUPERGROUP
        mock_chat.is_forum = True
        self.mock_client.get_chat = AsyncMock(return_value=mock_chat)

        created_count = 0
        async def mock_create(chat_id, title):
            nonlocal created_count
            created_count += 1
            mock_res = MagicMock()
            mock_res.id = 6000 + created_count
            return mock_res

        self.mock_client.create_forum_topic = AsyncMock(side_effect=mock_create)

        # Upload 1
        course1 = parse_bracket_topic_txt(SAMPLE_BRACKET_TXT)
        for unit in course1.units:
            await resolve_or_create_forum_topic(
                client=self.mock_client, chat_id=chat_id, subject=unit.title,
                bot_id="bot_1", custom_title=unit.display_header,
                topic_key=f"bracket_topic:{normalize_title(unit.title)}"
            )

        self.assertEqual(created_count, 2)  # Linear Algebra & Probability

        # Upload 2 (same TXT uploaded again)
        course2 = parse_bracket_topic_txt(SAMPLE_BRACKET_TXT)
        for unit in course2.units:
            tid = await resolve_or_create_forum_topic(
                client=self.mock_client, chat_id=chat_id, subject=unit.title,
                bot_id="bot_1", custom_title=unit.display_header,
                topic_key=f"bracket_topic:{normalize_title(unit.title)}"
            )
            self.assertIn(tid, (6001, 6002))

        # No new topics created on duplicate upload
        self.assertEqual(created_count, 2)

    # -------------------------------------------------------------------------
    # 19. NON-FORUM DESTINATIONS DO NOT RECEIVE FORUM PARAMETERS
    # -------------------------------------------------------------------------
    async def test_19_non_forum_destinations_no_thread_id(self):
        # 1. Private Chat
        mock_priv = MagicMock()
        mock_priv.id = 12345
        mock_priv.type = enums.ChatType.PRIVATE
        mock_priv.is_forum = False
        self.mock_client.get_chat = AsyncMock(return_value=mock_priv)
        kw_priv = await build_send_kwargs(self.mock_client, 12345, topic_thread_id=555)
        self.assertEqual(kw_priv, {})

        # 2. Normal Group
        mock_grp = MagicMock()
        mock_grp.id = -100111
        mock_grp.type = enums.ChatType.GROUP
        mock_grp.is_forum = False
        self.mock_client.get_chat = AsyncMock(return_value=mock_grp)
        kw_grp = await build_send_kwargs(self.mock_client, -100111, topic_thread_id=555)
        self.assertEqual(kw_grp, {})

        # 3. Channel
        mock_ch = MagicMock()
        mock_ch.id = -100222
        mock_ch.type = enums.ChatType.CHANNEL
        mock_ch.is_forum = False
        self.mock_client.get_chat = AsyncMock(return_value=mock_ch)
        kw_ch = await build_send_kwargs(self.mock_client, -100222, topic_thread_id=555)
        self.assertEqual(kw_ch, {})

    # -------------------------------------------------------------------------
    # 20. CRASH / RECOVERY PRESERVES BOT_ID + CHAT_ID + TOPIC MAPPING
    # -------------------------------------------------------------------------
    def test_20_crash_recovery_preserves_topic_mapping(self):
        chat_id = -100999888666
        db.save_topic_id(chat_id, "bracket_topic:linear_algebra", 9991, bot_id="bot_1")
        db.save_topic_id(chat_id, "bracket_topic:probability", 9992, bot_id="bot_1")
        db.save_topic_id(chat_id, "bracket_topic:linear_algebra", 8881, bot_id="bot_2")

        # Simulate bot restart / crash by re-initializing Database instance
        new_db = Database()
        new_db.topics_file = self.test_topics_file
        self.assertEqual(new_db.get_topic_id(chat_id, "bracket_topic:linear_algebra", bot_id="bot_1"), 9991)
        self.assertEqual(new_db.get_topic_id(chat_id, "bracket_topic:probability", bot_id="bot_1"), 9992)
        self.assertEqual(new_db.get_topic_id(chat_id, "bracket_topic:linear_algebra", bot_id="bot_2"), 8881)


if __name__ == "__main__":
    unittest.main()
