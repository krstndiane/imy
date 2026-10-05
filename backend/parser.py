"""parser.py - decide who said what.

Input : OCR lines with positions (from ocr.py)
Output: a list of {speaker: 'user' | 'person', text: str}

Two layouts are handled:

1. BUBBLE layout (Messenger, iMessage, WhatsApp, Viber, Telegram)
   Your own messages sit on the RIGHT, theirs on the LEFT, and yours are
   usually in a coloured bubble. We group lines into bubbles, then vote on
   the speaker from horizontal position + bubble colour.

2. HEADER layout (Discord, Slack-style)
   Everything is left-aligned; each group starts with "Name  Today at 4:32 PM".
   We read the names and match them against the person's name/nickname
   from the interview.

No parser is perfect, so the app has a review step where you can fix mistakes.
"""
import difflib
import re
from dataclasses import dataclass, field

import numpy as np

# ---------------------------------------------------------------- chrome filters
_TIME = r"\d{1,2}[:.]\d{2}(?::\d{2})?\s*(?:[ap]\.?\s?m\.?)?"
_DAY = r"(?:today|yesterday|(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?)"
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_DATE = rf"(?:{_MONTH}\.?\s+\d{{1,2}}(?:,?\s+\d{{4}})?|\d{{1,2}}[/.-]\d{{1,2}}(?:[/.-]\d{{2,4}})?)"

CHROME_PATTERNS = [
    rf"^\W*(?:{_DAY}|{_DATE})?[\s,at·•-]*(?:{_TIME})?\W*$",  # bare dates / times
    r"^\W*(delivered|seen|read|sent|sending|active now|active \d+\s?[mhd] ago|online|typing\.*|"
    r"last seen.*|type a message.*|write a message.*|message|aa|imessage|text message|"
    r"you sent.*|you replied.*|edited|new messages?|unread messages?|end-to-end encrypted.*|"
    r"messages and calls are end-to-end.*|tap to.*|reply|forwarded)\W*$",
]
CHROME_RES = [re.compile(p, re.I) for p in CHROME_PATTERNS]
TRAILING_TIME_RE = re.compile(rf"\s*{_TIME}\s*[vV✓√/\\]*\s*$", re.I)

# "Maria   Today at 4:32 PM"  /  "Maria — 10/12/2024 4:32 PM"
HEADER_RE = re.compile(
    rf"^(?P<name>[^\d:]{{2,32}}?)\s*[—–-]?\s*(?P<ts>(?:{_DAY}\s+at\s+{_TIME}|{_DATE}[\s,]*(?:at\s+)?(?:{_TIME})?|{_TIME}))\s*$",
    re.I,
)

PLATFORM_HINTS = {
    "whatsapp": ["whatsapp", "end-to-end encrypted", "last seen today", "last seen yesterday"],
    "messenger": ["messenger", "active now", "you sent", "you replied", "aa"],
    "discord": ["message @", "message #", "today at", "yesterday at", "discord"],
    "viber": ["viber", "type a message", "seen"],
    "telegram": ["telegram", "last seen recently", "write a message", "online"],
    "imessage": ["imessage", "delivered", "text message", "read "],
}


def is_chrome(text: str) -> bool:
    t = text.strip()
    if len(re.sub(r"[\W_]+", "", t)) == 0:
        return True
    return any(r.match(t) for r in CHROME_RES)


