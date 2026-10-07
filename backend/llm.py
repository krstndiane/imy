"""llm.py - optional free AI mode for chat replies (Google Gemini or Groq).

Put ONE of these in a `.env` file in the project root (or in your shell) and restart:

    GEMINI_API_KEY=...     free key from https://aistudio.google.com/apikey
    GROQ_API_KEY=...       free key from https://console.groq.com/keys

With a key, the AI writes each reply: it reads your message and the recent conversation,
and answers in the person's style (interview answers + measured style + parsed chats).
Without a key nothing here runs. With one, that text is sent to the provider you chose.
Screenshots are still read locally by Tesseract in this mode.

Free model line-ups change every few months, so the model isn't hard-coded: we ask the
provider what's available and pick a good one (IMY_LLM_MODEL overrides the choice).
"""
import json
import os
import re
import urllib.error
import urllib.request

from . import claude  # noqa: F401  (importing it loads the .env file)

TIMEOUT = 60
_working: dict[str, str] = {}   # provider -> the model that last answered


class LLMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def provider() -> str | None:
    if os.environ.get("IMY_LLM", "1") == "0":
        return None
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    return None


def enabled() -> bool:
    return provider() is not None


def label() -> str:
    p = provider()
    return f"{'Gemini' if p == 'gemini' else 'Groq'} ({_working.get(p, 'AI model')})"


def explain(exc: Exception) -> str:
    """One short, user-facing reason a call failed."""
    status = getattr(exc, "status", None)
    if status in (401, 403) or (status == 400 and "api key" in str(exc).lower()):
        return "the API key was rejected"
    if status == 429:
        return "the free limit was reached, try again in a minute"
    if status:
        return f"the AI provider returned an error ({status})"
    if isinstance(exc, (urllib.error.URLError, TimeoutError)):
        return "couldn't reach the AI provider (no internet?)"
    return str(exc) or exc.__class__.__name__


def _http(url: str, headers: dict, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **headers},
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:300]
        raise LLMError(f"{err.code}: {detail}", err.code) from None


def _version(name: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


# ------------------------------------------------------------------ Gemini
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_SKIP = ("image", "tts", "audio", "live", "embedding", "vision", "computer", "robotics", "aqa")


def _gemini_key() -> str:
    return os.environ.get("GEMINI_API_KEY") or os.environ["GOOGLE_API_KEY"]


def _gemini_models() -> list[str]:
    """Text models this key can use, best first: newest Flash, then Flash-Lite, then the rest."""
    data = _http(f"{GEMINI}/models?pageSize=200", {"x-goog-api-key": _gemini_key()})
    names = [m["name"].removeprefix("models/") for m in data.get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    names = [n for n in names if n.startswith("gemini") and not any(s in n for s in GEMINI_SKIP)]

    def rank(n: str):
        tier = 0 if "flash" in n and "lite" not in n else 1 if "flash" in n else 2   # Pro is rarely on the free tier
        return (tier, -_version(n), "preview" in n or "exp" in n, len(n))
    return sorted(names, key=rank)


def _gemini_chat(model: str, system: str, turns: list[dict]) -> str:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user" if t["role"] == "user" else "model", "parts": [{"text": t["content"]}]} for t in turns],
        "generationConfig": {"temperature": 0.9, "maxOutputTokens": 2000},
    }
    data = _http(f"{GEMINI}/models/{model}:generateContent", {"x-goog-api-key": _gemini_key()}, body)
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


# ------------------------------------------------------------------ Groq (OpenAI-compatible)
GROQ = "https://api.groq.com/openai/v1"
GROQ_PREFER = ("llama-3.3-70b", "gpt-oss-120b", "70b", "kimi", "qwen", "gpt-oss", "llama")
GROQ_SKIP = ("whisper", "guard", "tts", "playai", "compound", "safeguard")


def _groq_models() -> list[str]:
    data = _http(f"{GROQ}/models", {"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})
    names = [m["id"] for m in data.get("data", []) if not any(s in m["id"].lower() for s in GROQ_SKIP)]
    return sorted(names, key=lambda n: next((i for i, p in enumerate(GROQ_PREFER) if p in n.lower()), len(GROQ_PREFER)))


def _groq_chat(model: str, system: str, turns: list[dict]) -> str:
    body = {"model": model, "temperature": 0.9, "max_tokens": 400,
            "messages": [{"role": "system", "content": system}, *turns]}
    data = _http(f"{GROQ}/chat/completions", {"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"}, body)
    text = data["choices"][0]["message"].get("content") or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()   # some models show their reasoning


# ------------------------------------------------------------------ public
def chat(system: str, history: list[dict], user_text: str, max_bubbles: int) -> list[str]:
    """history: [{"role": "user"|"person", "text": ...}] oldest first. Returns the reply bubbles."""
    p = provider()
    list_models, call = (_gemini_models, _gemini_chat) if p == "gemini" else (_groq_models, _groq_chat)
    turns = claude.build_turns(history, user_text)
    forced = os.environ.get("IMY_LLM_MODEL")
    candidates = [forced] if forced else ([_working[p]] if p in _working else list_models()[:5])
    last: Exception = LLMError("no usable model found for this key")
    for model in candidates:
        try:
            text = call(model, system, turns)
        except LLMError as exc:
            last = exc
            if exc.status in (401, 403):      # bad key: no point trying other models
                break
            _working.pop(p, None)             # not found / over quota / unsupported: try the next one
            continue
        bubbles = [l.strip() for l in text.splitlines() if l.strip()][:max_bubbles]
        if bubbles:
            _working[p] = model
            return bubbles
        last = LLMError("empty response")
    raise last
