"""engine.py - the "brain". Decides what the person would say next.

How a reply is produced:

  1. TAUGHT    What you told imy directly - "how would they reply?" answers in the interview,
               and 👍 / Fix in the chat - is used first, word for word, when your message matches.
  2. LOCAL     smalltalk.py / personality.py write a reply for what you said, in their style
               (casing, Taglish, endearments, emojis measured from their real messages).
  3. AI        Groq (llm.py, through Pydantic AI) writes the reply AS THEM, using their style and
               real messages as examples. It can also really answer questions, in their voice.
               This is the default (IMY_AI_MODE=always). With IMY_AI_MODE=fallback, Groq is only
               asked when step 2 isn't confident - anything beyond hi / haha / goodnight.
  4. FALLBACK  No key, no internet or the free limit reached: step 2's reply. Nothing breaks.

Matching uses sentence-transformers when installed (loaded in the background, so the first message
isn't slow) and TF-IDF on letter patterns otherwise.
"""
import hashlib
import json
import os
import random
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from sklearn.preprocessing import normalize

from . import llm, notes, parser, personality, smalltalk, style as style_mod

# (strong, weak) cosine-similarity cut-offs. Above "strong" we trust the match.
# Between weak and strong we use it only if no intent rule applies.
THRESHOLDS = {"sentence-transformers": (0.65, 0.45), "tfidf": (0.55, 0.45)}
MAX_BUBBLES = 3
# Off (default): screenshots are used only to learn their style; replies are written fresh.
# IMY_REUSE_LINES=1 goes back to answering with their own old messages when one seems to match.
ECHO_STOP = {"about", "there", "their", "where", "which", "would", "could", "should", "really", "naman", "kamusta", "kumusta", "hahaha", "talaga", "bakit"}
REUSE_LINES = os.environ.get("IMY_REUSE_LINES", "0") == "1"

INTENTS = {
    "greeting": r"^\s*(hi+|hello+|hey+|yo|hoy|good (morning|afternoon|evening)|magandang (umaga|hapon|gabi))\b",
    "how_are_you": r"\b(how are you|how r u|how('?s| is) (it going|everything|your day)|hbu|wbu|(k[au])?musta|what'?s up|sup)\b",
    "thanks": r"\b(thanks|thank you|thx|ty|salamat)\b",
    "goodnight": r"\b(good ?night|gn|sleep well|sweet dreams|tulog na)\b",
    "miss_you": r"\b(i miss you|miss you|miss u|miss (na )?kita|namiss kita|na-?miss kita|namimiss kita|i missed you)\b",
    "love_you": r"\b(i love you|love you|luv u|love u|ily|mahal kita)\b",
    "sorry": r"\b(sorry|i'?m sorry|patawad|pasensya)\b",
    # everyday small talk
    "doing": r"\b(what are you doing|what r u doing|wyd|whatcha doing|ano(ng)? (gina)?gawa mo|gawa mo)\b",
    "eaten": r"\b(have you eaten|did you eat|kumain ka na|kain ka na|nag ?(lunch|dinner|breakfast) ka na)\b",
    "where": r"\b(where are you|where r u|wru|(na)?sa?an ka( na)?|asan ka( na)?)\b",
    "awake": r"\b(are you (still )?(awake|up)|still up|gising ka pa|tulog ka na ba)\b",
}
INTENT_RES = {k: re.compile(v, re.I) for k, v in INTENTS.items()}
INTENT_MAX_WORDS = {"greeting": 4, "how_are_you": 8, "thanks": 6, "goodnight": 6, "miss_you": 10, "love_you": 8, "sorry": 10,
                    "doing": 0, "eaten": 0, "where": 0, "awake": 0}   # 0: these are your questions, so never reuse their messages

