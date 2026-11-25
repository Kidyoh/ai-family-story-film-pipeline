"""Stage 4 - shots: turn each keyframe into a moving clip.

Two ways a shot moves, and the storyboard decides which:

  wan      image-to-video on a Wan-class model. Used on the four shots that
           carry the film's motion (the smoke, the walk, the valley, the
           coffee steam). Hands are never animated: that is the failure mode
           that ruins generated video, so hand shots are Ken Burns.
  kenburns slow push or pan over the still, rendered in FFmpeg. Deterministic,
           free, and it cannot invent a sixth finger.

If no video endpoint is configured the stage falls back to Ken Burns for every
shot and logs each fallback to runs.jsonl. A clone with no keys still finishes.

Run:  python -m pipeline.shots [--only A1] [--force]
Writes: outputs/clips/<shot>.mp4
"""

from __future__ import annotations

import json
import os

from . import common, providers
from .common import out_path, rel, stage_cli

WIDTH, HEIGHT = 1080, 1920
FPS = 24


def load_timing() -> dict:
    path = out_path("audio", "timing.json")
    if not path.exists():
        common.die("outputs/audio/timing.json is missing. Run `make narration` first: "
                   "the narration is what decides how long each shot lasts.")
    return json.loads(path.read_text())


def render_kenburns(source, target, *, duration: float, zoom_from: float, zoom_to: float, pan: str) -> None:
    """Zoompan over a still, rendered from a 2x downscale so the motion is smooth.

    The input is scaled up before the move and the crop is taken from that, which
    is what stops zoompan from stepping by whole pixels and juddering.
    """
    frames = max(2, int(round(duration * FPS)))
    z0, z1 = zoom_from, zoom_to
    if pan in {"left", "right", "up", "down"}:
        z0, z1 = max(1.02, z0), max(1.02, z1)
    zoom = f"{z0}+({z1}-{z0})*on/{frames - 1}"
    centre_x = "(iw/2-(iw/zoom/2))"
    centre_y = "(ih/2-(ih/zoom/2))"
    travel = "((on/{})-0.5)".format(frames - 1)
    offsets = {
        "none": (centre_x, centre_y),
        "in": (centre_x, centre_y),
        "out": (centre_x, centre_y),
        "left": (f"{centre_x}-{travel}*iw*0.04", centre_y),
        "right": (f"{centre_x}+{travel}*iw*0.04", centre_y),
        "up": (centre_x, f"{centre_y}-{travel}*ih*0.04"),
        "down": (centre_x, f"{centre_y}+{travel}*ih*0.04"),
    }
    x_expr, y_expr = offsets.get(pan, offsets["none"])
    common.ff(
        "-loop", "1", "-framerate", FPS, "-i", source, "-t", round(duration, 3),
        "-vf",
        f"scale={WIDTH * 2}:{HEIGHT * 2}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH * 2}:{HEIGHT * 2},"
        f"zoompan=z='{zoom}':x='{x_expr}':y='{y_expr}':d=1:s={WIDTH}x{HEIGHT}:fps={FPS},"
        f"format=yuv420p",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", FPS, target,
    )


def conform(source, target, *, duration: float, loop: bool) -> None:
    """Force a generated clip onto the film's grid: 1080x1920, 24 fps, exact length."""
    args = ["-i", source]
    if loop:
        args = ["-stream_loop", "-1", "-i", source]
    common.ff(
        *args, "-t", round(duration, 3),
        "-vf", f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
               f"crop={WIDTH}:{HEIGHT},fps={FPS},format=yuv420p",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", target,
    )


