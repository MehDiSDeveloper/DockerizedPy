// ==========================================================================
// چالش | tour.js — the guided introduction a first-time member walks through
// --------------------------------------------------------------------------
// **One run, walked across the pages it describes.** It starts at the bottom
// nav — the four doors of the app — then points at one of them, waits for the
// member to walk through it themselves, and picks up on the other side. Nav →
// خانه → nav → امروز → nav → چالش‌ها → nav → پروفایل, in that order, because
// that is the order the nav itself lists the four doors in.
//
// Five rules hold it together:
//
//   - **Targets are named, not selected.** A step points at a
//     `data-tour="<id>"` attribute in the template, never at a class chain —
//     a class is a styling decision that may be refactored, an attribute is a
//     promise.
//   - **State is a set of seen steps, not a position.** Every step carries a
//     stable `id`; localStorage remembers which ids have been shown, keyed
//     per member. There is no cursor to run past, so a member who wanders off
//     the path is picked up wherever they actually are — the flow re-anchors
//     itself at whatever it has not shown them yet.
//   - **A step whose target is absent is deferred, not skipped.** An empty
//     امروز list has no card, so that step is simply never marked seen — and
//     the first day the member does have one, it runs by itself, even though
//     the rest of that page's tour was seen long ago. It never blocks what
//     comes after it, because a page's run is a filter over the flow rather
//     than a cursor into it.
//   - **The tour does not navigate; it asks.** A step carrying
//     `action: "click"` hides «بعدی» and hands the spotlight itself over: the
//     ring becomes the one tappable thing on the screen, and the member's own
//     tap is what moves both the page and the tour. Nothing else is reachable
//     while the veil is up, so there is exactly one way forward. Such a step
//     ends that page's run — what follows it lives on the page it opens.
//   - **The spotlight is a hole in the veil, not a raised element.** The
//     target keeps its place in the document — nothing is re-parented,
//     re-stacked or cloned — and the veil is clipped with an even-odd path so
//     the blur simply stops at its edge. Lifting the target with z-index
//     instead breaks on the first ancestor that owns a stacking context,
//     which on this page set is most of them.
//
// Loaded by the four toured pages, always *after* app.js, whose renderIcons()
// this uses. The «؟» in the topbar replays a page's *own* steps — the nav
// steps that stitch the pages together belong to the first run, not to a
// member asking what this one screen does.
// ==========================================================================

