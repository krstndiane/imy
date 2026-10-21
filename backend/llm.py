"""llm.py - Groq, through Pydantic AI: the AI behind imy's replies, screenshot reading and photos.

What it does:
  chat()             writes a reply AS the person, in their voice (see engine.ai_prompt)
  read_screenshot()  reads who said what from a chat screenshot (replaces the old OCR)
  see()              puts a shared photo into words, so the reply can be about it
  ask()              one plain question, for imy's own background jobs (memory notes)

How replies are made (see engine.generate_reply):
  1. What YOU taught imy (interview answers, 👍 and Fix in the chat) is used first, word for word.
  2. Everything else goes to Groq, which writes the reply as them using their measured style and
     real messages as examples.
  3. If Groq can't answer (no key, over the free limit, no internet), imy's own pattern-based reply
     is sent instead. Nothing breaks.

Every call goes through Pydantic AI's Agent with a GroqModel. imy still picks the model itself: it
asks Groq which models exist, tries the best one first, and rests a model for a while after it hits
a limit or fails, so one busy model doesn't stop replies.

Settings (in .env, see .env.example):
    GROQ_API_KEY=...       free: https://console.groq.com/keys
    IMY_AI_MODE=always     always (default): Groq for every reply except what you taught
                           fallback: Groq only when imy's own reply isn't confident;  off: never
    IMY_LLM_MODEL=...      force one chat model id
    IMY_VISION_MODEL=...   force one image model id (screenshots and photos)
    IMY_AI_DAILY_PER_ACCOUNT=50 / IMY_AI_DAILY_SITE=0   daily limits (see usage.py)
"""
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

from pydantic import BaseModel, ValidationError, model_validator

from . import auth, config, looks, usage  # noqa: F401  (config loads the .env file)

TIMEOUT = float(os.environ.get("IMY_AI_TIMEOUT", "15"))   # seconds per request: a stuck call never freezes a chat
READ_TIMEOUT = float(os.environ.get("IMY_READ_TIMEOUT", "45"))   # reading a long screenshot takes longer
MAX_ATTEMPTS = 4                                         # different models tried before giving up on one reply
MODEL_LIST_TTL = 6 * 3600
_lock = threading.Lock()
_models: list = [0.0, []]                                # [fetched at, ranked model ids]
_cooldown: dict[str, float] = {}                         # model -> don't use until this time
_working: dict[str, str] = {}                            # "chat" | "see" -> the model that last answered
_last: dict[str, str] = {}                               # what was used last, for the status page
_plain: set[str] = set()                                 # models that reject the "think less" setting

PLATFORM_LABELS = {"messenger": "Messenger", "instagram": "Instagram DMs", "imessage": "iMessage", "whatsapp": "WhatsApp",
                   "telegram": "Telegram", "discord": "Discord", "viber": "Viber", "twitter": "Twitter/X DMs"}


class LLMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class OverLimit(LLMError):
    """This account (or the whole site) has used today's AI allowance - see usage.py.
    The message is written for the person reading it."""


# ------------------------------------------------------------------ is it on
def _key() -> str | None:
    return os.environ.get("GROQ_API_KEY") or None


def mode() -> str:
    if os.environ.get("IMY_LLM", "1") == "0":
        return "off"
    m = os.environ.get("IMY_AI_MODE", "always").lower()
    return m if m in ("fallback", "always", "off") else "always"


def providers() -> list[str]:
    """["groq"] when Groq is set up, [] otherwise. A list, so the status page and older code keep working."""
    return ["groq"] if mode() != "off" and _key() else []


def provider() -> str | None:
    return "groq" if providers() else None


def enabled() -> bool:
    """Groq is set up AND this account hasn't switched the outside AI off (account menu)."""
    return bool(providers()) and auth.cloud_ai_allowed()


def can_see() -> bool:
    return enabled()


def label() -> str:
    if not providers():
        return "AI"
    return f"Groq ({_last.get('model') or _working.get('chat') or 'AI model'})"


def status() -> dict:
    """For /api/status: is the AI on (never the key itself)."""
    opted_out = bool(providers()) and not auth.cloud_ai_allowed()     # this account switched it off
    return {"mode": mode(), "providers": [] if opted_out else providers(),
            "available": bool(providers()), "opted_out": opted_out, "last": dict(_last), "warning": ""}


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


