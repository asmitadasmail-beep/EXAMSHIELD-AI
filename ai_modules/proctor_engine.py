from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

import config
from ai_modules.face_detection import FaceDetector
from ai_modules.head_pose import HeadPoseEstimator
from ai_modules.webcam import WebcamStream
from ai_modules.yolo_detector import YoloDetector
from utils.logger import IncidentLogger
from utils.settings_store import get_settings


logger = logging.getLogger(__name__)


class ProctorEngine:
    def __init__(self, webcam: Optional[WebcamStream] = None):
        self.webcam = webcam or WebcamStream()
        self.face_detector: Optional[FaceDetector] = None
        self.head_pose_estimator: Optional[HeadPoseEstimator] = None
        self.yolo_detector: Optional[YoloDetector] = None
        self.incident_logger = IncidentLogger()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

        self._ai_ready = False
        self._ai_init_thread: Optional[threading.Thread] = None
        self._ai_init_lock = threading.Lock()

        self.session_id: Optional[int] = None
        self.face_missing_alert = False
        self._missing_face_started_at: Optional[float] = None
        self.looking_away_alert = False
        self._looking_away_started_at: Optional[float] = None
        self._looking_away_incident_armed = False
        self.multiple_person = False
        self.phone_detected = False
        self.warning_count = 0

        self._previous_multiple_person = False
        self._previous_phone_detected = False
        self._multi_person_consecutive = 0
        self._phone_consecutive = 0
        self._phone_missing_consecutive = 0

        self._frame_count = 0
        self._yolo_detections: List[Dict[str, Any]] = []
        self._last_pose: Optional[tuple[float, float, float]] = None
        self._last_gaze_direction = "NORMAL"
        self.calibration_status = "idle"
        self.baseline_yaw: Optional[float] = None
        self.baseline_pitch: Optional[float] = None
        self._calibration_started_at: Optional[float] = None
        self._calibration_samples: List[tuple[float, float]] = []
        self._smoothed_yaw: Optional[float] = None
        self._smoothed_pitch: Optional[float] = None
        self._last_gaze_log_at = 0.0
        self._last_gaze_state = "UNKNOWN"

        self.state = {
            "face_status": "present",
            "looking_away": False,
            "phone_detected": False,
            "person_count": 1,
        }
        self._running = False
        self.latest_processed_frame: Optional[np.ndarray] = None

        # Warm up AI detectors in background thread immediately so start_exam is instantaneous
        self._warmup_ai_async()

    def _warmup_ai_async(self) -> None:
        def _warmup():
            with self._ai_init_lock:
                if self._ai_ready:
                    return
                logger.info("Initializing and warming up AI detectors...")
                try:
                    if self.face_detector is None:
                        self.face_detector = FaceDetector()
                    if self.head_pose_estimator is None:
                        self.head_pose_estimator = HeadPoseEstimator()
                    if self.yolo_detector is None:
                        self.yolo_detector = YoloDetector()
                        self.yolo_detector.warmup()
                    self._ai_ready = True
                    logger.info("AI detectors initialized and ready.")
                except Exception as e:
                    logger.error("AI detector initialization error: %s", e, exc_info=True)

        self._ai_init_thread = threading.Thread(target=_warmup, daemon=True)
        self._ai_init_thread.start()

    def is_ai_ready(self) -> bool:
        return self._ai_ready

    def start(self, session_id: int) -> None:
        with self.lock:
            if self._running:
                return

            self.session_id = session_id
            self.face_missing_alert = False
            self._missing_face_started_at = None
            self.looking_away_alert = False
            self._looking_away_started_at = None
            self._looking_away_incident_armed = False
            self.multiple_person = False
            self.phone_detected = False
            self.warning_count = 0
            self._previous_multiple_person = False
            self._previous_phone_detected = False
            self._multi_person_consecutive = 0
            self._phone_consecutive = 0
            self._phone_missing_consecutive = 0
            self._frame_count = 0
            self._yolo_detections = []
            self._last_pose = None
            self._last_gaze_direction = "NORMAL"
            self.calibration_status = "pending"
            self.baseline_yaw = None
            self.baseline_pitch = None
            self._calibration_started_at = None
            self._calibration_samples = []
            self._smoothed_yaw = None
            self._smoothed_pitch = None
            self._last_gaze_state = "CALIBRATING"
            self.state.update(
                {
                    "face_status": "present",
                    "looking_away": False,
                    "phone_detected": False,
                    "person_count": 1,
                }
            )
            self.stop_event.clear()

            # 1. Start camera immediately
            self.webcam.start()
            if not self.webcam.is_running():
                logger.error("Webcam failed to start.")
                self._running = False
                return

            self._running = True
            self._calibration_started_at = time.monotonic()
            logger.info("ProctorEngine started for session %s.", session_id)
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
                logger.error("Error during frame processing: %s", e, exc_info=True)
                processed_frame = frame.copy()

            with self.lock:
                self.latest_processed_frame = processed_frame

            time.sleep(0.015)

        with self.lock:
            self._running = False

    def process_frame(self, frame: np.ndarray) -> np.ndarray:
        # If AI models are still warming up in background, display camera feed with overlay
        if not self._ai_ready or self.face_detector is None or self.head_pose_estimator is None or self.yolo_detector is None:
            cv2.putText(
                frame,
                "Initializing AI Detectors...",
                (24, 42),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (91, 192, 235),
                2,
            )
            return frame

        now = time.monotonic()
        settings = get_settings()

        with self.lock:
            self._frame_count += 1

            # -------------------------------------------------------------
            # 1. Object & Person Detection (YOLO Throttled to every 3rd frame)
            # -------------------------------------------------------------
            if self._frame_count % 3 == 0:
                self._yolo_detections = self.yolo_detector.detect(frame)

            yolo_detections = self._yolo_detections
            person_detections = [d for d in yolo_detections if d.get("class_name") == "person"]
            phone_detections = [
                d for d in yolo_detections if self.yolo_detector and self.yolo_detector.is_phone_class_name(d.get("class_name", ""))
            ]
            logger.debug(
                "YOLO filtered person_count=%d phone_count=%d threshold=%.2f detections=%s",
                len(person_detections),
                len(phone_detections),
                settings.get("YOLO_CONFIDENCE", 0.50),
                [(d.get("class_name_raw", d.get("class_name")), d.get("confidence")) for d in yolo_detections],
            )
            logger.debug(
                "YOLO phone_filter final_phone_detected=%s selected=%s",
                bool(phone_detections),
                [(d.get("class_name_raw", d.get("class_name")), d.get("confidence")) for d in phone_detections],
            )

            # --- Multi-person Debouncing ---
            multi_person_threshold = config.MULTI_PERSON_THRESHOLD
            is_multi_now = len(person_detections) > multi_person_threshold
            if is_multi_now:
                self._multi_person_consecutive += 1
            else:
                self._multi_person_consecutive = 0

            multi_confirm_frames = settings.get("MULTI_PERSON_CONFIRM_FRAMES", 2)
            self.multiple_person = self._multi_person_consecutive >= multi_confirm_frames

            if self.multiple_person and not self._previous_multiple_person:
                conf = self._person_confidence(person_detections)
                count = len(person_detections)
                self._log_incident(
                    "MULTIPLE_PERSON",
                    conf,
                    "HIGH",
                    f"Detected {count} persons in the camera frame.",
                )

            self._previous_multiple_person = self.multiple_person

            # --- Phone Detection Debouncing / Hysteresis ---
            is_phone_now = bool(phone_detections)
            phone_confirm_frames = max(1, int(settings.get("PHONE_CONFIRM_FRAMES", 2)))
            phone_clear_frames = max(phone_confirm_frames * 2, 3)

            if is_phone_now:
                self._phone_consecutive += 1
                self._phone_missing_consecutive = 0
            else:
                self._phone_missing_consecutive += 1
                self._phone_consecutive = 0

            if is_phone_now and self._phone_consecutive >= phone_confirm_frames and not self.phone_detected:
                self.phone_detected = True

            if not is_phone_now and self.phone_detected and self._phone_missing_consecutive >= phone_clear_frames:
                self.phone_detected = False

            if self.phone_detected and not self._previous_phone_detected:
                conf = self._person_confidence(phone_detections)
                self._log_incident(
                    "PHONE_DETECTED",
                    conf,
                    "HIGH",
                    f"Cell phone detected in the frame with confidence {conf:.2f}.",
                )

            if not self.phone_detected:
                self._previous_phone_detected = False
            else:
                self._previous_phone_detected = True

            self.state.update(
                {
                    "phone_detected": self.phone_detected,
                    "person_count": max(len(person_detections), 1 if not self.face_missing_alert else 0),
                }
            )

            # Draw YOLO Bounding Boxes
            self._draw_yolo_detections(frame, person_detections, (255, 120, 0), "Person")
            self._draw_yolo_detections(frame, phone_detections, (0, 140, 255), "Phone")

            # -------------------------------------------------------------
            # 2. Face Presence & Head Pose Detection
            # -------------------------------------------------------------
            face_present, bbox, face_conf = self.face_detector.detect(frame)

            if face_present:
                self.state["face_status"] = "present"
                self._missing_face_started_at = None
                self.face_missing_alert = False

                # 3. Head Pose Estimation
                pose = self.head_pose_estimator.process(frame)
                self._last_pose = pose

                if pose is not None:
                    yaw, pitch, roll = pose
                    logger.debug(
                        "session=%s raw_pose yaw=%.2f pitch=%.2f roll=%.2f calibration=%s",
                        self.session_id,
                        yaw,
                        pitch,
                        roll,
                        self.calibration_status,
                    )
                    self._update_gaze_state(yaw, pitch, now, settings)
                    gaze_dir = self._last_gaze_direction
                    display_gaze_dir = self._canonical_gaze_direction(gaze_dir)
                    looking_away = self.calibration_status == "complete" and display_gaze_dir != "LOOKING_AT_SCREEN"

                    if self.calibration_status == "complete":
                        if self._update_looking_away_incident(looking_away, now, float(settings["LOOKING_AWAY_SEC"])):
                            self._log_incident(
                                "LOOKING_AWAY",
                                0.95,
                                "MEDIUM",
                                f"Head deviation ({gaze_dir}): yaw={yaw:.1f}°, pitch={pitch:.1f}° for {settings['LOOKING_AWAY_SEC']} seconds.",
                            )
                else:
                    if self.calibration_status == "pending":
                        self._finish_calibration_if_due(now, settings)
                    self._last_gaze_direction = "UNKNOWN"
                    self._update_looking_away_incident(False, now, float(settings.get("LOOKING_AWAY_SEC", 3.0)))
                    self._set_gaze_state("DETECTION_UNAVAILABLE", now)

                self.state["looking_away"] = self.looking_away_alert

                # Draw Face Bounding Box & Direction
                if bbox is not None:
                    x = bbox["x"]
                    y = bbox["y"]
                    width = bbox["width"]
                    height = bbox["height"]
                    box_color = (0, 255, 0) if not self.looking_away_alert else (0, 165, 255)
                    cv2.rectangle(frame, (x, y), (x + width, y + height), box_color, 2)
                    label = f"Face ({self._canonical_gaze_direction(self._last_gaze_direction)})" if self._canonical_gaze_direction(self._last_gaze_direction) != "LOOKING_AT_SCREEN" else "Face (Screen)"
                    cv2.putText(
                        frame,
                        label,
                        (x, max(y - 10, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        box_color,
                        2,
                    )
            else:
                self.state["face_status"] = "missing"
                self.state["looking_away"] = False
                self._last_pose = None
                self._last_gaze_direction = "UNKNOWN"
                self._looking_away_started_at = None
                self.looking_away_alert = False
                if self.calibration_status == "pending":
                    self._finish_calibration_if_due(now, settings)
                self._set_gaze_state("CALIBRATING" if self.calibration_status == "pending" else "DETECTION_UNAVAILABLE", now)

                if self._missing_face_started_at is None:
                    self._missing_face_started_at = now

                missing_duration = now - self._missing_face_started_at
                if missing_duration >= settings["FACE_MISSING_SEC"]:
                    if not self.face_missing_alert:
                        self.face_missing_alert = True
                        self._log_incident(
                            "FACE_MISSING",
                            1.0,
                            "MEDIUM",
                            f"No face detected in camera for {settings['FACE_MISSING_SEC']} seconds.",
                        )

                cv2.putText(
                    frame,
                    "No face detected",
                    (24, 42),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.85,
                    (0, 0, 255),
                    2,
                )

            # Draw Alert Banner on frame if violation is active
            active_alerts = self._get_active_alerts_list()
            if active_alerts:
                banner_text = " | ".join(active_alerts)
                cv2.putText(
                    frame,
                    f"ALERT: {banner_text}",
                    (24, frame.shape[0] - 24),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 70, 255),
                    2,
                )

        return frame

    @staticmethod
    def _canonical_gaze_direction(direction: str) -> str:
        if direction in {"NORMAL", "LOOKING_AT_SCREEN"}:
            return "LOOKING_AT_SCREEN"
        return direction

    def _update_gaze_state(self, yaw: float, pitch: float, now: float, settings: dict) -> None:
        if self.calibration_status == "pending":
            if self.multiple_person:
                self._finish_calibration_if_due(now, settings)
                self._set_gaze_state("CALIBRATING", now)
                return
            self._calibration_samples.append((yaw, pitch))
            self._finish_calibration_if_due(now, settings)
            if self.calibration_status != "complete":
                self._set_gaze_state("CALIBRATING", now)
                return

        if self.calibration_status != "complete" or self.baseline_yaw is None or self.baseline_pitch is None:
            self._set_gaze_state("DETECTION_UNAVAILABLE", now)
            return

        alpha = float(settings.get("POSE_SMOOTHING_FACTOR", 0.35))
        if self._smoothed_yaw is None:
            self._smoothed_yaw, self._smoothed_pitch = yaw, pitch
        else:
            self._smoothed_yaw += alpha * (yaw - self._smoothed_yaw)
            self._smoothed_pitch += alpha * (pitch - self._smoothed_pitch)

        yaw_deviation = self._smoothed_yaw - self.baseline_yaw
        pitch_deviation = self._smoothed_pitch - self.baseline_pitch
        previous_state = self._last_gaze_direction if self._last_gaze_direction in {"LOOKING_UP", "LOOKING_DOWN", "NORMAL"} else "NORMAL"
        gaze_dir = self.head_pose_estimator.get_relative_gaze_direction(
            yaw_deviation,
            pitch_deviation,
            float(settings.get("GAZE_YAW_THRESHOLD_DEG", 22.0)),
            float(settings.get("GAZE_PITCH_THRESHOLD_DEG", 20.0)),
            float(settings.get("GAZE_PITCH_RETURN_THRESHOLD_DEG", 12.0)),
            float(settings.get("GAZE_PITCH_THRESHOLD_DEG", 20.0)),
            previous_state,
        )
        self._last_gaze_direction = gaze_dir
        self._last_gaze_state = self._canonical_gaze_direction(gaze_dir)
        self._last_pose = (self._smoothed_yaw, self._smoothed_pitch, self._last_pose[2] if self._last_pose else 0.0)
        self._set_gaze_state(self._last_gaze_state, now)
        if now - self._last_gaze_log_at >= 1.0:
            logger.debug(
                "session=%s pose yaw=%.1f pitch=%.1f yaw_deviation=%.1f pitch_deviation=%.1f state=%s",
                self.session_id, self._smoothed_yaw, self._smoothed_pitch,
                yaw_deviation, pitch_deviation, gaze_dir,
            )
            self._last_gaze_log_at = now

    def _finish_calibration_if_due(self, now: float, settings: dict) -> None:
        if self.calibration_status != "pending" or self._calibration_started_at is None:
            return
        if now - self._calibration_started_at < float(settings.get("CALIBRATION_DURATION_SEC", 3.0)):
            return
        baseline = self.head_pose_estimator.robust_baseline(self._calibration_samples)
        if baseline is None:
            self.calibration_status = "failed"
            self._set_gaze_state("CALIBRATION_FAILED", now)
            logger.warning("session=%s gaze calibration failed: valid_samples=%s", self.session_id, len(self._calibration_samples))
            return
        self.baseline_yaw, self.baseline_pitch = baseline
        self.calibration_status = "complete"
        self._smoothed_yaw = self.baseline_yaw
        self._smoothed_pitch = self.baseline_pitch
        self._set_gaze_state("NORMAL", now)
        logger.info("session=%s gaze calibration complete baseline_yaw=%.1f baseline_pitch=%.1f samples=%s", self.session_id, self.baseline_yaw, self.baseline_pitch, len(self._calibration_samples))

    def _set_gaze_state(self, state: str, now: float) -> None:
        if state != self._last_gaze_state:
            logger.debug("session=%s gaze state changed: %s -> %s", self.session_id, self._last_gaze_state, state)
            self._last_gaze_state = state

    def _get_active_alerts_list(self) -> List[str]:
        alerts = []
        if self.face_missing_alert:
            alerts.append("Face Not Detected")
        if self.looking_away_alert:
            alerts.append(f"Looking Away ({self._last_gaze_direction})")
        if self.multiple_person:
            alerts.append("Multiple People Detected")
        if self.phone_detected:
            alerts.append("Mobile Phone Detected")
        return alerts

    def _refresh_warning_count(self) -> None:
        if self.session_id is None:
            self.warning_count = 0
            return
        try:
            incidents = self.incident_logger.db_manager.get_session_incidents(self.session_id)
            self.warning_count = len(incidents)
        except Exception:
            logger.exception("Failed to refresh warning count for session %s", self.session_id)
            self.warning_count = max(self.warning_count, 0)

    def _update_looking_away_incident(self, is_abnormal: bool, now: float, threshold_seconds: float) -> bool:
        """Return True only when a new confirmed looking-away incident is created."""
        if not is_abnormal:
            self._looking_away_started_at = None
            self.looking_away_alert = False
            self._looking_away_incident_armed = False
            return False

        if self.looking_away_alert:
            return False

        if self._looking_away_started_at is None:
            self._looking_away_started_at = now

        if now - self._looking_away_started_at < threshold_seconds:
            return False

        self.looking_away_alert = True
        self._looking_away_incident_armed = True
        return True

    def _log_incident(self, event_type: str, confidence: float, severity: str, details: str) -> None:
        if self.session_id is not None:
            logger.warning(
                "INCIDENT session=%s type=%s severity=%s details=%s",
                self.session_id,
                event_type,
                severity,
                details,
            )
            self.incident_logger.log_event(
                self.session_id,
                event_type,
                confidence,
                severity,
                details,
            )
            self._refresh_warning_count()

    @staticmethod
    def _person_confidence(detections: List[Dict[str, Any]]) -> float:
        if not detections:
            return 0.0
        return max(float(d["confidence"]) for d in detections)

    @staticmethod
    def _draw_yolo_detections(
        frame: np.ndarray, detections: List[Dict[str, Any]], color: tuple, label_prefix: str
    ) -> None:
        for detection in detections:
            bbox = detection["bbox"]
            x, y = bbox["x"], bbox["y"]
            width, height = bbox["width"], bbox["height"]
            cv2.rectangle(frame, (x, y), (x + width, y + height), color, 2)
            label = f"{label_prefix} {detection['confidence']:.2f}"
            cv2.putText(
                frame,
                label,
                (x, max(y - 10, 20)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
            )

    def stop(self) -> None:
        self.stop_event.set()

        thread = self.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)

        self.webcam.stop()

        with self.lock:
            self._running = False
            self.thread = None
            self.latest_processed_frame = None
            logger.info("ProctorEngine stopped.")

    def is_running(self) -> bool:
        return self._running and self.webcam.is_running()

    def get_latest_frame_bytes(self) -> Optional[bytes]:
        with self.lock:
            if self.latest_processed_frame is None:
                # Return direct camera frame if processed frame is not yet generated
                frame = self.webcam.get_frame()
                if frame is None:
                    return None
                success, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                return buffer.tobytes() if success else None

            success, buffer = cv2.imencode(".jpg", self.latest_processed_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not success:
                return None
            return buffer.tobytes()

    def get_status(self) -> dict:
        with self.lock:
            running = self.is_running()
            cam_stat = self.webcam.get_status()
            camera_status = f"Camera {cam_stat}" if running else "Camera Off"

            face_status_label = self.face_detector.get_status() if self.face_detector else "Initializing..."
            pose_status_label = self.head_pose_estimator.get_status() if self.head_pose_estimator else "Initializing..."
            person_status_label = self.yolo_detector.get_status() if self.yolo_detector else "Initializing..."
            phone_status_label = person_status_label

            if not self._ai_ready:
                ai_status = "Initializing AI..."
            elif not running:
                ai_status = "Monitoring"
            elif self.calibration_status == "pending":
                ai_status = "Calibrating"
            elif self.calibration_status == "failed":
                ai_status = "Gaze Calibration Failed"
            elif self.face_missing_alert:
                ai_status = "Face Not Detected"
            elif self.looking_away_alert:
                ai_status = f"Looking Away ({self._last_gaze_direction})"
            elif self._last_gaze_state == "DETECTION_UNAVAILABLE":
                ai_status = "Head Pose Detection Unavailable"
            elif self.phone_detected:
                ai_status = "Phone Detected"
            elif self.multiple_person:
                ai_status = "Multiple People"
            elif self.face_detector and self.face_detector.get_backend() == "opencv":
                ai_status = "Running with OpenCV Fallback"
            elif self.face_detector and not self.face_detector.is_available():
                ai_status = "Face Detection Unavailable"
            elif self.head_pose_estimator and not self.head_pose_estimator.is_available():
                ai_status = "Head Pose Unavailable"
            elif self.yolo_detector and not self.yolo_detector.is_available():
                ai_status = "YOLO Unavailable"
            else:
                ai_status = "Fully Operational"

            active_alerts = self._get_active_alerts_list()
            if self.session_id is not None:
                self._refresh_warning_count()
            warning_count = self.warning_count if self.session_id is not None else 0
            canonical_gaze_direction = self._canonical_gaze_direction(self._last_gaze_direction)
            canonical_gaze_state = self._canonical_gaze_direction(self._last_gaze_state)

            return {
                "running": running,
                "camera_status": camera_status,
                "ai_status": ai_status,
                "ai_ready": self._ai_ready,
                "face_missing_alert": self.face_missing_alert,
                "looking_away_alert": self.looking_away_alert,
                "multiple_person": self.multiple_person,
                "phone_detected": self.phone_detected,
                "warning_count": warning_count,
                "gaze_direction": canonical_gaze_direction,
                "gaze_state": canonical_gaze_state,
                "calibration_status": self.calibration_status,
                "baseline_yaw": self.baseline_yaw,
                "baseline_pitch": self.baseline_pitch,
                "active_alerts": active_alerts,
                "subsystems": {
                    "camera": cam_stat,
                    "face_detector": face_status_label,
                    "head_pose": pose_status_label,
                    "person_detector": person_status_label,
                    "phone_detector": phone_status_label,
                },
                "live_checks": {
                    "face_detected": not self.face_missing_alert and self.state.get("face_status") == "present",
                    "looking_at_screen": not self.looking_away_alert and canonical_gaze_direction == "LOOKING_AT_SCREEN",
                    "gaze_direction": canonical_gaze_direction,
                    "gaze_state": canonical_gaze_state,
                    "single_person": not self.multiple_person and self.state.get("person_count", 1) == 1,
                    "person_count": self.state.get("person_count", 1),
                    "no_phone": not self.phone_detected,
                },
                **self.state,
                "session_id": self.session_id,
            }


