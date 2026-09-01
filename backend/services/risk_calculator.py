from __future__ import annotations

from typing import Tuple

from backend.database.db_manager import DatabaseManager
from utils.settings_store import get_settings


db_manager = DatabaseManager()


def compute_risk_score(session_id: int) -> Tuple[int, str]:
    query = """
        SELECT event_type, COUNT(*) AS event_count
        FROM incidents
        WHERE session_id = ?
        GROUP BY event_type
    """
    with db_manager.get_connection() as connection:
        rows = connection.execute(query, (session_id,)).fetchall()

    settings = get_settings()
    score = sum(
        settings["RISK_WEIGHTS"].get(row["event_type"], 0) * row["event_count"]
        for row in rows
    )
    score = min(score, settings["RISK_SCORE_CAP"])

    if score <= 30:
        category = "Low"
    elif score <= 60:
        category = "Medium"
    else:
        category = "High"

    return score, category