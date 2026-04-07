from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import requests
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from exponent_server_sdk import PushClient, PushMessage

from database import (
    save_user_to_db,
    get_user_by_id,
    save_assignments,
    get_assignments_for_user,
    update_user_assignment,
    save_refresh_token,
    get_refresh_token,
    revoke_refresh_token,
    get_notification_settings,
    upsert_notification_settings,
    register_device_token,
    sync_user_courses,
    get_tokens_for_course,
    get_active_course_representatives,
    save_new_assignment_globally,
    assignment_exists,
    get_assignment_by_moodle_id,
    update_assignment_due_date,
    link_assignment_to_course_users,
    get_all_assignments,
    get_pending_reminders,
    update_last_notified,
    update_assignment_note
)
from security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    decrypt_token
)
from models import (
    NotificationSettingsUpdate,
    AssignmentNoteUpdate
)

load_dotenv()

app = FastAPI(title="Moodle Ruppin Tasks API")

scheduler = BackgroundScheduler()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MOODLE_URL = "https://moodle.ruppin.ac.il/webservice/rest/server.php"
LOGIN_URL = "https://moodle.ruppin.ac.il/login/token.php"


# --- Models ---
class LoginRequest(BaseModel):
    username: str
    password: str

class RefreshRequest(BaseModel):
    refresh_token: str


# --- Helper: בדיקת טוקן בכל בקשה ---


def get_current_user_id(authorization: Optional[str]) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization.split(" ")[1]
    user_id = decode_access_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Token expired or invalid")
    return user_id

