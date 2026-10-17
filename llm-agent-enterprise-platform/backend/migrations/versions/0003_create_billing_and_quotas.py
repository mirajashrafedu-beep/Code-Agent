"""create billing and quotas

Revision ID: 0003_create_billing_and_quotas
Revises: 0002_create_threads_and_messages
Create Date: 2026-10-08 00:02:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0003_create_billing_and_quotas'
down_revision = '0002_create_threads_and_messages'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'user_quotas',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('monthly_token_limit', sa.BigInteger(), nullable=False, server_default='1000000'),
        sa.Column('tokens_used_this_month', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('monthly_spend_limit_usd', sa.Float(), nullable=False, server_default='50.0'),
        sa.Column('current_spend_usd', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('reset_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(op.f('ix_user_quotas_user_id'), 'user_quotas', ['user_id'], unique=True)

def downgrade() -> None:
    op.drop_index(op.f('ix_user_quotas_user_id'), table_name='user_quotas')
    op.drop_table('user_quotas')
