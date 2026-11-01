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
            existing_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(students)").fetchall()
            }
            for column, definition in (
                ("email", "TEXT"),
                ("password_hash", "TEXT"),
            ):
                if column not in existing_columns:
                    conn.execute(f"ALTER TABLE students ADD COLUMN {column} {definition}")
            self._seed_catalog(conn)

    @staticmethod
    def _seed_catalog(conn: sqlite3.Connection) -> None:
        catalog = {
            "Mathematics": ("Practice algebra, calculus and quantitative reasoning.", "calculator"),
            "Physics": ("Build confidence with mechanics, waves and energy.", "atom"),
            "Computer Science": ("Strengthen programming and data-structure fundamentals.", "code"),
            "English": ("Practice grammar, vocabulary and reading comprehension.", "language"),
        }
        for name, (description, icon) in catalog.items():
            conn.execute(
                "INSERT OR IGNORE INTO subjects (name, description, icon) VALUES (?, ?, ?)",
                (name, description, icon),
            )
        for subject_name, bank_name, bank_description, exam_name, difficulty in (
            ("Mathematics", "Algebra", "Equations, functions and algebraic reasoning.", "Algebra fundamentals", "Easy"),
            ("Mathematics", "Calculus", "Limits, derivatives and integration.", "Calculus essentials", "Medium"),
            ("Physics", "Mechanics", "Motion, forces and Newtonian mechanics.", "Mechanics practice", "Medium"),
            ("Computer Science", "Python Programming", "Core Python syntax and programming concepts.", "Python foundations", "Easy"),
            ("English", "Grammar", "Practical grammar and usage questions.", "Grammar checkpoint", "Easy"),
        ):
            subject = conn.execute("SELECT subject_id FROM subjects WHERE name = ?", (subject_name,)).fetchone()
            bank = conn.execute(
                "SELECT bank_id FROM question_banks WHERE subject_id = ? AND name = ?",
                (subject["subject_id"], bank_name),
            ).fetchone()
            if bank is None:
                cursor = conn.execute(
                    "INSERT INTO question_banks (subject_id, name, description) VALUES (?, ?, ?)",
                    (subject["subject_id"], bank_name, bank_description),
                )
                bank_id = cursor.lastrowid
                cursor = conn.execute(
                    "INSERT INTO exams (bank_id, name, duration_minutes, max_marks, difficulty, mode) VALUES (?, ?, 15, 3, ?, 'practice')",
                    (bank_id, exam_name, difficulty),
                )
                exam_id = cursor.lastrowid
                questions = (
                    ("Which expression represents a linear function?", ("x + 2", "x² + 2", "1/x", "√x"), 0, "A linear function has a constant rate of change."),
                    ("Which value is the identity element for addition?", ("0", "1", "-1", "10"), 0, "Adding zero leaves a number unchanged."),
                    ("Which statement is true about a well-defined algorithm?", ("It has finite steps", "It must use Python", "It cannot have inputs", "It must use recursion"), 0, "An algorithm must complete after a finite sequence of steps."),
                )
                for order, (statement, options, correct, explanation) in enumerate(questions):
                    question_cursor = conn.execute(
                        "INSERT INTO questions (exam_id, statement, explanation, sort_order) VALUES (?, ?, ?, ?)",
                        (exam_id, statement, explanation, order),
                    )
                    for option_order, option_text in enumerate(options):
                        conn.execute(
                            "INSERT INTO question_options (question_id, option_text, is_correct, sort_order) VALUES (?, ?, ?, ?)",
                            (question_cursor.lastrowid, option_text, int(option_order == correct), option_order),
                        )

    def list_subjects(self) -> list[dict]:
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT s.*, COUNT(DISTINCT qb.bank_id) AS bank_count,
                       COUNT(DISTINCT e.exam_id) AS exam_count
                FROM subjects s
                LEFT JOIN question_banks qb ON qb.subject_id = s.subject_id
                LEFT JOIN exams e ON e.bank_id = qb.bank_id
                GROUP BY s.subject_id ORDER BY s.name
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_subject(self, subject_id: int) -> Optional[dict]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM subjects WHERE subject_id = ?", (subject_id,)).fetchone()
            return dict(row) if row else None

    def list_exams(self, subject_id: int) -> list[dict]:
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT e.*, qb.name AS bank_name, s.name AS subject_name,
                       COUNT(q.question_id) AS question_count
                FROM exams e
                JOIN question_banks qb ON qb.bank_id = e.bank_id
                JOIN subjects s ON s.subject_id = qb.subject_id
                LEFT JOIN questions q ON q.exam_id = e.exam_id
                WHERE s.subject_id = ?
                GROUP BY e.exam_id ORDER BY e.name
                """,
                (subject_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_exam(self, exam_id: int) -> Optional[dict]:
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT e.*, qb.name AS bank_name, s.name AS subject_name
                FROM exams e
                JOIN question_banks qb ON qb.bank_id = e.bank_id
                JOIN subjects s ON s.subject_id = qb.subject_id
                WHERE e.exam_id = ?
                """,
                (exam_id,),
            ).fetchone()
            if row is None:
                return None
            exam = dict(row)
            questions = conn.execute(
                "SELECT * FROM questions WHERE exam_id = ? ORDER BY sort_order, question_id",
                (exam_id,),
            ).fetchall()
            exam["questions"] = []
            for question in questions:
                item = dict(question)
                item["options"] = [
                    dict(option) for option in conn.execute(
                        "SELECT option_id, option_text, sort_order FROM question_options WHERE question_id = ? ORDER BY sort_order, option_id",
                        (question["question_id"],),
                    ).fetchall()
                ]
                exam["questions"].append(item)
            exam["question_count"] = len(exam["questions"])
            return exam

    def create_attempt(self, student_id: int, exam_id: int, mode: str = "practice") -> int:
        with self.get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO exam_attempts (student_id, exam_id, mode) VALUES (?, ?, ?)",
                (student_id, exam_id, mode),
            )
            return int(cursor.lastrowid)

    def save_answer(self, attempt_id: int, question_id: int, option_id: Optional[int]) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO student_answers (attempt_id, question_id, option_id)
                VALUES (?, ?, ?)
                ON CONFLICT(attempt_id, question_id) DO UPDATE SET option_id = excluded.option_id, saved_at = CURRENT_TIMESTAMP
                """,
                (attempt_id, question_id, option_id),
            )

    def submit_attempt(self, attempt_id: int) -> Optional[dict]:
        with self.get_connection() as conn:
            attempt = conn.execute(
                "SELECT * FROM exam_attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if attempt is None:
                return None
            if attempt["status"] == "submitted":
                result = conn.execute("SELECT * FROM exam_results WHERE attempt_id = ?", (attempt_id,)).fetchone()
                return dict(result) if result else None
            rows = conn.execute(
                """
                SELECT q.question_id, q.marks, a.option_id, correct.option_id AS correct_option_id
                FROM questions q
                LEFT JOIN student_answers a ON a.question_id = q.question_id AND a.attempt_id = ?
                LEFT JOIN question_options correct ON correct.question_id = q.question_id AND correct.is_correct = 1
                WHERE q.exam_id = ?
                """,
                (attempt_id, attempt["exam_id"]),
            ).fetchall()
            correct = sum(1 for row in rows if row["option_id"] and row["option_id"] == row["correct_option_id"])
            answered = sum(1 for row in rows if row["option_id"])
            marks = sum(float(row["marks"]) for row in rows if row["option_id"] and row["option_id"] == row["correct_option_id"])
            now = datetime.now().isoformat(timespec="seconds")
            started = datetime.fromisoformat(attempt["started_at"])
            duration = max(int((datetime.now() - started).total_seconds()), 0)
            conn.execute("UPDATE exam_attempts SET submitted_at = ?, status = 'submitted' WHERE attempt_id = ?", (now, attempt_id))
            cursor = conn.execute(
                """
                INSERT INTO exam_results (attempt_id, correct_answers, incorrect_answers, unanswered, marks_obtained, percentage, time_taken_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (attempt_id, correct, answered - correct, len(rows) - answered, marks, (marks / len(rows) * 100) if rows else 0, duration),
            )
            return dict(conn.execute("SELECT * FROM exam_results WHERE result_id = ?", (cursor.lastrowid,)).fetchone())

    def get_attempt(self, attempt_id: int) -> Optional[dict]:
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT a.*, e.name AS exam_name, e.duration_minutes, e.max_marks,
                       s.name AS subject_name
                FROM exam_attempts a JOIN exams e ON e.exam_id = a.exam_id
                JOIN question_banks qb ON qb.bank_id = e.bank_id
                JOIN subjects s ON s.subject_id = qb.subject_id
                WHERE a.attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
            if row is None:
                return None
            attempt = dict(row)
            attempt["result"] = None
            result = conn.execute("SELECT * FROM exam_results WHERE attempt_id = ?", (attempt_id,)).fetchone()
            if result:
                attempt["result"] = dict(result)
            return attempt

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