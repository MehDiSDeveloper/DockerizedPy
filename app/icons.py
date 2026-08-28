"""Names of the inline SVGs in `app/static/js/app.js`, keyed by domain enum.

The icon set itself is client-side (`renderIcons()` swaps every `[data-icon]`
placeholder for the matching SVG), so all the server has to contribute is the
*name*. Doing that here rather than in JS keeps the Farsi `ChallengeCategory`
values -- which are the enum's real values and travel to the browser verbatim
(see CLAUDE.md) -- from being retyped into a second, silently-drifting map.

Every views router builds its own `Jinja2Templates`, so each one that renders a
category or cadence has to call `register_icon_filters(templates.env)`.
"""

from app.models.challenge import CadenceKind, ChallengeCategory

FALLBACK_ICON = "target"

CATEGORY_ICONS: dict[ChallengeCategory, str] = {
    ChallengeCategory.FITNESS: "catFitness",
    ChallengeCategory.NUTRITION: "catNutrition",
    ChallengeCategory.MENTAL_HEALTH: "catMental",
    ChallengeCategory.PRODUCTIVITY: "catProductivity",
    ChallengeCategory.SOCIAL_RESPONSIBILITY: "catSocial",
    ChallengeCategory.OTHER: "catOther",
}

CADENCE_ICONS: dict[CadenceKind, str] = {
    CadenceKind.ONCE: "cadenceOnce",
    CadenceKind.SCHEDULE: "cadenceSchedule",
    CadenceKind.RECURRING_DAYS: "cadenceDays",
    CadenceKind.RECURRING_QUOTA: "cadenceQuota",
}


def category_icon(value: ChallengeCategory | str | None) -> str:
    """Icon name for a category, given either the enum or its raw value.

    Today's feed carries `category` as a plain string (`TodayItem`), while the
    ORM hands back the enum -- both have to resolve to the same icon.
    """
    if value is None:
        return FALLBACK_ICON
    try:
        return CATEGORY_ICONS.get(ChallengeCategory(value), FALLBACK_ICON)
    except ValueError:
        return FALLBACK_ICON


def cadence_icon(value: CadenceKind | str | None) -> str:
    """Icon name for a cadence kind, given either the enum or its raw value."""
    if value is None:
        return FALLBACK_ICON
    try:
        return CADENCE_ICONS.get(CadenceKind(value), FALLBACK_ICON)
    except ValueError:
        return FALLBACK_ICON


def register_icon_filters(env) -> None:
    """Expose the two lookups as Jinja filters on one templates environment."""
    env.filters["category_icon"] = category_icon
    env.filters["cadence_icon"] = cadence_icon
