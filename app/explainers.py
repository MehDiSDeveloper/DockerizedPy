"""«راهنمای درجا» — the app's glossary, and the one place a term becomes text.

The screens in this app are dense with *derived* figures: a ring that is a
share of something, a streak that is recomputed rather than counted, a status
nobody stored, a grid whose columns are weeks and whose rows are weekdays. A
member cannot infer any of that from the number itself, and the honest fix is
not a paragraph under every heading — a page explained line by line is a page
nobody reads twice.

So an explanation is **on demand and in place**: a small «؟» beside the thing
it explains, opening a popover with two or three sentences and, where the
thing has named states, one line per state. Nothing is added to the page's
resting layout beyond a 16px dot, and the explanation sits next to what it
describes rather than in a help section somebody has to go and find.

Three rules hold it together:

- **One registry, not a phrase per template.** The same term is explained on
  several screens — «رشته» on the home dashboard and on challenge-detail,
  «وضعیت» on the public list and in the moderation roster — and two copies of
  an explanation drift the moment one is edited. This is the same call
  ``NOTIFICATION_META`` makes, and deliberately not the per-surface pattern
  (D7) the short Farsi *labels* follow: a label names a thing the reader is
  already looking at, an explanation makes a promise about how the app
  behaves, and a promise may not have two versions.
- **The text ships with the markup.** ``explain()`` renders the whole
  explanation into data attributes on the button, so there is no fetch, no
  loading state, nothing to fail offline, and a dot inside an infinite-scroll
  fragment works the moment it is appended. That is also why there is no JSON
  endpoint for this: nothing would call it.
- **A missing key is a render error, not a silent blank.** ``explain()``
  raises ``KeyError`` on an unknown key, so a typo fails loudly the first time
  the page is opened instead of shipping a dot that explains nothing.

Every ``Jinja2Templates`` environment has to call
:func:`register_explainer_filters`, the same registration contract
``register_icon_filters`` has — and because this one is chrome that appears on
nearly every screen, ``tests/test_explainers.py`` walks every view router's
environment and fails if one of them forgot.
"""

from dataclasses import dataclass, field

from markupsafe import Markup, escape


@dataclass(frozen=True)
class Explainer:
    """One glossary entry.

    ``points`` is for a term whose meaning *is* a small set of named states
    (the three challenge statuses, the four states a نوبت can be in). Prose
    listing them reads as a wall; one line each reads as a key. Each point is
    ``"<name> — <what it means>"`` and the renderer splits on the dash so the
    name can be emphasised without a second field to keep in step.
    """

    title: str
    body: str
    points: tuple[str, ...] = field(default_factory=tuple)


