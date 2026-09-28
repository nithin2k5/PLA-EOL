"""
vision_engine/vision_controller.py
==================================
Webcam-based part verification using Template Matching (Normalized Cross-Correlation).

Methodology:
  - Teach: the operator collects reference images and boxes the part in each one.
    The image patch inside each box becomes a 'Template'. Boxes are per image,
    because a single box applied to every reference crops background wherever the
    part happened to sit elsewhere — and a background template matches the live
    background, which would pass an empty fixture.
  - Inspect: capture a live frame and search for the Template across it with
    cv2.matchTemplate.
  - Result: if the best match score >= threshold the part is present and correct.

Storage:
  Everything lives in the EOL database, next to the test data it gates:
  TBL_VISION_SETTINGS holds the settings and camera choice as key/value rows,
  and TBL_VISION_MODEL holds one row per taught part, its templates packed
  into VM_MODEL as a compressed NumPy archive.
"""
import io
import json
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

from . import camera

DEFAULT_MATCH_THRESHOLD = 0.75


class VisionStorageError(OSError):
    """The vision tables could not be read or written.

    An OSError, so callers that already guard their saves against a disk
    error cover the database the same way.
    """


@dataclass
class VisionResult:
    """Structured result from a vision inspection."""
    ok: bool
    judgement: str              # "OK", "NG", "ERROR"
    part_number: str = ""
    match_score: float = 0.0    # 1.0 = perfect match, lower = worse
    threshold: float = 0.0
    processing_time_ms: int = 0
    error: Optional[str] = None

    # Diagnostics for the settings-page test view. Production ignores these;
    # they exist so the UI can show *where* the match landed instead of only a
    # number, without duplicating the matching logic outside inspect().
    match_box: Optional[Tuple[int, int, int, int]] = None   # (x, y, w, h) in frame
    frame: Optional[np.ndarray] = None                      # frame that was judged


# ── Database access ────────────────────────────────────────────────────────────

_tables_ready = False


def _run(sql: str, params: tuple = (), fetch: bool = False):
    """Run one statement on its own connection. Returns the rows when `fetch`."""
    global _tables_ready
    import db
    import mysql.connector

    try:
        if not _tables_ready:
            # Creates only what is missing, so a machine that has not been
            # restarted since the vision tables were added still gets them.
            db.init_database(raise_on_error=True)
            _tables_ready = True
        conn = db.connect()
        try:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall() if fetch else None
            conn.commit()
            cursor.close()
            return rows
        finally:
            conn.close()
    except mysql.connector.Error as e:
        raise VisionStorageError(str(e)) from e


def _read_settings() -> Dict[str, str]:
    """Every stored setting, or an empty dict when the database is unreachable.

    Reading falls back to defaults rather than failing: a machine with its
    database down should report "no camera" or "no model", not crash.
    """
    try:
        rows = _run("SELECT VS_KEY, VS_VALUE FROM TBL_VISION_SETTINGS", fetch=True)
    except VisionStorageError as e:
        print(f"Vision: could not read settings: {e}")
        return {}
    return {key: value for key, value in rows}


def _write_settings(values: Dict[str, object]):
    for key, value in values.items():
        _run("INSERT INTO TBL_VISION_SETTINGS (VS_KEY, VS_VALUE) VALUES (%s, %s) AS new "
             "ON DUPLICATE KEY UPDATE VS_VALUE = new.VS_VALUE",
             (key, str(value)))


