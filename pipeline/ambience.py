"""Stage 5b - ambience: the sound of each place, under the score.

Every shot in the contract carries an `ambience` line (wind in grass, a kebero
drum through a wall, city traffic). This stage generates one clip per shot from
that line and lays them on the timeline with short crossfades, so a painted
still has air around it and a cut between scenes is heard as well as seen.

It is an optional layer. Without a Hugging Face token the stage writes nothing
in a real build and the mix is score plus voice, exactly as before. In a mock
build it writes quiet pink noise per shot, so the mix path is still exercised.

Run:  python -m pipeline.ambience [--only A1] [--force]
Writes: outputs/audio/ambience/<shot>.wav, outputs/audio/ambience.wav
"""

from __future__ import annotations

import json
import wave
from array import array

from . import common, providers
from .common import out_path, rel, stage_cli

SAMPLE_RATE = 48000
CHANNELS = 2
FADE = 0.4           # crossfade between neighbouring shots, seconds
AMBIENCE_LUFS = -32  # 14 dB under the narration: felt, not listened to
RENDERER = "ambience-v1"
NEGATIVE = "music, melody, singing, speech, narration, low quality, distortion"


def run(storyboard: dict, args, only) -> None:
    timing_path = out_path("audio", "timing.json")
    if not timing_path.exists():
        common.die("outputs/audio/timing.json is missing. Run `make narration` first.")
    timing = json.loads(timing_path.read_text())
    spans = {entry["id"]: entry for entry in timing["shots"]}
    mode = common.provider_mode()
    clip_dir = out_path("audio", "ambience", ".keep").parent
    bed = out_path("audio", "ambience.wav")

    if mode == "real" and not providers.hf_token_present():
        print("  no HF_TOKEN, so no ambience layer; the mix is score and voice")
        common.log_run(stage="ambience", operation="ambience", provider="none", model="none",
                       status="skipped", note="HF_TOKEN is not set")
        bed.unlink(missing_ok=True)
        return

    placed = []
    for shot in storyboard["shots"]:
        text = (shot.get("ambience") or "").strip()
        entry = spans.get(shot["id"])
        if not text or not entry:
            continue
        target = clip_dir / f"{shot['id']}.wav"
        length = round(entry["duration_s"] + FADE, 2)
        placed.append((entry["start_s"], target))
        if only and shot["id"] not in only:
            continue
        seed = shot["keyframe"].get("seed", 1)
        key = common.artifact_key("ambience", shot["id"], text, length, seed, mode, RENDERER)
        if common.is_fresh(target, key) and not args.force:
            print(f"  {shot['id']}: cached")
            continue
        if args.dry_run:
            print(f"  {shot['id']}: would generate {length:.1f}s of: {text}")
            continue
        print(f"  {shot['id']}: {length:.1f}s of {text}")
        fades = f"afade=t=in:d={FADE},afade=t=out:st={max(0.0, length - FADE):.3f}:d={FADE}"
        if mode == "mock":
            common.ff("-f", "lavfi", "-i",
                      f"anoisesrc=color=pink:sample_rate={SAMPLE_RATE}:duration={length}:seed={seed}",
                      "-af", f"volume=-34dB,{fades}", "-ac", CHANNELS, "-ar", SAMPLE_RATE,
                      "-c:a", "pcm_s16le", target)
            common.log_run(stage="ambience", operation="ambience", provider="mock", model="noise",
                           status="ok", shot_id=shot["id"])
        else:
            try:
                raw, cost = providers.hf_audio(
                    prompt=f"field recording, ambient sound, {text}", seconds=length, seed=seed,
                    negative=NEGATIVE,
                )
            except providers.ProviderError as exc:
                # One missing clip is a quiet stretch, not a failed film.
                print(f"    ! {exc}; this shot has no ambience")
                common.log_run(stage="ambience", operation="ambience", provider="huggingface",
                               model="stable-audio", status="failed", shot_id=shot["id"], note=str(exc)[:200])
                target.unlink(missing_ok=True)
                continue
            staged = clip_dir / f"{shot['id']}.raw"
            staged.write_bytes(raw)
            try:
                level = common.gain_to_loudness(staged, AMBIENCE_LUFS)
                common.ff("-i", staged, "-t", length, "-af", f"volume={level:.2f}dB,{fades}",
                          "-ac", CHANNELS, "-ar", SAMPLE_RATE, "-c:a", "pcm_s16le", target)
            finally:
                staged.unlink(missing_ok=True)
            common.log_run(stage="ambience", operation="ambience", provider="huggingface",
                           model="stable-audio", status="ok", shot_id=shot["id"], seed=seed,
                           cost_usd_estimate=round(cost, 4))
        common.mark_fresh(target, key)

    if args.dry_run:
        return

    total = timing["total_s"]
    buffer = array("h", bytes(int(round(total * SAMPLE_RATE)) * CHANNELS * 2))
    used = 0
    for start, path in placed:
        if not path.exists():
            continue
        with wave.open(str(path), "rb") as handle:
            pcm = array("h", handle.readframes(handle.getnframes()))
        offset = int(round(start * SAMPLE_RATE)) * CHANNELS
        end = min(len(buffer), offset + len(pcm))
        for position in range(offset, end):
            buffer[position] = max(-32768, min(32767, buffer[position] + pcm[position - offset]))
        used += 1
    if not used:
        bed.unlink(missing_ok=True)
        print("  no ambience clips, so no ambience layer")
        return
    with wave.open(str(bed), "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(buffer.tobytes())
    print(f"  wrote {rel(bed)} from {used} clips")


if __name__ == "__main__":
    stage_cli("ambience", run, description="Generate per-shot ambience and lay it on the timeline")
