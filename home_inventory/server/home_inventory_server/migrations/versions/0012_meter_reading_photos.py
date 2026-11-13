"""meter reading photos

Additive: one nullable column (the photo of the meter for a reading).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-13 05:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('meter_readings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('photo_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_meter_readings_photo_id', 'attachments', ['photo_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('meter_readings', schema=None) as batch_op:
        batch_op.drop_constraint('fk_meter_readings_photo_id', type_='foreignkey')
        batch_op.drop_column('photo_id')

