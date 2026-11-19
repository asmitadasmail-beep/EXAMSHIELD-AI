from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import config


SETTINGS_PATH = config.BASE_DIR / "settings.json"
_settings_lock = threading.RLock()


def _defaults() -> dict[str, Any]:
    return {
        "FACE_MISSING_SEC": getattr(config, "FACE_MISSING_SEC", 5.0),
        "LOOKING_AWAY_SEC": getattr(config, "LOOKING_AWAY_SEC", 3.0),
        "LOOKING_AWAY_ANGLE_DEG": getattr(config, "LOOKING_AWAY_ANGLE_DEG", 25.0),
        "LOOKING_AWAY_PITCH_DEG": getattr(config, "LOOKING_AWAY_PITCH_DEG", 20.0),
        "CALIBRATION_DURATION_SEC": getattr(config, "CALIBRATION_DURATION_SEC", 3.0),
        "GAZE_YAW_THRESHOLD_DEG": getattr(config, "GAZE_YAW_THRESHOLD_DEG", 22.0),
        "GAZE_PITCH_THRESHOLD_DEG": getattr(config, "GAZE_PITCH_THRESHOLD_DEG", 18.0),
        "GAZE_PITCH_RETURN_THRESHOLD_DEG": getattr(config, "GAZE_PITCH_RETURN_THRESHOLD_DEG", 12.0),
        "POSE_SMOOTHING_FACTOR": getattr(config, "POSE_SMOOTHING_FACTOR", 0.35),
        "PHONE_CONFIRM_FRAMES": getattr(config, "PHONE_CONFIRM_FRAMES", 2),
        "MULTI_PERSON_CONFIRM_FRAMES": getattr(config, "MULTI_PERSON_CONFIRM_FRAMES", 2),
        "YOLO_CONFIDENCE": getattr(config, "YOLO_CONFIDENCE", 0.50),
        "RISK_WEIGHTS": dict(getattr(config, "RISK_WEIGHTS", {
            "FACE_MISSING": 20,
            "LOOKING_AWAY": 15,
            "MULTIPLE_PERSON": 30,
            "PHONE_DETECTED": 40,
        })),
        "RISK_SCORE_CAP": getattr(config, "RISK_SCORE_CAP", 100),
    }


def get_settings() -> dict[str, Any]:
    defaults = _defaults()
    with _settings_lock:
        if not SETTINGS_PATH.exists():
            return defaults
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return defaults

        settings = defaults
        for key in (
            "FACE_MISSING_SEC",
            "LOOKING_AWAY_SEC",
            "LOOKING_AWAY_ANGLE_DEG",
            "LOOKING_AWAY_PITCH_DEG",
            "PHONE_CONFIRM_FRAMES",
            "MULTI_PERSON_CONFIRM_FRAMES",
            "YOLO_CONFIDENCE",
            "CALIBRATION_DURATION_SEC",
            "GAZE_YAW_THRESHOLD_DEG",
            "GAZE_PITCH_THRESHOLD_DEG",
            "GAZE_PITCH_RETURN_THRESHOLD_DEG",
            "POSE_SMOOTHING_FACTOR",
        ):
            if key in saved:
                settings[key] = saved[key]
        if "LOOKING_AWAY_ANGLE_DEG" in saved and "GAZE_YAW_THRESHOLD_DEG" not in saved:
            settings["GAZE_YAW_THRESHOLD_DEG"] = saved["LOOKING_AWAY_ANGLE_DEG"]
        if "LOOKING_AWAY_PITCH_DEG" in saved and "GAZE_PITCH_THRESHOLD_DEG" not in saved:
            settings["GAZE_PITCH_THRESHOLD_DEG"] = saved["LOOKING_AWAY_PITCH_DEG"]
        if isinstance(saved.get("RISK_WEIGHTS"), dict):
            for event_type in settings["RISK_WEIGHTS"]:
                if event_type in saved["RISK_WEIGHTS"]:
                    settings["RISK_WEIGHTS"][event_type] = saved["RISK_WEIGHTS"][event_type]
        return settings


