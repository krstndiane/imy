"""Run with:  pytest backend/tests -q   (from the project root)

Tests the parts that don't need a real screenshot: the parser's speaker
logic, the style analyser, and the reply engine.
"""
import random

from backend import engine, parser, style
from backend.ocr import Line


def L(text, left, top, right, bottom):
    return Line(text, left, top, right, bottom, 90.0)


def test_text_export_whatsapp_and_plain():
    text = "[12/31/24, 10:15 PM] Mia: miss u\n[12/31/24, 10:16 PM] Me: me too\ncome home soon\n"
    msgs, review = parser.parse_text_export(text, ["Mia"])
    assert [m["speaker"] for m in msgs] == ["person", "user"]
    assert msgs[1]["text"] == "me too come home soon"
    assert not review


def test_clean_text_strips_trailing_time_and_pipe():
    assert parser.clean_text("| love you  10:32 PM vv") == "I love you"


def test_chrome_filter():
    assert parser.is_chrome("Today 4:32 PM")
    assert parser.is_chrome("Delivered")
    assert not parser.is_chrome("see you tomorrow")


def test_bubble_sides():
    import numpy as np
    W, H = 1000, 1400
    rgb = np.full((H, W, 3), 255, dtype=np.uint8)
    lines = [
        L("hey are you free", 150, 300, 520, 340),       # left  -> person
        L("yes! omw", 700, 420, 930, 460),               # right -> user
        L("ok see you", 150, 560, 460, 600),             # left  -> person
        L("bring snacks", 640, 700, 930, 740),           # right -> user
    ]
    msgs = parser.parse_bubbles(lines, (W, H), rgb)
    assert [m["speaker"] for m in msgs] == ["person", "user", "person", "user"]


def test_multiline_bubble_is_merged():
    lines = [L("this is a long", 150, 300, 500, 340), L("wrapped message", 150, 352, 480, 392)]
    bubbles = parser.group_bubbles(lines, 1000)
    assert len(bubbles) == 1 and bubbles[0].text == "this is a long wrapped message"


def test_header_layout_discord():
    lines = [
        L("Mia Today at 4:32 PM", 100, 100, 500, 130),
        L("hello there", 100, 140, 300, 170),
        L("Sam Today at 4:33 PM", 100, 220, 500, 250),
        L("hi!", 100, 260, 160, 290),
    ]
    assert parser.looks_like_header_layout(lines)
    msgs, review = parser.parse_headers(lines, (1000, 800), ["Mia"])
    assert [(m["speaker"], m["text"]) for m in msgs] == [("person", "hello there"), ("user", "hi!")]
    assert not review


def _messages():
    convo = [
        ("user", "hi"), ("person", "hey love 😊"),
        ("user", "what are you eating"), ("person", "pancakes haha"),
        ("user", "i miss you"), ("person", "miss u too love 😊"),
        ("user", "goodnight"), ("person", "night night love 😊"),
        ("user", "how was work"), ("person", "tiring, but okay"),
    ]
    return [{"speaker": s, "text": t, "screenshot_id": 1} for s, t in convo]


def _person():
    return {"id": 99, "name": "Mia", "data_version": 1, "use_llm": False,
            "answers": {"q1": "Mia", "q3": "love", "q11": "short messages", "q12": "emojis"}}


def test_engine_retrieval_and_intent(monkeypatch):
    monkeypatch.setenv("IMY_EMBEDDER", "tfidf")
    engine.VectorSpace._model, engine.VectorSpace._failed = None, False
    r = engine.generate_reply(_person(), _messages(), [], "what are you eating?")
    assert r.source == "retrieval" and "pancakes" in r.bubbles[0]
    r = engine.generate_reply(_person(), _messages(), [], "goodnight!")
    assert r.bubbles and r.source in ("retrieval", "intent")


def test_engine_with_no_screenshots(monkeypatch):
    monkeypatch.setenv("IMY_EMBEDDER", "tfidf")
    p = {**_person(), "id": 100}
    r = engine.generate_reply(p, [], [], "hello")
    assert r.source == "template" and r.bubbles[0]
    r = engine.generate_reply(p, [], [], "tell me about your day")
    assert r.source == "empty"


def test_style_from_messages():
    st = style.build_style(["haha ok 😂", "sure lol", "see you later 😂", "ok"] * 5, {"q3": "babe"})
    assert st["lowercase_start"] == 1.0 and st["emoji_ratio"] > 0.3 and st["median_words"] <= 3
    assert any("lowercase" in s for s in style.describe_style(st))
    assert style.apply_style("Miss you too.", st, random.Random(1)).startswith("miss")


def test_header_layout_splits_short_lines_but_joins_wrapped_ones():
    lines = [
        L("Mia Today at 4:32 PM", 100, 100, 500, 130),
        L("a long message that fills the whole row and", 100, 140, 900, 170),   # reaches the wrap edge
        L("wraps onto a second line", 100, 176, 420, 206),
        L("short one", 100, 212, 260, 242),                                      # ended early: new message
        L("Sam Today at 4:33 PM", 100, 300, 500, 330),
        L("ok", 100, 340, 140, 370),
    ]
    msgs, _ = parser.parse_headers(lines, (1000, 800), ["Mia"])
    assert [m["text"] for m in msgs] == [
        "a long message that fills the whole row and wraps onto a second line", "short one", "ok"]