# {e} is replaced by one of the person's endearments (sometimes, based on how often they use them).
TEMPLATES = {
    "greeting": ["hey{e}", "hi{e}", "hello{e}"],
    "how_are_you": ["I'm good{e}, how about you?", "doing okay{e}. you?"],
    "thanks": ["of course{e}", "anytime{e}"],
    "goodnight": ["goodnight{e}", "sleep well{e}"],
    "miss_you": ["miss you too{e}", "I miss you too{e}"],
    "love_you": ["love you too{e}"],
    "sorry": ["it's okay{e}", "don't worry about it{e}"],
    "doing": ["nothing much{e}, you?", "just resting{e}. you?"],
    "eaten": ["yeah, a while ago. you?", "not yet{e}, you?"],
    "where": ["just at home{e}", "home, why?"],
    "awake": ["yeah still up{e}, you?", "still up, why?"],
}
# The same, for people who write Tagalog/Taglish.
TEMPLATES_TL = {
    "greeting": ["uy{e}", "hi{e}", "hello{e}"],
    "how_are_you": ["okay lang naman{e}, ikaw?", "oks lang{e}, ikaw kamusta", "eto buhay pa hahaha, ikaw?"],
    "thanks": ["wala yun{e}", "no prob{e}"],
    "goodnight": ["goodnight{e}", "tulog ka na{e}", "sige tulog na{e}"],
    "miss_you": ["miss you too{e}", "miss na rin kita{e}"],
    "love_you": ["love you too{e}"],
    "sorry": ["oks lang yun{e}", "wala yun{e}"],
    "doing": ["wala naman{e}, ikaw?", "eto nakahiga lang hahaha, ikaw?", "nagpapahinga lang{e}"],
    "eaten": ["oo kanina pa, ikaw?", "di pa eh, ikaw?", "tapos na{e}, ikaw kumain ka na?"],
    "where": ["bahay lang{e}", "dito lang sa bahay, bakit?"],
    "awake": ["oo gising pa{e}, ikaw?", "gising pa, bakit?"],
}
# Only plain reactions (a laugh, a yes, an okay) are safe to reuse as a reply to anything.
REACTION_RE = re.compile(r"(?i)^(h*(ha|he)+h*a*|o+k+[sie]*|oo+|ye+s+|yup|sige+|mhm+|hmm+|true+|gets|nice+|noted|alrighty?|lol+)$")
# A random one-liner ("hahaha", "true") only makes sense after these kinds of message.
# After a question, an opinion request, bad/good news, etc. we compose a fitting reply instead.
FILLER_OK_ACTS = {"statement", "short_remark", "funny"}


@lru_cache(maxsize=1)
def _question_labels() -> dict:
    data = json.loads((Path(__file__).parent / "questions.json").read_text())
    return {q["id"]: q["text"] for s in data["sections"] for q in s["questions"]}


TAGALOG_RE = re.compile(r"\b(ako|ka|mo|ko|yung|lang|naman|hindi|di|oo|ang|mga|kasi|pero|wala|ba|po|din|rin|nga|ih|eh)\b", re.I)


def _usable_filler(text: str) -> bool:
    """Is this short message a real word, not an OCR scrap like "GB", "Fy" or "60"?"""
    if re.search(r"\d|\?", text) or len(text.split()) > 2 or re.search(r"\bba\b", text, re.I):
        return False
    if text.isupper() and not re.fullmatch(r"(?i)[\Whae]+", text):   # "ICT", "SEF"; but keep "HAHAHA"
        return False
    words = re.findall(r"[^\W\d_]+", text)
    return bool(words) and all(REACTION_RE.match(w) for w in words)


def detect_intent(text: str) -> str | None:
    found = [name for name, rx in INTENT_RES.items() if rx.search(text)]
    # "hi i miss you" is about missing them, not about saying hi
    return next((n for n in found if n != "greeting"), found[0] if found else None)


