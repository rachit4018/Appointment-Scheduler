"""Appointment endpoints.
 
Transitions are named routes (/confirm, /cancel, /reschedule) rather than a
general PATCH /appointments/{id}. Naming them makes the state machine
explicit in the URL space and gives each rule exactly one place to live.
"""
 
from typing import Annotated
 
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession
 
from app import notifications
from app.db import get_db
from app.deps import CurrentUser, RequirePatient, RequireProvider
from app.schemas import (
    AppointmentCreate,
    AppointmentDetailOut,
    AppointmentOut,
    RescheduleIn,
    VersionedAction,
)
from app.services import appointments as service
 
router = APIRouter(prefix="/api/appointments", tags=["appointments"])
 
Session = Annotated[AsyncSession, Depends(get_db)]
 
 
@router.get("", response_model=list[AppointmentOut])
async def list_appointments(session: Session, user: CurrentUser):
    return await service.list_for(session, user)
 
 
@router.get("/{appointment_id}", response_model=AppointmentDetailOut)
async def get_appointment(appointment_id: int, session: Session, user: CurrentUser):
    appt = await service.get_for(session, appointment_id, user)
    # Load the audit trail for the detail view.
    await session.refresh(appt, attribute_names=["history"])
    return appt
 
 
@router.post("", response_model=AppointmentOut, status_code=201)
async def request_appointment(
    payload: AppointmentCreate, session: Session, patient: RequirePatient
):
    return await service.create(
        session,
        patient=patient,
        provider_id=payload.provider_id,
        starts_at=payload.starts_at,
        appointment_type=payload.appointment_type,
        reason=payload.reason,
    )
 
 
@router.post("/{appointment_id}/confirm", response_model=AppointmentOut)
async def confirm_appointment(
    appointment_id: int,
    payload: VersionedAction,
    background_tasks: BackgroundTasks,
    session: Session,
    provider: RequireProvider,
):
    appt = await service.confirm(
        session,
        appointment_id=appointment_id,
        provider=provider,
        expected_version=payload.expected_version,
    )

    # can we put the select query to double check if the appointment is confirmed before sending the email?

    
    # Scheduled only after the confirm has committed. BackgroundTasks runs
    # after the response is sent, and dispatch() swallows anything the
    # sender raises, so the notification can neither delay nor fail the
    # confirm.
    background_tasks.add_task(
        notifications.dispatch,
        notifications.send_confirmation,
        to_email=appt.patient.email,
        patient_name=appt.patient.name,
        provider_name=appt.provider.name,
        starts_at=appt.starts_at,
        appointment_id=appt.id,
    )
    return appt
 
 
@router.post("/{appointment_id}/cancel", response_model=AppointmentOut)
async def cancel_appointment(
    appointment_id: int,
    payload: VersionedAction,
    session: Session,
    patient: RequirePatient,
):
    return await service.cancel(
        session,
        appointment_id=appointment_id,
        patient=patient,
        expected_version=payload.expected_version,
    )
 
 
@router.patch("/{appointment_id}/reschedule", response_model=AppointmentOut)
async def reschedule_appointment(
    appointment_id: int,
    payload: RescheduleIn,
    session: Session,
    provider: RequireProvider,
):
    return await service.reschedule(
        session,
        appointment_id=appointment_id,
        provider=provider,
        starts_at=payload.starts_at,
        expected_version=payload.expected_version,
    )