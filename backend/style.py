"""style.py - measure HOW a person writes.

Two sources are blended:
  1. The person's real messages from the screenshots (measured numbers)
  2. The interview answers (Q3, Q11-Q15), used as a fallback when there isn't
     much chat data yet.

The result is a plain dict called `style`. The engine uses it to choose
and lightly adjust replies; the UI shows it as "How they write".
"""
import re
import statistics
from collections import Counter

EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U0001F000-\U0001F2FF\u2600-\u27BF\u2764]")
LAUGH_RE = re.compile(r"\b(?:ha){2,}h?\b|\b(?:he){2,}h?\b|\blol+\b|\blmao+\b|\bwkwk+\b|\bcharot\b", re.I)
ELONG_RE = re.compile(r"(\w)\1{2,}")
WORD_RE = re.compile(r"[\w']+")

STOPWORDS = set("i a an the to of and is it in on at for you me my we u ur am are was be do so that this".split())
# Common texting shorthand (English + a little Taglish). Extend freely.
SLANG = set("""ikr tbh idk omg bruh lol lmao ngl kinda gonna wanna gotta hbu wbu nvm rn pls plz ur
u ya yep nope fr istg imo btw brb ily po opo naman talaga kasi diba nga sana sige ay hay hala
grabe charot char sis bes beh""".split())


def _split_list(s: str) -> list[str]:
    return [x.strip(" \"'“”‘’") for x in re.split(r"[,/;\n]| or ", s or "") if x.strip(" \"'“”‘’")]


def interview_prefs(answers: dict) -> dict:
    """Turn the free-text interview answers into simple flags."""
    low = lambda k: (answers.get(k) or "").lower()
    q11, q12, q15 = low("q11"), low("q12"), low("q15")
    length = "long" if re.search(r"long|paragraph|essay|detailed", q11) else \
             "short" if re.search(r"short|brief|few words|one[- ]liner|quick", q11) else None
    if re.search(r"\bno emoji|never use emoji|don'?t use emoji|without emoji", q12):
        emoji = "none"
    elif "emoji" in q12:
        emoji = "heavy"
    else:
        emoji = None
    formality = "formal" if re.search(r"formal|proper|polite", q12 + " " + q15) and "informal" not in q12 else \
                "casual" if re.search(r"casual|slang|chill|informal", q12 + " " + q15) else None
    tone = next((t for t in ("playful", "blunt", "casual", "formal") if t in q15), None)
    return {
        "length_pref": length,
        "emoji_pref": emoji,
        "formality": formality,
        "tone": tone,
        "humor": (answers.get("q14") or "").strip() or None,
        "endearments": _split_list(answers.get("q3", "")),
        "catchphrases": _split_list(answers.get("q13", "")),
    }


def _top_phrases(texts: list[str], min_count: int) -> list[tuple[str, int]]:
    counts: Counter = Counter()
    for t in texts:
        toks = WORD_RE.findall(t.lower())
        seen = set()
        for n in (2, 3):
            for i in range(len(toks) - n + 1):
                gram = toks[i:i + n]
                if all(w in STOPWORDS for w in gram):
                    continue
                seen.add(" ".join(gram))
        counts.update(seen)
    return [(p, c) for p, c in counts.most_common(10) if c >= min_count]


