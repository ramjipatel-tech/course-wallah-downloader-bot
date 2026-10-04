import unittest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from pyrogram import enums

from utils import (
    format_main_menu,
    format_universal_input_card,
    format_invalid_input_card,
    format_drm_input_card,
    format_drm_result_card,
    check_media_drm_status,
    format_youtube_quality_menu,
    format_4k_available_card,
    format_youtube_fallback_card,
    format_single_quality_card,
    format_batch_input_card,
    format_batch_summary_card,
    format_chat_id_card,
    format_api_token_card,
    format_watermark_input_card,
    format_help_menu_card,
    format_contact_card,
    format_status_card,
    format_success_card,
    format_success_card_with_buttons,
    format_failure_card,
    format_expired_callback_card,
    format_bot_online_card,
    format_pdf_download_card
)
import vars


class TestUIAndInputFlows(unittest.TestCase):
    """
    Comprehensive verification suite for Course Wallah UI/UX design system,
    input flows, navigation, callbacks, and cards.
    """

    @classmethod
    def setUpClass(cls):
        try:
            cls.loop = asyncio.get_event_loop()
        except RuntimeError:
            cls.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(cls.loop)

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    # =========================================================================
    # 1. /START & MAIN MENU DASHBOARD
    # =========================================================================
    def test_01_main_menu_dashboard_design(self):
        text, markup = format_main_menu(
            user_id=12345,
            first_name="Rahul",
            is_admin=False,
            bot_name="Course Wallah",
            expiry_display="01-01-2027"
        )
        self.assertIn("COURSE WALLAH", text)
        self.assertIn("Rahul", text)
        self.assertIn("01-01-2027", text)
        self.assertIn("Available Benefits", text)
        self.assertIn("Video Downloads", text)
        self.assertIn("PDF / Notes", text)

        # Verify buttons & callbacks
        all_callbacks = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
        self.assertIn("menu_download", all_callbacks)
        self.assertIn("menu_courses", all_callbacks)
        self.assertIn("menu_quality", all_callbacks)
        self.assertIn("menu_batch", all_callbacks)
        self.assertIn("menu_pdf", all_callbacks)
        self.assertIn("menu_drm", all_callbacks)
        self.assertIn("menu_subscription", all_callbacks)
        self.assertIn("menu_help", all_callbacks)
        self.assertIn("menu_contact", all_callbacks)

    def test_02_main_menu_admin_badge(self):
        text, markup = format_main_menu(
            user_id=12345,
            first_name="AdminUser",
            is_admin=True,
            bot_name="Course Wallah"
        )
        all_callbacks = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
        self.assertIn("adm_main", all_callbacks)

    # =========================================================================
    # 2. UNIVERSAL INPUT CARDS & VALIDATION
    # =========================================================================
    def test_03_universal_input_card(self):
        text, markup = format_universal_input_card(
            action_title="DOWNLOAD",
            instruction="Send your media URL",
            example="https://youtu.be/...",
            user_id=12345,
            show_home=True
        )
        self.assertIn("COURSE WALLAH", text)
        self.assertIn("DOWNLOAD", text)
        self.assertIn("Send your media URL", text)
        self.assertIn("https://youtu.be/...", text)

        all_buttons = [b for row in markup.inline_keyboard for b in row]
        button_cbs = [b.callback_data for b in all_buttons]
        self.assertIn("menu_main", button_cbs)
        self.assertIn("input_cancel:12345", button_cbs)

    def test_04_invalid_input_card_and_retry(self):
        text, markup = format_invalid_input_card(
            expected_type="YouTube / Course URL",
            user_id=12345
        )
        self.assertIn("INVALID INPUT", text)
        self.assertIn("YouTube / Course URL", text)

        all_cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("input_retry:12345", all_cbs)
        self.assertIn("menu_main", all_cbs)

    # =========================================================================
    # 3. /DRM CHECKER UI & DETECTION
    # =========================================================================
    def test_05_drm_input_and_results(self):
        # Input prompt
        text, markup = format_drm_input_card(user_id=54321)
        self.assertIn("DRM CHECKER", text)
        self.assertIn("The checker only reports DRM status", text)
        self.assertIn("It does NOT bypass DRM", text)
        all_cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("input_cancel:54321", all_cbs)

        # Clear stream result
        clear_res = format_drm_result_card("Calculus Lecture", is_drm=False, media_type="HLS")
        self.assertIn("No DRM Detected", clear_res)
        self.assertIn("Calculus Lecture", clear_res)

        # DRM detected result
        drm_res = format_drm_result_card("Protected Course", is_drm=True, media_type="MPEG-DASH", details="Widevine ContentProtection")
        self.assertIn("DRM Protected", drm_res)
        self.assertIn("Widevine", drm_res)

    @patch("utils.requests.get")
    def test_06_check_media_drm_status_logic(self, mock_get):
        # Test Widevine MPD
        mock_resp = MagicMock()
        mock_resp.text = '<MPD><ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/></MPD>'
        mock_get.return_value = mock_resp
        res = check_media_drm_status("https://example.com/manifest.mpd")
        self.assertTrue(res["is_drm"])
        self.assertIn("Widevine", res["details"])

        # Test Clear HLS
        mock_resp_clear = MagicMock()
        mock_resp_clear.text = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1280000\nchunklist.m3u8"
        mock_get.return_value = mock_resp_clear
        res_clear = check_media_drm_status("https://example.com/playlist.m3u8")
        self.assertFalse(res_clear["is_drm"])
        self.assertIn("Clear HLS", res_clear["details"])

    # =========================================================================
    # 4. QUALITY UI EVERYWHERE (4K, FALLBACK, MULTI, SINGLE)
    # =========================================================================
    def test_07_quality_menu_with_4k(self):
        qualities = [
            {"height": 2160, "formatted_size": "2.1 GB", "short_label": "2160p (4K)", "badge": "🔥"},
            {"height": 1080, "formatted_size": "800 MB", "short_label": "1080p", "badge": "🎬"},
            {"height": 720, "formatted_size": "400 MB", "short_label": "720p", "badge": "⚡"},
            {"height": 480, "formatted_size": "200 MB", "short_label": "480p", "badge": "📱"}
        ]
        text, markup = format_youtube_quality_menu(
            title="4K Demo Video",
            duration_sec=7200,
            qualities=qualities,
            job_id="job_q1",
            user_id=12345
        )
        self.assertIn("SELECT QUALITY", text)
        self.assertIn("4K Demo Video", text)
        self.assertIn("2h", text)

        all_buttons = [b for row in markup.inline_keyboard for b in row]
        button_texts = [b.text for b in all_buttons]
        callback_datas = [b.callback_data for b in all_buttons]

        self.assertTrue(any("4K UHD" in t for t in button_texts))
        self.assertIn("ytq:job_q1:2160:12345", callback_datas)
        self.assertIn("ytq:job_q1:1080:12345", callback_datas)
        self.assertIn("menu_main", callback_datas)
        self.assertIn("ytcancel:job_q1:12345", callback_datas)

    def test_08_4k_available_card(self):
        text, markup = format_4k_available_card(
            title="Scenic 4K Drone",
            duration_sec=1800,
            job_id="job_4k_drone",
            user_id=999,
            video_codec="VP9",
            audio_codec="AAC",
            size_str="1.8 GB"
        )
        self.assertIn("4K UHD AVAILABLE", text)
        self.assertIn("3840 × 2160", text)
        self.assertIn("VP9", text)
        self.assertIn("AAC", text)

        all_cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("ytq:job_4k_drone:2160:999", all_cbs)
        self.assertIn("ytmenu:job_4k_drone:999", all_cbs)

    def test_09_4k_unavailable_dynamic_fallback(self):
        highest_q = {"height": 1080, "short_label": "1080p", "badge": "🎬"}
        text, markup = format_youtube_fallback_card(
            title="Lecture in 1080p only",
            duration_sec=3600,
            requested_height=2160,
            highest_quality=highest_q,
            job_id="job_fb",
            user_id=12345
        )
        self.assertIn("QUALITY NOTICE", text)
        self.assertIn("4K UHD (2160p) is not available", text)
        self.assertIn("1080p", text)

        all_buttons = [b for row in markup.inline_keyboard for b in row]
        button_texts = [b.text for b in all_buttons]
        callback_datas = [b.callback_data for b in all_buttons]

        self.assertTrue(any("Download 1080p" in t for t in button_texts))
        self.assertIn("ytq:job_fb:1080:12345", callback_datas)
        self.assertIn("ytmenu:job_fb:12345", callback_datas)

    def test_10_single_quality_available_card(self):
        single_q = {"height": 720, "short_label": "720p HD", "badge": "⚡", "formatted_size": "350 MB"}
        text, markup = format_single_quality_card(
            title="Only 720p Stream Available",
            duration_sec=1200,
            single_quality=single_q,
            job_id="job_single",
            user_id=888
        )
        self.assertIn("QUALITY FOUND", text)
        self.assertIn("720p HD", text)

        all_cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("ytq:job_single:720:888", all_cbs)
        self.assertIn("menu_main", all_cbs)

    # =========================================================================
    # 5. BATCH & TXT UI
    # =========================================================================
    def test_11_batch_cards(self):
        text, markup = format_batch_input_card(user_id=12345)
        self.assertIn("BATCH DOWNLOADER", text)
        self.assertIn(".txt", text)
        self.assertIn("Each URL will be processed independently", text)

        summary_text, sum_markup = format_batch_summary_card(
            total_urls=25,
            ready_count=24,
            invalid_count=1,
            batch_id="batch_01",
            user_id=12345
        )
        self.assertIn("BATCH RECEIVED", summary_text)
        self.assertIn("25", summary_text)
        self.assertIn("24", summary_text)
        self.assertIn("1", summary_text)

        sum_cbs = [b.callback_data for row in sum_markup.inline_keyboard for b in row]
        self.assertIn("start_batch:batch_01:12345", sum_cbs)
        self.assertIn("menu_main", sum_cbs)

    # =========================================================================
    # 6. ID, TOKEN & WATERMARK INPUTS
    # =========================================================================
    def test_12_specialized_input_cards(self):
        # Chat ID setup
        id_text, id_markup = format_chat_id_card(user_id=12345)
        self.assertIn("CHAT ID SETUP", id_text)
        self.assertIn("-1001234567890", id_text)

        # API token setup (Privacy & secret protection)
        tok_text, tok_markup = format_api_token_card("PW Token", user_id=12345)
        self.assertIn("API CONFIGURATION", tok_text)
        self.assertIn("Never share this credential publicly", tok_text)
        self.assertIn("It will not be displayed back", tok_text)

        # Watermark setup
        wm_text, wm_markup = format_watermark_input_card(user_id=12345)
        self.assertIn("WATERMARK SETUP", wm_text)
        self.assertIn("Course Wallah", wm_text)

    # =========================================================================
    # 7. HELP, CONTACT & BOT ONLINE ANNOUNCEMENTS
    # =========================================================================
    def test_13_help_and_contact_cards(self):
        # Help menu
        h_text, h_markup = format_help_menu_card("Course Wallah")
        self.assertIn("HELP & SUPPORT", h_text)
        h_cbs = [b.callback_data for row in h_markup.inline_keyboard for b in row if b.callback_data]
        self.assertIn("menu_help_how", h_cbs)
        self.assertIn("menu_help_dl", h_cbs)
        self.assertIn("menu_help_quality", h_cbs)
        self.assertIn("menu_help_drm", h_cbs)
        self.assertIn("menu_contact", h_cbs)
        self.assertIn("menu_main", h_cbs)

        # Contact card
        c_text, c_markup = format_contact_card(
            bot_name="Course Wallah",
            support_link="https://t.me/course_wallah_official_bot",
            support_username="course_wallah_official_bot"
        )
        self.assertIn("CONTACT SUPPORT", c_text)
        self.assertIn("@course_wallah_official_bot", c_text)

    def test_14_bot_online_startup_card(self):
        onl_text, onl_markup = format_bot_online_card(
            bot_name="Course Wallah Multi-Bot",
            bot_username="cw_official_bot",
            active_bots=3,
            total_bots=3,
            recovered_jobs=2
        )
        self.assertIn("Bot is now ONLINE & LIVE", onl_text)
        self.assertIn("High-Speed Downloader", onl_text)
        self.assertIn("4K Quality Support", onl_text)
        self.assertIn("3/3 active", onl_text)
        self.assertIn("2 job(s) restored", onl_text)

    # =========================================================================
    # 8. DOWNLOAD STATUS, SUCCESS & FAILURE CARDS
    # =========================================================================
    def test_15_status_card_no_secret_leaks(self):
        status_text = format_status_card(
            title="Complete Organic Chemistry",
            quality="2160p",
            video_codec="VP9",
            audio_codec="AAC",
            current_part=12,
            total_parts=44,
            phase="UPLOADING",
            percent=82.5,
            speed_str="45.0 MB/s",
            eta_str="01:20"
        )
        self.assertIn("COURSE WALLAH", status_text)
        self.assertIn("Organic Chemistry", status_text)
        self.assertIn("2160p", status_text)
        self.assertIn("Part 12 / 44", status_text)
        self.assertIn("82.5%", status_text)
        # Ensure no technical tokens or filesystem absolute paths leaked
        self.assertNotIn("ffmpeg.exe", status_text)
        self.assertNotIn("videoplayback?expire=", status_text)
        self.assertNotIn("Bearer ", status_text)

    def test_16_failure_card_clean_reason(self):
        fail_text, fail_markup = format_failure_card(
            title="Physics Numerical Video",
            reason="HTTP 403 Forbidden: signed token expired on upstream CDN",
            job_id="job_err_1",
            user_id=12345,
            can_retry=True,
            can_choose_quality=True
        )
        self.assertIn("DOWNLOAD FAILED", fail_text)
        self.assertIn("Physics Numerical Video", fail_text)
        self.assertIn("Something went wrong while processing this video", fail_text)

        all_cbs = [b.callback_data for row in fail_markup.inline_keyboard for b in row]
        self.assertIn("retry_fail_job_err_1", all_cbs)
        self.assertIn("ytmenu:job_err_1:12345", all_cbs)
        self.assertIn("menu_main", all_cbs)

    def test_17_expired_callback_card(self):
        exp_text, exp_markup = format_expired_callback_card()
        self.assertIn("ACTION EXPIRED", exp_text)
        self.assertIn("This action has expired", exp_text)
        all_cbs = [b.callback_data for row in exp_markup.inline_keyboard for b in row]
        self.assertIn("menu_download", all_cbs)
        self.assertIn("menu_main", all_cbs)


if __name__ == "__main__":
    unittest.main()
