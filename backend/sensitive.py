"""sensitive.py - find and hide private details in chats before imy keeps or learns from them.

Real chats hold things no chat model should keep: a password someone sent, a one-time code, a GCash
or bank account number, a card number, an ID number. Every message is checked as it's saved:
screenshots, pasted text, export files, the interview, edits in Review, things taught in the chat,
and what you type to them in the chat itself. Anything found is replaced with a short marker such as
"[password hidden]", so imy never learns it, never repeats it, and never sends it to an AI service.

It leans towards hiding: hiding a harmless word costs little, keeping a password costs a lot. It is
still pattern matching, not a guarantee, which is why the upload screen asks people to check Review
and remove anything else private.

    mask(text)            -> (text with markers, Counter of kinds found)
    mask_messages(msgs)   -> (copies of the message dicts with "text" masked, Counter)
    describe(counts)      -> "2 passwords and 1 account number" ("" when nothing was found)
    hide_existing()       -> one-time sweep of chats saved before this check existed
"""
from __future__ import annotations

import json
import re
from collections import Counter

# kind: (one, many). The order is the order describe() lists them in.
KINDS = {
    "password": ("password", "passwords"),
    "code": ("one-time code or PIN", "one-time codes or PINs"),
    "card": ("card number", "card numbers"),
    "account": ("account number", "account numbers"),
    "id": ("ID number", "ID numbers"),
    "key": ("API key", "API keys"),
    "login": ("username", "usernames"),
    "email": ("email address", "email addresses"),
    "phone": ("phone number", "phone numbers"),
}
# When two finds overlap, the more specific kind wins ("gcash 0917..." is an account, not just a phone).
PRIORITY = ["key", "password", "code", "card", "account", "id", "login", "email", "phone"]


def marker(kind: str) -> str:
    return f"[{KINDS[kind][0]} hidden]"


_MARKER = re.compile(r"\[[a-zA-Z ,-]{3,40} hidden\]")
_TRAIL = ".,;:?)]}\"'”’"   # stripped from the end of a value ("my password is abc123." -> abc123)

# ---------------------------------------------------------------- the patterns
_KEYS = re.compile(
    r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}"          # Anthropic / OpenAI
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"
    r"|\bAKIA[0-9A-Z]{16}\b|\bAIza[0-9A-Za-z_\-]{35}"   # AWS, Google
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bgsk_[A-Za-z0-9]{20,}"   # Slack, Groq
    r"|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")   # a sign-in token (JWT)

_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")

# Philippine mobile numbers: 0917 123 4567, +63 917 123 4567, 639171234567
_PHONE = re.compile(r"(?<![\d+])(?:\+?63[\s-]?|0)9\d{2}[\s-]?\d{3}[\s-]?\d{4}(?!\d)")

# Long digit runs that pass the card checksum (13-19 digits, spaces or dashes allowed)
_CARD = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")

_ACCOUNT_WORDS = (r"account|acct|acc(?:t)?\.?\s*(?:no|num|number|#)|bank|bdo|bpi|metrobank|landbank|land\s*bank|"
                  r"unionbank|union\s*bank|security\s*bank|pnb|rcbc|china\s*bank|eastwest|ps\s*bank|gcash|g-cash|"
                  r"maya|paymaya|seabank|gotyme|cimb|paypal|coins\.ph|palawan|cebuana|remittance")
_ACCOUNT = re.compile(rf"(?i)\b(?:{_ACCOUNT_WORDS})\b[^\d\n]{{0,25}}?(\d[\d -]{{6,22}}\d)")

_ID_WORDS = (r"sss|tin|philhealth|phil\s*health|pag-?ibig|hdmf|umid|crn|passport|driver'?s?\s*licen[cs]e|"
             r"licen[cs]e\s*(?:no|number|#)|national\s*id|phil\s*sys|philsys|psn|prc|postal\s*id|voter'?s?\s*id|"
             r"student\s*(?:no|number|id)|employee\s*(?:no|number|id)")
_ID = re.compile(rf"(?i)\b(?:{_ID_WORDS})\b[^\n]{{0,30}}?\b([A-Z]{{0,3}}\d[\dA-Z-]{{4,24}})")

# A one-time code or PIN: the word, then 4-8 digits (3-4 for a card's security code)
_CODE_WORDS = (r"otp|one[\s-]*time\s*(?:pin|code|password)|verification\s*code|security\s*code|login\s*code|"
               r"auth(?:entication)?\s*code|2fa(?:\s*code)?|passcode|pin(?:\s*code)?|mpin|code")
