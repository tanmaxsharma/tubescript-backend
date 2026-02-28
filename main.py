from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from deep_translator import GoogleTranslator
from dotenv import load_dotenv
import os
import re
import requests

load_dotenv() 

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPADATA_API_KEY = os.getenv("SUPADATA_API_KEY", "")

def extract_video_id(url: str) -> str:
    patterns = [
        r'v=([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'shorts/([a-zA-Z0-9_-]{11})',
        r'embed/([a-zA-Z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url.strip()):
        return url.strip()
    return None

def format_time(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

class TranscriptRequest(BaseModel):
    url: str
    language: str = "en"

class TranslateRequest(BaseModel):
    text: str
    target_language: str

@app.get("/")
def root():
    return {
        "status": "YouTube Transcriber API is running 🚀",
        "version": "9.0",
        "supadata": bool(SUPADATA_API_KEY),
    }

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/transcript")
async def get_transcript(req: TranscriptRequest):
    print(f"[INFO] Request: {req.url}")

    video_id = extract_video_id(req.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL.")

    if not SUPADATA_API_KEY:
        raise HTTPException(status_code=500, detail="SUPADATA_API_KEY not configured.")

    video_url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        print(f"[INFO] Calling Supadata for: {video_id}")
        resp = requests.get(
            "https://api.supadata.ai/v1/transcript",
            headers={"x-api-key": SUPADATA_API_KEY},
            params={"url": video_url, "lang": req.language},
            timeout=30
        )

        print(f"[INFO] Supadata status: {resp.status_code}")

        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="No transcript found. This video may not have captions.")

        if resp.status_code == 402:
            raise HTTPException(status_code=402, detail="Supadata free quota exceeded. Please upgrade at supadata.ai")

        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Transcript service error: {resp.status_code}")

        data = resp.json()
        content = data.get("content", [])

        if not content:
            raise HTTPException(status_code=404, detail="No transcript content returned.")

        lines = []
        texts = []
        for item in content:
            text = item.get("text", "").strip()
            start_ms = float(item.get("offset", 0))
            duration_ms = float(item.get("duration", 0))
            start_s = start_ms / 1000
            duration_s = duration_ms / 1000

            if text:
                lines.append({
                    "text": text,
                    "start": round(start_s, 2),
                    "duration": round(duration_s, 2),
                    "formatted_time": format_time(start_s),
                })
                texts.append(text)

        full_text = " ".join(texts)
        print(f"[INFO] ✅ Success! {len(lines)} lines, {len(full_text.split())} words")

        return {
            "video_id": video_id,
            "transcript": lines,
            "full_text": full_text,
            "word_count": len(full_text.split()),
            "language": data.get("lang", req.language),
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

@app.get("/languages/{video_id}")
async def get_languages(video_id: str):
    if not SUPADATA_API_KEY:
        return {"video_id": video_id, "languages": []}
    try:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        resp = requests.get(
            "https://api.supadata.ai/v1/transcript",
            headers={"x-api-key": SUPADATA_API_KEY},
            params={"url": video_url},
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            available = data.get("availableLangs", [])
            languages = [{"code": lang, "name": lang, "auto_generated": False} for lang in available]
            return {"video_id": video_id, "languages": languages}
    except Exception as e:
        print(f"[WARN] get_languages failed: {e}")
    return {"video_id": video_id, "languages": []}

@app.post("/translate")
async def translate_text(req: TranslateRequest):
    if not req.text or not req.target_language:
        raise HTTPException(status_code=400, detail="Missing text or target language")
    try:
        chunk_size = 4500
        chunks = [req.text[i:i+chunk_size] for i in range(0, len(req.text), chunk_size)]
        translated = [
            GoogleTranslator(source='auto', target=req.target_language).translate(c)
            for c in chunks
        ]
        return {"translated": " ".join(translated), "target_language": req.target_language}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation failed: {str(e)}")