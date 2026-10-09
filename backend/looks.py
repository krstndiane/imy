"""looks.py - recognise WHICH APP a screenshot is from and HOW it looks.

parser.parse_screen() calls analyze() after the bubbles are found. It combines three kinds of clues,
the way a person would recognise an app at a glance:

  1. WORDS      app text that survives OCR: "Turn on active status" (Messenger), "iMessage" in the
                message box, "last seen recently" (Telegram), "end-to-end encrypted" (WhatsApp)...
  2. COLOURS    your bubble colour, their bubble colour and the background, compared with each app's
                light and dark palettes (WhatsApp's pale green, Messenger's blue, iMessage's grey...)
  3. LAYOUT     Messenger and Instagram put a small profile picture next to the other person's
                messages; iMessage, WhatsApp and Telegram (one-to-one chats) don't.

It also reads the chat's NAME from the title bar, so imy can tell when a screenshot is from a
different conversation than the person you're training.

The colours it measures are returned too: the chat screen can then copy your real chat (custom
Messenger themes included) instead of a generic palette.
"""
import difflib
import re

import numpy as np

# Reference colours (RGB), measured from real screenshots. Several per slot: apps change shades
# between versions, and Messenger's gradient goes from blue to violet down the screen.
PALETTES = {
    "messenger": {
        "light": {"bg": [(255, 255, 255)], "user": [(0, 132, 255), (10, 124, 255), (18, 109, 255), (69, 96, 246)],
                  "them": [(240, 240, 240), (228, 230, 235), (233, 233, 235)]},
        "dark": {"bg": [(0, 0, 0), (36, 37, 38), (24, 25, 26)], "user": [(0, 132, 255), (18, 109, 255), (49, 106, 246), (69, 96, 246)],
                 "them": [(51, 51, 52), (48, 48, 48), (62, 64, 66)]},
    },
    "imessage": {
        "light": {"bg": [(255, 255, 255)], "user": [(0, 122, 255), (27, 132, 255), (52, 199, 89)],
                  "them": [(233, 233, 235), (229, 229, 234)]},
        "dark": {"bg": [(0, 0, 0)], "user": [(10, 132, 255), (72, 146, 247), (48, 209, 88)],
                 "them": [(38, 38, 40), (44, 44, 46), (58, 58, 60)]},
    },
    "instagram": {
        "light": {"bg": [(255, 255, 255)], "user": [(55, 151, 240), (112, 63, 247), (88, 81, 219), (127, 61, 247)],
                  "them": [(239, 239, 239), (243, 243, 243)]},
        "dark": {"bg": [(0, 0, 0), (18, 18, 18)], "user": [(55, 151, 240), (112, 63, 247), (88, 81, 219)],
                 "them": [(38, 38, 38), (33, 33, 33)]},
    },
    "whatsapp": {
        "light": {"bg": [(239, 234, 226), (229, 221, 213), (236, 229, 221)], "user": [(217, 253, 211), (220, 248, 198), (231, 255, 219)],
                  "them": [(255, 255, 255)]},
        "dark": {"bg": [(11, 20, 26), (18, 27, 34)], "user": [(0, 92, 75), (20, 77, 55), (5, 97, 98)],
                 "them": [(32, 44, 51), (31, 44, 52), (36, 45, 52)]},
    },
    "telegram": {
        "light": {"bg": [(201, 219, 233), (168, 195, 159), (214, 229, 212)], "user": [(239, 253, 222), (225, 255, 199)],
                  "them": [(255, 255, 255)]},
        "dark": {"bg": [(14, 22, 33), (24, 34, 45)], "user": [(43, 82, 120), (118, 106, 200), (49, 99, 147)],
                 "them": [(24, 37, 51), (33, 48, 63)]},
    },
    "viber": {
        "light": {"bg": [(255, 255, 255), (242, 242, 247)], "user": [(115, 96, 242), (124, 82, 255), (232, 227, 255)],
                  "them": [(242, 242, 247), (235, 235, 240)]},
        "dark": {"bg": [(18, 18, 18), (0, 0, 0)], "user": [(115, 96, 242), (102, 88, 214)],
                 "them": [(44, 44, 46), (38, 38, 40)]},
    },
}
COLOR_WEIGHT = {"user": 2.0, "them": 1.0, "bg": 0.8}
COLOR_SCALE = 90.0      # an RGB distance this big counts as "not this app's colour" at all

