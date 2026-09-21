"""the act layer: referees, proof, verdicts and the immutable log

Turns a challenge into a *commitment* with three settable axes, without
changing what a single existing row means:

* **roles** -- ``ChallengeReferees``, a new and empty table.
* **proof** -- ``Challenges.proof_kind`` (server_default ``self``) plus a
  nullable ``Challenges.proof`` JSON column, and ``ProofAssets`` for the one
  proof kind that is a file.
* **outcome** -- ``Challenges.review_mode`` (server_default ``auto``) and
  ``CheckIns.verdict`` (server_default ``auto``), beside the ``state`` column
  that is deliberately left exactly as it was.

plus ``ActEvents``, the append-only log.

**There is no data migration, and that is the design rather than luck.**
Every column added here carries the old behaviour as its server default, so
the backfill Postgres performs when it adds them is itself the migration:
every challenge that exists becomes ``proof_kind = self`` /
``review_mode = auto`` -- self-reported, settled on the spot -- and every
check-in ever recorded becomes ``verdict = auto``, which is precisely what
"there was no referee and the report was final" means. The one predicate the
app counts on, ``state = 'completed' AND verdict IN ('auto','approved')``,
therefore selects exactly the rows ``state = 'completed'`` selected before.

``Challenges.proof`` is nullable where ``Challenges.cadence`` is not, and
that is what makes this cheap: a JSON server default is awkward on both
backends, and NULL already has an unambiguous reading --
``app.proof.parse_proof`` resolves it from ``proof_kind``.

Revision ID: c9d41f6a2b80
Revises: bfb2303d362b
Create Date: 2026-09-20 10:12:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d41f6a2b80"
down_revision: str | Sequence[str] | None = "bfb2303d362b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONVariant = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    """Upgrade schema."""
    # --- the role axis ---------------------------------------------------
    op.create_table(
        "ChallengeReferees",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("challenge_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "state", sa.String(length=16), server_default="invited", nullable=False
        ),
        sa.Column("invited_by_user_id", sa.Integer(), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_modifier_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["challenge_id"], ["Challenges.id"]),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["Users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["Users.id"]),
        sa.PrimaryKeyConstraint("id"),
        # One row per person per act: re-inviting somebody updates the row
        # they already have rather than stacking a second history on them.
        sa.UniqueConstraint(
            "challenge_id", "user_id", name="uq_referee_challenge_user"
        ),
    )
    op.create_index(
        op.f("ix_ChallengeReferees_challenge_id"),
        "ChallengeReferees",
        ["challenge_id"],
    )
    op.create_index(
        op.f("ix_ChallengeReferees_id"), "ChallengeReferees", ["id"]
    )
    # "What is waiting for me" -- the review queue's only read.
    op.create_index(
        "ix_referees_user_state", "ChallengeReferees", ["user_id", "state"]
    )

    # --- the proof ledger -------------------------------------------------
    op.create_table(
        "ProofAssets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("media_key", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_modifier_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["Users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("media_key", name="uq_proof_media_key"),
    )
    op.create_index(op.f("ix_ProofAssets_id"), "ProofAssets", ["id"])
    op.create_index(
        "ix_proof_assets_user_created", "ProofAssets", ["user_id", "created_at"]
    )

    # --- the log ----------------------------------------------------------
    op.create_table(
        "ActEvents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("challenge_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("subject_user_id", sa.Integer(), nullable=True),
        # Deliberately not a foreign key: the line has to outlive the report
        # it is about (see `app/models/act.py`).
        sa.Column("checkin_id", sa.Integer(), nullable=True),
        sa.Column("data", JSONVariant, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["Users.id"]),
        sa.ForeignKeyConstraint(["challenge_id"], ["Challenges.id"]),
        sa.ForeignKeyConstraint(["subject_user_id"], ["Users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ActEvents_challenge_id"), "ActEvents", ["challenge_id"])
    op.create_index(op.f("ix_ActEvents_id"), "ActEvents", ["id"])
    # By `id`, not `created_at`: `now()` is second-granular on SQLite and a
    # report and the ruling on it can land inside one second.
    op.create_index("ix_act_events_challenge_id", "ActEvents", ["challenge_id", "id"])

    # --- the two axes on Challenges --------------------------------------
    op.add_column(
        "Challenges",
        sa.Column(
            "proof_kind", sa.String(length=16), server_default="self", nullable=False
        ),
    )
    op.add_column("Challenges", sa.Column("proof", JSONVariant, nullable=True))
    op.add_column(
        "Challenges",
        sa.Column(
            "review_mode", sa.String(length=16), server_default="auto", nullable=False
        ),
    )

    # --- the outcome axis on CheckIns ------------------------------------
    op.add_column(
        "CheckIns",
        sa.Column(
            "verdict", sa.String(length=16), server_default="auto", nullable=False
        ),
    )
    op.add_column("CheckIns", sa.Column("proof_asset_id", sa.Integer(), nullable=True))
    op.add_column(
        "CheckIns", sa.Column("verdict_by_user_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "CheckIns", sa.Column("verdict_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("CheckIns", sa.Column("verdict_note", sa.String(length=300), nullable=True))
    op.create_unique_constraint(
        "uq_checkin_proof_asset", "CheckIns", ["proof_asset_id"]
    )
    op.create_foreign_key(
        "fk_checkins_proof_asset", "CheckIns", "ProofAssets", ["proof_asset_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_checkins_verdict_by", "CheckIns", "Users", ["verdict_by_user_id"], ["id"]
    )


def downgrade() -> None:
    """Downgrade schema.

    Reversible in shape: the columns and tables go, and with them every
    verdict, referee and logged event. What survives untouched is
    ``CheckIns.state`` -- the column this feature deliberately did not widen
    -- so a rolled-back deployment reads every report exactly as it did
    before, which is the point of having left it alone.
    """
    op.drop_constraint("fk_checkins_verdict_by", "CheckIns", type_="foreignkey")
    op.drop_constraint("fk_checkins_proof_asset", "CheckIns", type_="foreignkey")
    op.drop_constraint("uq_checkin_proof_asset", "CheckIns", type_="unique")
    op.drop_column("CheckIns", "verdict_note")
    op.drop_column("CheckIns", "verdict_at")
    op.drop_column("CheckIns", "verdict_by_user_id")
    op.drop_column("CheckIns", "proof_asset_id")
    op.drop_column("CheckIns", "verdict")

    op.drop_column("Challenges", "review_mode")
    op.drop_column("Challenges", "proof")
    op.drop_column("Challenges", "proof_kind")

    op.drop_index("ix_act_events_challenge_id", table_name="ActEvents")
    op.drop_index(op.f("ix_ActEvents_id"), table_name="ActEvents")
    op.drop_index(op.f("ix_ActEvents_challenge_id"), table_name="ActEvents")
    op.drop_table("ActEvents")

    op.drop_index("ix_proof_assets_user_created", table_name="ProofAssets")
    op.drop_index(op.f("ix_ProofAssets_id"), table_name="ProofAssets")
    op.drop_table("ProofAssets")

    op.drop_index("ix_referees_user_state", table_name="ChallengeReferees")
    op.drop_index(op.f("ix_ChallengeReferees_id"), table_name="ChallengeReferees")
    op.drop_index(
        op.f("ix_ChallengeReferees_challenge_id"), table_name="ChallengeReferees"
    )
    op.drop_table("ChallengeReferees")
