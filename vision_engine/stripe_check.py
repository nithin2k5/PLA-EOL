"""
vision_engine/stripe_check.py
=============================
Checks the painted stripes on a cable with the second camera ("cam2").

Each part number has a group of painted bands round its cable, for example
three yellow stripes on a black cable. A check passes when the taught box
holds the expected number of stripes, in the expected colour, at the size the
good part showed when it was taught.

Methodology:
  - Teach: the operator boxes the stripe group on a good part. The stripes'
    colour, count, size and the direction of the cable are read from that box
    and saved per part number.
  - Inspect: pixels of the expected colour inside the box are grouped into
    bands. Specks of that colour on the background are dropped because they
    are far smaller than a taught stripe. The bands are then counted along the
    cable, so stripes that touch still count separately.
  - Result: OK only if count, colour, stripe size and group length all match.
"""
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

from . import camera
from .vision_controller import load_camera_config

CAMERA_SOURCE = "cam2"

_ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
_MODELS_DIR = os.path.join(_ROOT_DIR, "vision_models", "stripes")

COLOURS = ("white", "red", "yellow", "green", "blue", "purple")

# How far a live stripe may differ from the taught one before it counts as
# a different part. Hand-painted bands vary, so this is loose.
SIZE_TOLERANCE = 0.4

# The part is fixtured, but not to the pixel: the search covers this much of
# the box's own size again on every side.
SEARCH_MARGIN = 0.25


@dataclass
class StripeResult:
    """Outcome of one stripe check."""
    ok: bool
    judgement: str                  # "OK", "NG", "ERROR"
    part_number: str = ""
    expected_colour: str = ""
    expected_count: int = 0
    count: int = 0                  # stripes of the expected colour found
    seen: str = ""                  # what the box actually holds, e.g. "red x3"
    processing_time_ms: int = 0
    error: Optional[str] = None
    # Diagnostics for the settings page: where the stripes were, and the
    # frame that was judged.
    stripe_boxes: List[np.ndarray] = field(default_factory=list)
    search_box: Optional[Tuple[int, int, int, int]] = None
    frame: Optional[np.ndarray] = None


# ── Colour masks ────────────────────────────────────────────────────────────

