"""widen new-api token key mapping length

Revision ID: 20260618_h8
Revises: 20260523_h7
Create Date: 2026-06-18

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '20260618_h8'
down_revision = '20260523_h7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        'llm_newapi_user_mapping',
        'newapi_token_key',
        existing_type=sa.String(length=48),
        type_=sa.String(length=128),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        'llm_newapi_user_mapping',
        'newapi_token_key',
        existing_type=sa.String(length=128),
        type_=sa.String(length=48),
        existing_nullable=False,
    )
