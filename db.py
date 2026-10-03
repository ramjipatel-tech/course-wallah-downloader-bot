import os
import json
import time
import shutil
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Union, Any

from vars import (
    OWNER_ID,
    ADMINS,
    BOT_USERNAME,
    DATA_DIR,
    USERS_DIR,
    JOBS_DIR,
    STATE_DIR,
    COOKIES_DIR,
    LOGS_DIR,
    TEMP_DIR,
    CONFIG_DIR,
    TOPICS_DIR,
    USERS_STATE_FILE,
    JOBS_STATE_FILE,
    CONFIG_STATE_FILE,
    TOPICS_STATE_FILE
)

_FILE_LOCKS: Dict[str, threading.RLock] = {}
_GLOBAL_LOCK = threading.RLock()


def _get_file_lock(filepath: str) -> threading.RLock:
    norm_path = os.path.normpath(filepath)
    with _GLOBAL_LOCK:
        if norm_path not in _FILE_LOCKS:
            _FILE_LOCKS[norm_path] = threading.RLock()
        return _FILE_LOCKS[norm_path]


def atomic_json_write(filepath: str, data: Any) -> bool:
    """
    Atomically writes JSON data to disk using temporary file + os.replace.
    Thread-safe and crash-resilient.
    """
    lock = _get_file_lock(filepath)
    with lock:
        try:
            parent_dir = os.path.dirname(filepath)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            temp_file = f"{filepath}.tmp_{os.getpid()}_{int(time.time() * 1000)}"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())

            # Atomic replace
            os.replace(temp_file, filepath)
            return True
        except Exception as e:
            print(f"[STORAGE ERROR] Failed atomic write to {filepath}: {e}")
            if 'temp_file' in locals() and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
            return False


def safe_json_read(filepath: str, default_factory: Any) -> Any:
    """
    Reads JSON data safely. If file is missing or corrupted, backs it up and returns default.
    """
    lock = _get_file_lock(filepath)
    with lock:
        if not os.path.exists(filepath):
            default_val = default_factory() if callable(default_factory) else default_factory
            atomic_json_write(filepath, default_val)
            return default_val

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return default_factory() if callable(default_factory) else default_factory
                return json.loads(content)
        except Exception as e:
            print(f"[STORAGE WARNING] Corrupted JSON in {filepath} ({e}). Creating backup.")
            backup_path = f"{filepath}.corrupt_{int(time.time())}"
            try:
                shutil.copyfile(filepath, backup_path)
            except Exception:
                pass
            default_val = default_factory() if callable(default_factory) else default_factory
            atomic_json_write(filepath, default_val)
            return default_val


