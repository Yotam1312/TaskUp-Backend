from dotenv import load_dotenv
load_dotenv()
import psycopg2
import psycopg2.extras
from datetime import datetime
from security import get_refresh_token_expiry, encrypt_token, decrypt_token
import os

# ── Connection ─────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "127.0.0.1"),
    "port":     os.getenv("DB_PORT", 5432),
    "dbname":   os.getenv("DB_NAME", "taskup_db"),
    "user":     os.getenv("DB_USER", "taskup_user"),
    "password": os.getenv("DB_PASSWORD", "taskup_pass")
}


def get_conn():
    """Open a new DB connection."""
    return psycopg2.connect(**DB_CONFIG)


# ── Users ──────────────────────────────────────────────────────────────────────

def save_user_to_db(name: str, moodle_token: str, moodle_user_id: int) -> int:
    """
    Insert or update user by moodle_user_id.
    מצפין את ה-wstoken לפני שמירה.
    Returns our DB id.
    """
    encrypted_token = encrypt_token(moodle_token)

    query = """
    INSERT INTO users (name, moodle_token, moodle_user_id)
    VALUES (%s, %s, %s)
    ON CONFLICT (moodle_user_id)
    DO UPDATE SET
        name         = EXCLUDED.name,
        moodle_token = EXCLUDED.moodle_token,
        last_updated = NOW()
    RETURNING id;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (name, encrypted_token, moodle_user_id))
            user_id = cur.fetchone()[0]
    return user_id


def get_user_by_id(user_id: int) -> dict | None:
    """
    שולף משתמש לפי ID.
    מפענח את ה-wstoken אוטומטית.
    """
    query = "SELECT id, name, moodle_token, moodle_user_id FROM users WHERE id = %s"
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, (user_id,))
            row = cur.fetchone()
    if not row:
        return None
    user = dict(row)
    user["moodle_token"] = decrypt_token(user["moodle_token"])  # פענוח אוטומטי
    return user


def get_all_users() -> list[dict]:
    """Used by scheduler. מפענח wstoken לכל משתמש."""
    query = "SELECT id, moodle_token, moodle_user_id FROM users"
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()
    result = []
    for r in rows:
        user = dict(r)
        user["moodle_token"] = decrypt_token(user["moodle_token"])
        result.append(user)
    return result


# ── Assignments + UserAssignments ──────────────────────────────────────────────

def save_assignments(user_id: int, assignments: list[dict]):
    """
    For each assignment:
    - Upsert into assignments (by link)
    - Upsert into user_assignments (by user_id + assignment_id)
    """
    upsert_assign = """
    INSERT INTO assignments (moodle_assign_id, title, course, open_date, due_date, link)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (link) DO UPDATE SET
        title    = EXCLUDED.title,
        due_date = EXCLUDED.due_date
    RETURNING id;
    """

    upsert_user_assign = """
    INSERT INTO user_assignments (user_id, assignment_id, is_submitted, is_archived)
    VALUES (%s, %s, %s, FALSE)
    ON CONFLICT (user_id, assignment_id) DO UPDATE SET
        is_submitted = EXCLUDED.is_submitted;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            for task in assignments:
                open_date = datetime.utcfromtimestamp(task["open_date"]) if task.get("open_date") else None
                due_date  = datetime.utcfromtimestamp(task["due_date"])  if task.get("due_date")  else None

                cur.execute(upsert_assign, (
                    task["moodle_assign_id"],
                    task["title"],
                    task["course"],
                    open_date,
                    due_date,
                    task["link"]
                ))
                assign_id = cur.fetchone()[0]

                cur.execute(upsert_user_assign, (
                    user_id,
                    assign_id,
                    task.get("is_submitted", False)
                ))

        print(f"Saved/updated {len(assignments)} assignments for user {user_id}.")


def get_assignments_for_user(user_id: int, submitted: bool, archived: bool) -> list[dict]:
    query = """
    SELECT
        ua.id,
        a.title,
        a.course,
        a.open_date,
        a.due_date,
        a.link,
        ua.is_submitted,
        ua.is_archived
    FROM user_assignments ua
    JOIN assignments a ON ua.assignment_id = a.id
    WHERE ua.user_id    = %s
      AND ua.is_submitted = %s
      AND ua.is_archived  = %s
    ORDER BY a.due_date ASC;
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, (user_id, submitted, archived))
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def update_user_assignment(ua_id: int, user_id: int, is_submitted: bool | None = None, is_archived: bool | None = None):
    if is_submitted is not None:
        query = "UPDATE user_assignments SET is_submitted = %s WHERE id = %s AND user_id = %s"
        val = is_submitted
    elif is_archived is not None:
        query = "UPDATE user_assignments SET is_archived = %s WHERE id = %s AND user_id = %s"
        val = is_archived
    else:
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (val, ua_id, user_id))


# ── Refresh Tokens ─────────────────────────────────────────────────────────────

def save_refresh_token(user_id: int, token: str):
    expires_at = get_refresh_token_expiry()
    query = """
    INSERT INTO refresh_tokens (user_id, token, expires_at)
    VALUES (%s, %s, %s)
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (user_id, token, expires_at))


def get_refresh_token(token: str) -> dict | None:
    query = "SELECT id, user_id, expires_at, is_revoked FROM refresh_tokens WHERE token = %s"
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, (token,))
            row = cur.fetchone()
    return dict(row) if row else None


def revoke_refresh_token(token: str):
    query = "UPDATE refresh_tokens SET is_revoked = TRUE WHERE token = %s"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (token,))


# ── Notification Settings ──────────────────────────────────────────────────────

def get_notification_settings(user_id: int) -> dict | None:
    query = """
    SELECT hours_before, notify_on_new_assignment, notify_on_due_date_change
    FROM notification_settings
    WHERE user_id = %s
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, (user_id,))
            row = cur.fetchone()
    return dict(row) if row else None


def upsert_notification_settings(user_id: int, hours_before: int, notify_on_new: bool, notify_on_change: bool):
    query = """
    INSERT INTO notification_settings (user_id, hours_before, notify_on_new_assignment, notify_on_due_date_change)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (user_id) DO UPDATE SET
        hours_before                = EXCLUDED.hours_before,
        notify_on_new_assignment    = EXCLUDED.notify_on_new_assignment,
        notify_on_due_date_change   = EXCLUDED.notify_on_due_date_change;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (user_id, hours_before, notify_on_new, notify_on_change))