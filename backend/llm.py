"""llm.py - the free AI "brain" that helps when imy's own training data isn't enough.

How replies are made (see engine.generate_reply):
  1. What YOU taught imy (interview answers, 👍 and Fix in the chat) is used first, word for word.
  2. Simple things (hi, haha, goodnight...) are answered from their style without any AI.
  3. Everything else, if an AI key is set: the AI writes the reply AS THEM - their words, length,
     Taglish, endearments - using the style imy measured and their real messages as examples.
     It can also actually answer questions ("ano magandang gawin pag di makatulog?") like a smart
     friend would, still in their voice.
  4. No key, or the free limit is reached: imy's own pattern-based reply. Nothing breaks.

Put a key in the `.env` file in the project folder (see .env.example), then restart:

    GROQ_API_KEY=...      free: https://console.groq.com/keys          <- recommended
    GEMINI_API_KEY=...    https://aistudio.google.com/apikey

Why Groq first: it's free, very fast (replies in about a second), and by default doesn't keep what you
send. Google's FREE Gemini tier says not to send personal information and may let people read what's
sent, so only use Gemini for real users' chats with billing turned on (then set IMY_GEMINI_PAID=1).
When both keys are set, imy uses Groq and switches to Gemini whenever Groq's free limit is reached.

Settings (optional, in .env):
    IMY_AI_MODE=fallback   fallback (default): AI only when the trained data isn't enough
                           always: AI for everything except what you taught it;  off: never
    IMY_AI_ORDER=groq,gemini
    IMY_LLM_MODEL=...      force one model id
    IMY_AI_DAILY_PER_ACCOUNT=50 / IMY_AI_DAILY_SITE=0   daily limits (see usage.py)
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

from . import auth
from . import claude  # noqa: F401  (importing it loads the .env file)
from . import usage

TIMEOUT = float(os.environ.get("IMY_AI_TIMEOUT", "15"))   # seconds per request: a stuck call never freezes a chat
MAX_ATTEMPTS = 4                                         # different models tried before giving up on one reply
MODEL_LIST_TTL = 6 * 3600
_lock = threading.Lock()
_models: dict[str, tuple[float, list[str]]] = {}         # provider -> (fetched at, ranked model ids)
_cooldown: dict[tuple[str, str], float] = {}             # (provider, model) -> don't use until this time
_working: dict[str, str] = {}                            # provider -> the model that last answered
_last: dict[str, str] = {}                               # what was used last, for the status page
_plain: set[str] = set()                                 # models that reject the "think less" setting


class LLMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class OverLimit(LLMError):
    """This account (or the whole site) has used today's AI allowance - see usage.py.
    The message is written for the person reading it."""


# ------------------------------------------------------------------ which providers are on
def _key(provider: str) -> str | None:
    if provider == "groq":
        return os.environ.get("GROQ_API_KEY")
    if provider == "gemini":
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    return None


def mode() -> str:
    if os.environ.get("IMY_LLM", "1") == "0":
        return "off"
    m = os.environ.get("IMY_AI_MODE", "fallback").lower()
    return m if m in ("fallback", "always", "off") else "fallback"


def providers() -> list[str]:
    if mode() == "off":
        return []
    order = [p.strip() for p in os.environ.get("IMY_AI_ORDER", "groq,gemini").split(",") if p.strip()]
    return [p for p in order if p in ("groq", "gemini") and _key(p)]


def provider() -> str | None:
    ps = providers()
    return ps[0] if ps else None


def enabled() -> bool:
    """An AI service is set up AND this account hasn't switched the outside AI off (account menu)."""
    return bool(providers()) and auth.cloud_ai_allowed()


def label() -> str:
    p = _last.get("provider") or provider()
    if not p:
        return "AI"
    return f"{'Gemini' if p == 'gemini' else 'Groq'} ({_last.get('model') or _working.get(p) or 'AI model'})"


def status() -> dict:
    """For /api/status: which brain is on (never the keys themselves)."""
    gem_free = "gemini" in providers() and os.environ.get("IMY_GEMINI_PAID") != "1"
    opted_out = bool(providers()) and not auth.cloud_ai_allowed()     # this account switched it off
    return {"mode": mode(), "providers": [] if opted_out else providers(),
            "available": bool(providers()), "opted_out": opted_out, "last": dict(_last),
            "warning": "Gemini's free tier isn't meant for personal chats; use it only with billing on."
            if gem_free else ""}


