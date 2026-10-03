# 🎓 Course Wallah Downloader

A high-performance, asynchronous Telegram bot engineered for batch course processing, high-speed encrypted stream downloading, academic topic categorization, PDF watermarking, and zero-database local JSON state persistence.

Built for **24x7 unattended production deployment** on Linux VMs using `systemd`.

---

## ⚡ Key Features

- **🚀 Concurrent Asynchronous Engine**: Controlled semaphore-based queues (4 concurrent downloads, 3 concurrent uploads, per-user isolation for 5–6 active users).
- **💾 Database-Free Atomic JSON Persistence**: Crash-resilient local persistence for users, subscriptions, forum topics, jobs, and settings. No MongoDB, PostgreSQL, or external databases needed.
- **🔄 Smart Stop, Resume & Auto-Recovery**:
  - `/stop`: Pauses download and saves exact progress checkpoint.
  - `/resume`: Continues interrupted downloads from the last processed item.
  - `/cancel`: Cancels download and cleans up temporary files safely.
  - **Crash Recovery**: Automatically detects interrupted jobs upon bot restart and resumes execution seamlessly.
- **🏛️ Real Telegram Forum Supergroup Routing**:
  - Automatically creates and maps top-level Telegram Forum Topics (`message_thread_id`) organized by Subject, Unit, and Topic (`RECORDED — SUBJECT — UNIT — TOPIC` / `LIVE — SUBJECT — UNIT — TOPIC`).
  - Persistent topic caching prevents duplicate topic creation.
- **📄 PDF Download & Watermarking Pipeline**:
  - Validates PDF structure and page count using PyMuPDF.
  - Applies branded header/footer watermarks with custom text and font styling.
- **🍪 Per-User YouTube Cookies**:
  - Fully isolated per-user cookie files in `data/cookies/{user_id}.txt`.
  - Commands: `/cookies`, `/getcookies`, `/deletecookies`.
  - Cookie contents are strictly protected and never exposed in logs.
- **🔒 DRM & API Security**:
  - Non-invasive: Gracefully detects DRM-protected media (`drmProtected == 1`) and notifies users without attempting unauthorized bypass.
  - Safe subprocess execution eliminates shell injection vulnerabilities.
- **📊 Real-Time Throttled Progress Cards**:
  - Dynamic ASCII progress bar with animated spinner.
  - Accurate speed and ETA calculation based on real bytes transferred and elapsed time.
  - 1.5–1.8s throttled edits with automatic Telegram FloodWait handling.

---

## 📂 Project Architecture & Directory Structure

```
course-wallah-downloader/
│
├── main.py                        # Main Telegram bot entrypoint & command dispatchers
├── vars.py                        # Environment configuration and directory definitions
├── db.py                          # Atomic JSON storage engine (Zero-DB persistence)
├── job_manager.py                 # Concurrency coordination, checkpoints & crash recovery
├── academic_parser.py             # Academic hierarchy (.txt course structure) parser
├── itsgolu.py                     # Media downloaders, M3U8 decryptors & FFmpeg handlers
├── auth.py                        # Subscription management & user authorization
├── clean.py                       # Automated cleanup utility for temporary files & expired users
├── html_handler.py                # Batch HTML course player generator
├── utils.py                       # Progress tracking, byte/time formatters, speed calculations
├── logs.py                        # Rotating file logger setup
│
├── requirements.txt               # Production Python dependencies
├── .env.example                   # Configuration template with placeholders
├── .gitignore                     # Git ignore rules protecting secrets & state
├── font.otf                       # Custom font for PDF watermarking
│
├── deploy/                        # Linux VM deployment package
│   ├── course-wallah.service      # systemd service unit file
│   ├── course-wallah.service.example # Template service unit file
│   ├── setup_vm.sh                # Fresh VM automated setup script (Ubuntu/Debian)
│   └── update.sh                  # Safe production git pull & restart script
│
├── scripts/                       # Operational scripts
│   └── healthcheck.sh             # Production health check script
│
├── assets/                        # Static assets (images, branding)
│   └── start/                     # Welcome banner images
│
├── data/                          # Persistent local storage (Ignored by Git)
│   ├── state/                     # bot_session.session, users.json, jobs.json, topics.json
│   ├── cookies/                   # Per-user isolated YouTube cookie files
│   ├── jobs/                      # Detailed job checkpoint files
│   ├── users/                     # User profile metadata
│   ├── topics/                    # Forum topic mappings
│   ├── config/                    # Bot settings
│   ├── logs/                      # Rotating log files (bot.log)
│   └── temp/                      # Per-job isolated temporary download folders
│
└── tests/
    ├── test_platform.py           # Unit & integration test suite
    ├── test_academic_parser.py    # Academic course parsing test suite
    ├── telegram_dispatch_test.py  # Pyrofork command & listener dispatch tests
    ├── live_smoke_test.py         # End-to-end runtime smoke test
    └── pdf_pipeline_test_fixed.py # Standalone PDF downloader & watermark test
```

---

## 📋 System Requirements

