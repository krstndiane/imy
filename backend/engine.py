"""engine.py - the "brain". Picks a reply in the person's own words.

How a reply is produced (no external API needed):

  1. EMBED     Turn the incoming message into a vector (sentence-transformers,
               or TF-IDF if that isn't installed).
  2. RETRIEVE  Compare it with every "thing you said" in the screenshots. Each
               one was followed by a real reply from the person; we find the
               closest matches and reuse that real reply.
  3. INTENTS   Greetings, "miss you", "goodnight" etc. are recognised by rules and
               answered from the person's own matching messages (or a small
               template shaped by their style) when retrieval isn't confident.
  4. FALLBACK  If nothing matches, pick one of their short all-purpose replies
               ("hahaha", "true", "oo") the way they'd actually respond.
  5. OPTIONAL  If "Use local LLM" is on and Ollama is running, a local model
               writes the reply instead, using retrieved examples + interview
               answers + the style summary as guidance.

Tweak the numbers in THRESHOLDS / TEMPLATES below to change behaviour.
"""
import hashlib
import json
import os
import random
import re
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from sklearn.preprocessing import normalize

from . import style as style_mod

# (strong, weak) cosine-similarity cut-offs. Above "strong" we trust the match.
# Between weak and strong we use it only if no intent rule applies.
THRESHOLDS = {"sentence-transformers": (0.65, 0.45), "tfidf": (0.55, 0.35)}
MAX_BUBBLES = 3

INTENTS = {
    "greeting": r"^\s*(hi+|hello+|hey+|yo|hoy|kumusta|musta|good (morning|afternoon|evening)|magandang (umaga|hapon|gabi))\b",
    "how_are_you": r"\b(how are you|how r u|how('?s| is) (it going|everything|your day)|hbu|wbu|kumusta ka|musta ka|what'?s up|sup)\b",
    "thanks": r"\b(thanks|thank you|thx|ty|salamat)\b",
    "goodnight": r"\b(good ?night|gn|sleep well|sweet dreams|tulog na)\b",
    "miss_you": r"\b(i miss you|miss you|miss u|namiss kita|na-?miss kita|i missed you)\b",
    "love_you": r"\b(i love you|love you|luv u|love u|ily|mahal kita)\b",
    "sorry": r"\b(sorry|i'?m sorry|patawad|pasensya)\b",
}
INTENT_RES = {k: re.compile(v, re.I) for k, v in INTENTS.items()}
INTENT_MAX_WORDS = {"greeting": 4, "how_are_you": 8, "thanks": 6, "goodnight": 6, "miss_you": 10, "love_you": 8, "sorry": 10}

# {e} is replaced by one of the person's endearments (sometimes, based on how often they use them).
TEMPLATES = {
    "greeting": ["hey{e}", "hi{e}", "hello{e}"],
    "how_are_you": ["I'm good{e}, how about you?", "doing okay{e}. you?"],
    "thanks": ["of course{e}", "anytime{e}"],
    "goodnight": ["goodnight{e}", "sleep well{e}"],
    "miss_you": ["miss you too{e}", "I miss you too{e}"],
    "love_you": ["love you too{e}"],
    "sorry": ["it's okay{e}", "don't worry about it{e}"],
}
ANSWER_LIKE = re.compile(r"^(yes|yeah|yep|oo|opo|no|nope|hindi|maybe|siguro|idk|depends|sure|okay|ok)\b", re.I)


@lru_cache(maxsize=1)
def _question_labels() -> dict:
    data = json.loads((Path(__file__).parent / "questions.json").read_text())
    return {q["id"]: q["text"] for s in data["sections"] for q in s["questions"]}


def detect_intent(text: str) -> str | None:
    for name, rx in INTENT_RES.items():
        if rx.search(text):
            return name
    return None


