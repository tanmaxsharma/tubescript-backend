from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi
from deep_translator import GoogleTranslator
import re
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROXY = os.getenv("PROXY_URL", None)

def get_proxies():
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
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url.strip()):
        return url.strip()
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

# ── ROOT ──────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "YouTube Transcriber API is running 🚀", "version": "2.0"}

@app.get("/health")
def health():
    return {"status": "ok"}

# ── TRANSCRIPT ────────────────────────────────────
@app.post("/transcript")
async def get_transcript(req: TranscriptRequest):
    print(f"[INFO] Request received: {req.url}")

    video_id = extract_video_id(req.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL. Could not extract video ID.")

    print(f"[INFO] Video ID: {video_id} | Language: {req.language}")

    proxies = get_proxies()
    print(f"[INFO] Using proxy: {True if proxies else False}")

    # Build list of configs to try
    configs_to_try = []
    if proxies:
        configs_to_try.append(proxies)
    configs_to_try.append(None)  # fallback without proxy

    last_error = None

    for proxy_config in configs_to_try:
        try:
            print(f"[INFO] Trying with proxy={proxy_config is not None}")

            if proxy_config:
                ytt = YouTubeTranscriptApi(proxies=proxy_config)
            else:
                ytt = YouTubeTranscriptApi()

            fetched = None

            # Try languages in order
            languages_to_try = [req.language, "en", "en-US", "en-GB", "en-IN", "a.en"]
            # Remove duplicates while preserving order
            seen = set()
            unique_langs = []
            for l in languages_to_try:
                if l not in seen:
                    seen.add(l)
                    unique_langs.append(l)

            for lang in unique_langs:
                try:
                    fetched = ytt.fetch(video_id, languages=[lang])
                    print(f"[INFO] Got transcript in language: {lang}")
                    break
                except Exception as e:
                    print(f"[INFO] Lang {lang} failed: {str(e)[:80]}")
                    continue

            # If still no transcript, try listing all available
            if fetched is None:
                print(f"[INFO] Trying to list all available transcripts...")
                try:
                    transcript_list = ytt.list(video_id)
                    all_transcripts = list(transcript_list)

                    # Try manual transcripts first
                    for t in all_transcripts:
                        if not t.is_generated:
                            fetched = t.fetch()
                            print(f"[INFO] Got manual transcript: {t.language_code}")
                            break

                    # Then try auto-generated
                    if fetched is None:
                        for t in all_transcripts:
                            fetched = t.fetch()
                            print(f"[INFO] Got auto transcript: {t.language_code}")
                            break

                except Exception as e:
                    print(f"[WARN] list() failed: {str(e)[:100]}")
                    last_error = str(e)

            if fetched is not None:
                # Process transcript
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
                            "start": round(float(start), 2),
                            "duration": round(float(duration), 2),
                            "formatted_time": format_time(float(start)),
                        })
                        full_text_parts.append(text)

                full_text = " ".join(full_text_parts)
                word_count = len(full_text.split())

                print(f"[INFO] Success! {len(transcript_lines)} lines, {word_count} words")

                return {
                    "video_id": video_id,
                    "transcript": transcript_lines,
                    "full_text": full_text,
                    "word_count": word_count,
                    "language": req.language,
                }

        except Exception as e:
            print(f"[WARN] Config failed: {str(e)[:150]}")
            last_error = str(e)
            continue

    # All configs failed
    print(f"[ERROR] All attempts failed. Last error: {last_error}")

    if last_error and ("blocked" in last_error.lower() or "ip" in last_error.lower()):
        raise HTTPException(
            status_code=404,
            detail="YouTube is blocking requests from this server's IP. Please add a PROXY_URL environment variable in Railway settings."
        )

    raise HTTPException(
        status_code=404,
        detail=f"No transcript found for this video. It may not have captions, or may be private/age-restricted."
    )

# ── LANGUAGES ─────────────────────────────────────
@app.get("/languages/{video_id}")
async def get_languages(video_id: str):
    proxies = get_proxies()

    configs_to_try = []
    if proxies:
        configs_to_try.append(proxies)
    configs_to_try.append(None)

    for proxy_config in configs_to_try:
        try:
            if proxy_config:
                ytt = YouTubeTranscriptApi(proxies=proxy_config)
            else:
                ytt = YouTubeTranscriptApi()

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
            print(f"[WARN] get_languages failed: {str(e)[:100]}")
            continue

    return {"video_id": video_id, "languages": []}

# ── TRANSLATE ─────────────────────────────────────
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
        return {
            "translated": " ".join(translated_chunks),
            "target_language": req.target_language
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation failed: {str(e)}")
