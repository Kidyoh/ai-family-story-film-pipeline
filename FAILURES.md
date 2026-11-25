# Failures

What broke while building this, why, and what changed because of it. Kept
because the fixes are the interesting part, and because a pipeline that claims
to have had no failures is a pipeline nobody ran.

## 1. FFmpeg without `drawtext`

**Symptom:** `make mock` died in `keyframes` with exit 234 and
`Error opening output file .../girl_sheet.png`.

**Cause:** the static FFmpeg 7.0.2 in `.tools/` is built without freetype, so
the `drawtext` filter does not exist. `common._find` preferred `.tools/` over the
imageio-ffmpeg binary, which does have freetype.

**Fix:** the resolver now prefers a build with text and libass (`$FFMPEG`, then
imageio-ffmpeg, then `.tools`, then `PATH`), and `mock_image` checks
`has_filter("drawtext")` before using it. Text on screen moved to libass/ASS
entirely, which is better for Amharic shaping anyway.

## 2. A lavfi source used as a filter

**Symptom:** the same run failed with `Simple filtergraph was expected to have
exactly 1 input and 1 output. However, it had 0 input(s)`.

**Cause:** `gradients` is a filter *source*. It was written into `-vf` while a
dummy `color=` input was supplied, so the graph had an input nothing consumed.

**Fix:** `gradients` is now the `-i` source and the rest of the chain is `-vf`.
Obvious in hindsight; two minutes of reading the filter list would have avoided
it, and now `has_filter` is a first-class helper.

## 3. The first assembly was 90.5 seconds

**Symptom:** `! the film is 90.5s, over the 90s ceiling` on the first full run.

**Cause:** narration padding (0.6 s lead + 0.9 s tail) was added to every
narrated shot and never reconciled with the brief.

**Fix:** pads trimmed to 0.5 / 0.8, and `narration.py` now trims slack from shots
that have it - last shot first, never below `speech + pads` - until the film fits.
The build reports each trim. If it still cannot fit, it says so and ships a
warning instead of silently exceeding the ceiling.

## 4. Every mock frame looked like scene A

**Symptom:** B4, a cold slate-blue valley, rendered warm orange.

**Cause:** `generate()` in `keyframes.py` called `mock_image(..., scene="A")`
while the shot's scene was available but not passed through.

**Fix:** `scene` is a parameter threaded from the shot. Found by extracting a
frame and looking at it, which is the argument for looking at output rather than
trusting a green log.

## 5. No Wan model on Workers AI

**Symptom:** the plan assumed Wan 2.2 i2v was available with the Cloudflare
token that generates images. It is not: Workers AI hosts FLUX for images, while
the Wan models in Cloudflare's catalog are third-party entries with their own
access.

**Fix:** two adapters with one contract - `CF_VIDEO_MODEL` (any catalog video
model id) or a self-hosted `WAN_ENDPOINT` (Wan 2.2 i2v on Kaggle or a rented
GPU). Without either, `shots.py` animates the keyframes with Ken Burns and logs
`status: "fallback"` for the four `wan` shots.

**Still open:** the real video path has not been executed end to end, because
there is no endpoint in this environment. It is the one part of the pipeline I
would not claim works until it runs. The mock and fallback paths are verified.

## 6. ACE-Step has no hosted API

**Symptom:** the plan said "ACE-Step, 3 candidates per cue", and there is nothing
to POST to.

**Fix:** same pattern as video: `MUSIC_ENDPOINT` points at an ACE-Step inference
server, contract documented in `ARCHITECTURE.md`. Without it, `music.py` writes a
deterministic synthesised bed from each cue's key, tempo and gain - correct shape,
correct lengths, honest placeholder - and logs the fallback.

## 7. Ambience is written but not generated

**Symptom:** every shot has an `ambience` string (`wind in grass`, `a horn`, `room
tone`), and nothing consumes it.

**Cause:** scope. A fourth generative capability (sound effects) would have added
an endpoint, a provider, a cache key and a mix layer in a three-day build, for
texture the score already covers.

**Decision:** the strings stay in the contract as notes for the next take, and
the gap is stated in `ARCHITECTURE.md` rather than dressed up. The mix is score
plus voice, and that is what the loudness numbers describe.

## 8. Disk

**Symptom:** 4.3 GB free on the build machine, 99% full, with real video about to
be generated.

**Fix:** nothing heavy runs locally. Wan output is conformed and the raw file is
deleted immediately (`clips/<shot>.raw.mp4`); the mock render is the largest
local artifact at 303 MB for the whole `outputs/`. Real video generation belongs
on Kaggle where the GPU and the disk are.

## 9. The speech-rate guess is still a guess

**Symptom:** `script.py` projects narration length at 2.2 words per second, and
the only audio that exists yet is the mock tone, whose length is generated from
that same guess.

**Cause:** no `GEMINI_API_KEY` in this environment, so the real Amharic rate has
never been measured.

**Fix (pending):** the first real `make narration` replaces the estimate with
measured audio in `timing.json`; `script.py`'s number only drives warnings, never
the edit. If the real rate is far from 2.2, the 90 s ceiling logic will trim
shots and print exactly how much.