def save_settings(values: dict[str, Any]) -> dict[str, Any]:
    settings = _defaults()
    updates: dict[str, Any] = {
        "FACE_MISSING_SEC": float(values.get("FACE_MISSING_SEC", settings["FACE_MISSING_SEC"])),
        "LOOKING_AWAY_SEC": float(values.get("LOOKING_AWAY_SEC", settings["LOOKING_AWAY_SEC"])),
        "LOOKING_AWAY_ANGLE_DEG": float(values.get("LOOKING_AWAY_ANGLE_DEG", settings["LOOKING_AWAY_ANGLE_DEG"])),
        "LOOKING_AWAY_PITCH_DEG": float(values.get("LOOKING_AWAY_PITCH_DEG", settings["LOOKING_AWAY_PITCH_DEG"])),
        "PHONE_CONFIRM_FRAMES": int(values.get("PHONE_CONFIRM_FRAMES", settings["PHONE_CONFIRM_FRAMES"])),
        "MULTI_PERSON_CONFIRM_FRAMES": int(values.get("MULTI_PERSON_CONFIRM_FRAMES", settings["MULTI_PERSON_CONFIRM_FRAMES"])),
        "YOLO_CONFIDENCE": float(values.get("YOLO_CONFIDENCE", settings["YOLO_CONFIDENCE"])),
        "CALIBRATION_DURATION_SEC": float(values.get("CALIBRATION_DURATION_SEC", settings["CALIBRATION_DURATION_SEC"])),
        "GAZE_YAW_THRESHOLD_DEG": float(values.get("GAZE_YAW_THRESHOLD_DEG", settings["GAZE_YAW_THRESHOLD_DEG"])),
        "GAZE_PITCH_THRESHOLD_DEG": float(values.get("GAZE_PITCH_THRESHOLD_DEG", settings["GAZE_PITCH_THRESHOLD_DEG"])),
        "GAZE_PITCH_RETURN_THRESHOLD_DEG": float(values.get("GAZE_PITCH_RETURN_THRESHOLD_DEG", settings["GAZE_PITCH_RETURN_THRESHOLD_DEG"])),
        "POSE_SMOOTHING_FACTOR": float(values.get("POSE_SMOOTHING_FACTOR", settings["POSE_SMOOTHING_FACTOR"])),
    }
    
    if "RISK_WEIGHTS" in values and isinstance(values["RISK_WEIGHTS"], dict):
        updates["RISK_WEIGHTS"] = {
            event_type: int(values["RISK_WEIGHTS"].get(event_type, settings["RISK_WEIGHTS"].get(event_type, 10)))
            for event_type in settings["RISK_WEIGHTS"]
        }
    else:
        risk_weights = dict(settings["RISK_WEIGHTS"])
        for event_type in risk_weights:
            if event_type in values:
                risk_weights[event_type] = int(values[event_type])
            elif f"RISK_{event_type}" in values:
                risk_weights[event_type] = int(values[f"RISK_{event_type}"])
        updates["RISK_WEIGHTS"] = risk_weights

    settings.update(updates)

    if settings["FACE_MISSING_SEC"] <= 0 or settings["LOOKING_AWAY_SEC"] <= 0 or settings["CALIBRATION_DURATION_SEC"] <= 0:
        raise ValueError("Durations must be greater than zero.")
    if not 0 < settings["YOLO_CONFIDENCE"] <= 1:
        raise ValueError("YOLO confidence must be greater than 0 and at most 1.")
    if settings["LOOKING_AWAY_ANGLE_DEG"] <= 0 or settings["LOOKING_AWAY_PITCH_DEG"] <= 0:
        raise ValueError("Looking-away angles must be greater than zero.")
    if settings["GAZE_YAW_THRESHOLD_DEG"] <= 0 or settings["GAZE_PITCH_THRESHOLD_DEG"] <= 0:
        raise ValueError("Gaze deviation thresholds must be greater than zero.")
    if settings["GAZE_PITCH_RETURN_THRESHOLD_DEG"] <= 0:
        raise ValueError("Pitch hysteresis threshold must be greater than zero.")
    if not 0 < settings["POSE_SMOOTHING_FACTOR"] <= 1:
        raise ValueError("Pose smoothing factor must be greater than 0 and at most 1.")
    if settings["PHONE_CONFIRM_FRAMES"] < 1 or settings["MULTI_PERSON_CONFIRM_FRAMES"] < 1:
        raise ValueError("Confirmation frame counts must be at least 1.")
    if any(weight < 0 for weight in settings["RISK_WEIGHTS"].values()):
        raise ValueError("Risk weights cannot be negative.")

    with _settings_lock:
        SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return settings