"""Score a naming run against the answer key data/speakers/<vid>.json (founder, 2026-11-02). Three answers are scored
side by side on the key's checked lines:

  text    Stage 1B's person per line (derived.line_speakers_text / the naming check's base)
  video   the video step's first voice per line (identity_pass.lines[].talk[0]), in its own keys
  final   after the naming check (derived.line_speakers)

  right person  pipeline keys mapped one-to-one onto the key's characters (eval.speakers.best_one_to_one), then agreement:
                "did it keep the people apart", whatever it called them
  right name    the name it gave matches the key's character (speaker_accuracy.match, aliases included)
  on screen     on lines where the key records who is visible (on_screen): the exact set, and person-level
                precision / recall, with keys mapped by the same one-to-one map
  two voices    on lines where the key has `voices` (two voices or a group): the parts match in order; plus lines the
                pipeline split that the key has as one voice

Usage: python -m selection_v2.eval.naming.score <facts.json> <vid> [...]   (prints one block per file)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from selection_v2 import speaker_accuracy as SA


def key_items(facts: dict, vid: str, count_partly: bool = False) -> tuple[list[dict], list[dict], dict]:
    """Key labels matched to the transcript line they overlap most: [{lid, speaker, offscreen, on_screen, voices}]."""
    kp = SA.key_path(vid)
    raw = json.loads(kp.read_text(encoding="utf-8"))
    key = SA.ES.load_key(kp)
    chars = [c for c in raw.get("characters") or [] if isinstance(c, dict)]
    aliases = {}
    if SA.ALIASES.exists():
        a = json.loads(SA.ALIASES.read_text(encoding="utf-8"))
        aliases = a.get(vid) or {}
    speech = [r for r in facts["lines"] if r["kind"] == "speech" and not r.get("zero_len")]
    out = []
    for l, rl in zip(key["lines"], raw["labels"]):
        if l["unknown"]:
            continue
        best, bo = None, 0.0
        for r in speech:
            ov = min(r["end"], l["end"]) - max(r["start"], l["start"])
            if ov > bo:
                best, bo = r, ov
        if best is not None and bo > 0:
            out.append({"lid": best["id"], "speaker": l["speaker"], "offscreen": l["offscreen"], "on_screen": (None if rl.get("on_screen") is None else list(dict.fromkeys(rl["on_screen"] + (rl.get("on_screen_partly") or [] if count_partly else [])))),
                        "voices": rl.get("voices"), "text": best["text"]})
    return out, chars, aliases


def systems(facts: dict) -> dict[str, dict]:
    """name -> {line: key, names: key -> (name, role), voices: line -> [[keys], ...], on_screen: line -> [keys]}."""
    ep = facts.get("episode_pass") or {}
    d = ep.get("derived") or {}
    nc = facts.get("naming_check") or facts.get("speakers_from_video") or {}     # whichever wrote the final map
    base = nc.get("base") or {}
    fixn = {c["key"]: (c.get("name"), c.get("desc")) for c in facts["episode"].get("speakers") or []}
    out = {}
    # the text step's map holds only the lines it reassigned (others keep the speaker-fix key): an EMPTY map is valid,
    # so pick by presence, never by truthiness
    text_map = base["line_speakers"] if "line_speakers" in base else d["line_speakers_text"] if "line_speakers_text" in d else d.get("line_speakers") or {}
    tn = {s["key"]: (s.get("name"), s.get("role")) for s in (base.get("speakers") or ep.get("speakers") or [])}
    out["text"] = {"line": {r["id"]: text_map.get(r["id"]) or r.get("speaker_key") for r in facts["lines"] if r["kind"] == "speech"},
                   "names": {**fixn, **tn}}
    idp = facts.get("identity_pass") or {}
    if isinstance(idp.get("lines"), dict):
        vn = {p["key"]: (p.get("name"), p.get("role")) for p in idp.get("people") or []}
        out["video"] = {"line": {lid: (e["talk"] or [None])[0] for lid, e in idp["lines"].items()},
                        "names": {**out["text"]["names"], **vn},
                        "voices": {lid: [e["talk"]] + [t["talk"] for t in e["turns"]] for lid, e in idp["lines"].items()},
                        "on_screen": {lid: e["see"] for lid, e in idp["lines"].items()}}
    if nc:
        fn = {s["key"]: (s.get("name"), s.get("role")) for s in ep.get("speakers") or []}
        out["final"] = {"line": dict(d.get("line_speakers") or {}), "names": {**fixn, **fn},
                        "voices": {lid: [p["keys"] for p in ps] for lid, ps in (d.get("line_voices") or {}).items()},
                        "on_screen": dict(d.get("on_screen") or {})}
    return out


def score_system(sysd: dict, items: list[dict], chars: list[dict], aliases: dict) -> dict:
    pairs = [(sysd["line"].get(it["lid"]), it["speaker"]) for it in items]
    m = SA.ES.best_one_to_one(pairs)
    person = sum(1 for p, t in pairs if m.get(p) == t)
    name = sum(1 for it in items if SA.match(sysd["names"].get(sysd["line"].get(it["lid"])), None, chars, aliases) == it["speaker"])
    res = {"n": len(items), "right_person": person, "right_name": name, "map": m,
           "wrong_person": [{"line": it["lid"], "true": it["speaker"], "got": m.get(sysd["line"].get(it["lid"]), sysd["line"].get(it["lid"]))}
                            for (p, t), it in zip(pairs, items) if m.get(p) != t]}
    if "on_screen" in sysd:
        ons = [it for it in items if it.get("on_screen") is not None and it["lid"] in sysd["on_screen"]]
        exact = tp = fp = fn = 0
        spk_ok = 0
        for it in ons:
            truth = set(it["on_screen"])
            got = {m.get(k, f"other:{k}") for k in sysd["on_screen"][it["lid"]]}
            exact += got == truth
            tp += len(got & truth); fp += len(got - truth); fn += len(truth - got)
            spk_ok += ((it["speaker"] in truth) == (m.get(sysd["line"].get(it["lid"])) in got))
        res["on_screen"] = {"n": len(ons), "exact": exact, "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                            "recall": round(tp / (tp + fn), 3) if tp + fn else None, "speaker_shown_right": spk_ok}
    if "voices" in sysd:
        multi = [it for it in items if it.get("voices")]
        ok = 0
        for it in multi:
            truth = [sorted(v["speakers"]) for v in it["voices"]]
            got = [sorted(m.get(k, f"other:{k}") for k in ks) for ks in sysd["voices"].get(it["lid"]) or [[sysd["line"].get(it["lid"])]]]
            ok += got == truth
        single = [it for it in items if not it.get("voices")]
        false_split = sum(1 for it in single if len(sysd["voices"].get(it["lid"]) or [[0]]) > 1 or len((sysd["voices"].get(it["lid"]) or [[0]])[0]) > 1)
        res["two_voices"] = {"n": len(multi), "right": ok, "false_splits": false_split}
    return res


def score(facts: dict, vid: str, count_partly: bool = False) -> dict:
    """count_partly: people the key has as seen only from behind / partly (on_screen_partly) count as on screen."""
    items, chars, aliases = key_items(facts, vid, count_partly)
    return {name: score_system(s, items, chars, aliases) for name, s in systems(facts).items()}


def fmt(vid: str, res: dict) -> str:
    out = []
    for name, r in res.items():
        line = f"{vid} {name:6s} right person {r['right_person']}/{r['n']} ({r['right_person'] / r['n']:.0%}) · right name {r['right_name']}/{r['n']} ({r['right_name'] / r['n']:.0%})"
        o = r.get("on_screen")
        if o and o["n"]:
            line += f" · on screen exact {o['exact']}/{o['n']}, precision {o['precision']}, recall {o['recall']}, speaker-shown right {o['speaker_shown_right']}/{o['n']}"
        v = r.get("two_voices")
        if v is not None:
            line += f" · two-voice lines {v['right']}/{v['n']}, false splits {v['false_splits']}"
        out.append(line)
    return "\n".join(out)


if __name__ == "__main__":
    args = sys.argv[1:]
    for p, vid in zip(args[::2], args[1::2]):
        f = json.loads(Path(p).read_text(encoding="utf-8"))
        print(fmt(vid, score(f, vid)))