#: The glossary. Keys are stable slugs — they are what a template names, so
#: rewording an entry must never mean editing the templates that point at it.
EXPLAINERS: dict[str, Explainer] = {
    # --- امروز / ثبت نوبت ---------------------------------------------------
    "today-list": Explainer(
        title="نوبت‌های امروز",
        body=(
            "هر نوبتی که چالش‌های تو امروز ازت خواستن، یک‌جا. فهرست بر اساس "
            "منطقهٔ زمانی خودِ هر عضویت ساخته می‌شود، نه ساعت گوشی؛ پس «امروز» "
            "همان روزی است که موقع پیوستن به چالش انتخاب شده."
        ),
    ),
    "checkin-states": Explainer(
        title="حالت هر نوبت",
        body="هر نوبت یکی از این چهار حالت را دارد؛ فقط دوتای اول را خودت ثبت می‌کنی.",
        points=(
            "تکمیل‌شده — ثبتش کردی.",
            "رد شده — خودت اعلام کردی که این نوبت را انجام ندادی.",
            "در انتظار — هنوز باز است و می‌توانی ثبتش کنی.",
            "ازدست‌رفته — مهلتش تمام شد و چیزی ثبت نشد.",
        ),
    ),
    "backfill": Explainer(
        title="مهلت ثبت با تأخیر",
        body=(
            "تا دو روز بعد از بسته شدن یک نوبت هنوز می‌توانی ثبتش کنی یا ثبت "
            "اشتباه را پاک کنی. بعد از آن قفل می‌شود، تا تاریخچه‌ای که بقیه "
            "می‌بینند با گذشت زمان بازنویسی نشود."
        ),
    ),
    "streak": Explainer(
        title="رشتهٔ پیوسته",
        body=(
            "تعداد نوبت‌های پشت‌سرهمی که ثبت کرده‌ای. با یک نوبتِ از دست رفته از "
            "نو شروع می‌شود، و هر بار از روی تاریخچهٔ واقعی‌ات دوباره حساب می‌شود "
            "— پس اگر نوبتی را با تأخیر ثبت کنی، رشته هم اصلاح می‌شود."
        ),
    ),
    # --- خانه ---------------------------------------------------------------
    "home-pulse": Explainer(
        title="نبض امروز",
        body=(
            "حلقه می‌گوید از تمام نوبت‌هایی که امروز داشتی، چند تا ثبت شده. "
            "نیمه‌شب به وقت خودت از نو پر می‌شود و کارِ ثبت‌نشده به فردا منتقل "
            "نمی‌شود."
        ),
    ),
    "home-grid": Explainer(
        title="روند فعالیت",
        body=(
            "هر ستون یک هفته است (از شنبه) و هر ردیف یک روز هفته. پس یک ردیفِ "
            "کم‌رنگ یعنی همیشه همان روز هفته از دستت در می‌رود — چیزی که در یک "
            "فهرست ساده معلوم نمی‌شود. خانهٔ پررنگ‌تر یعنی ثبت بیشتر در آن روز."
        ),
    ),
    "home-focus": Explainer(
        title="تمرکز تو",
        body=(
            "ثبت‌های سی روز اخیرت به تفکیک دستهٔ چالش. طول هر میله نسبت به "
            "پرکارترین دسته است و عدد کنارش تعداد واقعی."
        ),
    ),
    # --- چالش ---------------------------------------------------------------
    "challenge-status": Explainer(
        title="وضعیت چالش",
        body="وضعیت از روی تاریخ‌ها و برنامهٔ چالش حساب می‌شود، نه دستی.",
        points=(
            "شروع نشده — هنوز به تاریخ اولین نوبتش نرسیده.",
            "در حال اجرا — همین حالا از اعضا نوبت می‌خواهد.",
            "تمام شده — مهلتش گذشته یا سازنده بایگانی‌اش کرده.",
        ),
    ),
    "challenge-visibility": Explainer(
        title="چه کسی این چالش را می‌بیند",
        body="دسترسیِ دیدنِ چالش سه حالت دارد و سازنده هر وقت بخواهد عوضش می‌کند.",
        points=(
            "عمومی — در فهرست چالش‌ها پیدا می‌شود و هر کسی می‌تواند بپیوندد.",
            "فقط با لینک — در فهرست و جستجو نمی‌آید؛ تنها با لینک مستقیم باز می‌شود.",
            "خصوصی — فقط سازنده و کسانی که عضو شده‌اند می‌بینندش.",
        ),
    ),
    "cadence-plan": Explainer(
        title="برنامهٔ چالش",
        body=(
            "این‌که چالش چه موقع از تو نوبت می‌خواهد: یک بار، در تاریخ‌های مشخص، "
            "در روزهای معینی از هفته، یا سهمیه‌ای در هر هفته/ماه. تاریخ‌ها به "
            "تقویم شمسی و به وقت عضویت خودت نشان داده می‌شوند."
        ),
    ),
    "collective-progress": Explainer(
        title="پیشرفت جمعی",
        body=(
            "مجموع کاری که همهٔ اعضا در این چالش ثبت کرده‌اند — نه سهم شخصی تو. "
            "سهم خودت را در تب «من» می‌بینی."
        ),
    ),
    "locked-fields": Explainer(
        title="چرا بعضی تنظیم‌ها قفل می‌شوند",
        body=(
            "به‌محض این‌که کسی جز خودت به چالش بپیوندد، زمان‌بندی، هدف و نحوهٔ "
            "نمایش نام قفل می‌شوند. اینها شرطی است که بقیه با همان پیوسته‌اند و "
            "عوض‌کردنش تاریخچهٔ ثبت‌شدهٔ آنها را بی‌معنی می‌کند. عنوان و توضیح "
            "همچنان قابل ویرایش‌اند، و راهِ پایان دادن به چالش «بایگانی» است."
        ),
    ),
    "anonymity": Explainer(
        title="نمایش ناشناس",
        body=(
            "با «ناشناس» نام و تصویرت برای بقیهٔ اعضا پنهان می‌شود، ولی امتیاز و "
            "ثبت‌هایت سر جایشان می‌مانند و از فهرست حذف نمی‌شوی. سازندهٔ چالش هم "
            "خبر پیوستن یا خروجت را بدون نام می‌بیند. فقط مدیر برنامه، برای "
            "رسیدگی به تخلف، می‌تواند ببیند پشت این عضو کیست."
        ),
    ),
    "leaderboard": Explainer(
        title="جدول رتبه‌بندی",
        body=(
            "رتبه از روی تعداد ثبت‌های واقعی حساب می‌شود و نفرات مساوی رتبهٔ "
            "یکسان می‌گیرند — دو نفر با ثبت برابر هر دو دوم‌اند و نفر بعدی "
            "چهارم است. معیار دوم فقط برای مرتب‌سازی است و کنار هر ردیف دیده "
            "می‌شود."
        ),
    ),
    # --- گروه ---------------------------------------------------------------
    "group": Explainer(
        title="گروه چیست",
        body=(
            "فضای یک مجموعه است — یک شرکت، یک کلاس، یک تیم — که چالش‌هایش فقط "
            "برای اعضای همان گروه دیده می‌شود. گروه‌ها فهرست عمومی ندارند و "
            "جستجو نمی‌شوند؛ تنها راه ورود، لینک دعوت است."
        ),
    ),
    "group-challenge-visibility": Explainer(
        title="دیده شدن چالش‌های گروه",
        body=(
            "«عمومی» داخل گروه یعنی عمومی برای اعضای همین گروه، نه برای همهٔ "
            "کاربران برنامه. کسی که عضو گروه نیست این چالش‌ها را در هیچ فهرست و "
            "با هیچ لینکی نمی‌بیند. اگر از گروه خارج شوی، چالش‌هایی که عضوشان "
            "بوده‌ای و هر چه ثبت کرده‌ای برایت می‌ماند."
        ),
    ),
    "group-roles": Explainer(
        title="نقش‌ها در گروه",
        body="نقش گروه فقط داخل همین گروه معنی دارد و به چالش‌های شخصی‌ات کاری ندارد.",
        points=(
            "عضو — چالش‌های گروه را می‌بیند و شرکت می‌کند.",
            "مدیر — دعوت می‌کند، درخواست‌ها را تأیید می‌کند و چالش گروهی می‌سازد.",
            "مالک — همهٔ کارهای مدیر، به‌علاوهٔ تعیین مدیر، واگذاری و حذف گروه.",
        ),
    ),
    "participation-mode": Explainer(
        title="اجباری یا اختیاری",
        body=(
            "چالش «اجباری» را تا وقتی عضو این گروه هستی نمی‌شود ترک کرد؛ اگر از "
            "گروه خارج شوی، به یک عضویت معمولی تبدیل می‌شود که هر وقت خواستی "
            "می‌توانی کنارش بگذاری."
        ),
    ),
    "group-audience": Explainer(
        title="این چالش برای چه کسی است",
        body=(
            "«همهٔ اعضا» یک تصمیم ماندگار است: هر کسی که بعداً به گروه بپیوندد "
            "هم خودکار به این چالش اضافه می‌شود. «افراد منتخب» فقط همان‌هایی "
            "هستند که الان انتخاب می‌کنی، و بعداً هم می‌شود کم و زیادشان کرد."
        ),
    ),
    "invite-link": Explainer(
        title="لینک دعوت",
        body="هر لینک کد یکتای خودش را دارد و وضعیتش از روی همین‌ها حساب می‌شود.",
        points=(
            "فعال — باز است و با آن می‌شود عضو شد.",
            "پر شده — به سقف تعداد استفاده رسیده.",
            "منقضی — تاریخ اعتبارش گذشته.",
            "باطل‌شده — خودت بستی‌اش؛ لینک دیگر کار نمی‌کند.",
        ),
    ),
    "add-member": Explainer(
        title="افزودن مستقیم عضو",
        body=(
            "عضو تازه را با همان شماره‌ای که با آن وارد برنامه می‌شود اضافه می‌کنی؛ "
            "جستجوی اسم در کار نیست، چون فهرست اعضای برنامه برای هیچ‌کس باز نیست. "
            "کسی که اضافه می‌شود بی‌درنگ عضو گروه است، ولی تا وقتی دسترسی‌اش را "
            "تایید نکرده‌ای وارد چالش‌های عمومی گروه نمی‌شود."
        ),
    ),
    "public-link": Explainer(
        title="لینک عمومی گروه",
        body=(
            "یک آدرس همیشگی برای گروه که هرجا می‌شود گذاشت. کسی که بازش می‌کند "
            "نام و نشان و تعداد اعضای گروه را می‌بیند و فقط می‌تواند درخواست "
            "عضویت بفرستد — ورودش با تایید مدیران است. لینک دعوت معمولی برعکسِ "
            "این است و مستقیم وارد می‌کند."
        ),
    ),
    "member-trust": Explainer(
        title="دسترسی به چالش‌های گروه",
        body=(
            "عضویت در گروه و شرکت در چالش‌های آن دو تصمیم جدا هستند. عضو تازه از "
            "همان اول عضو است و اگر مدیری او را در چالشی «به اسم» انتخاب کند در "
            "آن ثبت می‌شود؛ ولی چالش‌هایی که برای «همهٔ اعضا» گذاشته شده تا وقتی "
            "دسترسی‌اش تایید نشده به او نمی‌رسد."
        ),
        points=(
            "در انتظار تایید — فقط چالش‌هایی که در آن‌ها انتخاب شده.",
            "تاییدشده — چالش‌های «همهٔ اعضا» هم از همان لحظه به او می‌رسد.",
        ),
    ),
    "join-request": Explainer(
        title="درخواست عضویت",
        body=(
            "وقتی لینک دعوت پر یا منقضی شده باشد، متقاضی می‌تواند درخواست بدهد "
            "و یکی از مدیران گروه تأییدش کند. تا وقتی تأیید نشده هیچ دسترسی‌ای "
            "به چالش‌های گروه ندارد."
        ),
    ),
    # --- مدیریت -------------------------------------------------------------
    "moderation": Explainer(
        title="مدیریت با ویرایش فرق دارد",
        body=(
            "مدیر برنامه می‌تواند وضعیت و دسترسیِ دیدنِ هر چالش را تغییر دهد یا "
            "بایگانی‌اش کند، ولی متن، زمان‌بندی و هدف چالش را نه — آنها نوشتهٔ "
            "سازنده است و زیر نام او منتشر شده. حذف کامل هم وقتی کسی در آن ثبت "
            "داشته باشد ممکن نیست؛ راهش بایگانی است."
        ),
    ),
    "admin-member-edit": Explainer(
        title="ویرایش حساب عضو",
        body=(
            "برای رسیدگی به درخواست پشتیبانی می‌شود نام و ایمیل عضو را اصلاح "
            "کرد؛ هر تغییر به نام تو ثبت می‌شود. رمز عبور، نقش و حذف حساب از "
            "این راه ممکن نیست."
        ),
    ),
}


