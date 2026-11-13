"""document readings

Additive: one new table (AI suggestions for documents attached to items).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-13 00:40:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('document_readings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('attachment_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('provider', sa.String(length=40), nullable=True),
    sa.Column('model', sa.String(length=120), nullable=True),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('pending', 'ready', 'failed', 'applied', 'dismissed')", name='ck_document_reading_status'),
    sa.ForeignKeyConstraint(['attachment_id'], ['attachments.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('document_readings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_document_readings_attachment_id'), ['attachment_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_document_readings_item_id'), ['item_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('document_readings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_document_readings_item_id'))
        batch_op.drop_index(batch_op.f('ix_document_readings_attachment_id'))

    op.drop_table('document_readings')
