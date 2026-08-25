"""initial schema with overlap exclusion constraint
 
Revision ID: 0001
Revises:
"""
 
import sqlalchemy as sa
from alembic import op
 
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None
 
 
def upgrade() -> None:
    # btree_gist lets a GiST index mix an equality column (provider_id) with
    # a range column (the time span). Without it, the constraint below cannot
    # be created.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
 
    user_role = sa.Enum("patient", "provider", name="user_role")
    appointment_status = sa.Enum(
        "pending", "confirmed", "cancelled", name="appointment_status"
    )
    appointment_type = sa.Enum(
        "consultation",
        "follow_up",
        "annual_physical",
        "lab_review",
        name="appointment_type",
    )
    history_action = sa.Enum(
        "created", "confirmed", "cancelled", "rescheduled", name="history_action"
    )
 
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("role", user_role, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
 
    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("patient_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("appointment_type", appointment_type, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column(
            "status", appointment_status, nullable=False, server_default="pending"
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_appointment_span"),
    )
    # op.create_index(
    #     "ix_appointments_provider_starts_at", "appointments", ["provider_id", "starts_at"]
    # )
    # op.create_index(
    #     "ix_appointments_patient_starts_at", "appointments", ["patient_id", "starts_at"]
    # )
 
    # ------------------------------------------------------------------
    # Problem 4.
    #
    # This is the whole answer. Two overlapping CONFIRMED appointments for
    # the same provider cannot exist, because the database refuses to store
    # the second one.
    #
    # Why here and not in the service layer: a check in Python has a window
    # between the SELECT and the UPDATE. Two requests can both read "no
    # conflict" and both write, because neither sees the other's uncommitted
    # transaction. The database is the only layer where concurrent
    # transactions are ordered, so it is the only layer where this can be
    # made impossible rather than unlikely.
    #
    # '[)' is half-open: an appointment ending at 14:30 and one starting at
    # 14:30 do not overlap.
    #
    # WHERE status = 'confirmed' is what makes the constraint partial:
    # pending requests may overlap freely, which is the point. Patients
    # request whatever time they want; only confirmation is exclusive.
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE appointments
        ADD CONSTRAINT no_overlapping_confirmed
        EXCLUDE USING gist (
            provider_id WITH =,
            tstzrange(starts_at, ends_at, '[)') WITH &&
        )
        WHERE (status = 'confirmed')
        """
    )
 
    op.create_table(
        "appointment_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "appointment_id",
            sa.Integer,
            sa.ForeignKey("appointments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", history_action, nullable=False),
        sa.Column("field_changed", sa.String(40), nullable=False),
        sa.Column("old_value", sa.String(64)),
        sa.Column("new_value", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_appointment_history_appointment_id", "appointment_history", ["appointment_id"]
    )
    op.create_index(
        "ix_history_appointment_created",
        "appointment_history",
        ["appointment_id", "created_at"],
    )
 
 
def downgrade() -> None:
    op.drop_table("appointment_history")
    op.execute("ALTER TABLE appointments DROP CONSTRAINT no_overlapping_confirmed")
    op.drop_table("appointments")
    op.drop_table("users")
    for name in ("history_action", "appointment_type", "appointment_status", "user_role"):
        op.execute(f"DROP TYPE IF EXISTS {name}")