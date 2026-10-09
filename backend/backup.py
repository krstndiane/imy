"""backup.py - a safe copy of everything imy stores.

    python -m backend.backup                 one backup, now
    python -m backend.backup --keep 14       keep the last 14 instead of 7
    python -m backend.backup --db-only       just the database, skip the photos (much smaller)
    python -m backend.backup --list          show the backups you have
    python -m backend.backup --check FILE    open a backup and prove it's complete and readable
    python -m backend.backup --restore FILE  unpack it into a NEW folder (never over your live data)
                             [--to DIR]      where to put it (default: data/restored-<time>)

Each backup is one zip in `data/backups/`: the database plus every uploaded file (screenshots,
profile pictures, shared photos).

Why not just copy the .db file? Because imy may be writing to it at that moment, and a half-copied
database is worse than none. SQLite has a proper way to do this - `Connection.backup` - which takes
a consistent snapshot while the site keeps running. That's what this uses.

Set IMY_BACKUP_DAILY=1 and imy makes one itself every 24 hours while it runs.

TO RESTORE:
    1. python -m backend.backup --restore data/backups/imy-....zip
       This unpacks into a new folder and checks it, so nothing live is touched.
    2. Stop imy, move your current `data/imy.db` (and its -wal/-shm files) and `data/uploads/` aside,
       move the restored `imy.db` and `uploads/` into `data/`, and start imy again.
Try it once before you need it - an untested backup isn't one. `--check` does the proving part
without unpacking anything.

A backup sitting on the same disk protects you from a bad deploy, a bad migration or a mistaken
delete. It does NOT protect you from losing the disk, so also turn on your host's own snapshots, or
download a zip now and then.
"""
import os
import shutil
import sqlite3
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .db import DATA_DIR, DB_PATH, UPLOAD_DIR

BACKUP_DIR = Path(os.environ.get("IMY_BACKUP_DIR", DATA_DIR / "backups"))
KEEP = int(os.environ.get("IMY_BACKUP_KEEP", "7"))
EVERY_SECONDS = 24 * 60 * 60


def _snapshot(to: Path) -> None:
    """A consistent copy of the database, even while imy is using it."""
    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    dest = sqlite3.connect(to)
    try:
        src.backup(dest)            # SQLite's own online backup: no half-written copies
    finally:
        dest.close()
        src.close()


def run(keep: int = KEEP, include_uploads: bool = True) -> dict:
    """Make one backup and tidy up old ones. Returns what it did."""
    if not DB_PATH.exists():
        return {"ok": False, "error": "There's no database to back up yet."}
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = BACKUP_DIR / f"imy-{stamp}.zip"
    n = 2
    while out.exists():          # two backups in the same second must not overwrite each other
        out = BACKUP_DIR / f"imy-{stamp}-{n}.zip"
        n += 1
    tmp_db = BACKUP_DIR / f".{out.stem}.db"
    files = 0
    try:
        _snapshot(tmp_db)
        # Written to a .part file first and renamed at the end, so a backup that was interrupted
        # can never be mistaken for a complete one.
        part = out.with_suffix(".zip.part")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.write(tmp_db, "imy.db")
            if include_uploads and UPLOAD_DIR.exists():
                for f in sorted(UPLOAD_DIR.rglob("*")):
                    if f.is_file():
                        z.write(f, str(Path("uploads") / f.relative_to(UPLOAD_DIR)))
                        files += 1
        part.replace(out)
    finally:
        tmp_db.unlink(missing_ok=True)
    removed = _tidy(keep)
    return {"ok": True, "file": str(out), "megabytes": round(out.stat().st_size / 1048576, 1),
            "uploads": files, "removed_old": removed}


def _newest_first() -> list[Path]:
    """By the time the file was written, not by its name - names tie when two land in one second."""
    return sorted(BACKUP_DIR.glob("imy-*.zip"), key=lambda f: f.stat().st_mtime, reverse=True)


def _tidy(keep: int) -> int:
    """Keep the newest `keep` backups, delete the rest. 0 or less keeps everything."""
    if keep <= 0:
        return 0
    backups = _newest_first()
    for old in backups[keep:]:
        old.unlink(missing_ok=True)
    for junk in BACKUP_DIR.glob("*.part"):      # leftovers from an interrupted run
        junk.unlink(missing_ok=True)
    return max(0, len(backups) - keep)


