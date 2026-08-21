import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional


class DatabaseManager:
    def __init__(self, db_path: Optional[Path] = None):
        project_root = Path(__file__).resolve().parents[2]
        default_db_path = project_root / "database" / "exam_proctoring.db"
        self.db_path = Path(db_path) if db_path else default_db_path
        self.schema_path = Path(__file__).resolve().parent / "schema.sql"

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def init_db(self) -> None:
        schema_sql = self.schema_path.read_text(encoding="utf-8")
        with self.get_connection() as conn:
            conn.executescript(schema_sql)

    def add_student(self, name: str, usn: str, subject: str, exam_code: str) -> int:
        query = """
            INSERT INTO students (name, usn, subject, exam_code)
            VALUES (?, ?, ?, ?)
        """
        with self.get_connection() as conn:
            cursor = conn.execute(query, (name, usn, subject, exam_code))
            return int(cursor.lastrowid)

    def create_session(self, student_id: int) -> int:
        query = """
            INSERT INTO exam_sessions (student_id, start_time, status)
            VALUES (?, ?, 'in_progress')
        """
        with self.get_connection() as conn:
            cursor = conn.execute(query, (student_id, datetime.now().isoformat(timespec="seconds")))
            return int(cursor.lastrowid)

    def end_session(
        self,
        session_id: int,
        end_time: str,
        duration: int,
        risk_score: float,
        risk_category: str,
        status: str,
    ) -> None:
        query = """
            UPDATE exam_sessions
            SET end_time = ?,
                duration_seconds = ?,
                risk_score = ?,
                risk_category = ?,
                status = ?
            WHERE session_id = ?
        """
        with self.get_connection() as conn:
            conn.execute(
                query,
                (end_time, duration, risk_score, risk_category, status, session_id),
            )

    def add_incident(
        self,
        session_id: int,
        event_type: str,
        confidence: float,
        severity: str,
        details: str,
    ) -> int:
        query = """
            INSERT INTO incidents (session_id, event_type, confidence, severity, details)
            VALUES (?, ?, ?, ?, ?)
        """
        with self.get_connection() as conn:
            cursor = conn.execute(query, (session_id, event_type, confidence, severity, details))
            return int(cursor.lastrowid)

    def get_session_incidents(self, session_id: int) -> list[dict]:
        query = """
            SELECT incident_id, session_id, event_type, timestamp, confidence, severity, details
            FROM incidents
            WHERE session_id = ?
            ORDER BY timestamp ASC
        """
        with self.get_connection() as conn:
            rows = conn.execute(query, (session_id,)).fetchall()
            return [dict(row) for row in rows]

    def get_all_sessions(self) -> list[dict]:
        query = """
            SELECT
                es.session_id,
                es.student_id,
                s.name,
                s.usn,
                s.subject,
                s.exam_code,
                es.start_time,
                es.end_time,
                es.duration_seconds,
                es.status,
                es.risk_score,
                es.risk_category
            FROM exam_sessions es
            JOIN students s ON s.student_id = es.student_id
            ORDER BY es.session_id DESC
        """
        with self.get_connection() as conn:
            rows = conn.execute(query).fetchall()
            return [dict(row) for row in rows]


if __name__ == "__main__":
    db = DatabaseManager()
    db.init_db()

    student_id = db.add_student(
        name="Test Student",
        usn="1CS23AI001",
        subject="Artificial Intelligence",
        exam_code="AI101",
    )
    session_id = db.create_session(student_id)
    db.add_incident(
        session_id=session_id,
        event_type="LOOKING_AWAY",
        confidence=0.91,
        severity="MEDIUM",
        details="Face angle exceeded configured threshold for 3 seconds.",
    )
    db.end_session(
        session_id=session_id,
        end_time=datetime.now().isoformat(timespec="seconds"),
        duration=120,
        risk_score=35.5,
        risk_category="Medium",
        status="completed",
    )

    print("All sessions:")
    for session in db.get_all_sessions():
        print(session)

    print("\nIncidents for new session:")
    for incident in db.get_session_incidents(session_id):
        print(incident)