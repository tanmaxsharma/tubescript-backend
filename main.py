from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from deep_translator import GoogleTranslator
import re
import os
import requests
import xml.etree.ElementTree as ET
import json
import html

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── GET THESE FROM ENVIRONMENT VARIABLES ──
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "")  # from scraperapi.com
PROXY_URL = os.getenv("PROXY_URL", "")              # optional backup proxy

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

def get_session(use_scraper=False, use_proxy=False):
    """Build a requests session with appropriate proxy"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    if use_scraper and SCRAPER_API_KEY:
        proxy = f"http://scraperapi:{SCRAPER_API_KEY}@proxy-server.scraperapi.com:8001"
        session.proxies = {"http": proxy, "https": proxy}
        session.verify = False
        print(f"[INFO] Using ScraperAPI proxy")
    elif use_proxy and PROXY_URL:
        session.proxies = {"http": PROXY_URL, "https": PROXY_URL}
        print(f"[INFO] Using custom proxy")

    return session

def fetch_transcript_from_youtube(video_id: str, session: requests.Session, lang: str = "en"):
    """
    Fetch transcript by calling YouTube's timedtext API directly.
    This bypasses youtube-transcript-api library entirely.
    """
    # Step 1: Get video page to extract caption tracks
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"[INFO] Fetching video page: {video_url}")

    resp = session.get(video_url, timeout=15)
    if resp.status_code != 200:
        raise Exception(f"YouTube returned {resp.status_code}")

    html_content = resp.text

    # Step 2: Extract captionTracks from page source
    # Look for the captions JSON in the page
    caption_tracks = []

    # Method A: Find captionTracks in ytInitialPlayerResponse
    patterns = [
        r'"captionTracks":(\[.*?\])',
        r'captionTracks":"(.*?)"',
    ]

    for pattern in patterns:
        match = re.search(pattern, html_content)
        if match:
            try:
                tracks_str = match.group(1)
                # Fix escaped unicode
                tracks_str = tracks_str.replace('\\u0026', '&').replace('\\', '')
                caption_tracks = json.loads(tracks_str)
                print(f"[INFO] Found {len(caption_tracks)} caption tracks")
                break
            except Exception as e:
                print(f"[WARN] JSON parse failed: {e}")
                continue

    if not caption_tracks:
        # Method B: Try direct timedtext API
        print(f"[INFO] Trying direct timedtext API...")
        timedtext_url = f"https://www.youtube.com/api/timedtext?v={video_id}&lang={lang}&fmt=json3"
        r = session.get(timedtext_url, timeout=10)
        if r.status_code == 200 and r.text.strip():
            return parse_timedtext_json(r.text)
        raise Exception("No caption tracks found in page source")

    # Step 3: Find best matching caption track
    best_track = None

    # Try requested language first
    for track in caption_tracks:
        base_url = track.get("baseUrl", "")
        lang_code = track.get("languageCode", "")
        if lang_code == lang or lang_code.startswith(lang):
            best_track = base_url
            print(f"[INFO] Found matching lang: {lang_code}")
            break

    # Try English
    if not best_track:
        for track in caption_tracks:
            base_url = track.get("baseUrl", "")
            lang_code = track.get("languageCode", "")
            if "en" in lang_code:
                best_track = base_url
                print(f"[INFO] Found English track: {lang_code}")
                break

    # Use first available
    if not best_track and caption_tracks:
        best_track = caption_tracks[0].get("baseUrl", "")
        print(f"[INFO] Using first available track")

    if not best_track:
        raise Exception("No usable caption track found")

    # Step 4: Fetch the actual transcript
    # Add fmt=json3 for JSON format
    if "fmt=" not in best_track:
        best_track += "&fmt=json3"

    print(f"[INFO] Fetching transcript from caption URL...")
    transcript_resp = session.get(best_track, timeout=10)

    if transcript_resp.status_code != 200:
        raise Exception(f"Caption fetch returned {transcript_resp.status_code}")

    return parse_timedtext_json(transcript_resp.text)

def parse_timedtext_json(json_text: str):
    """Parse YouTube's timedtext JSON3 format"""
    try:
        data = json.loads(json_text)
        lines = []
        texts = []

        events = data.get("events", [])
        for event in events:
            segs = event.get("segs", [])
            start_ms = event.get("tStartMs", 0)
            duration_ms = event.get("dDurationMs", 0)

            text_parts = []
            for seg in segs:
                utf8 = seg.get("utf8", "")
                if utf8 and utf8 != "\n":
                    text_parts.append(utf8)

            text = " ".join(text_parts).strip()
            # Clean HTML entities
            text = html.unescape(text)
            # Remove newlines within text
            text = text.replace("\n", " ").strip()

            if text:
                start_s = start_ms / 1000
                duration_s = duration_ms / 1000
                lines.append({
                    "text": text,
                    "start": round(start_s, 2),
                    "duration": round(duration_s, 2),
                    "formatted_time": format_time(start_s),
                })
                texts.append(text)

        if not lines:
            raise Exception("No lines parsed from timedtext")

        return lines, " ".join(texts)

    except json.JSONDecodeError:
        # Try XML format as fallback
        return parse_timedtext_xml(json_text)

