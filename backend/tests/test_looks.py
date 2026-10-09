"""Run with:  pytest backend/tests -q   (from the project root)

Recognising the app a screenshot came from, its look (dark/light, bubble colours) and its chat name,
plus quoted replies and group chats. Screens are built by hand, so no OCR is needed.
"""
import numpy as np

from backend import engine, llm, looks, parser
from backend.ocr import Line, Region, Screen

W, H = 1170, 2532


def L(text, x0, y0, x1, y1, conf=92):
    return Line(text, x0, y0, x1, y1, conf)


def screen(bg, them=None, user=None, avatars=0, lines=(), extra_regions=()):
    rgb = np.zeros((H, W, 3), dtype=np.uint8)
    rgb[:] = bg
    regions = []
    if them:
        regions.append(Region(60, 700, 600, 800, them, "", [L("hello there", 100, 730, 400, 770)]))
    if user:
        regions.append(Region(600, 900, 1140, 1000, user, "", [L("hi po", 900, 930, 1100, 970)]))
    regions += list(extra_regions)
    return Screen(list(lines) + [l for r in regions for l in r.lines], (W, H), rgb, 420, 2260, regions, bg,
                  [(20, 0, 80, 60)] * avatars)


def platform_of(sc, text=""):
    sides = [parser.bubble_side(r, W) for r in sc.bubbles]
    return looks.analyze(sc, sides, text)


def test_recognises_apps_from_colours_words_and_layout():
    m = platform_of(screen((0, 0, 0), (51, 51, 52), (18, 109, 255), avatars=2), "Turn on active status")
    assert m["platform"] == "messenger" and m["mode"] == "dark" and m["user"] == "#126dff"
    assert platform_of(screen((0, 0, 0), (38, 38, 40), (72, 146, 247)), "iMessage")["platform"] == "imessage"
    assert platform_of(screen((239, 234, 226), (255, 255, 255), (217, 253, 211)))["platform"] == "whatsapp"
    assert platform_of(screen((14, 22, 33), (24, 37, 51), (43, 82, 120)))["platform"] == "telegram"
    wa = platform_of(screen((11, 20, 26), (32, 44, 51), (0, 92, 75)))
    assert wa["platform"] == "whatsapp" and wa["mode"] == "dark" and wa["user_ink"] == "#ffffff"


def test_words_beat_colours_when_they_disagree():
    # A custom Messenger theme (pink bubbles) on white: the words give it away.
    m = platform_of(screen((255, 255, 255), (240, 240, 240), (255, 80, 160), avatars=1), "Active now\nAa")
    assert m["platform"] == "messenger"


def test_chat_name_and_wrong_chat_warning():
    title = L("Lawrence Panes", 300, 148, 700, 185)
    status = L("Active 5m ago", 300, 190, 600, 215)
    pinned = L("Fully Booked SM North — Level 5", 200, 300, 900, 345)
    sc = screen((0, 0, 0), (51, 51, 52), (18, 109, 255), lines=[L("11:50", 60, 58, 160, 98), title, status, pinned])
    assert looks.chat_title(sc) == "Lawrence Panes"
    assert looks.same_person("Lawrence Panes", ["law", "Lawrence"]) is True
    assert looks.same_person("Ana Cruz", ["law", "Lawrence"]) is False
    assert looks.same_person("", ["law"]) is None


def test_quoted_reply_is_skipped():
    label = L("You replied to Lawrence", 700, 600, 1100, 630)
    quote = Region(650, 640, 1140, 730, (40, 40, 40), "", [L("tara gym bukas", 700, 665, 1050, 705)])
    reply = Region(700, 745, 1140, 830, (18, 109, 255), "", [L("sige ba", 900, 770, 1100, 810)])
    sc = screen((0, 0, 0), (51, 51, 52), None, lines=[label], extra_regions=[quote, reply])
    out = parser.parse_screen(sc, ["Lawrence"])
    assert [(m["speaker"], m["text"]) for m in out["messages"]] == [("person", "hello there"), ("user", "sige ba")]