# ------------------------------------------------------------------ which models Groq has
GROQ = "https://api.groq.com/openai/v1"
# Free-plan chat models, best for this job first. Newer models Groq adds are still tried after these.
GROQ_PREFER = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b", "qwen/qwen3",
               "moonshotai/kimi", "meta-llama/llama-4", "llama-3.1-8b-instant"]
GROQ_FALLBACK = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
GROQ_SKIP = ("whisper", "guard", "tts", "playai", "compound", "safeguard", "orpheus", "distil")
# Which model can look at a picture changes every few months, so imy keeps any listed model whose
# name matches one of these. The fallback list is only used when Groq's list can't be fetched.
VISION_HINTS = ("qwen3.8", "llama-4", "scout", "maverick", "vision", "-vl", "llava", "pixtral")
GROQ_VISION_FALLBACK = ["qwen/qwen3.8-27b",                              # Groq's image model as of Oct 2026
                        "meta-llama/llama-4-scout-17b-16e-instruct",
                        "meta-llama/llama-4-maverick-17b-128e-instruct"]


def _http(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_key()}", "User-Agent": "imy/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        raise LLMError(f"{err.code}: {err.read().decode('utf-8', 'replace')[:300]}", err.code) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as err:
        raise LLMError(f"network: {err}") from None


def _ranked() -> list[str]:
    """Every usable Groq model, best for chat first (cached for a few hours)."""
    with _lock:
        if _models[1] and time.time() - _models[0] < MODEL_LIST_TTL:
            return _models[1]
    try:
        names = [m["id"] for m in _http(f"{GROQ}/models").get("data", []) if m.get("active", True)
                 and not any(s in m["id"].lower() for s in GROQ_SKIP)]
    except LLMError as exc:
        if exc.status in (401, 403):
            raise
        names = []

    def rank(n: str):
        low = n.lower()
        return next((i for i, p in enumerate(GROQ_PREFER) if low.startswith(p)), len(GROQ_PREFER)), n
    names = sorted(dict.fromkeys(names or GROQ_FALLBACK), key=rank)
    with _lock:
        _models[:] = [time.time(), names]
    return names


def _candidates(job: str = "chat") -> list[str]:
    """Models to try for a job ("chat" or "see"), best first, skipping models resting after a limit."""
    forced = os.environ.get("IMY_VISION_MODEL" if job == "see" else "IMY_LLM_MODEL")
    if forced:
        names = [forced]
    else:
        try:
            names = _ranked()
        except LLMError as exc:
            print(f"[imy] groq: {explain(exc)}")
            return []
        if job == "see":
            names = [n for n in names if any(h in n.lower() for h in VISION_HINTS)] or GROQ_VISION_FALLBACK
    if _working.get(job) in names:                       # the one that worked last time goes first
        names = [_working[job]] + [n for n in names if n != _working[job]]
    now = time.time()
    return [n for n in names[:5] if _cooldown.get(f"{job}:{n}", 0) <= now]


def _cool(job: str, model: str, exc: LLMError) -> None:
    """Rest a model for a while after it failed (a free limit, a removed model, a bad key)."""
    if exc.status in (401, 403):
        wait = 600
    else:
        wait = exc.retry_after or (60 if exc.status == 429 else 300 if exc.status in (404, 413) else 20)
    _cooldown[f"{job}:{model}"] = time.time() + min(wait, 3600)
    if _working.get(job) == model:
        _working.pop(job, None)


# ------------------------------------------------------------------ one call, through Pydantic AI
def _settings(model: str, temperature: float, max_tokens: int, timeout: float) -> dict:
    """Pydantic AI model settings. Thinking is turned down where the model supports it: imy wants the
    message, not the model's reasoning out loud."""
    s = {"temperature": temperature, "max_tokens": max_tokens, "timeout": timeout}
    if model not in _plain:
        if "gpt-oss" in model:
            s["extra_body"] = {"reasoning_effort": "low"}
        elif "qwen3" in model:
            s["extra_body"] = {"reasoning_effort": "none"}
    return s