# ------------------------------------------------------------------ embeddings
class VectorSpace:
    """Turns text into vectors and measures similarity."""
    _model = None
    _failed = False

    @classmethod
    def sentence_model(cls):
        if os.environ.get("IMY_EMBEDDER", "").lower() == "tfidf" or cls._failed:
            return None
        if cls._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                cls._model = SentenceTransformer(os.environ.get("IMY_MODEL", "all-MiniLM-L6-v2"))
            except Exception as exc:  # not installed, or model can't be downloaded offline
                print(f"[imy] sentence-transformers unavailable ({exc}); using TF-IDF instead.")
                cls._failed = True
                return None
        return cls._model

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
    source: str                 # retrieval | intent | template | filler | llm | empty
    score: float = 0.0
    matched_prompt: str | None = None
    intent: str | None = None
    note: str | None = None
    embedder: str = ""

    def debug(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "bubbles" and v not in (None, "")}


def _turns(messages: list[dict]) -> list[dict]:
    """Merge consecutive messages from one speaker (within one screenshot) into turns."""
    turns: list[dict] = []
    for m in messages:
        if turns and turns[-1]["speaker"] == m["speaker"] and turns[-1]["shot"] == m["screenshot_id"]:
            turns[-1]["texts"].append(m["text"])
        else:
            turns.append({"speaker": m["speaker"], "shot": m["screenshot_id"], "texts": [m["text"]]})
    return turns


