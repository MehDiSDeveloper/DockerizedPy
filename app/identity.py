"""Who a participant is to everyone else — the resolution, in one place.

A challenge carries an :class:`~app.models.challenge.IdentityMode` saying
whether its participants appear under their own name, under none, or under
whichever each of them picked. An enrollment carries the *answer* for one
member, ``Enrollment.is_anonymous``. This module is the only thing allowed
to turn the first into the second.

**The policy decides; the request is only consulted where the policy defers
to it.** There are two write boundaries and both go through this module:
:func:`resolve_anonymity` for somebody who joins, and
:func:`resolve_assigned_anonymity` for somebody a group administrator *puts
into* a challenge -- the second differs from the first in exactly one case,
and only because "was not asked" and "answered no" are not the same answer.
The first is called in
``routers/enrollment.py`` exactly as the ``avatar`` validator guards
``Users.avatar`` (CLAUDE.md, Avatars): a body asking to join a `named`
challenge anonymously is not an error, it is simply not the question that
challenge asks, and the stored flag says so. Nothing else validates, and
nothing else needs to.

**The answer is stored, not re-derived on every read.** The alternative --
recomputing from the challenge's *current* mode each time a name is
rendered -- is what makes a mode change retroactive, silently unmasking
people who joined under a different promise. The mode is therefore locked
once anyone but the owner has enrolled (``update_challenge``), so the stored
flag and the policy that produced it can never drift apart, and every
rendering surface can simply read the flag.

**The owner is never anonymous.** Anonymity here is about participation;
authorship is a separate thing, and challenge-detail keeps naming the
creator as «سازنده» in every mode. The creator's auto-enrolment is written
named and this function is not consulted for it.

Three surfaces read the answer, and each hides a different thing:

* the participant avatars on challenge-detail, via :func:`participant_avatar`
  -- a picture from a fixed catalogue names nobody, but an anonymous member
  showing *their own* avatar is still recognisable to anyone who has seen it
  elsewhere, so they get the same unset placeholder as a member who never
  picked one;
* the owner's join/leave notifications, which are raised with the actor left
  off entirely rather than with a name the feed would then have to remember
  to hide (see ``anonymous_actor`` in ``app/notifications.py``);
* the challenge leaderboard, via :func:`participant_display` -- the name-half
  of the same question, and the one surface that prints a member's name
  beside another member's numbers.

The operator panel is deliberately *not* one of them: an admin sees who is
really there, because moderating abuse it cannot attribute is not
moderation. The roster marks the row «ناشناس» instead, so the operator can
see the expectation they are holding.
"""

from __future__ import annotations

from app.models.challenge import IdentityMode


def resolve_anonymity(identity_mode: str | None, requested: bool) -> bool:
    """What ``Enrollment.is_anonymous`` becomes for someone joining.

    ``anonymous`` and ``named`` answer for everyone and ignore ``requested``;
    only ``member_choice`` consults it. An unrecognised mode -- a row from a
    newer version of the app, or one hand-edited -- resolves to the column
    default rather than raising: a challenge nobody can join is worse than
    one that behaves like the default it was created with.
    """
    if identity_mode == IdentityMode.ANONYMOUS.value:
        return True
    if identity_mode == IdentityMode.MEMBER_CHOICE.value:
        return bool(requested)
    return False


def resolve_assigned_anonymity(identity_mode: str | None) -> bool:
    """What ``is_anonymous`` becomes for somebody who was *put into* a
    challenge rather than joining it.

    Group challenges are the only place this happens: an administrator picks
    the participants, and the enrolment is written without the joiner ever
    being at a keyboard. :func:`resolve_anonymity` answers that case with
    ``requested=False`` -- i.e. *named* -- which is the wrong default here and
    is the one place the two functions differ.

    **Somebody who was never asked is not named.** A ``member_choice``
    challenge is one whose creator decided the answer belongs to each
    participant; assigning them into it and then publishing their name under
    the answer they did not give is the opposite of what that mode means. A
    corporate health challenge is exactly the case: an employee should not
    find their number beside their name because a screen they never saw
    defaulted for them. So the un-asked answer is the *private* one, and the
    member changes it themselves through
    ``PATCH /enrollments/{challenge_id}/anonymity`` -- which goes back
    through :func:`resolve_anonymity`, so the challenge's policy still
    decides and the door is not a way around it.

    ``named`` and ``anonymous`` are unaffected: they answer for everyone, and
    an assignment does not make the challenge a different challenge.
    """
    if identity_mode == IdentityMode.MEMBER_CHOICE.value:
        return True
    return resolve_anonymity(identity_mode, requested=False)


