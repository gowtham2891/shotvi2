"""Map each creator Short onto the episode's word indices.

usage: python -m eval.map_shorts scribe_dataset/<video_id> [--out-dir data/anchors]

Input:  scribe_dataset/<vid>/<vid>_scribe_transcript.json
        scribe_dataset/<vid>/creator_shorts/<short_id>_scribe_transcript.json
Output: data/anchors/<vid>.json         (machine-readable mapping, one entry per Short)
        data/anchors/<vid>_review.md    (side-by-side review for humans)

The Short and the episode were transcribed separately from the same audio, so the words do not match
exactly. Text similarity alone is ambiguous (Shorts repeat phrases, episodes have cold-open teasers that
duplicate later content), and timing alone cannot say which words exist. So both must agree:

1. Region: 3-word seeds (exact or phonetic-key match) are clustered in episode time. The cluster covering
   the most Short words is the source region. Other clusters are used only if they explain Short words the
   primary cluster does not (this rejects teaser duplicates but keeps genuinely far-away pieces).
2. Chain: a Viterbi pass over Short words. States are fuzzy episode candidates inside the region(s).
   Transitions are scored by timing: inside one continuous piece (episode time - Short time) is constant
   (measured jitter ~0.05s); a step in that offset with adjacent episode words is a pause trim; a step with
   skipped words is a cut; anything else (backward, far) is a jump with a fixed penalty.
3. Gap fill: between chained words, leftover Short words are aligned to leftover episode words with a
   fuzzy, merge/split-aware alignment, accepted only where timing agrees with the neighbouring piece.
4. Pieces: episode words between two consecutive matched words are classified by timing: kept if they fit
   inside the Short's audio at that point (same offset), cut otherwise. Pieces are maximal runs of kept
   episode words. Short words that match nothing are reported, never forced.
"""
import argparse
import bisect
import json
import re
import sys
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

try:
    from eval.schema_check import SKIP_S   # single source for the skip threshold (spec 0.2 S4)
except ImportError:  # pragma: no cover - run outside the repo
    SKIP_S = 30.0

# ---- tunables (generic, not per-podcast) -------------------------------------------------------------
SEED_N = 3              # words per seed n-gram
CLUSTER_GAP_S = 60.0    # seeds further apart than this in episode time start a new cluster
REGION_MARGIN_S = 40.0  # search margin around each seed cluster
MIN_NEW_POSITIONS = 6   # a secondary cluster must explain this many Short words the chosen ones do not
MIN_NEW_SEEDS = 2       # ... from at least this many 3-gram seeds (a single chance 3-gram is not evidence)
RIVAL_SHARE = 0.9       # a copy explaining >= 90% as many Short words is a rival: prefer body over teaser, flag
CAND_SIM = 0.60         # min similarity for a Viterbi candidate
FILL_SIM = 0.45         # min similarity inside a timing-backed gap fill (same continuous audio)
FILL_SIM_EDGE = 0.60    # min similarity when extending a piece across a cut edge (weaker timing evidence)
TOL_CONT = 0.20         # |offset change| (s) still treated as the same continuous audio (jitter ~0.05)
TOL_FIT = 0.12          # slack when deciding whether an episode word fits inside the Short's audio
MAX_SKIP = 10           # max consecutive unmatched Short words between chained words
SKIP_PEN = 0.30         # cost of an unmatched Short word (fillers: SKIP_PEN_FILLER)
SKIP_PEN_FILLER = 0.10
TRIM_PEN = 0.10         # next episode word, offset jumped forward (pause trimmed, no words removed)
CUT_PEN = 1.50          # forward cut (episode words removed), source gap <= CUT_MAX_GAP_S
CUT_DIST_PEN = 0.30     # + this * min(1, source gap / CUT_MAX_GAP_S): nearer cuts slightly preferred
CUT_MAX_GAP_S = 90.0
SMALL_CUT_PEN = 1.00    # forward cut of <= SMALL_CUT_WORDS words and <= SMALL_CUT_S of source
SMALL_CUT_WORDS = 3
SMALL_CUT_S = 3.0
CUT_TOPK = 12           # forward cuts are evaluated from the K best states of each recent Short word
JUMP_PEN = 2.50         # any other transition: backward (reorder) or far (new piece)
JUMP_PROX_PEN = 0.30    # tie-break: prefer jumps close to the previous piece
MIN_COVERAGE_TO_WRITE = 0.60  # below this a Short is reported as not found (wrong-episode control: <= 0.30)
EDGE_TOL_S = 0.35       # timing slack for words at cut edges (partially transcribed)

FILLERS = {"uh", "um", "umm", "uhm", "hmm", "hm", "ah", "oh", "mm", "mhm", "er",
           "ఉమ్", "మ్", "ఆ", "అ", "ఊ", "హ్మ్", "హా", "ఓ", "ఉ", "అం", "ఆం", "ఏ", "హ"}

# ---- normalisation and similarity --------------------------------------------------------------------

_TE_CONS = {
    "క": "k", "ఖ": "k", "గ": "g", "ఘ": "g", "ఙ": "n", "చ": "C", "ఛ": "C", "జ": "j", "ఝ": "j",
    "ఞ": "n", "ట": "t", "ఠ": "t", "డ": "d", "ఢ": "d", "ణ": "n", "త": "t", "థ": "t", "ద": "d",
    "ధ": "d", "న": "n", "ప": "p", "ఫ": "f", "బ": "b", "భ": "b", "మ": "m", "య": "y", "ర": "r",
    "ఱ": "r", "ల": "l", "ళ": "l", "ఴ": "l", "వ": "v", "శ": "s", "ష": "s", "స": "s", "హ": "h",
}
_TE_VOW = {"అ": "a", "ఆ": "a", "ఇ": "i", "ఈ": "i", "ఉ": "u", "ఊ": "u", "ఋ": "ru", "ఎ": "e",
           "ఏ": "e", "ఐ": "ai", "ఒ": "o", "ఓ": "o", "ఔ": "au"}
_TE_SIGN = {"ా": "a", "ి": "i", "ీ": "i", "ు": "u", "ూ": "u", "ృ": "ru", "ౄ": "ru", "ె": "e",
            "ే": "e", "ై": "ai", "ొ": "o", "ో": "o", "ౌ": "au"}
_VIRAMA = "్"


def norm(w):
    """NFC, lowercase, drop punctuation/symbols/format chars (ZWJ/ZWNJ)."""
    w = unicodedata.normalize("NFC", w).lower()
    return "".join(ch for ch in w if not unicodedata.category(ch).startswith(("P", "S", "C", "Z")))


def is_telugu(s):
    return any("ఀ" <= ch <= "౿" for ch in s)


def romanize(s):
    """Rough Telugu -> Latin transliteration so Telugu-script English ('అండ్') can meet 'and'."""
    out = []
    n = len(s)
    for k, ch in enumerate(s):
        if ch in _TE_CONS:
            out.append(_TE_CONS[ch])
            nxt = s[k + 1] if k + 1 < n else ""
            if nxt not in _TE_SIGN and nxt != _VIRAMA:
                out.append("a")
        elif ch in _TE_VOW:
            out.append(_TE_VOW[ch])
        elif ch in _TE_SIGN:
            out.append(_TE_SIGN[ch])
        elif ch in ("ం", "ఁ", "ఀ"):
            out.append("m" if k == n - 1 else "n")
        elif ch == "ః":
            out.append("h")
        elif ch == _VIRAMA or "ఀ" <= ch <= "౿":
            continue
        else:
            out.append(ch)
    return "".join(out)


def phon(s):
    """Coarse phonetic key shared by Telugu and Latin spellings."""
    r = romanize(s)
    r = (r.replace("ph", "f").replace("sh", "s").replace("ch", "C").replace("th", "t")
          .replace("dh", "d").replace("kh", "k").replace("gh", "g").replace("bh", "b")
          .replace("ck", "k"))
    r = re.sub(r"c(?=[eiy])", "s", r)
    r = (r.replace("c", "k").replace("C", "c").replace("q", "k").replace("x", "ks")
          .replace("w", "v").replace("z", "j").replace("ee", "i").replace("oo", "u"))
    r = re.sub(r"[ae]y(?![aeiou])", "e", r)          # okay/they -> oke/the (Telugu ఓకే)
    r = re.sub(r"(?<=[^aeiou])y", "i", r)
    r = re.sub(r"(.)\1+", r"\1", r)
    if len(r) > 3 and r.endswith("e") and not is_telugu(s):
        r = r[:-1]
    return r


class Sim:
    """Cached fuzzy word similarity in [0, 1]."""

    def __init__(self):
        self.cache = {}
        self.phon_cache = {}

    def _phon(self, s):
        p = self.phon_cache.get(s)
        if p is None:
            p = self.phon_cache[s] = phon(s)
        return p

    def __call__(self, a, b):
        """a, b are normalised words."""
        if a == b:
            return 1.0 if a else 0.0
        if not a or not b:
            return 0.0
        key = (a, b) if a < b else (b, a)
        v = self.cache.get(key)
        if v is None:
            v = self.cache[key] = self._compute(a, b)
        return v

    def _compute(self, a, b):
        la, lb = len(a), len(b)
        best = 0.0
        if 2 * min(la, lb) / (la + lb) >= 0.4:
            best = SequenceMatcher(None, a, b, autojunk=False).ratio()
        pa, pb = self._phon(a), self._phon(b)
        if pa and pb:
            if pa == pb:
                best = max(best, 0.95)
            elif 2 * min(len(pa), len(pb)) / (len(pa) + len(pb)) >= 0.4:
                best = max(best, 0.97 * SequenceMatcher(None, pa, pb, autojunk=False).ratio())
            # Cross-script (Telugu-script English vs Latin): English vowels map loosely onto Telugu ones
            # (of/ఆఫ్, but/బట్, actors/యాక్టర్స్), so also compare with every vowel run collapsed to one class.
            if is_telugu(a) != is_telugu(b):
                va, vb = re.sub(r"[aeiou]+", "a", pa), re.sub(r"[aeiou]+", "a", pb)
                if min(len(va), len(vb)) >= 4:
                    best = max(best, 0.93 * SequenceMatcher(None, va, vb, autojunk=False).ratio())
                elif va == vb and len(va) >= 2:     # short words: only an exact vowel-class match counts
                    best = max(best, 0.93)
        return best

    def partial(self, a, b):
        """Similarity allowing one side to be a truncated/stuttered form of the other (cut edges)."""
        s = self(a, b)
        short, long_ = (a, b) if len(a) <= len(b) else (b, a)
        if len(short) >= 2 and (long_.startswith(short) or long_.endswith(short)):
            s = max(s, 0.6)
        return s


