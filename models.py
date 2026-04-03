from pydantic import BaseModel


# בתוך main.py או models.py
class NotificationSettingsUpdate(BaseModel):
    hours_before: list[int]
    notify_on_new: bool       # שינינו את השם שיתאים ל-UI
    notify_on_change: bool    # שינינו את השם שיתאים ל-UI