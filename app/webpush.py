"""The Web Push protocol, and nothing about this app.

This module is to push what ``app/sms.py`` is to one-time codes: the only
place the wire format exists. It knows how to seal a few hundred bytes for
one browser and POST them to that browser's push service. It does not know
what a notification is, who a member is, or what a subscription row looks
like -- that is ``app/push.py``, which is the only caller.

Three standards meet here, and it is worth naming which does what, because
each contributes exactly one header or one field:

* **RFC 8291 (Message Encryption)** -- the payload is encrypted *to the
  device*, with a key pair the browser generated and never sent anywhere
  else. Google, Mozilla and Apple operate the delivery service in the middle
  and cannot read a word of it. This is why a subscription carries ``p256dh``
  and ``auth``, and why there is no "just send a JSON body" shortcut: an
  unencrypted payload is refused by every push service.
* **RFC 8188 (aes128gcm)** -- the container the ciphertext travels in. One
  record, so the framing is a fixed 86-byte header and nothing else.
* **RFC 8292 (VAPID)** -- a signed assertion saying *who is sending*. One
  ECDSA key pair for the whole deploy, its public half handed to the browser
  at subscribe time so the push service can bind the two.

**No dependency on pywebpush/py-vapid.** All three specifications above come
to about a hundred lines put together, they cannot change under us, and the
alternative is two more packages in an image that already pins its own
transport (``httpx``) and its own crypto (``cryptography``, which those two
libraries are wrappers over anyway).

The one thing this module *does* interpret is a response code, and only far
enough to answer one question: is this handle dead? 404 and 410 are the push
services' way of saying a browser is gone for good, and they are the only
reliable garbage-collection signal a sender ever gets.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

#: How long a push service should hold a message for a phone that is off.
#: Four hours rather than days: everything this app pushes is an *event* that
#: can also be read in the bell, and a notification about a comment that
#: arrives on Thursday for something said on Monday is noise.
DEFAULT_TTL = 4 * 60 * 60

#: RFC 8188's record size. One record is all this app ever sends -- the
#: payload is a title and a sentence -- so this is only a framing constant.
RECORD_SIZE = 4096

#: A VAPID assertion is bound to one push service's origin and to a clock.
#: Twelve hours is under the 24 the spec allows and far longer than a single
#: send, so one token covers a whole broadcast to one service.
VAPID_TTL = 12 * 60 * 60

#: The two answers that mean "this browser is gone". Everything else -- a
#: 429, a 500, a timeout -- is transient, and the row is kept.
GONE_STATUSES = (404, 410)


class WebPushError(Exception):
    """A send that did not reach the push service, or that it refused."""

    def __init__(self, message: str, *, status: int | None = None, gone: bool = False):
        super().__init__(message)
        self.status = status
        #: True only for the two statuses above: the caller deletes the row.
        self.gone = gone


def b64url_decode(value: str) -> bytes:
    """Base64url without padding, which is how every one of these travels."""
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    """The uncompressed 65-byte P-256 point every one of these specs wants."""
    return key.public_bytes(
        encoding=Encoding.X962, format=PublicFormat.UncompressedPoint
    )


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    """HKDF (RFC 5869) for outputs of at most one SHA-256 block.

    Every derivation in RFC 8291 asks for 32 bytes or fewer, so the expand
    step is a single HMAC and the general loop would be dead code.
    """
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


@dataclass(frozen=True)
class VapidKeys:
    """The deploy's identity to every push service.

    Kept in the environment as two base64url strings -- the private key is
    the raw 32-byte scalar, the public key the uncompressed 65-byte point,
    which is the shape ``applicationServerKey`` takes in the browser. So the
    value in ``.env`` and the value the page passes to ``subscribe()`` are
    the same string, with nothing to convert between them.
    """

    private_key: ec.EllipticCurvePrivateKey
    public_bytes: bytes
    subject: str

    @property
    def public_key(self) -> str:
        return b64url_encode(self.public_bytes)

    @classmethod
    def load(cls, private_b64: str, public_b64: str, subject: str) -> VapidKeys:
        private_value = int.from_bytes(b64url_decode(private_b64), "big")
        return cls(
            private_key=ec.derive_private_key(private_value, ec.SECP256R1()),
            public_bytes=b64url_decode(public_b64),
            subject=subject,
        )


def generate_vapid_keys() -> tuple[str, str]:
    """A fresh (private, public) pair, base64url, for ``.env``.

    Rotating them is not free: a browser's subscription is bound to the
    public key it was created with, so every existing subscription stops
    working and each device has to be asked again. The same class of decision
    as rotating ``secret_key``.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return b64url_encode(private), b64url_encode(_public_bytes(key.public_key()))