def colour_masks(bgr: np.ndarray) -> dict:
    """One mask per stripe colour. OpenCV hue runs 0-180."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    # Shine on glossy paint keeps its hue but loses saturation, so very bright
    # pixels qualify at a lower saturation. Without this a highlight running
    # along the cable splits every stripe in two.
    shine = (s > 40) & (v > 200)
    vivid = ((s > 90) & (v > 45)) | shine
    # Purple and green paint is less saturated than the rest
    pale = ((s > 55) & (v > 45)) | shine
    masks = {
        "white": (s < 55) & (v > 185),
        "red": vivid & ((h < 9) | (h > 165)),
        "yellow": vivid & (h >= 18) & (h <= 38),
        "green": pale & (h >= 40) & (h <= 88),
        "blue": vivid & (h >= 95) & (h <= 128),
        "purple": pale & (h >= 129) & (h <= 165),
    }
    kernel = np.ones((3, 3), np.uint8)
    return {c: cv2.morphologyEx(m.astype(np.uint8) * 255, cv2.MORPH_OPEN, kernel)
            for c, m in masks.items()}


def _paint_mask(masks: dict) -> np.ndarray:
    """Everything that is coloured paint, grown a little to cover its shine."""
    paint = np.zeros_like(masks["white"])
    for c in COLOURS:
        if c != "white":
            paint |= masks[c]
    return cv2.dilate(paint, np.ones((5, 5), np.uint8))


# ── Bands ───────────────────────────────────────────────────────────────────

def _bands(mask: np.ndarray, min_area: int = 12) -> list:
    """Solid colour patches: their pixels, centre and size across/along.

    Patches touching the edge of the mask are left out. Stripes sit inside
    the box drawn round them; a patch running off its edge is background,
    such as a green mat behind green stripes.
    """
    mh, mw = mask.shape[:2]
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < min_area or x == 0 or y == 0 or x + w >= mw or y + h >= mh:
            continue
        ys, xs = np.nonzero(labels == i)
        pts = np.column_stack([xs, ys]).astype(np.float32)
        (cx, cy), (w, h), ang = cv2.minAreaRect(pts)
        # A stripe is a filled band, not a hairline or a scattered smear
        if area / max(1.0, w * h) < 0.45:
            continue
        theta = math.radians(ang) if w >= h else math.radians(ang + 90)
        out.append({
            "pts": pts, "area": int(area), "centre": np.array([cx, cy]),
            "long": max(w, h), "short": max(1.0, min(w, h)),
            "dir": np.array([math.cos(theta), math.sin(theta)]),
            "box": cv2.boxPoints(((cx, cy), (w, h), ang)),
        })
    return out


def _cable_axis(bands: list) -> np.ndarray:
    """Unit vector along the cable, from the stripes themselves.

    Several stripes lie in a line along the cable. A single stripe crosses
    it, so the cable runs along its short side.
    """
    if len(bands) >= 2:
        cs = np.array([b["centre"] for b in bands], np.float32)
        vx, vy, _, _ = cv2.fitLine(cs, cv2.DIST_L2, 0, 0.01, 0.01).ravel()
        axis = np.array([vx, vy])
    else:
        d = bands[0]["dir"]
        axis = np.array([-d[1], d[0]])
    # Fix the sign so a taught axis and a live one compare
    if axis[0] < 0 or (axis[0] == 0 and axis[1] < 0):
        axis = -axis
    return axis / np.linalg.norm(axis)


def _runs(bands: list, axis: np.ndarray) -> list:
    """Stretches along the cable that are painted, as (start, end) in pixels.

    All stripe pixels are projected onto the cable axis. A run is one stripe,
    or several that touch. A shine streak along the cable that splits a stripe
    in two doesn't split its run, because both halves project to the same
    positions.
    """
    pts = np.vstack([b["pts"] for b in bands])
    t = pts @ axis
    bins = np.bincount((t - t.min()).astype(int)).astype(float)
    bins = np.convolve(bins, np.ones(3) / 3, mode="same")
    # A position belongs to a stripe only if paint covers a fair share of the
    # cable's width there, not a stray pixel at an edge
    reach = np.median([b["long"] for b in bands])
    covered = bins >= max(1.5, reach * 0.3)

    runs = []
    start = None
    for i, c in enumerate(np.append(covered, False)):
        if c and start is None:
            start = i
        elif not c and start is not None:
            runs.append((float(start), float(i)))
            start = None
    return runs


def _count(runs: list, stripe_width: float, pitch: float = 0.0) -> Tuple[int, float]:
    """(stripes, group length) from the runs.

    A run of k touching stripes is k stripes plus the k-1 gaps they nearly
    closed, so with the taught pitch (one stripe plus one gap) it holds
    (length + gap) / pitch stripes. Without a pitch, as when teaching, the
    run is measured in stripe widths instead.
    """
    # Slivers much thinner than a stripe are paint edges, not stripes
    runs = [r for r in runs if r[1] - r[0] > stripe_width * 0.35]
    if not runs:
        return 0, 0.0
    if pitch > stripe_width:
        gap = pitch - stripe_width
        sizes = [(e - s + gap) / pitch for s, e in runs]
    else:
        sizes = [(e - s) / stripe_width for s, e in runs]
    count = sum(max(1, int(round(k))) for k in sizes)
    return count, runs[-1][1] - runs[0][0]


# ── Model storage ───────────────────────────────────────────────────────────

def _model_path(part_number: str) -> str:
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', part_number)
    return os.path.join(_MODELS_DIR, f"{safe}.json")


def load_model(part_number: str) -> Optional[dict]:
    path = _model_path(part_number)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def has_model(part_number: str) -> bool:
    return load_model(part_number) is not None


def taught_parts() -> List[str]:
    """Part numbers with a stripe model, as stored in the files.

    Read from inside each file, because a file name has had characters that
    are not allowed in file names replaced.
    """
    if not os.path.isdir(_MODELS_DIR):
        return []
    parts = []
    for name in sorted(os.listdir(_MODELS_DIR)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(_MODELS_DIR, name), "r") as f:
                pno = json.load(f).get("part_number")
        except (OSError, ValueError):
            continue
        if pno:
            parts.append(pno)
    return parts


def save_model(model: dict):
    os.makedirs(_MODELS_DIR, exist_ok=True)
    with open(_model_path(model["part_number"]), "w") as f:
        json.dump(model, f, indent=4)
        f.write("\n")


def delete_model(part_number: str):
    path = _model_path(part_number)
    if os.path.exists(path):
        os.remove(path)


# ── Camera ──────────────────────────────────────────────────────────────────

def camera_settings() -> Tuple[int, int, int]:
    """(index, width, height) of camera 2; index -1 when none is chosen."""
    return load_camera_config(CAMERA_SOURCE)


def capture_frame() -> Optional[np.ndarray]:
    index, width, height = camera_settings()
    if index < 0:
        return None
    return camera.grab(index, width, height)


# ── Analysis ────────────────────────────────────────────────────────────────

def _crop(frame: np.ndarray, roi: dict, margin: float) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
    fh, fw = frame.shape[:2]
    mx, my = int(roi["width"] * margin), int(roi["height"] * margin)
    x0 = max(0, roi["x"] - mx)
    y0 = max(0, roi["y"] - my)
    x1 = min(fw, roi["x"] + roi["width"] + mx)
    y1 = min(fh, roi["y"] + roi["height"] + my)
    return frame[y0:y1, x0:x1], (x0, y0, x1 - x0, y1 - y0)


def _colour_bands(masks: dict, colour: str) -> list:
    """Bands of one colour. White ones lying on coloured paint are its shine."""
    mask = masks[colour]
    if colour == "white":
        mask = cv2.bitwise_and(mask, cv2.bitwise_not(_paint_mask(masks)))
    return _bands(mask)


def dominant_colour(patch: np.ndarray) -> Tuple[Optional[str], int]:
    """The stripe colour covering most of a patch, and how many pixels it has.

    Only band-shaped patches count, so a background colour filling the whole
    patch, or scattered specks, don't win.
    """
    masks = colour_masks(patch)
    patch_area = patch.shape[0] * patch.shape[1]
    best, best_area = None, 0
    for colour in COLOURS:
        bands = [b for b in _colour_bands(masks, colour) if b["area"] < patch_area * 0.5]
        if not bands:
            continue
        biggest = max(b["area"] for b in bands)
        # Specks next to real stripes are far smaller than them
        area = sum(b["area"] for b in bands if b["area"] >= biggest * 0.25)
        if area > best_area:
            best, best_area = colour, area
    return best, best_area


def read_stripes(frame: np.ndarray, roi: dict, colour: Optional[str] = None,
                 count: Optional[int] = None) -> Tuple[dict, List[np.ndarray]]:
    """Read a good part's stripes from the box, without saving anything.

    Returns the model it would save (less the part number) and the outline
    of each stripe in frame coordinates, so the operator can see what was
    found before saving. `colour` and `count` override what was read from the
    image. Raises ValueError with an operator-readable reason when no stripes
    are found.
    """
    if roi["width"] < 8 or roi["height"] < 8:
        raise ValueError("The box is too small. Draw it round the whole stripe group.")
    patch, (x0, y0, _, _) = _crop(frame, roi, 0.0)

    if colour is None:
        colour, _ = dominant_colour(patch)
        if colour is None:
            raise ValueError("No stripe colour found in the box.")
    elif colour not in COLOURS:
        raise ValueError(f"Unknown stripe colour '{colour}'.")

    masks = colour_masks(patch)
    bands = _colour_bands(masks, colour)
    if not bands:
        raise ValueError(f"No {colour} stripes found in the box.")
    biggest = max(b["area"] for b in bands)
    # The box is drawn round the stripes, so anything far smaller is a speck
    bands = [b for b in bands if b["area"] >= biggest * 0.25]

    axis = _cable_axis(bands)
    runs = _runs(bands, axis)
    # Most runs are single stripes, so the middle length is one stripe's
    # width. If they all touch, the operator corrects the count.
    width = float(np.median([e - s for s, e in runs]))
    found, span = _count(runs, width)
    if found == 0:
        raise ValueError(f"No {colour} stripes found in the box.")
    count = int(count) if count else found
    if count != found:
        # The operator corrected the count, so the runs weren't single
        # stripes. Share the group out evenly; the gaps can't be told apart
        # from the paint, so they count as part of each stripe.
        width = span / count

    model = {
        "roi": {k: int(roi[k]) for k in ("x", "y", "width", "height")},
        "frame_size": [int(frame.shape[1]), int(frame.shape[0])],
        "colour": colour,
        "count": count,
        "axis": [float(axis[0]), float(axis[1])],
        "stripe_width": width,
        "stripe_length": float(np.median([b["long"] for b in bands])),
        "span": span,
        # Start of one stripe to the start of the next
        "pitch": (span - width) / (count - 1) if count > 1 and count == found else 0.0,
        "size_tolerance": SIZE_TOLERANCE,
    }
    boxes = [b["box"] + np.array([x0, y0], np.float32) for b in bands]
    return model, boxes


def teach(part_number: str, frame: np.ndarray, roi: dict,
          colour: Optional[str] = None, count: Optional[int] = None) -> dict:
    """Read a good part's stripes from the box and save them for the part."""
    model, _ = read_stripes(frame, roi, colour, count)
    model = dict(part_number=part_number,
                 created=time.strftime("%Y-%m-%dT%H:%M:%S"), **model)
    save_model(model)
    return model


