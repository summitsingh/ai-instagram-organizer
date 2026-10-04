"""Offline unit tests for reels/assemble.py.

Command-construction tests only: ffmpeg is never invoked.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from reels.assemble import build_command, parse_clip


def test_parse_clip_plain():
    assert parse_clip("a.mp4") == ("a.mp4", None)


def test_parse_clip_with_seconds():
    assert parse_clip("a.mp4:3.5") == ("a.mp4", 3.5)


def test_parse_clip_colon_in_name_not_duration():
    # "a:b.mp4" has no numeric tail -> treated as a plain path
    assert parse_clip("a:b.mp4") == ("a:b.mp4", None)


def test_build_command_single_clip():
    cmd = build_command([("a.mp4", None)], "out.mp4")
    assert cmd[0] == "ffmpeg"
    assert "-i" in cmd and "a.mp4" in cmd
    assert cmd[-1] == "out.mp4"
    assert "-t" not in cmd  # no trim without a duration


def test_build_command_trims_and_concats():
    cmd = build_command([("a.mp4", 3.0), ("b.mp4", None)], "out.mp4")
    s = " ".join(cmd)
    assert "-t 3.0" in s
    assert "concat=n=2:v=1:a=0" in s
    assert "scale=1080:1920" in s


def test_build_command_audio(tmp_path):
    cmd = build_command([("a.mp4", 2)], "out.mp4", audio="song.mp3")
    s = " ".join(cmd)
    assert "-i song.mp3" in s
    assert "-c:a aac" in s
    assert "-shortest" in s


def test_build_command_custom_frame():
    cmd = build_command(
        [("a.mp4", None)], "out.mp4", width=720, height=1280, fps=24, crf=23
    )
    s = " ".join(cmd)
    assert "scale=720:1280" in s
    assert "fps=24" in s
    assert "-crf 23" in s


def test_build_command_rejects_empty():
    with pytest.raises(ValueError):
        build_command([], "out.mp4")


def test_build_command_rejects_bad_duration():
    with pytest.raises(ValueError):
        build_command([("a.mp4", 0)], "out.mp4")
