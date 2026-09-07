"""The shapes a browser and this server exchange about one device.

Deliberately the *browser's* shape rather than one of our own:
``PushSubscription.toJSON()`` in the page produces exactly
``{"endpoint": ..., "keys": {"p256dh": ..., "auth": ...}}``, so the page
posts what the browser handed it and nothing is unpacked and re-packed on
the way -- which is where a p256dh and an auth secret get swapped.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class PushKeys(BaseModel):
    """The device's half of the encryption, base64url as the browser wrote it."""

    p256dh: str = Field(min_length=1, max_length=128)
    auth: str = Field(min_length=1, max_length=48)


class PushSubscriptionCreate(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)
    keys: PushKeys

    @field_validator("endpoint")
    @classmethod
    def _https_only(cls, value: str) -> str:
        """A push endpoint is always https, and this is a URL we will POST to.

        The only field here that becomes an outbound request, so it is the
        only one worth refusing: without this, a body could point the sender
        at an internal address and use it to probe the network from inside.
        """
        if not value.startswith("https://"):
            raise ValueError("endpoint must be an https URL")
        return value


class PushSubscriptionDelete(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)


class PushKeyRead(BaseModel):
    """What a page needs before it may even offer the switch.

    ``enabled`` is false on a deploy with no VAPID keys -- development, by
    default -- and the page renders no row at all rather than one that would
    fail on the tap. The same rule the install row follows.
    """

    enabled: bool
    public_key: str | None = None