class PersonIndex:
    def __init__(self, person: dict, messages: list[dict]):
        self.person = person
        self.answers = person["answers"]
        self.name = person["name"]

        turns = _turns(messages)
        self.prompts: list[str] = []          # what the user said
        self.replies: list[list[str]] = []    # what the person said next
        for i, t in enumerate(turns):
            if t["speaker"] == "person" and i > 0 and turns[i - 1]["speaker"] == "user" and turns[i - 1]["shot"] == t["shot"]:
                self.prompts.append(" ".join(turns[i - 1]["texts"])[:400])
                self.replies.append(t["texts"][:MAX_BUBBLES])

        self.person_texts = [m["text"] for m in messages if m["speaker"] == "person"]
        self.style = style_mod.build_style(self.person_texts, self.answers)

        self.space = VectorSpace(self.prompts + self.person_texts)
        self.prompt_vecs = self.space.encode(self.prompts) if self.prompts else None
        self.strong, self.weak = THRESHOLDS[self.space.kind]

        # Their own short messages that fit almost any conversation.
        short = [t for t in self.person_texts if len(t.split()) <= 3 and not detect_intent(t)]
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
            order = np.argsort(-sims)[:5]
            matches = [(float(sims[i]), int(i)) for i in order]
        best = matches[0][0] if matches else 0.0

        def pick_match() -> Reply:
            pool = [(s, i) for s, i in matches if s >= best - 0.08]
            # prefer replies we haven't just used; softly favour higher similarity
            w = [np.exp((s - best) / 0.03) * (0.15 if self.replies[i][0].strip().lower() in recent else 1.0) for s, i in pool]
            s, i = rng.choices(pool, weights=w)[0]
            bubbles = [self._trim(b) for b in self.replies[i]]
            return Reply(bubbles, "retrieval", round(s, 3), self.prompts[i], intent, embedder=kind)

        if best >= self.strong:
            return pick_match()

        if intent:
            pool = [t for t in self.intent_pools.get(intent, []) if t.strip().lower() not in recent]
            if pool:
                return Reply([rng.choice(pool)], "intent", best, None, intent, embedder=kind)
            tpl = rng.choice(TEMPLATES[intent]).replace("{e}", self._endearment(rng))
            return Reply([style_mod.apply_style(tpl, self.style, rng)], "template", best, None, intent,
                         note="No matching message from them yet; used a template shaped by their style.", embedder=kind)

        if best >= self.weak:
            return pick_match()

        fillers = [f for f in self.fillers if f.strip().lower() not in recent] or self.fillers
        if fillers:
            if "?" in user_text:
                answers = [f for f in fillers if ANSWER_LIKE.match(f)]
                fillers = answers or fillers
            return Reply([rng.choice(fillers)], "filler", best, None, None,
                         note="No close match in the screenshots; used one of their short all-purpose replies.", embedder=kind)

        generic = self.style["catchphrases"] or ["hmm", "tell me more"]
        return Reply([style_mod.apply_style(rng.choice(generic), self.style, rng)], "empty", 0.0, None, None,
                     note="Not enough chat data yet. Add more screenshots for better replies.", embedder=kind)

    # ---- optional local LLM (Ollama)
    def _persona_prompt(self, examples: list[tuple[str, list[str]]]) -> str:
        labels = _question_labels()
        facts = "\n".join(f"- {labels[k]} {v.strip()}" for k, v in self.answers.items() if v and v.strip() and k in labels)
        style = "\n".join("- " + s for s in style_mod.describe_style(self.style))
        shots = "\n".join(f"Them: {p}\n{self.name}: " + " / ".join(r) for p, r in examples)
        return (
            f"You are role-playing {self.name} in a text conversation with someone close to them.\n"
            f"Write ONLY the text message(s) {self.name} would send next. No narration, no quotation marks. "
            f"If they would send several short messages, put each on its own line (max {MAX_BUBBLES}).\n\n"
            f"What the user told us about {self.name}:\n{facts or '(nothing provided)'}\n\n"
            f"How {self.name} writes:\n{style or '(unknown)'}\n\n"
            f"Real examples of how {self.name} replied before:\n{shots or '(none)'}\n"
        )

    def llm_reply(self, user_text: str, history: list[dict], rng: random.Random) -> Reply:
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        model = os.environ.get("OLLAMA_MODEL", "llama3.2")
        examples = []
        if self.prompt_vecs is not None:
            sims = self.space.similarity(self.prompt_vecs, self._query(user_text, history))
            examples = [(self.prompts[i], self.replies[i]) for i in np.argsort(-sims)[:6]]
        messages = [{"role": "system", "content": self._persona_prompt(examples)}]
        for h in history[-10:]:
            messages.append({"role": "user" if h["role"] == "user" else "assistant", "content": h["text"]})
        messages.append({"role": "user", "content": user_text})
        req = urllib.request.Request(
            host.rstrip("/") + "/api/chat",
            data=json.dumps({"model": model, "messages": messages, "stream": False, "options": {"temperature": 0.8}}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            text = json.load(resp)["message"]["content"].strip()
        bubbles = [l.strip() for l in text.splitlines() if l.strip()][:MAX_BUBBLES]
        if not bubbles:
            raise RuntimeError("empty response")
        return Reply(bubbles, "llm", 0.0, None, detect_intent(user_text), note=f"Written by local model {model}.", embedder=self.space.kind)


# ------------------------------------------------------------------ cache + public API
_CACHE: dict[int, tuple[int, PersonIndex]] = {}


def get_index(person: dict, messages: list[dict]) -> PersonIndex:
    """Build the index once per version of the person's data, then reuse it."""
    cached = _CACHE.get(person["id"])
    if cached and cached[0] == person["data_version"]:
        return cached[1]
    index = PersonIndex(person, messages)
    _CACHE[person["id"]] = (person["data_version"], index)
    return index


def forget(person_id: int) -> None:
    _CACHE.pop(person_id, None)


def generate_reply(person: dict, messages: list[dict], history: list[dict], user_text: str) -> Reply:
    index = get_index(person, messages)
    seed = int(hashlib.md5(f"{user_text}|{len(history)}".encode()).hexdigest(), 16) % (2 ** 32)
    rng = random.Random(seed)
    note = None
    if person.get("use_llm"):
        try:
            return index.llm_reply(user_text, history, rng)
        except Exception as exc:
            print(f"[imy] local LLM failed: {exc}")
            note = "Couldn't get a reply from the local model (is Ollama running?). Used pattern matching instead."
    reply = index.retrieval_reply(user_text, history, rng)
    if note:
        reply.note = note
    return reply
