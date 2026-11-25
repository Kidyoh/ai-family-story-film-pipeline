# Architecture

Six stages, one contract, one log. The point of the shape is that a mistake in
an earlier stage cannot silently become a different film: every stage reads the
same validated file and writes artifacts that name the inputs they came from.

```
storyboard.json ──validate──┐
                            ├─ script ──► outputs/plan.json, script.md
                            ├─ narration ──► per-shot WAV + timing.json + radio_cut.wav
                            │        └──────────────┐
                            ├─ keyframes ──► PNGs   │ (measured durations)
                            │        └──► shots ─────┤
                            ├─ music ──► score.wav ──┤
                            │                        ▼
                            └────────────► assemble ──► vertical.mp4, horizontal.mp4
```

## 1. Contract first

`storyboard.json` is the only creative input. It is validated against
`schema/storyboard.schema.json` (Draft 2020-12, `additionalProperties: false`
everywhere) before any stage starts, then checked for three things a schema
cannot express:

- every music cue's `from_shot` / `to_shot` is a real shot, in the right order
- every character id used by a shot exists
- every narration voice is declared in `voices`

A stage that fails validation exits before spending a model call. `make validate`
runs the same path and prints the plan without generating anything.

## 2. Narration decides the edit

The obvious order is picture first, voice to fit. That is wrong here, because
Amharic line length is the one thing I could not predict: 83 words came out at
42.1 s of speech in the first measured run.

So `narration.py` runs first and writes `outputs/audio/timing.json`:

```
shot_duration = max(storyboard.duration_s, speech + lead_in(0.5) + tail(0.8))
```

If the sum pushes the film past 90 s, slack is trimmed from the shots that have
it, last shot first, never below `speech + pads`. When the film cannot fit, the
stage says so instead of quietly shipping a 94-second cut. `shots.py` and
`music.py` read the same timing file, so picture, voice and score cannot drift.

## 3. Caching

Each artifact gets a sidecar `<artifact>.key` containing a SHA-256 of its
semantic inputs, not of the clock or the file's mtime:

| artifact | key includes |
|---|---|
| narration | shot id, Amharic text, voice id, style instruction, model, mode |
| keyframe | assembled prompt, negative prompt, seed, reference photo hash, mode |
| character sheet | description, style block, reference photo hash |
| clip | keyframe hash, duration, mode, Ken Burns params, motion text |
| cue candidate | caption, duration, bpm, key, candidate index, mode |

Changing the style block invalidates all 12 keyframes and nothing else.
Changing one Amharic line re-renders one WAV and the shots that follow it.
`--force` ignores the cache for a stage; `--only A1,B1` narrows to ids.

## 4. The log

`runs.jsonl`, one line per model call:

```json
{"ts": "2026-11-25T09:14:02+00:00", "stage": "narration", "operation": "tts",
 "provider": "real", "model": "gemini-2.5-flash-preview-tts", "status": "ok",
 "cached": false, "seconds": 4.2, "shot_id": "A2", "voice": "Kore",
 "cost_usd_estimate": 0.0013}
```

Written with a lock, appended, never rewritten. It records cache hits too
(`"cached": true`), which is what makes the cost table in `COSTS.md` auditable.
Secrets never enter it: no URLs, no prompts' reference bytes, no absolute paths.

## 5. Failure policy

- **Retries**: 4 attempts for TTS, 3 for images and music, 2 for video.
  Exponential backoff with jitter. 4xx (except 429) is raised immediately - a
  bad prompt is not a transient failure.
- **Isolation**: one shot failing does not abort the stage. The artifact is
  simply absent, and `assemble.py` names the missing file and the stage to
  re-run. Re-running `make film` picks up where it stopped.
- **Degradation, once, out loud**: if an optional capability is missing, the
  stage prints why and logs `status: "skipped"` or `"fallback"`, then does the
  deterministic thing. It does not retry the same missing endpoint per shot.

## 6. Endpoint contracts

Only implemented once each, in `pipeline/providers.py`.

- **Gemini TTS**: `generateContent` with `responseModalities: ["AUDIO"]` and
  `speechConfig.prebuiltVoiceConfig.voiceName`. Returns headerless 16-bit PCM at
  24 kHz, which `pcm_to_wav` wraps without re-encoding.
- **Gemini image**: same endpoint with `["IMAGE"]`; the family photograph goes in
  as an `inline_data` part with an instruction to repaint, not to copy.
- **FLUX (Workers AI)**: `POST /accounts/{id}/ai/run/@cf/black-forest-labs/flux-1-schnell`,
  at 768x1344 (4:7, cropped to 9:16 in `keyframes.py`). Handles both a raw image
  response and the base64-in-JSON variant.
- **Video**: either `CF_VIDEO_MODEL` (a Cloudflare catalog video model) or
  `WAN_ENDPOINT`, a self-hosted Wan 2.2 i2v server. The self-hosted contract is
  multipart: `image` file, form fields `prompt`, `seconds`, `seed`, mp4 back.
- **Music**: `MUSIC_ENDPOINT`, JSON POST `{caption, lyrics, duration, bpm, key,
  seed}`, audio bytes or `{"audio": "<base64>"}` back.

Video and music are deliberately not hard dependencies: the assignment grades
the pipeline and the film, and a 3-day build must not be blocked by a GPU queue.

## 7. One master, two exports

`shots.py` renders 1080x1920 only. The horizontal export is derived in
`assemble.py`:

```
background = master scaled to 1920x1080, cropped, gblur=45, dimmed, desaturated
foreground = master scaled to 1080 tall, centred
```

Composing every shot for two framings would have doubled image and video spend
for a second deliverable that is a derivative, not a second film. The
compromise is visible in the horizontal cut and is a deliberate trade - the
vertical frame is the one that was designed. `DESIGN.md` argues it.

## 8. Audio chain

```
score.wav ─┐
           ├─ sidechaincompress(thr .02, ratio 6, atk 300 ms, rel 1500 ms)
radio_cut ─┴─► amix(normalize=0) ─► loudnorm 2-pass (-14 LUFS, -1 dBTP) ─► mix.wav
```

The ducking parameters are in the contract, not in the code, so retuning the
mix means editing `music.ducking` and re-running `make assemble` (about three
minutes, no model calls). Operas are not applied twice: the mix is measured,
then normalised linearly, so the ducking curve survives.

## 9. What is not in the pipeline

- **Ambience** is described per shot in the contract and is not generated. It is
  the one place where the storyboard is ahead of the implementation; see
  `FAILURES.md`. The mix uses score + voice only.
- **Candidate selection** for music is a file (`outputs/audio/music/choices.json`),
  not a model. The stage writes three candidates per cue; a human picks.
- **The photograph** is never read by `script.py`, `shots.py`, `music.py` or
  `assemble.py`. Only `keyframes.py` touches it, and only as a reference.
