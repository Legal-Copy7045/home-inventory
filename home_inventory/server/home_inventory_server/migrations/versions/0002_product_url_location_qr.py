"""product web link; location QR codes

Revision ID: 0002
Revises: 0001
"""

import secrets

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

QR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def upgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("url", sa.String(length=2000), nullable=True))
    with op.batch_alter_table("locations") as batch:
        batch.add_column(sa.Column("qr_code", sa.String(length=16), nullable=True))
    # Give every existing location a permanent label code.
    conn = op.get_bind()
    used = {r[0] for r in conn.execute(sa.text("SELECT qr_code FROM containers"))}
    for (loc_id,) in conn.execute(sa.text("SELECT id FROM locations WHERE qr_code IS NULL")).fetchall():
        while True:
            code = "".join(secrets.choice(QR_ALPHABET) for _ in range(8))
            if code not in used:
                used.add(code)
                break
        conn.execute(sa.text("UPDATE locations SET qr_code = :c WHERE id = :i"), {"c": code, "i": loc_id})
    with op.batch_alter_table("locations") as batch:
        batch.create_unique_constraint("uq_locations_qr_code", ["qr_code"])


def downgrade() -> None:
    with op.batch_alter_table("locations") as batch:
        batch.drop_constraint("uq_locations_qr_code", type_="unique")
        batch.drop_column("qr_code")
    with op.batch_alter_table("products") as batch:
        batch.drop_column("url")
