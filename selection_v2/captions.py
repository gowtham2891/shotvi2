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
import re
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
    "highlight_box": None,               # "#RRGGBB[AA]": a box behind the word being spoken; None = off
    "emphasis": None,                    # "#RRGGBB": colour of words marked as keywords in the editor; None = off
    "glow": 0,                           # px: the outline softened into a glow (0 = a sharp outline)
    "animation": "none",                 # none | fade | pop (the spoken word scales up briefly) | reveal (words appear as spoken)
    "uppercase_latin": False,            # English words in capitals (Telugu has no case)
    "script": "telugu",                  # telugu | latin: Telugu words in English letters (ఏంటి -> enti, romanize.py)
    "max_lines": 2,
    "words_per_line": 0,                 # 0 = by width (CHARS_PER_LINE_AT_72 scaled by size); else a fixed count
    "pos_x": 0.5,                        # the caption block's centre, as a fraction of the width
    "pos_y": None,                       # the block's bottom, fraction of the height; None = default for the picture style
}
DEFAULT_POS_Y = {"fit": 0.74, "crop": 0.74}   # founder 2026-11-13: just clear of the Instagram / YouTube buttons (bottom ~22%)

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
    # 2026-11-12 from the research on Reels/Shorts caption styles (word box, karaoke reveal, lime highlight, glow)
    "word_box":   {"label": "Word box: a yellow box behind the spoken word", "font": "HindGuntur-Bold.ttf", "size": 84, "outline": 4, "shadow": 0, "highlight": "#1C2030", "highlight_box": "#FFD400"},
    "kumkum_box": {"label": "Kumkum box: a red box behind the spoken word", "font": "HindGuntur-Bold.ttf", "size": 84, "outline": 4, "shadow": 0, "highlight": "#FFFFFF", "highlight_box": "#E2493B"},
    "lime_pop":   {"label": "Lime pop: the spoken word turns lime green and pops", "size": 86, "outline": 5, "shadow": 2, "highlight": "#7CFF4F", "animation": "pop"},
    "reveal":     {"label": "Appear as spoken: words show up as they are said", "size": 84, "outline": 4, "highlight": "#FFD400", "animation": "reveal"},
    "neon":       {"label": "Neon: white text with a cyan glow", "size": 86, "outline": 4, "outline_colour": "#19E6FF", "glow": 10, "shadow": 0},
    "subtitle":   {"label": "Subtitle: small and calm, on a soft dark box", "font": "NotoSansTelugu-Regular.ttf", "size": 62, "box": "#00000099", "outline": 0, "shadow": 0, "max_lines": 2},
    "keywords":   {"label": "Gold keywords: words you mark in the editor turn gold", "outline": 4, "emphasis": "#FFC93C"},
}


HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
INT_LIMITS = {"size": (36, 160), "outline": (0, 12), "shadow": (0, 12), "max_lines": (1, 3), "words_per_line": (0, 8),
              "glow": (0, 20)}


