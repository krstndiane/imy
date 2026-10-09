"""notes.py - what they remember from your chats.

A chat only shows the AI the last few messages, so without this they'd forget that you have an exam
on Friday or that your dog is called Milo. After every few messages imy quietly re-reads the
conversation and keeps a short list of things worth remembering:

    - her exam in networking is on Friday
    - she calls her dog Milo
    - we agreed to watch the new episode together on Sunday

That list is saved with the person (so it survives closing the browser) and is given to the AI with
every reply, which is why they can bring things up later.

It runs in the background after a reply has already been sent, so it never makes a reply slower, and
if the AI is busy or switched off nothing happens - the chat just works without the extra memory.
"""
import json
import threading

from . import llm
from .db import db

EVERY = 8             # re-read the chat after this many new messages
MAX_NOTES = 16        # keep the list short, or the AI drowns in it
MAX_CHARS = 1400

_busy: set[int] = set()
_lock = threading.Lock()

PROMPT = """You keep short notes for {name} about their ongoing text conversation with the person they're chatting to.

Here are the notes so far:
{old}

Here is the recent conversation ("Them" is the other person, "{name}" is you):
{convo}

Write the updated notes: the few facts, plans and feelings that would be embarrassing to forget next
week - names, dates, places, what they're going through, what you two agreed to do.
Rules:
- One short note per line, starting with "- ". At most {max_notes} lines.
- Keep older notes that still matter, drop anything finished, out of date or trivial.
- Facts only, no advice and no commentary. If there is nothing worth keeping, reply with NOTHING.
"""


def load(raw: str) -> list[str]:
    """The saved notes as a list of lines (the column holds JSON; older rows may hold plain text)."""
    try:
        data = json.loads(raw) if raw else {}
        return [str(n) for n in data.get("notes", [])][:MAX_NOTES]
    except (TypeError, ValueError):
        return [l.strip("- ").strip() for l in (raw or "").splitlines() if l.strip()][:MAX_NOTES]


def _upto(raw: str) -> int:
    try:
        return int(json.loads(raw or "{}").get("upto", 0))
    except (TypeError, ValueError, AttributeError):
        return 0


def as_text(raw: str) -> str:
    return "\n".join(f"- {n}" for n in load(raw))


def update(pid: int, name: str) -> None:
    """Re-read this chat and rewrite the notes. Safe to call often; does nothing when not needed."""
    if not llm.enabled():
        return
    with _lock:
        if pid in _busy:
            return
        _busy.add(pid)
    try:
        with db() as conn:
            row = conn.execute("SELECT name, memory FROM persons WHERE id = ?", (pid,)).fetchone()
            if not row:
                return
            rows = conn.execute(
                "SELECT id, role, text FROM chat_messages WHERE person_id = ? AND kind != 'event' AND deleted = 0 "
                "ORDER BY id DESC LIMIT 60", (pid,)).fetchall()
        rows = rows[::-1]
        if not rows or rows[-1]["id"] - _upto(row["memory"]) < EVERY:
            return
        convo = "\n".join(f"{'Them' if r['role'] == 'user' else name}: {r['text'][:300]}" for r in rows)
        old = as_text(row["memory"]) or "(nothing yet)"
        text = llm.ask("You write brief, factual notes. You never add commentary.",
                       PROMPT.format(name=name, old=old, convo=convo[:6000], max_notes=MAX_NOTES))
        notes = [l.strip().lstrip("-•").strip() for l in text.splitlines() if l.strip().startswith(("-", "•"))]
        notes = [n[:160] for n in notes if n and n.upper() != "NOTHING"][:MAX_NOTES]
        total = 0
        kept = []
        for n in notes:
            total += len(n)
            if total > MAX_CHARS:
                break
            kept.append(n)
        with db() as conn:
            conn.execute("UPDATE persons SET memory = ? WHERE id = ?",
                         (json.dumps({"notes": kept, "upto": rows[-1]["id"]}), pid))
    except Exception as exc:                 # a background job must never take the site down
        print(f"[imy] couldn't update chat notes: {exc}")
    finally:
        with _lock:
            _busy.discard(pid)


def update_later(pid: int, name: str) -> None:
    """Start the update in the background, after the reply has gone out."""
    if llm.enabled():
        threading.Thread(target=update, args=(pid, name), daemon=True).start()