def _run(model: str, instructions: str, prompt, history: list[dict] | None = None, temperature: float = 0.85,
         max_tokens: int = 700, timeout: float = TIMEOUT) -> str:
    """Ask one Groq model once. `history`: earlier turns, [{"role": "user"|"assistant", "content"}].
    Returns its text; raises LLMError (with the HTTP status when there is one)."""
    try:
        from groq import AsyncGroq
        from pydantic_ai import Agent
        from pydantic_ai.exceptions import ModelHTTPError
        from pydantic_ai.models.groq import GroqModel
        from pydantic_ai.providers.groq import GroqProvider
    except ImportError as exc:
        raise LLMError(f"pydantic-ai isn't installed ({exc.name}); run: pip install -r backend/requirements.txt") from None
    messages = _history(history or [])

    def once(settings: dict) -> str:
        # A fresh client per call: Pydantic AI's run_sync runs its own event loop, and an async
        # client must not outlive the loop it was used on. The SDK's own retries are off; imy moves
        # on to the next model instead, which is faster than waiting on a busy one.
        client = AsyncGroq(api_key=_key(), max_retries=0, timeout=timeout)
        agent = Agent(GroqModel(model, provider=GroqProvider(groq_client=client)), instructions=instructions or None)
        try:
            result = agent.run_sync(prompt, message_history=messages or None, model_settings=settings)
        except ModelHTTPError as exc:
            raise LLMError(f"{exc.status_code}: {str(getattr(exc, 'body', '') or exc)[:300]}", exc.status_code,
                           getattr(exc, "retry_after", None)) from None
        except LLMError:
            raise
        except Exception as exc:          # no internet, a timeout, an answer Pydantic AI couldn't use
            raise LLMError(f"network: {exc.__class__.__name__}: {exc}") from None
        return re.sub(r"<think>.*?</think>", "", str(result.output or ""), flags=re.S).strip()

    settings = _settings(model, temperature, max_tokens, timeout)
    try:
        return once(settings)
    except LLMError as exc:
        if exc.status == 400 and "extra_body" in settings:   # this model doesn't take that setting: ask plainly, remember
            _plain.add(model)
            return once({k: v for k, v in settings.items() if k != "extra_body"})
        raise


def build_turns(history: list[dict], user_text: str) -> list[dict]:
    """[{"role": "user"|"person", "text"}] + the new message -> alternating user/assistant turns."""
    turns: list[dict] = []
    for h in history + [{"role": "user", "text": user_text}]:
        role = "user" if h["role"] == "user" else "assistant"
        if turns and turns[-1]["role"] == role:  # several bubbles in a row = one turn
            turns[-1]["content"] += "\n" + h["text"]
        elif turns or role == "user":  # the conversation has to open with the user
            turns.append({"role": role, "content": h["text"]})
    return turns


def _history(turns: list[dict]) -> list:
    """Earlier turns -> Pydantic AI messages."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
    return [ModelRequest(parts=[UserPromptPart(content=t["content"])]) if t["role"] == "user"
            else ModelResponse(parts=[TextPart(content=t["content"])]) for t in turns]


# ------------------------------------------------------------------ public
def chat(system: str, history: list[dict], user_text: str, max_bubbles: int, name: str = "") -> list[str]:
    """history: [{"role": "user"|"person", "text": ...}] oldest first. Returns the reply bubbles.
    Tries the best available model; on a limit or error moves to the next one. Raises LLMError if
    none answered, OverLimit if today's allowance is used."""
    reason = usage.blocked("reply")
    if reason:
        raise OverLimit(reason)
    if not providers():
        raise LLMError("no AI is set up (GROQ_API_KEY)")
    turns = build_turns(history[-14:], user_text)
    last: Exception = LLMError("no AI model is available right now")
    started = time.time()
    for attempt, model in enumerate(_candidates("chat")):
        if attempt >= MAX_ATTEMPTS or time.time() - started > 2 * TIMEOUT:
            break
        try:
            text = _run(model, system, turns[-1]["content"], turns[:-1])   # your new message is the prompt
        except LLMError as exc:
            last = exc
            print(f"[imy] groq/{model}: {explain(exc)}")
            _cool("chat", model, exc)
            if exc.status in (401, 403):         # a bad key: no point trying the other models now
                break
            continue
        bubbles = [b for b in (clean_line(l, name) for l in text.splitlines()) if b][:max_bubbles]
        if bubbles:
            _working["chat"] = model
            _last.update(provider="groq", model=model, at=time.strftime("%H:%M:%S"))
            usage.spend("reply")
            return bubbles
        last = LLMError("empty response")
    raise last


