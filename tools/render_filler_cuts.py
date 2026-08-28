#!/usr/bin/env python3
"""Render a reviewed, non-destructive filler/false-start edit plan.

Detection and rendering stay separate: this script applies only entries whose
``decision`` is explicitly ``"cut"``. It never overwrites the source video.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.subtitle_qc import parse_srt
from tools.atomic_delivery import commit_prepared_files


@dataclass(frozen=True)
class Cut:
    start: float
    end: float
    label: str
    reason: str

    @property
    def duration(self) -> float:
        return self.end - self.start


def load_cuts(plan_path: Path) -> list[Cut]:
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cuts", []), list):
        raise ValueError("edit plan must be an object with a cuts array")

    cuts: list[Cut] = []
    for item in payload.get("cuts", []):
        if not isinstance(item, dict):
            raise ValueError(f"cut entry must be an object: {item!r}")
        # Fail closed: an omitted decision is a candidate, not approval to edit.
        if item.get("decision") != "cut":
            continue
        start = float(item["start"])
        end = float(item["end"])
        if start < 0 or end <= start:
            raise ValueError(f"invalid cut interval: {item}")
        cuts.append(
            Cut(
                start=start,
                end=end,
                label=str(item.get("label", "")),
                reason=str(item.get("reason", "")),
            )
        )

    cuts.sort(key=lambda cut: (cut.start, cut.end))
    reviewed: list[Cut] = []
    for cut in cuts:
        if reviewed and cut.start < reviewed[-1].end - 1e-6:
            raise ValueError(f"overlapping reviewed cuts: {reviewed[-1]} and {cut}")
        reviewed.append(cut)
    return reviewed


def validate_cuts(cuts: list[Cut], duration: float) -> None:
    if duration <= 0:
        raise ValueError(f"invalid media duration: {duration}")
    if any(cut.end > duration + 0.01 for cut in cuts):
        offending = next(cut for cut in cuts if cut.end > duration + 0.01)
        raise ValueError(f"cut exceeds media duration {duration:.3f}s: {offending}")
    removed = sum(cut.duration for cut in cuts)
    if duration - removed < 0.1:
        raise ValueError("reviewed cuts would remove the entire video")


def format_timestamp(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def removed_before(timestamp: float, cuts: list[Cut]) -> float:
    removed = 0.0
    for cut in cuts:
        if timestamp <= cut.start:
            break
        removed += max(0.0, min(timestamp, cut.end) - cut.start)
    return removed


def retime_srt(source: Path, destination: Path, cuts: list[Cut]) -> None:
    """Retime cues after cuts; this does not rewrite words inside partial cues."""
    if source.resolve() == destination.resolve():
        raise ValueError("retimed subtitle output must not overwrite its source")
    output: list[str] = []
    for cue in parse_srt(source):
        start = cue["start"]
        end = cue["end"]
        new_start = start - removed_before(start, cuts)
        new_end = end - removed_before(end, cuts)
        if new_end - new_start < 0.08:
            continue
        output.append(
            f"{len(output) + 1}\n"
            f"{format_timestamp(new_start)} --> {format_timestamp(new_end)}\n"
            + cue["text"]
        )
    if not output:
        raise ValueError("all subtitle cues were removed by the edit plan")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n\n".join(output) + "\n", encoding="utf-8")


def default_clean_srt_candidate(output_video: Path) -> Path:
    """A retimed subtitle is provisional until text review and subtitle QC."""
    return output_video.with_suffix(".candidate.srt")


def validate_artifact_paths(paths: dict[str, Path | None]) -> dict[str, Path]:
    """Reject any input/output alias before rendering or retiming begins."""
    resolved = {
        role: path.resolve()
        for role, path in paths.items()
        if path is not None
    }
    seen: dict[Path, str] = {}
    for role, path in resolved.items():
        if path in seen:
            raise ValueError(
                f"artifact paths must be distinct: {seen[path]} and {role} both use {path}"
            )
        seen[path] = role
    return resolved


def _prepared_neighbor(target: Path, label: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{target.stem}.{label}.",
        suffix=target.suffix,
        dir=target.parent,
        delete=False,
    ) as handle:
        path = Path(handle.name)
    path.unlink(missing_ok=True)
    return path


def select_filter(cuts: list[Cut]) -> str:
    intervals = "+".join(
        f"between(t\\,{cut.start:.6f}\\,{cut.end:.6f})" for cut in cuts
    )
    return f"select=not({intervals}),setpts=N/FRAME_RATE/TB"


def probe_duration(media: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is not available")
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def build_clean_audio(
    video: Path, cuts: list[Cut], workdir: Path, duration: float
) -> Path:
    """Create sample-accurate compacted PCM audio from kept ranges."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not available")

    ranges: list[tuple[float, float]] = []
    cursor = 0.0
    for cut in cuts:
        if cut.start > cursor + 0.005:
            ranges.append((cursor, cut.start))
        cursor = max(cursor, cut.end)
    if duration > cursor + 0.005:
        ranges.append((cursor, duration))
    if not ranges:
        raise ValueError("edit plan leaves no audio range")

    clean_audio = workdir / "clean.wav"
    if len(ranges) == 1:
        start, end = ranges[0]
        filter_graph = (
            f"[0:a]atrim=start={start:.6f}:end={end:.6f},"
            "asetpts=PTS-STARTPTS[out]"
        )
    else:
        split_outputs = "".join(f"[a{index}]" for index in range(len(ranges)))
        graph_parts = [f"[0:a]asplit={len(ranges)}{split_outputs}"]
        for index, (start, end) in enumerate(ranges):
            graph_parts.append(
                f"[a{index}]atrim=start={start:.6f}:end={end:.6f},"
                f"asetpts=PTS-STARTPTS[s{index}]"
            )
        concat_inputs = "".join(f"[s{index}]" for index in range(len(ranges)))
        graph_parts.append(f"{concat_inputs}concat=n={len(ranges)}:v=0:a=1[out]")
        filter_graph = ";".join(graph_parts)

    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-filter_complex",
            filter_graph,
            "-map",
            "[out]",
            "-c:a",
            "pcm_s16le",
            str(clean_audio),
        ],
        check=True,
    )
    return clean_audio


