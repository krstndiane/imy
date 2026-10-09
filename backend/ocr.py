"""ocr.py - turn a screenshot into positioned lines of text.

We don't just want the text; we need to know WHERE each line sits, because
position (left vs right side) and bubble colour are how we tell who said what.

Two OCR passes are run: one on the normal image and one on the inverted image.
Light text on a coloured bubble (e.g. white on blue) is hard for Tesseract in
the normal pass, but easy once inverted. Lines found only in the second pass
are merged in.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
import pytesseract
from scipy import ndimage
from PIL import Image, ImageOps
from pytesseract import Output

if os.environ.get("TESSERACT_CMD"):  # needed on Windows if tesseract isn't on PATH
    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]

DUAL_PASS = os.environ.get("IMY_OCR_DUAL", "1") != "0"
OCR_LANG = os.environ.get("IMY_OCR_LANG", "eng")      # e.g. "eng+tgl" if the Tagalog language pack is installed
OCR_TIMEOUT = int(os.environ.get("IMY_OCR_TIMEOUT", "40"))   # seconds per Tesseract call; a stuck call is skipped
MAX_PIXELS = 24_000_000
# Tesseract runs as a separate program, so several bubbles can be read at the same time.
OCR_THREADS = max(1, int(os.environ.get("IMY_OCR_THREADS", min(4, os.cpu_count() or 1))))
_POOL = ThreadPoolExecutor(max_workers=OCR_THREADS, thread_name_prefix="imy-ocr")
TesseractNotFoundError = pytesseract.TesseractNotFoundError


@dataclass
class Line:
    text: str
    left: int
    top: int
    right: int
    bottom: int
    conf: float

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)

    @property
    def width(self) -> int:
        return max(1, self.right - self.left)


def prepare(img: Image.Image) -> Image.Image:
    """Normalise orientation/colour and scale to a size Tesseract likes."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    w, h = img.size
    if w * h > MAX_PIXELS:   # a very long scrolling capture: shrink it so reading stays fast and memory-safe
        f = (MAX_PIXELS / (w * h)) ** 0.5
        img = img.resize((max(1, int(w * f)), max(1, int(h * f))), Image.LANCZOS)
        w, h = img.size
    if w < 1000:
        img = img.resize((1000, int(img.height * 1000 / w)), Image.LANCZOS)
    elif w > 2200:
        img = img.resize((2200, int(img.height * 2200 / w)), Image.LANCZOS)
    return img


def _ocr_lines(gray: Image.Image) -> list[Line]:
    try:
        data = pytesseract.image_to_data(gray, lang=OCR_LANG, config="--psm 6", output_type=Output.DICT,
                                         timeout=OCR_TIMEOUT)
    except RuntimeError as exc:          # pytesseract raises RuntimeError when the timeout is hit
        print(f"[imy] OCR skipped part of an image: {exc}")
        return []
    groups: dict[tuple, list[dict]] = {}
    for i, raw in enumerate(data["text"]):
        text = raw.strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if conf < 30:  # Tesseract's own "I'm not sure this is a word" filter
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        groups.setdefault(key, []).append(
            dict(text=text, left=data["left"][i], top=data["top"][i],
                 w=data["width"][i], h=data["height"][i], conf=conf)
        )

    lines: list[Line] = []
    for words in groups.values():
        words.sort(key=lambda w: w["left"])
        med_h = float(np.median([w["h"] for w in words]))
        # Split a "line" into pieces where there's a big horizontal gap. This
        # separates e.g. a bubble on the left from a timestamp on the right.
        chunk = [words[0]]
        pieces = []
        for prev, cur in zip(words, words[1:]):
            if cur["left"] - (prev["left"] + prev["w"]) > 2.2 * med_h:
                pieces.append(chunk)
                chunk = [cur]
            else:
                chunk.append(cur)
        pieces.append(chunk)
        for piece in pieces:
            lines.append(Line(
                text=" ".join(w["text"] for w in piece),
                left=min(w["left"] for w in piece),
                top=min(w["top"] for w in piece),
                right=max(w["left"] + w["w"] for w in piece),
                bottom=max(w["top"] + w["h"] for w in piece),
                conf=float(np.mean([w["conf"] for w in piece])),
            ))
    return lines


