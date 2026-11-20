import os
import re
from datetime import datetime, timedelta
import pytest
from flask import session

from app import app
from backend.database.db_manager import DatabaseManager
from backend.routes import admin_routes, auth_routes, learning_routes


@pytest.fixture
def publishing_test_context(tmp_path, monkeypatch):
    test_db = tmp_path / "test_pub_grading.db"
    db = DatabaseManager(db_path=test_db)
    db.init_db()

    monkeypatch.setattr(admin_routes, "db_manager", db)
    monkeypatch.setattr(auth_routes, "db_manager", db)
    monkeypatch.setattr(learning_routes, "db_manager", db)
    import app as app_module
    monkeypatch.setattr(app_module, "DatabaseManager", lambda: db)

    # Seed Admin and Teacher
    with db.get_connection() as conn:
        conn.execute("DELETE FROM admin_users")
        admin_id = conn.execute(
            """
            INSERT INTO admin_users (email, password_hash, name, role, active)
            VALUES (?, ?, ?, 'Super Admin', 1)
            """,
            ("admin@example.test", "pbkdf2:sha256:600000$test$hash", "System Admin"),
        ).lastrowid
        teacher_id = conn.execute(
            """
            INSERT INTO admin_users (email, password_hash, name, role, active)
            VALUES (?, ?, ?, 'Teacher', 1)
            """,
            ("teacher@example.test", "pbkdf2:sha256:600000$test$hash", "Professor X"),
        ).lastrowid

    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()

    yield {
        "client": client,
        "database": db,
        "admin_id": admin_id,
        "teacher_id": teacher_id,
        "test_db": test_db,
    }


def _login_admin(client, admin_id=1, role="Super Admin", name="System Admin"):
    with client.session_transaction() as sess:
        sess.clear()
        sess["admin_user_id"] = admin_id
        sess["admin_role"] = role
        sess["admin_name"] = name
        sess["admin_csrf_token"] = "valid_csrf_token_123"
        sess["csrf_token"] = "valid_csrf_token_123"


# ==============================================================================
# PHASE 1 TESTS: EXAMINATION PUBLISHING & CODE VISIBILITY
# ==============================================================================

