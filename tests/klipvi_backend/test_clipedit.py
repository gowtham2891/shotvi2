"""backend.clipedit: moving a clip's outer edges and keeping the caption edits."""
import pytest

from backend import clipedit as E

FLOW = {"lines": [{"id": f"L{i:04d}", "kind": "speech", "start": float(10 * i), "end": float(10 * i + 8), "text": f"line {i}"}
                  for i in range(30)]}
ONE = [{"start": 100.0, "end": 140.0, "lines": ["L0010", "L0011", "L0012", "L0013"]}]
TWO = [{"start": 100.0, "end": 120.0, "lines": ["L0010", "L0011"]}, {"start": 150.0, "end": 170.0, "lines": ["L0015", "L0016"]}]


def test_retime_moves_only_the_outer_edges_and_their_lines():
    new = E.retime(FLOW, TWO, 85.0, 182.0, 400)
    assert new[0]["start"] == 85.0 and new[0]["end"] == 120.0 and new[1]["start"] == 150.0 and new[1]["end"] == 182.0
    assert new[0]["lines"][0] == "L0008" and new[1]["lines"][-1] == "L0018"
    assert TWO[0]["start"] == 100.0                     # the input is not changed


@pytest.mark.parametrize("start,end,why", [(-1, 140, "inside"), (100, 401, "inside"), (139.5, 140, "first part"), (100, 100.5, "last part"),
                                           (100, 102.5, "at least"), (0, 199, "at most")])
def test_retime_refuses(start, end, why):
    with pytest.raises(ValueError, match=why):
        E.retime(FLOW, ONE if why != "at most" else [{"start": 0.0, "end": 199.0, "lines": []}], start, end, 400)


def test_draft_keeps_edits_and_takes_new_words_from_the_transcript():
    # old clip clock: origin 99.9; an edited word at 0.5 s, a split before word 1
    draft = {"words": [{"tok": "EDITED", "start": 0.5, "end": 0.9}, {"tok": "b", "start": 2.0, "end": 2.4}], "breaks": [1]}
    new = E.retime(FLOW, ONE, 95.0, 140.0, 400)          # 5 s earlier: everything old moves +5 s
    words = [{"tok": "head", "start": 1.0, "end": 1.5}, {"tok": "x", "start": 5.5, "end": 5.9}, {"tok": "y", "start": 7.0, "end": 7.4}]
    out = E.rebase_draft(draft, ONE, new, words)
    toks = [(w["tok"], w["start"]) for w in out["words"]]
    assert toks[0] == ("head", 1.0)                       # new part: transcript word
    assert ("EDITED", 5.5) in toks and ("b", 7.0) in toks  # old edits, moved by 5 s
    assert ("x", 5.5) not in toks                          # the transcript's copy of an edited stretch is not doubled
    assert out["breaks"] == [toks.index(("b", 7.0))]       # the split still sits before "b"


def test_draft_with_a_cut_moves_edits_on_both_sides_by_the_same_amount():
    # TWO plays 100-120 then 150-170 (30 s cut); on the old clock part 2 starts at 20.4 s (20 s + both pads)
    draft = {"words": [{"tok": "BEFORE", "start": 5.0, "end": 5.4}, {"tok": "AFTER", "start": 25.0, "end": 25.4}], "breaks": [1]}
    new = E.retime(FLOW, TWO, 96.0, 170.0, 400)          # 4 s earlier
    out = E.rebase_draft(draft, TWO, new, [{"tok": "head", "start": 1.0, "end": 1.5}])
    toks = [(w["tok"], w["start"]) for w in out["words"]]
    assert toks == [("head", 1.0), ("BEFORE", 9.0), ("AFTER", 29.0)]
    assert out["breaks"] == [2]


def test_draft_trim_drops_words_now_outside():
    draft = {"words": [{"tok": "gone", "start": 1.0, "end": 1.4}, {"tok": "kept", "start": 20.0, "end": 20.4}], "breaks": []}
    new = E.retime(FLOW, ONE, 110.0, 140.0, 400)          # 10 s later: old words move -10 s
    out = E.rebase_draft(draft, ONE, new, [])
    assert [w["tok"] for w in out["words"]] == ["kept"] and out["words"][0]["start"] == 10.0


# ------------------------------------------------------------------------------------------- changing the cuts inside
def test_repiece_keeps_unchanged_parts_and_recomputes_changed_ones():
    new = E.repiece(FLOW, TWO, [[100, 120], [145, 170]], 400)        # the cut is 5 s smaller
    assert new[0] is not TWO[0] and new[0] == TWO[0]                  # unchanged part: same lines
    assert new[1]["start"] == 145 and new[1]["lines"] == ["L0014", "L0015", "L0016"]


def test_repiece_brings_a_cut_back_by_joining_the_parts():
    new = E.repiece(FLOW, TWO, [[100, 170]], 400)
    assert len(new) == 1 and new[0]["lines"] == [f"L00{i}" for i in range(10, 17)]
    assert len(E.repiece(FLOW, TWO, [[100, 150.02], [150, 170]], 400)) == 1    # touching parts are one part


def test_repiece_adds_a_cut():
    new = E.repiece(FLOW, ONE, [[100, 118], [130, 140]], 400)
    assert [(p["start"], p["end"]) for p in new] == [(100, 118), (130, 140)]
    assert new[0]["lines"] == ["L0010", "L0011"] and new[1]["lines"] == ["L0013"]


@pytest.mark.parametrize("spans,why", [([], "at least one"), ([[100, 100.1]], "at least 0.2"), ([[-1, 10]], "inside"),
                                       ([[100, 101]], "at least 3"), ([[0, 100], [110, 200]], "at most"), ([["x", 1]], "start and an end")])
def test_repiece_refuses(spans, why):
    with pytest.raises(ValueError, match=why):
        E.repiece(FLOW, ONE, spans, 400)


def test_draft_follows_a_cut_brought_back_and_a_new_cut():
    # TWO: part 2 starts at 20.4 s on the old clock. Bring the cut back -> AFTER (old 25.0 = source 154.5) is now at
    # 154.5 - 99.9 = 54.6 s, and the brought-back stretch's words come from the transcript.
    draft = {"words": [{"tok": "BEFORE", "start": 5.0, "end": 5.4}, {"tok": "AFTER", "start": 25.0, "end": 25.4}], "breaks": [1]}
    joined = E.repiece(FLOW, TWO, [[100, 170]], 400)
    words = [{"tok": "mid", "start": 30.0, "end": 30.4}, {"tok": "dup", "start": 5.0, "end": 5.4}]
    out = E.rebase_draft(draft, TWO, joined, words)
    assert [(w["tok"], w["start"]) for w in out["words"]] == [("BEFORE", 5.0), ("mid", 30.0), ("AFTER", 54.6)]
    assert out["breaks"] == [2]
    # a new cut over BEFORE (source 104.9) drops it; AFTER stays
    cut = E.repiece(FLOW, TWO, [[100, 103], [108, 120], [150, 170]], 400)
    out = E.rebase_draft(draft, TWO, cut, [])
    assert [w["tok"] for w in out["words"]] == ["AFTER"]
