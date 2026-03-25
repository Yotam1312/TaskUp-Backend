from pydantic import BaseModel


class NotificationSettingsUpdate(BaseModel):
    hours_before: int
    notify_on_new_assignment: bool
    notify_on_due_date_change: bool
