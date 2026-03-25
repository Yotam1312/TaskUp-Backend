-- ============================================================
-- TaskUp DB Schema — PostgreSQL
-- ============================================================

CREATE TABLE users (
    id              SERIAL          PRIMARY KEY,
    name            VARCHAR(200)    NOT NULL,
    moodle_token    VARCHAR(500)    NOT NULL,
    moodle_user_id  INTEGER         NOT NULL UNIQUE,
    created_at      TIMESTAMP       DEFAULT NOW(),
    last_updated    TIMESTAMP       DEFAULT NOW()
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
    hours_before                INTEGER     DEFAULT 24,
    notify_on_new_assignment    BOOLEAN     DEFAULT TRUE,
    notify_on_due_date_change   BOOLEAN     DEFAULT TRUE
);
