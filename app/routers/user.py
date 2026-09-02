import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import Select, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    get_admin_user,
    get_current_user,
    get_current_user_id,
    hash_password,
    set_session_cookie,
)
from app.database import get_db
from app.logging_config import log_event
from app.models.audit_base import newest_first
from app.models.user import User, UserRole
from app.permissions import Perm, can
from app.schemas.user import (
    UserAdminRead,
    UserCreate,
    UserPublicRead,
    UserRead,
    UserRoleUpdate,
    UserUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])

# The roster is paginated for the same reason the challenge list is: it is
# the one unbounded read in the app, and it grows with the membership rather
# than with anything the admin did.
DEFAULT_MEMBER_PAGE_SIZE = 20
MAX_MEMBER_PAGE_SIZE = 100

# `?role=` accepts the two real roles plus "all". Not a `UserRole` member,
# because "no filter" is not a role -- same shape as MINE_ALL/STATUS_ALL in
# routers/challenge.py.
ROLE_ALL = "all"
ROLE_VALUES = (ROLE_ALL, UserRole.MEMBER.value, UserRole.ADMIN.value)
ROLE_PATTERN = "^(all|member|admin)$"


def profile_visibility_filter(viewer: User):
    """A profile is visible to the member it belongs to -- and to an operator.

    Same shape as ``challenge_visibility_filter`` -- a clause composed into
    the query rather than a check bolted on after the row is loaded -- so the
    rule lives in one place and a miss falls out as "no such row" instead of
    a separate branch each caller has to remember. It is also what keeps the
    two front doors agreeing: ``GET /users/{id}`` and ``/views/users/{id}``
    compose this same clause, so loosening happens here or nowhere.

    **Own-profile-only is still the rule for members**, and for the original
    reason: the path id is a bare sequential integer and nothing in the app
    links a member to anyone else's profile, so anything looser for them is a
    walk of ``1..n`` that harvests the whole membership. That is also why the
    refusal is a 404 rather than a 403 at every caller.

    The one widening is ``Perm.USER_VIEW_ANY``, held by admins alone: the
    operator panel already lists every member, and a roster whose rows cannot
    be opened is a support desk that can see a name and nothing behind it. It
    is asked as a *permission* rather than as ``role == "admin"`` so the
    policy stays in ``app/permissions.py`` beside every other grant.

    Taking the ``User`` row rather than an id is what lets the clause ask
    that question at all -- and it costs nothing, since both callers already
    hold the row (``get_page_user`` / ``get_current_user`` load it to make an
    unrevocable cookie safe).
    """
    if can(viewer, Perm.USER_VIEW_ANY):
        return true()
    return User.id == viewer.id


def apply_member_filters(stmt: Select, q: str | None, role: str | None) -> Select:
    """The roster's search and role filters, shared by the JSON list endpoint
    and the admin page + its infinite-scroll fragment.

    Query logic lives here rather than in ``routers/views/admin.py`` for the
    same reason ``apply_challenge_filters`` does: the view imports it, so a
    filter can never mean one thing on the page and another in the API.

    Search spans name *and* email because those are the two things an admin
    actually has in hand when they go looking for someone -- a support
    request arrives as one or the other. LIKE wildcards in the term are
    escaped, so a query of ``%`` finds the member literally called ``%``
    rather than everyone.
    """
    if role and role != ROLE_ALL:
        stmt = stmt.where(User.role == role)
    q = (q or "").strip()
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        stmt = stmt.where(
            or_(
                User.name.ilike(pattern, escape="\\"),
                User.email.ilike(pattern, escape="\\"),
            )
        )
    return stmt


async def fetch_member_page(
    db: AsyncSession,
    *,
    q: str | None = None,
    role: str | None = None,
    offset: int = 0,
    limit: int = DEFAULT_MEMBER_PAGE_SIZE,
) -> tuple[list[User], bool]:
    """One page of the roster plus whether more remain.

    ``limit + 1`` is fetched and trimmed -- the same trick
    ``fetch_challenge_page`` uses -- so "is there another page" costs no
    second COUNT query. Ordered ``newest_first`` like every other list in
    the app: the members an operator has to deal with are the ones who just
    signed up, and the key is immutable, so the sequence a scroller pages
    through stays stable between requests.
    """
    stmt = apply_member_filters(select(User), q, role)
    stmt = stmt.order_by(*newest_first(User)).offset(offset).limit(limit + 1)
    members = list((await db.execute(stmt)).scalars().all())
    has_more = len(members) > limit
    return members[:limit], has_more


@router.get("/", response_model=list[UserAdminRead])
async def list_users(
    _admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(default=None, max_length=100),
    role: str = Query(default=ROLE_ALL, pattern=ROLE_PATTERN),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_MEMBER_PAGE_SIZE, ge=1, le=MAX_MEMBER_PAGE_SIZE),
):
    """Every member -- the admin panel's list, and the one read that ignores
    ``profile_visibility_filter`` on purpose.

    It is the widest read in the app, so it is the one route gated by
    ``Perm.USER_LIST`` rather than by mere authentication: before roles
    existed the only gate writable here was "is anyone signed in", which
    narrowed a roster harvest from "anyone" to "anyone who signs up". It
    answers ``UserAdminRead`` (email and role included) because the panel
    administers exactly those; the member-facing shape stays
    ``UserPublicRead``.

    It is paginated and filterable (``q``, ``role``, ``offset``, ``limit``)
    through the same ``fetch_member_page`` the panel's fragment endpoint
    uses, so the API and the page can never disagree about what a search
    matches.
    """
    members, _has_more = await fetch_member_page(
        db, q=q, role=role, offset=offset, limit=limit
    )
    return members


