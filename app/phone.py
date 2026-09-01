"""Iranian mobile numbers -- the one place a typed string becomes an identity.

A phone number is the *credential* in the OTP flow, so it is matched rather
than merely displayed, and every spelling of one number has to fold to the
same stored string. ``09121234567``, ``+98 912 123 4567``, ``۰۹۱۲۱۲۳۴۵۶۷`` and
``00989121234567`` are one account; without a single normalizer they are four,
and the fourth one gets an SMS the first three never see.

Two rules hold it together:

- **One canonical form, ``+989XXXXXXXXX``.** It is what
  :attr:`app.models.user.User.mobile` stores, what the OTP row is keyed on,
  and what the code's HMAC is bound to. Anything that cannot be read as an
  Iranian mobile is ``None`` -- refused at the boundary rather than stored in
  a shape half the app would fail to match later.
- **Iran-only, and deliberately so.** This is not
  ``schemas.user.UserBase._plausible_phone``, which stays permissive because
  ``Users.phone`` is a *contact detail* a member abroad may legitimately fill
  with anything dialable. This one gates a login, the SMS provider behind it
  (Kavenegar) delivers to Iranian carriers, and a number the app cannot text
  is a member the app cannot let back in. Widening it means widening the
  sender too, not just this regex.
"""

import re

# Farsi and Arabic-Indic digits, as typed on an Iranian keyboard or pasted out
# of an SMS. Folded before anything else looks at the string -- otherwise
# ۰۹۱۲... and 0912... are two different accounts for one phone.
DIGIT_FOLD = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# What gets stripped before the number is matched: whitespace, the dashes,
# dots and brackets a human types, and the invisible marks a Farsi keyboard
# and an RTL page wrap a left-to-right run in -- ZWNJ, LRM/RLM and the bidi
# embedding/override block, all of which really do travel with a number
# pasted out of a contact app.
#
# The invisible ones are named by code point rather than written into the
# pattern, because a source line carrying control characters is a line
# nobody can review -- it looks like a typo to every reader and to every
# diff.
_INVISIBLE = (0x200C, 0x200E, 0x200F, *range(0x202A, 0x202F))
_SEPARATORS = re.compile(
    r"[\s\-()." + "".join(chr(cp) for cp in _INVISIBLE) + "]"
)

_IRAN_MOBILE = re.compile(r"^(?:\+98|0098|98|0)?(9\d{9})$")

#: The canonical stored form, e.g. ``+989121234567``. 13 characters.
MOBILE_MAX_LENGTH = 16


def normalize_mobile(raw: str | None) -> str | None:
    """``+989XXXXXXXXX`` for anything readable as an Iranian mobile, else None.

    Returning ``None`` rather than raising keeps the decision with the caller:
    the schema turns it into a 422, the OTP router into a Farsi refusal, and a
    test can ask the question without catching anything.
    """
    if not raw:
        return None
    digits = _SEPARATORS.sub("", raw.translate(DIGIT_FOLD))
    match = _IRAN_MOBILE.match(digits)
    if not match:
        return None
    return f"+98{match.group(1)}"


def national_mobile(mobile: str) -> str:
    """``+989121234567`` -> ``09121234567`` -- the form Iranians read and dial.

    Storage stays canonical; this is presentation only, the same split the
    app already makes between UTC/Gregorian storage and Jalali display.
    """
    if mobile.startswith("+98"):
        return "0" + mobile[3:]
    return mobile


def mask_mobile(mobile: str) -> str:
    """``09121234567`` -> ``0912***4567``.

    Shown on the code-entry screen so the member can confirm *which* number
    the SMS went to before waiting on it, without the screen becoming a place
    a shoulder-surfer reads a whole number off. The prefix stays visible
    because that is the half people recognise their own line by.
    """
    national = national_mobile(mobile)
    if len(national) < 8:
        return national
    return f"{national[:4]}***{national[-4:]}"
