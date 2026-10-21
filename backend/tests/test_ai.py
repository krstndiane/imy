"""Groq through Pydantic AI (llm.py): what imy sends, how it reads screenshots, and what happens when a
model is busy or refuses a setting.

These run without the internet and without Pydantic AI installed: a small stand-in with the same
names (Agent, GroqModel, GroqProvider, BinaryContent, ModelRequest...) records every call. To also
try the real thing against Groq, set GROQ_API_KEY and run:  python -m backend.services

Run with:  pytest backend/tests -q   (from the project root)
"""
import json
import sys
import types
from contextlib import contextmanager

import pytest
from PIL import Image

from backend import llm


class FakeHTTPError(Exception):
    def __init__(self, status_code, body="", retry_after=None):
        super().__init__(f"status_code: {status_code}, body: {body}")
        self.status_code, self.body, self.retry_after = status_code, body, retry_after


@contextmanager
def fake_pydantic_ai(answer):
    """Install a stand-in pydantic_ai + groq. `answer(model, prompt, history, settings, instructions)`
    returns the model's text, or raises FakeHTTPError. Yields the list of recorded calls."""
    calls = []

    class AsyncGroq:
        def __init__(self, api_key=None, max_retries=None, timeout=None):
            self.api_key, self.max_retries, self.timeout = api_key, max_retries, timeout

    class GroqProvider:
        def __init__(self, groq_client=None, api_key=None):
            self.client = groq_client

    class GroqModel:
        def __init__(self, name, provider=None):
            self.name, self.provider = name, provider

    class Part:
        def __init__(self, content):
            self.content = content

    class ModelRequest:
        def __init__(self, parts):
            self.parts = parts

    ModelResponse = type("ModelResponse", (ModelRequest,), {})

    class BinaryContent:
        def __init__(self, data, media_type):
            self.data, self.media_type = data, media_type

    class Agent:
        def __init__(self, model, instructions=None, **kw):
            self.model, self.instructions = model, instructions

        def run_sync(self, prompt, message_history=None, model_settings=None):
            call = {"model": self.model.name, "key": self.model.provider.client.api_key,
                    "retries": self.model.provider.client.max_retries, "prompt": prompt,
                    "history": message_history, "settings": dict(model_settings or {}),
                    "instructions": self.instructions}
            calls.append(call)
            return types.SimpleNamespace(output=answer(**{k: call[k] for k in ("model", "prompt", "history", "settings", "instructions")}))

    mods = {
        "groq": types.SimpleNamespace(AsyncGroq=AsyncGroq),
        "pydantic_ai": types.SimpleNamespace(Agent=Agent, BinaryContent=BinaryContent),
        "pydantic_ai.exceptions": types.SimpleNamespace(ModelHTTPError=FakeHTTPError),
        "pydantic_ai.models.groq": types.SimpleNamespace(GroqModel=GroqModel),
        "pydantic_ai.providers.groq": types.SimpleNamespace(GroqProvider=GroqProvider),
        "pydantic_ai.messages": types.SimpleNamespace(ModelRequest=ModelRequest, ModelResponse=ModelResponse,
                                                      TextPart=Part, UserPromptPart=Part),
    }
    saved = {k: sys.modules.get(k) for k in mods}
    sys.modules.update(mods)
    try:
        yield calls
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def groq_on(monkeypatch, models=("openai/gpt-oss-120b", "llama-3.3-70b-versatile", "qwen/qwen3.8-27b")):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("IMY_AI_DAILY_PER_ACCOUNT", "0")
    monkeypatch.setenv("IMY_AI_DAILY_SITE", "0")
    monkeypatch.setattr(llm, "_ranked", lambda: list(models))
    monkeypatch.setattr(llm.usage, "spend", lambda kind="reply": None)
    llm._cooldown.clear()
    llm._working.clear()
    llm._plain.clear()


def test_a_reply_goes_to_groq_with_the_persona_history_and_settings(monkeypatch):
    groq_on(monkeypatch)
    with fake_pydantic_ai(lambda **k: "Mia: oo naman bebe\nkain ka na?") as calls:
        bubbles = llm.chat("You are Mia.", [{"role": "user", "text": "hi"}, {"role": "person", "text": "hello"},
                                            {"role": "person", "text": "miss u"}], "kumain ka na?", 3, "Mia")
    assert bubbles == ["oo naman bebe", "kain ka na?"]                   # "Mia:" taken off, one bubble per line
    [c] = calls
    assert c["model"] == "openai/gpt-oss-120b" and c["key"] == "gsk_test" and c["retries"] == 0
    assert c["instructions"] == "You are Mia." and c["prompt"] == "kumain ka na?"
    assert [type(m).__name__ for m in c["history"]] == ["ModelRequest", "ModelResponse"]
    assert c["history"][1].parts[0].content == "hello\nmiss u"            # their two bubbles = one turn
    assert c["settings"]["extra_body"] == {"reasoning_effort": "low"} and c["settings"]["max_tokens"] == 700


