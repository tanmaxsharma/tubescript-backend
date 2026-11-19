
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from deep_translator import GoogleTranslator
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import (
    WebshareProxyConfig,
    GenericProxyConfig,
)

from urllib.parse import urlparse, parse_qs
from typing import Optional
import os
import re
import requests


load_dotenv()

app = FastAPI(
    title="YouTube Transcriber API",
    version="13.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

WEBSHARE_USERNAME = os.getenv("WEBSHARE_USERNAME", "")
WEBSHARE_PASSWORD = os.getenv("WEBSHARE_PASSWORD", "")

PROXY_URL = os.getenv("PROXY_URL", "")

SUPADATA_API_KEY = os.getenv("SUPADATA_API_KEY", "")


# ============================================================
# BUILD YOUTUBE TRANSCRIPT API INSTANCE
# ============================================================

def get_ytt():
    """Return YouTubeTranscriptApi with configured proxy."""

    if WEBSHARE_USERNAME and WEBSHARE_PASSWORD:
        print("[INFO] Using Webshare proxy")

        return YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=WEBSHARE_USERNAME,
                proxy_password=WEBSHARE_PASSWORD,
            )
        )

    if PROXY_URL:
        print("[INFO] Using generic proxy")

        return YouTubeTranscriptApi(
            proxy_config=GenericProxyConfig(
                http_url=PROXY_URL,
                https_url=PROXY_URL,
            )
        )

    print("[WARN] No proxy configured")

    return YouTubeTranscriptApi()


# ============================================================
# HELPER FUNCTIONS
# ============================================================

VIDEO_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{11}$")


def extract_video_id(url: str) -> Optional[str]:
    """
    Extract and validate YouTube video ID.

    Supported formats:
    - youtube.com/watch?v=VIDEO_ID
    - youtu.be/VIDEO_ID
    - youtube.com/shorts/VIDEO_ID
    - youtube.com/embed/VIDEO_ID
    - Direct 11-character video ID
    """

    if not isinstance(url, str):
        return None

    url = url.strip()

    if not url:
        return None

    # Direct video ID
    if VIDEO_ID_PATTERN.fullmatch(url):
        return url

    # Parse URL safely
    try:
        parsed = urlparse(url)

        # Require a valid HTTP/HTTPS URL
        if parsed.scheme not in ("http", "https"):
            return None

        hostname = (parsed.hostname or "").lower()

        video_id = None

        # Standard YouTube URL
        if hostname in ("youtube.com", "www.youtube.com", "m.youtube.com"):
            if parsed.path == "/watch":
                query = parse_qs(parsed.query)
                video_id = query.get("v", [None])[0]

            else:
                match = re.match(
                    r"^/(?:shorts|embed)/([a-zA-Z0-9_-]{11})/?$",
                    parsed.path,
                )

                if match:
                    video_id = match.group(1)

        # Short YouTube URL
        elif hostname == "youtu.be":
            match = re.match(
                r"^/([a-zA-Z0-9_-]{11})/?$",
                parsed.path,
            )

            if match:
                video_id = match.group(1)

        if video_id and VIDEO_ID_PATTERN.fullmatch(video_id):
            return video_id

    except (ValueError, TypeError):
        return None

    return None


def format_time(seconds: float) -> str:
    """Convert seconds into H:MM:SS or M:SS."""

    seconds = max(0, int(seconds))

    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"

    return f"{minutes}:{secs:02d}"


def process_raw(raw):
    """Normalize dictionary and object transcript entries."""

    lines = []
    texts = []

    for entry in raw:
        if isinstance(entry, dict):
            text = str(entry.get("text") or "").strip()
            start = float(entry.get("start", 0) or 0)
            duration = float(entry.get("duration", 0) or 0)

        else:
            text = str(getattr(entry, "text", "") or "").strip()
            start = float(getattr(entry, "start", 0) or 0)
            duration = float(getattr(entry, "duration", 0) or 0)

        if not text:
            continue

        lines.append({
            "text": text,
            "start": round(start, 2),
            "duration": round(duration, 2),
            "formatted_time": format_time(start),
        })

        texts.append(text)

    return lines, " ".join(texts)


