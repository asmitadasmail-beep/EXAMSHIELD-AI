import os
from datetime import timedelta

import click
from flask import Flask, render_template, session
from werkzeug.security import generate_password_hash

import config
from backend.database.db_manager import DatabaseManager
from backend.routes.admin_routes import admin_bp
from backend.routes.auth_routes import auth_bp
from backend.routes.exam_routes import exam_bp
from backend.routes.report_routes import report_bp
from backend.routes.settings_routes import settings_bp
from backend.routes.learning_routes import learning_bp


app = Flask(
    __name__,
    template_folder="frontend/templates",
    static_folder="frontend/static",
)
app.secret_key = os.getenv("SECRET_KEY") or os.urandom(32)
app.config.from_object(config)
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "").lower() == "true",
)
app.register_blueprint(admin_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(exam_bp)
app.register_blueprint(report_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(learning_bp)


@app.cli.command("create-super-admin")
def create_super_admin():
    """Create the initial Super Admin account using interactive prompts."""
    email = click.prompt("Super Admin email").strip().lower()
    name = click.prompt("Full name").strip()
    password = click.prompt(
        "Password (minimum 12 characters)",
        hide_input=True,
        confirmation_prompt=True,
    )
    if not email or "@" not in email or not name:
        raise click.ClickException("Enter a valid email address and full name.")
    if len(password) < 12:
        raise click.ClickException("Administrative passwords must be at least 12 characters.")

    database = DatabaseManager()
    database.init_db()
    with database.get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT admin_user_id FROM admin_users WHERE role = 'Super Admin' AND active = 1"
        ).fetchone()
        if existing:
            raise click.ClickException("An active Super Admin already exists.")
        connection.execute(
            """
            INSERT INTO admin_users (email, name, password_hash, role)
            VALUES (?, ?, ?, 'Super Admin')
            """,
            (email, name, generate_password_hash(password)),
        )
    click.echo("Super Admin account created.")


def render_placeholder(title: str, message: str):
    return render_template(
        "base.html",
        page_title=title,
        heading=title,
        message=message,
    )


@app.route("/")
def index():
    from backend.database.db_manager import DatabaseManager
    database = DatabaseManager()
    database.init_db()
    return render_template("home.html", page_title="Practice smarter", subjects=database.list_subjects())


@app.route("/dashboard")
def dashboard():
    database = DatabaseManager()
    database.init_db()

    student_id = session.get("student_id")
    student_record = None
    if student_id:
        try:
            student_record = database.get_student_by_id(int(student_id))
        except Exception:
            student_record = None

    student_name = (
        student_record["name"]
        if student_record
        else session.get("student_name", "Student")
    )
    student_usn = (
        student_record["usn"]
        if student_record
        else session.get("student_usn", "Not registered")
    )
    department = (
        student_record["department"]
        if student_record and student_record.get("department")
        else "Computer Science & Engineering"
    )
    semester = (
        student_record["semester"]
        if student_record and student_record.get("semester")
        else "Semester 5"
    )

    student_info = {
        "student_id": student_id or "Not started",
        "student_name": student_name,
        "student_usn": student_usn,
        "department": department,
        "semester": semester,
    }

    available_exams = []
    completed_exams = []
    if student_id:
        try:
            available_exams = database.list_available_exams_for_student(int(student_id))
            completed_exams = database.list_completed_exams_for_student(int(student_id))
        except Exception:
            pass

    return render_template(
        "dashboard.html",
        page_title="Dashboard",
        student_info=student_info,
        available_exams=available_exams,
        completed_exams=completed_exams,
        warnings_count=session.get("warnings_count", 0),
        camera_status="Camera Off",
        ai_status="Monitoring",
    )


@app.route("/about")
def about():
    return render_template("about.html", page_title="About")


if __name__ == "__main__":
    app.run(debug=True)