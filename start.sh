#!/bin/sh
set -e

# Install ffmpeg if not already present (needed for audio conversion)
apt-get update && apt-get install -y ffmpeg

# Start FastAPI app with Uvicorn
uvicorn app:app --host 0.0.0.0 --port 8000