def clean_settings(raw: dict) -> dict:
    """A full, safe settings bundle from what the web app sends (``ValueError`` saying what is wrong). Only known keys;
    the font must be a file in fonts/; colours must be #RRGGBB or #RRGGBBAA; numbers stay in range. Nothing a person
    types can reach the subtitle file as formatting."""
    if not isinstance(raw, dict):
        raise ValueError("caption settings must be an object")
    unknown = set(raw) - set(DEFAULTS) - {"template", "label"}
    if unknown:
        raise ValueError(f"unknown caption settings: {', '.join(sorted(unknown))}")
    s = dict(DEFAULTS)
    for k, v in raw.items():
        if k in ("template", "label"):
            continue
        if k == "font":
            if not (isinstance(v, str) and v == Path(v).name and v.lower().endswith(".ttf") and (FONTS / v).is_file()):
                raise ValueError(f"unknown font {v!r}")
        elif k in ("colour", "outline_colour"):
            if not (isinstance(v, str) and HEX_COLOUR.match(v)):
                raise ValueError(f"{k} must be a colour like #FFFFFF")
        elif k in ("box", "highlight", "highlight_box", "emphasis"):
            if v is not None and not (isinstance(v, str) and HEX_COLOUR.match(v)):
                raise ValueError(f"{k} must be a colour like #000000B3, or empty")
        elif k == "animation":
            if v not in ("none", "fade", "pop", "reveal"):
                raise ValueError("animation must be none, fade, pop or reveal")
        elif k == "script":
            if v not in ("telugu", "latin"):
                raise ValueError("script must be telugu or latin")
        elif k == "uppercase_latin":
            if not isinstance(v, bool):
                raise ValueError("uppercase_latin must be true or false")
        elif k in INT_LIMITS:
            lo, hi = INT_LIMITS[k]
            if not (isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi):
                raise ValueError(f"{k} must be a whole number from {lo} to {hi}")
        elif k == "pos_x":
            if not (isinstance(v, (int, float)) and not isinstance(v, bool) and 0.05 <= v <= 0.95):
                raise ValueError("pos_x must be between 0.05 and 0.95")
            v = float(v)
        elif k == "pos_y":
            if v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool) and 0.05 <= v <= 0.97):
                raise ValueError("pos_y must be between 0.05 and 0.97, or empty")
            v = None if v is None else float(v)
        s[k] = v
    return s


MAX_WORDS = 4000
MAX_TOKEN_CHARS = 60
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_words(raw, max_s: float) -> list[dict]:
    """Edited caption words from the editor: [{tok, start, end}] on the clip's clock (``ValueError`` saying what is
    wrong). Text 1-60 characters, no control characters; times inside the clip, each word ending after it starts;
    returned in time order."""
    if not isinstance(raw, list) or len(raw) > MAX_WORDS:
        raise ValueError("caption words must be a list (at most 4000)")
    out = []
    for i, w in enumerate(raw):
        if not isinstance(w, dict):
            raise ValueError(f"word {i + 1} is not an object")
        tok = w.get("tok")
        if not isinstance(tok, str) or not tok.strip() or len(tok) > MAX_TOKEN_CHARS or _CONTROL.search(tok):
            raise ValueError(f"word {i + 1}: text must be 1-{MAX_TOKEN_CHARS} characters")
        try:
            a, b = float(w.get("start")), float(w.get("end"))
        except (TypeError, ValueError):
            raise ValueError(f"word {i + 1}: start and end must be numbers")
        if not (0 <= a < b <= max_s + 1.0):
            raise ValueError(f"word {i + 1}: times must be inside the clip")
        emph = w.get("emph", False)
        if not isinstance(emph, bool):
            raise ValueError(f"word {i + 1}: emph must be true or false")
        out.append({"tok": tok.strip(), "start": round(a, 3), "end": round(b, 3), **({"emph": True} if emph else {})})
    return sorted(out, key=lambda w: (w["start"], w["end"]))


def clean_breaks(raw, n_words: int) -> list[int]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(i, int) and not isinstance(i, bool) and 0 < i < n_words for i in raw):
        raise ValueError("line splits must be word positions inside the captions")
    return sorted(set(raw))