def parse_timedtext_xml(xml_text: str):
    """Parse YouTube's timedtext XML format as fallback"""
    lines = []
    texts = []

    try:
        root = ET.fromstring(xml_text)
        for text_elem in root.findall(".//text"):
            text = html.unescape(text_elem.text or "").strip()
            text = re.sub(r'<[^>]+>', '', text)  # remove any HTML tags
            start = float(text_elem.get("start", 0))
            duration = float(text_elem.get("dur", 0))

            if text:
                lines.append({
                    "text": text,
                    "start": round(start, 2),
                    "duration": round(duration, 2),
                    "formatted_time": format_time(start),
                })
                texts.append(text)

        if not lines:
            raise Exception("No lines in XML")

        return lines, " ".join(texts)

    except Exception as e:
        raise Exception(f"XML parse failed: {e}")

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
        "version": "6.0",
        "scraper_api": bool(SCRAPER_API_KEY),
        "proxy": bool(PROXY_URL),
    }

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/transcript")
async def get_transcript(req: TranscriptRequest):
    print(f"\n[INFO] === New Request ===")
    print(f"[INFO] URL: {req.url} | lang: {req.language}")

    video_id = extract_video_id(req.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL.")

    print(f"[INFO] Video ID: {video_id}")

    # Try different session configs in order
    session_configs = [
        # 1. ScraperAPI (most reliable bypass)
        {"use_scraper": True,  "use_proxy": False, "label": "ScraperAPI"},
        # 2. Custom proxy
        {"use_scraper": False, "use_proxy": True,  "label": "Custom Proxy"},
        # 3. Direct (may work on some IPs)
        {"use_scraper": False, "use_proxy": False, "label": "Direct"},
    ]

    # Skip configs we don't have credentials for
    active_configs = []
    for cfg in session_configs:
        if cfg["use_scraper"] and not SCRAPER_API_KEY:
            print(f"[INFO] Skipping ScraperAPI (no key set)")
            continue
        if cfg["use_proxy"] and not PROXY_URL:
            print(f"[INFO] Skipping proxy (no URL set)")
            continue
        active_configs.append(cfg)

    last_error = None

    for cfg in active_configs:
        try:
            print(f"[INFO] Trying: {cfg['label']}")
            session = get_session(
                use_scraper=cfg["use_scraper"],
                use_proxy=cfg["use_proxy"]
            )
            lines, full_text = fetch_transcript_from_youtube(video_id, session, req.language)
            word_count = len(full_text.split())
            print(f"[INFO] ✅ SUCCESS via {cfg['label']}! {len(lines)} lines, {word_count} words")

            return {
                "video_id": video_id,
                "transcript": lines,
                "full_text": full_text,
                "word_count": word_count,
                "language": req.language,
            }

        except Exception as e:
            print(f"[WARN] {cfg['label']} failed: {str(e)[:150]}")
            last_error = str(e)
            continue

    print(f"[ERROR] All methods failed: {last_error}")

    err_lower = (last_error or "").lower()
    if any(w in err_lower for w in ["blocked", "403", "too many", "cloud"]):
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
    session_configs = [
        {"use_scraper": True,  "use_proxy": False},
        {"use_scraper": False, "use_proxy": True},
        {"use_scraper": False, "use_proxy": False},
    ]

    for cfg in session_configs:
        if cfg["use_scraper"] and not SCRAPER_API_KEY:
            continue
        if cfg["use_proxy"] and not PROXY_URL:
            continue
        try:
            session = get_session(**cfg)
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            resp = session.get(video_url, timeout=15)
            html_content = resp.text

            match = re.search(r'"captionTracks":(\[.*?\])', html_content)
            if match:
                tracks_str = match.group(1).replace('\\u0026', '&').replace('\\', '')
                tracks = json.loads(tracks_str)
                languages = []
                for t in tracks:
                    lang_code = t.get("languageCode", "")
                    lang_name = t.get("name", {}).get("simpleText", lang_code)
                    is_auto = "asr" in t.get("kind", "")
                    if lang_code:
                        languages.append({
                            "code": lang_code,
                            "name": lang_name,
                            "auto_generated": is_auto,
                        })
                return {"video_id": video_id, "languages": languages}
        except Exception as e:
            print(f"[WARN] get_languages failed: {str(e)[:80]}")
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
            result = GoogleTranslator(source='auto', target=req.target_language).translate(chunk)
            translated_chunks.append(result)
        return {
            "translated": " ".join(translated_chunks),
            "target_language": req.target_language
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation failed: {str(e)}")