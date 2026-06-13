#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local-first speaker attribution for KDB post-production.

Pipeline:
  1. extract 16 kHz mono audio
  2. run local pyannote diarization
  3. optionally match diarized clusters to known speaker references
  4. merge speaker labels back into an existing SRT

The ASR transcript remains the source of truth for words. Diarization only
answers who spoke when.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np


DEFAULT_DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
FALLBACK_DIARIZATION_MODEL = "pyannote/speaker-diarization-3.1"
DEFAULT_EMBEDDING_MODEL = "pyannote/wespeaker-voxceleb-resnet34-LM"


@dataclass
class Turn:
    start: float
    end: float
    speaker: str
    raw_speaker: str | None = None
    confidence: float | None = None
    source: str = "diarization"

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Cue:
    index: int
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.001, self.end - self.start)


def episode_stem(media_path: Path) -> str:
    return media_path.with_suffix("").name


def default_process_dir(media_path: Path) -> Path:
    return media_path.parent / f"{episode_stem(media_path)}_process"


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def extract_audio(media_path: Path, wav_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found")
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    run([
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(media_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        str(wav_path),
    ])


def token_from_env() -> str | None:
    for key in ("HF_TOKEN", "HUGGINGFACE_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        val = os.environ.get(key)
        if val:
            return val
    try:
        from huggingface_hub import get_token

        return get_token()
    except Exception:
        return None


def resolve_device(preference: str):
    try:
        import torch
    except Exception:
        return None
    if preference == "auto":
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return torch.device("mps")
        if torch.cuda.is_available():
            return torch.device("cuda")
        return torch.device("cpu")
    return torch.device(preference)


def instantiate_pipeline(model_name: str, token: str | None):
    try:
        from pyannote.audio import Pipeline
    except Exception as exc:
        raise SystemExit(
            "pyannote.audio is not installed. Create the diarization env with:\n"
            "  /opt/homebrew/bin/python3.11 -m venv venv-diarization\n"
            "  venv-diarization/bin/pip install -r requirements-diarization.txt"
        ) from exc

    kwargs = {}
    if token:
        kwargs["token"] = token
        kwargs["use_auth_token"] = token
    for auth_key in ("token", "use_auth_token"):
        try:
            selected = {"token": token} if auth_key == "token" and token else {}
            if auth_key == "use_auth_token" and token:
                selected = {"use_auth_token": token}
            return Pipeline.from_pretrained(model_name, **selected)
        except TypeError:
            continue
        except Exception:
            raise
    return Pipeline.from_pretrained(model_name, **kwargs)


def annotation_to_turns(output, *, exclusive: bool) -> list[Turn]:
    annotation = output
    if exclusive and hasattr(output, "exclusive_speaker_diarization"):
        annotation = output.exclusive_speaker_diarization
    elif hasattr(output, "speaker_diarization"):
        annotation = output.speaker_diarization

    turns: list[Turn] = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        turns.append(Turn(float(turn.start), float(turn.end), str(speaker), raw_speaker=str(speaker)))
    turns.sort(key=lambda item: (item.start, item.end, item.speaker))
    return turns


def write_rttm(turns: Iterable[Turn], path: Path, file_id: str) -> None:
    with path.open("w", encoding="utf-8") as f:
        for turn in turns:
            f.write(
                f"SPEAKER {file_id} 1 {turn.start:.3f} {turn.duration:.3f} "
                f"<NA> <NA> {turn.speaker} <NA> <NA>\n"
            )


def run_diarization(
    audio_path: Path,
    *,
    model_name: str,
    num_speakers: int | None,
    min_speakers: int | None,
    max_speakers: int | None,
    exclusive: bool,
    device_preference: str,
) -> list[Turn]:
    token = token_from_env()
    if not token:
        print(
            "warning: no Hugging Face token found. If model loading fails, run "
            "`huggingface-cli login` and accept the pyannote model terms.",
            file=sys.stderr,
        )
    try:
        pipeline = instantiate_pipeline(model_name, token)
    except Exception as exc:
        if model_name == DEFAULT_DIARIZATION_MODEL:
            print(f"community-1 unavailable, trying legacy 3.1: {exc}", file=sys.stderr)
            pipeline = instantiate_pipeline(FALLBACK_DIARIZATION_MODEL, token)
        else:
            raise
    device = resolve_device(device_preference)
    if device is not None:
        try:
            pipeline.to(device)
            print(f"pyannote device: {device}", flush=True)
        except Exception as exc:
            print(f"warning: could not move diarization pipeline to {device}: {exc}", file=sys.stderr)

    kwargs: dict[str, int] = {}
    if num_speakers:
        kwargs["num_speakers"] = num_speakers
    if min_speakers:
        kwargs["min_speakers"] = min_speakers
    if max_speakers:
        kwargs["max_speakers"] = max_speakers
    output = pipeline(str(audio_path), **kwargs)
    return annotation_to_turns(output, exclusive=exclusive)


def parse_ref_arg(value: str) -> tuple[str, list[Path]]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("speaker reference must be NAME=path[,path...]")
    name, refs = value.split("=", 1)
    paths = [Path(item).expanduser().resolve() for item in refs.split(",") if item.strip()]
    if not name.strip() or not paths:
        raise argparse.ArgumentTypeError("speaker reference must be NAME=path[,path...]")
    for path in paths:
        if not path.exists():
            raise argparse.ArgumentTypeError(f"reference not found: {path}")
    return name.strip(), paths


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float32).reshape(-1)
    b = np.asarray(b, dtype=np.float32).reshape(-1)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def load_embedding_inference(model_name: str, token: str | None, device_preference: str):
    try:
        from pyannote.audio import Inference, Model
    except Exception as exc:
        raise SystemExit("pyannote.audio embedding dependencies are not available") from exc

    kwargs = {"token": token} if token else {}
    try:
        model = Model.from_pretrained(model_name, **kwargs)
    except TypeError:
        kwargs = {"use_auth_token": token} if token else {}
        model = Model.from_pretrained(model_name, **kwargs)
    device = resolve_device(device_preference)
    if device is not None:
        try:
            model.to(device)
        except Exception as exc:
            print(f"warning: could not move embedding model to {device}: {exc}", file=sys.stderr)
    try:
        return Inference(model, window="whole", device=device)
    except TypeError:
        return Inference(model, window="whole")


def embed_file(inference, path: Path) -> np.ndarray:
    emb = inference(str(path))
    if hasattr(emb, "data"):
        emb = emb.data
    arr = np.asarray(emb, dtype=np.float32)
    return arr.reshape(-1)


def extract_turn_clip(audio_path: Path, turn: Turn, target: Path, max_seconds: float = 12.0) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found")
    duration = min(max_seconds, max(0.5, turn.duration))
    run([
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{turn.start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(audio_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        str(target),
    ])


def average_embeddings(vectors: list[np.ndarray]) -> np.ndarray:
    if not vectors:
        raise ValueError("no embeddings")
    stacked = np.vstack([v.reshape(1, -1) for v in vectors])
    return stacked.mean(axis=0)


def identify_speakers(
    turns: list[Turn],
    audio_path: Path,
    refs: dict[str, list[Path]],
    *,
    embedding_model: str,
    device_preference: str,
    threshold: float,
    assign_remaining: str | None,
    work_dir: Path,
) -> tuple[dict[str, dict], list[Turn]]:
    raw_speakers = sorted({turn.raw_speaker or turn.speaker for turn in turns})
    if not refs:
        mapping = {speaker: {"name": speaker, "score": None, "method": "raw"} for speaker in raw_speakers}
        return mapping, turns

    token = token_from_env()
    inference = load_embedding_inference(embedding_model, token, device_preference)

    ref_embs: dict[str, np.ndarray] = {}
    for name, paths in refs.items():
        ref_embs[name] = average_embeddings([embed_file(inference, path) for path in paths])

    cluster_embs: dict[str, np.ndarray] = {}
    sample_dir = work_dir / "speaker_cluster_samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    for raw in raw_speakers:
        candidates = sorted(
            [turn for turn in turns if (turn.raw_speaker or turn.speaker) == raw and turn.duration >= 2.0],
            key=lambda item: item.duration,
            reverse=True,
        )[:3]
        vectors: list[np.ndarray] = []
        for idx, turn in enumerate(candidates, 1):
            clip = sample_dir / f"{raw}_{idx:02d}_{int(turn.start):06d}s.wav"
            extract_turn_clip(audio_path, turn, clip)
            vectors.append(embed_file(inference, clip))
        if vectors:
            cluster_embs[raw] = average_embeddings(vectors)

    scores: list[tuple[float, str, str]] = []
    for raw, emb in cluster_embs.items():
        for name, ref_emb in ref_embs.items():
            scores.append((cosine(emb, ref_emb), raw, name))
    scores.sort(reverse=True)

    mapping: dict[str, dict] = {}
    used_raw: set[str] = set()
    used_names: set[str] = set()
    for score, raw, name in scores:
        if score < threshold or raw in used_raw or name in used_names:
            continue
        mapping[raw] = {"name": name, "score": score, "method": "voice_match"}
        used_raw.add(raw)
        used_names.add(name)

    unmatched = [raw for raw in raw_speakers if raw not in mapping]
    if assign_remaining and len(raw_speakers) == 2 and len(unmatched) == 1:
        mapping[unmatched[0]] = {"name": assign_remaining, "score": None, "method": "remaining_speaker"}
    for raw in raw_speakers:
        mapping.setdefault(raw, {"name": raw, "score": None, "method": "unmatched"})

    labeled: list[Turn] = []
    for turn in turns:
        raw = turn.raw_speaker or turn.speaker
        item = mapping[raw]
        labeled.append(Turn(turn.start, turn.end, item["name"], raw_speaker=raw, confidence=item["score"]))
    return mapping, labeled


_SRT_TS_RE = re.compile(
    r"(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2}),(?P<sms>\d{3})\s*-->\s*"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2}),(?P<ems>\d{3})"
)


def parse_ts(match: re.Match[str], prefix: str) -> float:
    return (
        int(match.group(prefix + "h")) * 3600
        + int(match.group(prefix + "m")) * 60
        + int(match.group(prefix + "s"))
        + int(match.group(prefix + "ms")) / 1000
    )


def parse_srt(path: Path) -> list[Cue]:
    text = path.read_text(encoding="utf-8-sig")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        ts_idx = next((idx for idx, line in enumerate(lines) if "-->" in line), None)
        if ts_idx is None:
            continue
        match = _SRT_TS_RE.search(lines[ts_idx])
        if not match:
            continue
        try:
            index = int(lines[0]) if ts_idx > 0 and lines[0].isdigit() else len(cues) + 1
        except ValueError:
            index = len(cues) + 1
        body = "\n".join(lines[ts_idx + 1 :]).strip()
        cues.append(Cue(index, parse_ts(match, "s"), parse_ts(match, "e"), body))
    return cues


def fmt_ts(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def label_cue(cue: Cue, turns: list[Turn], *, min_dominance: float) -> tuple[str, dict[str, float], str]:
    by_speaker: dict[str, float] = {}
    for turn in turns:
        ov = overlap(cue.start, cue.end, turn.start, turn.end)
        if ov > 0:
            by_speaker[turn.speaker] = by_speaker.get(turn.speaker, 0.0) + ov
    if not by_speaker:
        return "UNKNOWN", {}, "no_overlap"
    total = sum(by_speaker.values())
    speaker, best = max(by_speaker.items(), key=lambda item: item[1])
    dominance = best / max(total, 0.001)
    coverage = best / cue.duration
    if dominance >= min_dominance and coverage >= 0.25:
        return speaker, by_speaker, "majority_overlap"
    if len([v for v in by_speaker.values() if v >= 0.2]) > 1:
        return "MIXED", by_speaker, "mixed_overlap"
    return "UNKNOWN", by_speaker, "low_confidence"


def write_labeled_srt(cues: list[Cue], labels: list[tuple[str, dict, str]], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for idx, (cue, (speaker, _, _)) in enumerate(zip(cues, labels), 1):
            prefix = f"{speaker}：" if speaker not in {"UNKNOWN", "MIXED"} else f"{speaker}: "
            text = cue.text.replace("\n", " ").strip()
            f.write(f"{idx}\n{fmt_ts(cue.start)} --> {fmt_ts(cue.end)}\n{prefix}{text}\n\n")


def write_labeled_md(cues: list[Cue], labels: list[tuple[str, dict, str]], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        current = None
        for cue, (speaker, _, _) in zip(cues, labels):
            if speaker != current:
                f.write(f"\n### {fmt_mmss(cue.start)} | {speaker}\n\n")
                current = speaker
            f.write(cue.text.replace("\n", " ").strip() + "\n")


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_qc(
    path: Path,
    *,
    media_path: Path,
    srt_path: Path | None,
    turns: list[Turn],
    mapping: dict[str, dict],
    labels: list[tuple[str, dict, str]],
    output_files: list[Path],
) -> None:
    duration_by_speaker: dict[str, float] = {}
    for turn in turns:
        duration_by_speaker[turn.speaker] = duration_by_speaker.get(turn.speaker, 0.0) + turn.duration
    label_counts: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for speaker, _, reason in labels:
        label_counts[speaker] = label_counts.get(speaker, 0) + 1
        reasons[reason] = reasons.get(reason, 0) + 1

    lines = [
        "# Speaker Attribution QC",
        "",
        f"- Media: `{media_path}`",
        f"- SRT: `{srt_path}`" if srt_path else "- SRT: none",
        "",
        "## Speaker Map",
        "",
    ]
    for raw, item in sorted(mapping.items()):
        score = item.get("score")
        score_text = "n/a" if score is None else f"{score:.3f}"
        lines.append(f"- `{raw}` -> `{item['name']}` ({item['method']}, score={score_text})")
    lines.extend(["", "## Speaking Time", ""])
    for speaker, seconds in sorted(duration_by_speaker.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"- `{speaker}`: {seconds/60:.1f} min")
    lines.extend(["", "## Cue Labels", ""])
    for speaker, count in sorted(label_counts.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"- `{speaker}`: {count} cues")
    lines.extend(["", "## Label Reasons", ""])
    for reason, count in sorted(reasons.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"- `{reason}`: {count}")
    lines.extend(["", "## Outputs", ""])
    for file in output_files:
        lines.append(f"- `{file}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local speaker attribution and merge labels into SRT")
    parser.add_argument("media", help="audio/video file")
    parser.add_argument("--srt", help="existing SRT to label; defaults to <media>.final.srt")
    parser.add_argument("--process-dir", help="process directory; defaults to <media>_process")
    parser.add_argument("--speaker-ref", action="append", type=parse_ref_arg, default=[],
                        help="known speaker reference: NAME=ref.wav[,ref2.wav]")
    parser.add_argument("--assign-remaining", help="for 2-speaker audio, label the unmatched speaker with this name")
    parser.add_argument("--num-speakers", type=int, help="exact number of speakers; recommended for known 2-person interviews")
    parser.add_argument("--min-speakers", type=int)
    parser.add_argument("--max-speakers", type=int)
    parser.add_argument("--model", default=DEFAULT_DIARIZATION_MODEL, help="pyannote diarization model")
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL, help="speaker embedding model")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "mps", "cuda"],
                        help="torch device for pyannote; default auto prefers Apple MPS")
    parser.add_argument("--match-threshold", type=float, default=0.45, help="cosine threshold for known speaker match")
    parser.add_argument("--min-dominance", type=float, default=0.60, help="speaker dominance threshold per cue")
    parser.add_argument("--no-exclusive", action="store_true", help="do not prefer exclusive diarization when available")
    parser.add_argument("--reuse-audio", action="store_true", help="reuse extracted 16 kHz audio if present")
    args = parser.parse_args()

    media_path = Path(args.media).expanduser().resolve()
    if not media_path.exists():
        raise SystemExit(f"media not found: {media_path}")
    stem = episode_stem(media_path)
    process_dir = Path(args.process_dir).expanduser().resolve() if args.process_dir else default_process_dir(media_path)
    process_dir.mkdir(parents=True, exist_ok=True)

    audio_path = process_dir / f"{stem}.diarization_16k.wav"
    if not audio_path.exists() or not args.reuse_audio:
        print(f"extract audio -> {audio_path}", flush=True)
        extract_audio(media_path, audio_path)
    else:
        print(f"reuse audio -> {audio_path}", flush=True)

    print("run local pyannote diarization", flush=True)
    raw_turns = run_diarization(
        audio_path,
        model_name=args.model,
        num_speakers=args.num_speakers,
        min_speakers=args.min_speakers,
        max_speakers=args.max_speakers,
        exclusive=not args.no_exclusive,
        device_preference=args.device,
    )
    raw_rttm = process_dir / f"{stem}.diarization.rttm"
    write_rttm(raw_turns, raw_rttm, stem)

    refs = dict(args.speaker_ref)
    print("match known speaker references" if refs else "no known speaker references; keep raw labels", flush=True)
    mapping, labeled_turns = identify_speakers(
        raw_turns,
        audio_path,
        refs,
        embedding_model=args.embedding_model,
        device_preference=args.device,
        threshold=args.match_threshold,
        assign_remaining=args.assign_remaining,
        work_dir=process_dir,
    )
    turns_json = process_dir / f"{stem}.speaker_turns.json"
    map_json = process_dir / f"{stem}.speaker_map.json"
    write_json(turns_json, [turn.__dict__ for turn in labeled_turns])
    write_json(map_json, mapping)

    srt_path = Path(args.srt).expanduser().resolve() if args.srt else media_path.with_suffix(".final.srt")
    output_files = [raw_rttm, turns_json, map_json]
    labels: list[tuple[str, dict, str]] = []
    if srt_path.exists():
        cues = parse_srt(srt_path)
        labels = [label_cue(cue, labeled_turns, min_dominance=args.min_dominance) for cue in cues]
        labeled_srt = media_path.with_suffix(".speaker_labeled.srt")
        labeled_md = media_path.with_suffix(".speaker_labeled.md")
        write_labeled_srt(cues, labels, labeled_srt)
        write_labeled_md(cues, labels, labeled_md)
        output_files.extend([labeled_srt, labeled_md])
    else:
        print(f"SRT not found, skipping SRT merge: {srt_path}", file=sys.stderr)

    qc_path = process_dir / f"{stem}.speaker_qc.md"
    write_qc(
        qc_path,
        media_path=media_path,
        srt_path=srt_path if srt_path.exists() else None,
        turns=labeled_turns,
        mapping=mapping,
        labels=labels,
        output_files=output_files,
    )
    output_files.append(qc_path)

    print("speaker attribution outputs:")
    for path in output_files:
        print(f"  {path}")


if __name__ == "__main__":
    main()
