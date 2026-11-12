"""photo inventory for locations (shelves) as well as containers

container_scans gains location_id; container_id becomes optional; exactly
one of the two is set.  Existing scans all belong to containers, so they
satisfy the new check unchanged.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-12 17:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('container_scans', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location_id', sa.Integer(), nullable=True))
        batch_op.alter_column('container_id', existing_type=sa.Integer(), nullable=True)
        batch_op.create_foreign_key('fk_container_scans_location_id', 'locations', ['location_id'], ['id'], ondelete='CASCADE')
        batch_op.create_index(batch_op.f('ix_container_scans_location_id'), ['location_id'], unique=False)
        batch_op.create_check_constraint(
            'ck_scan_single_target', '(container_id IS NULL) != (location_id IS NULL)'
        )


def downgrade() -> None:
    op.execute("DELETE FROM container_scans WHERE container_id IS NULL")
    with op.batch_alter_table('container_scans', schema=None) as batch_op:
        batch_op.drop_constraint('ck_scan_single_target', type_='check')
        batch_op.drop_index(batch_op.f('ix_container_scans_location_id'))
        batch_op.drop_constraint('fk_container_scans_location_id', type_='foreignkey')
        batch_op.drop_column('location_id')
        batch_op.alter_column('container_id', existing_type=sa.Integer(), nullable=False)