# App text that OCR picks up. Each pattern that appears anywhere on the screen adds HINT_WEIGHT.
HINTS = {
    "messenger": [r"\bmessenger\b", r"turn on active status", r"\bactive now\b", r"\bactive \d+\s?(m|h|min|hr)\b",
                  r"\byou sent\b", r"replied to (you|yourself|\w+)", r"^\W*aa\W*$", r"friends on facebook",
                  r"\bchat theme\b", r"say hi to"],
    "instagram": [r"\binstagram\b", r"message\.\.\.|message…", r"\bview profile\b", r"\bliked a message\b",
                  r"\bsent you a\b", r"\binstagram\b"],
    "imessage": [r"\bimessage\b", r"\btext message\b", r"\bdelivered\b", r"\bread \d", r"\b\d+ repl(y|ies)\b",
                 r"\b\d+ items\b", r"\bsms\b"],
    "whatsapp": [r"\bwhatsapp\b", r"end-to-end encrypted", r"\blast seen (today|yesterday|at)\b",
                 r"tap here for contact info", r"\bcontact info\b", r"\btype a message\b"],
    "telegram": [r"\btelegram\b", r"last seen (recently|within|a long time)", r"\bwrite a message\b",
                 r"\bsilent broadcast\b"],
    "viber": [r"\bviber\b", r"\bviber out\b", r"\bsecret chat\b", r"\bfree call\b"],
}
HINT_RES = {p: [re.compile(h, re.I | re.M) for h in hs] for p, hs in HINTS.items()}
HINT_WEIGHT = 2.5
AVATAR_APPS = {"messenger", "instagram"}
MIN_SCORE = 1.6         # below this we say "unknown" rather than guess

# Title-bar text that is not a name.
_NOT_NAME = re.compile(
    r"active|online|offline|last seen|turn on|typing|tap here|tap for|contact info|imessage|facetime|"
    r"\b(today|yesterday|mon|tue|wed|thu|fri|sat|sun)\b|\d{1,2}[:.]\d{2}|\bam\b|\bpm\b", re.I)
_STRIP_EDGES = re.compile(r"^[^\wÀ-ɏ]+|[^\wÀ-ɏ.]+$")


def _dist(a, b) -> float:
    return float(np.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b))))


