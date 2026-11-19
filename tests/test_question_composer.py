import io
import json
import pytest
from werkzeug.security import generate_password_hash
import docx

import app as app_module
from app import app
from backend.database.db_manager import DatabaseManager
from backend.routes import admin_routes
from backend.routes import auth_routes
from backend.routes import learning_routes


@pytest.fixture
def composer_context(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / "composer-test.db")
    database.init_db()
    monkeypatch.setattr(admin_routes, "db_manager", database)
    monkeypatch.setattr(auth_routes, "db_manager", database)
    monkeypatch.setattr(learning_routes, "db_manager", database)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "composer-test-secret"
    app.secret_key = app.config["SECRET_KEY"]

    with database.get_connection() as connection:
        teacher_id = connection.execute(
            """
            INSERT INTO admin_users (email, name, password_hash, role)
            VALUES (?, ?, ?, 'Teacher')
            """,
            ("composer_teacher@example.test", "Composer Teacher", generate_password_hash("teacher123")),
        ).lastrowid

        super_id = connection.execute(
            """
            INSERT INTO admin_users (email, name, password_hash, role)
            VALUES (?, ?, ?, 'Super Admin')
            """,
            ("composer_super@example.test", "Composer Super", generate_password_hash("super123")),
        ).lastrowid

    return {
        "client": app.test_client(),
        "database": database,
        "teacher_id": teacher_id,
        "super_id": super_id,
    }


def _csrf(client):
    with client.session_transaction() as session:
        return session.get("admin_csrf_token", "")


def _login_teacher(client):
    client.get("/admin/login")
    return client.post(
        "/admin/login",
        data={
            "email": "composer_teacher@example.test",
            "password": "teacher123",
            "csrf_token": _csrf(client),
        },
        follow_redirects=True,
    )


def test_composer_page_requires_authentication(composer_context):
    client = composer_context["client"]
    response = client.get("/admin/question-composer")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")


def test_composer_page_renders_for_logged_in_teacher(composer_context):
    client = composer_context["client"]
    _login_teacher(client)

    response = client.get("/admin/question-composer")
    assert response.status_code == 200
    html = response.data.decode("utf-8")
    assert "Smart Question Composer" in html
    assert "Write Questions (Editor)" in html
    assert "Bulk Text Entry" in html
    assert "Import Question Paper" in html
    assert "Extract from Study Material" in html
    assert "composer-container" in html


