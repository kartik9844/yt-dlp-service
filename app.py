from fastapi import FastAPI, Body, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import yt_dlp
import os
import uuid
import glob
import logging
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("yt-audio-service")

app = FastAPI(title="YouTube Audio Downloader (Async + Webhook)")

# ---- CONFIG ---- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES_PATH = os.path.join(BASE_DIR, "cookies.txt")

N8N_WEBHOOK_URL = "https://n8n.srv949845.hstgr.cloud/webhook/f30594bf-7b95-4766-9d7a-a84a2a359306"
TMP_DIR = "/tmp" if os.name != "nt" else os.path.join(BASE_DIR, "tmp")

os.makedirs(TMP_DIR, exist_ok=True)

# ---------------- #

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/download")
def download_audio(
    data: dict = Body(...),
    background_tasks: BackgroundTasks = None
):
    """
    Accepts JSON: {"url": "..."}
    Responds immediately.
    Processing happens in background.
    """
    url = data.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Missing 'url'")

    if not os.path.exists(COOKIES_PATH):
        raise HTTPException(status_code=500, detail="cookies.txt not found")

    logger.info("Received link, queuing background job")
    background_tasks.add_task(process_and_send_audio, url)

    return JSONResponse({
        "status": "received",
        "message": "Link received. Processing started."
    })

# ---------------- BACKGROUND TASK ---------------- #

def process_and_send_audio(url: str):
    try:
        file_id = str(uuid.uuid4())
        out_template = os.path.join(TMP_DIR, f"{file_id}.%(ext)s")

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": out_template,
            "cookiefile": "cookies.txt",   # ✅ IMPORTANT
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
            }],
            "quiet": True,
            "nocheckcertificate": True,
        }

        logger.info("Starting yt-dlp download")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        files = glob.glob(os.path.join(TMP_DIR, f"{file_id}*.m4a*"))
        if not files:
            raise RuntimeError("Audio file not found after download")

        audio_file = files[0]
        logger.info("Audio ready: %s", audio_file)

        # Send audio to n8n
        with open(audio_file, "rb") as f:
            response = requests.post(
                N8N_WEBHOOK_URL,
                files={
                    "file": ("audio.m4a", f, "audio/m4a")
                },
                data={
                    "source": "yt-dlp",
                    "original_url": url
                },
                timeout=180
            )

        response.raise_for_status()
        logger.info("Audio successfully sent to n8n")

        # Cleanup
        try:
            os.remove(audio_file)
        except Exception:
            pass

    except Exception as e:
        logger.exception("Background processing failed")

        # Notify n8n about error (optional but recommended)
        try:
            requests.post(
                N8N_WEBHOOK_URL,
                json={
                    "status": "error",
                    "error": str(e),
                    "url": url
                },
                timeout=30
            )
        except Exception:
            pass
