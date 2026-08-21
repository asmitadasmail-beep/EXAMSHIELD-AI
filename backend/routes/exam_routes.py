import logging
import threading
import time
from typing import Optional

from flask import Blueprint, Response, jsonify, session, stream_with_context

from ai_modules.proctor_engine import ProctorEngine
from backend.database.db_manager import DatabaseManager


exam_bp = Blueprint("exam", __name__)
logger = logging.getLogger(__name__)
db_manager = DatabaseManager()

_active_engine: Optional[ProctorEngine] = None
_engine_lock = threading.Lock()


@exam_bp.route("/exam/start", methods=["POST"])
def start_exam():
    global _active_engine
    with _engine_lock:
        if _active_engine is not None and _active_engine.is_running():
            return jsonify(_active_engine.get_status())

        student_id = session.get("student_id", 1)
        try:
            db_manager.init_db()
            session_id = db_manager.create_session(int(student_id))
        except Exception as e:
            logger.warning(f"Could not create DB session: {e}")
            session_id = 1

        engine = ProctorEngine()
        engine.start(session_id)

        if not engine.is_running():
            logger.error("Failed to start camera for proctor engine.")
            engine.stop()
            return (
                jsonify(
                    {
                        "running": False,
                        "camera_status": "Camera Status: Not Found",
                        "ai_status": "Monitoring",
                        "error": "Failed to open camera device.",
                    }
                ),
                400,
            )

        _active_engine = engine
        return jsonify(_active_engine.get_status())


@exam_bp.route("/exam/stop", methods=["POST"])
def stop_exam():
    global _active_engine
    with _engine_lock:
        if _active_engine is None:
            return jsonify(
                {
                    "running": False,
                    "camera_status": "Camera Off",
                    "ai_status": "Monitoring",
                }
            )
        engine = _active_engine
        _active_engine = None

    engine.stop()
    return jsonify(
        {
            "running": False,
            "camera_status": "Camera Off",
            "ai_status": "Monitoring",
        }
    )


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
                time.sleep(0.02)
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            )
            time.sleep(0.03)

    return Response(
        stream_with_context(generate()),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@exam_bp.route("/exam/status")
def exam_status():
    with _engine_lock:
        if _active_engine is None or not _active_engine.is_running():
            return jsonify(
                {
                    "running": False,
                    "camera_status": "Camera Off",
                    "ai_status": "Monitoring",
                    "face_missing_alert": False,
                }
            )
        return jsonify(_active_engine.get_status())

