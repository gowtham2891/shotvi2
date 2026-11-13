"""selection_v2.vertical: finding the real picture inside the frame (baked-in black bars)."""
from selection_v2 import vertical as V

W, H = 1920, 1080
BARS = (0, 108, 1920, 864)          # a 2.22:1 picture in a 16:9 frame (Couple Goals, Hello Madam)


def test_most_common_box_wins_over_dark_scenes():
    dark = (0, 300, 1920, 480)       # a dark scene fools the detector once
    assert V.choose_box([BARS] * 7 + [dark, (0, 110, 1920, 860)], W, H) == BARS


def test_no_bars_means_full_frame():
    assert V.choose_box([(0, 0, 1920, 1080)] * 5, W, H) == (0, 0, W, H)
    assert V.choose_box([], W, H) == (0, 0, W, H)


def test_thin_bars_are_ignored():
    assert V.choose_box([(0, 8, 1920, 1064)] * 5, W, H) == (0, 0, W, H)      # 0.7% bars: keep the full frame


def test_tiny_box_is_not_trusted():
    assert V.choose_box([(400, 300, 800, 400)] * 5, W, H) == (0, 0, W, H)


def test_side_bars_are_trimmed():
    assert V.choose_box([(240, 0, 1440, 1080)] * 4, W, H) == (240, 0, 1440, 1080)


def test_decide_falls_back_to_the_picture_middle():
    follow, x, why, decided = V.decide([], [], [], {}, 486, 1920, mid=900)
    assert (follow, x, decided) == ([], 900, False)
