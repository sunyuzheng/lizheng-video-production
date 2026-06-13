# Local speaker references

This directory is for optional local speaker reference clips used by
`tools/speaker_attribution.py`.

Reference clips are biometric material and are ignored by git. Do not commit or
share them unless you have explicit consent from the speaker.

Create references from clean single-speaker audio:

```bash
venv-diarization/bin/python tools/build_speaker_refs.py \
  /path/to/solo-speaker-audio.m4a \
  --speaker host \
  --out-dir data/speakers/host/refs \
  --count 3 \
  --clip-seconds 10
```

Use them during speaker attribution:

```bash
venv-diarization/bin/python tools/speaker_attribution.py /path/to/video.mp4 \
  --srt /path/to/video.final.srt \
  --speaker-ref host=data/speakers/host/refs/host_ref_01_000120s.wav,data/speakers/host/refs/host_ref_02_000360s.wav \
  --assign-remaining guest \
  --num-speakers 2
```