def send_push_notification(expo_token: str, title: str, body: str):
    """
    שולח התראת פוש למכשיר ספציפי דרך ה-API של אקספו.
    """
    url = "https://exp.host/--/api/v2/push/send"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {
        "to": expo_token,
        "title": title,
        "body": body,
        "sound": "default", # גורם לטלפון לצפצף
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error sending push notification: {e}")
        return None


# --- Helper Functions for Moodle API (לא שונה כלום) ---
def get_user_courses(wstoken: str, userid: int):
    params = {
        "wstoken": wstoken,
        "wsfunction": "core_enrol_get_users_courses",
        "userid": userid,
        "moodlewsrestformat": "json"
    }
    res = requests.get(MOODLE_URL, params=params).json()
    
    if isinstance(res, dict) and "exception" in res:
        raise HTTPException(status_code=400, detail="Invalid token or user ID")
    return res

def get_assignments_for_courses(wstoken: str, course_ids: list):
    params = {
        "wstoken": wstoken,
        "wsfunction": "mod_assign_get_assignments",
        "moodlewsrestformat": "json"
    }
    for i, cid in enumerate(course_ids):
        params[f"courseids[{i}]"] = cid
    res = requests.get(MOODLE_URL, params=params).json()
    return res.get("courses", [])

def get_submission_status(wstoken: str, userid: int, assign_id: int):
    params = {
        "wstoken": wstoken,
        "wsfunction": "mod_assign_get_submission_status",
        "assignid": assign_id,
        "userid": userid,
        "moodlewsrestformat": "json"
    }
    res = requests.get(MOODLE_URL, params=params).json()
    if "lastattempt" in res and "submission" in res["lastattempt"]:
        return res["lastattempt"]["submission"].get("status", "new")
    return "new"

def get_quizzes_for_courses(wstoken: str, course_ids: list):
    params = {
        "wstoken": wstoken,
        "wsfunction": "mod_quiz_get_quizzes_by_courses",
        "moodlewsrestformat": "json"
    }
    for i, cid in enumerate(course_ids):
        params[f"courseids[{i}]"] = cid
    res = requests.get(MOODLE_URL, params=params).json()
    return res.get("quizzes", [])

def get_quiz_submission_status(wstoken: str, userid: int, quiz_id: int):
    params = {
        "wstoken": wstoken,
        "wsfunction": "mod_quiz_get_user_attempts",
        "quizid": quiz_id,
        "userid": userid,
        "status": "all",
        "moodlewsrestformat": "json"
    }
    res = requests.get(MOODLE_URL, params=params).json()
    attempts = res.get("attempts", [])
    if attempts:
        # בודקים אם יש ניסיון שהסטטוס שלו 'finished'
        for attempt in attempts:
            if attempt.get("state") == "finished":
                return "submitted"
    return "new"


# ── Auth ───────────────────────────────────────────────────────────────────────

@app.post("/api/login")
def login_to_moodle(req: LoginRequest):
    # 1. התחברות למoodle (לא שונה)
    params = {
        "username": req.username,
        "password": req.password,
        "service": "moodle_mobile_app"
    }
    res = requests.get(LOGIN_URL, params=params).json()
    print(f"DEBUG Moodle Response: success ")

    if "error" in res:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    wstoken = res["token"]

    # 2. שליפת userid ושם (לא שונה)
    info_params = {
        "wstoken": wstoken,
        "wsfunction": "core_webservice_get_site_info",
        "moodlewsrestformat": "json"
    }
    info_res = requests.get(MOODLE_URL, params=info_params).json()
    userid = info_res.get("userid")
    fullname = info_res.get("fullname")

    # 3. שמירה בDB
    user_id = save_user_to_db(
        name=fullname,
        moodle_token=wstoken,
        moodle_user_id=userid
    )

    # # 4. שליפת מטלות ושמירה בDB (אותה לוגיקה כמו /api/tasks)
    # courses = get_user_courses(wstoken, userid)
    # course_map = {c["id"]: c["fullname"] for c in courses}
    # course_ids = list(course_map.keys())

    # assignments_to_save = []
    # if course_ids:
    #     assignments_data = get_assignments_for_courses(wstoken, course_ids)
    #     for course in assignments_data:
    #         course_name = course_map.get(course["id"], "קורס לא ידוע")
    #         for assign in course.get("assignments", []):
    #             assign_id = assign["id"]
    #             status = get_submission_status(wstoken, userid, assign_id)
    #             assignments_to_save.append({
    #                 "moodle_assign_id": assign_id,
    #                 "title": assign["name"],
    #                 "course": course_name,
    #                 "open_date": assign.get("allowsubmissionsfromdate"),
    #                 "due_date": assign.get("duedate"),
    #                 "link": f"https://moodle.ruppin.ac.il/mod/assign/view.php?id={assign['cmid']}",
    #                 "is_submitted": status == "submitted"
    #             })

    # save_assignments(user_id, assignments_to_save)

    # 5. הגדרות ברירת מחדל להתראות אם משתמש חדש
    if not get_notification_settings(user_id):
        upsert_notification_settings(user_id, [24], True, True)

    # 6. יצירת טוקנים שלנו
    access_token = create_access_token(user_id)
    refresh_token = generate_refresh_token()
    save_refresh_token(user_id, refresh_token)

    return {
        "success": True,
        "name": fullname,
        "access_token": access_token,
        "refresh_token": refresh_token
    }
    
@app.get("/api/test-notification")
def trigger_test_notification():
    # שים כאן את הטוקן שלך ישירות כדי לוודא שזה עובד
    my_token = "ExponentPushToken[O_PZroF2XB8khcoM0SW81J]"
    
    result = send_push_notification(
        expo_token=my_token,
        title="MyTasks",
        body="יא באללה וכמה לה לה וכל היום זה רק ללה"
    )
    
    return {"message": "Notification triggered", "expo_result": result}


@app.post("/api/refresh")
def refresh(req: RefreshRequest):
    from datetime import datetime
    token_data = get_refresh_token(req.refresh_token)
    if not token_data:
        raise HTTPException(status_code=401, detail="Refresh token not found")
    if token_data["is_revoked"]:
        raise HTTPException(status_code=401, detail="Refresh token revoked")
    if token_data["expires_at"] < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Refresh token expired")
    return {"access_token": create_access_token(token_data["user_id"])}


@app.post("/api/logout")
def logout(req: RefreshRequest):
    revoke_refresh_token(req.refresh_token)
    return {"success": True}


# ── Assignments ────────────────────────────────────────────────────────────────

@app.get("/api/assignments/pending")
def get_pending(authorization: Optional[str] = Header(None)):
    user_id = get_current_user_id(authorization)
    return get_assignments_for_user(user_id, submitted=False, archived=False)


@app.get("/api/assignments/submitted")
def get_submitted(authorization: Optional[str] = Header(None)):
    user_id = get_current_user_id(authorization)
    return get_assignments_for_user(user_id, submitted=True, archived=False)


@app.get("/api/assignments/archived")
def get_archived(authorization: Optional[str] = Header(None)):
    user_id = get_current_user_id(authorization)
    return get_assignments_for_user(user_id, submitted=False, archived=True)


@app.patch("/api/assignments/{assignment_id}/submit")
def mark_submitted(assignment_id: int, authorization: Optional[str] = Header(None)):
    user_id = get_current_user_id(authorization)
    update_user_assignment(assignment_id, user_id, is_submitted=True)
    return {"success": True}


@app.patch("/api/assignments/{assignment_id}/archive")
def mark_archived(assignment_id: int, authorization: Optional[str] = Header(None)):
    user_id = get_current_user_id(authorization)
    update_user_assignment(assignment_id, user_id, is_archived=True)
    return {"success": True}

@app.patch("/api/assignments/{assignment_id}/unarchive")
def unmark_archived(assignment_id: int, authorization: Optional[str] = Header(None)):
    user_id = get_current_user_id(authorization)
    update_user_assignment(assignment_id, user_id, is_archived=False)
    return {"success": True}

@app.patch("/api/assignments/{assignment_id}/unsubmit")
def unmark_submitted(assignment_id: int, authorization: Optional[str] = Header(None)):
    user_id = get_current_user_id(authorization)
    update_user_assignment(assignment_id, user_id, is_submitted=False)
    return {"success": True}


@app.patch("/api/assignments/{assignment_id}/note")
def update_note(
    assignment_id: int,
    body: AssignmentNoteUpdate,
    authorization: Optional[str] = Header(None)
):
    user_id = get_current_user_id(authorization)

    note = body.note
    if note is not None:
        note = note.strip()
        if note == "":
            note = None

    if note is not None and len(note) > 50:
        raise HTTPException(status_code=422, detail="Note max length is 50 characters")

    updated = update_assignment_note(assignment_id, user_id, note)
    if not updated:
        raise HTTPException(status_code=404, detail="Assignment not found")

    return {"success": True, "assignment_id": assignment_id, "note": note}


@app.get("/api/assignments/all")
def get_all(authorization: Optional[str] = Header(None)):
    user_id = get_current_user_id(authorization)
    return get_all_assignments(user_id)

# ── Sync ──────────────────────────────────────────────────────────────────────

@app.post("/api/assignments/sync")
def sync_assignments(authorization: Optional[str] = Header(None)):
    """
    מושך wstoken מהDB ומרענן את המטלות מMoodle.
    נקרא כשהמשתמש פותח את האפליקציה מחדש.
    """
    user_id = get_current_user_id(authorization)

    # שליפת wstoken מהDB
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    wstoken = user["moodle_token"]
    moodle_userid = user["moodle_user_id"]

    # שליפת מטלות מMoodle עם הwstoken מהDB
    courses = get_user_courses(wstoken, moodle_userid)
    sync_user_courses(user_id, courses)
    course_map = {c["id"]: c["fullname"] for c in courses}
    course_ids = list(course_map.keys())

    assignments_to_save = []
    if course_ids:
        assignments_data = get_assignments_for_courses(wstoken, course_ids)
        for course in assignments_data:
            course_name = course_map.get(course["id"], "קורס לא ידוע")
            for assign in course.get("assignments", []):
                assign_id = assign["id"]
                status = get_submission_status(wstoken, moodle_userid, assign_id)
                assignments_to_save.append({
                    "moodle_assign_id": assign_id,
                    "title": assign["name"],
                    "course": course_name,
                    "open_date": assign.get("allowsubmissionsfromdate"),
                    "due_date": assign.get("duedate"),
                    "link": f"https://moodle.ruppin.ac.il/mod/assign/view.php?id={assign['cmid']}",
                    "is_submitted": status == "submitted"
                })
        # --- סנכרון בחנים (Quizzes) ---
        quizzes_data = get_quizzes_for_courses(wstoken, course_ids)
        for quiz in quizzes_data:
            course_name = course_map.get(quiz["course"], "קורס לא ידוע")
            status = get_quiz_submission_status(wstoken, moodle_userid, quiz["id"])
            assignments_to_save.append({
                "moodle_assign_id": quiz["id"],
                "item_type": "quiz",
                "title": quiz["name"],
                "course": course_name,
                "open_date": quiz.get("timeopen"),
                "due_date": quiz.get("timeclose"),
                "link": f"https://moodle.ruppin.ac.il/mod/quiz/view.php?id={quiz['coursemodule']}",
                "is_submitted": status == "submitted"
            })

    save_assignments(user_id, assignments_to_save)
    return {"success": True, "synced": len(assignments_to_save)}


# ── Notifications ──────────────────────────────────────────────────────────────

@app.get("/api/notifications/settings")
def get_settings(authorization: Optional[str] = Header(None)):
    user_id = get_current_user_id(authorization)
    settings = get_notification_settings(user_id)
    if not settings:
        raise HTTPException(status_code=404, detail="Settings not found")
    return settings


# בתוך main.py
@app.post("/api/notifications/settings") # עדיף POST כי זה UPSERT, או להישאר עם PATCH אבל לוודא שבפרונט זה תואם
def update_settings(body: NotificationSettingsUpdate, authorization: Optional[str] = Header(None)):
    user_id = get_current_user_id(authorization)
    
    # קריאה לפונקציה המעודכנת ב-database.py
    upsert_notification_settings(
        user_id, 
        body.hours_before, 
        body.notify_on_new, 
        body.notify_on_change
    )
    return {"success": True}

class DeviceTokenRequest(BaseModel):
    token: str

@app.post("/api/notifications/register-device")
def register_device(req: DeviceTokenRequest, authorization: Optional[str] = Header(None)):
    user_id = get_current_user_id(authorization)
    register_device_token(user_id, req.token)
    return {"success": True, "message": "Device registered successfully"}


# --- הגדרת המשימה  ---
def discovery_task():
    print(f"--- [סריקה ] {datetime.now().strftime('%H:%M:%S')} ---")
    
    try:
        reps = get_active_course_representatives()
        for rep in reps:
            token_plain = decrypt_token(rep['moodle_token'])
            moodle_data = get_assignments_for_courses(token_plain, [rep['course_id']])
                        
            for course in moodle_data:
                for assign in course.get("assignments", []):
                    moodle_id = assign["id"]
                    new_due_date_ts = assign.get("duedate")
                    new_due_date_dt = datetime.utcfromtimestamp(new_due_date_ts) if new_due_date_ts else None
                    
                    # נניח שאתה מוסיף פונקציה שמחזירה את המטלה מה-DB (או None אם לא קיימת)
                    existing_assign = get_assignment_by_moodle_id(moodle_id) 
                    
                    # 1. גילוי מטלה חדשה לגמרי
                    if not existing_assign:
                        print(f"! גילוי חדש: {assign['name']} בקורס {rep['course_name']}")
                        assign_db_id = save_new_assignment_globally(
                            rep['course_id'], 
                            rep['course_name'], # <--- זה מה שהיה חסר!
                            assign
    )
                        
                        link_assignment_to_course_users(assign_db_id, rep['course_id'])
                        
                        tokens = get_tokens_for_course(rep['course_id'])
                        if tokens:
                            send_push_notifications(tokens, f"מטלה חדשה: {assign['name']}")
                            
                        
                    # 2. גילוי שינוי תאריך במטלה קיימת
                    elif existing_assign.get("due_date") != new_due_date_dt:
                        print(f"!!! שינוי תאריך: {assign['name']} בקורס {rep['course_name']}")
                        update_assignment_due_date(moodle_id, new_due_date_ts) # פונקציה לעדכון תאריך בלבד
                        
                        tokens = get_tokens_for_course(rep['course_id'])
                        if tokens:
                            send_push_notifications(tokens, f"עודכן תאריך הגשה: {assign['name']}")
                
            # --- סריקת בחנים (Quizzes) ---
            quizzes_data = get_quizzes_for_courses(token_plain, [rep['course_id']])
            for quiz in quizzes_data:
                moodle_id = quiz["id"]
                new_due_date_ts = quiz.get("timeclose")
                new_due_date_dt = datetime.utcfromtimestamp(new_due_date_ts) if new_due_date_ts else None
                
                existing_quiz = get_assignment_by_moodle_id(moodle_id, 'quiz')
                
                if not existing_quiz:
                    print(f"! גילוי בוחן חדש: {quiz['name']} בקורס {rep['course_name']}")
                    quiz_db_id = save_new_assignment_globally(rep['course_id'], rep['course_name'], quiz, 'quiz')
                    link_assignment_to_course_users(quiz_db_id, rep['course_id'])
                    tokens = get_tokens_for_course(rep['course_id'])
                    if tokens: send_push_notifications(tokens, f"בוחן חדש: {quiz['name']}")
                        
                elif existing_quiz.get("due_date") != new_due_date_dt:
                    print(f"!!! שינוי תאריך בוחן: {quiz['name']} בקורס {rep['course_name']}")
                    update_assignment_due_date(moodle_id, new_due_date_ts, 'quiz')
                    tokens = get_tokens_for_course(rep['course_id'])
                    if tokens: send_push_notifications(tokens, f"עודכן תאריך לבוחן: {quiz['name']}")

    except Exception as e:
        print(f"שגיאה קריטית בסריקה: {e}")

def reminder_task():
    print(f"--- [בדיקת תזכורות] {datetime.now().strftime('%H:%M:%S')} ---")
    try:
        reminders = get_pending_reminders()
        now = datetime.utcnow()
        
        for row in reminders:
            time_left = row['due_date'] - now
            hours_left = time_left.total_seconds() / 3600.0
            
            if hours_left < 0: continue
                
            valid_thresholds = [x for x in row['hours_before'] if hours_left <= x]
            if not valid_thresholds: continue
                
            target_threshold = min(valid_thresholds)
            
            if row['last_notified_hours'] != target_threshold:
                # --- בדיקת "ברגע האחרון" מול המודל ---
                token_plain = decrypt_token(row['moodle_token'])
                
                if row['item_type'] == 'quiz':
                    moodle_status = get_quiz_submission_status(token_plain, row['moodle_user_id'], row['moodle_assign_id'])
                    item_label = "הבוחן"
                else:
                    moodle_status = get_submission_status(token_plain, row['moodle_user_id'], row['moodle_assign_id'])
                    item_label = "המטלה"
                
                if moodle_status == "submitted":
                    print(f"-> המשתמש הגיש במודל. מעדכן DB עבור {item_label} {row['title']} ומבטל התראה.")
                    update_user_assignment(row['ua_id'], row['user_id'], is_submitted=True)
                    continue

                # אם באמת לא הוגש - שולחים את ההתראה המעוצבת
                words = row['title'].split()
                short_title = " ".join(words[:3]) + ("..." if len(words) > 3 else "")
                emoji = "🚨" if target_threshold <= 2 else "⏰" if target_threshold <= 12 else "⏳"
                if target_threshold < 25 :
                    message = f"{emoji} המטלה '{short_title}' מקורס '{row['course']}' מסתיימת בעוד פחות מ-{target_threshold} שעות!"

                else :
                         target_threshold_to_days = int(target_threshold/24)
                         message = f"{emoji} המטלה '{short_title}' מקורס '{row['course']}' מסתיימת בעוד פחות מ-{target_threshold_to_days} ימים!"
                
                send_push_notification(row['fcm_token'], "MyTasks - זמן להגשה", message)
                update_last_notified(row['ua_id'], target_threshold)
                
    except Exception as e:
        print(f"שגיאה בסריקת תזכורות: {e}")
# --- הפעלה ב-Startup ---

def should_run_discovery():
    now = datetime.now()
    h, m, wd = now.hour, now.minute, now.weekday()
    is_weekend = wd in [4, 5] # 4=שישי, 5=שבת (בישראל)
    
    # ימי חול (א-ה)
    if not is_weekend:
        if 8 <= h < 20: return True
        if (20 <= h <= 23 or h == 0) and m < 30: return True # בשעות האלו ירוץ רק באזור XX:00
        return False
        
    # סוף שבוע (ו-ש)
    else:
        if 8 <= h < 20: return True
        return False

def run_discovery_if_needed():
    if should_run_discovery():
        discovery_task()
    else:
        print(f"--- [סריקת מודל] {datetime.now().strftime('%H:%M:%S')} מחוץ לשעות הפעילות, מדלג ---")
        
        
@app.on_event("startup")
def start_scheduler():
    # 1. משימת סריקת המודל (רצה כל 30 דקות, אבל בודקת את חוקי השעות שלך)
    scheduler.add_job(id='moodle_discovery', func=run_discovery_if_needed, trigger='interval', minutes=30)
    
    # 2. משימת התזכורות (רצה כל 10 דקות 24/7 כדי לדייק בזמני ההתראה)
    scheduler.add_job(id='reminders_check', func=reminder_task, trigger='interval', minutes=10)
    
    scheduler.start()
    print("ה-Scheduler הופעל בהצלחה עם 2 המשימות!")
    
# --- שאר ה-Endpoints שלך (Login, Sync וכו') ---
@app.get("/")
def home():
    return {"status": "running"}
#פונקציה 
def send_push_notifications(tokens, message_body):
    """שולחת התראות לכל הטוקנים שברשימה באמצעות הפונקציה הבסיסית שעובדת"""
    for token in tokens:
        try:
            send_push_notification(
                expo_token=token,
                title="MyTasks",
                body=message_body
            )
        except Exception as e:
            print(f"שגיאה בשליחת פוש לטוקן {token}: {e}")
        
@app.get("/api/admin/scan")
def manual_discovery():
    """
    URL שמפעיל את סריקת המטלות החדשות באופן ידני.
    """
    print("🚀 מפעיל סריקה ידנית של המודל...")
    discovery_task()
    return {"status": "Discovery task triggered successfully", "time": datetime.now().strftime('%H:%M:%S')}