def detect_platform(raw_text: str) -> str:
    low = raw_text.lower()
    scores = {p: sum(low.count(h) for h in hints) for p, hints in PLATFORM_HINTS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "unknown"


def clean_text(text: str) -> str:
    t = TRAILING_TIME_RE.sub("", text.strip())  # WhatsApp "hello  10:32 PM vv"
    t = re.sub(r"^\|\s", "I ", t)                # OCR reads a leading capital I as "|"
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


# ---------------------------------------------------------------- bubble grouping
@dataclass
class Bubble:
    lines: list = field(default_factory=list)

    @property
    def left(self): return min(l.left for l in self.lines)
    @property
    def right(self): return max(l.right for l in self.lines)
    @property
    def top(self): return min(l.top for l in self.lines)
    @property
    def bottom(self): return max(l.bottom for l in self.lines)
    @property
    def text(self): return " ".join(l.text for l in self.lines)


def group_bubbles(lines, width: int, wrap_limit: int | None = None) -> list[Bubble]:
    """Stack lines into bubbles: close together vertically AND sharing an edge.

    wrap_limit (header layouts only): a line only continues onto the next one if it
    reached this x-position, i.e. it was long enough to have wrapped.
    """
    bubbles: list[Bubble] = []
    for ln in sorted(lines, key=lambda l: (l.top, l.left)):
        if bubbles:
            last = bubbles[-1].lines[-1]
            h = max(ln.height, last.height)
            gap = ln.top - last.bottom
            aligned = abs(ln.left - last.left) < 0.05 * width or abs(ln.right - last.right) < 0.05 * width
            same_row = ln.top < last.bottom - 0.5 * h
            wrapped = wrap_limit is None or last.right >= wrap_limit
            if gap < 0.8 * h and aligned and not same_row and wrapped:
                bubbles[-1].lines.append(ln)
                continue
        bubbles.append(Bubble([ln]))
    return bubbles


def bubble_saturation(rgb: np.ndarray, b: Bubble) -> float:
    """How colourful is the bubble? Sampled from the padding just left of the text."""
    h = max(l.height for l in b.lines)
    x0 = max(0, int(b.left - 1.2 * 0.35 * h * 2))
    x1 = max(x0 + 1, int(b.left - 0.3 * 0.35 * h * 2))
    y0, y1 = max(0, int(b.top)), min(rgb.shape[0], int(b.bottom))
    patch = rgb[y0:y1, x0:x1].reshape(-1, 3)
    if patch.size == 0:
        return 0.0
    r, g, bl = np.median(patch, axis=0) / 255.0
    mx, mn = max(r, g, bl), min(r, g, bl)
    return 0.0 if mx == 0 else float((mx - mn) / mx)


def classify_side(b: Bubble, width: int, sat: float) -> str:
    """Vote 'user' (right side / coloured) or 'person' (left side / grey)."""
    align = ((width - b.right) - b.left) / width      # >0: hugs the left edge
    score = align - (0.15 if sat > 0.28 else 0.0)     # colourful bubbles lean 'user'
    return "person" if score > 0 else "user"


def _is_header_chrome(b: Bubble, width: int, height: int, n_bubbles: int) -> bool:
    """Chat-app title bar (contact name) and centred system notices."""
    if n_bubbles >= 4 and b.bottom < 0.07 * height:
        return True
    centred = abs(((b.left + b.right) / 2) - width / 2) < 0.06 * width and (b.right - b.left) < 0.5 * width
    return centred and len(b.text.split()) <= 4 and n_bubbles >= 4 and b.top < 0.1 * height


def parse_bubbles(lines, size, rgb) -> list[dict]:
    width, height = size
    bubbles = group_bubbles(lines, width)
    out = []
    for b in bubbles:
        if _is_header_chrome(b, width, height, len(bubbles)):
            continue
        text = clean_text(b.text)
        if not text or is_chrome(text):
            continue
        out.append({"speaker": classify_side(b, width, bubble_saturation(rgb, b)), "text": text})
    return out


# ---------------------------------------------------------------- header layout
def _name_matches(name: str, person_names: list[str]) -> bool:
    n = name.lower().strip()
    for p in person_names:
        p = p.lower().strip()
        if p and (p in n or n in p or difflib.SequenceMatcher(None, n, p).ratio() > 0.75):
            return True
    return False


def assign_roles(names: list[str], person_names: list[str]) -> tuple[dict, bool]:
    """Map each speaker name to 'person' or 'user'. Returns (roles, needs_review)."""
    roles = {n: ("person" if _name_matches(n, person_names) else "user") for n in names}
    if "person" in roles.values():
        return roles, False
    # Nobody matched the person's name: guess (first name seen = person), flag for review.
    for i, n in enumerate(names):
        roles[n] = "person" if i == 0 else "user"
    return roles, True


def looks_like_header_layout(lines) -> bool:
    return sum(1 for l in lines if HEADER_RE.match(l.text.strip()) and not is_chrome(l.text)) >= 2


def parse_headers(lines, size, person_names) -> tuple[list[dict], bool]:
    width, _ = size
    groups: list[list] = []  # [name_or_None, [lines]]
    for ln in lines:
        m = HEADER_RE.match(ln.text.strip())
        if m and not is_chrome(ln.text) and len(m.group("name").split()) <= 4:
            groups.append([m.group("name").strip(" -—–:"), []])
        elif not is_chrome(ln.text):
            if not groups:
                groups.append([None, []])
            groups[-1][1].append(ln)
    names = list(dict.fromkeys(g[0] for g in groups if g[0]))
    roles, needs_review = assign_roles(names, person_names)
    body = [l for _, gl in groups for l in gl]
    wrap_limit = int(max(0.8 * max(l.right for l in body), 0.55 * width)) if body else None
    out = []
    for name, glines in groups:
        for b in group_bubbles(glines, width, wrap_limit):
            text = clean_text(b.text)
            if text and not is_chrome(text):
                out.append({"speaker": roles.get(name, "person"), "text": text})
    return out, needs_review


# ---------------------------------------------------------------- public entry points
def parse_screenshot(lines, size, rgb, person_names: list[str]) -> dict:
    raw_text = "\n".join(l.text for l in lines)
    platform = detect_platform(raw_text)
    needs_review = False
    if looks_like_header_layout(lines):
        platform = "discord" if platform == "unknown" else platform
        messages, needs_review = parse_headers(lines, size, person_names)
    else:
        messages = parse_bubbles([l for l in lines if not is_chrome(l.text)], size, rgb)
    return {"platform": platform, "messages": messages, "raw_text": raw_text, "needs_review": needs_review}


_WA_RE = re.compile(r"^\[?\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APap][Mm])?\]?\s*[-–]?\s*(?P<name>[^:]{1,40}):\s*(?P<msg>.*)$")
_PLAIN_RE = re.compile(r"^(?P<name>[^:\n]{1,30}):\s+(?P<msg>.+)$")


def parse_text_export(text: str, person_names: list[str]) -> tuple[list[dict], bool]:
    """Parse pasted chat text: WhatsApp exports or simple 'Name: message' lines."""
    rows: list[list] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _WA_RE.match(line) or _PLAIN_RE.match(line)
        if m:
            rows.append([m.group("name").strip(), m.group("msg").strip()])
        elif rows:
            rows[-1][1] += " " + line  # continuation of a multi-line message
    names = list(dict.fromkeys(r[0] for r in rows))
    roles, needs_review = assign_roles(names, person_names)
    return [{"speaker": roles[n], "text": m} for n, m in rows if m and "<Media omitted>" not in m], needs_review