def explain(exc: Exception) -> str:
    """One short, user-facing reason a call failed."""
    if isinstance(exc, OverLimit):
        return str(exc)
    st = getattr(exc, "status", None)
    if st in (401, 403) or (st == 400 and "api key" in str(exc).lower()):
        return "the API key was rejected"
    if st == 429:
        return "the free limit was reached for now"
    if st:
        return f"the AI service returned an error ({st})"
    if isinstance(exc, (urllib.error.URLError, TimeoutError)) or str(exc).startswith("network"):
        return "couldn't reach the AI service"
    return str(exc) or exc.__class__.__name__


# ------------------------------------------------------------------ HTTP
def _http(url: str, headers: dict, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "User-Agent": "imy/1.0", **headers},
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:400]
        retry = err.headers.get("retry-after") if err.headers else None
        try:
            retry_s = float(retry) if retry else None
        except ValueError:
            retry_s = None
        raise LLMError(f"{err.code}: {detail}", err.code, retry_s) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as err:
        raise LLMError(f"network: {err}") from None


def _version(name: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


# ------------------------------------------------------------------ Groq (OpenAI-compatible)
GROQ = os.environ.get("IMY_GROQ_URL", "https://api.groq.com/openai/v1")   # any OpenAI-compatible server works
# Free-plan chat models, best for this job first. Newer models Groq adds are still tried after these.
GROQ_PREFER = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b", "qwen/qwen3",
               "moonshotai/kimi", "meta-llama/llama-4", "llama-3.1-8b-instant"]
GROQ_FALLBACK = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
GROQ_SKIP = ("whisper", "guard", "tts", "playai", "compound", "safeguard", "orpheus", "distil")


def _groq_models() -> list[str]:
    try:
        data = _http(f"{GROQ}/models", {"Authorization": f"Bearer {_key('groq')}"})
        names = [m["id"] for m in data.get("data", []) if m.get("active", True)
                 and not any(s in m["id"].lower() for s in GROQ_SKIP)]
    except LLMError as exc:
        if exc.status in (401, 403):
            raise
        names = []
    names = names or GROQ_FALLBACK

    def rank(n: str):
        low = n.lower()
        return next((i for i, p in enumerate(GROQ_PREFER) if low.startswith(p)), len(GROQ_PREFER)), n
    return sorted(dict.fromkeys(names), key=rank)


def _groq_chat(model: str, system: str, turns: list[dict]) -> str:
    body = {"model": model, "temperature": 0.85, "max_completion_tokens": 700,
            "messages": [{"role": "system", "content": system}, *turns]}
    extra = {"reasoning_effort": "low"} if "gpt-oss" in model else {"reasoning_effort": "none"} if "qwen3" in model else {}
    if model in _plain:
        extra = {}
    headers = {"Authorization": f"Bearer {_key('groq')}"}
    try:
        data = _http(f"{GROQ}/chat/completions", headers, {**body, **extra})
    except LLMError as exc:
        if exc.status == 400 and extra:          # this model doesn't take the reasoning setting: ask plainly (and remember)
            data = _http(f"{GROQ}/chat/completions", headers, body)
            _plain.add(model)
        else:
            raise
    text = data["choices"][0]["message"].get("content") or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


# ------------------------------------------------------------------ Gemini
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_SKIP = ("image", "tts", "audio", "live", "embedding", "vision", "computer", "robotics", "aqa", "transcribe",
               "translate", "omni", "nano")


def _gemini_models() -> list[str]:
    data = _http(f"{GEMINI}/models?pageSize=200", {"x-goog-api-key": _key("gemini")})
    names = [m["name"].removeprefix("models/") for m in data.get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    names = [n for n in names if n.startswith("gemini") and not any(s in n for s in GEMINI_SKIP)]

    def rank(n: str):   # newest stable Flash first (fast, free), then Flash-Lite, then the rest
        tier = 0 if "flash" in n and "lite" not in n else 1 if "flash" in n else 2
        return (tier, "preview" in n or "exp" in n, -_version(n), len(n))
    return sorted(names, key=rank)


def _gemini_chat(model: str, system: str, turns: list[dict]) -> str:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user" if t["role"] == "user" else "model", "parts": [{"text": t["content"]}]} for t in turns],
        "generationConfig": {"temperature": 0.85, "maxOutputTokens": 800},
    }
    # Chat replies don't need long "thinking"; keep it minimal so answers come back fast.
    thinking = {"thinkingBudget": 0} if model.startswith("gemini-2.5") else {"thinkingLevel": "low"}
    url = f"{GEMINI}/models/{model}:generateContent"
    headers = {"x-goog-api-key": _key("gemini")}
    try:
        if model in _plain:
            raise LLMError("plain", 400)
        data = _http(url, headers, {**body, "generationConfig": {**body["generationConfig"], "thinkingConfig": thinking}})
    except LLMError as exc:
        if exc.status == 400:                    # this model doesn't take that thinking setting
            data = _http(url, headers, body)
            _plain.add(model)
        else:
            raise
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


