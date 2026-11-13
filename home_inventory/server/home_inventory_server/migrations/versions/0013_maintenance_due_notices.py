"""maintenance due notices

Additive: one nullable column recording which due date/meter reading a
task's "maintenance due" notice was last sent for, so it's sent once per
service interval.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-13 06:30:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('maintenance_tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('due_notified_key', sa.String(length=80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('maintenance_tasks', schema=None) as batch_op:
        batch_op.drop_column('due_notified_key')
