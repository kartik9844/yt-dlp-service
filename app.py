from fastapi import FastAPI, Body, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import yt_dlp
import os
import uuid
import glob
import logging
import requests

# basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("yt-audio-service")

app = FastAPI(title="YouTube Audio Downloader Microservice")

WEBHOOK_URL = "https://n8n.srv949845.hstgr.cloud/webhook/f30594bf-7b95-4766-9d7a-a84a2a359306"

def process_and_send_audio(url: str, file_id: str):
    """
    Background task to download audio and send it to the n8n webhook.
    """
    logger.info(f"Background processing started for {file_id}. URL: {url}")
    
    # Use yt-dlp templating
    out_template = f"/tmp/{file_id}.%(ext)s"
    
    # yt-dlp options
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "m4a",
        }],
        "logger": logger,
        "quiet": False,
    }

    final_file = None
    try:
        logger.info(f"Starting yt-dlp download for {file_id}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Try to find the downloaded file
        pattern = f"/tmp/{file_id}*.m4a*"
        files = glob.glob(pattern)
        logger.info(f"Globbed files for {file_id}: {files}")
        
        if not files:
            logger.error(f"No files found after yt-dlp download for {file_id}")
            # Optionally report error to webhook
            return

        final_file = files[0]
        logger.info(f"File downloaded successfully: {final_file}")

        # Send to n8n webhook
        logger.info(f"Uploading file {final_file} to webhook: {WEBHOOK_URL}")
        with open(final_file, 'rb') as f:
            files_payload = {'file': (os.path.basename(final_file), f, 'audio/m4a')}
            response = requests.post(WEBHOOK_URL, files=files_payload)
        
        logger.info(f"Webhook response: {response.status_code} - {response.text}")

    except Exception as e:
        logger.exception(f"Error processing {file_id}: {e}")
        # Could also notify webhook of failure here
    finally:
        # Cleanup
        if final_file and os.path.exists(final_file):
            logger.info(f"Cleaning up file: {final_file}")
            os.remove(final_file)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/download")
def download_audio(background_tasks: BackgroundTasks, data: dict = Body(...)):
    """
    Accepts JSON: {"url": "https://youtube.com/..."}
    Returns: JSON {"message": "received link processing"} immediately.
    Processing happens in background and result is sent to n8n webhook.
    """
    try:
        logger.info("Received download request")
        url = data.get("url")
        if not url:
            logger.warning("Missing 'url' in request body")
            raise HTTPException(status_code=400, detail="Missing 'url'")

        # Generate ID
        file_id = str(uuid.uuid4())
        
        # Enqueue background task
        background_tasks.add_task(process_and_send_audio, url, file_id)
        
        return JSONResponse({"message": "received link processing", "id": file_id})

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unhandled exception during request handling")
        raise HTTPException(status_code=500, detail=str(e))
