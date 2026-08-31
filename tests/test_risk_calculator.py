from pathlib import Path

import config
from ai_modules.head_pose import HeadPoseEstimator
from ai_modules.proctor_engine import ProctorEngine
from ai_modules.yolo_detector import YoloDetector
from backend.database.db_manager import DatabaseManager
from backend.services import risk_calculator
from utils.settings_store import get_settings, save_settings


def test_compute_risk_score_uses_incident_counts(tmp_path: Path):
    database = DatabaseManager(tmp_path / "risk-test.db")
    database.init_db()
    student_id = database.add_student("Test Student", "TEST001", "AI", "AI101")
    session_id = database.create_session(student_id)

    database.add_incident(session_id, "FACE_MISSING", 1.0, "MEDIUM", "face")
    database.add_incident(session_id, "FACE_MISSING", 1.0, "MEDIUM", "face")
    database.add_incident(session_id, "LOOKING_AWAY", 1.0, "MEDIUM", "away")
    database.add_incident(session_id, "PHONE_DETECTED", 0.9, "HIGH", "phone")

    original_manager = risk_calculator.db_manager
    risk_calculator.db_manager = database
    try:
        score, category = risk_calculator.compute_risk_score(session_id)
    finally:
        risk_calculator.db_manager = original_manager

    assert score == (2 * 20) + 15 + 40
    assert category == "High"


def test_compute_risk_score_caps_at_configured_limit(tmp_path: Path):
    database = DatabaseManager(tmp_path / "risk-cap-test.db")
    database.init_db()
    student_id = database.add_student("Test Student", "TEST002", "AI", "AI102")
    session_id = database.create_session(student_id)

    for _ in range(10):
        database.add_incident(session_id, "PHONE_DETECTED", 0.9, "HIGH", "phone")

    original_manager = risk_calculator.db_manager
    risk_calculator.db_manager = database
    try:
        score, category = risk_calculator.compute_risk_score(session_id)
    finally:
        risk_calculator.db_manager = original_manager

    assert score == config.RISK_SCORE_CAP
    assert category == "High"


def test_head_pose_gaze_direction_classification():
    # Normal orientation
    assert HeadPoseEstimator.get_gaze_direction(yaw=0.0, pitch=0.0, yaw_threshold=25.0, pitch_threshold=20.0) == "NORMAL"
    assert not HeadPoseEstimator.is_looking_away(yaw=5.0, pitch=-3.0, yaw_threshold=25.0, pitch_threshold=20.0)

    # Looking Right
    assert HeadPoseEstimator.get_gaze_direction(yaw=30.0, pitch=0.0, yaw_threshold=25.0, pitch_threshold=20.0) == "LOOKING_RIGHT"
    assert HeadPoseEstimator.is_looking_away(yaw=30.0, pitch=0.0, yaw_threshold=25.0, pitch_threshold=20.0)

    # Looking Left
    assert HeadPoseEstimator.get_gaze_direction(yaw=-35.0, pitch=0.0, yaw_threshold=25.0, pitch_threshold=20.0) == "LOOKING_LEFT"
    assert HeadPoseEstimator.is_looking_away(yaw=-35.0, pitch=0.0, yaw_threshold=25.0, pitch_threshold=20.0)

    # Looking Up (OpenCV image-space pitch: head-up produces negative pitch)
    assert HeadPoseEstimator.get_gaze_direction(yaw=0.0, pitch=-25.0, yaw_threshold=25.0, pitch_threshold=20.0) == "LOOKING_UP"
    assert HeadPoseEstimator.is_looking_away(yaw=0.0, pitch=-25.0, yaw_threshold=25.0, pitch_threshold=20.0)

    # Looking Down
    assert HeadPoseEstimator.get_gaze_direction(yaw=0.0, pitch=25.0, yaw_threshold=25.0, pitch_threshold=20.0) == "LOOKING_DOWN"
    assert HeadPoseEstimator.is_looking_away(yaw=0.0, pitch=25.0, yaw_threshold=25.0, pitch_threshold=20.0)


def test_calibrated_pose_uses_relative_deviation_and_rejects_outlier():
    samples = [(12.0 + (index % 2) * 0.4, -9.0 + (index % 3) * 0.3) for index in range(12)]
    samples.append((80.0, -70.0))
    baseline = HeadPoseEstimator.robust_baseline(samples)

    assert baseline is not None
    assert abs(baseline[0] - 12.4) < 0.5
    assert abs(baseline[1] - -8.7) < 0.5
    assert HeadPoseEstimator.get_relative_gaze_direction(19.0, 12.0, 20.0, 18.0) == "NORMAL"
    assert HeadPoseEstimator.get_relative_gaze_direction(-21.0, 0.0, 20.0, 18.0) == "LOOKING_LEFT"
    assert HeadPoseEstimator.get_relative_gaze_direction(0.0, -19.0, 20.0, 18.0) == "LOOKING_UP"


def test_calibration_requires_enough_valid_samples():
    assert HeadPoseEstimator.robust_baseline([(10.0, -5.0)] * 7) is None


