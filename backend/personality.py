"""personality.py - compose an in-character reply when no real message fits.

Used by engine.py when retrieval finds no close match. Instead of a generic
"hmm", we reason in three steps:

  1. WHAT is the user doing?   (asking an opinion, sharing bad/good news, joking...)
     -> classify_act()
  2. WHO is this person?       (logical or emotional, playful, blunt, calm...)
     -> read_traits()  uses interview Q6-Q10, plus Q14/Q15 and the measured style
  3. SAY it their way          pick a fitting line, then add their endearment,
     laugh, emoji, casing, brevity.

Replies are deliberately non-committal ("sounds interesting", "depends on the
details"). They never promise anything or give firm advice, because we know
nothing about the topic. They only need to be sensible and in character.

To extend: add lines to BANKS (act -> stance -> lines) or new patterns to
TRAIT_PATTERNS / classify_act().
"""
import re

from . import style as style_mod

# --------------------------------------------------------------- 1. what is the user doing?
WH = r"(what|why|how|when|where|who|which)\b"
YES_NO = r"(do|does|did|are|is|was|were|have|has|had|can|could|will|would|should|am|shall)\b"


def classify_act(text: str) -> str:
    t = text.lower().strip()
    words = t.split()
    if re.search(r"what do you think|what('?s| is) your (take|opinion)|your thoughts|thoughts\?|do you think|how about (this|that)|what about (this|that)", t):
        return "opinion"
    if re.search(r"^(can|could|will|would) you\b|\bplease\b|\bneed you to\b|\bhelp me\b", t):
        return "request"
    if "?" in t or re.match(rf"^({WH}|{YES_NO})", t):
        return "yes_no" if re.match(rf"^{YES_NO}", t) else "wh"
    if re.search(r"\b(ha){2,}|\blol+\b|\blmao\b|😂|🤣|\bfunny\b", t):
        return "funny"
    if re.search(r"\b(sad|tired|exhausted|stressed|anxious|sick|hurt|lonely|upset|crying|bad day|rough|worried|scared|overwhelmed|can'?t sleep|failed)\b", t):
        return "down"
    if re.search(r"\b(got the|passed|won|promoted|finally|excited|so happy|good news|great news|guess what|accepted|engaged|graduat|made it)\b", t):
        return "good_news"
    return "short_remark" if len(words) <= 3 else "statement"


# --------------------------------------------------------------- 2. who is this person?
TRAIT_PATTERNS = {
    "logical":   r"logic|rational|reason|practical|analy|calculat|think(s|ing)? things through|sensible|level-?headed",
    "emotional": r"emotion|feel|heart|sensitive|intuiti|sentimental|empath",
    "playful":   r"playful|funny|silly|jok|cheerful|goofy|bubbly|fun\b|teas|witty|sarcas",
    "blunt":     r"blunt|direct|straight|frank|no[- ]nonsense|serious|honest|to the point|strict",
    "calm":      r"calm|chill|laid[- ]?back|relaxed|easygoing|patient|steady|gentle|quiet",
    "warm":      r"caring|kind|sweet|warm|loving|nurtur|supportive|protective|thoughtful",
}
STOP = set("that this they them with about their what have from like much very most really things thing always often when where which would could should being".split())
EMOJI_FALLBACK = {"down": "🥺", "good_news": "🎉", "funny": "😂"}


def read_traits(answers: dict, st: dict) -> dict:
    a = lambda k: (answers.get(k) or "").lower()
    q8 = a("q8")
    # Q8: logical vs emotional decision making (the main "how do they think" signal)
    log, emo = bool(re.search(TRAIT_PATTERNS["logical"], q8)), bool(re.search(TRAIT_PATTERNS["emotional"], q8))
    thinking = "balanced" if log and emo else "logical" if log else "emotional" if emo else None
    # Q6 (3 words), Q10 (how they react), Q14 (humor), Q15 (how they respond) + the measured tone
    vibe_text = " ".join([a("q6"), a("q10"), a("q14"), a("q15"), (st.get("tone") or ""), (st.get("humor") or "").lower()])
    vibes = [v for v in ("playful", "blunt", "calm", "warm") if re.search(TRAIT_PATTERNS[v], vibe_text)]
    if "dramatic" in a("q10") or "emotional" in a("q10"):
        thinking = thinking or "emotional"

    # How long are their messages?
    med = st.get("median_words", 0)
    if st["n_messages"] >= 15:
        brevity = "short" if med <= 6 else "long" if med > 16 else "medium"
    else:
        brevity = st.get("length_pref") or ("short" if "blunt" in vibes else "medium")
    return {
        "thinking": thinking, "vibes": vibes, "brevity": brevity,
        "formal": st.get("formality") == "formal",
        "casual": st.get("formality") == "casual",
        "care_words": _keywords(a("q7")),     # what they care about most
        "worry_words": _keywords(a("q9")),    # what's usually on their mind
    }


