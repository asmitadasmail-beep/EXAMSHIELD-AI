import logging

from flask import Blueprint, jsonify, render_template, send_file

from backend.database.db_manager import DatabaseManager
from utils.report_generator import generate_pdf


report_bp = Blueprint("reports", __name__)
db_manager = DatabaseManager()
logger = logging.getLogger(__name__)


@report_bp.route("/reports")
def reports_page():
    try:
        sessions = db_manager.get_all_sessions()
    except Exception:
        logger.exception("Reports database query failed")
        return render_template("reports.html", page_title="Reports", sessions=[], error="Reports are temporarily unavailable.")
    return render_template("reports.html", page_title="Reports", sessions=sessions, error=None)


@report_bp.route("/report/<int:session_id>/generate")
def generate_report(session_id: int):
    try:
        report_path = generate_pdf(session_id)
    except ValueError as error:
        return jsonify({"error": str(error)}), 404
    except Exception:
        logger.exception("PDF report generation failed")
        return jsonify({"error": "The report could not be generated."}), 500
    return jsonify({"session_id": session_id, "path": str(report_path), "ready": True})


@report_bp.route("/report/<int:session_id>/download")
def download_report(session_id: int):
    try:
        report_path = generate_pdf(session_id)
    except ValueError as error:
        return jsonify({"error": str(error)}), 404
    except Exception:
        logger.exception("PDF report download failed")
        return jsonify({"error": "The report is temporarily unavailable."}), 500
    return send_file(
        report_path,
        as_attachment=True,
        download_name=report_path.name,
        mimetype="application/pdf",
    )