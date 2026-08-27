"""rewrite recurring_quota month keys from Gregorian to Jalali months

Revision ID: e91c47d2f0a3
Revises: b8e5c07d24af
Create Date: 2026-08-27 00:00:00.000000

`recurring_quota` with `period="month"` used to count a *Gregorian* month:
`period_bounds` did `d.replace(day=1)` and `month_key` emitted `M2026-08`. In a
Farsi, Jalali-facing app that is the wrong month -- the counter reset on 1
August, which falls mid-Shahrivar, so a user who met their quota within the
month they could actually see was split across two buckets and shown as having
missed it. Weeks were already Iranian (Saturday-start); only months were not.

`app.occurrences.month_key` now emits `M1405-06`, so every stored key of the
old shape has to be rewritten or it becomes unreachable: `_parse_period_key`
would read `M2026-08` as Jalali year 2026 and silently place the row ~600 years
in the future, where nothing would ever match it.

`CheckIns.occurrence_local_date` makes the rewrite lossless -- the new period is
derived from the day the check-in was actually recorded, not from the old key
(a Gregorian month straddles two Jalali months, so the key alone is ambiguous).
The `#seq` suffix is then renumbered from 1 within each new period.

Two consequences worth knowing:

* Rewriting is done in two passes through a temporary `MIGRATING<id>#…` key.
  `UniqueConstraint(enrollment_id, occurrence_key)` would otherwise be tripped
  mid-update by a row moving onto a key another row has not yet vacated.
* Where two Gregorian months merge into one Jalali month, the merged period can
  end up holding more check-ins than the cadence's `count`. They all still
  count toward the period total, but a sequence above `count` is refused by
  `is_key_writable`, so those specific rows become read-only. Nothing is lost.

The downgrade reverses the mapping the same way, from `occurrence_local_date`.
"""

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa

from alembic import op
from app.jalali import to_jalali

# revision identifiers, used by Alembic.
revision: str = "e91c47d2f0a3"
down_revision: str | Sequence[str] | None = "b8e5c07d24af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _gregorian_month_key(d: date) -> str:
    return f"M{d.year:04d}-{d.month:02d}"


def _jalali_month_key(d: date) -> str:
    jy, jm, _ = to_jalali(d)
    return f"M{jy:04d}-{jm:02d}"


def _rewrite(key_for) -> None:
    """Re-bucket every month-period check-in using `key_for(local_date)`."""
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            'SELECT id, enrollment_id, occurrence_key, occurrence_local_date '
            'FROM "CheckIns" WHERE occurrence_key LIKE :pattern '
            "ORDER BY enrollment_id, occurrence_local_date, id"
        ),
        {"pattern": "M%#%"},
    ).fetchall()
    if not rows:
        return

    seq_by_period: dict[tuple[int, str], int] = {}
    moves: list[tuple[int, str, str]] = []
    for row_id, enrollment_id, old_key, local_date in rows:
        # SQLite hands back a string where Postgres hands back a date.
        if isinstance(local_date, str):
            local_date = date.fromisoformat(local_date)
        new_period = key_for(local_date)
        bucket = (enrollment_id, new_period)
        seq = seq_by_period.get(bucket, 0) + 1
        seq_by_period[bucket] = seq
        new_key = f"{new_period}#{seq}"
        if new_key != old_key:
            moves.append((row_id, f"MIGRATING{row_id}#{seq}", new_key))

    update = sa.text('UPDATE "CheckIns" SET occurrence_key = :key WHERE id = :id')
    for row_id, temp_key, _ in moves:
        conn.execute(update, {"key": temp_key, "id": row_id})
    for row_id, _, new_key in moves:
        conn.execute(update, {"key": new_key, "id": row_id})


def upgrade() -> None:
    _rewrite(_jalali_month_key)


def downgrade() -> None:
    _rewrite(_gregorian_month_key)