def _overlap(a: Line, b: Line) -> float:
    """Intersection area divided by the smaller box's area (0..1)."""
    ix = max(0, min(a.right, b.right) - max(a.left, b.left))
    iy = max(0, min(a.bottom, b.bottom) - max(a.top, b.top))
    smaller = min(a.width * a.height, b.width * b.height)
    return (ix * iy) / smaller if smaller else 0.0


def _bubble_regions(rgb: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Find solid-coloured blobs that differ from the page background (chat bubbles).

    Returns boxes as (x0, y0, x1, y1). Works on a 4x-downsampled copy for speed.
    """
    step = 4
    small = rgb[::step, ::step].astype(int)
    quant = (small // 16).reshape(-1, 3)
    vals, counts = np.unique(quant, axis=0, return_counts=True)
    common = vals[counts.argmax()]                           # most common colour bin = page background
    in_bin = (small // 16 == common).all(axis=2)
    bg = np.median(small[in_bin], axis=0)                    # its true colour, not the bin centre
    mask = np.abs(small - bg).sum(axis=2) > 45
    mask = ndimage.binary_fill_holes(ndimage.binary_closing(mask, iterations=1))  # text inside a bubble = holes
    labels, _ = ndimage.label(mask)
    boxes = []
    for i, sl in enumerate(ndimage.find_objects(labels), start=1):
        y0, y1, x0, x1 = sl[0].start * step, sl[0].stop * step, sl[1].start * step, sl[1].stop * step
        w, h = x1 - x0, y1 - y0
        fill = (labels[sl] == i).sum() * step * step / max(w * h, 1)
        if 25 <= h <= 0.5 * rgb.shape[0] and w >= 60 and w <= 0.95 * rgb.shape[1] and fill > 0.6:
            boxes.append((x0, y0, x1, y1))
    return boxes


def _ocr_region(rgb: np.ndarray, box) -> list[Line]:
    """OCR one bubble on its own, rescaled so the text is dark on white whatever the colours."""
    x0, y0, x1, y1 = box
    # Inset the crop so the bubble's rounded corners don't get read as stray letters.
    ix, iy = int(0.25 * (y1 - y0)), int(0.1 * (y1 - y0))
    x0, x1, y0, y1 = x0 + ix, x1 - ix, y0 + iy, y1 - iy
    if x1 - x0 < 20 or y1 - y0 < 12:
        return []
    gray = np.asarray(Image.fromarray(rgb[y0:y1, x0:x1]).convert("L")).astype(float)
    diff = np.abs(gray - np.median(gray))                    # distance from the bubble colour
    out = 255 - np.clip(diff * 255 / max(diff.max(), 1), 0, 255)
    pad = 12
    crop = Image.fromarray(out.astype("uint8"))
    crop = ImageOps.expand(crop, border=pad, fill=255)
    lines = _ocr_lines(crop)
    for l in lines:
        l.left += x0 - pad; l.right += x0 - pad; l.top += y0 - pad; l.bottom += y0 - pad
    return lines


def extract_lines(img: Image.Image):
    """Returns (lines, (width, height), rgb_array) for the prepared image."""
    img = prepare(img)
    gray = ImageOps.autocontrast(img.convert("L"))
    inverted = _POOL.submit(_ocr_lines, ImageOps.invert(gray)) if DUAL_PASS else None   # both passes at once
    lines = _ocr_lines(gray)
    if inverted is not None:
        for extra in inverted.result():
            if extra.conf >= 60 and not any(_overlap(extra, l) > 0.3 for l in lines):
                lines.append(extra)
    rgb = np.asarray(img)
    # Third pass: bubbles in which the first passes found no text at all (typically white-on-colour).
    for box in _bubble_regions(rgb):
        x0, y0, x1, y1 = box
        if any(x0 <= (l.left + l.right) / 2 <= x1 and y0 <= (l.top + l.bottom) / 2 <= y1 for l in lines):
            continue
        lines.extend(l for l in _ocr_region(rgb, box) if l.conf >= 50)
    lines.sort(key=lambda l: (l.top, l.left))
    return lines, img.size, rgb




# =====================================================================================
# Screen-aware reading (used for uploads): read a screenshot the way a person does.
#   1. find the CHAT AREA: below the title bar / pinned-message bar, above the message box
#   2. find the BUBBLES inside it by shape and colour (avatars, buttons and icons are skipped)
#   3. read each bubble ON ITS OWN, so its text is complete and never mixed with a neighbour's
# parser.parse_screen() then decides who sent each bubble from its side and colour.
# =====================================================================================
import re

# Title-bar text: status lines, back-button badges like "<30", call/video labels.
HEADER_HINT_RE = re.compile(
    r"active|online|offline|last seen|turn on|typing|tap here|tap for|contact info|"
    r"^\W*[<«(]?\s*\d{1,3}\W*$|^\W*\d{1,2}[:.]\d{2}\W*$", re.I)
# The message box at the bottom of the screen.
COMPOSER_RE = re.compile(
    r"^\W{0,4}(aa|message\W*|imessage|text message|type a message|write a message|send a message|"
    r"message @\S*|message #\S*|start typing|reply)\W{0,6}$", re.I)


# ...often read with the icons around it, e.g. "(Message © ib" or "G@ & (Message ©".
COMPOSER_WORD_RE = re.compile(r"(?<![a-z])(i?message|aa)(?![a-z])", re.I)


@dataclass
class Region:
    """One bubble on screen, with the text read from inside it."""
    x0: int
    y0: int
    x1: int
    y1: int
    color: tuple
    cut: str = ""                       # "top" / "bottom" when the bubble runs off the chat area
    lines: list = None

    @property
    def text(self) -> str:
        return " ".join(l.text for l in (self.lines or []))


@dataclass
class Screen:
    lines: list          # every line found on the whole screenshot
    size: tuple          # (width, height)
    rgb: np.ndarray
    top: int             # chat area: y where messages start
    bottom: int          #            y where the message box begins
    bubbles: list        # Regions, top to bottom
    bg: tuple = (255, 255, 255)   # the chat background colour
    avatars: list = None          # small round shapes at the left edge (profile pictures beside messages)


def _background(rgb: np.ndarray) -> np.ndarray:
    small = rgb[::4, ::4].astype(int)
    quant = (small // 16).reshape(-1, 3)
    vals, counts = np.unique(quant, axis=0, return_counts=True)
    common = vals[counts.argmax()]
    return np.median(small[(small // 16 == common).all(axis=2)], axis=0)


def chat_area(rgb: np.ndarray, lines: list, person_names=()) -> tuple[int, int]:
    """(top, bottom) of the part of the screen where messages are."""
    H, W = rgb.shape[:2]
    bg = _background(rgb)
    frac = (np.abs(rgb.astype(int) - bg).sum(axis=2) > 30).mean(axis=1)    # how much of each row isn't background
    names = [n.lower() for n in person_names if n and len(n) >= 3]
    top = int(0.055 * H)                                                    # phone status bar
    # Full-width bars near the top: the title bar, a separator line, a pinned-message bar.
    for y in range(int(0.35 * H)):
        if frac[y] >= 0.95:
            top = max(top, y + 1)
    # Title-bar text: their name, "Active 1h ago", "Turn on active status", the "<30" back badge.
    for l in lines:
        low = l.text.lower()
        if l.bottom < 0.2 * H and (HEADER_HINT_RE.search(l.text) or any(n in low for n in names)):
            top = max(top, int(l.bottom + 0.5 * l.height))
    bottom = H
    for l in lines:                                                         # the message box
        t = l.text.strip()
        if l.top > 0.75 * H and (COMPOSER_RE.match(t) or (COMPOSER_WORD_RE.search(t) and len(t.split()) <= 6)):
            bottom = min(bottom, int(l.top - 0.7 * l.height))
    for y in range(int(0.8 * H), H):                                        # or a full-width bottom bar
        if frac[y] >= 0.95:
            bottom = min(bottom, y)
            break
    return top, max(bottom, top + 1)


def find_bubbles(rgb: np.ndarray, top: int, bottom: int, avatars: list | None = None) -> list[Region]:
    """Solid-coloured shapes inside the chat area that look like message bubbles.
    Profile pictures found at the left edge are added to `avatars` (if a list is given)."""
    H, W = rgb.shape[:2]
    step = 2
    bg = _background(rgb)
    small = rgb[::step, ::step].astype(int)
    mask = np.abs(small - bg).sum(axis=2) > 30
    mask[: top // step] = False
    mask[bottom // step:] = False
    mask = ndimage.binary_fill_holes(ndimage.binary_closing(mask, iterations=1))
    labels, _ = ndimage.label(mask)
    out = []
    full = np.abs(rgb.astype(int) - bg).sum(axis=2) > 30
    for i, sl in enumerate(ndimage.find_objects(labels), start=1):
        y0, y1, x0, x1 = sl[0].start * step, sl[0].stop * step, sl[1].start * step, sl[1].stop * step
        w, h = x1 - x0, y1 - y0
        fill = (labels[sl] == i).mean()
        if w < 0.06 * W or w > 0.93 * W or h < 0.018 * H or fill < 0.6:
            continue
        squareish = abs(w - h) < 0.3 * max(w, h) and w < 0.15 * W
        if squareish and (x0 < 0.08 * W or abs((x0 + x1) / 2 - W / 2) < 0.1 * W or w < 0.09 * W):
            if avatars is not None and x0 < 0.08 * W and w >= 0.04 * W:
                avatars.append((x0, y0, x1, y1))
            continue                                                       # avatars, the scroll-down button, icons
        # Stacked bubbles sit only a few pixels apart and can be found as one shape: split at background rows.
        cover = full[y0:y1, x0:x1].mean(axis=1)
        start = None
        for y, c in enumerate(list(cover) + [0.0]):
            if c >= 0.2 and start is None:
                start = y
            elif c < 0.2 and start is not None:
                if y - start >= 0.016 * H:
                    a, b = y0 + start, y0 + y
                    px = rgb[a:b, x0:x1][full[a:b, x0:x1]]
                    color = tuple(int(v) for v in np.median(px, axis=0)) if len(px) else (0, 0, 0)
                    cut = "top" if a <= top + 2 else "bottom" if b >= bottom - 2 else ""
                    out.append(Region(x0, a, x1, b, color, cut))
                start = None
    out.sort(key=lambda r: (r.y0, r.x0))
    return out


def read_bubble(rgb: np.ndarray, r: Region) -> list[Line]:
    """OCR one bubble on its own, recoloured to dark text on white whatever its colours."""
    H, W = rgb.shape[:2]
    h = r.y1 - r.y0
    ix, iy = int(min(0.25 * h, 0.035 * W)), int(min(0.12 * h, 0.008 * H))   # skip the rounded corners
    x0, x1, y0, y1 = r.x0 + ix, r.x1 - ix, r.y0 + iy, r.y1 - iy
    if x1 - x0 < 20 or y1 - y0 < 12:
        return []
    gray = np.asarray(Image.fromarray(rgb[y0:y1, x0:x1]).convert("L")).astype(float)
    diff = np.abs(gray - np.median(gray))
    out = 255 - np.clip(diff * 255 / max(diff.max(), 1), 0, 255)
    pad = 16
    crop = ImageOps.expand(Image.fromarray(out.astype("uint8")), border=pad, fill=255)
    lines = [l for l in _ocr_lines(crop) if l.conf >= 45]
    for l in lines:
        l.left += x0 - pad; l.right += x0 - pad; l.top += y0 - pad; l.bottom += y0 - pad
    # A bubble running off the top/bottom of the chat area: drop half-hidden lines at that edge.
    if r.cut == "top":
        lines = [l for l in lines if l.top > y0 + 2]
    if r.cut == "bottom":
        lines = [l for l in lines if l.bottom < y1 - 2]
    return lines


def read_screen(img: Image.Image, person_names=()) -> Screen:
    """Full screen-aware read: lines everywhere, plus each bubble read on its own."""
    lines, size, rgb = extract_lines(img)
    top, bottom = chat_area(rgb, lines, person_names)
    avatars: list = []
    bubbles = find_bubbles(rgb, top, bottom, avatars)
    for r, found in zip(bubbles, _POOL.map(lambda r: read_bubble(rgb, r), bubbles)):
        r.lines = found
    bg = tuple(int(v) for v in _background(rgb))
    return Screen(lines, size, rgb, top, bottom, [r for r in bubbles if r.text.strip()], bg, avatars)
