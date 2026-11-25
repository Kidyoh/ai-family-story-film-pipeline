"""Model adapters. One function per capability, each returning bytes.

Everything that talks to a network lives here, so the stage scripts stay
readable and every call has exactly one place to be logged and priced.

PROVIDER_MODE=mock never reaches the network. The mock output is deterministic:
the same storyboard always produces the same files.
"""

from __future__ import annotations

import base64
import io
import json
import os
import wave
from pathlib import Path

import requests

from . import common
from .common import ProviderError, with_retries

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Rough per-call prices in USD, used only for the estimate column in
# runs.jsonl and the tables in COSTS.md. Verified against the published price
# pages on 2026-11-24; treat them as budgets, not invoices.
PRICE = {
    "gemini-2.5-flash-image": 0.039,
    "gemini-3-pro-image-preview": 0.134,
    "@cf/black-forest-labs/flux-1-schnell": 0.0004,
    "@cf/black-forest-labs/flux-2-klein-4b": 0.0035,  # per 1080x1920 frame
    "@cf/black-forest-labs/flux-2-klein-9b": 0.0171,  # per 1080x1920 frame
    "gemini-2.5-flash-preview-tts": 0.00002,  # per character of narration
    "wan-i2v": 0.30,  # per 5 s clip, dependent on host
    "ace-step": 0.00,  # self-hosted
}


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------

def _gemini_key() -> str:
    common.load_env()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise ProviderError("GEMINI_API_KEY is not set (put it in .env, never in git)")
    return key


def _gemini_post(model: str, payload: dict, *, timeout: int = 180) -> dict:
    key = _gemini_key()
    url = f"{GEMINI_BASE}/models/{model}:generateContent"
    response = requests.post(
        url, params={"key": key}, json=payload, timeout=timeout,
        headers={"Content-Type": "application/json"},
    )
    if response.status_code >= 400:
        # Never echo the URL: it carries the key in the query string.
        raise requests.HTTPError(
            f"gemini {model} -> HTTP {response.status_code}: {response.text[:400]}",
            response=response,
        )
    return response.json()


def _first_inline(data: dict) -> tuple[str, bytes]:
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        raise ProviderError(f"gemini returned no content: {json.dumps(data)[:400]}")
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline:
            return inline.get("mimeType", inline.get("mime_type", "")), base64.b64decode(inline["data"])
    raise ProviderError("gemini returned no inline media")


LANGUAGE_NAMES = {"am": "Amharic", "en": "English", "om": "Afaan Oromo", "ti": "Tigrinya"}


def gemini_speech(*, text: str, voice_id: str, instruction: str, model: str | None = None,
                  language: str = "am") -> tuple[bytes, float]:
    """Text-to-speech in the film's language, returned as a 24 kHz mono WAV."""
    common.load_env()
    model = model or os.environ.get("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
    spoken = LANGUAGE_NAMES.get(language, language)
    prompt = (
        f"Read the following {spoken} line aloud exactly as written. Do not translate it, "
        f"do not add words. Style: {instruction}. Line: {text}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_id}},
            },
        },
    }
    data = with_retries(
        lambda: _gemini_post(model, payload, timeout=120), attempts=4, base_delay=15.0,
        label=f"tts {voice_id}",
    )
    mime, raw = _first_inline(data)
    return pcm_to_wav(raw, mime), PRICE.get(model, 0.0) * max(1, len(text))


def pcm_to_wav(raw: bytes, mime: str) -> bytes:
    """Gemini TTS returns headerless PCM (audio/L16;rate=24000) or an ogg/mp3 blob."""
    rate = 24000
    for token in mime.split(";"):
        token = token.strip()
        if token.startswith("rate="):
            try:
                rate = int(token.split("=", 1)[1])
            except ValueError:
                pass
    if "L16" not in mime and ("wav" in mime or "ogg" in mime or "mpeg" in mime):
        return raw  # already a container; ffmpeg will read it
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(raw)
    return buffer.getvalue()


