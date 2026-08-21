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