"""media.py - photos and files shared inside a chat, and working out what they show.

Two jobs:

  save()      store an upload (Supabase Storage on a live site, see storage.py) under
              "<person>/media/" or "<person>/avatar/", shrink huge photos, note its size.
              Profile pictures go through here too (square, small), so they load fast.

  describe()  put the photo into words, so the reply can be ABOUT the photo. It tries, in order:
                1. Groq's image model (llm.see) - the best by far
                2. simple facts from the image itself (a dark close-up, a wide bright photo...)
              Something always comes back, so sharing a photo never fails just because the AI is busy.

Nothing here talks to the database; main.py stores what these functions return.
"""
import io
import mimetypes
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from . import llm, storage

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v", ".3gp"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".opus", ".aac"}
DOC_EXT = {".pdf", ".txt", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".zip"}
ALLOWED = IMAGE_EXT | VIDEO_EXT | AUDIO_EXT | DOC_EXT

MAX_SIDE = 1600          # photos bigger than this are shrunk before being stored
AVATAR_SIDE = 320        # profile pictures are small squares


def kind_of(name: str) -> str:
    ext = Path(name or "").suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return "file"


def _shrink(data: bytes, side: int, square: bool = False) -> tuple[bytes, str, int, int]:
    """Make a web-friendly copy of a photo: upright, not enormous, JPEG or PNG."""
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)            # photos from phones are often sideways
    if getattr(img, "is_animated", False):        # a GIF stays a GIF, exactly as it was
        return data, "image/gif", img.width, img.height
    if square:
        img = ImageOps.fit(img, (side, side), Image.LANCZOS, centering=(0.5, 0.35))   # faces sit a bit high
    else:
        img.thumbnail((side, side), Image.LANCZOS)
    out = io.BytesIO()
    if img.mode in ("RGBA", "LA", "P") and not square:
        img.convert("RGBA").save(out, "PNG", optimize=True)
        return out.getvalue(), "image/png", img.width, img.height
    img.convert("RGB").save(out, "JPEG", quality=86, optimize=True)
    return out.getvalue(), "image/jpeg", img.width, img.height


class MediaError(ValueError):
    """Something about this file can't be used; the message is shown to the person."""


def save(folder: str, filename: str, data: bytes, avatar: bool = False) -> dict:
    """Store one file under `folder` ("12/media") and return what to remember about it.
    "path" is its stored name, for the database; "data" is the stored bytes (for describe())."""
    ext = Path(filename or "").suffix.lower()
    if ext in (".heic", ".heif"):
        raise MediaError("iPhone HEIC photos can't be read yet. Share a screenshot or save it as JPG first.")
    if ext not in ALLOWED:
        raise MediaError("That kind of file can't be shared here.")
    width = height = None
    mime = mimetypes.guess_type(filename or "")[0] or "application/octet-stream"
    if ext in IMAGE_EXT:
        try:
            data, mime, width, height = _shrink(data, AVATAR_SIDE if avatar else MAX_SIDE, square=avatar)
        except Exception:
            raise MediaError("That image couldn't be opened. Try a PNG or JPG.")
        ext = {"image/png": ".png", "image/gif": ".gif"}.get(mime, ".jpg")
    name = f"{uuid.uuid4().hex}{ext}"
    storage.put(f"{folder}/{name}", data, mime)
    return {"name": name, "path": f"{folder}/{name}", "filename": filename or name,
            "kind": "image" if ext in IMAGE_EXT else kind_of(filename),
            "mime": mime, "bytes": len(data), "width": width, "height": height, "data": data}


# ---------------------------------------------------------------- what is in it
def _plain_look(img: Image.Image) -> str:
    """Last resort: a couple of honest facts about the picture itself."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    w, h = img.size
    small = img.resize((32, 32))
    pixels = list(small.getdata())
    bright = sum(sum(p) for p in pixels) / (len(pixels) * 3)
    shape = "a tall photo" if h > w * 1.2 else "a wide photo" if w > h * 1.2 else "a square photo"
    light = "very dark" if bright < 60 else "dark" if bright < 110 else "bright" if bright > 190 else "normally lit"
    return f"{shape}, {light}"


def describe(data: bytes, kind: str, filename: str = "") -> str:
    """One short line saying what this file is, for the reply engine. Never raises."""
    if kind != "image":
        label = {"video": "a video", "audio": "a voice message or audio clip", "file": "a file"}.get(kind, "a file")
        return f"{label} called “{filename}”" if filename else label
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return "a photo"
    try:
        if llm.can_see():
            seen = llm.see(img)
            if seen:
                return seen
        return _plain_look(img)
    except Exception as exc:                      # never let a description break sending a photo
        print(f"[imy] couldn't describe a photo: {exc}")
        return "a photo"