def _int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _float(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


# ── Settings ───────────────────────────────────────────────────────────────────

def _default_config() -> dict:
    return {
        "vision_enabled": True,
        "camera_source": "cam1",
        "match_threshold": DEFAULT_MATCH_THRESHOLD,
    }


def load_vision_config() -> dict:
    cfg = _default_config()
    stored = _read_settings()
    if "vision_enabled" in stored:
        cfg["vision_enabled"] = stored["vision_enabled"].strip().lower() in ("1", "true", "yes")
    if stored.get("camera_source"):
        cfg["camera_source"] = stored["camera_source"]
    if "match_threshold" in stored:
        cfg["match_threshold"] = _float(stored["match_threshold"], DEFAULT_MATCH_THRESHOLD)
    return cfg


def save_vision_config(cfg: dict):
    _write_settings({
        "vision_enabled": 1 if cfg.get("vision_enabled", True) else 0,
        "camera_source": cfg.get("camera_source", "cam1"),
        "match_threshold": float(cfg.get("match_threshold", DEFAULT_MATCH_THRESHOLD)),
    })


def load_camera_config(source: str) -> Tuple[int, int, int]:
    """(index, width, height) saved for a camera source such as "cam1".

    An index of -1 means no device has been chosen for that source yet.
    """
    stored = _read_settings()
    return (
        _int(stored.get(f"{source}_index"), -1),
        _int(stored.get(f"{source}_width"), 640),
        _int(stored.get(f"{source}_height"), 480),
    )


def save_camera_config(source: str, index: int, width: int, height: int):
    """Store the device and resolution for one camera source."""
    _write_settings({
        f"{source}_index": int(index),
        f"{source}_width": int(width),
        f"{source}_height": int(height),
    })


def get_vision_controller() -> "VisionController":
    return VisionController()


# ── Model packing ──────────────────────────────────────────────────────────────

def _pack_model(model_cfg: dict, templates: List[np.ndarray]) -> bytes:
    buffer = io.BytesIO()
    arrays = {"config": np.array(json.dumps(model_cfg))}
    for i, t in enumerate(templates):
        arrays[f"template_{i}"] = t
    np.savez_compressed(buffer, **arrays)
    return buffer.getvalue()


def _unpack_model(blob: bytes) -> Tuple[dict, List[np.ndarray]]:
    data = np.load(io.BytesIO(blob), allow_pickle=False)
    model_cfg = json.loads(str(data["config"]))
    templates = []
    i = 0
    while f"template_{i}" in data:
        templates.append(data[f"template_{i}"])
        i += 1
    return model_cfg, templates


class VisionController:
    """Headless part verification against taught templates."""

    def __init__(self):
        self.config = load_vision_config()
        self._model_cache: Dict[str, dict] = {}

    def reload_config(self):
        self.config = load_vision_config()
        self._model_cache.clear()

    # ── Camera ──────────────────────────────────────────────────────────────

    def cam_settings(self) -> tuple:
        """(index, width, height) for the configured camera source."""
        return load_camera_config(self.config.get("camera_source", "cam1"))

    def _capture_frame(self) -> Optional[np.ndarray]:
        index, width, height = self.cam_settings()
        if index < 0:
            return None
        return camera.grab(index, width, height)

    def get_status(self) -> str:
        index, width, height = self.cam_settings()
        if index < 0:
            return "NO_CAMERA"
        return "READY" if camera.grab(index, width, height) is not None else "CAMERA_ERROR"

    # ── Model I/O ───────────────────────────────────────────────────────────

    def _load_model(self, part_number: str) -> Optional[dict]:
        """The taught model, None when there is none or it cannot be decoded.

        Raises VisionStorageError when the database cannot be reached, so an
        outage is not mistaken for a part that was never taught.
        """
        if part_number in self._model_cache:
            return self._model_cache[part_number]

        rows = _run("SELECT VM_MODEL FROM TBL_VISION_MODEL WHERE VM_PART_NUMBER = %s",
                    (part_number,), fetch=True)
        if not rows:
            return None

        try:
            model_cfg, templates = _unpack_model(bytes(rows[0][0]))
        except (OSError, ValueError, KeyError):
            return None

        model_cfg["templates"] = templates
        self._model_cache[part_number] = model_cfg
        return model_cfg

    def has_model(self, part_number: str) -> bool:
        rows = _run("SELECT 1 FROM TBL_VISION_MODEL WHERE VM_PART_NUMBER = %s",
                    (part_number,), fetch=True)
        return bool(rows)

    def get_mapped_parts(self) -> dict:
        """{part number: where its model is kept} for every taught part.

        Every model is a row of TBL_VISION_MODEL, so that is what each part
        maps to. Empty when the database cannot be reached.
        """
        try:
            rows = _run("SELECT VM_PART_NUMBER FROM TBL_VISION_MODEL "
                        "ORDER BY VM_PART_NUMBER", fetch=True)
        except VisionStorageError as e:
            print(f"Vision: could not list taught parts: {e}")
            return {}
        return {row[0]: "TBL_VISION_MODEL" for row in rows}

    # ── Production Inspection ───────────────────────────────────────────────

    def inspect(self, part_number: str, frame: Optional[np.ndarray] = None) -> VisionResult:
        """Judge one frame against a taught part.

        By default captures a fresh frame from the configured camera — the
        same path production uses. Pass `frame` to judge a still image
        instead, without needing the part in front of a camera at all.
        """
        start = time.time()

        def _error(msg: str) -> VisionResult:
            return VisionResult(
                ok=False, judgement="ERROR", part_number=part_number, error=msg,
                processing_time_ms=int((time.time() - start) * 1000),
            )

        if not self.config.get("vision_enabled", True):
            return _error("Vision inspection disabled")

        try:
            model = self._load_model(part_number)
        except VisionStorageError as e:
            return _error(f"Vision database unavailable: {e}")
        if model is None:
            return _error(f"No vision model found for '{part_number}'")

        templates = model.get("templates", [])
        if not templates:
            return _error("Model contains no templates")

        if frame is None:
            frame = self._capture_frame()
            if frame is None:
                return _error("Camera not available")

        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        best_score = -1.0
        best_box = None
        compared = 0
        for template in templates:
            if template.shape[0] > gray_frame.shape[0] or template.shape[1] > gray_frame.shape[1]:
                continue
            res = cv2.matchTemplate(gray_frame, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val > best_score:
                best_score = max_val
                best_box = (max_loc[0], max_loc[1], template.shape[1], template.shape[0])
            compared += 1

        print(f"[VISION DEBUG] pno={part_number} frame={gray_frame.shape[1]}x{gray_frame.shape[0]} "
              f"brightness={gray_frame.mean():.1f} templates={[(t.shape[1], t.shape[0]) for t in templates]} "
              f"compared={compared} best_score={best_score:.3f}")

        if compared == 0:
            return _error(
                f"Every template is larger than this frame "
                f"({gray_frame.shape[1]}x{gray_frame.shape[0]}) — "
                f"re-teach the part at this resolution, or use a larger test image"
            )

        threshold = model.get("match_threshold", self.config.get("match_threshold", DEFAULT_MATCH_THRESHOLD))
        elapsed = int((time.time() - start) * 1000)

        if best_score >= threshold:
            return VisionResult(
                ok=True, judgement="OK", part_number=part_number,
                match_score=best_score, threshold=threshold,
                processing_time_ms=elapsed,
                match_box=best_box, frame=frame,
            )
        return VisionResult(
            ok=False, judgement="NG", part_number=part_number,
            match_score=best_score, threshold=threshold,
            processing_time_ms=elapsed,
            error=f"No match found (score {best_score:.2f} < {threshold})",
            match_box=best_box, frame=frame,
        )

    # ── Model Building ──────────────────────────────────────────────────────

    def build_and_save_model(
        self, part_number: str, images: List[np.ndarray],
        roi: Union[dict, List[dict]],
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
    ) -> str:
        """Crop one template per reference image and save them as the part's model.

        `roi` is either a single box applied to every image, or one box per image.
        Per-image boxes matter whenever the part is not rigidly fixtured: a shared
        box lands on background in any reference where the part sat elsewhere, and
        a background template matches the live background at a high score — which
        would pass an empty fixture.

        Returns the part number saved.
        """
        rois = list(roi) if isinstance(roi, (list, tuple)) else [roi] * len(images)
        if len(rois) != len(images):
            raise ValueError(
                f"Got {len(rois)} regions for {len(images)} reference images."
            )

        templates = []
        for n, (img, r) in enumerate(zip(images, rois), start=1):
            if r is None:
                raise ValueError(f"Reference image {n} has no region marked.")
            x, y, w, h = r["x"], r["y"], r["width"], r["height"]
            if w < 10 or h < 10:
                raise ValueError(f"Region on reference image {n} is too small.")
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
            if y + h > gray.shape[0] or x + w > gray.shape[1]:
                raise ValueError(
                    f"Region on reference image {n} falls outside it."
                )
            templates.append(gray[y:y + h, x:x + w])

        created = time.strftime("%Y-%m-%dT%H:%M:%S")
        model_cfg = {
            "part_number": part_number,
            "created": created,
            "rois": rois,
            "roi": rois[0],          # older readers expect a single region
            "match_threshold": match_threshold,
            "num_references": len(templates),
        }

        _run("INSERT INTO TBL_VISION_MODEL "
             "(VM_PART_NUMBER, VM_MODEL, VM_THRESHOLD, VM_REFERENCES, VM_CREATED) "
             "VALUES (%s, %s, %s, %s, %s) AS new "
             "ON DUPLICATE KEY UPDATE VM_MODEL = new.VM_MODEL, "
             "VM_THRESHOLD = new.VM_THRESHOLD, VM_REFERENCES = new.VM_REFERENCES, "
             "VM_CREATED = new.VM_CREATED",
             (part_number, _pack_model(model_cfg, templates), float(match_threshold),
              len(templates), created.replace("T", " ")))
        self._model_cache.pop(part_number, None)

        return part_number

    def delete_model(self, part_number: str):
        _run("DELETE FROM TBL_VISION_MODEL WHERE VM_PART_NUMBER = %s", (part_number,))
        self._model_cache.pop(part_number, None)

    def model_info(self, part_number: str) -> Optional[dict]:
        """Metadata for a taught part, or None if it has no usable model."""
        try:
            model = self._load_model(part_number)
        except VisionStorageError:
            return None
        if model is None:
            return None
        templates = model.get("templates", [])
        roi = model.get("roi") or {}
        rois = model.get("rois") or ([roi] if roi else [])
        sizes = ([(t.shape[1], t.shape[0]) for t in templates] if templates else
                 [(r.get("width", 0), r.get("height", 0)) for r in rois])
        return {
            "references": model.get("num_references", len(templates)),
            "created": model.get("created", "—"),
            "threshold": model.get("match_threshold", self.config.get("match_threshold", DEFAULT_MATCH_THRESHOLD)),
            "roi": roi,
            "rois": rois,
            "template_size": sizes[0] if sizes else (0, 0),
            "template_sizes": sizes,
            "uniform_templates": len(set(sizes)) <= 1,
        }

    def set_model_threshold(self, part_number: str, threshold: float):
        """Rewrite a taught model's own threshold.

        A model carries the threshold it was taught with and that value wins over
        the global default at inspection time, so tuning a part has to reach into
        the stored model rather than the settings.
        """
        rows = _run("SELECT VM_MODEL FROM TBL_VISION_MODEL WHERE VM_PART_NUMBER = %s",
                    (part_number,), fetch=True)
        if not rows:
            raise ValueError(f"No vision model for '{part_number}'.")

        model_cfg, templates = _unpack_model(bytes(rows[0][0]))
        model_cfg["match_threshold"] = float(threshold)
        _run("UPDATE TBL_VISION_MODEL SET VM_MODEL = %s, VM_THRESHOLD = %s "
             "WHERE VM_PART_NUMBER = %s",
             (_pack_model(model_cfg, templates), float(threshold), part_number))
        self._model_cache.pop(part_number, None)

    def map_model_file(self, part_number: str, filename: str):
        """Kept for callers written against model files; there are none now."""
        raise ValueError("Vision models are stored in the database, "
                         "so there are no model files to map.")