- **Operating System**: Linux (Ubuntu 20.04+, Debian 11+, CentOS/RHEL 8+) or macOS / Windows (for local development)
- **Python**: Python 3.10, 3.11, 3.12, or 3.13+
- **System Binaries**:
  - `ffmpeg` & `ffprobe` (Required for video processing, stream copying, and watermarking)
  - `aria2` (Optional, for multi-threaded downloads)
  - `git` (For version control and updates)

---

## ⚙️ Environment Configuration

Copy the example configuration file and fill in your details:

```bash
cp .env.example .env
nano .env
```

### Key Configuration Variables

| Variable | Required | Default | Description |
| :--- | :---: | :--- | :--- |
| `API_ID` | **Yes** | — | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | **Yes** | — | Telegram API Hash from [my.telegram.org](https://my.telegram.org) |
| `BOT_TOKEN` | **Yes** | — | Telegram Bot Token from [@BotFather](https://t.me/BotFather) |
| `BOT_1_TOKEN` .. `BOT_N_TOKEN` | No | — | Multi-bot tokens for concurrent bot clients in 1 process |
| `OWNER_ID` | **Yes** | — | Telegram User ID of the primary owner |
| `ADMINS` | No | `OWNER_ID` | Comma-separated Telegram User IDs for bot admins |
| `CW_STORAGE_DIR` | No | `./data` | Central storage directory (Windows path, Linux `/data`, Volume mount) |
| `WATERMARK_TEXT` | No | `"Course Wallah"` | Branded watermark text on videos and PDFs |
| `WATERMARK_CRF` | No | `26` | libx264 Constant Rate Factor (23–28 for optimal size/quality) |
| `DOWNLOAD_WORKERS` | No | `4` | Maximum simultaneous download workers |
| `UPLOAD_WORKERS` | No | `3` | Maximum simultaneous Telegram upload workers |
| `MAX_ACTIVE_USERS` | No | `6` | Maximum concurrent users allowed active jobs |
| `FORUM_CHAT_ID` | No | `""` | Supergroup ID for Telegram Forum topic routing |
| `FFMPEG_PATH` | No | `ffmpeg` | Path to FFmpeg binary |
| `WEB_SERVER` | No | `False` | Enable lightweight health check web server |

---

## 🚀 Local Installation & Running

1. **Clone the repository:**
   ```bash
   git clone <REPOSITORY_URL>
   cd course-wallah-downloader
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate   # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

5. **Start the bot:**
   ```bash
   python main.py
   ```

---

## 🧪 Running Tests

Execute the automated test suites locally:

```bash
# Run complete platform tests (Persistence, Subscriptions, Cookies, Concurrency)
python test_platform.py

# Run Pyrofork command and listener dispatch tests
python telegram_dispatch_test.py

# Run academic course parsing tests
python test_academic_parser.py

# Run end-to-end runtime smoke test
python live_smoke_test.py
```

---

## 🛡️ Security Best Practices

- **Zero Secret Commits**: All sensitive tokens, API hashes, session files (`*.session`), cookie files (`*.txt`, `*.cookie`), and state databases are ignored by `.gitignore`.
- **Credential Sanitization**: The codebase uses strictly parameterized environment lookups without hardcoded fallbacks.
- **Log Privacy**: Sensitive credentials, auth tokens, session tokens, and YouTube cookies are never written to log files.
- **Per-User Isolation**: Cookie files and temporary download folders are strictly segregated by `user_id`.

---

---

## 🚀 One-Click Cloud & Container Deployment

### Deploy on Render
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/ramjipatel-tech/course-wallah-downloader)

> [!NOTE]
> Render deployment uses `render.yaml` to provision a **Docker Background Worker** with an attached 10 GB persistent disk for `/app/data`. Note that 24x7 background workers require a Render Starter+ plan.

---

### Deploy on Heroku
[![Deploy to Heroku](https://www.herokucdn.com/deploy/button.svg)](https://www.heroku.com/deploy?template=https://github.com/ramjipatel-tech/course-wallah-downloader)

> [!NOTE]
> Since this repository is private, ensure your Heroku account has access to the repository or clone and deploy via the Heroku Container Registry (`heroku container:push worker && heroku container:release worker`).

---

### Deploy on Railway

1. Open [Railway Dashboard](https://railway.app/new).
2. Click **Deploy from GitHub repo** and select `ramjipatel-tech/course-wallah-downloader`.
3. Add a Persistent Volume mounted to `/app/data`.
4. Configure environment variables (`API_ID`, `API_HASH`, `BOT_TOKEN`, `OWNER_ID`).
5. *(Optional)* To publish as a public template: In Railway Project Settings ➔ Template ➔ Publish Template, and replace this section with your published Railway Template button:
   ```markdown
   [![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template/YOUR_TEMPLATE_ID)
   ```

---

## 📖 Comprehensive Deployment Guide

For full platform comparison, hardware requirements, pricing breakdown, Docker Compose examples, and Linux VM systemd configuration, see [DEPLOY.md](file:///c:/Users/ramji/OneDrive/Desktop/downloader%20bot/DEPLOY.md).
