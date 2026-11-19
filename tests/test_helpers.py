
import pytest

from main import extract_video_id, format_time, process_raw


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("invalid-url", None),
    ],
)
def test_extract_video_id(url, expected):
    assert extract_video_id(url) == expected


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0, "0:00"),
        (59, "0:59"),
        (60, "1:00"),
        (3661, "1:01:01"),
        (7200, "2:00:00"),
    ],
)
def test_format_time(seconds, expected):
    assert format_time(seconds) == expected


def test_process_raw_with_transcript_entries():
    raw = [
        {"text": "Hello", "start": 0, "duration": 2},
        {"text": "World", "start": 2, "duration": 3},
    ]

    lines, full_text = process_raw(raw)

    assert len(lines) == 2
    assert full_text == "Hello World"
    assert lines[0]["formatted_time"] == "0:00"
    assert lines[1]["formatted_time"] == "0:02"


def test_process_raw_with_empty_input():
    lines, full_text = process_raw([])

    assert lines == []
    assert full_text == ""