def test_pitch_hysteresis_tolerates_normal_vertical_motion():
    assert HeadPoseEstimator.get_relative_gaze_direction(
        0.0, -10.0, 22.0, 20.0, 12.0, 20.0, prev_state="NORMAL"
    ) == "NORMAL"
    assert HeadPoseEstimator.get_relative_gaze_direction(
        0.0, -18.0, 22.0, 20.0, 12.0, 20.0, prev_state="NORMAL"
    ) == "NORMAL"
    assert HeadPoseEstimator.get_relative_gaze_direction(
        0.0, -28.0, 22.0, 20.0, 12.0, 20.0, prev_state="NORMAL"
    ) == "LOOKING_UP"
    assert HeadPoseEstimator.get_relative_gaze_direction(
        0.0, -17.0, 22.0, 20.0, 12.0, 20.0, prev_state="LOOKING_UP"
    ) == "LOOKING_UP"
    assert HeadPoseEstimator.get_relative_gaze_direction(
        0.0, -12.0, 22.0, 20.0, 12.0, 20.0, prev_state="LOOKING_UP"
    ) == "NORMAL"


def test_pitch_sign_matches_physical_up_down_direction():
    assert HeadPoseEstimator.get_relative_gaze_direction(
        0.0, -20.0, 22.0, 20.0, 12.0, 20.0, prev_state="NORMAL"
    ) == "LOOKING_UP"
    assert HeadPoseEstimator.get_relative_gaze_direction(
        0.0, 20.0, 22.0, 20.0, 12.0, 20.0, prev_state="NORMAL"
    ) == "LOOKING_DOWN"


def test_mixed_axis_direction_uses_larger_deviation():
    assert HeadPoseEstimator.get_relative_gaze_direction(-18.0, 22.0, 22.0, 20.0, 12.0, 20.0, prev_state="NORMAL") == "LOOKING_DOWN"
    assert HeadPoseEstimator.get_relative_gaze_direction(22.0, 12.0, 22.0, 20.0, 12.0, 20.0, prev_state="NORMAL") == "LOOKING_RIGHT"
    assert HeadPoseEstimator.get_relative_gaze_direction(-22.0, 12.0, 22.0, 20.0, 12.0, 20.0, prev_state="NORMAL") == "LOOKING_LEFT"


def test_phone_class_names_are_normalized_for_detection_filtering():
    assert YoloDetector.is_phone_class_name("cell phone") is True
    assert YoloDetector.is_phone_class_name("cell_phone") is True
    assert YoloDetector.is_phone_class_name("mobile phone") is True
    assert YoloDetector.is_phone_class_name("person") is False


def test_warning_count_tracks_session_incident_records(tmp_path: Path):
    db = DatabaseManager(tmp_path / "warning-count.db")
    db.init_db()
    student_id = db.add_student("Warn Student", "W001", "Math", "MATH1")
    session_id = db.create_session(student_id)

    engine = ProctorEngine()
    engine.session_id = session_id
    engine.incident_logger.db_manager = db
    engine._log_incident("LOOKING_AWAY", 0.95, "MEDIUM", "looked away")
    assert engine.warning_count == 1
    engine._log_incident("FACE_MISSING", 1.0, "MEDIUM", "face missing")
    assert engine.warning_count == 2

    incidents = db.get_session_incidents(session_id)
    assert len(incidents) == 2
    assert [incident["event_type"] for incident in incidents] == ["LOOKING_AWAY", "FACE_MISSING"]


def test_pitch_deviation_sign_mapping_is_canonical():
    assert HeadPoseEstimator._classify_vertical_deviation(-20.0, 18.0, 12.0, None) == "LOOKING_UP"
    assert HeadPoseEstimator._classify_vertical_deviation(20.0, 18.0, 12.0, None) == "LOOKING_DOWN"
    assert HeadPoseEstimator._classify_vertical_deviation(-10.0, 18.0, 12.0, None) == "NORMAL"


def test_looking_away_incident_rearms_after_return_to_screen(tmp_path: Path):
    db = DatabaseManager(tmp_path / "gaze-rearm.db")
    db.init_db()
    student_id = db.add_student("Gaze Student", "G001", "Physics", "PHY1")
    session_id = db.create_session(student_id)

    engine = ProctorEngine()
    engine.session_id = session_id
    engine.incident_logger.db_manager = db

    assert engine._update_looking_away_incident(is_abnormal=True, now=0.0, threshold_seconds=3.0) is False
    assert engine._update_looking_away_incident(is_abnormal=True, now=2.5, threshold_seconds=3.0) is False
    assert engine._update_looking_away_incident(is_abnormal=True, now=3.0, threshold_seconds=3.0) is True
    engine._log_incident("LOOKING_AWAY", 0.95, "MEDIUM", "confirmed looking away")
    engine._refresh_warning_count()
    assert engine.warning_count == 1

    assert engine._update_looking_away_incident(is_abnormal=True, now=4.0, threshold_seconds=3.0) is False
    assert engine.warning_count == 1

    assert engine._update_looking_away_incident(is_abnormal=False, now=5.0, threshold_seconds=3.0) is False
    assert engine.warning_count == 1

    assert engine._update_looking_away_incident(is_abnormal=True, now=8.0, threshold_seconds=3.0) is False
    assert engine._update_looking_away_incident(is_abnormal=True, now=11.0, threshold_seconds=3.0) is True
    engine._log_incident("LOOKING_AWAY", 0.95, "MEDIUM", "second confirmed looking away")
    engine._refresh_warning_count()
    assert engine.warning_count == 2