def build_style(texts: list[str], answers: dict | None = None) -> dict:
    answers = answers or {}
    prefs = interview_prefs(answers)
    texts = [t for t in texts if t.strip()]
    n = len(texts)
    words = [len(t.split()) for t in texts] or [0]

    first_alpha = [next((c for c in t if c.isalpha()), None) for t in texts]
    first_alpha = [c for c in first_alpha if c]
    all_emoji = Counter(e for t in texts for e in EMOJI_RE.findall(t))
    laughs = Counter(m.lower() for t in texts for m in LAUGH_RE.findall(t))
    tokens = Counter(w for t in texts for w in WORD_RE.findall(t.lower()))

    style = {
        "n_messages": n,
        "avg_words": round(statistics.mean(words), 1),
        "median_words": statistics.median(words),
        "p90_words": sorted(words)[int(0.9 * (len(words) - 1))],
        "short_ratio": round(sum(w <= 4 for w in words) / max(n, 1), 2),
        "lowercase_start": round(sum(c.islower() for c in first_alpha) / max(len(first_alpha), 1), 2) if first_alpha else None,
        "end_punct": round(sum(bool(re.search(r"[.!?]$", t.strip())) for t in texts) / max(n, 1), 2),
        "question_ratio": round(sum("?" in t for t in texts) / max(n, 1), 2),
        "emoji_ratio": round(sum(bool(EMOJI_RE.search(t)) for t in texts) / max(n, 1), 2),
        "top_emojis": [e for e, _ in all_emoji.most_common(4) if e != "\ufe0f"],
        "laugh": laughs.most_common(1)[0][0] if laughs else None,
        "laugh_ratio": round(sum(bool(LAUGH_RE.search(t)) for t in texts) / max(n, 1), 2),
        "elongation": round(sum(bool(ELONG_RE.search(t)) for t in texts) / max(n, 1), 2),
        "slang": [w for w, _ in tokens.most_common(200) if w in SLANG][:8],
        "top_phrases": _top_phrases(texts, 3 if n >= 40 else 2),
        "top_openers": [w for w, c in Counter((WORD_RE.findall(t.lower()) or [""])[0] for t in texts).most_common(5) if w and c >= 2],
        **prefs,
    }

    # Endearments: how often does the person actually use them?
    ends = [e for e in prefs["endearments"] if e]
    if ends and n:
        hits = sum(any(re.search(rf"\b{re.escape(e.lower())}\b", t.lower()) for e in ends) for t in texts)
        style["endearment_rate"] = round(hits / n, 2) if n >= 15 else 0.3
    else:
        style["endearment_rate"] = 0.0

    # Effective emoji rate: trust the data once there's enough of it, else the interview.
    if n >= 15:
        style["emoji_rate_eff"] = style["emoji_ratio"]
    else:
        style["emoji_rate_eff"] = {"heavy": 0.5, "none": 0.0}.get(prefs["emoji_pref"], 0.15)
    return style


def describe_style(st: dict) -> list[str]:
    """Human-readable summary (shown in the UI and fed to the optional LLM)."""
    out = []
    if st["n_messages"]:
        kind = "short, quick messages" if st["median_words"] <= 6 else "medium-length messages" if st["median_words"] <= 16 else "long messages"
        out.append(f"Usually writes {kind} (typically {int(st['median_words'])} words; {st['n_messages']} messages analysed).")
    elif st.get("length_pref"):
        out.append(f"Writes {st['length_pref']} messages (from the interview).")
    if st["lowercase_start"] is not None and st["n_messages"] >= 5:
        out.append("Mostly starts messages in lowercase." if st["lowercase_start"] >= 0.7 else
                   "Usually capitalises the first word." if st["lowercase_start"] <= 0.25 else "Mixes lowercase and capitals.")
        out.append("Rarely ends with a full stop." if st["end_punct"] <= 0.3 else "Often ends with punctuation.")
    if st["emoji_rate_eff"] >= 0.25:
        out.append("Uses emojis a lot" + (f" ({' '.join(st['top_emojis'])})." if st["top_emojis"] else "."))
    elif st["emoji_rate_eff"] < 0.05 and st["n_messages"] >= 15:
        out.append("Almost never uses emojis.")
    if st["laugh"]:
        out.append(f"Laughs in text with “{st['laugh']}”.")
    if st["slang"]:
        out.append("Texting shorthand: " + ", ".join(st["slang"]) + ".")
    if st["elongation"] >= 0.1:
        out.append("Stretches words out (sooo, nooo).")
    if st["catchphrases"]:
        out.append("Catchphrase: " + " / ".join(f"“{c}”" for c in st["catchphrases"][:3]) + ".")
    if st["top_phrases"]:
        out.append("Repeated phrases: " + ", ".join(f"“{p}”" for p, _ in st["top_phrases"][:5]) + ".")
    if st["endearments"]:
        out.append("Calls you: " + ", ".join(st["endearments"][:3]) + ".")
    if st["tone"]:
        out.append(f"Overall tone: {st['tone']}.")
    if st["humor"]:
        out.append(f"Humor: {st['humor']}.")
    return out


def apply_style(text: str, st: dict, rng) -> str:
    """Nudge a generic sentence toward how this person types.

    Only used on template/fallback text. Real retrieved messages are already
    in their voice, so we leave those alone.
    """
    t = text.strip()
    if not t:
        return t
    lc = st.get("lowercase_start")
    if lc is not None and st["n_messages"] >= 5:
        if lc >= 0.7:
            t = t[0].lower() + t[1:]
            if lc >= 0.85:
                t = re.sub(r"\bI\b", "i", t)
        elif lc <= 0.25:
            t = t[0].upper() + t[1:]
    if st["n_messages"] >= 5 and st["end_punct"] <= 0.3:
        t = t.rstrip(".")
    if st["top_emojis"] and not EMOJI_RE.search(t) and rng.random() < st["emoji_rate_eff"]:
        t += " " + rng.choice(st["top_emojis"])
    return t
