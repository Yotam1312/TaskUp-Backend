from dotenv import load_dotenv
load_dotenv()
import psycopg2
import psycopg2.extras
from datetime import datetime
from security import get_refresh_token_expiry, encrypt_token, decrypt_token
import os
from psycopg2.extras import RealDictCursor

# ── Connection ─────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "127.0.0.1"),
    "port":     int(os.getenv("DB_PORT", 5432)),
    "dbname":   os.getenv("DB_NAME", "postgres"), 
    "user":     os.getenv("DB_USER", "taskup_user"),
    "password": os.getenv("DB_PASSWORD", "taskup_pass")
}

def get_conn():
    """Open a new DB connection."""
    # הוספת sslmode='require' היא חובה עבור Azure PostgreSQL 
    return psycopg2.connect(**DB_CONFIG, sslmode='disable')


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
    עבור כל מטלה:
    - מעדכן/מכניס לטבלת assignments (לפי הקישור)
    - מעדכן/מכניס לטבלת user_assignments (לפי user_id + assignment_id)
    - מחשב ושומר סטטוס הגשה באיחור
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
    INSERT INTO user_assignments (user_id, assignment_id, is_submitted, is_archived, is_submitted_late)
    VALUES (%s, %s, %s, FALSE, %s)
    ON CONFLICT (user_id, assignment_id) DO UPDATE SET
        is_submitted_late = CASE 
            WHEN user_assignments.is_submitted = FALSE AND EXCLUDED.is_submitted = TRUE THEN EXCLUDED.is_submitted_late
            ELSE user_assignments.is_submitted_late
        END,
        is_submitted = user_assignments.is_submitted OR EXCLUDED.is_submitted;
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

                is_submitted = task.get("is_submitted", False)
                # המטלה נחשבת כ"הוגשה באיחור" רק אם הוגשה והתאריך הנוכחי עבר את היעד
                is_late = bool(is_submitted and due_date and due_date < datetime.utcnow())

                cur.execute(upsert_user_assign, (
                    user_id,
                    assign_id,
                    is_submitted,
                    is_late
                ))

        print(f"Saved/updated {len(assignments)} assignments for user {user_id}.")


def get_assignments_for_user(user_id: int, submitted: bool, archived: bool) -> list[dict]:
    now_ts = "EXTRACT(EPOCH FROM NOW())"
    
    query = f"""
    SELECT
        ua.id, a.title, a.course, a.open_date, a.due_date, a.link,
        ua.is_submitted, ua.is_archived, ua.note
    FROM user_assignments ua
    JOIN assignments a ON ua.assignment_id = a.id
    LEFT JOIN courses c ON a.course = c.fullname
    WHERE ua.user_id = %s
    """
    
    if archived:
        # מופיע בארכיון אם: הועבר ידנית OR הקורס הסתיים
        query += f" AND (ua.is_archived = TRUE OR (c.end_date IS NOT NULL AND c.end_date < {now_ts}))"
    elif submitted:
        # מופיע בהושלמו אם: הוגש AND לא הועבר לארכיון ידנית AND הקורס עדיין פעיל
        query += f" AND ua.is_submitted = TRUE AND ua.is_archived = FALSE AND (c.end_date IS NULL OR c.end_date >= {now_ts})"
    else:
        # מופיע בלביצוע אם: לא הוגש AND לא הועבר לארכיון ידנית AND הקורס עדיין פעיל
        query += f" AND ua.is_submitted = FALSE AND ua.is_archived = FALSE AND (c.end_date IS NULL OR c.end_date >= {now_ts})"
        
    query += " ORDER BY a.due_date ASC;"

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, (user_id,))
            rows = cur.fetchall()
    return [dict(r) for r in rows]

