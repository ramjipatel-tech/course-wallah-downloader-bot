<div align="center">

# 𝄟⃝⚡️ COURSE WALLAH DOWNLOADER BOT 🎓🔥

### High-Performance • Multi-Platform • Dynamic Multi-Bot • Single-Pass Watermarking • Zero-Database Architecture

[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.14-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Docker Build](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-2CA5E0?logo=telegram&logoColor=white)](https://t.me/course_wallah_official_bot)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

<p align="center">
  <b>A production-grade, hardened Telegram bot built for large-scale academic batch processing, encrypted HLS/Spayee/KGS/M3U8 video downloading, single-pass drawtext watermarking, automated PDF unlocker & branding, and dynamic multi-bot scaling.</b>
</p>

---

### 🚀 One-Click Cloud Deployments

Deploy your own instance in less than 60 seconds with full persistence and health checks:

| Platform | One-Click Deployment Button | Supported Tier | Storage Model |
| :--- | :---: | :---: | :---: |
| **Railway** | [![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template?template=https%3A%2F%2Fgithub.com%2Framjipatel-tech%2Fcourse-wallah-downloader-bot) | Free Trial / Hobby / Pro | Persistent Volume (`/data`) |
| **Render** | [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/ramjipatel-tech/course-wallah-downloader-bot) | Starter Worker / Standard | Persistent Disk (`/data`) |
| **Heroku** | [![Deploy to Heroku](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/ramjipatel-tech/course-wallah-downloader-bot) | Eco / Basic / Standard | Ephemeral Container |

---

</div>

## 🌟 Key Highlights & Core Capabilities

- **🤖 Dynamic Multi-Bot Single-Process Architecture**:
  - Run **1 to 10+ independent bot tokens** concurrently within **one single Python process**.
  - Dynamically scales based on configured tokens (`BOT_TOKEN` or `BOT_1_TOKEN`, `BOT_2_TOKEN`, etc.). Unset tokens are skipped gracefully without crashing.
  - Isolated contexts, SQLite sessions in `CW_STORAGE_DIR/sessions/`, separate queues, and independent job progress.
- **🎨 Lightning-Fast Single-Pass Watermark Engine**:
  - Burn text or image watermarks in a single pass using `libx264 ultrafast` and hardware acceleration (NVENC, VAAPI, QSV).
  - Strictly preserves source duration (diff $\le 0.10$s), native resolution, FPS, and audio synchronization without double-encoding.
- **💾 500 MB Low-Disk Optimized Storage Engine**:
  - **Zero Accumulation Pipeline**: Intermediate video segments, `.ts` chunks, un-watermarked source files, and split parts are **immediately deleted from disk** after processing and upload confirmation.
  - Safe for memory-constrained and ephemeral cloud disks (Render, Railway, Heroku).
- **🗄️ Zero External Database Requirement**:
  - Completely self-contained with atomic, thread-safe JSON persistence for users, jobs, subscription expiry, settings, and forum topics.
  - Zero external DB overhead (no MongoDB, Redis, or PostgreSQL required).
- **🏛️ Real Telegram Forum Supergroup Routing**:
  - Automatically creates and maps top-level Telegram Forum Topics (`message_thread_id`) organized by Subject and Unit (`RECORDED — SUBJECT — UNIT` / `LIVE — SUBJECT — UNIT`).
  - Caches topic IDs to prevent duplicate topic creation.
- **🔄 Fault-Tolerant Crash Resumption**:
  - Automatically recovers and resumes interrupted downloads across restarts using atomic JSON checkpoint files.
- **📄 Complete PDF Decryption & Watermarking**:
  - Automatically detects password-protected PDFs and unlocks them using smart password candidates (`user_id`, phone, batch IDs).
  - Embeds custom header/footer branding into every page using PyMuPDF.
- **🌐 Built-in HTTP Health Server**:
  - Lightweight background HTTP server listening on `$PORT` to satisfy Railway, Render, and Heroku web health probes.

---

## 🏗️ Architecture & Storage Layout

```
<CW_STORAGE_DIR>/ (default: /data or ./data)
├── downloads/      # Downloaded raw media before processing (deleted immediately post-process)
├── temp/           # Per-bot and per-job isolated working directories
│   ├── bot_1/
│   └── bot_2/
├── output/         # Processed final artifacts
├── state/          # users.json, jobs.json, topics.json, config.json (Atomic JSON State)
├── logs/           # bot.log rotating logs
├── sessions/       # Pyrogram .session SQLite databases (Persistent across restarts)
├── thumbnails/     # Cached video thumbnails
├── cache/          # Watermark & runtime caches
├── users/          # User subscription data
└── jobs/           # Active job checkpoints
```

---

## ⚙️ Environment Variables Reference

| Variable | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `API_ID` | **Yes** | — | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | **Yes** | — | Telegram API Hash from [my.telegram.org](https://my.telegram.org) |
| `BOT_TOKEN` | Optional | — | Primary Bot Token from [@BotFather](https://t.me/BotFather) (or use `BOT_1_TOKEN`) |
| `BOT_1_TOKEN` | Optional | — | Bot 1 Token for Multi-Bot deployment |
| `BOT_2_TOKEN` | Optional | — | Bot 2 Token (Optional: starts Bot 2 dynamically) |
| `BOT_3_TOKEN` | Optional | — | Bot 3 Token (Optional: starts Bot 3 dynamically) |
| `OWNER_ID` | **Yes** | — | Numeric Telegram User ID of the primary owner |
| `ADMINS` | No | `OWNER_ID` | Comma-separated list of admin Telegram IDs |
| `CW_STORAGE_DIR` | No | `/data` | Base storage path (e.g. `/data` on Railway/Render, or `B:\CourseWallahData`) |
| `WATERMARK_TEXT` | No | `Course Wallah` | Text to display on watermarked videos & PDFs |
| `WATERMARK_CRF` | No | `26` | Video encoding CRF value (lower = higher quality, 23–28 recommended) |
| `DOWNLOAD_WORKERS`| No | `4` | Number of concurrent download workers |
| `UPLOAD_WORKERS` | No | `3` | Number of concurrent upload workers |
| `MAX_ACTIVE_USERS`| No | `6` | Maximum concurrent users allowed simultaneously |
| `FORUM_CHAT_ID` | No | — | Optional Telegram Supergroup Chat ID with Forum Topics enabled |
| `PORT` | No | `8080` | Port for background HTTP health check server (auto-configured by Railway/Render/Heroku) |

---

## 🛠️ Deployment Methods

### 1. Railway (Recommended Cloud Container)

1. Click the **Deploy on Railway** button above or link your GitHub repository.
2. In Railway Dashboard, attach a **Persistent Volume** mounted at `/data`.
3. Add your Environment Variables (`API_ID`, `API_HASH`, `BOT_1_TOKEN`, `OWNER_ID`).
4. Railway will automatically build the Dockerfile and start `python main.py`.

---

### 2. Render (Blueprint / Background Worker)

1. Click the **Deploy to Render** button above.
2. Select **Worker** with **Docker** runtime.
3. Configure your Environment Variables in the Render dashboard.
4. Render automatically mounts a 10 GB persistent disk at `/data` as defined in [`render.yaml`](render.yaml).

---

### 3. Linux VM / VPS (Ubuntu / Debian / systemd)

```bash
# 1. Update system & install binaries
sudo apt-get update -y && sudo apt-get install -y python3 python3-pip python3-venv ffmpeg aria2 git fonts-dejavu-core

# 2. Clone repository
git clone https://github.com/ramjipatel-tech/course-wallah-downloader-bot.git /opt/course-wallah
cd /opt/course-wallah

# 3. Setup Virtual Environment
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. Configure Environment
cp .env.example .env
nano .env

# 5. Setup systemd Service
sudo cp deploy/course-wallah.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now course-wallah
sudo systemctl status course-wallah
```

---

### 4. Docker Self-Hosted

```bash
# Build image
docker build -t course-wallah-downloader-bot .

# Run container with persistent host storage mount
docker run -d \
  --name course-wallah-bot \
  --restart unless-stopped \
  -v /opt/data:/data \
  -e API_ID=12345678 \
  -e API_HASH=your_api_hash \
  -e BOT_1_TOKEN=your_bot_token \
  -e OWNER_ID=123456789 \
  -e CW_STORAGE_DIR=/data \
  course-wallah-downloader-bot
```

---

### 5. Local Windows Development

```powershell
# 1. Clone repository
git clone https://github.com/ramjipatel-tech/course-wallah-downloader-bot.git
cd course-wallah-downloader-bot

# 2. Setup Virtual Environment
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# 3. Create .env from template
copy .env.example .env

# 4. Run application
python main.py
```

---

## 📖 Bot Commands Reference

### 👤 User Commands

| Command | Description |
| :--- | :--- |
| `/start` | View welcome card, active subscription status, and system info |
| `/drm` | Process and batch download courses from structured `.txt` files or URLs |
| `/status` | Check live progress of active download and processing tasks |
| `/stop` | Pause the active download and save an exact resume checkpoint |
| `/resume` | Resume the paused download from the last completed item |
| `/cancel` | Cancel active task and safely wipe job temporary directory |
| `/cookies` | Upload private YouTube `cookies.txt` file |
| `/getcookies` | Inspect cookie status and validity |
| `/deletecookies` | Delete stored YouTube cookie file |
| `/plan` | View your subscription details and expiry date |
| `/t2t` | Convert raw text into a downloadable `.txt` file |
| `/t2h` | Convert course TXT links into an offline HTML Web Player |
| `/id` | Show your Telegram User ID and current Chat ID |
| `/help` | Display interactive help manual |

### 👑 Admin Commands

| Command | Description |
| :--- | :--- |
| `/admin` | Open interactive administrative control panel |
| `/users` | List all authorized subscribers across all bots |
| `/add <id> <days>` | Authorize a user for specified number of days |
| `/remove <id>` | Revoke a user's authorization |
| `/renew <id> <days>`| Extend an existing subscriber's plan |
| `/ban <id>` / `/unban` | Ban or unban a user across all bot instances |
| `/jobs` | Monitor active and recently processed tasks |
| `/stopall` / `/resumeall` | Pause or resume all ongoing downloads globally |
| `/broadcast` | Send announcement message to all registered users |
| `/stats` | View download metrics, speeds, and success rates |
| `/system` | Display CPU, RAM, disk, FFmpeg, and Python diagnostics |
| `/workers` | View and adjust live concurrency slots |
| `/storage` | Detailed storage breakdown across jobs, downloads, and temp files |
| `/cleanup` | Trigger garbage collection on temporary media and expired users |
| `/setgroup <id>` | Configure Telegram Supergroup Forum destination |
| `/topics` | List cached topic IDs mapped to subjects and units |
| `/setwatermark <text>`| Update global video watermark text |
| `/restart` | Safely restart bot processes |

---

## 🧪 Testing & Quality Assurance

This codebase includes a comprehensive test suite of **281 automated unit and integration tests**:

```bash
# Run all test suites
python -m unittest discover -p "test_*.py"
```

All 281 tests validate:
- ✅ Multi-Bot independent concurrency & token skipping
- ✅ Atomic JSON state serialization & lock protection
- ✅ Single-pass watermark precision & timing diffs
- ✅ Memory & disk safety with 500MB container bounds
- ✅ Forum topic resolution and caching
- ✅ PDF unlocker & header/footer watermarking

---

## 📜 License

This project is licensed under the **MIT License**.

---

<div align="center">
  <b>Developed with ❤️ for High-Throughput Academic Video Automation</b>
</div>
