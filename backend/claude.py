"""claude.py - optional Claude API mode (off unless ANTHROPIC_API_KEY is set).

With a key in the environment (or in a `.env` file in the project root) imy uses Claude for:

  1. SCREENSHOTS  Claude reads the image directly and returns who said what,
                  instead of Tesseract OCR + the layout rules in parser.py.
  2. CHAT         Claude writes the reply in the person's voice, using the interview
                  answers, the style summary and the whole parsed chat history.

Without a key nothing here runs and nothing leaves your computer. With a key,
screenshots, parsed messages, interview answers and your chat are sent to Anthropic.
If a call fails, callers fall back to the local path.
"""
import base64
import io
import json
import os
from pathlib import Path

from PIL import Image, ImageOps

try:  # optional: lets the key live in a .env file instead of the shell
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

MODEL = os.environ.get("IMY_CLAUDE_MODEL", "claude-opus-5-5")
# Refused requests are re-routed to another model server-side instead of failing.
FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
MAX_IMAGE_EDGE = 2576

_client = None


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("IMY_CLAUDE", "1") != "0"


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(timeout=120.0)
    return _client


def explain(exc: Exception) -> str:
    """One short, user-facing reason a Claude call failed."""
    import anthropic
    if isinstance(exc, anthropic.AuthenticationError):
        return "the Anthropic API key was rejected"
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "this API key isn't allowed to use that model"
    if isinstance(exc, anthropic.RateLimitError):
        return "the Anthropic rate limit was hit"
    if isinstance(exc, anthropic.APIStatusError):
        return f"Anthropic returned an error ({exc.status_code})"
    if isinstance(exc, anthropic.APIConnectionError):
        return "couldn't reach Anthropic (no internet?)"
    return str(exc) or exc.__class__.__name__


def _text(response) -> str:
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's answer was cut off")
    return "".join(b.text for b in response.content if b.type == "text").strip()


# ------------------------------------------------------------------ screenshots
OCR_SYSTEM = """# ROLE AND PURPOSE
You are an expert OCR and UI Parsing Engine. Your job is to analyze an uploaded chat screenshot, identify the messaging platform, extract UI metadata (colors, typography, layout), and parse ONLY the active message logs while completely filtering out non-chat UI elements.

### TASK INSTRUCTIONS

1. **PLATFORM & THEME IDENTIFICATION:**
   - Detect the platform (e.g., Facebook Messenger, Instagram DMs, iMessage, WhatsApp, Telegram, Discord, Viber, Twitter/X DMs).
   - Extract UI styles: background color (hex), dark or light mode, bubble color (sender vs. receiver).
   - Detect special elements (e.g., reaction badges like the smiley face icon with a plus sign, read receipts, timestamps).

2. **UI FILTERING & EXCLUSION RULES (STRICT):**
   - **IGNORE System Bars:** Status bar (time, battery, Wi-Fi), navigation bar, notch/island.
   - **IGNORE Header UI:** Contact name, profile picture, call/video buttons, back arrow, "Active Now" status.
   - **IGNORE Pinned Messages:** Any top banner, "Pinned Message" bar, or pinned preview area MUST NOT be parsed as chat text.
   - **IGNORE System Notifications:** "You set the emoji to...", "Unsent a message", date separators, encrypted chat notices.
   - **IGNORE Input Area:** "Message...", camera icon, mic icon, send button, keyboard.
   - **IGNORE quoted previews:** the small greyed "replying to" snippet above a bubble is not a new message.

3. **CHAT TEXT EXTRACTION:**
   - Extract only actual message bubbles, top to bottom, one entry per bubble.
   - Differentiate between **User** (self, right-aligned) and **Target** (the other person, left-aligned). In layouts with a name above each message instead of sides (e.g. Discord), use the names.
   - Preserve text casing, emojis, typos, line breaks, and punctuation exactly as written. Do not translate, correct or complete anything.
   - Skip bubbles that contain no text (photos, stickers, voice notes).
   - If the image is not a chat screenshot, return an empty parsed_chat_history."""

