"""ocr.py - turn a screenshot into positioned lines of text.

We don't just want the text; we need to know WHERE each line sits, because
position (left vs right side) and bubble colour are how we tell who said what.

Two OCR passes are run: one on the normal image and one on the inverted image.
Light text on a coloured bubble (e.g. white on blue) is hard for Tesseract in
the normal pass, but easy once inverted. Lines found only in the second pass
are merged in.
"""
import os
from dataclasses import dataclass

import numpy as np
import pytesseract
from scipy import ndimage
from PIL import Image, ImageOps
from pytesseract import Output

if os.environ.get("TESSERACT_CMD"):  # needed on Windows if tesseract isn't on PATH
    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]

DUAL_PASS = os.environ.get("IMY_OCR_DUAL", "1") != "0"
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
    w, _ = img.size
    if w < 1000:
        img = img.resize((1000, int(img.height * 1000 / w)), Image.LANCZOS)
    elif w > 2200:
        img = img.resize((2200, int(img.height * 2200 / w)), Image.LANCZOS)
    return img


def _ocr_lines(gray: Image.Image) -> list[Line]:
    data = pytesseract.image_to_data(gray, config="--psm 6", output_type=Output.DICT)
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
    lines = _ocr_lines(gray)
    if DUAL_PASS:
        for extra in _ocr_lines(ImageOps.invert(gray)):
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