def _vapid_header(keys: VapidKeys, endpoint: str) -> str:
    """The ``Authorization: vapid t=..., k=...`` header for one endpoint.

    The token is *audience-bound* -- signed for the origin of this endpoint
    and no other -- so one intercepted on the way to a push service cannot be
    replayed at another.
    """
    origin = urlparse(endpoint)
    audience = f"{origin.scheme}://{origin.netloc}"
    header = b64url_encode(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = b64url_encode(
        json.dumps(
            {
                "aud": audience,
                "exp": int(time.time()) + VAPID_TTL,
                "sub": keys.subject,
            }
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode("ascii")
    der = keys.private_key.sign(signing_input, ec.ECDSA(SHA256()))
    # JOSE wants the raw r||s pair, not the DER structure `cryptography`
    # produces -- the single most common reason a hand-rolled ES256 token is
    # rejected with no explanation attached.
    r, s = decode_dss_signature(der)
    signature = b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{claims}.{signature}, k={keys.public_key}"


def encrypt(payload: bytes, *, p256dh: str, auth: str) -> bytes:
    """Seal ``payload`` for one device, as a complete aes128gcm body.

    The ephemeral key pair is generated per message and thrown away with the
    stack frame: forward secrecy is free here, and reusing one would make
    every message ever sent to a device readable from one leaked scalar.
    """
    ua_public_bytes = b64url_decode(p256dh)
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), ua_public_bytes
    )
    auth_secret = b64url_decode(auth)

    ephemeral = ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(ephemeral.public_key())
    shared = ephemeral.exchange(ec.ECDH(), ua_public)

    # RFC 8291 3.4: the shared secret is stretched with the device's auth
    # secret and *both* public keys, so the derived key commits to who the
    # two parties are and not only to the maths between them.
    ikm = _hkdf(
        auth_secret,
        shared,
        b"WebPush: info\x00" + ua_public_bytes + as_public,
        32,
    )

    salt = os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)

    # `\x02` is RFC 8188's "this is the last record" delimiter, and it is part
    # of the plaintext rather than of the framing.
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + ciphertext


async def send(
    client: httpx.AsyncClient,
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    payload: bytes,
    keys: VapidKeys,
    ttl: int = DEFAULT_TTL,
) -> None:
    """POST one encrypted message. Raises :class:`WebPushError` on refusal.

    The client is passed in rather than opened here, so a broadcast uses one
    connection pool for the whole run -- the same reason ``_mute_map`` reads
    every recipient at once.
    """
    body = encrypt(payload, p256dh=p256dh, auth=auth)
    try:
        response = await client.post(
            endpoint,
            content=body,
            headers={
                "Authorization": _vapid_header(keys, endpoint),
                "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
                "TTL": str(ttl),
                # Everything here is worth waking a screen for and nothing is
                # time-critical; "normal" lets a phone batch it with whatever
                # else arrives rather than leaving doze for it.
                "Urgency": "normal",
            },
        )
    except httpx.HTTPError as exc:
        raise WebPushError(str(exc)) from exc

    if response.status_code in GONE_STATUSES:
        raise WebPushError(
            "subscription is gone", status=response.status_code, gone=True
        )
    if response.status_code >= 400:
        raise WebPushError(
            f"push service refused: {response.text[:200]}",
            status=response.status_code,
        )
