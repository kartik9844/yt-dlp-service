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

app = FastAPI(title="YouTube Audio Downloader (Async + Fast Mode)")

# ---------------- CONFIG ---------------- #

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES_PATH = os.path.join(BASE_DIR, "cookies.txt")

TMP_DIR = os.path.join(BASE_DIR, "tmp")
os.makedirs(TMP_DIR, exist_ok=True)

N8N_WEBHOOK_URL = "https://n8n.srv949845.hstgr.cloud/webhook/f30594bf-7b95-4766-9d7a-a84a2a359306"

CONCURRENT_FRAGMENTS = 4  # safe speed

# -------------------------------------- #

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/download")
def download_audio(
    data: dict = Body(...),
    background_tasks: BackgroundTasks = None
):
    """
    Expects JSON:
    {
      "url": "https://youtube.com/...",
      "name": "desired_filename"
    }
    """
    url = data.get("url")
    name = data.get("name")

    if not url:
        raise HTTPException(status_code=400, detail="Missing 'url'")

    if not name:
        raise HTTPException(status_code=400, detail="Missing 'name'")

    if not os.path.exists(COOKIES_PATH):
        raise HTTPException(status_code=500, detail="cookies.txt not found")

    logger.info("Received link + name → starting background task")
    background_tasks.add_task(process_and_send_audio, url, name)

    return JSONResponse({
        "status": "received",
        "message": "Link received. Processing started in background.",
        "name": name
    })

# ---------------- BACKGROUND JOB ---------------- #

def process_and_send_audio(url: str, name: str):
    audio_file = None
    try:
        file_id = str(uuid.uuid4())
        out_template = os.path.join(TMP_DIR, f"{file_id}.%(ext)s")

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": out_template,
            "cookiefile": COOKIES_PATH,
            "concurrent_fragment_downloads": CONCURRENT_FRAGMENTS,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
            }],
            "quiet": True,
            "no_warnings": True,
        }

        logger.info("yt-dlp download started for: %s", url)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        files = glob.glob(os.path.join(TMP_DIR, f"{file_id}*.m4a*"))
        if not files:
            raise RuntimeError("Audio file not found after download")

        audio_file = files[0]
        logger.info("Audio ready: %s", audio_file)

        # 🔥 SEND AUDIO + NAME BACK TO N8N
        with open(audio_file, "rb") as f:
            response = requests.post(
                N8N_WEBHOOK_URL,
                files={
                    "file": (f"{name}.m4a", f, "audio/m4a")
                },
                data={
                    "name": name,
                    "source": "yt-dlp",
                    "original_url": url
                },
                timeout=300
            )

        response.raise_for_status()
        logger.info("Audio + name successfully sent to n8n")

    except Exception as e:
        logger.exception("Background processing failed")

        # Notify n8n about error (optional)
        try:
            requests.post(
                N8N_WEBHOOK_URL,
                json={
                    "status": "error",
                    "error": str(e),
                    "url": url,
                    "name": name
                },
                timeout=30
            )
        except Exception:
            pass

    finally:
        # Cleanup
        if audio_file and os.path.exists(audio_file):
            try:
                os.remove(audio_file)
                logger.info("Cleaned up local audio file")
            except Exception:
                pass
