"""Stage 5 - music: four cues, one continuous score bed.

Each cue is generated from its caption in the contract, with `candidates`
variations. Nothing here picks the winner by ear, so the stage writes every
candidate and reads the choice from outputs/audio/music/choices.json; the first
candidate is the default, and a human can change the chooser's mind by editing
that file and re-running `make assemble`.

Without a music endpoint the stage writes a deterministic synthesised bed from
the cue's key and tempo. It is a placeholder with the right shape and the right
lengths - not a score.

Run:  python -m pipeline.music [--force]
Writes: outputs/audio/music/<cue>_candidate_<n>.wav, outputs/audio/score.wav
"""

from __future__ import annotations

import json
import os
import wave
from array import array

from . import common, providers
from .common import out_path, rel, stage_cli

# The score keeps the full band and stereo width a music model returns; the
# narration is 24 kHz mono speech, and assemble.py brings the two together.
SAMPLE_RATE = 48000
CHANNELS = 2
FADE = 1.2  # seconds of fade at each cue boundary
# Every cue is rendered FADE seconds longer than its window, so its fade-out
# plays under the next cue's fade-in: a crossfade instead of a dip to silence.
# The last cue's tail rings out under the first end card.
RENDERER = "score-v3"
# A generated cue is levelled to this, plus the cue's own gain_db, before the
# mix: with gain_db -6 the score sits 6 dB under the -18 LUFS narration even
# before the ducking pulls it further down under speech.
MUSIC_LUFS = -18.0

ROOT_NOTE = {
    "c": 130.81, "c#": 138.59, "db": 138.59, "d": 146.83, "d#": 155.56, "eb": 155.56,
    "e": 164.81, "f": 174.61, "f#": 185.00, "gb": 185.00, "g": 196.00,
    "g#": 207.65, "ab": 207.65, "a": 220.00, "a#": 233.08, "bb": 233.08, "b": 246.94,
}


def cue_windows(storyboard: dict, timing: dict) -> list[dict]:
    """A cue spans from the start of its first shot to the end of its last."""
    spans = {entry["id"]: (entry["start_s"], entry["start_s"] + entry["duration_s"]) for entry in timing["shots"]}
    order = [shot["id"] for shot in storyboard["shots"]]
    windows = []
    for cue in storyboard["music"]["cues"]:
        if cue["from_shot"] not in spans or cue["to_shot"] not in spans:
            continue
        start = spans[cue["from_shot"]][0]
        end = spans[cue["to_shot"]][1]
        if order.index(cue["to_shot"]) < order.index(cue["from_shot"]):
            common.die(f"cue {cue['id']} runs backwards in the shot order")
        windows.append({"cue": cue, "start": start, "end": end, "duration": round(end - start, 2),
                        "render": round(end - start + FADE, 2)})
    return windows


def music_source() -> str:
    """Which generator a real build would use; part of the cache key, so a
    synthesised fallback is replaced once a music model is configured."""
    common.load_env()
    if os.environ.get("MUSIC_ENDPOINT", "").strip():
        return "ace-step"
    if providers.openrouter_key_present():
        return "lyria"
    return "stable-audio" if providers.hf_token_present() else "none"


def synth_cue(target, *, duration: float, bpm: int, key: str, gain_db: float) -> None:
    """A slow pentatonic drone with a pulse, shaped to the cue's tempo.

    Deliberately plain: it exists so the film has something under the voice in
    a keyless build, and so the ducking can be tested for real.
    """
    root_name = key.split()[0].lower() if key else "c"
    root = ROOT_NOTE.get(root_name, 130.81)
    beat = 60.0 / max(40, bpm)
    common.ff(
        "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono:d={duration}",
        "-t", duration,
        "-f", "lavfi", "-i", f"sine=frequency={root}:sample_rate={SAMPLE_RATE}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency={root * 1.5:.2f}:sample_rate={SAMPLE_RATE}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency={root * 2:.2f}:sample_rate={SAMPLE_RATE}:duration={duration}",
        "-filter_complex",
        "[1][2][3]amix=inputs=3:normalize=1,"
        f"tremolo=f={1 / beat:.3f}:d=0.35,"
        "lowpass=f=1600,aecho=0.7:0.5:180|360:0.35|0.18,"
        f"volume={gain_db}dB,"
        f"afade=t=in:d={FADE},afade=t=out:st={max(0.0, duration - FADE):.3f}:d={FADE}[bed]",
        "-map", "[bed]", "-ac", CHANNELS, "-ar", SAMPLE_RATE, "-c:a", "pcm_s16le", target,
    )