def _within(value: float, taught: float, tolerance: float) -> bool:
    """`value` is within `tolerance` of `taught`, either way round.

    0.4 accepts 0.6x to 1/0.6 = 1.67x, so a stripe half again as big passes
    as readily as one a third smaller.
    """
    return taught > 0 and (1 - tolerance) <= value / taught <= 1 / (1 - tolerance)


def _judge(patch: np.ndarray, masks: dict, colour: str, model: dict) -> Tuple[int, bool, list]:
    """(count, matches the taught part, bands used) for one colour."""
    tol = model.get("size_tolerance", SIZE_TOLERANCE)
    length = model["stripe_length"]
    bands = [b for b in _colour_bands(masks, colour) if _within(b["long"], length, tol)]
    if not bands:
        return 0, False, []
    found, span = _count(_runs(bands, np.array(model["axis"])),
                         model["stripe_width"], model.get("pitch", 0.0))
    expected = model["count"]
    # One stripe has no group length to compare; its size already matched
    span_ok = expected == 1 or _within(span, model["span"], tol)
    return found, found == expected and span_ok, bands


def inspect(part_number: str, frame: Optional[np.ndarray] = None) -> StripeResult:
    """Check one part's stripes.

    By default grabs a fresh frame from camera 2. Pass `frame` to judge a
    still image instead.
    """
    start = time.time()

    def _error(msg: str) -> StripeResult:
        return StripeResult(
            ok=False, judgement="ERROR", part_number=part_number, error=msg,
            processing_time_ms=int((time.time() - start) * 1000),
        )

    model = load_model(part_number)
    if model is None:
        return _error(f"No stripe model for '{part_number}'")

    if frame is None:
        frame = capture_frame()
        if frame is None:
            return _error("Camera 2 not available")

    fw, fh = model.get("frame_size", [frame.shape[1], frame.shape[0]])
    if (frame.shape[1], frame.shape[0]) != (fw, fh):
        return _error(f"Camera 2 gives {frame.shape[1]}x{frame.shape[0]} frames, "
                      f"but this part was taught at {fw}x{fh}. Re-teach it.")

    patch, search = _crop(frame, model["roi"], SEARCH_MARGIN)
    masks = colour_masks(patch)
    colour, expected = model["colour"], model["count"]
    count, ok, bands = _judge(patch, masks, colour, model)

    seen = f"{colour} x{count}" if count else "none"
    if not ok:
        # Name what is there instead, so the operator sees "red x3", not just NG
        for other in COLOURS:
            if other == colour:
                continue
            n, other_ok, _ = _judge(patch, masks, other, model)
            if n and (other_ok or not count):
                seen = f"{other} x{n}"
                if other_ok:
                    break

    x0, y0 = search[0], search[1]
    boxes = [b["box"] + np.array([x0, y0], np.float32) for b in bands]
    elapsed = int((time.time() - start) * 1000)
    return StripeResult(
        ok=ok, judgement="OK" if ok else "NG", part_number=part_number,
        expected_colour=colour, expected_count=expected, count=count, seen=seen,
        processing_time_ms=elapsed,
        error=None if ok else f"Expected {colour} x{expected}, found {seen}",
        stripe_boxes=boxes, search_box=search, frame=frame,
    )
