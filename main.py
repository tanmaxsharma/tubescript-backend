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

def process_transcript(raw_data):
    transcript_lines = []
    full_text_parts = []
    for entry in raw_data:
        if isinstance(entry, dict):
            text = entry.get("text", "").strip()
            start = entry.get("start", 0)
            duration = entry.get("duration", 0)
        else:
            text = getattr(entry, "text", "").strip()
            start = getattr(entry, "start", 0)
            duration = getattr(entry, "duration", 0)
        if text:
            transcript_lines.append({
                "text": text,
                "start": round(float(start), 2),
                "duration": round(float(duration), 2),
                "formatted_time": format_time(float(start)),
            })
            full_text_parts.append(text)
    return transcript_lines, " ".join(full_text_parts)

class TranscriptRequest(BaseModel):
    url: str
    language: str = "en"

class TranslateRequest(BaseModel):
    text: str
    target_language: str

@app.get("/")
def root():
    return {"status": "YouTube Transcriber API is running 🚀", "version": "4.0"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/transcript")
async def get_transcript(req: TranscriptRequest):
    print(f"[INFO] Request: {req.url} | lang: {req.language}")

    video_id = extract_video_id(req.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL.")

    print(f"[INFO] Video ID: {video_id}")
    proxies = get_proxies()
    print(f"[INFO] Proxy: {'YES' if proxies else 'NO'}")

    last_error = None

    # Try with proxy first (if available), then without
    proxy_options = []
    if proxies:
        proxy_options.append(proxies)
    proxy_options.append(None)

    for current_proxy in proxy_options:
        kwargs = {"proxies": current_proxy} if current_proxy else {}
        proxy_label = "WITH proxy" if current_proxy else "WITHOUT proxy"

        # ── METHOD 1: get_transcript() directly ──
        try:
            print(f"[INFO] Trying get_transcript() {proxy_label}")
            langs = [req.language, "en", "hi", "en-US", "en-GB"]
            seen = set()
            unique_langs = [l for l in langs if not (l in seen or seen.add(l))]

            for lang in unique_langs:
                try:
                    raw_data = YouTubeTranscriptApi.get_transcript(
                        video_id, languages=[lang], **kwargs
                    )
                    transcript_lines, full_text = process_transcript(raw_data)
                    word_count = len(full_text.split())
                    print(f"[INFO] ✅ Method 1 success! lang={lang}, {word_count} words")
                    return {
                        "video_id": video_id,
                        "transcript": transcript_lines,
                        "full_text": full_text,
                        "word_count": word_count,
                        "language": lang,
                    }
                except Exception as e:
                    err = str(e)[:80]
                    print(f"[INFO] Lang {lang} failed: {err}")
                    last_error = str(e)
                    continue

        except Exception as e:
            print(f"[WARN] Method 1 outer failed: {str(e)[:100]}")
            last_error = str(e)

        # ── METHOD 2: list_transcripts() then fetch ──
        try:
            print(f"[INFO] Trying list_transcripts() {proxy_label}")
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id, **kwargs)
            all_t = list(transcript_list)
            print(f"[INFO] Found {len(all_t)} available transcripts")

            fetched_data = None
            used_lang = None

            # Priority 1: requested language
            for t in all_t:
                if t.language_code.startswith(req.language):
                    try:
                        fetched_data = t.fetch()
                        used_lang = t.language_code
                        break
                    except Exception:
                        continue

            # Priority 2: manual transcripts
            if fetched_data is None:
                for t in all_t:
                    if not t.is_generated:
                        try:
                            fetched_data = t.fetch()
                            used_lang = t.language_code
                            break
                        except Exception:
                            continue

            # Priority 3: any transcript
            if fetched_data is None:
                for t in all_t:
                    try:
                        fetched_data = t.fetch()
                        used_lang = t.language_code
                        break
                    except Exception:
                        continue

            if fetched_data is not None:
                try:
                    raw_data = fetched_data.to_raw_data()
                except AttributeError:
                    raw_data = list(fetched_data)

                transcript_lines, full_text = process_transcript(raw_data)
                word_count = len(full_text.split())
                print(f"[INFO] ✅ Method 2 success! lang={used_lang}, {word_count} words")
                return {
                    "video_id": video_id,
                    "transcript": transcript_lines,
                    "full_text": full_text,
                    "word_count": word_count,
                    "language": used_lang or req.language,
                }

        except Exception as e:
            print(f"[WARN] Method 2 failed: {str(e)[:100]}")
            last_error = str(e)

    # All failed
    print(f"[ERROR] All attempts failed. Last error: {last_error}")

    if last_error and any(w in last_error.lower() for w in ["blocked", "cloud", "ip", "403", "too many requests"]):
        raise HTTPException(
            status_code=503,
            detail="YouTube is blocking this server. Please try again later."
        )

    raise HTTPException(
        status_code=404,
        detail="No transcript found. This video may not have captions, or may be private/age-restricted."
    )

@app.get("/languages/{video_id}")
async def get_languages(video_id: str):
    proxies = get_proxies()
    proxy_options = []
    if proxies:
        proxy_options.append(proxies)
    proxy_options.append(None)

    for current_proxy in proxy_options:
        kwargs = {"proxies": current_proxy} if current_proxy else {}
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id, **kwargs)
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

@app.post("/translate")
async def translate_text(req: TranslateRequest):
    if not req.text or not req.target_language:
        raise HTTPException(status_code=400, detail="Missing text or target language")
    try:
        chunk_size = 4500
        chunks = [req.text[i:i+chunk_size] for i in range(0, len(req.text), chunk_size)]
        translated_chunks = []
        for chunk in chunks:
            translated = GoogleTranslator(
                source='auto', target=req.target_language
            ).translate(chunk)
            translated_chunks.append(translated)
        return {
            "translated": " ".join(translated_chunks),
            "target_language": req.target_language
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation failed: {str(e)}")