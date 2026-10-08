"""main.py - the web API (FastAPI).

Run from the project root:   uvicorn backend.main:app --reload
Interactive docs:            http://localhost:8000/docs
"""
import json
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from . import claude, engine, importers, ocr, parser, style as style_mod
from .db import DATA_DIR, UPLOAD_DIR, bump_version, db, init_db, now

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = ROOT / "frontend" / "dist"
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="imy", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"], allow_headers=["*"],
)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# ---------------------------------------------------------------- helpers
def _person_row(conn, pid: int):
    row = conn.execute("SELECT * FROM persons WHERE id = ?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "Person not found")
    return row


def _person_dict(row) -> dict:
    return {**dict(row), "answers": json.loads(row["answers"]), "use_llm": bool(row["use_llm"])}


def _person_summary(conn, row) -> dict:
    p = _person_dict(row)
    pid = p["id"]
    one = lambda sql: conn.execute(sql, (pid,)).fetchone()[0]
    return {
        "id": pid, "name": p["name"], "nickname": p["answers"].get("q2", ""),
        "answers": p["answers"], "use_llm": p["use_llm"], "platform": p.get("platform") or "",
        "created_at": p["created_at"], "updated_at": p["updated_at"],
        "screenshot_count": one("SELECT COUNT(*) FROM screenshots WHERE person_id = ? AND stored_path IS NOT NULL"),
        "message_count": one("SELECT COUNT(*) FROM messages WHERE person_id = ?"),
        "person_message_count": one("SELECT COUNT(*) FROM messages WHERE person_id = ? AND speaker = 'person'"),
    }


def _name_from_answers(answers: dict) -> str:
    return (answers.get("q1") or answers.get("q2") or "Unnamed").strip() or "Unnamed"


def _known_names(answers: dict) -> list[str]:
    names = [answers.get("q1", ""), answers.get("q2", "")]
    names += [n.split()[0] for n in names if n.strip()]
    return [n for n in names if n.strip()]


def _messages_for_engine(conn, pid: int) -> list[dict]:
    rows = conn.execute(
        "SELECT m.*, s.platform AS shot_platform FROM messages m JOIN screenshots s ON s.id = m.screenshot_id "
        "WHERE m.person_id = ? ORDER BY s.id, m.position", (pid,)).fetchall()
    return [dict(r) for r in rows]


def _person_for_engine(conn, pid: int) -> dict:
    """The person plus the platform most of their screenshots came from."""
    person = _person_dict(_person_row(conn, pid))
    if person.get("platform"):  # chosen by the user in setup
        return person
    row = conn.execute(
        "SELECT platform FROM screenshots WHERE person_id = ? AND platform NOT IN ('unknown', 'text-import', 'practice') "
        "GROUP BY platform ORDER BY COUNT(*) DESC LIMIT 1", (pid,)).fetchone()
    person["platform"] = row["platform"] if row else "unknown"
    return person


def _store_messages(conn, pid: int, shot_id: int, msgs: list[dict]) -> None:
    conn.executemany(
        "INSERT INTO messages (person_id, screenshot_id, position, speaker, text) VALUES (?,?,?,?,?)",
        [(pid, shot_id, i, m["speaker"], m["text"]) for i, m in enumerate(msgs)])


def _shot_out(conn, shot) -> dict:
    msgs = conn.execute("SELECT id, position, speaker, text FROM messages WHERE screenshot_id = ? ORDER BY position",
                        (shot["id"],)).fetchall()
    return {
        "id": shot["id"], "filename": shot["filename"], "platform": shot["platform"],
        "flipped": bool(shot["flipped"]),
        "url": f"/uploads/{shot['stored_path']}" if shot["stored_path"] else None,
        "messages": [dict(m) for m in msgs],
    }


# ---------------------------------------------------------------- questions
@app.get("/api/questions")
def get_questions():
    return json.loads((Path(__file__).parent / "questions.json").read_text())


# ---------------------------------------------------------------- persons
PLATFORMS = {"", "messenger", "imessage", "instagram", "whatsapp", "telegram"}


class AnswersIn(BaseModel):
    answers: dict[str, str] = {}
    platform: str | None = None   # None = leave unchanged


def _check_platform(platform: str | None) -> None:
    if platform is not None and platform not in PLATFORMS:
        raise HTTPException(400, "Unknown platform.")


def _reply_prompts() -> dict:
    """{question id: the message shown as yours} for every 'reply' question in questions.json."""
    data = json.loads((Path(__file__).parent / "questions.json").read_text())
    return {q["id"]: q["prompt"] for s in data["sections"] for q in s["questions"] if q.get("type") == "reply"}


def _sync_practice(conn, pid: int, answers: dict) -> None:
    """Turn 'If you texted X, they'd reply ___' answers into exchanges the engine learns from."""
    conn.execute("DELETE FROM screenshots WHERE person_id = ? AND platform = 'practice'", (pid,))
    pairs = [(prompt, answers[qid].strip()) for qid, prompt in _reply_prompts().items() if (answers.get(qid) or "").strip()]
    if not pairs:
        return
    cur = conn.execute(
        "INSERT INTO screenshots (person_id, filename, stored_path, platform, raw_text, created_at) VALUES (?,?,?,?,?,?)",
        (pid, "How they'd reply (interview)", None, "practice", "", now()))
    msgs = []
    for prompt, reply in pairs:
        msgs += [{"speaker": "user", "text": prompt}] + [{"speaker": "person", "text": line.strip()}
                                                         for line in reply.splitlines() if line.strip()]
    _store_messages(conn, pid, cur.lastrowid, msgs)


@app.get("/api/persons")
def list_persons():
    with db() as conn:
        rows = conn.execute("SELECT * FROM persons ORDER BY updated_at DESC").fetchall()
        return [_person_summary(conn, r) for r in rows]


@app.post("/api/persons")
def create_person(body: AnswersIn):
    _check_platform(body.platform)
    with db() as conn:
        ts = now()
        cur = conn.execute(
            "INSERT INTO persons (name, answers, platform, created_at, updated_at) VALUES (?,?,?,?,?)",
            (_name_from_answers(body.answers), json.dumps(body.answers), body.platform or "", ts, ts))
        _sync_practice(conn, cur.lastrowid, body.answers)
        return _person_summary(conn, _person_row(conn, cur.lastrowid))


@app.get("/api/persons/{pid}")
def get_person(pid: int):
    with db() as conn:
        return _person_summary(conn, _person_row(conn, pid))


@app.put("/api/persons/{pid}")
def update_person(pid: int, body: AnswersIn):
    _check_platform(body.platform)
    with db() as conn:
        _person_row(conn, pid)
        conn.execute("UPDATE persons SET name = ?, answers = ?, platform = COALESCE(?, platform) WHERE id = ?",
                     (_name_from_answers(body.answers), json.dumps(body.answers), body.platform, pid))
        _sync_practice(conn, pid, body.answers)
        bump_version(conn, pid)
        return _person_summary(conn, _person_row(conn, pid))


class SettingsIn(BaseModel):
    use_llm: bool


@app.put("/api/persons/{pid}/settings")
def update_settings(pid: int, body: SettingsIn):
    with db() as conn:
        _person_row(conn, pid)
        conn.execute("UPDATE persons SET use_llm = ? WHERE id = ?", (int(body.use_llm), pid))
        return _person_summary(conn, _person_row(conn, pid))


@app.delete("/api/persons/{pid}")
def delete_person(pid: int):
    with db() as conn:
        _person_row(conn, pid)
        conn.execute("DELETE FROM persons WHERE id = ?", (pid,))
    shutil.rmtree(UPLOAD_DIR / str(pid), ignore_errors=True)
    engine.forget(pid)
    return {"ok": True}


# ---------------------------------------------------------------- screenshots
@app.post("/api/persons/{pid}/screenshots")
def upload_screenshots(pid: int, files: list[UploadFile] = File(...)):
    """OCR each image, work out who said what, store everything."""
    results = []
    with db() as conn:
        person = _person_dict(_person_row(conn, pid))
        names = _known_names(person["answers"])
        folder = UPLOAD_DIR / str(pid)
        folder.mkdir(parents=True, exist_ok=True)
        for f in files:
            ext = Path(f.filename or "").suffix.lower()
            if ext not in ALLOWED_EXT:
                raise HTTPException(400, f"{f.filename}: unsupported file type (use PNG, JPG or WEBP).")
            dest = folder / f"{uuid.uuid4().hex}{ext}"
            with dest.open("wb") as out:
                shutil.copyfileobj(f.file, out)
            try:
                img = Image.open(dest)
                img.load()
            except Exception:
                dest.unlink(missing_ok=True)
                raise HTTPException(400, f"{f.filename}: couldn't read this image.")
            parsed = None
            if claude.enabled():
                try:
                    parsed = claude.parse_screenshot(img, names)
                except Exception as exc:  # fall back to local OCR
                    print(f"[imy] Claude couldn't read {f.filename}: {claude.explain(exc)}")
            if parsed is None:
                try:
                    lines, size, rgb = ocr.extract_lines(img)
                except ocr.TesseractNotFoundError:
                    dest.unlink(missing_ok=True)
                    raise HTTPException(503, "Tesseract OCR isn't installed or isn't on your PATH. See the README (step 1).")
                parsed = parser.parse_screenshot(lines, size, rgb, names)
            cur = conn.execute(
                "INSERT INTO screenshots (person_id, filename, stored_path, platform, raw_text, created_at) VALUES (?,?,?,?,?,?)",
                (pid, f.filename, f"{pid}/{dest.name}", parsed["platform"], parsed["raw_text"], now()))
            _store_messages(conn, pid, cur.lastrowid, parsed["messages"])
            shot = _shot_out(conn, conn.execute("SELECT * FROM screenshots WHERE id = ?", (cur.lastrowid,)).fetchone())
            shot["needs_review"] = parsed["needs_review"]
            results.append(shot)
        bump_version(conn, pid)
    return results


class TextImportIn(BaseModel):
    text: str
    person_name: str = ""


@app.post("/api/persons/{pid}/import-text")
def import_text(pid: int, body: TextImportIn):
    """Alternative to screenshots: paste a WhatsApp export or 'Name: message' lines.
    Also the best way to capture emojis, which OCR can't read."""
    with db() as conn:
        person = _person_dict(_person_row(conn, pid))
        names = ([body.person_name] if body.person_name.strip() else []) + _known_names(person["answers"])
        msgs, needs_review = parser.parse_text_export(body.text, names)
        if not msgs:
            raise HTTPException(400, "Couldn't find any messages. Use lines like  Name: message  or a WhatsApp export.")
        cur = conn.execute(
            "INSERT INTO screenshots (person_id, filename, stored_path, platform, raw_text, created_at) VALUES (?,?,?,?,?,?)",
            (pid, "pasted-text", None, "text-import", body.text[:20000], now()))
        _store_messages(conn, pid, cur.lastrowid, msgs)
        bump_version(conn, pid)
        shot = _shot_out(conn, conn.execute("SELECT * FROM screenshots WHERE id = ?", (cur.lastrowid,)).fetchone())
        shot["needs_review"] = needs_review
        return shot


# ---------------------------------------------------------------- chat export files
# Two steps, because one export can hold many chats and many senders:
#   1. POST import-file   read the file, save the result for a moment, show the chats in it
#   2. POST import-commit you pick the chat + which sender is them / you -> messages are stored
IMPORT_DIR = DATA_DIR / "imports"


def _cleanup_old_imports(max_age_s: int = 24 * 3600) -> None:
    import time
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    for f in IMPORT_DIR.glob("*.json"):
        if time.time() - f.stat().st_mtime > max_age_s:
            f.unlink(missing_ok=True)


@app.post("/api/persons/{pid}/import-file")
def import_file(pid: int, file: UploadFile = File(...)):
    """Read a WhatsApp / Messenger / Instagram / Telegram export and list the chats inside."""
    with db() as conn:
        person = _person_dict(_person_row(conn, pid))
    try:
        convs = importers.read_export(file.filename or "", file.file)
    except importers.ExportError as exc:
        raise HTTPException(400, str(exc))
    owner = importers.owner_name(convs)
    _cleanup_old_imports()
    token = uuid.uuid4().hex
    (IMPORT_DIR / f"{token}.json").write_text(json.dumps([
        {"id": c.id, "title": c.title, "platform": c.platform,
         "messages": [[m.sender, m.text, m.ts] for m in c.messages]} for c in convs]))
    out = []
    for c in convs:
        their, mine = importers.guess_roles(c, _known_names(person["answers"]), owner)
        stamps = [m.ts for m in c.messages if m.ts]
        out.append({"id": c.id, "title": c.title, "platform": c.platform, "message_count": len(c.messages),
                    "participants": c.participants(), "guess_person": their, "guess_me": mine,
                    "first": min(stamps) if stamps else None, "last": max(stamps) if stamps else None})
    return {"token": token, "filename": file.filename, "conversations": out}


class ImportCommitIn(BaseModel):
    token: str
    conversation_id: str
    person_name: str        # the sender who is the person you're teaching imy
    me_name: str            # the sender who is you (everyone else in a group chat is skipped)


@app.post("/api/persons/{pid}/import-commit")
def import_commit(pid: int, body: ImportCommitIn):
    if not re.fullmatch(r"[0-9a-f]{32}", body.token):
        raise HTTPException(400, "Bad import token.")
    path = IMPORT_DIR / f"{body.token}.json"
    if not path.exists():
        raise HTTPException(410, "That upload expired. Please choose the file again.")
    conv = next((c for c in json.loads(path.read_text()) if c["id"] == body.conversation_id), None)
    if not conv:
        raise HTTPException(404, "Chat not found in that file.")
    if body.person_name == body.me_name:
        raise HTTPException(400, "Pick different senders for them and for you.")
    roles = {body.person_name: "person", body.me_name: "user"}
    msgs = [{"speaker": roles[s], "text": t, "ts": ts} for s, t, ts in conv["messages"] if s in roles]
    if not any(m["speaker"] == "person" for m in msgs):
        raise HTTPException(400, f"{body.person_name} has no messages in this chat.")
    sessions = importers.split_sessions(msgs)
    title = conv["title"] or "Chat export"
    with db() as conn:
        _person_row(conn, pid)
        for n, chunk in enumerate(sessions, 1):
            cur = conn.execute(
                "INSERT INTO screenshots (person_id, filename, stored_path, platform, raw_text, created_at) VALUES (?,?,?,?,?,?)",
                (pid, f"{title} · talk {n} of {len(sessions)}", None, conv["platform"], "", now()))
            _store_messages(conn, pid, cur.lastrowid, chunk)
        bump_version(conn, pid)
    path.unlink(missing_ok=True)
    return {"sessions": len(sessions), "messages": len(msgs),
            "person_messages": sum(m["speaker"] == "person" for m in msgs),
            "user_messages": sum(m["speaker"] == "user" for m in msgs)}


@app.get("/api/persons/{pid}/screenshots")
def list_screenshots(pid: int):
    with db() as conn:
        _person_row(conn, pid)
        # "practice" = the interview's "how would they reply?" answers; they're edited in the interview, not here
        shots = conn.execute("SELECT * FROM screenshots WHERE person_id = ? AND platform != 'practice' ORDER BY id",
                             (pid,)).fetchall()
        return [_shot_out(conn, s) for s in shots]


@app.post("/api/screenshots/{sid}/flip")
def flip_screenshot(sid: int):
    """Swap user/person on every message in a screenshot (when the sides were guessed backwards)."""
    with db() as conn:
        shot = conn.execute("SELECT * FROM screenshots WHERE id = ?", (sid,)).fetchone()
        if not shot:
            raise HTTPException(404, "Screenshot not found")
        conn.execute("UPDATE messages SET speaker = CASE speaker WHEN 'user' THEN 'person' ELSE 'user' END WHERE screenshot_id = ?", (sid,))
        conn.execute("UPDATE screenshots SET flipped = 1 - flipped WHERE id = ?", (sid,))
        bump_version(conn, shot["person_id"])
        return _shot_out(conn, conn.execute("SELECT * FROM screenshots WHERE id = ?", (sid,)).fetchone())


@app.delete("/api/screenshots/{sid}")
def delete_screenshot(sid: int):
    with db() as conn:
        shot = conn.execute("SELECT * FROM screenshots WHERE id = ?", (sid,)).fetchone()
        if not shot:
            raise HTTPException(404, "Screenshot not found")
        conn.execute("DELETE FROM screenshots WHERE id = ?", (sid,))
        bump_version(conn, shot["person_id"])
    if shot["stored_path"]:
        (UPLOAD_DIR / shot["stored_path"]).unlink(missing_ok=True)
    return {"ok": True}


# ---------------------------------------------------------------- message corrections
class MessageEdit(BaseModel):
    text: str | None = None
    speaker: str | None = None


@app.put("/api/messages/{mid}")
def edit_message(mid: int, body: MessageEdit):
    with db() as conn:
        m = conn.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
        if not m:
            raise HTTPException(404, "Message not found")
        if body.speaker is not None and body.speaker not in ("user", "person"):
            raise HTTPException(400, "speaker must be 'user' or 'person'")
        conn.execute("UPDATE messages SET text = ?, speaker = ? WHERE id = ?",
                     (body.text if body.text is not None else m["text"],
                      body.speaker or m["speaker"], mid))
        bump_version(conn, m["person_id"])
        return dict(conn.execute("SELECT id, position, speaker, text FROM messages WHERE id = ?", (mid,)).fetchone())


@app.delete("/api/messages/{mid}")
def delete_message(mid: int):
    with db() as conn:
        m = conn.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
        if not m:
            raise HTTPException(404, "Message not found")
        conn.execute("DELETE FROM messages WHERE id = ?", (mid,))
        bump_version(conn, m["person_id"])
    return {"ok": True}


@app.post("/api/messages/{mid}/merge-up")
def merge_message_up(mid: int):
    """Glue this message onto the one before it (when one bubble was split in two)."""
    with db() as conn:
        m = conn.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
        if not m:
            raise HTTPException(404, "Message not found")
        prev = conn.execute("SELECT * FROM messages WHERE screenshot_id = ? AND position < ? ORDER BY position DESC LIMIT 1",
                            (m["screenshot_id"], m["position"])).fetchone()
        if not prev:
            raise HTTPException(400, "There's no message above this one.")
        conn.execute("UPDATE messages SET text = ? WHERE id = ?", (prev["text"] + " " + m["text"], prev["id"]))
        conn.execute("DELETE FROM messages WHERE id = ?", (mid,))
        bump_version(conn, m["person_id"])
    return {"ok": True}


# ---------------------------------------------------------------- chat
class ChatIn(BaseModel):
    message: str


@app.get("/api/persons/{pid}/chat")
def get_chat(pid: int):
    with db() as conn:
        _person_row(conn, pid)
        rows = conn.execute("SELECT id, role, text, debug, created_at FROM chat_messages WHERE person_id = ? ORDER BY id", (pid,)).fetchall()
        return [{**dict(r), "debug": json.loads(r["debug"]) if r["debug"] else None} for r in rows]


@app.post("/api/persons/{pid}/chat")
def send_chat(pid: int, body: ChatIn):
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "Message is empty.")
    with db() as conn:
        person = _person_for_engine(conn, pid)
        history = [dict(r) for r in conn.execute(
            "SELECT role, text FROM chat_messages WHERE person_id = ? ORDER BY id DESC LIMIT 16", (pid,)).fetchall()][::-1]
        conn.execute("INSERT INTO chat_messages (person_id, role, text, created_at) VALUES (?,?,?,?)", (pid, "user", text, now()))
        reply = engine.generate_reply(person, _messages_for_engine(conn, pid), history, text)
        debug = json.dumps(reply.debug())
        out = []
        for i, bubble in enumerate(reply.bubbles):
            cur = conn.execute("INSERT INTO chat_messages (person_id, role, text, debug, created_at) VALUES (?,?,?,?,?)",
                               (pid, "person", bubble, debug if i == len(reply.bubbles) - 1 else None, now()))
            out.append({"id": cur.lastrowid, "role": "person", "text": bubble,
                        "debug": reply.debug() if i == len(reply.bubbles) - 1 else None})
        return {"bubbles": out}


