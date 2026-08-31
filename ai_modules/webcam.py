import logging
import platform
import threading
from typing import Optional

import cv2
import numpy as np


logger = logging.getLogger(__name__)


def _create_capture():
    if platform.system() == "Windows":
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return cap
    cap = cv2.VideoCapture(0)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def _create_placeholder_frame(message: str) -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(frame, message, (32, 230), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    cv2.putText(frame, "ExamShield AI", (32, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (91, 192, 235), 2)
    return frame


def _encode_frame(frame: np.ndarray) -> bytes:
    success, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not success:
        raise RuntimeError("Failed to encode frame as JPEG.")
    return buffer.tobytes()


def probe_camera_available() -> bool:
    capture = None
    try:
        capture = _create_capture()
        return bool(capture and capture.isOpened())
    except Exception as error:
        logger.error("Camera probe failed: %s", error)
        return False
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
        self._last_status = "Camera Off"

    def start(self):
        if self.is_running():
            return

        with self._release_lock:
            self.stop_event.clear()
            self._last_status = "Starting"
            try:
                self.cap = _create_capture()
            except Exception as error:
                logger.error("Camera could not be opened: %s", error)
                self.cap = None
                self._running = False
                self._released = True
                self._last_status = "Error"
                return

            if self.cap is None or not self.cap.isOpened():
                logger.error("Camera Status: Not Found")
                self._running = False
                self._released = True
                self._last_status = "Camera Not Found"
                if self.cap is not None:
                    self.cap.release()
                self.cap = None
                return

            # Test reading one initial frame
            ret, initial_frame = self.cap.read()
            if ret and initial_frame is not None:
                with self.lock:
                    self.frame = initial_frame

            self._running = True
            self._released = False
            self._last_status = "Ready"
            self.thread = threading.Thread(target=self._read_frames, daemon=True)
            self.thread.start()
            logger.info("WebcamStream started successfully.")

    def _read_frames(self):
        while not self.stop_event.is_set() and self.cap is not None:
            try:
                success, frame = self.cap.read()
                if not success or frame is None:
                    logger.warning("Failed to read frame from camera.")
                    time.sleep(0.01)
                    continue

                with self.lock:
                    self.frame = frame
            except Exception as e:
                logger.error("Error reading camera frame: %s", e)
                time.sleep(0.02)

        self._running = False

    def stop(self):
        self.stop_event.set()

        thread = self.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)

        with self._release_lock:
            if self.cap is not None and not self._released:
                try:
                    self.cap.release()
                except Exception:
                    pass
                self._released = True
            self.cap = None
            self.thread = None
            self._running = False
            self._last_status = "Camera Off"

    def get_frame(self):
        with self.lock:
            if self.frame is None:
                return None
            return self.frame.copy()

    def is_running(self):
        return self._running and not self.stop_event.is_set()

    def get_status(self) -> str:
        if self.is_running():
            return "Ready"
        return self._last_status

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
