from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Sequence, Tuple

import cv2
import numpy as np

import config
from utils.settings_store import get_settings


logger = logging.getLogger(__name__)

try:
    import mediapipe as mp
except Exception:  # pragma: no cover
    mp = None

# MediaPipe Tasks Vision imports (MediaPipe 0.10+ / 1.0+)
try:
    from mediapipe.tasks.python.core.base_options import BaseOptions
    from mediapipe.tasks.python.vision.core.vision_task_running_mode import VisionTaskRunningMode
    from mediapipe.tasks.python.vision.face_landmarker import (
        FaceLandmarker as MpTaskFaceLandmarker,
        FaceLandmarkerOptions,
    )
except Exception:  # pragma: no cover
    BaseOptions = None
    VisionTaskRunningMode = None
    MpTaskFaceLandmarker = None
    FaceLandmarkerOptions = None


class HeadPoseEstimator:
    """Estimate head orientation from MediaPipe Face Landmarker or legacy Face Mesh."""

    LANDMARK_INDICES = (1, 152, 33, 263, 61, 291)
    MODEL_POINTS = np.array(
        [
            (0.0, 0.0, 0.0),          # Nose tip (1)
            (0.0, -330.0, -65.0),      # Chin (152)
            (-225.0, 170.0, -135.0),   # Left eye left corner (33)
            (225.0, 170.0, -135.0),    # Right eye right corner (263)
            (-150.0, -150.0, -125.0),  # Left mouth corner (61)
            (150.0, -150.0, -125.0),   # Right mouth corner (291)
        ],
        dtype=np.float64,
    )

    def __init__(self, min_detection_confidence: float = 0.5, min_tracking_confidence: float = 0.5):
        self._backend = "none"
        self._task_landmarker = None
        self._solutions_mesh = None

        # 1. Try MediaPipe Tasks Face Landmarker
        model_path = self._find_task_model()
        if (
            MpTaskFaceLandmarker is not None
            and FaceLandmarkerOptions is not None
            and BaseOptions is not None
            and VisionTaskRunningMode is not None
            and model_path is not None
        ):
            try:
                options = FaceLandmarkerOptions(
                    base_options=BaseOptions(model_asset_path=str(model_path)),
                    running_mode=VisionTaskRunningMode.IMAGE,
                    num_faces=1,
                    min_face_detection_confidence=min_detection_confidence,
                    min_face_presence_confidence=min_detection_confidence,
                    min_tracking_confidence=min_tracking_confidence,
                )
                self._task_landmarker = MpTaskFaceLandmarker.create_from_options(options)
                self._backend = "mediapipe_tasks"
                logger.info("HeadPoseEstimator initialized with MediaPipe Tasks Landmarker (%s)", model_path.name)
                return
            except Exception as error:
                logger.warning("MediaPipe Tasks FaceLandmarker failed to initialize: %s", error)

        # 2. Try legacy MediaPipe Solutions FaceMesh
        face_mesh_api = getattr(getattr(mp, "solutions", None), "face_mesh", None) if mp else None
        if face_mesh_api is not None:
            try:
                self._solutions_mesh = face_mesh_api.FaceMesh(
                    static_image_mode=False,
                    max_num_faces=1,
                    refine_landmarks=True,
                    min_detection_confidence=min_detection_confidence,
                    min_tracking_confidence=min_tracking_confidence,
                )
                self._backend = "mediapipe_solutions"
                logger.info("HeadPoseEstimator initialized with MediaPipe Solutions FaceMesh")
                return
            except Exception as error:
                logger.warning("MediaPipe Solutions FaceMesh failed to initialize: %s", error)

        logger.warning("No head pose estimation backend available.")
        self._backend = "none"

    def _find_task_model(self) -> Optional[Path]:
        env_path = os.getenv("MEDIAPIPE_FACE_LANDMARKER_MODEL")
        if env_path:
            candidate = Path(env_path)
            if candidate.exists():
                return candidate

        project_root = Path(__file__).resolve().parents[1]
        for name in ("face_landmarker.task", "face_landmarker_float16.task"):
            candidate = project_root / "models" / name
            if candidate.exists():
                return candidate

        return None

    def get_backend(self) -> str:
        return self._backend

    def is_available(self) -> bool:
        return self._backend != "none"

    def get_status(self) -> str:
        if self._backend == "mediapipe_tasks":
            return "Active (MediaPipe Tasks Landmarker)"
        elif self._backend == "mediapipe_solutions":
            return "Active (MediaPipe Solutions Mesh)"
        return "Unavailable"

    def process(self, frame) -> Optional[Tuple[float, float, float]]:
        """Return (yaw, pitch, roll) in degrees for the first face in a BGR frame."""
        if frame is None or not self.is_available():
            return None

        height, width = frame.shape[:2]
        if height <= 0 or width <= 0:
            return None

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # 1. MediaPipe Tasks Landmarker
        if self._backend == "mediapipe_tasks" and self._task_landmarker is not None:
            try:
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
                result = self._task_landmarker.detect(mp_image)
                if not result.face_landmarks or len(result.face_landmarks) == 0:
                    return None
                landmarks = result.face_landmarks[0]
                return self.estimate_pose(landmarks, width, height)
            except Exception as error:
                logger.debug("Tasks FaceLandmarker processing error: %s", error)
                return None

        # 2. Legacy MediaPipe Solutions FaceMesh
        if self._backend == "mediapipe_solutions" and self._solutions_mesh is not None:
            try:
                results = self._solutions_mesh.process(rgb_frame)
                faces = getattr(results, "multi_face_landmarks", None) or []
                if not faces:
                    return None
                return self.estimate_pose(faces[0].landmark, width, height)
            except Exception as error:
                logger.debug("Solutions FaceMesh processing error: %s", error)
                return None

        return None

    def estimate_pose(
        self, landmarks: Sequence, frame_width: int, frame_height: int
    ) -> Optional[Tuple[float, float, float]]:
        """Return yaw, pitch, and roll in degrees from Face Mesh landmarks."""
        if len(landmarks) <= max(self.LANDMARK_INDICES) or frame_width <= 0 or frame_height <= 0:
            return None

        try:
            image_points = np.array(
                [
                    (landmarks[index].x * frame_width, landmarks[index].y * frame_height)
                    for index in self.LANDMARK_INDICES
                ],
                dtype=np.float64,
            )
            focal_length = float(frame_width)
            camera_matrix = np.array(
                [
                    [focal_length, 0.0, frame_width / 2.0],
                    [0.0, focal_length, frame_height / 2.0],
                    [0.0, 0.0, 1.0],
                ],
                dtype=np.float64,
            )
            distortion_coefficients = np.zeros((4, 1), dtype=np.float64)

            success, rotation_vector, _ = cv2.solvePnP(
                self.MODEL_POINTS,
                image_points,
                camera_matrix,
                distortion_coefficients,
                flags=cv2.SOLVEPNP_ITERATIVE,
            )
            if not success:
                return None

            rotation_matrix, _ = cv2.Rodrigues(rotation_vector)
            angles, _, _, _, _, _ = cv2.RQDecomp3x3(rotation_matrix)
            # OpenCV returns the decomposition in roll, pitch, yaw order.
            # Keep the public tuple canonical as (yaw, pitch, roll), but do not
            # reinterpret the sign convention by swapping the final strings.
            roll, pitch, yaw = (float(angle) for angle in angles)
            return (
                self._normalize_euler_angle(yaw),
                self._normalize_euler_angle(pitch),
                self._normalize_euler_angle(roll),
            )
        except Exception as error:
            logger.debug("Pose calculation error: %s", error)
            return None

    @staticmethod
    def _normalize_euler_angle(angle: float) -> float:
        """Wrap Euler angles into a stable [-180, 180] range to avoid wrap-around flips."""
        wrapped = float(angle) % 360.0
        if wrapped > 180.0:
            wrapped -= 360.0
        if wrapped < -180.0:
            wrapped += 360.0
        if abs(wrapped) > 90.0:
            wrapped = wrapped - 180.0 if wrapped > 0 else wrapped + 180.0
        return wrapped

    @staticmethod
    def _classify_vertical_deviation(
        pitch_deviation: float,
        pitch_entry: float,
        pitch_return: float,
        prev_state: Optional[str] = None,
    ) -> str:
        """Classify vertical deviation using the canonical OpenCV pitch sign convention."""
        if prev_state in {"LOOKING_UP", "LOOKING_DOWN"}:
            if abs(pitch_deviation) <= pitch_return:
                return "NORMAL"
            if prev_state == "LOOKING_UP" and pitch_deviation <= -pitch_return:
                return "LOOKING_UP"
            if prev_state == "LOOKING_DOWN" and pitch_deviation >= pitch_return:
                return "LOOKING_DOWN"

        if abs(pitch_deviation) <= pitch_return:
            return "NORMAL"
        if pitch_deviation <= -pitch_entry:
            return "LOOKING_UP"
        if pitch_deviation >= pitch_entry:
            return "LOOKING_DOWN"
        return "NORMAL"

    @staticmethod
    def _dominant_axis_direction(yaw_deviation: float, pitch_deviation: float, yaw_threshold: float, pitch_threshold: float, prev_state: Optional[str] = None) -> str:
        """Prefer the larger deviation when both yaw and pitch exceed threshold."""
        yaw_abs = abs(yaw_deviation)
        pitch_abs = abs(pitch_deviation)

        if yaw_abs >= yaw_threshold and pitch_abs >= pitch_threshold:
            if yaw_abs >= pitch_abs:
                return "LOOKING_LEFT" if yaw_deviation < 0 else "LOOKING_RIGHT"
            return HeadPoseEstimator._classify_vertical_deviation(pitch_deviation, pitch_threshold, max(pitch_threshold * 0.7, pitch_threshold - 6.0), prev_state)

        if yaw_abs >= yaw_threshold:
            return "LOOKING_LEFT" if yaw_deviation < 0 else "LOOKING_RIGHT"
        if pitch_abs >= pitch_threshold:
            return HeadPoseEstimator._classify_vertical_deviation(pitch_deviation, pitch_threshold, max(pitch_threshold * 0.7, pitch_threshold - 6.0), prev_state)
        return "NORMAL"

    @classmethod
    def get_gaze_direction(
        cls,
        yaw: float,
        pitch: float,
        yaw_threshold: Optional[float] = None,
        pitch_threshold: Optional[float] = None,
        pitch_return_threshold: Optional[float] = None,
        pitch_entry_threshold: Optional[float] = None,
        prev_state: Optional[str] = None,
    ) -> str:
        """Categorize orientation into NORMAL, LOOKING_LEFT, LOOKING_RIGHT, LOOKING_UP, or LOOKING_DOWN."""
        settings = get_settings()
        y_thresh = float(settings.get("LOOKING_AWAY_ANGLE_DEG", 25.0) if yaw_threshold is None else yaw_threshold)
        p_entry = float(settings.get("GAZE_PITCH_THRESHOLD_DEG", 18.0) if pitch_entry_threshold is None else pitch_entry_threshold)
        p_exit = float(
            settings.get("GAZE_PITCH_RETURN_THRESHOLD_DEG", max(p_entry * 0.7, p_entry - 6.0))
            if pitch_return_threshold is None else pitch_return_threshold
        )

        if yaw >= y_thresh and abs(yaw) >= abs(pitch):
            return "LOOKING_RIGHT"
        if yaw <= -y_thresh and abs(yaw) >= abs(pitch):
            return "LOOKING_LEFT"

        if abs(pitch) >= p_entry and abs(pitch) >= abs(yaw):
            return cls._classify_vertical_deviation(pitch, p_entry, p_exit, prev_state)

        if yaw >= y_thresh:
            return "LOOKING_RIGHT"
        if yaw <= -y_thresh:
            return "LOOKING_LEFT"
        return cls._classify_vertical_deviation(pitch, p_entry, p_exit, prev_state)

    @classmethod
    def get_relative_gaze_direction(
        cls,
        yaw_deviation: float,
        pitch_deviation: float,
        yaw_threshold: float,
        pitch_threshold: float,
        pitch_return_threshold: Optional[float] = None,
        pitch_entry_threshold: Optional[float] = None,
        prev_state: Optional[str] = None,
    ) -> str:
        """Classify movement away from a calibrated neutral pose with a wider vertical normal zone."""
        y_thresh = float(yaw_threshold)
        p_entry = float(pitch_entry_threshold if pitch_entry_threshold is not None else pitch_threshold)
        p_exit = float(
            max(pitch_return_threshold if pitch_return_threshold is not None else (p_entry * 0.7), p_entry - 6.0)
        )

        if yaw_deviation >= y_thresh and abs(yaw_deviation) >= abs(pitch_deviation):
            return "LOOKING_RIGHT"
        if yaw_deviation <= -y_thresh and abs(yaw_deviation) >= abs(pitch_deviation):
            return "LOOKING_LEFT"

        if abs(pitch_deviation) >= p_entry and abs(pitch_deviation) >= abs(yaw_deviation):
            return cls._classify_vertical_deviation(pitch_deviation, p_entry, p_exit, prev_state)

        if yaw_deviation >= y_thresh:
            return "LOOKING_RIGHT"
        if yaw_deviation <= -y_thresh:
            return "LOOKING_LEFT"
        return cls._classify_vertical_deviation(pitch_deviation, p_entry, p_exit, prev_state)

    @staticmethod
    def robust_baseline(samples: Sequence[Tuple[float, float]], min_samples: int = 8) -> Optional[Tuple[float, float]]:
        """Return median yaw/pitch after rejecting large per-axis outliers."""
        if len(samples) < min_samples:
            return None

        values = np.asarray(samples, dtype=np.float64)
        medians = np.median(values, axis=0)
        deviations = np.abs(values - medians)
        mad = np.median(deviations, axis=0)
        limits = np.maximum(3.0 * mad, np.array([5.0, 5.0]))
        valid = np.all(deviations <= limits, axis=1)
        filtered = values[valid]
        if len(filtered) < min_samples:
            return None
        baseline = np.median(filtered, axis=0)
        return float(baseline[0]), float(baseline[1])

    @classmethod
    def is_looking_away(
        cls,
        yaw: float,
        pitch: float,
        yaw_threshold: Optional[float] = None,
        pitch_threshold: Optional[float] = None,
    ) -> bool:
        """Return True if yaw or pitch exceeds configured deviation threshold."""
        return cls.get_gaze_direction(yaw, pitch, yaw_threshold, pitch_threshold) != "NORMAL"