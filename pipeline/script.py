"""Stage 1 - turn the contract into a shooting script.

No model is called here. This stage exists so that every later stage reads the
same derived plan instead of re-inventing it: full prompts, wrapped subtitle
lines, the shot order and the projected runtime.

Run:  python -m pipeline.script
Writes: outputs/plan.json, outputs/script.md
"""

from __future__ import annotations

import json

from .common import OUT, load_storyboard, out_path, rel, stage_cli

WORDS_PER_SECOND = 2.2  # measured once narration.py has real audio; see DESIGN.md


def build_prompt(storyboard: dict, shot: dict) -> str:
    """Scene prompt + character descriptions + the fixed style block.

    Order matters: the scene first (what is happening), then who is in it, then
    the look. The style block is appended verbatim on every shot so the film
    looks like one film, not twelve illustrations.
    """
    pieces = [shot["keyframe"]["prompt"].strip().rstrip(".")]
    characters = {c["id"]: c for c in storyboard["characters"]}
    for character_id in shot["keyframe"].get("characters", []):
        pieces.append(f"the person is {characters[character_id]['description']}")
    palette = storyboard["style"].get("palette", {}).get(f"scene_{shot['scene'].lower()}")
    if palette:
        pieces.append(f"palette: {palette}")
    pieces.append(storyboard["style"]["block"])
    return ". ".join(p.rstrip(".") for p in pieces if p) + "."


def wrap_subtitle(text: str, width: int = 42) -> list[str]:
    """Two lines of about forty characters, split at the nearest space to the
    midpoint so a phrase is not broken after an article."""
    if len(text) <= width:
        return [text]
    words = text.split()
    best, best_score = [], None
    for index in range(1, len(words)):
        left, right = " ".join(words[:index]), " ".join(words[index:])
        score = abs(len(left) - len(right))
        if len(left) <= width + 4 and len(right) <= width + 4 and (best_score is None or score < best_score):
            best, best_score = [left, right], score
    return best or [text]


def projected_runtime(storyboard: dict) -> float:
    return sum(s["duration_s"] for s in storyboard["shots"]) + sum(c["duration_s"] for c in storyboard["end_cards"])


def run(storyboard: dict, args, only) -> None:
    shots = storyboard["shots"]
    narration_shots = [s for s in shots if s.get("narration")]
    words = sum(len(s["narration"]["text"].split()) for s in narration_shots)
    speak_seconds = words / WORDS_PER_SECOND
    runtime = projected_runtime(storyboard)

    if runtime > 90:
        print(f"  ! projected runtime {runtime:.1f}s exceeds the 90s ceiling")
    if speak_seconds > runtime - 6:
        print(f"  ! narration needs ~{speak_seconds:.0f}s of speech in a {runtime:.0f}s film; trim lines")

    plan = {
        "version": storyboard["version"],
        "fps": storyboard["meta"]["fps"],
        "title": storyboard["meta"]["title"],
        "language": storyboard["meta"]["language"],
        "subtitle_language": storyboard["meta"]["subtitle_language"],
        "projected_runtime_s": round(runtime, 2),
        "narration_words": words,
        "narration_seconds_estimate": round(speak_seconds, 1),
        "shots": [],
        "music": storyboard["music"],
        "end_cards": storyboard["end_cards"],
        "exports": storyboard["exports"],
    }

    start = 0.0
    for index, shot in enumerate(shots, start=1):
        script = shot.get("narration")
        duration = shot["duration_s"]
        plan["shots"].append({
            "id": shot["id"],
            "index": index,
            "scene": shot["scene"],
            "mode": shot["mode"],
            "duration_s": duration,
            "estimated_start_s": round(start, 2),
            "prompt": build_prompt(storyboard, shot),
            "seed": shot["keyframe"].get("seed"),
            "characters": shot["keyframe"].get("characters", []),
            "use_reference": shot["keyframe"].get("use_reference", True),
            "narration": script["text"] if script else None,
            "subtitle": script["subtitle"] if script else None,
            "subtitle_lines": wrap_subtitle(script["subtitle"]) if script else [],
            "voice": (script or {}).get("voice", "narrator"),
            "ambience": shot.get("ambience"),
            "reason": shot["reason"],
        })
        start += duration

    for card in plan["end_cards"]:
        plan["shots"].append({
            "id": card["id"],
            "index": len(plan["shots"]) + 1,
            "scene": "card",
            "mode": "card",
            "duration_s": card["duration_s"],
            "estimated_start_s": round(start, 2),
            "lines": card["lines"],
            "narration": None,
            "subtitle": None,
            "subtitle_lines": [],
        })
        start += card["duration_s"]

    plan["estimated_total_s"] = round(start, 2)
    path = out_path("plan.json")
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")

    if args.dry_run:
        print(json.dumps({"shots": len(shots), "runtime_s": plan["estimated_total_s"],
                          "narration_words": words}, ensure_ascii=False))
        return

    write_markdown(storyboard, plan)
    print(f"  {len(shots)} shots, {len(narration_shots)} narrated, {words} words "
          f"(~{speak_seconds:.0f}s speech), projected {plan['estimated_total_s']:.0f}s")
    print(f"  wrote {rel(path)} and outputs/script.md")


def write_markdown(storyboard: dict, plan: dict) -> None:
    lines = [
        f"# {plan['title']} - shooting script",
        "",
        f"_{storyboard['meta']['logline']}_",
        "",
        f"Runtime {plan['estimated_total_s']:.0f}s at {plan['fps']} fps. "
        f"Narration in `{plan['language']}`, subtitles in `{plan['subtitle_language']}`.",
        "",
        "| # | shot | len | mode | start | subtitle |",
        "|---|------|-----|------|-------|----------|",
    ]
    for shot in plan["shots"]:
        subtitle = " / ".join(shot.get("subtitle_lines") or [shot.get("subtitle") or "-"])
        lines.append(
            f"| {shot['index']} | {shot['id']} | {shot['duration_s']}s | {shot['mode']} | "
            f"{shot['estimated_start_s']:.1f}s | {subtitle} |"
        )
    for shot in plan["shots"]:
        if shot.get("prompt"):
            lines += ["", f"## {shot['id']}", "", f"**Why:** {shot['reason']}", "",
                      f"**Prompt** (seed {shot['seed']}):", "", f"> {shot['prompt']}"]
            if shot.get("narration"):
                lines += ["", f"**Amharic:** {shot['narration']}", f"**Subtitles:** {shot['subtitle']}"]
    out_path("script.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    stage_cli("script", run, description="Derive the shooting script from storyboard.json")
