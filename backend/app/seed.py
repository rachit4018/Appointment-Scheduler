"""Seed data.
 
Runs on startup and is idempotent: if users already exist it does nothing, so
restarting the container does not duplicate rows.
"""

import asyncio
import logging
from datetime import datetime, UTC, timedelta

from app.db import SessionLocal
from app.models import (
    Appointment,
    AppointmentHistory,
    AppointmentStatus,
    AppointmentType,
    User,
    Role,
    HistoryAction,

)
from app.services.appointments import _ends_at
from sqlalchemy import func, select
logger = logging.getLogger("seed")

def _at(days:int, hour:int, minute:int = 0) ->datetime:
    base = datetime.now(UTC)+timedelta(days=days)
    return base.replace(hour=hour,minute=minute, second=0,microsecond=0)

async def seed() -> None:
    async with SessionLocal() as session:
        existing = await session.scalar(select(func.count()).select_from(User))
        if existing:
            logger.info("seed skipped: %s users already present", existing)
            return
 
        patients = [
            User(name="Meera Shah", email="meera@example.com", role=Role.patient),
            User(name="Arjun Rao", email="arjun@example.com", role=Role.patient),
            User(name="Priya Nair", email="priya@example.com", role=Role.patient),
        ]
        providers = [
            User(name="Dr. Anand Patel", email="anand@clinic.example", role=Role.provider),
            User(name="Dr. Sara Iyer", email="sara@clinic.example", role=Role.provider),
        ]
        session.add_all(patients + providers)
        await session.flush()
 
        meera, arjun, priya = patients
        anand, sara = providers
 
        specs = [
            # (patient, provider, when, type, status, reason)
            (meera, anand, _at(2, 14), AppointmentType.consultation,
             AppointmentStatus.confirmed, "Persistent cough for two weeks"),
            (arjun, anand, _at(3, 10), AppointmentType.follow_up,
             AppointmentStatus.pending, "Follow-up on blood pressure medication"),
            (priya, anand, _at(3, 11), AppointmentType.lab_review,
             AppointmentStatus.pending, "Review lipid panel results"),
            (meera, sara, _at(5, 9), AppointmentType.annual_physical,
             AppointmentStatus.confirmed, "Annual check-up"),
            (arjun, sara, _at(1, 16), AppointmentType.consultation,
             AppointmentStatus.cancelled, "Rash on forearm"),
        ]
 
        for patient, provider, when, appt_type, appt_status, reason in specs:
            appt = Appointment(
                patient_id=patient.id,
                provider_id=provider.id,
                starts_at=when,
                ends_at=_ends_at(when, appt_type),
                appointment_type=appt_type,
                reason=reason,
                status=appt_status,
                version=1,
            )
            session.add(appt)
            await session.flush()
 
            # Seeded rows get a history trail too, so the audit view is not
            # empty on first load.
            session.add(
                AppointmentHistory(
                    appointment_id=appt.id,
                    actor_id=patient.id,
                    action=HistoryAction.created,
                    field_changed="status",
                    old_value=None,
                    new_value=AppointmentStatus.pending.value,
                )
            )
            session.add(
                AppointmentHistory(
                    appointment_id=appt.id,
                    actor_id=patient.id,
                    action=HistoryAction.created,
                    field_changed="starts_at",
                    old_value=None,
                    new_value=when.isoformat(),
                )
            )
            if appt_status is not AppointmentStatus.pending:
                session.add(
                    AppointmentHistory(
                        appointment_id=appt.id,
                        actor_id=(
                            provider.id
                            if appt_status is AppointmentStatus.confirmed
                            else patient.id
                        ),
                        action=(
                            HistoryAction.confirmed
                            if appt_status is AppointmentStatus.confirmed
                            else HistoryAction.cancelled
                        ),
                        field_changed="status",
                        old_value=AppointmentStatus.pending.value,
                        new_value=appt_status.value,
                    )
                )
 
        await session.commit()
        logger.info("seeded %s users and %s appointments",
                    len(patients) + len(providers), len(specs))
 
 
if __name__ == "__main__":
    asyncio.run(seed())