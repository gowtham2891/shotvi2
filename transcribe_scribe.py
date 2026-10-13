import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

try:
    import dotenv
    dotenv.load_dotenv()
except ImportError:
    pass

MODEL = "scribe_v2"
API = "https://api.elevenlabs.io/v1/speech-to-text"
CREDITS_PER_MIN = 66.7
TIMEOUT_PER_MIN_S = 30
MIN_TIMEOUT_S = 600
END_PUNCT = re.compile(r"[.?!।]$")
MAX_GAP_S = 0.70
MAX_WORDS = 30
MAX_SECONDS = 20.0
GHOST_SHARE = 0.02
RECOVER_MIN_S = 300
RECOVER_TOL_S = 0.5
KEYTERMS_SURCHARGE = 1.20   # ElevenLabs: keyterms add 20% to the base transcription cost
MAX_KEYTERMS = 1000


# Tried in this order; later keys are used only after earlier ones run out of credits.
KEY_VARS = ("ELEVENLABS_API_KEY", "ELEVENLABS_API_KEY_BACKUP", "ELEVENLABS_API_KEY_3", "ELEVENLABS_API_KEY_4",
            "ELEVENLABS_API_KEY_5")
_active = 0   # index into api_keys(); moves to the backup when the current key runs out of quota


def api_keys():
    keys = []
    for var in KEY_VARS:
        k = (os.environ.get(var) or "").strip()
        if k and k not in keys:
            keys.append(k)
    if not keys:
        raise SystemExit("ELEVENLABS_API_KEY is not set")
    return keys


def api_key():
    return api_keys()[_active]


def use_next_key(log=print, why="quota exhausted"):
    """Switch to the backup key. Returns False when there is none left."""
    global _active
    if _active + 1 >= len(api_keys()):
        return False
    _active += 1
    log(f"{why} on key #{_active}; switching to {KEY_VARS[_active]}")
    return True


def out_of_quota(r):
    t = r.text.lower()
    return r.status_code in (401, 402, 429) and ("quota" in t or "credit" in t or "limit" in t)


def duration_s(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def to_mp3(src, dst):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".partial.mp3")
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                        "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame",
                        "-q:a", "4", str(tmp)], capture_output=True, text=True)
    if r.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"ffmpeg failed:\n{r.stderr[-400:]}")
    tmp.replace(dst)   # only a complete file ever gets the final name
    return dst


def credits_left(key=None):
    d = requests.get("https://api.elevenlabs.io/v1/user/subscription",
                     headers={"xi-api-key": key or api_key()}, timeout=60).json()
    return d["character_limit"] - d["character_count"], d["character_limit"]


def recover(expect_secs):
    # Matching is by duration only, so it's only safe for long files: short
    # clips often share a length (two Shorts here are 0.1s apart) and are
    # cheap to simply re-upload.
    if expect_secs < RECOVER_MIN_S:
        return None
    h = {"xi-api-key": api_key()}
    try:   # a network error here must not kill the run: the caller simply retries the upload
        lst = (requests.get(f"{API}/transcripts", headers=h, timeout=60)
               .json().get("transcripts") or [])
        for t in lst[:8]:
            d = requests.get(f"{API}/transcripts/{t['id']}", headers=h,
                             timeout=600).json()
            if abs((d.get("audio_duration_secs") or 0) - expect_secs) <= RECOVER_TOL_S:
                return d
    except (requests.RequestException, ValueError):
        return None
    return None


