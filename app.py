import os

from flask import Flask, render_template, session

from ai_modules.webcam import get_camera_status_label
import config
from backend.routes.auth_routes import auth_bp
from backend.routes.exam_routes import exam_bp


app = Flask(
    __name__,
    template_folder="frontend/templates",
    static_folder="frontend/static",
)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
app.config.from_object(config)
app.register_blueprint(auth_bp)
app.register_blueprint(exam_bp)


def render_placeholder(title: str, message: str):
    return render_template(
        "base.html",
        page_title=title,
        heading=title,
        message=message,
    )


@app.route("/")
def index():
    return render_placeholder(
        "Home",
        "Offline Flask scaffold for the AI-powered exam proctoring project.",
    )


@app.route("/dashboard")
def dashboard():
    student_info = {
        "student_id": session.get("student_id", "Not started"),
        "student_name": session.get("student_name", "Not started"),
        "student_usn": session.get("student_usn", "Not started"),
        "student_subject": session.get("student_subject", "Not started"),
        "student_exam_code": session.get("student_exam_code", "Not started"),
    }
    return render_template(
        "dashboard.html",
        page_title="Dashboard",
        student_info=student_info,
        warnings_count=session.get("warnings_count", 0),
        camera_status=get_camera_status_label(),
        ai_status="Monitoring",
    )


@app.route("/reports")
def reports():
    return render_placeholder("Reports", "Coming soon")


@app.route("/about")
def about():
    return render_placeholder("About", "Coming soon")


@app.route("/settings")
def settings():
    return render_placeholder("Settings", "Coming soon")


if __name__ == "__main__":
    app.run(debug=True)