def update_user_assignment(ua_id: int, user_id: int, is_submitted: bool | None = None, is_archived: bool | None = None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if is_submitted is True:
                # בודקים אם כרגע הזמן עבר את תאריך היעד
                check_query = """
                    SELECT (a.due_date < (NOW() AT TIME ZONE 'UTC')) as late 
                    FROM user_assignments ua 
                    JOIN assignments a ON ua.assignment_id = a.id 
                    WHERE ua.id = %s
                """
                cur.execute(check_query, (ua_id,))
                result = cur.fetchone()
                is_late = (result[0] is True) if result else False
                
                query = "UPDATE user_assignments SET is_submitted = %s, is_submitted_late = %s WHERE id = %s AND user_id = %s"
                cur.execute(query, (True, is_late, ua_id, user_id))
            
            elif is_submitted is False:
                # בביטול הגשה מחזירים הכל לאחור
                query = "UPDATE user_assignments SET is_submitted = %s, is_submitted_late = FALSE WHERE id = %s AND user_id = %s"
                cur.execute(query, (False, ua_id, user_id))
                
            elif is_archived is not None:
                query = "UPDATE user_assignments SET is_archived = %s WHERE id = %s AND user_id = %s"
                cur.execute(query, (is_archived, ua_id, user_id))


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


# בתוך database.py
def upsert_notification_settings(user_id: int, hours_before: list, notify_on_new: bool, notify_on_change: bool):
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
            
            

# בתוך database.py
def register_device_token(user_id: int, token: str):
    """
    רושם טוקן למשתמש. אם הטוקן היה שייך למשתמש אחר, 
    הוא מועבר למשתמש הנוכחי כדי למנוע כפילויות במכשיר.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1. מחיקת הטוקן מכל משתמש אחר (כדי שלא יקבלו התראות של המכשיר הזה)
            cur.execute("DELETE FROM user_devices WHERE fcm_token = %s AND user_id != %s", (token, user_id))
            
            # 2. הכנסת הטוקן למשתמש הנוכחי
            query = """
                INSERT INTO user_devices (user_id, fcm_token)
                VALUES (%s, %s)
                ON CONFLICT (user_id, fcm_token) DO NOTHING;
            """
            cur.execute(query, (user_id, token))
            
def sync_user_courses(user_id, courses_list):
    """
    מבנכרן את רשימת הקורסים: 
    1. מעדכן נתונים גלובליים בטבלת courses.
    2. מקשר את המשתמש לקורסים בטבלת user_courses.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            for course in courses_list:
                # א. עדכון/הכנסה לטבלת קורסים הכללית
                cur.execute("""
                    INSERT INTO courses (id, fullname, start_date, end_date)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        fullname = EXCLUDED.fullname,
                        start_date = EXCLUDED.start_date,
                        end_date = EXCLUDED.end_date;
                """, (
                    course['id'], 
                    course['fullname'], 
                    course['startdate'], 
                    course['enddate']
                ))

                # ב. קישור המשתמש לקורס (אם לא קיים כבר)
                cur.execute("""
                    INSERT INTO user_courses (user_id, course_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING;
                """, (user_id, course['id']))

def get_active_course_representatives():
    """שולפת נציג אחד (ID וטוקן) לכל קורס שפעיל כרגע לפי תאריכים"""
    query = """
    SELECT DISTINCT ON (c.id) 
           c.id as course_id, c.fullname as course_name,
           u.id as user_id, u.moodle_token
    FROM courses c
    JOIN user_courses uc ON c.id = uc.course_id
    JOIN users u ON uc.user_id = u.id
    WHERE c.start_date <= EXTRACT(EPOCH FROM NOW()) 
      AND c.end_date >= EXTRACT(EPOCH FROM NOW())
      AND u.moodle_token IS NOT NULL;
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            return cur.fetchall()

def get_tokens_for_course(course_id):
    query = """
    SELECT d.fcm_token
    FROM user_devices d
    JOIN user_courses uc ON d.user_id = uc.user_id
    JOIN notification_settings ns ON d.user_id = ns.user_id
    WHERE uc.course_id = %s 
      AND d.fcm_token IS NOT NULL
      AND ns.notify_on_new_assignment = True; -- השם המדויק מהתמונה שלך
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (course_id,))
            return [row[0] for row in cur.fetchall()]
         
def assignment_exists(moodle_assign_id):
    """בודקת אם המטלה כבר רשומה במערכת שלנו"""
    query = "SELECT 1 FROM assignments WHERE moodle_assign_id = %s LIMIT 1;"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (moodle_assign_id,))
            return cur.fetchone() is not None
        
