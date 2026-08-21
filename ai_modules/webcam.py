import logging
import platform
import threading
from typing import Optional

import cv2
import numpy as np


logger = logging.getLogger(__name__)


def _create_capture():
    if platform.system() == "Windows":
        return cv2.VideoCapture(0, cv2.CAP_DSHOW)
    return cv2.VideoCapture(0)


def _create_placeholder_frame(message: str) -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(frame, message, (32, 230), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    cv2.putText(frame, "ExamGuard AI", (32, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (91, 192, 235), 2)
    return frame


def _encode_frame(frame: np.ndarray) -> bytes:
    success, buffer = cv2.imencode(".jpg", frame)
    if not success:
        raise RuntimeError("Failed to encode frame as JPEG.")
    return buffer.tobytes()


def probe_camera_available() -> bool:
    capture = _create_capture()
    try:
        return bool(capture and capture.isOpened())
    finally:
        if capture is not None:
            capture.release()


class WebcamStream:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.cap: Optional[cv2.VideoCapture] = None
        self.thread: Optional[threading.Thread] = None
        self.frame: Optional[np.ndarray] = None
        self._running = False
        self._released = True
        self._release_lock = threading.Lock()

    def start(self):
        if self.is_running():
            return

        with self._release_lock:
            self.stop_event.clear()
            self.cap = _create_capture()
            if self.cap is None or not self.cap.isOpened():
                logger.error("Camera Status: Not Found")
                self._running = False
                self._released = True
                if self.cap is not None:
                    self.cap.release()
                self.cap = None
                return

            self._running = True
            self._released = False
            self.thread = threading.Thread(target=self._read_frames, daemon=True)
            self.thread.start()

    def _read_frames(self):
        while not self.stop_event.is_set() and self.cap is not None:
            success, frame = self.cap.read()
            if not success:
                logger.error("Failed to read frame from camera.")
                self.stop_event.set()
                self._running = False
                break

            with self.lock:
                self.frame = frame

        self._running = False

    def stop(self):
        self.stop_event.set()

        thread = self.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)

        with self._release_lock:
            if self.cap is not None and not self._released:
                self.cap.release()
                self._released = True
            self.cap = None
            self.thread = None
            self._running = False
            cv2.destroyAllWindows()

    def get_frame(self):
        with self.lock:
            if self.frame is None:
                return None
            return self.frame.copy()

    def is_running(self):
        return self._running and not self.stop_event.is_set()

    def get_frame_bytes(self):
        frame = self.get_frame()
        if frame is None:
            return None
        return _encode_frame(frame)

    def get_placeholder_frame_bytes(self, message: str = "Camera Status: Not Found") -> bytes:
        return _encode_frame(_create_placeholder_frame(message))


_webcam_stream: Optional[WebcamStream] = None
_webcam_stream_lock = threading.Lock()


def get_webcam_stream() -> WebcamStream:
    global _webcam_stream
    with _webcam_stream_lock:
        if _webcam_stream is None:
            _webcam_stream = WebcamStream()
        return _webcam_stream


def get_camera_status_label() -> str:
    return "Camera Status: Ready" if probe_camera_available() else "Camera Status: Not Found"
