-- ============================================================
-- TaskUp DB Schema — PostgreSQL
-- ============================================================

CREATE TABLE users (
    id              SERIAL          PRIMARY KEY,
    name            VARCHAR(200)    NOT NULL,
    moodle_token    VARCHAR(500)    NOT NULL,
    moodle_user_id  INTEGER         NOT NULL UNIQUE,
    institution     VARCHAR(50)     NOT NULL DEFAULT 'ruppin',
    created_at      TIMESTAMP       DEFAULT NOW(),
    last_updated    TIMESTAMP       DEFAULT NOW(),
    UNIQUE (moodle_user_id, institution)
);

-- ============================================================

CREATE TABLE assignments (
    id              SERIAL          PRIMARY KEY,
    moodle_assign_id INTEGER        NOT NULL,
    title           VARCHAR(500)    NOT NULL,
    course          VARCHAR(300),
    open_date       TIMESTAMP,
    due_date        TIMESTAMP,
    link            VARCHAR(1000)   NOT NULL UNIQUE,   -- מונע כפילויות
    created_at      TIMESTAMP       DEFAULT NOW()
);

-- ============================================================

CREATE TABLE user_assignments (
    id              SERIAL          PRIMARY KEY,
    user_id         INTEGER         NOT NULL REFERENCES users(id),
    assignment_id   INTEGER         NOT NULL REFERENCES assignments(id),
    is_submitted    BOOLEAN         DEFAULT FALSE,
    is_archived     BOOLEAN         DEFAULT FALSE,
    note            TEXT            DEFAULT NULL,

    CONSTRAINT uq_user_assignment UNIQUE (user_id, assignment_id)
);

-- ============================================================

CREATE TABLE refresh_tokens (
    id              SERIAL          PRIMARY KEY,
    user_id         INTEGER         NOT NULL REFERENCES users(id),
    token           VARCHAR(500)    NOT NULL UNIQUE,
    expires_at      TIMESTAMP       NOT NULL,
    created_at      TIMESTAMP       DEFAULT NOW(),
    is_revoked      BOOLEAN         DEFAULT FALSE
);

-- ============================================================

CREATE TABLE notification_settings (
    id                          SERIAL      PRIMARY KEY,
    user_id                     INTEGER     NOT NULL REFERENCES users(id) UNIQUE,
    hours_before                INTEGER[]   DEFAULT '{24}',
    notify_on_new_assignment    BOOLEAN     DEFAULT TRUE,
    notify_on_due_date_change   BOOLEAN     DEFAULT TRUE
);

CREATE TABLE user_devices (
    user_id     INTEGER         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    fcm_token   VARCHAR(500)    NOT NULL,
    PRIMARY KEY (user_id, fcm_token)
);

ALTER TABLE user_assignments ADD COLUMN last_notified_hours INTEGER DEFAULT NULL;

CREATE TABLE courses (
    id               INTEGER PRIMARY KEY, 
    fullname         VARCHAR(300) NOT NULL,
    start_date       INTEGER NOT NULL,    
    end_date         INTEGER NOT NULL     
);

CREATE TABLE user_courses (
    user_id          INTEGER REFERENCES users(id) ON DELETE CASCADE,
    course_id        INTEGER REFERENCES courses(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, course_id)
);

ALTER TABLE assignments ADD COLUMN item_type VARCHAR(50) DEFAULT 'assign';
ALTER TABLE users ADD COLUMN language VARCHAR(10) DEFAULT 'he';