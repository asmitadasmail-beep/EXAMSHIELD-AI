CREATE TABLE IF NOT EXISTS students (
    student_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    usn          TEXT NOT NULL,
    subject      TEXT NOT NULL,
    exam_code    TEXT NOT NULL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    email        TEXT,
    password_hash TEXT,
    department   TEXT DEFAULT 'Computer Science & Engineering',
    semester     TEXT DEFAULT 'Semester 5',
    account_status TEXT DEFAULT 'Active'
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
    department TEXT NOT NULL DEFAULT '',
    semester TEXT NOT NULL DEFAULT '',
    academic_year TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    pass_marks REAL NOT NULL DEFAULT 0,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    randomize_questions INTEGER NOT NULL DEFAULT 0,
    randomize_options INTEGER NOT NULL DEFAULT 0,
    allow_multiple_attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'published',
    created_by INTEGER,
    FOREIGN KEY(bank_id) REFERENCES question_banks(bank_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS questions (
    question_id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER NOT NULL,
    statement TEXT NOT NULL,
    explanation TEXT NOT NULL DEFAULT '',
    marks REAL NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    question_type TEXT NOT NULL DEFAULT 'mcq',
    difficulty TEXT NOT NULL DEFAULT 'Medium',
    topic TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    negative_marks REAL NOT NULL DEFAULT 0,
    correct_answer_text TEXT NOT NULL DEFAULT '',
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
    answer_text TEXT NOT NULL DEFAULT '',
    marks_awarded REAL,
    teacher_feedback TEXT NOT NULL DEFAULT '',
    evaluated_by INTEGER,
    evaluated_at TIMESTAMP,
    saved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(attempt_id, question_id),
    FOREIGN KEY(attempt_id) REFERENCES exam_attempts(attempt_id) ON DELETE CASCADE,
    FOREIGN KEY(question_id) REFERENCES questions(question_id),
    FOREIGN KEY(option_id) REFERENCES question_options(option_id),
    FOREIGN KEY(evaluated_by) REFERENCES admin_users(admin_user_id) ON DELETE SET NULL
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
    grade TEXT NOT NULL DEFAULT 'F',
    status TEXT NOT NULL DEFAULT 'Fail',
    is_published INTEGER NOT NULL DEFAULT 1,
    evaluation_status TEXT NOT NULL DEFAULT 'evaluated',
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

CREATE TABLE IF NOT EXISTS admin_users (
    admin_user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('Super Admin', 'Teacher')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS teacher_exam_permissions (
    admin_user_id INTEGER NOT NULL,
    exam_id INTEGER NOT NULL,
    granted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (admin_user_id, exam_id),
    FOREIGN KEY (admin_user_id) REFERENCES admin_users(admin_user_id) ON DELETE CASCADE,
    FOREIGN KEY (exam_id) REFERENCES exams(exam_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_teacher_exam_permissions_exam
    ON teacher_exam_permissions(exam_id);

CREATE TABLE IF NOT EXISTS bank_questions (
    bank_question_id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id       INTEGER NOT NULL,
    topic            TEXT NOT NULL DEFAULT '',
    statement        TEXT NOT NULL,
    question_type    TEXT NOT NULL DEFAULT 'mcq',
    difficulty       TEXT NOT NULL DEFAULT 'Medium',
    marks            REAL NOT NULL DEFAULT 1,
    negative_marks   REAL NOT NULL DEFAULT 0,
    explanation      TEXT NOT NULL DEFAULT '',
    correct_answer_text TEXT NOT NULL DEFAULT '',
    image_url        TEXT NOT NULL DEFAULT '',
    created_by       INTEGER,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE,
    FOREIGN KEY(created_by) REFERENCES admin_users(admin_user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS bank_question_options (
    option_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_question_id INTEGER NOT NULL,
    option_text      TEXT NOT NULL,
    is_correct       INTEGER NOT NULL DEFAULT 0,
    sort_order       INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(bank_question_id) REFERENCES bank_questions(bank_question_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_bank_questions_subject ON bank_questions(subject_id);
CREATE INDEX IF NOT EXISTS idx_bank_questions_created_by ON bank_questions(created_by);
CREATE INDEX IF NOT EXISTS idx_bank_options_question ON bank_question_options(bank_question_id);

CREATE TABLE IF NOT EXISTS exam_audit_logs (
    log_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id       INTEGER DEFAULT 0,
    admin_user_id INTEGER,
    user_name     TEXT NOT NULL DEFAULT '',
    user_role     TEXT NOT NULL DEFAULT '',
    action        TEXT NOT NULL,
    entity_type   TEXT NOT NULL DEFAULT 'exam',
    entity_id     INTEGER NOT NULL DEFAULT 0,
    details       TEXT NOT NULL DEFAULT '',
    timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (admin_user_id) REFERENCES admin_users(admin_user_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_exam ON exam_audit_logs(exam_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON exam_audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON exam_audit_logs(admin_user_id);

CREATE TABLE IF NOT EXISTS grade_boundaries (
    boundary_id INTEGER PRIMARY KEY AUTOINCREMENT,
    grade       TEXT NOT NULL UNIQUE,
    min_score   REAL NOT NULL,
    max_score   REAL NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_type  TEXT NOT NULL CHECK(recipient_type IN ('teacher', 'student', 'admin', 'all')),
    recipient_id    INTEGER,
    title           TEXT NOT NULL,
    message         TEXT NOT NULL,
    type            TEXT NOT NULL,
    link            TEXT NOT NULL DEFAULT '',
    is_read         INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications(recipient_type, recipient_id);
CREATE INDEX IF NOT EXISTS idx_notifications_type ON notifications(type);

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
);

CREATE INDEX IF NOT EXISTS idx_composer_drafts_user ON composer_drafts(user_id);



