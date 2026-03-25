import os
import requests
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

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
    upsert_notification_settings
)
from security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token
)
from models import NotificationSettingsUpdate

load_dotenv()

app = FastAPI(title="TaskUp API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LOGIN_URL = os.getenv("LOGIN_URL", "https://moodle.ruppin.ac.il/login/token.php")
MOODLE_URL = os.getenv("MOODLE_URL", "https://moodle.ruppin.ac.il/webservice/rest/server.php")

# ── Headers שמדמים דפדפן אמיתי — פותר חסימת bot של Moodle ────────────────────
MOODLE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8",
    "Referer": "https://moodle.ruppin.ac.il/",
}


# ── Models ─────────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class RefreshRequest(BaseModel):
    refresh_token: str


# ── Helper: בדיקת access token ────────────────────────────────────────────────
def get_current_user_id(authorization: Optional[str]) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization.split(" ")[1]
    user_id = decode_access_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Token expired or invalid")
    return user_id


# ── Moodle API helpers ─────────────────────────────────────────────────────────
def get_user_courses(wstoken: str, userid: int):
    params = {
        "wstoken": wstoken,
        "wsfunction": "core_enrol_get_users_courses",
        "userid": userid,
        "moodlewsrestformat": "json"
    }
    res = requests.get(MOODLE_URL, params=params, headers=MOODLE_HEADERS).json()
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
    res = requests.get(MOODLE_URL, params=params, headers=MOODLE_HEADERS).json()
    return res.get("courses", [])


def get_submission_status(wstoken: str, userid: int, assign_id: int):
    params = {
        "wstoken": wstoken,
        "wsfunction": "mod_assign_get_submission_status",
        "assignid": assign_id,
        "userid": userid,
        "moodlewsrestformat": "json"
    }
    res = requests.get(MOODLE_URL, params=params, headers=MOODLE_HEADERS).json()
    if "lastattempt" in res and "submission" in res["lastattempt"]:
        return res["lastattempt"]["submission"].get("status", "new")
    return "new"


def fetch_and_save_assignments(wstoken: str, userid: int, user_id: int):
    """שולף את כל המטלות מMoodle ושומר בDB."""
    courses = get_user_courses(wstoken, userid)
    course_map = {c["id"]: c["fullname"] for c in courses}
    course_ids = list(course_map.keys())

    assignments_to_save = []
    if course_ids:
        assignments_data = get_assignments_for_courses(wstoken, course_ids)
        for course in assignments_data:
            course_name = course_map.get(course["id"], "קורס לא ידוע")
            for assign in course.get("assignments", []):
                assign_id = assign["id"]
                status = get_submission_status(wstoken, userid, assign_id)
                assignments_to_save.append({
                    "moodle_assign_id": assign_id,
                    "title": assign["name"],
                    "course": course_name,
                    "open_date": assign.get("allowsubmissionsfromdate"),
                    "due_date": assign.get("duedate"),
                    "link": f"https://moodle.ruppin.ac.il/mod/assign/view.php?id={assign['cmid']}",
                    "is_submitted": status == "submitted"
                })

    save_assignments(user_id, assignments_to_save)
    return len(assignments_to_save)


# ── Health Check ───────────────────────────────────────────────────────────────
@app.get("/")
def health_check():
    return {"status": "online", "message": "TaskUp Server is running!"}


# ── Auth ───────────────────────────────────────────────────────────────────────
@app.post("/api/login")
def login_to_moodle(req: LoginRequest):
    print(f"--- Login attempt for {req.username} ---")

    # 1. התחברות למoodle עם headers שמדמים דפדפן
    login_params = {
        "username": req.username,
        "password": req.password,
        "service": "moodle_mobile_app"
    }

    try:
        response = requests.get(LOGIN_URL, params=login_params, headers=MOODLE_HEADERS, timeout=15)
        content_type = response.headers.get("Content-Type", "")

        if "application/json" not in content_type.lower():
            print(f"Moodle returned HTML instead of JSON: {response.text[:300]}")
            raise HTTPException(status_code=502, detail="Moodle blocked the request")

        res = response.json()

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Failed to connect to Moodle: {str(e)}")

    if "error" in res:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    wstoken = res["token"]

    # 2. שליפת userid ושם
    info_params = {
        "wstoken": wstoken,
        "wsfunction": "core_webservice_get_site_info",
        "moodlewsrestformat": "json"
    }
    info_res = requests.get(MOODLE_URL, params=info_params, headers=MOODLE_HEADERS).json()
    userid = info_res.get("userid")
    fullname = info_res.get("fullname")

    # 3. שמירה בDB
    user_id = save_user_to_db(
        name=fullname,
        moodle_token=wstoken,
        moodle_user_id=userid
    )

    # 4. שליפת מטלות ושמירה
    count = fetch_and_save_assignments(wstoken, userid, user_id)
    print(f"Saved {count} assignments for {fullname}")

    # 5. הגדרות התראות ברירת מחדל
    if not get_notification_settings(user_id):
        upsert_notification_settings(user_id, 24, True, True)

    # 6. יצירת טוקנים
    access_token = create_access_token(user_id)
    refresh_token = generate_refresh_token()
    save_refresh_token(user_id, refresh_token)

    print(f"Login successful for {fullname}")

    return {
        "success": True,
        "name": fullname,
        "access_token": access_token,
        "refresh_token": refresh_token
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


# ── Sync ───────────────────────────────────────────────────────────────────────
@app.post("/api/assignments/sync")
def sync_assignments(authorization: Optional[str] = Header(None)):
    """מרענן מטלות מMoodle לפי wstoken מהDB."""
    user_id = get_current_user_id(authorization)

    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    count = fetch_and_save_assignments(
        user["moodle_token"],
        user["moodle_user_id"],
        user_id
    )
    return {"success": True, "synced": count}


# ── Notifications ──────────────────────────────────────────────────────────────
@app.get("/api/notifications/settings")
def get_settings(authorization: Optional[str] = Header(None)):
    user_id = get_current_user_id(authorization)
    settings = get_notification_settings(user_id)
    if not settings:
        raise HTTPException(status_code=404, detail="Settings not found")
    return settings


@app.patch("/api/notifications/settings")
def update_settings(body: NotificationSettingsUpdate, authorization: Optional[str] = Header(None)):
    user_id = get_current_user_id(authorization)
    upsert_notification_settings(user_id, body.hours_before, body.notify_on_new_assignment, body.notify_on_due_date_change)
    return {"success": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))