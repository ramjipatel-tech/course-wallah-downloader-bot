# 🚀 Multi-Platform Production Deployment Guide
## Project: Course Wallah Downloader

This guide covers complete, production-grade deployment of **Course Wallah Downloader** across diverse deployment platforms, including Windows Local, Linux VMs (systemd), Docker hosts, Render, Railway, and Heroku.

---

## 📊 Supported Deployment Targets Comparison Matrix

| Deployment Target | Cost Tier | 24x7 Always-On Support | Persistent Storage Support | Recommended Specs | Best Suited For |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Windows Local** | Free (Local PC/Laptop) | ✅ Always-On while PC runs | ✅ Native Persistent Drive (e.g. `CW_STORAGE_DIR=B:\CourseWallahData`) | 4+ Cores, 8 GB+ RAM, 50 GB+ SSD | Local development, testing & dedicated home server |
| **Linux VM (Ubuntu/Debian)** | Free / Paid (Oracle Always Free, Hetzner, AWS, DO) | ✅ Native 24x7 (`systemd`) | ✅ Native Persistent Local Disk (`CW_STORAGE_DIR=/data`) | 1-2 vCPU, 1-2 GB RAM, 25 GB+ SSD | **Primary Production Target (Highest Stability)** |
| **Render** | Paid (Starter Worker plan ~$7/mo) | ✅ Supported on Worker plan (Free web spins down) | ✅ Persistent Disk Mount (`CW_STORAGE_DIR=/data` on `/data`) | 1 CPU, 512 MB - 1 GB RAM | Managed cloud deployments with automated Git sync |
| **Railway** | Trial / Paid (Hobby/Pro plans) | ✅ Supported (Continuous worker container) | ✅ Persistent Volume Mount (`CW_STORAGE_DIR=/data` on `/data`) | 1-2 vCPU, 1-2 GB RAM | Quick container deployments with volume persistence |
| **Heroku** | Paid (Eco / Basic / Standard dynos) | ✅ Supported on Worker Dynos | ⚠️ Ephemeral filesystem (stateless runtime media) | Eco / Basic / Standard-1x | Traditional container workflows |
| **Self-Hosted Docker / VPS** | Free / Paid (Any Docker host) | ✅ Native Docker restart policies (`unless-stopped`) | ✅ Host Volume Mount (`-v /opt/data:/data`) | 1-2 vCPU, 1-2 GB RAM, 25 GB+ SSD | Private servers, Homelabs, Dedicated VPS |

> [!IMPORTANT]
> **Platform & Tier Honesty:**
> - Telegram downloader bots maintain long-lived MTProto TCP connections and execute CPU-intensive FFmpeg transcoding.
> - Platforms offering "free web apps" (e.g. Render Free Web, Glitch) **spin down after 15 minutes of inactivity** and kill background jobs.
> - For true 24x7 reliability, deploy on a **Linux VM (systemd)**, **Render Background Worker**, **Railway Worker**, or **Heroku Worker**.

---

## 💾 Centralized Storage Architecture (`CW_STORAGE_DIR`)

Course Wallah Downloader features a centralized storage abstraction supporting configurable storage paths via `CW_STORAGE_DIR`:

- **Windows Local Example**: `CW_STORAGE_DIR=B:\CourseWallahData`
- **Linux & Containers**: `CW_STORAGE_DIR=/data`
- **Default fallback**: `./data` relative to project root

### Managed Directory Hierarchy:
```
<CW_STORAGE_DIR>/
├── downloads/      # Downloaded raw assets before processing
├── temp/           # Per-bot and per-job isolated working directories
│   ├── bot_1/
│   └── bot_2/
├── output/         # Final processed outputs
├── state/          # users.json, jobs.json, topics.json, config.json
├── logs/           # bot.log rotating logs
├── sessions/       # Pyrogram .session SQLite databases
├── thumbnails/     # Cached video thumbnails
├── cache/          # Watermark & runtime caches
├── users/          # User subscription data
└── jobs/           # Active job checkpoints
```

---

## 🎨 Watermark Engine Configuration

The watermark engine runs a single-pass `drawtext` encode powered by `libx264 ultrafast`:
- **Preserves Duration**: Output duration strictly matches input duration (`diff <= 0.10s`).
- **Preserves Resolution**: Composites text onto original frame size (720p, 1080p, etc.).
- **Preserves FPS**: Maintains exact source frame rate and fractional timing.
- **Preserves Audio Sync**: Copies audio (`-c:a copy`) when compatible; automatically falls back to AAC single-pass encode on incompatible streams without re-encoding video twice.
- **Configurable CRF**: `WATERMARK_CRF=26` (default) ensures reasonable output file sizes without quality loss.

---

## 🖥 Target 1: Local Windows Deployment

1. **Install FFmpeg**:
   - Download FFmpeg from https://www.gyan.dev/ffmpeg/builds/
   - Extract and add `bin` folder to your system `PATH` (or set `FFMPEG_PATH` in `.env`).
2. **Install Dependencies**:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. **Configure `.env`**:
   ```env
   API_ID=12345678
   API_HASH=your_api_hash
   BOT_TOKEN=your_bot_token
   OWNER_ID=123456789
   CW_STORAGE_DIR=B:\CourseWallahData
   WATERMARK_CRF=26
   ```
4. **Run Bot**:
   ```powershell
   python main.py
   ```

---

## 🛠 Target 2: Linux VM (Ubuntu / Debian / systemd)

1. **Install System Binaries**:
   ```bash
   sudo apt-get update -y
   sudo apt-get install -y python3 python3-pip python3-venv ffmpeg fonts-dejavu-core aria2 git
   ```
2. **Clone & Setup**:
   ```bash
   git clone https://github.com/your-org/downloader-bot.git /opt/downloader-bot
   cd /opt/downloader-bot
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env
   nano .env
   ```
3. **Setup systemd Service**:
   ```bash
   sudo cp deploy/course-wallah.service /etc/systemd/system/course-wallah.service
   sudo systemctl daemon-reload
   sudo systemctl enable course-wallah
   sudo systemctl start course-wallah
   ```

---

## 🚂 Target 3: Railway Deployment

1. **Repository Deployment**:
   - Connect repository on Railway.
   - Railway uses `Dockerfile` and `railway.toml` automatically.
2. **Persistent Storage**:
   - In Railway Service Settings, add a **Volume**.
   - Mount path: `/data`.
3. **Environment Variables**:
   - Set `API_ID`, `API_HASH`, `BOT_TOKEN`, `OWNER_ID`, `CW_STORAGE_DIR=/data`.
4. Deploy the service.

---

## ☁️ Target 4: Render Background Worker

1. **Blueprint Deployment**:
   - In Render Dashboard, select **New -> Blueprint**.
   - Connect repository containing `render.yaml`.
2. **Disk Mount**:
   - `render.yaml` automatically configures a persistent disk at `/data` with `CW_STORAGE_DIR=/data`.
3. **Environment Variables**:
   - Fill in secret environment variables (`API_ID`, `API_HASH`, `BOT_TOKEN`, `OWNER_ID`).

---

## 🟣 Target 5: Heroku Container Deployment

1. **Deploy with Heroku Container Stack**:
   ```bash
   heroku create course-wallah-bot --stack=container
   heroku config:set API_ID=12345678 API_HASH="abc..." BOT_TOKEN="123456:ABC..." OWNER_ID=123456789
   git push heroku main
   heroku ps:scale worker=1
   heroku logs --tail --ps worker
   ```
   *Note: Heroku filesystem is ephemeral. Runtime media is processed on ephemeral disk safely.*
