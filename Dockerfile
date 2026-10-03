# ==============================================================================
# Course Wallah Downloader - Production Container
# ==============================================================================
FROM python:3.11-slim

# Prevent Python from writing .pyc and ensure real-time unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    CW_STORAGE_DIR=/data \
    PORT=8080

WORKDIR /app

# Install system dependencies: FFmpeg, FFprobe, fonts, aria2, and build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    aria2 \
    gcc \
    python3-dev \
    libxml2-dev \
    libxslt1-dev \
    libmagic1 \
    curl \
    ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create required persistent storage and runtime directories
RUN mkdir -p /data/downloads /data/temp /data/output /data/state /data/logs /data/sessions /data/thumbnails /data/cache /data/users /data/jobs /data/topics /data/cookies /data/config assets/start

# Expose optional health check / web port
EXPOSE 8080

# Production Entrypoint (runs main.py bot directly)
CMD ["python", "main.py"]
