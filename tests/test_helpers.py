
import pytest
from types import SimpleNamespace

from main import extract_video_id, format_time, process_raw


# ============================================================
# TEST: YOUTUBE VIDEO ID EXTRACTION
# ============================================================

@pytest.mark.parametrize(
    "url, expected",
    [
        # Valid YouTube URLs
        (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "dQw4w9WgXcQ",
        ),
        (
            "https://youtu.be/dQw4w9WgXcQ",
            "dQw4w9WgXcQ",
        ),
        (
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "dQw4w9WgXcQ",
        ),
        (
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "dQw4w9WgXcQ",
        ),

        # Direct video ID
        (
            "dQw4w9WgXcQ",
            "dQw4w9WgXcQ",
        ),

        # Invalid URLs
        ("invalid-url-too-long", None),
        ("", None),
        ("https://youtube.com/watch?v=invalid", None),
        ("https://youtu.be/invalid", None),
        (
            "https://google.com/watch?v=dQw4w9WgXcQ",
            None,
        ),
        (
            "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
            None,
        ),
        (
            "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
            None,
        ),
        (
            "https://youtube.com/shorts/invalid",
            None,
        ),
        (
            "https://youtube.com/embed/invalid",
            None,
        ),
    ],
)
def test_extract_video_id(url, expected):
    assert extract_video_id(url) == expected


# ============================================================
# TEST: TIME FORMATTING
# ============================================================

@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0, "0:00"),
        (1, "0:01"),
        (59, "0:59"),
        (60, "1:00"),
        (90, "1:30"),
        (3599, "59:59"),
        (3600, "1:00:00"),
        (3661, "1:01:01"),
        (7200, "2:00:00"),
    ],
)
def test_format_time(seconds, expected):
    assert format_time(seconds) == expected


# ============================================================
# TEST: DICTIONARY-BASED TRANSCRIPT PROCESSING
# ============================================================

def test_process_raw_with_transcript_entries():

    raw = [
        {
            "text": "Hello",
            "start": 0,
            "duration": 2,
        },
        {
            "text": "World",
            "start": 2,
            "duration": 3,
        },
    ]

    lines, full_text = process_raw(raw)

    assert len(lines) == 2
    assert full_text == "Hello World"

    assert lines[0]["text"] == "Hello"
    assert lines[0]["start"] == 0
    assert lines[0]["duration"] == 2
    assert lines[0]["formatted_time"] == "0:00"

    assert lines[1]["text"] == "World"
    assert lines[1]["formatted_time"] == "0:02"


# ============================================================
# TEST: EMPTY TRANSCRIPT
# ============================================================

def test_process_raw_with_empty_input():

    lines, full_text = process_raw([])

    assert lines == []
    assert full_text == ""


# ============================================================
# TEST: OBJECT-BASED TRANSCRIPT PROCESSING
# ============================================================

def test_process_raw_with_object_entries():

    raw = [
        SimpleNamespace(
            text="Hello",
            start=0,
            duration=2,
        ),
        SimpleNamespace(
            text="World",
            start=2,
            duration=3,
        ),
    ]

    lines, full_text = process_raw(raw)

    assert len(lines) == 2
    assert full_text == "Hello World"

    assert lines[0]["text"] == "Hello"
    assert lines[1]["text"] == "World"

    assert lines[0]["formatted_time"] == "0:00"
    assert lines[1]["formatted_time"] == "0:02"


# ============================================================
# TEST: EMPTY TRANSCRIPT ENTRIES
# ============================================================

def test_process_raw_ignores_empty_text():

    raw = [
        {"text": "Hello", "start": 0, "duration": 2},
        {"text": "", "start": 2, "duration": 1},
        {"text": "World", "start": 3, "duration": 2},
    ]

    lines, full_text = process_raw(raw)

    assert len(lines) == 2
    assert full_text == "Hello World"


# ============================================================
# TEST: MISSING TRANSCRIPT FIELDS
# ============================================================

def test_process_raw_with_missing_fields():

    raw = [
        {"text": "Hello"},
        {"text": "World", "start": 2},
    ]

    lines, full_text = process_raw(raw)

    assert len(lines) == 2
    assert full_text == "Hello World"

    assert lines[0]["start"] == 0
    assert lines[0]["duration"] == 0

    assert lines[1]["start"] == 2
    assert lines[1]["duration"] == 0

def test_process_raw_ignores_whitespace_only_text():
    raw = [
        {"text": "Hello", "start": 0, "duration": 2},
        {"text": "   ", "start": 2, "duration": 1},
        {"text": "\n\t", "start": 3, "duration": 1},
        {"text": "World", "start": 4, "duration": 2},
    ]

    lines, full_text = process_raw(raw)

    assert len(lines) == 2
    assert [line["text"] for line in lines] == ["Hello", "World"]
    assert full_text == "Hello World"