class Database:
    """
    Unified, reliable, database-free persistent storage platform.
    Manages users, subscriptions, forum topics, settings, and jobs with atomic JSON persistence.
    """

    def __init__(self):
        # Ensure directory structure
        for d in [DATA_DIR, USERS_DIR, JOBS_DIR, STATE_DIR, COOKIES_DIR, LOGS_DIR, TEMP_DIR, CONFIG_DIR, TOPICS_DIR]:
            os.makedirs(d, exist_ok=True)

        self.users_file = USERS_STATE_FILE
        self.jobs_file = JOBS_STATE_FILE
        self.config_file = CONFIG_STATE_FILE
        self.topics_file = TOPICS_STATE_FILE

        # Initialize state files if missing
        self._init_files()

    def _init_files(self):
        safe_json_read(self.users_file, dict)
        safe_json_read(self.jobs_file, dict)
        safe_json_read(self.config_file, dict)
        safe_json_read(self.topics_file, dict)

    # =========================================================================
    # 👑 ADMIN & AUTHORIZATION
    # =========================================================================

    def is_admin(self, user_id: int) -> bool:
        try:
            uid = int(user_id)
            return uid == OWNER_ID or uid in ADMINS
        except Exception:
            return False

    def is_banned(self, user_id: int) -> bool:
        try:
            uid = str(user_id)
            users = safe_json_read(self.users_file, dict)
            user = users.get(uid)
            return bool(user and user.get("banned", False))
        except Exception:
            return False

    def ban_user(self, user_id: int) -> bool:
        try:
            uid = str(user_id)
            users = safe_json_read(self.users_file, dict)
            if uid not in users:
                users[uid] = {
                    "user_id": int(user_id),
                    "name": f"User {user_id}",
                    "added_date": datetime.now().isoformat(),
                    "expiry_date": (datetime.now() + timedelta(days=365)).isoformat(),
                    "banned": True
                }
            else:
                users[uid]["banned"] = True
            return atomic_json_write(self.users_file, users)
        except Exception as e:
            print(f"[DB] Ban user error: {e}")
            return False

    def unban_user(self, user_id: int) -> bool:
        try:
            uid = str(user_id)
            users = safe_json_read(self.users_file, dict)
            if uid in users:
                users[uid]["banned"] = False
                return atomic_json_write(self.users_file, users)
            return True
        except Exception as e:
            print(f"[DB] Unban user error: {e}")
            return False

    def is_user_authorized(self, user_id: int, bot_username: str = BOT_USERNAME) -> bool:
        try:
            uid = int(user_id)
            if self.is_admin(uid):
                return True

            if self.is_banned(uid):
                return False

            users = safe_json_read(self.users_file, dict)
            user = users.get(str(uid))
            if not user:
                return False

            expiry_str = user.get("expiry_date")
            if not expiry_str:
                return False

            if isinstance(expiry_str, str):
                try:
                    expiry = datetime.fromisoformat(expiry_str)
                except ValueError:
                    try:
                        expiry = datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        return False
            elif isinstance(expiry_str, datetime):
                expiry = expiry_str
            else:
                return False

            return expiry > datetime.now()
        except Exception as e:
            print(f"[DB] is_user_authorized error: {e}")
            return False

    def is_channel_authorized(self, channel_id: int, bot_username: str = BOT_USERNAME) -> bool:
        return True

    # =========================================================================
    # 👤 USER MANAGEMENT
    # =========================================================================

    def add_user(self, user_id: int, name: str, days: int, bot_username: str = BOT_USERNAME) -> tuple[bool, Optional[datetime]]:
        try:
            uid = str(user_id)
            expiry_date = datetime.now() + timedelta(days=int(days))
            users = safe_json_read(self.users_file, dict)

            users[uid] = {
                "name": str(name),
                "user_id": int(user_id),
                "expiry_date": expiry_date.isoformat(),
                "added_date": datetime.now().isoformat(),
                "bot_username": str(bot_username),
                "banned": False
            }

            success = atomic_json_write(self.users_file, users)
            return success, expiry_date if success else None
        except Exception as e:
            print(f"[DB] Add User Error: {e}")
            return False, None

    def renew_user(self, user_id: int, days: int) -> tuple[bool, Optional[datetime]]:
        try:
            uid = str(user_id)
            users = safe_json_read(self.users_file, dict)
            if uid not in users:
                return False, None

            user = users[uid]
            cur_expiry_str = user.get("expiry_date")
            base_date = datetime.now()
            if cur_expiry_str:
                try:
                    cur_expiry = datetime.fromisoformat(cur_expiry_str)
                    if cur_expiry > base_date:
                        base_date = cur_expiry
                except Exception:
                    pass

            new_expiry = base_date + timedelta(days=int(days))
            user["expiry_date"] = new_expiry.isoformat()
            user["banned"] = False

            success = atomic_json_write(self.users_file, users)
            return success, new_expiry if success else None
        except Exception as e:
            print(f"[DB] Renew User Error: {e}")
            return False, None

    def remove_user(self, user_id: int, bot_username: str = BOT_USERNAME) -> bool:
        try:
            uid = str(user_id)
            users = safe_json_read(self.users_file, dict)
            if uid in users:
                del users[uid]
                return atomic_json_write(self.users_file, users)
            return False
        except Exception as e:
            print(f"[DB] Remove User Error: {e}")
            return False

    def list_users(self, bot_username: str = BOT_USERNAME) -> List[dict]:
        try:
            users = safe_json_read(self.users_file, dict)
            res = []
            for u in users.values():
                res.append({
                    "name": u.get("name", "Unknown"),
                    "user_id": u.get("user_id"),
                    "expiry_date": u.get("expiry_date"),
                    "banned": u.get("banned", False)
                })
            return res
        except Exception:
            return []

    def get_user(self, user_id: int, bot_username: str = BOT_USERNAME) -> Optional[dict]:
        try:
            users = safe_json_read(self.users_file, dict)
            return users.get(str(user_id))
        except Exception:
            return None

    def get_user_expiry_info(self, user_id: int, bot_username: str = BOT_USERNAME) -> Optional[dict]:
        try:
            user = self.get_user(user_id, bot_username)
            if not user:
                return None
            expiry_str = user.get("expiry_date")
            expiry = None
            if expiry_str:
                try:
                    expiry = datetime.fromisoformat(expiry_str)
                except Exception:
                    try:
                        expiry = datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass

            days_left = (expiry - datetime.now()).days if expiry else 0
            return {
                "name": user.get("name", "Unknown"),
                "user_id": int(user_id),
                "expiry_date": expiry.strftime("%d-%m-%Y") if expiry else "N/A",
                "days_left": max(0, days_left),
                "is_active": days_left > 0,
                "banned": user.get("banned", False)
            }
        except Exception:
            return None

    def list_bot_usernames(self) -> List[str]:
        try:
            users = safe_json_read(self.users_file, dict)
            usernames = list({u.get("bot_username") for u in users.values() if u.get("bot_username")})
            return usernames if usernames else ["course_wallah_official_bot"]
        except Exception:
            return ["course_wallah_official_bot"]

    async def cleanup_expired_users(self, bot=None) -> int:
        removed = 0
        now = datetime.now()
        users = safe_json_read(self.users_file, dict)
        to_del = []

        for uid, u in list(users.items()):
            if int(uid) == OWNER_ID or int(uid) in ADMINS:
                continue
            exp_str = u.get("expiry_date")
            if not exp_str:
                continue
            try:
                exp = datetime.fromisoformat(exp_str)
            except Exception:
                try:
                    exp = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue

            if exp <= now:
                to_del.append(uid)

        for uid in to_del:
            del users[uid]
            removed += 1
            if bot:
                try:
                    await bot.send_message(
                        int(uid),
                        "<b>⚠️ Your Subscription Has Expired</b>\n\n<blockquote>Your access has ended. Contact admin to renew your plan.</blockquote>"
                    )
                except Exception:
                    pass

        if removed > 0:
            atomic_json_write(self.users_file, users)
        return removed

    # =========================================================================
    # 📌 TOPICS & FORUM MAPPINGS
    # =========================================================================

    def get_unit_topic(self, chat_id: Union[int, str], subject: str, unit_key: str, bot_id: Optional[str] = None) -> Optional[int]:
        return self.get_topic_id(chat_id, f"{str(subject).strip().lower()}:{str(unit_key).strip().lower()}", bot_id=bot_id)

    def save_unit_topic(self, chat_id: Union[int, str], subject: str, unit_key: str, thread_id: int, bot_id: Optional[str] = None) -> bool:
        return self.save_topic_id(chat_id, f"{str(subject).strip().lower()}:{str(unit_key).strip().lower()}", thread_id, bot_id=bot_id)

    def get_topic_id(self, chat_id: Union[int, str], topic_key: str, bot_id: Optional[str] = None) -> Optional[int]:
        try:
            topics = safe_json_read(self.topics_file, dict)
            if bot_id:
                k_bot = f"{str(bot_id).strip().lower()}:{str(chat_id).strip()}:{str(topic_key).strip().lower()}"
                return topics.get(k_bot)
            # Standard / legacy fallback
            k_legacy = f"{str(chat_id).strip()}:{str(topic_key).strip().lower()}"
            return topics.get(k_legacy)
        except Exception:
            return None

    def save_topic_id(self, chat_id: Union[int, str], topic_key: str, thread_id: int, bot_id: Optional[str] = None) -> bool:
        try:
            topics = safe_json_read(self.topics_file, dict)
            if bot_id:
                k = f"{str(bot_id).strip().lower()}:{str(chat_id).strip()}:{str(topic_key).strip().lower()}"
            else:
                k = f"{str(chat_id).strip()}:{str(topic_key).strip().lower()}"
            topics[k] = int(thread_id)
            return atomic_json_write(self.topics_file, topics)
        except Exception as e:
            print(f"[DB] Save topic error: {e}")
            return False

    def get_forum_group(self, bot_id: Optional[str] = None) -> Optional[int]:
        if bot_id:
            val = self.get_setting(f"target_forum_group_{str(bot_id).strip().lower()}")
            if val is not None:
                try:
                    return int(val)
                except Exception:
                    pass
        val = self.get_setting("target_forum_group")
        if val is not None:
            try:
                return int(val)
            except Exception:
                return None
        return None

    def set_forum_group(self, group_id: int, bot_id: Optional[str] = None) -> bool:
        if bot_id:
            return self.set_setting(f"target_forum_group_{str(bot_id).strip().lower()}", int(group_id))
        return self.set_setting("target_forum_group", int(group_id))

    def get_storage_stats(self) -> dict:
        """Returns byte counts and file counts across storage categories."""
        stats = {
            "temp_bytes": 0,
            "temp_files": 0,
            "state_bytes": 0,
            "jobs_count": 0,
            "users_count": 0,
            "topics_count": 0,
            "logs_bytes": 0
        }
        try:
            for root, _, files in os.walk(TEMP_DIR):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        stats["temp_bytes"] += os.path.getsize(fp)
                        stats["temp_files"] += 1
                    except Exception:
                        pass

            for root, _, files in os.walk(LOGS_DIR):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        stats["logs_bytes"] += os.path.getsize(fp)
                    except Exception:
                        pass

            for root, _, files in os.walk(STATE_DIR):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        stats["state_bytes"] += os.path.getsize(fp)
                    except Exception:
                        pass

            stats["jobs_count"] = len(safe_json_read(self.jobs_file, dict))
            stats["users_count"] = len(safe_json_read(self.users_file, dict))
            stats["topics_count"] = len(safe_json_read(self.topics_file, dict))
        except Exception:
            pass
        return stats

    def get_channel_config(self, chat_id: Union[int, str]) -> dict:
        try:
            k = f"channel_{str(chat_id).strip()}"
            cfg = safe_json_read(self.config_file, dict)
            return cfg.get(k, {})
        except Exception:
            return {}

    def set_channel_config(self, chat_id: Union[int, str], config_dict: dict) -> bool:
        try:
            k = f"channel_{str(chat_id).strip()}"
            cfg = safe_json_read(self.config_file, dict)
            cfg[k] = config_dict
            return atomic_json_write(self.config_file, cfg)
        except Exception as e:
            print(f"[DB] Set channel config error: {e}")
            return False

    def list_all_topics(self, bot_id: Optional[str] = None) -> dict:
        topics = safe_json_read(self.topics_file, dict)
        if not bot_id:
            return topics
        prefix = f"{str(bot_id).strip().lower()}:"
        return {k: v for k, v in topics.items() if k.startswith(prefix)}

    # =========================================================================
    # ⚙️ CONFIGURATION & SETTINGS
    # =========================================================================

    def get_setting(self, key: str, default: Any = None) -> Any:
        try:
            cfg = safe_json_read(self.config_file, dict)
            return cfg.get(key, default)
        except Exception:
            return default

    def set_setting(self, key: str, value: Any) -> bool:
        try:
            cfg = safe_json_read(self.config_file, dict)
            cfg[key] = value
            return atomic_json_write(self.config_file, cfg)
        except Exception as e:
            print(f"[DB] Set setting error: {e}")
            return False

    def get_all_settings(self) -> dict:
        return safe_json_read(self.config_file, dict)

    def get_log_channel(self, bot_username: str = BOT_USERNAME):
        return self.get_setting(f"log_channel_{bot_username}")

    def set_log_channel(self, bot_username: str, channel_id: int) -> bool:
        return self.set_setting(f"log_channel_{bot_username}", int(channel_id))

    # =========================================================================
    # 📦 JOBS PERSISTENCE & CHECKPOINTS
    # =========================================================================

    def save_job(self, job_dict: dict) -> bool:
        """
        Saves job state to both the global jobs index and individual job file.
        """
        try:
            job_id = job_dict.get("job_id")
            if not job_id:
                return False

            # Update timestamp
            job_dict["updated_at"] = datetime.now().isoformat()

            # 1. Save individual job file
            ind_file = os.path.join(JOBS_DIR, f"{job_id}.json")
            atomic_json_write(ind_file, job_dict)

            # 2. Update jobs master index
            jobs = safe_json_read(self.jobs_file, dict)
            jobs[job_id] = {
                "job_id": job_id,
                "bot_id": job_dict.get("bot_id", "bot_1"),
                "bot_username": job_dict.get("bot_username", ""),
                "user_id": job_dict.get("user_id"),
                "chat_id": job_dict.get("chat_id"),
                "status": job_dict.get("status"),
                "phase": job_dict.get("phase"),
                "title": job_dict.get("batch_name") or job_dict.get("title"),
                "total_items": job_dict.get("total_items", 0),
                "current_index": job_dict.get("current_index", 0),
                "completed_count": len(job_dict.get("completed_indices", [])),
                "failed_count": len(job_dict.get("failed_indices", [])),
                "created_at": job_dict.get("created_at"),
                "updated_at": job_dict.get("updated_at")
            }
            atomic_json_write(self.jobs_file, jobs)
            return True
        except Exception as e:
            print(f"[DB] Save job error: {e}")
            return False

    def _matches_bot(self, j: dict, target_bot_id: Optional[str]) -> bool:
        """
        Strictly checks whether a job dictionary belongs to target_bot_id.
        If target_bot_id is provided, jobs from other bots MUST NEVER match.
        """
        if target_bot_id is None:
            return True
        t_id = str(target_bot_id).strip().lower()
        j_bot_id = str(j.get("bot_id", "") or "").strip().lower()
        j_bot_uname = str(j.get("bot_username", "") or "").strip().lower()
        if j_bot_id and j_bot_id == t_id:
            return True
        if j_bot_uname and j_bot_uname == t_id:
            return True
        if not j_bot_id and t_id in ("bot_1", ""):
            return True
        return False

    def get_job(self, job_id: str, bot_id: Optional[str] = None) -> Optional[dict]:
        try:
            job_data = None
            ind_file = os.path.join(JOBS_DIR, f"{job_id}.json")
            if os.path.exists(ind_file):
                job_data = safe_json_read(ind_file, dict)
            else:
                jobs = safe_json_read(self.jobs_file, dict)
                job_data = jobs.get(job_id)
            if job_data and bot_id is not None:
                if not self._matches_bot(job_data, bot_id):
                    return None
            return job_data
        except Exception:
            return None

    def list_jobs(self, user_id: Optional[int] = None, status: Optional[str] = None, bot_id: Optional[str] = None) -> List[dict]:
        try:
            jobs = safe_json_read(self.jobs_file, dict)
            res = []
            for j in jobs.values():
                if user_id is not None and str(j.get("user_id")) != str(user_id):
                    continue
                if status is not None and str(j.get("status")).upper() != str(status).upper():
                    continue
                if bot_id is not None and not self._matches_bot(j, bot_id):
                    continue
                res.append(j)
            return res
        except Exception:
            return []

    def get_user_active_job(self, user_id: int, bot_id: Optional[str] = None) -> Optional[dict]:
        try:
            jobs = safe_json_read(self.jobs_file, dict)
            active_statuses = {"QUEUED", "DOWNLOADING", "PROCESSING", "UPLOADING", "RECOVERING"}
            for j in jobs.values():
                if str(j.get("user_id")) == str(user_id) and j.get("status") in active_statuses:
                    if bot_id is not None and not self._matches_bot(j, bot_id):
                        continue
                    return self.get_job(j["job_id"], bot_id=bot_id)
            return None
        except Exception:
            return None

    def get_user_paused_job(self, user_id: int, bot_id: Optional[str] = None) -> Optional[dict]:
        try:
            jobs = safe_json_read(self.jobs_file, dict)
            for j in jobs.values():
                if str(j.get("user_id")) == str(user_id) and j.get("status") == "PAUSED":
                    if bot_id is not None and not self._matches_bot(j, bot_id):
                        continue
                    return self.get_job(j["job_id"], bot_id=bot_id)
            return None
        except Exception:
            return None

    def get_unfinished_jobs(self, bot_id: Optional[str] = None) -> List[dict]:
        try:
            jobs = safe_json_read(self.jobs_file, dict)
            unfinished_statuses = {"DOWNLOADING", "PROCESSING", "UPLOADING", "RECOVERING", "QUEUED"}
            res = []
            for j in jobs.values():
                if j.get("status") in unfinished_statuses:
                    if bot_id is not None and not self._matches_bot(j, bot_id):
                        continue
                    full_job = self.get_job(j["job_id"], bot_id=bot_id)
                    if full_job:
                        res.append(full_job)
            return res
        except Exception:
            return []

    def delete_job(self, job_id: str) -> bool:
        try:
            jobs = safe_json_read(self.jobs_file, dict)
            if job_id in jobs:
                del jobs[job_id]
                atomic_json_write(self.jobs_file, jobs)

            ind_file = os.path.join(JOBS_DIR, f"{job_id}.json")
            if os.path.exists(ind_file):
                try:
                    os.remove(ind_file)
                except Exception:
                    pass
            return True
        except Exception as e:
            print(f"[DB] Delete job error: {e}")
            return False

    # =========================================================================
    # 🍪 PER-USER YOUTUBE COOKIES MANAGEMENT
    # =========================================================================

    def get_user_cookies_path(self, user_id: int) -> str:
        u_dir = os.path.join(USERS_DIR, str(user_id))
        os.makedirs(u_dir, exist_ok=True)
        return os.path.join(u_dir, "youtube_cookies.txt")

    def save_user_cookies(self, user_id: int, content: str) -> bool:
        try:
            path = self.get_user_cookies_path(user_id)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"[DB] Save user cookies error: {e}")
            return False

    def has_user_cookies(self, user_id: int) -> bool:
        path = self.get_user_cookies_path(user_id)
        return os.path.exists(path) and os.path.getsize(path) > 10

    def delete_user_cookies(self, user_id: int) -> bool:
        try:
            path = self.get_user_cookies_path(user_id)
            if os.path.exists(path):
                os.remove(path)
                return True
            return False
        except Exception as e:
            print(f"[DB] Delete user cookies error: {e}")
            return False

    def get_user_cookies_info(self, user_id: int) -> dict:
        path = self.get_user_cookies_path(user_id)
        if os.path.exists(path):
            mtime = os.path.getmtime(path)
            dt = datetime.fromtimestamp(mtime).strftime("%d-%m-%Y %H:%M:%S")
            size = os.path.getsize(path)
            return {
                "configured": True,
                "updated_at": dt,
                "size_bytes": size
            }
        return {
            "configured": False,
            "updated_at": None,
            "size_bytes": 0
        }


# Global database instance
db = Database()
