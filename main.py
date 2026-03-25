import os
import requests
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# יבוא מהקבצים שלך
from database import (
    save_user_to_db, 
    save_assignments, 
    get_notification_settings, 
    upsert_notification_settings,
    save_refresh_token
)
from security import create_access_token, generate_refresh_token

# טעינת משתני סביבה
load_dotenv()

app = FastAPI(title="TaskUp API")

# --- הגדרות CORS (קריטי לעבודה עם Expo/Web) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # בבדיקות מאפשרים הכל, זה יפתור שגיאות בדפדפן
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# כתובות המודל - וודא שהן מוגדרות ב-Render או כאן
LOGIN_URL = os.getenv("LOGIN_URL", "https://moodle.ruppin.ac.il/login/token.php")
MOODLE_URL = os.getenv("MOODLE_URL", "https://moodle.ruppin.ac.il/webservice/rest/server.php")

class LoginRequest(BaseModel):
    username: str
    password: str

@app.get("/")
def health_check():
    return {"status": "online", "message": "TaskUp Server is running on Render!"}

@app.post("/api/login")
def login_to_moodle(req: LoginRequest):
    print(f"--- DEBUG START: Login attempt for {req.username} ---")
    
    # 1. ניסיון התחברות למודל
    login_params = {
        "username": req.username,
        "password": req.password,
        "service": "moodle_mobile_app"
    }
    
    try:
        response = requests.get(LOGIN_URL, params=login_params, timeout=15)
        print(f"DEBUG: Moodle Response Code: {response.status_code}")
        
        # בדיקה האם התשובה היא בכלל JSON
        content_type = response.headers.get("Content-Type", "")
        if "application/json" not in content_type.lower():
            print(f"DEBUG ERROR: Expected JSON but got {content_type}")
            print(f"DEBUG BODY (first 500 chars): {response.text[:500]}")
            raise HTTPException(
                status_code=502, 
                detail="Moodle blocked the request or returned HTML. Check Render Logs."
            )
        
        res = response.json()
    except requests.exceptions.RequestException as e:
        print(f"DEBUG CONNECTION ERROR: {str(e)}")
        raise HTTPException(status_code=503, detail="Failed to connect to Moodle server")

    if "error" in res:
        print(f"DEBUG LOGIN FAILED: {res.get('error')}")
        raise HTTPException(status_code=401, detail="Invalid Moodle credentials")

    wstoken = res["token"]
    print("DEBUG: Token received successfully")

    # 2. שליפת מידע על המשתמש (Site Info)
    info_params = {
        "wstoken": wstoken,
        "wsfunction": "core_webservice_get_site_info",
        "moodlewsrestformat": "json"
    }
    
    try:
        info_res = requests.get(MOODLE_URL, params=info_params).json()
        userid = info_res.get("userid")
        fullname = info_res.get("fullname")
        
        if not userid:
            raise ValueError("Could not find userid in Moodle response")
            
    except Exception as e:
        print(f"DEBUG INFO ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch user info from Moodle")

    # 3. שמירה בבסיס הנתונים (PostgreSQL)
    try:
        user_id = save_user_to_db(
            name=fullname,
            moodle_token=wstoken,
            moodle_user_id=userid
        )
        print(f"DEBUG: User saved to DB with ID: {user_id}")
    except Exception as e:
        print(f"DEBUG DB ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="Database save failed")

    # 4. משיכת מטלות (כאן תוסיף את הלוגיקה של get_user_courses וכו' אם הן קיימות)
    # לצורך העניין אני משאיר את זה כ-Placeholder כדי שהקוד ירוץ
    print("DEBUG: Processing assignments...")
    # ... כאן נכנס הקוד שלך של get_user_courses וכו' ...

    # 5. הגדרות התראות
    if not get_notification_settings(user_id):
        upsert_notification_settings(user_id, 24, True, True)

    # 6. יצירת טוקנים של האפליקציה (JWT)
    access_token = create_access_token(user_id)
    refresh_token = generate_refresh_token()
    save_refresh_token(user_id, refresh_token)

    print(f"--- DEBUG END: Login successful for {fullname} ---")
    
    return {
        "success": True,
        "name": fullname,
        "access_token": access_token,
        "refresh_token": refresh_token
    }

# פונקציות עזר (ודא שהן מוגדרות אצלך בשרת או תייבא אותן)
def get_user_courses(token, userid):
    # כאן תשים את הלוגיקה שלך
    return [] 

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))