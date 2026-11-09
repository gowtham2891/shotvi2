"""Box A (founder, 2026-11-06): the episode video stored ONCE at Google (a Gemini explicit cache holding only the video),
made by the video step (step 5) and read by the video step and the scene notes (step 6) at a tenth of the input price.

Facts from the founder's bill for 2026-11-05: making a box is free; storage is billed for the FULL time set (deleting
early does not refund), so the box is never deleted, it expires. Time kept: ``video_box.ttl_s`` (founder: 30 min).

Gemini rule: a call that reads a box cannot carry its own system instruction, so with a box the step's system text is
sent as the first text part, word for word (``request``). When the box is missing, expired, for another video or
could not be made, the step sends the video as before (nothing is lost but the saving).

The box's details live in ``<run_dir>/video_box.json`` so the next step (another process) can find it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from shotvi.llm.base import ModelSpec

BOX_FILE = "video_box.json"
LEAD = "The video above is the whole episode; its clock matches the line times below."


def enabled(cfg: dict) -> bool:
    return bool((cfg.get("video_box") or {}).get("enabled"))


def _path(run_dir) -> Path:
    return Path(run_dir) / BOX_FILE


def load(run_dir, media_key: str | None, model: str, cfg: dict) -> dict | None:
    """The box made earlier for this same video and model, when it still has ``min_left_s`` to live; else None."""
    p = _path(run_dir)
    if not p.exists() or not media_key:
        return None
    try:
        info = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    left = float(info.get("expire_at", 0)) - time.time()
    if info.get("media_key") != media_key or info.get("model") != model or left < float((cfg.get("video_box") or {}).get("min_left_s", 120)):
        return None
    return info


def make(run_dir, spec: ModelSpec, video_part: dict, media_key: str | None, cfg: dict, log=None, provider=None) -> dict | None:
    """Store the video (nothing else) for ``video_box.ttl_s`` seconds. None when Google refuses (the step then sends
    the video as before)."""
    from shotvi.llm.base import CacheUnavailable
    log = log or (lambda *a: None)
    if not media_key:
        return None
    if provider is None:
        from shotvi.llm.client import get_provider
        provider = get_provider(spec.provider)
    ttl = int((cfg.get("video_box") or {}).get("ttl_s", 1800))
    try:
        r = provider.create_cache(ModelSpec(spec.provider, spec.model, {}), "", LEAD, ttl,
                                  display_name=f"shotvi video box {media_key[:8]}", media=[video_part])
    except CacheUnavailable as e:
        log(f"video box: Google did not store the video ({e}); sending it as before")
        return None
    info = {"name": r["name"], "key": f"videobox|{spec.model}|{media_key}", "media_key": media_key, "model": spec.model,
            "tokens": r.get("tokens"), "ttl_s": ttl, "made_at": time.time(), "expire_at": time.time() + ttl - 30,
            "storage_usd": r.get("storage_usd")}
    _path(run_dir).write_text(json.dumps(info, indent=1), encoding="utf-8")
    log(f"video box: the episode video is stored at Google ({info['tokens']} tokens, kept {ttl // 60} min)")
    return info


def request(system: str, video_part: dict, text: str, box: dict | None) -> dict:
    """What the step sends: with a box, no system instruction (Gemini rule) and the system text first in the text;
    without, exactly as before. Keys: system, media, cached_content."""
    if box:
        return {"system": "", "media": [{"text": system}, {"text": text}], "cached_content": {"name": box["name"], "key": box["key"]}}
    return {"system": system, "media": [video_part, {"text": LEAD + "\n\n" + text}], "cached_content": None}


def spec_for(spec: ModelSpec, box: dict | None) -> ModelSpec:
    """With a box the video's resolution was fixed when it was stored, so the call does not set it again."""
    if not box or "media_resolution" not in spec.params:
        return spec
    return ModelSpec(spec.provider, spec.model, {k: v for k, v in spec.params.items() if k != "media_resolution"})
