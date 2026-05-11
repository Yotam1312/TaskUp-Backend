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
    return psycopg2.connect(**DB_CONFIG, sslmode='require')


# ── Users ──────────────────────────────────────────────────────────────────────

def save_user_to_db(name: str, moodle_token: str, moodle_user_id: int, institution: str) -> int:
    encrypted_token = encrypt_token(moodle_token)

    query = """
    INSERT INTO users (name, moodle_token, moodle_user_id, institution)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (moodle_user_id, institution)
    DO UPDATE SET
        name         = EXCLUDED.name,
        moodle_token = EXCLUDED.moodle_token,
        last_updated = NOW()
    RETURNING id;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (name, encrypted_token, moodle_user_id, institution))
            user_id = cur.fetchone()[0]
    return user_id


def get_user_by_id(user_id: int) -> dict | None:
    """
    שולף משתמש לפי ID כולל המוסד שלו.
    מפענח את ה-wstoken אוטומטית.
    """
    # הוספנו את institution ל-SELECT
    query = "SELECT id, name, moodle_token, moodle_user_id, institution FROM users WHERE id = %s"
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, (user_id,))
            row = cur.fetchone()
    if not row:
        return None
    user = dict(row)
    user["moodle_token"] = decrypt_token(user["moodle_token"])
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
    INSERT INTO assignments (moodle_assign_id, item_type, title, course, open_date, due_date, link)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
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
                    task.get("item_type", "assign"),
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
    
    # חצי שנה בשניות = 15552000
    course_expired_cond = f"(c.id IS NOT NULL AND ((c.end_date > 0 AND c.end_date < {now_ts}) OR (COALESCE(c.end_date, 0) = 0 AND (COALESCE(c.start_date, 0) + 15552000) < {now_ts})))"
    course_active_cond = f"(c.id IS NULL OR (c.end_date > 0 AND c.end_date >= {now_ts}) OR (COALESCE(c.end_date, 0) = 0 AND (COALESCE(c.start_date, 0) + 15552000) >= {now_ts}))"
    
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
        query += f" AND (ua.is_archived = TRUE OR {course_expired_cond})"
    elif submitted:
        query += f" AND ua.is_submitted = TRUE AND ua.is_archived = FALSE AND {course_active_cond}"
    else:
        query += f" AND ua.is_submitted = FALSE AND ua.is_archived = FALSE AND {course_active_cond}"
        
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
            
def update_user_language(user_id: int, language: str):
    query = "UPDATE users SET language = %s WHERE id = %s;"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (language, user_id))
            
def sync_user_courses(user_id, courses_list):
    """
    מסנכרן את רשימת הקורסים: 
    1. מעדכן נתונים גלובליים בטבלת courses (ממיר 0 ל-NULL).
    2. מקשר את המשתמש לקורסים בטבלת user_courses.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            for course in courses_list:
                # המרת 0 ל-None כדי שישמר כ-NULL ב-DB
                start_date = course.get('startdate')
                start_date = start_date if start_date else None
                end_date = course.get('enddate')
                end_date = end_date if end_date else None

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
                    start_date, 
                    end_date
                ))

                # ב. קישור המשתמש לקורס (אם לא קיים כבר)
                cur.execute("""
                    INSERT INTO user_courses (user_id, course_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING;
                """, (user_id, course['id']))

def get_active_course_representatives():
    now_ts = "EXTRACT(EPOCH FROM NOW())"
    query = f"""
    SELECT DISTINCT ON (c.id) 
           c.id as course_id, c.fullname as course_name,
           u.id as user_id, u.moodle_token, u.institution
    FROM courses c
    JOIN user_courses uc ON c.id = uc.course_id
    JOIN users u ON uc.user_id = u.id
    WHERE COALESCE(c.start_date, 0) <= {now_ts}
      AND (
          (c.end_date > 0 AND c.end_date >= {now_ts})
          OR
          (COALESCE(c.end_date, 0) = 0 AND (COALESCE(c.start_date, 0) + 15552000) >= {now_ts})
      )
      AND u.moodle_token IS NOT NULL;
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            return cur.fetchall()
        
def get_tokens_for_course(course_id):
    query = """
    SELECT d.fcm_token, u.language, 
           ns.notify_on_new_assignment, ns.notify_on_due_date_change
    FROM user_devices d
    JOIN user_courses uc ON d.user_id = uc.user_id
    JOIN notification_settings ns ON d.user_id = ns.user_id
    JOIN users u ON d.user_id = u.id
    WHERE uc.course_id = %s 
      AND d.fcm_token IS NOT NULL;
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, (course_id,))
            return cur.fetchall()
         
