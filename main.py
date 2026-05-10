from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import requests
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from exponent_server_sdk import PushClient, PushMessage
from fastapi import BackgroundTasks, Header

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
    update_assignment_note,
    update_user_language 
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
origins = [
    "http://localhost",
    "http://localhost:8000", # כדי שה-Swagger UI (Docs) של FastAPI יעבוד לך במחשב
    "http://localhost:8081", # הכתובת הדיפולטית של ה-Metro ב-Expo
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, 
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], 
    allow_headers=["*"], 
)






# --- Models ---
class LoginRequest(BaseModel):
    username: str
    password: str
    institution: str

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
    אם אקספו מודיע שהטוקן כבר לא קיים במכשיר, מוחק אותו מה-DB.
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
        "sound": "default",
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        res_json = response.json()
        
        # אקספו מחזיר 200 OK גם אם יש שגיאה פנימית עם הטוקן. נבדוק את התוכן:
        data = res_json.get("data", {})
        if isinstance(data, dict) and data.get("status") == "error":
            error_details = data.get("details", {})
            if error_details.get("error") == "DeviceNotRegistered":
                print(f"!!! טוקן לא פעיל זוהה ונמחק: {expo_token}")
                from database import remove_device_token
                remove_device_token(expo_token)
                
        return res_json
    except Exception as e:
        print(f"Error sending push notification to {expo_token}: {e}")
        return None

# --- Helper Functions for Moodle API (לא שונה כלום) ---
def get_user_courses(wstoken: str, userid: int, api_url: str):
    params = {
        "wstoken": wstoken,
        "wsfunction": "core_enrol_get_users_courses",
        "userid": userid,
        "moodlewsrestformat": "json"
    }
    res = requests.get(api_url, params=params).json() # משתמש ב-api_url הדינמי
    if isinstance(res, dict) and "exception" in res:
        raise HTTPException(status_code=400, detail="Invalid token or user ID")
    return res

def get_assignments_for_courses(wstoken: str, course_ids: list,api_url: str):
    params = {
        "wstoken": wstoken,
        "wsfunction": "mod_assign_get_assignments",
        "moodlewsrestformat": "json"
    }
    for i, cid in enumerate(course_ids):
        params[f"courseids[{i}]"] = cid
    res = requests.get(api_url, params=params).json()
    return res.get("courses", [])

def get_submission_status(wstoken: str, userid: int, assign_id: int,api_url: str):
    params = {
        "wstoken": wstoken,
        "wsfunction": "mod_assign_get_submission_status",
        "assignid": assign_id,
        "userid": userid,
        "moodlewsrestformat": "json"
    }
    res = requests.get(api_url, params=params).json()
    if "lastattempt" in res and "submission" in res["lastattempt"]:
        return res["lastattempt"]["submission"].get("status", "new")
    return "new"

def get_quizzes_for_courses(wstoken: str, course_ids: list,api_url: str):
    params = {
        "wstoken": wstoken,
        "wsfunction": "mod_quiz_get_quizzes_by_courses",
        "moodlewsrestformat": "json"
    }
    for i, cid in enumerate(course_ids):
        params[f"courseids[{i}]"] = cid
    res = requests.get(api_url, params=params).json()
    return res.get("quizzes", [])

