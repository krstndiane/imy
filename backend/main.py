"""main.py - the web API (FastAPI).

Run from the project root:   python -m uvicorn backend.main:app --reload
Interactive docs:            http://localhost:8000/docs

Built to keep running when something goes wrong: every request uses short database transactions
(slow work like reading screenshots or asking the AI happens outside them), one bad file never
fails a whole upload, a failing reply falls back to a simpler one, and unexpected errors come back
as a friendly message instead of taking the page down.
"""
import hashlib
import json
import os
import re
import shutil
import threading
import time
import traceback
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from . import claude, engine, importers, llm, looks, ocr, parser, style as style_mod
from .db import DATA_DIR, UPLOAD_DIR, bump_version, db, init_db, now

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = ROOT / "frontend" / "dist"
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
MAX_IMAGE_BYTES = int(os.environ.get("IMY_MAX_IMAGE_MB", "25")) * 1024 * 1024
MAX_EXPORT_BYTES = int(os.environ.get("IMY_MAX_EXPORT_MB", "500")) * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 60_000_000          # refuse absurd images (a "decompression bomb") instead of running out of memory


@asynccontextmanager
async def lifespan(_app):
    init_db()
    engine.VectorSpace.warm_up()             # load the matcher in the background: the first reply isn't slow
    if llm.enabled():
        print(f"[imy] AI brain on: {', '.join(llm.providers())} (mode: {llm.mode()})")
        if llm.status()["warning"]:
            print("[imy] WARNING: " + llm.status()["warning"])
    yield


