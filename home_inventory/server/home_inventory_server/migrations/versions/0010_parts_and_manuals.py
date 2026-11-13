"""parts, standards and manual readings

Additive: four new tables (product_links, part_fitments, task_parts,
manual_readings) and four columns on products (is_generic defaults to 0).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-12 23:40:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('part_fitments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('equipment_product_id', sa.Integer(), nullable=False),
    sa.Column('part_product_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=80), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=10, scale=3), nullable=True),
    sa.Column('unit', sa.String(length=10), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.Column('source_ref', sa.String(length=200), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('suggested', 'confirmed', 'rejected')", name='ck_fitment_status'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['equipment_product_id'], ['products.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['part_product_id'], ['products.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('equipment_product_id', 'part_product_id', 'role', name='uq_part_fitment')
    )
    with op.batch_alter_table('part_fitments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_part_fitments_equipment_product_id'), ['equipment_product_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_part_fitments_part_product_id'), ['part_product_id'], unique=False)

    op.create_table('product_links',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("kind IN ('meets', 'replaced_by')", name='ck_product_link_kind'),
    sa.CheckConstraint("status IN ('suggested', 'confirmed', 'rejected')", name='ck_product_link_status'),
    sa.CheckConstraint('product_id != target_id', name='ck_product_link_not_self'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['target_id'], ['products.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('product_id', 'target_id', 'kind', name='uq_product_link')
    )
    with op.batch_alter_table('product_links', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_product_links_product_id'), ['product_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_product_links_target_id'), ['target_id'], unique=False)

    op.create_table('manual_readings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('attachment_ids', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('provider', sa.String(length=40), nullable=True),
    sa.Column('model', sa.String(length=120), nullable=True),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('pending', 'ready', 'failed', 'applied', 'discarded')", name='ck_manual_reading_status'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('manual_readings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manual_readings_item_id'), ['item_id'], unique=False)

    op.create_table('task_parts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=10, scale=3), nullable=False),
    sa.Column('unit', sa.String(length=10), nullable=True),
    sa.Column('allow_equivalent', sa.Boolean(), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=True),
    sa.CheckConstraint('quantity > 0', name='ck_task_part_quantity'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['maintenance_tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('task_parts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_task_parts_product_id'), ['product_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_task_parts_task_id'), ['task_id'], unique=False)

    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_generic', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('specs', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('content_quantity', sa.Numeric(precision=10, scale=3), nullable=True))
        batch_op.add_column(sa.Column('content_unit', sa.String(length=10), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_column('content_unit')
        batch_op.drop_column('content_quantity')
        batch_op.drop_column('specs')
        batch_op.drop_column('is_generic')

    with op.batch_alter_table('task_parts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_task_parts_task_id'))
        batch_op.drop_index(batch_op.f('ix_task_parts_product_id'))

    op.drop_table('task_parts')
    with op.batch_alter_table('manual_readings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manual_readings_item_id'))

    op.drop_table('manual_readings')
    with op.batch_alter_table('product_links', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_product_links_target_id'))
        batch_op.drop_index(batch_op.f('ix_product_links_product_id'))

    op.drop_table('product_links')
    with op.batch_alter_table('part_fitments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_part_fitments_part_product_id'))
        batch_op.drop_index(batch_op.f('ix_part_fitments_equipment_product_id'))

    op.drop_table('part_fitments')