def test_parse_bulk_text_endpoint(composer_context):
    client = composer_context["client"]
    _login_teacher(client)
    csrf_token = _csrf(client)

    raw_text = """
1. Which data structure operates strictly on a First-In-First-Out (FIFO) basis?
A) Stack
B) Queue
C) Binary Tree
D) Graph
Answer: B
Explanation: In a Queue, the element inserted first is processed first.
Marks: 2
Difficulty: Easy
Topic: Data Structures

2. Python is an interpreted programming language.
A) True
B) False
Answer: True
Marks: 1
Difficulty: Easy

3. Explain the difference between primary key and unique key in SQL.
Marks: 5
Difficulty: Medium
Topic: Databases
"""

    response = client.post(
        "/admin/api/composer/parse-text",
        json={"text": raw_text},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["count"] == 3
    questions = data["questions"]

    # Check question 1 (MCQ)
    q1 = questions[0]
    assert q1["type"] == "mcq"
    assert len(q1["options"]) == 4
    assert q1["correct_answer"] in ["B", "Queue"]
    assert q1["marks"] == 2
    assert q1["difficulty"].lower() == "easy"
    assert q1["topic"] == "Data Structures"
    assert "element inserted first" in q1["explanation"]

    # Check question 2 (True/False)
    q2 = questions[1]
    assert q2["type"] == "true_false"
    assert q2["correct_answer"] in ["True", "A"]
    assert q2["marks"] == 1

    # Check question 3 (Subjective / Short answer)
    q3 = questions[2]
    assert q3["type"] in ["short_answer", "descriptive", "long_answer"]
    assert q3["marks"] == 5
    assert q3["topic"] == "Databases"


def test_parse_bulk_text_with_section_headers(composer_context):
    client = composer_context["client"]
    _login_teacher(client)
    csrf_token = _csrf(client)

    raw_text = """
Section A: Core Theoretical Foundations

1. What is the time complexity of searching an element in a balanced BST?
A) O(1)
B) O(log n)
C) O(n)
D) O(n^2)
Answer: B
Marks: 2

Section B: Applied Programming Problems

2. What does ACID stand for in transaction processing?
Answer: Atomicity, Consistency, Isolation, Durability
Marks: 4
"""

    response = client.post(
        "/admin/api/composer/parse-text",
        json={"text": raw_text},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    questions = data["questions"]
    assert len(questions) == 2
    assert "Section A" in questions[0]["section_name"]
    assert "Section B" in questions[1]["section_name"]


def test_upload_txt_document(composer_context):
    client = composer_context["client"]
    _login_teacher(client)
    csrf_token = _csrf(client)

    file_content = b"""
1. What is the derivative of sin(x)?
A) cos(x)
B) -cos(x)
C) tan(x)
D) -sin(x)
Answer: A
Marks: 2
Difficulty: Easy
"""

    data = {
        "file": (io.BytesIO(file_content), "calculus_test.txt"),
    }

    response = client.post(
        "/admin/api/composer/upload-document",
        data=data,
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data["success"] is True
    assert res_data["count"] == 1
    assert "derivative of sin(x)" in res_data["questions"][0]["statement"]


def test_upload_docx_document(composer_context):
    client = composer_context["client"]
    _login_teacher(client)
    csrf_token = _csrf(client)

    doc = docx.Document()
    doc.add_heading("Mid-Term Assessment", level=1)
    doc.add_paragraph("Section A")
    doc.add_paragraph("1. In networking, which protocol is connection-oriented?")
    doc.add_paragraph("A) UDP")
    doc.add_paragraph("B) TCP")
    doc.add_paragraph("C) ICMP")
    doc.add_paragraph("D) IP")
    doc.add_paragraph("Answer: B")
    doc.add_paragraph("Marks: 2")

    bio = io.BytesIO()
    doc.save(bio)
    bio.seek(0)

    data = {
        "file": (bio, "networking.docx"),
    }

    response = client.post(
        "/admin/api/composer/upload-document",
        data=data,
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data["success"] is True
    assert res_data["count"] >= 1
    assert any("TCP" in str(q.get("options")) or "TCP" in q.get("correct_answer", "") for q in res_data["questions"])


def test_upload_document_invalid_extension(composer_context):
    client = composer_context["client"]
    _login_teacher(client)
    csrf_token = _csrf(client)

    data = {
        "file": (io.BytesIO(b"binary data"), "virus.exe"),
    }

    response = client.post(
        "/admin/api/composer/upload-document",
        data=data,
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 400
    res_data = response.get_json()
    assert res_data["success"] is False
    assert "Unsupported file format" in res_data["error"]


def test_generate_from_study_material_offline(composer_context):
    client = composer_context["client"]
    _login_teacher(client)
    csrf_token = _csrf(client)

    material = """
A Binary Search Tree is a node-based binary tree data structure where each node has a key.
The left subtree of a node contains only nodes with keys lesser than the node's key.
The right subtree of a node contains only nodes with keys greater than the node's key.

Recursion is a programming method where the solution to a problem depends on solutions to smaller instances of the same problem.
A recursive function always requires a base case to terminate execution and avoid stack overflow errors.
"""

    response = client.post(
        "/admin/api/composer/generate-from-material",
        json={
            "text": material,
            "target_count": 3,
            "difficulty": "medium",
            "question_type": "all",
            "subject": "Computer Science",
        },
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["count"] > 0
    questions = data["questions"]

    for q in questions:
        assert ("statement" in q or "question_text" in q)
        assert ("question_type" in q or "type" in q)
        assert "marks" in q and q["marks"] > 0
        assert "source_reference" in q and len(q["source_reference"]) > 0


def test_composer_draft_lifecycle(composer_context):
    client = composer_context["client"]
    _login_teacher(client)
    csrf_token = _csrf(client)

    sample_questions = [
        {
            "statement": "What is 2 + 2?",
            "question_text": "What is 2 + 2?",
            "type": "mcq",
            "marks": 2,
            "difficulty": "easy",
            "options": ["1", "2", "3", "4"],
            "correct_answer": "4",
            "explanation": "Basic arithmetic",
            "section_name": "Section A",
        }
    ]
    sample_sections = [
        {"name": "Section A", "description": "Quick arithmetic", "marks": 2}
    ]

    # 1. Save new draft
    save_res = client.post(
        "/admin/api/composer/draft/save",
        json={
            "title": "Math Quiz Draft 1",
            "subject_id": 1,
            "sections": sample_sections,
            "questions": sample_questions,
            "metadata": {"tags": ["math", "intro"]},
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert save_res.status_code == 200
    save_data = save_res.get_json()
    assert save_data["success"] is True
    draft_id = save_data["draft_id"]
    assert draft_id > 0

    # 2. List drafts
    list_res = client.get("/admin/api/composer/drafts")
    assert list_res.status_code == 200
    list_data = list_res.get_json()
    assert list_data["success"] is True
    assert any(d["draft_id"] == draft_id for d in list_data["drafts"])

    # 3. Get single draft
    get_res = client.get(f"/admin/api/composer/draft/{draft_id}")
    assert get_res.status_code == 200
    get_data = get_res.get_json()
    assert get_data["success"] is True
    assert get_data["draft"]["title"] == "Math Quiz Draft 1"
    assert len(get_data["draft"]["questions"]) == 1
    assert get_data["draft"]["questions"][0]["statement"] == "What is 2 + 2?"

    # 4. Update draft
    update_res = client.post(
        "/admin/api/composer/draft/save",
        json={
            "draft_id": draft_id,
            "title": "Math Quiz Draft Updated",
            "subject_id": 1,
            "sections": sample_sections,
            "questions": sample_questions,
            "metadata": {"tags": ["math", "updated"]},
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert update_res.status_code == 200
    get_updated = client.get(f"/admin/api/composer/draft/{draft_id}").get_json()
    assert get_updated["draft"]["title"] == "Math Quiz Draft Updated"

    # 5. Delete draft
    del_res = client.delete(
        f"/admin/api/composer/draft/{draft_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert del_res.status_code == 200
    assert del_res.get_json()["success"] is True

    # Confirm it's gone
    get_after_del = client.get(f"/admin/api/composer/draft/{draft_id}")
    assert get_after_del.status_code == 404


def test_composer_export_to_question_bank(composer_context):
    client = composer_context["client"]
    _login_teacher(client)
    csrf_token = _csrf(client)

    questions_to_export = [
        {
            "statement": "What is the capital of France?",
            "question_type": "mcq",
            "marks": 2,
            "difficulty": "Easy",
            "topic": "Geography",
            "options": [
                {"option_text": "London", "is_correct": False},
                {"option_text": "Berlin", "is_correct": False},
                {"option_text": "Paris", "is_correct": True},
                {"option_text": "Madrid", "is_correct": False},
            ],
            "correct_answer_text": "Paris",
            "explanation": "Paris is the capital of France.",
            "tags": "geography,capitals",
        },
        {
            "statement": "Define polymorphism in Object-Oriented Programming.",
            "question_type": "short_answer",
            "marks": 3,
            "difficulty": "Medium",
            "topic": "OOP",
            "options": [],
            "correct_answer_text": "The ability of an object to take on many forms.",
            "explanation": "Polymorphism allows methods to perform different tasks based on the object.",
            "tags": "oop,polymorphism",
        },
    ]

    response = client.post(
        "/admin/api/composer/export-to-bank",
        json={
            "subject_id": 1,
            "questions": questions_to_export,
        },
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["saved_count"] == 2

    # Verify questions are queryable in the DB
    with composer_context["database"].get_connection() as conn:
        saved_q = conn.execute(
            "SELECT * FROM bank_questions WHERE statement LIKE '%capital of France%'"
        ).fetchone()
        assert saved_q is not None
        assert saved_q["topic"] == "Geography"
        assert saved_q["marks"] == 2

        # Check options for the question
        opts = conn.execute(
            "SELECT * FROM bank_question_options WHERE bank_question_id = ? ORDER BY option_id",
            (saved_q["bank_question_id"],),
        ).fetchall()
        assert len(opts) == 4
        assert any(opt["option_text"] == "Paris" and opt["is_correct"] == 1 for opt in opts)


def test_composer_create_examination(composer_context):
    client = composer_context["client"]
    _login_teacher(client)
    csrf_token = _csrf(client)

    exam_payload = {
        "title": "Semester Examination: Algorithms 2026",
        "subject": "Computer Science",
        "department": "Engineering",
        "semester": 4,
        "academic_year": "2025-2026",
        "description": "Comprehensive evaluation of algorithmic complexity and design.",
        "duration_minutes": 90,
        "passing_marks": 40,
        "negative_marking": 0.25,
        "randomize_questions": True,
        "randomize_options": True,
        "allow_multiple_attempts": False,
        "instructions": "All questions are mandatory. KaTeX formulas are supported.",
        "sections": [
            {"name": "Section A", "description": "Multiple Choice Questions", "marks": 4},
            {"name": "Section B", "description": "Subjective & Analysis", "marks": 6},
        ],
        "questions": [
            {
                "statement": "What is the worst-case complexity of QuickSort?",
                "question_type": "mcq",
                "marks": 2,
                "difficulty": "Medium",
                "topic": "Sorting",
                "section_name": "Section A",
                "options": [
                    {"option_text": "O(n)", "is_correct": False},
                    {"option_text": "O(n log n)", "is_correct": False},
                    {"option_text": "O(n^2)", "is_correct": True},
                    {"option_text": "O(log n)", "is_correct": False},
                ],
                "correct_answer_text": "O(n^2)",
                "explanation": "When the pivot chosen is consistently the smallest or largest element.",
            },
            {
                "statement": "Is Dijkstra's algorithm greedy?",
                "question_type": "true_false",
                "marks": 2,
                "difficulty": "Easy",
                "topic": "Graphs",
                "section_name": "Section A",
                "options": [
                    {"option_text": "True", "is_correct": True},
                    {"option_text": "False", "is_correct": False},
                ],
                "correct_answer_text": "True",
                "explanation": "Dijkstra's picks the minimum distance vertex greedily at each step.",
            },
            {
                "statement": "Explain the Master Theorem for divide-and-conquer recurrences.",
                "question_type": "long_answer",
                "marks": 6,
                "difficulty": "Hard",
                "topic": "Recurrences",
                "section_name": "Section B",
                "options": [],
                "correct_answer_text": "",
                "explanation": "T(n) = aT(n/b) + f(n)",
            },
        ],
    }

    response = client.post(
        "/admin/api/composer/create-exam",
        json=exam_payload,
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data["success"] is True
    exam_id = res_data["exam_id"]
    assert exam_id > 0

    # Query DB to verify exam record
    with composer_context["database"].get_connection() as conn:
        exam = conn.execute("SELECT * FROM exams WHERE exam_id = ?", (exam_id,)).fetchone()
        assert exam is not None
        assert exam["name"] == "Semester Examination: Algorithms 2026"
        assert exam["duration_minutes"] == 90
        assert exam["max_marks"] == 10  # 2 + 2 + 6

        # Check sections stored
        sections = json.loads(exam["sections_json"])
        assert len(sections) == 2
        assert sections[0]["name"] == "Section A"

        # Check questions stored with section names and marks
        questions = conn.execute(
            "SELECT * FROM questions WHERE exam_id = ? ORDER BY question_id",
            (exam_id,),
        ).fetchall()
        assert len(questions) == 3
        assert questions[0]["section_name"] == "Section A"
        assert questions[0]["marks"] == 2
        assert questions[2]["section_name"] == "Section B"
        assert questions[2]["marks"] == 6


def test_composer_image_upload(composer_context, tmp_path, monkeypatch):
    client = composer_context["client"]
    _login_teacher(client)
    csrf_token = _csrf(client)

    # 1x1 transparent PNG
    dummy_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    data = {
        "image": (io.BytesIO(dummy_png), "diagram.png"),
    }

    response = client.post(
        "/admin/api/composer/upload-image",
        data=data,
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data["success"] is True
    img_url = res_data.get("url") or res_data.get("image_url", "")
    assert img_url.startswith("/static/uploads/composer_images/")


def test_upload_pdf_document(composer_context):
    client = composer_context["client"]
    _login_teacher(client)
    csrf_token = _csrf(client)

    content = "BT /F1 12 Tf 50 700 Td (1. What is the capital of France? A. Paris B. Lyon Answer: A Marks: 2) Tj ET"
    stream_len = len(content)
    pdf_raw = f"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length {stream_len} >> stream
{content}
endstream endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000244 00000 n 
0000000300 00000 n 
trailer << /Size 6 /Root 1 0 R >>
startxref
380
%%EOF"""

    data = {
        "file": (io.BytesIO(pdf_raw.encode("latin-1")), "sample_exam.pdf"),
    }

    response = client.post(
        "/admin/api/composer/upload-document",
        data=data,
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data["success"] is True
    assert res_data["count"] >= 1
    assert any("capital of France" in q["statement"] for q in res_data["questions"])


def test_upload_empty_document_error_reporting(composer_context):
    client = composer_context["client"]
    _login_teacher(client)
    csrf_token = _csrf(client)

    data = {
        "file": (io.BytesIO(b"   "), "empty.txt"),
    }

    response = client.post(
        "/admin/api/composer/upload-document",
        data=data,
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 400
    res_data = response.get_json()
    assert res_data["success"] is False
    assert "No readable text could be extracted" in res_data["error"]


def test_parse_bulk_text_with_multiple_select_and_parentheses_numbering(composer_context):
    client = composer_context["client"]
    _login_teacher(client)
    csrf_token = _csrf(client)

    text = """
(1) Which of the following are prime numbers?
(A) 2
(B) 3
(C) 4
(D) 5
Ans: A, B, D
Marks: 3

(2) State True or False: Earth is the third planet from the sun.
Ans: True
Marks: 1
"""

    response = client.post(
        "/admin/api/composer/parse-text",
        json={"text": text},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data["success"] is True
    assert res_data["count"] == 2
    q1 = res_data["questions"][0]
    assert q1["question_type"] == "multiple_select"
    assert sum(1 for o in q1["options"] if o["is_correct"]) == 3
    q2 = res_data["questions"][1]
    assert q2["question_type"] == "true_false"

