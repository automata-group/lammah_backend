FROM python:3.11-slim

# Copy Deno binary from official image for yt-dlp signature decryption solving
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

# Install system dependencies and curl for health checks
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    libreoffice \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Always upgrade yt-dlp to latest (including nightlies) to keep up with YouTube's anti-bot changes
RUN pip install --no-cache-dir --upgrade --pre yt-dlp


# Copy application files
COPY . .

# Environment variables
ENV PORT=8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/v1/health || exit 1

# Start uvicorn directly (no entrypoint.sh needed)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
