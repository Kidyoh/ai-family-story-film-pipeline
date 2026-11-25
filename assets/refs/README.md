# Reference material

Real, human-owned source images live here. Nothing in this folder is written by the pipeline;
it is input only.

## The one photograph

Put the family photograph of my grandmother here, at exactly this path:

```
assets/refs/grandmother_source.png
```

The path is declared in `storyboard.json` under `characters[id=grandmother].source_photo`, so
renaming the file means editing that field too.

Rules for the file itself:

- JPEG, portrait crop, at least 800 px on the short edge, under ~15 MB.
- Anything larger or in another format is fine too — convert it:

  ```bash
  ffmpeg -i whatever.heic -vf "scale='if(gt(iw,ih),-2,1000)':-2" -q:v 3 assets/refs/grandmother_source.png
  ```

- If you have several photographs, use the clearest single face as `grandmother_source.png` and
  keep the rest here under other names. Only the declared file is read by the pipeline.
- If you have a photograph of her **young**, save it as `assets/refs/girl_source.jpg` and add
  `"source_photo": "assets/refs/girl_source.jpg"` to the `girl` character in `storyboard.json`.
  The girl is the same person at thirteen, and an old photograph cannot tell the model what her
  young face looked like — the two characters share a seed but not a face. Without that file the
  girl is generated from the written description alone and is the weakest likeness in the film.

## How the pipeline uses it

- `pipeline/keyframes.py` sends it to the image model as an image-to-image likeness reference
  (low strength), so the painted figure has her face structure and not a stranger's.
- The style block still applies: the output is a gouache-and-ink illustration, deliberately not a
  photograph. That is the likeness-risk mitigation described in `ETHICS.md`.
- The photograph is never composited into the film and never published. Only generated frames
  reach `outputs/`.
- If the file is missing, the stage logs a warning, records it in `runs.jsonl`, and falls back to
  the text-only prompt. The film still builds — it just stops looking like her.

## Privacy

The photo is of a person who has died, and it belongs to her family, not to this repo. If this
repository is ever made public, add `assets/refs/*.jpg` to `.gitignore` and keep the photograph on
your own machine. The clean-clone regeneration path does not require it: `PROVIDER_MODE=mock`
builds the whole film without any reference image, and so does a real run with the file absent.
