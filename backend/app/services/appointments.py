"""Appointment state transitions.

Every rule in the assignment lives here or in the database, never in the UI.
The split is deliberate:

  service layer  - which transitions are legal, who may perform them
  database       - which enum values exist, and that two confirmed
                   appointments for one provider cannot overlap

The reason the overlap rule is not in this file is that a check in Python has
a window between the SELECT and the UPDATE. The database is the only layer
where concurrent transactions are ordered.
"""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status as http
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AppointmentDurartions,
    Appointment,
    AppointmentHistory,
    AppointmentStatus,
    AppointmentType,
    HistoryAction,
    Role,
    User,
)

# Postgres SQLSTATE for exclusion_violation.
EXCLUSION_VIOLATION = "23P01"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _aware(value: datetime) -> datetime:
    """Everything is stored UTC-aware. Naive input is treated as UTC.

    Using datetime.now(UTC) rather than the naive utcnow() everywhere is the
    single most effective way to avoid silent timezone bugs in this model.
    """
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _ends_at(starts_at: datetime, appt_type: AppointmentType) -> datetime:
    """Duration is a property of the appointment type.

    Derived on the server so the stored end time is always consistent with
    the type, and so the patient is never asked how long their own
    appointment should be.
    """
    return starts_at + timedelta(minutes=AppointmentDurartions[appt_type])


def _record(
    session: AsyncSession,
    *,
    appointment_id: int,
    actor: User,
    action: HistoryAction,
    field: str,
    old: str | None,
    new: str,
) -> None:
    """Queue an audit row.

    Not committed here. It is flushed in the same transaction as the
    mutation, so an appointment can never change without its history row
    landing too.
    """
    session.add(
        AppointmentHistory(
            appointment_id=appointment_id,
            actor_id=actor.id,
            action=action,
            field_changed=field,
            old_value=old,
            new_value=new,
        )
    )


def _stale(current: Appointment) -> HTTPException:
    """409 for a write against a version the client no longer has.

    The response body carries the current row so the client can re-render and re-ask
    rather than just showing a dead end.
    """
    return HTTPException(
        status_code=http.HTTP_409_CONFLICT,
        detail={
            "code": "stale_version",
            "message": (
                "This appointment changed while you were looking at it. "
                "It is now "
                f"{current.starts_at.strftime('%d %b %Y, %H:%M')} "
                f"({current.status.value})."
            ),
            "current_version": current.version,
        },
    )


async def _load(session: AsyncSession, appointment_id: int) -> Appointment:
    appt = await session.get(Appointment, appointment_id)
    if appt is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Appointment not found.")
    return appt


def _assert_participant(appt: Appointment, user: User) -> None:  # checks the user is either the patient or provider for the appointment
    """Ownership is enforced here, not by hiding buttons.

    Flipping the role switcher to a provider and calling confirm on someone
    else's appointment still fails, even though the UI would never offer it.
    """
    if user.id not in (appt.patient_id, appt.provider_id):
        raise HTTPException(http.HTTP_403_FORBIDDEN, "Not your appointment.")


async def find_overlap(
    session: AsyncSession,
    *,
    provider_id: int,
    starts_at: datetime,
    ends_at: datetime,
    exclude_id: int | None = None,
) -> Appointment | None:
    """Pre-check used only to produce a helpful error message.

    This does NOT make double-booking impossible; the exclusion constraint
    does. Two requests can both pass this check and both proceed, because
    neither sees the other's uncommitted transaction. It exists so the
    provider can be told which appointment conflicts.
    """
    stmt = select(Appointment).where(
        Appointment.provider_id == provider_id,
        Appointment.status == AppointmentStatus.confirmed,
        # half-open [start, end): back-to-back slots do not collide
        Appointment.starts_at < ends_at,
        Appointment.ends_at > starts_at,
    )
    if exclude_id is not None:
        stmt = stmt.where(Appointment.id != exclude_id)
    return (await session.execute(stmt.limit(1))).scalars().first()


def _slot_taken(other: Appointment) -> HTTPException:
    return HTTPException(
        status_code=http.HTTP_409_CONFLICT,
        detail={
            "code": "slot_taken",
            "message": (
                "That slot overlaps a confirmed appointment "
                f"(#{other.id} at {other.starts_at.strftime('%d %b %Y, %H:%M')})."
            ),
        },
    )


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------

 # used in get list api
