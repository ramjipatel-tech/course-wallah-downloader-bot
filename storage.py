# ==============================================================================
# COURSE WALLAH CENTRAL STORAGE MANAGER & ABSTRACTION
# ==============================================================================
# Supports:
# - Local filesystem (Windows, Linux, macOS)
# - Cloud platform mounts (Railway Volume, Render Disk, Heroku ephemeral)
# - Ready for future S3/Object Storage backend integration
# - Multi-bot storage isolation (separate sessions, jobs, temp media, state)
# - Atomic file operations (.tmp writes -> atomic rename)
# - Free space monitoring & safety checks
# ==============================================================================

import os
import sys
import time
import shutil
import uuid
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Union, Dict, Any, List

logger = logging.getLogger("CourseWallahStorage")

# Absolute Application Root
APP_ROOT = Path(__file__).resolve().parent


class StorageBackend(ABC):
    """Abstract Base Class defining the Course Wallah Storage Interface."""

    @abstractmethod
    def get_root_dir(self) -> Path:
        """Returns the base data root directory."""
        pass

    @abstractmethod
    def get_dir(self, name: str) -> Path:
        """Returns the path for a standard sub-directory (e.g. temp, state, downloads)."""
        pass

    @abstractmethod
    def get_job_temp_dir(self, bot_id: str, job_id: str, user_id: Optional[Union[str, int]] = None) -> Path:
        """Returns an isolated temporary directory for a specific bot and job."""
        pass

    @abstractmethod
    def atomic_write_file(self, target_path: Union[str, Path], data: Union[str, bytes]) -> Path:
        """Atomically writes data to a target path using a temporary staging file."""
        pass

    @abstractmethod
    def atomic_move(self, src: Union[str, Path], dst: Union[str, Path]) -> Path:
        """Atomically moves/renames a completed file to its final destination."""
        pass

    @abstractmethod
    def get_disk_info(self, target_dir: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """Returns disk capacity, usage, free space, and safety flags."""
        pass

    @abstractmethod
    def check_free_space_mb(self, required_mb: float, target_dir: Optional[Union[str, Path]] = None) -> bool:
        """Verifies if sufficient free disk space exists before starting a large operation."""
        pass

    @abstractmethod
    def is_safe_path(self, target_path: Optional[Union[str, Path]]) -> bool:
        """Verifies that a path is strictly contained within managed storage bounds."""
        pass

    @abstractmethod
    def cleanup_file(self, filepath: Optional[Union[str, Path]]) -> bool:
        """Safely removes a file if contained within managed storage."""
        pass

    @abstractmethod
    def cleanup_dir(self, dir_path: Optional[Union[str, Path]]) -> bool:
        """Safely removes a directory if contained within managed storage."""
        pass

    def is_writable(self) -> bool:
        """Returns True if the managed storage root is writable."""
        return True

    def is_persistent_volume(self) -> bool:
        """Returns True if storage resides on an attached persistent volume or dedicated drive."""
        return False


class LocalStorage(StorageBackend):
    """
    Production Local Filesystem & Container Volume Storage Backend.
    Supports CW_STORAGE_DIR env var (e.g. B:\\CourseWallahData or /data).
    """

    def __init__(self, base_path: Optional[Union[str, Path]] = None):
        env_cw = os.environ.get("CW_STORAGE_DIR", "").strip()
        env_data = os.environ.get("DATA_DIR", "").strip()

        if base_path:
            p = Path(base_path)
        elif env_cw:
            p = Path(env_cw)
        elif env_data:
            p = Path(env_data)
        else:
            p = APP_ROOT / "data"

        if not p.is_absolute():
            p = (APP_ROOT / p).resolve()
        else:
            p = p.resolve()

        self.is_persistent: bool = False
        self.writable: bool = False

        # Verify writability of requested storage directory safely
        try:
            p.mkdir(parents=True, exist_ok=True)
            test_file = p / f".cw_storage_probe_{os.getpid()}_{int(time.time() * 1000)}"
            test_file.touch()
            test_file.unlink()
            self.root = p
            self.writable = True
            # Detect persistent volume: /data (Linux/Docker) or custom CW_STORAGE_DIR
            if str(p).startswith("/data") or (env_cw and Path(env_cw).resolve() == p):
                self.is_persistent = True
        except Exception as exc:
            logger.warning(f"[STORAGE] Configured path '{p}' is not accessible/writable ({exc}). Falling back to local data directory.")
            fallback = (APP_ROOT / "data").resolve()
            try:
                fallback.mkdir(parents=True, exist_ok=True)
                test_file = fallback / f".cw_storage_probe_{os.getpid()}"
                test_file.touch()
                test_file.unlink()
                self.root = fallback
                self.writable = True
            except Exception:
                import tempfile
                sys_temp = (Path(tempfile.gettempdir()) / "coursewallah_data").resolve()
                try:
                    sys_temp.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
                self.root = sys_temp
                self.writable = False

        # Standard Directory Architecture
        self.dirs: Dict[str, Path] = {
            "downloads": self.root / "downloads",
            "temp": self.root / "temp",
            "output": self.root / "output",
            "state": self.root / "state",
            "logs": self.root / "logs",
            "sessions": self.root / "sessions",
            "thumbnails": self.root / "thumbnails",
            "cache": self.root / "cache",
            "users": self.root / "users",
            "jobs": self.root / "jobs",
            "topics": self.root / "topics",
            "cookies": self.root / "cookies",
            "config": self.root / "config",
        }

        # Ensure all standard directories exist
        self.ensure_structure()

    def is_writable(self) -> bool:
        return getattr(self, "writable", True)

    def is_persistent_volume(self) -> bool:
        return getattr(self, "is_persistent", False)

    def ensure_structure(self) -> None:
        """Creates all standard storage subdirectories."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            for d in self.dirs.values():
                d.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning(f"[STORAGE] Directory creation warning: {exc}")

    def get_root_dir(self) -> Path:
        return self.root

    def get_dir(self, name: str) -> Path:
        name_clean = name.strip().lower()
        if name_clean in self.dirs:
            p = self.dirs[name_clean]
        else:
            p = self.root / name_clean
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return p

    def get_job_temp_dir(self, bot_id: str, job_id: str, user_id: Optional[Union[str, int]] = None) -> Path:
        """
        Creates and returns an isolated job temporary directory.
        Structure: <data_root>/temp/<user_id>/<bot_id>_<job_id>/ or <data_root>/temp/<bot_id>/<job_id>/
        """
        bot_clean = str(bot_id or "bot_1").strip().lower().replace("/", "_").replace("\\", "_")
        job_clean = str(job_id or f"job_{uuid.uuid4().hex[:8].upper()}").strip().replace("/", "_").replace("\\", "_")

        temp_root = self.get_dir("temp")
        if user_id is not None and str(user_id).strip():
            user_clean = str(user_id).strip().replace("/", "_").replace("\\", "_")
            job_dir = temp_root / user_clean / f"{bot_clean}_{job_clean}"
        else:
            job_dir = temp_root / bot_clean / job_clean

        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "downloads").mkdir(parents=True, exist_ok=True)
        (job_dir / "processed").mkdir(parents=True, exist_ok=True)
        (job_dir / "upload").mkdir(parents=True, exist_ok=True)
        return job_dir

    def atomic_write_file(self, target_path: Union[str, Path], data: Union[str, bytes]) -> Path:
        """
        Writes data atomically to target_path using a temporary .tmp file and atomic rename.
        """
        p = Path(target_path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp_p = p.parent / f"{p.name}.tmp.{uuid.uuid4().hex[:8]}"

        if isinstance(data, str):
            tmp_p.write_text(data, encoding="utf-8")
        else:
            tmp_p.write_bytes(data)

        # Atomic rename / replace
        try:
            tmp_p.replace(p)
        except OSError:
            # Cross-drive or Windows file lock fallback
            shutil.move(str(tmp_p), str(p))
        return p

    def atomic_move(self, src: Union[str, Path], dst: Union[str, Path]) -> Path:
        """
        Atomically moves a completed file to dst.
        """
        src_p = Path(src).resolve()
        dst_p = Path(dst).resolve()
        dst_p.parent.mkdir(parents=True, exist_ok=True)

        if not src_p.exists():
            raise FileNotFoundError(f"Source file does not exist: {src_p}")

        try:
            src_p.replace(dst_p)
        except OSError:
            shutil.move(str(src_p), str(dst_p))
        return dst_p

    def get_disk_info(self, target_dir: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """Returns safe disk space metrics."""
        check_path = Path(target_dir).resolve() if target_dir else self.root
        if not check_path.exists():
            check_path = APP_ROOT

        def _hrb(num: float) -> str:
            for unit in ["B", "KB", "MB", "GB", "TB"]:
                if abs(num) < 1024.0:
                    return f"{num:3.1f} {unit}"
                num /= 1024.0
            return f"{num:.1f} PB"

        try:
            usage = shutil.disk_usage(check_path)
            drive_letter = check_path.drive or check_path.anchor or "/"
            free_gb = usage.free / (1024 ** 3)
            total_gb = usage.total / (1024 ** 3)
            used_gb = usage.used / (1024 ** 3)
            pct_used = (usage.used / usage.total) * 100.0 if usage.total > 0 else 0.0

            return {
                "drive": drive_letter,
                "path": str(check_path),
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "total_human": _hrb(usage.total),
                "used_human": _hrb(usage.used),
                "free_human": _hrb(usage.free),
                "free_gb": free_gb,
                "total_gb": total_gb,
                "used_gb": used_gb,
                "percent_used": pct_used,
                "is_low_space": free_gb < 1.0,
            }
        except Exception as exc:
            logger.debug(f"[STORAGE] Disk usage query notice for {check_path}: {exc}")
            return {
                "drive": "Unknown",
                "path": str(check_path),
                "total_bytes": 0,
                "used_bytes": 0,
                "free_bytes": 0,
                "total_human": "0 B",
                "used_human": "0 B",
                "free_human": "0 B",
                "free_gb": 0.0,
                "total_gb": 0.0,
                "used_gb": 0.0,
                "percent_used": 0.0,
                "is_low_space": False,
            }

    def check_free_space_mb(self, required_mb: float, target_dir: Optional[Union[str, Path]] = None) -> bool:
        """Returns True if there is at least required_mb free storage."""
        info = self.get_disk_info(target_dir)
        free_mb = info.get("free_bytes", 0) / (1024 * 1024)
        if free_mb == 0 and info.get("total_bytes", 0) == 0:
            # Fallback if disk usage query is unhandled by OS/container
            return True
        return free_mb >= required_mb

    def is_safe_path(self, target_path: Optional[Union[str, Path]]) -> bool:
        """
        Safety check: Verifies target_path is within project storage root,
        downloads, temp, or system temp directory, and NEVER a system root.
        """
        if not target_path:
            return False
        try:
            resolved = Path(target_path).resolve()
            # Disallow root or drive anchor paths
            if str(resolved) == str(resolved.anchor) or len(resolved.parts) <= 1:
                return False

            import tempfile
            sys_temp = Path(tempfile.gettempdir()).resolve()
            allowed_roots = [
                self.root.resolve(),
                APP_ROOT.resolve(),
                self.get_dir("temp").resolve(),
                self.get_dir("downloads").resolve(),
                sys_temp,
            ]

            # Disallow matching root directories directly
            if resolved in allowed_roots:
                return False

            for allowed in allowed_roots:
                try:
                    resolved.relative_to(allowed)
                    return True
                except ValueError:
                    continue
            return False
        except Exception:
            return False

    def cleanup_file(self, filepath: Optional[Union[str, Path]]) -> bool:
        if not filepath:
            return False
        try:
            p = Path(filepath).resolve()
            if not p.exists() or not p.is_file():
                return False
            if self.is_safe_path(p):
                p.unlink(missing_ok=True)
                logger.debug(f"[STORAGE] Cleaned up file: {p}")
                return True
            else:
                logger.warning(f"[STORAGE CLEANUP REJECTED] Path {p} failed containment check.")
        except Exception as exc:
            logger.debug(f"[STORAGE] Error cleaning up file {filepath}: {exc}")
        return False

    def cleanup_dir(self, dir_path: Optional[Union[str, Path]]) -> bool:
        if not dir_path:
            return False
        try:
            p = Path(dir_path).resolve()
            if not p.exists() or not p.is_dir():
                return False
            if self.is_safe_path(p):
                shutil.rmtree(p, ignore_errors=True)
                logger.debug(f"[STORAGE] Cleaned up directory: {p}")
                return True
            else:
                logger.warning(f"[STORAGE CLEANUP REJECTED] Dir {p} failed containment check.")
        except Exception as exc:
            logger.debug(f"[STORAGE] Error cleaning up dir {dir_path}: {exc}")
        return False


# Global Singleton Storage Instance
_GLOBAL_STORAGE: Optional[StorageBackend] = None


def get_storage_manager() -> StorageBackend:
    """Returns the active global storage backend instance."""
    global _GLOBAL_STORAGE
    if _GLOBAL_STORAGE is None:
        _GLOBAL_STORAGE = LocalStorage()
    return _GLOBAL_STORAGE


def set_storage_manager(backend: StorageBackend) -> None:
    """Sets a custom storage backend (e.g. for testing or S3)."""
    global _GLOBAL_STORAGE
    _GLOBAL_STORAGE = backend


# Instantiate default manager
storage = get_storage_manager()