def test_exam_publish_endpoint_generates_and_returns_code(publishing_test_context):
    client = publishing_test_context["client"]
    db = publishing_test_context["database"]
    teacher_id = publishing_test_context["teacher_id"]
    _login_admin(client, teacher_id, "Teacher", "Professor X")

    # 1. Create a draft exam with 2 questions
    exam_id = db.create_examination_with_questions(
        name="Computer Architecture",
        subject_name="Computer Science",
        department="Computer Science & Engineering",
        semester="Semester 5",
        duration_minutes=45,
        max_marks=10,
        status="draft",
        created_by=teacher_id,
        questions=[
            {
                "statement": "What does ALU stand for?",
                "question_type": "mcq",
                "marks": 5.0,
                "options": [
                    {"option_text": "Arithmetic Logic Unit", "is_correct": True},
                    {"option_text": "Algorithm Logic Unit", "is_correct": False},
                ],
            },
            {
                "statement": "What is cache memory?",
                "question_type": "short_answer",
                "marks": 5.0,
                "correct_answer_text": "High speed buffer",
            },
        ],
    )

    # 2. Call POST /admin/exams/<id>/publish
    resp = client.post(f"/admin/exams/{exam_id}/publish", json={}, headers={"X-CSRFToken": "valid_csrf_token_123"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["status"] == "published"
    assert "exam_code" in data
    exam_code = data["exam_code"]
    assert exam_code is not None
    assert len(exam_code) >= 6

    # 3. Check permanent persistence in DB
    exam_rec = db.get_exam(exam_id)
    assert exam_rec["status"] == "published"
    assert exam_rec["exam_code"] == exam_code

    # 4. Calling publish again does NOT regenerate or overwrite the code
    resp2 = client.post(f"/admin/exams/{exam_id}/publish", json={}, headers={"X-CSRFToken": "valid_csrf_token_123"})
    assert resp2.status_code == 200
    data2 = resp2.get_json()
    assert data2["exam_code"] == exam_code


def test_exam_publish_blocks_empty_exam(publishing_test_context):
    client = publishing_test_context["client"]
    db = publishing_test_context["database"]
    teacher_id = publishing_test_context["teacher_id"]
    _login_admin(client, teacher_id, "Teacher", "Professor X")

    empty_id = db.create_examination_with_questions(
        name="Empty Paper",
        subject_name="Biology",
        department="Computer Science & Engineering",
        semester="Semester 5",
        status="draft",
        created_by=teacher_id,
        questions=[],
    )

    resp = client.post(f"/admin/exams/{empty_id}/publish", json={}, headers={"X-CSRFToken": "valid_csrf_token_123"})
    assert resp.status_code == 400
    assert "no questions" in resp.get_json()["error"]


def test_exam_create_and_status_change_return_exam_code(publishing_test_context):
    client = publishing_test_context["client"]
    db = publishing_test_context["database"]
    teacher_id = publishing_test_context["teacher_id"]
    _login_admin(client, teacher_id, "Teacher", "Professor X")

    # Exam create as published
    payload = {
        "title": "Discrete Mathematics",
        "subject_name": "Mathematics",
        "department": "Computer Science & Engineering",
        "semester": "Semester 3",
        "duration": 45,
        "status": "published",
        "questions": [
            {
                "statement": "What is a graph?",
                "question_type": "mcq",
                "marks": 5.0,
                "options": [
                    {"option_text": "Nodes and edges", "is_correct": True},
                    {"option_text": "A vegetable", "is_correct": False},
                ],
            }
        ],
    }
    resp = client.post("/admin/exams/create", json=payload, headers={"X-CSRFToken": "valid_csrf_token_123"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "exam_code" in data
    assert data["exam_code"] is not None

    # Status change on draft exam to published returns exam_code
    draft_id = db.create_examination_with_questions(
        name="Draft Physics",
        subject_name="Physics",
        department="Computer Science & Engineering",
        semester="Semester 5",
        status="draft",
        created_by=teacher_id,
        questions=[{"statement": "Gravity?", "marks": 1, "options": [{"option_text": "9.8", "is_correct": True}, {"option_text": "0", "is_correct": False}]}],
    )
    status_resp = client.post(f"/admin/exams/{draft_id}/status", json={"status": "published"}, headers={"X-CSRFToken": "valid_csrf_token_123"})
    assert status_resp.status_code == 200
    s_data = status_resp.get_json()
    assert s_data["success"] is True
    assert "exam_code" in s_data
    assert s_data["exam_code"] is not None


# ==============================================================================
# PHASE 2 TESTS: GRADING SYSTEM & OBJECTIVE/SUBJECTIVE EVALUATION
# ==============================================================================

def test_objective_grading_all_types_no_cartesian_multiplication(publishing_test_context):
    """
    Tests grading across:
    - MCQ
    - Multiple Select (with 2 correct options, verifying NO row duplication Cartesian product!)
    - Numerical (with floating point tolerance)
    - Fill in the blank (case/space insensitive)
    - True/False
    """
    client = publishing_test_context["client"]
    db = publishing_test_context["database"]

    # Create Exam
    exam_id = db.create_examination_with_questions(
        name="Comprehensive Engineering Assessment",
        subject_name="Computer Science",
        department="Computer Science & Engineering",
        semester="Semester 5",
        duration_minutes=30,
        max_marks=10.0,
        pass_marks=5.0,
        status="published",
        questions=[
            # 1. MCQ (2 marks)
            {
                "statement": "Which protocol is connection-oriented?",
                "question_type": "mcq",
                "marks": 2.0,
                "options": [
                    {"option_text": "TCP", "is_correct": True},
                    {"option_text": "UDP", "is_correct": False},
                ],
            },
            # 2. Multiple Select (3 marks) - HAS 2 CORRECT OPTIONS!
            {
                "statement": "Select all valid transport layer protocols.",
                "question_type": "multiple_select",
                "marks": 3.0,
                "options": [
                    {"option_text": "TCP", "is_correct": True},
                    {"option_text": "UDP", "is_correct": True},
                    {"option_text": "HTTP", "is_correct": False},
                    {"option_text": "IP", "is_correct": False},
                ],
            },
            # 3. Numerical (2 marks)
            {
                "statement": "What is the speed of light in 10^8 m/s (approx)?",
                "question_type": "numerical",
                "marks": 2.0,
                "correct_answer_text": "3.0",
            },
            # 4. Fill in the blank (2 marks)
            {
                "statement": "The process of converting source code to machine code is called _____.",
                "question_type": "fill_in_the_blanks",
                "marks": 2.0,
                "correct_answer_text": "compilation",
            },
            # 5. True/False (1 mark)
            {
                "statement": "Python is an interpreted language.",
                "question_type": "true_false",
                "marks": 1.0,
                "options": [
                    {"option_text": "True", "is_correct": True},
                    {"option_text": "False", "is_correct": False},
                ],
            },
        ],
    )

    exam_record = db.get_exam(exam_id)
    questions = exam_record["questions"]
    q_mcq = questions[0]
    q_msq = questions[1]
    q_num = questions[2]
    q_fill = questions[3]
    q_tf = questions[4]

    # Create Student & Attempt
    student_id = db.add_student("Diya Sen", "1CS23AI088", "Computer Science", exam_record["exam_code"])
    attempt_id = db.create_attempt(student_id, exam_id)

    # Get correct option IDs from DB directly
    with db.get_connection() as conn:
        def get_corr_opts(qid):
            return [r["option_id"] for r in conn.execute("SELECT option_id FROM question_options WHERE question_id = ? AND is_correct = 1", (qid,)).fetchall()]
        tcp_opt = get_corr_opts(q_mcq["question_id"])[0]
        correct_msq_ids = get_corr_opts(q_msq["question_id"])
        tf_opt = get_corr_opts(q_tf["question_id"])[0]

    with client.session_transaction() as sess:
        sess.clear()
        sess["student_id"] = student_id

    # 1. Answer Q1: TCP
    client.post(f"/api/attempt/{attempt_id}/answer", json={"question_id": q_mcq["question_id"], "option_id": tcp_opt})

    # 2. Answer Q2: TCP and UDP (multi-select with option_ids)
    client.post(f"/api/attempt/{attempt_id}/answer", json={
        "question_id": q_msq["question_id"],
        "option_ids": correct_msq_ids,
    })

    # 3. Answer Q3: 3.001 (within float tolerance of 3.0)
    client.post(f"/api/attempt/{attempt_id}/answer", json={"question_id": q_num["question_id"], "answer_text": "3.001"})

    # 4. Answer Q4: "Compilation " (case/whitespace trimmed)
    client.post(f"/api/attempt/{attempt_id}/answer", json={"question_id": q_fill["question_id"], "answer_text": "  Compilation  "})

    # 5. Answer Q5: True
    client.post(f"/api/attempt/{attempt_id}/answer", json={"question_id": q_tf["question_id"], "option_id": tf_opt})

    # Submit
    sub_resp = client.post(f"/attempt/{attempt_id}/submit")
    assert sub_resp.status_code == 302

    # Check results
    attempt_res = db.get_attempt(attempt_id)
    res = attempt_res["result"]
    assert res is not None

    # Total marks must be exactly 2 + 3 + 2 + 2 + 1 = 10.0 (NO Cartesian duplicate row doubling!)
    assert res["marks_obtained"] == 10.0
    assert attempt_res["max_marks"] == 10.0
    assert res["percentage"] == 100.0
    assert res["correct_answers"] == 5
    assert res["incorrect_answers"] == 0
    assert res["unanswered"] == 0
    assert res["status"] == "Passed"
    assert res["evaluation_status"] == "evaluated"
    assert res["is_published"] == 1


def test_subjective_evaluation_pipeline_and_recalculation(publishing_test_context):
    client = publishing_test_context["client"]
    db = publishing_test_context["database"]
    teacher_id = publishing_test_context["teacher_id"]

    # Exam with 1 MCQ (5 marks) and 1 Long Answer (5 marks)
    exam_id = db.create_examination_with_questions(
        name="Software Design Patterns",
        subject_name="Software Engineering",
        department="Computer Science & Engineering",
        semester="Semester 5",
        duration_minutes=30,
        max_marks=10.0,
        status="published",
        created_by=teacher_id,
        questions=[
            {
                "statement": "Which pattern ensures only one instance of a class exists?",
                "question_type": "mcq",
                "marks": 5.0,
                "options": [
                    {"option_text": "Singleton", "is_correct": True},
                    {"option_text": "Factory", "is_correct": False},
                ],
            },
            {
                "statement": "Explain the architectural advantages of the Observer pattern.",
                "question_type": "long_answer",
                "marks": 5.0,
                "correct_answer_text": "Decouples subjects and observers",
            },
        ],
    )

    exam = db.get_exam(exam_id)
    q_mcq = exam["questions"][0]
    q_essay = exam["questions"][1]

    # Student attempt
    student_id = db.add_student("Rohan Mehta", "1CS23AI055", "Software Engineering", exam["exam_code"])
    attempt_id = db.create_attempt(student_id, exam_id)

    with client.session_transaction() as sess:
        sess.clear()
        sess["student_id"] = student_id

    # Answer MCQ correctly, essay with text
    with db.get_connection() as conn:
        sing_opt = conn.execute("SELECT option_id FROM question_options WHERE question_id = ? AND is_correct = 1", (q_mcq["question_id"],)).fetchone()["option_id"]
    client.post(f"/api/attempt/{attempt_id}/answer", json={"question_id": q_mcq["question_id"], "option_id": sing_opt})
    client.post(f"/api/attempt/{attempt_id}/answer", json={"question_id": q_essay["question_id"], "answer_text": "Promotes loose coupling and 1-to-many dependency updates."})
    client.post(f"/attempt/{attempt_id}/submit")

    # Before teacher evaluation: pending_evaluation, unpublished, score is 5.0/10.0
    attempt = db.get_attempt(attempt_id)
    assert attempt["result"]["evaluation_status"] == "pending_evaluation"
    assert attempt["result"]["is_published"] == 0
    assert attempt["result"]["marks_obtained"] == 5.0
    assert attempt["result"]["percentage"] == 50.0

    # Teacher logs in and evaluates attempt
    _login_admin(client, teacher_id, "Teacher", "Professor X")

    # Save evaluation with 4.5/5 marks
    save_resp = client.post(
        f"/admin/evaluations/attempt/{attempt_id}/save",
        json={
            "evaluations": [{"question_id": q_essay["question_id"], "marks_awarded": 4.5, "feedback": "Clear explanation."}],
            "publish": False,
        },
        headers={"X-CSRFToken": "valid_csrf_token_123"},
    )
    assert save_resp.status_code == 200
    assert save_resp.get_json()["success"] is True

    # Check updated attempt: 5 (MCQ) + 4.5 (Essay) = 9.5 / 10 = 95.0%, evaluated, but still unpublished
    updated = db.get_attempt(attempt_id)
    assert updated["result"]["marks_obtained"] == 9.5
    assert updated["result"]["percentage"] == 95.0
    assert updated["result"]["is_published"] == 0
    assert updated["result"]["evaluation_status"] == "evaluated"

    # Teacher publishes evaluation
    pub_resp = client.post(
        f"/admin/evaluations/attempt/{attempt_id}/publish",
        json={},
        headers={"X-CSRFToken": "valid_csrf_token_123"},
    )
    assert pub_resp.status_code == 200
    final = db.get_attempt(attempt_id)
    assert final["result"]["is_published"] == 1
    assert final["result"]["evaluation_status"] == "published"


# ==============================================================================
# PHASE 3 TESTS: NUMERIC CONFIGURATION DEFAULTS
# ==============================================================================

def test_numeric_configuration_defaults_are_one(publishing_test_context):
    client = publishing_test_context["client"]
    db = publishing_test_context["database"]
    teacher_id = publishing_test_context["teacher_id"]
    _login_admin(client, teacher_id, "Teacher", "Professor X")

    # Check create exam template has value="1" for duration
    resp = client.get("/admin/exams/create")
    assert resp.status_code == 200
    assert b'id="exam-duration"' in resp.data
    assert b'value="1"' in resp.data

    # Check db_manager default duration is 1
    exam_id = db.create_examination_with_questions(
        name="Default Duration Test",
        subject_name="General",
        status="draft",
        questions=[{"statement": "Q?", "marks": 1, "options": [{"option_text": "A", "is_correct": True}]}],
    )
    exam = db.get_exam(exam_id)
    assert exam["duration_minutes"] == 1


# ==============================================================================
# PHASE 4 TESTS: SIMPLIFIED STUDENT LOGIN
# ==============================================================================

def test_simplified_student_login_with_valid_exam_code(publishing_test_context):
    client = publishing_test_context["client"]
    db = publishing_test_context["database"]

    # 1. Create a published examination
    exam_id = db.create_examination_with_questions(
        name="Operating Systems Quiz",
        subject_name="Operating Systems",
        department="Computer Science & Engineering",
        semester="Semester 5",
        status="published",
        questions=[{"statement": "What is a process?", "marks": 1, "options": [{"option_text": "Program in execution", "is_correct": True}]}],
    )
    exam = db.get_exam(exam_id)
    code = exam["exam_code"]

    # 2. Student logs in with only 3 fields: Name, USN, Exam Code
    resp = client.post(
        "/login",
        data={
            "name": "Vikram Rathore",
            "usn": "1CS23AI077",
            "exam_code": code,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # Redirects directly to the exam instructions for that exam
    assert f"/exam/{exam_id}/instructions" in resp.headers["Location"]

    with client.session_transaction() as sess:
        assert sess["student_name"] == "Vikram Rathore"
        assert sess["student_usn"] == "1CS23AI077"
        assert sess["student_exam_code"] == code


def test_simplified_student_login_rejects_invalid_exam_code(publishing_test_context):
    client = publishing_test_context["client"]

    # Attempt login with non-existent code
    resp = client.post(
        "/login",
        data={
            "name": "Invalid Code User",
            "usn": "1CS23AI999",
            "exam_code": "FAKE-CODE-000",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b"Invalid examination code" in resp.data


def test_simplified_student_login_validates_required_fields(publishing_test_context):
    client = publishing_test_context["client"]

    # Missing USN
    resp = client.post(
        "/login",
        data={
            "name": "Test User",
            "usn": "",
            "exam_code": "CODE-123",
        },
    )
    assert resp.status_code == 200
    assert b"required" in resp.data


def test_legacy_student_login_compatibility_preserved(publishing_test_context):
    client = publishing_test_context["client"]

    # Post with legacy subject field (as older tests do)
    resp = client.post(
        "/login",
        data={
            "name": "Legacy Candidate",
            "usn": "LEGACY99",
            "subject": "Cyber Forensics",
            "exam_code": "CF404",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/dashboard" in resp.headers["Location"]
    with client.session_transaction() as sess:
        assert sess["student_name"] == "Legacy Candidate"
        assert sess["student_usn"] == "LEGACY99"