# ------------------------------------------------------------------ embeddings
class VectorSpace:
    """Turns text into vectors and measures similarity."""
    _model = None
    _failed = False

    _loading = False
    _lock = threading.Lock()

    @classmethod
    def _load(cls):
        try:
            from sentence_transformers import SentenceTransformer
            cls._model = SentenceTransformer(os.environ.get("IMY_MODEL", "all-MiniLM-L6-v2"))
            print("[imy] sentence-transformers ready.")
        except Exception as exc:  # not installed, or the model can't be downloaded offline
            print(f"[imy] sentence-transformers unavailable ({exc.__class__.__name__}); using TF-IDF.")
            cls._failed = True
        finally:
            cls._loading = False

    @classmethod
    def warm_up(cls, wait: bool = False):
        """Start loading the sentence model in the background (called when the server starts).
        Until it's ready, replies use TF-IDF, so nobody waits for a 90 MB model to load."""
        if os.environ.get("IMY_EMBEDDER", "").lower() == "tfidf" or cls._failed or cls._model is not None:
            return
        with cls._lock:
            if cls._loading:
                return
            cls._loading = True
        if wait:
            cls._load()
        else:
            threading.Thread(target=cls._load, name="imy-embedder", daemon=True).start()

    @classmethod
    def sentence_model(cls):
        if os.environ.get("IMY_EMBEDDER", "").lower() == "tfidf" or cls._failed:
            return None
        if cls._model is None:
            cls.warm_up()
        return cls._model

    @classmethod
    def kind_now(cls) -> str:
        return "sentence-transformers" if cls.sentence_model() is not None else "tfidf"

    def __init__(self, corpus: list[str]):
        self.model = self.sentence_model()
        self.kind = "sentence-transformers" if self.model else "tfidf"
        if not self.model:
            from sklearn.feature_extraction.text import TfidfVectorizer
            # Character n-grams cope with typos, slang and Taglish better than whole words.
            self.tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True).fit(corpus or ["hello"])

    def encode(self, texts: list[str]):
        if self.model:
            return np.asarray(self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False), dtype=np.float32)
        return normalize(self.tfidf.transform(texts))  # sparse matrix

    @staticmethod
    def similarity(matrix, query) -> np.ndarray:
        s = matrix @ query.T
        s = s.toarray() if hasattr(s, "toarray") else s
        return np.asarray(s).ravel()


# ------------------------------------------------------------------ index
@dataclass
class Reply:
    bubbles: list[str]
    source: str                 # retrieval | intent | template | filler | personality | llm
    score: float = 0.0
    matched_prompt: str | None = None
    intent: str | None = None
    note: str | None = None
    embedder: str = ""
    rule: str | None = None     # which smalltalk rule answered (local replies)

    def debug(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "bubbles" and v not in (None, "")}


def _turns(messages: list[dict]) -> list[dict]:
    """Merge consecutive messages from one speaker (within one screenshot) into turns."""
    turns: list[dict] = []
    for m in messages:
        if turns and turns[-1]["speaker"] == m["speaker"] and turns[-1]["shot"] == m["screenshot_id"]:
            turns[-1]["texts"].append(m["text"])
        else:
            turns.append({"speaker": m["speaker"], "shot": m["screenshot_id"], "texts": [m["text"]],
                          "kind": m.get("shot_platform", "")})
    return turns


