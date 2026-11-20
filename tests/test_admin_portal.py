import pytest
from werkzeug.security import check_password_hash, generate_password_hash

import app as app_module
from app import app
from backend.database.db_manager import DatabaseManager
from backend.routes import admin_routes
from backend.routes import auth_routes
from backend.routes import learning_routes


@pytest.fixture
def admin_context(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / "admin-test.db")
    database.init_db()
    monkeypatch.setattr(admin_routes, "db_manager", database)
    monkeypatch.setattr(auth_routes, "db_manager", database)
    monkeypatch.setattr(learning_routes, "db_manager", database)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-session-secret"
    app.secret_key = app.config["SECRET_KEY"]

    with database.get_connection() as connection:
        exams = connection.execute(
            "SELECT exam_id FROM exams ORDER BY exam_id LIMIT 2"
        ).fetchall()
        exam_ids = [row["exam_id"] for row in exams]
        restricted_question = "UNASSIGNED QUESTION: confidential teacher-only review"
        super_admin = connection.execute(
            """
            INSERT INTO admin_users (email, name, password_hash, role)
            VALUES (?, ?, ?, 'Super Admin')
            """,
            ("superadmin@example.test", "Test Super Admin", generate_password_hash("secure-admin-password")),
        ).lastrowid
        teacher = connection.execute(
            """
            INSERT INTO admin_users (email, name, password_hash, role)
            VALUES (?, ?, ?, 'Teacher')
            """,
            ("teacher@example.test", "Test Teacher", generate_password_hash("secure-teacher-password")),
        ).lastrowid
        connection.execute(
            "INSERT INTO teacher_exam_permissions (admin_user_id, exam_id) VALUES (?, ?)",
            (teacher, exam_ids[0]),
        )
        assigned_student = connection.execute(
            """
            INSERT INTO students (name, usn, subject, exam_code)
            VALUES ('Assigned Student', 'ASSIGNED01', 'Mathematics', 'ALG')
            """
        ).lastrowid
        other_student = connection.execute(
            """
            INSERT INTO students (name, usn, subject, exam_code)
            VALUES ('Other Student', 'OTHER001', 'Physics', 'PHY')
            """
        ).lastrowid
        connection.execute(
            "INSERT INTO exam_attempts (student_id, exam_id) VALUES (?, ?)",
            (assigned_student, exam_ids[0]),
        )
        connection.execute(
            "INSERT INTO exam_attempts (student_id, exam_id) VALUES (?, ?)",
            (other_student, exam_ids[1]),
        )
        connection.execute(
            "INSERT INTO questions (exam_id, statement, marks) VALUES (?, ?, 2)",
            (exam_ids[1], restricted_question),
        )

    return {
        "client": app.test_client(),
        "database": database,
        "exam_ids": exam_ids,
        "restricted_question": restricted_question,
        "super_admin_id": super_admin,
        "teacher_id": teacher,
    }


def _csrf(client):
    with client.session_transaction() as session:
        return session["admin_csrf_token"]


def _login(client, email, password):
    client.get("/admin/login")
    return client.post(
        "/admin/login",
        data={"email": email, "password": password, "csrf_token": _csrf(client)},
        follow_redirects=True,
    )


def test_admin_login_page_has_form_token_and_password_field(admin_context):
    response = admin_context["client"].get("/admin/login")
    assert response.status_code == 200
    assert b"Teacher &amp; Admin Portal" in response.data
    assert b'name="csrf_token"' in response.data
    assert b'type="password"' in response.data


def test_admin_routes_redirect_unauthenticated_users(admin_context):
    response = admin_context["client"].get("/admin/dashboard")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")


def test_admin_login_uses_database_password_hash_and_generic_failure(admin_context):
    context = admin_context
    database = context["database"]
    with database.get_connection() as connection:
        stored_hash = connection.execute(
            "SELECT password_hash FROM admin_users WHERE email = ?",
            ("superadmin@example.test",),
        ).fetchone()["password_hash"]
    assert stored_hash != "secure-admin-password"
    assert check_password_hash(stored_hash, "secure-admin-password")

    client = context["client"]
    client.get("/admin/login")
    response = client.post(
        "/admin/login",
        data={"email": "superadmin@example.test", "password": "wrong", "csrf_token": _csrf(client)},
    )
    assert response.status_code == 200
    assert b"Invalid email or password." in response.data
    assert b"wrong" not in response.data


def test_admin_login_rejects_missing_or_invalid_csrf_token(admin_context):
    client = admin_context["client"]
    response = client.post(
        "/admin/login",
        data={"email": "superadmin@example.test", "password": "secure-admin-password"},
    )
    assert response.status_code == 400
    assert b"Your login form expired." in response.data