_CODE = re.compile(rf"(?i)\b(?:{_CODE_WORDS})\b[^\d\n]{{0,20}}?(?<!\d)(\d(?:[ -]?\d){{3,7}})(?!\d)")
_CODE_BEFORE = re.compile(r"(?i)(?<!\d)(\d{4,8})(?!\d)\s*(?:is|ang|=)?\s*(?:your|ur|yung|ung|ang)?\s*"
                          r"(?:otp|one[\s-]*time|verification|security|login|auth|2fa|pin|mpin|code)\b")
_CVV = re.compile(r"(?i)\b(?:cvv|cvc|cvv2|csc)\b[^\d\n]{0,10}?(?<!\d)(\d{3,4})(?!\d)")

# A password: the word, a few connecting words (English or Tagalog), then the password itself.
# "pw" and "pwd" are everyday Taglish for "pwede", so on their own they only count when written
# like a label ("pw: abc123"), see _PASS_COLON.
_PW_WORDS = r"password|pass\s*word|passwd|passwrd|pword|passcode|wi-?fi\s*pass(?:word)?"
_GLUE = {"ko", "mo", "nya", "niya", "nila", "namin", "natin", "is", "was", "ay", "yung", "ung", "na", "ang",
         "nung", "kasi", "pala", "dun", "to", "the", "my", "your", "ur", "our", "new", "bago", "old", "luma",
         "sa", "ng", "nang", "for", "of", "wifi", "gcash", "fb", "ig", "email", "gmail", "account", "acct",
         "ni", "ng", "mga", "here", "eto", "ito", "heto", "dito", "nito", "yan", "iyan", "e", "eh", "po", "-",
         "–", "—", ":", "=", "->", "=>", "pa"}
_NOT_A_PASSWORD = {
    "hidden", "wrong", "mali", "same", "pareho", "parehas", "changed", "chinange", "napalitan", "palitan",
    "change", "reset", "forgot", "forget", "nakalimutan", "limot", "incorrect", "correct", "tama", "required",
    "needed", "kailangan", "expired", "invalid", "okay", "sent", "nasend", "send", "ready", "below", "above",
    "there", "ano", "what", "hindi", "not", "rin", "din", "lang", "muna", "nalang", "please", "pls", "sige",
    "yes", "oo", "wala", "meron", "also", "too", "case", "sensitive", "protected", "manager", "strength",
    "strong", "weak", "mahina", "long", "short", "safe", "secure", "updated", "update", "saved", "save",
    "stored", "ask", "itanong", "tanong", "sorry", "later", "mamaya", "bukas", "tomorrow", "na-reset",
    "na-change", "remember", "tandaan", "alam", "know", "pala", "dapat", "should", "must", "will", "can",
    "cant", "can't", "dont", "don't", "hide", "share", "isend", "ibigay", "give", "type", "enter", "input",
    "field", "box", "link", "page", "screen", "form", "reset.", "and", "or", "but", "pero", "tapos", "then",
    "with", "without", "walang", "same.", "used", "gamit", "using", "lagi", "always", "never", "ever",
    "hint", "tip", "rules", "policy", "requirements", "characters", "chars", "letters", "numbers",
    "like", "still", "just", "very", "really", "super", "sobrang", "sana", "yata", "daw", "raw", "nga",
    "naman", "talaga", "kaya", "ganun", "ganito", "iba", "other", "different", "their", "this", "that",
    "these", "those", "nila", "ninyo", "niyo", "nyo", "kung", "kapag", "pag", "when", "because", "since",
    "until", "hanggang", "ulit", "again", "agad", "asap", "please.", "nalang.", "check", "tingnan", "mo",
    "kanina", "earlier", "already", "nakita", "seen", "dito", "doon", "yung", "iyong", "inyo", "mine",
    "yours", "ours", "akin", "sayo", "sa'yo", "atin", "lahat", "all", "every", "each", "some", "ilan",
}
_PW = re.compile(rf"(?i)\b(?:{_PW_WORDS})\b")
_PASS_COLON = re.compile(r"(?i)\b(?:pass|pw|pwd|pword)\s*[:=]\s*(\S{3,64})")
_LOGIN = re.compile(r"(?i)\b(?:username|user\s*name|user\s*id|login|log-?in\s*id|acc(?:ount)?\s*name)\s*[:=]\s*(\S{3,64})")

