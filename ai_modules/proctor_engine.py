from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import cv2

import config
from ai_modules.face_detection import FaceDetector
from ai_modules.webcam import WebcamStream


logger = logging.getLogger(__name__)


class ProctorEngine:
    def __init__(self):
        self.webcam = WebcamStream()
        self.face_detector = FaceDetector()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

        self.session_id: Optional[int] = None
        self.face_missing_alert = False
        self._missing_face_started_at: Optional[float] = None
        self._running = False
        self.latest_processed_frame = None

    def start(self, session_id: int) -> None:
        with self.lock:
            if self._running:
                return

            self.session_id = session_id
            self.face_missing_alert = False
            self._missing_face_started_at = None
            self.stop_event.clear()

            self.webcam.start()
            if not self.webcam.is_running():
                logger.error("Webcam failed to start.")
                self._running = False
                return

            self._running = True
            self.thread = threading.Thread(target=self._process_loop, daemon=True)
            self.thread.start()

    def _process_loop(self) -> None:
        while not self.stop_event.is_set() and self.webcam.is_running():
            frame = self.webcam.get_frame()
            if frame is None:
                time.sleep(0.01)
                continue

            try:
                processed_frame = self.process_frame(frame)
            except Exception as e:
                logger.error(f"Error during frame processing: {e}", exc_info=True)
                processed_frame = frame.copy()

            with self.lock:
                self.latest_processed_frame = processed_frame

            time.sleep(0.02)

        with self.lock:
            self._running = False

    def process_frame(self, frame):
        backend = self.face_detector.get_backend()
        now = time.monotonic()

        with self.lock:
            if backend == "none":
                cv2.putText(
                    frame,
                    "AI Detector Unavailable",
                    (24, 42),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 165, 255),
                    2,
                )
                return frame

            face_present, bbox, confidence = self.face_detector.detect(frame)

            if face_present:
                self._missing_face_started_at = None
                self.face_missing_alert = False

                if bbox is not None:
                    x = bbox["x"]
                    y = bbox["y"]
                    width = bbox["width"]
                    height = bbox["height"]
                    cv2.rectangle(frame, (x, y), (x + width, y + height), (0, 255, 0), 2)
                    label = f"Face {confidence:.2f}"
                    cv2.putText(
                        frame,
                        label,
                        (x, max(y - 10, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        (0, 255, 0),
                        2,
                    )
            else:
                if self._missing_face_started_at is None:
                    self._missing_face_started_at = now

                missing_duration = now - self._missing_face_started_at
                if missing_duration >= config.FACE_MISSING_SEC:
                    self.face_missing_alert = True

                cv2.putText(
                    frame,
                    "No face detected",
                    (24, 42),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.9,
                    (0, 0, 255),
                    2,
                )

        return frame

    def stop(self) -> None:
        self.stop_event.set()

        thread = self.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

        self.webcam.stop()

        with self.lock:
            self._running = False
            self.thread = None
            self.latest_processed_frame = None

    def is_running(self) -> bool:
        return self._running and self.webcam.is_running()

    def get_latest_frame_bytes(self) -> Optional[bytes]:
        with self.lock:
            if self.latest_processed_frame is None:
                return None
            success, buffer = cv2.imencode(".jpg", self.latest_processed_frame)
            if not success:
                return None
            return buffer.tobytes()

    def get_status(self) -> dict:
        with self.lock:
            running = self.is_running()
            camera_status = "Camera Ready" if running else "Camera Off"
            backend = self.face_detector.get_backend()

            if not running:
                ai_status = "Monitoring"
            elif backend == "none":
                ai_status = "AI Detector Unavailable"
            elif self.face_missing_alert:
                ai_status = "Face Not Detected"
            else:
                ai_status = "Monitoring"

            return {
                "running": running,
                "camera_status": camera_status,
                "ai_status": ai_status,
                "face_missing_alert": self.face_missing_alert,
                "session_id": self.session_id,
            }
