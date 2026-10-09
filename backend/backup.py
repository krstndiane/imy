"""backup.py - a safe copy of everything imy stores.

    python -m backend.backup                 one backup, now
    python -m backend.backup --keep 14       keep the last 14 instead of 7
    python -m backend.backup --db-only       just the database, skip the photos (much smaller)
    python -m backend.backup --list          show the backups you have

Each backup is one zip in `data/backups/`: the database plus every uploaded file (screenshots,
profile pictures, shared photos).

Why not just copy the .db file? Because imy may be writing to it at that moment, and a half-copied
database is worse than none. SQLite has a proper way to do this - `Connection.backup` - which takes
a consistent snapshot while the site keeps running. That's what this uses.

Set IMY_BACKUP_DAILY=1 and imy makes one itself every 24 hours while it runs.

TO RESTORE: stop imy, unzip the backup over your `data/` folder (so `imy.db` and `uploads/` land
where they were), start imy again. Try this once before you need it - an untested backup isn't one.

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


if __name__ == "__main__":
    args = sys.argv[1:]
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
