"""Run with:  pytest backend/tests -q   (from the project root)

Screen-aware reading: finding the chat area and the bubbles in a drawn "screenshot"
(no OCR needed), and the rules that turn bubbles into messages.
"""
import numpy as np
from PIL import Image, ImageDraw

from backend import ocr, parser
from backend.ocr import Line, Region, Screen

W, H = 1170, 2532


def fake_messenger():
    """Dark Messenger-like screen: title bar text, a full-width pinned bar, an avatar, one grey bubble
    (theirs, left), one blue bubble (yours, right), a round scroll button, and the message box."""
    img = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 280, W, 410], fill=(56, 56, 56))                          # pinned message bar
    d.ellipse([36, 700, 120, 784], fill=(180, 170, 150))                      # avatar
    d.rounded_rectangle([156, 640, 700, 760], radius=50, fill=(51, 51, 52))  # their bubble
    d.rounded_rectangle([600, 900, 1146, 1020], radius=50, fill=(50, 107, 246))   # your bubble
    d.ellipse([525, 1500, 645, 1620], fill=(51, 51, 52))                      # scroll-down button
    d.rounded_rectangle([156, 1100, 330, 1210], radius=50, fill=(51, 51, 52))    # short "oki" bubble
    d.rounded_rectangle([564, 2290, 1004, 2410], radius=60, fill=(51, 51, 52))   # message box
    return np.asarray(img)


def test_chat_area_skips_title_bar_pinned_bar_and_message_box():
    rgb = fake_messenger()
    lines = [Line("Turn on active status", 290, 200, 700, 250, 95), Line("(Message © ib", 600, 2313, 900, 2379, 70)]
    top, bottom = ocr.chat_area(rgb, lines, ["Lawrence"])
    assert 410 <= top < 640 and 2200 < bottom <= 2290


def test_bubbles_found_but_not_avatars_or_buttons():
    rgb = fake_messenger()
    found = ocr.find_bubbles(rgb, 411, 2260)
    boxes = [(r.x0 // 10, r.y0 // 10) for r in found]
    assert len(found) == 3, boxes                       # grey, blue and the short "oki" bubble
    assert all(r.x0 > 140 for r in found)               # the avatar at the far left isn't one
    assert not any(500 < r.x0 < 560 for r in found)     # nor is the round scroll button


def _screen(bubbles):
    return Screen(lines=[l for b in bubbles for l in b.lines], size=(W, H), rgb=None, top=400, bottom=2260, bubbles=bubbles)


def _bubble(x0, x1, text, color, conf=95, cut=""):
    r = Region(x0, 1000, x1, 1100, color, cut)
    r.lines = [Line(text, x0 + 20, 1020, x1 - 20, 1080, conf)]
    return r


def test_one_message_per_bubble_sides_and_skips():
    grey, blue = (51, 51, 52), (50, 107, 246)
    screen = _screen([
        _bubble(156, 900, "pag isipan mo muna", grey),
        _bubble(800, 1146, "end of nov diba?", blue),
        _bubble(156, 900, "hahaambalar jy,", grey, conf=65),         # covered by a button: garbled
        _bubble(600, 1146, "011341364666", blue, cut="bottom"),     # runs off the screen
        _bubble(156, 400, "Turn on active status", grey),           # app text that slipped into a box
    ])
    res = parser.parse_screen(screen, ["Lawrence"])
    assert [(m["speaker"], m["text"]) for m in res["messages"]] == [
        ("person", "pag isipan mo muna"), ("user", "end of nov diba?")]


def test_overlap_between_scrolled_screenshots_is_dropped():
    m = lambda s, t: {"speaker": s, "text": t}
    earlier = [m("person", "haha"), m("user", "na ano"), m("person", "oki")]
    new = [m("person", "oki"), m("user", "pag hindi?"), m("person", "good deal")]
    kept, skipped = parser.trim_overlap(new, [earlier])
    assert skipped == 1 and kept[0]["text"] == "pag hindi?"
    middle = [m("user", "hi"), m("person", "haha"), m("user", "bye")]
    kept, skipped = parser.trim_overlap([m("user", "hello"), m("person", "haha")], [middle])
    assert skipped == 0                                  # a "haha" in the middle of another screenshot is left alone