def get_quiz_submission_status(wstoken: str, userid: int, quiz_id: int, api_url: str):
    params = {
        "wstoken": wstoken,
        "wsfunction": "mod_quiz_get_user_attempts",
        "quizid": quiz_id,
        "userid": userid,
        "status": "all",
        "moodlewsrestformat": "json"
    }
    res = requests.get(api_url, params=params).json()
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
    # קביעת ה-URLs לפי המוסד
    if req.institution == "bgu":
        base_url = "https://moodle.bgu.ac.il/moodle"
    else:
        base_url = "https://moodle.ruppin.ac.il"

    login_url = f"{base_url}/login/token.php"
    moodle_api_url = f"{base_url}/webservice/rest/server.php"

    # התחברות למודל
    params = {
        "username": req.username,
        "password": req.password,
        "service": "moodle_mobile_app"
    }
    res = requests.get(login_url, params=params).json()

    if "error" in res:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    wstoken = res["token"]

    # שליפת מידע על המשתמש
    info_params = {
        "wstoken": wstoken,
        "wsfunction": "core_webservice_get_site_info",
        "moodlewsrestformat": "json"
    }
    info_res = requests.get(moodle_api_url, params=info_params).json()
    userid = info_res.get("userid")
    fullname = info_res.get("fullname")

    # שמירה ב-DB (הוספנו את המוסד)
    user_id = save_user_to_db(
        name=fullname,
        moodle_token=wstoken,
        moodle_user_id=userid,
        institution=req.institution
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
    
def get_moodle_urls(institution: str):
    base = "https://moodle.bgu.ac.il/moodle" if institution == "bgu" else "https://moodle.ruppin.ac.il"
    return {
        "api": f"{base}/webservice/rest/server.php",
        "login": f"{base}/login/token.php",
        "base": base
    }
    

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
    user_id = get_current_user_id(authorization)
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # שליפת הכתובות המתאימות למוסד של המשתמש הספציפי
    urls = get_moodle_urls(user.get("institution", "ruppin"))
    
    wstoken = user["moodle_token"]
    moodle_userid = user["moodle_user_id"]

    # העברת ה-API URL לפונקציית העזר
    courses = get_user_courses(wstoken, moodle_userid, urls["api"])
    sync_user_courses(user_id, courses)
    
    assignments_to_save = []
    
    if courses:
        for course in courses:
            # מעבירים את הכתובת גם למנוע הגילוי (Discovery Engine)
            all_items = run_discovery_engine(course["id"], course["fullname"], wstoken, user.get("institution", "ruppin"))
            
            for item in all_items:
                if item["type"] == "quiz":
                    # וודא שגם הפונקציה הזו עודכנה לקבל api_url
                    status = get_quiz_submission_status(wstoken, moodle_userid, item["id"], urls["api"])
                    link = f"{urls['base']}/mod/quiz/view.php?id={item['raw_data']['coursemodule']}"
                    open_date = item['raw_data'].get('timeopen')
                else:
                    status = get_submission_status(wstoken, moodle_userid, item["id"], urls["api"])
                    link = f"{urls['base']}/mod/assign/view.php?id={item['raw_data']['cmid']}"
                    open_date = item['raw_data'].get('allowsubmissionsfromdate')
                
                assignments_to_save.append({
                    "moodle_assign_id": item["id"],
                    "item_type": item["type"],
                    "title": item["name"],
                    "course": course["fullname"],
                    "open_date": open_date,
                    "due_date": item["due_date_ts"],
                    "link": link,
                    "is_submitted": status == "submitted"
                })

    save_assignments(user_id, assignments_to_save)
    return {"success": True, "synced": len(assignments_to_save)}


# ── Notifications ──────────────────────────────────────────────────────────────
INTERNAL_SECRET = os.getenv("INTERNAL_TASK_SECRET")

@app.post("/api/admin/trigger-discovery")
async def trigger_discovery_externally(
    background_tasks: BackgroundTasks, 
    x_task_secret: str = Header(None)
):
    # בדיקת אבטחה: רק מי שיודע את הקוד הסודי יכול להפעיל את הסריקה
    if x_task_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    # שימוש ב-BackgroundTasks מאפשר להחזיר תשובה מהירה ל-Azure 
    # בזמן שהסריקה הכבדה ממשיכה לרוץ ברקע של השרת
    background_tasks.add_task(discovery_task)
    
    return {"status": "Discovery triggered in background"}

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
    language: str = "he"  # ברירת מחדל

@app.post("/api/notifications/register-device")
def register_device(req: DeviceTokenRequest, authorization: Optional[str] = Header(None)):
    user_id = get_current_user_id(authorization)
    register_device_token(user_id, req.token)
    update_user_language(user_id, req.language)
    return {"success": True, "message": "Device registered successfully"}

# --- הגדרת המשימה  ---
def discovery_task():
    print(f"--- [סריקה ] {datetime.now().strftime('%H:%M:%S')} ---")
    try:
        reps = get_active_course_representatives()
        for rep in reps:
            token_plain = decrypt_token(rep['moodle_token'])
            run_discovery_engine(rep['course_id'], rep['course_name'], token_plain, rep.get('institution', 'ruppin'))
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
            lang = row.get('language', 'he')
            
            if row['last_notified_hours'] != target_threshold:
                token_plain = decrypt_token(row['moodle_token'])
                urls = get_moodle_urls(row.get('institution', 'ruppin'))
                
                # --- בדיקת סטטוס הגשה ברגע האחרון ---
                if row['item_type'] == 'quiz':
                    moodle_status = get_quiz_submission_status(token_plain, row['moodle_user_id'], row['moodle_assign_id'], urls['api'])
                    item_label = "הבוחן" if lang == 'he' else "The quiz"
                else:
                    moodle_status = get_submission_status(token_plain, row['moodle_user_id'], row['moodle_assign_id'], urls['api'])
                    item_label = "המטלה" if lang == 'he' else "The task"
                
                if moodle_status == "submitted":
                    print(f"-> המשתמש הגיש במודל. מעדכן DB עבור {row['title']} ומבטל התראה.")
                    update_user_assignment(row['ua_id'], row['user_id'], is_submitted=True)
                    continue

                # --- בניית ההודעה ---
                words = row['title'].split()
                short_title = " ".join(words[:3]) + ("..." if len(words) > 3 else "")
                emoji = "🚨" if target_threshold <= 2 else "⏰" if target_threshold <= 12 else "⏳"
                title = "מועד ההגשה מתקרב!"
                
                if target_threshold < 25:
                        message = f"{item_label} '{short_title}' מקורס '{row['course']}' מסתיימת בעוד פחות מ-{target_threshold} שעות! {emoji}"
                else:
                        days = int(target_threshold / 24)
                        message = f"{item_label} '{short_title}' מקורס '{row['course']}' מסתיימת בעוד פחות מ-{days} ימים! {emoji}"
                
                send_push_notification(row['fcm_token'], title, message)
                update_last_notified(row['ua_id'], target_threshold)
                
    except Exception as e:
        print(f"שגיאה בסריקת תזכורות: {e}")

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
                title="MyTask",
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


def notify_course_users(course_id: int, course_name: str, short_title: str, is_new: bool):
    """מושכת טוקנים ושולחת התראות חכמות לפי השפה הספציפית של כל משתמש"""
    tokens_data = get_tokens_for_course(course_id)
    for user_device in tokens_data:
        lang = user_device.get('language', 'he')
        if is_new:
            title = "מטלה חדשה"
            msg = f' נוספה מטלה חדשה "{short_title}" בקורס "{course_name}"'
        else:
            title = "שינוי במועד ההגשה"
            msg = f'עודכן תאריך הגשה עבור "{short_title}" בקורס "{course_name}"'
        send_push_notification(user_device['fcm_token'], title, msg)


def run_discovery_engine(course_id: int, course_name: str, token_plain: str, institution: str):
    """

    מנוע הסריקה המרכזי (Crowdsourced).

    מוריד ממודל -> מאחד -> מחפש דברים חדשים -> מעדכן DB גלובלי -> שולח פושים.

    מחזיר את הרשימה המאוחדת כדי שהסנכרון לא יצטרך לפנות למודל שוב.

    """
    urls = get_moodle_urls(institution)
    
    moodle_assignments = get_assignments_for_courses(token_plain, [course_id], urls['api'])
    moodle_quizzes = get_quizzes_for_courses(token_plain, [course_id], urls['api'])
    
    all_items = []
    for course in moodle_assignments:
        for item in course.get("assignments", []):
            all_items.append({"id": item["id"], "name": item["name"], "due_date_ts": item.get("duedate"), "raw_data": item, "type": "assign"})
    for quiz in moodle_quizzes:
        all_items.append({"id": quiz["id"], "name": quiz["name"], "due_date_ts": quiz.get("timeclose"), "raw_data": quiz, "type": "quiz"})

    for item in all_items:
        moodle_id = item["id"]
        new_due_date_ts = item["due_date_ts"]
        new_due_date_dt = datetime.utcfromtimestamp(new_due_date_ts) if new_due_date_ts else None
        item_type = item["type"]
        
        existing_item = get_assignment_by_moodle_id(moodle_id, item_type)
        
        # קיצור שם המטלה לעד 3 מילים
        words = item["name"].split()
        short_title = " ".join(words[:3]) + ("..." if len(words) > 3 else "")
        
        if not existing_item:
            print(f"! גילוי מנוע חכם: {item['name']} בקורס {course_name}")
            db_id = save_new_assignment_globally(course_id, course_name, item["raw_data"], urls['base'], item_type=item_type)
            link_assignment_to_course_users(db_id, course_id)
            notify_course_users(course_id, course_name, short_title, is_new=True)
            
        elif existing_item.get("due_date") != new_due_date_dt:
            print(f"!!! שינוי תאריך חכם: {item['name']} בקורס {course_name}")
            update_assignment_due_date(moodle_id, new_due_date_ts, item_type=item_type)
            notify_course_users(course_id, course_name, short_title, is_new=False)
            
    return all_items # מחזיר לטובת ה-Sync האישי