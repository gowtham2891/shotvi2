"""Synthetic Scribe-like transcripts for tests."""
from __future__ import annotations

WORD_S = 0.3
INTRA_GAP = 0.05


def build(segments, events=(), logprob=-0.05, duration=None, drop=(), **extra) -> dict:
    """segments: list of (speaker, text, gap_before_s). Words are 0.3s long,
    0.05s apart inside a segment. ``drop`` removes keys from every word
    (e.g. ("logprob",)). Returns a Scribe transcript dict."""
    words = []
    t = 0.0
    for spk, text, gap in segments:
        t += gap
        for k, w in enumerate(text.split()):
            if k:
                t += INTRA_GAP
            row = {"word": w, "start": round(t, 3), "end": round(t + WORD_S, 3), "speaker": spk,
                   "logprob": logprob}
            for key in drop:
                row.pop(key, None)
            words.append(row)
            t += WORD_S
    d = {
        "word_timestamps": words,
        "audio_events": [{"text": e[0], "start": e[1], "end": e[2]} for e in events],
        "audio_duration_secs": duration if duration is not None else t + 1.0,
        "language": "tel",
    }
    d.update(extra)
    return d


def filler(n: int, prefix: str = "w") -> str:
    """n distinct-looking neutral words."""
    return " ".join(f"{prefix}{i}" for i in range(n))


def sentence_block(n_sent: int, words_per: int = 8, prefix: str = "s") -> list[str]:
    """Sentences ending with a period."""
    out = []
    for k in range(n_sent):
        ws = [f"{prefix}{k}x{i}" for i in range(words_per)]
        ws[-1] += "."
        out.append(" ".join(ws))
    return out


def monologue(spk: str, n_sent: int, pause: float = 1.2, words_per: int = 8, prefix: str = "s"):
    """Segments for one speaker: sentences separated by `pause` seconds."""
    return [(spk, s, pause if k else 0.0) for k, s in enumerate(sentence_block(n_sent, words_per, prefix))]