def load_keyterms(path):
    """A JSON file with either a list of strings or a glossary dict (``keyterms`` list, else ``terms[].term``)."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(d, dict):
        d = d.get("keyterms") or [t["term"] for t in d.get("terms", [])]
    out = []
    for t in d:
        t = " ".join(str(t).split())
        if t and t not in out:
            out.append(t)
    bad = [t for t in out if len(t) >= 50 or len(t.split()) > 5 or any(ch in "<>{}[]\\" for ch in t)]
    if bad:
        raise SystemExit(f"keyterms break the API limits (<50 chars, <=5 words, none of <>{{}}[]\\): {bad[:5]}")
    if len(out) > MAX_KEYTERMS:
        raise SystemExit(f"{len(out)} keyterms; the API allows {MAX_KEYTERMS}")
    return out


def diarization_opts(num_speakers=None, diarization_threshold=None):
    """Validated optional diarization fields (ElevenLabs: num_speakers 1-32; diarization_threshold 0.1-0.4,
    only when num_speakers is unset). Empty when neither is given."""
    out = {}
    if num_speakers is not None:
        n = int(num_speakers)
        if not 1 <= n <= 32:
            raise SystemExit(f"num_speakers must be 1-32, got {num_speakers}")
        out["num_speakers"] = n
    if diarization_threshold is not None:
        if num_speakers is not None:
            raise SystemExit("diarization_threshold is only allowed when num_speakers is unset")
        x = float(diarization_threshold)
        if not 0.1 <= x <= 0.4:
            raise SystemExit(f"diarization_threshold must be 0.1-0.4, got {diarization_threshold}")
        out["diarization_threshold"] = x
    return out


def request_fields(language="", keyterms=None, num_speakers=None, diarization_threshold=None):
    """Form fields of the upload. Without keyterms / diarization options this is exactly the original request."""
    fields = {
        "model_id": MODEL,
        "diarize": "true",
        "timestamps_granularity": "word",
        "tag_audio_events": "true",
        "temperature": "0",
        "seed": "0",
    }
    if language:
        fields["language_code"] = language
    if keyterms:
        fields["keyterms"] = list(keyterms)   # requests sends a list as one repeated form part per term
    for k, v in diarization_opts(num_speakers, diarization_threshold).items():
        fields[k] = str(v)
    return fields


def call(mp3, secs, language="", log=print, keyterms=None, num_speakers=None, diarization_threshold=None):
    timeout = max(MIN_TIMEOUT_S, int(secs / 60 * TIMEOUT_PER_MIN_S))
    fields = request_fields(language, keyterms, num_speakers, diarization_threshold)
    # recover() matches past transcripts by duration only: with keyterms or diarization options it would return
    # an earlier transcript of the same audio (made without them) and silently fake the result.
    may_recover = not keyterms and not diarization_opts(num_speakers, diarization_threshold)
    attempt = 0
    while attempt < 3:
        attempt += 1
        try:
            log(f"uploading {Path(mp3).stat().st_size / 1048576:.0f} MB "
                f"(attempt {attempt}, key #{_active + 1}, waiting up to {timeout // 60} min)")
            with open(mp3, "rb") as f:
                r = requests.post(API, headers={"xi-api-key": api_key()},
                                  files={"file": (Path(mp3).name, f, "audio/mpeg")},
                                  data=fields, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if out_of_quota(r):
                try:   # ElevenLabs states the remaining balance in the refusal; log it (free, nothing billed)
                    log(f"key #{_active + 1} refused: {r.json()['detail']['message']}")
                except Exception:
                    log(f"key #{_active + 1} refused: {r.text[:200]}")
            if out_of_quota(r) and use_next_key(log):
                attempt -= 1          # a quota refusal on one key isn't a failed attempt
                continue
            raise SystemExit(f"ElevenLabs returned {r.status_code}:\n{r.text[:500]}")
        except SystemExit:
            raise
        except Exception as e:
            log(f"attempt {attempt} failed: {type(e).__name__}")
            got = recover(secs) if may_recover else None   # re-upload instead of faking (see above)
            if got:
                log("recovered an already-processed transcript")
                return got
            if attempt < 3:
                time.sleep(5)
    raise SystemExit("Upload failed three times and produced nothing.")


def split_entries(raw):
    words, events = [], []
    for e in (raw.get("words") or []):
        if e.get("start") is None or e.get("end") is None:
            continue
        text = (e.get("text") or "").strip()
        if not text:
            continue
        kind = e.get("type")
        row = {"word": text, "start": round(float(e["start"]), 3),
               "end": round(float(e["end"]), 3), "speaker": e.get("speaker_id")}
        if e.get("logprob") is not None:
            row["logprob"] = round(float(e["logprob"]), 3)
        if kind == "audio_event":
            events.append({"text": text, "start": row["start"], "end": row["end"]})
        elif kind in (None, "word"):
            words.append(row)
    return words, events


def build_sentences(words):
    out, cur = [], []

    def flush():
        if not cur:
            return
        out.append({"id": len(out),
                    "text": " ".join(w["word"] for w in cur),
                    "start": cur[0]["start"], "end": cur[-1]["end"],
                    "speaker": cur[0].get("speaker"),
                    "words": [cur[0]["_i"], cur[-1]["_i"]]})
        cur.clear()

    for i, w in enumerate(words):
        w["_i"] = i
        if cur:
            gap = w["start"] - cur[-1]["end"]
            span = w["end"] - cur[0]["start"]
            if (w.get("speaker") != cur[0].get("speaker") or gap >= MAX_GAP_S
                    or len(cur) >= MAX_WORDS or span > MAX_SECONDS):
                flush()
        cur.append(w)
        if END_PUNCT.search(w["word"]):
            flush()
    flush()
    for w in words:
        w.pop("_i", None)
    return out


def real_speakers(words):
    talk = {}
    for w in words:
        sp = w.get("speaker")
        talk[sp] = talk.get(sp, 0.0) + (w["end"] - w["start"])
    total = sum(talk.values()) or 1.0
    real = [sp for sp, t in talk.items()
            if sp is not None and t / total >= GHOST_SHARE]
    return sorted(real, key=lambda s: -talk[s]), talk


def transcribe(audio, language="", log=print, keyterms=None, num_speakers=None, diarization_threshold=None):
    audio = Path(audio)
    diar = diarization_opts(num_speakers, diarization_threshold)
    secs = duration_s(audio)
    mins = secs / 60.0
    need = int(mins * CREDITS_PER_MIN * (KEYTERMS_SURCHARGE if keyterms else 1.0))
    if keyterms:
        log(f"{len(keyterms)} keyterms (+{KEYTERMS_SURCHARGE - 1:.0%} cost); recovery by duration disabled")
    if diar:
        log(f"diarization options {diar}; recovery by duration disabled")
    # Start on the first key that has enough credits. A key that can't report its quota
    # (no user-read permission) is tried anyway; the upload switches keys if it's refused.
    while True:
        try:
            left, limit = credits_left()
            log(f"{mins:.1f} min, needs ~{need:,} credits, key #{_active + 1} has {left:,} of {limit:,}")
            if need <= left:
                break
            if not use_next_key(log, why=f"not enough credits ({left:,} < {need:,})"):
                raise SystemExit(f"Not enough credits on any key: needs ~{need:,}")
        except SystemExit:
            raise
        except Exception as e:
            log(f"could not read quota on key #{_active + 1} ({type(e).__name__}); needs ~{need:,} credits")
            break

    mp3 = audio.with_suffix(".upload.mp3")
    if not mp3.exists():
        log("converting to mp3")
        to_mp3(audio, mp3)

    raw = call(mp3, secs, language=language, log=log, keyterms=keyterms, **diar)
    words, events = split_entries(raw)
    sents = build_sentences(words)
    real, talk = real_speakers(words)
    ghosts = [s for s in talk if s is not None and s not in real]
    log(f"{len(sents):,} sentences, {len(words):,} words, {len(real)} speakers, "
        f"{len(events)} audio events")
    out = {
        "asr_model": f"elevenlabs/{MODEL}",
        "language": raw.get("language_code"),
        "language_probability": raw.get("language_probability"),
        "sentences": sents,
        "word_timestamps": words,
        "audio_events": events,
        "n_speakers": len(real),
        "speakers": real,
        "ghost_speakers": ghosts,
        "total_sentences": len(sents),
        "total_words": len(words),
        "audio_duration_secs": secs,
        "text": raw.get("text", ""),
    }
    if keyterms:
        out["keyterms"] = list(keyterms)
    if diar:
        out["diarization_options"] = diar
    if keyterms or diar:
        out["transcription_id"] = raw.get("transcription_id")
    return out


def save(data, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                          encoding="utf-8")


def start_on_key(var):
    """Start on a named key (e.g. ELEVENLABS_API_KEY_4) instead of the first one."""
    global _active
    k = (os.environ.get(var) or "").strip()
    if not k:
        raise SystemExit(f"{var} is not set")
    _active = api_keys().index(k)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(usage="python transcribe_scribe.py <audio.wav> [out.json] "
                                       "[--keyterms terms.json] [--key ELEVENLABS_API_KEY_4] "
                                       "[--num-speakers N | --diarization-threshold X]")
    ap.add_argument("audio")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--keyterms", default="", help="json: a list of terms, or a glossary with 'keyterms'")
    ap.add_argument("--key", default="", help="env var of the key to start on")
    ap.add_argument("--num-speakers", type=int, default=None, help="ElevenLabs num_speakers (1-32)")
    ap.add_argument("--diarization-threshold", type=float, default=None,
                    help="ElevenLabs diarization_threshold (0.1-0.4; lower = more speakers); not with --num-speakers")
    a = ap.parse_args()
    diar = diarization_opts(a.num_speakers, a.diarization_threshold)
    src = Path(a.audio)
    dst = Path(a.out) if a.out else \
        src.with_name(src.stem.replace("_audio", "") + "_scribe_transcript.json")
    kt = load_keyterms(a.keyterms) if a.keyterms else None
    if (kt or diar) and dst.exists():
        raise SystemExit(f"{dst} exists; a keyterms / diarization-option run never overwrites a transcript")
    if a.key:
        start_on_key(a.key)
    save(transcribe(src, keyterms=kt, **diar), dst)
    print(f"saved {dst}")
