"""looks.py - which app a chat looks like, and in what colours.

Groq's image model reads each screenshot (llm.read_screenshot) and says which app it's from, whether
it's dark or light, the chat's name, and the colours of the background and both bubbles. This file
turns those into what the chat screen needs:

  colours()      a screenshot's colours -> the fields the chat screen copies (with readable text colours)
  summarize()    one look for a person from all their screenshots (newest first)
  same_person()  does a chat's name belong to the person being trained? (the wrong-chat warning)
"""
import difflib
import re

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def rgb_of(text: str) -> tuple[int, int, int] | None:
    """"#1a2b3c" or "1a2b3c" -> (26, 43, 60); anything else -> None."""
    m = _HEX.match((text or "").strip())
    if not m:
        return None
    h = m.group(1)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


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


def colours(background: str, yours: str, theirs: str) -> dict:
    """Hex colours read from a screenshot -> {"bg", "user", "user_ink", "them", "them_ink", "mode"}.
    Colours that couldn't be read are left out, so the app's usual palette is used for them."""
    out: dict = {}
    bg, user, them = rgb_of(background), rgb_of(yours), rgb_of(theirs)
    if bg:
        out.update(bg=hex_of(bg), mode=mode_of(bg))
    if user and them:          # the chat screen copies the bubbles only as a pair
        out.update(user=hex_of(user), user_ink=ink_for(user), them=hex_of(them), them_ink=ink_for(them))
    return out


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
