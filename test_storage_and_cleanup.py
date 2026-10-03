import os
import sys
import shutil
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

from vars import PROJECT_ROOT, DATA_DIR, TEMP_DIR, DOWNLOADS_DIR, MAX_UPLOAD_SIZE_BYTES
from utils import (
    is_safe_temp_path,
    cleanup_uploaded_file,
    cleanup_job_temp_dir,
    cleanup_stale_temp_dirs,
    get_disk_storage_info
)
import itsgolu as helper


class TestStorageAndCleanup(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.join(TEMP_DIR, "test_cleanup_suite")
        os.makedirs(self.test_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_safe_temp_path_containment(self):
        """Verify that is_safe_temp_path protects critical system and user paths."""
        # Unsafe paths
        self.assertFalse(is_safe_temp_path("C:\\"))
        self.assertFalse(is_safe_temp_path("B:\\"))
        self.assertFalse(is_safe_temp_path("C:\\Users\\ramji"))
        self.assertFalse(is_safe_temp_path("C:\\Users\\ramji\\OneDrive\\Desktop"))
        self.assertFalse(is_safe_temp_path("C:\\Windows\\System32"))
        self.assertFalse(is_safe_temp_path(str(PROJECT_ROOT)))
        self.assertFalse(is_safe_temp_path(str(PROJECT_ROOT / "main.py")))
        self.assertFalse(is_safe_temp_path(str(PROJECT_ROOT / "vars.py")))
        self.assertFalse(is_safe_temp_path(str(PROJECT_ROOT / ".env")))

        # Safe paths within TEMP_DIR and DOWNLOADS_DIR
        valid_temp_file = os.path.join(TEMP_DIR, "bot_1_job_123", "video.mp4")
        valid_dl_file = os.path.join(DOWNLOADS_DIR, "lecture.pdf")
        self.assertTrue(is_safe_temp_path(valid_temp_file))
        self.assertTrue(is_safe_temp_path(valid_dl_file))
        self.assertTrue(is_safe_temp_path(os.path.join(self.test_dir, "test.txt")))

    def test_cleanup_uploaded_file(self):
        """Verify cleanup_uploaded_file removes valid temp files and rejects unsafe ones."""
        # Create a safe test file
        safe_file = os.path.join(self.test_dir, "safe_uploaded_video.mp4")
        with open(safe_file, "w") as f:
            f.write("test content")
        self.assertTrue(os.path.exists(safe_file))

        # Cleanup should succeed
        res = cleanup_uploaded_file(safe_file)
        self.assertTrue(res)
        self.assertFalse(os.path.exists(safe_file))

        # Attempting cleanup on main.py should be safely blocked
        main_py = str(PROJECT_ROOT / "main.py")
        res_main = cleanup_uploaded_file(main_py)
        self.assertFalse(res_main)
        self.assertTrue(os.path.exists(main_py))

    def test_cleanup_job_temp_dir(self):
        """Verify cleanup_job_temp_dir cleans entire job folder safely."""
        job_dir = os.path.join(self.test_dir, "bot_1_job_abc")
        os.makedirs(os.path.join(job_dir, "downloads"), exist_ok=True)
        sample_file = os.path.join(job_dir, "downloads", "item.mp4")
        with open(sample_file, "w") as f:
            f.write("content")

        self.assertTrue(os.path.exists(sample_file))
        res = cleanup_job_temp_dir(job_dir)
        self.assertTrue(res)
        self.assertFalse(os.path.exists(job_dir))

    def test_disk_storage_info(self):
        """Verify storage diagnostic extraction."""
        info = get_disk_storage_info()
        self.assertIn("drive", info)
        self.assertIn("free_human", info)
        self.assertIn("total_human", info)
        self.assertIn("is_low_space", info)
        self.assertIsInstance(info["free_bytes"], int)
        self.assertGreater(info["total_bytes"], 0)


class TestUploadAutoCleanup(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = os.path.join(TEMP_DIR, "test_upload_cleanup")
        os.makedirs(self.test_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("subprocess.run")
    @patch("itsgolu.duration", return_value=120.0)
    @patch("itsgolu.safe_video_send")
    async def test_normal_video_auto_cleanup_on_success(self, mock_send, mock_dur, mock_sub):
        """Verify that a video is immediately deleted from disk after confirmed Telegram upload."""
        mock_msg = MagicMock()
        mock_send.return_value = mock_msg

        vid_path = os.path.join(self.test_dir, "lesson_normal.mp4")
        with open(vid_path, "wb") as f:
            f.write(b"0" * 1024)

        mock_bot = MagicMock()
        mock_prog = AsyncMock()

        res = await helper.send_vid(
            bot=mock_bot,
            m=None,
            cc="Caption",
            filename=vid_path,
            thumb="/d",
            name="Lesson 1",
            prog=mock_prog,
            channel_id=-1001234567
        )

        self.assertIsNotNone(res)
        self.assertFalse(os.path.exists(vid_path), "File should be deleted after successful Telegram upload")

    @patch("subprocess.run")
    @patch("itsgolu.duration", return_value=120.0)
    @patch("itsgolu.safe_video_send")
    async def test_video_kept_on_upload_failure(self, mock_send, mock_dur, mock_sub):
        """Verify that a video is KEPT on disk for retry if Telegram upload fails."""
        mock_send.return_value = None  # Simulates failed upload
        mock_bot = MagicMock()
        mock_bot.send_document = AsyncMock(return_value=None)  # Fallback also fails
        mock_prog = AsyncMock()

        vid_path = os.path.join(self.test_dir, "lesson_failed.mp4")
        with open(vid_path, "wb") as f:
            f.write(b"0" * 1024)

        res = await helper.send_vid(
            bot=mock_bot,
            m=None,
            cc="Caption",
            filename=vid_path,
            thumb="/d",
            name="Lesson Failed",
            prog=mock_prog,
            channel_id=-1001234567
        )

        self.assertIsNone(res)
        self.assertTrue(os.path.exists(vid_path), "File MUST be kept on disk for retry when upload fails")

    @patch("subprocess.run")
    @patch("itsgolu.duration", return_value=60.0)
    @patch("itsgolu.split_large_video")
    @patch("itsgolu.safe_video_send")
    async def test_large_video_split_part_cleanup(self, mock_send, mock_split, mock_dur, mock_sub):
        """Verify that each split part is deleted immediately as it succeeds, without keeping all parts on disk."""
        mock_msg = MagicMock()
        mock_send.return_value = mock_msg

        # Create original large file (simulate exceeding threshold)
        large_file = os.path.join(self.test_dir, "large_video.mp4")
        with open(large_file, "wb") as f:
            f.seek(MAX_UPLOAD_SIZE_BYTES + 1024)
            f.write(b"\0")

        # Create 2 part files
        part1 = os.path.join(self.test_dir, "large_video_Part_1.mp4")
        part2 = os.path.join(self.test_dir, "large_video_Part_2.mp4")
        with open(part1, "wb") as f:
            f.write(b"0" * 512)
        with open(part2, "wb") as f:
            f.write(b"0" * 512)

        mock_split.return_value = [part1, part2]

        mock_bot = MagicMock()
        mock_prog = AsyncMock()

        res = await helper.send_vid(
            bot=mock_bot,
            m=None,
            cc="Caption",
            filename=large_file,
            thumb="/d",
            name="Large Lecture",
            prog=mock_prog,
            channel_id=-1001234567
        )

        self.assertIsNotNone(res)
        # Both parts and original file should be cleaned up after successful parts upload
        self.assertFalse(os.path.exists(part1), "Part 1 must be deleted after successful upload")
        self.assertFalse(os.path.exists(part2), "Part 2 must be deleted after successful upload")
        self.assertFalse(os.path.exists(large_file), "Original large file must be deleted after all parts uploaded")


if __name__ == "__main__":
    unittest.main()
