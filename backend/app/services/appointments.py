"""Business logic for appointments.

Each mutating function follows the same shape: load and check ownership,
check the optimistic-concurrency token (Problem 1), check the state
transition is legal, then write the change plus one audit row (Problem 2)
in the same transaction. Confirm and reschedule additionally guard the
commit against the database's overlap exclusion constraint (Problem 4).
"""

from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Appointment,
    AppointmentDurartions,
    AppointmentHistory,
    AppointmentStatus,
    AppointmentType,
    HistoryAction,
    Role,
    User,
)


def _ends_at(starts_at: datetime, appointment_type: AppointmentType) -> datetime:
    return starts_at + timedelta(minutes=AppointmentDurartions[appointment_type])


async def _load_owned(
    session: AsyncSession, appointment_id: int, *, owner_id: int, owner_column: str
) -> Appointment:
    appt = await session.get(Appointment, appointment_id)
    if appt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found."
        )
    if getattr(appt, owner_column) != owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a participant on this appointment.",
        )
    return appt


def _check_version(appt: Appointment, expected_version: int) -> None:
    if appt.version != expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "stale_version",
                "message": "This appointment has changed since you last viewed it.",
            },
        )


async def _commit_or_conflict(session: AsyncSession) -> None:
    """Commit, translating the overlap exclusion constraint into a 409.

    Two concurrent confirms can both pass the checks above because neither
    sees the other's uncommitted transaction. The exclusion constraint is
    what makes the second write impossible; this just turns that database
    error into the same conflict response a client already knows how to
    handle.
    """
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "slot_taken",
                "message": "This provider already has a confirmed appointment in that window.",
            },
        )


async def list_for(session: AsyncSession, user: User) -> list[Appointment]:
    column = Appointment.patient_id if user.role is Role.patient else Appointment.provider_id
    stmt = select(Appointment).where(column == user.id).order_by(Appointment.starts_at)
    return list((await session.execute(stmt)).unique().scalars().all())


async def get_for(session: AsyncSession, appointment_id: int, user: User) -> Appointment:
    appt = await session.get(Appointment, appointment_id)
    if appt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found."
        )
    if user.id not in (appt.patient_id, appt.provider_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a participant on this appointment.",
        )
    return appt


async def create(
    session: AsyncSession,
    *,
    patient: User,
    provider_id: int,
    starts_at: datetime,
    appointment_type: AppointmentType,
    reason: str | None,
) -> Appointment:
    provider = await session.get(User, provider_id)
    if provider is None or provider.role is not Role.provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found."
        )

    appt = Appointment(
        patient=patient,
        provider=provider,
        starts_at=starts_at,
        ends_at=_ends_at(starts_at, appointment_type),
        appointment_type=appointment_type,
        reason=reason,
        status=AppointmentStatus.pending,
        version=1,
    )
    session.add(appt)
    await session.flush()

    # Two rows rather than one: the audit trail records both that a request
    # was made and what time it asked for.
    session.add_all(
        [
            AppointmentHistory(
                appointment_id=appt.id,
                actor_id=patient.id,
                action=HistoryAction.created,
                field_changed="status",
                old_value=None,
                new_value=AppointmentStatus.pending.value,
            ),
            AppointmentHistory(
                appointment_id=appt.id,
                actor_id=patient.id,
                action=HistoryAction.created,
                field_changed="starts_at",
                old_value=None,
                new_value=starts_at.isoformat(),
            ),
        ]
    )
    await session.commit()
    await session.refresh(appt, attribute_names=["created_at", "updated_at"])
    return appt


async def confirm(
    session: AsyncSession, *, appointment_id: int, provider: User, expected_version: int
) -> Appointment:
    appt = await _load_owned(
        session, appointment_id, owner_id=provider.id, owner_column="provider_id"
    )
    _check_version(appt, expected_version)

    if appt.status is not AppointmentStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_status",
                "message": "Only a pending request can be confirmed.",
            },
        )

    old_status = appt.status.value
    appt.status = AppointmentStatus.confirmed
    appt.version += 1
    session.add(
        AppointmentHistory(
            appointment_id=appt.id,
            actor_id=provider.id,
            action=HistoryAction.confirmed,
            field_changed="status",
            old_value=old_status,
            new_value=appt.status.value,
        )
    )

    await _commit_or_conflict(session)
    await session.refresh(appt, attribute_names=["status", "version", "updated_at"])
    return appt


async def cancel(
    session: AsyncSession, *, appointment_id: int, patient: User, expected_version: int
) -> Appointment:
    appt = await _load_owned(
        session, appointment_id, owner_id=patient.id, owner_column="patient_id"
    )
    _check_version(appt, expected_version)

    if appt.status is not AppointmentStatus.confirmed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_status",
                "message": "Only a confirmed appointment can be cancelled.",
            },
        )

    old_status = appt.status.value
    appt.status = AppointmentStatus.cancelled
    appt.version += 1
    session.add(
        AppointmentHistory(
            appointment_id=appt.id,
            actor_id=patient.id,
            action=HistoryAction.cancelled,
            field_changed="status",
            old_value=old_status,
            new_value=appt.status.value,
        )
    )

    await session.commit()
    await session.refresh(appt, attribute_names=["status", "version", "updated_at"])
    return appt


async def reschedule(
    session: AsyncSession,
    *,
    appointment_id: int,
    provider: User,
    starts_at: datetime,
    expected_version: int,
) -> Appointment:
    appt = await _load_owned(
        session, appointment_id, owner_id=provider.id, owner_column="provider_id"
    )
    _check_version(appt, expected_version)

    if appt.status is AppointmentStatus.cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_status",
                "message": "A cancelled appointment cannot be rescheduled.",
            },
        )

    # Reschedule changes the time and nothing else: status is untouched, so
    # a confirmed appointment stays confirmed and a pending one stays pending.
    old_starts_at = appt.starts_at.isoformat()
    appt.starts_at = starts_at
    appt.ends_at = _ends_at(starts_at, appt.appointment_type)
    appt.version += 1
    session.add(
        AppointmentHistory(
            appointment_id=appt.id,
            actor_id=provider.id,
            action=HistoryAction.rescheduled,
            field_changed="starts_at",
            old_value=old_starts_at,
            new_value=starts_at.isoformat(),
        )
    )

    await _commit_or_conflict(session)
    await session.refresh(appt, attribute_names=["starts_at", "ends_at", "version", "updated_at"])
    return appt
