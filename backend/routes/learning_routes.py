from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from backend.database.db_manager import DatabaseManager


learning_bp = Blueprint("learning", __name__)
db_manager = DatabaseManager()


def _student_id():
    return session.get("student_id")


@learning_bp.route("/subjects")
def subjects():
    db_manager.init_db()
    return render_template("subjects.html", page_title="Subjects", subjects=db_manager.list_subjects())


@learning_bp.route("/subject/<int:subject_id>")
def subject_detail(subject_id: int):
    db_manager.init_db()
    subject = db_manager.get_subject(subject_id)
    if subject is None:
        return "Subject not found", 404
    return render_template(
        "exams.html",
        page_title=subject["name"],
        subject=subject,
        exams=db_manager.list_exams(subject_id),
    )


@learning_bp.route("/exam/<int:exam_id>/instructions")
def exam_instructions(exam_id: int):
    db_manager.init_db()
    exam = db_manager.get_exam(exam_id)
    if exam is None:
        return "Examination not found", 404
    return render_template("instructions.html", page_title="Exam instructions", exam=exam)


@learning_bp.route("/exam/<int:exam_id>/begin", methods=["POST"])
def begin_exam(exam_id: int):
    student_id = _student_id()
    if not student_id:
        return redirect(url_for("auth.login", next=url_for("learning.exam_instructions", exam_id=exam_id)))
    db_manager.init_db()
    if db_manager.get_exam(exam_id) is None:
        return "Examination not found", 404
    attempt_id = db_manager.create_attempt(int(student_id), exam_id, request.form.get("mode", "practice"))
    return redirect(url_for("learning.take_exam", attempt_id=attempt_id))


@learning_bp.route("/attempt/<int:attempt_id>")
def take_exam(attempt_id: int):
    if not _student_id():
        return redirect(url_for("auth.login"))
    db_manager.init_db()
    attempt = db_manager.get_attempt(attempt_id)
    if attempt is None:
        return "Attempt not found", 404
    exam = db_manager.get_exam(attempt["exam_id"])
    return render_template("take_exam.html", page_title=attempt["exam_name"], attempt=attempt, exam=exam)


@learning_bp.route("/api/attempt/<int:attempt_id>/answer", methods=["POST"])
def save_answer(attempt_id: int):
    if not _student_id():
        return jsonify({"error": "Login required."}), 401
    payload = request.get_json(silent=True) or {}
    question_id = payload.get("question_id")
    option_id = payload.get("option_id")
    if not isinstance(question_id, int) or (option_id is not None and not isinstance(option_id, int)):
        return jsonify({"error": "A valid question and option are required."}), 400
    attempt = db_manager.get_attempt(attempt_id)
    if attempt is None or attempt["student_id"] != int(_student_id()) or attempt["status"] != "in_progress":
        return jsonify({"error": "Attempt is unavailable."}), 409
    db_manager.save_answer(attempt_id, question_id, option_id)
    return jsonify({"saved": True})


@learning_bp.route("/attempt/<int:attempt_id>/submit", methods=["POST"])
def submit_exam(attempt_id: int):
    if not _student_id():
        return redirect(url_for("auth.login"))
    attempt = db_manager.get_attempt(attempt_id)
    if attempt is None or attempt["student_id"] != int(_student_id()):
        return "Attempt not found", 404
    db_manager.submit_attempt(attempt_id)
    return redirect(url_for("learning.result", attempt_id=attempt_id))


@learning_bp.route("/attempt/<int:attempt_id>/result")
def result(attempt_id: int):
    if not _student_id():
        return redirect(url_for("auth.login"))
    attempt = db_manager.get_attempt(attempt_id)
    if attempt is None or attempt["student_id"] != int(_student_id()):
        return "Attempt not found", 404
    return render_template("result.html", page_title="Exam result", attempt=attempt)
