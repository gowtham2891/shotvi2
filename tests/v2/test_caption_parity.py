"""The renderer's caption layout must equal tests/v2/caption_parity.json, which the browser engine is also tested
against (web/src/lib/captionEngine.test.jsx). Changing cues/wrap/romanize in one place fails one of the two tests:
change both engines, then regenerate the file (see the generator in the commit that added it)."""
import json
from pathlib import Path

import pytest

from selection_v2 import captions as C

CASES = json.loads((Path(__file__).parent / "caption_parity.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_layout_unchanged(case):
    s = C.clean_settings(case["settings"])
    got = [{"start": c["start"], "end": c["end"], "lines": C.wrap([C._tok(w["tok"], s) for w in c["words"]], s)}
           for c in C.cues(case["words"], s, case["breaks"])]
    assert got == case["expect"]
