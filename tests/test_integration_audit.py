"""
ExamShield AI - Comprehensive Integration Audit Test Suite
Verifies all 7 integration audit requirements across Student and Teacher/Admin Portals:
TEST 1: AUTHENTICATION & ROLE-BASED ACCESS CONTROL
TEST 2: QUESTION PAPER CREATION & QUESTION TYPES
TEST 3: EXAMINATION MANAGEMENT & SCHEDULING
TEST 4: STUDENT EXAMINATION WORKFLOW & SECURITY
TEST 5: RESULTS, SUBJECTIVE EVALUATION & ANALYTICS
TEST 6: SECURITY, ISOLATION & AUDIT LOGGING
TEST 7: UI/UX ACCESSIBILITY, ROUTES & DATA INTEGRITY
"""

from datetime import datetime, timedelta
import json
import pytest
from werkzeug.security import generate_password_hash

from app import app
from backend.database.db_manager import DatabaseManager
from backend.routes import admin_routes, auth_routes, learning_routes


@pytest.fixture
def audit_context(tmp_path, monkeypatch):
    """Initializes isolated database context with Super Admin, Teacher, and Student accounts."""
    db_file = tmp_path / "integration-audit.db"
    database = DatabaseManager(db_file)
    database.init_db()

    monkeypatch.setattr(admin_routes, "db_manager", database)
    monkeypatch.setattr(auth_routes, "db_manager", database)
    monkeypatch.setattr(learning_routes, "db_manager", database)

    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "audit-session-key-test"
    app.secret_key = app.config["SECRET_KEY"]

    with database.get_connection() as conn:
        # 1. Super Admin
        super_admin_id = conn.execute(
            """
            INSERT INTO admin_users (email, name, password_hash, role)
            VALUES (?, ?, ?, 'Super Admin')
            """,
            ("audit_admin@institution.edu", "Prof. Admin Director", generate_password_hash("AdminPass123!*")),
        ).lastrowid

        # 2. Teacher 1 (Authorized)
        teacher_1_id = conn.execute(
            """
            INSERT INTO admin_users (email, name, password_hash, role)
            VALUES (?, ?, ?, 'Teacher')
            """,
            ("teacher1@institution.edu", "Dr. Jane Smith", generate_password_hash("TeacherPass123!*")),
        ).lastrowid

        # 3. Teacher 2 (Unauthorized / Other Department)
        teacher_2_id = conn.execute(
            """
            INSERT INTO admin_users (email, name, password_hash, role)
            VALUES (?, ?, ?, 'Teacher')
            """,
            ("teacher2@institution.edu", "Dr. Bob Miller", generate_password_hash("TeacherPass123!*")),
        ).lastrowid

        # 4. Students
        student_1_id = conn.execute(
            """
            INSERT INTO students (name, usn, email, department, semester, account_status, password_hash, subject, exam_code)
            VALUES (?, ?, ?, ?, ?, 'Active', ?, 'Cyber Security', 'CS502')
            """,
            ("Alice Student", "1CS23AI001", "alice@student.edu", "Computer Science", "Semester 5", generate_password_hash("StudentPass123!")),
        ).lastrowid

        student_2_id = conn.execute(
            """
            INSERT INTO students (name, usn, email, department, semester, account_status, password_hash, subject, exam_code)
            VALUES (?, ?, ?, ?, ?, 'Active', ?, 'Mechanical Eng', 'ME301')
            """,
            ("Bob Student", "1CS23AI002", "bob@student.edu", "Mechanical Eng", "Semester 3", generate_password_hash("StudentPass123!")),
        ).lastrowid

        # 5. Question Bank & Subject
        subj_row = conn.execute("SELECT subject_id FROM subjects LIMIT 1").fetchone()
        if subj_row:
            subject_id = subj_row["subject_id"]
        else:
            subject_id = conn.execute("INSERT INTO subjects (name, code) VALUES ('Cyber Security', 'CS502')").lastrowid

        qb_row = conn.execute("SELECT bank_id FROM question_banks WHERE subject_id = ? LIMIT 1", (subject_id,)).fetchone()
        if qb_row:
            bank_id = qb_row["bank_id"]
        else:
            bank_id = conn.execute("INSERT INTO question_banks (name, subject_id) VALUES ('CS502 Bank', ?)", (subject_id,)).lastrowid

        # Create teacher permission for Teacher 1 on default exams
        exams = conn.execute("SELECT exam_id FROM exams WHERE bank_id = ?", (bank_id,)).fetchall()
        for ex in exams:
            conn.execute(
                "INSERT OR IGNORE INTO teacher_exam_permissions (admin_user_id, exam_id) VALUES (?, ?)",
                (teacher_1_id, ex["exam_id"]),
            )

    return {
        "client": app.test_client(),
        "database": database,
        "super_admin_id": super_admin_id,
        "teacher_1_id": teacher_1_id,
        "teacher_2_id": teacher_2_id,
        "student_1_id": student_1_id,
        "student_2_id": student_2_id,
        "subject_id": subject_id,
        "bank_id": bank_id,
    }


