import logging
import threading
import time
from datetime import datetime
from typing import Optional

from flask import Blueprint, Response, jsonify, send_file, session, stream_with_context

from ai_modules.proctor_engine import ProctorEngine
from backend.database.db_manager import DatabaseManager
from backend.services.risk_calculator import compute_risk_score
from utils.report_generator import export_csv


exam_bp = Blueprint("exam", __name__)
logger = logging.getLogger(__name__)
db_manager = DatabaseManager()

_active_engine: Optional[ProctorEngine] = None
_engine_lock = threading.Lock()


def _get_or_create_engine() -> ProctorEngine:
    global _active_engine
    if _active_engine is None:
        _active_engine = ProctorEngine()
    return _active_engine


@exam_bp.route("/exam/start", methods=["POST"])
def start_exam():
    with _engine_lock:
        engine = _get_or_create_engine()
        if engine.is_running():
            return jsonify(engine.get_status())

        student_id = session.get("student_id", 1)
        try:
            db_manager.init_db()
            session_id = db_manager.create_session(int(student_id))
        except Exception as e:
            logger.warning("Could not create DB session: %s", e)
            return jsonify(
                {
                    "running": False,
                    "camera_status": "Camera Off",
                    "ai_status": "Database Unavailable",
                    "error": "The exam database is unavailable. Please try again.",
                }
            ), 503

        engine.start(session_id)

        if not engine.is_running():
            logger.error("Failed to start camera for proctor engine.")
            engine.stop()
            return (
                jsonify(
                    {
                        "running": False,
                        "camera_status": "Camera Status: Not Found",
                        "ai_status": "Camera Error",
                        "face_status": "unknown",
                        "looking_away": False,
                        "person_count": 0,
                        "warning_count": 0,
                        "error": "Failed to open camera device. Please ensure your webcam is connected and not in use by another application.",
                    }
                ),
                400,
            )

        return jsonify(engine.get_status())


@exam_bp.route("/exam/stop", methods=["POST"])
def stop_exam():
    with _engine_lock:
        engine = _active_engine
        if engine is None or not engine.is_running():
            return jsonify(
                {
                    "running": False,
                    "camera_status": "Camera Off",
                    "ai_status": "Monitoring",
                    "face_status": "unknown",
                    "looking_away": False,
                    "person_count": 0,
                    "warning_count": 0,
                    "gaze_state": "UNKNOWN",
                    "calibration_status": "idle",
                    "multiple_person": False,
                    "phone_detected": False,
                }
            )

    engine.stop()
    session_id = engine.session_id
    end_time = datetime.now()
    duration = 0
    risk_score = 0
    risk_category = "Low"
    finalization_error = None

    if session_id is not None:
        try:
            with db_manager.get_connection() as connection:
                row = connection.execute(
                    "SELECT start_time FROM exam_sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            if row is not None and row["start_time"]:
                start_time = datetime.fromisoformat(row["start_time"])
                duration = max(int((end_time - start_time).total_seconds()), 0)
            risk_score, risk_category = compute_risk_score(session_id)
            db_manager.end_session(
                session_id=session_id,
                end_time=end_time.isoformat(timespec="seconds"),
                duration=duration,
                risk_score=risk_score,
                risk_category=risk_category,
                status="completed",
            )
            export_csv(session_id)
        except Exception:
            logger.exception("Exam session finalization failed")
            finalization_error = "The camera stopped, but the session could not be finalized."

    return jsonify(
        {
            "running": False,
            "camera_status": "Camera Off",
            "ai_status": "Monitoring",
            "face_status": "unknown",
            "looking_away": False,
            "person_count": 0,
            "multiple_person": False,
            "phone_detected": False,
            "warning_count": engine.warning_count if engine else 0,
            "session_id": session_id,
            "risk_score": risk_score if session_id is not None else 0,
            "risk_category": risk_category if session_id is not None else "Low",
            "error": finalization_error,
        }
    )


@exam_bp.route("/exam/report/<int:session_id>")
def download_report(session_id: int):
    try:
        report_path = export_csv(session_id)
    except Exception:
        logger.exception("CSV report generation failed")
        return jsonify({"error": "The CSV report is temporarily unavailable."}), 500
    return send_file(report_path, as_attachment=True, download_name=report_path.name, mimetype="text/csv")


@exam_bp.route("/stop_video_feed", methods=["POST"])
def stop_video_feed():
    return stop_exam()


@exam_bp.route("/video_feed")
def video_feed():
    def generate():
        while True:
            with _engine_lock:
                engine = _active_engine

            if engine is None or not engine.is_running():
                break

            frame_bytes = engine.get_latest_frame_bytes()
            if frame_bytes is None:
                time.sleep(0.015)
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            )
            time.sleep(0.025)

    return Response(
        stream_with_context(generate()),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@exam_bp.route("/exam/status")
def exam_status():
    with _engine_lock:
        if _active_engine is None or not _active_engine.is_running():
            # Return subsystem availability even if exam is not running
            engine = _active_engine or _get_or_create_engine()
            return jsonify(
                {
                    "running": False,
                    "camera_status": "Camera Off",
                    "ai_status": "Monitoring",
                    "ai_ready": engine.is_ai_ready(),
                    "face_status": "unknown",
                    "looking_away": False,
                    "person_count": 0,
                    "face_missing_alert": False,
                    "looking_away_alert": False,
                    "multiple_person": False,
                    "phone_detected": False,
                    "warning_count": 0,
                    "gaze_state": "UNKNOWN",
                    "calibration_status": "idle",
                    "active_alerts": [],
                    "subsystems": {
                        "camera": "Off",
                        "face_detector": engine.face_detector.get_status() if engine.face_detector else "Ready",
                        "head_pose": engine.head_pose_estimator.get_status() if engine.head_pose_estimator else "Ready",
                        "person_detector": engine.yolo_detector.get_status() if engine.yolo_detector else "Ready",
                        "phone_detector": engine.yolo_detector.get_status() if engine.yolo_detector else "Ready",
                    },
                    "live_checks": {
                        "face_detected": False,
                        "looking_at_screen": False,
                        "gaze_direction": "UNKNOWN",
                        "gaze_state": "UNKNOWN",
                        "single_person": False,
                        "person_count": 0,
                        "no_phone": True,
                    }
                }
            )
        return jsonify(_active_engine.get_status())