class PersonIndex:
    def __init__(self, person: dict, messages: list[dict]):
        self.person = person
        self.answers = person["answers"]
        self.name = person["name"]
        self.messages = messages
        self.platform = person.get("platform", "unknown")

        turns = _turns(messages)
        self.prompts: list[str] = []          # what the user said
        self.replies: list[list[str]] = []    # what the person said next
        self.practice: list[int] = []         # which of those came from "How would they reply?" in the interview
        for i, t in enumerate(turns):
            if t["speaker"] == "person" and i > 0 and turns[i - 1]["speaker"] == "user" and turns[i - 1]["shot"] == t["shot"]:
                if t["kind"] in ("practice", "correction"):   # told by you: interview answers and fixes from the chat
                    self.practice.append(len(self.prompts))
                self.prompts.append(" ".join(turns[i - 1]["texts"])[:400])
                self.replies.append(t["texts"][:MAX_BUBBLES])

        self.person_texts = [m["text"] for m in messages if m["speaker"] == "person"]
        self.style = style_mod.build_style(self.person_texts, self.answers)
        # Do they mostly write Tagalog/Taglish? Then composed replies should too.
        tl = sum(1 for t in self.person_texts if TAGALOG_RE.search(t))
        if len(self.person_texts) >= 15 or self.style.get("tagalog_pref") is None:
            self.style["tagalog"] = len(self.person_texts) >= 5 and tl / len(self.person_texts) >= 0.2
        else:
            self.style["tagalog"] = self.style["tagalog_pref"]

        self.space = VectorSpace(self.prompts + self.person_texts)
        self.prompt_vecs = self.space.encode(self.prompts) if self.prompts else None
        self.strong, self.weak = THRESHOLDS[self.space.kind]

        # Their own short messages that fit almost any conversation.
        short = [t for t in self.person_texts if len(t.split()) <= 3 and not detect_intent(t) and _usable_filler(t)]
        self.fillers = short
        # Their own messages grouped by intent, e.g. everything they said that counts as "goodnight".
        # (Length-capped so a long message that merely starts with "hey" isn't used as a greeting.)
        self.intent_pools = {
            k: [t for t in self.person_texts if rx.search(t) and len(t.split()) <= INTENT_MAX_WORDS[k]]
            for k, rx in INTENT_RES.items()
        }

    # ---- helpers
    def _query(self, text: str, history: list[dict]):
        q = self.space.encode([text])
        if history:  # blend in a little of the recent conversation for context
            ctx = self.space.encode([" ".join(h["text"] for h in history[-2:])])
            q = normalize(q + 0.25 * ctx)
        return q

    def _endearment(self, rng) -> str:
        ends = self.style["endearments"]
        if ends and rng.random() < self.style["endearment_rate"]:
            return " " + rng.choice(ends)
        return ""

    def _trim(self, text: str) -> str:
        """If the reply is far longer than they normally write, keep the first sentence."""
        st = self.style
        limit = max(12, int(st["p90_words"] * 1.5)) if st["n_messages"] >= 15 else (18 if st["length_pref"] == "short" else 60)
        if len(text.split()) > limit * 2:
            first = re.split(r"(?<=[.!?])\s+", text.strip())[0]
            return first if len(first.split()) >= 3 else text
        return text

    # ---- main entry
    def retrieval_reply(self, user_text: str, history: list[dict], rng: random.Random) -> Reply:
        kind = self.space.kind
        intent = detect_intent(user_text)
        recent = {h["text"].strip().lower() for h in history[-16:] if h["role"] == "person"}

        matches = []
        if self.prompt_vecs is not None:
            sims = self.space.similarity(self.prompt_vecs, self._query(user_text, history))
            order = np.argsort(-sims)[:8]
            said = set(re.findall(r"\w{4,}", user_text.lower()))

            def sensible(score: float, i: int) -> bool:
                # The reply must be real words, and (for letter-pattern matching, which is easily fooled by
                # short texts) what you said then must share an actual word with what you say now.
                if not re.search(r"[^\W\d_]{3,}", self.replies[i][0]):
                    return False
                return kind != "tfidf" or score >= 0.8 or bool(said & set(re.findall(r"\w{4,}", self.prompts[i].lower())))

            matches = [(float(sims[i]), int(i)) for i in order if sensible(float(sims[i]), int(i))][:5]
        best = matches[0][0] if matches else 0.0

        def pick_match() -> Reply | None:
            # Never repeat a reply they just gave: a real person wouldn't say the same thing twice.
            pool = [(s, i) for s, i in matches if s >= best - 0.08 and self.replies[i][0].strip().lower() not in recent]
            if not pool:
                return None
            w = [np.exp((s - best) / 0.03) for s, i in pool]  # softly favour higher similarity
            s, i = rng.choices(pool, weights=w)[0]
            bubbles = [self._trim(b) for b in self.replies[i]]
            return Reply(bubbles, "retrieval", round(s, 3), self.prompts[i], intent, embedder=kind)

        # A loose match is no answer to "what do you mean?" or "really?" - those refer to the last message.
        act = personality.classify_act(user_text)
        # Echoing a word they just said ("depende eh" -> "panong depende") is asking about that message.
        last = next((h["text"] for h in reversed(history) if h["role"] == "person"), "")
        echoed = (set(re.findall(r"\w{5,}", last.lower())) & set(re.findall(r"\w{5,}", user_text.lower()))) - ECHO_STOP
        if echoed and len(user_text.split()) <= 5 and act not in ("doubt", "funny") and not intent and not smalltalk.covers(user_text):
            act = "clarify"
        about_last = act in ("clarify", "doubt")

        practiced = None if about_last else self._practice_reply(user_text, history, intent, recent, rng)
        if practiced:
            return practiced

        if not REUSE_LINES:
            # Default: the screenshots only teach HOW they write. The reply itself is written for
            # what you just said (smalltalk.py, then personality.py), then put in their style.
            found = None if about_last else smalltalk.reply(user_text, history, self.style, self.answers, self.name, rng, recent)
            if found:
                text, why = found
                text = style_mod.apply_style(text.replace("{e}", self._endearment(rng)), self.style, rng)
                return Reply([text], "smalltalk", 0.0, None, intent, note=f"Written for what you said ({why}), in their style.",
                             embedder=kind, rule=smalltalk.rule_of(user_text) or "ack")
            text, why = personality.compose(user_text, self.style, self.answers, rng, recent, act)
            return Reply([text], "personality", 0.0, None, None, note=f"Written from their personality and writing style: {why}.", embedder=kind)

        if best >= (0.9 if about_last else self.strong) and (match := pick_match()):
            return match

        if intent:
            pool = [t for t in self.intent_pools.get(intent, []) if t.strip().lower() not in recent]
            if pool:
                return Reply([rng.choice(pool)], "intent", best, None, intent, embedder=kind)
            bank = TEMPLATES_TL if self.style.get("tagalog") else TEMPLATES
            tpl = rng.choice(bank[intent]).replace("{e}", self._endearment(rng))
            return Reply([style_mod.apply_style(tpl, self.style, rng)], "template", best, None, intent,
                         note="No matching message from them yet; used a template shaped by their style.", embedder=kind)

        if best >= self.weak and not about_last and (match := pick_match()):
            return match

        fillers = [f for f in self.fillers if f.strip().lower() not in recent]
        if fillers and act in FILLER_OK_ACTS:
            return Reply([rng.choice(fillers)], "filler", best, None, None,
                         note="No close match in the screenshots; used one of their short all-purpose replies.", embedder=kind)

        # Nothing real fits: write a sensible reply from their personality and style.
        text, why = personality.compose(user_text, self.style, self.answers, rng, recent, act)
        return Reply([text], "personality", best, None, None,
                     note=f"No close match in the screenshots, so this was composed from their personality and writing style: {why}.",
                     embedder=kind)

    def _practice_reply(self, user_text: str, history: list[dict], intent, recent: set, rng) -> Reply | None:
        """You told imy how they'd answer some messages ("If you texted 'pagod ako'..."). Those are the
        most direct data there is, so when what you say now clearly matches one, use your answer."""
        if not self.practice or self.prompt_vecs is None:
            return None
        sims = self.space.similarity(self.prompt_vecs, self.space.encode([user_text]))
        best_s, best_i = max((float(sims[i]), i) for i in self.practice)
        same_intent = [i for i in self.practice if intent and detect_intent(self.prompts[i]) == intent]
        threshold = 0.75 if self.space.kind == "sentence-transformers" else 0.6
        pick = best_i if best_s >= threshold else (rng.choice(same_intent) if same_intent else None)
        if pick is None or self.replies[pick][0].strip().lower() in recent:
            return None
        return Reply(list(self.replies[pick]), "practice", round(best_s, 3), self.prompts[pick], intent,
                     note="This is how you said they'd reply to a message like this (from the interview or a fix in the chat).",
                     embedder=self.space.kind)

    # ---- the free AI brain (llm.py)
    def _examples(self, user_text: str, history: list[dict], k: int = 6) -> list[tuple[str, list[str]]]:
        if self.prompt_vecs is None:
            return []
        sims = self.space.similarity(self.prompt_vecs, self._query(user_text, history))
        out, seen = [], set()
        for i in np.argsort(-sims)[:k * 4]:
            key = (self.prompts[i].lower(), " ".join(self.replies[i]).lower())
            if sims[i] > 0.05 and key not in seen:
                seen.add(key)
                out.append((self.prompts[i], self.replies[i]))
            if len(out) >= k:
                break
        return out

    def _samples(self, n: int = 28) -> list[str]:
        """A spread of their real messages (not OCR scraps), to show the AI how they sound."""
        seen, good = set(), []
        for t in self.person_texts:
            key = re.sub(r"\W+", "", t.lower())
            if key in seen or not re.search(r"[^\W\d_]{2,}", t) or len(t) > 220:
                continue
            seen.add(key)
            good.append(t)
        if len(good) <= n:
            return good
        step = len(good) / n
        return [good[int(i * step)] for i in range(n)]

    def ai_prompt(self, user_text: str, history: list[dict], shared: list[str] | None = None) -> str:
        labels = _question_labels()
        name = self.name
        nick = (self.answers.get("q2") or "").strip()
        # Nicknames set in the chat (Messenger-style) and the notes imy keeps from your conversations.
        nick_them = (self.person.get("nick_them") or "").strip()
        nick_me = (self.person.get("nick_me") or "").strip()
        remembered = notes.as_text(self.person.get("memory") or "")
        naming = "".join([
            f"\n- They saved your name in this chat as “{nick_them}”. Signing off or calling yourself that is normal." if nick_them else "",
            f"\n- You call them “{nick_me}”. Use it the way you'd use any nickname: sometimes, not every message." if nick_me else "",
        ])
        seen = "\n".join(f"- {s}" for s in (shared or []))
        facts = "\n".join(f"- {labels[k]} {v.strip()}" for k, v in self.answers.items()
                          if v and v.strip() and k in labels and k not in ("q1",))[:1800]
        style = "\n".join("- " + s for s in style_mod.describe_style(self.style))
        if self.style.get("tagalog"):
            style += "\n- Writes in Taglish (Tagalog mixed with English). Reply the same way."
        never = self.style.get("never_say") or []
        taught = [(self.prompts[i], self.replies[i]) for i in self.practice][:10]
        examples = [e for e in self._examples(user_text, history) if e not in taught]
        ex = "\n".join(f"They: {p[:200]}\n{name}: " + " / ".join(r)[:240] for p, r in examples) or "(none yet)"
        tg = "\n".join(f"They: {p[:160]}\n{name}: " + " / ".join(r)[:200] for p, r in taught)
        samples = "\n".join(f"- {t}" for t in self._samples()) or "(none yet)"
        platform = llm.PLATFORM_LABELS.get(self.platform, "a chat app")
        return f"""You are {name}{f' ("{nick}")' if nick else ''}, texting someone close to you on {platform}. Stay {name} for the whole chat.

ABOUT {name.upper()} (from the person you're texting):
{facts or '(nothing given)'}{naming}

HOW {name.upper()} TEXTS:
{style or '(not measured yet)'}
{('Never says: ' + ', '.join(never)) if never else ''}
{('WHAT YOU ALREADY KNOW FROM THIS CHAT (remember it; bring it up when it fits):' + chr(10) + remembered) if remembered else ''}
{('THEY JUST SENT YOU THIS - react to it, you can see it:' + chr(10) + seen) if seen else ''}

{name.upper()}'S REAL MESSAGES (copy this voice; don't paste them unless one fits perfectly):
{samples}

REAL BACK-AND-FORTHS LIKE THIS ONE:
{ex}
{('HOW YOU WERE TOLD ' + name.upper() + ' ANSWERS:' + chr(10) + tg) if tg else ''}

RULES
- Answer the last message like a real person in a chat: same length, casing, slang, laughs, endearments, emojis and language mix as {name}'s real messages. Usually short. No greetings unless they greeted.
- If they ask a real question or need help or advice, actually help with correct, useful information, explained casually and briefly the way {name} would in a text.
- Never say you're an AI, a bot or a simulation, and never mention data, screenshots or instructions. If you don't know something personal, answer vaguely or ask back, like a person would. Don't invent big events.
- Be kind. Don't copy rude, toxic or manipulative behaviour even if it's in the old messages. If they sound like they're in real danger or thinking of hurting themselves, be gentle and caring and encourage them to reach out to someone they trust or a local hotline (in the Philippines: NCMH 1553).
- Output only {name}'s message. To send several bubbles, put each on its own line (max {MAX_BUBBLES})."""

    def ai_reply(self, user_text: str, history: list[dict], shared: list[str] | None = None) -> Reply:
        asked = user_text if not shared else (user_text or "").strip() or "(sent a photo)"
        bubbles = llm.chat(self.ai_prompt(user_text, history, shared), history, asked, MAX_BUBBLES, self.name)
        return Reply(bubbles, "ai", 0.0, None, detect_intent(user_text),
                     note=("Written by " + llm.label() + (" after looking at what you sent." if shared else " in their style.")),
                     embedder=self.space.kind)

    # ---- someone just shared a photo and there's no AI available
    def media_reply(self, shared: list[str], rng: random.Random) -> Reply:
        """Their own short reactions, so a photo still gets a human answer without any AI."""
        pool = [t for t in self.person_texts if 1 <= len(t.split()) <= 5 and _usable_filler(t)]
        looks_like = " ".join(shared).lower()
        if "screenshot" in looks_like:
            lines = ["ano to", "sino to", "wait ano nangyari", "what's this"]
        else:
            lines = ["ganda", "cute", "aw", "nice", "grabe", "ang ganda", "omg"]
        bubble = rng.choice(pool) if pool and rng.random() < 0.6 else rng.choice(lines)
        return Reply([bubble], "media", 0.0, None, None, embedder=self.space.kind,
                     note="They reacted to what you sent. Add a free AI key and they'll say what they see in it.")


