"""media.py - photos and files shared inside a chat, and working out what they show.

Two jobs:

  save()      copy an upload into data/uploads/<person>/media/, shrink huge photos, note its size.
              Profile pictures go through here too (square, small), so they load fast.

  describe()  put the photo into words, so the reply can be ABOUT the photo. It tries, in order:
                1. a free AI that can see images (Groq's Llama 4, or Gemini) - the best by far
                2. Tesseract, if the photo is mostly text (a screenshot of something)
                3. simple facts from the image itself (a dark close-up, a wide bright photo...)
              Something always comes back, so sharing a photo never fails just because the AI is busy.

Nothing here talks to the database; main.py stores what these functions return.
"""
import base64
import io
import mimetypes
import re
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from . import llm

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v", ".3gp"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".opus", ".aac"}
DOC_EXT = {".pdf", ".txt", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".zip"}
ALLOWED = IMAGE_EXT | VIDEO_EXT | AUDIO_EXT | DOC_EXT

MAX_SIDE = 1600          # photos bigger than this are shrunk before being stored
AVATAR_SIDE = 320        # profile pictures are small squares
SEE_SIDE = 1024          # what gets sent to the AI that looks at it


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


def save(folder: Path, filename: str, data: bytes, avatar: bool = False) -> dict:
    """Store one file and return what to remember about it."""
    ext = Path(filename or "").suffix.lower()
    if ext in (".heic", ".heif"):
        raise MediaError("iPhone HEIC photos can't be read yet. Share a screenshot or save it as JPG first.")
    if ext not in ALLOWED:
        raise MediaError("That kind of file can't be shared here.")
    folder.mkdir(parents=True, exist_ok=True)
    width = height = None
    mime = mimetypes.guess_type(filename or "")[0] or "application/octet-stream"
    if ext in IMAGE_EXT:
        try:
            data, mime, width, height = _shrink(data, AVATAR_SIDE if avatar else MAX_SIDE, square=avatar)
        except Exception:
            raise MediaError("That image couldn't be opened. Try a PNG or JPG.")
        ext = {"image/png": ".png", "image/gif": ".gif"}.get(mime, ".jpg")
    name = f"{uuid.uuid4().hex}{ext}"
    (folder / name).write_bytes(data)
    return {"name": name, "filename": filename or name, "kind": "image" if ext in IMAGE_EXT else kind_of(filename),
            "mime": mime, "bytes": len(data), "width": width, "height": height}


# ---------------------------------------------------------------- what is in it
def _data_url(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
        small, mime, _, _ = _shrink(raw, SEE_SIDE)
    except Exception:
        return None
    if len(small) > 3_500_000:        # the free services refuse very large images
        return None
    return f"data:{mime};base64,{base64.b64encode(small).decode()}"


def _ocr_text(path: Path) -> str:
    """Words in the picture - useful when someone shares a screenshot of something."""
    try:
        from . import ocr
        text = ocr.plain_text(Image.open(path))
    except Exception:
        return ""
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) >= 25 else ""


def _plain_look(path: Path) -> str:
    """Last resort: a couple of honest facts about the picture itself."""
    try:
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    except Exception:
        return "a photo"
    w, h = img.size
    small = img.resize((32, 32))
    pixels = list(small.getdata())
    bright = sum(sum(p) for p in pixels) / (len(pixels) * 3)
    shape = "a tall photo" if h > w * 1.2 else "a wide photo" if w > h * 1.2 else "a square photo"
    light = "very dark" if bright < 60 else "dark" if bright < 110 else "bright" if bright > 190 else "normally lit"
    return f"{shape}, {light}"


def describe(path: Path, kind: str, filename: str = "") -> str:
    """One short line saying what this file is, for the reply engine. Never raises."""
    if kind != "image":
        label = {"video": "a video", "audio": "a voice message or audio clip", "file": "a file"}.get(kind, "a file")
        return f"{label} called “{filename}”" if filename else label
    try:
        if llm.can_see():
            url = _data_url(path)
            if url:
                seen = llm.see(url)
                if seen:
                    return seen
        words = _ocr_text(path)
        if words:
            return f"a screenshot. The words in it say: {words[:400]}"
        return _plain_look(path)
    except Exception as exc:                      # never let a description break sending a photo
        print(f"[imy] couldn't describe a photo: {exc}")
        return "a photo"
