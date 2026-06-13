#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build reusable single-speaker reference clips.

The script extracts a temporary 16 kHz mono WAV, scores fixed-duration windows by
RMS energy, and exports the loudest non-adjacent windows as short reference WAVs.
It intentionally avoids ASR: for voiceprint references, clean single-speaker
audio matters more than transcript content.
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def extract_mono_wav(source: Path, target: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found")
    run([
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        str(target),
    ])


def read_pcm(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError("expected mono 16-bit PCM WAV")
        rate = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
    return pcm, rate


def rms_for_window(pcm: bytes, start_sample: int, samples: int) -> float:
    start = start_sample * 2
    end = start + samples * 2
    chunk = memoryview(pcm)[start:end]
    if len(chunk) < samples * 2:
        return 0.0
    total = 0
    count = 0
    for i in range(0, len(chunk), 2):
        val = int.from_bytes(chunk[i : i + 2], byteorder="little", signed=True)
        total += val * val
        count += 1
    return math.sqrt(total / max(count, 1)) / 32768.0


def pick_windows(
    pcm: bytes,
    rate: int,
    *,
    clip_seconds: float,
    count: int,
    scan_step_seconds: float,
    min_start_seconds: float,
    min_gap_seconds: float,
) -> list[tuple[float, float]]:
    total_samples = len(pcm) // 2
    clip_samples = int(clip_seconds * rate)
    step_samples = max(1, int(scan_step_seconds * rate))
    start_sample = int(min_start_seconds * rate)
    candidates: list[tuple[float, float]] = []
    while start_sample + clip_samples <= total_samples:
        score = rms_for_window(pcm, start_sample, clip_samples)
        if score > 0.006:
            candidates.append((score, start_sample / rate))
        start_sample += step_samples

    selected: list[tuple[float, float]] = []
    for score, start in sorted(candidates, reverse=True):
        if all(abs(start - chosen_start) >= min_gap_seconds for _, chosen_start in selected):
            selected.append((score, start))
        if len(selected) >= count:
            break
    return sorted(selected, key=lambda item: item[1])


def export_clip(source: Path, target: Path, start: float, duration: float) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found")
    target.parent.mkdir(parents=True, exist_ok=True)
    run([
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        str(target),
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract clean speaker reference clips from a solo audio/video file")
    parser.add_argument("source", help="single-speaker source audio/video")
    parser.add_argument("--speaker", required=True, help="speaker id, e.g. host")
    parser.add_argument("--out-dir", required=True, help="directory for reference WAV files")
    parser.add_argument("--count", type=int, default=3, help="number of clips to export")
    parser.add_argument("--clip-seconds", type=float, default=10.0, help="duration of each reference clip")
    parser.add_argument("--scan-step-seconds", type=float, default=5.0, help="window scan step")
    parser.add_argument("--min-start-seconds", type=float, default=20.0, help="ignore the very beginning")
    parser.add_argument("--min-gap-seconds", type=float, default=120.0, help="minimum distance between clips")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"source not found: {source}")
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="kdb_speaker_refs_") as tmp:
        temp_wav = Path(tmp) / "source.wav"
        extract_mono_wav(source, temp_wav)
        pcm, rate = read_pcm(temp_wav)
        windows = pick_windows(
            pcm,
            rate,
            clip_seconds=args.clip_seconds,
            count=args.count,
            scan_step_seconds=args.scan_step_seconds,
            min_start_seconds=args.min_start_seconds,
            min_gap_seconds=args.min_gap_seconds,
        )

    if not windows:
        raise SystemExit("no suitable speech windows found")

    print(f"source: {source}")
    for idx, (score, start) in enumerate(windows, 1):
        target = out_dir / f"{args.speaker}_ref_{idx:02d}_{int(start):06d}s.wav"
        export_clip(source, target, start, args.clip_seconds)
        print(f"  {target}  start={start:.1f}s  rms={score:.4f}")


if __name__ == "__main__":
    main()
