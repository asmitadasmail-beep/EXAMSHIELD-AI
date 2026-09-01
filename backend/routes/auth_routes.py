import logging

from flask import Blueprint, redirect, render_template, request, session, url_for

from backend.database.db_manager import DatabaseManager


auth_bp = Blueprint("auth", __name__)
db_manager = DatabaseManager()
logger = logging.getLogger(__name__)


def _clean_form_value(key: str) -> str:
    return request.form.get(key, "").strip()


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        form_data = {
            "name": _clean_form_value("name"),
            "usn": _clean_form_value("usn"),
            "subject": _clean_form_value("subject"),
            "exam_code": _clean_form_value("exam_code"),
        }

        if not all(form_data.values()):
            return render_template(
                "login.html",
                page_title="Login",
                error="All fields are required.",
                form_data=form_data,
            )

        try:
            db_manager.init_db()
            student_id = db_manager.add_student(
                form_data["name"],
                form_data["usn"],
                form_data["subject"],
                form_data["exam_code"],
            )
        except Exception:
            logger.exception("Login database operation failed")
            return render_template(
                "login.html",
                page_title="Login",
                error="The database is unavailable. Please try again.",
                form_data=form_data,
            )

        session["student_id"] = student_id
        session["student_name"] = form_data["name"]
        session["student_usn"] = form_data["usn"]
        session["student_subject"] = form_data["subject"]
        session["student_exam_code"] = form_data["exam_code"]

        return redirect(url_for("dashboard"))

    return render_template(
        "login.html",
        page_title="Login",
        form_data={
            "name": "",
            "usn": "",
            "subject": "",
            "exam_code": "",
        },
    )