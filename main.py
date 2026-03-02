from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from deep_translator import GoogleTranslator
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import WebshareProxyConfig, GenericProxyConfig
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
# Option A — Webshare (BEST, $3/month rotating residential)
# Get from: https://dashboard.webshare.io/proxy/settings
WEBSHARE_USERNAME = os.getenv("WEBSHARE_USERNAME", "")
WEBSHARE_PASSWORD = os.getenv("WEBSHARE_PASSWORD", "")

# Option B — Any generic proxy (http://user:pass@host:port)
PROXY_URL = os.getenv("PROXY_URL", "")

# Option C — Supadata fallback
SUPADATA_API_KEY = os.getenv("SUPADATA_API_KEY", "")

# ── BUILD YTT INSTANCE ───────────────────────────────────────
def get_ytt():
    """Returns YouTubeTranscriptApi instance with correct proxy config"""

    # Priority 1: Webshare (most reliable — built-in support)
    if WEBSHARE_USERNAME and WEBSHARE_PASSWORD:
        print(f"[INFO] Using Webshare proxy")
        return YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=WEBSHARE_USERNAME,
                proxy_password=WEBSHARE_PASSWORD,
            )
        )

    # Priority 2: Generic proxy (any http proxy)
    if PROXY_URL:
        print(f"[INFO] Using generic proxy")
        return YouTubeTranscriptApi(
            proxy_config=GenericProxyConfig(
                http_url=PROXY_URL,
                https_url=PROXY_URL,
            )
        )

    # Priority 3: No proxy (direct — will likely be blocked on cloud)
    print(f"[WARN] No proxy configured — may be blocked on cloud")
    return YouTubeTranscriptApi()

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

# ── FETCH TRANSCRIPT ─────────────────────────────────────────
def fetch_transcript(video_id: str, lang: str):
    ytt = get_ytt()

    # Try fetch directly (fastest)
    try:
        langs = list(dict.fromkeys([lang, "en", "hi"]))
        fetched = ytt.fetch(video_id, languages=langs)
        try:
            raw = fetched.to_raw_data()
        except Exception:
            raw = list(fetched)
        lines, full_text = process_raw(raw)
        if lines:
            print(f"[INFO] ✅ fetch() success! {len(lines)} lines")
            return lines, full_text, lang
    except Exception as e:
        print(f"[WARN] fetch() failed: {str(e)[:100]}")

    # Try list then fetch (finds best available language)
    try:
        tl = ytt.list(video_id)
        all_t = list(tl)
        print(f"[INFO] Available: {[t.language_code for t in all_t]}")

        fetched = None
        used_lang = lang

        for t in all_t:
            if t.language_code.startswith(lang):
                fetched = t.fetch()
                used_lang = t.language_code
                break

        if not fetched:
            for t in all_t:
                if not t.is_generated:
                    fetched = t.fetch()
                    used_lang = t.language_code
                    break

        if not fetched and all_t:
            fetched = all_t[0].fetch()
            used_lang = all_t[0].language_code

        if fetched:
            try:
                raw = fetched.to_raw_data()
            except Exception:
                raw = list(fetched)
            lines, full_text = process_raw(raw)
            if lines:
                print(f"[INFO] ✅ list+fetch success! {len(lines)} lines, lang={used_lang}")
                return lines, full_text, used_lang

    except Exception as e:
        print(f"[WARN] list+fetch failed: {str(e)[:100]}")

    raise Exception("Could not fetch transcript with library")

# ── SUPADATA FALLBACK ─────────────────────────────────────────
def fetch_supadata(video_id: str, lang: str):
    if not SUPADATA_API_KEY:
        raise Exception("No Supadata key")

    print(f"[INFO] Trying Supadata fallback...")
    resp = requests.get(
        "https://api.supadata.ai/v1/transcript",
        headers={"x-api-key": SUPADATA_API_KEY},
        params={"url": f"https://www.youtube.com/watch?v={video_id}", "lang": lang},
        timeout=30
    )

    if resp.status_code == 402:
        raise Exception("Supadata quota exceeded")
    if resp.status_code != 200:
        raise Exception(f"Supadata {resp.status_code}")

    content = resp.json().get("content", [])
    if not content:
        raise Exception("Supadata empty")

    lines, texts = [], []
    for item in content:
        text    = item.get("text", "").strip()
        start_s = float(item.get("offset", 0)) / 1000
        dur_s   = float(item.get("duration", 0)) / 1000
        if text:
            lines.append({"text": text, "start": round(start_s,2), "duration": round(dur_s,2), "formatted_time": format_time(start_s)})
            texts.append(text)

    print(f"[INFO] ✅ Supadata success! {len(lines)} lines")
    return lines, " ".join(texts), lang

# ── MODELS ───────────────────────────────────────────────────
class TranscriptRequest(BaseModel):
    url: str
    language: str = "en"

class TranslateRequest(BaseModel):
    text: str
    target_language: str

# ── ROUTES ───────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "status": "YouTube Transcriber API 🚀",
        "version": "12.0",
        "webshare": bool(WEBSHARE_USERNAME),
        "generic_proxy": bool(PROXY_URL),
        "supadata": bool(SUPADATA_API_KEY),
    }

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/transcript")
async def get_transcript(req: TranscriptRequest):
    print(f"\n[INFO] === {req.url} ===")

    video_id = extract_video_id(req.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL.")

    # Try library first, then Supadata fallback
    try:
        lines, full_text, used_lang = fetch_transcript(video_id, req.language)
        return {
            "video_id":   video_id,
            "transcript": lines,
            "full_text":  full_text,
            "word_count": len(full_text.split()),
            "language":   used_lang,
        }
    except Exception as e:
        print(f"[WARN] Library failed: {str(e)[:100]}")

    try:
        lines, full_text, used_lang = fetch_supadata(video_id, req.language)
        return {
            "video_id":   video_id,
            "transcript": lines,
            "full_text":  full_text,
            "word_count": len(full_text.split()),
            "language":   used_lang,
        }
    except Exception as e:
        print(f"[WARN] Supadata failed: {str(e)[:100]}")

    raise HTTPException(
        status_code=404,
        detail="No transcript found. Video may not have captions, or may be private/age-restricted."
    )

@app.get("/languages/{video_id}")
async def get_languages(video_id: str):
    try:
        ytt = get_ytt()
        tl = ytt.list(video_id)
        return {"video_id": video_id, "languages": [
            {"code": t.language_code, "name": t.language, "auto_generated": t.is_generated}
            for t in tl
        ]}
    except Exception as e:
        print(f"[WARN] get_languages: {str(e)[:80]}")
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