CALLS = {"groq": (_groq_models, _groq_chat), "gemini": (_gemini_models, _gemini_chat)}


def _ranked(p: str) -> list[str]:
    with _lock:
        cached = _models.get(p)
    if cached and time.time() - cached[0] < MODEL_LIST_TTL:
        return cached[1]
    names = CALLS[p][0]()
    with _lock:
        _models[p] = (time.time(), names)
    return names


def _candidates() -> list[tuple[str, str]]:
    """(provider, model) pairs to try, best first, skipping models resting after a limit was hit."""
    now = time.time()
    forced = os.environ.get("IMY_LLM_MODEL")
    out = []
    for p in providers():
        try:
            names = [forced] if forced else _ranked(p)
        except LLMError as exc:
            print(f"[imy] {p}: {explain(exc)}")
            continue
        if p in _working and _working[p] in names:          # the one that worked last time goes first
            names = [_working[p]] + [n for n in names if n != _working[p]]
        out += [(p, n) for n in names[:5] if _cooldown.get((p, n), 0) <= now]
    return out


def _cool(p: str, model: str, exc: LLMError) -> None:
    """Rest a model for a while after it failed (a free limit, a removed model, a bad key)."""
    if exc.status in (401, 403):
        wait = 600
    else:
        wait = exc.retry_after or (60 if exc.status == 429 else 300 if exc.status in (404, 413) else 20)
    _cooldown[(p, model)] = time.time() + min(wait, 3600)
    if _working.get(p) == model:
        _working.pop(p, None)


# ------------------------------------------------------------------ public
def chat(system: str, history: list[dict], user_text: str, max_bubbles: int, name: str = "") -> list[str]:
    """history: [{"role": "user"|"person", "text": ...}] oldest first. Returns the reply bubbles.
    Tries the best available free model; on a limit or error moves to the next one (and to Gemini
    when Groq is used up). Raises LLMError if none answered, OverLimit if today's allowance is used."""
    reason = usage.blocked("reply")
    if reason:
        raise OverLimit(reason)
    turns = claude.build_turns(history[-14:], user_text)
    last: Exception = LLMError("no AI model is available right now")
    started = time.time()
    for attempt, (p, model) in enumerate(_candidates()):
        if attempt >= MAX_ATTEMPTS or time.time() - started > 2 * TIMEOUT:
            break
        try:
            text = CALLS[p][1](model, system, turns)
        except LLMError as exc:
            last = exc
            print(f"[imy] {p}/{model}: {explain(exc)}")
            _cool(p, model, exc)
            if exc.status in (401, 403):         # a bad key: don't try this provider's other models now
                for q, m in list(_candidates()):
                    if q == p:
                        _cooldown[(q, m)] = time.time() + 600
            continue
        except (KeyError, IndexError, TypeError) as exc:   # an unexpected answer shape
            last = LLMError(f"odd response: {exc}")
            _cool(p, model, LLMError("odd", 500))
            continue
        bubbles = [b for b in (clean_line(l, name) for l in text.splitlines()) if b][:max_bubbles]
        if bubbles:
            _working[p] = model
            _last.update(provider=p, model=model, at=time.strftime("%H:%M:%S"))
            usage.spend("reply")
            return bubbles
        last = LLMError("empty response")
    raise last


def ask(system: str, user_text: str) -> str:
    """One plain question to the AI, answered as raw text (used for imy's own background jobs,
    like keeping notes of what you told them). Returns "" if no model answered, or if this account
    has used today's AI allowance (background work waits until tomorrow)."""
    if usage.blocked("other"):
        return ""
    for attempt, (p, model) in enumerate(_candidates()):
        if attempt >= 2:
            break
        try:
            text = CALLS[p][1](model, system, [{"role": "user", "content": user_text}]).strip()
            usage.spend("other")
            return text
        except (LLMError, KeyError, IndexError, TypeError) as exc:
            print(f"[imy] {p}/{model}: {explain(exc) if isinstance(exc, LLMError) else exc}")
            if isinstance(exc, LLMError):
                _cool(p, model, exc)
    return ""


