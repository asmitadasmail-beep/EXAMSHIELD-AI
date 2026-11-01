CREATE TABLE IF NOT EXISTS students (
    student_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    usn          TEXT NOT NULL,
    subject      TEXT NOT NULL,
    exam_code    TEXT NOT NULL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS exam_sessions (
    session_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id       INTEGER NOT NULL,
    start_time       TIMESTAMP,
    end_time         TIMESTAMP,
    duration_seconds INTEGER,
    status           TEXT DEFAULT 'in_progress',
    risk_score       REAL DEFAULT 0.0,
    risk_category    TEXT,
    FOREIGN KEY (student_id) REFERENCES students(student_id)
);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   INTEGER NOT NULL,
    event_type   TEXT NOT NULL,
    timestamp    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    confidence   REAL,
    severity     TEXT,
    details      TEXT,
    FOREIGN KEY (session_id) REFERENCES exam_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_incidents_session ON incidents(session_id);

CREATE TABLE IF NOT EXISTS subjects (
    subject_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    icon TEXT NOT NULL DEFAULT 'book'
);

CREATE TABLE IF NOT EXISTS question_banks (
    bank_id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    UNIQUE(subject_id, name),
    FOREIGN KEY(subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS exams (
    exam_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 30,
    max_marks INTEGER NOT NULL DEFAULT 0,
    difficulty TEXT NOT NULL DEFAULT 'Mixed',
    negative_marking REAL NOT NULL DEFAULT 0,
    mode TEXT NOT NULL DEFAULT 'practice',
    FOREIGN KEY(bank_id) REFERENCES question_banks(bank_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS questions (
    question_id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER NOT NULL,
    statement TEXT NOT NULL,
    explanation TEXT NOT NULL DEFAULT '',
    marks REAL NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(exam_id) REFERENCES exams(exam_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS question_options (
    option_id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    option_text TEXT NOT NULL,
    is_correct INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(question_id) REFERENCES questions(question_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS exam_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    exam_id INTEGER NOT NULL,
    mode TEXT NOT NULL DEFAULT 'practice',
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    submitted_at TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'in_progress',
    UNIQUE(student_id, exam_id, started_at),
    FOREIGN KEY(student_id) REFERENCES students(student_id),
    FOREIGN KEY(exam_id) REFERENCES exams(exam_id)
);

CREATE TABLE IF NOT EXISTS student_answers (
    answer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    option_id INTEGER,
    saved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(attempt_id, question_id),
    FOREIGN KEY(attempt_id) REFERENCES exam_attempts(attempt_id) ON DELETE CASCADE,
    FOREIGN KEY(question_id) REFERENCES questions(question_id),
    FOREIGN KEY(option_id) REFERENCES question_options(option_id)
);

CREATE TABLE IF NOT EXISTS exam_results (
    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL UNIQUE,
    correct_answers INTEGER NOT NULL DEFAULT 0,
    incorrect_answers INTEGER NOT NULL DEFAULT 0,
    unanswered INTEGER NOT NULL DEFAULT 0,
    marks_obtained REAL NOT NULL DEFAULT 0,
    percentage REAL NOT NULL DEFAULT 0,
    time_taken_seconds INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(attempt_id) REFERENCES exam_attempts(attempt_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS student_face_profiles (
    profile_id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL UNIQUE,
    template BLOB,
    consent_given INTEGER NOT NULL DEFAULT 0,
    registered_at TIMESTAMP,
    FOREIGN KEY(student_id) REFERENCES students(student_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_questions_exam ON questions(exam_id);
CREATE INDEX IF NOT EXISTS idx_attempts_student ON exam_attempts(student_id);
CREATE INDEX IF NOT EXISTS idx_answers_attempt ON student_answers(attempt_id);