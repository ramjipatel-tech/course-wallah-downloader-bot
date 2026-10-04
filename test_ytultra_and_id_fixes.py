import os
import sys
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio

from pyrogram.enums import ChatType
from pyrogram.types import Message, Chat, User

from utils import (
    MediaType,
    MediaRouter,
    is_youtube_url,
    is_spayee_url,
    is_goclasses_url,
    is_appx_url,
    is_kgs_url
)
from youtube_fallback import (
    parse_ytultra_response,
    resolve_youtube_ytultra,
    resolve_youtube_ytultra_info,
    download_media_stream_url
)
import itsgolu as helper
from main import id_cmd


class TestYTUltraAndIDFixes(unittest.IsolatedAsyncioTestCase):

    # ==========================================================================
    # 1. YOUTUBE HOSTNAME DETECTION & DEFENSIVE CLASSIFICATION
    # ==========================================================================

    def test_01_youtube_hostname_strict_matching(self):
        """Only valid YouTube hostnames are classified as YouTube."""
        valid_hosts = [
            "https://www.youtube.com/watch?v=MdG0Vw9f1A4",
            "https://youtube.com/watch?v=MdG0Vw9f1A4",
            "https://youtu.be/MdG0Vw9f1A4",
            "https://m.youtube.com/watch?v=MdG0Vw9f1A4",
            "https://www.youtube-nocookie.com/embed/MdG0Vw9f1A4"
        ]
        for url in valid_hosts:
            self.assertTrue(is_youtube_url(url), f"Should match YouTube URL: {url}")
            self.assertEqual(MediaRouter.classify_url(url), MediaType.YOUTUBE)

        invalid_hosts = [
            "https://notyoutube.com/watch?v=123",
            "https://spayee.in/youtube-tutorial/master.m3u8",
            "https://courses.goclasses.in/lecture/youtube.m3u8",
            "https://example.com/search?q=youtube.com",
            "https://learning.spees.in/spees/youtube/index.m3u8*auth123"
        ]
        for url in invalid_hosts:
            self.assertFalse(is_youtube_url(url), f"Should NOT match non-YouTube URL: {url}")
            self.assertNotEqual(MediaRouter.classify_url(url), MediaType.YOUTUBE)

    # ==========================================================================
    # 2. YTULTRA DEFENSIVE RESPONSE PARSING & QUALITY SELECTION
    # ==========================================================================

    def test_02_ytultra_progressive_format_selection(self):
        """Selects progressive format containing both video and audio."""
        sample_data = {
            "code": "0000",
            "data": {
                "title": "Sample Progressive",
                "duration": "120",
                "medias": [
                    {"format": "1080p (12.22 GB) [.mp4]", "url": "https://cdn.ytultra.com/1080p.mp4?itag=299&mime=video/mp4"},
                    {"format": "720p (6.31 GB) [.mp4]", "url": "https://cdn.ytultra.com/720p.mp4?itag=22&mime=video/mp4", "has_audio": True},
                    {"format": "360p (1.65 GB) [.mp4]", "url": "https://cdn.ytultra.com/360p.mp4?itag=18&mime=video/mp4", "has_audio": True}
                ]
            }
        }
        res = parse_ytultra_response(sample_data, target_height=720)
        self.assertIsNotNone(res)
        self.assertEqual(res["height"], 720)
        self.assertTrue(res["has_audio"])
        self.assertTrue(res["is_progressive"])
        self.assertIn("720p.mp4", res["url"])

    def test_03_ytultra_separate_video_and_audio_selection(self):
        """When separate video-only and audio streams are provided, selects both for FFmpeg muxing."""
        sample_data = {
            "code": "0000",
            "data": {
                "title": "Sample Separate Streams",
                "duration": "300",
                "medias": [
                    {"format": "1080p (12.22 GB) [.mp4]", "url": "https://cdn.ytultra.com/1080p.mp4?itag=299&mime=video/mp4"},
                    {"format": "720p (6.31 GB) [.mp4]", "url": "https://cdn.ytultra.com/720p.mp4?itag=298&mime=video/mp4"},
                    {"format": "360p (1.65 GB) [.mp4]", "url": "https://cdn.ytultra.com/360p.mp4?itag=18&mime=video/mp4"},
                    {"format": "397.72 MB [.m4a]", "url": "https://cdn.ytultra.com/audio.m4a?itag=140&mime=audio/mp4"}
                ]
            }
        }
        # Target 720p: should pick 720p video-only + 140 audio
        res = parse_ytultra_response(sample_data, target_height=720)
        self.assertIsNotNone(res)
        self.assertEqual(res["height"], 720)
        self.assertTrue(res["has_audio"])
        self.assertFalse(res["is_progressive"])
        self.assertIn("720p.mp4", res["video_url"])
        self.assertIn("audio.m4a", res["audio_url"])

    def test_04_ytultra_prefers_progressive_when_matching_target(self):
        """When target is 360p, prefers 360p progressive directly."""
        sample_data = {
            "code": "0000",
            "data": {
                "medias": [
                    {"format": "720p (6.31 GB) [.mp4]", "url": "https://cdn.ytultra.com/720p.mp4?itag=298"},
                    {"format": "360p (1.65 GB) [.mp4]", "url": "https://cdn.ytultra.com/360p.mp4?itag=18"},
                    {"format": "397.72 MB [.m4a]", "url": "https://cdn.ytultra.com/audio.m4a?itag=140"}
                ]
            }
        }
        res = parse_ytultra_response(sample_data, target_height=360)
        self.assertIsNotNone(res)
        self.assertEqual(res["height"], 360)
        self.assertTrue(res["is_progressive"])

    def test_05_ytultra_rejects_video_without_audio(self):
        """When API response has only video-only streams and no audio streams, rejects and returns None."""
        sample_data = {
            "code": "0000",
            "data": {
                "medias": [
                    {"format": "1080p [.mp4]", "url": "https://cdn.ytultra.com/1080p.mp4?itag=299&mime=video/mp4", "has_audio": False},
                    {"format": "720p [.mp4]", "url": "https://cdn.ytultra.com/720p.mp4?itag=298&mime=video/mp4", "has_audio": False}
                ]
            }
        }
        res = parse_ytultra_response(sample_data, target_height=720)
        self.assertIsNone(res, "Video-only without audio must be rejected.")

    def test_06_ytultra_defensive_parsing_edge_cases(self):
        """Tolerates malformed JSON, missing data fields, empty formats, and null inputs."""
        self.assertIsNone(parse_ytultra_response(None))
        self.assertIsNone(parse_ytultra_response({}))
        self.assertIsNone(parse_ytultra_response({"code": "9999", "error": "Internal Error"}))
        self.assertIsNone(parse_ytultra_response({"data": {"medias": []}}))
        self.assertIsNone(parse_ytultra_response({"data": {"medias": [{"invalid": "item"}]}}))

        # Direct URL top-level fallback
        direct_data = {"url": "https://cdn.ytultra.com/direct.mp4"}
        res = parse_ytultra_response(direct_data, target_height=720)
        self.assertIsNotNone(res)
        self.assertEqual(res["url"], "https://cdn.ytultra.com/direct.mp4")

    # ==========================================================================
    # 3. DIRECT YTULTRA API RESOLVER & MUXING
    # ==========================================================================

    @patch("requests.post")
    def test_07_resolve_youtube_ytultra_api_call(self, mock_post):
        """Verifies YTUltra API request payload, headers and URL."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "code": "0000",
            "data": {
                "medias": [
                    {"format": "720p [.mp4]", "url": "https://cdn.ytultra.com/stream.mp4?itag=22", "has_audio": True}
                ]
            }
        }
        mock_post.return_value = mock_resp

        info = resolve_youtube_ytultra_info("https://youtube.com/watch?v=MdG0Vw9f1A4", "720p")
        self.assertIsNotNone(info)
        self.assertEqual(info["height"], 720)
        self.assertTrue(info["has_audio"])

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://api.ytultra.com/ikool/youtube/download")
        self.assertEqual(kwargs["json"], {"url": "https://youtube.com/watch?v=MdG0Vw9f1A4"})
        self.assertEqual(kwargs["headers"]["Origin"], "https://www.ytultra.com")
        self.assertEqual(kwargs["headers"]["Referer"], "https://www.ytultra.com/")

    # ==========================================================================
    # 4. /ID BUG FIX & TELEGRAM UPDATE TYPES
    # ==========================================================================

    async def test_08_id_cmd_private_chat(self):
        """/id in private chat returns Chat ID and User ID."""
        mock_client = MagicMock()
        mock_message = MagicMock(spec=Message)
        mock_message.chat = MagicMock(spec=Chat)
        mock_message.chat.id = 123456789
        mock_message.chat.type = ChatType.PRIVATE
        mock_message.chat.title = None
        mock_message.chat.first_name = "Ramji"
        mock_message.chat.last_name = "Patel"
        mock_message.chat.username = "ramjipatel"
        mock_message.from_user = MagicMock(spec=User)
        mock_message.from_user.id = 123456789
        mock_message.message_thread_id = None
        mock_message.reply_text = AsyncMock()

        await id_cmd(mock_client, mock_message)

        mock_message.reply_text.assert_called_once()
        reply_content = mock_message.reply_text.call_args[0][0]
        self.assertIn("123456789", reply_content)
        self.assertIn("private", reply_content)
        self.assertIn("Ramji Patel", reply_content)
        self.assertIn("@ramjipatel", reply_content)

    async def test_09_id_cmd_group_and_supergroup(self):
        """/id in supergroup returns Supergroup Chat ID and User ID."""
        mock_client = MagicMock()
        mock_message = MagicMock(spec=Message)
        mock_message.chat = MagicMock(spec=Chat)
        mock_message.chat.id = -1001987654321
        mock_message.chat.type = ChatType.SUPERGROUP
        mock_message.chat.title = "Course Wallah Community"
        mock_message.chat.first_name = None
        mock_message.chat.last_name = None
        mock_message.chat.username = "coursewallah_group"
        mock_message.from_user = MagicMock(spec=User)
        mock_message.from_user.id = 555666777
        mock_message.message_thread_id = None
        mock_message.reply_text = AsyncMock()

        await id_cmd(mock_client, mock_message)

        mock_message.reply_text.assert_called_once()
        reply_content = mock_message.reply_text.call_args[0][0]
        self.assertIn("-1001987654321", reply_content)
        self.assertIn("supergroup", reply_content)
        self.assertIn("Course Wallah Community", reply_content)
        self.assertIn("555666777", reply_content)

    async def test_10_id_cmd_forum_supergroup_separate_thread_id(self):
        """/id in forum supergroup keeps message_thread_id strictly separated from chat_id."""
        mock_client = MagicMock()
        mock_message = MagicMock(spec=Message)
        mock_message.chat = MagicMock(spec=Chat)
        mock_message.chat.id = -1001112223334
        mock_message.chat.type = ChatType.SUPERGROUP
        mock_message.chat.title = "Course Wallah Forum"
        mock_message.chat.first_name = None
        mock_message.chat.last_name = None
        mock_message.chat.username = None
        mock_message.from_user = MagicMock(spec=User)
        mock_message.from_user.id = 999888777
        mock_message.message_thread_id = 42
        mock_message.reply_text = AsyncMock()

        await id_cmd(mock_client, mock_message)

        mock_message.reply_text.assert_called_once()
        reply_content = mock_message.reply_text.call_args[0][0]
        self.assertIn("-1001112223334", reply_content)
        self.assertIn("42", reply_content)
        self.assertIn("Topic / Thread ID", reply_content)
        # Reply must include message_thread_id kwargs so it posts inside the forum topic
        self.assertEqual(mock_message.reply_text.call_args[1].get("message_thread_id"), 42)

    async def test_11_id_cmd_channel_post(self):
        """/id in channel returns Channel Chat ID without requiring user or forum thread."""
        mock_client = MagicMock()
        mock_message = MagicMock(spec=Message)
        mock_message.chat = MagicMock(spec=Chat)
        mock_message.chat.id = -1009998887776
        mock_message.chat.type = ChatType.CHANNEL
        mock_message.chat.title = "Course Wallah Announcements"
        mock_message.chat.first_name = None
        mock_message.chat.last_name = None
        mock_message.chat.username = "cw_channel"
        mock_message.from_user = None
        mock_message.message_thread_id = None
        mock_message.reply_text = AsyncMock()

        await id_cmd(mock_client, mock_message)

        mock_message.reply_text.assert_called_once()
        reply_content = mock_message.reply_text.call_args[0][0]
        self.assertIn("-1009998887776", reply_content)
        self.assertIn("channel", reply_content)
        self.assertIn("Course Wallah Announcements", reply_content)
        # Channels should not have topic thread id in kwargs
        self.assertNotIn("message_thread_id", mock_message.reply_text.call_args[1])

    # ==========================================================================
    # 5. PROVIDER ISOLATION & ZERO REGRESSION (APPX, SPAYEE, KGS, GO CLASSES)
    # ==========================================================================

    def test_12_provider_isolation_intact(self):
        """Ensure YouTube modifications do not alter other provider classifications."""
        spayee_url = "https://qcdn.spayee.in/spees/courses/123/master.m3u8*auth123"
        self.assertTrue(is_spayee_url(spayee_url))
        self.assertEqual(MediaRouter.classify_url(spayee_url), MediaType.SPAYEE_HLS)

        goclasses_url = "https://courses.goclasses.in/algo.m3u8"
        self.assertTrue(is_goclasses_url(goclasses_url))
        self.assertEqual(MediaRouter.classify_url(goclasses_url), MediaType.GO_CLASSES)

        appx_url = "https://appx.co.in/api/v1/courses/10/lecture/20"
        self.assertTrue(is_appx_url(appx_url))
        self.assertEqual(MediaRouter.classify_url(appx_url), MediaType.APPX_LECTURE)

        kgs_url = "https://kgs.example.com/hls/video.m3u8"
        self.assertTrue(is_kgs_url(kgs_url))
        self.assertEqual(MediaRouter.classify_url(kgs_url), MediaType.KGS_HLS)


if __name__ == "__main__":
    unittest.main()