def settings_hash(settings: dict, words: list[dict] | None = None, breaks: list[int] | None = None) -> str:
    """Same settings (and same edited words / line splits) -> same name, so an identical render is made once."""
    import hashlib
    clean = {k: settings[k] for k in sorted(DEFAULTS)}
    if words is not None or breaks:
        clean["_words"], clean["_breaks"] = words, breaks or []
    return hashlib.sha256(json.dumps(clean, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


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


def cues(ws: list[dict], s: dict | None = None, breaks: set[int] | list[int] | None = None) -> list[dict]:
    """Group words into caption cues: {start, end, words[]} (clip clock). A new cue at a pause, after a sentence end,
    when the lines are full (by width, or by a fixed words-per-line when set), or before a word index in ``breaks``
    (a line split made in the caption editor). web/src/lib/captionEngine.jsx follows the same rules for the preview."""
    s = s or DEFAULTS
    breaks = set(breaks or ())
    shown = _shown_len(s)     # lines are measured by the text shown (English letters can be longer than Telugu)
    out, cur = [], []
    max_words = (s["words_per_line"] * s["max_lines"]) if s["words_per_line"] else 10 ** 9
    max_chars = chars_per_line(s) * s["max_lines"]

    def close():
        if cur:
            out.append({"words": list(cur)})
            cur.clear()
    for i, w in enumerate(ws):
        if cur and i in breaks:
            close()
        if cur:
            gap = w["start"] - cur[-1]["end"]
            text_len = sum(shown(x["tok"]) + 1 for x in cur) + shown(w["tok"])
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


def _shown_len(s: dict):
    if s.get("script") == "latin":
        from .romanize import romanize
        return lambda t: len(romanize(t))
    return len


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


def _esc(t: str) -> str:
    """Caption text is data, never formatting: ``{`` starts an ASS override block and ``\\`` a code (\\N, \\h), so
    they become look-alike characters (from transcripts today, from people's edited captions later)."""
    return t.replace("\\", "\uFF3C").replace("{", "\uFF5B").replace("}", "\uFF5D")


def _tok(t: str, s: dict) -> str:
    if s.get("script") == "latin":
        from .romanize import romanize
        t = romanize(t)
    return _esc(t.upper() if s["uppercase_latin"] and t.isascii() else t)


def ass_text(cs: list[dict], s: dict, vertical_style: str = "crop", width: int = OUT_W, height: int = OUT_H) -> str:
    """The ASS subtitle file. One event per cue, or one per spoken word when something follows the spoken word
    (highlight colour, box behind the word, pop, reveal). web/src/components/klipvi/CaptionOverlay.jsx draws the same."""
    fam = font_family(FONTS / s["font"])
    pos_y = DEFAULT_POS_Y[vertical_style] if s.get("pos_y") is None else float(s["pos_y"])
    pos = f"\\an2\\pos({width * float(s['pos_x']):.0f},{height * pos_y:.0f})"
    border_style, outline_px, back = (3, 14, ass_colour(s["box"])) if s["box"] else (1, s["outline"], ass_colour("#000000"))
    outline_col = ass_colour(s["box"]) if s["box"] else ass_colour(s["outline_colour"])
    primary = ass_colour(s["colour"])
    word_box = ass_colour(s["highlight_box"]) if s.get("highlight_box") else None
    # the box behind the spoken word: a second style that draws a box behind every letter (BorderStyle 3); a copy of
    # the line on a lower layer shows only the spoken word's boxes (all other letters and boxes fully transparent)
    box_style = (f"Style: WordBox,{fam},{s['size']},&HFF000000,&HFF000000,{word_box},&HFF000000,0,0,0,0,100,100,0,0,3,"
                 f"{max(8, round(s['size'] * 0.14))},0,2,40,40,0,1\n") if word_box else ""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{fam},{s['size']},{primary},{primary},{outline_col},{back},0,0,0,0,100,100,0,0,{border_style},{outline_px},{s['shadow']},2,40,40,0,1
{box_style}
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    glow = f"\\blur{s['glow']}" if s.get("glow") and not s["box"] else ""
    hl = ass_colour(s["highlight"]) if s["highlight"] else None
    em = ass_colour(s["emphasis"] or s["highlight"] or "#FFD400")
    pop = s["animation"] == "pop"
    reveal = s["animation"] == "reveal"
    fade = s["animation"] == "fade"
    per_word = bool(hl or pop or reveal or word_box)
    lines = []

    def tok_text(w, colour_on: str | None = None) -> str:
        t = _tok(w["tok"], s)
        colour = colour_on or (em if w.get("emph") else None)
        return f"{{\\c{colour}&}}{t}{{\\c{primary}&}}" if colour else t

    for c in cs:
        rows = wrap([_tok(w["tok"], s) for w in c["words"]], s)
        n_flat = sum(len(r) for r in rows)
        words = c["words"][:n_flat]
        breaks, n = set(), 0
        for r in rows[:-1]:
            n += len(r); breaks.add(n - 1)
        sep = lambda j: "\\N" if j in breaks else " "
        if not per_word:
            text = "".join(tok_text(w) + sep(j) for j, w in enumerate(words)).rstrip(" ")
            fad = "\\fad(120,120)" if fade else ""
            lines.append(f"Dialogue: 1,{_ts(c['start'])},{_ts(c['end'])},Cap,,0,0,0,,{{{pos}{fad}{glow}}}{text}")
            continue
        # one event per spoken word: the whole cue (same line breaks, so nothing jumps), that word marked
        for i, w in enumerate(words):
            st = c["start"] if i == 0 else w["start"]
            en = c["end"] if i == len(words) - 1 else max(w["start"] + 0.05, words[i + 1]["start"])
            fad = ""
            if fade:     # the cue fades in once and out once (not on every word)
                fad = f"\\fad({120 if i == 0 else 0},{120 if i == len(words) - 1 else 0})"
            parts = []
            for j, x in enumerate(words):
                if reveal and j > i:
                    parts.append(f"{{\\alpha&HFF&}}{_tok(x['tok'], s)}{{\\alpha&H00&}}")
                elif j == i:
                    on = "\\t(0,110,\\fscx112\\fscy112)\\t(110,260,\\fscx100\\fscy100)" if pop else ""
                    colour = hl or (em if x.get("emph") else None)
                    col = ("\\c" + colour + "&") if colour else ""
                    # on its box the spoken word is clean: no outline or shadow (restored after it)
                    plain, back_to = ("\\bord0\\shad0", f"\\bord{outline_px}\\shad{s['shadow']}") if word_box else ("", "")
                    parts.append(f"{{{on}{col}{plain}}}{_tok(x['tok'], s)}{{\\c{primary}&\\fscx100\\fscy100{back_to}}}")
                else:
                    parts.append(tok_text(x))
                parts.append(sep(j))
            lines.append(f"Dialogue: 1,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{{{pos}{fad}{glow}}}{''.join(parts).rstrip(' ')}")
            if word_box:   # layer 0: the box behind word i only
                bparts = []
                for j, x in enumerate(words):
                    alpha = "\\3a&H00&" if j == i else "\\3a&HFF&"
                    bparts.append(f"{{{alpha}}}{_tok(x['tok'], s)}" + sep(j))
                on = "\\t(0,110,\\fscx112\\fscy112)\\t(110,260,\\fscx100\\fscy100)" if pop else ""
                lines.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},WordBox,,0,0,0,,{{{pos}{fad}{on}}}{''.join(bparts).rstrip(' ')}")
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
                    settings: dict | None = None, vertical_style: str = "crop", name: str | None = None,
                    edited_words: list[dict] | None = None, breaks: list[int] | None = None) -> dict:
    """``<out_dir>/<clip>_<vertical style>_cap_<name>.mp4`` with the .ass and the settings (.json) next to it.
    ``settings`` = a full bundle (from the frontend); else ``template`` (+ DEFAULTS). ``edited_words`` (clip clock,
    from the caption editor) replace the transcript's words; ``breaks`` force a new line before those word positions."""
    s = dict(settings) if settings else settings_for(template)
    name = name or s.get("template") or "custom"
    ws = edited_words if edited_words is not None else clip_words(facts, words, clip)
    cs = cues(ws, s, breaks)
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{clip['id']}_{vertical_style}_cap_{name}"
    ass_path = out_dir / f"{stem}.ass"
    ass_path.write_text(ass_text(cs, s, vertical_style), encoding="utf-8")
    dst = burn(Path(vertical_mp4), ass_path, out_dir / f"{stem}.mp4")
    used = {**s, "pos_y": DEFAULT_POS_Y[vertical_style] if s.get("pos_y") is None else s["pos_y"], "vertical_style": vertical_style}
    (out_dir / f"{stem}.json").write_text(json.dumps(used, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"file": str(dst), "ass": str(ass_path), "settings": used, "n_cues": len(cs)}
