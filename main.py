from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi
import re
import traceback

# ── v1.2.4 uses instance, not class methods ──────────────────────────────────
ytt = YouTubeTranscriptApi()   # ← This is the fix. Must instantiate first!

app = FastAPI(title="YouTube Transcriber API", version="4.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def extract_video_id(url: str) -> str:
    url = url.strip()
    patterns = [
        r"(?:v=)([a-zA-Z0-9_-]{11})",
        r"(?:youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:shorts/)([a-zA-Z0-9_-]{11})",
        r"(?:embed/)([a-zA-Z0-9_-]{11})",
        r"(?:live/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    if re.match(r"^[a-zA-Z0-9_-]{11}$", url):
        return url
    raise ValueError("Invalid YouTube URL. Please use a valid YouTube link.")

def format_time(seconds: float) -> str:
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def process_raw(raw_list: list) -> list:
    """Turn raw dicts into our clean format"""
    result = []
    for entry in raw_list:
        text = str(entry.get("text", "")).replace("\n", " ").strip()
        start = float(entry.get("start", 0))
        duration = float(entry.get("duration", 0))
        if text:
            result.append({
                "text": text,
                "start": start,
                "duration": duration,
                "formatted_time": format_time(start),
            })
    return result

# ─── Models ───────────────────────────────────────────────────────────────────

class TranscriptRequest(BaseModel):
    url: str
    language: str = "en"

class TranslateRequest(BaseModel):
    text: str
    target_language: str

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "YouTube Transcriber API is running 🚀", "version": "4.0.0"}


@app.post("/transcript")
async def get_transcript(req: TranscriptRequest):
    try:
        video_id = extract_video_id(req.url)
        print(f"[INFO] Fetching: {video_id} | lang: {req.language}")

        raw = None

        # Strategy 1: requested language + english fallback
        try:
            fetched = ytt.fetch(video_id, languages=[req.language, "en"])
            raw = fetched.to_raw_data()
            print(f"[OK] Got transcript via fetch()")
        except Exception as e1:
            print(f"[WARN] fetch() failed: {e1}")

        # Strategy 2: list all available and grab first one
        if not raw:
            try:
                transcript_list = ytt.list(video_id)
                for t in transcript_list:
                    fetched = t.fetch()
                    raw = fetched.to_raw_data()
                    print(f"[OK] Got transcript via list() in: {t.language_code}")
                    break
            except Exception as e2:
                print(f"[WARN] list() failed: {e2}")

        if not raw:
            raise HTTPException(
                status_code=404,
                detail="No transcript found. This video may not have captions, or it may be private/age-restricted."
            )

        processed = process_raw(raw)

        if not processed:
            raise HTTPException(status_code=404, detail="Transcript was empty.")

        full_text = " ".join([e["text"] for e in processed])
        print(f"[INFO] Done: {len(processed)} lines, {len(full_text.split())} words")

        return {
            "video_id": video_id,
            "transcript": processed,
            "full_text": full_text,
            "word_count": len(full_text.split()),
            "line_count": len(processed),
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/languages/{video_id}")
async def get_languages(video_id: str):
    try:
        transcript_list = ytt.list(video_id)
        languages = []
        for t in transcript_list:
            languages.append({
                "code": t.language_code,
                "name": t.language,
                "auto_generated": t.is_generated,
            })
        return {"languages": languages, "count": len(languages)}
    except Exception as e:
        print(f"[WARN] Languages error: {e}")
        return {"languages": [], "count": 0}


@app.post("/translate")
async def translate_text(req: TranslateRequest):
    try:
        from deep_translator import GoogleTranslator
        if not req.text.strip():
            raise HTTPException(status_code=400, detail="No text provided.")

        chunk_size = 4500
        chunks = [req.text[i:i+chunk_size] for i in range(0, len(req.text), chunk_size)]
        translated = []
        for chunk in chunks:
            result = GoogleTranslator(source='auto', target=req.target_language).translate(chunk)
            translated.append(result or "")

        return {"translated": " ".join(translated)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation failed: {str(e)}")