@app.delete("/api/persons/{pid}/chat")
def clear_chat(pid: int):
    with db() as conn:
        _person_row(conn, pid)
        conn.execute("DELETE FROM chat_messages WHERE person_id = ?", (pid,))
    return {"ok": True}


ENDEARMENTS = ["love", "babe", "baby", "bby", "bebe", "beb", "mahal", "mhie", "hon", "honey", "beh", "bes", "besh",
               "sis", "mars", "bro", "pre", "dude", "bub", "bubba", "lab", "langga", "palangga"]


@app.get("/api/persons/{pid}/guesses")
def get_guesses(pid: int):
    """'imy noticed…' cards for the interview: things measured from the real messages.
    Each says which answer to fill in if you confirm it (mode 'add' appends to a comma list)."""
    with db() as conn:
        person = _person_for_engine(conn, pid)
        msgs = [m for m in _messages_for_engine(conn, pid) if m.get("shot_platform") != "practice"]
    texts = [m["text"] for m in msgs if m["speaker"] == "person"]
    if len(texts) < 10:
        return []
    st = style_mod.build_style(texts, {})
    answers = person["answers"]
    has = lambda qid, v: v.lower() in (answers.get(qid) or "").lower()
    out = []
    low = [t.lower() for t in texts]
    counts = {e: sum(bool(re.search(rf"\b{e}\b", t)) for t in low) for e in ENDEARMENTS}
    for e, c in sorted(counts.items(), key=lambda kv: -kv[1])[:2]:
        if c >= 3 and not has("q3", e):
            out.append({"id": f"end_{e}", "text": f"They call you “{e}” a lot", "field": "q3", "value": e, "mode": "add"})
    if st["laugh"] and not answers.get("x_laugh"):
        out.append({"id": "laugh", "text": f"They laugh with “{st['laugh']}”", "field": "x_laugh", "value": st["laugh"], "mode": "set"})
    if st["lowercase_start"] is not None and st["lowercase_start"] >= 0.75 and not answers.get("x_case"):
        out.append({"id": "case", "text": "They type in all lowercase", "field": "x_case", "value": "all lowercase, no periods", "mode": "set"})
    if not answers.get("q11"):
        if st["median_words"] <= 5:
            out.append({"id": "len", "text": "They text in short, quick messages", "field": "q11",
                        "value": "Short, quick messages, never much", "mode": "set"})
        elif st["median_words"] > 16:
            out.append({"id": "len", "text": "They write long paragraphs", "field": "q11", "value": "One long paragraph", "mode": "set"})
    if st["top_emojis"] and st["emoji_ratio"] >= 0.1 and not answers.get("x_emoji_fav"):
        fav = " ".join(st["top_emojis"][:3])
        out.append({"id": "emoji", "text": f"Their favorite emojis: {fav}", "field": "x_emoji_fav", "value": fav, "mode": "set"})
    elif st["emoji_ratio"] < 0.03 and not answers.get("q12"):
        out.append({"id": "noemoji", "text": "They barely use emojis", "field": "q12", "value": "No emojis, ever", "mode": "set"})
    tl = sum(bool(engine.TAGALOG_RE.search(t)) for t in texts) / len(texts)
    if tl >= 0.25 and not answers.get("x_lang"):
        out.append({"id": "lang", "text": "They text in Taglish", "field": "x_lang", "value": "Taglish", "mode": "set"})
    for phrase, _ in st["top_phrases"][:2]:
        if not has("q13", phrase):
            out.append({"id": f"phrase_{phrase}", "text": f"They often say “{phrase}”", "field": "q13", "value": phrase, "mode": "add"})
    return out


@app.get("/api/persons/{pid}/style")
def get_style(pid: int):
    with db() as conn:
        person = _person_for_engine(conn, pid)
        index = engine.get_index(person, _messages_for_engine(conn, pid))
        return {"lines": style_mod.describe_style(index.style), "embedder": index.space.kind,
                "exchanges": len(index.prompts), "messages": index.style["n_messages"]}


# ---------------------------------------------------------------- serve the built frontend (optional)
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        target = FRONTEND_DIST / path
        return FileResponse(target if path and target.is_file() else FRONTEND_DIST / "index.html")
