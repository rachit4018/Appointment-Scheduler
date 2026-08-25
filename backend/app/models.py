import enum
from datetime import datetime
from sqlalchemy import (Integer, String, Enum, Text, func, Index, ForeignKey, DateTime)

from sqlalchemy.orm import DeclarativeBase,Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass

class Role(str,enum.Enum):
    patient = "patient"
    provider = "provider"

class AppointmentStatus(str,enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    cancelled = "cancelled"

class AppointmentType(str,enum.Enum):
    consultation = "consultation"
    follow_up = "follow_up"
    annual_physical = "annual_physical"
    lab_review = "lab_review"

class HistoryAction(str,enum.Enum):
    created = "created"
    updated = "updated"
    rescheduled = "rescheduled"
    cancelled = "cancelled"


# Duration is a property of the appointment type, not something the patient
# picks. Kept as a module constant rather than a lookup table: it is
# configuration, not data.

AppointmentDurartions = {
    AppointmentType.consultation: 30,
    AppointmentType.follow_up: 15,
    AppointmentType.annual_physical: 60,
    AppointmentType.lab_review: 15,
}


class User(Base):
    """Patients and providers.
 
    One table with a role column rather than two tables: both are people with
    a name and an email, and the only difference is what they are allowed to
    do, which is authorization rather than identity.
 
    No password column. The assignment specifies no real auth, so identity
    comes from the role switcher (see app/deps.py).
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key = True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role,name="user_role"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default = func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<User {self.id} {self.name} ({self.role.value})>"


class Appointment(Base):
    """A booking between a patient and a provider.
 
    Note there is no provider_name column. The name is reachable through
    provider_id and is resolved at read time; storing it twice would let the
    two copies drift apart the first time a name is corrected.
    """
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key = True)
    
    # Both FKs point at users. Naming them patient_id / provider_id rather
    # than user_id / provider_id makes the relationship readable without
    # having to look anything up

    patient_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    provider_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    # Stored as a single tz-aware instant rather than separate date and time
    # columns, so "upcoming" is one comparison and ordering is one index.

    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone = True),nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), nullable=False)

    # Optimistic concurrency token (Problem 1). Incremented on every write.
    # Clients echo back the version they rendered; a mismatch is a 409.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    appointment_type: Mapped[AppointmentType] = mapped_column(Enum(AppointmentType, name="appointment_type"), nullable =False)
    reason: Mapped[str | None] = mapped_column(Text)

    status: Mapped[AppointmentStatus] = mapped_column(Enum(AppointmentStatus, name = "appointment_status"), nullable= False, default=AppointmentStatus.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(),onupdate=func.now())

    # foreign_keys is required: two columns point at the same table, so
    # SQLAlchemy cannot infer which relationship uses which.

    patient: Mapped["User"] = relationship(foreign_keys=[patient_id], lazy="joined")
    provider: Mapped["User"] = relationship(foreign_keys=[provider_id], lazy="joined")


    history: Mapped[list["AppointmentHistory"]] = relationship(
        back_populates="appointment",
        order_by="AppointmentHistory.created_at",
        cascade="all, delete-orphan",
    )
    # Names are resolved at read time from the FKs. This is the reason there
    # is no provider_name column: the name is present everywhere a human
    # sees it without ever being stored twice.
    @property
    def patient_name(self) -> str:
        return self.patient.name

    @property
    def provider_name(self) -> str:
        return self.provider.name

    # __table_args__ = (
    #     # Every list query filters by one participant and orders by time, so
    #     # both columns belong in the same index.
    #     Index("ix_appointments_provider_starts_at", "provider_id", "starts_at"),
    #     Index("ix_appointments_patient_starts_at", "patient_id", "starts_at"),
    #     # The overlap constraint (Problem 4) is added in the Alembic migration
    #     # rather than here, because it needs raw DDL: EXCLUDE USING gist.
    # )



class AppointmentHistory(Base):
    """Append-only audit trail (Problem 2).
 
    One mechanism covers create, confirm, cancel and reschedule rather than
    separate logs per change type. Values are stored structured
    (field / old / new) rather than as prose, so the appointment's state at
    any past moment can be reconstructed by replaying rows instead of by
    parsing English out of a text column.
 
    Nothing ever updates or deletes from this table.
    """

    __tablename__ = "appointment_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id",on_delete="CASCADE"), nullable=False,index=True)

    # Who did it. Comes straight from get_current_user, so it costs nothing.
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    action: Mapped[HistoryAction] = mapped_column(
        Enum(HistoryAction, name="history_action"), nullable=False
    )
    field_changed: Mapped[str | None] = mapped_column(String(40), nullable=False)
    old_value: Mapped[str | None] = mapped_column(String(64))
    new_value: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
 
    appointment: Mapped["Appointment"] = relationship(back_populates="history")
    actor: Mapped["User"] = relationship(lazy="joined")
 
    @property
    def actor_name(self) -> str:
        return self.actor.name
 
    __table_args__ = (
        Index("ix_history_appointment_created", "appointment_id", "created_at"),
    )