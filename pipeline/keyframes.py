"""Stage 3 - keyframes: one still per shot, painted from the contract.

This is the stage where a real person enters the film, so it is the stage that
enforces the consent gate from ETHICS.md. If `meta.consent.status` is `pending`
nothing is generated at all.

Likeness rules:
  - a character's `source_photo` is sent as an image-to-image *reference*, never
    composited, never copied into outputs/
  - a missing photo is a warning, not a failure: the shot falls back to the
    written description and the fallback is recorded in runs.jsonl
  - the style block always applies, so the result is a painting, not a
    photograph of her

Run:  python -m pipeline.keyframes [--only A1,B1] [--force]
Writes: outputs/keyframes/<shot>.png, outputs/refs/<character>_sheet.png
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import common, providers
from .common import ROOT, out_path, rel, stage_cli
from .script import build_prompt

# The vertical frame is generated directly; horizontal is derived at assembly
# time from the same master. See DESIGN.md, "One master, two exports".
FRAME = (1080, 1920)


def reference_for(storyboard: dict, character_id: str, *, shot_id: str) -> Path | None:
    character = next(c for c in storyboard["characters"] if c["id"] == character_id)
    declared = character.get("source_photo")
    if not declared:
        return None
    path = ROOT / declared
    if not path.exists():
        common.log_run(stage="keyframes", operation="reference", provider="local",
                       model="none", status="missing", shot_id=shot_id, character=character_id,
                       note=f"{declared} not found; generated from text only")
        print(f"    ! {character_id}: {declared} is missing, generating from text only")
        print(f"      (see {declared.rsplit('/', 1)[0]}/README.md for how to add it)")
        return None
    return path


def sheet_for(storyboard: dict, character_id: str) -> Path | None:
    """The character's painted sheet, if this build has generated one."""
    character = next(c for c in storyboard["characters"] if c["id"] == character_id)
    declared = character.get("reference_sheet")
    if not declared:
        return None
    path = ROOT / declared
    return path if path.exists() and path.stat().st_size else None


def mock_frame(storyboard: dict, shot: dict, target: Path) -> None:
    common.mock_image(
        target, shot_id=shot["id"], scene=shot["scene"],
        label=shot["keyframe"]["prompt"].split(",")[0],
        width=FRAME[0], height=FRAME[1], seed=shot["keyframe"].get("seed", 1),
    )


def run(storyboard: dict, args, only) -> None:
    mode = common.provider_mode()
    palette_ok = bool(storyboard["style"].get("palette"))
    if not palette_ok:
        print("  ! no palette in the storyboard; keyframes will be less consistent")

    # Reference sheets: the golden look, generated once, then sent back as an
    # image reference with every shot the character appears in, which is what
    # keeps one face across twelve frames. They are also the artifact a human
    # reviewer checks before spending money on video.
    for character in storyboard["characters"]:
        sheet = ROOT / (character.get("reference_sheet") or f"outputs/refs/{character['id']}_sheet.png")
        if not character.get("reference_sheet"):
            continue
        key = common.artifact_key("refsheet", character["id"], character["description"],
                                  storyboard["style"]["block"], character.get("source_photo"), mode)
        if common.is_fresh(sheet, key) and not args.force:
            print(f"  refsheet {character['id']}: cached")
            continue
        if args.dry_run:
            print(f"  refsheet {character['id']}: would generate")
            continue
        sheet.parent.mkdir(parents=True, exist_ok=True)
        prompt = (f"character reference sheet, three-quarter portrait, neutral plain background, "
                  f"{character['description']}. {storyboard['style']['block']}")
        reference = reference_for(storyboard, character["id"], shot_id="refsheet")
        generate(storyboard, prompt=prompt, target=sheet, seed=(character.get("seed") or 7),
                 reference=reference, shot_id="refsheet", stage="keyframes", mode=mode)
        common.mark_fresh(sheet, key)

    for shot in storyboard["shots"]:
        if only and shot["id"] not in only:
            continue
        target = out_path("keyframes", f"{shot['id']}.png")
        prompt = build_prompt(storyboard, shot)
        reference = None
        sheets: list[Path] = []
        if shot["keyframe"].get("use_reference", True):
            for character_id in shot["keyframe"].get("characters", []):
                if reference is None:
                    reference = reference_for(storyboard, character_id, shot_id=shot["id"])
                # Mock frames ignore references, so keep the mock cache stable.
                sheet = sheet_for(storyboard, character_id) if mode == "real" else None
                if sheet:
                    sheets.append(sheet)
        key = common.artifact_key(
            "keyframe", shot["id"], prompt, storyboard["style"]["negative"], shot["keyframe"].get("seed"),
            str(reference) if reference else None,
            common.sha256_file(reference) if reference else None, mode,
            *[common.sha256_file(sheet) for sheet in sheets],
        )
        if common.is_fresh(target, key) and not args.force:
            print(f"  {shot['id']}: cached")
            common.log_run(stage="keyframes", operation="image", provider=mode, model="cache",
                           status="ok", cached=True, shot_id=shot["id"])
            continue
        if args.dry_run:
            print(f"  {shot['id']}: would generate ({'with' if reference or sheets else 'without'} reference)")
            continue
        source = " from the family photograph" if reference else (" from the character sheet" if sheets else "")
        print(f"  {shot['id']}: painting{source} ...")
        generate(storyboard, prompt=prompt, target=target, seed=shot["keyframe"].get("seed", 1),
                 reference=reference, sheets=sheets, shot_id=shot["id"], stage="keyframes", mode=mode,
                 scene=shot["scene"])
        common.mark_fresh(target, key)