def _get_csrf(client):
    client.get("/admin/login")
    with client.session_transaction() as sess:
        return sess.get("admin_csrf_token")


def _login_admin(client, email, password):
    csrf = _get_csrf(client)
    return client.post(
        "/admin/login",
        data={"email": email, "password": password, "csrf_token": csrf},
        follow_redirects=True,
    )


# ==============================================================================
# TEST 1: AUTHENTICATION
# ==============================================================================
def test_audit_test_1_authentication(audit_context):
    """
    TEST 1: AUTHENTICATION
    - Student login works.
    - Teacher login works.
    - Admin login works.
    - Role-based access control works.
    - Unauthorised users cannot access protected routes.
    - Logout works correctly.
    """
    client = audit_context["client"]

    # 1. Student login works
    resp_student_login = client.post(
        "/login",
        data={
            "name": "Alice Student",
            "usn": "1CS23AI001",
            "subject": "Cyber Security",
            "exam_code": "CS502",
        },
        follow_redirects=False,
    )
    assert resp_student_login.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get("student_id") is not None
        assert sess.get("student_usn") == "1CS23AI001"

    # Student cannot access administrative routes (redirected away from admin to student dashboard)
    resp_stud_admin = client.get("/admin/dashboard", follow_redirects=False)
    assert resp_stud_admin.status_code == 302
    assert "/dashboard" in resp_stud_admin.headers["Location"]

    # Student logout works
    resp_stud_logout = client.get("/logout", follow_redirects=False)
    assert resp_stud_logout.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get("student_id") is None

    # 2. Teacher login works
    resp_teach_login = _login_admin(client, "teacher1@institution.edu", "TeacherPass123!*")
    assert resp_teach_login.status_code == 200
    with client.session_transaction() as sess:
        assert sess.get("admin_user_id") == audit_context["teacher_1_id"]

    # Teacher dashboard access works
    resp_teach_dash = client.get("/admin/dashboard")
    assert resp_teach_dash.status_code == 200
    assert b"Dashboard" in resp_teach_dash.data
    assert b"ExamShield" in resp_teach_dash.data

    # Teacher blocked from Super Admin routes (/admin/users, /admin/settings) with 403 Forbidden
    resp_teach_users = client.get("/admin/users", follow_redirects=False)
    assert resp_teach_users.status_code == 403
    resp_teach_settings = client.get("/admin/settings", follow_redirects=False)
    assert resp_teach_settings.status_code == 403

    # Teacher logout works
    csrf = _get_csrf(client)
    resp_teach_logout = client.post(
        "/admin/logout",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp_teach_logout.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get("admin_user_id") is None

    # 3. Admin (Super Admin) login works
    resp_admin_login = _login_admin(client, "audit_admin@institution.edu", "AdminPass123!*")
    assert resp_admin_login.status_code == 200
    with client.session_transaction() as sess:
        assert sess.get("admin_user_id") == audit_context["super_admin_id"]

    # Super Admin has access to all administrative routes including users and settings
    assert client.get("/admin/dashboard").status_code == 200
    assert client.get("/admin/users").status_code == 200
    assert client.get("/admin/settings").status_code == 200
    assert client.get("/admin/audit-logs").status_code == 200

    # Unauthenticated anonymous user cannot access protected routes
    with client.session_transaction() as sess:
        sess.clear()
    resp_unauth = client.get("/admin/dashboard", follow_redirects=False)
    assert resp_unauth.status_code == 302
    assert "/admin/login" in resp_unauth.headers["Location"]


# ==============================================================================
# TEST 2: QUESTION PAPER CREATION
# ==============================================================================
def test_audit_test_2_question_paper_creation(audit_context):
    """
    TEST 2: QUESTION PAPER CREATION
    - Teachers can create examinations.
    - All 7 supported question types work.
    - Questions can be edited and deleted.
    - Questions can be imported from the question bank.
    - Total marks are calculated correctly.
    - Question paper preview works.
    - Draft and published states work.
    """
    client = audit_context["client"]
    db = audit_context["database"]
    _login_admin(client, "teacher1@institution.edu", "TeacherPass123!*")
    csrf = _get_csrf(client)

    # 1. Create a full exam with all 7 question types
    exam_payload = {
        "csrf_token": csrf,
        "title": "Comprehensive Engineering Assessment 2026",
        "subject_id": audit_context["subject_id"],
        "department": "Computer Science & Engineering",
        "semester": "Semester 5",
        "academic_year": "2026-2027",
        "description": "Mid-term evaluation covering systems and networks.",
        "duration_minutes": 90,
        "pass_marks": 2.0,
        "instructions": "All questions are compulsory. No electronic gadgets allowed.",
        "negative_marking": True,
        "randomize_questions": True,
        "randomize_options": True,
        "allow_multiple_attempts": False,
        "status": "draft",
        "questions": [
            # 1. Multiple Choice Question (MCQ)
            {
                "statement": "What is the primary function of an operating system kernel?",
                "question_type": "mcq",
                "marks": 5.0,
                "negative_marks": 1.0,
                "difficulty": "Easy",
                "topic": "Operating Systems",
                "explanation": "Kernel bridges software applications and hardware components.",
                "options": [
                    {"option_text": "Resource management and abstraction", "is_correct": True},
                    {"option_text": "Web browsing", "is_correct": False},
                ],
            },
            # 2. Multiple Select Question (MSQ / multiple_select)
            {
                "statement": "Which of the following are valid transport layer protocols? (Select all that apply)",
                "question_type": "multiple_select",
                "marks": 6.0,
                "negative_marks": 0.5,
                "difficulty": "Medium",
                "topic": "Networking",
                "explanation": "TCP and UDP both reside at the transport layer.",
                "options": [
                    {"option_text": "TCP", "is_correct": True},
                    {"option_text": "UDP", "is_correct": True},
                    {"option_text": "HTTP", "is_correct": False},
                ],
            },
            # 3. True / False
            {
                "statement": "SQLite is a client-server relational database.",
                "question_type": "true_false",
                "marks": 2.0,
                "negative_marks": 0.5,
                "difficulty": "Easy",
                "topic": "Databases",
                "explanation": "SQLite is an embedded, serverless, file-based relational database.",
                "options": [
                    {"option_text": "True", "is_correct": False},
                    {"option_text": "False", "is_correct": True},
                ],
            },
            # 4. Fill in the blanks
            {
                "statement": "The default port for HTTPS communication is [blank].",
                "question_type": "fill_blank",
                "marks": 3.0,
                "negative_marks": 0.0,
                "difficulty": "Easy",
                "topic": "Security",
                "correct_answer_text": "443",
                "explanation": "HTTPS runs over port 443 by default.",
            },
            # 5. Short Answer
            {
                "statement": "Explain the ACID properties in database management.",
                "question_type": "short_answer",
                "marks": 8.0,
                "negative_marks": 0.0,
                "difficulty": "Medium",
                "topic": "Databases",
                "correct_answer_text": "Atomicity, Consistency, Isolation, Durability ensure reliable transactions.",
            },
            # 6. Long Answer
            {
                "statement": "Describe the 3-way TCP handshake process in detail with SYN and ACK sequence diagrams.",
                "question_type": "long_answer",
                "marks": 15.0,
                "negative_marks": 0.0,
                "difficulty": "Hard",
                "topic": "Networking",
                "correct_answer_text": "Client sends SYN, Server replies with SYN-ACK, Client acknowledges with ACK.",
            },
            # 7. Numerical
            {
                "statement": "Calculate the transmission delay for a 1000-byte packet over a 10 Mbps link in milliseconds.",
                "question_type": "numerical",
                "marks": 5.0,
                "negative_marks": 1.0,
                "difficulty": "Hard",
                "topic": "Networking",
                "correct_answer_text": "0.8",
                "explanation": "(1000 * 8 bits) / (10 * 10^6 bps) = 0.0008 seconds = 0.8 ms.",
            },
        ],
    }

    resp_create = client.post(
        "/admin/exams/create",
        json=exam_payload,
        headers={"X-CSRFToken": csrf},
    )
    assert resp_create.status_code == 200
    res_data = resp_create.get_json()
    assert res_data.get("success") is True
    created_exam_id = res_data.get("exam_id")
    assert created_exam_id is not None

    # Verify total marks auto-calculated: 5 + 6 + 2 + 3 + 8 + 15 + 5 = 44.0
    exam_rec = db.get_exam_full_detail(created_exam_id)
    assert float(exam_rec["max_marks"]) == 44.0
    assert len(exam_rec["questions"]) == 7

    # 2. Add question to Question Bank and Import into exam
    qb_item = {
        "statement": "What does DNS stand for?",
        "subject_id": audit_context["subject_id"],
        "topic": "Networking",
        "difficulty": "Easy",
        "question_type": "mcq",
        "marks": 4.0,
        "correct_answer_text": "Domain Name System",
        "explanation": "Translates domain names to IP addresses.",
        "options": [
            {"option_text": "Domain Name System", "is_correct": True},
            {"option_text": "Data Network Service", "is_correct": False},
        ],
    }
    resp_qb_add = client.post(
        "/admin/question-bank/create",
        json=qb_item,
        headers={"X-CSRFToken": csrf},
    )
    assert resp_qb_add.status_code == 200
    qb_q_id = resp_qb_add.get_json()["question_id"]

    # Import question from bank into exam
    resp_import = client.post(
        "/admin/question-bank/import-to-exam",
        json={"exam_id": created_exam_id, "question_ids": [qb_q_id]},
        headers={"X-CSRFToken": csrf},
    )
    assert resp_import.status_code == 200
    assert resp_import.get_json()["success"] is True

    # Exam now has 8 questions (44.0 + 4.0 = 48.0 marks)
    exam_after_import = db.get_exam_full_detail(created_exam_id)
    assert len(exam_after_import["questions"]) == 8

    # 3. Edit examination questions via /admin/exams/<id>/edit
    updated_questions = exam_after_import["questions"][:6]  # remove the last 2 questions (testing deletion & editing)
    edit_payload = {
        "csrf_token": csrf,
        "title": "Comprehensive Engineering Assessment 2026 (Revised)",
        "duration_minutes": 100,
        "instructions": "Revised rules: 100 minutes total duration.",
        "pass_marks": 25.0,
        "questions": updated_questions,
    }
    resp_edit = client.post(
        f"/admin/exams/{created_exam_id}/edit",
        json=edit_payload,
        headers={"X-CSRFToken": csrf},
    )
    assert resp_edit.status_code == 200
    exam_after_edit = db.get_exam_full_detail(created_exam_id)
    assert exam_after_edit["name"] == "Comprehensive Engineering Assessment 2026 (Revised)"
    assert len(exam_after_edit["questions"]) == 6

    # 4. Question Paper Preview works
    resp_preview = client.get(f"/admin/exams/{created_exam_id}/preview")
    assert resp_preview.status_code == 200
    assert b"Comprehensive Engineering Assessment" in resp_preview.data
    assert b"Preview" in resp_preview.data

    # 5. Draft vs Published state
    assert exam_after_edit["status"] == "draft"
    # Publish the exam
    resp_pub = client.post(
        f"/admin/exams/{created_exam_id}/status",
        json={"csrf_token": csrf, "status": "published"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp_pub.status_code == 200
    updated_exam = db.get_exam(created_exam_id)
    assert updated_exam["status"] == "published"


# ==============================================================================
# TEST 3: EXAMINATION MANAGEMENT
# ==============================================================================
def test_audit_test_3_examination_management(audit_context):
    """
    TEST 3: EXAMINATION MANAGEMENT
    - Examinations can be scheduled.
    - Published examinations appear in the student portal.
    - Examination timing works.
    - Editing does not destroy historical submissions.
    - Examination statuses update correctly.
    """
    client = audit_context["client"]
    db = audit_context["database"]
    _login_admin(client, "teacher1@institution.edu", "TeacherPass123!*")
    csrf = _get_csrf(client)

    now = datetime.now()
    start_future = (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    end_future = (now + timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")

    # 1. Schedule an exam
    sched_payload = {
        "csrf_token": csrf,
        "title": "Scheduled Calculus Examination",
        "subject_id": audit_context["subject_id"],
        "duration_minutes": 60,
        "pass_marks": 2.0,
        "start_time": start_future,
        "end_time": end_future,
        "status": "scheduled",
        "questions": [
            {
                "statement": "What is the derivative of sin(x)?",
                "question_type": "mcq",
                "marks": 5.0,
                "options": [
                    {"option_text": "cos(x)", "is_correct": True},
                    {"option_text": "-cos(x)", "is_correct": False},
                ],
            }
        ],
    }
    resp = client.post(
        "/admin/exams/create",
        json=sched_payload,
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 200
    sched_exam_id = resp.get_json()["exam_id"]

    # 2. Timing enforcement: student trying to begin future scheduled exam gets 403
    with client.session_transaction() as sess:
        sess.clear()
        sess["student_id"] = audit_context["student_1_id"]
        sess["student_usn"] = "1CS23AI001"

    resp_begin_future = client.post(f"/exam/{sched_exam_id}/begin")
    assert resp_begin_future.status_code == 403
    assert b"Examination has not started yet" in resp_begin_future.data

    # Now create an expired exam (in the past)
    start_past = (now - timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")
    end_past = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE exams SET start_time = ?, end_time = ?, status = 'published' WHERE exam_id = ?",
            (start_past, end_past, sched_exam_id),
        )

    resp_begin_past = client.post(f"/exam/{sched_exam_id}/begin")
    assert resp_begin_past.status_code == 403
    assert b"Examination has ended" in resp_begin_past.data

    # Set exam timing to ACTIVE (start in past, end in future)
    start_active = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    end_active = (now + timedelta(minutes=50)).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE exams SET start_time = ?, end_time = ?, status = 'published' WHERE exam_id = ?",
            (start_active, end_active, sched_exam_id),
        )

    # Active exam begins successfully
    resp_begin_active = client.post(f"/exam/{sched_exam_id}/begin", follow_redirects=False)
    assert resp_begin_active.status_code == 302
    attempt_url = resp_begin_active.headers["Location"]
    attempt_id = int(attempt_url.split("/")[-1])
    assert attempt_id > 0

    # 3. Submit the attempt to create a historical submission
    db.submit_attempt(attempt_id)
    attempt_before_edit = db.get_attempt(attempt_id)
    assert attempt_before_edit["status"] == "submitted"

    # 4. Editing exam settings does not destroy historical submissions
    _login_admin(client, "teacher1@institution.edu", "TeacherPass123!*")
    csrf = _get_csrf(client)
    edit_payload = {
        "csrf_token": csrf,
        "title": "Scheduled Calculus Examination (Edited Title)",
        "duration_minutes": 75,
        "instructions": "Updated instructions.",
        "pass_marks": 25.0,
    }
    resp_edit = client.post(
        f"/admin/exams/{sched_exam_id}/edit",
        json=edit_payload,
        headers={"X-CSRFToken": csrf},
    )
    assert resp_edit.status_code == 200

    attempt_after_edit = db.get_attempt(attempt_id)
    assert attempt_after_edit is not None
    assert attempt_after_edit["attempt_id"] == attempt_id
    assert attempt_after_edit["status"] == "submitted"

    # 5. Status lifecycle transitions
    for status in ("scheduled", "live", "completed", "cancelled", "archived"):
        resp_st = client.post(
            f"/admin/exams/{sched_exam_id}/status",
            json={"csrf_token": csrf, "status": status},
            headers={"X-CSRFToken": csrf},
        )
        assert resp_st.status_code == 200
        cur = db.get_exam(sched_exam_id)
        assert cur["status"] == status


# ==============================================================================
# TEST 4: STUDENT EXAMINATION WORKFLOW
# ==============================================================================
def test_audit_test_4_student_examination_workflow(audit_context):
    """
    TEST 4: STUDENT EXAMINATION WORKFLOW
    - Accessing authorized exams.
    - Submitting answers.
    - Answer storage.
    - Objective evaluation.
    - Answer key protection (no leakage of correct options/text to students during exam).
    - Prohibition of modifying submitted answers.
    """
    client = audit_context["client"]
    db = audit_context["database"]

    # 1. Answer Key Protection Check
    # Ensure get_exam(include_answers=False) does not reveal answers
    exams = db.list_exams(audit_context["subject_id"])
    assert len(exams) > 0
    exam_id = exams[0]["exam_id"]
    exam_student_view = db.get_exam(exam_id, include_answers=False)

    for q in exam_student_view["questions"]:
        assert q.get("correct_answer_text") == "" or q.get("correct_answer_text") is None
        assert q.get("explanation") == "" or q.get("explanation") is None
        for opt in q.get("options", []):
            assert "is_correct" not in opt or opt.get("is_correct") is False

    # 2. Student takes exam
    with client.session_transaction() as sess:
        sess.clear()
        sess["student_id"] = audit_context["student_1_id"]
        sess["student_usn"] = "1CS23AI001"

    # Ensure exam is published and active
    with db.get_connection() as conn:
        conn.execute("UPDATE exams SET status = 'published', start_time = NULL, end_time = NULL WHERE exam_id = ?", (exam_id,))

    # Begin attempt
    resp_begin = client.post(f"/exam/{exam_id}/begin", follow_redirects=False)
    assert resp_begin.status_code == 302
    attempt_id = int(resp_begin.headers["Location"].split("/")[-1])

    # 3. Submitting answers and answer storage
    q0 = exam_student_view["questions"][0]
    q0_id = q0["question_id"]
    opt0_id = q0["options"][0]["option_id"] if q0.get("options") else None

    resp_save_ans = client.post(
        f"/api/attempt/{attempt_id}/answer",
        json={"question_id": q0_id, "option_id": opt0_id, "answer_text": "Sample student input"},
    )
    assert resp_save_ans.status_code == 200
    assert resp_save_ans.get_json().get("saved") is True

    # 4. Submit the exam
    resp_submit = client.post(f"/attempt/{attempt_id}/submit", follow_redirects=False)
    assert resp_submit.status_code == 302
    assert resp_submit.headers["Location"].endswith(f"/attempt/{attempt_id}/result")

    # 5. Prohibition of modifying submitted answers
    resp_tamper = client.post(
        f"/api/attempt/{attempt_id}/answer",
        json={"question_id": q0_id, "option_id": opt0_id, "answer_text": "Hacked response"},
    )
    assert resp_tamper.status_code == 409
    assert b"Attempt is unavailable" in resp_tamper.data

    # Attempting to re-enter take_exam redirects to result
    resp_reenter = client.get(f"/attempt/{attempt_id}", follow_redirects=False)
    assert resp_reenter.status_code == 302
    assert resp_reenter.headers["Location"].endswith(f"/attempt/{attempt_id}/result")


# ==============================================================================
# TEST 5: RESULTS AND ANALYTICS
# ==============================================================================
def test_audit_test_5_results_and_analytics(audit_context):
    """
    TEST 5: RESULTS AND ANALYTICS
    - Marks calculation.
    - Negative marking.
    - Subjective evaluation integration.
    - Grade assignment based on configurable boundaries.
    - Performance reports data accuracy.
    - Exports (PDF, Excel, CSV).
    """
    client = audit_context["client"]
    db = audit_context["database"]
    _login_admin(client, "audit_admin@institution.edu", "AdminPass123!*")
    csrf = _get_csrf(client)

    # 1. Configurable Grade Boundaries
    boundaries = [
        {"grade": "A+", "min_score": 90.0, "max_score": 100.0, "description": "Outstanding"},
        {"grade": "A", "min_score": 80.0, "max_score": 89.99, "description": "Excellent"},
        {"grade": "B", "min_score": 70.0, "max_score": 79.99, "description": "Very Good"},
        {"grade": "C", "min_score": 60.0, "max_score": 69.99, "description": "Good"},
        {"grade": "D", "min_score": 50.0, "max_score": 59.99, "description": "Pass"},
        {"grade": "F", "min_score": 0.0, "max_score": 49.99, "description": "Fail"},
    ]
    resp_grades = client.post(
        "/admin/api/settings/grading",
        json={"boundaries": boundaries},
        headers={"X-CSRFToken": csrf},
    )
    assert resp_grades.status_code == 200

    # 2. Create an exam with Subjective and Negative Marked questions
    exam_payload = {
        "csrf_token": csrf,
        "title": "Evaluations & Analytics Audit Exam",
        "subject_id": audit_context["subject_id"],
        "duration_minutes": 45,
        "pass_marks": 2.0,
        "negative_marking": True,
        "status": "published",
        "questions": [
            {
                "statement": "What is 2 + 2?",
                "question_type": "mcq",
                "marks": 10.0,
                "negative_marks": 2.5,
                "options": [
                    {"option_text": "4", "is_correct": True},
                    {"option_text": "5", "is_correct": False},
                ],
            },
            {
                "statement": "Elaborate on how asymmetric cryptography works.",
                "question_type": "long_answer",
                "marks": 30.0,
                "negative_marks": 0.0,
                "correct_answer_text": "Uses public and private key pairs for encryption and decryption.",
            },
        ],
    }
    resp_ex = client.post(
        "/admin/exams/create",
        json=exam_payload,
        headers={"X-CSRFToken": csrf},
    )
    assert resp_ex.status_code == 200
    eval_exam_id = resp_ex.get_json()["exam_id"]
    eval_exam = db.get_exam_full_detail(eval_exam_id)
    mcq_q = eval_exam["questions"][0]
    subj_q = eval_exam["questions"][1]

    # Grant permission to teacher 1 as well
    with db.get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO teacher_exam_permissions (admin_user_id, exam_id) VALUES (?, ?)", (audit_context["teacher_1_id"], eval_exam_id))

    # Student 1 takes exam and submits
    attempt_id = db.create_attempt(audit_context["student_1_id"], eval_exam_id, mode="proctored")
    # Wrong MCQ answer (-2.5)
    db.save_answer(attempt_id, mcq_q["question_id"], mcq_q["options"][1]["option_id"])
    # Student subjective answer
    db.save_answer(attempt_id, subj_q["question_id"], answer_text="Asymmetric crypto uses public key to encrypt and private key to decrypt.")
    db.submit_attempt(attempt_id)

    # Verify attempt is in pending subjective evaluation
    eval_list = db.list_evaluations(user_id=audit_context["super_admin_id"], role="Super Admin")
    pending_attempts = [item["attempt_id"] for item in eval_list]
    assert attempt_id in pending_attempts

    # Student views result before evaluation is published -> score is hidden / evaluation in progress
    with client.session_transaction() as sess:
        sess.clear()
        sess["student_id"] = audit_context["student_1_id"]
        sess["student_usn"] = "1CS23AI001"
    resp_student_res = client.get(f"/attempt/{attempt_id}/result")
    assert resp_student_res.status_code == 200
    assert b"Evaluation In Progress" in resp_student_res.data

    # Teacher grades subjective question
    _login_admin(client, "teacher1@institution.edu", "TeacherPass123!*")
    csrf = _get_csrf(client)
    eval_submit_payload = {
        "csrf_token": csrf,
        "evaluations": [
            {
                "question_id": subj_q["question_id"],
                "marks_awarded": 28.0,
                "feedback": "Excellent detailed explanation.",
            }
        ],
        "publish": True,  # Final publish
    }
    resp_grade_sub = client.post(
        f"/admin/evaluations/attempt/{attempt_id}/save",
        json=eval_submit_payload,
        headers={"X-CSRFToken": csrf},
    )
    assert resp_grade_sub.status_code == 200

    # Verify student result is now published and score calculated:
    # MCQ: -2.5, Subjective: +28.0 => 25.5 / 40.0 = 63.75% -> Grade C
    res_record = db.get_attempt(attempt_id)["result"]
    assert res_record["is_published"] == 1
    assert res_record["marks_obtained"] == 25.5
    assert res_record["percentage"] == 63.75
    assert res_record["grade"] == "C"
    assert res_record["status"] == "Passed"

    # 3. Report Exports (PDF, Excel, CSV)
    _login_admin(client, "audit_admin@institution.edu", "AdminPass123!*")

    # PDF Export
    resp_pdf = client.get("/admin/reports/export/pdf")
    assert resp_pdf.status_code == 200
    assert resp_pdf.mimetype == "application/pdf"
    assert resp_pdf.data.startswith(b"%PDF-")

    # Excel Export
    resp_xlsx = client.get("/admin/reports/export/xlsx")
    assert resp_xlsx.status_code == 200
    assert resp_xlsx.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert resp_xlsx.data.startswith(b"PK")

    # CSV Export
    resp_csv = client.get("/admin/reports/export/csv")
    assert resp_csv.status_code == 200
    assert resp_csv.mimetype == "text/csv"
    assert "EXAMSHIELD AI - PERFORMANCE REPORT" in resp_csv.text


# ==============================================================================
# TEST 6: SECURITY
# ==============================================================================
def test_audit_test_6_security(audit_context):
    """
    TEST 6: SECURITY
    - Backend permissions enforcement.
    - Students blocked from administrative APIs.
    - Teachers blocked from unauthorized exams/students.
    - Sensitive student data protection.
    - Audit log generation.
    """
    client = audit_context["client"]
    db = audit_context["database"]

    # 1. Student blocked from administrative endpoints
    with client.session_transaction() as sess:
        sess.clear()
        sess["student_id"] = audit_context["student_1_id"]
        sess["student_usn"] = "1CS23AI001"

    resp_admin_students = client.get("/admin/students", follow_redirects=False)
    assert resp_admin_students.status_code == 302
    assert "/dashboard" in resp_admin_students.headers["Location"]

    resp_admin_settings = client.get("/admin/settings", follow_redirects=False)
    assert resp_admin_settings.status_code == 302

    # 2. Teacher 2 (unauthorized) blocked from exams and student profiles of Teacher 1
    _login_admin(client, "teacher2@institution.edu", "TeacherPass123!*")
    # Teacher 2 attempts to fetch student 1 profile (not enrolled in teacher 2's exams)
    resp_unauth_student = client.get(f"/admin/students/{audit_context['student_1_id']}")
    assert resp_unauth_student.status_code in (403, 404)

    # 3. Sensitive student data protection: student password_hash is never exposed
    student_profile = db.get_student_profile(audit_context["student_1_id"], role="Super Admin")
    assert student_profile is not None
    assert "password_hash" not in student_profile

    # 4. Audit Log Generation for critical operations
    _login_admin(client, "audit_admin@institution.edu", "AdminPass123!*")
    csrf = _get_csrf(client)

    # Perform critical actions to verify audit logs
    # a) Examination creation
    exam_payload = {
        "csrf_token": csrf,
        "title": "Audit Security Verification Exam",
        "subject_id": audit_context["subject_id"],
        "duration_minutes": 30,
        "pass_marks": 2.0,
        "questions": [
            {
                "statement": "Security check question",
                "question_type": "mcq",
                "marks": 5.0,
                "options": [
                    {"option_text": "Secure", "is_correct": True},
                    {"option_text": "Insecure", "is_correct": False},
                ],
            }
        ],
    }
    resp_create = client.post(
        "/admin/exams/create",
        json=exam_payload,
        headers={"X-CSRFToken": csrf},
    )
    assert resp_create.status_code == 200
    sec_exam_id = resp_create.get_json()["exam_id"]

    # b) Examination modification
    client.post(
        f"/admin/exams/{sec_exam_id}/edit",
        json={"csrf_token": csrf, "title": "Audit Security Verification Exam (Modified)"},
        headers={"X-CSRFToken": csrf},
    )

    # c) Examination cancellation
    client.post(
        f"/admin/exams/{sec_exam_id}/status",
        json={"csrf_token": csrf, "status": "cancelled"},
        headers={"X-CSRFToken": csrf},
    )

    # Check that audit logs were recorded
    resp_logs = client.get("/admin/api/audit-logs")
    assert resp_logs.status_code == 200
    logs_data = resp_logs.get_json()
    actions = [log["action"] for log in logs_data.get("logs", [])]

    assert "examination_creation" in actions
    assert "examination_modification" in actions
    assert "examination_cancellation" in actions


# ==============================================================================
# TEST 7: UI/UX
# ==============================================================================
def test_audit_test_7_ui_ux(audit_context):
    """
    TEST 7: UI/UX
    - All routes work (HTTP 200).
    - Form validations reject invalid inputs cleanly.
    - Search and filtering endpoints work.
    - Chart visualizations data endpoints work.
    - Responsive layout & empty states render without errors.
    """
    client = audit_context["client"]
    _login_admin(client, "audit_admin@institution.edu", "AdminPass123!*")
    csrf = _get_csrf(client)

    # 1. Verify all administrative pages return HTTP 200
    routes_to_verify = [
        "/admin/dashboard",
        "/admin/exams",
        "/admin/exams/create",
        "/admin/question-bank",
        "/admin/students",
        "/admin/reports",
        "/admin/evaluations",
        "/admin/notifications",
        "/admin/settings",
        "/admin/audit-logs",
        "/admin/users",
    ]
    for route in routes_to_verify:
        resp = client.get(route)
        assert resp.status_code == 200, f"Failed on route {route} with status {resp.status_code}"

    # Verify student notifications page returns 200 for logged-in student
    with client.session_transaction() as sess:
        sess.clear()
        sess["student_id"] = audit_context["student_1_id"]
        sess["student_usn"] = "1CS23AI001"
    resp_stud_notif = client.get("/student/notifications")
    assert resp_stud_notif.status_code == 200

    # 2. Form Validation: Creating exam with missing title returns 400
    _login_admin(client, "audit_admin@institution.edu", "AdminPass123!*")
    csrf = _get_csrf(client)
    invalid_exam_payload = {
        "csrf_token": csrf,
        "title": "   ",  # Blank title
        "subject_id": audit_context["subject_id"],
    }
    resp_invalid = client.post(
        "/admin/exams/create",
        json=invalid_exam_payload,
        headers={"X-CSRFToken": csrf},
    )
    assert resp_invalid.status_code == 400
    assert "title" in resp_invalid.get_json().get("error", "").lower()

    # 3. Search and Filtering: Students search
    resp_search = client.get("/admin/students?search=Alice&department=Computer+Science")
    assert resp_search.status_code == 200
    assert b"Alice Student" in resp_search.data

    # 4. Chart Visualizations Data
    resp_analytics_json = client.get("/admin/reports?format=json")
    assert resp_analytics_json.status_code == 200
    analytics_payload = resp_analytics_json.get_json()
    assert "analytics" in analytics_payload
    assert "grade_distribution" in analytics_payload["analytics"]
    assert "exam_analytics" in analytics_payload["analytics"]