(function () {
  "use strict";

  // Bumping TOUR_VERSION replays every page's tour for everyone — that is the
  // intended way to ship a changed run, so change it only when the steps
  // really did change. An entry written by an older version has a different
  // shape entirely and is discarded rather than translated.
  const TOUR_VERSION = 5;

  // Onboarding is an *account's* state, not a device's: a phone that has
  // already walked one member through the app must still introduce it to the
  // next member who signs in on it. localStorage is the only store this page
  // has, so the member id from layout.html's <body> goes into the key rather
  // than the value -- one entry per member, and no way for a read to
  // accidentally answer with someone else's progress. The id is blank only on
  // a signed-out page, and no page that loads this script is one.
  function tourKey() {
    const id = document.body && document.body.dataset.userId;
    return id ? "chalesh-tour:" + id : "chalesh-tour";
  }

  // `id` is a step's persisted identity and must outlive edits to its
  // wording. `path` is where the step lives: a concrete page, `ANY_PAGE` for
  // the nav steps (the nav is in layout.html, so they are answerable from
  // wherever the member happens to be), or a prefix ending in `*` for a page
  // whose URL carries an id. `action: "click"` means the step waits for the
  // member to tap the thing it points at.
  const ANY_PAGE = "*";

  // What a handover step says when it does not name its own target. Kept
  // beside the steps rather than in the CSS or the markup, because it is
  // copy: a step may override it with `actionHint`.
  const HANDOVER_HINT = "برای ادامه، بخش مشخص‌شده رو بزن";

  const TOUR_STEPS = [
    {
      id: "nav",
      path: ANY_PAGE,
      target: "nav",
      title: "مسیرهای اصلی",
      body: "چهار بخش برنامه همیشه همین‌جاست: امروز، خانه، چالش‌ها و پروفایل. از این نوار به هر کدوم می‌ری.",
    },
    {
      id: "nav-home",
      path: ANY_PAGE,
      target: "nav-home",
      action: "click",
      actionHint: "برای ادامه، «خانه» رو بزن تا با هم بریم اونجا",
      title: "خانه",
      body: "خانه میز کار توئه: می‌بینی این مدت چطور گذشته و چالش‌ها و برنامهٔ پیش‌روت کجاست. روی «خانه» بزن تا با هم بریم.",
    },
    {
      id: "home-pulse",
      path: "/views/home/",
      target: "home-pulse",
      title: "نبض امروز",
      body: "خلاصهٔ امروزت: چقدر ثبت کردی و رشته‌ات کجاست.",
    },
    {
      id: "home-grid",
      path: "/views/home/",
      target: "home-grid",
      title: "روند فعالیت",
      body: "نگاهی به سه ماه اخیرت، تا ببینی روند کارت چطور بوده.",
    },
    {
      id: "home-focus",
      path: "/views/home/",
      target: "home-focus",
      title: "تمرکز تو",
      body: "وقتت بیشتر صرف چه دسته‌هایی شده؛ حاصل یک ماه اخیر.",
    },
    {
      id: "home-challenges",
      path: "/views/home/",
      target: "home-challenges",
      title: "چالش‌های من",
      body: "چالش‌هایی که توشون هستی همین‌جاست؛ با یک ضربه سراغ هرکدوم برو.",
    },
    {
      id: "nav-today",
      path: ANY_PAGE,
      target: "nav-today",
      action: "click",
      actionHint: "برای ادامه، «امروز» رو بزن تا با هم بریم اونجا",
      title: "امروز",
      body: "امروز می‌گه همین امروز چه نوبت‌هایی داری و چی مونده. روی «امروز» بزن.",
    },
    {
      id: "today-count",
      path: "/views/today/",
      target: "today-count",
      title: "مانده تا امروز",
      body: "هر روز از اینجا شروع کن. این عدد می‌گه امروز چند نوبت مونده که ثبتش کنی.",
    },
    {
      id: "today-item",
      path: "/views/today/",
      target: "today-item",
      title: "کارت هر نوبت",
      body: "هر کارت یک نوبت از یک چالشه. با یک ضربه ثبتش می‌کنی: انجام شد، یا رد کردم.",
    },
    {
      id: "nav-explore",
      path: ANY_PAGE,
      target: "nav-explore",
      action: "click",
      actionHint: "برای ادامه، «چالش‌ها» رو بزن تا با هم بریم اونجا",
      title: "چالش‌ها",
      body: "حالا بریم سراغ خود چالش‌ها. روی «چالش‌ها» بزن.",
    },
    {
      id: "explore-filters",
      path: "/views/challenges/",
      target: "explore-filters",
      title: "فیلترها",
      body: "از اینجا چالش‌ها رو بر اساس دسته یا اینکه فقط چالش‌های خودت باشن، فیلتر کن.",
    },
    {
      id: "explore-create",
      path: "/views/challenges/",
      target: "explore-create",
      title: "ساخت چالش",
      body: "و از اینجا چالش خودت رو بساز.",
    },
    {
      id: "nav-profile",
      path: ANY_PAGE,
      target: "nav-profile",
      action: "click",
      actionHint: "برای ادامه، «پروفایل» رو بزن تا با هم بریم اونجا",
      title: "پروفایل",
      body: "می‌مونه حساب خودت. روی «پروفایل» بزن تا آخرین بخش رو ببینی.",
    },
    {
      id: "profile-account",
      path: "/views/users/*",
      target: "profile-account",
      title: "اطلاعات حساب",
      body: "نام و راه‌های تماست اینجاست؛ با دکمهٔ ویرایش هر وقت خواستی عوضشون کن.",
    },
    {
      id: "profile-groups",
      path: "/views/users/*",
      target: "profile-groups",
      title: "گروه‌ها",
      body: "اگر جایی تو رو به گروهی اضافه کرده، گروه‌ها و چالش‌هاشون از اینجا در دسترسه.",
    },
    {
      id: "profile-settings",
      path: "/views/users/*",
      target: "profile-settings",
      title: "تنظیمات",
      body: "پوسته، اعلان‌ها و بقیهٔ تنظیمات برنامه اینجاست. همین! خوش بگذره.",
    },
  ];

  // --- geometry ------------------------------------------------------------
  const HOLE_PAD = 8;        // breathing room between the target and the hole
  const HOLE_RADIUS = 14;    // fallback when the target has no radius of its own
  const POP_GAP = 20;        // hole to callout; must clear the arrow tip it grows
  const EDGE = 12;           // smallest distance from the callout to a screen edge
  const ARROW = 11;          // half-diagonal of the rotated square that points
  const SETTLE_MS = 260;     // smooth scrolling has no portable completion event

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  // --- persisted progress --------------------------------------------------
  // The whole state is the set of step ids already shown.
  function readSeen() {
    try {
      const raw = JSON.parse(localStorage.getItem(tourKey()) || "null");
      if (!raw || raw.v !== TOUR_VERSION || !Array.isArray(raw.seen)) return new Set();
      return new Set(raw.seen.filter(function (id) { return typeof id === "string"; }));
    } catch (e) {
      return new Set();
    }
  }

  function markSeen(ids) {
    const seen = readSeen();
    ids.forEach(function (id) { seen.add(id); });
    try {
      localStorage.setItem(
        tourKey(),
        JSON.stringify({ v: TOUR_VERSION, seen: Array.from(seen) })
      );
    } catch (e) {
      // private mode: the tour still runs, it just introduces itself again
    }
  }

  // A trailing slash differs between a typed URL and a router mount, and the
  // mismatch would silently disable the whole tour.
  function samePath(a, b) {
    const trim = (p) => (p.length > 1 ? p.replace(/\/+$/, "") : p);
    return trim(a) === trim(b);
  }

  // Where a step is answerable. A concrete path is that one page; ANY_PAGE is
  // the nav, which layout.html puts on every page; a trailing `*` is a prefix,
  // for the profile, whose URL carries the member's own id.
  function pathMatches(step, ownPageOnly) {
    if (step.path === ANY_PAGE) return !ownPageOnly;
    if (step.path.slice(-1) === "*") {
      return location.pathname.indexOf(step.path.slice(0, -1)) === 0;
    }
    return samePath(step.path, location.pathname);
  }

  // First match wins: the امروز card step points at a repeated attribute and
  // means "the first one", which is the card the eye is already on.
  function targetOf(step) {
    return document.querySelector('[data-tour="' + step.target + '"]');
  }

  // The steps answerable on this page right now, in flow order. A step whose
  // anchor is missing drops out here and is never marked seen, so it comes
  // back by itself the first time the page does render it -- and, because
  // this is a filter rather than a cursor, it holds up nothing behind it.
  function availableHere(ownPageOnly) {
    return TOUR_STEPS.filter(function (step) {
      return pathMatches(step, ownPageOnly) && targetOf(step);
    });
  }

  // A step that waits for a tap opens another page, so whatever follows it
  // belongs to that page's run, not this one.
  function untilHandover(steps) {
    const at = steps.findIndex(function (step) { return step.action === "click"; });
    return at < 0 ? steps : steps.slice(0, at + 1);
  }

  // --- the overlay ---------------------------------------------------------
  let session = null;

  function roundedRectPath(x, y, w, h, r) {
    const rr = Math.max(0, Math.min(r, w / 2, h / 2));
    return (
      "M" + (x + rr) + "," + y +
      "H" + (x + w - rr) + "A" + rr + "," + rr + " 0 0 1 " + (x + w) + "," + (y + rr) +
      "V" + (y + h - rr) + "A" + rr + "," + rr + " 0 0 1 " + (x + w - rr) + "," + (y + h) +
      "H" + (x + rr) + "A" + rr + "," + rr + " 0 0 1 " + x + "," + (y + h - rr) +
      "V" + (y + rr) + "A" + rr + "," + rr + " 0 0 1 " + (x + rr) + "," + y + "Z"
    );
  }

  // The target's own corner radius, so the spotlight reads as the same object
  // rather than as a rectangle laid over it.
  // A percentage radius (the FAB, an avatar) has no px value to grow by, and
  // must not be read as one: it means "as round as this box gets", which
  // roundedRectPath's own clamp to half the shorter side already delivers.
  function radiusOf(el) {
    const declared = getComputedStyle(el).borderTopLeftRadius;
    if (declared.indexOf("%") >= 0) return Infinity;
    const raw = parseFloat(declared);
    if (!Number.isFinite(raw) || raw <= 0) return HOLE_RADIUS;
    return raw + HOLE_PAD;
  }

  function buildOverlay() {
    const root = document.createElement("div");
    root.className = "tour";

    const veil = document.createElement("div");
    veil.className = "tour-veil";

    const ring = document.createElement("div");
    ring.className = "tour-ring";
    ring.setAttribute("aria-hidden", "true");

    const pop = document.createElement("div");
    pop.className = "tour-pop";
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-modal", "true");
    pop.setAttribute("aria-labelledby", "tourPopTitle");
    pop.setAttribute("aria-describedby", "tourPopBody");
    // The dialog itself takes focus on each step, not its primary button: a
    // programmatic focus() on a button paints a focus ring on a control the
    // member reached with their thumb. Tab from here still lands on قبلی.
    pop.tabIndex = -1;
    pop.innerHTML =
      '<i class="tour-arrow" aria-hidden="true"></i>' +
      '<div class="tour-pop-head">' +
      '<span class="tour-count"></span>' +
      '<button type="button" class="icon-btn tour-x" data-icon="x" aria-label="بستن تور"></button>' +
      "</div>" +
      '<h3 id="tourPopTitle"></h3>' +
      '<p id="tourPopBody"></p>' +
      // The handover instruction, hidden on every other step. A step that
      // waits for a tap has to *say* so: the breathing ring alone reads as
      // «look here», which is what every other step's ring already says.
      '<p class="tour-cta" hidden>' +
      '<span class="tour-cta-icon" data-icon="tap" aria-hidden="true"></span>' +
      '<span class="tour-cta-text"></span></p>' +
      '<div class="tour-foot">' +
      '<span class="tour-dots" aria-hidden="true"></span>' +
      '<div class="tour-btns">' +
      '<button type="button" class="tour-btn tour-prev">قبلی</button>' +
      '<button type="button" class="tour-btn primary tour-next">بعدی</button>' +
      "</div></div>";

    root.append(veil, ring, pop);
    document.body.appendChild(root);
    if (window.renderIcons) window.renderIcons(root);
    return { root: root, veil: veil, ring: ring, pop: pop };
  }

  // --- placement -----------------------------------------------------------
  function place() {
    if (!session) return;
    const el = targetOf(session.order[session.at]);
    if (!el) return;

    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    const r = el.getBoundingClientRect();

    // Clamped to the viewport: a target taller than the screen would otherwise
    // punch its hole straight through the callout's own row. A target that
    // has scrolled fully past an edge (mid-scroll, or a stale measurement)
    // can put the far edge on the near side of the clamped start -- floored
    // at 0 so a transient bad read collapses the hole instead of going
    // negative and corrupting the clip path, the ring and the popup maths
    // that all key off it.
    const hx = Math.max(0, r.left - HOLE_PAD);
    const hy = Math.max(0, r.top - HOLE_PAD);
    const hw = Math.max(0, Math.min(vw, r.right + HOLE_PAD) - hx);
    const hh = Math.max(0, Math.min(vh, r.bottom + HOLE_PAD) - hy);
    const rad = radiusOf(el);

    // The ring is a plain box and has no path to clamp it, so it takes the
    // same limit roundedRectPath applies internally.
    const ringRad = Math.min(rad, hw / 2, hh / 2);
    session.ring.style.cssText =
      "left:" + hx + "px; top:" + hy + "px; width:" + hw + "px; height:" + hh +
      "px; border-radius:" + ringRad + "px;";

    if (session.canClip) {
      session.veil.style.clipPath =
        'path(evenodd, "M0,0H' + vw + "V" + vh + 'H0Z ' +
        roundedRectPath(hx, hy, hw, hh, rad) + '")';
    }

    // Measure the callout at its natural size before deciding where it goes.
    const pop = session.pop;
    pop.style.left = "0px";
    pop.style.top = "0px";
    const pw = pop.offsetWidth;
    const ph = pop.offsetHeight;

    const below = hy + hh + POP_GAP;
    const above = hy - POP_GAP - ph;
    let top;
    let side;
    if (below + ph <= vh - EDGE) {
      top = below;
      side = "top";          // the arrow sits on the callout's top edge
    } else if (above >= EDGE) {
      top = above;
      side = "bottom";
    } else {
      // Neither side fits, so centre the callout and drop the arrow rather
      // than draw one pointing at nothing.
      top = Math.max(EDGE, Math.min((vh - ph) / 2, vh - ph - EDGE));
      side = "none";
    }

    const centre = hx + hw / 2;
    const left = Math.max(EDGE, Math.min(centre - pw / 2, vw - pw - EDGE));

    pop.style.left = Math.round(left) + "px";
    pop.style.top = Math.round(top) + "px";
    pop.dataset.arrow = side;
    pop.querySelector(".tour-arrow").style.left =
      Math.round(Math.max(ARROW + 6, Math.min(centre - left, pw - ARROW - 6))) + "px";
  }

  // --- rendering a step ----------------------------------------------------
  function render() {
    const step = session.order[session.at];
    const pop = session.pop;
    const isLast = session.at === session.order.length - 1;

    // The counter promises a number the member can reach, so it counts only
    // the steps this page actually rendered -- «۱ از ۲» for a member with
    // nothing due today is the honest answer.
    pop.querySelector(".tour-count").textContent =
      (session.at + 1) + " از " + session.order.length;
    pop.querySelector("#tourPopTitle").textContent = step.title;
    pop.querySelector("#tourPopBody").textContent = step.body;
    pop.querySelector(".tour-dots").innerHTML = session.order
      .map(function (_, i) { return '<i class="' + (i === session.at ? "on" : "") + '"></i>'; })
      .join("");

    pop.querySelector(".tour-prev").disabled = session.at === 0;
    // Nothing follows a page's run, so its last step simply ends the tour.
    pop.querySelector(".tour-next").textContent = isLast ? "تمام" : "بعدی";

    // A handover step has no «بعدی»: the spotlight itself is the button, and
    // offering a second way on would let the member past the one thing the
    // step is asking them to find. The ring is the only part of the overlay
    // that takes a pointer, so «anywhere else» still goes nowhere.
    const handover = step.action === "click";
    pop.querySelector(".tour-next").hidden = handover;
    session.ring.classList.toggle("is-handover", handover);
    // Two channels for one instruction, because the ring alone is ambiguous:
    // the sentence names the act, and `is-handover` gives the ring a faster,
    // wider beat than the «look here» one every other step wears.
    const cta = pop.querySelector(".tour-cta");
    cta.hidden = !handover;
    if (handover) {
      cta.querySelector(".tour-cta-text").textContent =
        step.actionHint || HANDOVER_HINT;
      if (window.renderIcons) window.renderIcons(cta);
    }

    markSeen([step.id]);

    const el = targetOf(step);
    if (el) {
      el.scrollIntoView({
        block: "center",
        inline: "nearest",
        behavior: reduceMotion.matches ? "auto" : "smooth",
      });
    }
    cancelAnimationFrame(session.settle);
    // A fixed wait after starting the scroll was tried here first and could
    // not be made reliable: a smooth scroll's duration depends on distance
    // and device, a still-loading avatar or icon can shift the layout above
    // the target after the scroll already landed, and either one leaves
    // place() measuring a target that has not actually stopped moving yet --
    // which is what a fully broken-looking overlay (a solid veil, the
    // popup off past an edge) turns out to be downstream of, since place()
    // has no way to tell a bad measurement from a real one. So instead of
    // guessing how long to wait, this polls the target's own rect on every
    // frame and only measures once it has read the same position several
    // frames in a row -- which is true the instant an instant scroll lands,
    // and only once a smooth one (or a late layout shift) has actually
    // finished, however long that takes. STABLE_FRAMES rules out a false
    // "already stable" read on the very first frame, before the scroll has
    // had a chance to move anything yet; MAX_FRAMES is a backstop so a
    // target that never stops (it should not exist, but must not hang the
    // tour) still gets measured eventually.
    const STABLE_FRAMES = 3;
    const MAX_FRAMES = 90;
    let stable = 0;
    let frames = 0;
    let lastRect = null;
    const poll = function () {
      if (!session || session.order[session.at] !== step) return;
      const landed = targetOf(step);
      const rect = landed ? landed.getBoundingClientRect() : null;
      frames += 1;
      const unchanged =
        rect && lastRect &&
        Math.abs(rect.top - lastRect.top) < 0.5 &&
        Math.abs(rect.left - lastRect.left) < 0.5;
      stable = unchanged ? stable + 1 : 0;
      lastRect = rect;
      if (stable < STABLE_FRAMES && frames < MAX_FRAMES) {
        session.settle = requestAnimationFrame(poll);
        return;
      }
      // A step whose target never arrived (or scrolled somewhere the clamp
      // in place() cannot make sense of) still gets one instant correction,
      // same fallback as before, then one more settle pass to measure it.
      if (landed) {
        const box = landed.getBoundingClientRect();
        if ((box.bottom < 0 || box.top > document.documentElement.clientHeight) && frames < MAX_FRAMES) {
          landed.scrollIntoView({ block: "center", inline: "nearest" });
          stable = 0;
          lastRect = null;
          session.settle = requestAnimationFrame(poll);
          return;
        }
      }
      place();
      session.root.classList.add("is-ready");
      pop.focus();
    };
    session.settle = requestAnimationFrame(poll);
  }

  // Forward from a handover step is the member's own tap on the target: the
  // page navigates, and the run picks up from the first unseen step there.
  // The tour never calls location itself -- it clicks what it was pointing
  // at, so the member ends up exactly where that control leads.
  function advance() {
    const step = session.order[session.at];
    if (step.action !== "click") { go(1); return; }
    const el = targetOf(step);
    markSeen([step.id]);
    teardown();
    if (el) el.click();
  }

  function go(delta) {
    const next = session.at + delta;
    if (next < 0) return;
    if (next >= session.order.length) { finish(); return; }
    session.at = next;
    render();
  }

  // Two different endings, and the difference matters now that the run walks
  // across pages. Reaching «تمام» ends *this page's* run: the member was
  // offered everything it had, and whatever the flow still has to say waits
  // on the page it belongs to. Closing early ends the *tour* -- somebody who
  // taps ✕ is not asking to be met again on the next screen -- so every
  // remaining step is marked seen, and the «؟» in the topbar is the way back.
  function finish(dismissed) {
    const ids = (dismissed ? TOUR_STEPS : session.order).map(function (s) {
      return s.id;
    });
    markSeen(ids);
    teardown();
  }

  function dismiss() {
    finish(true);
  }

  function teardown() {
    if (!session) return;
    cancelAnimationFrame(session.settle);
    window.removeEventListener("resize", session.onReflow);
    window.removeEventListener("scroll", session.onReflow, true);
    document.removeEventListener("keydown", session.onKeydown, true);
    session.root.remove();
    const restore = session.previouslyFocused;
    session = null;
    if (restore && typeof restore.focus === "function") restore.focus();
  }

  // --- start ---------------------------------------------------------------
  function run(steps) {
    if (session || !steps.length) return;

    const ui = buildOverlay();
    session = {
      root: ui.root,
      veil: ui.veil,
      ring: ui.ring,
      pop: ui.pop,
      order: steps,
      at: 0,
      canClip: CSS.supports("clip-path", 'path("M0,0Z")'),
      previouslyFocused: document.activeElement,
      settle: 0,
      onReflow: null,
      onKeydown: null,
    };

    let queued = false;
    session.onReflow = function () {
      if (queued) return;
      queued = true;
      requestAnimationFrame(function () {
        queued = false;
        place();
      });
    };
    window.addEventListener("resize", session.onReflow);
    window.addEventListener("scroll", session.onReflow, true);

    session.onKeydown = function (e) {
      if (e.key === "Escape") { e.preventDefault(); dismiss(); return; }
      // RTL: the left arrow points forward.
      if (e.key === "ArrowLeft" || e.key === "PageDown") { e.preventDefault(); advance(); return; }
      if (e.key === "ArrowRight" || e.key === "PageUp") { e.preventDefault(); go(-1); return; }
      // The overlay is modal, so Tab must not walk out into the page behind it.
      if (e.key !== "Tab") return;
      const focusables = Array.prototype.slice.call(
        session.pop.querySelectorAll("button:not([disabled])")
      );
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", session.onKeydown, true);

    session.pop.querySelector(".tour-x").addEventListener("click", dismiss);
    session.pop.querySelector(".tour-prev").addEventListener("click", function () { go(-1); });
    session.pop.querySelector(".tour-next").addEventListener("click", function () { advance(); });
    // The ring lies exactly over the hole, so a tap on it is a tap on the
    // target -- it only takes one while `.is-handover` says the step is
    // asking for it.
    session.ring.addEventListener("click", function () { advance(); });
    // The veil swallows the tap that would otherwise reach the page behind it,
    // but it does not advance: the one control a stranger is sure about should
    // not share a surface with "anywhere else on the screen".
    session.veil.addEventListener("click", function (e) { e.stopPropagation(); });
    // touch-action pins a finger, but a wheel or a trackpad still scrolls.
    session.veil.addEventListener("wheel", function (e) { e.preventDefault(); }, { passive: false });

    render();
  }

  // The «؟» button in the topbar: this page's own steps, seen or not. The nav
  // steps are left out on purpose -- somebody asking what *this screen* does
  // is not asking to be walked to another one.
  function replay() {
    teardown();
    run(availableHere(true));
  }

  window.startTour = replay;

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-tour-help]").forEach(function (el) {
      el.addEventListener("click", replay);
    });

    // A beat for the icons and the first cards to land, so the spotlight is
    // measured against the layout the member is actually looking at.
    setTimeout(function () {
      const seen = readSeen();
      run(untilHandover(
        availableHere(false).filter(function (step) { return !seen.has(step.id); })
      ));
    }, 400);
  });
})();