def listing() -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    return [{"file": f.name, "megabytes": round(f.stat().st_size / 1048576, 1),
             "made": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")}
            for f in _newest_first()]


# ---------------------------------------------------------------- proving and restoring
TABLES = ("users", "persons", "screenshots", "messages", "chat_messages", "media")


def _safe_name(name: str) -> bool:
    """Zip entries can say things like "../../etc/x"; only plain paths inside the backup are allowed."""
    parts = Path(name).parts
    return bool(parts) and not Path(name).is_absolute() and ".." not in parts and ":" not in parts[0]


def _inspect_db(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES if t in have}
    finally:
        conn.close()
    return {"integrity": ok, "rows": counts}


def check(zip_path) -> dict:
    """Open a backup and prove it: every file's checksum, the database's own integrity check, and
    how many rows and uploaded files it holds. Nothing is written anywhere except a temp folder."""
    import tempfile
    import zlib
    zip_path = Path(zip_path)
    try:
        with zipfile.ZipFile(zip_path) as z:
            bad = z.testzip()                    # re-reads every entry and checks its CRC
            names = z.namelist()
            if "imy.db" not in names:
                return {"ok": False, "error": "This zip has no imy.db in it."}
            uploads = sum(1 for n in names if n.startswith("uploads/") and not n.endswith("/"))
            with tempfile.TemporaryDirectory() as tmp:
                z.extract("imy.db", tmp)
                info = _inspect_db(Path(tmp) / "imy.db")
    except (zipfile.BadZipFile, zlib.error, sqlite3.DatabaseError, OSError, EOFError) as exc:
        return {"ok": False, "error": f"This backup is damaged and can't be used ({exc.__class__.__name__}: {exc})."}
    ok = bad is None and info["integrity"] == "ok"
    return {"ok": ok, "file": str(zip_path), "damaged_entry": bad, "uploads": uploads, **info}


def restore(zip_path, to=None) -> dict:
    """Unpack a backup into a NEW folder and check it. Refuses to write over an existing database:
    restoring is something you finish by hand, with imy stopped (see the steps at the top)."""
    zip_path = Path(zip_path)
    to = Path(to) if to else DATA_DIR / f"restored-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    if (to / "imy.db").exists():
        return {"ok": False, "error": f"{to} already has an imy.db. Pick an empty folder with --to."}
    to.mkdir(parents=True, exist_ok=True)
    files = 0
    with zipfile.ZipFile(zip_path) as z:
        for entry in z.infolist():
            if entry.is_dir():
                continue
            if not _safe_name(entry.filename):
                return {"ok": False, "error": f"Refusing an unsafe path in the zip: {entry.filename}"}
            if entry.filename != "imy.db" and not entry.filename.startswith("uploads/"):
                continue
            target = to / entry.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(entry) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            files += entry.filename != "imy.db"
    info = _inspect_db(to / "imy.db")
    return {"ok": info["integrity"] == "ok", "folder": str(to), "uploads": files, **info}


def start_daily() -> None:
    """With IMY_BACKUP_DAILY=1, make one every 24 hours in the background. Never interrupts the site:
    if a backup fails it's printed and the next one is tried tomorrow."""
    if os.environ.get("IMY_BACKUP_DAILY", "0") != "1":
        return

    def loop():
        while True:
            try:
                info = run()
                print(f"[imy] backup: {info.get('file', info.get('error'))}")
            except Exception as exc:
                print(f"[imy] backup failed: {exc}")
            time.sleep(EVERY_SECONDS)

    threading.Thread(target=loop, daemon=True).start()
    print(f"[imy] daily backups on, kept in {BACKUP_DIR}")


def _free_space_note() -> str:
    try:
        free = shutil.disk_usage(BACKUP_DIR if BACKUP_DIR.exists() else DATA_DIR).free
        return f"{round(free / 1073741824, 1)} GB free on this disk"
    except OSError:
        return ""


def _print_contents(info: dict) -> None:
    rows = ", ".join(f"{n} {t}" for t, n in info.get("rows", {}).items())
    print(f"  database: {info.get('integrity')}   {rows}")
    print(f"  uploaded files: {info.get('uploads', 0)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--check" in args:
        info = check(args[args.index("--check") + 1])
        if "error" in info:
            print(info["error"]); sys.exit(1)
        print(("OK - this backup is complete and readable: " if info["ok"] else "PROBLEM with ") + info["file"])
        if info.get("damaged_entry"):
            print(f"  damaged file inside: {info['damaged_entry']}")
        _print_contents(info)
        sys.exit(0 if info["ok"] else 1)
    if "--restore" in args:
        to = args[args.index("--to") + 1] if "--to" in args else None
        info = restore(args[args.index("--restore") + 1], to)
        if "error" in info:
            print(info["error"]); sys.exit(1)
        print(("Restored into " if info["ok"] else "Restored, but the database check FAILED: ") + info["folder"])
        _print_contents(info)
        print("Nothing live was touched. To use it: stop imy, put this imy.db and uploads/ in place of the ones in data/, start imy.")
        sys.exit(0 if info["ok"] else 1)
    if "--list" in args:
        rows = listing()
        print(f"{len(rows)} backup(s) in {BACKUP_DIR}")
        for r in rows:
            print(f"  {r['made']}  {r['megabytes']:>7} MB  {r['file']}")
        sys.exit(0)
    keep = int(args[args.index("--keep") + 1]) if "--keep" in args else KEEP
    result = run(keep=keep, include_uploads="--db-only" not in args)
    if not result["ok"]:
        print(result["error"])
        sys.exit(1)
    print(f"Backed up to {result['file']}  ({result['megabytes']} MB, {result['uploads']} uploaded files)")
    if result["removed_old"]:
        print(f"Removed {result['removed_old']} older backup(s), keeping the newest {keep}.")
    note = _free_space_note()
    if note:
        print(note)
