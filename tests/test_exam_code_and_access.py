import os
import re
from datetime import datetime, timedelta
import pytest
from flask import session

from app import app
from backend.database.db_manager import DatabaseManager
from backend.routes import admin_routes, auth_routes, learning_routes


@pytest.fixture
def exam_test_context(tmp_path, monkeypatch):
    test_db = tmp_path / "test_exam_shield.db"
    db = DatabaseManager(db_path=test_db)
    db.init_db()

    # Monkey-patch blueprint db_managers to use the test database
    monkeypatch.setattr(admin_routes, "db_manager", db)
    monkeypatch.setattr(auth_routes, "db_manager", db)
    monkeypatch.setattr(learning_routes, "db_manager", db)
    import app as app_module
    monkeypatch.setattr(app_module, "DatabaseManager", lambda: db)

    # Create test student
    with db.get_connection() as conn:
        conn.execute("DELETE FROM students")
        cur = conn.execute(
            """
            INSERT INTO students (name, usn, subject, exam_code, department, semester, account_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Aarav Sharma",
                "1CS23AI099",
                "Computer Networks",
                "CN4-M4P9X2",
                "Computer Science & Engineering",
                "Semester 5",
                "Active",
            ),
        )
        student_id = cur.lastrowid

    # Configure app for testing
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()

    yield {
        "client": client,
        "database": db,
        "student_id": student_id,
        "test_db": test_db,
    }


def test_unique_exam_code_generation_and_format(exam_test_context):
    db = exam_test_context["database"]

    # Test code generation with various parameters
    code1 = db.generate_exam_code(subject_name="Computer Networks", semester="Semester 5", exam_name="Midterm 1")
    code2 = db.generate_exam_code(subject_name="Mathematics", semester="Semester 3", exam_name="Final")
    code3 = db.generate_exam_code(subject_name="Software Engineering", semester="", exam_name="Quiz")

    # Format check: {PREFIX}-{TOKEN}
    assert "-" in code1
    prefix, token = code1.split("-")
    assert prefix.startswith("CN")
    assert "5" in prefix
    assert len(token) == 6
    # No ambiguous characters: 0, O, 1, I
    for ch in "0O1I":
        assert ch not in token

    # Check collision resistance by generating 50 codes
    codes = {db.generate_exam_code("Data Structures", "3", "Exam") for _ in range(50)}
    assert len(codes) == 50


def test_exam_creation_and_publishing_code_assignment(exam_test_context):
    db = exam_test_context["database"]

    # 1. Create exam with questions
    exam_id = db.create_examination_with_questions(
        name="Operating Systems End-Term",
        subject_name="Operating Systems",
        department="Computer Science & Engineering",
        semester="Semester 5",
        duration_minutes=45,
        max_marks=20,
        status="published",
        questions=[
            {
                "statement": "What is virtual memory?",
                "question_type": "mcq",
                "marks": 5.0,
                "options": [
                    {"option_text": "Memory management capability", "is_correct": True},
                    {"option_text": "Physical RAM only", "is_correct": False},
                ],
            }
        ],
    )

    exam = db.get_exam(exam_id)
    assert exam is not None
    assert exam["exam_code"] is not None
    assert len(exam["exam_code"]) >= 6

    # Verify get_exam_by_code
    exam_by_code = db.get_exam_by_code(exam["exam_code"])
    assert exam_by_code is not None
    assert exam_by_code["exam_id"] == exam_id

    # Case-insensitivity check
    exam_by_code_lower = db.get_exam_by_code(exam["exam_code"].lower())
    assert exam_by_code_lower is not None
    assert exam_by_code_lower["exam_id"] == exam_id


def test_validate_student_exam_access_rules(exam_test_context):
    db = exam_test_context["database"]
    student_id = exam_test_context["student_id"]

    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    # 1. Non-existent code
    valid, msg, _ = db.validate_student_exam_access(student_id, exam_code="INVALID-CODE-999")
    assert not valid
    assert "Invalid examination code" in msg

    # 2. Draft exam
    draft_id = db.create_examination_with_questions(
        name="Draft Biology",
        subject_name="Biology",
        department="Computer Science & Engineering",
        semester="Semester 5",
        status="draft",
        questions=[{"statement": "Cell structure?", "marks": 2, "options": [{"option_text": "A", "is_correct": True}]}],
    )
    draft_exam = db.get_exam(draft_id)
    valid, msg, _ = db.validate_student_exam_access(student_id, exam_code=draft_exam["exam_code"])
    assert not valid
    assert "not available" in msg

    # 3. Exam with 0 questions
    with db.get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO exams (bank_id, name, duration_minutes, max_marks, status, exam_code, department, semester)
            VALUES (1, 'Empty Exam', 30, 0, 'published', 'EMPTY-123456', 'Computer Science & Engineering', 'Semester 5')
            """
        )
        empty_id = cur.lastrowid
    valid, msg, _ = db.validate_student_exam_access(student_id, exam_id=empty_id)
    assert not valid
    assert "no questions available" in msg

    # 4. Future scheduled exam
    future_start = (now + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    future_id = db.create_examination_with_questions(
        name="Future Exam",
        subject_name="Math",
        department="Computer Science & Engineering",
        semester="Semester 5",
        start_time=future_start,
        status="scheduled",
        questions=[{"statement": "Q1", "marks": 1, "options": [{"option_text": "A", "is_correct": True}]}],
    )
    valid, msg, _ = db.validate_student_exam_access(student_id, exam_id=future_id)
    assert not valid
    assert "Examination has not started yet" in msg

    # 5. Expired exam
    past_start = (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    past_end = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    past_id = db.create_examination_with_questions(
        name="Past Exam",
        subject_name="Math",
        department="Computer Science & Engineering",
        semester="Semester 5",
        start_time=past_start,
        end_time=past_end,
        status="published",
        questions=[{"statement": "Q1", "marks": 1, "options": [{"option_text": "A", "is_correct": True}]}],
    )
    valid, msg, _ = db.validate_student_exam_access(student_id, exam_id=past_id)
    assert not valid
    assert "Examination has ended" in msg

    # 6. Department mismatch
    wrong_dept_id = db.create_examination_with_questions(
        name="Mechanical Thermo",
        subject_name="Thermodynamics",
        department="Mechanical Engineering",
        semester="Semester 5",
        status="published",
        questions=[{"statement": "Q1", "marks": 1, "options": [{"option_text": "A", "is_correct": True}]}],
    )
    valid, msg, _ = db.validate_student_exam_access(student_id, exam_id=wrong_dept_id)
    assert not valid
    assert "department required" in msg

    # 7. Semester mismatch
    wrong_sem_id = db.create_examination_with_questions(
        name="Sem 1 Physics",
        subject_name="Physics",
        department="Computer Science & Engineering",
        semester="Semester 1",
        status="published",
        questions=[{"statement": "Q1", "marks": 1, "options": [{"option_text": "A", "is_correct": True}]}],
    )
    valid, msg, _ = db.validate_student_exam_access(student_id, exam_id=wrong_sem_id)
    assert not valid
    assert "semester" in msg

    # 8. Valid matching exam
    valid_id = db.create_examination_with_questions(
        name="Valid CS Exam",
        subject_name="Computer Science",
        department="Computer Science & Engineering",
        semester="Semester 5",
        status="published",
        questions=[{"statement": "Q1", "marks": 5, "options": [{"option_text": "A", "is_correct": True}]}],
    )
    valid_exam = db.get_exam(valid_id)
    valid, msg, exam = db.validate_student_exam_access(student_id, exam_code=valid_exam["exam_code"])
    assert valid
    assert "Access granted" in msg


def test_api_student_join_endpoint(exam_test_context):
    client = exam_test_context["client"]
    db = exam_test_context["database"]
    student_id = exam_test_context["student_id"]

    # 1. Unauthenticated request
    resp = client.post("/api/student/exams/join", json={"exam_code": "TEST-123456"})
    assert resp.status_code == 401

    # 2. Authenticated as student
    with client.session_transaction() as sess:
        sess["student_id"] = student_id
        sess["student_name"] = "Aarav Sharma"
        sess["student_usn"] = "1CS23AI099"

    # Empty code
    resp = client.post("/api/student/exams/join", json={"exam_code": ""})
    assert resp.status_code == 400
    assert "Please enter an examination code" in resp.get_json()["message"]

    # Invalid code
    resp = client.post("/api/student/exams/join", json={"exam_code": "NONEXISTENT-CODE"})
    assert resp.status_code == 400
    assert "Invalid examination code" in resp.get_json()["message"]

    # Create genuine exam
    exam_id = db.create_examination_with_questions(
        name="Artificial Intelligence Core",
        subject_name="Artificial Intelligence",
        department="Computer Science & Engineering",
        semester="Semester 5",
        status="published",
        questions=[
            {
                "statement": "What is heuristic search?",
                "marks": 2,
                "options": [
                    {"option_text": "Informed search method", "is_correct": True},
                    {"option_text": "Random guess", "is_correct": False},
                ],
            }
        ],
    )
    exam = db.get_exam(exam_id)

    # Valid join
    resp = client.post("/api/student/exams/join", json={"exam_code": exam["exam_code"]})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert data["exam_id"] == exam_id
    assert f"/exam/{exam_id}/instructions" in data["redirect_url"]


def test_student_dashboard_displays_real_exams_and_removes_fake_data(exam_test_context):
    client = exam_test_context["client"]
    db = exam_test_context["database"]
    student_id = exam_test_context["student_id"]

    # Create published exam for student cohort
    exam_id = db.create_examination_with_questions(
        name="Cloud Computing & DevOps",
        subject_name="Cloud Computing",
        department="Computer Science & Engineering",
        semester="Semester 5",
        duration_minutes=40,
        max_marks=30,
        status="published",
        questions=[{"statement": "What is IaaS?", "marks": 5, "options": [{"option_text": "Infra as a service", "is_correct": True}]}],
    )
    exam = db.get_exam(exam_id)

    with client.session_transaction() as sess:
        sess["student_id"] = student_id
        sess["student_name"] = "Aarav Sharma"
        sess["student_usn"] = "1CS23AI099"
        # Simulate legacy bogus session strings to verify they are NOT rendered as current exam
        sess["student_subject"] = "FAKE UNVERIFIED SUBJECT"
        sess["student_exam_code"] = "FAKE-EXAM-999"

    resp = client.get("/dashboard")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")

    # Verify genuine exam title and code appear
    assert "Cloud Computing &amp; DevOps" in html or "Cloud Computing & DevOps" in html
    assert exam["exam_code"] in html

    # Verify input field and join button exist
    assert 'id="examCodeInput"' in html
    assert 'id="joinExamBtn"' in html

    # Verify the fake session text is NOT displayed as the current examination
    assert "Current examination" not in html
    assert "FAKE UNVERIFIED SUBJECT" not in html


def test_server_timer_integrity_and_answer_key_protection(exam_test_context):
    db = exam_test_context["database"]
    student_id = exam_test_context["student_id"]

    # Create exam
    exam_id = db.create_examination_with_questions(
        name="Security & Cryptography",
        subject_name="Cybersecurity",
        department="Computer Science & Engineering",
        semester="Semester 5",
        duration_minutes=30,
        max_marks=10,
        status="published",
        questions=[
            {
                "statement": "What does RSA stand for?",
                "explanation": "Rivest Shamir Adleman",
                "marks": 5,
                "correct_answer_text": "Rivest, Shamir, and Adleman",
                "options": [
                    {"option_text": "Rivest Shamir Adleman", "is_correct": True},
                    {"option_text": "Random System Algorithm", "is_correct": False},
                ],
            }
        ],
    )

    # Student begins exam
    attempt_id = db.create_attempt(student_id, exam_id, mode="proctored")
    attempt = db.get_attempt(attempt_id)
    assert attempt is not None

    # Server timer: total_seconds = 1800, remaining_seconds <= 1800
    assert attempt["total_seconds"] == 1800
    assert 1795 <= attempt["remaining_seconds"] <= 1800

    # Answer keys must be redacted while in_progress
    q = attempt["questions"][0]
    assert "explanation" not in q
    assert "correct_answer_text" not in q
    assert "is_option_correct" not in q

    # Save student answer
    exam = db.get_exam(exam_id)
    selected_opt_id = exam["questions"][0]["options"][0]["option_id"]
    db.save_answer(attempt_id, q["question_id"], option_id=selected_opt_id)

    # Simulate page refresh by fetching attempt again
    refreshed_attempt = db.get_attempt(attempt_id)
    assert refreshed_attempt["questions"][0]["option_id"] == selected_opt_id
