"""equipment entities and reservations

Additive: ``equipment_entities`` (Home Assistant entities shown with a piece
of equipment: battery, energy, power, status) and ``stock_reservations``
(stock set aside for repair jobs; reserving never changes quantities).

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-14 12:13:17.322207
"""

import sqlalchemy as sa
from alembic import op

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('equipment_entities',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('entity_id', sa.String(length=255), nullable=False),
    sa.Column('purpose', sa.String(length=12), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("purpose IN ('battery', 'energy', 'power', 'status', 'other')", name='ck_equipment_entity_purpose'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('item_id', 'entity_id', name='uq_equipment_entity')
    )
    with op.batch_alter_table('equipment_entities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_equipment_entities_item_id'), ['item_id'], unique=False)

    op.create_table('stock_reservations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('repair_job_id', sa.Integer(), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('quantity > 0', name='ck_reservation_quantity'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['repair_job_id'], ['repair_jobs.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('stock_reservations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_stock_reservations_item_id'), ['item_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_stock_reservations_repair_job_id'), ['repair_job_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('stock_reservations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_stock_reservations_repair_job_id'))
        batch_op.drop_index(batch_op.f('ix_stock_reservations_item_id'))

    op.drop_table('stock_reservations')
    with op.batch_alter_table('equipment_entities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_equipment_entities_item_id'))

    op.drop_table('equipment_entities')