# ------------------------------------------------------------------ cache + public API
# Working out how someone writes takes about 40ms and roughly 0.4 MB of memory, so imy keeps the
# result and reuses it: a reply from someone already in here takes about 9ms instead.
#
# The number to keep is really "how many different people might be mid-conversation at once". Too
# few and busy times push each other out, and everyone pays the 40ms again; too many and the memory
# adds up. 200 is about 75 MB, which is comfortable on a small server and covers far more people
# chatting at once than one copy of imy can serve anyway.
_CACHE: "OrderedDict[int, tuple[int, str, PersonIndex]]" = OrderedDict()
CACHE_SIZE = int(os.environ.get("IMY_CACHE_PEOPLE", "200"))
_cache_lock = threading.Lock()


def get_index(person: dict, messages) -> PersonIndex:
    """Build the index once per version of the person's data, then reuse it.

    `messages` can be a list, or a function returning one. Pass a function and their training
    messages are only read from the database when the index genuinely has to be rebuilt. Reading
    thousands of messages on every reply, only to throw them away because the index was already
    cached, is pure waste once a lot of people are chatting."""
    kind = VectorSpace.kind_now()
    with _cache_lock:
        cached = _CACHE.get(person["id"])
        if cached and cached[0] == person["data_version"] and cached[1] == kind:
            _CACHE.move_to_end(person["id"])
            return cached[2]
    index = PersonIndex(person, messages() if callable(messages) else messages)
    with _cache_lock:
        _CACHE[person["id"]] = (person["data_version"], index.space.kind, index)
        _CACHE.move_to_end(person["id"])
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)
    return index


