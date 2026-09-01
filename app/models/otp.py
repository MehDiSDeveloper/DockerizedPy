"""The one-time code as a stored row -- the event, never the secret.

One table, ``OtpCodes``, and one rule that shapes all of it: **the code is
never stored**. What the row holds is an HMAC of the code bound to the mobile
it was sent to (``app.otp.hash_code``), so a dump of this table lets nobody
sign in as anybody -- the same reason ``Users.password_hash`` is a hash, for
a secret with a two-minute life instead of a two-year one.

Everything else on the row exists to answer one of the three questions a
login code has to survive:

- *is it still valid* -- ``expires_at``, ``consumed_at``
- *is somebody guessing* -- ``attempts``, and the burn rule in ``app.otp``
- *is somebody flooding* -- ``created_at`` (from ``AuditBase``) plus
  ``request_ip``, both read as a window rather than a counter, so the limit
  cannot drift out of step with the rows it is counting

Rows are kept after use rather than deleted. A consumed row is what makes a
replay of the same code fail closed, and the history is the only record of a
number being hammered; :func:`app.otp.purge_expired_codes` is what eventually
clears them.
"""

from sqlalchemy import Column, DateTime, Index, Integer, String

from app.models.audit_base import AuditBase
from app.phone import MOBILE_MAX_LENGTH


class OtpCode(AuditBase):
    __tablename__ = "OtpCodes"

    id = Column(Integer, primary_key=True, index=True)

    # Canonical `+989…`, the form `app.phone.normalize_mobile` produces. Never
    # a raw typed string: the row is looked up by this, so a second spelling
    # of one number would be a code the member can never use.
    mobile = Column(String(MOBILE_MAX_LENGTH), nullable=False, index=True)

    # HMAC-SHA256(secret_key, "<mobile>:<code>"), hex. Binding the mobile in
    # is what stops a code observed for one number from being replayed against
    # another -- without it, two rows created in the same second share a
    # digest and either code opens either account.
    code_hash = Column(String(64), nullable=False)

    # Why the code was sent. `login` is the only purpose today; the column
    # exists so a second one (changing your number, confirming a deletion)
    # cannot be satisfied by a code the member requested for a login.
    purpose = Column(String(24), nullable=False, default="login")

    # How many wrong guesses this row has taken. Bounded by
    # `app.otp.MAX_ATTEMPTS`, after which the row is burned rather than left
    # to be ground through: six digits is 10^6, which a script exhausts in
    # minutes if guessing is free.
    attempts = Column(Integer, nullable=False, default=0)

    expires_at = Column(DateTime(timezone=True), nullable=False)
    # Set the moment the code is accepted *or* burned. One column for both,
    # because both answers are the same to every later reader: this row can
    # no longer let anyone in.
    consumed_at = Column(DateTime(timezone=True), nullable=True)

    # Who asked. Only ever read as part of the per-IP window in `app.otp`, so
    # that one host cannot pay for SMS out of somebody else's budget by
    # walking a list of numbers. Nullable: a request through a transport that
    # exposes no client address is still a legitimate request.
    request_ip = Column(String(45), nullable=True)

    __table_args__ = (
        # Every read here is "the live rows for this mobile, newest first" and
        # every rate check is "rows for this mobile since T" -- one composite
        # index serves both, and without it each login attempt is a scan of
        # every code the app has ever sent.
        Index("ix_otpcodes_mobile_created", "mobile", "created_at"),
        Index("ix_otpcodes_ip_created", "request_ip", "created_at"),
    )