_TOKEN = re.compile(r"\S+")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _luhn(number: str) -> bool:
    total, odd = 0, True
    for ch in reversed(number):
        d = int(ch)
        if not odd:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        odd = not odd
    return total % 10 == 0


def _looks_like_password(value: str, explicit: bool) -> bool:
    """`explicit`: written as "password: X" / "password = X", so X is meant as the password."""
    word = value.lower().strip(_TRAIL)
    if len(word) < (3 if explicit else 4) or word in _NOT_A_PASSWORD or word in _GLUE or _MARKER.fullmatch(value):
        return False
    if explicit:
        return True
    has_digit = any(c.isdigit() for c in value)
    has_symbol = any(not c.isalnum() for c in value.strip(_TRAIL))
    mixed = any(c.isupper() for c in value[1:]) and any(c.islower() for c in value)
    return has_digit or has_symbol or mixed or len(word) >= 6


def _passwords(text: str):
    """Spans of the password itself after "password ... is X" (English or Tagalog)."""
    for m in _PW.finditer(text):
        pos, explicit = m.end(), False
        for _ in range(5):                         # skip up to 4 connecting words: "password ko sa gmail ay ..."
            nxt = _TOKEN.search(text, pos)
            if not nxt or "\n" in text[pos:nxt.start()]:
                break
            tok = nxt.group()
            if tok.lower().rstrip(":=") in _GLUE or tok.strip(":=") == "":
                explicit = explicit or ":" in tok or "=" in tok
                pos = nxt.end()
                continue
            lead = len(tok) - len(tok.lstrip(":="))   # "password:abc123" / "password =abc123"
            explicit = explicit or lead > 0
            value = tok[lead:].rstrip(_TRAIL)
            start = nxt.start() + lead
            if value and _looks_like_password(value, explicit):
                yield start, start + len(value)
            break
    for m in _PASS_COLON.finditer(text):
        value = m.group(1).rstrip(_TRAIL)
        if _looks_like_password(value, True):
            yield m.start(1), m.start(1) + len(value)


def scan(text: str) -> list[tuple[int, int, str]]:
    """Where the private details are: [(start, end, kind)], not overlapping, in text order."""
    if not text:
        return []
    found: list[tuple[int, int, str]] = []

    def add(start: int, end: int, kind: str) -> None:
        if end > start:
            found.append((start, end, kind))

    for m in _KEYS.finditer(text):
        add(m.start(), m.end(), "key")
    for s, e in _passwords(text):
        add(s, e, "password")
    for rx in (_CODE, _CVV):
        for m in rx.finditer(text):
            add(m.start(1), m.end(1), "code")
    for m in _CODE_BEFORE.finditer(text):
        add(m.start(1), m.end(1), "code")
    for m in _CARD.finditer(text):
        digits = _digits(m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits) and len(set(digits)) > 1:
            add(m.start(), m.end(), "card")
    for m in _ACCOUNT.finditer(text):
        if 8 <= len(_digits(m.group(1))) <= 20:
            add(m.start(1), m.end(1), "account")
    for m in _ID.finditer(text):
        if len(_digits(m.group(1))) >= 5:
            add(m.start(1), m.end(1), "id")
    for m in _LOGIN.finditer(text):
        value = m.group(1).rstrip(_TRAIL)
        if value.lower() not in _NOT_A_PASSWORD and not _MARKER.fullmatch(value):
            add(m.start(1), m.start(1) + len(value), "login")
    for m in _EMAIL.finditer(text):
        add(m.start(), m.end(), "email")
    for m in _PHONE.finditer(text):
        add(m.start(), m.end(), "phone")

    taken = [(m.start(), m.end()) for m in _MARKER.finditer(text)]   # never re-hide a marker
    keep: list[tuple[int, int, str]] = []
    for s, e, kind in sorted(found, key=lambda f: (PRIORITY.index(f[2]), f[0], -(f[1] - f[0]))):
        if any(s < te and ts < e for ts, te in taken):
            continue
        taken.append((s, e))
        keep.append((s, e, kind))
    return sorted(keep)


def mask(text: str) -> tuple[str, Counter]:
    hits = scan(text)
    if not hits:
        return text, Counter()
    out, last = [], 0
    for s, e, kind in hits:
        out += [text[last:s], marker(kind)]
        last = e
    out.append(text[last:])
    return "".join(out), Counter(kind for _, _, kind in hits)