def ask(system: str, user_text: str) -> str:
    """One plain question, answered as raw text (imy's own background jobs, like keeping notes of what
    you told them). Returns "" if no model answered or this account has used today's AI allowance."""
    if usage.blocked("other") or not providers():
        return ""
    for attempt, model in enumerate(_candidates("chat")):
        if attempt >= 2:
            break
        try:
            text = _run(model, system, user_text, temperature=0.3)
            usage.spend("other")
            return text
        except LLMError as exc:
            print(f"[imy] groq/{model}: {explain(exc)}")
            _cool("chat", model, exc)
    return ""


# ------------------------------------------------------------------ pictures
MAX_IMAGE_EDGE = 2048          # tall phone screenshots stay readable; Groq takes images up to ~4 MB


def image_content(img):
    """A PIL image -> Pydantic AI BinaryContent (PNG, or JPEG when a PNG would be too big to send)."""
    from PIL import ImageOps
    from pydantic_ai import BinaryContent
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    if buf.tell() <= 3_000_000:
        return BinaryContent(data=buf.getvalue(), media_type="image/png")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return BinaryContent(data=buf.getvalue(), media_type="image/jpeg")


def _look(prompt: str, img, instructions: str = "", max_tokens: int = 300, temperature: float = 0.3,
          timeout: float = TIMEOUT, tries: int = 3) -> tuple[str, str]:
    """Show a picture to the best image model. Returns (text, model); raises LLMError if none answered."""
    try:
        content = image_content(img)
    except ImportError as exc:
        raise LLMError(f"pydantic-ai isn't installed ({exc.name})") from None
    last: Exception = LLMError("no image model is available right now")
    for model in _candidates("see")[:tries]:
        try:
            text = _run(model, instructions, [prompt, content], temperature=temperature, max_tokens=max_tokens,
                        timeout=timeout)
        except LLMError as exc:
            last = exc
            print(f"[imy] groq/{model} couldn't look at the picture: {explain(exc)}")
            _cool("see", model, exc)
            if exc.status in (401, 403):
                break
            continue
        if text:
            _working["see"] = model
            return text, model
        last = LLMError("empty response")
    raise last


SEE_PROMPT = ("Describe this image for someone who can't see it, in one or two short sentences. "
              "Say what it shows, the mood, and any text that stands out. If it's a screenshot, say so and "
              "summarise what's on it. No preamble, just the description.")


def see(img, prompt: str = SEE_PROMPT) -> str:
    """One short description of a photo (a PIL image). Returns "" if no model could look at it, or if
    this account has used today's AI allowance (the photo still sends; media.py describes it less)."""
    if usage.blocked("photo") or not providers():
        return ""
    try:
        text, _ = _look(prompt, img, tries=2)
    except LLMError:
        return ""
    usage.spend("photo")
    return " ".join(text.split())[:600]


# ------------------------------------------------------------------ reading a chat screenshot
SCREEN_INSTRUCTIONS = """You read chat screenshots. Look at the picture and write down the conversation in it.

Rules:
- Only real message bubbles, top to bottom, one entry per bubble.
- side: "right" for bubbles on the right (the phone owner, usually coloured), "left" for the other person.
  In layouts with a name above each message instead of sides, the phone owner is "right".
- Copy the text exactly: casing, emojis, typos, Tagalog/English mix, line breaks. Don't translate, fix or finish anything.
- Skip: the status bar, the header (name, photo, call buttons, "Active now"), pinned messages, date separators,
  notices like "You set the nickname..." or "unsent a message", the small "replying to" quote above a bubble,
  reactions, read receipts, the typing box, and bubbles with no text (photos, stickers, voice notes).
- If it isn't a chat screenshot, return an empty messages list.

Answer with JSON only, no other words, in exactly this shape:
{"platform": "messenger|instagram|imessage|whatsapp|telegram|viber|discord|twitter|unknown",
 "theme": "dark|light",
 "chat_title": "the name at the top, or empty",
 "group_chat": false,
 "colors": {"background": "#rrggbb", "right_bubble": "#rrggbb", "left_bubble": "#rrggbb"},
 "messages": [{"side": "left|right", "sender": "name shown above the bubble, or empty", "text": "..."}]}"""

