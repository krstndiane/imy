"""parser.py - who said what in pasted chat text, plus the clean-up rules shared by every importer.

  parse_text_export()  pasted text: WhatsApp exports or simple "Name: message" lines
  trim_overlap()       screenshots taken while scrolling repeat a few messages: drop the repeats
  is_chrome() / clean_text() / is_contact_header()
                       app text that isn't a message ("Seen", "Active now", times, the title bar),
                       used by cleanup.py and the reply engine

Screenshots are read by Groq's image model now (llm.read_screenshot); the old OCR layout rules
that used to live here are gone. No reader is perfect, so the app has a review step for mistakes.
"""
import difflib
import re

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
    r"^.{0,14}\(message(\s+\S{1,3}){0,2}\W*$",              # the input bar, e.g. "60 @G BG & (Message"
    r"^\W*\d{1,3}\W*$",                                        # lone numbers: unread badges, "1" read markers
    # Messenger / Instagram / WhatsApp / iMessage system lines
    r"^\W*(turn on active status|active \d+\s?(m|h|d|min|mins|hr|hrs|hours?|minutes?) ago|"
    r"you can now (message|call).*|you('re| are) now connected.*|say hi to.*|"
    r"seen by.*|seen \d.*|replied to (you|yourself|themselves|\w+)|you replied to.*|.* replied to you|"
    r".* reacted .* to (your|a) message.*|.* (pinned|unpinned) a message.*|pinned message.*|"
    r".* unsent a message.*|you unsent a message.*|this message was (deleted|unsent).*|"
    r"missed (audio|video|voice) call.*|(audio|video|voice) call( ended)?.*|.* called you.*|tap to call back.*|"
    r"message requests?.*|view profile|you('re| are) friends on facebook.*|"
    r"messages and calls are (end-to-end encrypted|secured).*|.* changed the (theme|chat theme|emoji|nickname).*|"
    r".* set (the|your|their) nickname.*|.* named the group.*|.* (joined|left) the (group|call).*|"
    r"today|yesterday|now|just now|sent|delivered|read|seen|edited)\W*$",
]
CHROME_RES = [re.compile(p, re.I) for p in CHROME_PATTERNS]
TRAILING_TIME_RE = re.compile(rf"\s*{_TIME}\s*[vV✓√/\\]*\s*$", re.I)

# "Maria   Today at 4:32 PM"  /  "Maria — 10/12/2024 4:32 PM"
HEADER_RE = re.compile(
    rf"^(?P<name>[^\d:]{{2,32}}?)\s*[—–-]?\s*(?P<ts>(?:{_DAY}\s+at\s+{_TIME}|{_DATE}[\s,]*(?:at\s+)?(?:{_TIME})?|{_TIME}))\s*$",
    re.I,
)



def is_chrome(text: str) -> bool:
    t = text.strip()
    if len(re.sub(r"[\W_]+", "", t)) == 0:
        return True
    return any(r.match(t) for r in CHROME_RES)


# OCR reads the round profile picture beside a message as "@", "©" or "€".
AVATAR_RE = re.compile(r"^(?:[<(\[]?[@©®€][>)\]]?\s+)+")


def clean_text(text: str) -> str:
    t = TRAILING_TIME_RE.sub("", text.strip())  # WhatsApp "hello  10:32 PM vv"
    t = AVATAR_RE.sub("", t)
    t = re.sub(r"\s[@©®]\s", " ", t)             # ...and again when two lines were joined
    t = re.sub(r"^\|\s", "I ", t)                # OCR reads a leading capital I as "|"
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


def is_contact_header(text: str, person_names: list[str]) -> bool:
    """The chat's title bar: the person's name plus at most a couple of stray OCR tokens."""
    low = text.lower()
    return any(len(n.strip()) >= 4 and n.strip().lower() in low and len(text.split()) <= len(n.split()) + 2
               for n in person_names)


# ---------------------------------------------------------------- who is who
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



def _norm(t: str) -> str:
    return re.sub(r"[\W_]+", "", t.lower())


def trim_overlap(new: list[dict], others: list[list[dict]]) -> tuple[list[dict], int]:
    """Screenshots taken while scrolling overlap: the first (or last) few messages of one are the
    last (or first) few of the one before. Drop that repeated stretch so nothing is learned twice.
    Only the very edges are compared (first messages vs another screenshot's last ones, and the reverse),
    so a repeated "haha" in the middle of a chat is never touched."""
    key = lambda m: (m["speaker"], _norm(m["text"]))
    keys = [key(m) for m in new]
    best_head = best_tail = 0
    for old in others:
        ok = [key(m) for m in old]
        for k in range(min(len(keys), len(ok)), 0, -1):          # start of new == end of an older one
            if keys[:k] == ok[-k:]:
                best_head = max(best_head, k); break
        for k in range(min(len(keys), len(ok)), 0, -1):          # end of new == start of an older one
            if keys[-k:] == ok[:k]:
                best_tail = max(best_tail, k); break
    head, tail = best_head, best_tail
    if head + tail >= len(new):
        return [], len(new)
    return new[head:len(new) - tail], head + tail


# ---------------------------------------------------------------- pasted text
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