def luminance(c) -> float:
    r, g, b = (v / 255 for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def mode_of(bg) -> str:
    return "dark" if luminance(bg) < 0.35 else "light"


def hex_of(c) -> str:
    return "#%02x%02x%02x" % tuple(int(max(0, min(255, v))) for v in c[:3])


def ink_for(c) -> str:
    """Readable text colour on a bubble of colour c."""
    return "#ffffff" if luminance(c) < 0.6 else "#111111"


def _median(colors):
    return tuple(int(v) for v in np.median(np.array(colors), axis=0)) if colors else None


def score_platforms(text: str, colors: dict, n_avatars: int) -> dict:
    """{platform: score}. colors: {"bg": rgb, "user": rgb | None, "them": rgb | None}."""
    mode = mode_of(colors["bg"])
    scores = {}
    for p, pal in PALETTES.items():
        s = sum(HINT_WEIGHT for rx in HINT_RES[p] if rx.search(text))
        for slot, w in COLOR_WEIGHT.items():
            c = colors.get(slot)
            if c is not None:
                d = min(_dist(c, ref) for ref in pal[mode][slot])
                s += w * max(0.0, 1 - d / COLOR_SCALE)
        if n_avatars:
            s += 1.5 if p in AVATAR_APPS else -0.5
        scores[p] = round(s, 2)
    return scores


def chat_title(screen) -> str:
    """The contact name in the title bar, e.g. "Lawrence Panes" (empty if it can't be read)."""
    W, H = screen.size
    cands = []
    for l in screen.lines:
        if not (0.035 * H < l.top and l.bottom <= screen.top + 6):
            continue
        t = _STRIP_EDGES.sub("", re.sub(r"^[<«(]?\s*\d{1,3}\b", "", l.text.strip())).strip()
        t = re.sub(r"\s+[>›)\]}|]+.*$", "", t).strip()            # "Lawrence Panes ›" / "Lawrence Panes >)"
        words = t.split()
        if not (1 <= len(words) <= 4) or len(re.sub(r"[^A-Za-zÀ-ɏ]", "", t)) < 3 or _NOT_NAME.search(t):
            continue
        if not any(len(w) >= 3 and w[0].isalpha() for w in words):
            continue
        if _on_colour(screen.rgb, l):          # text on a coloured bubble scrolled under the header
            continue
        cands.append((l.top, l.height, t))
    if not cands:
        return ""
    # The name is the first sizeable text under the status bar; pinned-message banners come after it.
    tallest = max(h for _, h, _ in cands)
    return min(c for c in cands if c[1] >= 0.6 * tallest)[2]


def _on_colour(rgb, l) -> bool:
    patch = rgb[max(0, l.top):max(l.top + 1, l.bottom), max(0, l.left):max(l.left + 1, l.right)].reshape(-1, 3)
    if not len(patch):
        return False
    r, g, b = (np.median(patch, axis=0) / 255.0)
    mx, mn = max(r, g, b), min(r, g, b)
    return mx > 0 and (mx - mn) / mx > 0.35


def same_person(title: str, names: list[str]) -> bool | None:
    """Does the chat title belong to the person being trained? None = can't tell."""
    t = title.lower().strip()
    known = [n.lower().strip() for n in names if n and len(n.strip()) >= 2]
    if not t or not known:
        return None
    for n in known:
        if n in t or t in n or any(w == n for w in t.split()) or difflib.SequenceMatcher(None, t, n).ratio() >= 0.72:
            return True
    return False


def analyze(screen, sides: list[str], raw_text: str) -> dict:
    """Everything the screenshot says about the app. `sides` = 'user'/'person' for each screen.bubbles."""
    user = [r.color for r, s in zip(screen.bubbles, sides) if s == "user"]
    them = [r.color for r, s in zip(screen.bubbles, sides) if s == "person"]
    colors = {"bg": tuple(screen.bg), "user": _median(user), "them": _median(them)}
    scores = score_platforms(raw_text, colors, len(screen.avatars or []))
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    (best, top), (_, second) = ranked[0], ranked[1]
    platform = best if top >= MIN_SCORE else "unknown"
    confidence = 0.0 if platform == "unknown" else round(min(1.0, (top - second) / 3 + 0.35), 2)
    out = {
        "platform": platform, "confidence": confidence, "scores": scores,
        "mode": mode_of(colors["bg"]), "bg": hex_of(colors["bg"]),
        "title": chat_title(screen),
    }
    if colors["user"]:
        out["user"], out["user_ink"] = hex_of(colors["user"]), ink_for(colors["user"])
        # Messenger and Instagram colour your bubbles with a gradient down the screen: keep both ends.
        mine = sorted((r.y0, r.color) for r, s in zip(screen.bubbles, sides) if s == "user")
        if len(mine) >= 2 and _dist(mine[0][1], mine[-1][1]) > 25:
            out["user_top"], out["user_bottom"] = hex_of(mine[0][1]), hex_of(mine[-1][1])
    if colors["them"]:
        out["them"], out["them_ink"] = hex_of(colors["them"]), ink_for(colors["them"])
    return out


def summarize(metas: list[dict], chosen: str = "") -> dict:
    """One look for a person from all their screenshots' metadata (newest first).
    Returns {"platform", "detected", "mode", "bg", "user", "them", ...} - colours only from screenshots of
    the app the chat is shown as."""
    found = [m for m in metas if m.get("platform") not in (None, "", "unknown")]
    counts: dict[str, float] = {}
    for m in found:
        counts[m["platform"]] = counts.get(m["platform"], 0) + 0.5 + m.get("confidence", 0)
    detected = max(counts, key=counts.get) if counts else ""
    platform = chosen or detected
    look = {"platform": platform, "detected": detected, "counts": {k: round(v, 1) for k, v in counts.items()}}
    same = [m for m in found if m["platform"] == platform and m.get("user") and m.get("them")]
    if same:
        m = same[0]
        look.update({k: m[k] for k in ("mode", "bg", "user", "user_ink", "them", "them_ink") if k in m})
        grad = next((g for g in same if g.get("user_top")), None)
        if grad:
            look.update(user_top=grad["user_top"], user_bottom=grad["user_bottom"])
    elif metas and metas[0].get("mode"):
        look["mode"] = metas[0]["mode"]
    return look