def test_super_admin_has_full_access_and_can_create_teacher_accounts(admin_context):
    context = admin_context
    client = context["client"]
    response = _login(client, "superadmin@example.test", "secure-admin-password")
    assert response.status_code == 200
    assert b"Admin dashboard" in response.data
    assert client.get("/admin/settings").status_code == 200
    assert client.get("/admin/users").status_code == 200
    assert client.get("/admin/exams").status_code == 200

    response = client.post(
        "/admin/users",
        data={
            "csrf_token": _csrf(client),
            "name": "New Teacher",
            "email": "new.teacher@example.test",
            "role": "Teacher",
            "password": "long-teacher-password",
            "exam_ids": [str(context["exam_ids"][1])],
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"The account was created successfully." in response.data
    with context["database"].get_connection() as connection:
        account = connection.execute(
            "SELECT password_hash, role FROM admin_users WHERE email = ?",
            ("new.teacher@example.test",),
        ).fetchone()
        grant_count = connection.execute(
            """
            SELECT COUNT(*) AS count FROM teacher_exam_permissions p
            JOIN admin_users u ON u.admin_user_id = p.admin_user_id
            WHERE u.email = ?
            """,
            ("new.teacher@example.test",),
        ).fetchone()["count"]
    assert account["role"] == "Teacher"
    assert check_password_hash(account["password_hash"], "long-teacher-password")
    assert grant_count == 1


def test_teacher_is_scoped_to_authorised_exams_students_and_reports(admin_context):
    context = admin_context
    client = context["client"]
    _login(client, "teacher@example.test", "secure-teacher-password")

    exams = client.get("/admin/exams")
    assert exams.status_code == 200
    with context["database"].get_connection() as connection:
        exam_names = connection.execute(
            "SELECT exam_id, name FROM exams WHERE exam_id IN (?, ?)",
            context["exam_ids"],
        ).fetchall()
    names_by_id = {row["exam_id"]: row["name"] for row in exam_names}
    assert names_by_id[context["exam_ids"][0]].encode() in exams.data
    assert names_by_id[context["exam_ids"][1]].encode() not in exams.data

    students = client.get("/admin/students")
    assert b"Assigned Student" in students.data
    assert b"Other Student" not in students.data
    questions = client.get("/admin/question-bank")
    with context["database"].get_connection() as connection:
        exam_questions = {
            row["exam_id"]: row["statement"]
            for row in connection.execute(
                """
                SELECT q.exam_id, q.statement FROM questions q
                WHERE q.exam_id IN (?, ?)
                """,
                context["exam_ids"],
            ).fetchall()
        }
    assert questions.status_code == 200
    assert exam_questions[context["exam_ids"][0]].encode() in questions.data
    assert context["restricted_question"].encode() not in questions.data

    dashboard = client.get("/admin/dashboard")
    assert dashboard.status_code == 200
    assert b"Teachers" not in dashboard.data
    assert client.get("/admin/reports").status_code == 200
    assert client.get("/admin/exams/create").status_code == 200


def test_teacher_cannot_access_super_admin_functions(admin_context):
    client = admin_context["client"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    assert client.get("/admin/settings").status_code == 403
    assert client.get("/admin/users").status_code == 403


def test_student_is_redirected_to_student_portal_without_losing_session(admin_context):
    client = admin_context["client"]
    response = client.post(
        "/register",
        data={
            "name": "Student User",
            "usn": "STUDENT42",
            "email": "student42@example.test",
            "password": "student-password",
            "confirm_password": "student-password",
        },
    )
    assert response.status_code == 302
    with client.session_transaction() as session:
        session.clear()

    response = client.post(
        "/account-login",
        data={"email": "student42@example.test", "password": "student-password"},
    )
    assert response.status_code == 302

    response = client.get("/admin/dashboard")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    with client.session_transaction() as session:
        assert session["student_id"]
        assert session["student_name"] == "Student User"


def test_legacy_student_login_remains_available(admin_context):
    client = admin_context["client"]
    response = client.post(
        "/login",
        data={
            "name": "Legacy Student",
            "usn": "LEGACY01",
            "subject": "Mathematics",
            "exam_code": "ALG101",
        },
    )
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["student_name"] == "Legacy Student"


def test_admin_logout_requires_post_and_clears_session(admin_context):
    client = admin_context["client"]
    _login(client, "superadmin@example.test", "secure-admin-password")
    response = client.post("/admin/logout", data={"csrf_token": _csrf(client)})
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert "admin_user_id" not in session


def test_admin_logout_does_not_clear_a_student_session(admin_context):
    client = admin_context["client"]
    client.post(
        "/register",
        data={
            "name": "Student User",
            "usn": "STUDENT44",
            "email": "student44@example.test",
            "password": "student-password",
            "confirm_password": "student-password",
        },
    )
    client.get("/admin/login")
    response = client.post("/admin/logout", data={"csrf_token": _csrf(client)})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    with client.session_transaction() as session:
        assert session["student_name"] == "Student User"


def test_super_admin_bootstrap_cli_creates_hashed_account(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / "bootstrap-test.db")
    monkeypatch.setattr(app_module, "DatabaseManager", lambda: database)

    result = app.test_cli_runner().invoke(
        args=["create-super-admin"],
        input="bootstrap@example.test\nBootstrap Admin\nbootstrap-password\nbootstrap-password\n",
    )
    assert result.exit_code == 0, result.output
    with database.get_connection() as connection:
        account = connection.execute(
            "SELECT email, role, password_hash FROM admin_users"
        ).fetchone()
    assert account["email"] == "bootstrap@example.test"
    assert account["role"] == "Super Admin"
    assert check_password_hash(account["password_hash"], "bootstrap-password")


def test_forged_role_in_session_does_not_grant_super_admin_access(admin_context):
    client = admin_context["client"]
    with client.session_transaction() as session:
        session["admin_user_id"] = admin_context["teacher_id"]
        session["admin_role"] = "Super Admin"
    assert client.get("/admin/settings").status_code == 403


def test_inactive_admin_user_cannot_log_in(admin_context):
    context = admin_context
    client = context["client"]
    database = context["database"]
    with database.get_connection() as connection:
        connection.execute(
            """
            INSERT INTO admin_users (email, name, password_hash, role, active)
            VALUES (?, ?, ?, 'Teacher', 0)
            """,
            ("disabled@example.test", "Disabled Teacher", generate_password_hash("secure-teacher-password")),
        )
    response = _login(client, "disabled@example.test", "secure-teacher-password")
    assert response.status_code == 200
    assert b"Invalid email or password." in response.data


def test_admin_root_redirects_unauthenticated_and_students(admin_context):
    client = admin_context["client"]
    response = client.get("/admin")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")

    client.post(
        "/register",
        data={
            "name": "Student A",
            "usn": "STUDENT_A",
            "email": "student_a@example.test",
            "password": "student-password",
            "confirm_password": "student-password",
        },
    )
    response = client.get("/admin")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_already_logged_in_admin_visiting_login_redirects_to_dashboard(admin_context):
    client = admin_context["client"]
    _login(client, "superadmin@example.test", "secure-admin-password")
    response = client.get("/admin/login")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/dashboard")


def test_teacher_cannot_create_users_even_with_direct_post(admin_context):
    client = admin_context["client"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    response = client.post(
        "/admin/users",
        data={
            "csrf_token": _csrf(client),
            "name": "Illegal User",
            "email": "illegal@example.test",
            "role": "Super Admin",
            "password": "long-illegal-password",
        },
    )
    assert response.status_code == 403


def test_student_login_pages_contain_admin_portal_links(admin_context):
    client = admin_context["client"]
    resp1 = client.get("/login")
    assert resp1.status_code == 200
    assert b"/admin/login" in resp1.data

    resp2 = client.get("/account-login")
    assert resp2.status_code == 200
    assert b"/admin/login" in resp2.data


def test_dashboard_renders_all_six_sections_and_metrics(admin_context):
    client = admin_context["client"]
    _login(client, "superadmin@example.test", "secure-admin-password")
    response = client.get("/admin/dashboard")
    assert response.status_code == 200
    # Statistics cards
    assert b"Total Students" in response.data
    assert b"Teachers" in response.data
    assert b"Total Examinations" in response.data
    assert b"Upcoming" in response.data
    assert b"Active Examinations" in response.data
    assert b"Completed" in response.data
    assert b"Average Performance" in response.data

    # Section 1: Examination Activity Graph
    assert b"Examination Activity" in response.data
    # Section 2: Student Performance Trend Graph
    assert b"Student Performance Trend" in response.data
    assert b"Distinction" in response.data
    # Section 3: Upcoming Examinations List
    assert b"Upcoming Examinations" in response.data
    # Section 4: Recent Examination Activity
    assert b"Recent Examination Activity" in response.data
    # Section 5: Recently Submitted Examinations
    assert b"Recently Submitted Examinations" in response.data
    # Section 6: Performance Highlights
    assert b"Performance Highlights" in response.data

    # Navigation & Topbar elements
    assert b"adminSearchInput" in response.data
    assert b"notificationMenuBtn" in response.data
    assert b"profileMenuBtn" in response.data


def test_admin_notifications_route(admin_context):
    client = admin_context["client"]
    _login(client, "superadmin@example.test", "secure-admin-password")
    response = client.get("/admin/notifications")
    assert response.status_code == 200
    assert b"Notifications Center" in response.data


def test_exam_create_page_renders_builder_components(admin_context):
    client = admin_context["client"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    response = client.get("/admin/exams/create")
    assert response.status_code == 200
    assert b"Create Question Paper" in response.data
    assert b"Examination Configuration &amp; Metadata" in response.data
    assert b"exam-title" in response.data
    assert b"exam-duration" in response.data
    assert b"paper-summary-bar" in response.data
    assert b"stat-total-marks" in response.data
    assert b"+ MCQ" in response.data
    assert b"+ Multi-Select" in response.data
    assert b"+ True/False" in response.data
    assert b"+ Blanks" in response.data
    assert b"+ Short Answer" in response.data
    assert b"+ Long Answer" in response.data
    assert b"+ Numerical" in response.data
    assert b"Import from Faculty Question Bank" in response.data
    assert b"Institutional Question Paper Preview" in response.data


def test_exam_create_valid_paper_creates_in_database_and_assigns_teacher(admin_context):
    client = admin_context["client"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    csrf = _csrf(client)
    payload = {
        "csrf_token": csrf,
        "title": "Data Structures Mid-Term Assessment",
        "subject_name": "Computer Science",
        "department": "CSE",
        "semester": "Semester 3",
        "academic_year": "2025-2026",
        "description": "Mid-term exam covering arrays, linked lists and stacks.",
        "instructions": "All questions are compulsory. Calculator permitted.",
        "duration_minutes": 60,
        "pass_marks": 4,
        "negative_marking": 0.25,
        "randomize_questions": True,
        "randomize_options": True,
        "allow_multiple_attempts": False,
        "status": "published",
        "questions": [
            {
                "statement": "What is the worst-case time complexity of searching in a binary search tree?",
                "question_type": "mcq",
                "marks": 2,
                "difficulty": "Medium",
                "topic": "Trees",
                "explanation": "Skewed BST has O(n) height.",
                "options": [
                    {"option_text": "O(1)", "is_correct": False},
                    {"option_text": "O(log n)", "is_correct": False},
                    {"option_text": "O(n)", "is_correct": True},
                    {"option_text": "O(n log n)", "is_correct": False},
                ],
            },
            {
                "statement": "A stack operates on a First-In First-Out (FIFO) basis.",
                "question_type": "true_false",
                "marks": 1,
                "difficulty": "Easy",
                "topic": "Stacks",
                "explanation": "Stacks operate on LIFO principle.",
                "options": [
                    {"option_text": "True", "is_correct": False},
                    {"option_text": "False", "is_correct": True},
                ],
            },
            {
                "statement": "Define what constitutes an abstract data type.",
                "question_type": "short_answer",
                "marks": 3,
                "difficulty": "Medium",
                "topic": "ADT",
                "correct_answer_text": "A data type defined by its behavior from point of view of user.",
                "explanation": "Encapsulates values and valid operations.",
            },
            {
                "statement": "What is the maximum number of nodes in a binary tree of height 4?",
                "question_type": "numerical",
                "marks": 2,
                "difficulty": "Hard",
                "topic": "Trees",
                "correct_answer_text": "31",
                "explanation": "2^(h+1) - 1 = 2^5 - 1 = 31.",
            },
        ],
    }

    response = client.post(
        "/admin/exams/create",
        json=payload,
        headers={"X-CSRFToken": csrf},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    exam_id = data["exam_id"]
    assert exam_id > 0

    # Verify database persistence
    db = admin_context["database"]
    detail = db.get_exam_full_detail(exam_id)
    assert detail is not None
    assert detail["name"] == "Data Structures Mid-Term Assessment"
    assert detail["max_marks"] == 8.0  # 2 + 1 + 3 + 2 = 8 marks auto-calculated
    assert detail["duration_minutes"] == 60
    assert detail["department"] == "CSE"
    assert len(detail["questions"]) == 4

    # Verify teacher permission was automatically granted
    with db.get_connection() as conn:
        perm = conn.execute(
            "SELECT 1 FROM teacher_exam_permissions WHERE admin_user_id = ? AND exam_id = ?",
            (admin_context["teacher_id"], exam_id),
        ).fetchone()
        assert perm is not None


def test_exam_create_validates_required_fields_and_rejects_empty_papers(admin_context):
    client = admin_context["client"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    csrf = _csrf(client)

    # Empty title
    resp1 = client.post(
        "/admin/exams/create",
        json={"csrf_token": csrf, "title": "", "questions": []},
    )
    assert resp1.status_code == 400
    assert "Examination title is required" in resp1.get_json()["error"]

    # Zero questions when publishing
    resp2 = client.post(
        "/admin/exams/create",
        json={"csrf_token": csrf, "title": "Empty Exam", "status": "published", "questions": []},
    )
    assert resp2.status_code == 400
    assert "At least one question is required" in resp2.get_json()["error"]

    # Question with no correct option
    resp3 = client.post(
        "/admin/exams/create",
        json={
            "csrf_token": csrf,
            "title": "Invalid MCQ Exam",
            "status": "published",
            "questions": [
                {
                    "statement": "Which one?",
                    "question_type": "mcq",
                    "marks": 2,
                    "options": [
                        {"option_text": "A", "is_correct": False},
                        {"option_text": "B", "is_correct": False},
                    ],
                }
            ],
        },
    )
    assert resp3.status_code == 400
    assert "must have at least one marked correct option" in resp3.get_json()["error"]


def test_question_bank_api_returns_questions(admin_context):
    client = admin_context["client"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    response = client.get("/admin/api/question-bank/questions")
    assert response.status_code == 200
    data = response.get_json()
    assert "questions" in data
    assert isinstance(data["questions"], list)


def test_exam_preview_route_and_role_scoping(admin_context):
    client = admin_context["client"]
    _login(client, "teacher@example.test", "secure-teacher-password")

    # Teacher has permission on exam_ids[0]
    allowed_id = admin_context["exam_ids"][0]
    response = client.get(f"/admin/exams/{allowed_id}/preview")
    assert response.status_code == 200
    assert b"EXAMSHIELD ACADEMIC PORTAL" in response.data
    assert b"General Instructions to Candidates" in response.data
    assert b"Toggle Answer Key" in response.data

    # Teacher does NOT have permission on exam_ids[1]
    forbidden_id = admin_context["exam_ids"][1]
    resp_forbidden = client.get(f"/admin/exams/{forbidden_id}/preview")
    assert resp_forbidden.status_code == 403


def test_exam_create_blocks_unauthenticated_and_student_users(admin_context):
    client = admin_context["client"]

    # Anonymous user gets redirected to admin login
    resp_anon = client.get("/admin/exams/create")
    assert resp_anon.status_code == 302
    assert "/admin/login" in resp_anon.headers["Location"]

    # Student session gets redirected to student dashboard
    with client.session_transaction() as sess:
        sess["student_id"] = 1
    resp_student = client.get("/admin/exams/create")
    assert resp_student.status_code == 302
    assert "/dashboard" in resp_student.headers["Location"]


def test_question_bank_page_renders_filters_and_statistics(admin_context):
    client = admin_context["client"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    response = client.get("/admin/question-bank")
    assert response.status_code == 200
    assert b"Question Bank" in response.data
    assert b"bank-filters-form" in response.data
    assert b"name=\"search\"" in response.data
    assert b"name=\"subject_id\"" in response.data
    assert b"name=\"topic\"" in response.data
    assert b"name=\"difficulty\"" in response.data
    assert b"name=\"type\"" in response.data
    assert b"name=\"marks\"" in response.data
    assert b"name=\"sort\"" in response.data
    assert b"bank-bulk-bar" in response.data
    assert b"Total Questions" in response.data


def test_question_bank_create_edit_duplicate_and_delete(admin_context):
    client = admin_context["client"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    csrf = _csrf(client)

    # 1. Add Question
    create_payload = {
        "csrf_token": csrf,
        "statement": "What is the time complexity of quicksort average case?",
        "subject_name": "Computer Science",
        "topic": "Sorting Algorithms",
        "question_type": "mcq",
        "difficulty": "Medium",
        "marks": 2.0,
        "negative_marks": 0.5,
        "explanation": "Average case is O(n log n) with random pivot.",
        "options": [
            {"option_text": "O(n)", "is_correct": False},
            {"option_text": "O(n log n)", "is_correct": True},
            {"option_text": "O(n^2)", "is_correct": False},
            {"option_text": "O(log n)", "is_correct": False},
        ],
    }
    resp_create = client.post(
        "/admin/question-bank/create",
        json=create_payload,
        headers={"X-CSRFToken": csrf},
    )
    assert resp_create.status_code == 200
    data = resp_create.get_json()
    assert data["success"] is True
    q_id = data["question_id"]
    assert q_id > 0

    # Verify retrieval
    resp_get = client.get(f"/admin/api/question-bank/{q_id}")
    assert resp_get.status_code == 200
    q_data = resp_get.get_json()["question"]
    assert q_data["statement"] == "What is the time complexity of quicksort average case?"
    assert q_data["marks"] == 2.0
    assert len(q_data["options"]) == 4

    # 2. Edit Question
    edit_payload = {
        "csrf_token": csrf,
        "statement": "What is the average runtime of Quicksort algorithm?",
        "topic": "Advanced Sorting",
        "question_type": "mcq",
        "difficulty": "Hard",
        "marks": 3.0,
        "negative_marks": 0.5,
        "explanation": "Average time is O(n log n).",
        "options": [
            {"option_text": "O(n)", "is_correct": False},
            {"option_text": "O(n log n)", "is_correct": True},
        ],
    }
    resp_edit = client.post(
        f"/admin/question-bank/{q_id}/edit",
        json=edit_payload,
        headers={"X-CSRFToken": csrf},
    )
    assert resp_edit.status_code == 200
    assert resp_edit.get_json()["success"] is True

    # Verify edit persistence
    resp_get2 = client.get(f"/admin/api/question-bank/{q_id}")
    q_data2 = resp_get2.get_json()["question"]
    assert q_data2["statement"] == "What is the average runtime of Quicksort algorithm?"
    assert q_data2["marks"] == 3.0
    assert q_data2["difficulty"] == "Hard"

    # 3. Duplicate Question
    resp_dup = client.post(
        f"/admin/question-bank/{q_id}/duplicate",
        headers={"X-CSRFToken": csrf},
    )
    assert resp_dup.status_code == 200
    dup_id = resp_dup.get_json()["new_question_id"]
    assert dup_id != q_id

    resp_get_dup = client.get(f"/admin/api/question-bank/{dup_id}")
    dup_data = resp_get_dup.get_json()["question"]
    assert "(Copy)" in dup_data["statement"]
    assert dup_data["marks"] == 3.0

    # 4. Delete Question
    resp_del = client.post(
        f"/admin/question-bank/{q_id}/delete",
        headers={"X-CSRFToken": csrf},
    )
    assert resp_del.status_code == 200
    assert resp_del.get_json()["success"] is True


def test_question_bank_validation_rejects_invalid_data(admin_context):
    client = admin_context["client"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    csrf = _csrf(client)

    # Empty statement
    resp1 = client.post(
        "/admin/question-bank/create",
        json={"csrf_token": csrf, "statement": "", "marks": 1},
        headers={"X-CSRFToken": csrf},
    )
    assert resp1.status_code == 400
    assert "statement cannot be empty" in resp1.get_json()["error"]

    # Zero marks
    resp2 = client.post(
        "/admin/question-bank/create",
        json={"csrf_token": csrf, "statement": "Valid statement", "marks": 0},
        headers={"X-CSRFToken": csrf},
    )
    assert resp2.status_code == 400

    # MCQ with fewer than 2 options
    resp3 = client.post(
        "/admin/question-bank/create",
        json={
            "csrf_token": csrf,
            "statement": "Valid statement",
            "marks": 1,
            "question_type": "mcq",
            "options": [{"option_text": "Only one", "is_correct": True}],
        },
        headers={"X-CSRFToken": csrf},
    )
    assert resp3.status_code == 400


def test_question_bank_filter_and_search_api(admin_context):
    client = admin_context["client"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    csrf = _csrf(client)

    # Add a specific distinct question to test search
    client.post(
        "/admin/question-bank/create",
        json={
            "csrf_token": csrf,
            "statement": "Quantum entanglement occurs when particles interact.",
            "subject_name": "Physics",
            "topic": "Quantum Mechanics",
            "question_type": "true_false",
            "difficulty": "Hard",
            "marks": 5.0,
            "options": [
                {"option_text": "True", "is_correct": True},
                {"option_text": "False", "is_correct": False},
            ],
        },
        headers={"X-CSRFToken": csrf},
    )

    # Search by keyword
    resp_search = client.get("/admin/question-bank?search=entanglement", headers={"Accept": "application/json"})
    assert resp_search.status_code == 200
    data_search = resp_search.get_json()
    assert len(data_search["questions"]) >= 1
    assert any("entanglement" in q["statement"].lower() for q in data_search["questions"])

    # Filter by difficulty
    resp_diff = client.get("/admin/question-bank?difficulty=Hard", headers={"Accept": "application/json"})
    assert resp_diff.status_code == 200
    data_diff = resp_diff.get_json()
    assert all(q["difficulty"] == "Hard" for q in data_diff["questions"])


def test_question_bank_import_to_exam_creates_associations_preserving_bank(admin_context):
    client = admin_context["client"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    csrf = _csrf(client)
    db = admin_context["database"]

    # 1. Create a question in the Question Bank
    res = client.post(
        "/admin/question-bank/create",
        json={
            "csrf_token": csrf,
            "statement": "Which layer of the OSI model handles routing?",
            "subject_name": "Computer Science",
            "topic": "Networking",
            "question_type": "mcq",
            "marks": 2.0,
            "options": [
                {"option_text": "Transport", "is_correct": False},
                {"option_text": "Network", "is_correct": True},
                {"option_text": "Data Link", "is_correct": False},
                {"option_text": "Physical", "is_correct": False},
            ],
        },
        headers={"X-CSRFToken": csrf},
    )
    bank_q_id = res.get_json()["question_id"]

    # 2. Import into authorised exam (exam_ids[0])
    target_exam = admin_context["exam_ids"][0]
    with db.get_connection() as conn:
        initial_q_count = conn.execute("SELECT COUNT(*) AS c FROM questions WHERE exam_id = ?", (target_exam,)).fetchone()["c"]

    resp_import = client.post(
        "/admin/question-bank/import-to-exam",
        json={
            "csrf_token": csrf,
            "question_ids": [bank_q_id],
            "exam_id": target_exam,
        },
        headers={"X-CSRFToken": csrf},
    )
    assert resp_import.status_code == 200
    import_data = resp_import.get_json()
    assert import_data["success"] is True
    assert import_data["imported_count"] == 1

    # Verify a new question row was added to the examination
    with db.get_connection() as conn:
        new_q_count = conn.execute("SELECT COUNT(*) AS c FROM questions WHERE exam_id = ?", (target_exam,)).fetchone()["c"]
        assert new_q_count == initial_q_count + 1

        # Check that the imported question exists in the exam with its options
        exam_q = conn.execute("SELECT * FROM questions WHERE exam_id = ? AND statement LIKE '%OSI model%'", (target_exam,)).fetchone()
        assert exam_q is not None
        opts = conn.execute("SELECT * FROM question_options WHERE question_id = ?", (exam_q["question_id"],)).fetchall()
        assert len(opts) == 4

    # 3. Verify the original reusable question in bank_questions was NOT modified or deleted
    bank_q = db.get_bank_question(bank_q_id)
    assert bank_q is not None
    assert bank_q["statement"] == "Which layer of the OSI model handles routing?"

    # 4. Verify teacher CANNOT import into unauthorized examination (exam_ids[1])
    forbidden_exam = admin_context["exam_ids"][1]
    resp_forbidden = client.post(
        "/admin/question-bank/import-to-exam",
        json={
            "csrf_token": csrf,
            "question_ids": [bank_q_id],
            "exam_id": forbidden_exam,
        },
        headers={"X-CSRFToken": csrf},
    )
    assert resp_forbidden.status_code == 403


def test_question_bank_bulk_delete(admin_context):
    client = admin_context["client"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    csrf = _csrf(client)

    # Create 2 questions
    r1 = client.post("/admin/question-bank/create", json={"csrf_token": csrf, "statement": "Bulk Q1", "marks": 1, "question_type": "short_answer", "correct_answer_text": "Ans1"}, headers={"X-CSRFToken": csrf})
    r2 = client.post("/admin/question-bank/create", json={"csrf_token": csrf, "statement": "Bulk Q2", "marks": 1, "question_type": "short_answer", "correct_answer_text": "Ans2"}, headers={"X-CSRFToken": csrf})
    id1 = r1.get_json()["question_id"]
    id2 = r2.get_json()["question_id"]

    resp_bulk = client.post(
        "/admin/question-bank/bulk-delete",
        json={"csrf_token": csrf, "question_ids": [id1, id2]},
        headers={"X-CSRFToken": csrf},
    )
    assert resp_bulk.status_code == 200
    assert resp_bulk.get_json()["deleted_count"] == 2


def test_admin_exams_table_columns_and_filters(admin_context):
    client = admin_context["client"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    exam_id = admin_context["exam_ids"][0]

    # 1. HTML View contains the 12 required table columns and status chips
    resp = client.get("/admin/exams")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "Examination name" in html
    assert "Subject" in html
    assert "Department" in html
    assert "Semester" in html
    assert "Created by" in html
    assert "Scheduled date" in html
    assert "Duration" in html
    assert "Total marks" in html
    assert "Enrolled" in html
    assert "Submissions" in html
    assert "Status" in html
    assert "Actions" in html

    # Status pills / tabs
    assert "All Examinations" in html
    assert "Live" in html
    assert "Scheduled" in html
    assert "Draft" in html

    # 2. JSON endpoint returns exams and status counts
    resp_json = client.get("/admin/exams?format=json")
    assert resp_json.status_code == 200
    data = resp_json.get_json()
    assert "exams" in data
    assert "status_counts" in data
    assert any(e["exam_id"] == exam_id for e in data["exams"])

    # 3. Filter by search keyword
    target_exam = next(e for e in data["exams"] if e["exam_id"] == exam_id)
    keyword = target_exam["name"][:5]
    resp_search = client.get(f"/admin/exams?search={keyword}&format=json")
    assert resp_search.status_code == 200
    search_data = resp_search.get_json()
    assert len(search_data["exams"]) >= 1
    assert any(e["exam_id"] == exam_id for e in search_data["exams"])


def test_admin_exam_status_transitions_and_authorization(admin_context):
    client = admin_context["client"]
    db = admin_context["database"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    csrf = _csrf(client)
    authorized_exam_id = admin_context["exam_ids"][0]
    unauthorized_exam_id = admin_context["exam_ids"][1]

    # 1. Transition authorized exam to Live
    resp_live = client.post(
        f"/admin/exams/{authorized_exam_id}/status",
        json={"csrf_token": csrf, "status": "live"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp_live.status_code == 200
    assert resp_live.get_json()["status"] == "live"

    # Verify database status and start_time
    with db.get_connection() as conn:
        row = conn.execute("SELECT status, start_time FROM exams WHERE exam_id = ?", (authorized_exam_id,)).fetchone()
        assert row["status"] == "live"
        assert row["start_time"] is not None

    # 2. Transition authorized exam to Completed
    resp_end = client.post(
        f"/admin/exams/{authorized_exam_id}/status",
        json={"csrf_token": csrf, "status": "completed"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp_end.status_code == 200
    assert resp_end.get_json()["status"] == "completed"

    with db.get_connection() as conn:
        row = conn.execute("SELECT status, end_time FROM exams WHERE exam_id = ?", (authorized_exam_id,)).fetchone()
        assert row["status"] == "completed"
        assert row["end_time"] is not None

    # 3. Transition authorized exam to Archived
    resp_archive = client.post(
        f"/admin/exams/{authorized_exam_id}/status",
        json={"csrf_token": csrf, "status": "archived"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp_archive.status_code == 200
    assert resp_archive.get_json()["status"] == "archived"

    # 4. Check audit logs were created
    resp_logs = client.get(f"/admin/api/exams/{authorized_exam_id}/audit-logs")
    assert resp_logs.status_code == 200
    logs = resp_logs.get_json()["logs"]
    assert len(logs) >= 3
    assert any(l["action"] == "STATUS_CHANGE" for l in logs)

    # 5. Teacher CANNOT modify status of unauthorized examination
    resp_forbidden = client.post(
        f"/admin/exams/{unauthorized_exam_id}/status",
        json={"csrf_token": csrf, "status": "live"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp_forbidden.status_code == 403


def test_admin_exam_scheduling_validation(admin_context):
    client = admin_context["client"]
    db = admin_context["database"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    csrf = _csrf(client)
    authorized_exam_id = admin_context["exam_ids"][0]
    unauthorized_exam_id = admin_context["exam_ids"][1]

    # 1. Valid schedule window
    resp_valid = client.post(
        f"/admin/exams/{authorized_exam_id}/schedule",
        json={
            "csrf_token": csrf,
            "start_time": "2026-12-01T09:00",
            "end_time": "2026-12-01T12:00",
        },
        headers={"X-CSRFToken": csrf},
    )
    assert resp_valid.status_code == 200
    assert resp_valid.get_json()["success"] is True

    with db.get_connection() as conn:
        row = conn.execute("SELECT status, start_time, end_time FROM exams WHERE exam_id = ?", (authorized_exam_id,)).fetchone()
        assert row["status"] == "scheduled"
        assert row["start_time"] == "2026-12-01T09:00"
        assert row["end_time"] == "2026-12-01T12:00"

    # 2. Invalid schedule where end_time <= start_time
    resp_invalid = client.post(
        f"/admin/exams/{authorized_exam_id}/schedule",
        json={
            "csrf_token": csrf,
            "start_time": "2026-12-01T15:00",
            "end_time": "2026-12-01T11:00",
        },
        headers={"X-CSRFToken": csrf},
    )
    assert resp_invalid.status_code == 400
    assert "Scheduled end time must be later than start time" in resp_invalid.get_json()["error"]

    # 3. Unauthorized teacher attempt
    resp_forbidden = client.post(
        f"/admin/exams/{unauthorized_exam_id}/schedule",
        json={
            "csrf_token": csrf,
            "start_time": "2026-12-01T09:00",
            "end_time": "2026-12-01T12:00",
        },
        headers={"X-CSRFToken": csrf},
    )
    assert resp_forbidden.status_code == 403


def test_admin_exam_duplication(admin_context):
    client = admin_context["client"]
    db = admin_context["database"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    csrf = _csrf(client)
    source_exam_id = admin_context["exam_ids"][0]

    resp_dup = client.post(
        f"/admin/exams/{source_exam_id}/duplicate",
        json={"csrf_token": csrf},
        headers={"X-CSRFToken": csrf},
    )
    assert resp_dup.status_code == 200
    data = resp_dup.get_json()
    assert data["success"] is True
    new_exam_id = data["new_exam_id"]
    assert new_exam_id != source_exam_id

    # Verify duplicated exam properties: title contains (Copy), status is Draft, questions cloned
    new_exam = db.get_exam_full_detail(new_exam_id)
    assert new_exam is not None
    assert "(Copy)" in new_exam["name"]
    assert new_exam["status"].lower() == "draft"
    assert len(new_exam["questions"]) > 0

    # Verify teacher has access to the cloned exam via HTTP endpoint and database
    resp_list = client.get("/admin/exams?format=json")
    assert resp_list.status_code == 200
    teacher_exams = resp_list.get_json()["exams"]
    assert any(e["exam_id"] == new_exam_id for e in teacher_exams)


def test_admin_exam_editing_and_submissions_integrity(admin_context):
    client = admin_context["client"]
    db = admin_context["database"]
    _login(client, "teacher@example.test", "secure-teacher-password")
    csrf = _csrf(client)
    exam_id = admin_context["exam_ids"][0]
    unauthorized_exam_id = admin_context["exam_ids"][1]

    # 1. GET edit page
    resp_get = client.get(f"/admin/exams/{exam_id}/edit")
    assert resp_get.status_code == 200
    assert b"Edit Examination" in resp_get.data

    # 2. Unauthorized teacher cannot GET or POST edit page
    resp_unauth_get = client.get(f"/admin/exams/{unauthorized_exam_id}/edit")
    assert resp_unauth_get.status_code == 403

    resp_unauth_post = client.post(
        f"/admin/exams/{unauthorized_exam_id}/edit",
        json={"csrf_token": csrf, "title": "Hacked Title"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp_unauth_post.status_code == 403

    # 3. Teacher edits exam details and updates questions
    exam_detail = db.get_exam_full_detail(exam_id)
    existing_questions = exam_detail["questions"]
    first_q = existing_questions[0]

    updated_title = f"{exam_detail['name']} - Revised 2026"
    resp_edit = client.post(
        f"/admin/exams/{exam_id}/edit",
        json={
            "csrf_token": csrf,
            "title": updated_title,
            "duration_minutes": 65,
            "department": "Computer Science & Engineering",
            "semester": "Semester 6",
            "instructions": "Answer all questions carefully.",
            "pass_marks": 1.0,
            "negative_marking": 0.25,
            "randomize_questions": True,
            "randomize_options": True,
            "allow_multiple_attempts": False,
            "questions": [
                {
                    "question_id": first_q["question_id"],
                    "statement": "Updated Question Statement for Exam Management Test?",
                    "marks": 2.0,
                    "question_type": "mcq",
                    "explanation": "Updated explanation",
                    "options": [
                        {"option_text": "Option Alpha", "is_correct": True},
                        {"option_text": "Option Beta", "is_correct": False},
                    ],
                }
            ],
        },
        headers={"X-CSRFToken": csrf},
    )
    assert resp_edit.status_code == 200
    assert resp_edit.get_json()["success"] is True

    # 4. Verify updates in DB
    refreshed = db.get_exam_full_detail(exam_id)
    assert refreshed["name"] == updated_title
    assert refreshed["duration_minutes"] == 65
    assert refreshed["semester"] == "Semester 6"

    # 5. Check submissions route
    resp_subs = client.get(f"/admin/exams/{exam_id}/submissions")
    assert resp_subs.status_code == 200
    assert b"Examination Submissions" in resp_subs.data

    resp_subs_json = client.get(f"/admin/exams/{exam_id}/submissions?format=json")
    assert resp_subs_json.status_code == 200
    assert "submissions" in resp_subs_json.get_json()

    # 6. Unauthorized teacher cannot access submissions
    resp_unauth_subs = client.get(f"/admin/exams/{unauthorized_exam_id}/submissions")
    assert resp_unauth_subs.status_code == 403


def test_student_directory_table_columns_and_filtering(admin_context):
    client = admin_context["client"]
    db = admin_context["database"]
    _login(client, "superadmin@example.test", "secure-admin-password")

    # 1. HTML view rendering
    resp = client.get("/admin/students")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "Student name" in html
    assert "Registration number" in html
    assert "Email" in html
    assert "Department" in html
    assert "Semester" in html
    assert "Total exams attempted" in html
    assert "Average percentage" in html
    assert "Overall performance" in html
    assert "Account status" in html
    assert "Actions" in html

    # 2. JSON endpoint returns student records and summary
    resp_json = client.get("/admin/students?format=json")
    assert resp_json.status_code == 200
    data = resp_json.get_json()
    assert "students" in data
    assert "total" in data
    assert data["total"] > 0
    first_st = data["students"][0]
    assert "student_id" in first_st
    assert "name" in first_st
    assert "usn" in first_st
    assert "email" in first_st
    assert "department" in first_st
    assert "semester" in first_st
    assert "total_attempts" in first_st
    assert "overall_performance" in first_st
    assert "account_status" in first_st

    # 3. Search by student name
    target_name = first_st["name"]
    resp_search = client.get(f"/admin/students?search={target_name}&format=json")
    assert resp_search.status_code == 200
    search_data = resp_search.get_json()
    assert len(search_data["students"]) >= 1
    assert any(s["name"] == target_name for s in search_data["students"])

    # 4. Search by registration number (USN)
    target_usn = first_st["usn"]
    resp_search_usn = client.get(f"/admin/students?search={target_usn}&format=json")
    assert resp_search_usn.status_code == 200
    usn_data = resp_search_usn.get_json()
    assert len(usn_data["students"]) >= 1
    assert any(s["usn"] == target_usn for s in usn_data["students"])


def test_student_profile_details_metrics_and_charts(admin_context):
    client = admin_context["client"]
    db = admin_context["database"]
    _login(client, "superadmin@example.test", "secure-admin-password")

    # Find a student who has an attempt
    with db.get_connection() as conn:
        attempt_student = conn.execute(
            """
            SELECT s.student_id, s.name, s.usn FROM students s
            JOIN exam_attempts a ON a.student_id = s.student_id
            LIMIT 1
            """
        ).fetchone()

    assert attempt_student is not None
    st_id = attempt_student["student_id"]

    # 1. GET profile HTML
    resp = client.get(f"/admin/students/{st_id}")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert attempt_student["name"] in html
    assert attempt_student["usn"] in html
    assert "Total Examinations" in html
    assert "Average Marks" in html
    assert "Average Percentage" in html
    assert "Highest Score" in html
    assert "Lowest Score" in html
    assert "Performance Progression Timeline" in html
    assert "Subject-Wise Proficiency" in html
    assert "Examination Participation" in html

    # 2. JSON format endpoint returns complete profile dictionary
    resp_json = client.get(f"/admin/students/{st_id}?format=json")
    assert resp_json.status_code == 200
    data = resp_json.get_json()
    assert "student" in data
    assert "total_attempts" in data
    assert "avg_marks" in data
    assert "avg_percentage" in data
    assert "highest_score" in data
    assert "lowest_score" in data
    assert "passed_count" in data
    assert "examination_history" in data
    assert "subject_performance" in data
    assert "performance_progression" in data
    assert "strongest_topics" in data
    assert "weakest_topics" in data
    assert data["student"]["student_id"] == st_id

    # 3. Nonexistent student returns 404
    resp_404 = client.get("/admin/students/999999")
    assert resp_404.status_code == 404


def test_student_profile_teacher_permission_scoping(admin_context):
    client = admin_context["client"]
    db = admin_context["database"]
    _login(client, "teacher@example.test", "secure-teacher-password")

    # In fixture: assigned_student attempted exam_ids[0] (authorized for teacher)
    # other_student attempted exam_ids[1] (unauthorized for teacher)
    with db.get_connection() as conn:
        assigned_id = conn.execute("SELECT student_id FROM students WHERE name = 'Assigned Student'").fetchone()["student_id"]
        other_id = conn.execute("SELECT student_id FROM students WHERE name = 'Other Student'").fetchone()["student_id"]

    # 1. Teacher CAN access authorized student's profile
    resp_assigned = client.get(f"/admin/students/{assigned_id}")
    assert resp_assigned.status_code == 200
    assert b"Assigned Student" in resp_assigned.data

    # 2. Teacher CANNOT access unauthorized student's profile (HTTP 403)
    resp_forbidden = client.get(f"/admin/students/{other_id}")
    assert resp_forbidden.status_code == 403

    # 3. API endpoint also returns 403 for unauthorized student
    resp_api_forbidden = client.get(f"/admin/api/students/{other_id}/performance")
    assert resp_api_forbidden.status_code == 403


def test_student_directory_sorting_and_participation_filter(admin_context):
    client = admin_context["client"]
    _login(client, "superadmin@example.test", "secure-admin-password")

    # Filter by participation: attempted
    resp_attempted = client.get("/admin/students?participation=attempted&format=json")
    assert resp_attempted.status_code == 200
    attempted_data = resp_attempted.get_json()["students"]
    for s in attempted_data:
        assert s["total_attempts"] > 0

    # Filter by participation: none
    resp_none = client.get("/admin/students?participation=none&format=json")
    assert resp_none.status_code == 200
    none_data = resp_none.get_json()["students"]
    for s in none_data:
        assert s["total_attempts"] == 0

    # Sort by performance descending
    resp_sort_perf = client.get("/admin/students?sort_by=perf_desc&format=json")
    assert resp_sort_perf.status_code == 200
    sort_data = resp_sort_perf.get_json()["students"]
    percentages = [s["avg_percentage"] for s in sort_data if s["avg_percentage"] is not None]
    if len(percentages) >= 2:
        assert percentages == sorted(percentages, reverse=True)


def test_reports_page_render_and_json_analytics(admin_context):
    client = admin_context["client"]
    _login(client, "superadmin@example.test", "secure-admin-password")

    # 1. Test HTML rendering
    resp_html = client.get("/admin/reports")
    assert resp_html.status_code == 200
    assert b"Student Performance Analytics" in resp_html.data
    assert b"Total Enrolled" in resp_html.data
    assert b"Average Performance" in resp_html.data
    assert b"Subject-Wise Performance" in resp_html.data
    assert b"Performance Progression" in resp_html.data
    assert b"Pass / Fail Distribution" in resp_html.data
    assert b"Score Distribution Histogram" in resp_html.data
    assert b"Question-Wise Accuracy &amp; Difficulty Rating" in resp_html.data
    assert b"Export PDF" in resp_html.data
    assert b"Export Excel" in resp_html.data
    assert b"Export CSV" in resp_html.data

    # 2. Test JSON API response
    resp_json = client.get("/admin/reports?format=json")
    assert resp_json.status_code == 200
    payload = resp_json.get_json()
    assert payload["status"] == "success"
    analytics = payload["analytics"]
    assert "overview" in analytics
    assert "score_distribution" in analytics
    assert "grade_distribution" in analytics
    assert "subject_performance" in analytics
    assert "exam_analytics" in analytics
    assert "student_rankings" in analytics
    assert "question_accuracy" in analytics
    assert "easiest_questions" in analytics
    assert "most_difficult_questions" in analytics
    assert "filters" in payload


def test_reports_filtering_and_active_filters(admin_context):
    client = admin_context["client"]
    exam_ids = admin_context["exam_ids"]
    _login(client, "superadmin@example.test", "secure-admin-password")

    # Filter by specific examination
    resp = client.get(f"/admin/reports?exam_id={exam_ids[0]}&format=json")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert data["filters"]["exam_id"] == exam_ids[0]
    # Filtered exams should only contain exam_ids[0]
    assert len(data["analytics"]["exam_analytics"]) == 1
    assert data["analytics"]["exam_analytics"][0]["exam_id"] == exam_ids[0]

    # Filter with non-matching subject
    resp_empty = client.get("/admin/reports?subject_id=999999&format=json")
    assert resp_empty.status_code == 200
    empty_data = resp_empty.get_json()
    assert empty_data["analytics"]["overview"]["total_enrolled"] == 0
    assert len(empty_data["analytics"]["exam_analytics"]) == 0


def test_reports_teacher_permission_scoping(admin_context):
    client = admin_context["client"]
    exam_ids = admin_context["exam_ids"]
    _login(client, "teacher@example.test", "secure-teacher-password")

    # 1. Teacher can access authorized exam (exam_ids[0])
    resp_ok = client.get(f"/admin/reports?exam_id={exam_ids[0]}&format=json")
    assert resp_ok.status_code == 200
    assert resp_ok.get_json()["status"] == "success"

    # 2. Teacher CANNOT access unauthorized exam (exam_ids[1]) -> HTTP 403
    resp_forbidden_json = client.get(f"/admin/reports?exam_id={exam_ids[1]}&format=json")
    assert resp_forbidden_json.status_code == 403
    assert "Access denied" in resp_forbidden_json.get_json()["error"]

    resp_forbidden_html = client.get(f"/admin/reports?exam_id={exam_ids[1]}")
    assert resp_forbidden_html.status_code == 403
    assert b"Access Warning" in resp_forbidden_html.data


def test_reports_export_endpoints(admin_context):
    client = admin_context["client"]
    exam_ids = admin_context["exam_ids"]
    _login(client, "superadmin@example.test", "secure-admin-password")

    # 1. Export PDF
    resp_pdf = client.get("/admin/reports/export/pdf")
    assert resp_pdf.status_code == 200
    assert resp_pdf.mimetype == "application/pdf"
    assert resp_pdf.data.startswith(b"%PDF-")
    assert len(resp_pdf.data) > 1000

    # 2. Export XLSX (Excel)
    resp_xlsx = client.get("/admin/reports/export/xlsx")
    assert resp_xlsx.status_code == 200
    assert resp_xlsx.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert resp_xlsx.data.startswith(b"PK")  # ZIP header for XLSX
    assert len(resp_xlsx.data) > 1000

    # 3. Export CSV
    resp_csv = client.get("/admin/reports/export/csv")
    assert resp_csv.status_code == 200
    assert resp_csv.mimetype == "text/csv"
    assert "EXAMSHIELD AI - PERFORMANCE REPORT (CSV)" in resp_csv.text
    assert "COHORT PERFORMANCE OVERVIEW" in resp_csv.text

    # 4. Invalid export format
    resp_invalid = client.get("/admin/reports/export/unsupported_format")
    assert resp_invalid.status_code == 400

    # 5. Teacher unauthorized export returns 403
    with client.session_transaction() as sess:
        sess.clear()
    _login(client, "teacher@example.test", "secure-teacher-password")
    resp_teacher_forbidden = client.get(f"/admin/reports/export/pdf?exam_id={exam_ids[1]}")
    assert resp_teacher_forbidden.status_code == 403


def test_reports_analytics_calculation_fidelity(admin_context):
    client = admin_context["client"]
    db = admin_context["database"]
    _login(client, "superadmin@example.test", "secure-admin-password")

    with db.get_connection() as conn:
        # Create a dedicated subject, bank, and exam
        subj_id = conn.execute("INSERT INTO subjects (name) VALUES ('Software Engineering')").lastrowid
        bank_id = conn.execute("INSERT INTO question_banks (subject_id, name) VALUES (?, 'SE Bank')", (subj_id,)).lastrowid
        exam_id = conn.execute("""
            INSERT INTO exams (bank_id, name, duration_minutes, max_marks, pass_marks, department, semester, academic_year, status)
            VALUES (?, 'SE Final 2026', 60, 20.0, 10.0, 'Information Technology', 'Semester 6', '2025-2026', 'published')
        """, (bank_id,)).lastrowid

        # Insert 3 questions with different topics and difficulties
        q1 = conn.execute("""
            INSERT INTO questions (exam_id, statement, topic, difficulty, marks, sort_order)
            VALUES (?, 'What is Agile methodology?', 'Agile', 'Easy', 5.0, 1)
        """, (exam_id,)).lastrowid
        q1_opt_correct = conn.execute("INSERT INTO question_options (question_id, option_text, is_correct) VALUES (?, 'Iterative', 1)", (q1,)).lastrowid
        q1_opt_wrong = conn.execute("INSERT INTO question_options (question_id, option_text, is_correct) VALUES (?, 'Linear only', 0)", (q1,)).lastrowid

        q2 = conn.execute("""
            INSERT INTO questions (exam_id, statement, topic, difficulty, marks, sort_order)
            VALUES (?, 'Explain SOLID design principles', 'Design Patterns', 'Hard', 10.0, 2)
        """, (exam_id,)).lastrowid
        q2_opt_correct = conn.execute("INSERT INTO question_options (question_id, option_text, is_correct) VALUES (?, 'Object oriented principles', 1)", (q2,)).lastrowid
        q2_opt_wrong = conn.execute("INSERT INTO question_options (question_id, option_text, is_correct) VALUES (?, 'Procedural guidelines', 0)", (q2,)).lastrowid

        q3 = conn.execute("""
            INSERT INTO questions (exam_id, statement, topic, difficulty, marks, sort_order)
            VALUES (?, 'What is unit testing?', 'Testing', 'Medium', 5.0, 3)
        """, (exam_id,)).lastrowid
        q3_opt_correct = conn.execute("INSERT INTO question_options (question_id, option_text, is_correct) VALUES (?, 'Testing individual components', 1)", (q3,)).lastrowid
        q3_opt_wrong = conn.execute("INSERT INTO question_options (question_id, option_text, is_correct) VALUES (?, 'Testing full systems only', 0)", (q3,)).lastrowid

        # Insert 3 students:
        # Student A: scored 19/20 (95%), Passed, Grade A+
        st_a = conn.execute("""
            INSERT INTO students (name, usn, email, department, semester, account_status, subject, exam_code)
            VALUES ('Alice Developer', '1IT26001', 'alice@example.test', 'Information Technology', 'Semester 6', 'Active', 'Software Engineering', 'SE2026')
        """).lastrowid
        att_a = conn.execute("""
            INSERT INTO exam_attempts (student_id, exam_id, status, started_at, submitted_at)
            VALUES (?, ?, 'submitted', '2026-03-01 10:00:00', '2026-03-01 10:45:00')
        """, (st_a, exam_id)).lastrowid
        conn.execute("""
            INSERT INTO exam_results (attempt_id, marks_obtained, percentage, correct_answers, incorrect_answers, unanswered, time_taken_seconds)
            VALUES (?, 19.0, 95.0, 3, 0, 0, 2700)
        """, (att_a,))
        # Answers for Alice: correct on q1, q2, q3
        conn.execute("INSERT INTO student_answers (attempt_id, question_id, option_id) VALUES (?, ?, ?)", (att_a, q1, q1_opt_correct))
        conn.execute("INSERT INTO student_answers (attempt_id, question_id, option_id) VALUES (?, ?, ?)", (att_a, q2, q2_opt_correct))
        conn.execute("INSERT INTO student_answers (attempt_id, question_id, option_id) VALUES (?, ?, ?)", (att_a, q3, q3_opt_correct))

        # Student B: scored 8/20 (40%), Failed, Grade F
        st_b = conn.execute("""
            INSERT INTO students (name, usn, email, department, semester, account_status, subject, exam_code)
            VALUES ('Bob Slacker', '1IT26002', 'bob@example.test', 'Information Technology', 'Semester 6', 'Active', 'Software Engineering', 'SE2026')
        """).lastrowid
        att_b = conn.execute("""
            INSERT INTO exam_attempts (student_id, exam_id, status, started_at, submitted_at)
            VALUES (?, ?, 'submitted', '2026-03-01 10:00:00', '2026-03-01 10:55:00')
        """, (st_b, exam_id)).lastrowid
        conn.execute("""
            INSERT INTO exam_results (attempt_id, marks_obtained, percentage, correct_answers, incorrect_answers, unanswered, time_taken_seconds)
            VALUES (?, 8.0, 40.0, 1, 1, 1, 3300)
        """, (att_b,))
        # Answers for Bob: correct on q1, wrong on q2, unanswered on q3
        conn.execute("INSERT INTO student_answers (attempt_id, question_id, option_id) VALUES (?, ?, ?)", (att_b, q1, q1_opt_correct))
        conn.execute("INSERT INTO student_answers (attempt_id, question_id, option_id) VALUES (?, ?, ?)", (att_b, q2, q2_opt_wrong))

        # Student C: in progress attempt (not evaluated yet)
        st_c = conn.execute("""
            INSERT INTO students (name, usn, email, department, semester, account_status, subject, exam_code)
            VALUES ('Charlie Ongoing', '1IT26003', 'charlie@example.test', 'Information Technology', 'Semester 6', 'Active', 'Software Engineering', 'SE2026')
        """).lastrowid
        conn.execute("""
            INSERT INTO exam_attempts (student_id, exam_id, status, started_at)
            VALUES (?, ?, 'in_progress', '2026-03-01 10:00:00')
        """, (st_c, exam_id))

    # Query analytics for this exam
    resp = client.get(f"/admin/reports?exam_id={exam_id}&format=json")
    assert resp.status_code == 200
    res = resp.get_json()["analytics"]

    # 1. Overview Checks
    overview = res["overview"]
    assert overview["students_attempted"] == 3
    assert overview["students_evaluated"] == 2  # Charlie in_progress is excluded from marks evaluation
    assert overview["highest_marks"] == 19.0
    assert overview["lowest_marks"] == 8.0
    assert overview["average_marks"] == 13.5  # (19 + 8) / 2
    assert overview["average_percentage"] == 67.5  # (95 + 40) / 2
    assert overview["pass_percentage"] == 50.0  # Alice passed (>= 10), Bob failed
    assert overview["failure_percentage"] == 50.0

    # 2. Score & Grade Distribution Checks
    score_dist = res["score_distribution"]
    assert score_dist["21-40%"] == 1  # Bob: 40%
    assert score_dist["81-100%"] == 1  # Alice: 95%
    assert score_dist["0-20%"] == 0

    grade_dist = res["grade_distribution"]
    assert grade_dist["A+"] == 1  # Alice
    assert grade_dist["F"] == 1  # Bob

    # 3. Question-Wise Accuracy Checks
    q_acc = res["question_accuracy"]
    assert len(q_acc) == 3
    # Q1: 2 candidates answered, both correct -> 100%
    q1_stat = next(q for q in q_acc if q["question_id"] == q1)
    assert q1_stat["accuracy"] == 100.0
    assert q1_stat["correct_count"] == 2
    # Q2: 1 correct (Alice), 1 incorrect (Bob) -> 50%
    q2_stat = next(q for q in q_acc if q["question_id"] == q2)
    assert q2_stat["accuracy"] == 50.0
    assert q2_stat["correct_count"] == 1
    assert q2_stat["incorrect_count"] == 1
    # Q3: 1 correct (Alice), Bob unanswered -> 50%
    q3_stat = next(q for q in q_acc if q["question_id"] == q3)
    assert q3_stat["accuracy"] == 50.0
    assert q3_stat["unanswered_count"] == 1

    # 4. Easiest and Hardest questions
    assert res["easiest_questions"][0]["question_id"] == q1
    assert res["easiest_questions"][0]["accuracy"] == 100.0

    # 5. Rankings & Individual Student Analytics
    rankings = res["student_rankings"]
    assert len(rankings) == 2
    alice_rank = rankings[0]
    assert alice_rank["name"] == "Alice Developer"
    assert alice_rank["rank"] == 1
    assert alice_rank["grade"] == "A+"
    assert alice_rank["status"] in ("Pass", "Passed")
    assert alice_rank["correct_answers"] == 3
    assert alice_rank["incorrect_answers"] == 0
    assert alice_rank["unanswered_questions"] == 0
    assert "Agile" in alice_rank["topic_strengths"]

    bob_rank = rankings[1]
    assert bob_rank["name"] == "Bob Slacker"
    assert bob_rank["rank"] == 2
    assert bob_rank["grade"] == "F"
    assert bob_rank["status"] in ("Fail", "Failed")
    assert bob_rank["correct_answers"] == 1
    assert bob_rank["incorrect_answers"] == 1
    assert bob_rank["unanswered_questions"] == 1
    assert "Design Patterns" in bob_rank["topic_weaknesses"]

    # 6. Top Students & Support Needed Alerts
    assert res["top_students"][0]["name"] == "Alice Developer"
    assert len(res["support_needed_students"]) == 1
    assert res["support_needed_students"][0]["name"] == "Bob Slacker"

    # 7. Filter by Department & Semester
    resp_dept = client.get("/admin/reports?department=Information+Technology&semester=Semester+6&format=json")
    assert resp_dept.status_code == 200
    assert resp_dept.get_json()["filters"]["department"] == "Information Technology"
    assert resp_dept.get_json()["filters"]["semester"] == "Semester 6"

    # 8. Filter by Individual Student
    resp_st = client.get(f"/admin/reports?student_id={st_a}&format=json")
    assert resp_st.status_code == 200
    st_data = resp_st.get_json()["analytics"]["student_rankings"]
    assert len(st_data) == 1
    assert st_data[0]["name"] == "Alice Developer"

    # 9. Verify consistent data structure keys (averages, aliases, percentages)
    subj_perf = res["subject_performance"]
    assert len(subj_perf) >= 1
    assert "average_percentage" in subj_perf[0]
    assert "avg_percentage" in subj_perf[0]
    assert "average_marks" in subj_perf[0]
    assert "avg_marks" in subj_perf[0]
    assert "pass_percentage" in subj_perf[0]
    assert "pass_rate" in subj_perf[0]
    assert subj_perf[0]["average_percentage"] == 67.5

    assert "average_percentage" in alice_rank
    assert "avg_percentage" in alice_rank
    assert "average_marks" in alice_rank
    assert "avg_marks" in alice_rank
    assert "registration_number" in alice_rank
    assert "student_name" in alice_rank
    assert "total_examinations_attempted" in alice_rank
    assert "overall_performance" in alice_rank
    assert "pass_fail_status" in alice_rank
    assert alice_rank["average_percentage"] == 95.0

    # 10. Verify HTML Rendering with populated attempts and subject performance
    # This directly verifies that Jinja2 renders without UndefinedError: 'dict object' has no attribute 'average_percentage'
    resp_html = client.get(f"/admin/reports?exam_id={exam_id}")
    assert resp_html.status_code == 200
    assert b"Software Engineering" in resp_html.data
    assert b"Alice Developer" in resp_html.data
    assert b"67.5%" in resp_html.data
    assert b"Pass" in resp_html.data


def test_configurable_grading_system(admin_context):
    client = admin_context["client"]
    db = admin_context["database"]

    # 1. Verify default boundaries in database
    default_boundaries = db.get_grade_boundaries()
    assert len(default_boundaries) >= 5
    assert any(b["grade"] == "A+" for b in default_boundaries)
    assert any(b["grade"] == "F" for b in default_boundaries)

    # 2. Teacher cannot access or update grading settings (403)
    _login(client, "teacher@example.test", "secure-teacher-password")
    resp_t = client.get("/admin/settings")
    assert resp_t.status_code == 403
    resp_t_api = client.get("/admin/api/settings/grading")
    assert resp_t_api.status_code == 403

    # 3. Super Admin logs in and configures custom grading boundaries
    with client.session_transaction() as sess:
        sess.clear()
    _login(client, "superadmin@example.test", "secure-admin-password")
    resp_sa = client.get("/admin/settings")
    assert resp_sa.status_code == 200
    assert b"Configurable Grading Scale" in resp_sa.data

    custom_boundaries = [
        {"grade": "A+", "min_score": 95.0, "max_score": 100.0, "description": "High Honors", "sort_order": 1},
        {"grade": "A", "min_score": 85.0, "max_score": 94.9, "description": "Honors", "sort_order": 2},
        {"grade": "B", "min_score": 75.0, "max_score": 84.9, "description": "Above Average", "sort_order": 3},
        {"grade": "C", "min_score": 65.0, "max_score": 74.9, "description": "Average", "sort_order": 4},
        {"grade": "D", "min_score": 55.0, "max_score": 64.9, "description": "Pass", "sort_order": 5},
        {"grade": "F", "min_score": 0.0, "max_score": 54.9, "description": "Fail", "sort_order": 6},
    ]

    csrf_tok = _csrf(client)
    resp_update = client.post(
        "/admin/api/settings/grading",
        json={"boundaries": custom_boundaries, "csrf_token": csrf_tok},
    )
    assert resp_update.status_code == 200
    assert resp_update.get_json()["success"] is True

    # 4. Verify grade calculation respects the newly configured boundaries
    assert db.calculate_grade(96.0) == "A+"
    assert db.calculate_grade(90.0) == "A"   # In default was A+, now under new scale it's A
    assert db.calculate_grade(80.0) == "B"   # In default was A, now B
    assert db.calculate_grade(50.0) == "F"   # In default was D, now F (< 55)

    # 5. Verify pass/fail status calculation
    assert db.calculate_pass_fail(marks_obtained=12, pass_marks=10, percentage=60.0).lower() in ("pass", "passed")
    assert db.calculate_pass_fail(marks_obtained=8, pass_marks=10, percentage=40.0).lower() in ("fail", "failed")


def test_negative_marking_and_subjective_handling(admin_context):
    client = admin_context["client"]
    db = admin_context["database"]

    with db.get_connection() as conn:
        subj_id = conn.execute("INSERT INTO subjects (name) VALUES ('Calculus')").lastrowid
        bank_id = conn.execute("INSERT INTO question_banks (subject_id, name) VALUES (?, 'Calc Bank')", (subj_id,)).lastrowid
        # Exam with negative marking = 1.0 mark penalty
        exam_id = conn.execute("""
            INSERT INTO exams (bank_id, name, duration_minutes, max_marks, pass_marks, negative_marking, status)
            VALUES (?, 'Calculus Midterm', 45, 20.0, 10.0, 1.0, 'published')
        """, (bank_id,)).lastrowid

        # Objective question: 5 marks, 1 mark penalty
        q_mcq = conn.execute("""
            INSERT INTO questions (exam_id, statement, marks, negative_marks, question_type, sort_order)
            VALUES (?, 'What is d/dx of sin(x)?', 5.0, 1.0, 'mcq', 1)
        """, (exam_id,)).lastrowid
        opt_correct = conn.execute("INSERT INTO question_options (question_id, option_text, is_correct) VALUES (?, 'cos(x)', 1)", (q_mcq,)).lastrowid
        opt_wrong = conn.execute("INSERT INTO question_options (question_id, option_text, is_correct) VALUES (?, '-cos(x)', 0)", (q_mcq,)).lastrowid

        # Subjective short answer question: 5 marks
        q_subj = conn.execute("""
            INSERT INTO questions (exam_id, statement, marks, question_type, sort_order)
            VALUES (?, 'State Rolle Theorem in your own words.', 5.0, 'short_answer', 2)
        """, (exam_id,)).lastrowid

        # Student 1: gets MCQ wrong, submits subjective answer
        st1 = conn.execute("INSERT INTO students (name, usn, subject, exam_code) VALUES ('Student Neg', 'USN001', 'Calculus', 'CALC')").lastrowid
        att1 = conn.execute("INSERT INTO exam_attempts (student_id, exam_id, status, started_at) VALUES (?, ?, 'in_progress', '2026-03-01 10:00:00')", (st1, exam_id)).lastrowid

    # Save answers: student selects wrong option for MCQ, enters text for subjective
    with client.session_transaction() as sess:
        sess.clear()
        sess["student_id"] = st1

    resp_ans1 = client.post(f"/api/attempt/{att1}/answer", json={"question_id": q_mcq, "option_id": opt_wrong})
    assert resp_ans1.status_code == 200
    resp_ans2 = client.post(f"/api/attempt/{att1}/answer", json={"question_id": q_subj, "answer_text": "If f is continuous on [a,b]..."})
    assert resp_ans2.status_code == 200

    # Submit exam
    resp_sub = client.post(f"/attempt/{att1}/submit", follow_redirects=True)
    assert resp_sub.status_code == 200

    # Check attempt results in database:
    # Because MCQ was wrong, score = 0 - 1.0 -> floored at 0.0
    # Because subjective question exists, evaluation_status = 'pending_evaluation' and is_published = 0
    res = db.get_attempt(att1)["result"]
    assert res["evaluation_status"] == "pending_evaluation"
    assert res["is_published"] == 0
    assert res["marks_obtained"] == 0.0

    # Student viewing /attempt/{att1}/result MUST see "Evaluation in progress" and NOT see final published grade
    resp_res = client.get(f"/attempt/{att1}/result")
    assert resp_res.status_code == 200
    assert b"Evaluation In Progress" in resp_res.data
    assert b"Score &amp; Grade Withheld Until Publication" in resp_res.data


def test_teacher_subjective_evaluation_and_publishing(admin_context):
    client = admin_context["client"]
    db = admin_context["database"]

    with db.get_connection() as conn:
        subj_id = conn.execute("INSERT INTO subjects (name) VALUES ('History')").lastrowid
        bank_id = conn.execute("INSERT INTO question_banks (subject_id, name) VALUES (?, 'History Bank')", (subj_id,)).lastrowid
        exam_id = conn.execute("""
            INSERT INTO exams (bank_id, name, duration_minutes, max_marks, pass_marks, status)
            VALUES (?, 'World War II Review', 60, 20.0, 10.0, 'published')
        """, (bank_id,)).lastrowid

        # Assign exam permission to Teacher
        teacher_id = admin_context["teacher_id"]
        conn.execute("INSERT OR IGNORE INTO teacher_exam_permissions (admin_user_id, exam_id) VALUES (?, ?)", (teacher_id, exam_id))

        # Subjective Question 1 (10 marks)
        q1 = conn.execute("""
            INSERT INTO questions (exam_id, statement, marks, question_type, sort_order, correct_answer_text)
            VALUES (?, 'Analyze the strategic significance of the Battle of Midway.', 10.0, 'long_answer', 1, 'Marked turning point in Pacific Theater.')
        """, (exam_id,)).lastrowid

        # Objective Question 2 (10 marks)
        q2 = conn.execute("""
            INSERT INTO questions (exam_id, statement, marks, question_type, sort_order)
            VALUES (?, 'In which year did WWII officially conclude?', 10.0, 'numerical', 2)
        """, (exam_id,)).lastrowid
        conn.execute("UPDATE questions SET correct_answer_text = '1945' WHERE question_id = ?", (q2,))

        st = conn.execute("INSERT INTO students (name, usn, subject, exam_code) VALUES ('George Orwell', 'HIST001', 'History', 'HIST')").lastrowid
        att = conn.execute("INSERT INTO exam_attempts (student_id, exam_id, status, started_at) VALUES (?, ?, 'in_progress', '2026-03-01 10:00:00')", (st, exam_id)).lastrowid

    # Candidate submits answers
    with client.session_transaction() as sess:
        sess.clear()
        sess["student_id"] = st

    client.post(f"/api/attempt/{att}/answer", json={"question_id": q1, "answer_text": "Midway crippled Japanese aircraft carriers..."})
    client.post(f"/api/attempt/{att}/answer", json={"question_id": q2, "answer_text": "1945"})
    client.post(f"/attempt/{att}/submit")

    # Initial state: objective is 10/10, subjective is unevaluated -> total 10/20 (50%), pending_evaluation, unpublished
    att_data = db.get_attempt(att)
    assert att_data["result"]["evaluation_status"] == "pending_evaluation"
    assert att_data["result"]["is_published"] == 0

    # 1. Teacher logs in and views evaluations list
    with client.session_transaction() as sess:
        sess.clear()
    _login(client, "teacher@example.test", "secure-teacher-password")

    resp_list = client.get("/admin/evaluations", headers={"Accept": "application/json"})
    assert resp_list.status_code == 200
    assert any(e["attempt_id"] == att for e in resp_list.get_json()["evaluations"])

    # 2. Teacher opens evaluation interface for this attempt
    resp_eval_view = client.get(f"/admin/evaluations/attempt/{att}")
    assert resp_eval_view.status_code == 200
    assert b"George Orwell" in resp_eval_view.data
    assert b"Midway crippled Japanese aircraft carriers" in resp_eval_view.data

    # 3. Teacher saves draft evaluation (assigns 8.5/10 marks and feedback, publish=0)
    csrf_tok = _csrf(client)
    resp_draft = client.post(
        f"/admin/evaluations/attempt/{att}/save",
        json={
            "evaluations": [{"question_id": q1, "marks_awarded": 8.5, "feedback": "Well articulated thesis."}],
            "publish": False,
            "csrf_token": csrf_tok,
        },
    )
    assert resp_draft.status_code == 200
    assert resp_draft.get_json()["success"] is True

    # Re-check attempt: total marks = 10 (objective) + 8.5 (subjective) = 18.5 / 20 = 92.5%, status 'evaluated', but NOT published
    updated = db.get_attempt(att)
    assert updated["result"]["marks_obtained"] == 18.5
    assert updated["result"]["percentage"] == 92.5
    assert updated["result"]["is_published"] == 0
    assert updated["result"]["evaluation_status"] == "evaluated"

    # Student still cannot see score while unpublished
    with client.session_transaction() as sess:
        sess.clear()
        sess["student_id"] = st
    resp_st_check = client.get(f"/attempt/{att}/result")
    assert b"Evaluation In Progress" in resp_st_check.data

    # 4. Teacher publishes final result
    with client.session_transaction() as sess:
        sess.clear()
    _login(client, "teacher@example.test", "secure-teacher-password")
    csrf_tok = _csrf(client)
    resp_pub = client.post(
        f"/admin/evaluations/attempt/{att}/publish",
        json={"csrf_token": csrf_tok},
    )
    assert resp_pub.status_code == 200
    assert resp_pub.get_json()["success"] is True

    # Check database: published = 1
    final_att = db.get_attempt(att)
    assert final_att["result"]["is_published"] == 1
    assert final_att["result"]["evaluation_status"] == "published"

    # 5. Student can now see verified final score, letter grade, and instructor feedback
    with client.session_transaction() as sess:
        sess.clear()
        sess["student_id"] = st
    resp_st_final = client.get(f"/attempt/{att}/result")
    assert resp_st_final.status_code == 200
    assert b"18.5" in resp_st_final.data
    assert b"Well articulated thesis" in resp_st_final.data


def test_teacher_and_student_notifications(admin_context):
    client = admin_context["client"]
    db = admin_context["database"]

    # 1. Create teacher notification for pending evaluation & new submission
    db.create_notification(
        recipient_type="teacher",
        recipient_id=admin_context["teacher_id"],
        title="New Examination Submission",
        message="Candidate John Doe submitted attempt.",
        type="new_submission",
    )
    db.create_notification(
        recipient_type="teacher",
        recipient_id=admin_context["teacher_id"],
        title="Pending Evaluation Required",
        message="Subjective answers need grading.",
        type="pending_evaluation",
    )

    # Teacher accesses notifications
    _login(client, "teacher@example.test", "secure-teacher-password")
    resp_notifs = client.get("/admin/notifications")
    assert resp_notifs.status_code == 200
    assert b"New Examination Submission" in resp_notifs.data

    # API endpoint returns notifications and unread count
    resp_api = client.get("/admin/api/notifications")
    assert resp_api.status_code == 200
    data = resp_api.get_json()
    assert data["unread_count"] >= 2
    notif_id = data["notifications"][0]["notification_id"]

    # Mark single notification as read
    csrf_tok = _csrf(client)
    resp_read = client.post(f"/admin/api/notifications/{notif_id}/read", json={"csrf_token": csrf_tok})
    assert resp_read.status_code == 200
    assert resp_read.get_json()["success"] is True

    # Mark all notifications as read
    resp_all_read = client.post("/admin/api/notifications/read-all", json={"csrf_token": csrf_tok})
    assert resp_all_read.status_code == 200
    assert resp_all_read.get_json()["success"] is True
    assert db.get_unread_notification_count("teacher", admin_context["teacher_id"]) == 0

    # 2. Student notifications
    with db.get_connection() as conn:
        st_id = conn.execute("INSERT INTO students (name, usn, subject, exam_code) VALUES ('Jane Student', 'USN999', 'Math', 'MTH')").lastrowid

    # Create notifications for student: exam published, schedule changed, results published
    db.create_notification(recipient_type="student", recipient_id=st_id, title="New Examination Published", message="Midterm 1 is available.", type="new_exam_published")
    db.create_notification(recipient_type="student", recipient_id=st_id, title="Examination Schedule Changed", message="Exam starts at 10 AM.", type="schedule_changed")
    db.create_notification(recipient_type="student", recipient_id=st_id, title="Examination Results Published", message="Your score is 95%.", type="results_published")

    # Student logs in
    with client.session_transaction() as sess:
        sess.clear()
        sess["student_id"] = st_id

    resp_st_page = client.get("/student/notifications")
    assert resp_st_page.status_code == 200
    assert b"New Examination Published" in resp_st_page.data
    assert b"Examination Schedule Changed" in resp_st_page.data
    assert b"Examination Results Published" in resp_st_page.data

    # Student API test
    resp_st_api = client.get("/api/student/notifications")
    assert resp_st_api.status_code == 200
    st_notifs = resp_st_api.get_json()["notifications"]
    assert len(st_notifs) == 3

    # Student mark all read
    resp_st_all_read = client.post("/api/student/notifications/read-all")
    assert resp_st_all_read.status_code == 200
    assert db.get_unread_notification_count("student", st_id) == 0


def test_audit_logs_tracking(admin_context):
    client = admin_context["client"]
    db = admin_context["database"]

    # 1. Direct logging of actions to verify all required audit action types
    actions_to_test = [
        ("examination_creation", "exam", 101, "Created final examination"),
        ("examination_modification", "exam", 101, "Modified exam schedule and duration"),
        ("examination_cancellation", "exam", 101, "Cancelled exam due to weather"),
        ("question_changes", "question", 501, "Edited statement for question #501"),
        ("marks_modification", "attempt", 201, "Adjusted marks awarded for subjective essay"),
        ("result_publication", "result", 201, "Published final evaluation results"),
        ("teacher_account_changes", "teacher", 15, "Created new teacher account with permissions"),
    ]

    for action, etype, eid, detail in actions_to_test:
        db.log_audit(
            action=action,
            entity_type=etype,
            entity_id=eid,
            details=detail,
            admin_user_id=admin_context["super_admin_id"],
            user_name="Super Admin",
            user_role="Super Admin",
            exam_id=101 if etype == "exam" else 0,
        )

    # 2. Super Admin views audit logs via UI and API
    _login(client, "superadmin@example.test", "secure-admin-password")
    resp_logs_page = client.get("/admin/audit-logs")
    assert resp_logs_page.status_code == 200
    assert b"Administrative Audit Logs" in resp_logs_page.data

    # JSON API
    resp_api = client.get("/admin/api/audit-logs")
    assert resp_api.status_code == 200
    logs = resp_api.get_json()["logs"]
    actions_found = {l["action"] for l in logs}
    for action, _, _, _ in actions_to_test:
        assert action in actions_found

    # Verify log entry structure
    sample_log = next(l for l in logs if l["action"] == "examination_cancellation")
    assert sample_log["user_name"] == "Super Admin"
    assert sample_log["user_role"] == "Super Admin"
    assert sample_log["entity_type"] == "exam"
    assert sample_log["entity_id"] == 101
    assert "timestamp" in sample_log

    # 3. Filter audit logs by action
    resp_filter_action = client.get("/admin/api/audit-logs?action=marks_modification")
    assert resp_filter_action.status_code == 200
    filtered_logs = resp_filter_action.get_json()["logs"]
    assert len(filtered_logs) >= 1
    assert all(l["action"] == "marks_modification" for l in filtered_logs)

    # 4. Filter audit logs by entity_type
    resp_filter_entity = client.get("/admin/api/audit-logs?entity_type=teacher")
    assert resp_filter_entity.status_code == 200
    assert all(l["entity_type"] == "teacher" for l in resp_filter_entity.get_json()["logs"])

    # 5. Export audit logs to CSV
    resp_csv = client.get("/admin/audit-logs/export/csv")
    assert resp_csv.status_code == 200
    assert resp_csv.mimetype == "text/csv"
    assert b"Log ID,Timestamp,Action,User Name,User Role" in resp_csv.data
    assert b"examination_cancellation" in resp_csv.data

    # 6. Teacher permission scoping: Teacher only sees logs for their assigned exams or their own actions
    with client.session_transaction() as sess:
        sess.clear()
    _login(client, "teacher@example.test", "secure-teacher-password")
    resp_teacher_logs = client.get("/admin/api/audit-logs")
    assert resp_teacher_logs.status_code == 200
    teacher_logs = resp_teacher_logs.get_json()["logs"]
    # Teacher does not see unassigned exam #101 logs
    assert not any(l.get("exam_id") == 101 for l in teacher_logs)


def test_student_reports_undefined_error_regression_and_weighted_average(admin_context):
    client = admin_context["client"]
    db = admin_context["database"]
    _login(client, "superadmin@example.test", "secure-admin-password")

    with db.get_connection() as conn:
        # Create unique subjects
        s_phys = conn.execute("INSERT INTO subjects (name) VALUES ('Applied Physics Regression')").lastrowid
        s_chem = conn.execute("INSERT INTO subjects (name) VALUES ('Organic Chemistry Regression')").lastrowid

        b_phys = conn.execute("INSERT INTO question_banks (subject_id, name) VALUES (?, 'Physics Bank')", (s_phys,)).lastrowid
        b_chem = conn.execute("INSERT INTO question_banks (subject_id, name) VALUES (?, 'Chemistry Bank')", (s_chem,)).lastrowid

        # Exam 1: Physics (max_marks: 50.0, pass_marks: 20.0)
        ex_phys = conn.execute("""
            INSERT INTO exams (bank_id, name, duration_minutes, max_marks, pass_marks, department, semester, academic_year, status)
            VALUES (?, 'Physics Midterm', 45, 50.0, 20.0, 'Science', 'Semester 1', '2026', 'published')
        """, (b_phys,)).lastrowid

        # Exam 2: Chemistry (max_marks: 100.0, pass_marks: 40.0)
        ex_chem = conn.execute("""
            INSERT INTO exams (bank_id, name, duration_minutes, max_marks, pass_marks, department, semester, academic_year, status)
            VALUES (?, 'Chemistry Final', 90, 100.0, 40.0, 'Science', 'Semester 1', '2026', 'published')
        """, (b_chem,)).lastrowid

        # Students
        st_alpha = conn.execute("""
            INSERT INTO students (name, usn, email, department, semester, account_status, subject, exam_code)
            VALUES ('Student Alpha', 'USN-ALPHA-01', 'alpha@example.test', 'Science', 'Semester 1', 'Active', 'Applied Physics Regression', 'PHYS01')
        """).lastrowid

        st_beta = conn.execute("""
            INSERT INTO students (name, usn, email, department, semester, account_status, subject, exam_code)
            VALUES ('Student Beta', 'USN-BETA-02', 'beta@example.test', 'Science', 'Semester 1', 'Active', 'Applied Physics Regression', 'PHYS01')
        """).lastrowid

        # Student Alpha submissions:
        # Physics: 40 / 50 (80%)
        att_a1 = conn.execute("INSERT INTO exam_attempts (student_id, exam_id, status, started_at, submitted_at) VALUES (?, ?, 'submitted', '2026-04-01 10:00:00', '2026-04-01 10:40:00')", (st_alpha, ex_phys)).lastrowid
        conn.execute("INSERT INTO exam_results (attempt_id, marks_obtained, percentage, correct_answers, incorrect_answers, unanswered, time_taken_seconds) VALUES (?, 40.0, 80.0, 8, 2, 0, 2400)", (att_a1,))

        # Chemistry: 70 / 100 (70%)
        att_a2 = conn.execute("INSERT INTO exam_attempts (student_id, exam_id, status, started_at, submitted_at) VALUES (?, ?, 'submitted', '2026-04-02 10:00:00', '2026-04-02 11:20:00')", (st_alpha, ex_chem)).lastrowid
        conn.execute("INSERT INTO exam_results (attempt_id, marks_obtained, percentage, correct_answers, incorrect_answers, unanswered, time_taken_seconds) VALUES (?, 70.0, 70.0, 14, 6, 0, 4800)", (att_a2,))

        # Student Beta submissions:
        # Physics: 15 / 50 (30%, Failed)
        att_b1 = conn.execute("INSERT INTO exam_attempts (student_id, exam_id, status, started_at, submitted_at) VALUES (?, ?, 'submitted', '2026-04-01 10:00:00', '2026-04-01 10:45:00')", (st_beta, ex_phys)).lastrowid
        conn.execute("INSERT INTO exam_results (attempt_id, marks_obtained, percentage, correct_answers, incorrect_answers, unanswered, time_taken_seconds) VALUES (?, 15.0, 30.0, 3, 7, 0, 2700)", (att_b1,))

        # Chemistry: 30 / 100 (30%, Failed)
        att_b2 = conn.execute("INSERT INTO exam_attempts (student_id, exam_id, status, started_at, submitted_at) VALUES (?, ?, 'submitted', '2026-04-02 10:00:00', '2026-04-02 11:30:00')", (st_beta, ex_chem)).lastrowid
        conn.execute("INSERT INTO exam_results (attempt_id, marks_obtained, percentage, correct_answers, incorrect_answers, unanswered, time_taken_seconds) VALUES (?, 30.0, 30.0, 6, 14, 0, 5400)", (att_b2,))

    # 1. Verify JSON Analytics & Weighted Averages
    resp_json = client.get("/admin/reports?department=Science&format=json")
    assert resp_json.status_code == 200
    data = resp_json.get_json()["analytics"]

    # Student Alpha weighted average:
    # Total marks = 40 + 70 = 110.0, Total max = 50 + 100 = 150.0
    # Average percentage = (110.0 / 150.0) * 100 = 73.3%
    rankings = data["student_rankings"]
    alpha_rank = next(s for s in rankings if s["student_id"] == st_alpha)
    assert alpha_rank["total_marks"] == 110.0
    assert alpha_rank["total_max_marks"] == 150.0
    assert alpha_rank["average_percentage"] == 73.3
    assert alpha_rank["avg_percentage"] == 73.3
    assert alpha_rank["average_marks"] == 55.0  # (110 / 2 attempts)
    assert alpha_rank["status"] == "Pass"
    assert alpha_rank["pass_fail_status"] == "Pass"
    assert alpha_rank["overall_performance"] == "Good"

    # Student Beta weighted average:
    # Total marks = 15 + 30 = 45.0, Total max = 50 + 100 = 150.0
    # Average percentage = (45.0 / 150.0) * 100 = 30.0%
    beta_rank = next(s for s in rankings if s["student_id"] == st_beta)
    assert beta_rank["total_marks"] == 45.0
    assert beta_rank["total_max_marks"] == 150.0
    assert beta_rank["average_percentage"] == 30.0
    assert beta_rank["avg_percentage"] == 30.0
    assert beta_rank["status"] == "Fail"
    assert beta_rank["pass_fail_status"] == "Fail"
    assert beta_rank["overall_performance"] == "Needs Improvement"

    # Subject performance:
    # Physics: Total marks = 40 + 15 = 55.0, Total max = 50 + 50 = 100.0 -> 55.0%
    # Chemistry: Total marks = 70 + 30 = 100.0, Total max = 100 + 100 = 200.0 -> 50.0%
    sub_perf = data["subject_performance"]
    phys_perf = next(s for s in sub_perf if s["subject_name"] == "Applied Physics Regression")
    chem_perf = next(s for s in sub_perf if s["subject_name"] == "Organic Chemistry Regression")
    assert phys_perf["average_percentage"] == 55.0
    assert chem_perf["average_percentage"] == 50.0
    assert "average_percentage" in phys_perf and "avg_percentage" in phys_perf
    assert "average_marks" in phys_perf and "avg_marks" in phys_perf
    assert "pass_rate" in phys_perf and "pass_percentage" in phys_perf

    # 2. CRITICAL TEST: Render HTML template with populated attempts and subject performance
    # This previously failed with: jinja2.exceptions.UndefinedError: 'dict object' has no attribute 'average_percentage'
    resp_html = client.get("/admin/reports?department=Science")
    assert resp_html.status_code == 200
    assert b"Applied Physics Regression" in resp_html.data
    assert b"Organic Chemistry Regression" in resp_html.data
    assert b"Student Alpha" in resp_html.data
    assert b"Student Beta" in resp_html.data
    assert b"73.3%" in resp_html.data
    assert b"30.0%" in resp_html.data

    # 3. Test filter parameters on HTML view
    resp_filter_subj = client.get(f"/admin/reports?subject_id={s_phys}")
    assert resp_filter_subj.status_code == 200
    assert b"Applied Physics Regression" in resp_filter_subj.data

    resp_filter_student = client.get(f"/admin/reports?student_id={st_alpha}")
    assert resp_filter_student.status_code == 200
    assert b"Student Alpha" in resp_filter_student.data

    # 4. Test Export Endpoints with populated data
    for fmt in ["pdf", "xlsx", "csv"]:
        resp_export = client.get(f"/admin/reports/export/{fmt}?department=Science")
        assert resp_export.status_code == 200
        assert len(resp_export.data) > 0