# A message that ends by announcing a secret, which then comes in the next bubble:
#   "password ko:" / "eto yung wifi pass" -> "Mahal1234!"      "otp?" / "anong code" -> "482913"
_ASKS_PASSWORD = re.compile(rf"(?i)(?:\b(?:{_PW_WORDS})\b[\s\w]{{0,20}}?[:=?]?|\b(?:pass|pw|pwd)\s*[:=])\s*$")
_ASKS_CODE = re.compile(rf"(?i)\b(?:{_CODE_WORDS}|cvv|cvc)\b[\s\w]{{0,20}}?[:=?]?\s*$")
_ASKS_ACCOUNT = re.compile(rf"(?i)\b(?:{_ACCOUNT_WORDS})\b[\s\w.#]{{0,25}}?[:=?]?\s*$")


def _follow_up(prev: str, text: str) -> str:
    """The kind of secret `text` is when the message before it announced one, or ""."""
    value = text.strip()
    if not value or " " in value or _MARKER.search(value):
        return ""
    digits = _digits(value)
    if _ASKS_CODE.search(prev) and value.replace(" ", "").replace("-", "").isdigit() and 3 <= len(digits) <= 8:
        return "code"
    if _ASKS_ACCOUNT.search(prev) and 8 <= len(digits) <= 20 and len(digits) >= len(value) - 4:
        return "account"
    if _ASKS_PASSWORD.search(prev) and _looks_like_password(value, explicit=prev.rstrip().endswith((":", "="))):
        return "password"
    return ""


def mask_messages(msgs: list[dict]) -> tuple[list[dict], Counter]:
    total, out, prev = Counter(), [], ""
    for m in msgs:
        raw = m.get("text") or ""
        kind = _follow_up(prev, raw)
        text, hits = (raw.replace(raw.strip(), marker(kind)), Counter({kind: 1})) if kind else mask(raw)
        total += hits
        out.append({**m, "text": text})
        prev = raw
    return out, total


def describe(counts) -> str:
    """{"password": 2, "account": 1} -> "2 passwords and 1 account number"."""
    counts = Counter(counts or {})
    parts = [f"{counts[k]} {KINDS[k][0] if counts[k] == 1 else KINDS[k][1]}" for k in KINDS if counts.get(k)]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


# ---------------------------------------------------------------- chats saved before this existed
SWEEP = "hide-private-details-v1"   # bump the version when the patterns improve, and it runs again


def hide_existing() -> int:
    """Hide private details in everything saved before this check existed. Runs once per SWEEP
    version (remembered in the upkeep table). Returns how many things it changed."""
    from .db import bump_version, db, now

    with db() as conn:
        if conn.execute("SELECT 1 FROM upkeep WHERE name = ?", (SWEEP,)).fetchone():
            return 0
    changed, persons = 0, set()
    with db() as conn:
        for row in conn.execute("SELECT id, person_id, text FROM messages").fetchall():
            text, hits = mask(row["text"] or "")
            if hits:
                conn.execute("UPDATE messages SET text = ? WHERE id = ?", (text, row["id"]))
                changed += 1
                persons.add(row["person_id"])
        for row in conn.execute("SELECT id, person_id, raw_text, meta FROM screenshots").fetchall():
            raw, raw_hits = mask(row["raw_text"] or "")
            msg_hits = Counter()
            for m in conn.execute("SELECT text FROM messages WHERE screenshot_id = ?", (row["id"],)):
                msg_hits += Counter(k for k in re.findall(r"\[([a-zA-Z ,-]+) hidden\]", m["text"] or ""))
            kinds = {v[0]: k for k, v in KINDS.items()}
            hits = raw_hits | Counter({kinds[name]: n for name, n in msg_hits.items() if name in kinds})
            if hits:
                try:
                    meta = json.loads(row["meta"] or "{}")
                except ValueError:
                    meta = {}
                meta["sensitive"] = dict(hits)
                conn.execute("UPDATE screenshots SET raw_text = ?, meta = ? WHERE id = ?", (raw, json.dumps(meta), row["id"]))
                changed += 1
                persons.add(row["person_id"])
        for row in conn.execute("SELECT id, text FROM chat_messages WHERE role = 'user'").fetchall():
            text, hits = mask(row["text"] or "")
            if hits:
                conn.execute("UPDATE chat_messages SET text = ? WHERE id = ?", (text, row["id"]))
                changed += 1
        for pid in persons:
            bump_version(conn, pid)
        conn.execute("INSERT OR REPLACE INTO upkeep (name, done_at) VALUES (?, ?)", (SWEEP, now()))
    if changed:
        print(f"[imy] hid private details in {changed} saved item(s) from before this check existed")
    return changed