def generate(storyboard: dict, *, prompt: str, target: Path, seed: int, reference: Path | None,
             shot_id: str, stage: str, mode: str, scene: str = "A",
             sheets: list[Path] | None = None) -> None:
    """Generate one image, choosing the provider by what the request needs.

    FLUX.2 on Cloudflare handles both cases and is the default: it accepts
    width, height, seed and up to four reference images, and it is the only one
    of the two available on a free tier. Gemini's image model is the fallback
    for a key that has no Cloudflare account, even though its free quota is 0.
    """
    negative = storyboard["style"]["negative"]
    if mode == "mock":
        common.mock_image(target, shot_id=shot_id, scene=scene, label=prompt.split(",")[0],
                          width=FRAME[0], height=FRAME[1], seed=seed)
        common.log_run(stage=stage, operation="image", provider="mock", model="mock",
                       status="ok", shot_id=shot_id, bytes=target.stat().st_size)
        return

    # FLUX.2 on Cloudflare does both jobs: plain text-to-image, and an edit when
    # a reference photo is supplied. It is the default because Gemini's image
    # model is paid-only (free-tier quota is zero), so a key alone cannot paint
    # a frame. Gemini remains the fallback when there is no Cloudflare token.
    common.load_env()
    secrets = common.secrets_present()
    if not secrets["CLOUDFLARE_API_TOKEN"] and not secrets["GEMINI_API_KEY"] and providers.openrouter_key_present():
        raw, cost = providers.openrouter_image(prompt=prompt, negative=negative,
                                               reference=reference, sheets=sheets)
        model = os.environ.get("OPENROUTER_IMAGE_MODEL") or providers.OPENROUTER_IMAGE_MODEL
        provider_name = "openrouter"
    elif not secrets["CLOUDFLARE_API_TOKEN"]:
        # Gemini takes one reference here: the photograph if there is one,
        # otherwise the character's painted sheet.
        sheet = sheets[0] if sheets and reference is None else None
        raw, cost = providers.gemini_image(
            prompt=prompt, negative=negative, aspect_ratio="9:16", reference=reference or sheet,
            reference_is_sheet=sheet is not None,
        )
        model = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
        provider_name = "gemini"
    else:
        raw, cost = providers.cloudflare_image(
            prompt=prompt, negative=negative, width=FRAME[0], height=FRAME[1],
            seed=seed, reference=reference, sheets=sheets,
        )
        model = os.environ.get("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-2-klein-4b")
        provider_name = "cloudflare"

    suffix = ".jpg" if raw[:3] == b"\xff\xd8\xff" else ".png"
    # Never the target's own name: a PNG reply would make ffmpeg read and
    # write the same file.
    staged = target.with_name(target.stem + ".staged" + suffix)
    staged.write_bytes(raw)
    common.ff("-i", staged, "-vf", f"scale={FRAME[0]}:{FRAME[1]}:force_original_aspect_ratio=increase,"
                                   f"crop={FRAME[0]}:{FRAME[1]}", "-frames:v", "1", target)
    if staged != target:
        staged.unlink(missing_ok=True)
    common.log_run(stage=stage, operation="image", provider=provider_name,
                   model=model, status="ok", shot_id=shot_id, seed=seed,
                   reference=rel(reference) if reference else None,
                   sheets=[rel(sheet) for sheet in sheets] if sheets else None,
                   cost_usd_estimate=round(cost, 4), bytes=target.stat().st_size)


if __name__ == "__main__":
    stage_cli("keyframes", run, description="Generate one painted keyframe per shot")
