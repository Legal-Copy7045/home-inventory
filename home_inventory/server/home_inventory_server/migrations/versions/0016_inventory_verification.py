"""inventory verification

Additive: ``verification_sessions`` (one physical check of a place, box or
category) and ``verification_entries`` (what was found for each thing), and
``items.verified_at`` / ``verified_by_id``.  Existing items start "never
checked"; nothing else changes.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-14 11:46:28.131076
"""

import sqlalchemy as sa
from alembic import op

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('verification_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('scope_type', sa.String(length=12), nullable=False),
    sa.Column('scope_id', sa.Integer(), nullable=False),
    sa.Column('scope_label', sa.String(length=300), nullable=False),
    sa.Column('started_by_id', sa.Integer(), nullable=True),
    sa.Column('started_by', sa.String(length=128), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expected', sa.JSON(), nullable=True),
    sa.Column('summary', sa.JSON(), nullable=True),
    sa.CheckConstraint("scope_type IN ('location', 'container', 'category')", name='ck_verification_scope'),
    sa.ForeignKeyConstraint(['started_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('verification_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_verification_sessions_scope_id'), ['scope_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_verification_sessions_started_at'), ['started_at'], unique=False)

    op.create_table('verification_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('session_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=True),
    sa.Column('item_name', sa.String(length=300), nullable=False),
    sa.Column('result', sa.String(length=12), nullable=False),
    sa.Column('quantity_before', sa.Integer(), nullable=True),
    sa.Column('quantity_after', sa.Integer(), nullable=True),
    sa.Column('recorded_place', sa.String(length=500), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('checked_by_id', sa.Integer(), nullable=True),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("result IN ('present', 'adjusted', 'missing', 'found_here')", name='ck_verification_result'),
    sa.ForeignKeyConstraint(['checked_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['session_id'], ['verification_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('verification_entries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_verification_entries_item_id'), ['item_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_verification_entries_session_id'), ['session_id'], unique=False)

    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('verified_by_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_items_verified_by_id', 'users', ['verified_by_id'], ['id'], ondelete='SET NULL')



def downgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.drop_constraint('fk_items_verified_by_id', type_='foreignkey')
        batch_op.drop_column('verified_by_id')
        batch_op.drop_column('verified_at')

    with op.batch_alter_table('verification_entries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_verification_entries_session_id'))
        batch_op.drop_index(batch_op.f('ix_verification_entries_item_id'))

    op.drop_table('verification_entries')
    with op.batch_alter_table('verification_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_verification_sessions_started_at'))
        batch_op.drop_index(batch_op.f('ix_verification_sessions_scope_id'))

    op.drop_table('verification_sessions')
