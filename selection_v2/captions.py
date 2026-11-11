"""Burned-in Telugu / Tenglish captions for the vertical clips (founder, 2026-11-10).

Like OpusClip / Submagic, captions are a SET OF SETTINGS, not one style: font, size, colours, outline, shadow, box,
word highlight, animation, words per line, position. A TEMPLATE is a saved bundle of settings (``TEMPLATES``: the
library the user starts from; later each user saves their own "brand" bundle). Any bundle renders. The settings used
are saved next to every render so the frontend can reload, edit (drag the position, change the font...) and
re-render. Text = the word-fixed transcript; timing = Scribe's word times (``stage3_find.word_table``).

How: the clip's words in play order (cold open first, cuts left out, on the clip's own clock) are grouped into short
cues (a new cue at a pause, a sentence end or when the lines are full), written as an ASS subtitle file and burned in
with ffmpeg + libass (``ffmpeg-full`` from Homebrew; the plain brew ffmpeg has no text filters). libass shapes Telugu
correctly through HarfBuzz; fonts come from ``fonts/`` (free, OFL / Noto), never from the system.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .config import ROOT
from .stage3_find import word_table
from .vertical import OUT_H, OUT_W, PAD_IN, PAD_OUT

FONTS = ROOT / "fonts"
PAUSE_S = 0.45            # a gap longer than this starts a new cue
TAIL_S = 0.20             # a cue stays this long after its last word
MIN_CUE_S = 0.9
SENT_END = ".?!।"
CHARS_PER_LINE_AT_72 = 20  # Telugu glyphs are wide: about this many characters fit a line at size 72 on 1080 wide

# ----------------------------------------------------------------------------------------------- settings
DEFAULTS: dict = {
    "font": "NotoSansTelugu-Bold.ttf",   # a file in fonts/
    "size": 78,                          # px on a 1920-high frame
    "colour": "#FFFFFF",                 # text
    "outline_colour": "#000000",
    "outline": 3,                        # px; 0 = none
    "shadow": 1,                         # px
    "box": None,                         # "#RRGGBB" or "#RRGGBBAA" (AA = opacity) for a box behind the text; None = no box
    "highlight": None,                   # "#RRGGBB": the word being spoken in this colour; None = off
    "animation": "none",                 # none | fade | pop (the spoken word scales up briefly)
    "uppercase_latin": False,            # English words in capitals (Telugu has no case)
    "max_lines": 2,
    "words_per_line": 0,                 # 0 = by width (CHARS_PER_LINE_AT_72 scaled by size); else a fixed count
    "pos_x": 0.5,                        # the caption block's centre, as a fraction of the width
    "pos_y": None,                       # the block's bottom, fraction of the height; None = default for the picture style
}
DEFAULT_POS_Y = {"fit": 0.80, "crop": 0.84}

# the template library: name -> settings on top of DEFAULTS (+ a label for the picker)
TEMPLATES: dict[str, dict] = {
    "clean":      {"label": "Clean: white, black outline (Noto Sans Telugu Bold)"},
    "highlight":  {"label": "Word highlight: the spoken word turns yellow", "highlight": "#FFD400"},
    "pop":        {"label": "Pop: the spoken word jumps slightly", "animation": "pop", "highlight": "#FFD400"},
    "boxed":      {"label": "Boxed: white on a dark box (Mandali)", "font": "Mandali-Regular.ttf", "size": 76, "box": "#000000B3", "outline": 0, "shadow": 0},
    "yellow":     {"label": "Yellow pop: yellow text, black outline (Ramabhadra)", "font": "Ramabhadra-Regular.ttf", "size": 84, "colour": "#FFD400", "outline": 4},
    "serif":      {"label": "Serif: classic (Noto Serif Telugu Bold)", "font": "NotoSerifTelugu-Bold.ttf", "size": 74, "outline_colour": "#202020", "shadow": 2},
    "bold":       {"label": "Bold shadow (Hind Guntur Bold)", "font": "HindGuntur-Bold.ttf", "size": 80, "outline": 2, "shadow": 4},
    "mandali":    {"label": "Mandali: light, white with soft shadow, fades in", "font": "Mandali-Regular.ttf", "size": 78, "outline": 2, "shadow": 2, "animation": "fade"},
    "mallanna":   {"label": "Mallanna: rounded, white, orange highlight", "font": "Mallanna-Regular.ttf", "size": 80, "highlight": "#FF8A00"},
    "ntr":        {"label": "NTR: bold display, yellow on black box", "font": "NTR-Regular.ttf", "size": 84, "colour": "#FFD400", "box": "#000000CC", "outline": 0, "shadow": 0},
    "timmana":    {"label": "Timmana: friendly, white with green highlight", "font": "Timmana-Regular.ttf", "size": 80, "highlight": "#3DDC84"},
    "tenali":     {"label": "Tenali Ramakrishna: playful, cream text", "font": "TenaliRamakrishna-Regular.ttf", "size": 84, "colour": "#FFF3C4", "outline": 3},
    "lakki":      {"label": "Lakki Reddy: comic, yellow on dark box", "font": "LakkiReddy-Regular.ttf", "size": 86, "colour": "#FFE066", "box": "#1A1A1ACC", "outline": 0, "shadow": 0},
    "hind_pink":  {"label": "Hind Guntur: white with pink highlight, pop", "font": "HindGuntur-Bold.ttf", "size": 80, "highlight": "#FF4FA3", "animation": "pop"},
    "one_line":   {"label": "One line, big: 3 words at a time (Noto Sans Telugu Bold)", "size": 92, "max_lines": 1, "words_per_line": 3, "highlight": "#FFD400"},
}


def settings_for(template: str | None = None, **overrides) -> dict:
    """A full settings bundle: DEFAULTS + a template + overrides (what the user changed)."""
    s = dict(DEFAULTS)
    if template:
        s.update({k: v for k, v in TEMPLATES[template].items() if k != "label"})
        s["template"] = template
    s.update({k: v for k, v in overrides.items() if v is not None})
    return s


def ass_colour(hex_colour: str) -> str:
    """'#RRGGBB' or '#RRGGBBAA' (AA = opacity 00..FF) -> ASS '&HAABBGGRR' (AA = transparency)."""
    h = hex_colour.lstrip("#").upper()
    r, g, b = h[0:2], h[2:4], h[4:6]
    alpha = 255 - int(h[6:8], 16) if len(h) == 8 else 0
    return f"&H{alpha:02X}{b}{g}{r}"


def font_family(path: Path) -> str:
    """The family name libass matches on (read from the TTF)."""
    import freetype
    f = freetype.Face(str(path))
    return (f.family_name or b"").decode("utf-8", "replace") or path.stem


def ffmpeg_with_subtitles() -> str:
    """An ffmpeg that has the ``subtitles`` filter: ffmpeg-full (brew, keg-only) first, else the ffmpeg on PATH."""
    for cand in ("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg", "/usr/local/opt/ffmpeg-full/bin/ffmpeg", shutil.which("ffmpeg") or "ffmpeg"):
        try:
            out = subprocess.run([cand, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=60).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        if " subtitles " in out:
            return cand
    raise RuntimeError("no ffmpeg with the subtitles filter (install ffmpeg-full: brew install ffmpeg-full)")


# ----------------------------------------------------------------------------------------------- words -> cues
def clip_words(facts: dict, words: list[dict], clip: dict) -> list[dict]:
    """The clip's spoken words in play order with times on the CLIP's clock (the pieces as rendered: padded, joined)."""
    by_line: dict[str, list[dict]] = {}
    for w in word_table(facts, words):
        by_line.setdefault(w["line"], []).append(w)
    out, offset = [], 0.0
    for p in clip["pieces"]:
        a, b = max(0.0, float(p["start"]) - PAD_IN), float(p["end"]) + PAD_OUT
        for lid in p["lines"]:
            for w in by_line.get(lid, []):
                if w["end"] <= a or w["start"] >= b:
                    continue
                out.append({"tok": w["tok"], "start": round(offset + max(w["start"], a) - a, 3), "end": round(offset + min(w["end"], b) - a, 3), "line": lid})
        offset += b - a
    return out


def chars_per_line(s: dict) -> int:
    return max(8, int(round(CHARS_PER_LINE_AT_72 * 72 / max(40, s["size"]))))


def cues(ws: list[dict], s: dict | None = None) -> list[dict]:
    """Group words into caption cues: {start, end, words[]} (clip clock). A new cue at a pause, after a sentence end,
    or when the lines are full (by width, or by a fixed words-per-line when set)."""
    s = s or DEFAULTS
    out, cur = [], []
    max_words = (s["words_per_line"] * s["max_lines"]) if s["words_per_line"] else 10 ** 9
    max_chars = chars_per_line(s) * s["max_lines"]

    def close():
        if cur:
            out.append({"words": list(cur)})
            cur.clear()
    for w in ws:
        if cur:
            gap = w["start"] - cur[-1]["end"]
            text_len = sum(len(x["tok"]) + 1 for x in cur) + len(w["tok"])
            if gap > PAUSE_S or cur[-1]["tok"][-1:] in SENT_END and len(cur) >= 2 or len(cur) >= max_words or (not s["words_per_line"] and text_len > max_chars):
                close()
        cur.append(w)
    close()
    for i, c in enumerate(out):
        c["start"] = max(0.0, c["words"][0]["start"] - 0.05)
        end = max(c["words"][-1]["end"] + TAIL_S, c["start"] + MIN_CUE_S)
        if i + 1 < len(out):
            end = min(end, max(c["words"][-1]["end"], out[i + 1]["words"][0]["start"] - 0.05))
        c["end"] = round(end, 3)
        c["start"] = round(c["start"], 3)
    return out


def wrap(toks: list[str], s: dict | None = None) -> list[list[str]]:
    """Up to ``max_lines`` balanced lines of words (or fixed words per line when set)."""
    s = s or DEFAULTS
    if s["words_per_line"]:
        n = s["words_per_line"]
        return [toks[i:i + n] for i in range(0, len(toks), n)][: s["max_lines"]] or [toks]
    if s["max_lines"] == 1 or len(toks) == 1 or sum(len(t) + 1 for t in toks) <= chars_per_line(s):
        return [toks]
    total = sum(len(t) + 1 for t in toks)
    best, best_diff = 1, 10 ** 9
    for k in range(1, len(toks)):
        a = sum(len(t) + 1 for t in toks[:k]); b = total - a
        if abs(a - b) < best_diff:
            best, best_diff = k, abs(a - b)
    return [toks[:best], toks[best:]]


# ----------------------------------------------------------------------------------------------- ASS
def _ts(t: float) -> str:
    h, r = divmod(max(0.0, t), 3600); m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _tok(t: str, s: dict) -> str:
    return t.upper() if s["uppercase_latin"] and t.isascii() else t


def ass_text(cs: list[dict], s: dict, vertical_style: str = "crop", width: int = OUT_W, height: int = OUT_H) -> str:
    fam = font_family(FONTS / s["font"])
    pos_y = DEFAULT_POS_Y[vertical_style] if s.get("pos_y") is None else float(s["pos_y"])
    pos = f"\\an2\\pos({width * float(s['pos_x']):.0f},{height * pos_y:.0f})"
    border_style, outline_px, back = (3, 14, ass_colour(s["box"])) if s["box"] else (1, s["outline"], ass_colour("#000000"))
    outline_col = ass_colour(s["box"]) if s["box"] else ass_colour(s["outline_colour"])
    primary = ass_colour(s["colour"])
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{fam},{s['size']},{primary},{primary},{outline_col},{back},0,0,0,0,100,100,0,0,{border_style},{outline_px},{s['shadow']},2,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    fade = "\\fad(120,120)" if s["animation"] == "fade" else ""
    hl = ass_colour(s["highlight"]) if s["highlight"] else None
    pop = s["animation"] == "pop"
    lines = []
    for c in cs:
        rows = wrap([_tok(w["tok"], s) for w in c["words"]], s)
        flat = [t for r in rows for t in r]
        breaks, n = set(), 0
        for r in rows[:-1]:
            n += len(r); breaks.add(n - 1)
        if not hl and not pop:
            text = "\\N".join(" ".join(r) for r in rows)
            lines.append(f"Dialogue: 0,{_ts(c['start'])},{_ts(c['end'])},Cap,,0,0,0,,{{{pos}{fade}}}{text}")
            continue
        # per spoken word: the whole cue text with that word coloured and/or popped (same line breaks, so no jumping)
        words = c["words"][: len(flat)]
        for i, w in enumerate(words):
            st = c["start"] if i == 0 else w["start"]
            en = c["end"] if i == len(words) - 1 else max(w["start"] + 0.05, words[i + 1]["start"])
            parts = []
            for j, t in enumerate(flat):
                if j == i:
                    on = (f"\\c{hl}&" if hl else "") + ("\\t(0,110,\\fscx112\\fscy112)\\t(110,260,\\fscx100\\fscy100)" if pop else "")
                    parts.append(f"{{{on}}}{t}{{\\c{primary}&\\fscx100\\fscy100}}")
                else:
                    parts.append(t)
                parts.append("\\N" if j in breaks else " ")
            lines.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{{{pos}{fade}}}{''.join(parts).strip()}")
    return head + "\n".join(lines) + "\n"


def burn(src: Path, ass_path: Path, dst: Path) -> Path:
    ff = ffmpeg_with_subtitles()
    dst.parent.mkdir(parents=True, exist_ok=True)
    rel_ass = Path(ass_path).resolve().relative_to(ROOT).as_posix()     # the subtitles filter chokes on ':' in absolute paths
    rel_fonts = FONTS.relative_to(ROOT).as_posix()
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(src), "-vf", f"subtitles={rel_ass}:fontsdir={rel_fonts}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "copy", "-movflags", "+faststart", str(dst)],
                   check=True, cwd=ROOT)
    return dst


def render_captions(facts: dict, words: list[dict], clip: dict, vertical_mp4: Path, out_dir: Path, template: str | None = None,
                    settings: dict | None = None, vertical_style: str = "crop", name: str | None = None) -> dict:
    """``<out_dir>/<clip>_<vertical style>_cap_<name>.mp4`` with the .ass and the settings (.json) next to it.
    ``settings`` = a full bundle (from the frontend); else ``template`` (+ DEFAULTS)."""
    s = dict(settings) if settings else settings_for(template)
    name = name or s.get("template") or "custom"
    cs = cues(clip_words(facts, words, clip), s)
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{clip['id']}_{vertical_style}_cap_{name}"
    ass_path = out_dir / f"{stem}.ass"
    ass_path.write_text(ass_text(cs, s, vertical_style), encoding="utf-8")
    dst = burn(Path(vertical_mp4), ass_path, out_dir / f"{stem}.mp4")
    used = {**s, "pos_y": DEFAULT_POS_Y[vertical_style] if s.get("pos_y") is None else s["pos_y"], "vertical_style": vertical_style}
    (out_dir / f"{stem}.json").write_text(json.dumps(used, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"file": str(dst), "ass": str(ass_path), "settings": used, "n_cues": len(cs)}
