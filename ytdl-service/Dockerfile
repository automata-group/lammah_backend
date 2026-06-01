FROM python:3.11-slim

# Install system dependencies (ffmpeg and curl for health check)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Always upgrade yt-dlp to latest (including nightly) to stay ahead of YouTube's anti-bot changes
RUN pip install --no-cache-dir --upgrade --pre yt-dlp

# Copy application files
COPY . .

# Environment variables
ENV PORT=8001
ENV UPLOAD_DIR=/app/uploads

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Start uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
