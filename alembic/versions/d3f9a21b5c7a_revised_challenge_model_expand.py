"""revised challenge model: cadence/visibility/checkins/stats (expand only)

Revision ID: d3f9a21b5c7a
Revises: c2b7f4a19d3e
Create Date: 2026-08-25 00:00:00.000000

This migration only adds and backfills. The legacy columns
(`is_public`, `challenge_type`, `recurrence_pattern`, `interval`, `end_date`)
are left in place so this step is reversible; they are dropped in a later
contract migration.
"""

import json
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3f9a21b5c7a"
down_revision: str | Sequence[str] | None = "c2b7f4a19d3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSONVariant = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

# Best-effort cutoff used by downgrade() to identify the owner-enrollment rows
# inserted by step 13 below (see downgrade() docstring note).
_BACKFILL_CUTOFF = datetime(2026, 8, 25, tzinfo=timezone.utc)

_CHALLENGE_TYPE_ENUM = postgresql.ENUM(
    "RecurringChallenge", "OneTimeChallenge", name="challengetype"
)
_RECURRING_TYPE_ENUM = postgresql.ENUM(
    "Daily", "Weekly", "Monthly", "yearly", name="recurringtype"
)
_ENROLLMENT_STATUS_ENUM = postgresql.ENUM(
    "ENROLLED", "PENDING", "IN_PROGRESS", "DONE", name="enrollmentstatus"
)


