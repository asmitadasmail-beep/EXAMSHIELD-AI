from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from backend.database.db_manager import DatabaseManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = PROJECT_ROOT / "logs" / "app.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

_file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
)
_incident_logger = logging.getLogger("examshield.incidents")
_incident_logger.setLevel(logging.INFO)
if not _incident_logger.handlers:
    _incident_logger.addHandler(_file_handler)
_incident_logger.propagate = False


class IncidentLogger:
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db_manager = db_manager or DatabaseManager()

    def log_event(
        self,
        session_id: int,
        event_type: str,
        confidence: float,
        severity: str,
        details: str,
    ) -> Optional[int]:
        _incident_logger.info(
            "session_id=%s event_type=%s confidence=%.3f severity=%s details=%s",
            session_id,
            event_type,
            confidence,
            severity,
            details,
        )
        try:
            return self.db_manager.add_incident(
                session_id=session_id,
                event_type=event_type,
                confidence=confidence,
                severity=severity,
                details=details,
            )
        except Exception:
            _incident_logger.exception(
                "Failed to persist incident session_id=%s event_type=%s",
                session_id,
                event_type,
            )
            return None