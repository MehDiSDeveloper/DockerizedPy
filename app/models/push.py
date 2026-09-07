"""One row per *device* a member has allowed to be woken.

A push subscription is not an account setting and not a preference -- it is a
handle a browser minted for one installation of the app on one device, and it
is only ever created and destroyed by that device. That is why this is a table
rather than a column on ``Users``: a member reads the app on a phone and a
laptop, and switching the phone off must not switch the laptop off with it.

**The endpoint is the identity.** A browser hands back a URL at its own push
service (`fcm.googleapis.com/...`, `updates.push.services.mozilla.com/...`,
`web.push.apple.com/...`); it is unguessable, it is what a message is POSTed
to, and re-subscribing on the same device returns the same one. So it is
UNIQUE, and saving is an upsert on it -- a member who re-enables the switch
after clearing a permission does not accumulate rows.

``p256dh`` and ``auth`` are the device's half of the encryption (RFC 8291):
the payload is sealed *to them* before it leaves this server, so the push
service in the middle forwards bytes it cannot read. They are stored exactly
as the browser base64url-encoded them.

**A dead subscription is deleted, never flagged.** A push service answers 404
or 410 for a handle whose browser is gone, which is the only reliable signal
this app gets, and a `disabled` column would need something to ever clear it.
See `app/push.py`.
"""

from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text

from app.models.audit_base import AuditBase


class PushSubscription(AuditBase):
    __tablename__ = "PushSubscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("Users.id"), nullable=False, index=True
    )
    #: The push service's URL for this device. Long on purpose -- FCM's runs
    #: past 200 characters and a truncated endpoint is a silently dead row.
    endpoint = Column(Text, nullable=False, unique=True)
    #: The device's public key (uncompressed P-256 point, base64url) and its
    #: 16-byte auth secret. Both come from `subscription.getKey()`.
    p256dh = Column(String(128), nullable=False)
    auth = Column(String(48), nullable=False)
    #: Purely so the settings screen can say *which* device this is. Never
    #: parsed for behaviour -- a user agent string is not a fact about a
    #: browser, it is a story it tells.
    user_agent = Column(String(255), nullable=True)

    __table_args__ = (
        # "everything this member is subscribed with", which is the only
        # question ever asked of this table.
        Index("ix_pushsubscriptions_user", "user_id"),
    )
