from datetime import datetime
from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

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


def _parse_iso(dt_val):
    if not dt_val:
        return None
    s = str(dt_val).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


@learning_bp.route("/api/student/exams/join", methods=["POST"])
def api_join_exam():
    student_id = _student_id()
    if not student_id:
        return jsonify({"status": "error", "message": "Authentication required. Please sign in to join an examination."}), 401

    payload = request.get_json(silent=True) or {}
    exam_code = (payload.get("exam_code") or request.form.get("exam_code") or "").strip()
    if not exam_code:
        return jsonify({"status": "error", "message": "Please enter an examination code."}), 400

    db_manager.init_db()
    is_valid, msg, exam = db_manager.validate_student_exam_access(int(student_id), exam_code=exam_code)
    if not is_valid:
        return jsonify({"status": "error", "message": msg}), 400

    return jsonify({
        "status": "success",
        "message": "Access granted.",
        "exam_id": exam["exam_id"],
        "exam_name": exam.get("name", "Examination"),
        "redirect_url": url_for("learning.exam_instructions", exam_id=exam["exam_id"]),
    })


@learning_bp.route("/exam/join", methods=["POST"])
def join_exam():
    student_id = _student_id()
    if not student_id:
        return redirect(url_for("auth.login"))

    exam_code = (request.form.get("exam_code") or "").strip()
    if not exam_code:
        flash("Please enter an examination code.", "error")
        return redirect(url_for("dashboard"))

    db_manager.init_db()
    is_valid, msg, exam = db_manager.validate_student_exam_access(int(student_id), exam_code=exam_code)
    if not is_valid:
        flash(msg, "error")
        return redirect(url_for("dashboard"))

    return redirect(url_for("learning.exam_instructions", exam_id=exam["exam_id"]))


@learning_bp.route("/api/student/exams/available")
def api_available_exams():
    student_id = _student_id()
    if not student_id:
        return jsonify({"status": "error", "message": "Authentication required."}), 401
    db_manager.init_db()
    exams = db_manager.list_available_exams_for_student(int(student_id))
    return jsonify({"status": "success", "exams": exams})


@learning_bp.route("/exam/<int:exam_id>/instructions")
def exam_instructions(exam_id: int):
    student_id = _student_id()
    if not student_id:
        return redirect(url_for("auth.login", next=url_for("learning.exam_instructions", exam_id=exam_id)))

    db_manager.init_db()
    is_valid, msg, exam = db_manager.validate_student_exam_access(int(student_id), exam_id=exam_id)
    if not is_valid:
        return render_template(
            "instructions.html",
            page_title="Access Denied",
            exam=exam or {"name": "Examination", "duration_minutes": 0, "max_marks": 0},
            access_denied_message=msg,
        ), 403

    active_attempt = db_manager.get_active_attempt(int(student_id), exam_id)
    return render_template(
        "instructions.html",
        page_title="Exam instructions",
        exam=exam,
        active_attempt=active_attempt,
    )


@learning_bp.route("/exam/<int:exam_id>/begin", methods=["POST"])
def begin_exam(exam_id: int):
    student_id = _student_id()
    if not student_id:
        return redirect(url_for("auth.login", next=url_for("learning.exam_instructions", exam_id=exam_id)))
    db_manager.init_db()

    is_valid, msg, exam = db_manager.validate_student_exam_access(int(student_id), exam_id=exam_id)
    if not is_valid:
        return msg, 403

    # If student already has an active in_progress attempt, resume it
    existing_attempt = db_manager.get_active_attempt(int(student_id), exam_id)
    if existing_attempt:
        return redirect(url_for("learning.take_exam", attempt_id=existing_attempt["attempt_id"]))

    attempt_id = db_manager.create_attempt(int(student_id), exam_id, request.form.get("mode", "practice"))
    return redirect(url_for("learning.take_exam", attempt_id=attempt_id))