@router.get("/{user_id}", response_model=UserPublicRead)
async def get_user(
    user_id: int,
    viewer: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """404, not 403, for an id this caller may not see.

    A 403 here would answer the only question an enumerator is asking -- it
    confirms which ids exist -- and a member has no business distinguishing
    "not yours" from "not there" for a resource they can never be shown. An
    admin passes ``profile_visibility_filter`` and so never meets it.

    The read shape stays ``UserPublicRead`` for everyone, admin included: the
    wider ``UserAdminRead`` belongs to the roster route that requires
    ``Perm.USER_LIST``, and widening it here would widen it for members too.
    """
    result = await db.execute(
        select(User).where(User.id == user_id, profile_visibility_filter(viewer))
    )
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    return db_user


@router.post("/", response_model=UserRead, status_code=201)
async def create_user(
    user: UserCreate, response: Response, db: AsyncSession = Depends(get_db)
):
    if user.password != user.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    db_user = User(
        name=user.name,
        email=user.email,
        password_hash=hash_password(user.password),
        avatar=user.avatar,
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    set_session_cookie(response, db_user.id)
    log_event(logger, "user.registered", user_id=db_user.id, method="password")
    return db_user


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int,
    user: UserUpdate,
    viewer: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Your own account -- or, for an operator, a member's.

    ``Perm.USER_EDIT_ANY`` is the support grant (see ``app/permissions.py``
    for why it is given where ``CHALLENGE_EDIT`` is withheld). It changes who
    may call this and nothing else: the body is still ``UserUpdate``, so the
    role remains unreachable from here and keeps its own door,
    ``PATCH /users/{id}/role``.

    The refusal stays a **403 raised before the lookup**, for member and
    operator alike. Answering without touching the row is what keeps this
    route from leaking which ids exist -- every id gets the same answer -- so
    it needs no 404-vs-403 split of its own.

    ``last_modifier_user_id`` is the *acting* user, never the row's owner:
    that column is the only record that an edit was made on someone's behalf,
    and it is what makes granting the permission acceptable at all.
    """
    if user_id != viewer.id and not can(viewer, Perm.USER_EDIT_ANY):
        raise HTTPException(status_code=403, detail="Not allowed to edit this user")

    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    for key, value in user.model_dump(exclude_unset=True).items():
        setattr(db_user, key, value)
    db_user.updated_at = datetime.now(UTC)
    db_user.last_modifier_user_id = viewer.id

    try:
        await db.commit()
    except IntegrityError:
        # `email` is UNIQUE and editable from the profile's account-info
        # sheet, so a taken address is a routine outcome here, not a bug --
        # without this it surfaces as a 500 the sheet can only call "failed".
        await db.rollback()
        raise HTTPException(status_code=409, detail="Email already in use")
    await db.refresh(db_user)
    # Only the field *names* -- the values are the member's own details and
    # the log is not the place to keep a copy of them. `by_admin` marks an
    # edit made on somebody's behalf, the one thing that needs finding later.
    log_event(
        logger,
        "user.updated",
        user_id=db_user.id,
        fields=sorted(user.model_dump(exclude_unset=True)),
        by_admin=db_user.id != viewer.id,
    )
    return db_user


@router.patch("/{user_id}/role", response_model=UserAdminRead)
async def set_user_role(
    user_id: int,
    payload: UserRoleUpdate,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Change a member's app-wide role. The only writer of ``Users.role``.

    Its own route rather than a field on ``PATCH /users/{id}`` -- see
    ``UserRoleUpdate`` -- so the escalation path has exactly one door and
    that door carries the admin dependency.

    Self-demotion is refused: an admin dropping their own last privilege
    locks the panel for everyone, and there is no console to undo it from.
    Handing the role to someone else first is the supported way out.
    """
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot change your own role")

    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    previous_role = db_user.role
    db_user.role = payload.role.value
    db_user.updated_at = datetime.now(UTC)
    db_user.last_modifier_user_id = admin.id
    await db.commit()
    await db.refresh(db_user)
    # A privilege change is the audit line an operator will be asked about
    # months later, so it is WARNING: rare, and never routine noise.
    log_event(
        logger,
        "user.role_changed",
        level=logging.WARNING,
        user_id=db_user.id,
        actor_user_id=admin.id,
        previous_role=previous_role,
        role=db_user.role,
    )
    return db_user


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Own account only -- deliberately *not* widened to an operator.

    Reading a member's record and correcting it is support
    (``USER_EDIT_ANY``); erasing the person is not, and there is no
    permission for it. Even the owner is refused once any history exists --
    the IntegrityError below is answered 409 rather than cascaded, the same
    call ``DELETE /challenges/{id}`` makes once others have joined.
    """
    if user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Not allowed to delete this user")

    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(db_user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Cannot delete user with existing challenges or enrollments",
        )
    log_event(logger, "user.deleted", level=logging.WARNING, user_id=user_id)
