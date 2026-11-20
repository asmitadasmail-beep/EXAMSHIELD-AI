import hmac
import json
import logging
import os
import secrets
from datetime import datetime
from functools import wraps

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from backend.database.db_manager import DatabaseManager
from backend.services.report_exporter import export_csv, export_pdf, export_xlsx
from backend.services.document_parser import extract_document_text, is_allowed_document, parse_bulk_text
from backend.services.ai_composer_service import extract_questions_with_ai, generate_questions_from_study_material, get_ai_status


admin_bp = Blueprint("admin", __name__)
db_manager = DatabaseManager()
logger = logging.getLogger(__name__)

ADMIN_ROLES = {"Super Admin", "Teacher"}


def _normalise(value: str | None) -> str:
    return str(value or "").strip().lower()


def _csrf_token() -> str:
    token = session.get("admin_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["admin_csrf_token"] = token
    return token


@admin_bp.context_processor
def inject_admin_csrf_token():
    return {"admin_csrf_token": _csrf_token}


def _valid_csrf_token() -> bool:
    submitted = (
        request.form.get("csrf_token", "")
        or request.headers.get("X-CSRFToken", "")
        or request.headers.get("X-Csrf-Token", "")
    )
    if not submitted and request.is_json:
        data = request.get_json(silent=True) or {}
        submitted = data.get("csrf_token", "")
    expected = session.get("admin_csrf_token", "")
    return bool(submitted and expected and hmac.compare_digest(str(submitted), str(expected)))


def _current_admin():
    admin_user_id = session.get("admin_user_id")
    if not isinstance(admin_user_id, int):
        return None

    db_manager.init_db()
    with db_manager.get_connection() as conn:
        row = conn.execute(
            """
            SELECT admin_user_id, email, name, role
            FROM admin_users
            WHERE admin_user_id = ? AND active = 1
            """,
            (admin_user_id,),
        ).fetchone()
    return dict(row) if row else None


def _admin_required(*roles: str):
    allowed_roles = set(roles or ADMIN_ROLES)

    def decorate(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            admin_user = _current_admin()
            if admin_user is None:
                session.pop("admin_user_id", None)
                session.pop("admin_csrf_token", None)
                if session.get("student_id") is not None:
                    return redirect(url_for("dashboard"))
                return redirect(url_for("admin.login"))
            if admin_user["role"] not in allowed_roles:
                abort(403)
            g.admin_user = admin_user
            return view_func(*args, **kwargs)

        return wrapped

    return decorate


def _has_exam_permission(admin_user: dict, exam_id: int) -> bool:
    """Check if the administrator has permission to access the given examination."""
    if admin_user.get("role") == "Super Admin":
        return True
    admin_user_id = admin_user.get("admin_user_id")
    if not admin_user_id:
        return False
    db_manager.init_db()
    with db_manager.get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM teacher_exam_permissions
            WHERE admin_user_id = ? AND exam_id = ?
            """,
            (admin_user_id, exam_id),
        ).fetchone()
    return row is not None


def _exam_permission_required(param_name: str = "exam_id"):
    """Decorator to enforce that the logged-in admin has permission for the specified exam."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            exam_id = kwargs.get(param_name)
            if exam_id is not None:
                try:
                    exam_id_int = int(exam_id)
                except (ValueError, TypeError):
                    abort(404)
                if not _has_exam_permission(g.admin_user, exam_id_int):
                    abort(403)
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


def _compute_admin_overview(admin_user: dict) -> dict:
    db_manager.init_db()
    with db_manager.get_connection() as conn:
        exam_filter = ""
        parameters: tuple = ()
        if admin_user["role"] == "Teacher":
            exam_filter = """
                WHERE EXISTS (
                    SELECT 1 FROM teacher_exam_permissions p
                    WHERE p.exam_id = e.exam_id AND p.admin_user_id = ?
                )
            """
            parameters = (admin_user["admin_user_id"],)

        total_examinations = conn.execute(
            f"SELECT COUNT(*) AS count FROM exams e {exam_filter}", parameters
        ).fetchone()["count"]
        total_questions = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM questions q JOIN exams e ON e.exam_id = q.exam_id
            {exam_filter}
            """,
            parameters,
        ).fetchone()["count"]
        attempt_filter = ""
        if admin_user["role"] == "Teacher":
            attempt_filter = """
                AND EXISTS (
                    SELECT 1 FROM teacher_exam_permissions p
                    WHERE p.exam_id = a.exam_id AND p.admin_user_id = ?
                )
            """
        attempt_parameters = (admin_user["admin_user_id"],) if attempt_filter else ()
        active_examinations = conn.execute(
            f"SELECT COUNT(*) AS count FROM exam_attempts a WHERE a.status = 'in_progress' {attempt_filter}",
            attempt_parameters,
        ).fetchone()["count"]
        completed_examinations = conn.execute(
            f"SELECT COUNT(*) AS count FROM exam_attempts a WHERE a.status = 'submitted' {attempt_filter}",
            attempt_parameters,
        ).fetchone()["count"]
        if admin_user["role"] == "Teacher":
            student_count = conn.execute(
                """
                SELECT COUNT(DISTINCT a.student_id) AS count
                FROM exam_attempts a
                JOIN teacher_exam_permissions p ON p.exam_id = a.exam_id
                WHERE p.admin_user_id = ?
                """,
                (admin_user["admin_user_id"],),
            ).fetchone()["count"]
        else:
            student_count = conn.execute(
                "SELECT COUNT(*) AS count FROM students"
            ).fetchone()["count"]
        average_school_performance = conn.execute(
            f"""
            SELECT COALESCE(AVG(r.percentage), 0) AS average_percentage
            FROM exam_results r
            JOIN exam_attempts a ON a.attempt_id = r.attempt_id
            WHERE 1 = 1 {attempt_filter}
            """,
            attempt_parameters,
        ).fetchone()["average_percentage"]
        upcoming_examinations = conn.execute(
            f"""
            SELECT COUNT(*) AS count FROM exams e
            {exam_filter}
            {"AND" if exam_filter else "WHERE"} NOT EXISTS (
                SELECT 1 FROM exam_attempts a WHERE a.exam_id = e.exam_id
            )
            """,
            parameters,
        ).fetchone()["count"]
        teacher_count = None
        if admin_user["role"] == "Super Admin":
            teacher_count = conn.execute(
                "SELECT COUNT(*) AS count FROM admin_users WHERE role = 'Teacher' AND active = 1"
            ).fetchone()["count"]

    return {
        "students": student_count,
        "teachers": teacher_count,
        "total_examinations": total_examinations,
        "upcoming_examinations": upcoming_examinations,
        "active_examinations": active_examinations,
        "completed_examinations": completed_examinations,
        "questions_in_bank": total_questions,
        "average_student_performance": round(float(average_school_performance or 0), 2),
    }


@admin_bp.route("/admin")
def index():
    if session.get("admin_user_id"):
        return redirect(url_for("admin.dashboard"))
    if session.get("student_id") is not None:
        return redirect(url_for("dashboard"))
    return redirect(url_for("admin.login"))


@admin_bp.route("/admin/login", methods=["GET", "POST"])
def login():
    try:
        current_admin = _current_admin()
    except Exception:
        logger.exception("Administrative session lookup failed")
        current_admin = None
        lookup_error = "Login is temporarily unavailable. Please try again."
    else:
        lookup_error = None
    if current_admin:
        return redirect(url_for("admin.dashboard"))

    error = lookup_error
    if request.method == "POST":
        if not _valid_csrf_token():
            return render_template(
                "admin/login.html",
                page_title="Admin login",
                error="Your login form expired. Please refresh and try again.",
            ), 400
        email = _normalise(request.form.get("email"))
        password = request.form.get("password", "")
        try:
            db_manager.init_db()
            with db_manager.get_connection() as conn:
                account = conn.execute(
                    """
                    SELECT admin_user_id, password_hash
                    FROM admin_users
                    WHERE email = ? AND active = 1
                    """,
                    (email,),
                ).fetchone()
            if account and check_password_hash(account["password_hash"], password):
                session.clear()
                session.permanent = True
                session["admin_user_id"] = int(account["admin_user_id"])
                session["admin_csrf_token"] = secrets.token_urlsafe(32)
                return redirect(url_for("admin.dashboard"))
            error = "Invalid email or password."
        except Exception:
            logger.exception("Administrative login failed")
            error = "Login is temporarily unavailable. Please try again."

    return render_template("admin/login.html", page_title="Admin login", error=error)


@admin_bp.route("/admin/logout", methods=["POST"])
def logout():
    if _current_admin() is None:
        if session.get("student_id") is not None:
            return redirect(url_for("dashboard"))
        return redirect(url_for("admin.login"))
    if not _valid_csrf_token():
        abort(400)
    session.clear()
    return redirect(url_for("admin.login"))


def _fetch_notifications(conn, admin_user: dict) -> list[dict]:
    is_teacher = admin_user.get("role") == "Teacher"
    uid = admin_user.get("admin_user_id")
    try:
        db_manager.sync_system_notifications(user_id=uid, role=admin_user.get("role"))
        notifs = db_manager.get_notifications(
            recipient_type="teacher",
            recipient_id=uid,
            limit=10,
        )
        if notifs:
            return notifs
    except Exception:
        pass

    if is_teacher:
        rows = conn.execute(
            """
            SELECT a.attempt_id, a.submitted_at AS timestamp, 'new_submission' AS type,
                   s.name AS student_name, e.name AS exam_name, r.percentage, r.marks_obtained, e.max_marks,
                   'New Submission' AS title,
                   ('Candidate ' || s.name || ' submitted ' || e.name) AS message
            FROM exam_attempts a
            JOIN students s ON s.student_id = a.student_id
            JOIN exams e ON e.exam_id = a.exam_id
            JOIN exam_results r ON r.attempt_id = a.attempt_id
            JOIN teacher_exam_permissions p ON p.exam_id = e.exam_id
            WHERE p.admin_user_id = ? AND a.status = 'submitted'
            ORDER BY a.submitted_at DESC LIMIT 10
            """,
            (uid,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT a.attempt_id, a.submitted_at AS timestamp, 'new_submission' AS type,
                   s.name AS student_name, e.name AS exam_name, r.percentage, r.marks_obtained, e.max_marks,
                   'New Submission' AS title,
                   ('Candidate ' || s.name || ' submitted ' || e.name) AS message
            FROM exam_attempts a
            JOIN students s ON s.student_id = a.student_id
            JOIN exams e ON e.exam_id = a.exam_id
            JOIN exam_results r ON r.attempt_id = a.attempt_id
            WHERE a.status = 'submitted'
            ORDER BY a.submitted_at DESC LIMIT 10
            """
        ).fetchall()
    return [dict(r) for r in rows]


def _fetch_dashboard_data(admin_user: dict) -> dict:
    db_manager.init_db()
    with db_manager.get_connection() as conn:
        is_teacher = admin_user.get("role") == "Teacher"
        uid = admin_user.get("admin_user_id")

        # Base overview statistics
        stats = _compute_admin_overview(admin_user)

        # 1. Subject Activity Breakdown (for Examination activity graph)
        if is_teacher:
            subject_activity = [
                dict(row) for row in conn.execute(
                    """
                    SELECT s.name AS subject_name,
                           COUNT(a.attempt_id) AS total_attempts,
                           SUM(CASE WHEN a.status = 'submitted' THEN 1 ELSE 0 END) AS submitted_count,
                           SUM(CASE WHEN a.status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress_count
                    FROM subjects s
                    JOIN question_banks qb ON qb.subject_id = s.subject_id
                    JOIN exams e ON e.bank_id = qb.bank_id
                    JOIN teacher_exam_permissions p ON p.exam_id = e.exam_id
                    LEFT JOIN exam_attempts a ON a.exam_id = e.exam_id
                    WHERE p.admin_user_id = ?
                    GROUP BY s.subject_id
                    ORDER BY total_attempts DESC, s.name ASC
                    """,
                    (uid,),
                ).fetchall()
            ]
        else:
            subject_activity = [
                dict(row) for row in conn.execute(
                    """
                    SELECT s.name AS subject_name,
                           COUNT(a.attempt_id) AS total_attempts,
                           SUM(CASE WHEN a.status = 'submitted' THEN 1 ELSE 0 END) AS submitted_count,
                           SUM(CASE WHEN a.status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress_count
                    FROM subjects s
                    JOIN question_banks qb ON qb.subject_id = s.subject_id
                    JOIN exams e ON e.bank_id = qb.bank_id
                    LEFT JOIN exam_attempts a ON a.exam_id = e.exam_id
                    GROUP BY s.subject_id
                    ORDER BY total_attempts DESC, s.name ASC
                    """
                ).fetchall()
            ]

        # 2. Performance Distribution & Trend (for Student performance trend graph)
        attempt_join = ""
        attempt_params = ()
        if is_teacher:
            attempt_join = "JOIN teacher_exam_permissions p ON p.exam_id = a.exam_id WHERE p.admin_user_id = ?"
            attempt_params = (uid,)

        band_row = conn.execute(
            f"""
            SELECT 
                SUM(CASE WHEN r.percentage >= 85 THEN 1 ELSE 0 END) AS distinction,
                SUM(CASE WHEN r.percentage >= 70 AND r.percentage < 85 THEN 1 ELSE 0 END) AS proficient,
                SUM(CASE WHEN r.percentage >= 50 AND r.percentage < 70 THEN 1 ELSE 0 END) AS average,
                SUM(CASE WHEN r.percentage < 50 THEN 1 ELSE 0 END) AS needs_improvement,
                COUNT(*) AS total_evaluated,
                COALESCE(MAX(r.percentage), 0) AS max_percentage,
                COALESCE(MIN(r.percentage), 0) AS min_percentage
            FROM exam_results r
            JOIN exam_attempts a ON a.attempt_id = r.attempt_id
            {attempt_join}
            """,
            attempt_params,
        ).fetchone()

        performance_bands = {
            "distinction": (band_row["distinction"] or 0) if band_row else 0,
            "proficient": (band_row["proficient"] or 0) if band_row else 0,
            "average": (band_row["average"] or 0) if band_row else 0,
            "needs_improvement": (band_row["needs_improvement"] or 0) if band_row else 0,
            "total_evaluated": (band_row["total_evaluated"] or 0) if band_row else 0,
            "max_percentage": round(float(band_row["max_percentage"] or 0), 1) if band_row else 0,
            "min_percentage": round(float(band_row["min_percentage"] or 0), 1) if band_row else 0,
        }

        # Trendline: chronological scores
        trend_rows = conn.execute(
            f"""
            SELECT a.attempt_id, a.submitted_at, r.percentage, e.name AS exam_name, s.name AS student_name
            FROM exam_results r
            JOIN exam_attempts a ON a.attempt_id = r.attempt_id
            JOIN exams e ON e.exam_id = a.exam_id
            JOIN students s ON s.student_id = a.student_id
            {attempt_join}
            ORDER BY a.submitted_at ASC LIMIT 10
            """,
            attempt_params,
        ).fetchall()
        performance_trend = [dict(r) for r in trend_rows]

        # 3. Upcoming Examinations List
        if is_teacher:
            upcoming_exams = [
                dict(r) for r in conn.execute(
                    """
                    SELECT e.exam_id, e.name, e.duration_minutes, e.max_marks, e.difficulty, e.mode,
                           s.name AS subject_name, qb.name AS bank_name,
                           (SELECT COUNT(*) FROM questions q WHERE q.exam_id = e.exam_id) AS question_count
                    FROM exams e
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    JOIN teacher_exam_permissions p ON p.exam_id = e.exam_id
                    WHERE p.admin_user_id = ?
                    AND NOT EXISTS (SELECT 1 FROM exam_attempts a WHERE a.exam_id = e.exam_id)
                    ORDER BY e.exam_id DESC LIMIT 6
                    """,
                    (uid,),
                ).fetchall()
            ]
        else:
            upcoming_exams = [
                dict(r) for r in conn.execute(
                    """
                    SELECT e.exam_id, e.name, e.duration_minutes, e.max_marks, e.difficulty, e.mode,
                           s.name AS subject_name, qb.name AS bank_name,
                           (SELECT COUNT(*) FROM questions q WHERE q.exam_id = e.exam_id) AS question_count
                    FROM exams e
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    WHERE NOT EXISTS (SELECT 1 FROM exam_attempts a WHERE a.exam_id = e.exam_id)
                    ORDER BY e.exam_id DESC LIMIT 6
                    """
                ).fetchall()
            ]

        # 4. Recent Examination Activity
        if is_teacher:
            recent_activity = [
                dict(r) for r in conn.execute(
                    """
                    SELECT a.attempt_id, a.mode, a.started_at, a.submitted_at, a.status,
                           s.name AS student_name, s.usn, e.name AS exam_name, subj.name AS subject_name
                    FROM exam_attempts a
                    JOIN students s ON s.student_id = a.student_id
                    JOIN exams e ON e.exam_id = a.exam_id
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects subj ON subj.subject_id = qb.subject_id
                    JOIN teacher_exam_permissions p ON p.exam_id = e.exam_id
                    WHERE p.admin_user_id = ?
                    ORDER BY a.started_at DESC LIMIT 6
                    """,
                    (uid,),
                ).fetchall()
            ]
        else:
            recent_activity = [
                dict(r) for r in conn.execute(
                    """
                    SELECT a.attempt_id, a.mode, a.started_at, a.submitted_at, a.status,
                           s.name AS student_name, s.usn, e.name AS exam_name, subj.name AS subject_name
                    FROM exam_attempts a
                    JOIN students s ON s.student_id = a.student_id
                    JOIN exams e ON e.exam_id = a.exam_id
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects subj ON subj.subject_id = qb.subject_id
                    ORDER BY a.started_at DESC LIMIT 6
                    """
                ).fetchall()
            ]

        # 5. Recently Submitted Examinations
        if is_teacher:
            recent_submissions = [
                dict(r) for r in conn.execute(
                    """
                    SELECT a.attempt_id, a.submitted_at, a.status,
                           s.name AS student_name, s.usn, e.name AS exam_name, e.max_marks,
                           r.marks_obtained, r.percentage, r.time_taken_seconds,
                           r.correct_answers, r.incorrect_answers, r.unanswered
                    FROM exam_attempts a
                    JOIN students s ON s.student_id = a.student_id
                    JOIN exams e ON e.exam_id = a.exam_id
                    JOIN exam_results r ON r.attempt_id = a.attempt_id
                    JOIN teacher_exam_permissions p ON p.exam_id = e.exam_id
                    WHERE p.admin_user_id = ? AND a.status = 'submitted'
                    ORDER BY a.submitted_at DESC LIMIT 6
                    """,
                    (uid,),
                ).fetchall()
            ]
        else:
            recent_submissions = [
                dict(r) for r in conn.execute(
                    """
                    SELECT a.attempt_id, a.submitted_at, a.status,
                           s.name AS student_name, s.usn, e.name AS exam_name, e.max_marks,
                           r.marks_obtained, r.percentage, r.time_taken_seconds,
                           r.correct_answers, r.incorrect_answers, r.unanswered
                    FROM exam_attempts a
                    JOIN students s ON s.student_id = a.student_id
                    JOIN exams e ON e.exam_id = a.exam_id
                    JOIN exam_results r ON r.attempt_id = a.attempt_id
                    WHERE a.status = 'submitted'
                    ORDER BY a.submitted_at DESC LIMIT 6
                    """
                ).fetchall()
            ]

        # 6. Recent Student Performance Highlights
        if is_teacher:
            performance_highlights = [
                dict(r) for r in conn.execute(
                    """
                    SELECT a.attempt_id, s.name AS student_name, s.usn, e.name AS exam_name,
                           r.marks_obtained, e.max_marks, r.percentage, r.time_taken_seconds,
                           a.submitted_at
                    FROM exam_results r
                    JOIN exam_attempts a ON a.attempt_id = r.attempt_id
                    JOIN students s ON s.student_id = a.student_id
                    JOIN exams e ON e.exam_id = a.exam_id
                    JOIN teacher_exam_permissions p ON p.exam_id = e.exam_id
                    WHERE p.admin_user_id = ?
                    ORDER BY r.percentage DESC, r.created_at DESC LIMIT 5
                    """,
                    (uid,),
                ).fetchall()
            ]
        else:
            performance_highlights = [
                dict(r) for r in conn.execute(
                    """
                    SELECT a.attempt_id, s.name AS student_name, s.usn, e.name AS exam_name,
                           r.marks_obtained, e.max_marks, r.percentage, r.time_taken_seconds,
                           a.submitted_at
                    FROM exam_results r
                    JOIN exam_attempts a ON a.attempt_id = r.attempt_id
                    JOIN students s ON s.student_id = a.student_id
                    JOIN exams e ON e.exam_id = a.exam_id
                    ORDER BY r.percentage DESC, r.created_at DESC LIMIT 5
                    """
                ).fetchall()
            ]

        # Notifications list
        notifications = _fetch_notifications(conn, admin_user)

    return {
        "stats": stats,
        "subject_activity": subject_activity,
        "performance_bands": performance_bands,
        "performance_trend": performance_trend,
        "upcoming_exams": upcoming_exams,
        "recent_activity": recent_activity,
        "recent_submissions": recent_submissions,
        "performance_highlights": performance_highlights,
        "notifications": notifications,
    }


@admin_bp.route("/admin/dashboard")
@_admin_required()
def dashboard():
    data = _fetch_dashboard_data(g.admin_user)
    return render_template(
        "admin/dashboard.html",
        page_title="Dashboard",
        stats=data["stats"],
        subject_activity=data["subject_activity"],
        performance_bands=data["performance_bands"],
        performance_trend=data["performance_trend"],
        upcoming_exams=data["upcoming_exams"],
        recent_activity=data["recent_activity"],
        recent_submissions=data["recent_submissions"],
        performance_highlights=data["performance_highlights"],
        notifications=data["notifications"],
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/notifications")
@_admin_required()
def notifications():
    db_manager.init_db()
    uid = g.admin_user["admin_user_id"]
    role = g.admin_user["role"]
    db_manager.sync_system_notifications(user_id=uid, role=role)
    tab = request.args.get("type") or request.args.get("tab") or "all"

    notifs = db_manager.get_notifications(
        recipient_type="teacher",
        recipient_id=uid,
        unread_only=(tab == "unread"),
        type_filter=tab if tab != "unread" else None,
        limit=100,
    )
    unread_count = db_manager.get_unread_notification_count(recipient_type="teacher", recipient_id=uid)

    counts = {
        "all": len(db_manager.get_notifications(recipient_type="teacher", recipient_id=uid, limit=100)),
        "new_submission": len(db_manager.get_notifications(recipient_type="teacher", recipient_id=uid, type_filter="new_submission", limit=100)),
        "upcoming_exam": len(db_manager.get_notifications(recipient_type="teacher", recipient_id=uid, type_filter="upcoming_exam", limit=100)),
        "completed_exam": len(db_manager.get_notifications(recipient_type="teacher", recipient_id=uid, type_filter="completed_exam", limit=100)),
        "pending_evaluation": len(db_manager.get_notifications(recipient_type="teacher", recipient_id=uid, type_filter="pending_evaluation", limit=100)),
    }

    if request.is_json:
        return jsonify({
            "status": "success",
            "notifications": notifs,
            "unread_count": unread_count,
            "counts": counts,
            "active_tab": tab,
        })

    return render_template(
        "admin/notifications.html",
        page_title="Notifications Center",
        notifications=notifs,
        unread_count=unread_count,
        counts=counts,
        active_tab=tab,
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/api/notifications")
@_admin_required()
def api_admin_notifications():
    db_manager.init_db()
    uid = g.admin_user["admin_user_id"]
    type_filter = request.args.get("type")
    unread_only = request.args.get("unread") == "true"
    notifs = db_manager.get_notifications(
        recipient_type="teacher",
        recipient_id=uid,
        unread_only=unread_only,
        type_filter=type_filter,
        limit=50,
    )
    unread_count = db_manager.get_unread_notification_count(recipient_type="teacher", recipient_id=uid)
    return jsonify({
        "status": "success",
        "notifications": notifs,
        "unread_count": unread_count,
    })


@admin_bp.route("/admin/api/notifications/<int:notification_id>/read", methods=["POST"])
@_admin_required()
def api_mark_admin_notification_read(notification_id: int):
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid CSRF token."}), 400
    db_manager.init_db()
    db_manager.mark_notification_as_read(notification_id, recipient_id=g.admin_user["admin_user_id"])
    return jsonify({"success": True})


@admin_bp.route("/admin/api/notifications/read-all", methods=["POST"])
@_admin_required()
def api_mark_all_admin_notifications_read():
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid CSRF token."}), 400
    db_manager.init_db()
    count = db_manager.mark_all_notifications_as_read(recipient_type="teacher", recipient_id=g.admin_user["admin_user_id"])
    return jsonify({"success": True, "marked_count": count})


@admin_bp.route("/admin/exams")
@_admin_required()
def exams():
    db_manager.init_db()
    user_id = g.admin_user["admin_user_id"]
    role = g.admin_user["role"]

    search = request.args.get("search", "").strip() or None
    raw_subject = request.args.get("subject_id", "").strip()
    try:
        subject_id = int(raw_subject) if raw_subject else None
    except (ValueError, TypeError):
        subject_id = None

    status = request.args.get("status", "").strip() or None
    department = request.args.get("department", "").strip() or None
    semester = request.args.get("semester", "").strip() or None
    sort_by = request.args.get("sort_by", "newest").strip() or "newest"

    # Fetch all accessible exams to compute status counts and filter options
    all_exams = db_manager.list_managed_exams(user_id=user_id, role=role)
    status_counts = {
        "all": len(all_exams),
        "draft": sum(1 for e in all_exams if e["status"].lower() == "draft"),
        "scheduled": sum(1 for e in all_exams if e["status"].lower() == "scheduled"),
        "live": sum(1 for e in all_exams if e["status"].lower() == "live"),
        "completed": sum(1 for e in all_exams if e["status"].lower() == "completed"),
        "cancelled": sum(1 for e in all_exams if e["status"].lower() == "cancelled"),
        "archived": sum(1 for e in all_exams if e["status"].lower() == "archived"),
    }

    # Filtered exams
    filtered_exams = db_manager.list_managed_exams(
        user_id=user_id,
        role=role,
        search=search,
        subject_id=subject_id,
        status=status,
        department=department,
        semester=semester,
        sort_by=sort_by,
    )

    if request.headers.get("Accept") == "application/json" or request.args.get("format") == "json":
        return jsonify({
            "exams": filtered_exams,
            "status_counts": status_counts,
            "total": len(filtered_exams),
        })

    subjects = db_manager.list_subjects()
    departments = sorted(list({e.get("department") for e in all_exams if e.get("department")}))
    semesters = sorted(list({e.get("semester") for e in all_exams if e.get("semester")}))

    return render_template(
        "admin/exams.html",
        page_title="Examinations",
        stats=_compute_admin_overview(g.admin_user),
        exams=filtered_exams,
        subjects=subjects,
        departments=departments,
        semesters=semesters,
        status_counts=status_counts,
        current_filter={
            "search": search or "",
            "subject_id": subject_id,
            "status": status or "",
            "department": department or "",
            "semester": semester or "",
            "sort_by": sort_by,
        },
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/exams/create", methods=["GET", "POST"])
@_admin_required()
def exam_create():
    db_manager.init_db()
    if request.method == "POST":
        if not _valid_csrf_token():
            if request.is_json:
                return jsonify({"success": False, "error": "Invalid or expired CSRF token."}), 400
            abort(400)

        if request.is_json:
            data = request.get_json(silent=True) or {}
        else:
            data = request.form.to_dict()

        # Part 1: Examination Details
        title = str(data.get("title") or data.get("name") or "").strip()
        if not title:
            msg = "Examination title is required."
            if request.is_json:
                return jsonify({"success": False, "error": msg}), 400
            return render_template(
                "admin/exam_create.html",
                page_title="Create Question Paper",
                subjects=db_manager.list_subjects(),
                error=msg,
                admin_user=g.admin_user,
            )

        subject_name = str(data.get("subject_name") or data.get("subject") or "").strip()
        subject_id = data.get("subject_id")
        try:
            subject_id = int(subject_id) if subject_id else None
        except (ValueError, TypeError):
            subject_id = None

        department = str(data.get("department") or data.get("course") or "").strip()
        semester = str(data.get("semester") or "").strip()
        academic_year = str(data.get("academic_year") or data.get("year") or "").strip()
        description = str(data.get("description") or "").strip()
        instructions = str(data.get("instructions") or "").strip()

        try:
            duration_minutes = int(data.get("duration_minutes") or data.get("duration") or 1)
        except (ValueError, TypeError):
            duration_minutes = 1
        if duration_minutes <= 0:
            msg = "Examination duration must be at least 1 minute."
            if request.is_json:
                return jsonify({"success": False, "error": msg}), 400
            return render_template(
                "admin/exam_create.html",
                page_title="Create Question Paper",
                subjects=db_manager.list_subjects(),
                error=msg,
                admin_user=g.admin_user,
            )

        try:
            pass_marks = float(data.get("pass_marks") or 0)
        except (ValueError, TypeError):
            pass_marks = 0.0

        try:
            negative_marking = float(data.get("negative_marking") or 0)
        except (ValueError, TypeError):
            negative_marking = 0.0

        randomize_questions = bool(data.get("randomize_questions", False))
        randomize_options = bool(data.get("randomize_options", False))
        allow_multiple_attempts = bool(data.get("allow_multiple_attempts", False))
        start_time = data.get("start_time") or None
        end_time = data.get("end_time") or None
        status = str(data.get("status") or "published").strip().lower()
        if status not in {"draft", "published", "scheduled"}:
            status = "published"

        # Questions extraction
        questions_raw = data.get("questions")
        if isinstance(questions_raw, str):
            try:
                questions = json.loads(questions_raw)
            except Exception:
                questions = []
        elif isinstance(questions_raw, list):
            questions = questions_raw
        else:
            questions = []

        # Part 5: Validation rules
        if status in {"published", "scheduled"}:
            if not questions:
                msg = "At least one question is required to publish or schedule an examination."
                if request.is_json:
                    return jsonify({"success": False, "error": msg}), 400
                return render_template(
                    "admin/exam_create.html",
                    page_title="Create Question Paper",
                    subjects=db_manager.list_subjects(),
                    error=msg,
                    admin_user=g.admin_user,
                )

            if status == "scheduled":
                if not start_time:
                    msg = "Start date and time are required for scheduled examinations."
                    if request.is_json:
                        return jsonify({"success": False, "error": msg}), 400
                    return render_template(
                        "admin/exam_create.html",
                        page_title="Create Question Paper",
                        subjects=db_manager.list_subjects(),
                        error=msg,
                        admin_user=g.admin_user,
                    )
                if start_time and end_time and start_time > end_time:
                    msg = "Scheduled end time cannot be before start time."
                    if request.is_json:
                        return jsonify({"success": False, "error": msg}), 400
                    return render_template(
                        "admin/exam_create.html",
                        page_title="Create Question Paper",
                        subjects=db_manager.list_subjects(),
                        error=msg,
                        admin_user=g.admin_user,
                    )

            computed_marks = 0.0
            for idx, q in enumerate(questions, start=1):
                stmt = str(q.get("statement", "")).strip()
                if not stmt:
                    msg = f"Question #{idx} statement cannot be blank."
                    if request.is_json:
                        return jsonify({"success": False, "error": msg}), 400
                    return render_template(
                        "admin/exam_create.html",
                        page_title="Create Question Paper",
                        subjects=db_manager.list_subjects(),
                        error=msg,
                        admin_user=g.admin_user,
                    )

                try:
                    q_marks = float(q.get("marks", 1))
                except (ValueError, TypeError):
                    q_marks = 1.0
                if q_marks <= 0:
                    msg = f"Question #{idx} marks must be greater than 0."
                    if request.is_json:
                        return jsonify({"success": False, "error": msg}), 400
                    return render_template(
                        "admin/exam_create.html",
                        page_title="Create Question Paper",
                        subjects=db_manager.list_subjects(),
                        error=msg,
                        admin_user=g.admin_user,
                    )
                computed_marks += q_marks

                q_type = str(q.get("question_type", "mcq")).strip().lower()
                if q_type in {"mcq", "multiple_select", "true_false"}:
                    opts = q.get("options") or []
                    if len(opts) < 2:
                        msg = f"Question #{idx} ({q_type}) must have at least 2 options."
                        if request.is_json:
                            return jsonify({"success": False, "error": msg}), 400
                        return render_template(
                            "admin/exam_create.html",
                            page_title="Create Question Paper",
                            subjects=db_manager.list_subjects(),
                            error=msg,
                            admin_user=g.admin_user,
                        )
                    has_corr = any(bool(opt.get("is_correct")) for opt in opts if isinstance(opt, dict))
                    if not has_corr:
                        msg = f"Question #{idx} ({q_type}) must have at least one marked correct option."
                        if request.is_json:
                            return jsonify({"success": False, "error": msg}), 400
                        return render_template(
                            "admin/exam_create.html",
                            page_title="Create Question Paper",
                            subjects=db_manager.list_subjects(),
                            error=msg,
                            admin_user=g.admin_user,
                        )
                elif q_type in {"fill_blank", "short_answer", "numerical"}:
                    corr_text = str(q.get("correct_answer_text", "")).strip()
                    if not corr_text:
                        msg = f"Question #{idx} ({q_type}) requires a correct answer."
                        if request.is_json:
                            return jsonify({"success": False, "error": msg}), 400
                        return render_template(
                            "admin/exam_create.html",
                            page_title="Create Question Paper",
                            subjects=db_manager.list_subjects(),
                            error=msg,
                            admin_user=g.admin_user,
                        )
        else:
            # Draft status: compute marks if any
            computed_marks = sum(float(q.get("marks", 1)) for q in questions if isinstance(q, dict))

        try:
            req_max_marks = float(data.get("max_marks") or 0)
        except (ValueError, TypeError):
            req_max_marks = 0.0

        final_max_marks = computed_marks if computed_marks > 0 else (req_max_marks if req_max_marks > 0 else 10.0)

        if pass_marks > final_max_marks:
            msg = f"Passing marks ({pass_marks}) cannot exceed total marks ({final_max_marks})."
            if request.is_json:
                return jsonify({"success": False, "error": msg}), 400
            return render_template(
                "admin/exam_create.html",
                page_title="Create Question Paper",
                subjects=db_manager.list_subjects(),
                error=msg,
                admin_user=g.admin_user,
            )

        # Persistence to database
        try:
            exam_id = db_manager.create_examination_with_questions(
                name=title,
                subject_id=subject_id,
                subject_name=subject_name,
                duration_minutes=duration_minutes,
                max_marks=final_max_marks,
                negative_marking=negative_marking,
                department=department,
                semester=semester,
                academic_year=academic_year,
                description=description,
                instructions=instructions,
                pass_marks=pass_marks,
                start_time=start_time,
                end_time=end_time,
                randomize_questions=randomize_questions,
                randomize_options=randomize_options,
                allow_multiple_attempts=allow_multiple_attempts,
                status=status,
                created_by=g.admin_user["admin_user_id"],
                questions=questions,
            )
            db_manager.log_audit(
                action="examination_creation",
                entity_type="exam",
                entity_id=exam_id,
                details=f"Created examination '{title}' ({status.capitalize()}) with {len(questions)} question(s). Total marks: {final_max_marks}.",
                admin_user_id=g.admin_user["admin_user_id"],
                user_name=g.admin_user["name"],
                user_role=g.admin_user["role"],
                exam_id=exam_id,
            )
            if status in ("published", "scheduled"):
                db_manager.create_notification(
                    recipient_type="student",
                    recipient_id=None,
                    title="New Examination Published",
                    message=f"A new examination '{title}' has been published.",
                    type="new_exam_published",
                    link="/dashboard",
                )
            if start_time:
                db_manager.create_notification(
                    recipient_type="teacher",
                    recipient_id=None,
                    title="Upcoming Examination Scheduled",
                    message=f"Examination '{title}' scheduled to commence on {start_time}.",
                    type="upcoming_exam",
                    link=f"/admin/exams?exam_id={exam_id}",
                )
        except Exception:
            logger.exception("Failed to create examination with questions")
            msg = "Database error: Could not save the question paper. Please try again."
            if request.is_json:
                return jsonify({"success": False, "error": msg}), 500
            return render_template(
                "admin/exam_create.html",
                page_title="Create Question Paper",
                subjects=db_manager.list_subjects(),
                error=msg,
                admin_user=g.admin_user,
            )

        if request.is_json:
            exam_rec = db_manager.get_exam(exam_id)
            return jsonify({
                "success": True,
                "exam_id": exam_id,
                "status": status,
                "exam_code": exam_rec.get("exam_code") if exam_rec else None,
                "message": f"Examination '{title}' successfully saved as {status.capitalize()}.",
                "redirect_url": url_for("admin.exams"),
            })
        return redirect(url_for("admin.exams"))

    # GET Request
    return render_template(
        "admin/exam_create.html",
        page_title="Create Question Paper",
        subjects=db_manager.list_subjects(),
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/api/question-bank/questions")
@_admin_required()
def api_question_bank_questions():
    db_manager.init_db()
    teacher_id = g.admin_user["admin_user_id"]
    questions = db_manager.get_questions_from_bank(teacher_id)
    search = request.args.get("search", "").strip().lower()
    subject_filter = request.args.get("subject", "").strip().lower()
    q_type_filter = request.args.get("type", "").strip().lower()

    filtered = []
    for q in questions:
        if search and search not in q.get("statement", "").lower() and search not in q.get("topic", "").lower():
            continue
        if subject_filter and subject_filter != q.get("subject_name", "").lower():
            continue
        if q_type_filter and q_type_filter != q.get("question_type", "").lower():
            continue
        filtered.append(q)

    return jsonify({"questions": filtered, "count": len(filtered)})


@admin_bp.route("/admin/exams/<int:exam_id>/preview")
@_admin_required()
def exam_preview(exam_id: int):
    if not _has_exam_permission(g.admin_user, exam_id):
        abort(403)
    db_manager.init_db()
    exam = db_manager.get_exam_full_detail(exam_id)
    if exam is None:
        abort(404)
    if request.headers.get("Accept") == "application/json" or request.args.get("format") == "json":
        return jsonify(exam)
    return render_template(
        "admin/exam_preview.html",
        page_title=f"Question Paper Preview - {exam['name']}",
        exam=exam,
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/exams/<int:exam_id>/edit", methods=["GET", "POST"])
@_admin_required()
@_exam_permission_required()
def exam_edit(exam_id: int):
    db_manager.init_db()
    exam = db_manager.get_exam_full_detail(exam_id)
    if exam is None:
        abort(404)

    submissions = db_manager.get_exam_submissions(
        exam_id, user_id=g.admin_user["admin_user_id"], role=g.admin_user["role"]
    )
    has_submissions = len(submissions) > 0

    if request.method == "POST":
        if not _valid_csrf_token():
            if request.is_json:
                return jsonify({"success": False, "error": "Invalid or expired CSRF token."}), 400
            abort(400)

        if request.is_json:
            data = request.get_json(silent=True) or {}
        else:
            data = request.form.to_dict()

        title = str(data.get("title") or data.get("name") or "").strip()
        if not title:
            msg = "Examination title is required."
            if request.is_json:
                return jsonify({"success": False, "error": msg}), 400
            return render_template(
                "admin/exam_edit.html",
                page_title=f"Edit Examination - {exam['name']}",
                exam=exam,
                has_submissions=has_submissions,
                submission_count=len(submissions),
                subjects=db_manager.list_subjects(),
                error=msg,
                admin_user=g.admin_user,
            )

        try:
            duration_minutes = int(data.get("duration_minutes") or data.get("duration") or 1)
        except (ValueError, TypeError):
            duration_minutes = 1
        if duration_minutes <= 0:
            msg = "Examination duration must be at least 1 minute."
            if request.is_json:
                return jsonify({"success": False, "error": msg}), 400
            return render_template(
                "admin/exam_edit.html",
                page_title=f"Edit Examination - {exam['name']}",
                exam=exam,
                has_submissions=has_submissions,
                submission_count=len(submissions),
                subjects=db_manager.list_subjects(),
                error=msg,
                admin_user=g.admin_user,
            )

        instructions = str(data.get("instructions") or "").strip()
        description = str(data.get("description") or "").strip()
        department = str(data.get("department") or "").strip()
        semester = str(data.get("semester") or "").strip()
        academic_year = str(data.get("academic_year") or "").strip()

        try:
            pass_marks = float(data.get("pass_marks") or 0.0)
        except (ValueError, TypeError):
            pass_marks = 0.0

        try:
            negative_marking = float(data.get("negative_marking") or 0.0)
        except (ValueError, TypeError):
            negative_marking = 0.0

        randomize_questions = bool(data.get("randomize_questions", False))
        randomize_options = bool(data.get("randomize_options", False))
        allow_multiple_attempts = bool(data.get("allow_multiple_attempts", False))
        start_time = data.get("start_time") or None
        end_time = data.get("end_time") or None
        status = data.get("status")

        if start_time and end_time and start_time >= end_time:
            msg = "Scheduled end time must be later than start time."
            if request.is_json:
                return jsonify({"success": False, "error": msg}), 400
            return render_template(
                "admin/exam_edit.html",
                page_title=f"Edit Examination - {exam['name']}",
                exam=exam,
                has_submissions=has_submissions,
                submission_count=len(submissions),
                subjects=db_manager.list_subjects(),
                error=msg,
                admin_user=g.admin_user,
            )

        # Questions handling if provided
        questions = None
        if "questions" in data:
            q_raw = data.get("questions")
            if isinstance(q_raw, str):
                try:
                    questions = json.loads(q_raw)
                except Exception:
                    questions = []
            elif isinstance(q_raw, list):
                questions = q_raw
            else:
                questions = []

            # Validate questions if updated
            computed_marks = 0.0
            for idx, q in enumerate(questions, start=1):
                stmt = str(q.get("statement", "")).strip()
                if not stmt:
                    msg = f"Question #{idx} statement cannot be blank."
                    if request.is_json:
                        return jsonify({"success": False, "error": msg}), 400
                    return render_template(
                        "admin/exam_edit.html",
                        page_title=f"Edit Examination - {exam['name']}",
                        exam=exam,
                        has_submissions=has_submissions,
                        submission_count=len(submissions),
                        subjects=db_manager.list_subjects(),
                        error=msg,
                        admin_user=g.admin_user,
                    )
                try:
                    q_marks = float(q.get("marks", 1))
                except (ValueError, TypeError):
                    q_marks = 1.0
                if q_marks <= 0:
                    msg = f"Question #{idx} marks must be greater than 0."
                    if request.is_json:
                        return jsonify({"success": False, "error": msg}), 400
                    return render_template(
                        "admin/exam_edit.html",
                        page_title=f"Edit Examination - {exam['name']}",
                        exam=exam,
                        has_submissions=has_submissions,
                        submission_count=len(submissions),
                        subjects=db_manager.list_subjects(),
                        error=msg,
                        admin_user=g.admin_user,
                    )
                computed_marks += q_marks

            if pass_marks > computed_marks and computed_marks > 0:
                msg = f"Passing marks ({pass_marks}) cannot exceed total marks ({computed_marks})."
                if request.is_json:
                    return jsonify({"success": False, "error": msg}), 400
                return render_template(
                    "admin/exam_edit.html",
                    page_title=f"Edit Examination - {exam['name']}",
                    exam=exam,
                    has_submissions=has_submissions,
                    submission_count=len(submissions),
                    subjects=db_manager.list_subjects(),
                    error=msg,
                    admin_user=g.admin_user,
                )

        updated = db_manager.update_examination(
            exam_id=exam_id,
            name=title,
            duration_minutes=duration_minutes,
            instructions=instructions,
            description=description,
            department=department,
            semester=semester,
            academic_year=academic_year,
            pass_marks=pass_marks,
            negative_marking=negative_marking,
            randomize_questions=randomize_questions,
            randomize_options=randomize_options,
            allow_multiple_attempts=allow_multiple_attempts,
            start_time=start_time,
            end_time=end_time,
            status=status,
            questions=questions,
            user_id=g.admin_user["admin_user_id"],
            role=g.admin_user["role"],
        )

        if not updated:
            msg = "Failed to update examination. Check permissions."
            if request.is_json:
                return jsonify({"success": False, "error": msg}), 403
            return render_template(
                "admin/exam_edit.html",
                page_title=f"Edit Examination - {exam['name']}",
                exam=exam,
                has_submissions=has_submissions,
                submission_count=len(submissions),
                subjects=db_manager.list_subjects(),
                error=msg,
                admin_user=g.admin_user,
            )

        db_manager.log_audit(
            action="examination_modification",
            entity_type="exam",
            entity_id=exam_id,
            details=f"Updated examination '{title}'. Settings and parameters modified.",
            admin_user_id=g.admin_user["admin_user_id"],
            user_name=g.admin_user["name"],
            user_role=g.admin_user["role"],
            exam_id=exam_id,
        )
        if questions is not None:
            db_manager.log_audit(
                action="question_changes",
                entity_type="exam",
                entity_id=exam_id,
                details=f"Modified questions for examination '{title}' ({len(questions)} question(s)).",
                admin_user_id=g.admin_user["admin_user_id"],
                user_name=g.admin_user["name"],
                user_role=g.admin_user["role"],
                exam_id=exam_id,
            )

        if request.is_json:
            return jsonify({
                "success": True,
                "exam_id": exam_id,
                "message": "Examination updated successfully.",
                "redirect_url": url_for("admin.exams"),
            })

        flash("Examination updated successfully.", "success")
        return redirect(url_for("admin.exams"))

    # GET request
    audit_logs = db_manager.get_exam_audit_logs(exam_id)
    return render_template(
        "admin/exam_edit.html",
        page_title=f"Edit Examination - {exam['name']}",
        exam=exam,
        has_submissions=has_submissions,
        submission_count=len(submissions),
        subjects=db_manager.list_subjects(),
        audit_logs=audit_logs,
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/exams/<int:exam_id>/status", methods=["POST"])
@_admin_required()
@_exam_permission_required()
def exam_status_change(exam_id: int):
    if not _valid_csrf_token():
        if request.is_json:
            return jsonify({"success": False, "error": "Invalid or expired CSRF token."}), 400
        abort(400)

    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict()

    new_status = str(data.get("status") or data.get("new_status") or "").strip().lower()
    reason = str(data.get("reason") or "").strip()

    valid_statuses = {"draft", "scheduled", "live", "published", "completed", "cancelled", "archived"}
    if new_status not in valid_statuses:
        msg = f"Invalid examination status: {new_status}"
        if request.is_json:
            return jsonify({"success": False, "error": msg}), 400
        abort(400)

    try:
        success = db_manager.change_exam_status(
            exam_id=exam_id,
            new_status=new_status,
            user_id=g.admin_user["admin_user_id"],
            role=g.admin_user["role"],
            reason=reason,
        )
    except ValueError as e:
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(str(e), "error")
        return redirect(url_for("admin.exams"))

    if not success:
        if request.is_json:
            return jsonify({"success": False, "error": "Unauthorized or exam not found."}), 403
        abort(403)

    action_type = "examination_cancellation" if new_status == "cancelled" else "examination_modification"
    details_str = f"Status transitioned to '{new_status}'." + (f" Reason: {reason}" if reason else "")
    db_manager.log_audit(
        action=action_type,
        entity_type="exam",
        entity_id=exam_id,
        details=details_str,
        admin_user_id=g.admin_user["admin_user_id"],
        user_name=g.admin_user["name"],
        user_role=g.admin_user["role"],
        exam_id=exam_id,
    )
    if new_status == "cancelled":
        db_manager.create_notification(
            recipient_type="all",
            recipient_id=None,
            title="Examination Cancelled",
            message=f"Examination #{exam_id} has been cancelled. {reason}".strip(),
            type="examination_cancellation",
            link="/dashboard",
        )
    elif new_status == "completed":
        db_manager.create_notification(
            recipient_type="teacher",
            recipient_id=None,
            title="Examination Completed",
            message=f"Examination #{exam_id} has concluded. Submissions are available for review.",
            type="completed_exam",
            link=f"/admin/evaluations?exam_id={exam_id}",
        )
    elif new_status in ("live", "published"):
        db_manager.create_notification(
            recipient_type="student",
            recipient_id=None,
            title="Examination Live",
            message=f"Examination #{exam_id} is now active and accessible.",
            type="new_exam_published",
            link="/dashboard",
        )

    exam_rec = db_manager.get_exam(exam_id)
    exam_code = exam_rec.get("exam_code") if exam_rec else None

    if request.is_json:
        return jsonify({
            "success": True,
            "status": new_status,
            "exam_code": exam_code,
            "message": f"Examination status updated to {new_status.title()}.",
        })
    flash(f"Examination status updated to {new_status.title()}.", "success")
    return redirect(url_for("admin.exams"))


@admin_bp.route("/admin/exams/<int:exam_id>/publish", methods=["POST"])
@_admin_required()
@_exam_permission_required()
def exam_publish(exam_id: int):
    if not _valid_csrf_token():
        if request.is_json:
            return jsonify({"success": False, "error": "Invalid or expired CSRF token."}), 400
        abort(400)

    db_manager.init_db()
    exam = db_manager.get_exam(exam_id)
    if not exam:
        if request.is_json:
            return jsonify({"success": False, "error": "Examination not found."}), 404
        flash("Examination not found.", "error")
        return redirect(url_for("admin.exams"))

    # Validate exam has at least 1 question
    questions = db_manager.get_exam_questions(exam_id)
    if not questions:
        msg = "Cannot publish an examination with no questions. Please add questions first."
        if request.is_json:
            return jsonify({"success": False, "error": msg}), 400
        flash(msg, "error")
        return redirect(url_for("admin.exams"))

    # Update status to 'published' (generates & stores permanent exam_code if missing)
    try:
        db_manager.change_exam_status(exam_id, "published", g.admin_user["admin_user_id"])
    except Exception as exc:
        msg = str(exc) or "Failed to publish examination."
        if request.is_json:
            return jsonify({"success": False, "error": msg}), 400
        flash(msg, "error")
        return redirect(url_for("admin.exams"))

    updated_exam = db_manager.get_exam(exam_id)
    exam_code = updated_exam.get("exam_code") if updated_exam else None

    # Audit log
    db_manager.log_audit(
        action="examination_publishing",
        entity_type="exam",
        entity_id=exam_id,
        details=f"Examination '{exam['name']}' published with access code {exam_code}",
        admin_user_id=g.admin_user["admin_user_id"],
        user_name=g.admin_user["name"],
        user_role=g.admin_user["role"],
        exam_id=exam_id,
    )

    # Student notification
    db_manager.create_notification(
        recipient_type="student",
        recipient_id=None,
        title="New Examination Published",
        message=f"Examination '{exam['name']}' has been published. Use Exam Code: {exam_code}",
        type="new_exam_published",
        link="/dashboard",
    )

    if request.is_json:
        return jsonify({
            "success": True,
            "exam_id": exam_id,
            "status": "published",
            "exam_code": exam_code,
            "message": f"Examination '{exam['name']}' published successfully. Exam Code: {exam_code}",
        })
    flash(f"Examination published successfully! Exam Code: {exam_code}", "success")
    return redirect(url_for("admin.exams"))


@admin_bp.route("/admin/exams/<int:exam_id>/schedule", methods=["POST"])
@_admin_required()
@_exam_permission_required()
def exam_schedule(exam_id: int):
    if not _valid_csrf_token():
        if request.is_json:
            return jsonify({"success": False, "error": "Invalid or expired CSRF token."}), 400
        abort(400)

    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict()

    start_time = str(data.get("start_time") or "").strip()
    end_time = str(data.get("end_time") or "").strip()

    if not start_time or not end_time:
        msg = "Both start time and end time are required for scheduling."
        if request.is_json:
            return jsonify({"success": False, "error": msg}), 400
        flash(msg, "error")
        return redirect(url_for("admin.exams"))

    if start_time >= end_time:
        msg = "Scheduled end time must be later than start time."
        if request.is_json:
            return jsonify({"success": False, "error": msg}), 400
        flash(msg, "error")
        return redirect(url_for("admin.exams"))

    try:
        success = db_manager.schedule_examination(
            exam_id=exam_id,
            start_time=start_time,
            end_time=end_time,
            user_id=g.admin_user["admin_user_id"],
            role=g.admin_user["role"],
        )
    except Exception as e:
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(str(e), "error")
        return redirect(url_for("admin.exams"))

    if not success:
        if request.is_json:
            return jsonify({"success": False, "error": "Unauthorized or exam not found."}), 403
        abort(403)

    db_manager.log_audit(
        action="examination_modification",
        entity_type="exam",
        entity_id=exam_id,
        details=f"Examination #{exam_id} scheduled window: {start_time} to {end_time}",
        admin_user_id=g.admin_user["admin_user_id"],
        user_name=g.admin_user["name"],
        user_role=g.admin_user["role"],
        exam_id=exam_id,
    )
    db_manager.create_notification(
        recipient_type="student",
        recipient_id=None,
        title="Examination Schedule Changed",
        message=f"Schedule for examination #{exam_id} has been set to {start_time} - {end_time}.",
        type="schedule_changed",
        link="/dashboard",
    )

    if request.is_json:
        return jsonify({
            "success": True,
            "start_time": start_time,
            "end_time": end_time,
            "message": "Examination scheduled successfully.",
        })
    flash("Examination scheduled successfully.", "success")
    return redirect(url_for("admin.exams"))


@admin_bp.route("/admin/exams/<int:exam_id>/duplicate", methods=["POST"])
@_admin_required()
@_exam_permission_required()
def exam_duplicate(exam_id: int):
    if not _valid_csrf_token():
        if request.is_json:
            return jsonify({"success": False, "error": "Invalid or expired CSRF token."}), 400
        abort(400)

    try:
        new_exam_id = db_manager.duplicate_examination(
            exam_id=exam_id,
            user_id=g.admin_user["admin_user_id"],
            role=g.admin_user["role"],
        )
    except PermissionError:
        if request.is_json:
            return jsonify({"success": False, "error": "Permission denied."}), 403
        abort(403)
    except ValueError as e:
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 404
        abort(404)

    if request.is_json:
        return jsonify({
            "success": True,
            "new_exam_id": new_exam_id,
            "redirect_url": url_for("admin.exams"),
            "message": "Examination duplicated successfully as Draft.",
        })
    flash("Examination duplicated successfully as Draft.", "success")
    return redirect(url_for("admin.exams"))


@admin_bp.route("/admin/exams/<int:exam_id>/submissions")
@_admin_required()
@_exam_permission_required()
def exam_submissions(exam_id: int):
    db_manager.init_db()
    exam = db_manager.get_exam_full_detail(exam_id)
    if exam is None:
        abort(404)

    submissions = db_manager.get_exam_submissions(
        exam_id=exam_id,
        user_id=g.admin_user["admin_user_id"],
        role=g.admin_user["role"],
    )

    if request.headers.get("Accept") == "application/json" or request.args.get("format") == "json":
        return jsonify({"exam": exam, "submissions": submissions, "count": len(submissions)})

    return render_template(
        "admin/exam_submissions.html",
        page_title=f"Submissions - {exam['name']}",
        exam=exam,
        submissions=submissions,
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/api/exams/<int:exam_id>/audit-logs")
@_admin_required()
@_exam_permission_required()
def exam_audit_logs(exam_id: int):
    db_manager.init_db()
    logs = db_manager.get_exam_audit_logs(exam_id)
    return jsonify({"success": True, "exam_id": exam_id, "logs": logs})


@admin_bp.route("/admin/question-bank")
@_admin_required()
def question_bank():
    db_manager.init_db()
    user_id = g.admin_user["admin_user_id"]
    role = g.admin_user["role"]

    search = request.args.get("search", "").strip() or None
    raw_subject = request.args.get("subject_id")
    try:
        subject_id = int(raw_subject) if raw_subject else None
    except (ValueError, TypeError):
        subject_id = None

    topic = request.args.get("topic", "").strip() or None
    difficulty = request.args.get("difficulty", "").strip() or None
    q_type = request.args.get("type", "").strip() or None
    raw_marks = request.args.get("marks")
    try:
        marks = float(raw_marks) if raw_marks else None
    except (ValueError, TypeError):
        marks = None

    sort_by = request.args.get("sort", "newest").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        per_page = max(5, min(int(request.args.get("per_page", 15)), 100))
    except (ValueError, TypeError):
        per_page = 15

    data = db_manager.list_bank_questions(
        user_id=user_id,
        role=role,
        search=search,
        subject_id=subject_id,
        topic=topic,
        difficulty=difficulty,
        question_type=q_type,
        marks=marks,
        sort_by=sort_by,
        page=page,
        per_page=per_page,
    )

    filter_options = db_manager.get_bank_filter_options(user_id=user_id, role=role)
    authorized_exams = db_manager.get_authorized_exams_for_user(user_id=user_id, role=role)

    if request.headers.get("Accept") == "application/json" or request.args.get("format") == "json":
        return jsonify({
            "questions": data["items"],
            "pagination": {
                "total_items": data["total_items"],
                "total_pages": data["total_pages"],
                "current_page": data["current_page"],
                "per_page": data["per_page"],
                "has_prev": data["has_prev"],
                "has_next": data["has_next"],
            },
            "filter_options": filter_options,
        })

    return render_template(
        "admin/question_bank.html",
        page_title="Question Bank",
        stats=_compute_admin_overview(g.admin_user),
        questions=data["items"],
        pagination=data,
        filter_options=filter_options,
        authorized_exams=authorized_exams,
        active_filters={
            "search": search or "",
            "subject_id": subject_id,
            "topic": topic or "",
            "difficulty": difficulty or "",
            "type": q_type or "",
            "marks": marks,
            "sort": sort_by,
            "per_page": per_page,
        },
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/api/question-bank/<int:question_id>")
@_admin_required()
def api_get_bank_question(question_id: int):
    db_manager.init_db()
    q = db_manager.get_bank_question(question_id)
    if not q:
        abort(404)
    return jsonify({"question": q})


@admin_bp.route("/admin/question-bank/create", methods=["POST"])
@_admin_required()
def bank_question_create():
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid CSRF token."}), 400

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    if not payload:
        return jsonify({"success": False, "error": "No question data received."}), 400

    statement = str(payload.get("statement", "")).strip()
    if not statement:
        return jsonify({"success": False, "error": "Question statement cannot be empty."}), 400

    try:
        marks = float(payload.get("marks", 1))
    except (ValueError, TypeError):
        marks = 1.0
    if marks <= 0:
        return jsonify({"success": False, "error": "Marks must be greater than zero."}), 400

    try:
        negative_marks = float(payload.get("negative_marks", 0))
    except (ValueError, TypeError):
        negative_marks = 0.0

    raw_subject_id = payload.get("subject_id")
    subject_id = int(raw_subject_id) if raw_subject_id else None
    subject_name = str(payload.get("subject_name", "")).strip() or None
    topic = str(payload.get("topic", "General")).strip()
    question_type = str(payload.get("question_type", "mcq")).strip().lower()
    difficulty = str(payload.get("difficulty", "Medium")).strip()
    explanation = str(payload.get("explanation", "")).strip()
    correct_answer_text = str(payload.get("correct_answer_text", "")).strip()
    image_url = str(payload.get("image_url", "")).strip()

    raw_options = payload.get("options")
    if isinstance(raw_options, str):
        try:
            options = json.loads(raw_options)
        except Exception:
            options = []
    elif isinstance(raw_options, list):
        options = raw_options
    else:
        options = []

    # Question integrity validation
    if question_type in {"mcq", "multiple_select", "true_false"}:
        if len(options) < 2:
            return jsonify({"success": False, "error": f"{question_type.upper()} requires at least two options."}), 400
        has_correct = any(bool(opt.get("is_correct")) for opt in options if isinstance(opt, dict))
        if not has_correct:
            return jsonify({"success": False, "error": "At least one correct answer option must be selected."}), 400
    elif question_type in {"fill_blank", "short_answer", "numerical"}:
        if not correct_answer_text:
            return jsonify({"success": False, "error": f"Correct answer specification is required for {question_type}."}), 400

    db_manager.init_db()
    try:
        new_id = db_manager.create_bank_question(
            statement=statement,
            subject_id=subject_id,
            subject_name=subject_name,
            topic=topic,
            question_type=question_type,
            difficulty=difficulty,
            marks=marks,
            negative_marking=negative_marks,
            explanation=explanation,
            correct_answer_text=correct_answer_text,
            image_url=image_url,
            created_by=g.admin_user["admin_user_id"],
            options=options,
        )
        db_manager.log_audit(
            action="question_changes",
            entity_type="question",
            entity_id=new_id,
            details=f"Created bank question #{new_id} ({question_type}) in {subject_name or 'General'}",
            admin_user_id=g.admin_user["admin_user_id"],
            user_name=g.admin_user["name"],
            user_role=g.admin_user["role"],
        )
        return jsonify({
            "success": True,
            "question_id": new_id,
            "message": "Question successfully added to Question Bank.",
        })
    except Exception:
        logger.exception("Failed to create bank question")
        return jsonify({"success": False, "error": "Database error while adding question."}), 500


@admin_bp.route("/admin/question-bank/<int:question_id>/edit", methods=["POST"])
@_admin_required()
def bank_question_edit(question_id: int):
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid CSRF token."}), 400

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    if not payload:
        return jsonify({"success": False, "error": "No question data received."}), 400

    statement = str(payload.get("statement", "")).strip()
    if not statement:
        return jsonify({"success": False, "error": "Question statement cannot be empty."}), 400

    try:
        marks = float(payload.get("marks", 1))
    except (ValueError, TypeError):
        marks = 1.0
    if marks <= 0:
        return jsonify({"success": False, "error": "Marks must be greater than zero."}), 400

    try:
        negative_marks = float(payload.get("negative_marks", 0))
    except (ValueError, TypeError):
        negative_marks = 0.0

    raw_subject_id = payload.get("subject_id")
    subject_id = int(raw_subject_id) if raw_subject_id else None
    subject_name = str(payload.get("subject_name", "")).strip() or None
    topic = str(payload.get("topic", "General")).strip()
    question_type = str(payload.get("question_type", "mcq")).strip().lower()
    difficulty = str(payload.get("difficulty", "Medium")).strip()
    explanation = str(payload.get("explanation", "")).strip()
    correct_answer_text = str(payload.get("correct_answer_text", "")).strip()
    image_url = str(payload.get("image_url", "")).strip()

    raw_options = payload.get("options")
    if isinstance(raw_options, str):
        try:
            options = json.loads(raw_options)
        except Exception:
            options = []
    elif isinstance(raw_options, list):
        options = raw_options
    else:
        options = []

    if question_type in {"mcq", "multiple_select", "true_false"}:
        if len(options) < 2:
            return jsonify({"success": False, "error": f"{question_type.upper()} requires at least two options."}), 400
        has_correct = any(bool(opt.get("is_correct")) for opt in options if isinstance(opt, dict))
        if not has_correct:
            return jsonify({"success": False, "error": "At least one correct answer option must be selected."}), 400
    elif question_type in {"fill_blank", "short_answer", "numerical"}:
        if not correct_answer_text:
            return jsonify({"success": False, "error": f"Correct answer specification is required for {question_type}."}), 400

    db_manager.init_db()
    updated = db_manager.update_bank_question(
        bank_question_id=question_id,
        statement=statement,
        subject_id=subject_id,
        subject_name=subject_name,
        topic=topic,
        question_type=question_type,
        difficulty=difficulty,
        marks=marks,
        negative_marking=negative_marks,
        explanation=explanation,
        correct_answer_text=correct_answer_text,
        image_url=image_url,
        user_id=g.admin_user["admin_user_id"],
        role=g.admin_user["role"],
        options=options,
    )
    if not updated:
        return jsonify({"success": False, "error": "You do not have permission to edit this question or question was not found."}), 403

    db_manager.log_audit(
        action="question_changes",
        entity_type="question",
        entity_id=question_id,
        details=f"Modified bank question #{question_id}",
        admin_user_id=g.admin_user["admin_user_id"],
        user_name=g.admin_user["name"],
        user_role=g.admin_user["role"],
    )
    return jsonify({"success": True, "message": "Question successfully updated."})


@admin_bp.route("/admin/question-bank/<int:question_id>/delete", methods=["POST"])
@_admin_required()
def bank_question_delete(question_id: int):
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid CSRF token."}), 400

    db_manager.init_db()
    deleted = db_manager.delete_bank_question(
        bank_question_id=question_id,
        user_id=g.admin_user["admin_user_id"],
        role=g.admin_user["role"],
    )
    if not deleted:
        return jsonify({"success": False, "error": "You do not have permission to delete this question or question was not found."}), 403

    db_manager.log_audit(
        action="question_changes",
        entity_type="question",
        entity_id=question_id,
        details=f"Deleted bank question #{question_id}",
        admin_user_id=g.admin_user["admin_user_id"],
        user_name=g.admin_user["name"],
        user_role=g.admin_user["role"],
    )
    return jsonify({"success": True, "message": "Question deleted from Question Bank."})


@admin_bp.route("/admin/question-bank/<int:question_id>/duplicate", methods=["POST"])
@_admin_required()
def bank_question_duplicate(question_id: int):
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid CSRF token."}), 400

    db_manager.init_db()
    new_id = db_manager.duplicate_bank_question(
        bank_question_id=question_id,
        user_id=g.admin_user["admin_user_id"],
    )
    if not new_id:
        return jsonify({"success": False, "error": "Question could not be duplicated."}), 404

    return jsonify({
        "success": True,
        "new_question_id": new_id,
        "message": "Question duplicated successfully in Question Bank.",
    })


@admin_bp.route("/admin/question-bank/import-to-exam", methods=["POST"])
@_admin_required()
def bank_question_import_to_exam():
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid CSRF token."}), 400

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    if not payload:
        return jsonify({"success": False, "error": "No import request data received."}), 400

    raw_exam_id = payload.get("exam_id")
    try:
        exam_id = int(raw_exam_id)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Valid examination ID is required."}), 400

    raw_ids = payload.get("question_ids") or []
    if isinstance(raw_ids, str):
        try:
            raw_ids = json.loads(raw_ids)
        except Exception:
            raw_ids = [int(i.strip()) for i in raw_ids.split(",") if i.strip()]

    try:
        question_ids = [int(i) for i in raw_ids if i is not None]
    except (ValueError, TypeError):
        question_ids = []

    if not question_ids:
        return jsonify({"success": False, "error": "Please select at least one question to import."}), 400

    db_manager.init_db()
    try:
        res = db_manager.import_bank_questions_to_exam(
            question_ids=question_ids,
            exam_id=exam_id,
            user_id=g.admin_user["admin_user_id"],
            role=g.admin_user["role"],
        )
        return jsonify({
            "success": True,
            "imported_count": res["count"],
            "exam_name": res["exam_name"],
            "message": f"Successfully imported {res['count']} question(s) into '{res['exam_name']}'.",
        })
    except PermissionError as pe:
        return jsonify({"success": False, "error": str(pe)}), 403
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 404
    except Exception:
        logger.exception("Failed to import questions to exam")
        return jsonify({"success": False, "error": "Server error while importing questions."}), 500


@admin_bp.route("/admin/question-bank/bulk-delete", methods=["POST"])
@_admin_required()
def bank_question_bulk_delete():
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid CSRF token."}), 400

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    raw_ids = payload.get("question_ids") or []
    if isinstance(raw_ids, str):
        try:
            raw_ids = json.loads(raw_ids)
        except Exception:
            raw_ids = [int(i.strip()) for i in raw_ids.split(",") if i.strip()]

    deleted_count = 0
    db_manager.init_db()
    for qid in raw_ids:
        try:
            if db_manager.delete_bank_question(int(qid), g.admin_user["admin_user_id"], g.admin_user["role"]):
                deleted_count += 1
        except Exception:
            pass

    return jsonify({
        "success": True,
        "deleted_count": deleted_count,
        "message": f"Successfully deleted {deleted_count} question(s).",
    })


@admin_bp.route("/admin/students")
@_admin_required()
def students():
    db_manager.init_db()
    user_id = g.admin_user["admin_user_id"]
    role = g.admin_user["role"]

    search = request.args.get("search", "").strip() or None
    department = request.args.get("department", "").strip() or None
    semester = request.args.get("semester", "").strip() or None
    participation = request.args.get("participation", "").strip() or None
    sort_by = request.args.get("sort_by", "name_asc").strip() or "name_asc"

    # Fetch all accessible students for this user (to compute summary counts and filter options)
    all_students = db_manager.list_managed_students(user_id=user_id, role=role)

    # Filtered list
    filtered_students = db_manager.list_managed_students(
        user_id=user_id,
        role=role,
        search=search,
        department=department,
        semester=semester,
        participation=participation,
        sort_by=sort_by,
    )

    if request.headers.get("Accept") == "application/json" or request.args.get("format") == "json":
        return jsonify({
            "students": filtered_students,
            "total": len(filtered_students),
        })

    # Summary metrics across all accessible students
    attempted_students = [s for s in all_students if s["total_attempts"] > 0 and s["avg_percentage"] is not None]
    avg_perf = (
        round(sum(s["avg_percentage"] for s in attempted_students) / len(attempted_students), 1)
        if attempted_students else 0.0
    )
    summary = {
        "total_students": len(all_students),
        "active_students": sum(1 for s in all_students if s["account_status"].lower() == "active"),
        "attempted_count": len(attempted_students),
        "unattempted_count": len(all_students) - len(attempted_students),
        "average_performance": avg_perf,
    }

    # Extract unique departments and semesters for filter dropdowns
    departments = sorted(list({s["department"] for s in all_students if s.get("department")}))
    semesters = sorted(list({s["semester"] for s in all_students if s.get("semester")}))

    return render_template(
        "admin/students.html",
        page_title="Students",
        students=filtered_students,
        departments=departments,
        semesters=semesters,
        summary=summary,
        current_filter={
            "search": search or "",
            "department": department or "",
            "semester": semester or "",
            "participation": participation or "",
            "sort_by": sort_by,
        },
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/students/<int:student_id>")
@_admin_required()
def student_profile(student_id: int):
    db_manager.init_db()
    with db_manager.get_connection() as conn:
        exists = conn.execute("SELECT 1 FROM students WHERE student_id = ?", (student_id,)).fetchone()
    if not exists:
        abort(404)

    profile = db_manager.get_student_profile(
        student_id=student_id,
        user_id=g.admin_user["admin_user_id"],
        role=g.admin_user["role"],
    )
    if profile is None:
        abort(403)

    if request.headers.get("Accept") == "application/json" or request.args.get("format") == "json":
        return jsonify(profile)

    return render_template(
        "admin/student_profile.html",
        page_title=f"Student Profile - {profile['student']['name']}",
        profile=profile,
        student=profile["student"],
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/api/students/<int:student_id>/performance")
@_admin_required()
def student_performance_api(student_id: int):
    db_manager.init_db()
    with db_manager.get_connection() as conn:
        exists = conn.execute("SELECT 1 FROM students WHERE student_id = ?", (student_id,)).fetchone()
    if not exists:
        return jsonify({"error": "Student not found"}), 404

    profile = db_manager.get_student_profile(
        student_id=student_id,
        user_id=g.admin_user["admin_user_id"],
        role=g.admin_user["role"],
    )
    if profile is None:
        return jsonify({"error": "Unauthorized"}), 403

    return jsonify(profile)


@admin_bp.route("/admin/reports")
@_admin_required()
def reports():
    subject_id = request.args.get("subject_id", type=int)
    exam_id = request.args.get("exam_id", type=int)
    department = request.args.get("department", type=str)
    if department:
        department = department.strip() or None
    semester = request.args.get("semester", type=str)
    if semester:
        semester = semester.strip() or None
    academic_year = request.args.get("academic_year", type=str)
    if academic_year:
        academic_year = academic_year.strip() or None
    student_id = request.args.get("student_id", type=int)

    try:
        analytics = db_manager.get_performance_analytics(
            user_id=g.admin_user["admin_user_id"],
            role=g.admin_user["role"],
            subject_id=subject_id,
            exam_id=exam_id,
            department=department,
            semester=semester,
            academic_year=academic_year,
            student_id=student_id,
        )
    except PermissionError as pe:
        if request.is_json or request.args.get("format") == "json" or request.headers.get("Accept") == "application/json":
            return jsonify({"error": str(pe)}), 403
        flash(str(pe), "error")
        return (
            render_template(
                "admin/reports.html",
                page_title="Performance Analytics",
                error=str(pe),
                admin_user=g.admin_user,
                analytics={
                    "overview": {
                        "total_enrolled": 0,
                        "students_attempted": 0,
                        "students_evaluated": 0,
                        "unattempted_students": 0,
                        "average_marks": 0.0,
                        "avg_marks": 0.0,
                        "average_percentage": 0.0,
                        "avg_percentage": 0.0,
                        "pass_percentage": 0.0,
                        "pass_rate": 0.0,
                        "failure_percentage": 0.0,
                        "fail_rate": 0.0,
                        "highest_marks": 0.0,
                        "highest_score": 0.0,
                        "lowest_marks": 0.0,
                        "lowest_score": 0.0,
                        "total_attempts": 0,
                        "pass_count": 0,
                        "fail_count": 0,
                    },
                    "score_distribution": {},
                    "grade_distribution": {},
                    "subject_performance": [],
                    "exam_analytics": [],
                    "student_rankings": [],
                    "top_students": [],
                    "support_needed_students": [],
                    "performance_trends": [],
                    "question_accuracy": [],
                    "easiest_questions": [],
                    "most_difficult_questions": [],
                },
                filter_options={"subjects": [], "exams": [], "departments": [], "semesters": [], "academic_years": [], "students": []},
                active_filters={},
            ),
            403,
        )

    filter_options = db_manager.get_reports_filter_options(
        user_id=g.admin_user["admin_user_id"],
        role=g.admin_user["role"],
    )

    active_filters = {
        "subject_id": subject_id,
        "subject_name": next((s["name"] for s in filter_options.get("subjects", []) if s["subject_id"] == subject_id), None),
        "exam_id": exam_id,
        "exam_name": next((e["name"] for e in filter_options.get("exams", []) if e["exam_id"] == exam_id), None),
        "department": department,
        "semester": semester,
        "academic_year": academic_year,
        "student_id": student_id,
        "student_name": next((st["full_name"] for st in filter_options.get("students", []) if st["student_id"] == student_id), None),
    }

    if request.is_json or request.args.get("format") == "json" or request.headers.get("Accept") == "application/json":
        return jsonify({
            "status": "success",
            "analytics": analytics,
            "filters": active_filters,
        })

    return render_template(
        "admin/reports.html",
        page_title="Student Performance Analytics",
        analytics=analytics,
        filter_options=filter_options,
        active_filters=active_filters,
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/reports/export/<format_type>")
@_admin_required()
def export_reports(format_type: str):
    fmt = format_type.lower().strip()
    if fmt not in ("pdf", "xlsx", "csv"):
        return jsonify({"error": f"Unsupported export format: {format_type}"}), 400

    subject_id = request.args.get("subject_id", type=int)
    exam_id = request.args.get("exam_id", type=int)
    department = request.args.get("department", type=str)
    if department:
        department = department.strip() or None
    semester = request.args.get("semester", type=str)
    if semester:
        semester = semester.strip() or None
    academic_year = request.args.get("academic_year", type=str)
    if academic_year:
        academic_year = academic_year.strip() or None
    student_id = request.args.get("student_id", type=int)

    try:
        analytics = db_manager.get_performance_analytics(
            user_id=g.admin_user["admin_user_id"],
            role=g.admin_user["role"],
            subject_id=subject_id,
            exam_id=exam_id,
            department=department,
            semester=semester,
            academic_year=academic_year,
            student_id=student_id,
        )
    except PermissionError as pe:
        return jsonify({"error": str(pe)}), 403

    filter_options = db_manager.get_reports_filter_options(
        user_id=g.admin_user["admin_user_id"],
        role=g.admin_user["role"],
    )

    filter_meta = {
        "subject_id": subject_id,
        "subject_name": next((s["name"] for s in filter_options.get("subjects", []) if s["subject_id"] == subject_id), ""),
        "exam_id": exam_id,
        "exam_name": next((e["name"] for e in filter_options.get("exams", []) if e["exam_id"] == exam_id), ""),
        "department": department or "",
        "semester": semester or "",
        "academic_year": academic_year or "",
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if fmt == "pdf":
        pdf_bytes = export_pdf(analytics, filter_meta)
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="examshield_report_{timestamp}.pdf"'},
        )
    elif fmt == "xlsx":
        xlsx_bytes = export_xlsx(analytics, filter_meta)
        return Response(
            xlsx_bytes,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="examshield_report_{timestamp}.xlsx"'},
        )
    elif fmt == "csv":
        csv_str = export_csv(analytics, filter_meta)
        return Response(
            csv_str.encode("utf-8"),
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="examshield_report_{timestamp}.csv"'},
        )


@admin_bp.route("/admin/settings", methods=["GET", "POST"])
@_admin_required("Super Admin")
def settings():
    db_manager.init_db()
    error = None
    success_msg = None

    if request.method == "POST":
        if not _valid_csrf_token():
            if request.is_json:
                return jsonify({"success": False, "error": "Invalid CSRF token."}), 400
            abort(400)

        boundaries_input = []
        if request.is_json:
            data = request.get_json(silent=True) or {}
            boundaries_input = data.get("boundaries") or []
        else:
            grades = request.form.getlist("grade[]") or request.form.getlist("grade")
            min_scores = request.form.getlist("min_score[]") or request.form.getlist("min_score")
            max_scores = request.form.getlist("max_score[]") or request.form.getlist("max_score")
            descriptions = request.form.getlist("description[]") or request.form.getlist("description")
            for i in range(len(grades)):
                try:
                    boundaries_input.append({
                        "grade": grades[i].strip(),
                        "min_score": float(min_scores[i]),
                        "max_score": float(max_scores[i]),
                        "description": descriptions[i].strip() if i < len(descriptions) else "",
                        "sort_order": i + 1,
                    })
                except (ValueError, IndexError):
                    continue

        if not boundaries_input:
            error = "Invalid grade boundaries data."
        else:
            try:
                db_manager.update_grade_boundaries(boundaries_input)
                db_manager.log_audit(
                    action="examination_modification",
                    entity_type="grading",
                    entity_id=0,
                    details=f"Updated grade boundaries ({len(boundaries_input)} scale tiers configured)",
                    admin_user_id=g.admin_user["admin_user_id"],
                    user_name=g.admin_user["name"],
                    user_role=g.admin_user["role"],
                )
                success_msg = "Grading scheme updated successfully."
            except Exception as e:
                error = f"Failed to save grade boundaries: {str(e)}"

        if request.is_json:
            if error:
                return jsonify({"success": False, "error": error}), 400
            return jsonify({
                "success": True,
                "message": success_msg,
                "boundaries": db_manager.get_grade_boundaries(),
            })

    boundaries = db_manager.get_grade_boundaries()
    return render_template(
        "admin/settings.html",
        page_title="Settings",
        role=g.admin_user["role"],
        boundaries=boundaries,
        error=error,
        success_msg=success_msg,
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/api/settings/grading", methods=["GET", "POST"])
@_admin_required("Super Admin")
def api_settings_grading():
    db_manager.init_db()
    if request.method == "POST":
        if not _valid_csrf_token():
            return jsonify({"success": False, "error": "Invalid CSRF token."}), 400
        data = request.get_json(silent=True) or {}
        boundaries = data.get("boundaries")
        if not boundaries or not isinstance(boundaries, list):
            return jsonify({"success": False, "error": "Boundaries list is required."}), 400
        try:
            db_manager.update_grade_boundaries(boundaries)
            db_manager.log_audit(
                action="examination_modification",
                entity_type="grading",
                entity_id=0,
                details=f"Updated grading scheme via API ({len(boundaries)} boundaries)",
                admin_user_id=g.admin_user["admin_user_id"],
                user_name=g.admin_user["name"],
                user_role=g.admin_user["role"],
            )
            return jsonify({"success": True, "boundaries": db_manager.get_grade_boundaries()})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 400

    return jsonify({"success": True, "boundaries": db_manager.get_grade_boundaries()})


@admin_bp.route("/admin/evaluations")
@_admin_required()
def evaluations_list():
    db_manager.init_db()
    uid = g.admin_user["admin_user_id"]
    role = g.admin_user["role"]

    raw_exam_id = request.args.get("exam_id")
    try:
        exam_id = int(raw_exam_id) if raw_exam_id else None
    except (ValueError, TypeError):
        exam_id = None

    status_filter = request.args.get("status", "all").strip().lower()
    search = request.args.get("search", "").strip() or None

    evaluations = db_manager.list_evaluations(
        user_id=uid,
        role=role,
        exam_id=exam_id,
        status=status_filter if status_filter != "all" else None,
        search=search,
    )

    filter_options = db_manager.get_reports_filter_options(user_id=uid, role=role)

    all_evals = db_manager.list_evaluations(user_id=uid, role=role, exam_id=exam_id)
    counts = {
        "all": len(all_evals),
        "pending": sum(1 for e in all_evals if e.get("evaluation_status") == "pending_evaluation"),
        "evaluated": sum(1 for e in all_evals if e.get("evaluation_status") == "evaluated"),
        "published": sum(1 for e in all_evals if e.get("evaluation_status") == "published"),
    }

    if request.is_json or request.headers.get("Accept") == "application/json" or request.args.get("format") == "json":
        return jsonify({
            "status": "success",
            "evaluations": evaluations,
            "counts": counts,
        })

    return render_template(
        "admin/evaluations.html",
        page_title="Subjective Evaluation",
        evaluations=evaluations,
        filter_options=filter_options,
        active_exam_id=exam_id,
        active_status=status_filter,
        search=search or "",
        counts=counts,
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/evaluations/attempt/<int:attempt_id>")
@_admin_required()
def evaluate_attempt(attempt_id: int):
    db_manager.init_db()
    detail = db_manager.get_attempt_evaluation_detail(attempt_id, user_id=g.admin_user["admin_user_id"], role=g.admin_user["role"])
    if not detail:
        abort(404)

    exam_id = detail["attempt"]["exam_id"]
    if not _has_exam_permission(g.admin_user, exam_id):
        abort(403)

    grade_boundaries = db_manager.get_grade_boundaries()

    if request.is_json:
        return jsonify({"status": "success", "attempt_detail": detail})

    return render_template(
        "admin/evaluate_attempt.html",
        page_title=f"Evaluate Attempt #{attempt_id} - {detail['student']['name']}",
        attempt=detail["attempt"],
        exam=detail["exam"],
        student=detail["student"],
        result=detail["result"],
        subjective_questions=detail["subjective_questions"],
        objective_questions=detail["objective_questions"],
        grade_boundaries=grade_boundaries,
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/evaluations/attempt/<int:attempt_id>/save", methods=["POST"])
@_admin_required()
def save_attempt_evaluation(attempt_id: int):
    if not _valid_csrf_token():
        if request.is_json:
            return jsonify({"success": False, "error": "Invalid CSRF token."}), 400
        abort(400)

    db_manager.init_db()
    detail = db_manager.get_attempt_evaluation_detail(attempt_id, user_id=g.admin_user["admin_user_id"], role=g.admin_user["role"])
    if not detail:
        abort(404)

    exam_id = detail["attempt"]["exam_id"]
    if not _has_exam_permission(g.admin_user, exam_id):
        abort(403)

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        evaluations = payload.get("evaluations", [])
        publish = bool(payload.get("publish", False))
    else:
        publish = request.form.get("publish") in ("true", "1", "yes")
        evaluations = []
        for q in detail["subjective_questions"]:
            qid = q["question_id"]
            marks_raw = request.form.get(f"marks_{qid}")
            feedback = request.form.get(f"feedback_{qid}", "").strip()
            if marks_raw is not None and marks_raw != "":
                try:
                    marks_val = float(marks_raw)
                    evaluations.append({
                        "question_id": qid,
                        "marks_awarded": max(0.0, min(marks_val, float(q["marks"] or 1.0))),
                        "feedback": feedback,
                    })
                except ValueError:
                    pass

    uid = g.admin_user["admin_user_id"]
    updated_attempt = db_manager.save_evaluation(
        attempt_id=attempt_id,
        evaluations=evaluations,
        evaluator_id=uid,
        publish=publish,
    )

    msg = "Evaluation published successfully." if publish else "Evaluation saved as draft."
    if request.is_json:
        return jsonify({
            "success": True,
            "message": msg,
            "attempt": updated_attempt,
        })

    flash(msg, "success")
    return redirect(url_for("admin.evaluate_attempt", attempt_id=attempt_id))


@admin_bp.route("/admin/evaluations/attempt/<int:attempt_id>/publish", methods=["POST"])
@_admin_required()
def publish_attempt_evaluation(attempt_id: int):
    if not _valid_csrf_token():
        if request.is_json:
            return jsonify({"success": False, "error": "Invalid CSRF token."}), 400
        abort(400)

    db_manager.init_db()
    detail = db_manager.get_attempt_evaluation_detail(attempt_id, user_id=g.admin_user["admin_user_id"], role=g.admin_user["role"])
    if not detail:
        abort(404)

    exam_id = detail["attempt"]["exam_id"]
    if not _has_exam_permission(g.admin_user, exam_id):
        abort(403)

    uid = g.admin_user["admin_user_id"]
    updated_attempt = db_manager.publish_attempt_result(attempt_id, admin_user_id=uid)

    if request.is_json:
        return jsonify({
            "success": True,
            "message": "Results published successfully.",
            "attempt": updated_attempt,
        })

    flash("Results published successfully to the candidate.", "success")
    return redirect(url_for("admin.evaluate_attempt", attempt_id=attempt_id))


@admin_bp.route("/admin/evaluations/exam/<int:exam_id>/publish-all", methods=["POST"])
@_admin_required()
@_exam_permission_required()
def publish_all_exam_evaluations(exam_id: int):
    if not _valid_csrf_token():
        if request.is_json:
            return jsonify({"success": False, "error": "Invalid CSRF token."}), 400
        abort(400)

    db_manager.init_db()
    uid = g.admin_user["admin_user_id"]
    count = db_manager.publish_exam_results(exam_id, admin_user_id=uid)

    if request.is_json:
        return jsonify({
            "success": True,
            "count": count,
            "message": f"Published results for {count} candidate(s).",
        })

    flash(f"Published results for {count} candidate(s).", "success")
    return redirect(url_for("admin.evaluations_list", exam_id=exam_id))


@admin_bp.route("/admin/audit-logs")
@_admin_required()
def audit_logs_view():
    db_manager.init_db()
    uid = g.admin_user["admin_user_id"]
    role = g.admin_user["role"]

    action = request.args.get("action", "").strip() or None
    entity_type = request.args.get("entity_type", "").strip() or None
    search = request.args.get("search", "").strip() or None
    raw_exam_id = request.args.get("exam_id")
    try:
        exam_id = int(raw_exam_id) if raw_exam_id else None
    except (ValueError, TypeError):
        exam_id = None

    logs = db_manager.list_audit_logs(
        user_id=uid,
        role=role,
        action=action,
        entity_type=entity_type,
        exam_id=exam_id,
        search=search,
        limit=200,
    )

    filter_options = db_manager.get_reports_filter_options(user_id=uid, role=role)

    if request.is_json:
        return jsonify({
            "status": "success",
            "logs": logs,
            "count": len(logs),
        })

    return render_template(
        "admin/audit_logs.html",
        page_title="Audit Logs",
        logs=logs,
        filter_options=filter_options,
        active_action=action or "",
        active_entity_type=entity_type or "",
        active_exam_id=exam_id,
        search=search or "",
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/api/audit-logs")
@_admin_required()
def api_audit_logs():
    db_manager.init_db()
    uid = g.admin_user["admin_user_id"]
    role = g.admin_user["role"]

    action = request.args.get("action", "").strip() or None
    entity_type = request.args.get("entity_type", "").strip() or None
    search = request.args.get("search", "").strip() or None
    raw_exam_id = request.args.get("exam_id")
    try:
        exam_id = int(raw_exam_id) if raw_exam_id else None
    except (ValueError, TypeError):
        exam_id = None

    logs = db_manager.list_audit_logs(
        user_id=uid,
        role=role,
        action=action,
        entity_type=entity_type,
        exam_id=exam_id,
        search=search,
        limit=200,
    )
    return jsonify({
        "status": "success",
        "logs": logs,
        "count": len(logs),
    })


@admin_bp.route("/admin/audit-logs/export/csv")
@_admin_required()
def export_audit_logs_csv():
    db_manager.init_db()
    uid = g.admin_user["admin_user_id"]
    role = g.admin_user["role"]

    action = request.args.get("action", "").strip() or None
    entity_type = request.args.get("entity_type", "").strip() or None
    search = request.args.get("search", "").strip() or None
    raw_exam_id = request.args.get("exam_id")
    try:
        exam_id = int(raw_exam_id) if raw_exam_id else None
    except (ValueError, TypeError):
        exam_id = None

    logs = db_manager.list_audit_logs(
        user_id=uid,
        role=role,
        action=action,
        entity_type=entity_type,
        exam_id=exam_id,
        search=search,
        limit=1000,
    )

    import io
    import csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Log ID", "Timestamp", "Action", "User Name", "User Role", "Admin User ID", "Entity Type", "Entity ID", "Exam ID", "Details"])
    for l in logs:
        writer.writerow([
            l.get("log_id"),
            l.get("timestamp"),
            l.get("action"),
            l.get("user_name"),
            l.get("user_role"),
            l.get("admin_user_id"),
            l.get("entity_type"),
            l.get("entity_id"),
            l.get("exam_id"),
            l.get("details"),
        ])

    csv_data = output.getvalue()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        csv_data.encode("utf-8"),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit_logs_{timestamp}.csv"'},
    )


@admin_bp.route("/admin/users", methods=["GET", "POST"])
@_admin_required("Super Admin")
def users():
    db_manager.init_db()
    error = None
    created = False
    with db_manager.get_connection() as conn:
        exams = conn.execute(
            "SELECT exam_id, name FROM exams ORDER BY name"
        ).fetchall()

    if request.method == "POST":
        if not _valid_csrf_token():
            abort(400)
        email = _normalise(request.form.get("email"))
        name = request.form.get("name", "").strip()
        role = request.form.get("role", "")
        password = request.form.get("password", "")
        raw_exam_ids = request.form.getlist("exam_ids")
        try:
            exam_ids = [int(value) for value in raw_exam_ids]
        except ValueError:
            exam_ids = []
            error = "Select valid examinations."

        if error is None and (not email or "@" not in email or not name):
            error = "Enter a valid email address and full name."
        elif error is None and role not in ADMIN_ROLES:
            error = "Select a valid account role."
        elif error is None and len(password) < 12:
            error = "Administrative passwords must be at least 12 characters."
        elif error is None and role == "Super Admin" and exam_ids:
            error = "Examination assignments apply only to teacher accounts."

        new_uid = None
        if error is None:
            try:
                with db_manager.get_connection() as conn:
                    existing = conn.execute(
                        "SELECT admin_user_id FROM admin_users WHERE email = ?",
                        (email,),
                    ).fetchone()
                    existing_exams = {
                        row["exam_id"] for row in conn.execute(
                            "SELECT exam_id FROM exams WHERE exam_id IN ({})".format(
                                ",".join("?" for _ in exam_ids) or "NULL"
                            ),
                            exam_ids,
                        ).fetchall()
                    } if exam_ids else set()
                    if existing:
                        error = "An administrative account with that email already exists."
                    elif len(existing_exams) != len(set(exam_ids)):
                        error = "One or more selected examinations are unavailable."
                    else:
                        cursor = conn.execute(
                            """
                            INSERT INTO admin_users (email, name, password_hash, role)
                            VALUES (?, ?, ?, ?)
                            """,
                            (email, name, generate_password_hash(password), role),
                        )
                        new_uid = int(cursor.lastrowid)
                        if role == "Teacher":
                            conn.executemany(
                                """
                                INSERT INTO teacher_exam_permissions (admin_user_id, exam_id)
                                VALUES (?, ?)
                                """,
                                [(new_uid, exam_id) for exam_id in sorted(set(exam_ids))],
                            )
                        created = True
            except Exception:
                logger.exception("Administrative account creation failed")
                error = "The account could not be created. Please try again."

            if created and new_uid:
                try:
                    db_manager.log_audit(
                        action="teacher_account_changes",
                        entity_type="teacher" if role == "Teacher" else "admin",
                        entity_id=new_uid,
                        details=f"Created {role} account for {name} ({email}) with {len(exam_ids)} exam assignment(s)",
                        admin_user_id=g.admin_user["admin_user_id"],
                        user_name=g.admin_user["name"],
                        user_role=g.admin_user["role"],
                    )
                except Exception:
                    pass

    with db_manager.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT u.admin_user_id, u.email, u.name, u.role, u.active,
                   COUNT(p.exam_id) AS assigned_exams
            FROM admin_users u
            LEFT JOIN teacher_exam_permissions p ON p.admin_user_id = u.admin_user_id
            GROUP BY u.admin_user_id ORDER BY u.name
            """
        ).fetchall()
    return render_template(
        "admin/users.html",
        page_title="Manage users",
        users=[dict(row) for row in rows],
        exams=[dict(row) for row in exams],
        error=error,
        created=created,
        admin_user=g.admin_user,
    )


# =============================================================================
# SMART QUESTION COMPOSER & QUESTION PAPER IMPORT SYSTEM ROUTES
# =============================================================================

@admin_bp.route("/admin/question-composer")
@_admin_required()
def question_composer():
    """Renders the advanced 3-panel Smart Question Composer interface."""
    db_manager.init_db()
    uid = g.admin_user["admin_user_id"]
    role = g.admin_user["role"]

    subjects = db_manager.list_subjects()
    drafts = db_manager.list_composer_drafts(user_id=uid, role=role)
    ai_status = get_ai_status()

    return render_template(
        "admin/question_composer.html",
        page_title="Smart Question Composer",
        active_tab="question_composer",
        subjects=subjects,
        drafts=drafts,
        ai_status=ai_status,
        admin_user=g.admin_user,
    )


@admin_bp.route("/admin/api/composer/parse-text", methods=["POST"])
@_admin_required()
def composer_parse_text():
    """Parses raw question text pasted by the teacher into structured questions."""
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid or expired CSRF token."}), 400

    payload = request.get_json(silent=True) or {}
    raw_text = str(payload.get("text", "")).strip()
    if not raw_text:
        return jsonify({"success": False, "error": "Question text cannot be empty."}), 400

    subject_name = str(payload.get("subject", "General")).strip()
    questions = parse_bulk_text(raw_text, default_subject=subject_name)

    return jsonify({
        "success": True,
        "questions": questions,
        "count": len(questions),
        "message": f"Successfully parsed {len(questions)} question(s).",
    })


@admin_bp.route("/admin/api/composer/upload-document", methods=["POST"])
@_admin_required()
def composer_upload_document():
    """Uploads an existing question paper or study material (PDF, DOCX, TXT, Image) and extracts questions."""
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid or expired CSRF token."}), 400

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"success": False, "error": "Please select a valid document to upload."}), 400

    filename = secure_filename(file.filename)
    if not is_allowed_document(filename):
        return jsonify({
            "success": False,
            "error": "Unsupported file format. Supported formats: PDF, DOCX, TXT, PNG, JPG.",
        }), 400

    file_bytes = file.read()
    if len(file_bytes) > 25 * 1024 * 1024:
        return jsonify({"success": False, "error": "File size exceeds 25 MB limit."}), 400

    mode = request.form.get("mode", "exam_paper")  # 'exam_paper' or 'study_material'
    subject = request.form.get("subject", "General")
    topic = request.form.get("topic", "General")

    try:
        raw_text, meta = extract_document_text(file_bytes, filename)
    except Exception as exc:
        logger.exception("Text extraction failed for %s", filename)
        return jsonify({"success": False, "error": f"Failed to extract text from document: {str(exc)}"}), 400

    if not raw_text or not raw_text.strip():
        return jsonify({
            "success": False,
            "error": "No readable text could be extracted from this document. If this is a scanned PDF or image, ensure text is legible or OCR is available.",
        }), 400

    if mode == "study_material":
        try:
            num_q = int(request.form.get("num_questions", 5))
        except (ValueError, TypeError):
            num_q = 5
        difficulty = request.form.get("difficulty", "Medium")
        q_types_raw = request.form.getlist("question_types") or ["mcq", "short_answer", "true_false"]
        questions = generate_questions_from_study_material(
            material_text=raw_text,
            subject=subject,
            topic=topic,
            num_questions=num_q,
            difficulty=difficulty,
            question_types=q_types_raw,
        )
    else:
        questions = extract_questions_with_ai(raw_text, default_subject=subject)

    return jsonify({
        "success": True,
        "questions": questions,
        "count": len(questions),
        "raw_text_sample": raw_text[:2000],
        "meta": meta,
        "message": f"Successfully extracted {len(questions)} question(s) from {filename}.",
    })


@admin_bp.route("/admin/api/composer/generate-from-material", methods=["POST"])
@_admin_required()
def composer_generate_from_material():
    """Generates grounded questions from provided study material text."""
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid or expired CSRF token."}), 400

    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", "")).strip()
    if not text:
        return jsonify({"success": False, "error": "Study material text cannot be empty."}), 400

    subject = str(payload.get("subject", "General")).strip()
    topic = str(payload.get("topic", "Key Concepts")).strip()
    try:
        num_questions = int(payload.get("num_questions", 5))
    except (ValueError, TypeError):
        num_questions = 5
    num_questions = max(1, min(num_questions, 50))
    difficulty = str(payload.get("difficulty", "Medium")).strip()
    question_types = payload.get("question_types") or ["mcq", "short_answer", "true_false"]
    try:
        marks_per_q = float(payload.get("marks_per_question", 2.0))
    except (ValueError, TypeError):
        marks_per_q = 2.0

    questions = generate_questions_from_study_material(
        material_text=text,
        subject=subject,
        topic=topic,
        num_questions=num_questions,
        difficulty=difficulty,
        question_types=question_types,
        marks_per_question=marks_per_q,
    )

    return jsonify({
        "success": True,
        "questions": questions,
        "count": len(questions),
        "message": f"Generated {len(questions)} grounded questions.",
    })


@admin_bp.route("/admin/api/composer/draft/save", methods=["POST"])
@_admin_required()
def composer_save_draft():
    """Auto-saves or manually saves the teacher's active question composer session."""
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid or expired CSRF token."}), 400

    payload = request.get_json(silent=True) or {}
    draft_id = payload.get("draft_id")
    title = str(payload.get("title", "Untitled Draft")).strip() or "Untitled Draft"
    subject_id = payload.get("subject_id")
    try:
        subject_id = int(subject_id) if subject_id else None
    except (ValueError, TypeError):
        subject_id = None

    sections = payload.get("sections") or []
    questions = payload.get("questions") or []
    metadata = payload.get("metadata") or {}

    uid = g.admin_user["admin_user_id"]
    try:
        saved_id = db_manager.save_composer_draft(
            user_id=uid,
            title=title,
            subject_id=subject_id,
            sections=sections,
            questions=questions,
            metadata=metadata,
            draft_id=int(draft_id) if draft_id else None,
        )
        return jsonify({
            "success": True,
            "draft_id": saved_id,
            "message": "Draft saved successfully.",
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        })
    except Exception as exc:
        logger.exception("Failed to save composer draft")
        return jsonify({"success": False, "error": str(exc)}), 500


@admin_bp.route("/admin/api/composer/drafts")
@_admin_required()
def composer_list_drafts():
    """Lists saved composer drafts for the current teacher/admin."""
    uid = g.admin_user["admin_user_id"]
    role = g.admin_user["role"]
    drafts = db_manager.list_composer_drafts(user_id=uid, role=role)
    return jsonify({"success": True, "drafts": drafts})


@admin_bp.route("/admin/api/composer/draft/<int:draft_id>")
@_admin_required()
def composer_get_draft(draft_id: int):
    """Loads a specific composer draft."""
    uid = g.admin_user["admin_user_id"]
    role = g.admin_user["role"]
    draft = db_manager.get_composer_draft(draft_id, user_id=uid, role=role)
    if not draft:
        return jsonify({"success": False, "error": "Draft not found or unauthorized."}), 404
    return jsonify({"success": True, "draft": draft})


@admin_bp.route("/admin/api/composer/draft/<int:draft_id>", methods=["DELETE"])
@_admin_required()
def composer_delete_draft(draft_id: int):
    """Deletes a saved composer draft."""
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid CSRF token."}), 400
    uid = g.admin_user["admin_user_id"]
    role = g.admin_user["role"]
    deleted = db_manager.delete_composer_draft(draft_id, user_id=uid, role=role)
    return jsonify({"success": deleted, "message": "Draft deleted." if deleted else "Draft not found."})


@admin_bp.route("/admin/api/composer/export-to-bank", methods=["POST"])
@_admin_required()
def composer_export_to_bank():
    """Saves approved questions from the composer directly into the Question Bank."""
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid or expired CSRF token."}), 400

    payload = request.get_json(silent=True) or {}
    questions = payload.get("questions") or []
    if not questions:
        return jsonify({"success": False, "error": "No questions selected for Question Bank export."}), 400

    raw_subject_id = payload.get("subject_id")
    try:
        subject_id = int(raw_subject_id) if raw_subject_id else None
    except (ValueError, TypeError):
        subject_id = None

    if not subject_id:
        first_sub = db_manager.list_subjects()
        subject_id = first_sub[0]["subject_id"] if first_sub else 1

    default_topic = str(payload.get("default_topic", "General")).strip() or "General"
    uid = g.admin_user["admin_user_id"]

    try:
        result = db_manager.bulk_save_to_question_bank(
            questions=questions,
            user_id=uid,
            subject_id=subject_id,
            default_topic=default_topic,
        )
        saved_count = result["saved_count"]

        # Audit log
        try:
            db_manager.log_audit(
                action="question_changes",
                entity_type="question_bank",
                entity_id=subject_id,
                details=f"Exported {saved_count} questions from Smart Composer to Question Bank (Subject ID: {subject_id})",
                admin_user_id=uid,
                user_name=g.admin_user["name"],
                user_role=g.admin_user["role"],
            )
        except Exception:
            pass

        return jsonify({
            "success": True,
            "saved_count": saved_count,
            "message": f"Successfully exported {saved_count} question(s) to the Question Bank.",
        })
    except Exception as exc:
        logger.exception("Failed to export to question bank")
        return jsonify({"success": False, "error": str(exc)}), 500


@admin_bp.route("/admin/api/composer/create-exam", methods=["POST"])
@_admin_required()
def composer_create_exam():
    """Builds and publishes/schedules a complete examination directly from the Composer."""
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid or expired CSRF token."}), 400

    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    if not title:
        return jsonify({"success": False, "error": "Examination title is required."}), 400

    questions = data.get("questions") or []
    sections = data.get("sections") or []
    status = str(data.get("status", "draft")).strip().lower()
    if status not in {"draft", "published", "scheduled"}:
        status = "draft"

    if status in ("published", "scheduled") and not questions:
        return jsonify({"success": False, "error": "At least one question is required to publish or schedule an examination."}), 400

    raw_subject_id = data.get("subject_id")
    try:
        subject_id = int(raw_subject_id) if raw_subject_id else None
    except (ValueError, TypeError):
        subject_id = None

    try:
        duration_minutes = int(data.get("duration_minutes", 60))
    except (ValueError, TypeError):
        duration_minutes = 60

    try:
        pass_marks = float(data.get("pass_marks", 0.0))
    except (ValueError, TypeError):
        pass_marks = 0.0

    try:
        negative_marking = float(data.get("negative_marking", 0.0))
    except (ValueError, TypeError):
        negative_marking = 0.0

    start_time = data.get("start_time") or None
    end_time = data.get("end_time") or None
    if status == "scheduled" and not start_time:
        return jsonify({"success": False, "error": "Start date and time are required for scheduled examinations."}), 400

    uid = g.admin_user["admin_user_id"]

    try:
        exam_id = db_manager.create_examination_with_questions(
            name=title,
            subject_id=subject_id,
            duration_minutes=duration_minutes,
            pass_marks=pass_marks,
            negative_marking=negative_marking,
            instructions=str(data.get("instructions", "")).strip(),
            department=str(data.get("department", "")).strip(),
            semester=str(data.get("semester", "")).strip(),
            academic_year=str(data.get("academic_year", "")).strip(),
            start_time=start_time,
            end_time=end_time,
            randomize_questions=bool(data.get("randomize_questions", False)),
            randomize_options=bool(data.get("randomize_options", False)),
            allow_multiple_attempts=bool(data.get("allow_multiple_attempts", False)),
            status=status,
            created_by=uid,
            questions=questions,
            sections=sections,
        )

        # Audit log
        try:
            db_manager.log_audit(
                action="examination_creation",
                entity_type="exam",
                entity_id=exam_id,
                details=f"Created examination '{title}' ({status}) from Smart Composer with {len(questions)} questions in {len(sections)} sections",
                admin_user_id=uid,
                user_name=g.admin_user["name"],
                user_role=g.admin_user["role"],
            )
        except Exception:
            pass

        return jsonify({
            "success": True,
            "exam_id": exam_id,
            "message": f"Examination '{title}' created successfully.",
            "redirect_url": url_for("admin.exams"),
        })
    except Exception as exc:
        logger.exception("Failed to create exam from composer")
        return jsonify({"success": False, "error": str(exc)}), 500


@admin_bp.route("/admin/api/composer/upload-image", methods=["POST"])
@_admin_required()
def composer_upload_image():
    """Uploads an image to embed in rich-text questions or options."""
    if not _valid_csrf_token():
        return jsonify({"success": False, "error": "Invalid CSRF token."}), 400

    image_file = request.files.get("image")
    if not image_file or not image_file.filename:
        return jsonify({"success": False, "error": "No image file provided."}), 400

    filename = secure_filename(image_file.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        return jsonify({"success": False, "error": "Allowed image formats: PNG, JPG, JPEG, GIF, WEBP, SVG."}), 400

    from pathlib import Path
    import secrets

    upload_dir = Path(__file__).resolve().parent.parent.parent / "frontend" / "static" / "uploads" / "composer_images"
    upload_dir.mkdir(parents=True, exist_ok=True)

    unique_name = f"qimg_{secrets.token_hex(8)}{ext}"
    dest_path = upload_dir / unique_name
    image_file.save(dest_path)

    img_link = f"/static/uploads/composer_images/{unique_name}"
    return jsonify({
        "success": True,
        "image_url": img_link,
        "url": img_link,
        "message": "Image uploaded successfully.",
    })