# בתוך database.py
def save_new_assignment_globally(course_id, course_name, assign):
    query = """
        INSERT INTO assignments (moodle_assign_id, title, course, open_date, due_date, link)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (link) DO UPDATE SET title = EXCLUDED.title 
        RETURNING id;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # המרה קריטית: ממסר (int) לאובייקט תאריך של פייתון
            open_ts = assign.get('allowsubmissionsfromdate')
            due_ts = assign.get('duedate')
            
# החלף את שתי השורות האלו:
            open_date = datetime.utcfromtimestamp(open_ts) if open_ts else None
            due_date = datetime.utcfromtimestamp(due_ts) if due_ts else None
            
            
            cur.execute(query, (
                assign['id'], 
                assign['name'], 
                course_name, # שימוש בשם הקורס ולא ב-ID
                open_date, 
                due_date,
                f"https://moodle.ruppin.ac.il/mod/assign/view.php?id={assign.get('cmid')}"
            ))
            return cur.fetchone()[0]
            
def get_assignment_by_moodle_id(moodle_assign_id: int) -> dict | None:
    """שולף מטלה קיימת לפי מזהה מודל כדי לבדוק שינויים"""
    query = "SELECT id, due_date FROM assignments WHERE moodle_assign_id = %s LIMIT 1;"
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, (moodle_assign_id,))
            row = cur.fetchone()
    return dict(row) if row else None

def update_assignment_due_date(moodle_assign_id: int, new_due_date_ts: int):
    """מעדכן רק את תאריך ההגשה של מטלה קיימת"""
    due_date_dt = datetime.utcfromtimestamp(new_due_date_ts) if new_due_date_ts else None
    query = "UPDATE assignments SET due_date = %s WHERE moodle_assign_id = %s;"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (due_date_dt, moodle_assign_id))

def link_assignment_to_course_users(assign_id: int, course_id: int):
    query = """
        INSERT INTO user_assignments (user_id, assignment_id, is_submitted, is_archived)
        SELECT user_id, %s, FALSE, FALSE
        FROM user_courses
        WHERE course_id = %s
        ON CONFLICT (user_id, assignment_id) DO NOTHING;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (assign_id, course_id))
            
def get_all_assignments(user_id: int) -> list[dict]:
    query = """
    SELECT
        ua.id, a.title, a.course, a.open_date, a.due_date, a.link,
        ua.is_submitted, ua.is_archived, ua.is_submitted_late, ua.note,
        -- בדיקה האם הקורס הסתיים (לפי שעון UTC)
        (c.end_date IS NOT NULL AND c.end_date < EXTRACT(EPOCH FROM NOW())) as is_course_expired,
        CASE
            WHEN ua.is_archived = TRUE OR (c.end_date IS NOT NULL AND c.end_date < EXTRACT(EPOCH FROM NOW())) THEN 'archive'
            WHEN ua.is_submitted = TRUE THEN 'completed'
            ELSE 'pending'
        END as computed_status
    FROM user_assignments ua
    JOIN assignments a ON ua.assignment_id = a.id
    LEFT JOIN courses c ON a.course = c.fullname
    WHERE ua.user_id = %s
    ORDER BY a.due_date ASC;
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, (user_id,))
            rows = cur.fetchall()
    return [dict(r) for r in rows]

def get_pending_reminders():
    query = """
    SELECT
        ua.id AS ua_id,
        ua.user_id,
        ua.last_notified_hours,
        a.title,
        a.course,
        a.due_date,
        a.moodle_assign_id, -- דרוש לבדיקה מול מודל
        ns.hours_before,
        d.fcm_token,
        u.moodle_token,      -- דרוש לבדיקה מול מודל
        u.moodle_user_id     -- דרוש לבדיקה מול מודל
    FROM user_assignments ua
    JOIN assignments a ON ua.assignment_id = a.id
    JOIN notification_settings ns ON ua.user_id = ns.user_id
    JOIN user_devices d ON ua.user_id = d.user_id
    JOIN users u ON ua.user_id = u.id
    WHERE ua.is_submitted = FALSE
      AND ua.is_archived = FALSE
      AND a.due_date > (NOW() AT TIME ZONE 'UTC')
      AND ns.hours_before IS NOT NULL;
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            return cur.fetchall()

def update_last_notified(ua_id: int, hours: int):
    """מעדכן את שעת ההתראה האחרונה שנשלחה"""
    query = "UPDATE user_assignments SET last_notified_hours = %s WHERE id = %s;"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (hours, ua_id))

def update_assignment_note(ua_id: int, user_id: int, note: str | None) -> bool:
    query = """
    UPDATE user_assignments
    SET note = %s
    WHERE id = %s AND user_id = %s
    RETURNING id;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (note, ua_id, user_id))
            row = cur.fetchone()
    return row is not None