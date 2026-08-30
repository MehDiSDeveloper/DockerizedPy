from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import Select, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    get_admin_user,
    get_current_user_id,
    hash_password,
    set_session_cookie,
)
from app.database import get_db
from app.models.user import User, UserRole
from app.schemas.user import (
    UserAdminRead,
    UserCreate,
    UserPublicRead,
    UserRead,
    UserRoleUpdate,
    UserUpdate,
)

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


def profile_visibility_filter(user_id: int):
    """A profile is visible only to the member it belongs to.

    Same shape as ``challenge_visibility_filter`` -- a clause composed into
    the query rather than a check bolted on after the row is loaded -- so the
    rule lives in one place and a miss falls out as "no such row" instead of
    a separate branch each caller has to remember.

    Own-profile-only is the honest rule for what the app actually is today:
    nothing anywhere links to another member's profile (the bottom nav points
    at your own id; challenge-detail shows participant initials, never a
    roster), so there is no legitimate way to arrive at someone else's. The
    path id is a bare sequential integer, so anything looser than this is a
    walk of ``1..n`` that harvests the whole membership. If a participant
    list ever ships, loosen *this function* -- e.g. to "or we share a
    challenge" -- and both the page and the JSON endpoint follow.
    """
    return User.id == user_id


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
    second COUNT query. Ordered by id so the sequence a scroller pages
    through is stable between requests; a mutable sort key would let a row
    the admin has already seen reappear on the next page.
    """
    stmt = apply_member_filters(select(User), q, role)
    stmt = stmt.order_by(User.id).offset(offset).limit(limit + 1)
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
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """404, not 403, for someone else's id.

    A 403 here would answer the only question an enumerator is asking -- it
    confirms which ids exist -- and the caller has no business distinguishing
    "not yours" from "not there" for a resource they can never be shown.
    """
    result = await db.execute(
        select(User).where(User.id == user_id, profile_visibility_filter(current_user_id))
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
    return db_user


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int,
    user: UserUpdate,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    if user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Not allowed to edit this user")

    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    for key, value in user.model_dump(exclude_unset=True).items():
        setattr(db_user, key, value)
    db_user.updated_at = datetime.now(UTC)
    db_user.last_modifier_user_id = current_user_id

    try:
        await db.commit()
    except IntegrityError:
        # `email` is UNIQUE and editable from the profile's account-info
        # sheet, so a taken address is a routine outcome here, not a bug --
        # without this it surfaces as a 500 the sheet can only call "failed".
        await db.rollback()
        raise HTTPException(status_code=409, detail="Email already in use")
    await db.refresh(db_user)
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

    db_user.role = payload.role.value
    db_user.updated_at = datetime.now(UTC)
    db_user.last_modifier_user_id = admin.id
    await db.commit()
    await db.refresh(db_user)
    return db_user


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
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