async def list_for(session: AsyncSession, user: User) -> list[Appointment]: # get requests for appointments for a user, either patient or provider
    """Scoped by role: a patient sees theirs, a provider sees theirs.

    Cancelled appointments are included rather than hidden, so cancelling
    does not feel like deletion.
    """
    column = (
        Appointment.patient_id if user.role is Role.patient else Appointment.provider_id
    )
    stmt = (
        select(Appointment)
        .where(column == user.id)
        .order_by(Appointment.starts_at.asc())
    )
    return list((await session.execute(stmt)).unique().scalars().all())


# used in get specific appointment api
async def get_for(session: AsyncSession, appointment_id: int, user: User) -> Appointment: # get a specific appointment for a user, either patient or provider
    appt = await _load(session, appointment_id)
    _assert_participant(appt, user)
    return appt


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------

# checks the provider exist and is a provider -> creates a new appointment with pending status -> records history -> commits transaction
async def create(
    session: AsyncSession,
    *,
    patient: User,
    provider_id: int,
    starts_at: datetime,
    appointment_type: AppointmentType,
    reason: str | None,
) -> Appointment:
    """Patient requests an appointment. Always lands in pending."""
    provider = await session.get(User, provider_id)
    if provider is None or provider.role is not Role.provider:
        raise HTTPException(http.HTTP_400_BAD_REQUEST, "Unknown provider.")

    starts_at = _aware(starts_at)
    ends_at = _ends_at(starts_at, appointment_type)

    appt = Appointment(
        patient_id=patient.id,
        provider_id=provider_id,
        starts_at=starts_at,
        ends_at=ends_at,
        appointment_type=appointment_type,
        reason=reason,
        status=AppointmentStatus.pending,
        version=1,
    )
    session.add(appt)
    await session.flush()

    # Two rows so the timeline starts at "requested", and so the original
    # time is recoverable by replay when someone asks what it was before.
    _record(
        session,
        appointment_id=appt.id,
        actor=patient,
        action=HistoryAction.created,
        field="status",
        old=None,
        new=AppointmentStatus.pending.value,
    )
    _record(
        session,
        appointment_id=appt.id,
        actor=patient,
        action=HistoryAction.created,
        field="starts_at",
        old=None,
        new=starts_at.isoformat(),
    )

    await session.commit()
    await session.refresh(appt)
    return appt

# flow - gets appointment -> checks if user is provider and appointment is pending -> checks for overlap -> updates appointment to confirmed and increments version -> records history -> commits transaction
async def confirm(
    session: AsyncSession,
    *,
    appointment_id: int,
    provider: User,
    expected_version: int,
) -> Appointment:
    """Provider confirms a pending appointment.

    This is the only transition that moves a booking to confirmed;
    rescheduling deliberately does not.
    """
    appt = await _load(session, appointment_id)

    if appt.provider_id != provider.id:
        raise HTTPException(http.HTTP_403_FORBIDDEN, "Not your appointment.")
    if appt.status is not AppointmentStatus.pending:
        raise HTTPException(
            http.HTTP_409_CONFLICT,
            f"Only pending appointments can be confirmed (this one is "
            f"{appt.status.value}).",
        )

    # UX only. Correctness comes from the constraint below.
    clash = await find_overlap(
        session,
        provider_id=appt.provider_id,
        starts_at=appt.starts_at,
        ends_at=appt.ends_at,
        exclude_id=appt.id,
    )
    if clash is not None:
        raise _slot_taken(clash)

    old_status = appt.status.value

    try:
        # One atomic statement. The version predicate closes the window
        # between the read above and this write: if anything changed in
        # between, zero rows match and nothing is written.
        result = await session.execute(
            update(Appointment)
            .where(
                Appointment.id == appointment_id,
                Appointment.version == expected_version,  # problem 1: lost race with another write
                Appointment.status == AppointmentStatus.pending,
            )
            .values(
                status=AppointmentStatus.confirmed,
                version=Appointment.version + 1,
            )
        )

        if result.rowcount == 0:
            await session.rollback()
            raise _stale(await _load(session, appointment_id))

        _record(
            session,
            appointment_id=appointment_id,
            actor=provider,
            action=HistoryAction.confirmed,
            field="status",
            old=old_status,
            new=AppointmentStatus.confirmed.value,
        )
        await session.commit()

    except IntegrityError as exc:
        await session.rollback()
        if getattr(exc.orig, "sqlstate", None) == EXCLUSION_VIOLATION:
            # Lost the race. The other confirm committed first; this one was
            # refused by the database, not by application code.
            raise HTTPException(
                status_code=http.HTTP_409_CONFLICT,
                detail={
                    "code": "slot_taken",
                    "message": (
                        "That slot was confirmed by someone else a moment "
                        "ago. Pick another time."
                    ),
                },
            ) from exc
        raise

    await session.refresh(appt)
    return appt