# ============================================================
# FETCH TRANSCRIPT
# ============================================================

def fetch_transcript(video_id: str, lang: str):

    ytt = get_ytt()

    # Strategy 1: Direct fetch
    try:
        languages = list(dict.fromkeys([lang, "en", "hi"]))

        fetched = ytt.fetch(
            video_id,
            languages=languages,
        )

        try:
            raw = fetched.to_raw_data()
        except Exception:
            raw = list(fetched)

        lines, full_text = process_raw(raw)

        if lines:
            print(f"[INFO] Direct fetch success: {len(lines)} lines")
            return lines, full_text, lang

    except Exception as e:
        print(f"[WARN] Direct fetch failed: {str(e)[:100]}")

    # Strategy 2: List available transcripts
    try:
        transcript_list = ytt.list(video_id)
        all_transcripts = list(transcript_list)

        print(
            "[INFO] Available languages:",
            [t.language_code for t in all_transcripts],
        )

        fetched = None
        used_lang = lang

        # Prefer requested language
        for transcript in all_transcripts:
            if transcript.language_code.startswith(lang):
                fetched = transcript.fetch()
                used_lang = transcript.language_code
                break

        # Prefer manually created transcripts
        if fetched is None:
            for transcript in all_transcripts:
                if not transcript.is_generated:
                    fetched = transcript.fetch()
                    used_lang = transcript.language_code
                    break

        # Last available transcript
        if fetched is None and all_transcripts:
            fetched = all_transcripts[0].fetch()
            used_lang = all_transcripts[0].language_code

        if fetched is not None:

            try:
                raw = fetched.to_raw_data()
            except Exception:
                raw = list(fetched)

            lines, full_text = process_raw(raw)

            if lines:
                print(
                    f"[INFO] List fetch success: "
                    f"{len(lines)} lines, language={used_lang}"
                )

                return lines, full_text, used_lang

    except Exception as e:
        print(f"[WARN] List fetch failed: {str(e)[:100]}")

    raise Exception("Could not fetch transcript with library")


# ============================================================
# SUPADATA FALLBACK
# ============================================================

def fetch_supadata(video_id: str, lang: str):

    if not SUPADATA_API_KEY:
        raise Exception("Supadata API key not configured")

    print("[INFO] Trying Supadata fallback")

    response = requests.get(
        "https://api.supadata.ai/v1/transcript",
        headers={
            "x-api-key": SUPADATA_API_KEY,
        },
        params={
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "lang": lang,
        },
        timeout=30,
    )

    if response.status_code == 402:
        raise Exception("Supadata quota exceeded")

    if response.status_code != 200:
        raise Exception(
            f"Supadata returned HTTP {response.status_code}"
        )

    content = response.json().get("content", [])

    if not content:
        raise Exception("Supadata returned empty transcript")

    lines = []
    texts = []

    for item in content:

        text = str(item.get("text") or "").strip()

        start = float(item.get("offset", 0) or 0) / 1000
        duration = float(item.get("duration", 0) or 0) / 1000

        if not text:
            continue

        lines.append({
            "text": text,
            "start": round(start, 2),
            "duration": round(duration, 2),
            "formatted_time": format_time(start),
        })

        texts.append(text)

    if not lines:
        raise Exception("Supadata returned no valid transcript entries")

    print(f"[INFO] Supadata success: {len(lines)} lines")

    return lines, " ".join(texts), lang


# ============================================================
# REQUEST MODELS
# ============================================================

class TranscriptRequest(BaseModel):

    url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
    )

    language: str = Field(
        default="en",
        min_length=2,
        max_length=10,
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, value):

        value = value.strip()

        if not value:
            raise ValueError("YouTube URL cannot be empty")

        return value

    @field_validator("language")
    @classmethod
    def validate_language(cls, value):

        value = value.strip()

        if not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,5})?", value):
            raise ValueError("Invalid language code")

        return value.lower()


