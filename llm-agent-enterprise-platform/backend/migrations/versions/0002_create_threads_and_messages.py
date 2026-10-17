"""create threads and messages

Revision ID: 0002_create_threads_and_messages
Revises: 0001_create_users_table
Create Date: 2026-10-08 00:01:00.000000

Notes for the ORM models and services:
- conversations.updated_at means "last activity". A trigger bumps it on every
  update and whenever a message is inserted, so ordering threads by
  updated_at DESC gives the sidebar order with no application code.
- messages.created_at uses clock_timestamp(), not now(). now() is frozen at
  the start of a transaction, so a user message and its reply saved in one
  transaction would get the same time and lose their order.
- tool_calls and rag_sources are JSONB. Declare them as
  sqlalchemy.dialects.postgresql.JSONB in the models.
- Allowed message roles are listed in MESSAGE_ROLES below.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_create_threads_and_messages"
down_revision = "0001_create_users_table"
branch_labels = None
depends_on = None

MESSAGE_ROLES = ("system", "user", "assistant", "tool")
_ROLE_LIST = ", ".join(f"'{role}'" for role in MESSAGE_ROLES)

# set_updated_at() is created in revision 0001.
CONVERSATIONS_UPDATED_AT_TRIGGER = """
CREATE TRIGGER trg_conversations_set_updated_at
BEFORE UPDATE ON conversations
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
"""

TOUCH_CONVERSATION_FUNCTION = """
CREATE OR REPLACE FUNCTION touch_conversation() RETURNS trigger AS $$
BEGIN
    UPDATE conversations SET updated_at = now() WHERE id = NEW.conversation_id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

MESSAGES_TOUCH_TRIGGER = """
CREATE TRIGGER trg_messages_touch_conversation
AFTER INSERT ON messages
FOR EACH ROW EXECUTE FUNCTION touch_conversation();
"""


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), server_default="New Conversation", nullable=False),
        sa.Column("is_pinned", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("model_override", sa.String(100), nullable=True),
        sa.Column("system_persona", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_conversations_user_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("char_length(btrim(title)) > 0", name="ck_conversations_title_not_blank"),
    )
    # Serves "this user's threads, newest first". Also covers the user_id
    # foreign key, so no separate index on user_id is needed.
    op.create_index(
        "ix_conversations_user_id_updated_at",
        "conversations",
        ["user_id", sa.text("updated_at DESC")],
    )
    op.execute(CONVERSATIONS_UPDATED_AT_TRIGGER)

    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=True),
        sa.Column("rag_sources", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"],
            name="fk_messages_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(f"role IN ({_ROLE_LIST})", name="ck_messages_role"),
        sa.CheckConstraint("token_count >= 0", name="ck_messages_token_count_non_negative"),
    )
    # Serves "this conversation's messages in order". Also covers the
    # conversation_id foreign key.
    op.create_index(
        "ix_messages_conversation_id_created_at",
        "messages",
        ["conversation_id", "created_at"],
    )
    op.execute(TOUCH_CONVERSATION_FUNCTION)
    op.execute(MESSAGES_TOUCH_TRIGGER)


def downgrade() -> None:
    # Dropping a table also drops its indexes and triggers.
    op.drop_table("messages")
    op.execute("DROP FUNCTION IF EXISTS touch_conversation()")
    op.drop_table("conversations")