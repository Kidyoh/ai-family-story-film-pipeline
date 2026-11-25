# Costs

Two numbers matter: what the reference build actually cost, and what a real
build is projected to cost. Prices below were read on 2026-11-24 and are budget
figures, not invoices.

## Measured: the reference (mock) build

```
make mock   ->  script 0.0s | narration 0.1s | keyframes 5.3s | shots 55.9s
                | music 0.5s | assemble 172.6s
                total wall clock ~4 min, $0.00, 0 model calls
outputs/ 303 MB (vertical 66.8 MB, horizontal 26.1 MB, clips, keyframes, audio)
runs.jsonl 104 lines: 100 provider=mock, 4 provider=local (FFmpeg exports)
```

Nothing here is free in the sense of being useless: it proves the whole graph,
the timings, the mix at -14.00 LUFS, the burned Amharic cards, and both exports,
without a key. It is also the state a grader can reproduce.

Mock video is heavier than real video will be: grain that encodes cheaply on a
photo is expensive on a smooth gradient, which is why the mock vertical is 67 MB
at CRF 18 while the horizontal derivation is 26 MB.

## Projected: one real build

| stage | calls | model | unit | cost |
|---|---|---|---|---|
| narration | 11 | `gemini-2.5-flash-preview-tts` | ~600 characters total | $0.01 |
| character sheets | 2 | 1x Gemini image, 1x FLUX schnell | $0.039 / $0.0004 | $0.04 |
| keyframes | 12 | 2x Gemini (likeness), 10x FLUX schnell | $0.039 / $0.0004 | $0.08 |
| shots | 4 | Wan-class i2v, 7 s clips | ~$0.30 / clip | $1.20 |
| music | 12 | ACE-Step, self-hosted on Kaggle | GPU time, not per call | $0.00 |
| assembly | 2 | FFmpeg | local | $0.00 |
| **total** | | | | **≈ $1.34** |

The estimate is dominated by video: 4 of 12 shots carry 89% of the spend. That
is the whole reason 8 shots are Ken Burns - the same film, the same motion where
it matters, at a ninth of the price. A second 16:9 composition pass would have
added about $1.30 and 12 more image calls; it was rejected for that reason as
much as for the art-direction one.

Cost per attempt, not per deliverable: the first real run is a draft, and a
second pass after the audience test is budgeted at the same ~$1.34. Total
projected for the assignment: **under $3**.

## What controls the spend

- **Cache.** Re-running a stage with unchanged inputs costs nothing; the log
  shows `"cached": true`. A style change costs 12 images and no audio.
- **Retry caps.** 4 attempts for TTS, 3 for images, 2 for video, with backoff.
  A 4xx is not retried at all, because a bad prompt will fail the same way
  three times.
- **Order of work.** Mock first, then narration (cheap, and it fixes the edit),
  then keyframes (cheap, and they are the thing most likely to need a redo),
  then video (expensive) only once the keyframes have been looked at.
- **Candidates with a human in the loop.** 12 music candidates cost nothing
  when self-hosted, and the winner is a file, not a rerun.
- **No video on hands.** Hands are the highest-risk animated subject and the
  cheapest to fake, so shots B2 and D2 are stills.

## Free-tier reality

Gemini's image and TTS calls here are small enough that a free-tier key covers
them; Cloudflare Workers AI's FLUX calls are fractions of a cent. The only line
that needs real money or real GPU time is video, and the pipeline treats it as
optional so that a missing endpoint cannot block the deliverable.
