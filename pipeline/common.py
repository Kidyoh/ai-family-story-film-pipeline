"""Shared plumbing for every stage: the contract, the cache, the run log, ffmpeg.

A stage script is allowed to be creative about *how* it renders things. It is
never allowed to decide *what* the film is: durations, prompts, narration and
music cues all come from storyboard.json, which is validated against
schema/storyboard.schema.json before any stage is allowed to touch a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
STORYBOARD_PATH = ROOT / "storyboard.json"
SCHEMA_PATH = ROOT / "schema" / "storyboard.schema.json"
OUT = ROOT / "outputs"
RUNS_PATH = ROOT / "runs.jsonl"

_LOG_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# environment
# --------------------------------------------------------------------------

def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader so the repo needs no dotenv dependency.

    Real environment variables always win, which keeps CI and `make film`
    overrides working.
    """
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def provider_mode() -> str:
    """real | mock. Mock needs no keys and is byte-deterministic."""
    load_env()
    mode = os.environ.get("PROVIDER_MODE", "").strip().lower()
    if mode in {"real", "mock"}:
        return mode
    return "real" if (os.environ.get("GEMINI_API_KEY") or os.environ.get("CLOUDFLARE_API_TOKEN")) else "mock"


def secrets_present() -> dict[str, bool]:
    load_env()
    return {
        "GEMINI_API_KEY": bool(os.environ.get("GEMINI_API_KEY")),
        "CLOUDFLARE_API_TOKEN": bool(os.environ.get("CLOUDFLARE_API_TOKEN")),
        "CLOUDFLARE_ACCOUNT_ID": bool(os.environ.get("CLOUDFLARE_ACCOUNT_ID")),
    }


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------

