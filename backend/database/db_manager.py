import json
import math
import re
import secrets
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
                ("department", "TEXT DEFAULT 'Computer Science & Engineering'"),
                ("semester", "TEXT DEFAULT 'Semester 5'"),
                ("account_status", "TEXT DEFAULT 'Active'"),
            ):
                if column not in existing_columns:
                    conn.execute(f"ALTER TABLE students ADD COLUMN {column} {definition}")

            existing_exam_cols = {
                row["name"] for row in conn.execute("PRAGMA table_info(exams)").fetchall()
            }
            for col_name, col_def in (
                ("department", "TEXT NOT NULL DEFAULT ''"),
                ("semester", "TEXT NOT NULL DEFAULT ''"),
                ("academic_year", "TEXT NOT NULL DEFAULT ''"),
                ("description", "TEXT NOT NULL DEFAULT ''"),
                ("instructions", "TEXT NOT NULL DEFAULT ''"),
                ("pass_marks", "REAL NOT NULL DEFAULT 0"),
                ("start_time", "TIMESTAMP"),
                ("end_time", "TIMESTAMP"),
                ("randomize_questions", "INTEGER NOT NULL DEFAULT 0"),
                ("randomize_options", "INTEGER NOT NULL DEFAULT 0"),
                ("allow_multiple_attempts", "INTEGER NOT NULL DEFAULT 0"),
                ("status", "TEXT NOT NULL DEFAULT 'published'"),
                ("created_by", "INTEGER"),
                ("sections_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("exam_code", "TEXT"),
            ):
                if col_name not in existing_exam_cols:
                    conn.execute(f"ALTER TABLE exams ADD COLUMN {col_name} {col_def}")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_exams_code ON exams(exam_code)")

            existing_q_cols = {
                row["name"] for row in conn.execute("PRAGMA table_info(questions)").fetchall()
            }
            for col_name, col_def in (
                ("question_type", "TEXT NOT NULL DEFAULT 'mcq'"),
                ("difficulty", "TEXT NOT NULL DEFAULT 'Medium'"),
                ("topic", "TEXT NOT NULL DEFAULT ''"),
                ("image_url", "TEXT NOT NULL DEFAULT ''"),
                ("negative_marks", "REAL NOT NULL DEFAULT 0"),
                ("correct_answer_text", "TEXT NOT NULL DEFAULT ''"),
                ("section_name", "TEXT NOT NULL DEFAULT ''"),
            ):
                if col_name not in existing_q_cols:
                    conn.execute(f"ALTER TABLE questions ADD COLUMN {col_name} {col_def}")

            existing_ans_cols = {
                row["name"] for row in conn.execute("PRAGMA table_info(student_answers)").fetchall()
            }
            for col_name, col_def in (
                ("answer_text", "TEXT NOT NULL DEFAULT ''"),
                ("marks_awarded", "REAL"),
                ("teacher_feedback", "TEXT NOT NULL DEFAULT ''"),
                ("evaluated_by", "INTEGER"),
                ("evaluated_at", "TIMESTAMP"),
            ):
                if col_name not in existing_ans_cols:
                    conn.execute(f"ALTER TABLE student_answers ADD COLUMN {col_name} {col_def}")

            existing_res_cols = {
                row["name"] for row in conn.execute("PRAGMA table_info(exam_results)").fetchall()
            }
            for col_name, col_def in (
                ("grade", "TEXT NOT NULL DEFAULT 'F'"),
                ("status", "TEXT NOT NULL DEFAULT 'Fail'"),
                ("is_published", "INTEGER NOT NULL DEFAULT 1"),
                ("evaluation_status", "TEXT NOT NULL DEFAULT 'evaluated'"),
            ):
                if col_name not in existing_res_cols:
                    conn.execute(f"ALTER TABLE exam_results ADD COLUMN {col_name} {col_def}")

            existing_audit_cols = {
                row["name"] for row in conn.execute("PRAGMA table_info(exam_audit_logs)").fetchall()
            }
            for col_name, col_def in (
                ("user_name", "TEXT NOT NULL DEFAULT ''"),
                ("user_role", "TEXT NOT NULL DEFAULT ''"),
                ("entity_type", "TEXT NOT NULL DEFAULT 'exam'"),
                ("entity_id", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if col_name not in existing_audit_cols:
                    conn.execute(f"ALTER TABLE exam_audit_logs ADD COLUMN {col_name} {col_def}")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS composer_drafts (
                    draft_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    title TEXT NOT NULL DEFAULT 'Untitled Draft',
                    subject_id INTEGER,
                    sections_json TEXT NOT NULL DEFAULT '[]',
                    questions_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES admin_users(admin_user_id) ON DELETE CASCADE,
                    FOREIGN KEY(subject_id) REFERENCES subjects(subject_id) ON DELETE SET NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_composer_drafts_user ON composer_drafts(user_id)")

            self._seed_grade_boundaries(conn)
            self._seed_catalog(conn)
            self._seed_bank_questions(conn)
            self._backfill_exam_codes(conn)

    STOP_WORDS = {"and", "of", "the", "in", "for", "to", "a", "an", "on", "with", "at", "by", "is"}
    CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # excludes 0, O, 1, I

    def _generate_unique_exam_code(
        self,
        conn: sqlite3.Connection,
        subject_name: str = "",
        semester: str = "",
        exam_name: str = "",
    ) -> str:
        source = (subject_name or exam_name or "EXAM").strip()
        words = re.findall(r'[A-Za-z0-9]+', source)
        meaningful = [w.upper() for w in words if w.lower() not in self.STOP_WORDS]
        if meaningful:
            if len(meaningful) >= 2:
                acronym = "".join(w[0] for w in meaningful)[:4]
            else:
                acronym = meaningful[0][:4]
        else:
            acronym = "EXAM"
        if len(acronym) < 2:
            acronym = (acronym + "EXAM")[:4]

        sem_num = ""
        if semester:
            m = re.search(r'\d+', str(semester))
            if m:
                sem_num = m.group(0)

        prefix = f"{acronym}{sem_num}"

        for _ in range(200):
            token = "".join(secrets.choice(self.CODE_CHARS) for _ in range(6))
            candidate = f"{prefix}-{token}"
            row = conn.execute("SELECT 1 FROM exams WHERE UPPER(exam_code) = ?", (candidate.upper(),)).fetchone()
            if not row:
                return candidate

        token = "".join(secrets.choice(self.CODE_CHARS) for _ in range(8))
        return f"{prefix}-{token}"

    def generate_exam_code(self, subject_name: str = "", semester: str = "", exam_name: str = "") -> str:
        with self.get_connection() as conn:
            return self._generate_unique_exam_code(conn, subject_name=subject_name, semester=semester, exam_name=exam_name)

    def _backfill_exam_codes(self, conn: sqlite3.Connection) -> None:
        try:
            rows = conn.execute("""
                SELECT e.exam_id, e.name, e.semester, s.name AS subject_name
                FROM exams e
                LEFT JOIN question_banks qb ON qb.bank_id = e.bank_id
                LEFT JOIN subjects s ON s.subject_id = qb.subject_id
                WHERE e.exam_code IS NULL OR TRIM(e.exam_code) = ''
            """).fetchall()
            for row in rows:
                code = self._generate_unique_exam_code(
                    conn,
                    subject_name=row["subject_name"] or "",
                    semester=row["semester"] or "",
                    exam_name=row["name"] or "",
                )
                conn.execute("UPDATE exams SET exam_code = ? WHERE exam_id = ?", (code, row["exam_id"]))
        except Exception:
            pass

    @staticmethod
    def _seed_grade_boundaries(conn: sqlite3.Connection) -> None:
        try:
            count = conn.execute("SELECT COUNT(*) AS c FROM grade_boundaries").fetchone()["c"]
        except Exception:
            return
        if count == 0:
            default_boundaries = [
                ("A+", 90.0, 100.0, "Outstanding academic performance", 1),
                ("A", 80.0, 89.99, "Excellent understanding and mastery", 2),
                ("B", 70.0, 79.99, "Good performance with minor gaps", 3),
                ("C", 60.0, 69.99, "Satisfactory comprehension", 4),
                ("D", 50.0, 59.99, "Minimum passing performance", 5),
                ("F", 0.0, 49.99, "Below minimum passing threshold", 6),
            ]
            conn.executemany(
                """
                INSERT INTO grade_boundaries (grade, min_score, max_score, description, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                default_boundaries,
            )

    @staticmethod
    def _seed_bank_questions(conn: sqlite3.Connection) -> None:
        try:
            count = conn.execute("SELECT COUNT(*) AS c FROM bank_questions").fetchone()["c"]
        except Exception:
            return
        if count == 0:
            cursor = conn.execute("""
                SELECT q.statement, q.explanation, q.marks, q.question_type, q.difficulty,
                       q.topic, q.image_url, q.negative_marks, q.correct_answer_text,
                       qb.subject_id, q.question_id, qb.name AS bank_name
                FROM questions q
                JOIN exams e ON e.exam_id = q.exam_id
                JOIN question_banks qb ON qb.bank_id = e.bank_id
                ORDER BY q.question_id
            """)
            questions_to_seed = cursor.fetchall()
            for q in questions_to_seed:
                cur = conn.execute(
                    """
                    INSERT INTO bank_questions (
                        subject_id, topic, statement, question_type, difficulty, marks,
                        negative_marks, explanation, correct_answer_text, image_url, created_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        q["subject_id"],
                        q["topic"] or q["bank_name"] or "Fundamentals",
                        q["statement"],
                        q["question_type"] or "mcq",
                        q["difficulty"] or "Medium",
                        q["marks"] or 1,
                        q["negative_marks"] or 0,
                        q["explanation"] or "",
                        q["correct_answer_text"] or "",
                        q["image_url"] or "",
                    ),
                )
                new_bq_id = cur.lastrowid
                options = conn.execute(
                    "SELECT option_text, is_correct, sort_order FROM question_options WHERE question_id = ? ORDER BY sort_order",
                    (q["question_id"],),
                ).fetchall()
                for opt in options:
                    conn.execute(
                        """
                        INSERT INTO bank_question_options (bank_question_id, option_text, is_correct, sort_order)
                        VALUES (?, ?, ?, ?)
                        """,
                        (new_bq_id, opt["option_text"], opt["is_correct"], opt["sort_order"]),
                    )

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

    def get_exam(self, exam_id: int, include_answers: bool = False) -> Optional[dict]:
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
                if not include_answers:
                    item.pop("correct_answer_text", None)
                    item.pop("explanation", None)
                item["options"] = [
                    dict(option) for option in conn.execute(
                        "SELECT option_id, option_text, sort_order FROM question_options WHERE question_id = ? ORDER BY sort_order, option_id",
                        (question["question_id"],),
                    ).fetchall()
                ]
                exam["questions"].append(item)
            exam["question_count"] = len(exam["questions"])
            return exam

    def get_exam_questions(self, exam_id: int) -> list[dict]:
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM questions WHERE exam_id = ? ORDER BY sort_order, question_id",
                (exam_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_exam_by_code(self, exam_code: str, include_answers: bool = False) -> Optional[dict]:
        if not exam_code or not str(exam_code).strip():
            return None
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT exam_id FROM exams WHERE UPPER(exam_code) = ?",
                (str(exam_code).strip().upper(),),
            ).fetchone()
            if not row:
                return None
            return self.get_exam(row["exam_id"], include_answers=include_answers)

    def get_exam_full_detail(self, exam_id: int) -> Optional[dict]:
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT e.*, qb.name AS bank_name, s.name AS subject_name, s.subject_id
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
                        "SELECT option_id, option_text, is_correct, sort_order FROM question_options WHERE question_id = ? ORDER BY sort_order, option_id",
                        (question["question_id"],),
                    ).fetchall()
                ]
                exam["questions"].append(item)
            exam["question_count"] = len(exam["questions"])
            return exam

    def get_exam_questions(self, exam_id: int) -> list[dict]:
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM questions WHERE exam_id = ? ORDER BY sort_order, question_id",
                (exam_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def create_examination_with_questions(
        self,
        name: str,
        subject_id: Optional[int] = None,
        subject_name: Optional[str] = None,
        bank_name: Optional[str] = None,
        duration_minutes: int = 1,
        max_marks: float = 0,
        difficulty: str = "Mixed",
        negative_marking: float = 0,
        mode: str = "practice",
        department: str = "",
        semester: str = "",
        academic_year: str = "",
        description: str = "",
        instructions: str = "",
        pass_marks: float = 0,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        randomize_questions: bool = False,
        randomize_options: bool = False,
        allow_multiple_attempts: bool = False,
        status: str = "published",
        created_by: Optional[int] = None,
        questions: Optional[list[dict]] = None,
        sections: Optional[list[dict]] = None,
        exam_code: Optional[str] = None,
    ) -> int:
        with self.get_connection() as conn:
            # 1. Subject determination
            target_subject_id = subject_id
            target_subject_name = (subject_name or "").strip()
            if not target_subject_id and target_subject_name:
                row = conn.execute("SELECT subject_id, name FROM subjects WHERE name = ?", (target_subject_name,)).fetchone()
                if row:
                    target_subject_id = row["subject_id"]
                    target_subject_name = row["name"]
                else:
                    cur = conn.execute("INSERT INTO subjects (name, description, icon) VALUES (?, '', 'book')", (target_subject_name,))
                    target_subject_id = cur.lastrowid
            elif not target_subject_id:
                first = conn.execute("SELECT subject_id, name FROM subjects ORDER BY subject_id LIMIT 1").fetchone()
                target_subject_id = first["subject_id"] if first else 1
                target_subject_name = first["name"] if first else "General"
            elif not target_subject_name:
                s_row = conn.execute("SELECT name FROM subjects WHERE subject_id = ?", (target_subject_id,)).fetchone()
                target_subject_name = s_row["name"] if s_row else "General"

            # 2. Question Bank determination
            target_bank_name = (bank_name or "General Assessment").strip()
            bank_row = conn.execute(
                "SELECT bank_id FROM question_banks WHERE subject_id = ? AND name = ?",
                (target_subject_id, target_bank_name),
            ).fetchone()
            if bank_row:
                target_bank_id = bank_row["bank_id"]
            else:
                cur = conn.execute(
                    "INSERT INTO question_banks (subject_id, name, description) VALUES (?, ?, '')",
                    (target_subject_id, target_bank_name),
                )
                target_bank_id = cur.lastrowid

            # 3. Calculate total marks from questions if not set
            computed_marks = 0.0
            if questions:
                for q in questions:
                    computed_marks += float(q.get("marks", 1))
            final_max_marks = max_marks if max_marks > 0 else (computed_marks if computed_marks > 0 else 10.0)

            # 4. Determine unique exam code
            if exam_code and str(exam_code).strip():
                final_exam_code = str(exam_code).strip().upper()
            else:
                final_exam_code = self._generate_unique_exam_code(
                    conn,
                    subject_name=target_subject_name,
                    semester=semester,
                    exam_name=name,
                )

            # 5. Insert into exams
            exam_cursor = conn.execute(
                """
                INSERT INTO exams (
                    bank_id, name, duration_minutes, max_marks, difficulty, negative_marking, mode,
                    department, semester, academic_year, description, instructions, pass_marks,
                    start_time, end_time, randomize_questions, randomize_options, allow_multiple_attempts,
                    status, created_by, sections_json, exam_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_bank_id,
                    name.strip(),
                    int(duration_minutes),
                    float(final_max_marks),
                    difficulty,
                    float(negative_marking),
                    mode,
                    department.strip(),
                    semester.strip(),
                    academic_year.strip(),
                    description.strip(),
                    instructions.strip(),
                    float(pass_marks),
                    start_time if start_time else None,
                    end_time if end_time else None,
                    int(randomize_questions),
                    int(randomize_options),
                    int(allow_multiple_attempts),
                    status,
                    created_by,
                    json.dumps(sections) if sections else "[]",
                    final_exam_code,
                ),
            )
            exam_id = int(exam_cursor.lastrowid)

            # 6. Teacher permission grant if created by teacher
            if created_by:
                user_row = conn.execute("SELECT role FROM admin_users WHERE admin_user_id = ?", (created_by,)).fetchone()
                if user_row and user_row["role"] == "Teacher":
                    conn.execute(
                        "INSERT OR IGNORE INTO teacher_exam_permissions (admin_user_id, exam_id) VALUES (?, ?)",
                        (created_by, exam_id),
                    )

            # 6. Insert questions and options
            if questions:
                for order, q in enumerate(questions):
                    statement = str(q.get("statement") or q.get("question_text") or "").strip()
                    if not statement:
                        continue
                    q_cur = conn.execute(
                        """
                        INSERT INTO questions (
                            exam_id, statement, explanation, marks, sort_order,
                            question_type, difficulty, topic, image_url, negative_marks, correct_answer_text,
                            section_name
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            exam_id,
                            statement,
                            q.get("explanation", "").strip(),
                            float(q.get("marks", 1)),
                            int(q.get("sort_order", order)),
                            str(q.get("question_type") or q.get("type") or "mcq").strip().lower(),
                            q.get("difficulty", "Medium"),
                            q.get("topic", "").strip(),
                            q.get("image_url", "").strip(),
                            float(q.get("negative_marks", 0)),
                            str(q.get("correct_answer_text") or q.get("correct_answer") or "").strip(),
                            str(q.get("section_name") or q.get("section") or "").strip(),
                        ),
                    )
                    question_id = int(q_cur.lastrowid)

                    options = q.get("options") or []
                    for opt_order, opt in enumerate(options):
                        opt_text = (opt.get("option_text") or opt.get("text") or "").strip() if isinstance(opt, dict) else str(opt).strip()
                        is_corr = int(bool(opt.get("is_correct", False))) if isinstance(opt, dict) else 0
                        if opt_text:
                            conn.execute(
                                """
                                INSERT INTO question_options (question_id, option_text, is_correct, sort_order)
                                VALUES (?, ?, ?, ?)
                                """,
                                (question_id, opt_text, is_corr, opt_order),
                            )

            self.log_exam_action(exam_id, created_by, "CREATED", f"Created examination '{name.strip()}' with status '{status}'")
            return exam_id

    def list_managed_exams(
        self,
        user_id: int,
        role: str = "Teacher",
        search: Optional[str] = None,
        subject_id: Optional[int] = None,
        status: Optional[str] = None,
        department: Optional[str] = None,
        semester: Optional[str] = None,
        sort_by: str = "newest",
    ) -> list[dict]:
        with self.get_connection() as conn:
            if role == "Super Admin":
                query = """
                    SELECT 
                        e.exam_id, e.name, e.duration_minutes, e.max_marks, e.pass_marks,
                        e.negative_marking, e.difficulty, e.mode,
                        COALESCE(NULLIF(e.department, ''), 'Computer Science & Engineering') AS department,
                        COALESCE(NULLIF(e.semester, ''), 'Semester 5') AS semester,
                        COALESCE(NULLIF(e.academic_year, ''), '2025-2026') AS academic_year,
                        e.description, e.instructions, e.start_time, e.end_time,
                        e.randomize_questions, e.randomize_options, e.allow_multiple_attempts,
                        COALESCE(NULLIF(e.status, ''), 'live') AS status,
                        e.created_by,
                        s.subject_id, s.name AS subject_name,
                        qb.name AS bank_name,
                        COALESCE(u.name, 'Faculty Admin') AS creator_name,
                        COALESCE(u.role, 'Faculty') AS creator_role,
                        COUNT(DISTINCT q.question_id) AS question_count,
                        COUNT(DISTINCT a.student_id) AS enrolled_students,
                        COUNT(DISTINCT CASE WHEN a.status = 'submitted' THEN a.attempt_id END) AS submission_count
                    FROM exams e
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    LEFT JOIN admin_users u ON u.admin_user_id = e.created_by
                    LEFT JOIN questions q ON q.exam_id = e.exam_id
                    LEFT JOIN exam_attempts a ON a.exam_id = e.exam_id
                    GROUP BY e.exam_id
                    ORDER BY e.exam_id DESC
                """
                rows = conn.execute(query).fetchall()
            else:
                query = """
                    SELECT 
                        e.exam_id, e.name, e.duration_minutes, e.max_marks, e.pass_marks,
                        e.negative_marking, e.difficulty, e.mode,
                        COALESCE(NULLIF(e.department, ''), 'Computer Science & Engineering') AS department,
                        COALESCE(NULLIF(e.semester, ''), 'Semester 5') AS semester,
                        COALESCE(NULLIF(e.academic_year, ''), '2025-2026') AS academic_year,
                        e.description, e.instructions, e.start_time, e.end_time,
                        e.randomize_questions, e.randomize_options, e.allow_multiple_attempts,
                        COALESCE(NULLIF(e.status, ''), 'live') AS status,
                        e.created_by,
                        s.subject_id, s.name AS subject_name,
                        qb.name AS bank_name,
                        COALESCE(u.name, 'Faculty Admin') AS creator_name,
                        COALESCE(u.role, 'Faculty') AS creator_role,
                        COUNT(DISTINCT q.question_id) AS question_count,
                        COUNT(DISTINCT a.student_id) AS enrolled_students,
                        COUNT(DISTINCT CASE WHEN a.status = 'submitted' THEN a.attempt_id END) AS submission_count
                    FROM exams e
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    JOIN teacher_exam_permissions p ON p.exam_id = e.exam_id
                    LEFT JOIN admin_users u ON u.admin_user_id = e.created_by
                    LEFT JOIN questions q ON q.exam_id = e.exam_id
                    LEFT JOIN exam_attempts a ON a.exam_id = e.exam_id
                    WHERE p.admin_user_id = ?
                    GROUP BY e.exam_id
                    ORDER BY e.exam_id DESC
                """
                rows = conn.execute(query, (user_id,)).fetchall()

            exams = []
            now_iso = datetime.now().isoformat()

            for r in rows:
                item = dict(r)
                raw_st = (item.get("status") or "live").lower()
                if raw_st in ("published", "active", "in_progress"):
                    canonical_status = "Live"
                elif raw_st in ("draft",):
                    canonical_status = "Draft"
                elif raw_st in ("scheduled",):
                    canonical_status = "Scheduled"
                elif raw_st in ("completed", "ended"):
                    canonical_status = "Completed"
                elif raw_st in ("cancelled", "canceled"):
                    canonical_status = "Cancelled"
                elif raw_st in ("archived",):
                    canonical_status = "Archived"
                else:
                    canonical_status = "Live"

                start_t = item.get("start_time")
                end_t = item.get("end_time")
                if canonical_status == "Scheduled" and start_t:
                    if end_t and now_iso > str(end_t):
                        canonical_status = "Completed"
                    elif now_iso >= str(start_t) and (not end_t or now_iso <= str(end_t)):
                        canonical_status = "Live"

                item["status"] = canonical_status
                item["raw_status"] = raw_st
                exams.append(item)

            filtered = []
            search_clean = (search or "").strip().lower()

            for ex in exams:
                if search_clean:
                    match = (
                        search_clean in ex["name"].lower()
                        or search_clean in ex["subject_name"].lower()
                        or search_clean in ex["department"].lower()
                        or search_clean in ex["creator_name"].lower()
                    )
                    if not match:
                        continue

                if subject_id and ex["subject_id"] != subject_id:
                    continue

                if status and ex["status"].lower() != status.lower():
                    continue

                if department and ex["department"].lower() != department.lower():
                    continue

                if semester and ex["semester"].lower() != semester.lower():
                    continue

                filtered.append(ex)

            if sort_by == "oldest":
                filtered.sort(key=lambda x: x["exam_id"])
            elif sort_by == "name":
                filtered.sort(key=lambda x: x["name"].lower())
            elif sort_by == "duration":
                filtered.sort(key=lambda x: -x["duration_minutes"])
            elif sort_by == "marks":
                filtered.sort(key=lambda x: -x["max_marks"])
            elif sort_by == "submissions":
                filtered.sort(key=lambda x: -x["submission_count"])
            else:  # newest
                filtered.sort(key=lambda x: -x["exam_id"])

            return filtered

    def update_examination(
        self,
        exam_id: int,
        name: str,
        duration_minutes: int,
        instructions: str = "",
        description: str = "",
        department: str = "",
        semester: str = "",
        academic_year: str = "",
        pass_marks: float = 0.0,
        negative_marking: float = 0.0,
        randomize_questions: bool = False,
        randomize_options: bool = False,
        allow_multiple_attempts: bool = False,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        status: Optional[str] = None,
        questions: Optional[list[dict]] = None,
        user_id: Optional[int] = None,
        role: str = "Teacher",
    ) -> bool:
        with self.get_connection() as conn:
            exam = conn.execute("SELECT * FROM exams WHERE exam_id = ?", (exam_id,)).fetchone()
            if not exam:
                return False

            if role == "Teacher" and user_id:
                perm = conn.execute("SELECT 1 FROM teacher_exam_permissions WHERE admin_user_id = ? AND exam_id = ?", (user_id, exam_id)).fetchone()
                if not perm:
                    return False

            conn.execute("""
                UPDATE exams
                SET name = ?, duration_minutes = ?, instructions = ?, description = ?,
                    department = ?, semester = ?, academic_year = ?, pass_marks = ?,
                    negative_marking = ?, randomize_questions = ?, randomize_options = ?,
                    allow_multiple_attempts = ?, start_time = ?, end_time = ?
                WHERE exam_id = ?
            """, (
                name.strip(),
                int(duration_minutes),
                instructions.strip(),
                description.strip(),
                department.strip(),
                semester.strip(),
                academic_year.strip(),
                float(pass_marks),
                float(negative_marking),
                int(randomize_questions),
                int(randomize_options),
                int(allow_multiple_attempts),
                start_time if start_time else None,
                end_time if end_time else None,
                exam_id,
            ))

            if status:
                conn.execute("UPDATE exams SET status = ? WHERE exam_id = ?", (status.lower(), exam_id))

            if questions is not None:
                attempts_count = conn.execute("SELECT COUNT(*) AS c FROM exam_attempts WHERE exam_id = ?", (exam_id,)).fetchone()["c"]
                existing_q_rows = conn.execute("SELECT question_id FROM questions WHERE exam_id = ?", (exam_id,)).fetchall()
                existing_q_ids = {r["question_id"] for r in existing_q_rows}
                incoming_q_ids = set()

                for order, q in enumerate(questions):
                    q_id = q.get("question_id") or q.get("id")
                    statement = str(q.get("statement", "")).strip()
                    if not statement:
                        continue

                    q_marks = float(q.get("marks", 1))
                    q_neg = float(q.get("negative_marks", 0))
                    q_type = q.get("question_type", "mcq")
                    q_diff = q.get("difficulty", "Medium")
                    q_topic = q.get("topic", "")
                    q_img = q.get("image_url", "")
                    q_expl = q.get("explanation", "")
                    q_corr_text = q.get("correct_answer_text", "")

                    if q_id and int(q_id) in existing_q_ids:
                        actual_qid = int(q_id)
                        incoming_q_ids.add(actual_qid)
                        conn.execute("""
                            UPDATE questions
                            SET statement = ?, explanation = ?, marks = ?, sort_order = ?,
                                question_type = ?, difficulty = ?, topic = ?, image_url = ?,
                                negative_marks = ?, correct_answer_text = ?
                            WHERE question_id = ? AND exam_id = ?
                        """, (
                            statement, q_expl, q_marks, order, q_type, q_diff, q_topic,
                            q_img, q_neg, q_corr_text, actual_qid, exam_id
                        ))
                    else:
                        cur = conn.execute("""
                            INSERT INTO questions (
                                exam_id, statement, explanation, marks, sort_order,
                                question_type, difficulty, topic, image_url, negative_marks, correct_answer_text
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            exam_id, statement, q_expl, q_marks, order, q_type, q_diff, q_topic,
                            q_img, q_neg, q_corr_text
                        ))
                        actual_qid = int(cur.lastrowid)
                        incoming_q_ids.add(actual_qid)

                    options = q.get("options")
                    if options is not None:
                        conn.execute("DELETE FROM question_options WHERE question_id = ?", (actual_qid,))
                        for opt_order, opt in enumerate(options):
                            if isinstance(opt, dict):
                                opt_text = str(opt.get("option_text", "")).strip()
                                is_corr = int(bool(opt.get("is_correct", False)))
                            else:
                                opt_text = str(opt).strip()
                                is_corr = 0
                            if opt_text:
                                conn.execute("""
                                    INSERT INTO question_options (question_id, option_text, is_correct, sort_order)
                                    VALUES (?, ?, ?, ?)
                                """, (actual_qid, opt_text, is_corr, opt_order))

                if attempts_count == 0:
                    for old_qid in existing_q_ids:
                        if old_qid not in incoming_q_ids:
                            conn.execute("DELETE FROM question_options WHERE question_id = ?", (old_qid,))
                            conn.execute("DELETE FROM questions WHERE question_id = ?", (old_qid,))

                conn.execute("""
                    UPDATE exams
                    SET max_marks = (SELECT COALESCE(SUM(marks), 0) FROM questions WHERE exam_id = ?)
                    WHERE exam_id = ?
                """, (exam_id, exam_id))

            conn.execute("""
                INSERT INTO exam_audit_logs (exam_id, admin_user_id, action, details)
                VALUES (?, ?, 'EDITED', ?)
            """, (exam_id, user_id, f"Updated examination '{name.strip()}'. Settings and questions modified."))

            return True

    def change_exam_status(
        self,
        exam_id: int,
        new_status: str,
        user_id: Optional[int] = None,
        role: str = "Teacher",
        reason: str = "",
    ) -> bool:
        with self.get_connection() as conn:
            exam = conn.execute("SELECT * FROM exams WHERE exam_id = ?", (exam_id,)).fetchone()
            if not exam:
                return False

            if role == "Teacher" and user_id:
                perm = conn.execute("SELECT 1 FROM teacher_exam_permissions WHERE admin_user_id = ? AND exam_id = ?", (user_id, exam_id)).fetchone()
                if not perm:
                    return False

            norm_status = new_status.lower().strip()
            if norm_status not in ("draft", "scheduled", "live", "published", "completed", "cancelled", "archived"):
                return False

            if norm_status in ("live", "published"):
                q_count = conn.execute("SELECT COUNT(*) AS c FROM questions WHERE exam_id = ?", (exam_id,)).fetchone()["c"]
                if q_count == 0:
                    raise ValueError("Cannot start or publish an examination with no questions.")

            # Ensure unique exam code exists when publishing or scheduling
            if norm_status in ("live", "published", "scheduled"):
                has_code = False
                try:
                    if exam["exam_code"] and str(exam["exam_code"]).strip():
                        has_code = True
                except (IndexError, KeyError):
                    pass
                if not has_code:
                    s_name = ""
                    try:
                        s_row = conn.execute(
                            "SELECT s.name FROM exams e JOIN question_banks qb ON qb.bank_id = e.bank_id JOIN subjects s ON s.subject_id = qb.subject_id WHERE e.exam_id = ?",
                            (exam_id,),
                        ).fetchone()
                        if s_row:
                            s_name = s_row["name"]
                    except Exception:
                        pass
                    new_code = self._generate_unique_exam_code(
                        conn,
                        subject_name=s_name,
                        semester=exam["semester"] if "semester" in exam.keys() else "",
                        exam_name=exam["name"] if "name" in exam.keys() else "",
                    )
                    conn.execute("UPDATE exams SET exam_code = ? WHERE exam_id = ?", (new_code, exam_id))

            conn.execute("UPDATE exams SET status = ? WHERE exam_id = ?", (norm_status, exam_id))

            now_iso = datetime.now().isoformat()
            if norm_status in ("live", "published") and not exam["start_time"]:
                conn.execute("UPDATE exams SET start_time = ? WHERE exam_id = ?", (now_iso, exam_id))

            if norm_status in ("completed",) and not exam["end_time"]:
                conn.execute("UPDATE exams SET end_time = ? WHERE exam_id = ?", (now_iso, exam_id))

            details = f"Status transitioned from '{exam['status']}' to '{norm_status}'."
            if reason:
                details += f" Reason: {reason}"
            conn.execute("""
                INSERT INTO exam_audit_logs (exam_id, admin_user_id, action, details)
                VALUES (?, ?, 'STATUS_CHANGE', ?)
            """, (exam_id, user_id, details))

            return True

    def schedule_examination(
        self,
        exam_id: int,
        start_time: str,
        end_time: str,
        user_id: Optional[int] = None,
        role: str = "Teacher",
    ) -> bool:
        with self.get_connection() as conn:
            exam = conn.execute("SELECT * FROM exams WHERE exam_id = ?", (exam_id,)).fetchone()
            if not exam:
                return False

            if role == "Teacher" and user_id:
                perm = conn.execute("SELECT 1 FROM teacher_exam_permissions WHERE admin_user_id = ? AND exam_id = ?", (user_id, exam_id)).fetchone()
                if not perm:
                    return False

            if not start_time or not end_time or start_time >= end_time:
                raise ValueError("Scheduled end time must be later than start time.")

            conn.execute("""
                UPDATE exams
                SET start_time = ?, end_time = ?, status = 'scheduled'
                WHERE exam_id = ?
            """, (start_time, end_time, exam_id))

            conn.execute("""
                INSERT INTO exam_audit_logs (exam_id, admin_user_id, action, details)
                VALUES (?, ?, 'SCHEDULED', ?)
            """, (exam_id, user_id, f"Examination scheduled from {start_time} to {end_time}."))

            return True

    def duplicate_examination(
        self,
        exam_id: int,
        user_id: Optional[int] = None,
        role: str = "Teacher",
    ) -> int:
        source = self.get_exam_full_detail(exam_id)
        if not source:
            raise ValueError("Source examination not found.")

        if role == "Teacher" and user_id:
            with self.get_connection() as conn:
                perm = conn.execute("SELECT 1 FROM teacher_exam_permissions WHERE admin_user_id = ? AND exam_id = ?", (user_id, exam_id)).fetchone()
                if not perm:
                    raise PermissionError("Permission denied.")

        new_name = f"{source['name']} (Copy)"
        new_exam_id = self.create_examination_with_questions(
            name=new_name,
            subject_id=source.get("subject_id"),
            subject_name=source.get("subject_name"),
            duration_minutes=source.get("duration_minutes", 30),
            max_marks=source.get("max_marks", 0),
            difficulty=source.get("difficulty", "Mixed"),
            negative_marking=source.get("negative_marking", 0),
            mode=source.get("mode", "practice"),
            department=source.get("department", ""),
            semester=source.get("semester", ""),
            academic_year=source.get("academic_year", ""),
            description=source.get("description", ""),
            instructions=source.get("instructions", ""),
            pass_marks=source.get("pass_marks", 0),
            start_time=None,
            end_time=None,
            randomize_questions=bool(source.get("randomize_questions", 0)),
            randomize_options=bool(source.get("randomize_options", 0)),
            allow_multiple_attempts=bool(source.get("allow_multiple_attempts", 0)),
            status="draft",
            created_by=user_id,
            questions=source.get("questions", []),
        )

        self.log_exam_action(new_exam_id, user_id, "DUPLICATED", f"Cloned from Exam #{exam_id} ('{source['name']}'). Created as Draft.")
        return new_exam_id

    def get_exam_submissions(self, exam_id: int, user_id: Optional[int] = None, role: str = "Teacher") -> list[dict]:
        with self.get_connection() as conn:
            exam = conn.execute("SELECT * FROM exams WHERE exam_id = ?", (exam_id,)).fetchone()
            if not exam:
                return []
            if role == "Teacher" and user_id:
                perm = conn.execute("SELECT 1 FROM teacher_exam_permissions WHERE admin_user_id = ? AND exam_id = ?", (user_id, exam_id)).fetchone()
                if not perm:
                    return []

            rows = conn.execute("""
                SELECT a.attempt_id, a.mode, a.started_at, a.submitted_at, a.status,
                       s.student_id, s.name AS student_name, s.usn, s.subject,
                       e.name AS exam_name, e.max_marks,
                       r.marks_obtained, r.percentage, r.time_taken_seconds,
                       r.correct_answers, r.incorrect_answers, r.unanswered,
                       es.risk_score, es.risk_category
                FROM exam_attempts a
                JOIN students s ON s.student_id = a.student_id
                JOIN exams e ON e.exam_id = a.exam_id
                LEFT JOIN exam_results r ON r.attempt_id = a.attempt_id
                LEFT JOIN exam_sessions es ON es.student_id = s.student_id
                WHERE a.exam_id = ?
                ORDER BY a.started_at DESC
            """, (exam_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_exam_audit_logs(self, exam_id: int) -> list[dict]:
        with self.get_connection() as conn:
            rows = conn.execute("""
                SELECT l.*, COALESCE(u.name, 'System Admin') AS admin_name, u.role AS admin_role
                FROM exam_audit_logs l
                LEFT JOIN admin_users u ON u.admin_user_id = l.admin_user_id
                WHERE l.exam_id = ?
                ORDER BY l.timestamp DESC, l.log_id DESC
            """, (exam_id,)).fetchall()
            return [dict(r) for r in rows]

    def log_exam_action(self, exam_id: int, user_id: Optional[int], action: str, details: str = "") -> None:
        try:
            with self.get_connection() as conn:
                conn.execute("""
                    INSERT INTO exam_audit_logs (exam_id, admin_user_id, action, details)
                    VALUES (?, ?, ?, ?)
                """, (exam_id, user_id, action, details))
        except Exception:
            pass

    def list_managed_students(
        self,
        user_id: int,
        role: str = "Teacher",
        search: Optional[str] = None,
        department: Optional[str] = None,
        semester: Optional[str] = None,
        participation: Optional[str] = None,
        sort_by: str = "name_asc",
    ) -> list[dict]:
        with self.get_connection() as conn:
            if role == "Super Admin":
                query = """
                    SELECT 
                        s.student_id,
                        s.name,
                        s.usn,
                        COALESCE(s.email, LOWER(s.usn) || '@student.edu') AS email,
                        COALESCE(NULLIF(s.department, ''), 'Computer Science & Engineering') AS department,
                        COALESCE(NULLIF(s.semester, ''), 'Semester 5') AS semester,
                        COALESCE(NULLIF(s.account_status, ''), 'Active') AS account_status,
                        s.created_at,
                        COUNT(DISTINCT a.attempt_id) AS total_attempts,
                        AVG(CASE WHEN a.status = 'submitted' THEN r.percentage END) AS avg_percentage,
                        AVG(CASE WHEN a.status = 'submitted' THEN r.marks_obtained END) AS avg_marks,
                        MAX(CASE WHEN a.status = 'submitted' THEN r.percentage END) AS max_percentage,
                        MIN(CASE WHEN a.status = 'submitted' THEN r.percentage END) AS min_percentage
                    FROM students s
                    LEFT JOIN exam_attempts a ON a.student_id = s.student_id
                    LEFT JOIN exam_results r ON r.attempt_id = a.attempt_id
                    GROUP BY s.student_id
                    ORDER BY s.student_id DESC
                """
                rows = conn.execute(query).fetchall()
            else:
                query = """
                    SELECT 
                        s.student_id,
                        s.name,
                        s.usn,
                        COALESCE(s.email, LOWER(s.usn) || '@student.edu') AS email,
                        COALESCE(NULLIF(s.department, ''), 'Computer Science & Engineering') AS department,
                        COALESCE(NULLIF(s.semester, ''), 'Semester 5') AS semester,
                        COALESCE(NULLIF(s.account_status, ''), 'Active') AS account_status,
                        s.created_at,
                        COUNT(DISTINCT a.attempt_id) AS total_attempts,
                        AVG(CASE WHEN a.status = 'submitted' THEN r.percentage END) AS avg_percentage,
                        AVG(CASE WHEN a.status = 'submitted' THEN r.marks_obtained END) AS avg_marks,
                        MAX(CASE WHEN a.status = 'submitted' THEN r.percentage END) AS max_percentage,
                        MIN(CASE WHEN a.status = 'submitted' THEN r.percentage END) AS min_percentage
                    FROM students s
                    JOIN exam_attempts a ON a.student_id = s.student_id
                    JOIN teacher_exam_permissions p ON p.exam_id = a.exam_id AND p.admin_user_id = ?
                    LEFT JOIN exam_results r ON r.attempt_id = a.attempt_id
                    GROUP BY s.student_id
                    ORDER BY s.student_id DESC
                """
                rows = conn.execute(query, (user_id,)).fetchall()

            students_list = []
            for row in rows:
                item = dict(row)
                avg_pct = item.get("avg_percentage")
                attempts = item.get("total_attempts", 0)
                if attempts == 0 or avg_pct is None:
                    overall = "No Attempts"
                elif avg_pct >= 80.0:
                    overall = "Excellent"
                elif avg_pct >= 65.0:
                    overall = "Good"
                elif avg_pct >= 50.0:
                    overall = "Average"
                else:
                    overall = "Needs Improvement"
                item["overall_performance"] = overall
                if item["avg_percentage"] is not None:
                    item["avg_percentage"] = round(float(item["avg_percentage"]), 1)
                item["average_percentage"] = item["avg_percentage"]
                if item["avg_marks"] is not None:
                    item["avg_marks"] = round(float(item["avg_marks"]), 1)
                item["average_marks"] = item["avg_marks"]
                item["registration_number"] = item.get("usn")
                item["student_name"] = item.get("name")
                item["total_examinations_attempted"] = item.get("submitted_attempts", 0) or attempts
                students_list.append(item)

            filtered = []
            search_clean = (search or "").strip().lower()

            for st in students_list:
                if search_clean:
                    if search_clean not in st["name"].lower() and search_clean not in st["usn"].lower():
                        continue

                if department and st["department"].lower() != department.strip().lower():
                    continue

                if semester and st["semester"].lower() != semester.strip().lower():
                    continue

                if participation == "attempted" and st["total_attempts"] == 0:
                    continue
                elif participation == "none" and st["total_attempts"] > 0:
                    continue

                filtered.append(st)

            if sort_by == "perf_desc":
                filtered.sort(key=lambda x: (x["avg_percentage"] is not None, x["avg_percentage"] or 0), reverse=True)
            elif sort_by == "perf_asc":
                filtered.sort(key=lambda x: (x["avg_percentage"] is not None, x["avg_percentage"] or 0))
            elif sort_by == "attempts_desc":
                filtered.sort(key=lambda x: -x["total_attempts"])
            elif sort_by == "usn_asc":
                filtered.sort(key=lambda x: x["usn"].lower())
            elif sort_by == "name_asc":
                filtered.sort(key=lambda x: x["name"].lower())
            else:
                filtered.sort(key=lambda x: x["name"].lower())

            return filtered

    def get_student_profile(
        self,
        student_id: int,
        user_id: Optional[int] = None,
        role: str = "Teacher",
    ) -> Optional[dict]:
        with self.get_connection() as conn:
            student_row = conn.execute(
                "SELECT * FROM students WHERE student_id = ?", (student_id,)
            ).fetchone()
            if not student_row:
                return None

            # Permission check for Teacher: must have at least one attempt in teacher's authorized exams
            if role == "Teacher" and user_id:
                perm = conn.execute(
                    """
                    SELECT 1 FROM exam_attempts a
                    JOIN teacher_exam_permissions p ON p.exam_id = a.exam_id
                    WHERE a.student_id = ? AND p.admin_user_id = ?
                    LIMIT 1
                    """,
                    (student_id, user_id),
                ).fetchone()
                if not perm:
                    return None

            student = dict(student_row)
            student["email"] = student.get("email") or f"{student['usn'].lower()}@student.edu"
            student["department"] = student.get("department") or "Computer Science & Engineering"
            student["semester"] = student.get("semester") or "Semester 5"
            student["account_status"] = student.get("account_status") or "Active"
            student.pop("password_hash", None)

            # Check biometric face registration
            face_row = conn.execute(
                "SELECT profile_id, consent_given, registered_at FROM student_face_profiles WHERE student_id = ?",
                (student_id,),
            ).fetchone()
            student["face_verified"] = bool(face_row and face_row["consent_given"])
            student["face_registered_at"] = face_row["registered_at"] if face_row else None

            # Examination history query (scoped to teacher permissions if Teacher)
            if role == "Teacher" and user_id:
                attempts_query = """
                    SELECT 
                        a.attempt_id, a.exam_id, a.mode, a.started_at, a.submitted_at, a.status,
                        e.name AS exam_name, e.duration_minutes, e.max_marks, e.pass_marks,
                        COALESCE(NULLIF(e.department, ''), 'Computer Science & Engineering') AS exam_department,
                        COALESCE(NULLIF(e.semester, ''), 'Semester 5') AS exam_semester,
                        s.name AS subject_name,
                        r.marks_obtained, r.percentage, r.time_taken_seconds,
                        r.correct_answers, r.incorrect_answers, r.unanswered,
                        es.risk_score, es.risk_category
                    FROM exam_attempts a
                    JOIN exams e ON e.exam_id = a.exam_id
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    JOIN teacher_exam_permissions p ON p.exam_id = a.exam_id AND p.admin_user_id = ?
                    LEFT JOIN exam_results r ON r.attempt_id = a.attempt_id
                    LEFT JOIN exam_sessions es ON es.student_id = a.student_id
                    WHERE a.student_id = ?
                    ORDER BY a.started_at ASC, a.attempt_id ASC
                """
                attempts_rows = conn.execute(attempts_query, (user_id, student_id)).fetchall()
            else:
                attempts_query = """
                    SELECT 
                        a.attempt_id, a.exam_id, a.mode, a.started_at, a.submitted_at, a.status,
                        e.name AS exam_name, e.duration_minutes, e.max_marks, e.pass_marks,
                        COALESCE(NULLIF(e.department, ''), 'Computer Science & Engineering') AS exam_department,
                        COALESCE(NULLIF(e.semester, ''), 'Semester 5') AS exam_semester,
                        s.name AS subject_name,
                        r.marks_obtained, r.percentage, r.time_taken_seconds,
                        r.correct_answers, r.incorrect_answers, r.unanswered,
                        es.risk_score, es.risk_category
                    FROM exam_attempts a
                    JOIN exams e ON e.exam_id = a.exam_id
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    LEFT JOIN exam_results r ON r.attempt_id = a.attempt_id
                    LEFT JOIN exam_sessions es ON es.student_id = a.student_id
                    WHERE a.student_id = ?
                    ORDER BY a.started_at ASC, a.attempt_id ASC
                """
                attempts_rows = conn.execute(attempts_query, (student_id,)).fetchall()

            attempts = [dict(r) for r in attempts_rows]
            submitted_attempts = [a for a in attempts if a["status"] == "submitted" and a["marks_obtained"] is not None]

            # Overall metrics
            total_attempts = len(attempts)
            if submitted_attempts:
                tot_p_obt = sum(a["marks_obtained"] for a in submitted_attempts)
                tot_p_max = sum(a.get("max_marks", 100.0) for a in submitted_attempts)
                avg_marks = tot_p_obt / len(submitted_attempts)
                avg_percentage = (tot_p_obt / tot_p_max * 100.0) if tot_p_max > 0 else 0.0
                highest_score = max(a["marks_obtained"] for a in submitted_attempts)
                highest_percentage = max(a["percentage"] for a in submitted_attempts)
                lowest_score = min(a["marks_obtained"] for a in submitted_attempts)
                lowest_percentage = min(a["percentage"] for a in submitted_attempts)
                passed_count = sum(1 for a in submitted_attempts if a["marks_obtained"] >= (a.get("pass_marks") or 0))
            else:
                avg_marks = 0.0
                avg_percentage = 0.0
                highest_score = 0.0
                highest_percentage = 0.0
                lowest_score = 0.0
                lowest_percentage = 0.0
                passed_count = 0

            # Subject-wise performance breakdown
            subjects_map = {}
            for a in submitted_attempts:
                subj = a["subject_name"]
                if subj not in subjects_map:
                    subjects_map[subj] = {
                        "subject_name": subj,
                        "attempts_count": 0,
                        "total_marks": 0.0,
                        "total_max": 0.0,
                        "percentages": [],
                        "passed_count": 0,
                    }
                entry = subjects_map[subj]
                entry["attempts_count"] += 1
                entry["total_marks"] += a["marks_obtained"]
                entry["total_max"] += a["max_marks"]
                entry["percentages"].append(a["percentage"])
                if a["marks_obtained"] >= (a.get("pass_marks") or 0):
                    entry["passed_count"] += 1

            subject_performance = []
            for subj, data in subjects_map.items():
                s_tot_m = data["total_marks"]
                s_tot_mx = data["total_max"]
                s_avg_p = (s_tot_m / s_tot_mx * 100.0) if s_tot_mx > 0 else 0.0
                s_avg_m = (s_tot_m / data["attempts_count"]) if data["attempts_count"] else 0.0
                s_pass_p = (data["passed_count"] / data["attempts_count"] * 100.0) if data["attempts_count"] else 0.0

                data["avg_percentage"] = round(s_avg_p, 1)
                data["average_percentage"] = round(s_avg_p, 1)
                data["avg_marks"] = round(s_avg_m, 1)
                data["average_marks"] = round(s_avg_m, 1)
                data["highest_percentage"] = round(max(data["percentages"]), 1) if data["percentages"] else 0.0
                data["highest_score"] = data["highest_percentage"]
                data["pass_percentage"] = round(s_pass_p, 1)
                data["pass_rate"] = round(s_pass_p, 1)
                data["total_marks"] = round(data["total_marks"], 1)
                data["total_max"] = round(data["total_max"], 1)
                subject_performance.append(data)
            subject_performance.sort(key=lambda x: -x["average_percentage"])

            # Performance progression (chronological sequence)
            progression = []
            for idx, a in enumerate(submitted_attempts, 1):
                progression.append({
                    "step": idx,
                    "attempt_id": a["attempt_id"],
                    "exam_name": a["exam_name"],
                    "subject_name": a["subject_name"],
                    "date": a["submitted_at"] or a["started_at"],
                    "marks_obtained": a["marks_obtained"],
                    "max_marks": a["max_marks"],
                    "percentage": round(float(a["percentage"]), 1),
                    "risk_category": a.get("risk_category") or "Low",
                })

            # Strongest and Weakest topics from student_answers
            if role == "Teacher" and user_id:
                topic_query = """
                    SELECT 
                        COALESCE(NULLIF(q.topic, ''), s.name, 'General') AS topic_name,
                        COUNT(sa.answer_id) AS total_questions,
                        SUM(CASE WHEN qo.is_correct = 1 THEN 1 ELSE 0 END) AS correct_answers
                    FROM student_answers sa
                    JOIN exam_attempts a ON a.attempt_id = sa.attempt_id
                    JOIN questions q ON q.question_id = sa.question_id
                    JOIN exams e ON e.exam_id = a.exam_id
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    JOIN teacher_exam_permissions p ON p.exam_id = e.exam_id AND p.admin_user_id = ?
                    LEFT JOIN question_options qo ON qo.option_id = sa.option_id
                    WHERE a.student_id = ?
                    GROUP BY topic_name
                """
                topic_rows = conn.execute(topic_query, (user_id, student_id)).fetchall()
            else:
                topic_query = """
                    SELECT 
                        COALESCE(NULLIF(q.topic, ''), s.name, 'General') AS topic_name,
                        COUNT(sa.answer_id) AS total_questions,
                        SUM(CASE WHEN qo.is_correct = 1 THEN 1 ELSE 0 END) AS correct_answers
                    FROM student_answers sa
                    JOIN exam_attempts a ON a.attempt_id = sa.attempt_id
                    JOIN questions q ON q.question_id = sa.question_id
                    JOIN exams e ON e.exam_id = a.exam_id
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    LEFT JOIN question_options qo ON qo.option_id = sa.option_id
                    WHERE a.student_id = ?
                    GROUP BY topic_name
                """
                topic_rows = conn.execute(topic_query, (student_id,)).fetchall()

            topics = []
            for tr in topic_rows:
                t_total = tr["total_questions"]
                t_corr = tr["correct_answers"] or 0
                acc = round((t_corr / t_total * 100.0), 1) if t_total > 0 else 0.0
                topics.append({
                    "topic": tr["topic_name"],
                    "total_questions": t_total,
                    "correct_answers": t_corr,
                    "accuracy": acc,
                })

            topics_by_acc = sorted(topics, key=lambda x: -x["accuracy"])
            strongest_topics = [t for t in topics_by_acc if t["accuracy"] >= 50.0]
            if not strongest_topics and topics_by_acc:
                strongest_topics = topics_by_acc[:3]

            weakest_topics = [t for t in sorted(topics, key=lambda x: x["accuracy"]) if t["accuracy"] < 60.0]
            if not weakest_topics and topics_by_acc:
                weakest_topics = sorted(topics, key=lambda x: x["accuracy"])[:3]

            return {
                "student": student,
                "total_attempts": total_attempts,
                "submitted_attempts_count": len(submitted_attempts),
                "total_examinations_attempted": len(submitted_attempts),
                "avg_marks": round(avg_marks, 1),
                "average_marks": round(avg_marks, 1),
                "avg_percentage": round(avg_percentage, 1),
                "average_percentage": round(avg_percentage, 1),
                "highest_score": round(highest_score, 1),
                "highest_percentage": round(highest_percentage, 1),
                "lowest_score": round(lowest_score, 1),
                "lowest_percentage": round(lowest_percentage, 1),
                "passed_count": passed_count,
                "pass_percentage": round(passed_count / len(submitted_attempts) * 100.0, 1) if submitted_attempts else 0.0,
                "pass_rate": round(passed_count / len(submitted_attempts) * 100.0, 1) if submitted_attempts else 0.0,
                "examination_history": attempts,
                "subject_performance": subject_performance,
                "performance_progression": progression,
                "strongest_topics": strongest_topics,
                "weakest_topics": weakest_topics,
            }

    def get_performance_analytics(
        self,
        user_id: int,
        role: str = "Teacher",
        subject_id: Optional[int] = None,
        exam_id: Optional[int] = None,
        department: Optional[str] = None,
        semester: Optional[str] = None,
        academic_year: Optional[str] = None,
        student_id: Optional[int] = None,
    ) -> dict:
        with self.get_connection() as conn:
            # 1. Permission validation for Teacher
            if role == "Teacher" and user_id:
                if exam_id is not None:
                    perm = conn.execute(
                        "SELECT 1 FROM teacher_exam_permissions WHERE admin_user_id = ? AND exam_id = ?",
                        (user_id, exam_id),
                    ).fetchone()
                    if not perm:
                        raise PermissionError("Access denied: You are not authorized to view reports for this examination.")

            # 2. Query all accessible examinations matching filters
            if role == "Teacher" and user_id:
                exam_q = """
                    SELECT e.exam_id, e.name, e.duration_minutes, e.max_marks, e.pass_marks,
                           e.negative_marking, e.mode,
                           COALESCE(NULLIF(e.department, ''), 'Computer Science & Engineering') AS department,
                           COALESCE(NULLIF(e.semester, ''), 'Semester 5') AS semester,
                           COALESCE(NULLIF(e.academic_year, ''), '2025-2026') AS academic_year,
                           e.start_time, e.end_time, e.status,
                           s.subject_id, s.name AS subject_name,
                           qb.bank_id, qb.name AS bank_name
                    FROM exams e
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    JOIN teacher_exam_permissions p ON p.exam_id = e.exam_id AND p.admin_user_id = ?
                    WHERE 1=1
                """
                exam_params = [user_id]
            else:
                exam_q = """
                    SELECT e.exam_id, e.name, e.duration_minutes, e.max_marks, e.pass_marks,
                           e.negative_marking, e.mode,
                           COALESCE(NULLIF(e.department, ''), 'Computer Science & Engineering') AS department,
                           COALESCE(NULLIF(e.semester, ''), 'Semester 5') AS semester,
                           COALESCE(NULLIF(e.academic_year, ''), '2025-2026') AS academic_year,
                           e.start_time, e.end_time, e.status,
                           s.subject_id, s.name AS subject_name,
                           qb.bank_id, qb.name AS bank_name
                    FROM exams e
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    WHERE 1=1
                """
                exam_params = []

            if subject_id:
                exam_q += " AND s.subject_id = ?"
                exam_params.append(subject_id)
            if exam_id:
                exam_q += " AND e.exam_id = ?"
                exam_params.append(exam_id)
            if department:
                exam_q += " AND LOWER(e.department) = LOWER(?)"
                exam_params.append(department.strip())
            if semester:
                exam_q += " AND LOWER(e.semester) = LOWER(?)"
                exam_params.append(semester.strip())
            if academic_year:
                exam_q += " AND LOWER(e.academic_year) = LOWER(?)"
                exam_params.append(academic_year.strip())

            exam_rows = conn.execute(exam_q, tuple(exam_params)).fetchall()
            matching_exams = [dict(r) for r in exam_rows]
            matching_exam_ids = [e["exam_id"] for e in matching_exams]

            if not matching_exam_ids:
                return {
                    "overview": {
                        "total_enrolled": 0,
                        "students_attempted": 0,
                        "students_evaluated": 0,
                        "unattempted_students": 0,
                        "average_marks": 0.0,
                        "average_percentage": 0.0,
                        "pass_percentage": 0.0,
                        "failure_percentage": 0.0,
                        "highest_marks": 0.0,
                        "lowest_marks": 0.0,
                        "total_attempts": 0,
                        "pass_count": 0,
                        "fail_count": 0,
                    },
                    "score_distribution": {"0_20": 0, "21_40": 0, "41_60": 0, "61_80": 0, "81_100": 0, "0-20%": 0, "21-40%": 0, "41-60%": 0, "61-80%": 0, "81-100%": 0},
                    "grade_distribution": {"A_plus": 0, "A": 0, "B": 0, "C": 0, "D": 0, "F": 0, "A+": 0},
                    "subject_performance": [],
                    "exam_analytics": [],
                    "student_rankings": [],
                    "top_students": [],
                    "support_needed_students": [],
                    "performance_trends": [],
                    "question_accuracy": [],
                    "easiest_questions": [],
                    "most_difficult_questions": [],
                }

            # 3. Query attempts and results
            placeholders = ",".join("?" for _ in matching_exam_ids)
            attempts_q = f"""
                SELECT 
                    a.attempt_id, a.student_id, a.exam_id, a.mode, a.started_at, a.submitted_at, a.status,
                    st.name AS student_name, st.usn,
                    COALESCE(st.email, LOWER(st.usn) || '@student.edu') AS student_email,
                    COALESCE(NULLIF(st.department, ''), e.department, 'Computer Science & Engineering') AS department,
                    COALESCE(NULLIF(st.semester, ''), e.semester, 'Semester 5') AS semester,
                    e.name AS exam_name, e.max_marks, e.pass_marks, e.negative_marking,
                    s.subject_id, s.name AS subject_name,
                    r.result_id, r.marks_obtained, r.percentage, r.time_taken_seconds,
                    r.correct_answers, r.incorrect_answers, r.unanswered,
                    es.risk_score, es.risk_category
                FROM exam_attempts a
                JOIN students st ON st.student_id = a.student_id
                JOIN exams e ON e.exam_id = a.exam_id
                JOIN question_banks qb ON qb.bank_id = e.bank_id
                JOIN subjects s ON s.subject_id = qb.subject_id
                LEFT JOIN exam_results r ON r.attempt_id = a.attempt_id
                LEFT JOIN exam_sessions es ON es.student_id = a.student_id
                WHERE a.exam_id IN ({placeholders})
            """
            att_params = list(matching_exam_ids)
            if student_id:
                attempts_q += " AND a.student_id = ?"
                att_params.append(student_id)

            attempt_rows = conn.execute(attempts_q, tuple(att_params)).fetchall()
            all_attempts = [dict(r) for r in attempt_rows]
            submitted_attempts = [
                a for a in all_attempts if a["status"] == "submitted" and a["result_id"] is not None
            ]

            # Total enrolled in department / cohort
            cohort_students_row = conn.execute("SELECT COUNT(*) AS c FROM students").fetchone()
            total_students_cohort = cohort_students_row["c"] if cohort_students_row else len(all_attempts)
            attempted_student_ids = {a["student_id"] for a in all_attempts}

            dept_filter = department or (matching_exams[0]["department"] if exam_id and len(matching_exams) == 1 else None)
            sem_filter = semester or (matching_exams[0]["semester"] if exam_id and len(matching_exams) == 1 else None)
            cohort_q = "SELECT COUNT(*) AS c FROM students WHERE 1=1"
            cohort_p = []
            if dept_filter:
                cohort_q += " AND department = ?"
                cohort_p.append(dept_filter)
            if sem_filter:
                cohort_q += " AND semester = ?"
                cohort_p.append(sem_filter)
            filtered_cohort_row = conn.execute(cohort_q, tuple(cohort_p)).fetchone()
            cohort_count = filtered_cohort_row["c"] if filtered_cohort_row and filtered_cohort_row["c"] > 0 else total_students_cohort
            total_enrolled = max(len(attempted_student_ids), cohort_count)
            unattempted_students = max(0, total_enrolled - len(attempted_student_ids))

            # Metric calculations
            if submitted_attempts:
                tot_obt = sum(a["marks_obtained"] for a in submitted_attempts)
                tot_max = sum(a.get("max_marks", 100.0) for a in submitted_attempts)
                avg_marks = tot_obt / len(submitted_attempts)
                avg_pct = (tot_obt / tot_max * 100.0) if tot_max > 0 else 0.0
                highest_marks = max(a["marks_obtained"] for a in submitted_attempts)
                lowest_marks = min(a["marks_obtained"] for a in submitted_attempts)
                pass_count = sum(1 for a in submitted_attempts if a["marks_obtained"] >= (a.get("pass_marks") or 0))
                fail_count = len(submitted_attempts) - pass_count
                pass_pct = (pass_count / len(submitted_attempts)) * 100.0
                fail_pct = 100.0 - pass_pct
            else:
                avg_marks = 0.0
                avg_pct = 0.0
                highest_marks = 0.0
                lowest_marks = 0.0
                pass_count = 0
                fail_count = 0
                pass_pct = 0.0
                fail_pct = 0.0

            overview = {
                "total_enrolled": total_enrolled,
                "students_attempted": len(attempted_student_ids),
                "students_evaluated": len({a["student_id"] for a in submitted_attempts}),
                "unattempted_students": unattempted_students,
                "average_marks": round(avg_marks, 1),
                "avg_marks": round(avg_marks, 1),
                "average_percentage": round(avg_pct, 1),
                "avg_percentage": round(avg_pct, 1),
                "pass_percentage": round(pass_pct, 1),
                "pass_rate": round(pass_pct, 1),
                "failure_percentage": round(fail_pct, 1),
                "fail_rate": round(fail_pct, 1),
                "highest_marks": round(highest_marks, 1),
                "highest_score": round(highest_marks, 1),
                "lowest_marks": round(lowest_marks, 1),
                "lowest_score": round(lowest_marks, 1),
                "total_attempts": len(all_attempts),
                "pass_count": pass_count,
                "fail_count": fail_count,
            }

            # Score distribution (Histograms)
            c_0_20 = sum(1 for a in submitted_attempts if a["percentage"] <= 20.0)
            c_21_40 = sum(1 for a in submitted_attempts if 20.0 < a["percentage"] <= 40.0)
            c_41_60 = sum(1 for a in submitted_attempts if 40.0 < a["percentage"] <= 60.0)
            c_61_80 = sum(1 for a in submitted_attempts if 60.0 < a["percentage"] <= 80.0)
            c_81_100 = sum(1 for a in submitted_attempts if a["percentage"] > 80.0)

            score_dist = {
                "0_20": c_0_20,
                "21_40": c_21_40,
                "41_60": c_41_60,
                "61_80": c_61_80,
                "81_100": c_81_100,
                "0-20%": c_0_20,
                "21-40%": c_21_40,
                "41-60%": c_41_60,
                "61-80%": c_61_80,
                "81-100%": c_81_100,
            }

            # Grade distribution
            g_aplus = sum(1 for a in submitted_attempts if a["percentage"] >= 90.0)
            g_a = sum(1 for a in submitted_attempts if 80.0 <= a["percentage"] < 90.0)
            g_b = sum(1 for a in submitted_attempts if 70.0 <= a["percentage"] < 80.0)
            g_c = sum(1 for a in submitted_attempts if 60.0 <= a["percentage"] < 70.0)
            g_d = sum(1 for a in submitted_attempts if 50.0 <= a["percentage"] < 60.0)
            g_f = sum(1 for a in submitted_attempts if a["percentage"] < 50.0)

            grade_dist = {
                "A_plus": g_aplus,
                "A+": g_aplus,
                "A": g_a,
                "B": g_b,
                "C": g_c,
                "D": g_d,
                "F": g_f,
            }

            # Subject-wise performance (Bar Chart data)
            sub_map = {}
            for a in submitted_attempts:
                s_name = a["subject_name"]
                if s_name not in sub_map:
                    sub_map[s_name] = {
                        "subject_name": s_name,
                        "subject_id": a["subject_id"],
                        "exam_count": set(),
                        "candidates_count": 0,
                        "marks_list": [],
                        "max_marks_list": [],
                        "pct_list": [],
                        "passed": 0,
                    }
                entry = sub_map[s_name]
                entry["exam_count"].add(a["exam_id"])
                entry["candidates_count"] += 1
                entry["marks_list"].append(a["marks_obtained"])
                entry["max_marks_list"].append(a.get("max_marks", 100.0))
                entry["pct_list"].append(a["percentage"])
                if a["marks_obtained"] >= (a.get("pass_marks") or 0):
                    entry["passed"] += 1

            subject_performance = []
            for s_name, edata in sub_map.items():
                m_list = edata["marks_list"]
                mx_list = edata["max_marks_list"]
                s_tot_obt = sum(m_list)
                s_tot_max = sum(mx_list)
                s_avg_pct = (s_tot_obt / s_tot_max * 100.0) if s_tot_max > 0 else 0.0
                s_avg_m = (s_tot_obt / len(m_list)) if m_list else 0.0
                s_pass_pct = (edata["passed"] / edata["candidates_count"] * 100.0) if edata["candidates_count"] else 0.0
                s_high = max(m_list) if m_list else 0.0
                s_low = min(m_list) if m_list else 0.0

                subject_performance.append({
                    "subject_name": s_name,
                    "subject_id": edata["subject_id"],
                    "exam_count": len(edata["exam_count"]),
                    "candidates_evaluated": edata["candidates_count"],
                    "avg_marks": round(s_avg_m, 1),
                    "average_marks": round(s_avg_m, 1),
                    "avg_percentage": round(s_avg_pct, 1),
                    "average_percentage": round(s_avg_pct, 1),
                    "highest_score": round(s_high, 1),
                    "highest_marks": round(s_high, 1),
                    "lowest_score": round(s_low, 1),
                    "lowest_marks": round(s_low, 1),
                    "pass_percentage": round(s_pass_pct, 1),
                    "pass_rate": round(s_pass_pct, 1),
                })
            subject_performance.sort(key=lambda x: -x["average_percentage"])

            # Student answers for topic mastery
            student_topic_map = {}
            sa_q = f"""
                SELECT 
                    sa.attempt_id, a.student_id,
                    COALESCE(NULLIF(q.topic, ''), s.name, 'General') AS topic_name,
                    qo.is_correct
                FROM student_answers sa
                JOIN exam_attempts a ON a.attempt_id = sa.attempt_id
                JOIN questions q ON q.question_id = sa.question_id
                JOIN exams e ON e.exam_id = a.exam_id
                JOIN question_banks qb ON qb.bank_id = e.bank_id
                JOIN subjects s ON s.subject_id = qb.subject_id
                LEFT JOIN question_options qo ON qo.option_id = sa.option_id
                WHERE a.exam_id IN ({placeholders}) AND a.status = 'submitted'
            """
            sa_rows = conn.execute(sa_q, tuple(matching_exam_ids)).fetchall()
            for sa in sa_rows:
                sid = sa["student_id"]
                top = sa["topic_name"]
                if sid not in student_topic_map:
                    student_topic_map[sid] = {}
                if top not in student_topic_map[sid]:
                    student_topic_map[sid][top] = {"total": 0, "correct": 0}
                student_topic_map[sid][top]["total"] += 1
                if sa["is_correct"] == 1:
                    student_topic_map[sid][top]["correct"] += 1

            # Student Rankings & Individual Analytics (PART 2 & PART 3)
            student_agg = {}
            for a in submitted_attempts:
                sid = a["student_id"]
                if sid not in student_agg:
                    student_agg[sid] = {
                        "student_id": sid,
                        "name": a["student_name"],
                        "usn": a["usn"],
                        "email": a["student_email"],
                        "department": a["department"],
                        "semester": a["semester"],
                        "attempts_count": 0,
                        "total_marks_scored": 0.0,
                        "total_max_marks": 0.0,
                        "marks_list": [],
                        "pct_list": [],
                        "passed_exams_count": 0,
                        "total_time_seconds": 0,
                        "correct_answers": 0,
                        "incorrect_answers": 0,
                        "unanswered_questions": 0,
                        "all_passed": True,
                    }
                s_entry = student_agg[sid]
                s_entry["attempts_count"] += 1
                s_entry["total_marks_scored"] += a["marks_obtained"]
                s_entry["total_max_marks"] += a.get("max_marks", 100.0)
                s_entry["marks_list"].append(a["marks_obtained"])
                s_entry["pct_list"].append(a["percentage"])
                s_entry["total_time_seconds"] += (a.get("time_taken_seconds") or 0)
                s_entry["correct_answers"] += (a.get("correct_answers") or 0)
                s_entry["incorrect_answers"] += (a.get("incorrect_answers") or 0)
                s_entry["unanswered_questions"] += (a.get("unanswered") or 0)
                if a["marks_obtained"] >= (a.get("pass_marks") or 0):
                    s_entry["passed_exams_count"] += 1
                else:
                    s_entry["all_passed"] = False

            ranked_students = []
            for sid, sdata in student_agg.items():
                p_avg = (sdata["total_marks_scored"] / sdata["total_max_marks"] * 100.0) if sdata["total_max_marks"] > 0 else 0.0
                grade = "A+" if p_avg >= 90.0 else "A" if p_avg >= 80.0 else "B" if p_avg >= 70.0 else "C" if p_avg >= 60.0 else "D" if p_avg >= 50.0 else "F"
                status = "Pass" if p_avg >= 50.0 and sdata["all_passed"] else "Fail"
                overall = "Excellent" if p_avg >= 80.0 else "Good" if p_avg >= 65.0 else "Average" if p_avg >= 50.0 else "Needs Improvement"
                avg_m = (sdata["total_marks_scored"] / sdata["attempts_count"]) if sdata["attempts_count"] else 0.0
                p_rate = (sdata["passed_exams_count"] / sdata["attempts_count"] * 100.0) if sdata["attempts_count"] else 0.0

                # Topics strengths & weaknesses for this student
                s_topics = student_topic_map.get(sid, {})
                strengths = []
                weaknesses = []
                for tname, tstat in s_topics.items():
                    acc = (tstat["correct"] / tstat["total"] * 100.0) if tstat["total"] > 0 else 0.0
                    if acc >= 60.0:
                        strengths.append(tname)
                    else:
                        weaknesses.append(tname)

                m_time = sdata["total_time_seconds"] // sdata["attempts_count"] if sdata["attempts_count"] else 0
                time_str = f"{m_time // 60}m {m_time % 60}s" if m_time else "N/A"

                ranked_students.append({
                    "student_id": sid,
                    "student_name": sdata["name"],
                    "name": sdata["name"],
                    "registration_number": sdata["usn"],
                    "usn": sdata["usn"],
                    "email": sdata["email"],
                    "department": sdata["department"],
                    "semester": sdata["semester"],
                    "attempts_count": sdata["attempts_count"],
                    "total_examinations_attempted": sdata["attempts_count"],
                    "total_marks": round(sdata["total_marks_scored"], 1),
                    "total_marks_obtained": round(sdata["total_marks_scored"], 1),
                    "total_max_marks": round(sdata["total_max_marks"], 1),
                    "maximum_marks": round(sdata["total_max_marks"], 1),
                    "avg_marks": round(avg_m, 1),
                    "average_marks": round(avg_m, 1),
                    "avg_percentage": round(p_avg, 1),
                    "average_percentage": round(p_avg, 1),
                    "highest_score": round(max(sdata["marks_list"]), 1) if sdata["marks_list"] else 0.0,
                    "highest_marks": round(max(sdata["marks_list"]), 1) if sdata["marks_list"] else 0.0,
                    "highest_percentage": round(max(sdata["pct_list"]), 1) if sdata["pct_list"] else 0.0,
                    "lowest_score": round(min(sdata["marks_list"]), 1) if sdata["marks_list"] else 0.0,
                    "lowest_marks": round(min(sdata["marks_list"]), 1) if sdata["marks_list"] else 0.0,
                    "lowest_percentage": round(min(sdata["pct_list"]), 1) if sdata["pct_list"] else 0.0,
                    "pass_percentage": round(p_rate, 1),
                    "pass_rate": round(p_rate, 1),
                    "grade": grade,
                    "status": status,
                    "pass_fail_status": status,
                    "overall_performance": overall,
                    "avg_time_taken": time_str,
                    "time_taken": time_str,
                    "correct_answers": sdata["correct_answers"],
                    "incorrect_answers": sdata["incorrect_answers"],
                    "unanswered_questions": sdata["unanswered_questions"],
                    "topic_strengths": strengths[:4],
                    "topic_weaknesses": weaknesses[:4],
                })

            ranked_students.sort(key=lambda x: -x["average_percentage"])
            for rank_idx, st in enumerate(ranked_students, 1):
                st["rank"] = rank_idx

            top_students = ranked_students[:5]
            support_needed_students = [
                s for s in ranked_students if s["average_percentage"] < 50.0 or s["status"] in ("Fail", "Failed")
            ]

            # Examination comparison & specific analytics (PART 1 & PART 3)
            exam_analytics = []
            for ex in matching_exams:
                eid = ex["exam_id"]
                ex_att = [a for a in all_attempts if a["exam_id"] == eid]
                ex_sub = [a for a in submitted_attempts if a["exam_id"] == eid]

                ex_dept = ex.get("department")
                ex_sem = ex.get("semester")
                ex_c_q = "SELECT COUNT(*) AS c FROM students WHERE 1=1"
                ex_c_p = []
                if ex_dept:
                    ex_c_q += " AND department = ?"
                    ex_c_p.append(ex_dept)
                if ex_sem:
                    ex_c_q += " AND semester = ?"
                    ex_c_p.append(ex_sem)
                ex_c_row = conn.execute(ex_c_q, tuple(ex_c_p)).fetchone()
                ex_cohort_cnt = ex_c_row["c"] if ex_c_row and ex_c_row["c"] > 0 else (total_students_cohort if total_students_cohort > 0 else len(ex_att))
                ex_enrolled = max(len(set(a["student_id"] for a in ex_att)), ex_cohort_cnt)
                ex_attempted = len(set(a["student_id"] for a in ex_att))
                ex_unattempted = max(0, ex_enrolled - ex_attempted)

                if ex_sub:
                    ex_tot_obt = sum(a["marks_obtained"] for a in ex_sub)
                    ex_tot_max = sum(a.get("max_marks", 100.0) for a in ex_sub)
                    e_avg_m = ex_tot_obt / len(ex_sub)
                    e_avg_p = (ex_tot_obt / ex_tot_max * 100.0) if ex_tot_max > 0 else 0.0
                    e_high = max(a["marks_obtained"] for a in ex_sub)
                    e_low = min(a["marks_obtained"] for a in ex_sub)
                    e_pass = sum(1 for a in ex_sub if a["marks_obtained"] >= (ex.get("pass_marks") or 0))
                    e_pass_pct = (e_pass / len(ex_sub)) * 100.0
                    e_fail_pct = 100.0 - e_pass_pct
                else:
                    e_avg_m = 0.0
                    e_avg_p = 0.0
                    e_high = 0.0
                    e_low = 0.0
                    e_pass_pct = 0.0
                    e_fail_pct = 0.0

                ec_0_20 = sum(1 for a in ex_sub if a["percentage"] <= 20.0)
                ec_21_40 = sum(1 for a in ex_sub if 20.0 < a["percentage"] <= 40.0)
                ec_41_60 = sum(1 for a in ex_sub if 40.0 < a["percentage"] <= 60.0)
                ec_61_80 = sum(1 for a in ex_sub if 60.0 < a["percentage"] <= 80.0)
                ec_81_100 = sum(1 for a in ex_sub if a["percentage"] > 80.0)

                e_dist = {
                    "0_20": ec_0_20,
                    "21_40": ec_21_40,
                    "41_60": ec_41_60,
                    "61_80": ec_61_80,
                    "81_100": ec_81_100,
                    "0-20%": ec_0_20,
                    "21-40%": ec_21_40,
                    "41-60%": ec_41_60,
                    "61-80%": ec_61_80,
                    "81-100%": ec_81_100,
                }

                exam_analytics.append({
                    "exam_id": eid,
                    "name": ex["name"],
                    "exam_name": ex["name"],
                    "subject_name": ex["subject_name"],
                    "department": ex["department"],
                    "semester": ex["semester"],
                    "duration_minutes": ex["duration_minutes"],
                    "max_marks": ex["max_marks"],
                    "pass_marks": ex["pass_marks"],
                    "enrolled_count": ex_enrolled,
                    "attempted_count": ex_attempted,
                    "unattempted_count": ex_unattempted,
                    "submitted_count": len(ex_sub),
                    "avg_marks": round(e_avg_m, 1),
                    "average_marks": round(e_avg_m, 1),
                    "avg_percentage": round(e_avg_p, 1),
                    "average_percentage": round(e_avg_p, 1),
                    "highest_marks": round(e_high, 1),
                    "highest_score": round(e_high, 1),
                    "lowest_marks": round(e_low, 1),
                    "lowest_score": round(e_low, 1),
                    "pass_percentage": round(e_pass_pct, 1),
                    "pass_rate": round(e_pass_pct, 1),
                    "failure_percentage": round(e_fail_pct, 1),
                    "fail_rate": round(e_fail_pct, 1),
                    "score_distribution": e_dist,
                })

            # Question-wise accuracy & difficulty analysis (PART 1 & PART 4)
            q_query = f"""
                SELECT 
                    q.question_id, q.exam_id, q.statement,
                    COALESCE(NULLIF(q.topic, ''), 'General') AS topic,
                    q.difficulty, q.marks, q.negative_marks, q.question_type,
                    e.name AS exam_name
                FROM questions q
                JOIN exams e ON e.exam_id = q.exam_id
                WHERE q.exam_id IN ({placeholders})
                ORDER BY q.sort_order, q.question_id
            """
            q_rows = conn.execute(q_query, tuple(matching_exam_ids)).fetchall()

            question_accuracy = []
            for qr in q_rows:
                qid = qr["question_id"]
                ans_stats = conn.execute(
                    """
                    SELECT 
                        COUNT(sa.answer_id) AS total_answers,
                        SUM(CASE WHEN qo.is_correct = 1 THEN 1 ELSE 0 END) AS correct_count,
                        SUM(CASE WHEN qo.is_correct = 0 OR qo.is_correct IS NULL THEN 1 ELSE 0 END) AS incorrect_count
                    FROM student_answers sa
                    JOIN exam_attempts a ON a.attempt_id = sa.attempt_id AND a.status = 'submitted'
                    LEFT JOIN question_options qo ON qo.option_id = sa.option_id
                    WHERE sa.question_id = ?
                    """,
                    (qid,),
                ).fetchone()

                total_ans = ans_stats["total_answers"] if ans_stats else 0
                corr = ans_stats["correct_count"] if ans_stats and ans_stats["correct_count"] else 0
                incorr = ans_stats["incorrect_count"] if ans_stats and ans_stats["incorrect_count"] else 0
                tot_eval = len([a for a in submitted_attempts if a["exam_id"] == qr["exam_id"]])
                unanswered = max(0, tot_eval - total_ans)
                accuracy = round((corr / tot_eval * 100.0), 1) if tot_eval > 0 else (round((corr / total_ans * 100.0), 1) if total_ans > 0 else 0.0)

                question_accuracy.append({
                    "question_id": qid,
                    "exam_id": qr["exam_id"],
                    "exam_name": qr["exam_name"],
                    "statement": qr["statement"],
                    "topic": qr["topic"],
                    "difficulty": qr["difficulty"],
                    "marks": qr["marks"],
                    "question_type": qr["question_type"],
                    "total_candidates": tot_eval,
                    "correct_count": corr,
                    "incorrect_count": incorr,
                    "unanswered_count": unanswered,
                    "accuracy": accuracy,
                })

            # Easiest & Most Difficult questions
            q_sorted_acc = sorted(question_accuracy, key=lambda x: x["accuracy"])
            most_difficult_questions = q_sorted_acc[:5]
            easiest_questions = sorted(question_accuracy, key=lambda x: -x["accuracy"])[:5]

            # Performance trends across chronological dates (Line Chart data)
            trends_sorted = sorted(
                submitted_attempts,
                key=lambda x: x["submitted_at"] or x["started_at"] or "",
            )
            performance_trends = []
            for t in trends_sorted:
                dt_str = (t["submitted_at"] or t["started_at"] or "")[:10]
                performance_trends.append({
                    "date": dt_str,
                    "exam_name": t["exam_name"],
                    "subject_name": t["subject_name"],
                    "student_name": t["student_name"],
                    "marks_obtained": t["marks_obtained"],
                    "max_marks": t["max_marks"],
                    "percentage": round(float(t["percentage"]), 1),
                })

            return {
                "overview": overview,
                "score_distribution": score_dist,
                "grade_distribution": grade_dist,
                "subject_performance": subject_performance,
                "exam_analytics": exam_analytics,
                "student_rankings": ranked_students,
                "top_students": top_students,
                "support_needed_students": support_needed_students,
                "performance_trends": performance_trends,
                "question_accuracy": question_accuracy,
                "easiest_questions": easiest_questions,
                "most_difficult_questions": most_difficult_questions,
            }

    def list_bank_questions(
        self,
        user_id: int,
        role: str = "Teacher",
        search: Optional[str] = None,
        subject_id: Optional[int] = None,
        topic: Optional[str] = None,
        difficulty: Optional[str] = None,
        question_type: Optional[str] = None,
        marks: Optional[float] = None,
        sort_by: str = "newest",
        page: int = 1,
        per_page: int = 15,
    ) -> dict:
        with self.get_connection() as conn:
            # 1. Fetch reusable questions from bank_questions
            if role == "Teacher":
                bq_rows = conn.execute("""
                    SELECT bq.bank_question_id, bq.bank_question_id AS id, 'bank' AS source,
                           bq.statement, bq.marks, bq.negative_marks, bq.question_type,
                           bq.difficulty, bq.topic, bq.image_url, bq.explanation,
                           bq.correct_answer_text, bq.created_by, bq.created_at, bq.updated_at,
                           s.subject_id, s.name AS subject_name,
                           COALESCE(u.name, 'Academic Faculty') AS creator_name,
                           COALESCE(u.role, 'Faculty') AS creator_role
                    FROM bank_questions bq
                    JOIN subjects s ON s.subject_id = bq.subject_id
                    LEFT JOIN admin_users u ON u.admin_user_id = bq.created_by
                    WHERE (bq.created_by = ? OR bq.created_by IS NULL)
                    ORDER BY bq.bank_question_id DESC
                """, (user_id,)).fetchall()
            else:
                bq_rows = conn.execute("""
                    SELECT bq.bank_question_id, bq.bank_question_id AS id, 'bank' AS source,
                           bq.statement, bq.marks, bq.negative_marks, bq.question_type,
                           bq.difficulty, bq.topic, bq.image_url, bq.explanation,
                           bq.correct_answer_text, bq.created_by, bq.created_at, bq.updated_at,
                           s.subject_id, s.name AS subject_name,
                           COALESCE(u.name, 'Academic Faculty') AS creator_name,
                           COALESCE(u.role, 'Faculty') AS creator_role
                    FROM bank_questions bq
                    JOIN subjects s ON s.subject_id = bq.subject_id
                    LEFT JOIN admin_users u ON u.admin_user_id = bq.created_by
                    ORDER BY bq.bank_question_id DESC
                """).fetchall()

            # 2. Also fetch exam questions from authorised exams
            if role == "Teacher":
                eq_rows = conn.execute("""
                    SELECT q.question_id AS id, 'exam' AS source,
                           q.statement, q.marks, q.negative_marks, q.question_type,
                           q.difficulty, q.topic, q.image_url, q.explanation,
                           q.correct_answer_text, e.created_by, NULL AS created_at, NULL AS updated_at,
                           s.subject_id, s.name AS subject_name,
                           e.name AS creator_name, 'Examination' AS creator_role
                    FROM questions q
                    JOIN exams e ON e.exam_id = q.exam_id
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    JOIN teacher_exam_permissions p ON p.exam_id = e.exam_id
                    WHERE p.admin_user_id = ?
                    ORDER BY q.question_id DESC
                """, (user_id,)).fetchall()
            else:
                eq_rows = conn.execute("""
                    SELECT q.question_id AS id, 'exam' AS source,
                           q.statement, q.marks, q.negative_marks, q.question_type,
                           q.difficulty, q.topic, q.image_url, q.explanation,
                           q.correct_answer_text, e.created_by, NULL AS created_at, NULL AS updated_at,
                           s.subject_id, s.name AS subject_name,
                           e.name AS creator_name, 'Examination' AS creator_role
                    FROM questions q
                    JOIN exams e ON e.exam_id = q.exam_id
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    ORDER BY q.question_id DESC
                """).fetchall()

            items = []
            seen_statements = set()

            for r in bq_rows:
                item = dict(r)
                opts = conn.execute("""
                    SELECT option_id, option_text, is_correct, sort_order
                    FROM bank_question_options
                    WHERE bank_question_id = ?
                    ORDER BY sort_order, option_id
                """, (item["bank_question_id"],)).fetchall()
                item["options"] = [dict(o) for o in opts]
                item["can_edit"] = (role == "Super Admin" or item["created_by"] == user_id)
                item["can_delete"] = (role == "Super Admin" or item["created_by"] == user_id)
                items.append(item)
                seen_statements.add(item["statement"].strip())

            for r in eq_rows:
                stmt = r["statement"].strip()
                if stmt in seen_statements:
                    continue
                item = dict(r)
                item["bank_question_id"] = -item["id"]  # negative to indicate exam origin
                opts = conn.execute("""
                    SELECT option_id, option_text, is_correct, sort_order
                    FROM question_options
                    WHERE question_id = ?
                    ORDER BY sort_order, option_id
                """, (item["id"],)).fetchall()
                item["options"] = [dict(o) for o in opts]
                item["can_edit"] = (role == "Super Admin")
                item["can_delete"] = (role == "Super Admin")
                items.append(item)
                seen_statements.add(stmt)

            # 3. Filters
            filtered = []
            search_clean = (search or "").strip().lower()
            topic_clean = (topic or "").strip().lower()

            for q in items:
                if search_clean:
                    match = (
                        search_clean in q["statement"].lower()
                        or search_clean in q.get("topic", "").lower()
                        or search_clean in q.get("explanation", "").lower()
                        or any(search_clean in opt.get("option_text", "").lower() for opt in q.get("options", []))
                    )
                    if not match:
                        continue

                if subject_id and q["subject_id"] != subject_id:
                    continue

                if topic_clean and topic_clean not in q.get("topic", "").lower():
                    continue

                if difficulty and q.get("difficulty", "").lower() != difficulty.lower():
                    continue

                if question_type and q.get("question_type", "").lower() != question_type.lower():
                    continue

                if marks is not None and abs(float(q.get("marks", 0)) - float(marks)) > 0.01:
                    continue

                filtered.append(q)

            # 4. Sorting
            if sort_by == "oldest":
                filtered.sort(key=lambda x: str(x.get("created_at") or ""))
            elif sort_by == "marks_desc":
                filtered.sort(key=lambda x: (-float(x.get("marks", 0)), x.get("statement", "")))
            elif sort_by == "marks_asc":
                filtered.sort(key=lambda x: (float(x.get("marks", 0)), x.get("statement", "")))
            elif sort_by == "difficulty":
                diff_map = {"Easy": 1, "Medium": 2, "Hard": 3}
                filtered.sort(key=lambda x: (diff_map.get(x.get("difficulty", "Medium"), 2), x.get("statement", "")))
            elif sort_by == "subject":
                filtered.sort(key=lambda x: (x.get("subject_name", "").lower(), x.get("statement", "")))
            elif sort_by == "statement":
                filtered.sort(key=lambda x: x.get("statement", "").lower())
            else:  # newest
                filtered.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)

            # 5. Pagination
            total_items = len(filtered)
            per_page = max(1, min(per_page, 100))
            total_pages = max(1, math.ceil(total_items / per_page))
            current_page = max(1, min(page, total_pages))

            start_idx = (current_page - 1) * per_page
            end_idx = start_idx + per_page
            paged_items = filtered[start_idx:end_idx]

            return {
                "items": paged_items,
                "total_items": total_items,
                "total_pages": total_pages,
                "current_page": current_page,
                "per_page": per_page,
                "has_prev": current_page > 1,
                "has_next": current_page < total_pages,
            }

    def get_bank_question(self, question_id: int) -> Optional[dict]:
        with self.get_connection() as conn:
            # Positive ID -> bank_questions
            if question_id > 0:
                row = conn.execute("""
                    SELECT bq.*, s.name AS subject_name, u.name AS creator_name, u.role AS creator_role
                    FROM bank_questions bq
                    JOIN subjects s ON s.subject_id = bq.subject_id
                    LEFT JOIN admin_users u ON u.admin_user_id = bq.created_by
                    WHERE bq.bank_question_id = ?
                """, (question_id,)).fetchone()
                if row:
                    item = dict(row)
                    opts = conn.execute("""
                        SELECT option_id, option_text, is_correct, sort_order
                        FROM bank_question_options
                        WHERE bank_question_id = ?
                        ORDER BY sort_order, option_id
                    """, (question_id,)).fetchall()
                    item["options"] = [dict(o) for o in opts]
                    return item

            # Negative ID or fallback -> questions
            actual_q_id = abs(question_id)
            q_row = conn.execute("""
                SELECT q.*, s.name AS subject_name, s.subject_id, e.name AS exam_name
                FROM questions q
                JOIN exams e ON e.exam_id = q.exam_id
                JOIN question_banks qb ON qb.bank_id = e.bank_id
                JOIN subjects s ON s.subject_id = qb.subject_id
                WHERE q.question_id = ?
            """, (actual_q_id,)).fetchone()
            if q_row:
                item = dict(q_row)
                item["bank_question_id"] = -item["question_id"]
                opts = conn.execute("""
                    SELECT option_id, option_text, is_correct, sort_order
                    FROM question_options
                    WHERE question_id = ?
                    ORDER BY sort_order, option_id
                """, (actual_q_id,)).fetchall()
                item["options"] = [dict(o) for o in opts]
                return item
            return None

    def create_bank_question(
        self,
        statement: str,
        subject_id: Optional[int] = None,
        subject_name: Optional[str] = None,
        topic: str = "",
        question_type: str = "mcq",
        difficulty: str = "Medium",
        marks: float = 1.0,
        negative_marking: float = 0.0,
        explanation: str = "",
        correct_answer_text: str = "",
        image_url: str = "",
        created_by: Optional[int] = None,
        options: Optional[list[dict]] = None,
    ) -> int:
        with self.get_connection() as conn:
            target_subject_id = subject_id
            if not target_subject_id and subject_name:
                row = conn.execute("SELECT subject_id FROM subjects WHERE name = ?", (subject_name.strip(),)).fetchone()
                if row:
                    target_subject_id = row["subject_id"]
                else:
                    cur = conn.execute("INSERT INTO subjects (name, description, icon) VALUES (?, '', 'book')", (subject_name.strip(),))
                    target_subject_id = cur.lastrowid
            elif not target_subject_id:
                first = conn.execute("SELECT subject_id FROM subjects ORDER BY subject_id LIMIT 1").fetchone()
                target_subject_id = first["subject_id"] if first else 1

            cur = conn.execute("""
                INSERT INTO bank_questions (
                    subject_id, topic, statement, question_type, difficulty, marks,
                    negative_marks, explanation, correct_answer_text, image_url, created_by,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, (
                target_subject_id,
                (topic or "General").strip(),
                statement.strip(),
                question_type,
                difficulty,
                float(marks),
                float(negative_marking),
                explanation.strip(),
                correct_answer_text.strip(),
                image_url.strip(),
                created_by,
            ))
            bank_q_id = int(cur.lastrowid)

            if options:
                for opt_order, opt in enumerate(options):
                    if isinstance(opt, dict):
                        opt_text = opt.get("option_text", "").strip()
                        is_corr = int(bool(opt.get("is_correct", False)))
                    else:
                        opt_text = str(opt).strip()
                        is_corr = 0
                    if opt_text:
                        conn.execute("""
                            INSERT INTO bank_question_options (bank_question_id, option_text, is_correct, sort_order)
                            VALUES (?, ?, ?, ?)
                        """, (bank_q_id, opt_text, is_corr, opt_order))

            return bank_q_id

    def update_bank_question(
        self,
        bank_question_id: int,
        statement: str,
        subject_id: Optional[int] = None,
        subject_name: Optional[str] = None,
        topic: str = "",
        question_type: str = "mcq",
        difficulty: str = "Medium",
        marks: float = 1.0,
        negative_marking: float = 0.0,
        explanation: str = "",
        correct_answer_text: str = "",
        image_url: str = "",
        user_id: Optional[int] = None,
        role: str = "Teacher",
        options: Optional[list[dict]] = None,
    ) -> bool:
        with self.get_connection() as conn:
            if bank_question_id <= 0:
                # If negative ID, this was an exam question -> promote to bank_questions
                actual_id = abs(bank_question_id)
                q_row = conn.execute("SELECT * FROM questions WHERE question_id = ?", (actual_id,)).fetchone()
                if not q_row:
                    return False
                new_id = self.create_bank_question(
                    statement=statement,
                    subject_id=subject_id,
                    subject_name=subject_name,
                    topic=topic,
                    question_type=question_type,
                    difficulty=difficulty,
                    marks=marks,
                    negative_marking=negative_marking,
                    explanation=explanation,
                    correct_answer_text=correct_answer_text,
                    image_url=image_url,
                    created_by=user_id,
                    options=options,
                )
                return bool(new_id > 0)

            existing = conn.execute("SELECT * FROM bank_questions WHERE bank_question_id = ?", (bank_question_id,)).fetchone()
            if not existing:
                return False
            if role == "Teacher" and user_id and existing["created_by"] and existing["created_by"] != user_id:
                return False

            target_subject_id = subject_id or existing["subject_id"]
            if subject_name and not subject_id:
                row = conn.execute("SELECT subject_id FROM subjects WHERE name = ?", (subject_name.strip(),)).fetchone()
                if row:
                    target_subject_id = row["subject_id"]
                else:
                    cur = conn.execute("INSERT INTO subjects (name, description, icon) VALUES (?, '', 'book')", (subject_name.strip(),))
                    target_subject_id = cur.lastrowid

            conn.execute("""
                UPDATE bank_questions
                SET subject_id = ?, topic = ?, statement = ?, question_type = ?, difficulty = ?,
                    marks = ?, negative_marks = ?, explanation = ?, correct_answer_text = ?,
                    image_url = ?, updated_at = CURRENT_TIMESTAMP
                WHERE bank_question_id = ?
            """, (
                target_subject_id,
                (topic or "General").strip(),
                statement.strip(),
                question_type,
                difficulty,
                float(marks),
                float(negative_marking),
                explanation.strip(),
                correct_answer_text.strip(),
                image_url.strip(),
                bank_question_id,
            ))

            if options is not None:
                conn.execute("DELETE FROM bank_question_options WHERE bank_question_id = ?", (bank_question_id,))
                for opt_order, opt in enumerate(options):
                    if isinstance(opt, dict):
                        opt_text = opt.get("option_text", "").strip()
                        is_corr = int(bool(opt.get("is_correct", False)))
                    else:
                        opt_text = str(opt).strip()
                        is_corr = 0
                    if opt_text:
                        conn.execute("""
                            INSERT INTO bank_question_options (bank_question_id, option_text, is_correct, sort_order)
                            VALUES (?, ?, ?, ?)
                        """, (bank_question_id, opt_text, is_corr, opt_order))
            return True

    def delete_bank_question(
        self,
        bank_question_id: int,
        user_id: Optional[int] = None,
        role: str = "Teacher",
    ) -> bool:
        with self.get_connection() as conn:
            if bank_question_id <= 0:
                actual_id = abs(bank_question_id)
                if role == "Super Admin":
                    conn.execute("DELETE FROM questions WHERE question_id = ?", (actual_id,))
                    return True
                return False

            existing = conn.execute("SELECT * FROM bank_questions WHERE bank_question_id = ?", (bank_question_id,)).fetchone()
            if not existing:
                return False
            if role == "Teacher" and user_id and existing["created_by"] and existing["created_by"] != user_id:
                return False
            conn.execute("DELETE FROM bank_question_options WHERE bank_question_id = ?", (bank_question_id,))
            conn.execute("DELETE FROM bank_questions WHERE bank_question_id = ?", (bank_question_id,))
            return True

    def duplicate_bank_question(
        self,
        bank_question_id: int,
        user_id: Optional[int] = None,
    ) -> Optional[int]:
        q = self.get_bank_question(bank_question_id)
        if not q:
            return None
        new_statement = f"{q['statement']} (Copy)"
        return self.create_bank_question(
            statement=new_statement,
            subject_id=q["subject_id"],
            topic=q.get("topic", "General"),
            question_type=q.get("question_type", "mcq"),
            difficulty=q.get("difficulty", "Medium"),
            marks=q.get("marks", 1),
            negative_marking=q.get("negative_marks", 0),
            explanation=q.get("explanation", ""),
            correct_answer_text=q.get("correct_answer_text", ""),
            image_url=q.get("image_url", ""),
            created_by=user_id,
            options=q.get("options", []),
        )

    def import_bank_questions_to_exam(
        self,
        question_ids: list[int],
        exam_id: int,
        user_id: Optional[int] = None,
        role: str = "Teacher",
    ) -> dict:
        with self.get_connection() as conn:
            exam = conn.execute("SELECT exam_id, name FROM exams WHERE exam_id = ?", (exam_id,)).fetchone()
            if not exam:
                raise ValueError("Target examination does not exist.")
            if role == "Teacher" and user_id:
                perm = conn.execute("SELECT 1 FROM teacher_exam_permissions WHERE admin_user_id = ? AND exam_id = ?", (user_id, exam_id)).fetchone()
                if not perm:
                    raise PermissionError("You do not have permission to modify this examination.")

            max_order_row = conn.execute("SELECT MAX(sort_order) AS max_order FROM questions WHERE exam_id = ?", (exam_id,)).fetchone()
            next_order = (max_order_row["max_order"] if max_order_row and max_order_row["max_order"] is not None else -1) + 1

            imported_count = 0
            for qid in question_ids:
                q = self.get_bank_question(qid)
                if not q:
                    continue
                q_cur = conn.execute("""
                    INSERT INTO questions (
                        exam_id, statement, explanation, marks, sort_order, question_type,
                        difficulty, topic, image_url, negative_marks, correct_answer_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    exam_id,
                    q["statement"],
                    q.get("explanation", ""),
                    float(q.get("marks", 1)),
                    next_order,
                    q.get("question_type", "mcq"),
                    q.get("difficulty", "Medium"),
                    q.get("topic", ""),
                    q.get("image_url", ""),
                    float(q.get("negative_marks", 0)),
                    q.get("correct_answer_text", ""),
                ))
                new_q_id = int(q_cur.lastrowid)
                next_order += 1
                imported_count += 1

                for opt_order, opt in enumerate(q.get("options", [])):
                    conn.execute("""
                        INSERT INTO question_options (question_id, option_text, is_correct, sort_order)
                        VALUES (?, ?, ?, ?)
                    """, (new_q_id, opt["option_text"], int(bool(opt.get("is_correct", False))), opt_order))

            conn.execute("""
                UPDATE exams
                SET max_marks = (SELECT COALESCE(SUM(marks), 0) FROM questions WHERE exam_id = ?)
                WHERE exam_id = ?
            """, (exam_id, exam_id))

            return {
                "success": True,
                "count": imported_count,
                "exam_id": exam_id,
                "exam_name": exam["name"],
            }

    def get_bank_filter_options(self, user_id: int, role: str = "Teacher") -> dict:
        with self.get_connection() as conn:
            subjects = [dict(r) for r in conn.execute("SELECT subject_id, name FROM subjects ORDER BY name").fetchall()]
            topics_rows = conn.execute("SELECT DISTINCT topic FROM bank_questions WHERE topic != '' ORDER BY topic").fetchall()
            topics = [r["topic"] for r in topics_rows]

            if role == "Teacher":
                total_q = conn.execute("SELECT COUNT(*) AS c FROM bank_questions WHERE created_by = ? OR created_by IS NULL", (user_id,)).fetchone()["c"]
                my_q = conn.execute("SELECT COUNT(*) AS c FROM bank_questions WHERE created_by = ?", (user_id,)).fetchone()["c"]
            else:
                total_q = conn.execute("SELECT COUNT(*) AS c FROM bank_questions").fetchone()["c"]
                my_q = conn.execute("SELECT COUNT(*) AS c FROM bank_questions WHERE created_by = ?", (user_id,)).fetchone()["c"]

            return {
                "subjects": subjects,
                "topics": topics,
                "total_questions": total_q,
                "my_questions": my_q,
            }

    def get_authorized_exams_for_user(self, user_id: int, role: str = "Teacher") -> list[dict]:
        with self.get_connection() as conn:
            if role == "Super Admin":
                rows = conn.execute("""
                    SELECT e.exam_id, e.name, s.name AS subject_name, e.duration_minutes, e.max_marks
                    FROM exams e
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    ORDER BY e.name
                """).fetchall()
            else:
                rows = conn.execute("""
                    SELECT e.exam_id, e.name, s.name AS subject_name, e.duration_minutes, e.max_marks
                    FROM exams e
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    JOIN teacher_exam_permissions p ON p.exam_id = e.exam_id
                    WHERE p.admin_user_id = ?
                    ORDER BY e.name
                """, (user_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_reports_filter_options(self, user_id: int, role: str = "Teacher") -> dict:
        """Returns filter options (subjects, exams, departments, semesters, academic_years, students) scoped to user permissions."""
        with self.get_connection() as conn:
            if role == "Teacher" and user_id:
                subj_rows = conn.execute("""
                    SELECT DISTINCT s.subject_id, s.name
                    FROM subjects s
                    JOIN question_banks qb ON qb.subject_id = s.subject_id
                    JOIN exams e ON e.bank_id = qb.bank_id
                    JOIN teacher_exam_permissions p ON p.exam_id = e.exam_id AND p.admin_user_id = ?
                    ORDER BY s.name
                """, (user_id,)).fetchall()
                exams_rows = conn.execute("""
                    SELECT e.exam_id, e.name, s.name AS subject_name, e.department, e.semester, e.academic_year
                    FROM exams e
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    JOIN teacher_exam_permissions p ON p.exam_id = e.exam_id AND p.admin_user_id = ?
                    ORDER BY e.name
                """, (user_id,)).fetchall()
                student_rows = conn.execute("""
                    SELECT DISTINCT st.student_id, st.name AS full_name, st.usn, st.email, st.department, st.semester
                    FROM students st
                    JOIN exam_attempts a ON a.student_id = st.student_id
                    JOIN teacher_exam_permissions p ON p.exam_id = a.exam_id AND p.admin_user_id = ?
                    ORDER BY st.name
                """, (user_id,)).fetchall()
            else:
                subj_rows = conn.execute("SELECT subject_id, name FROM subjects ORDER BY name").fetchall()
                exams_rows = conn.execute("""
                    SELECT e.exam_id, e.name, s.name AS subject_name, e.department, e.semester, e.academic_year
                    FROM exams e
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    ORDER BY e.name
                """).fetchall()
                student_rows = conn.execute("""
                    SELECT student_id, name AS full_name, usn, email, department, semester
                    FROM students
                    ORDER BY name
                """).fetchall()

            dept_set = set()
            sem_set = set()
            year_set = set()
            for er in exams_rows:
                if er["department"]:
                    dept_set.add(er["department"])
                if er["semester"]:
                    sem_set.add(er["semester"])
                if er["academic_year"]:
                    year_set.add(er["academic_year"])
            for sr in student_rows:
                if sr["department"]:
                    dept_set.add(sr["department"])
                if sr["semester"]:
                    sem_set.add(sr["semester"])

            return {
                "subjects": [dict(r) for r in subj_rows],
                "exams": [dict(r) for r in exams_rows],
                "departments": sorted(list(dept_set)) if dept_set else ["Computer Science & Engineering", "Information Technology", "Electronics & Communication"],
                "semesters": sorted(list(sem_set)) if sem_set else ["Semester 1", "Semester 2", "Semester 3", "Semester 4", "Semester 5", "Semester 6", "Semester 7", "Semester 8"],
                "academic_years": sorted(list(year_set), reverse=True) if year_set else ["2025-2026", "2024-2025"],
                "students": [dict(r) for r in student_rows],
            }

    def get_questions_from_bank(self, teacher_id: Optional[int] = None) -> list[dict]:
        with self.get_connection() as conn:
            is_teacher = False
            if teacher_id:
                user = conn.execute("SELECT role FROM admin_users WHERE admin_user_id = ?", (teacher_id,)).fetchone()
                is_teacher = bool(user and user["role"] == "Teacher")

            if is_teacher:
                bq_rows = conn.execute("""
                    SELECT bq.bank_question_id AS question_id, bq.*, s.name AS subject_name, bq.topic AS bank_name, 'Question Bank' AS exam_name
                    FROM bank_questions bq
                    JOIN subjects s ON s.subject_id = bq.subject_id
                    WHERE (bq.created_by = ? OR bq.created_by IS NULL)
                    ORDER BY bq.bank_question_id DESC LIMIT 100
                """, (teacher_id,)).fetchall()
            else:
                bq_rows = conn.execute("""
                    SELECT bq.bank_question_id AS question_id, bq.*, s.name AS subject_name, bq.topic AS bank_name, 'Question Bank' AS exam_name
                    FROM bank_questions bq
                    JOIN subjects s ON s.subject_id = bq.subject_id
                    ORDER BY bq.bank_question_id DESC LIMIT 100
                """).fetchall()

            result = []
            for r in bq_rows:
                item = dict(r)
                options = conn.execute(
                    "SELECT option_id, option_text, is_correct, sort_order FROM bank_question_options WHERE bank_question_id = ? ORDER BY sort_order",
                    (item["question_id"],),
                ).fetchall()
                item["options"] = [dict(o) for o in options]
                result.append(item)

            if is_teacher:
                eq_rows = conn.execute("""
                    SELECT q.*, e.name AS exam_name, s.name AS subject_name, qb.name AS bank_name
                    FROM questions q
                    JOIN exams e ON e.exam_id = q.exam_id
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    JOIN teacher_exam_permissions p ON p.exam_id = e.exam_id
                    WHERE p.admin_user_id = ?
                    ORDER BY q.question_id DESC LIMIT 100
                """, (teacher_id,)).fetchall()
            else:
                eq_rows = conn.execute("""
                    SELECT q.*, e.name AS exam_name, s.name AS subject_name, qb.name AS bank_name
                    FROM questions q
                    JOIN exams e ON e.exam_id = q.exam_id
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    ORDER BY q.question_id DESC LIMIT 100
                """).fetchall()

            existing_statements = {item["statement"] for item in result}
            for r in eq_rows:
                if r["statement"] in existing_statements:
                    continue
                item = dict(r)
                options = conn.execute(
                    "SELECT option_id, option_text, is_correct, sort_order FROM question_options WHERE question_id = ? ORDER BY sort_order",
                    (r["question_id"],),
                ).fetchall()
                item["options"] = [dict(o) for o in options]
                result.append(item)

            return result

    def create_attempt(self, student_id: int, exam_id: int, mode: str = "practice") -> int:
        now_iso = datetime.now().isoformat()
        with self.get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO exam_attempts (student_id, exam_id, mode, started_at) VALUES (?, ?, ?, ?)",
                (student_id, exam_id, mode, now_iso),
            )
            return int(cursor.lastrowid)

    def get_grade_boundaries(self) -> list[dict]:
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM grade_boundaries ORDER BY sort_order ASC, min_score DESC"
            ).fetchall()
            if not rows:
                self._seed_grade_boundaries(conn)
                rows = conn.execute(
                    "SELECT * FROM grade_boundaries ORDER BY sort_order ASC, min_score DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def update_grade_boundaries(self, boundaries: list[dict], user_id: Optional[int] = None) -> bool:
        with self.get_connection() as conn:
            conn.execute("DELETE FROM grade_boundaries")
            for idx, b in enumerate(boundaries, 1):
                grade = str(b.get("grade", "")).strip().upper()
                min_score = float(b.get("min_score", 0))
                max_score = float(b.get("max_score", 100))
                desc = str(b.get("description", "")).strip()
                conn.execute(
                    """
                    INSERT INTO grade_boundaries (grade, min_score, max_score, description, sort_order)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (grade, min_score, max_score, desc, idx),
                )
        if user_id:
            self.log_audit(
                action="grade_boundaries_updated",
                entity_type="system",
                entity_id=0,
                details=f"Updated institutional grade boundaries ({len(boundaries)} grades defined)",
                admin_user_id=user_id,
            )
        return True

    def calculate_grade(self, percentage: float, boundaries: Optional[list[dict]] = None) -> str:
        if boundaries is None:
            boundaries = self.get_grade_boundaries()
        pct = float(percentage)
        for b in sorted(boundaries, key=lambda x: -float(x["min_score"])):
            if pct >= float(b["min_score"]):
                return b["grade"]
        return "F"

    def calculate_pass_fail(self, marks_obtained: float = 0.0, pass_marks: float = 0.0, percentage: float = 0.0, min_pass_percentage: float = 50.0, **kwargs) -> str:
        if "total_marks" in kwargs and not marks_obtained:
            marks_obtained = kwargs["total_marks"]
        if pass_marks and float(pass_marks) > 0:
            return "Passed" if float(marks_obtained) >= float(pass_marks) else "Failed"
        return "Passed" if float(percentage) >= min_pass_percentage else "Failed"

    def save_answer(self, attempt_id: int, question_id: int, option_id: Optional[int] = None, answer_text: Optional[str] = None) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO student_answers (attempt_id, question_id, option_id, answer_text, saved_at)
                VALUES (?, ?, ?, COALESCE(?, ''), CURRENT_TIMESTAMP)
                ON CONFLICT(attempt_id, question_id) DO UPDATE SET
                    option_id = excluded.option_id,
                    answer_text = excluded.answer_text,
                    saved_at = CURRENT_TIMESTAMP
                """,
                (attempt_id, question_id, option_id, answer_text if answer_text is not None else ""),
            )

    save_student_answer = save_answer

    def _evaluate_attempt_questions(self, conn, attempt_id: int, exam: dict) -> dict:
        """
        Evaluates questions for an attempt without SQL join multiplication.
        Accurately scores MCQ, Multiple Select (exact set comparison), True/False,
        Numerical (with tolerance), Fill in the blank, Short Answer, and Long Answer.
        """
        exam_id = exam["exam_id"]
        q_rows = conn.execute(
            """
            SELECT question_id, marks, negative_marks, question_type, correct_answer_text
            FROM questions
            WHERE exam_id = ?
            ORDER BY sort_order, question_id
            """,
            (exam_id,),
        ).fetchall()

        all_options = conn.execute(
            """
            SELECT option_id, question_id, option_text, is_correct
            FROM question_options
            WHERE question_id IN (SELECT question_id FROM questions WHERE exam_id = ?)
            ORDER BY sort_order, option_id
            """,
            (exam_id,),
        ).fetchall()

        all_answers = conn.execute(
            """
            SELECT question_id, option_id, answer_text, marks_awarded
            FROM student_answers
            WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchall()

        options_by_q = {}
        for opt in all_options:
            options_by_q.setdefault(opt["question_id"], []).append(opt)

        answers_by_q = {ans["question_id"]: ans for ans in all_answers}

        exam_neg = float(exam.get("negative_marking") or 0.0)
        correct_count = 0
        incorrect_count = 0
        unanswered_count = 0
        total_marks = 0.0
        has_subjective = False
        all_subjective_evaluated = True

        for q in q_rows:
            qid = q["question_id"]
            qtype = (q["question_type"] or "mcq").strip().lower()
            qmarks = float(q["marks"] or 1.0)
            qneg = float(q["negative_marks"] or 0.0) or exam_neg

            ans = answers_by_q.get(qid)
            opts = options_by_q.get(qid, [])
            correct_opt_ids = {opt["option_id"] for opt in opts if opt["is_correct"] == 1}

            # If instructor has already evaluated this answer
            if ans and ans["marks_awarded"] is not None:
                awarded = float(ans["marks_awarded"])
                total_marks += awarded
                if awarded >= (qmarks * 0.5):
                    correct_count += 1
                else:
                    incorrect_count += 1
                continue

            if qtype in ("long_answer", "descriptive"):
                has_subjective = True
                all_subjective_evaluated = False
                ans_text = (ans["answer_text"] if ans else "") or ""
                if not ans_text.strip():
                    unanswered_count += 1
            elif qtype == "short_answer":
                ans_text = ((ans["answer_text"] if ans else "") or "").strip()
                corr_text = (q["correct_answer_text"] or "").strip()
                if corr_text and ans_text:
                    if ans_text.lower() == corr_text.lower():
                        total_marks += qmarks
                        correct_count += 1
                    else:
                        total_marks -= qneg
                        incorrect_count += 1
                elif not ans_text:
                    unanswered_count += 1
                    if not corr_text:
                        has_subjective = True
                        all_subjective_evaluated = False
                else:
                    has_subjective = True
                    all_subjective_evaluated = False
            elif qtype in ("numerical",):
                ans_text = ((ans["answer_text"] if ans else "") or "").strip()
                corr_text = (q["correct_answer_text"] or "").strip()
                if ans_text:
                    is_corr = False
                    try:
                        f_ans = float(ans_text)
                        f_corr = float(corr_text)
                        if abs(f_ans - f_corr) <= 0.01:
                            is_corr = True
                    except (ValueError, TypeError):
                        if ans_text.lower() == corr_text.lower():
                            is_corr = True
                    if is_corr:
                        total_marks += qmarks
                        correct_count += 1
                    else:
                        total_marks -= qneg
                        incorrect_count += 1
                else:
                    unanswered_count += 1
            elif qtype in ("fill_in_the_blanks", "fill_blank"):
                ans_text = ((ans["answer_text"] if ans else "") or "").strip()
                corr_text = (q["correct_answer_text"] or "").strip()
                correct_opt_texts = {opt["option_text"].strip().lower() for opt in opts if opt["is_correct"] == 1}
                if ans_text:
                    if (corr_text and ans_text.lower() == corr_text.lower()) or (ans_text.lower() in correct_opt_texts):
                        total_marks += qmarks
                        correct_count += 1
                    else:
                        total_marks -= qneg
                        incorrect_count += 1
                else:
                    unanswered_count += 1
            elif qtype == "multiple_select":
                selected_ids = set()
                if ans:
                    if ans["answer_text"]:
                        for part in ans["answer_text"].split(","):
                            p = part.strip()
                            if p.isdigit():
                                selected_ids.add(int(p))
                    if not selected_ids and ans["option_id"]:
                        selected_ids.add(ans["option_id"])

                if selected_ids:
                    if correct_opt_ids and selected_ids == correct_opt_ids:
                        total_marks += qmarks
                        correct_count += 1
                    else:
                        total_marks -= qneg
                        incorrect_count += 1
                else:
                    unanswered_count += 1
            else:
                # mcq, true_false
                opt_id = ans["option_id"] if ans else None
                if opt_id:
                    if correct_opt_ids and opt_id in correct_opt_ids:
                        total_marks += qmarks
                        correct_count += 1
                    else:
                        total_marks -= qneg
                        incorrect_count += 1
                else:
                    unanswered_count += 1

        total_marks = max(0.0, round(total_marks, 2))
        calc_max_marks = sum(float(q["marks"] or 1.0) for q in q_rows)
        max_marks = float(exam.get("max_marks") or 0)
        if max_marks <= 0:
            max_marks = calc_max_marks if calc_max_marks > 0 else 1.0

        percentage = round((total_marks / max_marks * 100.0), 2) if max_marks > 0 else 0.0
        grade = self.calculate_grade(percentage)
        pass_status = self.calculate_pass_fail(total_marks, float(exam.get("pass_marks") or 0), percentage)

        return {
            "correct_count": correct_count,
            "incorrect_count": incorrect_count,
            "unanswered_count": unanswered_count,
            "total_marks": total_marks,
            "max_marks": max_marks,
            "percentage": percentage,
            "grade": grade,
            "pass_status": pass_status,
            "has_subjective": has_subjective,
            "all_subjective_evaluated": all_subjective_evaluated,
        }

    def recalculate_attempt_result(self, attempt_id: int) -> dict:
        with self.get_connection() as conn:
            attempt = conn.execute("SELECT * FROM exam_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            if not attempt:
                return {}
            exam = conn.execute("SELECT * FROM exams WHERE exam_id = ?", (attempt["exam_id"],)).fetchone()
            eval_data = self._evaluate_attempt_questions(conn, attempt_id, dict(exam))

            current_res = conn.execute("SELECT * FROM exam_results WHERE attempt_id = ?", (attempt_id,)).fetchone()
            is_pub = current_res["is_published"] if current_res else (1 if not eval_data["has_subjective"] else 0)
            if eval_data["has_subjective"] and not eval_data["all_subjective_evaluated"]:
                eval_status = "pending_evaluation"
            elif eval_data["has_subjective"] and eval_data["all_subjective_evaluated"]:
                eval_status = "published" if is_pub else "evaluated"
            else:
                eval_status = "evaluated"

            conn.execute(
                """
                UPDATE exam_results
                SET correct_answers = ?, incorrect_answers = ?, unanswered = ?,
                    marks_obtained = ?, percentage = ?, grade = ?, status = ?,
                    evaluation_status = ?, is_published = ?
                WHERE attempt_id = ?
                """,
                (
                    eval_data["correct_count"],
                    eval_data["incorrect_count"],
                    eval_data["unanswered_count"],
                    eval_data["total_marks"],
                    eval_data["percentage"],
                    eval_data["grade"],
                    eval_data["pass_status"],
                    eval_status,
                    is_pub,
                    attempt_id,
                ),
            )
            return dict(conn.execute("SELECT * FROM exam_results WHERE attempt_id = ?", (attempt_id,)).fetchone())

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

            exam = conn.execute("SELECT * FROM exams WHERE exam_id = ?", (attempt["exam_id"],)).fetchone()
            student = conn.execute("SELECT * FROM students WHERE student_id = ?", (attempt["student_id"],)).fetchone()

            now = datetime.now().isoformat(timespec="seconds")
            started = datetime.fromisoformat(attempt["started_at"])
            duration = max(int((datetime.now() - started).total_seconds()), 0)
            conn.execute("UPDATE exam_attempts SET submitted_at = ?, status = 'submitted' WHERE attempt_id = ?", (now, attempt_id))

            eval_data = self._evaluate_attempt_questions(conn, attempt_id, dict(exam))
            has_subjective = eval_data["has_subjective"]
            eval_status = "pending_evaluation" if has_subjective else "evaluated"
            is_published = 0 if has_subjective else 1

            cursor = conn.execute(
                """
                INSERT INTO exam_results (attempt_id, correct_answers, incorrect_answers, unanswered, marks_obtained, percentage, time_taken_seconds, grade, status, is_published, evaluation_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    eval_data["correct_count"],
                    eval_data["incorrect_count"],
                    eval_data["unanswered_count"],
                    eval_data["total_marks"],
                    eval_data["percentage"],
                    duration,
                    eval_data["grade"],
                    eval_data["pass_status"],
                    is_published,
                    eval_status,
                ),
            )
            res_id = cursor.lastrowid
            total_marks = eval_data["total_marks"]
            max_marks = eval_data["max_marks"]
            percentage = eval_data["percentage"]

        # Emit Notifications
        s_name = student["name"] if student else "Student"
        s_usn = student["usn"] if student else "Candidate"
        e_name = exam["name"] if exam else "Examination"

        self.create_notification(
            recipient_type="teacher",
            recipient_id=None,
            title="New Examination Submission",
            message=f"Candidate {s_name} ({s_usn}) submitted attempt for {e_name}.",
            type="new_submission",
            link=f"/admin/evaluations/attempt/{attempt_id}" if has_subjective else "/admin/reports",
        )

        if has_subjective:
            self.create_notification(
                recipient_type="teacher",
                recipient_id=None,
                title="Pending Evaluation Required",
                message=f"Candidate {s_name} submitted subjective answers in {e_name} requiring instructor grading.",
                type="pending_evaluation",
                link=f"/admin/evaluations/attempt/{attempt_id}",
            )
        else:
            if student:
                self.create_notification(
                    recipient_type="student",
                    recipient_id=student["student_id"],
                    title="Examination Results Published",
                    message=f"Your results for {e_name} are now available. Score: {total_marks}/{max_marks} ({percentage}%).",
                    type="results_published",
                    link=f"/attempt/{attempt_id}/result",
                )

        with self.get_connection() as conn:
            return dict(conn.execute("SELECT * FROM exam_results WHERE result_id = ?", (res_id,)).fetchone())

    def list_evaluations(self, user_id: int, role: str = "Teacher", exam_id: Optional[int] = None, status: Optional[str] = None, search: Optional[str] = None) -> list[dict]:
        with self.get_connection() as conn:
            base_q = """
                SELECT a.attempt_id, a.student_id, a.exam_id, a.status AS attempt_status,
                       a.started_at, a.submitted_at,
                       s.name AS student_name, s.usn AS student_usn, s.department AS student_department, s.semester AS student_semester,
                       e.name AS exam_name, e.max_marks, e.pass_marks, sub.name AS subject_name,
                       r.result_id, r.marks_obtained, r.percentage, r.grade, r.status AS pass_status,
                       r.is_published, r.evaluation_status,
                       (SELECT COUNT(*) FROM questions q WHERE q.exam_id = e.exam_id AND q.question_type IN ('short_answer', 'long_answer')) AS total_subjective,
                       (SELECT COUNT(*) FROM student_answers sa JOIN questions q ON q.question_id = sa.question_id WHERE sa.attempt_id = a.attempt_id AND q.question_type IN ('short_answer', 'long_answer') AND sa.marks_awarded IS NOT NULL) AS evaluated_subjective
                FROM exam_attempts a
                JOIN students s ON s.student_id = a.student_id
                JOIN exams e ON e.exam_id = a.exam_id
                JOIN question_banks qb ON qb.bank_id = e.bank_id
                JOIN subjects sub ON sub.subject_id = qb.subject_id
                LEFT JOIN exam_results r ON r.attempt_id = a.attempt_id
            """
            params = []
            where_clauses = ["a.status = 'submitted'"]
            if role == "Teacher" and user_id:
                where_clauses.append("e.exam_id IN (SELECT exam_id FROM teacher_exam_permissions WHERE admin_user_id = ?)")
                params.append(user_id)
            if exam_id:
                where_clauses.append("e.exam_id = ?")
                params.append(exam_id)
            if status:
                if status == "pending":
                    where_clauses.append("r.evaluation_status IN ('pending_evaluation', 'partially_evaluated')")
                elif status == "evaluated":
                    where_clauses.append("r.evaluation_status = 'evaluated'")
                elif status == "published":
                    where_clauses.append("r.is_published = 1")
            if search:
                pattern = f"%{search.strip().lower()}%"
                where_clauses.append("(LOWER(s.name) LIKE ? OR LOWER(s.usn) LIKE ? OR LOWER(e.name) LIKE ?)")
                params.extend([pattern, pattern, pattern])

            query = f"{base_q} WHERE {' AND '.join(where_clauses)} ORDER BY a.submitted_at DESC"
            rows = conn.execute(query, tuple(params)).fetchall()
            return [dict(r) for r in rows]

    def get_attempt_evaluation_detail(self, attempt_id: int, user_id: int = 0, role: str = "Super Admin") -> Optional[dict]:
        with self.get_connection() as conn:
            if role == "Teacher" and user_id:
                perm = conn.execute("""
                    SELECT 1 FROM exam_attempts a
                    JOIN teacher_exam_permissions p ON p.exam_id = a.exam_id
                    WHERE a.attempt_id = ? AND p.admin_user_id = ?
                """, (attempt_id, user_id)).fetchone()
                if not perm:
                    return None

            attempt = conn.execute("""
                SELECT a.*, s.name AS student_name, s.usn AS student_usn, s.email AS student_email,
                       s.department AS student_department, s.semester AS student_semester,
                       e.name AS exam_name, e.max_marks, e.pass_marks, e.duration_minutes, e.negative_marking,
                       sub.name AS subject_name,
                       r.result_id, r.marks_obtained, r.percentage, r.grade, r.status AS pass_status,
                       r.is_published, r.evaluation_status, r.correct_answers, r.incorrect_answers, r.unanswered
                FROM exam_attempts a
                JOIN students s ON s.student_id = a.student_id
                JOIN exams e ON e.exam_id = a.exam_id
                JOIN question_banks qb ON qb.bank_id = e.bank_id
                JOIN subjects sub ON sub.subject_id = qb.subject_id
                LEFT JOIN exam_results r ON r.attempt_id = a.attempt_id
                WHERE a.attempt_id = ?
            """, (attempt_id,)).fetchone()
            if not attempt:
                return None

            att_dict = dict(attempt)

            q_rows = conn.execute("""
                SELECT q.question_id, q.statement, q.explanation, q.marks, q.negative_marks,
                       q.question_type, q.difficulty, q.topic, q.correct_answer_text, q.sort_order,
                       sa.answer_id, sa.option_id, sa.answer_text, sa.marks_awarded, sa.teacher_feedback,
                       sa.evaluated_at, sa.evaluated_by,
                       opt.option_text AS selected_option_text, opt.is_correct AS is_selected_correct,
                       (SELECT option_text FROM question_options WHERE question_id = q.question_id AND is_correct = 1 LIMIT 1) AS correct_option_text,
                       eval_user.name AS evaluated_by_name
                FROM questions q
                LEFT JOIN student_answers sa ON sa.question_id = q.question_id AND sa.attempt_id = ?
                LEFT JOIN question_options opt ON opt.option_id = sa.option_id
                LEFT JOIN admin_users eval_user ON eval_user.admin_user_id = sa.evaluated_by
                WHERE q.exam_id = ?
                ORDER BY q.sort_order, q.question_id
            """, (attempt_id, attempt["exam_id"])).fetchall()

            att_dict["questions"] = [dict(qr) for qr in q_rows]
            att_dict["attempt"] = att_dict
            att_dict["exam"] = {
                "exam_id": attempt["exam_id"],
                "name": attempt["exam_name"],
                "max_marks": attempt["max_marks"],
                "pass_marks": attempt["pass_marks"],
                "subject_name": attempt["subject_name"],
            }
            att_dict["student"] = {
                "student_id": attempt["student_id"],
                "name": attempt["student_name"],
                "usn": attempt["student_usn"],
                "email": attempt["student_email"],
            }
            att_dict["result"] = att_dict
            att_dict["subjective_questions"] = [q for q in att_dict["questions"] if q["question_type"] in ("short_answer", "long_answer")]
            att_dict["objective_questions"] = [q for q in att_dict["questions"] if q["question_type"] not in ("short_answer", "long_answer")]
            return att_dict

    def save_evaluation(self, attempt_id: int, evaluations: list[dict], evaluator_id: int, publish: bool = False) -> dict:
        with self.get_connection() as conn:
            for item in evaluations:
                qid = item.get("question_id")
                marks_aw = item.get("marks_awarded")
                feedback = item.get("feedback", "")
                if qid is not None:
                    conn.execute("""
                        INSERT INTO student_answers (attempt_id, question_id, marks_awarded, teacher_feedback, evaluated_by, evaluated_at)
                        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(attempt_id, question_id) DO UPDATE SET
                            marks_awarded = excluded.marks_awarded,
                            teacher_feedback = excluded.teacher_feedback,
                            evaluated_by = excluded.evaluated_by,
                            evaluated_at = CURRENT_TIMESTAMP
                    """, (attempt_id, qid, marks_aw, feedback, evaluator_id))

        self.recalculate_attempt_result(attempt_id)

        user_info = None
        attempt = None
        with self.get_connection() as conn:
            user_info = conn.execute("SELECT name, role FROM admin_users WHERE admin_user_id = ?", (evaluator_id,)).fetchone()
            attempt = conn.execute("SELECT student_id, exam_id FROM exam_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        u_name = user_info["name"] if user_info else "Teacher"
        u_role = user_info["role"] if user_info else "Teacher"
        exam_id = attempt["exam_id"] if attempt else 0

        self.log_audit(
            action="marks_modification",
            entity_type="attempt",
            entity_id=attempt_id,
            details=f"Evaluated marks & feedback for {len(evaluations)} question(s) on attempt #{attempt_id}",
            admin_user_id=evaluator_id,
            user_name=u_name,
            user_role=u_role,
            exam_id=exam_id,
        )

        if publish:
            self.publish_attempt_result(attempt_id, evaluator_id)

        return self.get_attempt(attempt_id) or {}

    def publish_attempt_result(self, attempt_id: int, admin_user_id: int) -> dict:
        with self.get_connection() as conn:
            conn.execute("""
                UPDATE exam_results
                SET is_published = 1, evaluation_status = 'published'
                WHERE attempt_id = ?
            """, (attempt_id,))
            row = conn.execute("""
                SELECT a.student_id, a.exam_id, s.name AS student_name, e.name AS exam_name,
                       r.marks_obtained, r.percentage, r.grade
                FROM exam_attempts a
                JOIN students s ON s.student_id = a.student_id
                JOIN exams e ON e.exam_id = a.exam_id
                JOIN exam_results r ON r.attempt_id = a.attempt_id
                WHERE a.attempt_id = ?
            """, (attempt_id,)).fetchone()

        if row:
            self.create_notification(
                recipient_type="student",
                recipient_id=row["student_id"],
                title="Examination Results Published",
                message=f"Your verified results for '{row['exam_name']}' are now available. Score: {row['marks_obtained']} ({row['percentage']}%, Grade: {row['grade']}).",
                type="results_published",
                link=f"/attempt/{attempt_id}/result",
            )
            user_info = None
            with self.get_connection() as conn:
                user_info = conn.execute("SELECT name, role FROM admin_users WHERE admin_user_id = ?", (admin_user_id,)).fetchone()
            self.log_audit(
                action="result_publication",
                entity_type="result",
                entity_id=attempt_id,
                details=f"Published evaluation results for candidate {row['student_name']} on {row['exam_name']}",
                admin_user_id=admin_user_id,
                user_name=user_info["name"] if user_info else "",
                user_role=user_info["role"] if user_info else "",
                exam_id=row["exam_id"],
            )

        return self.get_attempt(attempt_id) or {}

    def publish_exam_results(self, exam_id: int, admin_user_id: int) -> int:
        with self.get_connection() as conn:
            attempts = conn.execute("""
                SELECT a.attempt_id FROM exam_attempts a
                JOIN exam_results r ON r.attempt_id = a.attempt_id
                WHERE a.exam_id = ? AND r.is_published = 0
            """, (exam_id,)).fetchall()
            count = 0
            for a in attempts:
                self.publish_attempt_result(a["attempt_id"], admin_user_id)
                count += 1
            return count

    def create_notification(self, recipient_type: str, recipient_id: Optional[int], title: str, message: str, type: str, link: str = "") -> int:
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO notifications (recipient_type, recipient_id, title, message, type, link)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (recipient_type, recipient_id, title, message, type, link),
            )
            return cursor.lastrowid

    def get_notifications(self, recipient_type: str, recipient_id: Optional[int] = None, unread_only: bool = False, limit: int = 50, type_filter: Optional[str] = None) -> list[dict]:
        with self.get_connection() as conn:
            self.sync_system_notifications()

            query = "SELECT * FROM notifications WHERE (recipient_type = ? OR recipient_type = 'all')"
            params = [recipient_type]
            if recipient_id is not None:
                query += " AND (recipient_id = ? OR recipient_id IS NULL)"
                params.append(recipient_id)
            if unread_only:
                query += " AND is_read = 0"
            if type_filter and type_filter != "all":
                query += " AND type = ?"
                params.append(type_filter)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, tuple(params)).fetchall()
            return [dict(r) for r in rows]

    def mark_notification_as_read(self, notification_id: int, recipient_id: Optional[int] = None) -> bool:
        with self.get_connection() as conn:
            query = "UPDATE notifications SET is_read = 1 WHERE notification_id = ?"
            params = [notification_id]
            if recipient_id is not None:
                query += " AND (recipient_id = ? OR recipient_id IS NULL)"
                params.append(recipient_id)
            conn.execute(query, tuple(params))
            return True

    def mark_all_notifications_as_read(self, recipient_type: str, recipient_id: Optional[int] = None) -> int:
        with self.get_connection() as conn:
            query = "UPDATE notifications SET is_read = 1 WHERE (recipient_type = ? OR recipient_type = 'all') AND is_read = 0"
            params = [recipient_type]
            if recipient_id is not None:
                query += " AND (recipient_id = ? OR recipient_id IS NULL)"
                params.append(recipient_id)
            cursor = conn.execute(query, tuple(params))
            return cursor.rowcount

    def get_unread_notification_count(self, recipient_type: str, recipient_id: Optional[int] = None) -> int:
        with self.get_connection() as conn:
            query = "SELECT COUNT(*) AS c FROM notifications WHERE (recipient_type = ? OR recipient_type = 'all') AND is_read = 0"
            params = [recipient_type]
            if recipient_id is not None:
                query += " AND (recipient_id = ? OR recipient_id IS NULL)"
                params.append(recipient_id)
            row = conn.execute(query, tuple(params)).fetchone()
            return row["c"] if row else 0

    def sync_system_notifications(self, user_id: Optional[int] = None, role: Optional[str] = None, student_id: Optional[int] = None) -> None:
        try:
            with self.get_connection() as conn:
                now_str = datetime.now().isoformat()
                upcoming = conn.execute("""
                    SELECT exam_id, name, start_time, department, semester
                    FROM exams
                    WHERE start_time IS NOT NULL AND start_time > ? AND status = 'published'
                """, (now_str,)).fetchall()
                for ex in upcoming:
                    exists = conn.execute(
                        "SELECT 1 FROM notifications WHERE type = 'upcoming_exam' AND link LIKE ?",
                        (f"%exam_id={ex['exam_id']}%",)
                    ).fetchone()
                    if not exists:
                        conn.execute("""
                            INSERT INTO notifications (recipient_type, recipient_id, title, message, type, link)
                            VALUES ('teacher', NULL, 'Upcoming Examination Scheduled', ?, 'upcoming_exam', ?)
                        """, (f"Examination '{ex['name']}' is scheduled to start on {ex['start_time']}.", f"/admin/exams?exam_id={ex['exam_id']}"))

                completed = conn.execute("""
                    SELECT exam_id, name, end_time FROM exams
                    WHERE (status = 'completed' OR (end_time IS NOT NULL AND end_time < ?))
                """, (now_str,)).fetchall()
                for ex in completed:
                    exists = conn.execute(
                        "SELECT 1 FROM notifications WHERE type = 'completed_exam' AND link LIKE ?",
                        (f"%exam_id={ex['exam_id']}%",)
                    ).fetchone()
                    if not exists:
                        conn.execute("""
                            INSERT INTO notifications (recipient_type, recipient_id, title, message, type, link)
                            VALUES ('teacher', NULL, 'Examination Concluded', ?, 'completed_exam', ?)
                        """, (f"Examination window for '{ex['name']}' has closed. All submissions are ready for review.", f"/admin/reports?exam_id={ex['exam_id']}"))
        except Exception:
            pass

    def log_audit(self, action: str, entity_type: str, entity_id: int, details: str, admin_user_id: Optional[int] = None, user_name: str = "", user_role: str = "", exam_id: int = 0) -> int:
        with self.get_connection() as conn:
            if admin_user_id and (not user_name or not user_role):
                user = conn.execute("SELECT name, role FROM admin_users WHERE admin_user_id = ?", (admin_user_id,)).fetchone()
                if user:
                    user_name = user["name"]
                    user_role = user["role"]
            cursor = conn.execute(
                """
                INSERT INTO exam_audit_logs (exam_id, admin_user_id, user_name, user_role, action, entity_type, entity_id, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (exam_id, admin_user_id, user_name, user_role, action, entity_type, entity_id, details),
            )
            return cursor.lastrowid

    def list_audit_logs(self, user_id: int, role: str = "Teacher", action: Optional[str] = None, entity_type: Optional[str] = None, exam_id: Optional[int] = None, search: Optional[str] = None, limit: int = 100, offset: int = 0) -> list[dict]:
        with self.get_connection() as conn:
            where_clauses = ["1=1"]
            params = []
            if role == "Teacher" and user_id:
                where_clauses.append("(admin_user_id = ? OR exam_id IN (SELECT exam_id FROM teacher_exam_permissions WHERE admin_user_id = ?) OR entity_type = 'teacher')")
                params.extend([user_id, user_id])
            if action:
                where_clauses.append("action = ?")
                params.append(action)
            if entity_type:
                where_clauses.append("entity_type = ?")
                params.append(entity_type)
            if exam_id:
                where_clauses.append("exam_id = ?")
                params.append(exam_id)
            if search:
                p = f"%{search.strip().lower()}%"
                where_clauses.append("(LOWER(details) LIKE ? OR LOWER(user_name) LIKE ? OR LOWER(action) LIKE ?)")
                params.extend([p, p, p])

            query = f"""
                SELECT log_id, exam_id, admin_user_id, user_name, user_role, action, entity_type, entity_id, details, timestamp
                FROM exam_audit_logs
                WHERE {' AND '.join(where_clauses)}
                ORDER BY log_id DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            rows = conn.execute(query, tuple(params)).fetchall()
            return [dict(r) for r in rows]

    def get_attempt(self, attempt_id: int) -> Optional[dict]:
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT a.*, e.name AS exam_name, e.duration_minutes, e.max_marks,
                       e.exam_code, s.name AS subject_name
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

            q_rows = conn.execute(
                """
                SELECT q.question_id, q.statement, q.explanation, q.marks, q.negative_marks,
                       q.question_type, q.difficulty, q.topic, q.correct_answer_text,
                       a.option_id, a.answer_text, a.marks_awarded, a.teacher_feedback,
                       opt.option_text AS selected_option_text,
                       opt.is_correct AS is_option_correct
                FROM questions q
                LEFT JOIN student_answers a ON a.question_id = q.question_id AND a.attempt_id = ?
                LEFT JOIN question_options opt ON opt.option_id = a.option_id
                WHERE q.exam_id = ?
                ORDER BY q.sort_order, q.question_id
                """,
                (attempt_id, attempt["exam_id"]),
            ).fetchall()

            # Calculate server-side remaining time
            duration_min = attempt.get("duration_minutes") or 30
            total_seconds = int(duration_min * 60)
            started_at_str = attempt.get("started_at")
            elapsed_seconds = 0
            if started_at_str:
                try:
                    clean_ts = str(started_at_str).replace("Z", "").replace(" ", "T")
                    started_dt = datetime.fromisoformat(clean_ts)
                    diff = (datetime.now() - started_dt).total_seconds()
                    if diff > total_seconds + 3600:
                        from datetime import timezone
                        utc_diff = (datetime.now(timezone.utc).replace(tzinfo=None) - started_dt).total_seconds()
                        if 0 <= utc_diff <= total_seconds + 3600:
                            diff = utc_diff
                    elapsed_seconds = int(max(0, diff))
                except Exception:
                    elapsed_seconds = 0

            remaining_seconds = max(0, total_seconds - elapsed_seconds)
            attempt["total_seconds"] = total_seconds
            attempt["elapsed_seconds"] = elapsed_seconds
            attempt["remaining_seconds"] = remaining_seconds

            is_submitted = (attempt.get("status") == "submitted")
            questions = []
            for qr in q_rows:
                qd = dict(qr)
                if not is_submitted:
                    qd.pop("explanation", None)
                    qd.pop("correct_answer_text", None)
                    qd.pop("is_option_correct", None)
                questions.append(qd)
            attempt["questions"] = questions
            return attempt

    def get_student_by_id(self, student_id: int) -> Optional[dict]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM students WHERE student_id = ?", (student_id,)).fetchone()
            return dict(row) if row else None

    def get_active_attempt(self, student_id: int, exam_id: int) -> Optional[dict]:
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM exam_attempts
                WHERE student_id = ? AND exam_id = ? AND status = 'in_progress'
                ORDER BY attempt_id DESC LIMIT 1
                """,
                (student_id, exam_id),
            ).fetchone()
            return dict(row) if row else None

    def validate_student_exam_access(
        self,
        student_id: int,
        exam_code: Optional[str] = None,
        exam_id: Optional[int] = None,
    ) -> tuple[bool, str, Optional[dict]]:
        with self.get_connection() as conn:
            # 1. Student verification
            student = conn.execute("SELECT * FROM students WHERE student_id = ?", (student_id,)).fetchone()
            if not student:
                return False, "Student account not found or session expired. Please sign in again.", None

            student_dict = dict(student)

            # 2. Locate exam
            exam_row = None
            if exam_code:
                code_clean = str(exam_code).strip().upper()
                exam_row = conn.execute(
                    """
                    SELECT e.*, s.name AS subject_name, s.subject_id
                    FROM exams e
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    WHERE UPPER(e.exam_code) = ?
                    """,
                    (code_clean,),
                ).fetchone()
                if not exam_row:
                    return False, "Invalid examination code. Please check the code and try again.", None
            elif exam_id:
                exam_row = conn.execute(
                    """
                    SELECT e.*, s.name AS subject_name, s.subject_id
                    FROM exams e
                    JOIN question_banks qb ON qb.bank_id = e.bank_id
                    JOIN subjects s ON s.subject_id = qb.subject_id
                    WHERE e.exam_id = ?
                    """,
                    (exam_id,),
                ).fetchone()
                if not exam_row:
                    return False, "Examination not found.", None
            else:
                return False, "Examination code or ID is required.", None

            exam = dict(exam_row)
            eid = exam["exam_id"]

            # 3. Status verification
            exam_status = (exam.get("status") or "").lower().strip()
            if exam_status in ("draft", "cancelled", "archived"):
                return False, "This examination is not available.", exam

            # 4. Question count verification
            q_cnt = conn.execute("SELECT COUNT(*) AS c FROM questions WHERE exam_id = ?", (eid,)).fetchone()["c"]
            if q_cnt == 0:
                return False, "This examination is currently being prepared and has no questions available.", exam

            # 5. Schedule verification
            now = datetime.now()
            start_time_raw = exam.get("start_time")
            if start_time_raw:
                try:
                    st = datetime.fromisoformat(str(start_time_raw).replace("Z", "").replace(" ", "T"))
                    if now < st:
                        formatted_time = st.strftime("%Y-%m-%d %H:%M")
                        return False, f"Examination has not started yet. Scheduled start time: {formatted_time}.", exam
                except Exception:
                    pass

            end_time_raw = exam.get("end_time")
            if end_time_raw:
                try:
                    et = datetime.fromisoformat(str(end_time_raw).replace("Z", "").replace(" ", "T"))
                    if now > et:
                        return False, "Examination has ended and is no longer accepting submissions.", exam
                except Exception:
                    pass

            # 6. Department & Semester eligibility
            exam_dept = (exam.get("department") or "").strip().lower()
            student_dept = (student_dict.get("department") or "").strip().lower()
            if exam_dept and student_dept and exam_dept != student_dept:
                return False, "You are not enrolled in the department required for this examination.", exam

            exam_sem = (exam.get("semester") or "").strip().lower()
            student_sem = (student_dict.get("semester") or "").strip().lower()
            if exam_sem and student_sem:
                exam_sem_num = re.sub(r'[^0-9]', '', exam_sem)
                student_sem_num = re.sub(r'[^0-9]', '', student_sem)
                if exam_sem_num and student_sem_num:
                    if exam_sem_num != student_sem_num:
                        return False, "You are not eligible for this examination based on your current semester.", exam
                elif exam_sem != student_sem:
                    return False, "You are not eligible for this examination based on your current semester.", exam

            # 7. Attempt verification
            attempts = conn.execute(
                "SELECT * FROM exam_attempts WHERE student_id = ? AND exam_id = ? ORDER BY attempt_id DESC",
                (student_id, eid),
            ).fetchall()
            has_in_progress = any(a["status"] == "in_progress" for a in attempts)
            has_submitted = any(a["status"] == "submitted" for a in attempts)

            if has_submitted and not exam.get("allow_multiple_attempts") and not has_in_progress:
                return False, "You have already completed this examination.", exam

            return True, "Access granted.", exam

    def list_available_exams_for_student(self, student_id: int) -> list[dict]:
        with self.get_connection() as conn:
            student = conn.execute("SELECT * FROM students WHERE student_id = ?", (student_id,)).fetchone()
            student_dict = dict(student) if student else {}
            student_dept = (student_dict.get("department") or "").strip().lower()
            student_sem = (student_dict.get("semester") or "").strip().lower()
            student_sem_num = re.sub(r'[^0-9]', '', student_sem)

            rows = conn.execute(
                """
                SELECT e.*, s.name AS subject_name,
                       (SELECT COUNT(*) FROM questions q WHERE q.exam_id = e.exam_id) AS question_count
                FROM exams e
                JOIN question_banks qb ON qb.bank_id = e.bank_id
                JOIN subjects s ON s.subject_id = qb.subject_id
                WHERE e.status IN ('published', 'scheduled', 'live')
                ORDER BY e.exam_id DESC
                """
            ).fetchall()

            now = datetime.now()
            available_list = []

            for r in rows:
                ex = dict(r)
                eid = ex["exam_id"]

                # Check eligibility
                ex_dept = (ex.get("department") or "").strip().lower()
                ex_sem = (ex.get("semester") or "").strip().lower()
                ex_sem_num = re.sub(r'[^0-9]', '', ex_sem)

                dept_match = not ex_dept or not student_dept or (ex_dept == student_dept)
                sem_match = not ex_sem or not student_sem or (
                    (ex_sem_num == student_sem_num) if (ex_sem_num and student_sem_num) else (ex_sem == student_sem)
                )
                is_eligible = dept_match and sem_match

                # Check attempts
                attempts = conn.execute(
                    "SELECT attempt_id, status FROM exam_attempts WHERE student_id = ? AND exam_id = ? ORDER BY attempt_id DESC",
                    (student_id, eid),
                ).fetchall()
                in_prog = next((a for a in attempts if a["status"] == "in_progress"), None)
                submitted = [a for a in attempts if a["status"] == "submitted"]

                # Determine schedule window
                is_upcoming = False
                is_ended = False
                if ex.get("start_time"):
                    try:
                        st = datetime.fromisoformat(str(ex["start_time"]).replace("Z", "").replace(" ", "T"))
                        if now < st:
                            is_upcoming = True
                    except Exception:
                        pass
                if ex.get("end_time"):
                    try:
                        et = datetime.fromisoformat(str(ex["end_time"]).replace("Z", "").replace(" ", "T"))
                        if now > et:
                            is_ended = True
                    except Exception:
                        pass

                active_attempt_id = in_prog["attempt_id"] if in_prog else None

                if in_prog:
                    display_state = "LIVE"
                    state_badge = "IN PROGRESS"
                    state_class = "badge-amber"
                    can_start = True
                    action_text = "Resume Examination"
                elif submitted and not ex.get("allow_multiple_attempts"):
                    display_state = "COMPLETED"
                    state_badge = "COMPLETED"
                    state_class = "badge-gray"
                    can_start = False
                    action_text = "Completed"
                elif is_ended:
                    display_state = "COMPLETED"
                    state_badge = "ENDED"
                    state_class = "badge-gray"
                    can_start = False
                    action_text = "Ended"
                elif is_upcoming:
                    display_state = "UPCOMING"
                    state_badge = "UPCOMING"
                    state_class = "badge-blue"
                    can_start = False
                    action_text = "Starts Soon"
                elif not is_eligible:
                    display_state = "NOT_AVAILABLE"
                    state_badge = "NOT ELIGIBLE"
                    state_class = "badge-red"
                    can_start = False
                    action_text = "Ineligible Cohort"
                elif ex.get("question_count", 0) == 0:
                    display_state = "NOT_AVAILABLE"
                    state_badge = "PREPARING"
                    state_class = "badge-gray"
                    can_start = False
                    action_text = "No Questions"
                else:
                    display_state = "LIVE"
                    state_badge = "LIVE"
                    state_class = "badge-green"
                    can_start = True
                    action_text = "Start Examination"

                ex["display_state"] = display_state
                ex["state_badge"] = state_badge
                ex["state_class"] = state_class
                ex["can_start"] = can_start
                ex["action_text"] = action_text
                ex["is_eligible"] = is_eligible
                ex["active_attempt_id"] = active_attempt_id
                ex["has_completed"] = bool(submitted)
                ex["attempts_count"] = len(attempts)

                available_list.append(ex)

            return available_list

    def list_completed_exams_for_student(self, student_id: int) -> list[dict]:
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT a.attempt_id, a.exam_id, a.started_at, a.submitted_at, a.mode,
                       e.name AS exam_name, e.exam_code, e.max_marks, e.pass_marks,
                       s.name AS subject_name,
                       r.marks_obtained, r.percentage, r.grade, r.status AS result_status,
                       r.time_taken_seconds, r.correct_answers, r.incorrect_answers, r.unanswered
                FROM exam_attempts a
                JOIN exams e ON e.exam_id = a.exam_id
                JOIN question_banks qb ON qb.bank_id = e.bank_id
                JOIN subjects s ON s.subject_id = qb.subject_id
                LEFT JOIN exam_results r ON r.attempt_id = a.attempt_id
                WHERE a.student_id = ? AND a.status = 'submitted'
                ORDER BY a.submitted_at DESC, a.attempt_id DESC
                """,
                (student_id,),
            ).fetchall()

            results = []
            for r in rows:
                item = dict(r)
                time_sec = item.get("time_taken_seconds") or 0
                item["formatted_time"] = f"{time_sec // 60}m {time_sec % 60}s" if time_sec else "N/A"
                item["marks_display"] = f"{item.get('marks_obtained', 0):.1f} / {item.get('max_marks', 0):.1f}"
                item["percentage_display"] = f"{item.get('percentage', 0):.1f}%"
                results.append(item)
            return results

    def add_student(
        self,
        name: str,
        usn: str,
        subject: str = "",
        exam_code: str = "",
        department: str = "",
        semester: str = "",
    ) -> int:
        clean_usn = usn.strip()
        clean_name = name.strip()
        with self.get_connection() as conn:
            existing = conn.execute(
                "SELECT student_id, name, subject, exam_code FROM students WHERE UPPER(TRIM(usn)) = ?",
                (clean_usn.upper(),),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE students
                    SET name = CASE WHEN ? != '' THEN ? ELSE name END,
                        subject = CASE WHEN ? != '' THEN ? ELSE subject END,
                        exam_code = CASE WHEN ? != '' THEN ? ELSE exam_code END,
                        department = CASE WHEN ? != '' THEN ? ELSE department END,
                        semester = CASE WHEN ? != '' THEN ? ELSE semester END
                    WHERE student_id = ?
                    """,
                    (clean_name, clean_name, subject, subject, exam_code, exam_code, department, department, semester, semester, existing["student_id"]),
                )
                return int(existing["student_id"])

            cursor = conn.execute(
                """
                INSERT INTO students (name, usn, subject, exam_code, department, semester, account_status)
                VALUES (?, ?, ?, ?, ?, ?, 'Active')
                """,
                (clean_name, clean_usn, subject, exam_code, department or "Computer Science & Engineering", semester or "Semester 5"),
            )
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

    # =========================================================================
    # SMART QUESTION COMPOSER & DRAFT MANAGEMENT
    # =========================================================================

    def save_composer_draft(
        self,
        user_id: int,
        title: str,
        subject_id: Optional[int],
        sections: list[dict],
        questions: list[dict],
        metadata: Optional[dict] = None,
        draft_id: Optional[int] = None,
    ) -> int:
        title = (title or "Untitled Examination Draft").strip()
        sec_json = json.dumps(sections or [])
        q_json = json.dumps(questions or [])
        meta_json = json.dumps(metadata or {})
        with self.get_connection() as conn:
            if draft_id:
                existing = conn.execute(
                    "SELECT draft_id FROM composer_drafts WHERE draft_id = ? AND user_id = ?",
                    (draft_id, user_id),
                ).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE composer_drafts
                        SET title = ?, subject_id = ?, sections_json = ?, questions_json = ?, metadata_json = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE draft_id = ? AND user_id = ?
                        """,
                        (title, subject_id, sec_json, q_json, meta_json, draft_id, user_id),
                    )
                    return draft_id

            cur = conn.execute(
                """
                INSERT INTO composer_drafts (user_id, title, subject_id, sections_json, questions_json, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (user_id, title, subject_id, sec_json, q_json, meta_json),
            )
            return int(cur.lastrowid)

    def get_composer_draft(self, draft_id: int, user_id: int = 0, role: str = "Teacher") -> Optional[dict]:
        with self.get_connection() as conn:
            if role == "Super Admin":
                row = conn.execute("SELECT * FROM composer_drafts WHERE draft_id = ?", (draft_id,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM composer_drafts WHERE draft_id = ? AND user_id = ?",
                    (draft_id, user_id),
                ).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["sections"] = json.loads(d.get("sections_json") or "[]")
            except Exception:
                d["sections"] = []
            try:
                d["questions"] = json.loads(d.get("questions_json") or "[]")
            except Exception:
                d["questions"] = []
            try:
                d["metadata"] = json.loads(d.get("metadata_json") or "{}")
            except Exception:
                d["metadata"] = {}
            return d

    def list_composer_drafts(self, user_id: int = 0, role: str = "Teacher") -> list[dict]:
        with self.get_connection() as conn:
            if role == "Super Admin":
                rows = conn.execute(
                    """
                    SELECT d.draft_id, d.user_id, d.title, d.subject_id, d.created_at, d.updated_at,
                           s.name AS subject_name, u.name AS author_name,
                           d.questions_json, d.sections_json
                    FROM composer_drafts d
                    LEFT JOIN subjects s ON s.subject_id = d.subject_id
                    LEFT JOIN admin_users u ON u.admin_user_id = d.user_id
                    ORDER BY d.updated_at DESC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT d.draft_id, d.user_id, d.title, d.subject_id, d.created_at, d.updated_at,
                           s.name AS subject_name, u.name AS author_name,
                           d.questions_json, d.sections_json
                    FROM composer_drafts d
                    LEFT JOIN subjects s ON s.subject_id = d.subject_id
                    LEFT JOIN admin_users u ON u.admin_user_id = d.user_id
                    WHERE d.user_id = ?
                    ORDER BY d.updated_at DESC
                    """,
                    (user_id,),
                ).fetchall()

            result = []
            for r in rows:
                item = dict(r)
                item["question_count"] = 0
                item["section_count"] = 0
                try:
                    q_list = json.loads(item.pop("questions_json", "") or "[]")
                    item["question_count"] = len(q_list)
                except Exception:
                    pass
                try:
                    sec_list = json.loads(item.pop("sections_json", "") or "[]")
                    item["section_count"] = len(sec_list)
                except Exception:
                    pass
                result.append(item)
            return result

    def delete_composer_draft(self, draft_id: int, user_id: int = 0, role: str = "Teacher") -> bool:
        with self.get_connection() as conn:
            if role == "Super Admin":
                res = conn.execute("DELETE FROM composer_drafts WHERE draft_id = ?", (draft_id,))
            else:
                res = conn.execute("DELETE FROM composer_drafts WHERE draft_id = ? AND user_id = ?", (draft_id, user_id))
            return res.rowcount > 0

    def bulk_save_to_question_bank(
        self,
        questions: list[dict],
        user_id: int,
        subject_id: int,
        default_topic: str = "General",
    ) -> dict:
        saved_ids = []
        with self.get_connection() as conn:
            for q in questions:
                stmt = str(q.get("statement") or q.get("question_text") or "").strip()
                if not stmt:
                    continue
                q_type = str(q.get("question_type") or q.get("type") or "mcq").strip().lower()
                topic = str(q.get("topic") or default_topic).strip()
                difficulty = str(q.get("difficulty", "Medium")).strip()
                marks = float(q.get("marks", 1.0))
                neg_marks = float(q.get("negative_marks", 0.0))
                expl = str(q.get("explanation", "")).strip()
                corr_text = str(q.get("correct_answer_text") or q.get("correct_answer") or "").strip()
                img_url = str(q.get("image_url", "")).strip()

                cur = conn.execute(
                    """
                    INSERT INTO bank_questions (
                        subject_id, topic, statement, question_type, difficulty, marks,
                        negative_marks, explanation, correct_answer_text, image_url, created_by,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (subject_id, topic, stmt, q_type, difficulty, marks, neg_marks, expl, corr_text, img_url, user_id),
                )
                bq_id = int(cur.lastrowid)
                saved_ids.append(bq_id)

                opts = q.get("options") or []
                for idx, opt in enumerate(opts):
                    if isinstance(opt, str):
                        opt_text = opt.strip()
                        is_corr = 1 if (opt_text.lower() == corr_text.lower() or chr(65 + idx) == corr_text.upper()) else 0
                    elif isinstance(opt, dict):
                        opt_text = str(opt.get("option_text") or opt.get("text") or "").strip()
                        is_corr = 1 if (opt.get("is_correct") or (corr_text and opt_text.lower() == corr_text.lower())) else 0
                    else:
                        continue
                    if not opt_text:
                        continue
                    conn.execute(
                        """
                        INSERT INTO bank_question_options (bank_question_id, option_text, is_correct, sort_order)
                        VALUES (?, ?, ?, ?)
                        """,
                        (bq_id, opt_text, is_corr, idx),
                    )
        return {"saved_count": len(saved_ids), "question_ids": saved_ids}


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