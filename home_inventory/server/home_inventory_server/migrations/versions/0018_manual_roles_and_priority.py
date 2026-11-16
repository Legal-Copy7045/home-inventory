"""manual roles and priority

Additive: ``attachments.manual_role`` (what a manual covers: operator,
engine, parts, service, other) and ``attachments.manual_priority`` (its rank
among the item's manuals: 1 wins when two manuals disagree).  Existing
manuals keep no role or rank and are read oldest first, as before.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-16 20:30:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('attachments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('manual_role', sa.String(length=12), nullable=True))
        batch_op.add_column(sa.Column('manual_priority', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('attachments', schema=None) as batch_op:
        batch_op.drop_column('manual_priority')
        batch_op.drop_column('manual_role')