_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "         "sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _num_words(n):
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + _num_words(n % 100))
    return str(n)


def expand_numbers(text):
    """Scribe writes numbers as digits in one transcript and words in the other ('1:32కో' vs 'one thirty
    two కో'), so compare in words."""
    return re.sub(r"\d+", lambda m: " " + _num_words(int(m.group())) + " ", text)


def has_digit(w):
    return any(ch.isdigit() for ch in w["word"])


def load_words(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    words = d["word_timestamps"]
    for w in words:
        w["n"] = norm(expand_numbers(w["word"]))
        # stutter "దె-దెబ్బ": also compare using the last hyphen part
        parts = [p for p in re.split(r"[-‐–]", unicodedata.normalize("NFC", w["word"]).lower()) if p]
        w["n_alt"] = norm(parts[-1]) if len(parts) > 1 else None
    return words, d


def wsim(sim, a, b):
    s = sim(a["n"], b["n"])
    if a.get("n_alt"):
        s = max(s, sim(a["n_alt"], b["n"]))
    if b.get("n_alt"):
        s = max(s, sim(a["n"], b["n_alt"]))
    return s


def is_filler(w):
    return w["n"] in FILLERS


_LATIN = re.compile(r"^[a-z0-9']+$")


def is_droppable(w):
    """Tokens Scribe routinely leaves out of a Short transcript, so they may be kept on timing alone at an edit:
    fillers and Latin tokens of at most 2 letters ('a', 'I', 'so'). One-syllable Telugu words (రా, నా, మీ, ఈ) carry
    meaning and are not droppable: without text evidence they count as cut."""
    return is_filler(w) or (bool(_LATIN.match(w["n"])) and len(w["n"]) <= 2)


# ---- mapping ------------------------------------------------------------------------------------------

class EpisodeIndex:
    def __init__(self, words, demote_ranges=None):
        self.E = words
        self.demote_ranges = list(demote_ranges or [])   # e.g. the opening teaser (shotvi.segment)
        self.starts = [w["start"] for w in words]
        self.phon = [phon(w["n"]) for w in words]
        self.ngram = defaultdict(list)
        for j in range(len(words) - SEED_N + 1):
            ws = words[j:j + SEED_N]
            if all(is_filler(w) or not w["n"] for w in ws):
                continue
            self.ngram[("n",) + tuple(w["n"] for w in ws)].append(j)
            self.ngram[("p",) + tuple(self.phon[j + t] for t in range(SEED_N))].append(j)

    def idx_at_time(self, t):
        return bisect.bisect_left(self.starts, t)


def find_regions(S, ep):
    """Cluster exact/phonetic 3-gram seeds by episode time. Returns (regions, clusters info).
    ep.demote_ranges: inclusive episode word ranges (opening teaser) whose copies lose to body copies."""
    E = ep.E
    demote = getattr(ep, "demote_ranges", None) or []
    s_phon = [phon(w["n"]) for w in S]
    seeds = []
    for i in range(len(S) - SEED_N + 1):
        ws = S[i:i + SEED_N]
        if all(is_filler(w) or not w["n"] for w in ws):
            continue
        hits = set(ep.ngram.get(("n",) + tuple(w["n"] for w in ws), []))
        hits |= set(ep.ngram.get(("p",) + tuple(s_phon[i:i + SEED_N]), []))
        for j in hits:
            seeds.append((E[j]["start"], i, j))
    if not seeds:
        return [], []
    seeds.sort()
    clusters, cur = [], [seeds[0]]
    for s in seeds[1:]:
        if s[0] - cur[-1][0] > CLUSTER_GAP_S:
            clusters.append(cur)
            cur = [s]
        else:
            cur.append(s)
    clusters.append(cur)
    infos = []
    for c in clusters:
        pos = set()
        for _, i, _ in c:
            pos.update(range(i, i + SEED_N))
        js = sorted(j for _, _, j in c)
        mid_j = js[len(js) // 2]
        infos.append({"t0": c[0][0], "t1": c[-1][0], "positions": pos, "n_seeds": len(c),
                      "demoted": any(a <= mid_j <= b for a, b in demote)})
    # Primary: the cluster explaining the most Short words. A copy inside a demoted range (the episode's
    # opening teaser montage) loses to a body copy that explains >= RIVAL_SHARE as many words; among equal
    # copies the later one wins (a teaser always precedes the moment it previews).
    infos.sort(key=lambda x: (-len(x["positions"]), -x["t0"]))
    top = len(infos[0]["positions"])
    body = [inf for inf in infos if not inf["demoted"] and len(inf["positions"]) >= RIVAL_SHARE * top]
    prim = body[0] if body else infos[0]
    infos.remove(prim)
    infos.insert(0, prim)
    rivals = [inf for inf in infos[1:] if len(inf["positions"]) >= RIVAL_SHARE * len(prim["positions"])
              and len(inf["positions"] & prim["positions"]) >= RIVAL_SHARE * len(inf["positions"])]
    for r in rivals:
        r["rival_of_primary"] = True
    chosen = [prim]
    covered = set(prim["positions"])

    def dist_to_primary(inf):
        return max(0.0, inf["t0"] - prim["t1"], prim["t0"] - inf["t1"])

    # Greedy: add clusters that explain Short words the chosen ones do not. When several clusters explain
    # (nearly) the same new words - e.g. an episode teaser that repeats body content - prefer one outside the
    # demoted ranges, then the one closest to the primary region (consistent with the Short's other pieces).
    # A secondary cluster needs real evidence: MIN_NEW_POSITIONS new Short words from >= MIN_NEW_SEEDS seeds.
    rest = [inf for inf in infos[1:]]
    while rest:
        gains = [(len(inf["positions"] - covered), inf) for inf in rest]
        gains = [(g, inf) for g, inf in gains if inf["n_seeds"] >= MIN_NEW_SEEDS]
        if not gains:
            break
        best_gain = max(g for g, _ in gains)
        if best_gain < MIN_NEW_POSITIONS:
            break
        eligible = [inf for g, inf in gains if g >= 0.75 * best_gain]
        pick = min(eligible, key=lambda inf: (inf["demoted"], dist_to_primary(inf)))
        lost = [inf for inf in eligible if inf is not pick
                and len((inf["positions"] - covered) & (pick["positions"] - covered)) >= MIN_NEW_POSITIONS]
        for r in lost:
            r["lost_to_nearer"] = True
        chosen.append(pick)
        covered |= pick["positions"]
        rest.remove(pick)
    for k, inf in enumerate(infos):
        inf["chosen"] = any(inf is c for c in chosen)
        inf["primary"] = k == 0
        # share of this cluster's Short words that the chosen clusters also explain (1.0 = pure duplicate)
        inf["dup_share"] = (len(inf["positions"] & set().union(*(c["positions"] for c in chosen if c is not inf)))
                            / max(1, len(inf["positions"])))
    regions = []
    for inf in chosen:
        j0 = ep.idx_at_time(inf["t0"] - REGION_MARGIN_S)
        j1 = min(len(E) - 1, ep.idx_at_time(inf["t1"] + REGION_MARGIN_S))
        regions.append((j0, j1))
    return regions, infos


def build_candidates(S, ep, regions, sim):
    E = ep.E
    region_js = sorted({j for a, b in regions for j in range(a, b + 1)})
    by_norm = defaultdict(list)
    for j in region_js:
        if E[j]["n"]:
            by_norm[E[j]["n"]].append(j)
    cands = []
    type_cache = {}
    for w in S:
        key = (w["n"], w.get("n_alt"))
        if key not in type_cache:
            lst = []
            if w["n"]:
                for en, js in by_norm.items():
                    s = sim(w["n"], en)
                    if w.get("n_alt"):
                        s = max(s, sim(w["n_alt"], en))
                    if s >= CAND_SIM:
                        lst.extend((j, s) for j in js)
            type_cache[key] = lst
        cands.append([(j, s, E[j]["start"] - w["start"]) for j, s in type_cache[key]])
    return cands


def reward(s):
    return 1.0 if s >= 0.999 else 1.5 * s - 0.5


def viterbi(S, E, cands):
    """Chain Short words to episode candidates; returns [(i, j, sim)] in Short order."""
    m = len(S)
    skip_cost = [SKIP_PEN_FILLER if is_filler(w) or not w["n"] else SKIP_PEN for w in S]
    cum_skip = [0.0]
    for c in skip_cost:
        cum_skip.append(cum_skip[-1] + c)

    def skip(a, b):  # cost of skipping Short words a..b-1
        return cum_skip[b] - cum_skip[a]

    dp = []      # dp[i][c] = best score ending with Short word i matched to cands[i][c]
    bp = []      # backpointer (i', c') or None
    best = []    # (score, c) best state at i
    # gbest[i] = max over i' < i of best[i'] + cum_skip[i'+1]; stores (value, i', c')
    gbest = (float("-inf"), None, None)
    by_j = []    # per i: dict j -> c
    by_ob = []   # per i: dict offset bucket -> [c]
    topk = []    # per i: [c] best-scoring states, for forward cuts
    for i in range(m):
        if i > 0 and best[i - 1][1] is not None:
            v = best[i - 1][0] + cum_skip[i]
            if v > gbest[0]:
                gbest = (v, i - 1, best[i - 1][1])
        row, brow = [], []
        for (j, s, o) in cands[i]:
            r = reward(s)
            # start fresh (everything before skipped)
            sc, b = r - skip(0, i), None
            # generic jump from the best earlier state
            if gbest[1] is not None:
                pi, pc = gbest[1], gbest[2]
                pj = cands[pi][pc][0]
                prox = JUMP_PROX_PEN * min(1.0, abs(E[j]["start"] - E[pj]["start"]) / 120.0)
                v = gbest[0] - cum_skip[i] - JUMP_PEN - prox + r
                if v > sc:
                    sc, b = v, (pi, pc)
            # near transitions
            for pi in range(max(0, i - MAX_SKIP), i):
                sk = skip(pi + 1, i)
                prow = dp[pi]
                ob = int(o // TOL_CONT)
                for bucket in (ob - 1, ob, ob + 1):
                    for pc in by_ob[pi].get(bucket, ()):
                        pj, _, po = cands[pi][pc]
                        if pj < j and abs(o - po) <= TOL_CONT:
                            v = prow[pc] - sk + r
                            if v > sc:
                                sc, b = v, (pi, pc)
                # pause trim: next episode word, offset stepped forward (no words removed)
                pc = by_j[pi].get(j - 1)
                if pc is not None:
                    do = o - cands[pi][pc][2]
                    # (Short i-1, i) -> (episode j-1, j): also tolerate a single-word timestamp outlier
                    if TOL_CONT < do <= 30.0 or (pi == i - 1 and -0.6 <= do < -TOL_CONT):
                        v = prow[pc] - sk - TRIM_PEN + r
                        if v > sc:
                            sc, b = v, (pi, pc)
                # forward cut: episode words removed, offset stepped forward
                for pc in topk[pi]:
                    pj, _, po = cands[pi][pc]
                    do = o - po
                    if pj < j - 1 and TOL_CONT < do:
                        gap = E[j]["start"] - E[pj]["end"]
                        if j - pj - 1 <= SMALL_CUT_WORDS and gap <= SMALL_CUT_S:
                            pen = SMALL_CUT_PEN          # tightening: a few words dropped
                        elif gap <= CUT_MAX_GAP_S:
                            pen = CUT_PEN + CUT_DIST_PEN * min(1.0, max(0.0, gap) / CUT_MAX_GAP_S)
                        else:
                            pen = None
                        if pen is not None:
                            v = prow[pc] - sk - pen + r
                            if v > sc:
                                sc, b = v, (pi, pc)
            row.append(sc)
            brow.append(b)
        dp.append(row)
        bp.append(brow)
        if row:
            c = max(range(len(row)), key=row.__getitem__)
            best.append((row[c], c))
        else:
            best.append((float("-inf"), None))
        bj, bo = {}, defaultdict(list)
        for c, (j, s, o) in enumerate(cands[i]):
            if j not in bj or row[c] > row[bj[j]]:
                bj[j] = c
            bo[int(o // TOL_CONT)].append(c)
        by_j.append(bj)
        by_ob.append(bo)
        topk.append(sorted(range(len(row)), key=row.__getitem__, reverse=True)[:CUT_TOPK])
    # end: best state + skipping the rest
    end_best, end_state = -skip(0, m), None
    for i in range(m):
        if best[i][1] is not None:
            v = best[i][0] - skip(i + 1, m)
            if v > end_best:
                end_best, end_state = v, (i, best[i][1])
    path = []
    st = end_state
    while st is not None:
        i, c = st
        j, s, _ = cands[i][c]
        path.append((i, j, s))
        st = bp[i][c]
    path.reverse()
    return path


# ---- gap filling --------------------------------------------------------------------------------------

def _time_dev(sw_list, ew_list, off, tol, edge_side=None):
    """Start-time deviation (s) if the Short words, shifted by off, line up with the episode words
    (intervals overlap, starts within tol); None otherwise."""
    s0 = sw_list[0]["start"] + off
    s1 = sw_list[-1]["end"] + off
    e0 = ew_list[0]["start"]
    e1 = ew_list[-1]["end"]
    # Scribe often stretches a word over an adjacent pause, so one edge can be off; trust the better edge
    if edge_side == "tail":      # continues a piece: the start is reliable, the end may be cut
        dev = abs(s0 - e0)
    elif edge_side == "head":    # starts a piece: the end is reliable, the start may be cut
        dev = abs(s1 - e1)
    else:
        dev = min(abs(s0 - e0), abs(s1 - e1))
    if s0 - 0.05 <= e1 and e0 <= s1 + 0.05 and dev <= tol:
        return dev
    return None


def align_local(S, E, us, vs, offs, sim, edge=False):
    """edge: False (continuous gap), "tail" (after a piece, before a cut) or "head" (after a cut)."""
    """Fuzzy alignment of Short idx list `us` to episode idx list `vs` (both in order).
    Ops: 1-1, 1-2 (Short word = two episode words), 2-1, gaps. A pair is allowed only if timing agrees
    with one of the offsets in `offs`. Returns list of (i_lo, i_hi, j_lo, j_hi, sim)."""
    nu, nv = len(us), len(vs)
    if nu == 0 or nv == 0:
        return []
    tol = EDGE_TOL_S + 0.1 if edge else TOL_CONT + 0.1
    min_sim = FILL_SIM_EDGE if edge else FILL_SIM
    NEG = float("-inf")
    D = [[NEG] * (nv + 1) for _ in range(nu + 1)]
    B = [[None] * (nv + 1) for _ in range(nu + 1)]
    for a in range(nu + 1):
        for b in range(nv + 1):
            if a == 0 and b == 0:
                D[0][0] = 0.0
                continue
            best, arg = NEG, None
            if a > 0 and D[a - 1][b] > best:
                best, arg = D[a - 1][b], (a - 1, b, None)
            if b > 0 and D[a][b - 1] > best:
                best, arg = D[a][b - 1], (a, b - 1, None)
            for da, db in ((1, 1), (1, 2), (2, 1), (3, 1), (4, 1), (1, 3), (1, 4)):
                if da > 2 and not (b >= 1 and has_digit(E[vs[b - 1]])):
                    continue
                if db > 2 and not (a >= 1 and has_digit(S[us[a - 1]])):
                    continue
                if a < da or b < db or D[a - da][b - db] == NEG:
                    continue
                sws = [S[us[k]] for k in range(a - da, a)]
                ews = [E[vs[k]] for k in range(b - db, b)]
                # consecutive indices only for merges
                if da >= 2 and us[a - 1] != us[a - da] + da - 1:
                    continue
                if db >= 2 and vs[b - 1] != vs[b - db] + db - 1:
                    continue
                devs = [d for d in (_time_dev(sws, ews, o, tol, edge or None) for o in offs) if d is not None]
                if not devs:
                    continue
                dev = min(devs)
                sn = "".join(w["n"] for w in sws)
                en = "".join(w["n"] for w in ews)
                if da == 1 and db == 1:
                    s = sim.partial(sn, en) if edge else wsim(sim, sws[0], ews[0])
                else:
                    s = sim(sn, en)
                    if s < 0.75:
                        continue
                gain = s - 0.3
                if s < min_sim:
                    if not (edge and da == 1 and db == 1):
                        continue
                    # At a cut edge, precise timing can outweigh a transcription difference: the same audio
                    # heard as 'చూస్తున్నా' vs 'చూశాను' (dev 0.004s). Timing-only pairs (text disagrees) keep the
                    # episode word but are not counted as text matches.
                    o = min(offs, key=lambda o: abs(sws[0]["start"] + o - ews[0]["start"]))
                    s0, s1 = sws[0]["start"] + o, sws[0]["end"] + o
                    e0, e1 = ews[0]["start"], ews[0]["end"]
                    iou = max(0.0, min(s1, e1) - max(s0, e0)) / max(1e-6, max(s1, e1) - min(s0, e0))
                    if s >= 0.5 and dev <= 0.10:
                        pass
                    elif dev <= 0.08 and iou >= 0.6:
                        gain = 0.15
                    else:
                        continue
                v = (D[a - da][b - db] + gain - 0.5 * dev
                     - (0.05 if (da, db) != (1, 1) else 0.0))
                if v > best:
                    best, arg = v, (a - da, b - db, s)
            D[a][b] = best
            B[a][b] = arg
    out = []
    a, b = nu, nv
    while a or b:
        pa, pb, s = B[a][b]
        if s is not None:
            out.append((us[pa], us[a - 1], vs[pb], vs[b - 1], s))
        a, b = pa, pb
    out.reverse()
    return out


def fill_gaps(S, E, path, sim):
    """Turn the Viterbi chain into a list of matched units (i_lo, i_hi, j_lo, j_hi, sim, how)."""
    units = [(i, i, j, j, s, "chain") for i, j, s in path]
    if not units:
        return units
    out = []
    m = len(S)

    def off(u):
        return E[u[2]]["start"] - S[u[0]]["start"]

    def window(j_from, j_to):
        j_from, j_to = max(0, j_from), min(len(E) - 1, j_to)
        return list(range(j_from, j_to + 1)) if j_to >= j_from else []

    # leading Short words: extend the first unit backwards
    first = units[0]
    if first[0] > 0:
        us = list(range(0, first[0]))
        vs = window(first[2] - len(us) - 3, first[2] - 1)
        out.extend(align_local(S, E, us, vs, [off(first)], sim, edge="head"))
    for k, u in enumerate(units):
        out.append(u)
        if k + 1 == len(units):
            break
        v = units[k + 1]
        us = list(range(u[1] + 1, v[0]))
        cont = abs(off(v) - off(u)) <= TOL_CONT and v[2] > u[3]
        adjacent = v[2] == u[3] + 1
        if cont or adjacent:
            vs = window(u[3] + 1, v[2] - 1)
            out.extend(align_local(S, E, us, vs, [off(u), off(v)], sim))
            continue
        if not us:
            continue
        # a cut or jump: Short words in between belong to the tail of u or the head of v
        tail_vs = window(u[3] + 1, u[3] + len(us) + 3)
        head_vs = window(v[2] - len(us) - 3, v[2] - 1)
        best, best_pairs = -1.0, []
        for split in range(len(us) + 1):
            a = align_local(S, E, us[:split], tail_vs, [off(u)], sim, edge="tail")
            b = align_local(S, E, us[split:], head_vs, [off(v)], sim, edge="head")
            if a and b and v[2] > u[3] and max(x[3] for x in a) >= min(x[2] for x in b):
                continue
            score = sum(x[4] for x in a) + sum(x[4] for x in b)
            if score > best:
                best, best_pairs = score, a + b
        out.extend(best_pairs)
    last = units[-1]
    if last[1] < m - 1:
        us = list(range(last[1] + 1, m))
        vs = window(last[3] + 1, last[3] + len(us) + 3)
        out.extend(align_local(S, E, us, vs, [off(last)], sim, edge="tail"))
    out = [(a, b, c, d, s, "chain" if len(x) > 5 and x[5] == "chain" else "fill")
           for x in out for (a, b, c, d, s) in [x[:5]]]
    out.sort(key=lambda x: (x[0], x[2]))
    return out


def extend_merges(S, E, units, sim):
    """One Short word can cover two episode words ('questionకి' vs 'question కి', 'ఇలా-ఇలాగా' vs
    'ఇలా ఇలాగ'). Grow a single-word unit by one neighbouring, unused episode word when the joined text is
    more similar and the neighbour lies inside the Short word's time span."""
    used = set()
    s_used = set()
    for u in units:
        used.update(range(u[2], u[3] + 1))
        s_used.update(range(u[0], u[1] + 1))

    def claimed(j, i):
        """Episode word j matches the neighbouring unmatched Short word i better on its own."""
        return 0 <= i < len(S) and i not in s_used and wsim(sim, S[i], E[j]) >= 0.6

    out = []
    for k, u in enumerate(units):
        i_lo, i_hi, j_lo, j_hi, s, how = u
        if i_lo != i_hi:
            out.append(u)
            continue
        sw = S[i_lo]
        # right neighbour
        j = j_hi + 1
        off = E[j_lo]["start"] - sw["start"]
        if j < len(E) and j not in used and not claimed(j, i_hi + 1):
            e = E[j]
            if e["start"] - off >= sw["start"] - 0.05 and e["end"] - off <= sw["end"] + 0.25:
                cur = sim(sw["n"], "".join(E[x]["n"] for x in range(j_lo, j_hi + 1)))
                s2 = sim(sw["n"], "".join(E[x]["n"] for x in range(j_lo, j + 1)))
                if s2 >= cur + 0.03 and s2 >= 0.75:
                    j_hi, s = j, max(s, s2)
                    used.add(j)
        # left neighbour (the Short word starts earlier than E[j_lo] if it also covers E[j_lo - 1])
        j = j_lo - 1
        if j >= 0 and j not in used and not claimed(j, i_lo - 1):
            # align on the Short word's end (its start is what we are unsure about)
            off_e = E[j_hi]["end"] - sw["end"]
            e = E[j]
            if abs(e["start"] - off_e - sw["start"]) <= TOL_CONT + 0.1:
                cur = sim(sw["n"], "".join(E[x]["n"] for x in range(j_lo, j_hi + 1)))
                s2 = sim(sw["n"], "".join(E[x]["n"] for x in range(j, j_hi + 1)))
                if s2 >= cur + 0.03 and s2 >= 0.75:
                    j_lo, s = j, max(s, s2)
                    used.add(j)
        out.append((i_lo, i_hi, j_lo, j_hi, s, how))
    # Short side split: 'ఖర్చు పెట్టి' (two Short words) vs 'ఖర్చుపెట్టి' (one episode word)
    matched_i = {i for u in out for i in range(u[0], u[1] + 1)}
    res = []
    for u in out:
        i_lo, i_hi, j_lo, j_hi, s, how = u
        i = i_hi + 1
        if i < len(S) and i not in matched_i:
            off = E[j_lo]["start"] - S[i_lo]["start"]
            if S[i]["start"] + off <= E[j_hi]["end"] + 0.1:
                joined = "".join(S[x]["n"] for x in range(i_lo, i + 1))
                en = "".join(E[x]["n"] for x in range(j_lo, j_hi + 1))
                s2 = sim(joined, en)
                if s2 >= s + 0.03:
                    i_hi, s = i, s2
                    matched_i.add(i)
        res.append((i_lo, i_hi, j_lo, j_hi, s, how))
    return res




# ---- pieces -------------------------------------------------------------------------------------------

def _group_units(S, E, units):
    """Group matched units into pieces (maximal runs of kept, contiguous episode words).
    Returns (pieces, transitions between consecutive units)."""
    if not units:
        return [], []

    def off(u):
        return E[u[2]]["start"] - S[u[0]]["start"]

    groups = [[units[0]]]
    extra_head = defaultdict(list)   # piece idx -> episode words kept before its first unit
    extra_tail = defaultdict(list)
    trans = []
    for p, q in zip(units, units[1:]):
        g = len(groups) - 1
        op, oq = off(p), off(q)
        d_o = oq - op
        if q[2] > p[3]:
            mids = list(range(p[3] + 1, q[2]))
            mid_dur = sum(E[jj]["end"] - E[jj]["start"] for jj in mids)
            # Was the audio edited here? Same offset on both sides = the episode audio between them plays
            # unchanged in the Short, so every episode word in between is present (Scribe just did not
            # transcribe it separately). A small offset step can still be a cut filler: compare the step
            # with the duration of the words in between.
            edited = abs(d_o) > TOL_CONT or (mids and d_o >= 0.12 and d_o >= 0.6 * mid_dur)
            if not edited:
                groups[-1].append(q)
                trans.append({"type": "cont", "kept_untranscribed": mids, "delta": d_o})
                continue
            if not mids:
                groups[-1].append(q)
                trans.append({"type": "trim" if d_o > 0 else "stretch", "kept_untranscribed": [], "delta": d_o})
                continue
            # Edited with episode words in between: an untranscribed word is kept only if it fits inside the
            # Short's silent gap (on either side's timeline) AND is the kind of token Scribe drops (filler,
            # 1-2 letters). Real words Scribe would have transcribed are treated as cut.
            gap0 = S[p[1]]["end"]
            gap1 = S[q[0]]["start"]
            kept_a, kept_b, cut, edge_fit = [], [], [], []
            for jj in mids:
                e = E[jj]
                droppable = is_droppable(e)
                in_a = (e["start"] - op >= gap0 - TOL_FIT) and (e["end"] - op <= gap1 + TOL_FIT)
                in_b = (e["start"] - oq >= gap0 - TOL_FIT) and (e["end"] - oq <= gap1 + TOL_FIT)
                if (in_a or in_b) and not droppable:
                    edge_fit.append(jj)
                if droppable and in_a and not kept_b and not cut:
                    kept_a.append(jj)
                elif droppable and in_b:
                    kept_b.append(jj)
                else:
                    cut.append(jj)
            if cut:
                kept_b = [jj for jj in kept_b if jj > cut[-1]]
                cut = [jj for jj in mids if jj not in kept_a and jj not in kept_b]
            if not cut:
                groups[-1].append(q)
                trans.append({"type": "trim", "kept_untranscribed": mids, "delta": d_o})
                continue
            extra_tail[g].extend(kept_a)
            extra_head[g + 1].extend(kept_b)
            trans.append({"type": "cut", "cut": cut, "kept_untranscribed": kept_a + kept_b,
                          "edge_fit_untranscribed": edge_fit, "delta": d_o})
            groups.append([q])
        elif q[2] > p[3] - 3 and abs(d_o) <= TOL_CONT:
            # tiny overlap reorder in the transcript (overlapping speech), same audio
            groups[-1].append(q)
            trans.append({"type": "cont", "kept_untranscribed": [], "delta": d_o})
        else:
            trans.append({"type": "jump", "delta": d_o})
            groups.append([q])
    pieces = []
    for g, us in enumerate(groups):
        j_lo = min(u[2] for u in us)
        j_hi = max(u[3] for u in us)
        if extra_head[g]:
            j_lo = min(j_lo, min(extra_head[g]))
        if extra_tail[g]:
            j_hi = max(j_hi, max(extra_tail[g]))
        pieces.append({"units": us, "j_lo": j_lo, "j_hi": j_hi})
    return pieces, trans


RELIABLE_SIM = 0.80     # units at or above this similarity define piece structure (their timing is trusted)


def build_pieces(S, E, units):
    """Piece structure comes from reliable units only; a weak fuzzy fill (e.g. 'ready'~'కథ' 0.47) has an
    unreliable start time, so it is attached afterwards to the neighbouring piece whose timing it fits, or
    dropped (reported unmatched) if it fits none. Returns (pieces, transitions, units actually used)."""
    rel = [u for u in units if u[4] >= RELIABLE_SIM]
    if not rel:
        rel = list(units)
    rel_ids = {id(u) for u in rel}
    weak = [u for u in units if id(u) not in rel_ids]
    pieces, trans = _group_units(S, E, rel)
    if not pieces:
        return pieces, trans, []

    def off(u):
        return E[u[2]]["start"] - S[u[0]]["start"]

    dropped = []
    for w in weak:
        # candidate pieces: the one containing w's Short position, else the ones on either side
        before = [k for k, p in enumerate(pieces) if p["units"][0][0] <= w[0]]
        k0 = before[-1] if before else None
        k1 = (k0 + 1 if k0 is not None else 0)
        opts = []
        for k in (k0, k1):
            if k is None or k >= len(pieces):
                continue
            p = pieces[k]
            inside = p["units"][0][0] <= w[0] <= p["units"][-1][1]
            if inside:
                ok = p["j_lo"] <= w[2] and w[3] <= p["j_hi"]
            elif w[0] > p["units"][-1][1]:          # w follows piece k in the Short: extends its tail
                nxt = pieces[k + 1]["j_lo"] if k + 1 < len(pieces) else None
                ok = w[3] > p["j_hi"] and w[2] - p["j_hi"] <= 6 and (nxt is None or nxt <= p["j_hi"] or w[3] < nxt)
            else:                                   # w precedes piece k: extends its head
                prv = pieces[k - 1]["j_hi"] if k > 0 else None
                ok = w[2] < p["j_lo"] and p["j_lo"] - w[3] <= 6 and (prv is None or prv >= p["j_lo"] or w[3] > prv)
            if not ok:
                continue
            ref = min(p["units"], key=lambda u: abs(u[0] - w[0]))
            dev = abs(off(w) - off(ref))
            if inside or dev <= EDGE_TOL_S + 0.3:
                opts.append((0 if inside else dev, k))
        if not opts:
            dropped.append(w)
            continue
        _, k = min(opts)
        p = pieces[k]
        p["units"] = sorted(p["units"] + [w], key=lambda u: (u[0], u[2]))
        p["j_lo"] = min(p["j_lo"], w[2])
        p["j_hi"] = max(p["j_hi"], w[3])
    drop_ids = {id(u) for u in dropped}
    used = [u for u in units if id(u) not in drop_ids]
    # a piece is a maximal run of kept words: merge neighbours that became contiguous
    merged = [pieces[0]]
    for p in pieces[1:]:
        q = merged[-1]
        if p["j_lo"] == q["j_hi"] + 1:
            q["units"] = q["units"] + p["units"]
            q["j_hi"] = p["j_hi"]
        else:
            merged.append(p)
    return merged, trans, used


def merge_ranges(ranges):
    out = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


# natural boundary (clip_spec.md section 0): word i ends with . ? ! । , or speakers differ, or gap >= 0.5s
END_PUNCT = re.compile(r"[.?!।]['\"”’)\]]*$")
BOUNDARY_GAP_S = 0.5
T4_SKIPPABLE = FILLERS | {"so", "and", "but", "అంటే", "అండ్", "సో", "బట్", "కానీ", "ఇంక", "ఇక", "అయితే",
                          "okay", "ok", "ఓకే", "అది", "అలా"}


def natural_after(E, i):
    """True if there is a natural boundary between word i and i+1 (i = -1 or last word: True)."""
    if i < 0 or i >= len(E) - 1:
        return True
    a, b = E[i], E[i + 1]
    return (bool(END_PUNCT.search(a["word"])) or a.get("speaker") != b.get("speaker")
            or b["start"] - a["end"] >= BOUNDARY_GAP_S)


def boundary_report(E, pieces):
    """How the Short's first and last played words sit relative to natural boundaries (spec T4/T5)."""
    s = pieces[0]["j_lo"]
    e = pieces[-1]["j_hi"]
    start_ok = natural_after(E, s - 1)
    start_t4 = start_ok
    start_note = ""
    if not start_ok:
        for back in (1, 2):
            k = s - back
            if k < 0:
                break
            dropped = [E[x]["n"] for x in range(k, s)]
            if natural_after(E, k - 1) and all(d in T4_SKIPPABLE for d in dropped):
                start_t4 = True
                start_note = "after dropped " + " ".join(E[x]["word"] for x in range(k, s))
                break
    end_ok = natural_after(E, e)
    inner = []
    for p in pieces:
        inner.append((natural_after(E, p["j_lo"] - 1), natural_after(E, p["j_hi"])))
    return {
        "start_natural": start_ok, "start_natural_t4": start_t4, "start_note": start_note,
        "start_context": " ".join(E[x]["word"] for x in range(max(0, s - 4), s)) + " | " +
                         " ".join(E[x]["word"] for x in range(s, min(len(E), s + 4))),
        "end_natural": end_ok,
        "end_context": " ".join(E[x]["word"] for x in range(max(0, e - 3), e + 1)) + " | " +
                       " ".join(E[x]["word"] for x in range(e + 1, min(len(E), e + 5))),
        "piece_edges_natural": sum(a for a, _ in inner) + sum(b for _, b in inner),
        "piece_edges_total": 2 * len(inner),
    }


EXC_TOL_S = 0.5         # |off(B) - off(A)| at which A ... B is treated as one continuous stretch of audio
EXC_MAX_WORDS = 60      # longest excursion (chain entries) tested
SCALE_TOL = 0.03        # |time scale - 1| above which the Short is treated as sped up / slowed down
SCALE_MIN_PAIRS = 20
SCALE_MAX_GAP_S = 0.15   # no pause after the first word of a pair (nothing to trim)


def drop_excursions(S, E, path):
    """Remove chance excursions A -> far -> B from the Viterbi chain: if B continues A in the episode
    (j_B > j_A) with the same offset (the Short time between them equals the episode time between them),
    the Short plays that episode stretch unchanged, so the far words matched in between are chance text
    matches (the Short ASR heard something else). A real insertion would shift B's offset by its duration.
    Returns (path, removed entries)."""
    def off(e):
        return E[e[1]]["start"] - S[e[0]]["start"]
    path = list(path)
    removed = []
    a = 0
    while a < len(path) - 2:
        ja, oa = path[a][1], off(path[a])
        hit = None
        for b in range(a + 2, min(len(path), a + 2 + EXC_MAX_WORDS)):
            jb = path[b][1]
            if jb > ja and abs(off(path[b]) - oa) <= EXC_TOL_S:
                mid = path[a + 1:b]
                if all(not (ja < e[1] < jb) for e in mid):
                    hit = b
                break
        if hit is not None:
            removed.extend(path[a + 1:hit])
            del path[a + 1:hit]
        else:
            a += 1
    return path, removed


def estimate_scale(S, E, path):
    """Robust playback-speed estimate (episode seconds per Short second). Uses only adjacent exact matches
    (Short i, i+1 -> episode j, j+1) with no pause after the first word on either side: there is nothing an
    editor could trim there, so creators' pause trims (jump cuts) do not bias the fit; only a real speed
    change does. Returns (median ratio, number of pairs)."""
    ratios = []
    ex = [(i, j) for i, j, s in path if s >= 0.999]
    for (i0, j0), (i1, j1) in zip(ex, ex[1:]):
        if i1 != i0 + 1 or j1 != j0 + 1:
            continue
        ds = S[i1]["start"] - S[i0]["start"]
        de = E[j1]["start"] - E[j0]["start"]
        if ds >= 0.2 and S[i1]["start"] - S[i0]["end"] <= SCALE_MAX_GAP_S and E[j1]["start"] - E[j0]["end"] <= SCALE_MAX_GAP_S:
            ratios.append(de / ds)
    if len(ratios) < SCALE_MIN_PAIRS:
        return 1.0, len(ratios)
    ratios.sort()
    return ratios[len(ratios) // 2], len(ratios)


def rescale(S, k):
    out = []
    for w in S:
        w2 = dict(w)
        w2["start"], w2["end"] = w["start"] * k, w["end"] * k
        out.append(w2)
    return out


def _map_once(S, ep, sim):
    E = ep.E
    regions, clusters = find_regions(S, ep)
    if not regions:
        return {"clusters": clusters, "units": [], "pieces": [], "trans": [], "path": [], "excursions": []}
    cands = build_candidates(S, ep, regions, sim)
    path = viterbi(S, E, cands)
    path, exc = drop_excursions(S, E, path)
    units = fill_gaps(S, E, path, sim)
    units = extend_merges(S, E, units, sim)
    pieces, trans, units = build_pieces(S, E, units)
    return {"clusters": clusters, "units": units, "pieces": pieces, "trans": trans, "path": path,
            "excursions": exc}


def map_short(short_id, S, ep, sim):
    """Map one Short. Returns an internal result dict (see to_anchor_clip for the file format).
    A Short played faster or slower than the episode is detected from its exact matches, rescaled to episode
    speed and mapped again (flagged when |scale - 1| > SCALE_TOL)."""
    E = ep.E
    r = _map_once(S, ep, sim)
    scale, n_pairs = estimate_scale(S, E, r["path"])
    res = {"short_id": short_id, "S": S, "n": len(S), "time_scale": round(scale, 4),
           "time_scale_pairs": n_pairs}
    if abs(scale - 1.0) > SCALE_TOL:
        S2 = rescale(S, scale)
        r = _map_once(S2, ep, sim)
        res["S"] = S2
        res["S_orig"] = S
    res.update(r)
    return finish(res, E)


def finish(res, E):
    S, units, pieces = res["S"], res["units"], res["pieces"]
    n = len(S)
    matched_i = {}
    time_only_i = {}   # Short word -> unit kept on timing alone (text differs)
    for u in units:
        for i in range(u[0], u[1] + 1):
            if u[4] >= FILL_SIM:
                matched_i[i] = u
            else:
                time_only_i[i] = u
    clusters = res["clusters"]
    flags = []
    details = []
    if clusters:
        used_windows = [(c["t0"] - REGION_MARGIN_S, c["t1"] + REGION_MARGIN_S) for c in clusters if c["chosen"]]
    for k, p in enumerate(pieces):
        us = p["units"]
        i_lo, i_hi = us[0][0], us[-1][1]
        tus = [u for u in us if u[4] >= FILL_SIM]
        n_matched = sum(u[1] - u[0] + 1 for u in tus)
        n_exact = sum(u[1] - u[0] + 1 for u in us if u[4] >= 0.999)
        # evidence for a tiny piece comes from content words only: a matched "ఉమ్" proves nothing
        nf = [u for u in tus if not all(is_filler(S[i]) for i in range(u[0], u[1] + 1))]
        nf_matched = sum(u[1] - u[0] + 1 for u in nf)
        nf_exact = sum(u[1] - u[0] + 1 for u in nf if u[4] >= 0.999)
        offs = [E[u[2]]["start"] - S[u[0]]["start"] for u in us]
        steps = [b - a for a, b in zip(offs, offs[1:])]
        jitter = max((abs(x) for x in steps if abs(x) <= TOL_CONT), default=0.0)
        n_trims = sum(1 for x in steps if x > TOL_CONT)
        stretch = [x for x in steps if x < -0.6]
        mean_sim = sum(u[4] * (u[1] - u[0] + 1) for u in tus) / max(1, n_matched)
        issues = []
        if nf_matched <= 2:
            # a tiny piece is believable when it sits a few words from a neighbouring piece (a filler or
            # a word cut between them) and matches well
            nb = False
            for q in (pieces[k - 1] if k > 0 else None, pieces[k + 1] if k + 1 < len(pieces) else None):
                if q is None:
                    continue
                if 0 < p["j_lo"] - q["j_hi"] <= 8 or 0 < q["j_lo"] - p["j_hi"] <= 8:
                    nb = True
            strong_text = nf_exact >= 1 or (nf_matched >= 2 and mean_sim >= 0.85)
            prv = pieces[k - 1] if k > 0 else None
            nxt = pieces[k + 1] if k + 1 < len(pieces) else None
            in_order = (prv is not None and nxt is not None and prv["j_hi"] < p["j_lo"]
                        and p["j_hi"] < nxt["j_lo"] and nxt["j_lo"] - prv["j_hi"] <= 80)
            # first / last piece: in order with its only neighbour and close to it
            if prv is None and nxt is not None:
                in_order = in_order or 0 < nxt["j_lo"] - p["j_hi"] <= 30
            if nxt is None and prv is not None:
                in_order = in_order or 0 < p["j_lo"] - prv["j_hi"] <= 30
            if nb and strong_text and mean_sim >= 0.8 and nf_matched >= 1:
                issues.append("tiny piece, corroborated by a neighbouring piece")
            elif nf_matched == 0:
                issues.append("tiny piece, only filler words matched")
            elif in_order and nf_exact >= 2:
                issues.append("tiny piece, corroborated by episode order between its neighbours")
            elif nb:
                issues.append("tiny piece (<=2 matched words), weak text evidence")
            else:
                issues.append("tiny piece (<=2 matched words), not near another piece")
        if mean_sim < 0.8:
            issues.append(f"low text similarity ({mean_sim:.2f})")
        if stretch:
            issues.append(f"Short has {-min(stretch):.1f}s extra audio inside the piece")
        if clusters and not any(a <= E[p["j_lo"]]["start"] <= b for a, b in used_windows):
            issues.append("outside the seed regions")
        doubtful = (any(x.startswith(("tiny piece (", "tiny piece, only filler")) for x in issues) or mean_sim < 0.7
                    or any("extra audio" in x for x in issues))
        quality = "doubtful" if doubtful else ("ok" if issues or n_exact / max(1, n_matched) < 0.6 else "good")
        d = {
            "piece": k + 1,
            "short_words": [i_lo, i_hi],
            "short_time": [S[i_lo]["start"], S[i_hi]["end"]],
            "episode_words": [p["j_lo"], p["j_hi"]],
            "episode_time": [E[p["j_lo"]]["start"], E[p["j_hi"]]["end"]],
            "offset_s": round(offs[0], 3),
            "offset_end_s": round(offs[-1], 3),
            "pause_trims": n_trims,
            "n_short_words": i_hi - i_lo + 1,
            "n_matched": n_matched,
            "n_exact": n_exact,
            "mean_sim": round(mean_sim, 3),
            "timing_jitter_s": round(jitter, 3),
            "quality": quality,
            "issues": issues,
        }
        if k + 1 < len(pieces):
            q = pieces[k + 1]
            if q["j_lo"] > p["j_hi"]:
                gap_s = E[q["j_lo"]]["start"] - E[p["j_hi"]]["end"]
                cut = list(range(p["j_hi"] + 1, q["j_lo"]))
                d["after"] = {"type": "skip" if gap_s > SKIP_S else "cut", "n_words": len(cut),
                              "source_gap_s": round(gap_s, 2),
                              "text": _clip([E[x]["word"] for x in cut], 24)}
            else:
                d["after"] = {"type": "backward", "back_s": round(E[p["j_hi"]]["end"] - E[q["j_lo"]]["start"], 2),
                              "overlap_words": max(0, min(p["j_hi"], q["j_hi"]) - max(p["j_lo"], q["j_lo"]) + 1)}
        if issues:
            flags.append(f"piece {k + 1}: " + "; ".join(issues))
        details.append(d)

    piece_of_i = {}
    for d in details:
        for i in range(d["short_words"][0], d["short_words"][1] + 1):
            piece_of_i[i] = d["piece"]
    unmatched = []
    for i, w in enumerate(S):
        if i in matched_i:
            continue
        ent = {"i": i, "word": w["word"], "time": w["start"]}
        if i in time_only_i:
            u = time_only_i[i]
            ent["context"] = f"inside piece {piece_of_i.get(i)}, timing-only match at a cut edge"
            ent["episode_words_at_time"] = [{"j": jj, "word": E[jj]["word"]} for jj in range(u[2], u[3] + 1)]
        elif i in piece_of_i:
            d = details[piece_of_i[i] - 1]
            prev_u = max((u for u in pieces[d["piece"] - 1]["units"] if u[1] < i), key=lambda u: u[1],
                         default=None)
            o = (E[prev_u[2]]["start"] - S[prev_u[0]]["start"]) if prev_u else d["offset_s"]
            t0, t1 = w["start"] + o, w["end"] + o
            at = []
            for jj in range(d["episode_words"][0], d["episode_words"][1] + 1):
                ov = min(t1, E[jj]["end"]) - max(t0, E[jj]["start"])
                shorter = max(0.02, min(t1 - t0, E[jj]["end"] - E[jj]["start"]))
                if ov >= 0.5 * shorter:
                    at.append(jj)
            ent["context"] = f"inside piece {d['piece']}"
            ent["episode_words_at_time"] = [{"j": jj, "word": E[jj]["word"]} for jj in at]
        else:
            before = [x["piece"] for x in details if x["short_words"][1] < i]
            ent["context"] = ("before piece 1" if not before else
                              f"after piece {before[-1]}" if before[-1] == len(details) else
                              f"between pieces {before[-1]} and {before[-1] + 1}")
        unmatched.append(ent)
    n_real = sum(1 for w in S if w["n"])
    n_matched = len(matched_i)
    unmatched_nf = [u for u in unmatched if not is_filler(S[u["i"]]) and S[u["i"]]["n"]]
    coverage = n_matched / max(1, n)
    doubtful_words = sum(d["n_short_words"] for d in details if d["quality"] == "doubtful")
    conf = coverage * (1 - doubtful_words / max(1, n)) if details else 0.0
    others = []
    for c in clusters[1:]:
        if len(c["positions"]) >= MIN_NEW_POSITIONS:
            others.append({"t0": round(c["t0"], 1), "t1": round(c["t1"], 1),
                           "n_short_words_explained": len(c["positions"]), "used": c["chosen"],
                           "duplicate_share": round(c["dup_share"], 2),
                           "lost_to_nearer_copy": bool(c.get("lost_to_nearer"))})
    # --- extra evidence the reviewer should see
    scale = res.get("time_scale", 1.0)
    if abs(scale - 1.0) > SCALE_TOL:
        flags.insert(0, f"Short plays at {scale:.3f}x episode speed (fit from {res.get('time_scale_pairs')} exact "
                        "word pairs); mapped after rescaling")
        for d in details:   # report Short times in the Short's own seconds
            d["short_time"] = [round(t / scale, 3) for t in d["short_time"]]
        for u in unmatched:
            u["time"] = round(u["time"] / scale, 3)
    exc = res.get("excursions") or []
    if exc:
        flags.append(f"{len(exc)} chance text match(es) far away ignored (same audio continues around them): "
                     + " ".join(E[j]["word"] for _, j, _ in exc[:6]))
    for c in clusters[1:]:
        if c.get("rival_of_primary"):
            flags.append(f"ambiguous source: another copy at {c['t0']:.0f}s explains {len(c['positions'])} Short "
                         f"words vs {len(clusters[0]['positions'])} for the chosen one at {clusters[0]['t0']:.0f}s"
                         + (" (that copy is in the opening teaser)" if c.get("demoted") else "")
                         + (" (chosen copy is in the opening teaser)" if clusters[0].get("demoted") else ""))
    kept_untr = []
    for t in res.get("trans") or []:
        for j in t.get("kept_untranscribed") or []:
            kept_untr.append({"j": j, "word": E[j]["word"], "at": t["type"]})
    res.update({
        "kept_untranscribed": kept_untr,
        "details": details,
        "unmatched": unmatched,
        "coverage": coverage,
        "coverage_non_filler": 1 - len(unmatched_nf) / max(1, n_real),
        "n_matched": n_matched,
        "n_fuzzy": sum(1 for u in matched_i.values() if u[4] < 0.999),
        "confidence": conf,
        "confidence_label": ("high" if conf >= 0.9 else "medium" if conf >= 0.75 else "low") if details else "none",
        "flags": flags if details else ["no seed matches: Short not found in this episode"],
        "other_clusters": others,
        "kept": merge_ranges([[p["j_lo"], p["j_hi"]] for p in pieces]),
        "boundary": boundary_report(E, pieces) if pieces else None,
    })
    return res


# ---- file format (docs/spec/output_schema.md, anchorClip) ---------------------------------------------

METHOD = ("eval.map_shorts v2: 3-gram seed clusters (opening teaser demoted, later copy wins ties) -> "
          "timing-constrained Viterbi over fuzzy word candidates -> chance-excursion filter -> playback-speed "
          "fit and rescale -> timing-checked fuzzy gap fill; doubtful pieces kept out of the clip core")


def span(E, a, b):
    return {"start_word": a, "end_word": b, "start": round(E[a]["start"], 3), "end": round(E[b]["end"], 3)}


def derive_core(E, pieces):
    """region / edited_duration / reordered / n_blocks exactly as output_schema.md defines them."""
    lo = min(p["start_word"] for p in pieces)
    hi = max(p["end_word"] for p in pieces)
    edited = sum(E[p["end_word"]]["end"] - E[p["start_word"]]["start"] for p in pieces)
    reordered = any(b["start_word"] <= a["end_word"] for a, b in zip(pieces, pieces[1:]))
    jumps = sum(1 for a, b in zip(pieces, pieces[1:])
                if b["start_word"] <= a["end_word"]
                or E[b["start_word"]]["start"] - E[a["end_word"]]["end"] > SKIP_S)
    return span(E, lo, hi), round(edited, 3), reordered, 1 + jumps


def core_pieces(res):
    """Pieces that enter the clip core. Doubtful pieces (tiny with no content-word evidence, low similarity,
    extra audio) stay in mapping.pieces for review but never in pieces / region / n_blocks / edited_duration."""
    return [p for p, d in zip(res["pieces"], res["details"]) if d["quality"] != "doubtful"]


def to_anchor_clip(res, E, short_meta):
    """Returns (clip or None, reason when None)."""
    core = core_pieces(res)
    pieces = [span(E, p["j_lo"], p["j_hi"]) for p in core]
    for d in res["details"]:
        d["in_core"] = d["quality"] != "doubtful"
    n_out = sum(1 for d in res["details"] if not d["in_core"])
    notes = (f"coverage {res['coverage']:.1%} of Short words matched by text "
             f"({res['coverage_non_filler']:.1%} of non-filler words); {res['n_fuzzy']} fuzzy matches; "
             f"confidence {res['confidence_label']}")
    if n_out:
        notes += f"; {n_out} doubtful piece(s) kept out of the clip core"
    if res["flags"]:
        notes += ". Flags: " + " | ".join(res["flags"])
    kept = merge_ranges([[p["j_lo"], p["j_hi"]] for p in core])
    in_core = {j for a, b in kept for j in range(a, b + 1)}
    mapping = {
        "method": METHOD,
        "confidence": round(res["confidence"], 4),
        "short_words": res["n"],
        "matched_words": res["n_matched"],
        "notes": notes,
        "coverage": round(res["coverage"], 4),
        "coverage_non_filler": round(res["coverage_non_filler"], 4),
        "n_fuzzy_matches": res["n_fuzzy"],
        "confidence_label": res["confidence_label"],
        "time_scale": res.get("time_scale", 1.0),
        "flags": res["flags"],
        "pieces": res["details"],
        "kept_untranscribed": [k for k in res.get("kept_untranscribed", []) if k["j"] in in_core],
        "unmatched_short_words": res["unmatched"],
        "other_text_matches": res["other_clusters"],
        "boundary": boundary_report(E, core) if core else None,
    }
    clip = {"id": res["short_id"], "short_id": res["short_id"]}
    for k in ("url", "title"):
        if short_meta.get(k):
            clip[k] = short_meta[k]
    if short_meta.get("duration") is not None:
        clip["short_duration_secs"] = round(short_meta["duration"], 3)
    if not pieces:
        # schema requires >= 1 piece; such a Short is listed in the file's `unmapped` instead
        return None, ("no seed matches in this episode" if not res["pieces"]
                      else "every mapped piece is doubtful")
    region, edited, reordered, n_blocks = derive_core(E, pieces)
    clip.update({
        "pieces": pieces,
        "region": region,
        "edited_duration": edited,
        "reordered": reordered,
        "n_blocks": n_blocks,
        "text": " ".join(E[j]["word"] for p in pieces for j in range(p["start_word"], p["end_word"] + 1)),
        "kept_episode_words": kept,
        "n_kept_episode_words": sum(b - a + 1 for a, b in kept),
        "mapping": mapping,
    })
    return clip, None


# ---- review markdown ----------------------------------------------------------------------------------

def _clip(ws, max_words=40):
    if len(ws) <= max_words:
        return " ".join(ws)
    h = max_words // 2
    return " ".join(ws[:h]) + f" … [{len(ws) - 2 * h} words] … " + " ".join(ws[-h:])


def fmt_t(t):
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:05.2f}"


def review_short(res, clip, E):
    S = res["S"]
    sid = res["short_id"]
    L = [f"## {sid}", ""]
    if clip is None:
        L += ["**Not mapped**: " + ("; ".join(res["flags"]) or "no 3-word seed matched this episode."), ""]
        return L
    r = clip["region"]
    b = res["boundary"]
    L.append(f"- coverage **{res['coverage']:.0%}** ({res['n_matched']}/{res['n']} Short words matched by text; "
             f"non-filler {res['coverage_non_filler']:.0%}); fuzzy matches {res['n_fuzzy']}; confidence "
             f"**{res['confidence_label']}** ({res['confidence']:.2f})")
    L.append(f"- region {fmt_t(r['start'])}–{fmt_t(r['end'])} ({r['end'] - r['start']:.0f}s, words "
             f"{r['start_word']}–{r['end_word']}); **{len(clip['pieces'])} pieces**, {clip['n_blocks']} block(s); "
             f"kept {clip['n_kept_episode_words']} episode words, edited duration {clip['edited_duration']:.1f}s "
             f"(Short audio {clip.get('short_duration_secs', 0):.1f}s)")
    moves = [d for d in res["details"] if d.get("after", {}).get("type") == "backward"]
    L.append(f"- reordered: **{'yes' if clip['reordered'] else 'no'}**" +
             "".join(f"; after P{d['piece']} jumps back {d['after']['back_s']}s" for d in moves))
    L.append(f"- start on natural boundary: {'yes' if b['start_natural'] else 'T4-ok' if b['start_natural_t4'] else 'NO'}"
             f" (`{b['start_context']}`) · end: {'yes' if b['end_natural'] else 'NO'} (`{b['end_context']}`)")
    if res["other_clusters"]:
        L.append("- same text also found at: " + ", ".join(
            f"{fmt_t(c['t0'])}–{fmt_t(c['t1'])} ({c['n_short_words_explained']} words, "
            f"{'used' if c['used'] else 'ignored'}"
            f"{', duplicate of used match' if not c['used'] and c['duplicate_share'] >= 0.75 else ''}"
            f"{', lost to nearer copy' if c['lost_to_nearer_copy'] else ''})" for c in res["other_clusters"]))
    if res["flags"]:
        L.append("- flags: " + " | ".join(res["flags"]))
    L.append("")
    unit_by_i = {u[0]: u for u in res["units"]}
    unmatched_by_ctx = defaultdict(list)
    for u in res["unmatched"]:
        unmatched_by_ctx[u["context"]].append(u)

    def loose(ctx):
        if unmatched_by_ctx.get(ctx):
            L.append(f"> Short words with no episode match ({ctx}): " +
                     " ".join(f"**[+{u['word']}]**" for u in unmatched_by_ctx[ctx]))
            L.append("")

    loose("before piece 1")
    for d, p in zip(res["details"], res["pieces"]):
        a, bb = d["short_words"]
        trims = (f" → {d['offset_end_s']:+.2f}s ({d['pause_trims']} pause trims)" if d["pause_trims"] else "")
        L.append(f"**P{d['piece']}** · Short {d['short_time'][0]:.2f}–{d['short_time'][1]:.2f}s [w{a}–{bb}] ← "
                 f"episode {fmt_t(d['episode_time'][0])}–{fmt_t(d['episode_time'][1])} "
                 f"[w{d['episode_words'][0]}–{d['episode_words'][1]}] · offset {d['offset_s']:+.2f}s{trims} · "
                 f"{d['n_matched']}/{d['n_short_words']} matched, {d['n_exact']} exact · **{d['quality']}**"
                 + (f" · {'; '.join(d['issues'])}" if d["issues"] else ""))
        s_toks, used_j = [], set()
        for u in p["units"]:
            used_j.update(range(u[2], u[3] + 1))
        for i in range(a, bb + 1):
            if i in unit_by_i:
                u = unit_by_i[i]
                st = " ".join(S[k]["word"] for k in range(u[0], u[1] + 1))
                et = " ".join(E[k]["word"] for k in range(u[2], u[3] + 1))
                exact = u[4] >= 0.999 and u[0] == u[1] and u[2] == u[3]
                s_toks.append(st if exact else f"`{st}≈{et}`" if u[4] >= FILL_SIM else f"`{st}≠{et}`")
            elif i not in {x for u in res["units"] for x in range(u[0], u[1] + 1)}:
                s_toks.append(f"**[+{S[i]['word']}]**")
        e_toks = [E[j]["word"] if j in used_j else f"*({E[j]['word']})*"
                  for j in range(d["episode_words"][0], d["episode_words"][1] + 1)]
        L.append(f"- S: {' '.join(s_toks)}")
        L.append(f"- E: {' '.join(e_toks)}")
        mism = [u for ctx, us_ in unmatched_by_ctx.items() for u in us_
                if (ctx == f"inside piece {d['piece']}" or ctx.startswith(f"inside piece {d['piece']},"))
                and u.get("episode_words_at_time")]
        if mism:
            L.append("- same time, different words: " + ", ".join(
                f"{u['word']} ↔ {' '.join(x['word'] for x in u['episode_words_at_time'])}" for u in mism))
        af = d.get("after")
        if af:
            if af["type"] == "backward":
                L.append(f"- then **JUMP BACK** {af['back_s']}s (reorder)"
                         + (f", replays {af['overlap_words']} words" if af["overlap_words"] else ""))
            else:
                L.append(f"- then **{af['type'].upper()}** {af['n_words']} words / {af['source_gap_s']}s of source: "
                         f"~~{af['text']}~~" if af["n_words"] else
                         f"- then **{af['type'].upper()}** 0 words / {af['source_gap_s']}s")
        L.append("")
        nxt = d["piece"] + 1
        loose(f"between pieces {d['piece']} and {nxt}")
    loose(f"after piece {len(res['details'])}")
    # episode view
    lo, hi = r["start_word"], r["end_word"]
    L.append(f"<details><summary>Episode view {fmt_t(r['start'])}–{fmt_t(r['end'])}: ⟦Pn … Pn⟧ kept, "
             f"~~struck~~ cut</summary>")
    L.append("")
    starts, ends, kept = defaultdict(list), defaultdict(list), set()
    for d in res["details"]:
        starts[d["episode_words"][0]].append(d["piece"])
        ends[d["episode_words"][1]].append(d["piece"])
        kept.update(range(d["episode_words"][0], d["episode_words"][1] + 1))
    toks, cutrun = [], []

    def flush():
        if cutrun:
            toks.append("~~" + _clip(cutrun, 30) + "~~")
            cutrun.clear()

    for j in range(lo, hi + 1):
        for pn in starts.get(j, []):
            flush()
            toks.append(f"⟦P{pn}")
        if j in kept:
            flush()
            toks.append(E[j]["word"])
        else:
            cutrun.append(E[j]["word"])
        for pn in ends.get(j, []):
            toks.append(f"P{pn}⟧")
    flush()
    L.append(" ".join(toks))
    L.append("")
    L.append("</details>")
    L.append("")
    return L


def spec_stats(results, E):
    """Numbers product-lead asked for: natural boundaries (T4/T5), source gaps between pieces (S4),
    cold-open replay (O4)."""
    clips = [(r, c) for r, c in results if c is not None]
    n = len(clips)
    L = ["## Spec calibration (this episode)", ""]
    if not n:
        return L
    st = sum(r["boundary"]["start_natural"] for r, _ in clips)
    st4 = sum(r["boundary"]["start_natural_t4"] for r, _ in clips)
    en = sum(r["boundary"]["end_natural"] for r, _ in clips)
    pe = sum(r["boundary"]["piece_edges_natural"] for r, _ in clips)
    pt = sum(r["boundary"]["piece_edges_total"] for r, _ in clips)
    L.append(f"- Short starts on a natural boundary: {st}/{n} strict, {st4}/{n} with T4's 2-word allowance; "
             f"ends: {en}/{n}. Inner piece edges on a natural boundary: {pe}/{pt}.")
    gaps = [d["after"]["source_gap_s"] for r, _ in clips for d in r["details"]
            if d.get("after", {}).get("type") in ("cut", "skip")]
    buckets = [(0, 1), (1, 3), (3, 10), (10, 20), (20, 40), (40, 80), (80, 1e9)]
    hist = ", ".join(f"{a}–{b if b < 1e9 else '∞'}s: {sum(1 for g in gaps if a <= g < b)}" for a, b in buckets)
    L.append(f"- Forward source gaps between consecutive pieces ({len(gaps)} gaps): {hist}.")
    back = [(r["short_id"], d) for r, _ in clips for d in r["details"] if d.get("after", {}).get("type") == "backward"]
    for sid, d in back:
        L.append(f"- {sid}: jump back {d['after']['back_s']}s after P{d['piece']}; replayed words: "
                 f"{d['after']['overlap_words']}")
    L.append("")
    return L


# ---- CLI ----------------------------------------------------------------------------------------------

def sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def short_id_from_url(url):
    """YouTube Short / watch URL -> video id ('https://youtube.com/shorts/ID?feature=share' -> 'ID')."""
    from urllib.parse import parse_qs, urlparse
    u = urlparse(url.strip())
    q = parse_qs(u.query)
    if q.get("v"):
        return q["v"][0]
    parts = [x for x in u.path.split("/") if x]
    return parts[-1] if parts else ""


def teaser_ranges(tpath):
    """Opening-teaser word ranges from shotvi.segment (demoted as Short sources, spec R3). Empty if the
    segmenter is unavailable; the mapper then falls back to preferring the later of equal copies."""
    try:
        from shotvi.segment.pipeline import segment_file
        return [tuple(r) for r in segment_file(tpath).excluded_word_ranges(kinds=("teaser",))]
    except Exception as e:  # noqa: BLE001 - optional dependency; the fallback is safe
        print(f"warning: teaser detection unavailable ({type(e).__name__}: {e})", file=sys.stderr)
        return []


def run(vid_dir, out_dir):
    vid_dir = Path(vid_dir)
    vid = vid_dir.name
    tpath = vid_dir / f"{vid}_scribe_transcript.json"
    E, ed = load_words(tpath)
    teaser = teaser_ranges(tpath)
    ep = EpisodeIndex(E, demote_ranges=teaser)
    print(f"opening teaser (demoted): {teaser or 'none detected'}")
    sim = Sim()
    meta = {}
    cc = vid_dir / "creator_clips.json"
    if cc.exists():  # optional [{short_id?, url?, title?}]
        for x in json.loads(cc.read_text(encoding="utf-8")):
            key = x.get("short_id") or short_id_from_url(x.get("url") or "")
            meta[key] = x
    shorts = sorted((vid_dir / "creator_shorts").glob("*_scribe_transcript.json"), key=lambda p: p.name.lower())
    results, clips, unmapped = [], [], []
    for path in shorts:
        sid = path.name[: -len("_scribe_transcript.json")]
        S, sd = load_words(path)
        res = map_short(sid, S, ep, sim)
        m = dict(meta.get(sid, {}))
        m["duration"] = sd.get("audio_duration_secs")
        clip, reason = to_anchor_clip(res, E, m)
        if clip is not None:
            res["boundary"] = clip["mapping"]["boundary"]
        if clip is not None and res["coverage"] < MIN_COVERAGE_TO_WRITE:
            # Shorts from another episode still pick up ~10-30% chance matches on common Tenglish phrases
            # (negative control); never write those as anchors.
            reason = (f"coverage {res['coverage']:.0%} < {MIN_COVERAGE_TO_WRITE:.0%}: probably not from this "
                      "episode")
            clip = None
        if clip is None:
            res["flags"].insert(0, f"not written: {reason}")
            unmapped.append({"short_id": sid, "coverage": round(res["coverage"], 4), "reason": reason})
        results.append((res, clip))
        if clip is not None:
            clips.append(clip)
        print(f"{sid}: coverage {res['coverage']:.1%}, {len(res['pieces'])} pieces, "
              f"confidence {res['confidence_label']} ({res['confidence']:.2f})")
    doc = {
        "schema_version": "1.1",
        "kind": "anchors",
        "video_id": vid,
        "derived_params": {"skip_s": SKIP_S},
        "transcript": {"path": tpath.as_posix(), "sha256": sha256_file(tpath), "n_words": len(E),
                       "audio_duration_secs": ed.get("audio_duration_secs")},
        "generator": METHOD,
        "clips": clips,
        # additive (not yet in clip_proposal.schema.json): Shorts that exist but could not be mapped. Recall
        # counts them in its denominator so a Short never silently disappears.
        "unmapped": unmapped,
    }
    review = [f"# Short → episode mapping review: {vid}", "",
              "Generated by `python -m eval.map_shorts`. Pieces are listed in Short (playback) order. Inside a "
              "piece, (episode time − Short time) is constant; *pause trims* shift it without removing words. "
              "Legend: `short≈episode` fuzzy match · **[+word]** Short word with no episode match · "
              "`short≠episode` same time, different words (kept on timing at a cut edge) · "
              "*(word)* episode word inside a piece that the Short transcript did not write out (audio present, "
              "proven by timing) · ~~struck~~ episode words the creator cut.", "",
              "| Short | coverage | pieces | region | edited | reordered | confidence | flags |",
              "|---|---|---|---|---|---|---|---|"]
    for res, clip in results:
        if clip is None:
            review.append(f"| {res['short_id']} | {res['coverage']:.0%} | - | - | - | - | not mapped | "
                          f"{'; '.join(res['flags'])[:80]} |")
            continue
        r = clip["region"]
        review.append(f"| {res['short_id']} | {res['coverage']:.0%} | {len(clip['pieces'])} | "
                      f"{fmt_t(r['start'])}–{fmt_t(r['end'])} | {clip['edited_duration']:.0f}s | "
                      f"{'yes' if clip['reordered'] else 'no'} | {res['confidence_label']} "
                      f"({res['confidence']:.2f}) | {len(res['flags'])} |")
    review.append("")
    review.extend(spec_stats(results, E))
    for res, clip in results:
        review.extend(review_short(res, clip, E))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / f"{vid}.json"
    out_json.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / f"{vid}_review.md").write_text("\n".join(review), encoding="utf-8")
    print(f"wrote {out_json} and {out_dir / (vid + '_review.md')}")
    try:
        from eval.schema_check import check_file
    except ImportError:
        return
    errs = check_file(out_json)
    print("schema check: " + ("OK" if not errs else f"{len(errs)} problem(s)"))
    for e in errs[:20]:
        print("  -", e)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Map creator Shorts onto episode word indices.")
    ap.add_argument("vid_dir", help="scribe_dataset/<video_id>")
    ap.add_argument("--out-dir", default="data/anchors")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    run(a.vid_dir, a.out_dir)


if __name__ == "__main__":
    sys.exit(main())
