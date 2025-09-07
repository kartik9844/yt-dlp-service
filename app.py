from fastapi import FastAPI, Body
from fastapi.responses import FileResponse, JSONResponse
import yt_dlp
import os
import uuid
import glob

app = FastAPI(title="YouTube Audio Downloader Microservice")

@app.post("/download")
def download_audio(data: dict = Body(...)):
    """
    Accepts: {"url": "https://youtube.com/..."}
    Returns: m4a audio file for transcription
    """
    try:
        url = data.get("url")
        if not url:
            return JSONResponse({"error": "Missing 'url'"}, status_code=400)

        # Unique filename base (without extension)
        file_id = str(uuid.uuid4())
        out_file = f"/tmp/{file_id}.m4a"

        # yt-dlp options
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": out_file,
            "cookiefile": "cookies.txt",  # 👈 use Render disk or local path
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
            }]
        }

        # Download audio
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Handle yt-dlp double extension issue (.m4a.m4a)
        final_file = out_file
        if not os.path.exists(final_file):
            alt_file = final_file + ".m4a"
            if os.path.exists(alt_file):
                final_file = alt_file
            else:
                # fallback: glob search
                files = glob.glob(f"/tmp/{file_id}*.m4a*")
                if files:
                    final_file = files[0]
                else:
                    return JSONResponse({"error": "File not found after download"}, status_code=500)

        # Return file
        return FileResponse(final_file, media_type="audio/m4a", filename="audio.m4a")

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