# ------------------------------------------------------------------ looking at a photo
# Some free models can see images. imy uses one to put into words what a shared photo shows, so the
# reply can be about the photo ("ang ganda ng view!") instead of a guess. If no model can see, the
# photo is still sent and saved - imy just describes less (see media.py).
# Which model can look at a picture changes every few months, so imy asks Groq for its current list
# (see _vision_candidates) and keeps anything whose name matches one of these. The list below is only
# used when that request fails; it's in date order, newest first, so an old name further down is
# simply skipped after one 404.
VISION_HINTS = ("qwen3.8", "llama-4", "scout", "maverick", "vision", "-vl", "llava", "pixtral")
GROQ_VISION_FALLBACK = ["qwen/qwen3.8-27b",                              # Groq's image model as of Oct 2026
                        "meta-llama/llama-4-scout-17b-16e-instruct",     # retired; harmless to try
                        "meta-llama/llama-4-maverick-17b-128e-instruct"]
SEE_PROMPT = ("Describe this image for someone who can't see it, in one or two short sentences. "
              "Say what it shows, the mood, and any text that stands out. If it's a screenshot, say so and "
              "summarise what's on it. No preamble, just the description.")


def _groq_see(model: str, data_url: str, prompt: str) -> str:
    # The picture and the question go in one message, the way Groq expects. Thinking is switched off
    # where the model supports it: imy wants the description, not the model's reasoning out loud.
    body = {"model": model, "temperature": 0.3, "max_completion_tokens": 300,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt},
                                                      {"type": "image_url", "image_url": {"url": data_url}}]}]}
    extra = {} if model in _plain else {"reasoning_effort": "none"} if "qwen3" in model else {}
    headers = {"Authorization": f"Bearer {_key('groq')}"}
    try:
        data = _http(f"{GROQ}/chat/completions", headers, {**body, **extra})
    except LLMError as exc:
        if exc.status == 400 and extra:        # this model doesn't take that setting: ask plainly, and remember
            data = _http(f"{GROQ}/chat/completions", headers, body)
            _plain.add(model)
        else:
            raise
    text = data["choices"][0]["message"].get("content") or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def _gemini_see(model: str, data_url: str, prompt: str) -> str:
    mime, _, b64 = data_url.removeprefix("data:").partition(";base64,")
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}, {"inline_data": {"mime_type": mime, "data": b64}}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 220}}
    data = _http(f"{GEMINI}/models/{model}:generateContent", {"x-goog-api-key": _key("gemini")}, body)
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


def _vision_candidates() -> list[tuple[str, str]]:
    now = time.time()
    out = []
    for p in providers():
        try:
            names = [n for n in _ranked(p) if any(h in n.lower() for h in VISION_HINTS)]
        except LLMError:
            names = []
        if not names and p == "groq":
            names = GROQ_VISION_FALLBACK
        if not names and p == "gemini":
            names = ["gemini-flash-latest"]
        out += [(p, n) for n in names[:2] if _cooldown.get((p, "see:" + n), 0) <= now]
    return out


def can_see() -> bool:
    return bool(providers()) and auth.cloud_ai_allowed()


def see(data_url: str, prompt: str = SEE_PROMPT) -> str:
    """One short description of an image. Returns "" if no free model could look at it, or if this
    account has used today's AI allowance (the photo still sends; media.py describes it less)."""
    if usage.blocked("photo"):
        return ""
    seers = {"groq": _groq_see, "gemini": _gemini_see}
    for p, model in _vision_candidates()[:3]:
        try:
            text = seers[p](model, data_url, prompt)
        except LLMError as exc:
            print(f"[imy] {p}/{model} couldn't look at the photo: {explain(exc)}")
            _cooldown[(p, "see:" + model)] = time.time() + (600 if exc.status in (401, 403) else 120)
            continue
        except (KeyError, IndexError, TypeError):
            continue
        text = " ".join(text.split())
        if text:
            usage.spend("photo")
            return text[:600]
    return ""


def clean_line(line: str, name: str = "") -> str:
    """Models sometimes add "Name:", quotes or list markers; a text message has none of those."""
    t = line.strip()
    if not t or (t.startswith(("(", "[", "*")) and t.endswith((")", "]", "*"))):   # stage directions: "(laughs)"
        return ""
    t = re.sub(r"^\s*(?:[-•]\s+|\d+[.)]\s+)", "", t)
    for n in {name, (name.split() or [""])[0]} - {""}:
        if t.lower().startswith(n.lower() + ":"):
            t = t[len(n) + 1:]
    t = t.strip()
    if len(t) >= 2 and t[0] in "\"“" and t[-1] in "\"”":
        t = t[1:-1]
    return t.strip()
