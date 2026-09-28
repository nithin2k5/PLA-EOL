"""
vision_engine/camera.py
=======================
Single owner per physical camera device.

A DirectShow device accepts only one cv2.VideoCapture at a time, so the live
preview and the inspection path must share one stream rather than each opening
the device. Consumers acquire a reference-counted stream by index; the device is
opened on the first acquire and released when the last reference goes away.
"""
import threading
import time
from typing import Optional

import cv2
import numpy as np

_REGISTRY_LOCK = threading.Lock()
_STREAMS: dict = {}

# ~1s of failed reads at the 20ms retry interval.
_MAX_CONSECUTIVE_READ_FAILURES = 50


class CameraStream:
    """Background reader for one camera index. Serves the most recent frame."""

    def __init__(self, index: int, width: int, height: int):
        self.index = index
        self._width = width
        self._height = height
        self._cap = None
        self._frame = None
        self._frames_read = 0
        self._frame_lock = threading.Lock()
        self._running = False
        self._refs = 0
        self._opened = threading.Event()
        self._open_ok = False
        self._thread = None

    # ── lifecycle ───────────────────────────────────────────────────────────

    def _start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
            self._cap = cap
            self._open_ok = True
        else:
            cap.release()
            self._running = False
        self._opened.set()

        # A device can open and then never deliver a frame (unplugged mid-session,
        # held by another process). Give up rather than let a reader block until
        # its timeout — an inspection stalling the test cycle is worse than a
        # fast "camera unavailable".
        consecutive_failures = 0
        while self._running:
            ret, frame = self._cap.read()
            if ret:
                with self._frame_lock:
                    self._frame = frame
                    self._frames_read += 1
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= _MAX_CONSECUTIVE_READ_FAILURES:
                    self._running = False
                    break
                time.sleep(0.02)

        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def wait_until_open(self, timeout: float = 5.0) -> bool:
        self._opened.wait(timeout)
        return self._open_ok

    def is_alive(self) -> bool:
        """False once the reader has given up on the device."""
        return self._running

    def release(self):
        with _REGISTRY_LOCK:
            self._refs -= 1
            if self._refs > 0:
                return
            _STREAMS.pop(self.index, None)
        self._running = False
        # Block until this device's cv2.VideoCapture is actually closed before
        # returning. DirectShow tolerates only one capture per device -- without
        # this, a caller that immediately re-acquires the same index (a new
        # CameraStream, on a new thread) can open a second cv2.VideoCapture
        # while this one is still mid-read or mid-release, which is a known way
        # to hard-crash (segfault) the process rather than raise a catchable
        # Python exception.
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    # ── frames ──────────────────────────────────────────────────────────────

    def latest(self) -> Optional[np.ndarray]:
        """Most recent frame without waiting, or None if none captured yet."""
        with self._frame_lock:
            return self._frame.copy() if self._frame is not None else None

    def read(self, timeout: float = 5.0, settle_frames: int = 30) -> Optional[np.ndarray]:
        """Newest frame, once the device has produced at least `settle_frames`.

        The settle count lets auto-exposure stabilise on a stream that has just
        been opened; an already-running preview satisfies it immediately because
        the counter is cumulative over the stream's life. 30 frames is ~1s at
        30fps -- judging a cold device sooner than that matches a template
        against a still-darkening frame and scores it far below what the same
        part scores once exposure settles.
        """
        if not self.wait_until_open(timeout):
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._frame_lock:
                if self._frames_read >= settle_frames and self._frame is not None:
                    return self._frame.copy()
            if not self._running:
                return None
            time.sleep(0.02)
        with self._frame_lock:
            return self._frame.copy() if self._frame is not None else None


def acquire(index: int, width: int = 640, height: int = 480) -> Optional[CameraStream]:
    """Reference-counted handle to the stream for `index`. Call release() when done."""
    if index < 0:
        return None
    with _REGISTRY_LOCK:
        stream = _STREAMS.get(index)
        if stream is None:
            stream = CameraStream(index, width, height)
            _STREAMS[index] = stream
            stream._refs = 1
            stream._start()
        else:
            stream._refs += 1
    return stream


def is_live(index: int) -> bool:
    with _REGISTRY_LOCK:
        return index in _STREAMS


def grab(index: int, width: int = 640, height: int = 480,
         timeout: float = 5.0) -> Optional[np.ndarray]:
    """One frame from `index`, reusing a live stream if one is already open."""
    stream = acquire(index, width, height)
    if stream is None:
        return None
    try:
        return stream.read(timeout=timeout)
    finally:
        stream.release()