app = FastAPI(title="imy", lifespan=lifespan)
ORIGINS = [o.strip() for o in os.environ.get("IMY_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["*"], allow_headers=["*"])
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# ---------------------------------------------------------------- never crash
@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    """Anything we didn't plan for: log it for you, show the user a calm message."""
    print(f"[imy] error on {request.method} {request.url.path}:\n{traceback.format_exc()}")
    return JSONResponse({"detail": "Something went wrong on imy's side. Please try again."}, status_code=500)


# A small per-visitor speed limit, so one person (or a bot) can't use up the free AI limit or the CPU.
LIMITS = {"chat": (40, 60), "upload": (120, 60), "import": (20, 60)}   # (requests, per seconds)
_hits: dict[tuple[str, str], deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def rate_limit(request: Request | None, kind: str) -> None:
    if request is None or os.environ.get("IMY_RATE_LIMIT", "1") == "0":
        return
    who = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    n, per = LIMITS[kind]
    t = time.time()
    with _hits_lock:
        q = _hits[(who, kind)]
        while q and q[0] < t - per:
            q.popleft()
        if len(q) >= n:
            raise HTTPException(429, "Slow down a little, then try again.")
        q.append(t)


@app.get("/api/health")
def health():
    """For hosting platforms: is the server up and can it use the database?"""
    with db() as conn:
        conn.execute("SELECT 1").fetchone()
    return {"ok": True}


@app.get("/api/status")
def status():
    """Which brain is on (no keys are ever shown)."""
    return {"ai": llm.status(), "claude": claude.enabled(), "matcher": engine.VectorSpace.kind_now()}


# ---------------------------------------------------------------- helpers
def _person_row(conn, pid: int):
    row = conn.execute("SELECT * FROM persons WHERE id = ?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "Person not found")
    return row


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def _person_dict(row) -> dict:
    return {**dict(row), "answers": _loads(row["answers"], {}), "use_llm": bool(row["use_llm"])}


def _look(conn, pid: int, chosen: str, prefs: dict) -> dict:
    """How the chat screen should look: the chosen app (or the one detected in the screenshots), in light
    or dark like the screenshots, with the bubble colours measured from them."""
    metas = [_loads(r["meta"], {}) for r in conn.execute(
        "SELECT meta FROM screenshots WHERE person_id = ? AND stored_path IS NOT NULL ORDER BY id DESC LIMIT 40", (pid,))]
    look = looks.summarize(metas, chosen)
    if not look["detected"]:   # chat exports know their app too
        row = conn.execute("SELECT platform FROM screenshots WHERE person_id = ? AND stored_path IS NULL AND platform IN "
                           "('messenger','instagram','whatsapp','telegram','imessage','viber') GROUP BY platform "
                           "ORDER BY COUNT(*) DESC LIMIT 1", (pid,)).fetchone()
        if row:
            look["detected"] = row["platform"]
            look["platform"] = chosen or row["platform"]
    look["pref_mode"] = prefs.get("mode", "auto")
    look["use_colors"] = prefs.get("colors", True)
    return look


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
        "look": _look(conn, pid, p.get("platform") or "", _loads(p.get("look"), {})),
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
        "SELECT platform FROM screenshots WHERE person_id = ? AND platform NOT IN ('unknown', 'text-import', 'practice', 'correction') "
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
    meta = _loads(shot["meta"], {}) if "meta" in shot.keys() else {}
    return {
        "id": shot["id"], "filename": shot["filename"], "platform": shot["platform"],
        "flipped": bool(shot["flipped"]),
        "url": f"/uploads/{shot['stored_path']}" if shot["stored_path"] else None,
        "messages": [dict(m) for m in msgs],
        "meta": {k: meta[k] for k in ("platform", "confidence", "mode", "title", "group", "senders") if k in meta},
    }


# ---------------------------------------------------------------- questions
@app.get("/api/questions")
def get_questions():
    return json.loads((Path(__file__).parent / "questions.json").read_text())


# ---------------------------------------------------------------- persons
PLATFORMS = {"", "messenger", "imessage", "instagram", "whatsapp", "telegram", "viber"}


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


class LookIn(BaseModel):
    platform: str | None = None     # the app the chat looks like ("" = imy's own look)
    mode: str | None = None         # "auto" (like the screenshots), "light" or "dark"
    colors: bool | None = None      # copy the bubble colours from the screenshots


@app.put("/api/persons/{pid}/look")
def update_look(pid: int, body: LookIn):
    _check_platform(body.platform)
    if body.mode is not None and body.mode not in ("auto", "light", "dark"):
        raise HTTPException(400, "mode must be auto, light or dark")
    with db() as conn:
        row = _person_row(conn, pid)
        prefs = _loads(row["look"], {})
        if body.mode is not None:
            prefs["mode"] = body.mode
        if body.colors is not None:
            prefs["colors"] = body.colors
        conn.execute("UPDATE persons SET look = ?, platform = COALESCE(?, platform) WHERE id = ?",
                     (json.dumps(prefs), body.platform, pid))
        return _person_summary(conn, _person_row(conn, pid))


@app.delete("/api/persons/{pid}")
def delete_person(pid: int):
    with db() as conn:
        _person_row(conn, pid)
        paths = [r["stored_path"] for r in conn.execute(
            "SELECT stored_path FROM screenshots WHERE person_id = ? AND stored_path IS NOT NULL", (pid,))]
        conn.execute("DELETE FROM persons WHERE id = ?", (pid,))
    for p in paths:   # includes images that came from a merged profile's folder
        (UPLOAD_DIR / p).unlink(missing_ok=True)
    shutil.rmtree(UPLOAD_DIR / str(pid), ignore_errors=True)
    engine.forget(pid)
    return {"ok": True}


# ---------------------------------------------------------------- screenshots
def _read_image(img, names: list[str]) -> dict:
    """Who-said-what for one screenshot: Claude if switched on, else the local screen-aware reader."""
    if claude.enabled():
        try:
            return claude.parse_screenshot(img, names)
        except Exception as exc:  # fall back to local OCR
            print(f"[imy] Claude couldn't read a screenshot: {claude.explain(exc)}")
    try:
        screen = ocr.read_screen(img, names)
    except ocr.TesseractNotFoundError:
        raise HTTPException(503, "Tesseract OCR isn't installed or isn't on your PATH. See the README (step 1).")
    return parser.parse_screen(screen, names)


def _other_shot_messages(conn, pid: int, except_id: int | None = None) -> list[list[dict]]:
    rows = conn.execute("SELECT id FROM screenshots WHERE person_id = ? AND stored_path IS NOT NULL AND id != ?",
                        (pid, except_id or -1)).fetchall()
    return [[dict(m) for m in conn.execute("SELECT speaker, text FROM messages WHERE screenshot_id = ? ORDER BY position",
                                           (r["id"],))] for r in rows]


def _file_hash(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _save_upload(f: UploadFile, dest: Path, limit: int) -> None:
    """Copy an upload to disk, refusing files over `limit` bytes without reading them all into memory."""
    size = 0
    with dest.open("wb") as out:
        while chunk := f.file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"{f.filename}: the file is too big (max {limit // (1024 * 1024)} MB).")
            out.write(chunk)


def _find_twin(conn, pid: int, digest: str):
    """The same image already uploaded for this person? Old rows without a fingerprint get one now."""
    row = conn.execute("SELECT * FROM screenshots WHERE person_id = ? AND sha1 = ?", (pid, digest)).fetchone()
    if row:
        return row
    for r in conn.execute("SELECT id, stored_path FROM screenshots WHERE person_id = ? AND stored_path IS NOT NULL "
                          "AND sha1 IS NULL", (pid,)).fetchall():
        path = UPLOAD_DIR / r["stored_path"]
        if path.exists():
            conn.execute("UPDATE screenshots SET sha1 = ? WHERE id = ?", (_file_hash(path), r["id"]))
    return conn.execute("SELECT * FROM screenshots WHERE person_id = ? AND sha1 = ?", (pid, digest)).fetchone()


def _warning(meta: dict, names: list[str]) -> str:
    title = meta.get("title") or ""
    if title and looks.same_person(title, names) is False and not meta.get("group"):
        return f"This looks like a chat with “{title}”, not {names[0] if names else 'this person'}. Remove it if it's the wrong chat."
    if meta.get("group") and not any(looks.same_person(s, names) for s in meta.get("senders", [])):
        return "This is a group chat and none of the names matched. Check who's who."
    return ""


def _process_image(pid: int, f: UploadFile) -> dict:
    """Save, recognise and store ONE screenshot. Raises HTTPException with a friendly message on failure."""
    ext = Path(f.filename or "").suffix.lower()
    if ext in (".heic", ".heif"):
        raise HTTPException(400, f"{f.filename}: HEIC photos can't be read yet. Take a screenshot instead (it saves as PNG).")
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"{f.filename}: unsupported file type (use PNG, JPG or WEBP).")
    with db() as conn:
        person = _person_dict(_person_row(conn, pid))
    names = _known_names(person["answers"])
    folder = UPLOAD_DIR / str(pid)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{uuid.uuid4().hex}{ext}"
    _save_upload(f, dest, MAX_IMAGE_BYTES)
    digest = _file_hash(dest)
    with db() as conn:
        twin = _find_twin(conn, pid, digest)
        if twin:
            dest.unlink(missing_ok=True)
            shot = _shot_out(conn, twin)
            shot.update(duplicate=True, needs_review=False)
            return shot
    try:
        img = Image.open(dest)
        img.load()
    except Exception:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"{f.filename}: couldn't read this image.")
    try:
        parsed = _read_image(img, names)        # slow (OCR): done outside any database transaction
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        print(f"[imy] couldn't read {f.filename}:\n{traceback.format_exc()}")
        raise HTTPException(422, f"{f.filename}: imy couldn't read this screenshot. Try a clearer, uncropped one.")
    meta = parsed.get("meta") or {"platform": parsed["platform"]}
    with db() as conn:
        # Screenshots taken while scrolling overlap: skip messages another screenshot already has.
        msgs, skipped = parser.trim_overlap(parsed["messages"], _other_shot_messages(conn, pid))
        cur = conn.execute(
            "INSERT INTO screenshots (person_id, filename, stored_path, platform, raw_text, created_at, meta, sha1) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (pid, f.filename, f"{pid}/{dest.name}", parsed["platform"], parsed["raw_text"], now(), json.dumps(meta), digest))
        _store_messages(conn, pid, cur.lastrowid, msgs)
        bump_version(conn, pid)
        shot = _shot_out(conn, conn.execute("SELECT * FROM screenshots WHERE id = ?", (cur.lastrowid,)).fetchone())
    warning = _warning(meta, names)
    shot.update(needs_review=parsed["needs_review"] or bool(warning), overlap_skipped=skipped, warning=warning)
    return shot


@app.post("/api/persons/{pid}/screenshots")
def upload_screenshots(pid: int, files: list[UploadFile] = File(...), request: Request = None):
    """Read each image (chat area only, one message per bubble), drop what's already been added, store the rest.
    One bad file doesn't stop the others: it comes back with an "error" instead."""
    rate_limit(request, "upload")
    with db() as conn:
        _person_row(conn, pid)
    results = []
    for f in files:
        try:
            results.append(_process_image(pid, f))
        except HTTPException as exc:
            if len(files) == 1:
                raise
            results.append({"filename": f.filename, "error": exc.detail, "messages": []})
    return results


@app.post("/api/screenshots/{sid}/reread")
def reread_screenshot(sid: int):
    """Read a stored screenshot again with the current reader (e.g. after imy got smarter). Replaces its messages."""
    with db() as conn:
        shot = conn.execute("SELECT * FROM screenshots WHERE id = ?", (sid,)).fetchone()
        if not shot or not shot["stored_path"]:
            raise HTTPException(404, "Screenshot not found")
        person = _person_dict(_person_row(conn, shot["person_id"]))
    path = UPLOAD_DIR / shot["stored_path"]
    if not path.exists():
        raise HTTPException(410, "The image file is missing. Remove this item and upload it again.")
    names = _known_names(person["answers"])
    parsed = _read_image(Image.open(path), names)
    meta = parsed.get("meta") or {"platform": parsed["platform"]}
    with db() as conn:
        msgs, skipped = parser.trim_overlap(parsed["messages"], _other_shot_messages(conn, shot["person_id"], sid))
        conn.execute("DELETE FROM messages WHERE screenshot_id = ?", (sid,))
        conn.execute("UPDATE screenshots SET platform = ?, raw_text = ?, flipped = 0, meta = ? WHERE id = ?",
                     (parsed["platform"], parsed["raw_text"], json.dumps(meta), sid))
        _store_messages(conn, shot["person_id"], sid, msgs)
        bump_version(conn, shot["person_id"])
        out = _shot_out(conn, conn.execute("SELECT * FROM screenshots WHERE id = ?", (sid,)).fetchone())
    warning = _warning(meta, names)
    out.update(needs_review=parsed["needs_review"] or bool(warning), overlap_skipped=skipped, warning=warning)
    return out


class TextImportIn(BaseModel):
    text: str
    person_name: str = ""


@app.post("/api/persons/{pid}/import-text")
def import_text(pid: int, body: TextImportIn):
    """Alternative to screenshots: paste a WhatsApp export or 'Name: message' lines.
    Also the best way to capture emojis, which OCR can't read."""
    if len(body.text) > 2_000_000:
        raise HTTPException(413, "That's a lot of text. Upload the chat export file instead.")
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
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    for f in IMPORT_DIR.glob("*.json"):
        if time.time() - f.stat().st_mtime > max_age_s:
            f.unlink(missing_ok=True)


@app.post("/api/persons/{pid}/import-file")
def import_file(pid: int, file: UploadFile = File(...), request: Request = None):
    """Read a chat export (any supported app; recognised by its content) and list the chats inside.
    The chat that matches this person's name is marked and listed first."""
    rate_limit(request, "import")
    with db() as conn:
        person = _person_dict(_person_row(conn, pid))
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size > MAX_EXPORT_BYTES:
        raise HTTPException(413, f"That file is too big (max {MAX_EXPORT_BYTES // (1024 * 1024)} MB). Export just this one chat.")
    try:
        convs = importers.read_export(file.filename or "", file.file)
    except importers.ExportError as exc:
        raise HTTPException(400, str(exc))
    except Exception:
        print(f"[imy] import failed:\n{traceback.format_exc()}")
        raise HTTPException(400, "imy couldn't read that file. Make sure it's the chat download from the app, not edited.")
    owner = importers.owner_name(convs)
    _cleanup_old_imports()
    token = uuid.uuid4().hex
    (IMPORT_DIR / f"{token}.json").write_text(json.dumps([
        {"id": c.id, "title": c.title, "platform": c.platform,
         "messages": [[m.sender, m.text, m.ts] for m in c.messages]} for c in convs]))
    names = _known_names(person["answers"])
    out = []
    for c in convs:
        their, mine = importers.guess_roles(c, names, owner)
        stamps = [m.ts for m in c.messages if m.ts]
        match = bool(their) and (looks.same_person(c.title, names) is not False or len(c.participants()) <= 2)
        out.append({"id": c.id, "title": c.title, "platform": c.platform, "message_count": len(c.messages),
                    "participants": c.participants(), "guess_person": their, "guess_me": mine, "match": match,
                    "first": min(stamps) if stamps else None, "last": max(stamps) if stamps else None})
    out.sort(key=lambda c: (not (c["match"] and looks.same_person(c["title"], names)), not c["match"], -c["message_count"]))
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
        row = _person_row(conn, pid)
        for n, chunk in enumerate(sessions, 1):
            cur = conn.execute(
                "INSERT INTO screenshots (person_id, filename, stored_path, platform, raw_text, created_at, meta) VALUES (?,?,?,?,?,?,?)",
                (pid, f"{title} · talk {n} of {len(sessions)}", None, conv["platform"], "", now(),
                 json.dumps({"platform": conv["platform"], "title": title})))
            _store_messages(conn, pid, cur.lastrowid, chunk)
        if not row["platform"] and conv["platform"] in PLATFORMS:   # nothing chosen yet: look like the app it came from
            conn.execute("UPDATE persons SET platform = ? WHERE id = ?", (conv["platform"], pid))
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
    message: str = ""
    messages: list[str] = []    # several messages sent in a row ("burst"): answered together, like a person would


@app.get("/api/persons/{pid}/chat")
def get_chat(pid: int):
    with db() as conn:
        _person_row(conn, pid)
        rows = conn.execute("SELECT id, role, text, debug, created_at FROM chat_messages WHERE person_id = ? ORDER BY id", (pid,)).fetchall()
        return [{**dict(r), "debug": _loads(r["debug"], None)} for r in rows]


SAFE_REPLIES = ["wait lang", "teka", "hmm", "one sec"]


@app.post("/api/persons/{pid}/chat")
def send_chat(pid: int, body: ChatIn, request: Request = None):
    rate_limit(request, "chat")
    texts = [t.strip()[:4000] for t in (body.messages or [body.message]) if t and t.strip()][:12]
    if not texts:
        raise HTTPException(400, "Message is empty.")
    with db() as conn:            # 1. save what you said (a short write)
        person = _person_for_engine(conn, pid)
        history = [dict(r) for r in conn.execute(
            "SELECT role, text FROM chat_messages WHERE person_id = ? ORDER BY id DESC LIMIT 16", (pid,)).fetchall()][::-1]
        for t in texts:
            conn.execute("INSERT INTO chat_messages (person_id, role, text, created_at) VALUES (?,?,?,?)", (pid, "user", t, now()))
        training = _messages_for_engine(conn, pid)
    history += [{"role": "user", "text": t} for t in texts[:-1]]
    try:                          # 2. think (no database lock held: other requests keep working)
        reply = engine.generate_reply(person, training, history, texts[-1] if len(texts) == 1 else "\n".join(texts))
        bubbles = [b for b in reply.bubbles if b and b.strip()] or [SAFE_REPLIES[0]]
        debug = reply.debug()
    except Exception:
        print(f"[imy] reply failed:\n{traceback.format_exc()}")
        bubbles, debug = [SAFE_REPLIES[len(history) % len(SAFE_REPLIES)]], {"source": "safe", "note": "Something went wrong; sent a neutral reply."}
    out = []
    with db() as conn:            # 3. save the reply (another short write)
        for i, bubble in enumerate(bubbles):
            last = i == len(bubbles) - 1
            cur = conn.execute("INSERT INTO chat_messages (person_id, role, text, debug, created_at) VALUES (?,?,?,?,?)",
                               (pid, "person", bubble, json.dumps(debug) if last else None, now()))
            out.append({"id": cur.lastrowid, "role": "person", "text": bubble, "created_at": now(), "debug": debug if last else None})
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
        msgs = [m for m in _messages_for_engine(conn, pid) if m.get("shot_platform") not in ("practice", "correction")]
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


# ---------------------------------------------------------------- teaching from the chat
class TeachIn(BaseModel):
    prompt: str             # what you said
    reply: str              # what they would really say (a fix), or the reply imy gave that sounded right (a 👍)


@app.post("/api/persons/{pid}/teach")
def teach(pid: int, body: TeachIn):
    """Save "when I say X, they'd say Y" from the chat. Stored with the person's other chats (one item
    called "Taught in chat", editable in Review) and used before anything else when you say something similar."""
    prompt, reply = body.prompt.strip(), body.reply.strip()
    if not prompt or not reply:
        raise HTTPException(400, "Both your message and their reply are needed.")
    with db() as conn:
        _person_row(conn, pid)
        row = conn.execute("SELECT id FROM screenshots WHERE person_id = ? AND platform = 'correction'", (pid,)).fetchone()
        sid = row["id"] if row else conn.execute(
            "INSERT INTO screenshots (person_id, filename, stored_path, platform, raw_text, created_at) VALUES (?,?,?,?,?,?)",
            (pid, "Taught in chat", None, "correction", "", now())).lastrowid
        start = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM messages WHERE screenshot_id = ?", (sid,)).fetchone()[0]
        lines = [{"speaker": "user", "text": prompt}] + [{"speaker": "person", "text": l.strip()} for l in reply.splitlines() if l.strip()]
        conn.executemany("INSERT INTO messages (person_id, screenshot_id, position, speaker, text) VALUES (?,?,?,?,?)",
                         [(pid, sid, start + i, m["speaker"], m["text"]) for i, m in enumerate(lines)])
        bump_version(conn, pid)
        taught = conn.execute("SELECT COUNT(*) FROM messages WHERE screenshot_id = ? AND speaker = 'user'", (sid,)).fetchone()[0]
    return {"ok": True, "taught": taught}


# ---------------------------------------------------------------- merging duplicates
class MergeIn(BaseModel):
    from_id: int


@app.post("/api/persons/{pid}/merge")
def merge_person(pid: int, body: MergeIn):
    """Move everything learned about another person (screenshots, chats, answers they're missing) into this one,
    then remove the other one. For when the same person was set up twice."""
    if body.from_id == pid:
        raise HTTPException(400, "Pick a different person to merge in.")
    with db() as conn:
        target = _person_dict(_person_row(conn, pid))
        other = _person_dict(_person_row(conn, body.from_id))
        answers = {**{k: v for k, v in other["answers"].items() if (v or "").strip()},
                   **{k: v for k, v in target["answers"].items() if (v or "").strip()}}   # this person's answers win
        conn.execute("UPDATE screenshots SET person_id = ? WHERE person_id = ? AND platform NOT IN ('practice', 'correction')",
                     (pid, body.from_id))
        conn.execute("UPDATE messages SET person_id = ? WHERE screenshot_id IN (SELECT id FROM screenshots WHERE person_id = ?)",
                     (pid, pid))
        for kind in ("correction",):   # their chat corrections join this person's
            rows = conn.execute("SELECT id FROM screenshots WHERE person_id = ? AND platform = ?", (body.from_id, kind)).fetchall()
            for r in rows:
                conn.execute("UPDATE screenshots SET person_id = ?, filename = 'Taught in chat (merged)' WHERE id = ?", (pid, r["id"]))
                conn.execute("UPDATE messages SET person_id = ? WHERE screenshot_id = ?", (pid, r["id"]))
        conn.execute("UPDATE persons SET answers = ? WHERE id = ?", (json.dumps(answers), pid))
        _sync_practice(conn, pid, answers)
        conn.execute("DELETE FROM persons WHERE id = ?", (body.from_id,))   # its uploaded images stay on disk; they're still used
        bump_version(conn, pid)
        engine.forget(body.from_id)
        return _person_summary(conn, _person_row(conn, pid))


@app.get("/api/persons/{pid}/style")
def get_style(pid: int):
    with db() as conn:
        person = _person_for_engine(conn, pid)
        msgs = _messages_for_engine(conn, pid)
    index = engine.get_index(person, msgs)
    return {"lines": style_mod.describe_style(index.style), "embedder": index.space.kind,
            "exchanges": len(index.prompts), "messages": index.style["n_messages"],
            "ai": llm.status()}


# ---------------------------------------------------------------- serve the built frontend (optional)
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")
    _DIST = FRONTEND_DIST.resolve()

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Not found")
        target = (FRONTEND_DIST / path).resolve()
        inside = target == _DIST or _DIST in target.parents     # never serve files outside frontend/dist
        return FileResponse(target if path and inside and target.is_file() else FRONTEND_DIST / "index.html")