def test_a_busy_model_is_rested_and_the_next_one_answers(monkeypatch):
    groq_on(monkeypatch)

    def answer(model, **k):
        if model == "openai/gpt-oss-120b":
            raise FakeHTTPError(429, "rate limit", retry_after=30)
        return "sige"
    with fake_pydantic_ai(answer) as calls:
        assert llm.chat("sys", [], "uy", 3) == ["sige"]
        assert llm.chat("sys", [], "uy ulit", 3) == ["sige"]             # the rested one isn't tried again yet
    assert [c["model"] for c in calls] == ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "llama-3.3-70b-versatile"]
    assert llm._cooldown["chat:openai/gpt-oss-120b"] > llm._cooldown.get("chat:llama-3.3-70b-versatile", 0)


def test_a_model_that_refuses_the_thinking_setting_is_asked_plainly(monkeypatch):
    groq_on(monkeypatch, models=("qwen/qwen3-32b",))

    def answer(settings, **k):
        if "extra_body" in settings:
            raise FakeHTTPError(400, "reasoning_effort not supported")
        return "ok lang"
    with fake_pydantic_ai(answer) as calls:
        assert llm.chat("sys", [], "kamusta", 3) == ["ok lang"]
        assert llm.chat("sys", [], "kamusta ulit", 3) == ["ok lang"]
    assert ["extra_body" in c["settings"] for c in calls] == [True, False, False]   # remembered


def test_a_rejected_key_stops_at_once_and_says_so(monkeypatch):
    groq_on(monkeypatch)
    with fake_pydantic_ai(lambda **k: (_ for _ in ()).throw(FakeHTTPError(401, "invalid api key"))) as calls:
        with pytest.raises(llm.LLMError) as err:
            llm.chat("sys", [], "hi", 3)
    assert len(calls) == 1 and llm.explain(err.value) == "the API key was rejected"


def test_without_pydantic_ai_installed_the_message_says_how_to_fix_it(monkeypatch):
    groq_on(monkeypatch)
    saved = sys.modules.get("pydantic_ai")
    sys.modules["pydantic_ai"] = None                                     # makes "import pydantic_ai" fail
    try:
        with pytest.raises(llm.LLMError, match="pip install"):
            llm._run("m", "", "hi")
    finally:
        if saved is None:
            sys.modules.pop("pydantic_ai", None)
        else:
            sys.modules["pydantic_ai"] = saved


SCREEN = {"platform": "messenger", "theme": "dark", "chat_title": "Mia 💙", "group_chat": False,
          "colors": {"background": "#000000", "right_bubble": "#0084ff", "left_bubble": "#303030"},
          "messages": [{"side": "left", "sender": "", "text": "bebe gising ka pa?"},
                       {"side": "right", "sender": "", "text": "oo hehe"},
                       {"side": "left", "sender": "", "text": ""}]}


def test_screenshots_are_read_by_the_image_model(monkeypatch):
    groq_on(monkeypatch)
    with fake_pydantic_ai(lambda **k: "```json\n" + json.dumps(SCREEN) + "\n```") as calls:
        out = llm.read_screenshot(Image.new("RGB", (1170, 2532), (0, 0, 0)), ["Mia"])
    [c] = calls
    assert c["model"] == "qwen/qwen3.8-27b"                                # only a model that can see is used
    assert c["instructions"] == llm.SCREEN_INSTRUCTIONS and c["settings"]["temperature"] == 0.0
    text, image = c["prompt"]
    assert "Mia" in text and image.media_type == "image/png" and image.data[:4] == b"\x89PNG"
    assert out["messages"] == [{"speaker": "person", "text": "bebe gising ka pa?"}, {"speaker": "user", "text": "oo hehe"}]
    assert out["platform"] == "messenger" and out["needs_review"] is False
    assert out["meta"]["title"] == "Mia 💙" and out["meta"]["user"] == "#0084ff" and out["meta"]["mode"] == "dark"


def test_an_unusable_screenshot_answer_tries_another_model(monkeypatch):
    groq_on(monkeypatch, models=("qwen/qwen3.8-27b", "meta-llama/llama-4-scout-17b-16e-instruct"))
    answers = iter(["Sorry, I can't help with that.", json.dumps(SCREEN)])
    with fake_pydantic_ai(lambda **k: next(answers)) as calls:
        out = llm.read_screenshot(Image.new("RGB", (400, 800)), [])
    assert [c["model"] for c in calls] == ["qwen/qwen3.8-27b", "meta-llama/llama-4-scout-17b-16e-instruct"]
    assert len(out["messages"]) == 2


def test_a_shared_photo_is_described(monkeypatch):
    groq_on(monkeypatch)
    with fake_pydantic_ai(lambda **k: "  A slice of   chocolate cake\non a plate. ") as calls:
        assert llm.see(Image.new("RGB", (800, 600), (120, 60, 20))) == "A slice of chocolate cake on a plate."
    assert calls[0]["prompt"][0] == llm.SEE_PROMPT
