#!/usr/bin/env python3
"""Assemble real-motion video clips into a single vertical reel (ffmpeg).

Scope is deliberately minimal and honest: trim + concatenate real video
clips, scale/crop to a vertical frame, optionally mux an audio track.
Still-image slideshows and Ken Burns-style pan/zoom are NOT supported by
design — reels here are real motion or nothing.

ffmpeg is an optional system dependency; the script errors clearly when it
is missing.

Usage:
    python reels/assemble.py --clip intro.mp4:3.0 --clip main.mp4 \
        --clip outro.mp4:2.5 --audio track.mp3 --out reel.mp4

    # print the ffmpeg command without running it:
    python reels/assemble.py --clip a.mp4:3 --out reel.mp4 --print-cmd
"""

import argparse
import os
import shutil
import subprocess
import sys

DEFAULT_W, DEFAULT_H, DEFAULT_FPS, DEFAULT_CRF = 1080, 1920, 30, 20


def parse_clip(spec):
    """'path' or 'path:seconds' -> (path, seconds|None)."""
    if ":" in spec and not spec.startswith("http"):
        # allow Windows drive letters and colons in filenames: split on the
        # LAST colon, and only treat it as a duration if it parses as float
        head, _, tail = spec.rpartition(":")
        try:
            return head, float(tail)
        except ValueError:
            pass
    return spec, None


def build_command(
    clips,
    out,
    audio=None,
    width=DEFAULT_W,
    height=DEFAULT_H,
    fps=DEFAULT_FPS,
    crf=DEFAULT_CRF,
):
    """Pure function: (clips, opts) -> ffmpeg argv list. No side effects."""
    if not clips:
        raise ValueError("at least one --clip is required")
    cmd = ["ffmpeg", "-y"]
    for path, dur in clips:
        if dur is not None:
            if dur <= 0:
                raise ValueError(f"non-positive duration in clip {path!r}")
            cmd += ["-ss", "0", "-t", str(dur)]
        cmd += ["-i", path]
    if audio:
        cmd += ["-i", audio]

    vfilters = []
    for i in range(len(clips)):
        vfilters.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps}[v{i}]"
        )
    vcat = "".join(f"[v{i}]" for i in range(len(clips)))
    filt = ";".join(vfilters) + f";{vcat}concat=n={len(clips)}:v=1:a=0[v]"
    cmd += ["-filter_complex", filt, "-map", "[v]"]
    if audio:
        cmd += ["-map", f"{len(clips)}:a", "-c:a", "aac", "-shortest"]
    cmd += [
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        out,
    ]
    return cmd


def check_ffmpeg():
    if shutil.which("ffmpeg") is None:
        raise SystemExit(
            "ffmpeg not found. Install it first:\n"
            "  macOS:   brew install ffmpeg\n"
            "  Ubuntu:  sudo apt install ffmpeg\n"
            "  Windows: winget install Gyan.FFmpeg   (or ffmpeg.org builds)"
        )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--clip",
        action="append",
        required=True,
        metavar="SPEC",
        help="'path' or 'path:seconds' (repeatable, in order)",
    )
    ap.add_argument("--audio", help="audio track to mux (trimmed to video)")
    ap.add_argument("--out", required=True, help="output mp4 path")
    ap.add_argument("--width", type=int, default=DEFAULT_W)
    ap.add_argument("--height", type=int, default=DEFAULT_H)
    ap.add_argument("--fps", type=int, default=DEFAULT_FPS)
    ap.add_argument(
        "--crf",
        type=int,
        default=DEFAULT_CRF,
        help="x264 quality 0-51, lower is better (default 20)",
    )
    ap.add_argument(
        "--print-cmd",
        action="store_true",
        help="print the ffmpeg command without running it",
    )
    args = ap.parse_args(argv)

    clips = []
    for spec in args.clip:
        path, dur = parse_clip(spec)
        if not args.print_cmd and not os.path.isfile(path):
            ap.error(f"clip not found: {path}")
        clips.append((path, dur))
    if args.audio and not args.print_cmd and not os.path.isfile(args.audio):
        ap.error(f"audio not found: {args.audio}")

    cmd = build_command(
        clips,
        args.out,
        audio=args.audio,
        width=args.width,
        height=args.height,
        fps=args.fps,
        crf=args.crf,
    )
    if args.print_cmd:
        print(" ".join(cmd))
        return 0
    check_ffmpeg()
    print("+", " ".join(cmd))
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        raise SystemExit(f"ffmpeg failed (exit {rc})")
    print(f"ASSEMBLE_OK out={args.out} clips={len(clips)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
