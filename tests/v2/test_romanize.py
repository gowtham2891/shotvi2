"""Telugu in English letters: the renderer's rules (selection_v2/romanize.py) on the shared word list, which the
browser copy (web/src/lib/romanize.test.jsx) checks too, so preview and download spell the same."""
import json
from pathlib import Path

import pytest

from selection_v2 import captions as C
from selection_v2.romanize import romanize

CASES = json.loads((Path(__file__).parent / "romanize_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["te"] for c in CASES])
def test_shared_cases(case):
    assert romanize(case["te"]) == case["en"]


def test_founder_example_and_casual_rules():
    assert romanize("ఏంటి") == "enti"
    assert romanize("నాకు") == "naaku"           # long vowel doubled inside the word
    assert romanize("గోలేంట్రా?") == "golentra?"  # final long aa written a, punctuation kept
    assert romanize("సంబంధం") == "sambandham"    # nasal before b and at the end -> m


def test_captions_use_it_only_when_asked():
    s = C.clean_settings({"script": "latin"})
    assert C._tok("ఏంటి", s) == "enti" and C._tok("ఏంటి", C.clean_settings({})) == "ఏంటి"
    with pytest.raises(ValueError):
        C.clean_settings({"script": "hindi"})
