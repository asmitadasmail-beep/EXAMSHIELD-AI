import logging

from flask import Blueprint, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

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
            return render_template("login.html", page_title="Login", error="All fields are required.", form_data=form_data)
        try:
            db_manager.init_db()
            student_id = db_manager.add_student(
                form_data["name"], form_data["usn"], form_data["subject"], form_data["exam_code"]
            )
        except Exception:
            logger.exception("Login database operation failed")
            return render_template("login.html", page_title="Login", error="The database is unavailable. Please try again.", form_data=form_data)
        session.update(
            student_id=student_id,
            student_name=form_data["name"],
            student_usn=form_data["usn"],
            student_subject=form_data["subject"],
            student_exam_code=form_data["exam_code"],
        )
        return redirect(url_for("dashboard"))
    return render_template(
        "login.html",
        page_title="Login",
        form_data={"name": "", "usn": "", "subject": "", "exam_code": ""},
    )


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    form_data = {"name": "", "usn": "", "email": ""}
    error = None
    if request.method == "POST":
        form_data = {key: _clean_form_value(key) for key in ("name", "usn", "email")}
        password = request.form.get("password", "")
        confirmation = request.form.get("confirm_password", "")
        if not all(form_data.values()) or not password:
            error = "All fields are required."
        elif password != confirmation:
            error = "Passwords do not match."
        else:
            try:
                db_manager.init_db()
                with db_manager.get_connection() as connection:
                    existing = connection.execute(
                        "SELECT student_id FROM students WHERE usn = ? OR email = ?",
                        (form_data["usn"], form_data["email"]),
                    ).fetchone()
                    if existing:
                        error = "That student ID or email is already registered."
                    else:
                        cursor = connection.execute(
                            """
                            INSERT INTO students (name, usn, email, password_hash, subject, exam_code)
                            VALUES (?, ?, ?, ?, '', '')
                            """,
                            (form_data["name"], form_data["usn"], form_data["email"], generate_password_hash(password)),
                        )
                        session["student_id"] = int(cursor.lastrowid)
                        session["student_name"] = form_data["name"]
                        session["student_usn"] = form_data["usn"]
                        return redirect(url_for("learning.subjects"))
            except Exception:
                logger.exception("Registration database operation failed")
                error = "Registration is temporarily unavailable."
    return render_template("register.html", page_title="Register", form_data=form_data, error=error)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/account-login", methods=["GET", "POST"])
def account_login():
    error = None
    if request.method == "POST":
        email = _clean_form_value("email")
        password = request.form.get("password", "")
        try:
            db_manager.init_db()
            with db_manager.get_connection() as connection:
                student = connection.execute(
                    "SELECT student_id, name, usn, password_hash FROM students WHERE email = ?",
                    (email,),
                ).fetchone()
            if student is None or not student["password_hash"] or not check_password_hash(student["password_hash"], password):
                error = "Invalid email or password."
            else:
                session.update(
                    student_id=student["student_id"],
                    student_name=student["name"],
                    student_usn=student["usn"],
                )
                return redirect(url_for("learning.subjects"))
        except Exception:
            logger.exception("Account login failed")
            error = "Login is temporarily unavailable."
    return render_template("account_login.html", page_title="Account login", error=error)
