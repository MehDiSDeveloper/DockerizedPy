"""
generate_test_user.py

Builds one **test account** whose data covers every state the UI can render:
all four cadence kinds, all three enrollment statuses, all three visibilities,
all three lifecycle statuses, all three derived challenge statuses, all six
categories, several timezones, goal/no-goal challenges, and check-in histories
that produce completed / skipped / missed / pending occurrences on purpose.

It is **idempotent and self-contained**: every row it writes belongs to the
accounts listed in `TEST_EMAILS`, and each run deletes that whole set first.
Nothing else in the database is touched.

    python -m app.scripts.generate_test_user

Log in as  test@chalesh.dev  /  test1234
"""

import asyncio
import random
import sys
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.database import AsyncSessionLocal as async_session_maker
from app.models.challenge import (
    Challenge,
    ChallengeCategory,
    LifecycleStatus,
    Visibility,
)
from app.models.checkin import CheckIn
from app.models.enrollment import ChallengeRole, Enrollment, EnrollmentStatus
from app.models.stats import ChallengeStats
from app.models.user import User
from app.occurrences import (
    compute_streaks,
    expected_keys_desc,
    local_today,
    period_bounds,
    period_key,
)
from app.schemas.cadence import (
    CadenceUnion,
    OnceCadence,
    RecurringDaysCadence,
    RecurringQuotaCadence,
    ScheduleCadence,
)

# Deterministic: two runs produce the same fixture, so a screenshot taken
# today still matches the data tomorrow apart from the moving "today".
rng = random.Random(1404)

TEST_EMAIL = "test@chalesh.dev"
TEST_PASSWORD = "test1234"
TEST_NAME = "کاربر تست"

# Peers exist to make participant counts, collective goals and the "locked
# because others joined" guards real. They are part of the fixture and get
# purged with it.
PEERS = [
    ("سارا رضایی", "peer1@chalesh.dev", "cameo-ivy"),
    ("امیر کاظمی", "peer2@chalesh.dev", "bottts-neutral-cato"),
    ("نگین شریفی", "peer3@chalesh.dev", "marbles-iva"),
    ("پویا احمدی", "peer4@chalesh.dev", "clay-dilan"),
    ("مریم حسینی", "peer5@chalesh.dev", "shapes-ilona"),
    # Deliberately no avatar: the "_default" placeholder must be visible
    # somewhere in a freshly built fixture.
    ("بدون آواتار", "peer6@chalesh.dev", None),
]
TEST_EMAILS = [TEST_EMAIL] + [email for _, email, _ in PEERS]

TZ_TEHRAN = "Asia/Tehran"
TZ_BERLIN = "Europe/Berlin"
TZ_NEWYORK = "America/New_York"
TZ_KIRITIMATI = "Pacific/Kiritimati"  # UTC+14, the far edge of "today"

NOTES = [
    "امروز سخت‌تر از همیشه بود ولی انجامش دادم.",
    "صبح زود، قبل از کار.",
    "با دوستام رفتیم، خیلی چسبید.",
    "کمتر از هدف ولی بهتر از هیچی.",
    None,
    None,
    None,
]


# ---------------------------------------------------------------------------
# check-in fill policies
# ---------------------------------------------------------------------------
# Each policy answers, for the i-th occurrence counted back from today
# (0 = the most recent one): "completed", "skipped", or None.
# None leaves no row at all, which the read layer derives as `missed` for a
# closed window and `pending` for one that is still open -- that is how the
# fixture gets pending/missed states without storing them.


def _perfect(i, total):
    return "completed"


def _mostly(i, total):
    if i % 9 == 4:
        return "skipped"
    if i % 13 == 7:
        return None
    return "completed"


def _spotty(i, total):
    if i % 3 == 2:
        return None
    if i % 5 == 1:
        return "skipped"
    return "completed"


def _streak_broken(i, total):
    # The last three windows are empty: today reads as pending, the two before
    # it as missed -> current streak 0, longest streak large.
    return None if i < 3 else "completed"


def _recent_only(i, total):
    return "completed" if i < 5 else None


def _today_open(i, total):
    # Everything done except today, so the Today feed has a card to show.
    return None if i == 0 else "completed"


def _empty(i, total):
    return None


def _sparse(i, total):
    return "completed" if i % 4 == 0 else None


def _open_today_mixed(i, total):
    # Today's window left open (so the Today feed has a card for this
    # cadence) with a skip and a gap behind it, rather than a clean run.
    if i == 0:
        return None
    if i == 2:
        return "skipped"
    if i == 5:
        return None
    return "completed"


def _abandoned(i, total):
    # A run that stopped a fortnight ago and never came back.
    return None if i < 14 else "completed"


POLICIES = {
    "perfect": _perfect,
    "mostly": _mostly,
    "spotty": _spotty,
    "streak_broken": _streak_broken,
    "recent_only": _recent_only,
    "today_open": _today_open,
    "open_today_mixed": _open_today_mixed,
    "today_done": _perfect,
    "empty": _empty,
    "sparse": _sparse,
    "abandoned": _abandoned,
}