def _keywords(s: str) -> set:
    return {w for w in re.findall(r"[a-z']{4,}", s) if w not in STOP}


# --------------------------------------------------------------- 3. the lines
# act -> stance -> options. Stances: neutral (always allowed), logical, emotional,
# playful, blunt, calm. Keep lines non-committal and lowercase-friendly.
BANKS = {
    "opinion": {
        "neutral":   ["sounds interesting", "i can see that", "that makes sense", "hmm, could go either way"],
        "logical":   ["depends on the details honestly", "what are the pros and cons though?", "it holds up if you look at it that way"],
        "emotional": ["something about it feels right", "i feel good about it", "i think it matters how you feel about it"],
        "playful":   ["ooh i'm into it", "okay that's kinda fun", "i mean... why not"],
        "blunt":     ["it's fine", "could work", "not sold yet"],
        "calm":      ["sounds okay to me", "no rush, think it over"],
    },
    "yes_no": {
        "neutral":   ["maybe", "probably", "i think so", "hmm, not sure"],
        "logical":   ["depends on the situation", "probably, if it all checks out", "not necessarily"],
        "emotional": ["i feel like yes", "honestly i feel a little unsure"],
        "playful":   ["ooh maybe", "mmm possibly", "you tell me"],
        "blunt":     ["yes", "not really", "probably"],
        "calm":      ["i think so", "should be fine"],
    },
    "wh": {
        "neutral":   ["good question", "hmm, let me think", "depends, why?"],
        "logical":   ["it depends on a few things", "what's the context?"],
        "emotional": ["what made you think of that?", "why do you ask?"],
        "playful":   ["ooh good question", "okay why do you ask", "you're making me think"],
        "blunt":     ["not sure", "depends", "no idea"],
        "calm":      ["let's figure it out", "give me a sec"],
    },
    "request": {
        "neutral":   ["okay, what do you need?", "yeah, what's up?"],
        "logical":   ["what exactly do you need?", "okay, walk me through it"],
        "emotional": ["i'm here, what do you need?", "of course, what's going on?"],
        "playful":   ["ooh what is it", "depends, what are we talking about"],
        "blunt":     ["what is it?", "what do you need?"],
        "calm":      ["okay, we'll figure it out"],
    },
    "down": {
        "neutral":   ["that sounds rough", "i'm here", "that's a lot"],
        "logical":   ["that sounds like a lot, what's weighing on you most?", "one thing at a time, okay?"],
        "emotional": ["aw, that sounds really hard", "i'm sorry you're feeling that way", "sending you a hug"],
        "playful":   ["aww no, tell me what happened", "okay sit down and tell me everything"],
        "blunt":     ["that sucks", "ugh, what happened?"],
        "calm":      ["breathe, it's okay", "you'll get through it"],
    },
    "good_news": {
        "neutral":   ["that's great", "nice, tell me more", "glad to hear that"],
        "logical":   ["that's solid news", "good, that's what you worked for"],
        "emotional": ["omg i'm so happy for you", "that makes me so happy"],
        "playful":   ["wait what, tell me everything", "let's gooo"],
        "blunt":     ["nice", "good for you"],
        "calm":      ["that's good to hear", "i'm glad"],
    },
    "funny": {
        "neutral":   ["haha", "that's funny", "lol true"],
        "logical":   ["okay that's actually funny"],
        "emotional": ["haha you're so cute"],
        "playful":   ["hahaha stop", "you're so silly", "i can't"],
        "blunt":     ["lol", "okay that's funny"],
        "calm":      ["haha okay"],
    },
    "short_remark": {
        "neutral":   ["i see", "okay", "true", "mm"],
        "logical":   ["makes sense", "fair point"],
        "emotional": ["aww", "i feel that"],
        "playful":   ["oh really?", "haha okay"],
        "blunt":     ["ok", "fair"],
        "calm":      ["mhm", "okay"],
    },
    "statement": {
        "neutral":   ["i see", "tell me more", "that makes sense", "hmm, interesting"],
        "logical":   ["that makes sense, so what's next?", "what are you going to do about it?"],
        "emotional": ["i feel you", "how do you feel about it?", "that sounds like it meant a lot"],
        "playful":   ["wait really?", "oh? go on", "okay and then what"],
        "blunt":     ["and then?", "fair enough"],
        "calm":      ["mhm, go on", "okay, i'm listening"],
    },
}
CARE_LINES = ["that really matters to me", "you know i care about that", "i'm glad you brought that up"]
WORRY_LINES = ["that's been on my mind too", "i think about that a lot"]
FOLLOW_UPS = {"logical": "what's your plan?", "emotional": "how are you feeling about it?"}
STANDALONE_ACTS = {"statement", "short_remark", "funny"}   # acts where a catchphrase on its own fits


