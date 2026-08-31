from __future__ import annotations

from pathlib import Path

import pandas as pd
from fpdf import FPDF

from backend.database.db_manager import DatabaseManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_REPORTS_DIR = PROJECT_ROOT / "reports" / "csv"
REPORT_COLUMNS = ["timestamp", "event_type", "confidence", "severity", "details"]
PDF_REPORTS_DIR = PROJECT_ROOT / "reports" / "pdf"

RECOMMENDATIONS = {
    "Low": "No irregularities detected.",
    "Medium": "Minor irregularities detected; review the highlighted events.",
    "High": "Multiple high-severity events detected; manual review recommended.",
}


def export_csv(session_id: int) -> Path:
    database = DatabaseManager()
    incidents = database.get_session_incidents(session_id)
    report = pd.DataFrame(incidents, columns=REPORT_COLUMNS)

    CSV_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = CSV_REPORTS_DIR / f"session_{session_id}.csv"
    report.to_csv(report_path, index=False)
    return report_path


def generate_pdf(session_id: int) -> Path:
    database = DatabaseManager()
    with database.get_connection() as connection:
        session = connection.execute(
            """
            SELECT es.*, s.student_id, s.name, s.usn, s.subject, s.exam_code
            FROM exam_sessions es
            JOIN students s ON s.student_id = es.student_id
            WHERE es.session_id = ?
            """,
            (session_id,),
        ).fetchone()

    if session is None:
        raise ValueError(f"Session {session_id} was not found.")

    incidents = database.get_session_incidents(session_id)
    event_counts = {event_type: 0 for event_type in (
        "PHONE_DETECTED", "LOOKING_AWAY", "MULTIPLE_PERSON", "FACE_MISSING"
    )}
    for incident in incidents:
        if incident["event_type"] in event_counts:
            event_counts[incident["event_type"]] += 1

    risk_category = session["risk_category"] or "Low"
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"Exam Proctoring Report - Session {session_id}", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Student Details", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    for label, value in (
        ("Student ID", session["student_id"]),
        ("Name", session["name"]),
        ("USN", session["usn"]),
        ("Subject", session["subject"]),
        ("Exam Code", session["exam_code"]),
    ):
        pdf.cell(0, 6, f"{label}: {value}", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Exam Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    summary = (
        ("Start time", session["start_time"]),
        ("End time", session["end_time"]),
        ("Duration (seconds)", session["duration_seconds"] or 0),
        ("Total alert count", len(incidents)),
        ("Overall risk", f"{session['risk_score'] or 0} ({risk_category})"),
    )
    for label, value in summary:
        pdf.cell(0, 6, f"{label}: {value}", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Alert Counts", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    for label, event_type in (
        ("Phone detections", "PHONE_DETECTED"),
        ("Looking-away events", "LOOKING_AWAY"),
        ("Multiple-person events", "MULTIPLE_PERSON"),
        ("Face-missing events", "FACE_MISSING"),
    ):
        pdf.cell(0, 6, f"{label}: {event_counts[event_type]}", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Recommendations", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    pdf.multi_cell(0, 6, RECOMMENDATIONS.get(risk_category, RECOMMENDATIONS["Medium"]))

    PDF_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = PDF_REPORTS_DIR / f"session_{session_id}.pdf"
    pdf.output(str(report_path))
    return report_path