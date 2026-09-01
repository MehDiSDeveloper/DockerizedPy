// ==========================================================================
// چالش | tour.js — the guided introduction a first-time member walks through
// --------------------------------------------------------------------------
// Three small, independent per-page tours — امروز, خانه, چالش‌ها — not one
// run spread across them. The tour never navigates: each page introduces its
// own screen, its last step says «تمام», and every other page waits for the
// member to walk over there themselves.
//
// Four rules hold it together:
//
//   - **Targets are named, not selected.** A step points at a
//     `data-tour="<id>"` attribute in the template, never at a class chain —
//     a class is a styling decision that may be refactored, an attribute is a
//     promise.
//   - **State is a set of seen steps, not a position.** Every step carries a
//     stable `id`; localStorage remembers which ids have been shown, keyed
//     per member. There is no cursor to run past, so each page's tour is
//     answerable on its own terms whenever the member happens to arrive.
//   - **A step whose target is absent is deferred, not skipped.** An empty
//     امروز list has no card, so that step is simply never marked seen — and
//     the first day the member does have one, it runs by itself, even though
//     the rest of that page's tour was seen long ago.
//   - **The spotlight is a hole in the veil, not a raised element.** The
//     target keeps its place in the document — nothing is re-parented,
//     re-stacked or cloned — and the veil is clipped with an even-odd path so
//     the blur simply stops at its edge. Lifting the target with z-index
//     instead breaks on the first ancestor that owns a stacking context,
//     which on this page set is most of them.
//
// Loaded by the three toured pages, always *after* app.js, whose
// renderIcons() this uses.
// ==========================================================================

(function () {
  "use strict";

  // Bumping TOUR_VERSION replays every page's tour for everyone — that is the
  // intended way to ship a changed run, so change it only when the steps
  // really did change. An entry written by an older version has a different
  // shape entirely and is discarded rather than translated.
  const TOUR_VERSION = 3;

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
  // wording; `path` is what groups steps into one page's little tour.
  const TOUR_STEPS = [
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
      id: "nav",
      path: "/views/home/",
      target: "nav",
      title: "مسیرهای اصلی",
      body: "چهار بخش برنامه همیشه همین‌جاست: امروز، خانه، چالش‌ها و پروفایل.",
    },
    {
      id: "home-pulse",
      path: "/views/home/",
      target: "home-pulse",
      title: "نبض امروز",
      body: "چقدر از کارهای امروزت ثبت شده، رشتهٔ روزهای پیوسته‌ات کجاست و تا حالا چند بار ثبت کردی — همه یک‌جا.",
    },
    {
      id: "home-grid",
      path: "/views/home/",
      target: "home-grid",
      title: "روند فعالیت",
      body: "دوازده هفتهٔ اخیر. هر ستون یک هفته و هر ردیف یک روز هفته‌ست، پس می‌بینی کدوم روزها معمولاً از دستت در می‌ره.",
    },
    {
      id: "explore-search",
      path: "/views/challenges/",
      target: "explore-search",
      title: "پیدا کردن چالش",
      body: "چالش‌های عمومی بقیه رو اینجا جستجو کن؛ دکمهٔ کنارش دسته و محدودهٔ جستجو رو فیلتر می‌کنه.",
    },
    {
      id: "explore-create",
      path: "/views/challenges/",
      target: "explore-create",
      title: "ساخت چالش",
      body: "و از اینجا چالش خودت رو بساز — زمان‌بندی و هدفش کاملاً با خودته.",
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

  // First match wins: the امروز card step points at a repeated attribute and
  // means "the first one", which is the card the eye is already on.
  function targetOf(step) {
    return document.querySelector('[data-tour="' + step.target + '"]');
  }

  // This page's steps that are actually on screen, in order. A step whose
  // anchor is missing drops out here and is never marked seen, so it comes
  // back by itself the first time the page does render it.
  function availableHere() {
    return TOUR_STEPS.filter(function (step) {
      return samePath(step.path, location.pathname) && targetOf(step);
    });
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
    // punch its hole straight through the callout's own row.
    const hx = Math.max(0, r.left - HOLE_PAD);
    const hy = Math.max(0, r.top - HOLE_PAD);
    const hw = Math.min(vw, r.right + HOLE_PAD) - hx;
    const hh = Math.min(vh, r.bottom + HOLE_PAD) - hy;
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

    markSeen([step.id]);

    const el = targetOf(step);
    if (el) {
      el.scrollIntoView({
        block: "center",
        inline: "nearest",
        behavior: reduceMotion.matches ? "auto" : "smooth",
      });
    }
    clearTimeout(session.settle);
    session.settle = setTimeout(function () {
      place();
      session.root.classList.add("is-ready");
      pop.focus();
    }, reduceMotion.matches ? 0 : SETTLE_MS);
  }

  function go(delta) {
    const next = session.at + delta;
    if (next < 0) return;
    if (next >= session.order.length) { finish(); return; }
    session.at = next;
    render();
  }

  // Reaching the end, or closing early: either way the member was offered
  // this page's whole run, so none of it should ambush them again. Steps that
  // never rendered are not in `order` and stay pending.
  function finish() {
    markSeen(session.order.map(function (s) { return s.id; }));
    teardown();
  }

  function teardown() {
    if (!session) return;
    clearTimeout(session.settle);
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
      if (e.key === "Escape") { e.preventDefault(); finish(); return; }
      // RTL: the left arrow points forward.
      if (e.key === "ArrowLeft" || e.key === "PageDown") { e.preventDefault(); go(1); return; }
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

    session.pop.querySelector(".tour-x").addEventListener("click", finish);
    session.pop.querySelector(".tour-prev").addEventListener("click", function () { go(-1); });
    session.pop.querySelector(".tour-next").addEventListener("click", function () { go(1); });
    // The veil swallows the tap that would otherwise reach the page behind it,
    // but it does not advance: the one control a stranger is sure about should
    // not share a surface with "anywhere else on the screen".
    session.veil.addEventListener("click", function (e) { e.stopPropagation(); });
    // touch-action pins a finger, but a wheel or a trackpad still scrolls.
    session.veil.addEventListener("wheel", function (e) { e.preventDefault(); }, { passive: false });

    render();
  }

  // The «؟» button in the topbar: this page's whole tour, seen or not.
  function replay() {
    teardown();
    run(availableHere());
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
      run(availableHere().filter(function (step) { return !seen.has(step.id); }));
    }, 400);
  });
})();
