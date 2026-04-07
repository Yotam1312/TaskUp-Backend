from typing import Optional
from pydantic import BaseModel


class NotificationSettingsUpdate(BaseModel):
    hours_before: list[int]
    notify_on_new: bool
    notify_on_change: bool


class AssignmentNoteUpdate(BaseModel):
    note: Optional[str] = None