# Quota policies answer "how many of `count` were completed" per period,
# newest period first (0 = the period containing today).
QUOTA_POLICIES = {
    # Past periods met the target, the current one is half done -> the Today
    # feed shows "۲ از ۵" and the current period row is partially filled.
    "quota_partial": lambda i, count: count if i else max(1, count // 2),
    "quota_full": lambda i, count: count,
    "quota_thin": lambda i, count: 1,
    "quota_missed": lambda i, count: 0 if i < 2 else count,
}


# ---------------------------------------------------------------------------
# the fixture
# ---------------------------------------------------------------------------


def build_specs(now: datetime, today: date) -> list[dict]:
    """Every challenge in the fixture, spelled out.

    `owner` is "me" or a peer index. `me` is the test user's own enrollment
    (None = not enrolled, which is what makes the discovery/enroll paths
    testable). `peers` are extra participants: (index, policy, days_ago).
    """

    def at(days: int, hour: int = 9, minute: int = 0) -> datetime:
        """A UTC instant `days` from today at a local Tehran wall time."""
        return datetime.combine(
            today + timedelta(days=days), time(hour, minute), tzinfo=ZoneInfo(TZ_TEHRAN)
        ).astimezone(UTC)

    return [
        # -- 1. the flagship: daily, owned by me, busy, with a collective goal
        {
            "key": "daily_flagship",
            "title": "۱۰ هزار قدم هر روز",
            "description": (
                "هر روز حداقل ۱۰ هزار قدم راه برو. پیاده‌روی صبحگاهی، پله به‌جای "
                "آسانسور، یا یک دور کامل پارک — هر کدام که برایت راحت‌تر است."
            ),
            "rules": "ثبت روزانه تا پایان همان روز. مسافت را به کیلومتر وارد کن.",
            "category": ChallengeCategory.FITNESS,
            "cadence": RecurringDaysCadence(
                mode="weekdays", weekdays=[0, 1, 2, 3, 4, 5, 6]
            ),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": 45,
            "goal": (Decimal(400), "کیلومتر"),
            "owner": "me",
            "created_days_ago": 90,
            "me": {
                "policy": "today_open",
                "tz": TZ_TEHRAN,
                "start": 84,
                "status": "active",
            },
            "peers": [
                (0, "mostly", 80),
                (1, "spotty", 60),
                (2, "perfect", 40),
                (3, "sparse", 30),
            ],
        },
        # -- 2. broken streak: long history, last three windows empty
        {
            "key": "rd_broken",
            "title": "روزهای بدون شکر",
            "description": "شنبه، دوشنبه و چهارشنبه بدون هیچ نوشیدنی شیرین.",
            "rules": "قهوه تلخ مجاز است.",
            "category": ChallengeCategory.NUTRITION,
            "cadence": RecurringDaysCadence(mode="weekdays", weekdays=[0, 2, 4]),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": None,
            "goal": None,
            "owner": 0,
            "created_days_ago": 75,
            "me": {
                "policy": "streak_broken",
                "tz": TZ_TEHRAN,
                "start": 70,
                "status": "active",
            },
            "peers": [(1, "perfect", 70), (4, "mostly", 50)],
        },
        # -- 3. every N days + unlisted + a goal measured in minutes
        {
            "key": "rd_every_3_days",
            "title": "مدیتیشن یک روز در میان",
            "description": "هر سه روز یک‌بار، بیست دقیقه سکوت.",
            "rules": "بدون موبایل.",
            "category": ChallengeCategory.MENTAL_HEALTH,
            "cadence": RecurringDaysCadence(mode="every_n_days", n=3),
            "visibility": Visibility.UNLISTED.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": 30,
            "goal": (Decimal(600), "دقیقه"),
            "owner": 1,
            "created_days_ago": 65,
            "me": {
                "policy": "spotty",
                "tz": TZ_TEHRAN,
                "start": 60,
                "status": "active",
            },
            "peers": [(2, "mostly", 55)],
        },
        # -- 4. every N weeks, private, mine, nobody else -> deletable
        {
            "key": "rd_every_2_weeks",
            "title": "بازبینی دو هفته‌ای اهداف",
            "description": "هر دو هفته یک‌بار، مرور اهداف و نوشتن خلاصه.",
            "rules": "فقط برای خودم.",
            "category": ChallengeCategory.PRODUCTIVITY,
            "cadence": RecurringDaysCadence(mode="every_n_weeks", n=2),
            "visibility": Visibility.PRIVATE.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": None,
            "goal": None,
            "owner": "me",
            "created_days_ago": 60,
            "me": {
                "policy": "perfect",
                "tz": TZ_TEHRAN,
                "start": 56,
                "status": "active",
            },
            "peers": [],
        },
        # -- 5. every N (Jalali) months
        {
            "key": "rd_every_month",
            "title": "اهدای خون ماهانه",
            "description": "هر ماه شمسی یک‌بار، در همان روزی که شروع کردی.",
            "rules": "با کارت ملی مراجعه کن.",
            "category": ChallengeCategory.SOCIAL_RESPONSIBILITY,
            "cadence": RecurringDaysCadence(mode="every_n_months", n=1),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": 120,
            "goal": None,
            "owner": 2,
            "created_days_ago": 110,
            "me": {
                "policy": "recent_only",
                "tz": TZ_TEHRAN,
                "start": 100,
                "status": "active",
            },
            "peers": [(0, "perfect", 100), (5, "sparse", 90)],
        },
        # -- 6. schedule: past sessions, one today, and future ones
        {
            "key": "sched_mixed",
            "title": "کلاس‌های آنلاین پایتون",
            "description": "جلسه‌های ضبط‌شده و زنده، طبق تقویم کلاس.",
            "rules": "ورود ۱۰ دقیقه قبل از شروع.",
            "category": ChallengeCategory.PRODUCTIVITY,
            "cadence": ScheduleCadence(
                datetimes=[
                    at(-24, 18),
                    at(-20, 18),
                    at(-16, 18),
                    at(-11, 18),
                    at(-7, 18),
                    at(-3, 18),
                    at(0, 21),
                    at(4, 18),
                    at(9, 18),
                ]
            ),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": 20,
            "goal": None,
            "owner": 0,
            "created_days_ago": 30,
            "me": {
                "policy": "open_today_mixed",
                "tz": TZ_TEHRAN,
                "start": 26,
                "status": "active",
            },
            "peers": [(3, "perfect", 26), (4, "spotty", 20)],
        },
        # -- 7. schedule entirely in the future -> derived status "upcoming"
        {
            "key": "sched_future",
            "title": "اردوی کوهنوردی بهار",
            "description": "سه صعود گروهی که هنوز شروع نشده‌اند.",
            "rules": "کفش مناسب الزامی است.",
            "category": ChallengeCategory.FITNESS,
            "cadence": ScheduleCadence(datetimes=[at(6, 6), at(13, 6), at(21, 6)]),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": 30,
            "goal": None,
            "owner": "me",
            "created_days_ago": 8,
            "me": {"policy": "empty", "tz": TZ_TEHRAN, "start": 8, "status": "active"},
            "peers": [(1, "empty", 6), (5, "empty", 4)],
        },
        # -- 8. weekly quota, half done this week -> Today shows the counter
        {
            "key": "quota_week",
            "title": "پنج بار ورزش در هفته",
            "description": "هفته‌ای پنج نوبت، هر روزی که خواستی.",
            "rules": "هر نوبت حداقل ۳۰ دقیقه.",
            "category": ChallengeCategory.FITNESS,
            "cadence": RecurringQuotaCadence(period="week", count=5),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": 60,
            "goal": (Decimal(120), "جلسه"),
            "owner": "me",
            "created_days_ago": 70,
            "me": {
                "policy": "quota_partial",
                "tz": TZ_TEHRAN,
                "start": 63,
                "status": "active",
            },
            "peers": [
                (2, "quota_full", 63),
                (3, "quota_thin", 50),
                (5, "quota_missed", 35),
            ],
        },
        # -- 9. weekly quota already met this week -> absent from Today
        {
            "key": "quota_week_full",
            "title": "سه نشست کتاب‌خوانی در هفته",
            "description": "هفته‌ای سه نشست کتاب‌خوانی.",
            "rules": "حداقل ۲۰ صفحه در هر نشست.",
            "category": ChallengeCategory.MENTAL_HEALTH,
            "cadence": RecurringQuotaCadence(period="week", count=3),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": None,
            "goal": (Decimal(900), "صفحه"),
            "owner": 1,
            "created_days_ago": 50,
            "me": {
                "policy": "quota_full",
                "tz": TZ_TEHRAN,
                "start": 49,
                "status": "active",
            },
            "peers": [(0, "quota_thin", 45)],
        },
        # -- 10. monthly (Jalali) quota
        {
            "key": "quota_month",
            "title": "دوازده پیاده‌روی در ماه",
            "description": "ماهی دوازده نوبت پیاده‌روی طولانی.",
            "rules": "هر نوبت حداقل ۵ کیلومتر.",
            "category": ChallengeCategory.NUTRITION,
            "cadence": RecurringQuotaCadence(period="month", count=12),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": 90,
            "goal": None,
            "owner": 2,
            "created_days_ago": 100,
            "me": {
                "policy": "quota_partial",
                "tz": TZ_TEHRAN,
                "start": 95,
                "status": "active",
            },
            "peers": [(4, "quota_full", 95), (5, "quota_missed", 70)],
        },
        # -- 11. one-off, never recorded -> permanently due in Today
        {
            "key": "once_open",
            "title": "ثبت‌نام در دوره کمک‌های اولیه",
            "description": "یک کار، یک بار. تا وقتی ثبتش نکنی باز می‌ماند.",
            "rules": "مدرک را نگه دار.",
            "category": ChallengeCategory.OTHER,
            "cadence": OnceCadence(),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": 25,
            "goal": None,
            "owner": 3,
            "created_days_ago": 20,
            "me": {"policy": "empty", "tz": TZ_TEHRAN, "start": 18, "status": "active"},
            "peers": [(0, "perfect", 18), (1, "empty", 12)],
        },
        # -- 12. one-off, already recorded -> gone from Today, done on detail
        {
            "key": "once_done",
            "title": "کاشت یک درخت",
            "description": "یک بار انجامش بده و محلش را ثبت کن.",
            "rules": "محل کاشت را در یادداشت بنویس.",
            "category": ChallengeCategory.SOCIAL_RESPONSIBILITY,
            "cadence": OnceCadence(),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": None,
            "goal": None,
            "owner": "me",
            "created_days_ago": 40,
            "me": {
                "policy": "perfect",
                "tz": TZ_TEHRAN,
                "start": 35,
                "status": "active",
            },
            "peers": [(2, "perfect", 30), (4, "empty", 22)],
        },
        # -- 13. my enrollment is "completed", the challenge itself is finished
        {
            "key": "finished_completed",
            "title": "چالش ۳۰ روزه شنا",
            "description": "یک ماه شنا، تمام شده.",
            "rules": "مهلت این چالش گذشته است.",
            "category": ChallengeCategory.FITNESS,
            "cadence": RecurringDaysCadence(
                mode="weekdays",
                weekdays=[0, 2, 4],
                end_date=datetime.combine(
                    today - timedelta(days=6), time(20, 30), tzinfo=UTC
                ),
            ),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": -6,
            "goal": (Decimal(60), "جلسه"),
            "owner": 0,
            "created_days_ago": 60,
            "me": {
                "policy": "perfect",
                "tz": TZ_TEHRAN,
                "start": 52,
                "status": "completed",
            },
            "peers": [(1, "mostly", 52), (3, "spotty", 45)],
        },
        # -- 14. my enrollment is "abandoned"
        {
            "key": "abandoned_enrollment",
            "title": "نوشتن روزانه خاطرات",
            "description": "هر روز چند خط بنویس.",
            "rules": "حداقل ۱۰۰ کلمه.",
            "category": ChallengeCategory.MENTAL_HEALTH,
            "cadence": RecurringDaysCadence(mode="every_n_days", n=1),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": None,
            "goal": None,
            "owner": 1,
            "created_days_ago": 60,
            "me": {
                "policy": "abandoned",
                "tz": TZ_TEHRAN,
                "start": 55,
                "status": "abandoned",
            },
            "peers": [(5, "perfect", 55)],
        },
        # -- 15/16/17. the same "today" judged in three different timezones
        {
            "key": "tz_berlin",
            "title": "یوگای صبحگاهی (برلین)",
            "description": "ثبت‌نام من در منطقه زمانی اروپا/برلین است.",
            "rules": "پیش از ساعت ۹ صبح محلی.",
            "category": ChallengeCategory.MENTAL_HEALTH,
            "cadence": RecurringDaysCadence(
                mode="weekdays", weekdays=[0, 1, 2, 3, 4, 5, 6]
            ),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": 40,
            "goal": None,
            "owner": 2,
            "created_days_ago": 40,
            "me": {
                "policy": "today_open",
                "tz": TZ_BERLIN,
                "start": 35,
                "status": "active",
            },
            "peers": [(0, "mostly", 35)],
        },
        {
            "key": "tz_newyork",
            "title": "چهار جلسه تمرکز در هفته (نیویورک)",
            "description": "سهمیه هفتگی با ساعت آمریکا/نیویورک.",
            "rules": "هر جلسه ۵۰ دقیقه.",
            "category": ChallengeCategory.PRODUCTIVITY,
            "cadence": RecurringQuotaCadence(period="week", count=4),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": None,
            "goal": (Decimal(80), "جلسه"),
            "owner": 3,
            "created_days_ago": 45,
            "me": {
                "policy": "quota_partial",
                "tz": TZ_NEWYORK,
                "start": 42,
                "status": "active",
            },
            "peers": [(4, "quota_full", 42)],
        },
        {
            "key": "tz_kiritimati",
            "title": "آب خوردن روزانه (کیریتیماتی)",
            "description": "لبه دورترین «امروز» ممکن، UTC+14.",
            "rules": "هشت لیوان در روز.",
            "category": ChallengeCategory.OTHER,
            "cadence": RecurringDaysCadence(
                mode="weekdays", weekdays=[0, 1, 2, 3, 4, 5, 6]
            ),
            "visibility": Visibility.PRIVATE.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": None,
            "goal": (Decimal(500), "لیوان آب"),
            "owner": "me",
            "created_days_ago": 30,
            "me": {
                "policy": "today_done",
                "tz": TZ_KIRITIMATI,
                "start": 25,
                "status": "active",
            },
            "peers": [],
        },
        # -- 18. enrolled but starting later: nothing due, no history yet
        {
            "key": "future_start",
            "title": "دویدن نیمه‌ماراتن (از هفته آینده)",
            "description": "ثبت‌نام کرده‌ای ولی تاریخ شروعت هنوز نرسیده.",
            "rules": "برنامه از روز شروع فعال می‌شود.",
            "category": ChallengeCategory.FITNESS,
            "cadence": RecurringDaysCadence(mode="weekdays", weekdays=[0, 2, 4]),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": 70,
            "goal": None,
            "owner": 0,
            "created_days_ago": 5,
            "me": {"policy": "empty", "tz": TZ_TEHRAN, "start": -3, "status": "active"},
            "peers": [(2, "perfect", 5)],
        },
        # -- 19. draft, mine, private -> derived status "upcoming"
        {
            "key": "draft_mine",
            "title": "پیش‌نویس: چالش زمستانی",
            "description": "هنوز منتشر نشده. فقط خودم می‌بینمش.",
            "rules": "در حال نوشتن قوانین.",
            "category": ChallengeCategory.OTHER,
            "cadence": RecurringDaysCadence(mode="weekdays", weekdays=[1, 3]),
            "visibility": Visibility.PRIVATE.value,
            "lifecycle": LifecycleStatus.DRAFT.value,
            "due_days": None,
            "goal": None,
            "owner": "me",
            "created_days_ago": 2,
            "me": {"policy": "empty", "tz": TZ_TEHRAN, "start": 2, "status": "active"},
            "peers": [],
        },
        # -- 20. archived, mine, with a real history behind it
        {
            "key": "archived_mine",
            "title": "چالش تابستانی دوچرخه‌سواری",
            "description": "تمام شد و بایگانی‌اش کردم.",
            "rules": "بایگانی‌شده.",
            "category": ChallengeCategory.FITNESS,
            "cadence": RecurringDaysCadence(mode="weekdays", weekdays=[1, 3, 5]),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ARCHIVED.value,
            "due_days": -10,
            "goal": (Decimal(250), "کیلومتر"),
            "owner": "me",
            "created_days_ago": 80,
            "me": {
                "policy": "mostly",
                "tz": TZ_TEHRAN,
                "start": 75,
                "status": "completed",
            },
            "peers": [(1, "perfect", 75), (4, "sparse", 60)],
        },
        # -- 21. finished by a past due_date, and I am NOT enrolled
        {
            "key": "due_past_not_mine",
            "title": "پاک‌سازی ساحل (پایان‌یافته)",
            "description": "مهلتش گذشته؛ در فهرست باید «تمام شده» باشد.",
            "rules": "دستکش و کیسه بیاور.",
            "category": ChallengeCategory.SOCIAL_RESPONSIBILITY,
            "cadence": RecurringDaysCadence(mode="weekdays", weekdays=[5, 6]),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": -4,
            "goal": None,
            "owner": 0,
            "created_days_ago": 50,
            "me": None,
            "peers": [(1, "mostly", 45), (2, "perfect", 45)],
        },
        # -- 22. public, active, not enrolled -> the enroll button's target
        {
            "key": "joinable_plain",
            "title": "زودتر از خورشید بیدار شو",
            "description": "هر روز پیش از ۶ صبح بیدار شو. هنوز عضو نشده‌ای.",
            "rules": "ساعت بیداری را ثبت کن.",
            "category": ChallengeCategory.PRODUCTIVITY,
            "cadence": RecurringDaysCadence(mode="weekdays", weekdays=[0, 1, 2, 3, 4]),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": 50,
            "goal": None,
            "owner": 3,
            "created_days_ago": 25,
            "me": None,
            "peers": [(0, "perfect", 24), (4, "mostly", 20), (5, "spotty", 15)],
        },
        # -- 23. public with a collective goal, not enrolled: the progress bar
        #        has to render for a non-participant too
        {
            "key": "joinable_goal",
            "title": "هزار کیلومتر جمعی",
            "description": "مجموع مسافت همه شرکت‌کننده‌ها. عضو نیستی ولی پیشرفت را می‌بینی.",
            "rules": "مسافت هر نوبت را وارد کن.",
            "category": ChallengeCategory.FITNESS,
            "cadence": RecurringQuotaCadence(period="week", count=4),
            "visibility": Visibility.PUBLIC.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": 80,
            "goal": (Decimal(1000), "کیلومتر"),
            "owner": 4,
            "created_days_ago": 55,
            "me": None,
            "peers": [
                (0, "quota_full", 52),
                (1, "quota_partial", 52),
                (5, "quota_thin", 40),
            ],
        },
        # -- 24. unlisted and not mine: reachable by URL, absent from listings
        {
            "key": "unlisted_not_mine",
            "title": "چالش لینک‌دار (فهرست‌نشده)",
            "description": "فقط با لینک مستقیم پیدا می‌شود.",
            "rules": "لینک را دست‌به‌دست کن.",
            "category": ChallengeCategory.OTHER,
            "cadence": RecurringDaysCadence(mode="every_n_days", n=2),
            "visibility": Visibility.UNLISTED.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": None,
            "goal": None,
            "owner": 1,
            "created_days_ago": 18,
            "me": None,
            "peers": [(3, "mostly", 17)],
        },
        # -- 25. private and not mine: must stay invisible, the negative case
        {
            "key": "private_not_mine",
            "title": "چالش خصوصی دیگران",
            "description": "نباید برای کاربر تست قابل دیدن باشد.",
            "rules": "خصوصی.",
            "category": ChallengeCategory.NUTRITION,
            "cadence": RecurringDaysCadence(mode="weekdays", weekdays=[2]),
            "visibility": Visibility.PRIVATE.value,
            "lifecycle": LifecycleStatus.ACTIVE.value,
            "due_days": None,
            "goal": None,
            "owner": 2,
            "created_days_ago": 15,
            "me": None,
            "peers": [],
        },
    ]


# ---------------------------------------------------------------------------
# check-in generation
# ---------------------------------------------------------------------------

# A short stretch where the test user recorded nothing at all, while the other
# participants kept going. Without it every single day of the 12-week activity
# grid is filled, so the "no activity" cell never renders -- and the current
# streak always equals the longest one, which hides the difference between the
# two tiles. The peers are deliberately unaffected, so challenge-level totals
# stay busy across the gap.
AWAY_START_DAYS_AGO = 27
AWAY_DAYS = 4

# Where each collective goal should land once the history exists, as a
# fraction of the amount actually logged. The `goal_amount` in the spec is
# only a unit-bearing placeholder: how much a random walk of check-ins adds
# up to is not knowable in advance, and guessing it left every progress bar
# pinned at 100%. These are re-derived after the check-ins are written, so
# the bars span "barely started" to "overshot" on purpose.
GOAL_FILL = {
    "daily_flagship": 0.68,
    "rd_every_3_days": 0.82,
    "quota_week": 0.45,
    "quota_week_full": 1.24,  # target already passed
    "tz_kiritimati": 0.30,
    "tz_newyork": 0.55,
    "finished_completed": 1.00,  # exactly met, and finished
    "archived_mine": 0.93,
    "joinable_goal": 0.12,  # barely started
}

MAX_HISTORY = 200  # occurrences walked back per enrollment
MAX_PERIODS = 20  # quota periods walked back per enrollment


def _key_local_date(key: str, tz: str, fallback: date) -> date:
    if key.startswith("D"):
        return date.fromisoformat(key[1:])
    if key.startswith("S"):
        return datetime.fromisoformat(key[1:]).astimezone(ZoneInfo(tz)).date()
    return fallback  # "single"


def _occurred_at(local_date: date, tz: str, now: datetime, hour: int) -> datetime:
    """A plausible UTC instant inside `local_date`, never in the future.

    The clamp matters for the UTC+14 enrollment: 20:00 local today is still
    ahead of `now`, and a check-in stamped in the future makes the activity
    grid and the velocity sort disagree with the clock.
    """
    stamp = datetime.combine(
        local_date, time(hour, rng.randrange(0, 60)), tzinfo=ZoneInfo(tz)
    ).astimezone(UTC)
    return min(stamp, now)


def _make_checkin(
    *,
    enrollment: Enrollment,
    challenge: Challenge,
    key: str,
    local_date: date,
    state: str,
    now: datetime,
    at_utc: datetime | None = None,
) -> CheckIn:
    tz = enrollment.timezone
    occurred = at_utc or _occurred_at(local_date, tz, now, rng.randrange(7, 22))
    amount = None
    unit = None
    if challenge.goal_unit and state == "completed":
        unit = challenge.goal_unit
        amount = Decimal(str(round(rng.uniform(1.0, 9.0), 2)))
    return CheckIn(
        enrollment_id=enrollment.id,
        challenge_id=challenge.id,
        occurrence_key=key,
        state=state,
        occurrence_local_date=local_date,
        occurred_at_utc=occurred,
        timezone=tz,
        amount=amount,
        unit=unit,
        note=rng.choice(NOTES),
        # `created_at` drives Discover's 7-day velocity sort, so it has to
        # track the occurrence rather than the moment the fixture was built.
        created_at=occurred,
    )


def build_history(
    session: AsyncSession,
    *,
    enrollment: Enrollment,
    challenge: Challenge,
    cadence: CadenceUnion,
    policy: str,
    now: datetime,
    away: bool = False,
) -> list[CheckIn]:
    """Write one enrollment's whole check-in history and its streaks."""
    tz = enrollment.timezone
    today = local_today(tz, now)
    start = enrollment.start_date
    rows: list[CheckIn] = []
    completed_keys: set[str] = set()
    period_counts: dict[str, int] = {}

    if isinstance(cadence, RecurringQuotaCadence):
        quota_of = QUOTA_POLICIES.get(policy, QUOTA_POLICIES["quota_partial"])
        # Walk periods back from today rather than reading expected_keys_desc,
        # because each period's real bounds are what the rows are dated inside.
        periods: list[tuple[str, date, date]] = []
        cursor = today
        while len(periods) < MAX_PERIODS:
            p_start, p_end = period_bounds(cursor, cadence.period)
            if p_end < start:
                break
            periods.append((period_key(cursor, cadence.period), p_start, p_end))
            cursor = p_start - timedelta(days=1)

        for i, (pkey, p_start, p_end) in enumerate(periods):
            first = max(p_start, start)
            last = min(p_end, today)
            if last < first:
                continue
            span = (last - first).days
            done = min(cadence.count, max(0, quota_of(i, cadence.count)))
            for seq in range(1, done + 1):
                d = first + timedelta(
                    days=min(span, (seq - 1) * max(1, span // max(1, done)))
                )
                key = f"{pkey}#{seq}"
                rows.append(
                    _make_checkin(
                        enrollment=enrollment,
                        challenge=challenge,
                        key=key,
                        local_date=d,
                        state="completed",
                        now=now,
                    )
                )
            period_counts[pkey] = done
            # One skipped slot every few periods, so the quota history shows
            # more than completed/empty.
            if done < cadence.count and i % 3 == 1:
                rows.append(
                    _make_checkin(
                        enrollment=enrollment,
                        challenge=challenge,
                        key=f"{pkey}#{done + 1}",
                        local_date=last,
                        state="skipped",
                        now=now,
                    )
                )
    else:
        decide = POLICIES.get(policy, POLICIES["mostly"])
        keys = expected_keys_desc(
            cadence, start_date=start, tz=tz, until=today, limit=MAX_HISTORY
        )
        for i, key in enumerate(keys):  # i == 0 is the most recent occurrence
            state = decide(i, len(keys))
            if state is None:
                continue  # no row -> derived as missed (or pending, if open)
            local_date = _key_local_date(key, tz, start)
            at_utc = None
            if key.startswith("S"):
                # A scheduled session is recorded around the session itself.
                at_utc = min(
                    datetime.fromisoformat(key[1:])
                    + timedelta(minutes=rng.randrange(5, 90)),
                    now,
                )
            rows.append(
                _make_checkin(
                    enrollment=enrollment,
                    challenge=challenge,
                    key=key,
                    local_date=local_date,
                    state=state,
                    now=now,
                    at_utc=at_utc,
                )
            )
            if state == "completed":
                completed_keys.add(key)

    if away:
        # Drop the away window and re-derive the tallies from what survived,
        # so the streaks the enrollment stores match the rows that exist.
        away_end = today - timedelta(days=AWAY_START_DAYS_AGO)
        away_start = away_end - timedelta(days=AWAY_DAYS - 1)
        rows = [
            r for r in rows if not (away_start <= r.occurrence_local_date <= away_end)
        ]
        completed_keys = {r.occurrence_key for r in rows if r.state == "completed"}
        period_counts = {}
        for r in rows:
            if r.state == "completed" and "#" in r.occurrence_key:
                pkey = r.occurrence_key.split("#", 1)[0]
                period_counts[pkey] = period_counts.get(pkey, 0) + 1

    for row in rows:
        session.add(row)

    enrollment.current_streak, enrollment.longest_streak = compute_streaks(
        cadence,
        start_date=start,
        tz=tz,
        now_utc=now,
        completed_keys=completed_keys,
        period_completed_counts=period_counts,
    )
    completed_dates = [r.occurrence_local_date for r in rows if r.state == "completed"]
    enrollment.last_checkin_local_date = (
        max(completed_dates) if completed_dates else None
    )
    return rows


# ---------------------------------------------------------------------------
# purge + build
# ---------------------------------------------------------------------------


async def purge(session: AsyncSession) -> tuple[int, int]:
    """Remove everything a previous run of this script created.

    Scoped strictly to the fixture accounts: their challenges, every
    enrollment in those challenges, and every enrollment they hold elsewhere.
    Rows belonging to other users are never touched.
    """
    user_ids = list(
        (await session.execute(select(User.id).where(User.email.in_(TEST_EMAILS))))
        .scalars()
        .all()
    )
    if not user_ids:
        return 0, 0

    challenge_ids = list(
        (
            await session.execute(
                select(Challenge.id).where(Challenge.owner_id.in_(user_ids))
            )
        )
        .scalars()
        .all()
    )
    enrollment_ids = list(
        (
            await session.execute(
                select(Enrollment.id).where(
                    (Enrollment.user_id.in_(user_ids))
                    | (Enrollment.challenge_id.in_(challenge_ids or [-1]))
                )
            )
        )
        .scalars()
        .all()
    )

    if enrollment_ids:
        await session.execute(
            delete(CheckIn).where(CheckIn.enrollment_id.in_(enrollment_ids))
        )
        await session.execute(
            delete(Enrollment).where(Enrollment.id.in_(enrollment_ids))
        )
    if challenge_ids:
        await session.execute(
            delete(CheckIn).where(CheckIn.challenge_id.in_(challenge_ids))
        )
        await session.execute(
            delete(ChallengeStats).where(ChallengeStats.challenge_id.in_(challenge_ids))
        )
        await session.execute(delete(Challenge).where(Challenge.id.in_(challenge_ids)))
    await session.execute(delete(User).where(User.id.in_(user_ids)))
    await session.flush()
    return len(user_ids), len(challenge_ids)


async def build(session: AsyncSession, now: datetime) -> list[tuple[dict, Challenge]]:
    today = local_today(TZ_TEHRAN, now)

    me = User(
        name=TEST_NAME,
        email=TEST_EMAIL,
        password_hash=hash_password(TEST_PASSWORD),
        avatar="glyphs-dana",
        created_at=now - timedelta(days=120),
    )
    session.add(me)
    peers = []
    for name, email, avatar in PEERS:
        peer = User(
            name=name,
            email=email,
            password_hash=hash_password(TEST_PASSWORD),
            avatar=avatar,
            created_at=now - timedelta(days=110),
        )
        session.add(peer)
        peers.append(peer)
    await session.flush()

    built: list[tuple[dict, Challenge]] = []
    for spec in build_specs(now, today):
        cadence: CadenceUnion = spec["cadence"]
        owner = me if spec["owner"] == "me" else peers[spec["owner"]]
        goal_amount, goal_unit = spec["goal"] or (None, None)
        challenge = Challenge(
            title=spec["title"][:50],
            description=spec["description"],
            rules=spec["rules"],
            due_date=(
                now + timedelta(days=spec["due_days"])
                if spec["due_days"] is not None
                else None
            ),
            owner_id=owner.id,
            category=spec["category"],
            cadence_kind=cadence.kind,
            cadence=cadence.model_dump(mode="json"),
            visibility=spec["visibility"],
            lifecycle_status=spec["lifecycle"],
            goal_amount=goal_amount,
            goal_unit=goal_unit,
            created_at=now - timedelta(days=spec["created_days_ago"]),
        )
        session.add(challenge)
        built.append((spec, challenge))
    await session.flush()

    for spec, challenge in built:
        cadence = spec["cadence"]
        # (user, policy, days_ago, enrollment status). D1: the owner is always
        # enrolled in their own challenge, so add them if the spec didn't.
        roster: list[tuple[User, str, int, str, str]] = []
        if spec["me"]:
            m = spec["me"]
            roster.append((me, m["policy"], m["start"], m["status"], m["tz"]))
        listed_peers = {idx for idx, _, _ in spec["peers"]}
        if spec["owner"] != "me" and spec["owner"] not in listed_peers:
            roster.append(
                (
                    peers[spec["owner"]],
                    "quota_partial"
                    if isinstance(cadence, RecurringQuotaCadence)
                    else "mostly",
                    spec["created_days_ago"],
                    EnrollmentStatus.ACTIVE.value,
                    TZ_TEHRAN,
                )
            )
        for idx, policy, days_ago in spec["peers"]:
            roster.append(
                (peers[idx], policy, days_ago, EnrollmentStatus.ACTIVE.value, TZ_TEHRAN)
            )

        totals = {
            "participants": 0,
            "completions": 0,
            "amount": Decimal(0),
            "last": None,
        }
        for user, policy, days_ago, status, tz in roster:
            enrollment = Enrollment(
                user_id=user.id,
                challenge_id=challenge.id,
                status=status,
                timezone=tz,
                start_date=local_today(tz, now) - timedelta(days=days_ago),
                created_at=now - timedelta(days=max(days_ago, 0)),
                role=(
                    ChallengeRole.OWNER.value
                    if user.id == challenge.owner_id
                    else ChallengeRole.PARTICIPANT.value
                ),
            )
            session.add(enrollment)
            await session.flush()
            rows = build_history(
                session,
                enrollment=enrollment,
                challenge=challenge,
                cadence=cadence,
                policy=policy,
                now=now,
                away=user is me,
            )
            totals["participants"] += 1
            for row in rows:
                if row.state != "completed":
                    continue
                totals["completions"] += 1
                if row.amount:
                    totals["amount"] += row.amount
                if totals["last"] is None or row.occurred_at_utc > totals["last"]:
                    totals["last"] = row.occurred_at_utc

        fill = GOAL_FILL.get(spec["key"])
        if fill and totals["amount"] > 0:
            challenge.goal_amount = Decimal(round(float(totals["amount"]) / fill))

        session.add(
            ChallengeStats(
                challenge_id=challenge.id,
                participant_count=totals["participants"],
                total_completions=totals["completions"],
                total_amount=totals["amount"],
                last_checkin_at=totals["last"],
            )
        )

    return built


async def main():
    # The fixture prints Farsi titles; a cp1252 console would raise on them
    # *after* the commit, which reads as a failed run that actually succeeded.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError, OSError:
        pass
    async with async_session_maker() as session:
        now = datetime.now(UTC)
        removed_users, removed_challenges = await purge(session)
        if removed_users:
            print(
                f"پاک‌سازی اجرای قبلی: {removed_users} کاربر، "
                f"{removed_challenges} چالش حذف شد."
            )
        built = await build(session, now)
        await session.commit()

        checkins = (
            (
                await session.execute(
                    select(CheckIn.id).where(
                        CheckIn.challenge_id.in_([c.id for _, c in built])
                    )
                )
            )
            .scalars()
            .all()
        )

        print()
        print("=" * 72)
        print(f"  کاربر تست ساخته شد:  {TEST_EMAIL}  /  {TEST_PASSWORD}")
        print("=" * 72)
        print(f"  {len(PEERS) + 1} کاربر، {len(built)} چالش، {len(checkins)} ثبت نوبت")
        print()
        print(
            f"  {'id':>4}  {'key':<22} {'cadence':<16} {'enrolled':<10} what to look at"
        )
        print("  " + "-" * 88)
        for spec, challenge in built:
            enrolled = spec["me"]["status"] if spec["me"] else "—"
            print(
                f"  {challenge.id:>4}  {spec['key']:<22} "
                f"{spec['cadence'].kind:<16} {enrolled:<10} {spec['title']}"
            )
        print()
        print("  /views/today/   امروز: وعده های باز (روزانه، سهمیه‌ای، جلسه‌ای، یک‌باره)")
        print("  /views/home/    خانه: نقشه ۱۲ هفته‌ای، زنجیره ها، تفکیک دسته‌بندی")
        print("  /views/challenges/  کاوش: وضعیت‌ها، دسته‌بندی‌ها، جست‌وجو، فیلترها")
        print()


if __name__ == "__main__":
    asyncio.run(main())