def _recurring_cadence(recurrence_pattern: str, interval: int | None, end_iso: str | None):
    n = interval if interval else 1
    if recurrence_pattern == "Daily":
        return {"kind": "recurring_days", "mode": "every_n_days", "n": n, "weekdays": [], "end_date": end_iso}
    if recurrence_pattern == "Weekly":
        return {"kind": "recurring_days", "mode": "every_n_weeks", "n": n, "weekdays": [], "end_date": end_iso}
    if recurrence_pattern == "Monthly":
        return {"kind": "recurring_days", "mode": "every_n_months", "n": n, "weekdays": [], "end_date": end_iso}
    if recurrence_pattern == "yearly":
        return {"kind": "recurring_days", "mode": "every_n_months", "n": n * 12, "weekdays": [], "end_date": end_iso}
    raise ValueError(f"Unknown recurrence_pattern: {recurrence_pattern!r}")


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. Add all new Challenges columns, nullable, no server defaults yet ---
    op.add_column("Challenges", sa.Column("cadence_kind", sa.String(32), nullable=True))
    op.add_column("Challenges", sa.Column("cadence", JSONVariant, nullable=True))
    op.add_column("Challenges", sa.Column("visibility", sa.String(16), nullable=True))
    op.add_column("Challenges", sa.Column("lifecycle_status", sa.String(16), nullable=True))
    op.add_column("Challenges", sa.Column("goal_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("Challenges", sa.Column("goal_unit", sa.String(32), nullable=True))
    op.add_column("Challenges", sa.Column("legacy_cadence", JSONVariant, nullable=True))

    # --- 2. Cast the three changing enum columns to VARCHAR, drop the enum types ---
    op.alter_column(
        "Challenges",
        "challenge_type",
        type_=sa.String(50),
        postgresql_using="challenge_type::text",
        existing_nullable=False,
    )
    op.alter_column(
        "Challenges",
        "recurrence_pattern",
        type_=sa.String(50),
        postgresql_using="recurrence_pattern::text",
        existing_nullable=True,
    )
    op.alter_column(
        "Enrollments",
        "status",
        type_=sa.String(16),
        postgresql_using="status::text",
        existing_nullable=False,
    )
    _CHALLENGE_TYPE_ENUM.drop(bind, checkfirst=True)
    _RECURRING_TYPE_ENUM.drop(bind, checkfirst=True)
    _ENROLLMENT_STATUS_ENUM.drop(bind, checkfirst=True)
    # Do not drop challengecategory -- it is untouched (D7/D8).

    # --- 3. Backfill visibility ---
    bind.execute(
        sa.text(
            "UPDATE \"Challenges\" SET visibility = CASE WHEN is_public THEN 'public' ELSE 'private' END"
        )
    )

    # --- 4. Backfill lifecycle_status ---
    bind.execute(sa.text("UPDATE \"Challenges\" SET lifecycle_status = 'active'"))

    # --- 5. Backfill cadence_kind / cadence / legacy_cadence ---
    rows = bind.execute(
        sa.text(
            'SELECT id, challenge_type, recurrence_pattern, interval, end_date '
            'FROM "Challenges"'
        )
    ).fetchall()
    for row in rows:
        end_iso = row.end_date.isoformat() if row.end_date else None
        if row.challenge_type == "OneTimeChallenge":
            cadence_kind = "once"
            cadence = {"kind": "once"}
            legacy_cadence = None
        else:  # RecurringChallenge
            legacy_cadence = {
                "recurrence_pattern": row.recurrence_pattern,
                "interval": row.interval,
                "end_date": end_iso,
            }
            cadence_kind = "recurring_days"
            cadence = _recurring_cadence(row.recurrence_pattern, row.interval, end_iso)
        bind.execute(
            sa.text(
                'UPDATE "Challenges" SET cadence_kind = :cadence_kind, '
                "cadence = CAST(:cadence AS JSONB), "
                "legacy_cadence = CAST(:legacy_cadence AS JSONB) "
                "WHERE id = :id"
            ),
            {
                "cadence_kind": cadence_kind,
                "cadence": json.dumps(cadence),
                "legacy_cadence": json.dumps(legacy_cadence) if legacy_cadence is not None else None,
                "id": row.id,
            },
        )

    # --- 6. Set Challenges NOT NULL ---
    op.alter_column("Challenges", "cadence_kind", nullable=False)
    op.alter_column("Challenges", "cadence", nullable=False)
    op.alter_column("Challenges", "visibility", nullable=False)
    op.alter_column("Challenges", "lifecycle_status", nullable=False)

    # --- 7. Add all new Enrollments columns, nullable; rename completed_count ---
    op.add_column("Enrollments", sa.Column("timezone", sa.String(64), nullable=True))
    op.add_column("Enrollments", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column("Enrollments", sa.Column("current_streak", sa.Integer(), nullable=True))
    op.add_column("Enrollments", sa.Column("longest_streak", sa.Integer(), nullable=True))
    op.add_column(
        "Enrollments", sa.Column("last_checkin_local_date", sa.Date(), nullable=True)
    )
    op.alter_column(
        "Enrollments", "completed_count", new_column_name="legacy_completed_count"
    )

    # --- 8. Backfill enrollments ---
    bind.execute(
        sa.text(
            "UPDATE \"Enrollments\" SET "
            "timezone = 'Asia/Tehran', "
            "start_date = (created_at AT TIME ZONE 'Asia/Tehran')::date, "
            "current_streak = 0, "
            "longest_streak = 0, "
            "last_checkin_local_date = NULL"
        )
    )

    # --- 9. Remap status ---
    bind.execute(
        sa.text(
            "UPDATE \"Enrollments\" SET status = CASE "
            "WHEN status IN ('ENROLLED', 'PENDING', 'IN_PROGRESS') THEN 'active' "
            "WHEN status = 'DONE' THEN 'completed' "
            "ELSE status END"
        )
    )

    # --- 10. Set Enrollments NOT NULL ---
    op.alter_column("Enrollments", "timezone", nullable=False)
    op.alter_column("Enrollments", "start_date", nullable=False)
    op.alter_column("Enrollments", "current_streak", nullable=False)
    op.alter_column("Enrollments", "longest_streak", nullable=False)
    op.alter_column("Enrollments", "legacy_completed_count", nullable=False)

    # --- 11. Create CheckIns ---
    op.create_table(
        "CheckIns",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "enrollment_id",
            sa.Integer(),
            sa.ForeignKey("Enrollments.id"),
            nullable=False,
        ),
        sa.Column(
            "challenge_id", sa.Integer(), sa.ForeignKey("Challenges.id"), nullable=False
        ),
        sa.Column("occurrence_key", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("occurrence_local_date", sa.Date(), nullable=False),
        sa.Column("occurred_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("photo_url", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_modifier_user_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint(
            "enrollment_id", "occurrence_key", name="uq_enrollment_occurrence"
        ),
    )
    op.create_index(
        "ix_checkins_challenge_created", "CheckIns", ["challenge_id", "created_at"]
    )
    op.create_index(
        "ix_checkins_enrollment_localdate",
        "CheckIns",
        ["enrollment_id", "occurrence_local_date"],
    )

    # --- 12. Create ChallengeStats ---
    op.create_table(
        "ChallengeStats",
        sa.Column(
            "challenge_id",
            sa.Integer(),
            sa.ForeignKey("Challenges.id"),
            primary_key=True,
        ),
        sa.Column("participant_count", sa.Integer(), nullable=False),
        sa.Column("total_completions", sa.Integer(), nullable=False),
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("last_checkin_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- 13. Insert the missing owner enrollments ---
    bind.execute(
        sa.text(
            """
            INSERT INTO "Enrollments"
              (challenge_id, user_id, status, legacy_completed_count, timezone,
               start_date, current_streak, longest_streak, created_at)
            SELECT c.id, c.owner_id, 'active', 0, 'Asia/Tehran',
                   (c.created_at AT TIME ZONE 'Asia/Tehran')::date, 0, 0, now()
            FROM "Challenges" c
            WHERE NOT EXISTS (
              SELECT 1 FROM "Enrollments" e
              WHERE e.challenge_id = c.id AND e.user_id = c.owner_id
            )
            """
        )
    )

    # --- 14. Seed ChallengeStats ---
    bind.execute(
        sa.text(
            """
            INSERT INTO "ChallengeStats"
              (challenge_id, participant_count, total_completions, total_amount, updated_at)
            SELECT c.id,
                   (SELECT count(*) FROM "Enrollments" e WHERE e.challenge_id = c.id),
                   COALESCE((SELECT sum(e.legacy_completed_count) FROM "Enrollments" e
                             WHERE e.challenge_id = c.id), 0),
                   0, now()
            FROM "Challenges" c
            """
        )
    )


def downgrade() -> None:
    """Reverse the expand migration.

    NOTE: this downgrade cannot restore the exact pre-migration enrollment
    status granularity. `ENROLLED` / `PENDING` / `IN_PROGRESS` were all
    collapsed to `active` on upgrade, and that distinction is lost -- this
    downgrade maps `active` back to `ENROLLED` for all of them.

    NOTE: the delete of the 75 owner-enrollment rows inserted by step 13 is
    best-effort. It identifies them as rows where `user_id = challenge.owner_id`,
    `legacy_completed_count = 0`, and `created_at >= _BACKFILL_CUTOFF` (this
    migration's Create Date, used as a stand-in for "the migration timestamp").
    It cannot distinguish those rows from a legitimate zero-progress owner
    enrollment created after this migration ran.
    """
    bind = op.get_bind()

    # --- reverse 14 & 12: drop ChallengeStats ---
    op.drop_table("ChallengeStats")

    # --- reverse 11: drop CheckIns ---
    op.drop_index("ix_checkins_enrollment_localdate", table_name="CheckIns")
    op.drop_index("ix_checkins_challenge_created", table_name="CheckIns")
    op.drop_table("CheckIns")

    # --- reverse 13: best-effort delete of inserted owner enrollments ---
    bind.execute(
        sa.text(
            """
            DELETE FROM "Enrollments" e
            USING "Challenges" c
            WHERE e.challenge_id = c.id
              AND e.user_id = c.owner_id
              AND e.legacy_completed_count = 0
              AND e.created_at >= :cutoff
            """
        ),
        {"cutoff": _BACKFILL_CUTOFF},
    )

    # --- reverse 9: remap statuses back (lossy, see docstring) ---
    bind.execute(
        sa.text(
            "UPDATE \"Enrollments\" SET status = CASE "
            "WHEN status = 'active' THEN 'ENROLLED' "
            "WHEN status = 'completed' THEN 'DONE' "
            "ELSE status END"
        )
    )

    # --- reverse 7: rename column back ---
    op.alter_column(
        "Enrollments", "legacy_completed_count", new_column_name="completed_count"
    )

    # --- reverse 7/8/10: drop the new Enrollments columns ---
    op.drop_column("Enrollments", "last_checkin_local_date")
    op.drop_column("Enrollments", "longest_streak")
    op.drop_column("Enrollments", "current_streak")
    op.drop_column("Enrollments", "start_date")
    op.drop_column("Enrollments", "timezone")

    # --- reverse 1/3/4/5/6: drop the new Challenges columns ---
    op.drop_column("Challenges", "legacy_cadence")
    op.drop_column("Challenges", "goal_unit")
    op.drop_column("Challenges", "goal_amount")
    op.drop_column("Challenges", "lifecycle_status")
    op.drop_column("Challenges", "visibility")
    op.drop_column("Challenges", "cadence")
    op.drop_column("Challenges", "cadence_kind")

    # --- reverse 2: recreate the three PG enum types and cast back ---
    _CHALLENGE_TYPE_ENUM.create(bind, checkfirst=True)
    _RECURRING_TYPE_ENUM.create(bind, checkfirst=True)
    _ENROLLMENT_STATUS_ENUM.create(bind, checkfirst=True)

    op.alter_column(
        "Challenges",
        "challenge_type",
        type_=_CHALLENGE_TYPE_ENUM,
        postgresql_using="challenge_type::challengetype",
        existing_nullable=False,
    )
    op.alter_column(
        "Challenges",
        "recurrence_pattern",
        type_=_RECURRING_TYPE_ENUM,
        postgresql_using="recurrence_pattern::recurringtype",
        existing_nullable=True,
    )
    op.alter_column(
        "Enrollments",
        "status",
        type_=_ENROLLMENT_STATUS_ENUM,
        postgresql_using="status::enrollmentstatus",
        existing_nullable=False,
    )