def gemini_image(
    *,
    prompt: str,
    negative: str,
    aspect_ratio: str,
    reference: Path | None,
    model: str | None = None,
    reference_is_sheet: bool = False,
) -> tuple[bytes, float]:
    """Keyframe generation. With a reference photo this is an edit request
    ('use this face structure, repaint in the style'), which is what keeps the
    likeness close without ever showing the photograph."""
    common.load_env()
    model = model or os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
    parts: list[dict] = []
    if reference is not None:
        suffix = reference.suffix.lower().lstrip(".")
        mime = "image/png" if suffix == "png" else "image/jpeg"
        parts.append({
            "inline_data": {
                "mime_type": mime,
                "data": base64.b64encode(reference.read_bytes()).decode("ascii"),
            }
        })
        parts.append({
            "text": (
                "This image is a painted character reference sheet. Paint the same person - same "
                "face, hair, skin tone and clothing - in the scene below, in the same painted "
                "style. Do not copy the sheet's pose or plain background.\n\n"
                f"Scene: {prompt}\n\nAvoid: {negative}"
            ) if reference_is_sheet else (
                "Repaint this photograph as a hand-painted illustration. Keep the person's face "
                "structure, brow, nose and the age in their features; change the lighting, "
                "clothing and setting to match the scene below. Do not copy the photograph's "
                "background. The photograph is a likeness reference only and must not appear "
                f"in the result.\n\nScene: {prompt}\n\nAvoid: {negative}"
            )
        })
    else:
        parts.append({"text": f"{prompt}\n\nAvoid: {negative}"})
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": aspect_ratio}},
    }
    data = with_retries(lambda: _gemini_post(model, payload, timeout=240), attempts=3, label="keyframe")
    _, raw = _first_inline(data)
    return raw, PRICE.get(model, 0.0)


# --------------------------------------------------------------------------
# Cloudflare Workers AI
# --------------------------------------------------------------------------

def _cloudflare_creds() -> tuple[str, str]:
    common.load_env()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not token or not account:
        raise ProviderError("CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID must both be set")
    return token, account


def _reference_tile(reference: Path, *, fit: bool = False) -> bytes:
    """A reference image as a 512px JPEG tile.

    FLUX.2 edits from reference images rather than copying them, and it caps
    each input at 512px, so only a face-sized tile ever leaves this machine.
    Sending the full photograph would add cost and privacy exposure for detail
    the painting does not use.

    A photograph is centre-cropped to a square. A painted character sheet is
    9:16 with the face in the upper third, so `fit` scales it down whole
    instead of cropping the head off.
    """
    tile = common.out_path("tmp", f"reference_{reference.stem}.jpg")
    scale = ("scale=512:512:force_original_aspect_ratio=decrease:force_divisible_by=2" if fit
             else "scale=512:512:force_original_aspect_ratio=increase,crop=512:512")
    common.ff("-i", reference, "-vf", scale,
              "-frames:v", "1", "-q:v", "3", tile)
    try:
        return tile.read_bytes()
    finally:
        tile.unlink(missing_ok=True)


def cloudflare_image(*, prompt: str, negative: str, width: int, height: int, seed: int,
                     reference: Path | None = None,
                     sheets: list[Path] | None = None,
                     model: str | None = None) -> tuple[bytes, float]:
    """FLUX.2 on Workers AI: text-to-image, or an edit when a reference is given.

    FLUX.2 takes multipart form data even for a prompt-only request, and the
    reference has to arrive as `input_image_0`. Unlike flux-1-schnell it accepts
    width, height and seed, so a 9:16 frame is composed for the master instead
    of being cropped out of a square. The 4B model is the default because the
    9B one costs about five times more and would exhaust the daily free
    allowance after roughly six frames.

    `reference` is a photograph (centre-cropped); `sheets` are painted
    character sheets (fitted whole). Together they fill input_image_0..3.

    FLUX has no negative prompt. Text after "Avoid:" is read as more prompt, so
    naming "extra fingers" or "watermark" asks for them; the negative list is
    accepted for interface parity with Gemini and deliberately not sent.
    """
    common.load_env()
    model = model or os.environ.get("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-2-klein-4b")
    token, account = _cloudflare_creds()
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    fields: list[tuple[str, tuple]] = [
        ("prompt", (None, prompt)),
        ("width", (None, str(width))),
        ("height", (None, str(height))),
        ("seed", (None, str(seed))),
    ]
    tiles = [_reference_tile(reference)] if reference is not None else []
    tiles += [_reference_tile(sheet, fit=True) for sheet in (sheets or [])]
    for index, tile in enumerate(tiles[:4]):
        fields.append((f"input_image_{index}", (f"reference_{index}.jpg", tile, "image/jpeg")))

    def call():
        response = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                                 files=fields, timeout=300)
        if response.status_code >= 400:
            raise requests.HTTPError(
                f"cloudflare {model} -> HTTP {response.status_code}: {response.text[:300]}",
                response=response,
            )
        if response.headers.get("content-type", "").startswith("application/json"):
            body = response.json()
            if not body.get("success", True):
                raise ProviderError(f"cloudflare error: {json.dumps(body.get('errors'))[:300]}")
            image = (body.get("result") or {}).get("image")
            if not image:
                raise ProviderError("cloudflare returned JSON without an image")
            # base64 of a JPEG or PNG; the caller sniffs the magic bytes and
            # lets ffmpeg normalise whatever container comes back.
            return base64.b64decode(image)
        return response.content

    raw = with_retries(call, attempts=3, label="keyframe")
    return raw, PRICE.get(model, 0.0)


