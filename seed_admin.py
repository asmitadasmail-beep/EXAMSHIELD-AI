"""
ExamShield AI - Seed Administrative & Faculty Accounts
Run: py seed_admin.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from werkzeug.security import generate_password_hash
from backend.database.db_manager import DatabaseManager


def main():
    db = DatabaseManager()
    db.init_db()

    with db.get_connection() as conn:
        # 1. Super Admin
        super_admin = conn.execute(
            "SELECT * FROM admin_users WHERE email = 'admin@examshield.edu'"
        ).fetchone()
        if not super_admin:
            conn.execute(
                """
                INSERT INTO admin_users (email, name, password_hash, role, active)
                VALUES (?, ?, ?, 'Super Admin', 1)
                """,
                ("admin@examshield.edu", "Institutional Director", generate_password_hash("AdminPass123!*")),
            )
            print(" [+] Created Super Admin: admin@examshield.edu / AdminPass123!*")
        else:
            print(" [i] Super Admin already exists: admin@examshield.edu")

        # 2. Teacher
        teacher = conn.execute(
            "SELECT * FROM admin_users WHERE email = 'teacher@examshield.edu'"
        ).fetchone()
        if not teacher:
            t_id = conn.execute(
                """
                INSERT INTO admin_users (email, name, password_hash, role, active)
                VALUES (?, ?, ?, 'Teacher', 1)
                """,
                ("teacher@examshield.edu", "Dr. Faculty Instructor", generate_password_hash("TeacherPass123!*")),
            ).lastrowid
            print(" [+] Created Faculty Teacher: teacher@examshield.edu / TeacherPass123!*")

            # Grant permissions on existing exams
            exams = conn.execute("SELECT exam_id FROM exams").fetchall()
            for e in exams:
                conn.execute(
                    "INSERT OR IGNORE INTO teacher_exam_permissions (admin_user_id, exam_id) VALUES (?, ?)",
                    (t_id, e["exam_id"]),
                )
        else:
            print(" [i] Faculty Teacher already exists: teacher@examshield.edu")

    print("\nInitialization Complete. You can now log into http://127.0.0.1:5000/admin/login")


if __name__ == "__main__":
    main()