def assignment_exists(moodle_assign_id):
    """בודקת אם המטלה כבר רשומה במערכת שלנו"""
    query = "SELECT 1 FROM assignments WHERE moodle_assign_id = %s LIMIT 1;"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (moodle_assign_id,))
            return cur.fetchone() is not None
        
# בתוך database.py
def save_new_assignment_globally(course_id, course_name, assign, base_url, item_type='assign'):
    query = """
        INSERT INTO assignments (moodle_assign_id, item_type, title, course, open_date, due_date, link)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (link) DO UPDATE SET title = EXCLUDED.title 
        RETURNING id;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            open_ts = assign.get('allowsubmissionsfromdate') or assign.get('timeopen')
            due_ts = assign.get('duedate') or assign.get('timeclose')
            open_date = datetime.utcfromtimestamp(open_ts) if open_ts else None
            due_date = datetime.utcfromtimestamp(due_ts) if due_ts else None
            
            # הלינק מיוצר דינמית במקום ההארד קוד הישן
            link_url = f"{base_url}/mod/quiz/view.php?id={assign.get('coursemodule')}" if item_type == 'quiz' else f"{base_url}/mod/assign/view.php?id={assign.get('cmid')}"
            
            cur.execute(query, (assign['id'], item_type, assign['name'], course_name, open_date, due_date, link_url))
            return cur.fetchone()[0]
            
def get_assignment_by_moodle_id(moodle_assign_id: int, item_type: str = 'assign') -> dict | None:
    query = "SELECT id, due_date FROM assignments WHERE moodle_assign_id = %s AND item_type = %s LIMIT 1;"
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, (moodle_assign_id, item_type))
            row = cur.fetchone()
    return dict(row) if row else None

def update_assignment_due_date(moodle_assign_id: int, new_due_date_ts: int, item_type: str = 'assign'):
    due_date_dt = datetime.utcfromtimestamp(new_due_date_ts) if new_due_date_ts else None
    query = "UPDATE assignments SET due_date = %s WHERE moodle_assign_id = %s AND item_type = %s;"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (due_date_dt, moodle_assign_id, item_type))

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
    now_ts = "EXTRACT(EPOCH FROM NOW())"
    expired_logic = f"((c.end_date > 0 AND c.end_date < {now_ts}) OR (COALESCE(c.end_date, 0) = 0 AND (COALESCE(c.start_date, 0) + 15552000) < {now_ts}))"
    
    query = f"""
    SELECT
        ua.id, a.title, a.course, a.open_date, a.due_date, a.link,
        ua.is_submitted, ua.is_archived, ua.is_submitted_late, ua.note,
        
        -- בדיקה האם הקורס הסתיים (לפי תאריך או חצי שנה מתחילתו)
        (c.id IS NOT NULL AND {expired_logic}) as is_course_expired,
        
        CASE
            WHEN ua.is_archived = TRUE OR (c.id IS NOT NULL AND {expired_logic}) THEN 'archive'
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
        ua.id AS ua_id, ua.user_id, ua.last_notified_hours,
        a.title, a.course, a.due_date, a.moodle_assign_id, a.item_type,
        ns.hours_before, d.fcm_token, u.moodle_token, u.moodle_user_id,
        u.language, u.institution
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

def remove_device_token(token: str):
    """מוחק טוקן מת מהמסד כדי לשמור על טבלה נקייה"""
    query = "DELETE FROM user_devices WHERE fcm_token = %s;"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (token,))