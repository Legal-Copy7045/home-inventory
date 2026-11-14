"""equipment and checklists

Additive.  Equipment profiles get ``managed`` (turning equipment back into an
ordinary item keeps its details, tasks and history), a lifecycle status and
where it came from.  Maintenance tasks get their checklist group and kind,
where they came from (user / manufacturer's manual / generic schedule) with
the page reference, a review status, whether a person has edited them, and
what the manual said.

Existing tasks keep working unchanged: they're marked as added by the user
(their origin wasn't recorded before) and get a checklist group from their
intervals: an hour interval -> "hours", only a yearly interval -> "annual",
another calendar interval -> "periodic", none -> "one_time".

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-14 11:05:44.501148
"""

import sqlalchemy as sa
from alembic import op

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('equipment_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('managed', sa.Boolean(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('lifecycle_status', sa.String(length=20), server_default='active', nullable=False))
        batch_op.add_column(sa.Column('acquired_from', sa.String(length=200), nullable=True))
        batch_op.create_index(batch_op.f('ix_equipment_profiles_managed'), ['managed'], unique=False)

    with op.batch_alter_table('maintenance_tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('schedule_group', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('task_kind', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('source', sa.String(length=12), server_default='user', nullable=False))
        batch_op.add_column(sa.Column('source_ref', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('reading_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('review_status', sa.String(length=12), server_default='approved', nullable=False))
        batch_op.add_column(sa.Column('user_edited', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('manual_values', sa.JSON(), nullable=True))
        batch_op.create_foreign_key('fk_maintenance_tasks_reading_id', 'manual_readings', ['reading_id'], ['id'], ondelete='SET NULL')

    op.execute("""
        UPDATE maintenance_tasks SET schedule_group = CASE
            WHEN interval_hours IS NOT NULL OR interval_cycles IS NOT NULL THEN 'hours'
            WHEN interval_days = 365 THEN 'annual'
            WHEN interval_days IS NOT NULL THEN 'periodic'
            ELSE 'one_time' END
        WHERE schedule_group IS NULL
    """)


def downgrade() -> None:
    with op.batch_alter_table('maintenance_tasks', schema=None) as batch_op:
        batch_op.drop_constraint('fk_maintenance_tasks_reading_id', type_='foreignkey')
        batch_op.drop_column('manual_values')
        batch_op.drop_column('user_edited')
        batch_op.drop_column('review_status')
        batch_op.drop_column('reading_id')
        batch_op.drop_column('source_ref')
        batch_op.drop_column('source')
        batch_op.drop_column('task_kind')
        batch_op.drop_column('schedule_group')

    with op.batch_alter_table('equipment_profiles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_equipment_profiles_managed'))
        batch_op.drop_column('acquired_from')
        batch_op.drop_column('lifecycle_status')
        batch_op.drop_column('managed')