class TranslateRequest(BaseModel):

    text: str = Field(
        ...,
        min_length=1,
    )

    target_language: str = Field(
        ...,
        min_length=2,
        max_length=10,
    )

    @field_validator("text")
    @classmethod
    def validate_text(cls, value):

        value = value.strip()

        if not value:
            raise ValueError("Text cannot be empty")

        return value

    @field_validator("target_language")
    @classmethod
    def validate_target_language(cls, value):

        value = value.strip()

        if not re.fullmatch(
            r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,5})?",
            value,
        ):
            raise ValueError("Invalid target language")

        return value.lower()


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def root():

    return {
        "status": "YouTube Transcriber API",
        "version": "13.0",
        "webshare": bool(WEBSHARE_USERNAME),
        "generic_proxy": bool(PROXY_URL),
        "supadata": bool(SUPADATA_API_KEY),
    }


@app.get("/health")
def health():

    return {
        "status": "ok",
    }


# ============================================================
# TRANSCRIPT ENDPOINT
# ============================================================

@app.post("/transcript")
async def get_transcript(req: TranscriptRequest):

    print(f"\n[INFO] === {req.url} ===")

    video_id = extract_video_id(req.url)

    if not video_id:
        raise HTTPException(
            status_code=400,
            detail="Invalid YouTube URL or video ID.",
        )

    # Strategy 1: YouTube Transcript API
    try:

        lines, full_text, used_lang = fetch_transcript(
            video_id,
            req.language,
        )

        return {
            "video_id": video_id,
            "transcript": lines,
            "full_text": full_text,
            "word_count": len(full_text.split()),
            "language": used_lang,
        }

    except Exception as e:
        print(f"[WARN] Library failed: {str(e)[:100]}")

    # Strategy 2: Supadata fallback
    try:

        lines, full_text, used_lang = fetch_supadata(
            video_id,
            req.language,
        )

        return {
            "video_id": video_id,
            "transcript": lines,
            "full_text": full_text,
            "word_count": len(full_text.split()),
            "language": used_lang,
        }

    except Exception as e:
        print(f"[WARN] Supadata failed: {str(e)[:100]}")

    raise HTTPException(
        status_code=404,
        detail=(
            "No transcript found. Video may not have captions, "
            "or may be private/age-restricted."
        ),
    )


# ============================================================
# AVAILABLE LANGUAGES ENDPOINT
# ============================================================

@app.get("/languages/{video_id}")
async def get_languages(video_id: str):

    if not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid YouTube video ID.",
        )

    try:

        ytt = get_ytt()
        transcript_list = ytt.list(video_id)

        return {
            "video_id": video_id,
            "languages": [
                {
                    "code": transcript.language_code,
                    "name": transcript.language,
                    "auto_generated": transcript.is_generated,
                }
                for transcript in transcript_list
            ],
        }

    except Exception as e:

        print(f"[WARN] get_languages: {str(e)[:80]}")

        return {
            "video_id": video_id,
            "languages": [],
        }


# ============================================================
# TRANSLATION ENDPOINT
# ============================================================

@app.post("/translate")
async def translate_text(req: TranslateRequest):

    try:

        chunks = [
            req.text[i:i + 4500]
            for i in range(0, len(req.text), 4500)
        ]

        translator = GoogleTranslator(
            source="auto",
            target=req.target_language,
        )

        translated_chunks = []

        for chunk in chunks:

            translated = translator.translate(chunk)

            if translated is None:
                raise Exception("Translation service returned empty response")

            translated_chunks.append(translated)

        result = " ".join(translated_chunks)

        return {
            "translated": result,
            "target_language": req.target_language,
        }

    except Exception as e:

        print(f"[WARN] Translation failed: {str(e)[:100]}")

        raise HTTPException(
            status_code=500,
            detail="Translation failed. Please try again later.",
        )