def render(video: Path, output: Path, cuts: list[Cut], bitrate: str) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not available")
    if video.resolve() == output.resolve():
        raise ValueError("output must not overwrite the source video")
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(video)
    validate_cuts(cuts, duration)
    suffix = output.suffix or ".mp4"
    rendering_output = output.with_name(f".{output.stem}.rendering{suffix}")
    rendering_output.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="kdb-filler-render-") as tmp:
        clean_audio = build_clean_audio(video, cuts, Path(tmp), duration)
        command = [
            ffmpeg,
            "-hide_banner",
            "-y",
            "-i",
            str(video),
            "-i",
            str(clean_audio),
            "-filter_complex",
            f"[0:v]{select_filter(cuts)}[v]",
            "-map",
            "[v]",
            "-map",
            "1:a:0",
            "-c:v",
            "h264_videotoolbox",
            "-b:v",
            bitrate,
            "-pix_fmt",
            "yuv420p",
            "-tag:v",
            "avc1",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(rendering_output),
        ]
        try:
            subprocess.run(command, check=True)
            rendering_output.replace(output)
        finally:
            rendering_output.unlink(missing_ok=True)


def render_with_subtitle_bundle(
    video: Path,
    output: Path,
    cuts: list[Cut],
    bitrate: str,
    srt_in: Path,
    srt_out: Path,
) -> None:
    """Prepare video and retimed SRT first, then commit them as one bundle."""
    prepared_video = _prepared_neighbor(output, "bundle-candidate")
    prepared_srt = _prepared_neighbor(srt_out, "bundle-candidate")
    try:
        # Parse and retime before invoking ffmpeg, so malformed subtitles cannot
        # replace a known-good clean video.
        retime_srt(srt_in, prepared_srt, cuts)
        render(video, prepared_video, cuts, bitrate)
        commit_prepared_files(
            [(prepared_video, output), (prepared_srt, srt_out)]
        )
    finally:
        prepared_video.unlink(missing_ok=True)
        prepared_srt.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render reviewed filler-word cuts")
    parser.add_argument("video", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--srt-in", type=Path)
    parser.add_argument("--srt-out", type=Path)
    parser.add_argument("--bitrate", default="22M")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.srt_out and not args.srt_in:
        parser.error("--srt-out requires --srt-in")

    video = args.video.resolve()
    if not video.exists():
        raise SystemExit(f"Video not found: {video}")
    plan = args.plan.resolve()
    output = (args.output or video.with_name(f"{video.stem}.clean.mp4")).resolve()
    srt_in = args.srt_in.resolve() if args.srt_in else None
    srt_out = (
        (args.srt_out or default_clean_srt_candidate(output)).resolve()
        if srt_in
        else None
    )
    try:
        validate_artifact_paths(
            {
                "source_video": video,
                "edit_plan": plan,
                "output_video": output,
                "source_srt": srt_in,
                "output_srt": srt_out,
            }
        )
    except ValueError as error:
        parser.error(str(error))

    cuts = load_cuts(plan)
    if not cuts:
        raise SystemExit("No explicitly reviewed cuts found in plan")
    duration = probe_duration(video)
    validate_cuts(cuts, duration)
    total = sum(cut.duration for cut in cuts)
    print(f"Reviewed cuts: {len(cuts)}")
    print(f"Removed duration: {total:.3f}s")
    for cut in cuts:
        print(f"  {cut.start:8.3f}-{cut.end:8.3f}  {cut.label}  {cut.reason}")
    if args.dry_run:
        return

    if srt_in and srt_out:
        render_with_subtitle_bundle(
            video, output, cuts, args.bitrate, srt_in, srt_out
        )
        print(f"Video: {output}")
        print(f"Subtitle candidate (review + QC required): {srt_out}")
    else:
        render(video, output, cuts, args.bitrate)
        print(f"Video: {output}")


if __name__ == "__main__":
    main()
