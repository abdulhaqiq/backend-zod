"""add_linkedin_fields_to_users

Revision ID: 4c10ad3f0e50
Revises: a1b2c3d4e5f7
Create Date: 2026-03-20 21:33:47.329980

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '4c10ad3f0e50'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('linkedin_id', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('linkedin_url', sa.String(length=512), nullable=True))
    op.add_column('users', sa.Column('linkedin_verified', sa.Boolean(), nullable=False, server_default='false'))
    op.create_index(op.f('ix_users_linkedin_id'), 'users', ['linkedin_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_linkedin_id'), table_name='users')
    op.drop_column('users', 'linkedin_verified')
    op.drop_column('users', 'linkedin_url')
    op.drop_column('users', 'linkedin_id')