def asks_the_joiner(identity_mode: str | None) -> bool:
    """Whether joining this challenge is a question rather than a tap.

    The one thing the join button needs to know, named so the template does
    not restate the enum comparison and drift from :func:`resolve_anonymity`.
    """
    return identity_mode == IdentityMode.MEMBER_CHOICE.value


def participant_avatar(enrollment) -> str | None:
    """The avatar id to render for one participant, or ``None`` for unset.

    ``None`` is what ``avatar_url`` already answers ``_default.svg`` for, so
    an anonymous participant renders as the same shadowed head as a member
    who never picked a picture -- no new asset, and nothing that reads as
    "this one is hiding".
    """
    if enrollment.is_anonymous:
        return None
    return enrollment.user.avatar if enrollment.user else None


def register_identity_filters(env) -> None:
    """Expose the renderer to a Jinja environment.

    Every views router builds its own ``Jinja2Templates``, so a router that
    renders a participant must call this -- exactly like
    ``register_avatar_filters``. Forgetting it is a template error at render
    time, not at import.
    """
    env.filters["participant_avatar"] = participant_avatar


# What an anonymous participant is called on a surface that lists people by
# name. The same stand-in the notification feed uses for an actor it must not
# name (`UNKNOWN_ACTOR`), spelled here rather than imported: the two are
# deliberately indistinguishable to a reader, and tying them together would
# make one of them change when the other is reworded.
ANONYMOUS_NAME = "عضو ناشناس"


def participant_display(enrollment, viewer_id: int | None = None) -> dict:
    """How one participant appears to `viewer_id` on a list of participants.

    The leaderboard is the first surface in the app that prints a *name*
    beside somebody else's numbers, and this is the one place that decides
    what that name is -- the name-half of :func:`participant_avatar`, kept in
    the same module for the same reason: an anonymity promise honoured by the
    surfaces that remembered it is not a promise.

    Three answers, in this order:

    * **your own row is always you.** You already know who you are, so
      showing yourself your own name reveals nothing, and a leaderboard that
      cannot point at «تو» is a leaderboard nobody can read their position
      off. ``is_you`` is what the row highlights on.
    * **an anonymous participant is** :data:`ANONYMOUS_NAME` **and the unset
      avatar**, exactly what ``participant_avatar`` already answers -- their
      *own* picture is recognisable to anyone who has seen it elsewhere.
      They still rank: hiding the name is the promise, not withholding the
      score, and a leaderboard that dropped them would let everyone else
      derive who is missing.
    * **everyone else is their name and their picture** -- and nothing more.
      There is no link to a profile here, and that is the design: making the
      roster readable inside a challenge is one step, and reaching a member's
      profile from it is a second one that belongs to
      ``profile_visibility_filter`` alone (CLAUDE.md).
    """
    is_you = viewer_id is not None and enrollment.user_id == viewer_id
    hidden = bool(enrollment.is_anonymous) and not is_you
    return {
        "name": ANONYMOUS_NAME if hidden else (
            enrollment.user.name if enrollment.user else ANONYMOUS_NAME
        ),
        # Not `participant_avatar`: that one hides an anonymous member's
        # picture unconditionally, which is right for the avatar stack (where
        # nothing says which face is whose) and wrong here, where the row is
        # already labelled «تو» and hiding your own face from yourself would
        # just make your own position harder to find.
        "avatar": None if hidden else (
            enrollment.user.avatar if enrollment.user else None
        ),
        "is_anonymous": bool(enrollment.is_anonymous),
        "is_you": is_you,
    }