def test_report_aggregation_counts_each_incident_row(tmp_path: Path):
    db = DatabaseManager(tmp_path / "report-aggregation.db")
    db.init_db()
    student_id = db.add_student("Report Student", "R001", "Math", "MATH2")
    session_id = db.create_session(student_id)

    for idx in range(3):
        db.add_incident(session_id, "LOOKING_AWAY", 0.95, "MEDIUM", f"away {idx + 1}")

    incidents = db.get_session_incidents(session_id)
    counts = {"LOOKING_AWAY": 0}
    for incident in incidents:
        if incident["event_type"] == "LOOKING_AWAY":
            counts["LOOKING_AWAY"] += 1

    assert counts["LOOKING_AWAY"] == 3
    assert len(incidents) == 3


def test_new_session_warning_count_resets(tmp_path: Path):
    db = DatabaseManager(tmp_path / "session-reset.db")
    db.init_db()
    student_id = db.add_student("Session Student", "S001", "CS", "CS1")
    session_id = db.create_session(student_id)

    engine = ProctorEngine()
    engine.session_id = session_id
    engine.incident_logger.db_manager = db
    engine._log_incident("LOOKING_AWAY", 0.95, "MEDIUM", "first")
    engine._refresh_warning_count()
    assert engine.warning_count == 1

    new_session_id = db.create_session(student_id)
    engine.session_id = new_session_id
    engine._refresh_warning_count()
    assert engine.warning_count == 0


def test_phone_state_requires_confirmation_and_keeps_grace_period():
    engine = ProctorEngine()
    engine.phone_detected = False
    engine._previous_phone_detected = False
    engine._phone_consecutive = 0
    engine._phone_missing_consecutive = 0

    engine._phone_consecutive = 2
    engine.phone_detected = True
    engine._previous_phone_detected = False
    engine._log_incident("PHONE_DETECTED", 0.8, "HIGH", "phone present")
    assert engine.warning_count == 0

    engine._phone_missing_consecutive = 1
    engine.phone_detected = True
    assert engine.phone_detected is True
    engine._phone_missing_consecutive = 3
    engine.phone_detected = False
    assert engine.phone_detected is False

    engine._phone_consecutive = 2
    engine.phone_detected = True
    engine._previous_phone_detected = False
    engine._log_incident("PHONE_DETECTED", 0.8, "HIGH", "phone present again")
    assert engine.phone_detected is True


def test_gaze_canonical_state_is_consistent_for_overlay_and_dashboard():
    engine = ProctorEngine()
    for direction in ["LOOKING_LEFT", "LOOKING_UP", "LOOKING_DOWN", "LOOKING_RIGHT", "NORMAL"]:
        engine._last_gaze_direction = direction
        canonical = engine._canonical_gaze_direction(direction)
        status = engine.get_status()
        assert status["gaze_direction"] == canonical
        if canonical == "LOOKING_AT_SCREEN":
            assert status["live_checks"]["looking_at_screen"] in {True, False}


def test_settings_store_defaults_and_validation():
    settings = get_settings()
    assert "FACE_MISSING_SEC" in settings
    assert "LOOKING_AWAY_ANGLE_DEG" in settings
    assert "LOOKING_AWAY_PITCH_DEG" in settings
    assert "PHONE_CONFIRM_FRAMES" in settings
    assert "MULTI_PERSON_CONFIRM_FRAMES" in settings
    assert "RISK_WEIGHTS" in settings
    assert settings["RISK_WEIGHTS"]["PHONE_DETECTED"] == 40


def test_database_manager_operations(tmp_path: Path):
    db = DatabaseManager(tmp_path / "db-test.db")
    db.init_db()

    student_id = db.add_student("Alice Smith", "1CS21AI042", "Operating Systems", "OS202")
    assert student_id > 0

    session_id = db.create_session(student_id)
    assert session_id > 0

    inc_id = db.add_incident(
        session_id=session_id,
        event_type="LOOKING_AWAY",
        confidence=0.95,
        severity="MEDIUM",
        details="Looking left for 3.5 seconds",
    )
    assert inc_id > 0

    incidents = db.get_session_incidents(session_id)
    assert len(incidents) == 1
    assert incidents[0]["event_type"] == "LOOKING_AWAY"

    db.end_session(
        session_id=session_id,
        end_time="2026-08-28T15:00:00",
        duration=180,
        risk_score=15.0,
        risk_category="Low",
        status="completed",
    )

    all_sessions = db.get_all_sessions()
    assert len(all_sessions) == 1
    assert all_sessions[0]["status"] == "completed"
    assert all_sessions[0]["risk_category"] == "Low"
