from fastapi import FastAPI, Body, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import yt_dlp
import os
import uuid
import glob
import logging
import requests
import subprocess
import math
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("yt-audio-service")

app = FastAPI(title="YouTube Audio Downloader + Smart Splitter (Stable)")

# ---------------- CONFIG ---------------- #

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES_PATH = os.path.join(BASE_DIR, "cookies.txt")

TMP_DIR = os.path.join(BASE_DIR, "tmp")
os.makedirs(TMP_DIR, exist_ok=True)

# Webhooks
N8N_WEBHOOK_PROD = "https://n8n.srv949845.hstgr.cloud/webhook/f30594bf-7b95-4766-9d7a-a84a2a359306"
N8N_WEBHOOK_TEST = "https://n8n.srv949845.hstgr.cloud/webhook-test/f30594bf-7b95-4766-9d7a-a84a2a359306"

CHUNK_SECONDS = 1800      # 30 minutes
ONE_HOUR_SECONDS = 3600

# -------------------------------------- #

@app.get("/health")
def health():
    return {"status": "ok"}

# -------- SHARED REQUEST HANDLER -------- #

def handle_download_request(data, background_tasks, webhook_url, mode):
    url = data.get("url")
    name = data.get("name")
    serial_no = data.get("serial_no")

    if not url or not name or not serial_no:
        raise HTTPException(status_code=400, detail="url, name, serial_no required")

    if not os.path.exists(COOKIES_PATH):
        raise HTTPException(status_code=500, detail="cookies.txt missing")

    logger.info("Request received | mode=%s | serial=%s | name=%s", mode, serial_no, name)

    background_tasks.add_task(
        process_and_send_audio,
        url,
        name,
        serial_no,
        webhook_url,
        mode
    )

    return JSONResponse({
        "status": "received",
        "mode": mode,
        "message": "Processing started",
        "name": name,
        "serial_no": serial_no
    })

# -------- ENDPOINTS -------- #

@app.post("/download")
def download_production(data: dict = Body(...), background_tasks: BackgroundTasks = None):
    return handle_download_request(data, background_tasks, N8N_WEBHOOK_PROD, "production")

@app.post("/download-test")
def download_test(data: dict = Body(...), background_tasks: BackgroundTasks = None):
    return handle_download_request(data, background_tasks, N8N_WEBHOOK_TEST, "test")

# ---------------- UTILITIES ---------------- #

def get_audio_duration(file_path: str) -> int:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True
    )
    return int(float(result.stdout.strip()))

def split_audio(file_path: str, base_name: str, total_parts: int):
    chunk_files = []

    for i in range(total_parts):
        start = i * CHUNK_SECONDS
        out_file = os.path.join(TMP_DIR, f"{base_name}_part{i+1}.m4a")

        logger.info("Splitting part %s/%s", i + 1, total_parts)

        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", file_path,
                "-ss", str(start),
                "-t", str(CHUNK_SECONDS),
                "-acodec", "aac",
                "-b:a", "128k",
                out_file
            ],
            check=True
        )

        chunk_files.append(out_file)

    return chunk_files

def send_to_webhook(file_path: str, payload: dict, webhook_url: str):
    try:
        with open(file_path, "rb") as f:
            response = requests.post(
                webhook_url,
                files={"file": (os.path.basename(file_path), f, "audio/m4a")},
                data=payload,
                timeout=600
            )

        if response.status_code != 200:
            logger.error(
                "❌ Webhook failed | file=%s | status=%s | response=%s",
                os.path.basename(file_path),
                response.status_code,
                response.text
            )
        else:
            logger.info(
                "✅ Webhook success | serial=%s | part=%s/%s | file=%s",
                payload.get("serial_no"),
                payload.get("part_no"),
                payload.get("total_parts"),
                os.path.basename(file_path)
            )

    except Exception as e:
        logger.exception(
            "🔥 Webhook exception | file=%s | error=%s",
            os.path.basename(file_path),
            str(e)
        )

# ---------------- BACKGROUND JOB ---------------- #

def process_and_send_audio(url, name, serial_no, webhook_url, mode):
    uid = str(uuid.uuid4())

    try:
        out_template = os.path.join(TMP_DIR, f"{uid}.%(ext)s")

        # ✅ MOST STABLE yt-dlp CONFIG (VIDEO → AUDIO)
        ydl_opts = {
    # ✅ MOST TOLERANT FORMAT (works for ALL video types)
    "format": "best/bestvideo*+bestaudio/best",

    "outtmpl": out_template,
    "cookiefile": COOKIES_PATH,

    # ✅ Let yt-dlp decide merge container
    # (DO NOT force mp4)
    "postprocessors": [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "m4a",
            "preferredquality": "128",
        }
    ],

    "quiet": True,
    "no_warnings": True,
}

        logger.info("Downloading video | serial=%s | mode=%s", serial_no, mode)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        files = glob.glob(os.path.join(TMP_DIR, f"{uid}*.m4a"))
        if not files:
            raise RuntimeError("Audio file not found after extraction")

        audio_file = files[0]

        duration = get_audio_duration(audio_file)
        logger.info("Duration: %s seconds", duration)

        if duration <= ONE_HOUR_SECONDS:
            chunk_files = [audio_file]
            total_parts = 1
        else:
            total_parts = math.ceil(duration / CHUNK_SECONDS)
            chunk_files = split_audio(audio_file, uid, total_parts)

        logger.info("Prepared %s part(s)", total_parts)

        with ThreadPoolExecutor(max_workers=total_parts) as executor:
            for idx, chunk in enumerate(chunk_files, start=1):
                payload = {
                    "serial_no": serial_no,
                    "name": f"{name} Part {idx}" if total_parts > 1 else name,
                    "part_no": idx,
                    "total_parts": total_parts,
                    "remark": f"Part {idx} of {total_parts}",
                    "mode": mode,
                    "source": "yt-dlp",
                    "original_url": url
                }

                executor.submit(send_to_webhook, chunk, payload, webhook_url)

    except Exception:
        logger.exception("Processing failed | serial=%s", serial_no)

    finally:
        for f in glob.glob(os.path.join(TMP_DIR, f"{uid}*")):
            try:
                os.remove(f)
            except Exception:
                pass