@learning_bp.route("/attempt/<int:attempt_id>")
def take_exam(attempt_id: int):
    if not _student_id():
        return redirect(url_for("auth.login"))
    db_manager.init_db()
    attempt = db_manager.get_attempt(attempt_id)
    if attempt is None or attempt["student_id"] != int(_student_id()):
        return "Attempt not found", 404
    if attempt.get("status") == "submitted":
        return redirect(url_for("learning.result", attempt_id=attempt_id))
    exam = db_manager.get_exam(attempt["exam_id"])
    if not exam:
        return "Examination not found", 404
    exam["remaining_seconds"] = attempt.get("remaining_seconds")
    exam["questions"] = attempt.get("questions")
    return render_template("take_exam.html", page_title=attempt["exam_name"], attempt=attempt, exam=exam)


@learning_bp.route("/api/attempt/<int:attempt_id>/answer", methods=["POST"])
def save_answer(attempt_id: int):
    if not _student_id():
        return jsonify({"error": "Login required."}), 401
    payload = request.get_json(silent=True) or {}
    question_id = payload.get("question_id")
    option_id = payload.get("option_id")
    option_ids = payload.get("option_ids")
    answer_text = payload.get("answer_text")

    if not isinstance(question_id, int):
        return jsonify({"error": "A valid question is required."}), 400

    # Support multiple-select option IDs array
    if option_ids is not None and isinstance(option_ids, list):
        parsed_ids = []
        for x in option_ids:
            try:
                parsed_ids.append(int(x))
            except (ValueError, TypeError):
                pass
        answer_text = ",".join(str(x) for x in sorted(parsed_ids))
        if len(parsed_ids) == 1:
            option_id = parsed_ids[0]
        elif len(parsed_ids) == 0:
            option_id = None
        else:
            option_id = parsed_ids[0]

    if option_id is not None and not isinstance(option_id, int):
        try:
            option_id = int(option_id)
        except (ValueError, TypeError):
            return jsonify({"error": "A valid option is required."}), 400

    attempt = db_manager.get_attempt(attempt_id)
    if attempt is None or attempt["student_id"] != int(_student_id()) or attempt["status"] != "in_progress":
        return jsonify({"error": "Attempt is unavailable."}), 409

    db_manager.save_answer(attempt_id, question_id, option_id, answer_text=answer_text)
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
    is_published = True
    if attempt.get("result"):
        is_published = bool(attempt["result"].get("is_published", 1))
    return render_template("result.html", page_title="Exam result", attempt=attempt, is_published=is_published)


@learning_bp.route("/student/notifications")
def student_notifications():
    sid = _student_id()
    if not sid:
        return redirect(url_for("auth.login"))
    db_manager.init_db()
    notifs = db_manager.get_notifications(recipient_type="student", recipient_id=int(sid), limit=50)
    unread_count = db_manager.get_unread_notification_count(recipient_type="student", recipient_id=int(sid))
    return render_template(
        "student_notifications.html",
        page_title="My Notifications",
        notifications=notifs,
        unread_count=unread_count,
    )


@learning_bp.route("/api/student/notifications")
def api_student_notifications():
    sid = _student_id()
    if not sid:
        return jsonify({"error": "Login required."}), 401
    db_manager.init_db()
    type_filter = request.args.get("type")
    unread_only = request.args.get("unread") == "true"
    notifs = db_manager.get_notifications(
        recipient_type="student",
        recipient_id=int(sid),
        unread_only=unread_only,
        type_filter=type_filter,
    )
    unread_count = db_manager.get_unread_notification_count(recipient_type="student", recipient_id=int(sid))
    return jsonify({
        "status": "success",
        "notifications": notifs,
        "unread_count": unread_count,
    })


@learning_bp.route("/api/student/notifications/<int:notification_id>/read", methods=["POST"])
def api_mark_student_notification_read(notification_id: int):
    sid = _student_id()
    if not sid:
        return jsonify({"error": "Login required."}), 401
    db_manager.init_db()
    db_manager.mark_notification_as_read(notification_id, recipient_id=int(sid))
    return jsonify({"success": True})


@learning_bp.route("/api/student/notifications/read-all", methods=["POST"])
def api_mark_all_student_notifications_read():
    sid = _student_id()
    if not sid:
        return jsonify({"error": "Login required."}), 401
    db_manager.init_db()
    count = db_manager.mark_all_notifications_as_read(recipient_type="student", recipient_id=int(sid))
    return jsonify({"success": True, "marked_count": count})
