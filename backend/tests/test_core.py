"""Run with:  pytest backend/tests -q   (from the project root)

Tests the text parser, the style analyser, and the reply engine.
"""
import random

from backend import engine, parser, style


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
    monkeypatch.setattr(engine, "REUSE_LINES", True)   # the older answer-with-their-own-lines mode
    monkeypatch.setenv("IMY_EMBEDDER", "tfidf")
    engine.VectorSpace._model, engine.VectorSpace._failed = None, False
    r = engine.generate_reply(_person(), _messages(), [], "what are you eating?")
    assert r.source == "retrieval" and "pancakes" in r.bubbles[0]
    r = engine.generate_reply(_person(), _messages(), [], "goodnight!")
    assert r.bubbles and r.source in ("retrieval", "intent")


def test_replies_are_written_fresh_not_copied(monkeypatch):
    """Default mode: screenshots teach style only; replies answer what was said."""
    monkeypatch.setenv("IMY_EMBEDDER", "tfidf")
    theirs = {m["text"].lower() for m in _messages() if m["speaker"] == "person"}
    history = []
    for said, expect in [("what are you eating?", None), ("how are you", "you"), ("can i tell you something?", None), ("thanks", None)]:
        r = engine.generate_reply({**_person(), "id": 202}, _messages(), history, said)
        assert r.source in ("smalltalk", "personality") and r.bubbles[0]
        assert r.bubbles[0].lower() not in theirs
        if expect:
            assert expect in r.bubbles[0].lower()
        history += [{"role": "user", "text": said}, {"role": "person", "text": r.bubbles[0]}]


def test_engine_with_no_screenshots(monkeypatch):
    monkeypatch.setattr(engine, "REUSE_LINES", True)
    monkeypatch.setenv("IMY_EMBEDDER", "tfidf")
    p = {**_person(), "id": 100}
    r = engine.generate_reply(p, [], [], "hello")
    assert r.source == "template" and r.bubbles[0]
    r = engine.generate_reply(p, [], [], "tell me about your day")
    assert r.source == "personality" and r.bubbles[0]


def test_style_from_messages():
    st = style.build_style(["haha ok 😂", "sure lol", "see you later 😂", "ok"] * 5, {"q3": "babe"})
    assert st["lowercase_start"] == 1.0 and st["emoji_ratio"] > 0.3 and st["median_words"] <= 3
    assert any("lowercase" in s for s in style.describe_style(st))
    assert style.apply_style("Miss you too.", st, random.Random(1)).startswith("miss")


import pytest

from backend import personality


@pytest.mark.parametrize("text,act", [
    ("what do you think about that?", "opinion"),
    ("are you coming tonight?", "yes_no"),
    ("why is the sky red", "wh"),
    ("i'm so tired and stressed today", "down"),
    ("guess what, i passed my exam", "good_news"),
    ("hahaha that was so funny", "funny"),
    ("can you help me with something", "request"),
    ("ok", "short_remark"),
    ("i went to the market this morning and bought mangoes", "statement"),
])
def test_classify_act(text, act):
    assert personality.classify_act(text) == act


def _compose(answers, text, seed=1, texts=()):
    st = style.build_style(list(texts), answers)
    return personality.compose(text, st, answers, random.Random(seed))


def test_never_admits_missing_data():
    for q in ["what do you think about that?", "hello?", "i'm sad", "ok", "tell me a story about your trip"]:
        reply, _ = _compose({}, q)
        assert "data" not in reply.lower() and "screenshot" not in reply.lower() and reply


def test_personality_changes_the_reply_pool():
    q = "what do you think about that?"
    logical = {_compose({"q8": "very logical and practical"}, q, s)[0] for s in range(60)}
    emotional = {_compose({"q8": "driven by her feelings"}, q, s)[0] for s in range(60)}
    assert "what are the pros and cons though?" in logical and "what are the pros and cons though?" not in emotional
    assert "something about it feels right" in emotional and "something about it feels right" not in logical


def test_short_blunt_stays_brief():
    ans = {"q11": "short messages", "q15": "blunt and direct", "q8": "logical"}
    for seed in range(30):
        reply, _ = _compose(ans, "what do you think about that?", seed)
        assert len(reply.split()) <= 4, reply


def test_endearment_emoji_and_casing_from_style():
    ans = {"q3": "love", "q12": "lots of emojis", "q15": "playful", "q11": "short"}
    replies = [_compose(ans, "i got the job!", seed)[0] for seed in range(40)]
    assert any("love" in r for r in replies)
    assert any(any(ord(c) > 0x2000 for c in r) for r in replies)   # emoji appear
    assert all(r == r.lower() or r[0].islower() or any(ord(c) > 0x2000 for c in r) for r in replies)


def test_formal_person_gets_capital_and_period():
    reply, _ = _compose({"q12": "very formal and proper", "q11": "short"}, "what do you think about that?")
    assert reply[0].isupper() and reply[-1] in ".!?"


def test_topic_they_care_about():
    reply, why = _compose({"q7": "family and cooking"}, "my family is visiting tomorrow")
    assert "Q7" in why


def test_filler_not_used_after_a_question(monkeypatch):
    monkeypatch.setattr(engine, "REUSE_LINES", True)
    monkeypatch.setenv("IMY_EMBEDDER", "tfidf")
    msgs = [{"speaker": s, "text": t, "screenshot_id": 1} for s, t in
            [("person", "hahaha"), ("person", "true"), ("person", "oo")]]   # only fillers, no exchanges
    p = {**_person(), "id": 101}
    r = engine.generate_reply(p, msgs, [], "what do you think about that?")
    assert r.source == "personality"
    r = engine.generate_reply(p, msgs, [], "i was at the market today")
    assert r.source == "filler"


def test_endearment_goes_before_question_mark_and_formal_sentences_are_capitalised():
    ans = {"q3": "babe", "q15": "blunt"}
    st = {**style.build_style([], ans), "endearment_rate": 1.0}
    reply, _ = personality.compose("i'm so tired today", st, ans, random.Random(5))
    assert "? babe" not in reply
    reply, _ = _compose({"q12": "formal", "q11": "long paragraphs"}, "what do you think about that?")
    assert ". T" in reply or reply.count(".") == 1 and reply[0].isupper()