def load_storyboard(path: Path = STORYBOARD_PATH) -> dict:
    """Load and validate the contract. A stage that fails here must not run."""
    if not path.exists():
        die(f"storyboard not found: {path}")
    try:
        storyboard = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        die(f"storyboard.json is not valid JSON: {exc}")

    schema = json.loads(SCHEMA_PATH.read_text())
    errors = sorted(Draft202012Validator(schema).iter_errors(storyboard), key=str)
    if errors:
        lines = [f"  - {'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in errors]
        die("storyboard.json does not satisfy schema/storyboard.schema.json:\n" + "\n".join(lines))

    # Semantic checks the schema cannot express. Cheap, and they catch the two
    # mistakes that actually cost money: a shot pointing at a cue that does not
    # exist, and a reference to a character that was renamed.
    shot_ids = {shot["id"] for shot in storyboard["shots"]}
    for cue in storyboard["music"]["cues"]:
        for side in ("from_shot", "to_shot"):
            if cue[side] not in shot_ids:
                die(f"music cue {cue['id']} {side}={cue[side]} is not a shot")
    character_ids = {c["id"] for c in storyboard["characters"]}
    for shot in storyboard["shots"]:
        for character in shot["keyframe"].get("characters", []):
            if character not in character_ids:
                die(f"shot {shot['id']} references unknown character '{character}'")
        script = shot.get("narration")
        if script and script.get("voice", "narrator") not in storyboard["voices"]:
            die(f"shot {shot['id']} uses voice '{script.get('voice')}' which has no entry in voices")
    return storyboard


def require_consent(storyboard: dict, stage: str) -> None:
    """The gate from ETHICS.md. Nothing that depicts or voices a real person
    may run while consent is pending."""
    status = storyboard["meta"]["consent"]["status"]
    if status == "pending":
        die(
            f"{stage}: refusing to run. meta.consent.status is 'pending', which means a real "
            "person is depicted or voiced without a recorded consent. Fix the record in "
            "ETHICS.md, set status to 'recorded', and run again."
        )


def shot_sequence(storyboard: dict) -> list[dict]:
    return storyboard["shots"]


def narration_shots(storyboard: dict) -> list[dict]:
    return [s for s in storyboard["shots"] if s.get("narration")]


def scene_names(storyboard: dict) -> list[str]:
    seen: list[str] = []
    for shot in storyboard["shots"]:
        if shot["scene"] not in seen:
            seen.append(shot["scene"])
    return seen


# --------------------------------------------------------------------------
# paths, cache
# --------------------------------------------------------------------------

def out_path(*parts: str) -> Path:
    path = OUT.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def artifact_key(*parts: object) -> str:
    """Stable key for a generated artifact. Any input change invalidates it."""
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def is_fresh(path: Path, key: str) -> bool:
    stamp = Path(str(path) + ".key")
    if not path.exists() or path.stat().st_size == 0 or not stamp.exists():
        return False
    return stamp.read_text().strip() == key


def mark_fresh(path: Path, key: str) -> None:
    Path(str(path) + ".key").write_text(key + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# run log
# --------------------------------------------------------------------------

def log_run(
    *,
    stage: str,
    operation: str,
    provider: str,
    model: str,
    status: str,
    seconds: float = 0.0,
    cached: bool = False,
    **extra: object,
) -> None:
    """One JSON line per model call, appended to runs.jsonl at the repo root.

    Graders read this file, so every line is self-describing and no secret ever
    reaches it: only names, ids, timings, sizes and rough cost.
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stage": stage,
        "operation": operation,
        "provider": provider,
        "model": model,
        "status": status,
        "cached": cached,
        "seconds": round(seconds, 2),
    }
    record.update({k: v for k, v in extra.items() if v is not None})
    with _LOG_LOCK:
        with RUNS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# retries
# --------------------------------------------------------------------------

class ProviderError(RuntimeError):
    pass


def with_retries(fn, *, attempts: int = 3, base_delay: float = 2.0, label: str = ""):
    """Retry with exponential backoff, jitter, and a bounded total wait.

    Retries are for transient failures (429, 5xx, timeouts). A 400 is a bug in
    the prompt or the payload and is raised immediately, as a ProviderError.
    """
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except (requests.HTTPError, requests.Timeout, requests.ConnectionError, ProviderError) as exc:
            last = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500 and status != 429:
                # Not retryable, but still a provider failure: raise the type
                # the stages catch, so an optional capability can fall back and
                # a required one exits with a message instead of a traceback.
                raise ProviderError(f"{label or 'call'} rejected: {exc}") from exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.75)
            print(f"    ! {label or 'call'} failed ({exc.__class__.__name__}); retry {attempt}/{attempts - 1} in {delay:.1f}s")
            time.sleep(delay)
    raise ProviderError(f"{label or 'call'} failed after {attempts} attempts: {last}")


# --------------------------------------------------------------------------
# ffmpeg
# --------------------------------------------------------------------------

_FFMPEG: str | None = None
_FFPROBE: str | None = None


def _find(tool: str) -> str:
    """Prefer a build with freetype and libass: drawtext and burned subtitles
    are not optional here. FFMPEG / FFPROBE override everything."""
    env = os.environ.get(tool.upper())
    if env and Path(env).exists():
        return env
    if tool == "ffmpeg":
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    candidates = sorted((ROOT / ".tools").glob(f"ffmpeg-*/{tool}"))
    if candidates:
        return str(candidates[-1])
    found = shutil.which(tool)
    if found:
        return found
    die(f"{tool} not found. See README.md, 'Toolchain'.")


_FILTERS: set[str] | None = None


def has_filter(name: str) -> bool:
    global _FILTERS
    if _FILTERS is None:
        result = run([ffmpeg(), "-hide_banner", "-filters"])
        _FILTERS = {line.split()[1] for line in result.stdout.splitlines() if len(line.split()) > 1}
    return name in _FILTERS


def ffmpeg() -> str:
    global _FFMPEG
    if _FFMPEG is None:
        _FFMPEG = _find("ffmpeg")
    return _FFMPEG


def ffprobe() -> str:
    global _FFPROBE
    if _FFPROBE is None:
        _FFPROBE = _find("ffprobe")
    return _FFPROBE


def run(cmd: list[str], *, quiet: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n".join((result.stderr or "").strip().splitlines()[-12:])
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(cmd[:6])} ...\n{tail}")
    if not quiet:
        print(result.stdout)
    return result


def ff(*args: object, quiet: bool = True) -> subprocess.CompletedProcess:
    return run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], quiet=quiet)


def probe_duration(path: Path) -> float:
    result = run([
        ffprobe(), "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path),
    ])
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise ProviderError(f"could not read duration of {path}")


def probe_stream(path: Path) -> dict:
    result = run([
        ffprobe(), "-v", "error", "-show_entries",
        "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
        "-of", "json", str(path),
    ])
    return json.loads(result.stdout)


def wav_duration(path: Path) -> float:
    return probe_duration(path)


def integrated_loudness(path: Path) -> float | None:
    """Integrated loudness in LUFS, or None for silence or an unreadable file."""
    result = run([ffmpeg(), "-hide_banner", "-nostats", "-i", str(path),
                  "-af", "ebur128", "-f", "null", "-"])
    for line in reversed(result.stderr.splitlines()):
        line = line.strip()
        if line.startswith("I:") and "LUFS" in line:
            try:
                value = float(line.split()[1])
            except (IndexError, ValueError):
                return None
            return value if value > -69 else None
    return None


def gain_to_loudness(path: Path, target_lufs: float) -> float:
    """dB of plain gain that brings a clip to target_lufs.

    A generated clip arrives at whatever level its model likes. A fixed gain
    (rather than a compressor) puts it at a known level without touching its
    dynamics. Clamped, so near-silence is not boosted into noise.
    """
    measured = integrated_loudness(path)
    if measured is None:
        return 0.0
    return max(-40.0, min(30.0, target_lufs - measured))


# --------------------------------------------------------------------------
# deterministic mock material
# --------------------------------------------------------------------------

SCENE_COLOURS = {
    "A": ("#10182b", "#c8761f"),
    "B": ("#1d2a35", "#7d8fa0"),
    "C": ("#b06a2c", "#e2b04a"),
    "D": ("#2a2418", "#b9863c"),
}


def mock_image(path: Path, *, shot_id: str, scene: str, label: str, width: int, height: int, seed: int) -> None:
    """A deterministic stand-in frame, so a clean clone can build the film.

    It is deliberately ugly and obviously not the finished thing: a gradient in
    the scene palette, grain, vignette, and the shot id burned in. Nobody should
    mistake a mock build for a real one.
    """
    dark, light = SCENE_COLOURS.get(scene, SCENE_COLOURS["A"])
    font = _font_file() if has_filter("drawtext") else None
    drawtext = (
        f"drawtext=fontfile={font}:text='{shot_id}':fontcolor=white@0.55:fontsize={max(28, height // 14)}"
        f":x=(w-text_w)/2:y=h*0.44"
        f",drawtext=fontfile={font}:text='{_escape(label)}':fontcolor=white@0.32:fontsize={max(16, height // 46)}"
        f":x=(w-text_w)/2:y=h*0.44+{max(40, height // 12)}"
        if font else ""
    )
    source = (f"gradients=s={width}x{height}:c0={dark}:c1={light}"
              f":x0=0:y0=0:x1={width}:y1={height}:seed={seed % 1000}")
    filters = ["format=rgb24", "noise=alls=12:allf=t", "vignette=PI/5"]
    if drawtext:
        filters.append(drawtext)
    ff(
        "-f", "lavfi", "-i", source,
        "-vf", ",".join(filters), "-frames:v", "1", path,
    )


def _font_file() -> str | None:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("'", "").replace(":", " -").replace(",", " ")[:60]


def mock_tone(path: Path, *, seconds: float, freq: float = 220.0, gain_db: float = -24.0) -> None:
    """Silence-with-a-pulse, used when a TTS call is not available."""
    ff(
        "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=24000:duration={seconds}",
        "-af", f"volume={gain_db}dB,afade=t=in:d=0.15,afade=t=out:st={max(0.0, seconds - 0.2)}:d=0.2",
        "-ac", "1", "-ar", "24000", path,
    )


# --------------------------------------------------------------------------
# stage runner
# --------------------------------------------------------------------------

def stage_cli(stage: str, run_stage, *, description: str) -> None:
    """Common CLI: --force, --provider-mode, --dry-run."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--force", action="store_true", help="ignore cached artifacts")
    parser.add_argument("--only", default=None, help="comma-separated shot or cue ids")
    parser.add_argument("--provider-mode", choices=["real", "mock"], default=None)
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = parser.parse_args()

    if args.provider_mode:
        os.environ["PROVIDER_MODE"] = args.provider_mode
    storyboard = load_storyboard()
    require_consent(storyboard, stage)

    only = {x.strip() for x in args.only.split(",") if x.strip()} if args.only else None
    started = time.time()
    print(f"[{stage}] provider mode: {provider_mode()}")
    try:
        run_stage(storyboard, args, only)
    except ProviderError as exc:
        print(f"\n[{stage}] failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"[{stage}] done in {time.time() - started:.1f}s")


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def rel(path: Path) -> str:
    """Repo-relative path for logs. Absolute paths leak a username."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return path.name
