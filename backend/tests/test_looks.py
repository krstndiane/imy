"""Run with:  pytest backend/tests -q   (from the project root)

The chat's look (app, dark/light, bubble colours) and when Groq writes the reply.
"""
from backend import engine, llm, looks


def test_colours_from_a_screenshot_and_readable_text_on_them():
    c = looks.colours("#000000", "#0084FF", "#3a3b3c")
    assert c == {"bg": "#000000", "mode": "dark", "user": "#0084ff", "user_ink": "#ffffff",
                 "them": "#3a3b3c", "them_ink": "#ffffff"}
    assert looks.colours("#ffffff", "#e9e9eb", "") == {"bg": "#ffffff", "mode": "light"}   # bubbles only as a pair
    assert looks.colours("not a colour", "", "") == {}


def test_chat_name_matches_the_person():
    assert looks.same_person("Lawrence Dela Cruz", ["Lawrence"]) is True
    assert looks.same_person("Mama", ["Lawrence", "law"]) is False
    assert looks.same_person("", ["Lawrence"]) is None


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


def test_groq_writes_every_reply_by_default(monkeypatch):
    person, msgs = _person_and_msgs()
    monkeypatch.delenv("IMY_AI_MODE", raising=False)
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "chat", lambda system, history, text, n, name="": ["hello bru"])
    r = engine.generate_reply({**person, "id": 779}, msgs, [], "hi")
    assert llm.mode() == "always" and r.source == "ai" and r.bubbles == ["hello bru"]


def test_ai_only_when_the_trained_data_isnt_enough(monkeypatch):
    person, msgs = _person_and_msgs()
    monkeypatch.setenv("IMY_AI_MODE", "fallback")
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
