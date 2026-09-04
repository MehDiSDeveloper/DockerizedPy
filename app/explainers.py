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
  several screens — «زنجیره» on the home dashboard and on challenge-detail,
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
        title="وعده‌های امروز",
        body=(
            "هر کاری که چالش‌هایت امروز از تو خواسته‌اند، یک‌جا. «امروز» به وقت "
            "منطقهٔ زمانی خودِ هر عضویت حساب می‌شود، نه ساعت گوشی."
        ),
    ),
    "checkin-states": Explainer(
        title="حالت هر وعده",
        body="هر وعده یکی از این چهار حالت را دارد؛ فقط دوتای اول را خودت ثبت می‌کنی.",
        points=(
            "تکمیل‌شده — ثبتش کردی.",
            "رد شده — خودت اعلام کردی که این وعده را انجام ندادی.",
            "در انتظار — هنوز باز است و می‌توانی ثبتش کنی.",
            "ازدست‌رفته — مهلتش تمام شد و چیزی ثبت نشد.",
        ),
    ),
    "backfill": Explainer(
        title="مهلت ثبت با تأخیر",
        body=(
            "تا دو روز بعد از بسته شدن یک وعده هنوز می‌توانی ثبتش کنی یا ثبت "
            "اشتباه را پاک کنی. بعد از آن قفل می‌شود."
        ),
    ),
    "streak": Explainer(
        title="زنجیرهٔ پیوسته",
        body=(
            "تعداد وعده‌های پشت‌سرهمی که ثبت کرده‌ای. با یک وعدهٔ از دست رفته از "
            "نو شروع می‌شود و با ثبت باتأخیر هم اصلاح می‌شود."
        ),
    ),
    # --- خانه ---------------------------------------------------------------
    "home-pulse": Explainer(
        title="امروز در یک نگاه",
        body=(
            "حلقه می‌گوید از وعده‌های امروزت چند تا ثبت شده. نیمه‌شب به وقت "
            "خودت از نو شروع می‌شود."
        ),
    ),
    "home-grid": Explainer(
        title="روند فعالیت",
        body=(
            "هر ستون یک هفته است و هر ردیف یک روز هفته؛ خانهٔ پررنگ‌تر یعنی ثبت "
            "بیشتر. یک ردیفِ کم‌رنگ یعنی همیشه همان روز هفته از دستت در می‌رود."
        ),
    ),
    "home-focus": Explainer(
        title="تمرکز",
        body=("ثبت‌های سی روز اخیرت به تفکیک دستهٔ چالش، تا ببینی وقتت کجا رفته."),
    ),
    # --- چالش ---------------------------------------------------------------
    "challenge-status": Explainer(
        title="وضعیت چالش",
        body="وضعیت از روی تاریخ‌ها و زمان‌بندی چالش حساب می‌شود، نه دستی.",
        points=(
            "شروع نشده — هنوز به تاریخ اولین وعده‌اش نرسیده.",
            "در حال اجرا — همین حالا از اعضا وعده می‌خواهد.",
            "تمام‌شده — مهلتش گذشته یا سازنده بایگانی‌اش کرده.",
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
        title="زمان‌بندی چالش",
        body=(
            "این‌که چالش چه موقع از تو وعده می‌خواهد: یک بار، در تاریخ‌های مشخص، "
            "در روزهای معینی از هفته، یا تکرار مدت‌دار در هر هفته و ماه."
        ),
    ),
    "collective-progress": Explainer(
        title="پیشرفت جمعی",
        body=(
            "مجموع کاری که همهٔ اعضا ثبت کرده‌اند، نه سهم شخصی تو؛ سهم خودت در "
            "تب «من» است."
        ),
    ),
    "locked-fields": Explainer(
        title="چرا بعضی تنظیم‌ها قفل می‌شوند",
        body=(
            "به‌محض این‌که کسی جز خودت به چالش بپیوندد، زمان‌بندی، هدف و نحوهٔ "
            "نمایش نام قفل می‌شوند؛ اینها شرطی است که بقیه با همان پیوسته‌اند. "
            "عنوان و توضیح همچنان قابل ویرایش‌اند."
        ),
    ),
    "anonymity": Explainer(
        title="نمایش ناشناس",
        body=(
            "نام و تصویرت برای بقیهٔ اعضا پنهان می‌شود، ولی امتیاز و ثبت‌هایت سر "
            "جایشان می‌مانند. فقط مدیر برنامه می‌تواند ببیند پشت این عضو کیست."
        ),
    ),
    "leaderboard": Explainer(
        title="جدول رتبه‌بندی",
        body=(
            "رتبه از روی تعداد ثبت‌های واقعی حساب می‌شود و نفرات مساوی رتبهٔ "
            "یکسان می‌گیرند."
        ),
    ),
    # --- گروه ---------------------------------------------------------------
    "group": Explainer(
        title="گروه چیست",
        body=(
            "فضای یک مجموعه — یک شرکت، یک کلاس، یک تیم — که چالش‌هایش فقط برای "
            "اعضای همان گروه دیده می‌شود. تنها راه ورود، لینک دعوت است."
        ),
    ),
    "group-challenge-visibility": Explainer(
        title="دیده شدن چالش‌های گروه",
        body=(
            "«عمومی» داخل گروه یعنی عمومی برای اعضای همین گروه، نه همهٔ کاربران "
            "برنامه. کسی که عضو گروه نیست این چالش‌ها را اصلاً نمی‌بیند."
        ),
    ),
    "subgroup": Explainer(
        title="زیرگروه چیست",
        body=(
            "گروهی که داخل یک گروه دیگر ساخته می‌شود — مثل دپارتمان‌های یک شرکت. "
            "زیرگروه اعضا، لینک‌ها و چالش‌های خودش را دارد."
        ),
        points=(
            "اعضا — عضو زیرگروه، چالش‌های گروه بالادست را هم می‌بیند.",
            "مدیریت — مدیران گروه بالادست، مدیر زیرگروه‌ها هم هستند.",
            "چالش‌ها — «همهٔ اعضا» در گروه بالادست به زیرگروه‌ها هم می‌رسد.",
        ),
    ),
    "group-roles": Explainer(
        title="نقش‌ها در گروه",
        body="نقش گروه فقط داخل همین گروه معنی دارد و به چالش‌های شخصی‌ات کاری ندارد.",
        points=(
            "عضو — چالش‌های گروه را می‌بیند و شرکت می‌کند.",
            "مدیر — دعوت می‌کند، درخواست‌ها را تأیید می‌کند و چالش و زیرگروه می‌سازد.",
            "مالک — همهٔ کارهای مدیر، به‌علاوهٔ تعیین مدیر، واگذاری و حذف گروه.",
        ),
    ),
    "participation-mode": Explainer(
        title="اجباری یا اختیاری",
        body=(
            "چالش «اجباری» را تا وقتی عضو این گروه هستی نمی‌شود ترک کرد؛ چالش "
            "«اختیاری» را هر وقت خواستی."
        ),
    ),
    "group-audience": Explainer(
        title="این چالش برای چه کسی است",
        body=(
            "«همهٔ اعضا» یعنی هر کسی که بعداً هم به گروه بپیوندد خودکار اضافه "
            "می‌شود. «افراد منتخب» فقط همان‌هایی که انتخاب می‌کنی."
        ),
    ),
    "invite-link": Explainer(
        title="لینک دعوت",
        body="هر لینک کد یکتای خودش را دارد و وضعیتش از روی همین‌ها حساب می‌شود.",
        points=(
            "فعال — باز است و با آن می‌شود عضو شد.",
            "پر شده — به سقف تعداد استفاده رسیده.",
            "منقضی — تاریخ اعتبارش گذشته.",
            "غیرفعال‌شده — خودت بستی‌اش؛ لینک دیگر کار نمی‌کند.",
        ),
    ),
    "add-member": Explainer(
        title="افزودن مستقیم عضو",
        body=(
            "عضو تازه را با همان شماره یا ایمیلی که با آن وارد برنامه می‌شود "
            "اضافه می‌کنی؛ جستجوی اسم در کار نیست."
        ),
    ),
    "public-link": Explainer(
        title="لینک عمومی گروه",
        body=(
            "یک آدرس همیشگی برای گروه که هرجا می‌شود گذاشت. با آن کسی مستقیم "
            "عضو نمی‌شود؛ فقط درخواست می‌دهد و مدیران تأیید می‌کنند."
        ),
    ),
    "join-request": Explainer(
        title="درخواست عضویت",
        body=(
            "متقاضی درخواست می‌دهد و یکی از مدیران گروه تأییدش می‌کند. تا پیش از "
            "تأیید، هیچ دسترسی‌ای به چالش‌های گروه ندارد."
        ),
    ),
    # --- مدیریت -------------------------------------------------------------
    "moderation": Explainer(
        title="مدیریت با ویرایش فرق دارد",
        body=(
            "مدیر برنامه می‌تواند وضعیت و دسترسیِ دیدنِ هر چالش را عوض کند یا "
            "بایگانی‌اش کند، ولی متن و زمان‌بندی چالش را نه — آنها نوشتهٔ سازنده است."
        ),
    ),
    # --- مسیر ---------------------------------------------------------------
    "roadmap": Explainer(
        title="مسیر",
        body=(
            "چند چالش که پشت سر هم چیده شده‌اند تا با هم یک برنامه بسازند. "
            "چالش‌ها همان چالش‌های همیشگی‌اند؛ مسیر فقط ترتیبشان را می‌گوید و "
            "هر قدم را وقتی وعده‌اش شد برایت باز می‌کند."
        ),
    ),
    "roadmap-step-state": Explainer(
        title="حالت هر قدم",
        body="هر قدم مسیر یکی از این حالت‌ها را دارد.",
        points=(
            "هنوز وعده‌اش نیست — قدم‌های قبلی هنوز تمام نشده‌اند.",
            "آمادهٔ شروع — باز شده و در چالشش ثبت‌نام شده‌ای.",
            "در حال انجام — شروعش کرده‌ای ولی شرطش هنوز کامل نیست.",
            "تمام شد — شرط این قدم برآورده شده.",
            "رد شد — چالش این قدم بایگانی شده، پس مسیر از رویش گذشت.",
        ),
    ),
    "roadmap-completion": Explainer(
        title="شرط تمام شدن قدم",
        body=(
            "چالش‌های عادتی پایان ندارند، پس هر قدم خودش می‌گوید کِی تمام "
            "می‌شود: چند بار ثبت، چند وعدهٔ پشت‌سرهم، رسیدن به مقداری مشخص، "
            "گذشتن چند روز، یا اعلام خودت. این شرط مال همین مسیر است و روی "
            "خود چالش اثری ندارد."
        ),
    ),
    "roadmap-order": Explainer(
        title="ترتیب قدم‌ها",
        body=(
            "به‌طور پیش‌فرض ترتیب یک پیشنهاد است: قدم‌های بعدی را می‌بینی و "
            "اگر آماده بودی می‌توانی زودتر سراغشان بروی. اگر سازنده «ترتیب "
            "اجباری» را روشن کرده باشد، هر قدم فقط بعد از تمام شدن قدم قبلی "
            "باز می‌شود."
        ),
    ),
    "roadmap-invite": Explainer(
        title="لینک مسیر",
        body=(
            "با این لینک هرکسی می‌تواند این مسیر را شروع کند، حتی اگر مسیر در "
            "فهرست عمومی نباشد. می‌توانی برایش ظرفیت و تاریخ انقضا بگذاری و "
            "هر وقت خواستی غیرفعالش کنی."
        ),
    ),
    "roadmap-privacy": Explainer(
        title="چالش‌های خصوصی در مسیر",
        body=(
            "گذاشتن یک چالش در مسیر، آن چالش را عمومی نمی‌کند. فقط کسی که "
            "مسیر را شروع کرده باشد چالش‌های قدم‌هایش را می‌بیند؛ بقیه عنوان "
            "چالش خصوصی را هم نمی‌بینند."
        ),
    ),
    "admin-member-edit": Explainer(
        title="ویرایش حساب عضو",
        body=(
            "برای پشتیبانی می‌شود نام و ایمیل عضو را اصلاح کرد. رمز عبور، نقش و "
            "حذف حساب از این راه ممکن نیست."
        ),
    ),
    # --- نصب ----------------------------------------------------------------
    "install-ios": Explainer(
        title="افزودن به صفحهٔ خانه",
        body=(
            "سافاری دکمهٔ نصب ندارد و باید خودت اضافه‌اش کنی. بعد از آن «چالش» "
            "مثل یک برنامهٔ معمولی باز می‌شود، بدون نوار مرورگر."
        ),
        points=(
            "یک — در سافاری دکمهٔ هم‌رسانی (مربع با فلش رو به بالا) را بزن.",
            "دو — از فهرست، «Add to Home Screen» را انتخاب کن.",
            "سه — نامش را تأیید کن؛ آیکون روی صفحهٔ خانه می‌نشیند.",
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
