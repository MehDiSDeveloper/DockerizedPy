"""«ریل» و «لیست» — the challenge list's two shapes.

The list draws the *same* cards two ways and the shape is the only state:
`.rail` on the container is the cover feed, `.is-immersive` on that same
element is the full-screen reader. Both are client-side, so what a server
test can pin is the contract underneath them, and each of these is a thing
that has already broken once in this codebase's shape:

* **one markup, not two** — the reader's extras ride inside the card, so a
  card arriving on page two of the infinite scroll behaves exactly like a
  card the page rendered. The fragment is the half that silently drifts.
* **the shared partial still renders elsewhere** — `_challenge_cards.html`
  is rendered by the group router's Jinja env too, which registers a smaller
  filter set than the challenge router's. A filter added to the partial
  without checking that env is a 500 on the group page and nowhere else.
* **«لیست» is unchanged** — the card is still one real `<a href>` to the
  challenge, which is both the list mode's whole behaviour and the reader's
  «صفحه چالش» action (it declines to preventDefault rather than navigating
  itself).
* **the default is «ریل»**, and the switch offers exactly the two modes.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.group_helpers import (
    add_member,
    make_challenge,
    make_group,
    make_user,
    sign_in,
)


@pytest.fixture
async def described(db: AsyncSession):
    owner = await make_user(db, "Owner")
    challenge = await make_challenge(db, owner, title="چالش ریل")
    # Long enough to be cut: «بیشتر» exists only when there is something
    # behind it, which is the whole point of the rule below.
    challenge.description = (
        "توضیح کامل چالش برای نمای تمام‌صفحه، بلند به اندازه‌ای که "
        "کوتاه‌شده نمایش داده شود و کلید «بیشتر» معنا پیدا کند."
    )
    await db.commit()
    return owner, challenge


async def test_the_page_ships_in_rail_with_both_modes_offered(
    client: AsyncClient, described
) -> None:
    res = await client.get("/views/challenges/")
    assert res.status_code == 200

    # «ریل» is the default, so it is what the markup ships as -- the stored
    # preference only ever has to correct it to «لیست».
    assert 'class="challenge-list rail"' in res.text
    assert 'data-view="rail"' in res.text
    assert 'data-view="list"' in res.text
    # ...and it is remembered under one key, read before first paint.
    assert "chalesh-challenge-view" in res.text


async def test_a_card_carries_the_readers_extras_on_both_doors(
    client: AsyncClient, described
) -> None:
    """The reader is built from the cards themselves, so a card from the
    fragment must arrive with everything a card from the page has."""
    _, challenge = described

    for path in ("/views/challenges/", "/views/challenges/fragment"):
        res = await client.get(path)
        assert res.status_code == 200, path
        assert 'class="rail-extra"' in res.text, path
        # the collapsible description and the explicit way to the page
        assert challenge.description in res.text, path
        assert "data-rail-toggle" in res.text, path
        # Collapsed and full ship together and the toggle swaps them. A CSS
        # line-clamp cannot express this: it clamps a short description to
        # nothing, so the control appeared to do nothing at all.
        assert 'class="rd-short"' in res.text, path
        assert 'class="rd-full"' in res.text, path
        assert "data-rail-open" in res.text, path


async def test_a_short_description_gets_no_toggle(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The control exists only where there is something behind it -- a
    «بیشتر» that reveals nothing is the bug this rule replaced."""
    owner = await make_user(db, "Brief")
    challenge = await make_challenge(db, owner, title="چالش کوتاه")
    challenge.description = "کوتاه."
    await db.commit()

    # The fragment is the card markup alone -- the page also carries the
    # listener that reads this attribute, which would match either way.
    res = await client.get("/views/challenges/fragment")
    assert res.status_code == 200
    assert challenge.description in res.text
    assert "data-rail-toggle" not in res.text


async def test_the_card_is_still_one_link_to_the_challenge(
    client: AsyncClient, described
) -> None:
    """«لیست» is the card exactly as it was, and the reader's «صفحه چالش»
    is that same anchor rather than a second navigation path."""
    _, challenge = described
    res = await client.get("/views/challenges/")
    assert res.text.count(f'href="/views/challenges/{challenge.id}"') == 1


async def test_the_shared_partial_still_renders_on_the_group_page(
    client: AsyncClient, db: AsyncSession
) -> None:
    """`_challenge_cards.html` is rendered by two Jinja envs with different
    filter sets; a filter added for the reader that only one of them
    registers is a 500 here and a green suite everywhere else."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    await make_challenge(db, owner, group=group, title="چالش گروهی")

    sign_in(client, member)
    res = await client.get(f"/views/groups/{group.id}")
    assert res.status_code == 200, res.text
    assert "چالش گروهی" in res.text
