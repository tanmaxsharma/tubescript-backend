from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled, VideoUnavailable
from deep_translator import GoogleTranslator
import re
import os
import requests

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── PROXY SETUP ──────────────────────────────────────────────
# Free proxies from webshare.io — sign up free at webshare.io
# Get 10 free proxies → paste one here
# Format: "http://username:password@proxy_host:proxy_port"
PROXY = os.getenv("PROXY_URL", None)

def get_proxy_config():
    if PROXY:
        return {"http": PROXY, "https": PROXY}
    return None

def extract_video_id(url: str) -> str:
    patterns = [
        r'v=([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'shorts/([a-zA-Z0-9_-]{11})',
        r'embed/([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def format_time(seconds: float) -> str:
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"

class TranscriptRequest(BaseModel):
    url: str
    language: str = "en"

class TranslateRequest(BaseModel):
    text: str
    target_language: str

@app.get("/")
def root():
    return {"status": "YouTube Transcriber API is running 🚀"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/transcript")
async def get_transcript(req: TranscriptRequest):
    video_id = extract_video_id(req.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    print(f"[INFO] Fetching: {video_id} | lang: {req.language}")

    proxies = get_proxy_config()

    # Try with proxy first if available, then without
    attempt_configs = []
    if proxies:
        attempt_configs.append({"proxies": proxies})
    attempt_configs.append({"proxies": None})  # fallback: no proxy

    for config in attempt_configs:
        try:
            ytt = YouTubeTranscriptApi(proxies=config["proxies"])

            # Try requested language first
            languages_to_try = [req.language, "en", "en-US", "en-GB", "a.en"]

            fetched = None
            for lang in languages_to_try:
                try:
                    fetched = ytt.fetch(video_id, languages=[lang])
                    break
                except Exception:
                    continue

            # If still nothing, try listing all available
            if fetched is None:
                try:
                    transcript_list = ytt.list(video_id)
                    # Try manual first, then auto-generated
                    for t in transcript_list:
                        if not t.is_generated:
                            fetched = t.fetch()
                            break
                    if fetched is None:
                        for t in transcript_list:
                            fetched = t.fetch()
                            break
                except Exception:
                    pass

            if fetched is None:
                continue  # try next config

            raw = fetched.to_raw_data()

            transcript_lines = []
            full_text_parts = []

            for entry in raw:
                text = entry.get("text", "").strip()
                start = entry.get("start", 0)
                duration = entry.get("duration", 0)
                if text:
                    transcript_lines.append({
                        "text": text,
                        "start": round(start, 2),
                        "duration": round(duration, 2),
                        "formatted_time": format_time(start),
                    })
                    full_text_parts.append(text)

            full_text = " ".join(full_text_parts)
            word_count = len(full_text.split())

            return {
                "video_id": video_id,
                "transcript": transcript_lines,
                "full_text": full_text,
                "word_count": word_count,
                "language": req.language,
            }

        except Exception as e:
            print(f"[WARN] fetch() failed: {e}")
            continue

    raise HTTPException(
        status_code=404,
        detail="Transcript not found. YouTube may be blocking cloud server requests. Try using a proxy (see PROXY_URL env variable)."
    )

@app.get("/languages/{video_id}")
async def get_languages(video_id: str):
    proxies = get_proxy_config()

    configs = []
    if proxies:
        configs.append(proxies)
    configs.append(None)

    for proxy_config in configs:
        try:
            ytt = YouTubeTranscriptApi(proxies=proxy_config)
            transcript_list = ytt.list(video_id)
            languages = []
            for t in transcript_list:
                languages.append({
                    "code": t.language_code,
                    "name": t.language,
                    "auto_generated": t.is_generated,
                })
            return {"video_id": video_id, "languages": languages}
        except Exception as e:
            print(f"[WARN] list() failed: {e}")
            continue

    return {"video_id": video_id, "languages": []}

@app.post("/translate")
async def translate_text(req: TranslateRequest):
    if not req.text or not req.target_language:
        raise HTTPException(status_code=400, detail="Missing text or target language")
    try:
        chunk_size = 4500
        chunks = [req.text[i:i+chunk_size] for i in range(0, len(req.text), chunk_size)]
        translated_chunks = []
        for chunk in chunks:
            translated = GoogleTranslator(source='auto', target=req.target_language).translate(chunk)
            translated_chunks.append(translated)
        return {"translated": " ".join(translated_chunks), "target_language": req.target_language}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation failed: {str(e)}")