PLATFORMS = {"messenger", "instagram", "imessage", "whatsapp", "telegram", "viber", "discord", "twitter", "unknown"}


class _Lenient(BaseModel):
    """Models sometimes send null for a field they couldn't fill: treat it as empty."""

    @model_validator(mode="before")
    @classmethod
    def _no_nulls(cls, data):
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data


class ReadBubble(_Lenient):
    side: str = "left"
    sender: str = ""
    text: str = ""


class ScreenRead(_Lenient):
    """What a screenshot says, as the model returns it (validated before imy uses it)."""
    platform: str = "unknown"
    theme: str = ""
    chat_title: str = ""
    group_chat: bool = False
    colors: dict[str, str | None] = {}
    messages: list[ReadBubble] = []


def parse_screen_json(text: str) -> ScreenRead:
    """The model's answer -> ScreenRead. Accepts ```json fences or a few words around the JSON."""
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("the screenshot reader didn't answer with JSON")
    try:
        return ScreenRead.model_validate(json.loads(body[start:end + 1]))
    except (ValueError, ValidationError) as exc:
        raise LLMError(f"the screenshot reader's answer couldn't be used: {str(exc)[:200]}") from None


def screen_result(read: ScreenRead, raw: str, model: str = "") -> dict:
    """ScreenRead -> what imy stores for a screenshot (see main._process_image)."""
    messages = [{"speaker": "user" if b.side.lower().startswith("r") else "person", "text": b.text.strip()}
                for b in read.messages if b.text.strip()]
    platform = read.platform.lower().strip() if read.platform.lower().strip() in PLATFORMS else "unknown"
    senders = list(dict.fromkeys(b.sender.strip() for b in read.messages if b.sender.strip() and not b.side.lower().startswith("r")))
    meta = {"platform": platform, "confidence": 1.0 if platform != "unknown" else 0.0,
            "mode": read.theme.lower() if read.theme.lower() in ("dark", "light") else "",
            "title": read.chat_title.strip(), "group": bool(read.group_chat), "senders": senders, "reader": model}
    c = read.colors or {}
    meta.update(looks.colours(c.get("background") or "", c.get("right_bubble") or "", c.get("left_bubble") or ""))
    if read.theme.lower() in ("dark", "light"):
        meta["mode"] = read.theme.lower()      # the model's own answer beats the background guess
    meta = {k: v for k, v in meta.items() if v not in ("", [], None)}
    return {"platform": platform, "messages": messages, "raw_text": raw, "meta": meta,
            "needs_review": len({m["speaker"] for m in messages}) < 2}


def read_screenshot(img, person_names: list[str]) -> dict:
    """Who said what in one chat screenshot (a PIL image), read by Groq's image model.
    Raises OverLimit / LLMError when it can't be read; the upload screen shows why."""
    reason = usage.blocked("read")
    if reason:
        raise OverLimit(reason)
    if not providers():
        raise LLMError("no AI is set up (GROQ_API_KEY)")
    hint = "Read this chat screenshot."
    if person_names:
        hint += " The other person (left side) may be shown as: " + ", ".join(dict.fromkeys(person_names)) + "."
    last: Exception = LLMError("couldn't read this screenshot")
    for _ in range(2):        # a second try (next model) when the first answer isn't usable JSON
        text, model = _look(hint, img, SCREEN_INSTRUCTIONS, max_tokens=4000, temperature=0.0, timeout=READ_TIMEOUT)
        try:
            out = screen_result(parse_screen_json(text), text, model)
        except LLMError as exc:
            last = exc
            _cooldown[f"see:{model}"] = time.time() + 60
            continue
        usage.spend("read")
        return out
    raise last


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
