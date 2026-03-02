from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from deep_translator import GoogleTranslator
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi
import os, re, requests, time

load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── ENV ──────────────────────────────────────────────────────
# Set PROXY_URL in Railway Variables like:
# http://USERNAME:PASSWORD@p.webshare.io:80
PROXY_URL        = os.getenv("PROXY_URL", "")
SUPADATA_API_KEY = os.getenv("SUPADATA_API_KEY", "")

# ── HELPERS ──────────────────────────────────────────────────
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

def process_raw(raw):
    lines, texts = [], []
    for e in raw:
        if isinstance(e, dict):
            text  = e.get("text", "").strip()
            start = float(e.get("start", 0))
            dur   = float(e.get("duration", 0))
        else:
            text  = str(getattr(e, "text", "")).strip()
            start = float(getattr(e, "start", 0))
            dur   = float(getattr(e, "duration", 0))
        if text:
            lines.append({
                "text": text,
                "start": round(start, 2),
                "duration": round(dur, 2),
                "formatted_time": format_time(start),
            })
            texts.append(text)
    return lines, " ".join(texts)

# ── METHOD 1: NEW API (v1.x) with Webshare proxy ─────────────
def method_new_api(video_id: str, lang: str):
    """Uses new YouTubeTranscriptApi() instance style — v1.0+"""
    proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

    # Pass proxy into constructor — this is the correct new way
    ytt = YouTubeTranscriptApi(proxies=proxies)

    print(f"[INFO] New API | proxy={'YES' if proxies else 'NO'}")

    # List all available transcripts
    transcript_list = ytt.list(video_id)
    all_t = list(transcript_list)
    print(f"[INFO] Found {len(all_t)} transcripts: {[t.language_code for t in all_t]}")

    fetched = None
    used_lang = lang

    # 1. Try requested language
    for t in all_t:
        if t.language_code == lang or t.language_code.startswith(lang):
            fetched   = t.fetch()
            used_lang = t.language_code
            print(f"[INFO] Got requested lang: {used_lang}")
            break

    # 2. Try manual transcript
    if fetched is None:
        for t in all_t:
            if not t.is_generated:
                fetched   = t.fetch()
                used_lang = t.language_code
                print(f"[INFO] Got manual: {used_lang}")
                break

    # 3. Take any available
    if fetched is None and all_t:
        fetched   = all_t[0].fetch()
        used_lang = all_t[0].language_code
        print(f"[INFO] Got first available: {used_lang}")

    if fetched is None:
        raise Exception("No transcript available")

    try:
        raw = fetched.to_raw_data()
    except Exception:
        raw = list(fetched)

    lines, full_text = process_raw(raw)
    print(f"[INFO] ✅ New API success! {len(lines)} lines, lang={used_lang}")
    return lines, full_text, used_lang

# ── METHOD 2: Direct fetch shortcut ──────────────────────────
def method_fetch_direct(video_id: str, lang: str):
    """Uses ytt.fetch() directly — fastest if language known"""
    proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
    ytt = YouTubeTranscriptApi(proxies=proxies)

    print(f"[INFO] Direct fetch | langs=[{lang}, en, hi]")
    langs_to_try = list(dict.fromkeys([lang, "en", "hi", "en-US"]))
    fetched = ytt.fetch(video_id, languages=langs_to_try)

    try:
        raw = fetched.to_raw_data()
    except Exception:
        raw = list(fetched)

    lines, full_text = process_raw(raw)
    print(f"[INFO] ✅ Direct fetch success! {len(lines)} lines")
    return lines, full_text, lang

# ── METHOD 3: Supadata fallback ───────────────────────────────
def method_supadata(video_id: str, lang: str):
    if not SUPADATA_API_KEY:
        raise Exception("No Supadata key configured")

    print(f"[INFO] Supadata fallback...")
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    resp = requests.get(
        "https://api.supadata.ai/v1/transcript",
        headers={"x-api-key": SUPADATA_API_KEY},
        params={"url": video_url, "lang": lang},
        timeout=30
    )

    if resp.status_code == 402:
        raise Exception("Supadata quota exceeded")
    if resp.status_code == 404:
        raise Exception("No transcript found on Supadata")
    if resp.status_code != 200:
        raise Exception(f"Supadata error {resp.status_code}: {resp.text[:80]}")

    content = resp.json().get("content", [])
    if not content:
        raise Exception("Supadata returned empty content")

    lines, texts = [], []
    for item in content:
        text     = item.get("text", "").strip()
        start_s  = float(item.get("offset", 0)) / 1000
        dur_s    = float(item.get("duration", 0)) / 1000
        if text:
            lines.append({
                "text": text,
                "start": round(start_s, 2),
                "duration": round(dur_s, 2),
                "formatted_time": format_time(start_s),
            })
            texts.append(text)

    if not lines:
        raise Exception("Supadata: no lines parsed")

    print(f"[INFO] ✅ Supadata success! {len(lines)} lines")
    return lines, " ".join(texts), lang

# ── MODELS ────────────────────────────────────────────────────
class TranscriptRequest(BaseModel):
    url: str
    language: str = "en"

class TranslateRequest(BaseModel):
    text: str
    target_language: str

# ── ROUTES ────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "status": "YouTube Transcriber API 🚀",
        "version": "11.0",
        "proxy": bool(PROXY_URL),
        "supadata": bool(SUPADATA_API_KEY),
    }

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/transcript")
async def get_transcript(req: TranscriptRequest):
    print(f"\n[INFO] === Request: {req.url} | lang: {req.language} ===")

    video_id = extract_video_id(req.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL.")

    print(f"[INFO] Video ID: {video_id}")

    # Try all methods in order
    methods = [
        ("Direct fetch",  lambda: method_fetch_direct(video_id, req.language)),
        ("List + fetch",  lambda: method_new_api(video_id, req.language)),
        ("Supadata",      lambda: method_supadata(video_id, req.language)),
    ]

    last_error = None
    for name, method in methods:
        try:
            lines, full_text, used_lang = method()
            return {
                "video_id":   video_id,
                "transcript": lines,
                "full_text":  full_text,
                "word_count": len(full_text.split()),
                "language":   used_lang,
            }
        except Exception as e:
            print(f"[WARN] {name} failed: {str(e)[:100]}")
            last_error = str(e)
            continue

    print(f"[ERROR] All methods failed: {last_error}")
    raise HTTPException(
        status_code=404,
        detail="No transcript found. Video may not have captions, or may be private/age-restricted."
    )

@app.get("/languages/{video_id}")
async def get_languages(video_id: str):
    try:
        proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
        ytt = YouTubeTranscriptApi(proxies=proxies)
        tl = ytt.list(video_id)
        return {
            "video_id": video_id,
            "languages": [
                {"code": t.language_code, "name": t.language, "auto_generated": t.is_generated}
                for t in tl
            ]
        }
    except Exception as e:
        print(f"[WARN] get_languages failed: {str(e)[:80]}")
    return {"video_id": video_id, "languages": []}

@app.post("/translate")
async def translate_text(req: TranslateRequest):
    if not req.text or not req.target_language:
        raise HTTPException(status_code=400, detail="Missing fields")
    try:
        chunks = [req.text[i:i+4500] for i in range(0, len(req.text), 4500)]
        result = " ".join(
            GoogleTranslator(source='auto', target=req.target_language).translate(c)
            for c in chunks
        )
        return {"translated": result, "target_language": req.target_language}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation failed: {str(e)}")