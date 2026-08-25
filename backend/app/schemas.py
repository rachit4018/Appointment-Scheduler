from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from app.models import AppointmentStatus, AppointmentType, HistoryAction, Role

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
 
    id: int
    name: str
    email: str
    role: Role
 
 
class HistoryEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
 
    id: int
    action: HistoryAction
    field_changed: str
    old_value: str | None
    new_value: str
    actor_name: str
    created_at: datetime
 
 
class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
 
    id: int
    patient_id: int
    patient_name: str
    provider_id: int
    provider_name: str
    starts_at: datetime
    ends_at: datetime
    appointment_type: AppointmentType
    reason: str | None
    status: AppointmentStatus
    version: int
    created_at: datetime
    updated_at: datetime
 
 
class AppointmentDetailOut(AppointmentOut):
    history: list[HistoryEntryOut] = []
 
 
class AppointmentCreate(BaseModel):
    provider_id: int
    starts_at: datetime
    appointment_type: AppointmentType
    reason: str | None = Field(default=None, max_length=2000)
 
 
class VersionedAction(BaseModel):
    """Body for any action that mutates an existing appointment.
 
    expected_version is the version the client rendered. If the row has moved
    on since then, the write is rejected with 409 rather than silently
    applied to something the user never saw.
    """
 
    expected_version: int
 
 
class RescheduleIn(VersionedAction):
    starts_at: datetime
 
 
class ConflictOut(BaseModel):
    """409 payload. Carries the current row so the client can re-render and
    re-ask rather than just showing an error."""
 
    detail: str
    current: AppointmentOut