def forget(person_id: int) -> None:
    with _cache_lock:
        _CACHE.pop(person_id, None)


# Local replies that are good enough on their own: short, simple moments. Anything else goes to the AI
# (when one is set up), because it needs real understanding of what was said.
SIMPLE_RULES = {"greeting", "laugh", "thanks", "goodnight", "goodmorning", "bye", "yes", "no", "fine", "miss_you",
                "love_you", "time", "day", "who"}


def confident(reply: Reply, user_text: str) -> bool:
    if reply.source == "practice":
        return True
    if llm.mode() == "always":
        return False
    if reply.source in ("retrieval", "intent") and REUSE_LINES:
        return reply.score >= THRESHOLDS.get(reply.embedder, (0.6, 0.45))[0]
    return reply.source == "smalltalk" and reply.rule in SIMPLE_RULES and len(user_text.split()) <= 6


def _says_never(reply: Reply, never: list[str]) -> bool:
    return any(p in b.lower() for p in never for b in reply.bubbles)


def local_reply(index: PersonIndex, user_text: str, history: list[dict], seed: int) -> Reply:
    rng = random.Random(seed)
    never = index.style.get("never_say") or []
    for attempt in range(4):   # try a few times to avoid anything they'd "never say"
        reply = index.retrieval_reply(user_text, history, rng)
        if not _says_never(reply, never):
            break
        rng = random.Random(seed + attempt + 1)
    reply.bubbles = [b for b in (parser.clean_text(b) for b in reply.bubbles) if b] or reply.bubbles
    return reply


def generate_reply(person: dict, messages: list[dict], history: list[dict], user_text: str,
                   shared: list[str] | None = None) -> Reply:
    """`shared`: what the person just sent as a photo or file, already put into words (see media.py).
    When there is one, the reply has to be about it, so it goes straight to the AI."""
    index = get_index(person, messages)
    seed = int(hashlib.md5(f"{user_text}|{len(history)}".encode()).hexdigest(), 16) % (2 ** 32)
    note = None
    if shared:
        if llm.enabled():
            try:
                return index.ai_reply(user_text, history, shared)
            except Exception as exc:
                print(f"[imy] reply to a shared photo failed: {exc}")
        return index.media_reply(shared, random.Random(seed))
    reply = local_reply(index, user_text, history, seed)
    if note is None and llm.enabled() and not confident(reply, user_text):
        try:
            ai = index.ai_reply(user_text, history)
            if not _says_never(ai, index.style.get("never_say") or []):
                return ai
        except Exception as exc:   # free limit, no internet, odd answer: imy's own reply is still fine
            print(f"[imy] AI reply failed: {exc}")
            note = f"The AI couldn't answer ({llm.explain(exc)}), so imy wrote this from their style."
    if note:
        reply.note = note
    return reply