OCR_SCHEMA = {
    "type": "object",
    "properties": {
        "platform_metadata": {
            "type": "object",
            "properties": {
                "detected_platform": {"type": "string", "enum": [
                    "Messenger", "Instagram", "iMessage", "WhatsApp", "Telegram", "Discord", "Viber", "Twitter/X", "Unknown"]},
                "theme_mode": {"type": "string", "enum": ["dark", "light"]},
                "background_color": {"type": "string"},
                "target_bubble_color": {"type": "string"},
                "user_bubble_color": {"type": "string"},
                "has_reaction_icon": {"type": "boolean"},
            },
            "required": ["detected_platform", "theme_mode", "background_color",
                         "target_bubble_color", "user_bubble_color", "has_reaction_icon"],
            "additionalProperties": False,
        },
        "parsed_chat_history": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sender": {"type": "string", "enum": ["Target", "User"]},
                    "text": {"type": "string"},
                    "timestamp": {"type": ["string", "null"]},
                    "reaction": {"type": ["string", "null"]},
                },
                "required": ["sender", "text", "timestamp", "reaction"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["platform_metadata", "parsed_chat_history"],
    "additionalProperties": False,
}

PLATFORM_KEYS = {"Messenger": "messenger", "Instagram": "instagram", "iMessage": "imessage", "WhatsApp": "whatsapp",
                 "Telegram": "telegram", "Discord": "discord", "Viber": "viber", "Twitter/X": "twitter", "Unknown": "unknown"}


def _image_block(img: Image.Image) -> dict:
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = base64.standard_b64encode(buf.getvalue()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def parse_screenshot(img: Image.Image, person_names: list[str]) -> dict:
    """Same return shape as parser.parse_screenshot()."""
    hint = "Parse this chat screenshot."
    if person_names:
        hint += " The Target person may appear under the name(s): " + ", ".join(dict.fromkeys(person_names)) + "."
    response = client().beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=OCR_SYSTEM,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": OCR_SCHEMA}},
        messages=[{"role": "user", "content": [_image_block(img), {"type": "text", "text": hint}]}],
        **FALLBACK,
    )
    raw = _text(response)
    data = json.loads(raw)
    messages = [
        {"speaker": "person" if m["sender"] == "Target" else "user", "text": m["text"].strip()}
        for m in data["parsed_chat_history"] if m["text"].strip()
    ]
    speakers = {m["speaker"] for m in messages}
    return {
        "platform": PLATFORM_KEYS.get(data["platform_metadata"]["detected_platform"], "unknown"),
        "messages": messages,
        "raw_text": raw,  # the full JSON, incl. theme colours, timestamps and reactions
        "needs_review": len(speakers) < 2,
    }


# ------------------------------------------------------------------ chat
PERSONA_SYSTEM = """# SYSTEM INSTRUCTION: PLATFORM-AWARE CHAT AGENT

### ROLE & CORE PURPOSE
You are simulating a persona based on provided chat history and questionnaire details. Your goal is to engage in a realistic, natural conversation that matches the communication habits, tone, and formatting of the target person on a specific messaging platform. The person you are talking to knows this is a simulation built from their own screenshots.

---

### CONTEXT DATA
- **Target Name:** {name}
- **Relationship Dynamic:** {relationship}
- **Active Platform:** {platform}

**Key Personality Attributes (questionnaire answers from the user):**
{facts}

**How {name} writes (measured from their real messages):**
{style}

**Extracted Chat History** ("User" is the person you are talking to; blank lines separate screenshots):
{logs}

---

### CONVERSATIONAL BEHAVIOR RULES

1. **Natural Human Flow:**
   - Do not write long, overly polished paragraphs unless {name} types that way in the chat history.
   - Match sentence length, rhythm, lowercasing, abbreviation use (e.g., omg, fr, lol, sige, haha), language mix and emoji density of {name}'s real messages.
   - The chat history shows HOW {name} writes. It is not a script: never paste an old message as your reply. Always answer what the user just said, logically and in context, the way a real person would.
   - Respond like a real human on a chat app. Never sound like an AI assistant, never summarise, never explain yourself.

2. **Platform-Specific Adaptations:**
   - **Messenger:** short, multi-message bursts; casual casing.
   - **Instagram DMs:** lowercase typing, modern internet shorthand, concise thoughts.
   - **iMessage:** natural auto-capitalization, direct phrase structures.
   - **WhatsApp:** complete sentences, standard punctuation, direct updates.
   Where {name}'s real messages differ from these platform habits, their real messages win.

3. **Memory & Context Usage:**
   - Reference inside jokes, past shared events, and background info naturally, without ever citing "the logs", "the screenshots" or "memory". Do not invent specific facts or events that aren't supported by the context.

4. **Safety & Boundaries:**
   - Stay in character while keeping this a safe, comforting space. Do not simulate harmful, manipulative or toxic behaviour, even if it appears in the history.

### OUTPUT FORMAT
Write ONLY the message(s) {name} would send next: no narration, no quotation marks, no name prefix. If {name} would send several short messages in a row, put each on its own line (at most {max_bubbles})."""

PLATFORM_LABELS = {"messenger": "Messenger", "instagram": "Instagram DMs", "imessage": "iMessage", "whatsapp": "WhatsApp",
                   "telegram": "Telegram", "discord": "Discord", "viber": "Viber", "twitter": "Twitter/X DMs"}


def persona_system(name: str, relationship: str, platform: str, facts: str, style: str, messages: list[dict],
                   max_bubbles: int) -> str:
    lines, shot = [], None
    for m in messages:
        if shot is not None and m["screenshot_id"] != shot:
            lines.append("")
        shot = m["screenshot_id"]
        lines.append(f"{name if m['speaker'] == 'person' else 'User'}: {m['text']}")
    return PERSONA_SYSTEM.format(
        name=name, relationship=relationship or "(not given)",
        platform=PLATFORM_LABELS.get(platform, "Unknown (follow the chat history)"),
        facts=facts or "(nothing provided)", style=style or "(unknown)",
        logs="\n".join(lines) or "(none yet)", max_bubbles=max_bubbles,
    )


def chat(system: str, history: list[dict], user_text: str, max_bubbles: int) -> list[str]:
    """history: [{"role": "user"|"person", "text": ...}] oldest first."""
    turns: list[dict] = []
    for h in history + [{"role": "user", "text": user_text}]:
        role = "user" if h["role"] == "user" else "assistant"
        if turns and turns[-1]["role"] == role:  # several bubbles in a row = one turn
            turns[-1]["content"] += "\n" + h["text"]
        elif turns or role == "user":  # the conversation has to open with the user
            turns.append({"role": role, "content": h["text"]})
    response = client().beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        # The persona + history never change between messages, so cache them.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": "low"},
        messages=turns,
        **FALLBACK,
    )
    bubbles = [l.strip() for l in _text(response).splitlines() if l.strip()][:max_bubbles]
    if not bubbles:
        raise RuntimeError("empty response")
    return bubbles
