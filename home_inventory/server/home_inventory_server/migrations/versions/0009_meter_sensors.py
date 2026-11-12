"""operating hours / cycles read from Home Assistant sensors

Additive: two nullable columns on equipment_profiles.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-12 19:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('equipment_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('hours_entity_id', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('cycles_entity_id', sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('equipment_profiles', schema=None) as batch_op:
        batch_op.drop_column('cycles_entity_id')
        batch_op.drop_column('hours_entity_id')
