"""product web link; location QR codes

Revision ID: 0002
Revises: 0001

Written to be safe to re-run: 0.1.2/0.1.3 shipped a version of this
migration that could stop half way on databases with nested locations
(it rebuilt the locations table while foreign keys were enforced), leaving
``products.url`` and ``locations.qr_code`` in place but the revision at
0001.  Every step therefore checks what already exists, and the unique rule
on ``locations.qr_code`` is a unique index, which needs no table rebuild.
"""

import secrets

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

QR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()
    if "url" not in _columns("products"):
        op.add_column("products", sa.Column("url", sa.String(length=2000), nullable=True))
    if "qr_code" not in _columns("locations"):
        op.add_column("locations", sa.Column("qr_code", sa.String(length=16), nullable=True))

    # Give every location a permanent label code, unique across containers
    # and locations (a half-applied earlier attempt may have set some).
    used = {r[0] for r in conn.execute(sa.text("SELECT qr_code FROM containers")) if r[0]}
    used |= {r[0] for r in conn.execute(sa.text("SELECT qr_code FROM locations WHERE qr_code IS NOT NULL"))}
    seen: set[str] = set()
    for loc_id, code in conn.execute(sa.text("SELECT id, qr_code FROM locations ORDER BY id")).fetchall():
        if code and code not in seen:
            seen.add(code)
            continue
        while True:
            new = "".join(secrets.choice(QR_ALPHABET) for _ in range(8))
            if new not in used:
                used.add(new)
                seen.add(new)
                break
        conn.execute(sa.text("UPDATE locations SET qr_code = :c WHERE id = :i"), {"c": new, "i": loc_id})

    conn.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ix_locations_qr_code ON locations (qr_code)"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_locations_qr_code"))
    with op.batch_alter_table("locations") as batch:
        batch.drop_column("qr_code")
    with op.batch_alter_table("products") as batch:
        batch.drop_column("url")
