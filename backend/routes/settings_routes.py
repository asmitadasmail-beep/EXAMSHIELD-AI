from flask import Blueprint, redirect, render_template, request, url_for

from utils.settings_store import get_settings, save_settings


settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/settings", methods=["GET", "POST"])
def settings_page():
    error = None
    saved = False
    if request.method == "POST":
        try:
            payload = {
                "FACE_MISSING_SEC": request.form["FACE_MISSING_SEC"],
                "LOOKING_AWAY_SEC": request.form["LOOKING_AWAY_SEC"],
                "LOOKING_AWAY_ANGLE_DEG": request.form["LOOKING_AWAY_ANGLE_DEG"],
                "LOOKING_AWAY_PITCH_DEG": request.form.get("LOOKING_AWAY_PITCH_DEG", 20.0),
                "CALIBRATION_DURATION_SEC": request.form.get("CALIBRATION_DURATION_SEC", 3.0),
                "GAZE_YAW_THRESHOLD_DEG": request.form.get("GAZE_YAW_THRESHOLD_DEG", 22.0),
                "GAZE_PITCH_THRESHOLD_DEG": request.form.get("GAZE_PITCH_THRESHOLD_DEG", 20.0),
                "GAZE_PITCH_RETURN_THRESHOLD_DEG": request.form.get("GAZE_PITCH_RETURN_THRESHOLD_DEG", 12.0),
                "POSE_SMOOTHING_FACTOR": request.form.get("POSE_SMOOTHING_FACTOR", 0.35),
                "PHONE_CONFIRM_FRAMES": request.form.get("PHONE_CONFIRM_FRAMES", 2),
                "MULTI_PERSON_CONFIRM_FRAMES": request.form.get("MULTI_PERSON_CONFIRM_FRAMES", 2),
                "YOLO_CONFIDENCE": request.form["YOLO_CONFIDENCE"],
                "FACE_MISSING": request.form.get("RISK_FACE_MISSING", request.form.get("FACE_MISSING", 20)),
                "LOOKING_AWAY": request.form.get("RISK_LOOKING_AWAY", request.form.get("LOOKING_AWAY", 15)),
                "MULTIPLE_PERSON": request.form.get("RISK_MULTIPLE_PERSON", request.form.get("MULTIPLE_PERSON", 30)),
                "PHONE_DETECTED": request.form.get("RISK_PHONE_DETECTED", request.form.get("PHONE_DETECTED", 40)),
            }
            save_settings(payload)
            saved = True
        except (KeyError, OSError, TypeError, ValueError) as exc:
            error = str(exc)

    return render_template(
        "settings.html",
        page_title="Settings",
        settings=get_settings(),
        saved=saved,
        error=error,
    )