def cloudflare_video(*, image: bytes, motion: str, seconds: float, seed: int) -> tuple[bytes, float]:
    """Image-to-video on a Wan-class model hosted by Cloudflare.

    Cloudflare's video models are catalog entries rather than fixed Workers AI
    models, so the id is configuration: set CF_VIDEO_MODEL (for example the
    Alibaba Wan image-to-video slug shown in the Cloudflare AI catalog) and the
    stage will use it. Without it, shots.py falls back to Ken Burns and says so.
    """
    common.load_env()
    model = os.environ.get("CF_VIDEO_MODEL", "").strip()
    if not model:
        raise ProviderError("CF_VIDEO_MODEL is not set, so no video model is available")
    token, account = _cloudflare_creds()
    files = {"input_image": ("keyframe.png", image, "image/png")}
    data = {
        "prompt": motion,
        "seconds": str(int(round(seconds))),
        "seed": str(seed),
    }
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"

    def call():
        response = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                                 data=data, files=files, timeout=600)
        if response.status_code >= 400:
            raise requests.HTTPError(
                f"cloudflare video -> HTTP {response.status_code}: {response.text[:300]}",
                response=response,
            )
        return response.content

    return with_retries(call, attempts=2, label="wan"), PRICE["wan-i2v"]


def wan_endpoint_video(*, image: bytes, motion: str, seconds: float, seed: int) -> tuple[bytes, float]:
    """Optional self-hosted Wan 2.2 i2v endpoint (Kaggle, RunPod, Colab).

    Contract: multipart POST with `image`, and form fields `prompt`, `seconds`,
    `seed`; the response body is an mp4. Documented in ARCHITECTURE.md.
    """
    common.load_env()
    endpoint = os.environ.get("WAN_ENDPOINT", "").strip()
    if not endpoint:
        raise ProviderError("WAN_ENDPOINT is not set")
    headers = {}
    key = os.environ.get("WAN_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    def call():
        response = requests.post(
            endpoint, headers=headers, timeout=900,
            files={"image": ("keyframe.png", image, "image/png")},
            data={"prompt": motion, "seconds": str(seconds), "seed": str(seed)},
        )
        if response.status_code >= 400:
            raise requests.HTTPError(f"wan -> HTTP {response.status_code}: {response.text[:300]}",
                                     response=response)
        return response.content

    return with_retries(call, attempts=2, label="wan"), PRICE["wan-i2v"]


# --------------------------------------------------------------------------
# ACE-Step (music)
# --------------------------------------------------------------------------

def ace_step(*, caption: str, seconds: float, bpm: int, key: str, seed: int) -> tuple[bytes, float]:
    """Music generation through an ACE-Step HTTP service.

    ACE-Step has no first-party hosted API, so the endpoint is deployment
    configuration: MUSIC_ENDPOINT points at an ACE-Step inference server and
    the contract is documented in ARCHITECTURE.md. Without it music.py writes
    the deterministic score fallback and records that in runs.jsonl.
    """
    common.load_env()
    endpoint = os.environ.get("MUSIC_ENDPOINT", "").strip()
    if not endpoint:
        raise ProviderError("MUSIC_ENDPOINT is not set, so no music model is available")
    payload = {
        "caption": caption,
        "lyrics": "",
        "duration": round(seconds, 2),
        "bpm": bpm,
        "key": key,
        "seed": seed,
        "audio_duration": round(seconds, 2),
    }

    def call():
        response = requests.post(endpoint, json=payload, timeout=600)
        if response.status_code >= 400:
            raise requests.HTTPError(f"ace-step -> HTTP {response.status_code}: {response.text[:300]}",
                                     response=response)
        if response.headers.get("content-type", "").startswith("application/json"):
            body = response.json()
            audio = body.get("audio") or body.get("audio_base64")
            if audio:
                return base64.b64decode(audio)
            raise ProviderError("ace-step returned JSON without audio")
        return response.content

    return with_retries(call, attempts=3, label="music"), PRICE["ace-step"]
