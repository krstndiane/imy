"""storage.py - where uploaded files live: screenshots, chat photos and profile pictures.

On a live site they're kept in Supabase Storage, not on the server's disk, so the server can be
replaced, rebuilt or moved without losing anyone's pictures. Set in .env:

    SUPABASE_URL=https://<project>.supabase.co
    SUPABASE_SERVICE_KEY=...          the project's service_role key (Project Settings > API). Server only.
    IMY_SUPABASE_BUCKET=imy-uploads   created for you, private (optional; this is the default)

Without those (your own computer, tests) files go in data/uploads/ instead, exactly as before.

The bucket is PRIVATE: nobody can open a file with a Supabase link. Pictures still come through imy
at /uploads/<path>, which checks that the person the file belongs to is yours (see main.serve_upload).

Every file's name starts with the id of the person it belongs to ("12/ab34....png",
"12/media/...", "12/avatar/..."), so removing a person removes everything under "12/".

    put(name, data, mime)   save (replaces a file with the same name)
    get(name)               the bytes, or None if there's no such file
    delete(names)           remove these files (missing ones are fine)
    delete_folder(prefix)   remove everything under "12/"
    kind()                  "supabase" or "local", for the status page and preflight
"""
import json
import mimetypes
import os
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config  # noqa: F401  (loads .env)
from . import db as _db       # _db.UPLOAD_DIR, read each time (tests point it at a fresh folder)

TIMEOUT = float(os.environ.get("IMY_STORAGE_TIMEOUT", "30"))


class StorageError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _settings() -> tuple[str, str, str] | None:
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
    if not (url and key):
        return None
    return url, key, os.environ.get("IMY_SUPABASE_BUCKET", "imy-uploads")


def kind() -> str:
    return "supabase" if _settings() else "local"


def _safe(name: str) -> str:
    """A stored name: "<person id>/...", no "..", no leading slash. Anything else is refused."""
    parts = [p for p in (name or "").replace("\\", "/").split("/") if p]
    if not parts or any(p in (".", "..") for p in parts) or not parts[0].isdigit():
        raise StorageError(f"not a valid file name: {name!r}")
    return "/".join(parts)


# ---------------------------------------------------------------- Supabase Storage (REST API)
_bucket_ready: set[str] = set()
_bucket_lock = threading.Lock()


def _call(method: str, path: str, data: bytes | None = None, content_type: str = "application/json",
          extra: dict | None = None) -> bytes:
    url, key, _ = _settings()
    req = urllib.request.Request(f"{url}/storage/v1/{path}", data=data, method=method, headers={
        "Authorization": f"Bearer {key}", "apikey": key, "Content-Type": content_type, **(extra or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:300]
        raise StorageError(f"Supabase Storage said {err.code}: {body}", err.code) from None
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise StorageError(f"couldn't reach Supabase Storage: {err}") from None


def _quote(name: str) -> str:
    return urllib.parse.quote(name, safe="/")


def _ensure_bucket() -> None:
    """Create the private bucket the first time it's needed (does nothing if it already exists)."""
    _, _, bucket = _settings()
    with _bucket_lock:
        if bucket in _bucket_ready:
            return
        try:
            _call("POST", "bucket", json.dumps({"id": bucket, "name": bucket, "public": False}).encode())
        except StorageError as exc:
            if exc.status not in (400, 409) and "already exists" not in str(exc).lower():   # exists already: fine
                raise
        _bucket_ready.add(bucket)


def _list(prefix: str) -> list[str]:
    """Every file under a folder, at any depth."""
    _, _, bucket = _settings()
    out, folders = [], [prefix.rstrip("/")]
    while folders:
        folder = folders.pop()
        offset = 0
        while True:
            body = json.dumps({"prefix": folder, "limit": 1000, "offset": offset}).encode()
            items = json.loads(_call("POST", f"object/list/{bucket}", body) or b"[]")
            for item in items:
                full = f"{folder}/{item['name']}" if folder else item["name"]
                (out if item.get("id") else folders).append(full)   # folders come back without an id
            if len(items) < 1000:
                break
            offset += 1000
    return out


# ---------------------------------------------------------------- public
def put(name: str, data: bytes, mime: str | None = None) -> None:
    name = _safe(name)
    mime = mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
    if not _settings():
        path = _db.UPLOAD_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return
    _ensure_bucket()
    _, _, bucket = _settings()
    _call("POST", f"object/{bucket}/{_quote(name)}", data, mime, {"x-upsert": "true", "Cache-Control": "max-age=86400"})


def get(name: str) -> bytes | None:
    try:
        name = _safe(name)
    except StorageError:
        return None
    if not _settings():
        path = (_db.UPLOAD_DIR / name).resolve()
        if _db.UPLOAD_DIR.resolve() not in path.parents or not path.is_file():
            return None
        return path.read_bytes()
    _, _, bucket = _settings()
    try:
        return _call("GET", f"object/{bucket}/{_quote(name)}")
    except StorageError as exc:
        if exc.status in (400, 404):          # Supabase answers 400 "Object not found" for a missing file
            return None
        raise


def delete(names) -> None:
    """Remove files. Never raises for a file that's already gone; other failures are printed, because
    removing a picture must never stop a person or an account from being deleted."""
    names = [n for n in (names or []) if n]
    clean = []
    for n in names:
        try:
            clean.append(_safe(n))
        except StorageError:
            continue
    if not clean:
        return
    if not _settings():
        for n in clean:
            (_db.UPLOAD_DIR / n).unlink(missing_ok=True)
        return
    _, _, bucket = _settings()
    try:
        for i in range(0, len(clean), 1000):
            _call("DELETE", f"object/{bucket}", json.dumps({"prefixes": clean[i:i + 1000]}).encode())
    except StorageError as exc:
        print(f"[imy] couldn't remove {len(clean)} file(s) from Supabase: {exc}")


def delete_folder(prefix: str) -> None:
    """Everything stored for one person ("12/")."""
    try:
        prefix = _safe(prefix)
    except StorageError:
        return
    if not _settings():
        shutil.rmtree(_db.UPLOAD_DIR / prefix, ignore_errors=True)
        return
    try:
        delete(_list(prefix))
    except StorageError as exc:
        print(f"[imy] couldn't list files to remove under {prefix}/: {exc}")


def check() -> str:
    """Save, read back and remove a tiny test file. Returns "" if it all worked, else what went wrong.
    Used by `python -m backend.preflight` and `python -m backend.services`."""
    name = "0/imy-check.txt"
    try:
        put(name, b"imy storage check", "text/plain")
        ok = get(name) == b"imy storage check"
        delete([name])
        return "" if ok else "the test file came back different"
    except StorageError as exc:
        return str(exc)