def compose(user_text: str, st: dict, answers: dict, rng, recent=()) -> tuple[str, str]:
    """Returns (reply_text, short explanation for the 'why this reply?' popover)."""
    act = classify_act(user_text)
    tr = read_traits(answers, st)

    # Stances to draw from: how they think, then their strongest vibes. Earlier = heavier.
    stances = []
    if tr["thinking"] in ("logical", "emotional"):
        stances.append(tr["thinking"])
    for v in tr["vibes"][:2]:
        stances.append("emotional" if v == "warm" else v)
    stances = list(dict.fromkeys(stances))
    stances = (stances + ["neutral"])[:3]

    # 1) A topic they care about / worry about gets an in-character reaction.
    words = set(re.findall(r"[a-z']{4,}", user_text.lower()))
    if tr["care_words"] & words:
        pool, why = CARE_LINES, "your message touches something they care about (interview Q7)"
    elif tr["worry_words"] & words:
        pool, why = WORRY_LINES, "your message touches something they worry about (interview Q9)"
    else:
        bank = BANKS[act]
        pool = [line for s in stances for line in bank.get(s, [])]
        pool += [line for s in stances[:1] for line in bank.get(s, [])]   # primary stance counts double
        why = f"they tend to be {', '.join(s for s in stances if s != 'neutral') or 'easygoing'}; your message reads as {act.replace('_', ' ')}"

    # 2) Match their message length.
    if tr["brevity"] == "short":
        pool = [p for p in pool if len(p.split()) <= 4] or pool
    fresh = [p for p in pool if p.lower() not in recent] or pool
    text = rng.choice(fresh)

    # 3) Their catchphrase, only where it can stand alone without clashing.
    cps = [c for c in st.get("catchphrases", []) if 0 < len(c.split()) <= 6]
    if cps and act in STANDALONE_ACTS and rng.random() < 0.25:
        text, why = rng.choice(cps), why + "; used their catchphrase"
    elif tr["brevity"] == "long" and not text.endswith("?"):
        text += ". " + FOLLOW_UPS.get(tr["thinking"], "tell me more about it")

    # 4) Warmth: laugh, endearment.
    if "playful" in stances and st.get("laugh") and act in ("funny", "statement", "short_remark") and rng.random() < 0.4:
        text += " " + st["laugh"]
    ends = st.get("endearments") or []
    if ends:
        p = st.get("endearment_rate", 0.3) + (0.25 if act in ("down", "good_news") else 0)
        if rng.random() < min(p, 0.6):
            e = rng.choice(ends)
            # keep it natural: "what happened babe?" rather than "what happened? babe"
            text = f"{text[:-1]} {e}{text[-1]}" if text.endswith("?") else f"{text} {e}"

    # 5) Their typing style: casing, punctuation, emoji (measured, else from the interview).
    style_for_text = {**st, "top_emojis": st["top_emojis"] or ([EMOJI_FALLBACK.get(act, "😊")] if st["emoji_rate_eff"] >= 0.4 else [])}
    text = style_mod.apply_style(text, style_for_text, rng)
    if st["n_messages"] < 5:                      # no data to measure: use the interview
        if tr["formal"]:
            text = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)  # capitalise each sentence
            text += "" if text[-1] in ".!?" else "."
        elif tr["casual"]:
            text = text.lower()
    return text, why