def test_group_chat_keeps_only_the_person_and_you():
    mia = Region(60, 700, 600, 790, (51, 51, 52), "", [L("ako na bahala", 100, 725, 500, 765)])
    law = Region(60, 900, 600, 990, (51, 51, 52), "", [L("g ako bukas", 100, 925, 500, 965)])
    me = Region(600, 1100, 1140, 1190, (18, 109, 255), "", [L("sige sige", 900, 1125, 1100, 1165)])
    names = [L("Mia", 70, 655, 140, 690), L("Lawrence", 70, 855, 260, 890)]
    sc = screen((0, 0, 0), lines=names, extra_regions=[mia, law, me])
    out = parser.parse_screen(sc, ["Lawrence"])
    assert out["meta"]["group"] and out["meta"]["senders"] == ["Lawrence", "Mia"]
    assert [(m["speaker"], m["text"]) for m in out["messages"]] == [("person", "g ako bukas"), ("user", "sige sige")]


def test_look_summary_prefers_the_chosen_app_and_its_colours():
    metas = [{"platform": "imessage", "confidence": 1, "mode": "dark", "user": "#4891f7", "them": "#262628"},
             {"platform": "messenger", "confidence": 1, "mode": "dark", "user": "#126cff", "them": "#333334"},
             {"platform": "messenger", "confidence": 1, "mode": "dark", "user": "#126cff", "them": "#333334"}]
    look = looks.summarize(metas)
    assert look["detected"] == "messenger" and look["user"] == "#126cff"
    assert looks.summarize(metas, "imessage")["them"] == "#262628"


def _person_and_msgs():
    person = {"id": 777, "name": "Lawrence", "data_version": 1, "answers": {"q1": "Lawrence", "q2": "law"}, "platform": "messenger"}
    msgs = []
    for i, (a, b) in enumerate([("pagod ako", "pahinga ka muna bru"), ("tara gym", "g ako bukas"), ("kain ka na", "di pa eh hahaha")] * 3):
        msgs += [{"screenshot_id": i, "speaker": "user", "text": a, "shot_platform": "messenger"},
                 {"screenshot_id": i, "speaker": "person", "text": b, "shot_platform": "messenger"}]
    return person, msgs


def test_ai_only_when_the_trained_data_isnt_enough(monkeypatch):
    person, msgs = _person_and_msgs()
    asked = []

    def fake_chat(system, history, text, n, name=""):
        asked.append(text)
        assert "Lawrence" in system and "Taglish" in system
        return ["try mo mag stretching bru", "tas walang phone bago matulog"]
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "chat", fake_chat)
    assert engine.generate_reply(person, msgs, [], "hi").source == "smalltalk"          # simple: no AI needed
    r = engine.generate_reply(person, msgs, [], "ano magandang gawin pag di makatulog?")
    assert r.source == "ai" and r.bubbles == ["try mo mag stretching bru", "tas walang phone bago matulog"]
    assert asked == ["ano magandang gawin pag di makatulog?"]


def test_ai_failure_falls_back_to_a_local_reply(monkeypatch):
    person, msgs = _person_and_msgs()

    def broken(*a, **k):
        raise llm.LLMError("429: limit", 429)
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "chat", broken)
    r = engine.generate_reply({**person, "id": 778}, msgs, [], "ano masasabi mo sa bagong cafe sa may amin?")
    assert r.source != "ai" and r.bubbles and "free limit" in r.note


def test_ai_text_is_cleaned_up():
    assert llm.clean_line("Lawrence: sige bru", "Lawrence") == "sige bru"
    assert llm.clean_line("“hahaha oo nga”") == "hahaha oo nga"
    assert llm.clean_line("(laughs)") == ""
    assert llm.clean_line("- teka lang") == "teka lang"