def explain(key: str) -> Markup:
    """Render the «؟» affordance for one glossary entry.

    The whole explanation travels in data attributes: ``initExplainers()`` in
    ``app.js`` reads them on the tap. That keeps a dot appended by an
    infinite-scroll fragment working with no re-initialisation, and keeps the
    popover instant and offline-proof.

    An unknown key raises — see the module docstring.
    """
    item = EXPLAINERS[key]
    points = (
        Markup(' data-explain-points="{}"').format(escape("\n".join(item.points)))
        if item.points
        else Markup("")
    )
    return Markup(
        '<button type="button" class="explain-dot" data-explain="{key}"'
        ' data-explain-title="{title}" data-explain-body="{body}"{points}'
        ' aria-expanded="false" aria-label="راهنما: {title}">'
        '<span aria-hidden="true">؟</span></button>'
    ).format(
        key=escape(key),
        title=escape(item.title),
        body=escape(item.body),
        points=points,
    )


def register_explainer_filters(env) -> None:
    """Expose ``explain()`` as a Jinja global on one environment.

    Same registration contract as ``register_icon_filters``. This one is
    chrome rather than a domain label, so ``tests/test_explainers.py`` asserts
    every view router's environment has it rather than leaving it to be
    discovered at render time.
    """
    env.globals["explain"] = explain