def try_video(storyboard: dict, shot: dict, keyframe, duration: float, seed: int):
    """One attempt at real image-to-video; returns a temp path or None."""
    common.load_env()
    image = keyframe.read_bytes()
    motion = shot.get("motion") or shot["keyframe"]["prompt"]
    temp = out_path("clips", f"{shot['id']}.raw.mp4")
    used = None
    try:
        if common.secrets_present()["CLOUDFLARE_API_TOKEN"] and os.environ.get("CF_VIDEO_MODEL"):
            raw, cost = providers.cloudflare_video(image=image, motion=motion, seconds=duration, seed=seed)
            used = "cloudflare"
        elif os.environ.get("WAN_ENDPOINT"):
            raw, cost = providers.wan_endpoint_video(image=image, motion=motion, seconds=duration, seed=seed)
            used = "wan-endpoint"
        else:
            raise providers.ProviderError("no video endpoint configured")
    except providers.ProviderError as exc:
        print(f"    ! {exc}; using Ken Burns")
        common.log_run(stage="shots", operation="video", provider="none", model="none",
                       status="skipped", shot_id=shot["id"], note=str(exc))
        return None
    temp.write_bytes(raw)
    if temp.stat().st_size < 2048:
        temp.unlink(missing_ok=True)
        common.log_run(stage="shots", operation="video", provider=used, model="video",
                       status="empty-response", shot_id=shot["id"])
        return None
    common.log_run(stage="shots", operation="video", provider=used,
                   model="wan", status="ok", shot_id=shot["id"],
                   seed=seed, cost_usd_estimate=round(cost, 4), bytes=temp.stat().st_size)
    return temp


def run(storyboard: dict, args, only) -> None:
    timing = load_timing()
    durations = {entry["id"]: entry["duration_s"] for entry in timing["shots"]}
    mode = common.provider_mode()
    # Narration trims shots to fit the 90 s ceiling, so a shot shorter than its
    # storyboard intent is normal. Only warn when the cut leaves no room for the
    # line itself, which means the timing file is stale.
    lead = timing.get("narration_lead_in_s", 0.5)
    tail = timing.get("narration_tail_s", 0.6)
    for entry in timing["shots"]:
        if entry["id"] in durations and entry["narration_s"]:
            floor = entry["narration_s"] + lead + tail - 0.05
            if entry["duration_s"] < floor:
                print(f"  ! timing for {entry['id']} is shorter than its line; run `make narration`")

    for shot in storyboard["shots"]:
        if only and shot["id"] not in only:
            continue
        keyframe = out_path("keyframes", f"{shot['id']}.png")
        if not keyframe.exists():
            common.die(f"missing {rel(keyframe)}. Run `make keyframes` first.")
        target = out_path("clips", f"{shot['id']}.mp4")
        duration = durations.get(shot["id"], shot["duration_s"])
        kb = shot.get("kenburns") or {}
        key = common.artifact_key(
            "clip", shot["id"], common.sha256_file(keyframe), duration, shot["mode"], kb,
            shot.get("motion"), mode,
        )
        if common.is_fresh(target, key) and not args.force:
            print(f"  {shot['id']}: cached")
            continue
        if args.dry_run:
            print(f"  {shot['id']}: would render {duration:.1f}s as {shot['mode']}")
            continue

        print(f"  {shot['id']}: {duration:.1f}s, {shot['mode']}")
        rendered = False
        if shot["mode"] == "wan" and mode == "real":
            temp = try_video(storyboard, shot, keyframe, duration, shot["keyframe"].get("seed", 1))
            if temp:
                conform(temp, target, duration=duration, loop=True)
                temp.unlink(missing_ok=True)
                rendered = True
                common.log_run(stage="shots", operation="render", provider="local", model="ffmpeg",
                               status="ok", shot_id=shot["id"], seconds=duration, mode="wan")
        if not rendered:
            if mode == "mock":
                common.log_run(stage="shots", operation="render", provider="mock", model="ffmpeg",
                               status="ok", shot_id=shot["id"], mode="kenburns-mock")
            render_kenburns(
                keyframe, target, duration=duration,
                zoom_from=kb.get("zoom_from", 1.0), zoom_to=kb.get("zoom_to", 1.08),
                pan=kb.get("pan", "in"),
            )
            if shot["mode"] == "wan" and mode == "real":
                common.log_run(stage="shots", operation="render", provider="local", model="ffmpeg",
                               status="fallback", shot_id=shot["id"], seconds=duration,
                               mode="kenburns", note="no video endpoint; keyframe animated in FFmpeg")
        common.mark_fresh(target, key)
        print(f"    wrote {rel(target)}")


if __name__ == "__main__":
    stage_cli("shots", run, description="Animate each keyframe into a shot clip")
