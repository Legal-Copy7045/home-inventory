"""who took an item, by user id

Additive: ``items.taken_by_id``.  Until now "Take it" recorded only the
taker's display name ("Taken by Sam" in ``status_note``).  Existing taken
items are matched to a user when exactly one user has that display name (or
user name); otherwise the column stays empty and the item still shows as
taken, by nobody in particular, until it's put away.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-14 07:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None

PREFIX = "Taken by "


def upgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('taken_by_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_items_taken_by_id'), ['taken_by_id'], unique=False)
        batch_op.create_foreign_key('fk_items_taken_by_id', 'users', ['taken_by_id'], ['id'], ondelete='SET NULL')

    bind = op.get_bind()
    users = bind.execute(sa.text("SELECT id, display_name, username FROM users")).fetchall()
    rows = bind.execute(sa.text(
        "SELECT id, status_note FROM items WHERE status = 'in_use' AND status_note LIKE :p"), {"p": PREFIX + "%"}).fetchall()
    for item_id, note in rows:
        name = note[len(PREFIX):].strip()
        matches = {u.id for u in users if name and name in (u.display_name, u.username)}
        if len(matches) == 1:
            bind.execute(sa.text("UPDATE items SET taken_by_id = :u WHERE id = :i"), {"u": matches.pop(), "i": item_id})


def downgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.drop_constraint('fk_items_taken_by_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_items_taken_by_id'))
        batch_op.drop_column('taken_by_id')
