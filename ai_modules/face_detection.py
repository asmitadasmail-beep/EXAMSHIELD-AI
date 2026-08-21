import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import cv2

logger = logging.getLogger(__name__)

try:
    import mediapipe as mp
except Exception:  # pragma: no cover - import guard for environments without MediaPipe
    mp = None

try:
    from mediapipe.solutions import face_detection as mp_face_detection
except Exception:  # pragma: no cover - this venv ships MediaPipe 1.0 without solutions
    mp_face_detection = None

try:
    from mediapipe.tasks.python.core.base_options import BaseOptions
    from mediapipe.tasks.python.vision.core.vision_task_running_mode import VisionTaskRunningMode
    from mediapipe.tasks.python.vision.face_detector import (
        FaceDetector as MpTaskFaceDetector,
        FaceDetectorOptions,
    )
except Exception:  # pragma: no cover - task API is optional fallback support
    BaseOptions = None
    VisionTaskRunningMode = None
    MpTaskFaceDetector = None
    FaceDetectorOptions = None


@dataclass
class FaceBoundingBox:
    x: int
    y: int
    width: int
    height: int


class FaceDetector:
    def __init__(self, min_detection_confidence: float = 0.5):
        self.min_detection_confidence = min_detection_confidence
        self._backend = "none"
        self._solutions_detector = None
        self._task_detector = None
        self._cascade = None

        if mp_face_detection is not None:
            try:
                self._solutions_detector = mp_face_detection.FaceDetection(
                    model_selection=0,
                    min_detection_confidence=min_detection_confidence,
                )
                self._backend = "mediapipe_solutions"
                return
            except Exception as e:
                logger.warning(f"MediaPipe solutions face detector failed to initialize: {e}")

        model_path = self._find_task_model()
        if (
            MpTaskFaceDetector is not None
            and FaceDetectorOptions is not None
            and BaseOptions is not None
            and VisionTaskRunningMode is not None
            and model_path is not None
        ):
            try:
                options = FaceDetectorOptions(
                    base_options=BaseOptions(model_asset_path=str(model_path)),
                    running_mode=VisionTaskRunningMode.IMAGE,
                    min_detection_confidence=min_detection_confidence,
                )
                self._task_detector = MpTaskFaceDetector.create_from_options(options)
                self._backend = "mediapipe_tasks"
                return
            except Exception as e:
                logger.warning(f"MediaPipe tasks face detector failed to initialize: {e}")

        cascade_path = self._find_cascade_model()
        if hasattr(cv2, "CascadeClassifier") and cascade_path is not None:
            try:
                classifier = cv2.CascadeClassifier(str(cascade_path))
                if not classifier.empty():
                    self._cascade = classifier
                    self._backend = "opencv"
                    return
            except Exception as e:
                logger.warning(f"OpenCV CascadeClassifier failed to load: {e}")

        self._backend = "none"
        logger.warning("No face detection backend available.")

    def get_backend(self) -> str:
        return self._backend

    def _find_task_model(self) -> Optional[Path]:
        env_path = os.getenv("MEDIAPIPE_FACE_DETECTOR_MODEL")
        if env_path:
            candidate = Path(env_path)
            if candidate.exists():
                return candidate

        project_root = Path(__file__).resolve().parents[1]
        candidate = project_root / "models" / "blaze_face_short_range.tflite"
        if candidate.exists():
            return candidate

        return None

    def _find_cascade_model(self) -> Optional[Path]:
        candidates = []
        project_root = Path(__file__).resolve().parents[1]
        candidates.append(project_root / "models" / "haarcascade_frontalface_default.xml")

        if hasattr(cv2, "data") and hasattr(cv2.data, "haarcascades"):
            candidates.append(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")

        try:
            cv2_dir = Path(cv2.__file__).resolve().parent
            candidates.append(cv2_dir / "data" / "haarcascade_frontalface_default.xml")
        except Exception:
            pass

        for candidate in candidates:
            if candidate.exists():
                return candidate

        return None

    def detect(self, frame) -> Tuple[bool, Optional[dict], float]:
        if frame is None or self._backend == "none":
            return False, None, 0.0

        if self._backend == "mediapipe_solutions" and self._solutions_detector is not None:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self._solutions_detector.process(rgb_frame)
            detections = results.detections or []
            if not detections:
                return False, None, 0.0

            detection = detections[0]
            bbox = detection.location_data.relative_bounding_box
            image_height, image_width = frame.shape[:2]
            x = max(int(bbox.xmin * image_width), 0)
            y = max(int(bbox.ymin * image_height), 0)
            width = max(int(bbox.width * image_width), 0)
            height = max(int(bbox.height * image_height), 0)
            confidence = float(detection.score[0]) if detection.score else 0.0

            return True, FaceBoundingBox(x=x, y=y, width=width, height=height).__dict__, confidence

        if self._backend == "mediapipe_tasks" and self._task_detector is not None:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
            result = self._task_detector.detect(mp_image)
            detections = result.detections or []
            if not detections:
                return False, None, 0.0

            detection = detections[0]
            bbox = detection.bounding_box
            confidence = float(detection.categories[0].score) if detection.categories else 0.0

            return (
                True,
                FaceBoundingBox(
                    x=int(bbox.origin_x),
                    y=int(bbox.origin_y),
                    width=int(bbox.width),
                    height=int(bbox.height),
                ).__dict__,
                confidence,
            )

        if self._backend == "opencv" and self._cascade is not None:
            gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self._cascade.detectMultiScale(gray_frame, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
            if len(faces) == 0:
                return False, None, 0.0

            x, y, width, height = faces[0]
            return True, FaceBoundingBox(x=int(x), y=int(y), width=int(width), height=int(height)).__dict__, 0.8

        return False, None, 0.0

