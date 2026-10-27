FROM python:3.12-slim

# Install system dependencies (ffmpeg is required for audio extraction, thumbnails, and compression)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create downloads folder
RUN mkdir -p downloads

ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