# checks the patient exist -> 
async def cancel(
    session: AsyncSession,
    *,
    appointment_id: int,
    patient: User,
    expected_version: int,
) -> Appointment:
    """Patient cancels a confirmed appointment.

    Pending appointments cannot be cancelled, per the spec. Note the patient
    has no confirm action at all: the spec's status flow and patient view
    give them exactly one verb.
    """
    appt = await _load(session, appointment_id)

    if appt.patient_id != patient.id:
        raise HTTPException(http.HTTP_403_FORBIDDEN, "Not your appointment.")
    if appt.status is not AppointmentStatus.confirmed:
        raise HTTPException(
            http.HTTP_409_CONFLICT,
            f"Only confirmed appointments can be cancelled (this one is "
            f"{appt.status.value}).",
        )

    old_status = appt.status.value

    result = await session.execute(
        update(Appointment)
        .where(
            Appointment.id == appointment_id,
            Appointment.version == expected_version, #problem 1: lost race with another write
            Appointment.status == AppointmentStatus.confirmed,
        )
        .values(
            status=AppointmentStatus.cancelled,
            version=Appointment.version + 1,
        )
    )

    if result.rowcount == 0:
        await session.rollback()
        # This is Problem 1's live case: the provider moved a confirmed
        # appointment while the patient was looking at the old time.
        raise _stale(await _load(session, appointment_id))

    _record(
        session,
        appointment_id=appointment_id,
        actor=patient,
        action=HistoryAction.cancelled,
        field="status",
        old=old_status,
        new=AppointmentStatus.cancelled.value,
    )
    await session.commit()
    await session.refresh(appt)
    return appt


# checks the provider exist -> starts_at is converted to UTC -> ends_at is calculated based on appointment type -> checks if user is provider and appointment is not cancelled -> checks for overlap if appointment is confirmed -> updates appointment with new starts_at and ends_at and increments version -> records history -> commits transaction
async def reschedule(
    session: AsyncSession,
    *,
    appointment_id: int,
    provider: User,
    starts_at: datetime,
    expected_version: int,
) -> Appointment:
    """Provider moves an appointment.

    Changes the time and nothing else. A rescheduled pending appointment
    stays pending; confirming is a separate, explicit action.
    """
    appt = await _load(session, appointment_id)

    if appt.provider_id != provider.id:
        raise HTTPException(http.HTTP_403_FORBIDDEN, "Not your appointment.")
    if appt.status is AppointmentStatus.cancelled:
        raise HTTPException(
            http.HTTP_409_CONFLICT, "Cancelled appointments cannot be rescheduled."
        )

    starts_at = _aware(starts_at)
    ends_at = _ends_at(starts_at, appt.appointment_type)
    old_starts = appt.starts_at

    if appt.status is AppointmentStatus.confirmed:
        clash = await find_overlap(
            session,
            provider_id=appt.provider_id,
            starts_at=starts_at,
            ends_at=ends_at,
            exclude_id=appt.id,
        )
        if clash is not None:
            raise _slot_taken(clash)

    try:
        result = await session.execute(
            update(Appointment)
            .where(
                Appointment.id == appointment_id,
                Appointment.version == expected_version, # problem 1: lost race with another write
            )
            .values(
                starts_at=starts_at,
                ends_at=ends_at,
                version=Appointment.version + 1,
            )
        )

        if result.rowcount == 0:
            await session.rollback()
            raise _stale(await _load(session, appointment_id))

        # Only starts_at is logged. ends_at is derived from it and the type,
        # so logging it too would be redundant.
        _record(
            session,
            appointment_id=appointment_id,
            actor=provider,
            action=HistoryAction.rescheduled,
            field="starts_at",
            old=old_starts.isoformat(),
            new=starts_at.isoformat(),
        )
        await session.commit()

    except IntegrityError as exc:
        await session.rollback()
        if getattr(exc.orig, "sqlstate", None) == EXCLUSION_VIOLATION:
            raise HTTPException(
                status_code=http.HTTP_409_CONFLICT,
                detail={
                    "code": "slot_taken",
                    "message": "That time overlaps another confirmed appointment.",
                },
            ) from exc
        raise

    await session.refresh(appt)
    return appt
