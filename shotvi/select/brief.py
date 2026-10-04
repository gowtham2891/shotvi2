"""Episode brief: one cheap call per episode (prompt ``brief``) that gives scorer and ranker episode-wide memory:
who the guest and host are, the main topics with time ranges, key names, recurring stories, what the guest is known
for. Used ONLY in the scorer and ranker prompts (tighten/joins inputs stay unchanged, so their cache stays valid).
"""
from __future__ import annotations

from shotvi.llm import ModelSpec

from .prompts import load_prompt

SCHEMA = {
    "type": "object",
    "properties": {
        "guest": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
                  "required": ["name", "role"]},
        "host": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
                 "required": ["name", "role"]},
        "topics": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}},
            "required": ["label", "start", "end"]}},
        "key_names": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "what": {"type": "string"}}, "required": ["name", "what"]}},
        "recurring": {"type": "array", "items": {"type": "string"}},
        "known_for": {"type": "string"},
    },
    "required": ["guest", "host", "topics", "key_names", "recurring", "known_for"],
}


def build_brief(ctx, cfg, llm) -> dict:
    spec = cfg["models"].get("brief")
    if not spec:
        return {}
    p = load_prompt("brief")
    full = ctx.seg.render(legend=True)
    legend, _, body = full.partition("\n\n")
    return llm.generate_json("brief", ModelSpec.from_dict(spec), p.system,
                             p.render(legend=legend, transcript=body), SCHEMA, meta={"video": ctx.video_id})


def brief_text(b: dict) -> str:
    """Compact text block for scorer / ranker prompts."""
    if not b:
        return "(no episode brief)"
    g, h = b.get("guest", {}), b.get("host", {})
    lines = [f"Guest: {g.get('name', '?')} ({g.get('role', '?')}). Host: {h.get('name', '?')} ({h.get('role', '?')})."]
    if b.get("known_for"):
        lines.append(f"Known for: {b['known_for']}")
    if b.get("topics"):
        lines.append("Topics: " + "; ".join(f"{t['start']}-{t['end']} {t['label']}" for t in b["topics"][:10]))
    if b.get("key_names"):
        lines.append("Key names: " + "; ".join(f"{k['name']} ({k['what']})" for k in b["key_names"][:20]))
    if b.get("recurring"):
        lines.append("Recurring stories / jokes: " + "; ".join(b["recurring"][:8]))
    return "\n".join(lines)
