"""create users table

Revision ID: 0001_create_users_table
Revises:
Create Date: 2026-10-08 00:00:00.000000

Requires PostgreSQL 13 or newer (gen_random_uuid() is built in).

Notes for the ORM model and auth service:
- Emails must be stored lower-case. The database rejects anything else, so
  call email.strip().lower() before every insert and lookup.
- Declare the email column with unique=True only. Do not add index=True,
  or autogenerate will see a second index and try to change this one.
- updated_at is maintained by a trigger. The shared set_updated_at()
  function is reused by later migrations for other tables.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_create_users_table"
down_revision = None
branch_labels = None
depends_on = None

# To add a tier later, write a migration that replaces the check constraint.
SUBSCRIPTION_TIERS = ("free", "pro", "enterprise")
_TIER_LIST = ", ".join(f"'{tier}'" for tier in SUBSCRIPTION_TIERS)

SET_UPDATED_AT_FUNCTION = """
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

USERS_UPDATED_AT_TRIGGER = """
CREATE TRIGGER trg_users_set_updated_at
BEFORE UPDATE ON users
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
"""


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("subscription_tier", sa.String(50), server_default="free", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # Constraints are named explicitly so this file does not depend on
        # the naming convention configured in env.py.
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        # One unique constraint is enough. It creates the index on email.
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("email = lower(email)", name="ck_users_email_normalized"),
        sa.CheckConstraint("position('@' in email) > 1", name="ck_users_email_has_at"),
        sa.CheckConstraint(
            f"subscription_tier IN ({_TIER_LIST})",
            name="ck_users_subscription_tier",
        ),
    )
    op.execute(SET_UPDATED_AT_FUNCTION)
    op.execute(USERS_UPDATED_AT_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_users_set_updated_at ON users")
    op.drop_table("users")
    # Later migrations drop their own triggers first, since downgrades run
    # in reverse order, so nothing else depends on this function by now.
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")