def run(storyboard: dict, args, only) -> None:
    timing_path = out_path("audio", "timing.json")
    if not timing_path.exists():
        common.die("outputs/audio/timing.json is missing. Run `make narration` first.")
    timing = json.loads(timing_path.read_text())
    windows = cue_windows(storyboard, timing)
    mode = common.provider_mode()
    music_dir = out_path("audio", "music", ".keep").parent
    choices_path = music_dir / "choices.json"

    chosen: dict[str, str] = {}
    if choices_path.exists():
        chosen = json.loads(choices_path.read_text())
    if args.dry_run:
        for window in windows:
            print(f"  {window['cue']['id']}: {window['duration']:.1f}s from "
                  f"{window['cue']['from_shot']} to {window['cue']['to_shot']}")
        return

    for window in windows:
        cue = window["cue"]
        if only and cue["id"] not in only:
            continue
        # MUSIC_CANDIDATES overrides the storyboard for a build nobody will
        # audition, where extra takes are money spent on files never heard.
        candidates = int(os.environ.get("MUSIC_CANDIDATES") or cue.get("candidates", 3))
        for index in range(1, candidates + 1):
            target = music_dir / f"{cue['id']}_candidate_{index}.wav"
            key = common.artifact_key("music", cue["id"], cue["caption"], window["render"],
                                      cue.get("bpm"), cue.get("key"), index, mode, RENDERER,
                                      music_source() if mode == "real" else None)
            if common.is_fresh(target, key) and not args.force:
                print(f"  {cue['id']} #{index}: cached")
                continue
            fell_back = False
            print(f"  {cue['id']} #{index}: {window['duration']:.1f}s, {cue.get('key')} at {cue.get('bpm')} bpm")
            if mode == "real":
                try:
                    common.load_env()
                    used = music_source()
                    if used == "lyria":
                        raw, cost = providers.openrouter_music(
                            prompt=f"{cue['caption']}, {cue.get('bpm', 80)} bpm, {cue.get('key', 'C minor')}",
                            seconds=window["render"], seed=1000 + index,
                        )
                    elif used != "stable-audio":
                        used = "ace-step"
                        raw, cost = providers.ace_step(
                            caption=cue["caption"], seconds=window["render"],
                            bpm=cue.get("bpm", 80), key=cue.get("key", "C minor"), seed=1000 + index,
                        )
                    else:
                        # Stable Audio has no tempo or key fields, so they go
                        # into the words.
                        raw, cost = providers.hf_audio(
                            prompt=f"{cue['caption']}, {cue.get('bpm', 80)} bpm, {cue.get('key', 'C minor')}",
                            seconds=window["render"], seed=1000 + index,
                            negative="vocals, singing, speech, low quality, distortion",
                        )
                    staged = music_dir / f"{cue['id']}_candidate_{index}.raw"
                    staged.write_bytes(raw)
                    level = common.gain_to_loudness(staged, MUSIC_LUFS + cue.get("gain_db", -6))
                    common.ff("-i", staged, "-af", f"volume={level:.2f}dB,afade=t=in:d={FADE},"
                                                   f"afade=t=out:st={max(0.0, window['render'] - FADE):.3f}:d={FADE}",
                              "-t", window["render"], "-ac", CHANNELS, "-ar", SAMPLE_RATE,
                              "-c:a", "pcm_s16le", target)
                    staged.unlink(missing_ok=True)
                    common.log_run(stage="music", operation="music",
                                   provider={"stable-audio": "huggingface", "lyria": "openrouter"}.get(used, "ace-step"),
                                   model=used,
                                   status="ok", cue_id=cue["id"], candidate=index,
                                   cost_usd_estimate=round(cost, 4))
                except providers.ProviderError as exc:
                    print(f"    ! {exc}; writing the synthesised fallback instead")
                    fell_back = True
                    common.log_run(stage="music", operation="music", provider="none", model="none",
                                   status="fallback", cue_id=cue["id"], candidate=index, note=str(exc))
                    synth_cue(target, duration=window["render"], bpm=cue.get("bpm", 80),
                              key=cue.get("key", "C minor"), gain_db=cue.get("gain_db", -6))
            else:
                synth_cue(target, duration=window["render"], bpm=cue.get("bpm", 80),
                          key=cue.get("key", "C minor"), gain_db=cue.get("gain_db", -6))
                common.log_run(stage="music", operation="music", provider="mock", model="synth",
                               status="ok", cue_id=cue["id"], candidate=index)
            # A fallback written because a configured model failed is not
            # cached, so the next run tries the model again.
            if not (fell_back and music_source() != "none"):
                common.mark_fresh(target, key)

    # Assemble the bed: cues are summed onto the timeline, each one's fade-out
    # overlapping the next one's fade-in, so a scene change is a crossfade
    # rather than a splice or a hole.
    total = timing["total_s"]
    buffer = array("h", bytes(int(round(total * SAMPLE_RATE)) * CHANNELS * 2))
    for window in windows:
        cue = window["cue"]
        pick = chosen.get(cue["id"], f"{cue['id']}_candidate_1.wav")
        path = music_dir / pick
        if not path.exists():
            print(f"  ! {cue['id']}: {pick} does not exist, skipping the cue")
            continue
        with wave.open(str(path), "rb") as handle:
            if (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) != (CHANNELS, 2, SAMPLE_RATE):
                common.die(f"{rel(path)} is not {SAMPLE_RATE} Hz stereo 16-bit; re-run `make music --force`")
            pcm = array("h", handle.readframes(handle.getnframes()))
        offset = int(round(window["start"] * SAMPLE_RATE)) * CHANNELS
        end = min(len(buffer), offset + len(pcm))
        for position in range(offset, end):
            buffer[position] = max(-32768, min(32767, buffer[position] + pcm[position - offset]))

    score = out_path("audio", "score.wav")
    with wave.open(str(score), "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(buffer.tobytes())

    if not choices_path.exists():
        choices_path.write_text(json.dumps(
            {c["id"]: f"{c['id']}_candidate_1.wav" for c in storyboard["music"]["cues"]}, indent=2) + "\n")
    print(f"  wrote {rel(score)} ({total:.1f}s) and {rel(choices_path)}")


if __name__ == "__main__":
    stage_cli("music", run, description="Generate and place the four music cues")
