// ==========================================================================
// چالش | app.js — shared icon set used across all Jinja-rendered templates
// ==========================================================================

// ---- icon set (inline SVG, stroke-based, consistent 24px grid) ----------
const icons = {
  home: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/></svg>`,
  list: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/></svg>`,
  plus: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>`,
  user: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.6-4 5-6 8-6s6.4 2 8 6"/></svg>`,
  bell: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 1112 0c0 5 2 6 2 6H4s2-1 2-6"/><path d="M10 20a2 2 0 004 0"/></svg>`,
  search: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg>`,
  check: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>`,
  x: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>`,
  trash: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>`,
  clock: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>`,
  users: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.2"/><path d="M2.5 20c1-3.3 3.4-5 6.5-5s5.5 1.7 6.5 5"/><circle cx="17" cy="8" r="2.6"/><path d="M15 8.2A2.6 2.6 0 1119.6 9.7"/><path d="M15.5 15.2c2.6.2 4.5 1.8 5.3 4.8"/></svg>`,
  flame: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2c1 4-4 5-4 9a4 4 0 008 0c1.3 1 2 2.6 2 4.2A6.2 6.2 0 0112 22a6.2 6.2 0 01-6-6.4C6 10 12 8 12 2z"/></svg>`,
  seal: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8"/><path d="M9 12l2 2 4-4"/></svg>`,
  empty: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="7" width="16" height="13" rx="2"/><path d="M8 7V5a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>`,
  mail: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>`,
  lock: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 118 0v3"/></svg>`,
  eye: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>`,
  eyeOff: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l18 18"/><path d="M10.6 5.1A11 11 0 0123 12s-1.6 2.8-4.4 4.9M6.4 6.9C3.8 8.8 2 12 2 12s4 7 11 7c1.3 0 2.6-.2 3.7-.6"/><path d="M9.5 9.5a3 3 0 004.2 4.2"/></svg>`,
  chevronRight: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>`,
  chevronLeft: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg>`,
  calendar: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/></svg>`,
  filter: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16M7 12h10M10 19h4"/></svg>`,
  logout: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/></svg>`,
  edit: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/></svg>`,
  share: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 10.6l6.8-3.8M8.6 13.4l6.8 3.8"/></svg>`,
  target: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></svg>`,
  chart: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V10M12 20V4M20 20v-7"/></svg>`,
  camera: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg>`,
  info: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 8h.01"/></svg>`,
};

// Injects inline SVGs into every [data-icon] placeholder under `root`.
// Exposed on window so pages can re-run it after inserting new markup
// (e.g. infinite-scroll fragments) that DOMContentLoaded never saw.
function renderIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    el.innerHTML = icons[el.dataset.icon] || "";
  });
}
window.renderIcons = renderIcons;

// ==========================================================================
// Dates and times
// ==========================================================================
// Storage stays Gregorian and UTC (see CLAUDE.md); this is a display layer
// only. The server renders ISO-8601 into a data attribute *and* as the
// element's text, and these helpers upgrade it in place to the Persian
// (Jalali) calendar. The ISO value survives in the attribute, so anything
// reading it back keeps working -- and if Intl has no Persian calendar, or JS
// never runs at all, the untouched ISO text is still a real date.
//
//   data-jalali="2026-07-04"                 -- a floating local calendar date
//   data-jalali="2026-07-04T14:30:00+00:00"  -- an absolute instant
//   data-jalali-format="day-month"           -- key into JALALI_FORMATS
//   data-jalali-tz="Asia/Tehran"             -- zone to resolve an instant in
//   data-jalali-prefix="هفتهٔ "                 -- literal text glued in front
//   data-jalali-attr="aria-label"            -- write here instead of textContent
//
// An instant must be rendered in the timezone it was *judged* in, not the
// viewer's: the occurrence engine derives "today" from each enrollment's own
// `timezone`, so a user abroad must still see check-in windows in the zone
// their streak is being scored against. Elements carrying an enrollment-scoped
// instant therefore pass data-jalali-tz explicitly. APP_TIMEZONE is only the
// fallback for challenge-level instants (due_date, created_at) that have no
// enrollment behind them -- it mirrors DEFAULT_TIMEZONE in
// app/routers/challenge.py, which already formats created_at the same way.
const APP_TIMEZONE = "Asia/Tehran";

// hour12 is pinned off: fa-IR defaults to a 12-hour clock with ق.ظ/ب.ظ,
// which is not how Iranians read times.
const JALALI_FORMATS = {
  "weekday-day-month": { weekday: "long", day: "numeric", month: "long" },
  "day-month": { day: "numeric", month: "long" },
  "day-month-year": { day: "numeric", month: "long", year: "numeric" },
  month: { month: "long", year: "numeric" },
  numeric: { year: "numeric", month: "2-digit", day: "2-digit" },
  time: { hour: "2-digit", minute: "2-digit", hour12: false },
  datetime: {
    day: "numeric", month: "long",
    hour: "2-digit", minute: "2-digit", hour12: false,
  },
  // dateStyle rather than a weekday/day/month/year bag on purpose: CLDR's fa
  // *full* pattern is year-first ("۱۴۰۵ شهریور ۵, پنجشنبه"), which no Iranian
  // writes. dateStyle:"long" gives the idiomatic "۵ شهریور ۱۴۰۵ ساعت ۲۳:۳۰".
  "datetime-full": { dateStyle: "long", timeStyle: "short", hour12: false },
};

// querySelectorAll skips `root` itself, which would silently miss a fragment
// whose own top-level node carries the attribute.
function selectAll(root, selector) {
  const found = Array.from(root.querySelectorAll(selector));
  if (root instanceof Element && root.matches(selector)) found.unshift(root);
  return found;
}

const ISO_OFFSET_RE = /(Z|[+-]\d{2}:?\d{2})$/;

// A bare "YYYY-MM-DD" is a local calendar date (occurrence dates, quota period
// starts) with no instant behind it, so it must never be shifted into another
// zone. It is parsed at local noon rather than midnight because in zones that
// skip midnight on a DST jump, "T00:00:00" can land on the previous day.
//
// An instant with no offset is UTC, not the viewer's local time -- which is
// what `new Date()` would otherwise assume, silently moving the rendered day
// for anyone outside Tehran. Everything the app writes is UTC (see CLAUDE.md);
// it arrives offset-less only because SQLite has no aware datetime type, so
// dev/test runs hand back "2026-10-06T11:06:36" where Postgres appends +00:00.
function parseDateValue(raw) {
  const value = String(raw || "").trim();
  if (!value) return null;
  const isInstant = value.includes("T");
  let text = `${value}T12:00:00`;
  if (isInstant) text = ISO_OFFSET_RE.test(value) ? value : `${value}Z`;
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : { date, isInstant };
}

// Returns null (rather than a garbled string) whenever it cannot format, so
// every caller can fall back to leaving the server's ISO text alone.
function formatJalali(raw, { format, timeZone } = {}) {
  const parsed = parseDateValue(raw);
  if (!parsed) return null;
  const opts = { ...(JALALI_FORMATS[format] || JALALI_FORMATS["day-month"]) };
  if (parsed.isInstant) opts.timeZone = timeZone || APP_TIMEZONE;
  try {
    const fmt = new Intl.DateTimeFormat("fa-IR-u-ca-persian", opts);
    // CLDR's fa year+month skeleton is also year-first ("۱۴۰۵ شهریور") and,
    // unlike the full date, has no dateStyle to fall back on -- so this one
    // label is reassembled from its parts.
    if (format === "month") {
      const parts = Object.fromEntries(
        fmt.formatToParts(parsed.date).map((p) => [p.type, p.value])
      );
      return `${parts.month} ${parts.year}`;
    }
    return fmt.format(parsed.date);
  } catch {
    return null; // no Persian calendar, or an unknown IANA zone
  }
}
window.formatJalali = formatJalali;

function renderJalaliDates(root = document) {
  selectAll(root, "[data-jalali]").forEach((el) => {
    const text = formatJalali(el.dataset.jalali, {
      format: el.dataset.jalaliFormat,
      timeZone: el.dataset.jalaliTz,
    });
    if (text === null) return;
    const out = (el.dataset.jalaliPrefix || "") + text;
    if (el.dataset.jalaliAttr) el.setAttribute(el.dataset.jalaliAttr, out);
    else el.textContent = out;
  });
}
window.renderJalaliDates = renderJalaliDates;

// ---- deadlines -----------------------------------------------------------
// A due occurrence carries absolute instants, but a bare clock time ("23:59")
// answers the wrong question -- what the user wants to know is how much room
// is left. The server supplies the fixed half of the sentence, since it is the
// only side that knows the cadence ("تا پایان این هفته"), and this fills in the
// live remainder beside it. The exact instant stays reachable through
// <time datetime> and a formatted title tooltip.
const RELATIVE_UNITS = [
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
];
const URGENT_WINDOW_MS = 3 * 60 * 60 * 1000;

function formatRemaining(target, now) {
  const diffSeconds = (target.getTime() - now.getTime()) / 1000;
  const abs = Math.abs(diffSeconds);
  if (abs < 60) {
    return diffSeconds >= 0 ? "کمتر از یک دقیقه" : "همین حالا";
  }
  try {
    const rtf = new Intl.RelativeTimeFormat("fa", { numeric: "auto" });
    for (const [unit, seconds] of RELATIVE_UNITS) {
      if (abs >= seconds) return rtf.format(Math.trunc(diffSeconds / seconds), unit);
    }
  } catch {
    /* no RelativeTimeFormat -- leave whatever the server rendered */
  }
  return null;
}

function renderDeadlines(root = document) {
  const now = new Date();
  selectAll(root, "[data-deadline]").forEach((el) => {
    const iso = el.dataset.deadline;
    const parsed = parseDateValue(iso); // same offset-less-means-UTC rule
    if (!parsed) return;
    const target = parsed.date;
    const remainingMs = target.getTime() - now.getTime();
    const text = formatRemaining(target, now);
    if (text !== null) el.textContent = text;
    el.classList.toggle("is-passed", remainingMs <= 0);
    el.classList.toggle("is-urgent", remainingMs > 0 && remainingMs < URGENT_WINDOW_MS);
    // `closes_at_utc` is an *exclusive* bound -- local midnight opening the
    // next day. Counting down to it is right, but printing it verbatim shows
    // "۶ شهریور ساعت ۰:۰۰" under a card that says "تا پایان امروز (۵ شهریور)".
    // The tooltip therefore names the last instant still inside the window.
    // A scheduled occurrence is a point in time, not a bound, so it is exempt.
    const tipAt = el.hasAttribute("data-deadline-exclusive")
      ? new Date(target.getTime() - 1).toISOString()
      : iso;
    const exact = formatJalali(tipAt, {
      format: "datetime-full",
      timeZone: el.dataset.deadlineTz,
    });
    if (exact) el.title = exact;
  });
}
window.renderDeadlines = renderDeadlines;

// One entry point, so callers inserting markup (infinite-scroll fragments)
// cannot localize half of it and forget the rest.
function renderDates(root = document) {
  renderJalaliDates(root);
  renderDeadlines(root);
}
window.renderDates = renderDates;

// Relative deadlines go stale just by sitting on screen. One shared minute
// tick keeps every card honest instead of each one owning a timer.
const DEADLINE_TICK_MS = 60 * 1000;

document.addEventListener("DOMContentLoaded", () => {
  renderIcons();
  renderDates();
  setInterval(() => renderDeadlines(document), DEADLINE_TICK_MS);

  // Elements not wired up to a real feature yet (notifications, account
  // settings, ...) get a clear "coming soon" toast instead of doing
  // nothing when clicked -- a dead, silent control is worse UX than an
  // honest "not built yet" message.
  document.querySelectorAll("[data-coming-soon]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      showToast(el.dataset.comingSoon || "این بخش به‌زودی اضافه می‌شود");
    });
  });

  // Make custom `role="button"` elements (e.g. non-<button> menu rows)
  // keyboard-activatable with Enter/Space, matching native button behavior.
  document.querySelectorAll('[role="button"][tabindex]').forEach((el) => {
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        el.click();
      }
    });
  });
});

// ---- small shared UI/network helpers used across pages ----------------

// Debounce: delays invoking `fn` until `wait` ms have passed since the
// last call. Used for the search inputs so we don't hit the server on
// every keystroke.
function debounce(fn, wait) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}
window.debounce = debounce;

// Lightweight, non-blocking toast for feedback that doesn't warrant an
// alert() dialog (e.g. "coming soon" stubs, "link copied").
function showToast(message) {
  let el = document.getElementById("appToast");
  if (!el) {
    el = document.createElement("div");
    el.id = "appToast";
    el.className = "toast";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.add("visible");
  clearTimeout(el._hideTimer);
  el._hideTimer = setTimeout(() => el.classList.remove("visible"), 2400);
}
window.showToast = showToast;

// Generic infinite-scroll helper: observes `sentinel` and fetches
// successive pages from `buildUrl(offset, limit)` as it enters the
// viewport, appending the returned HTML fragment into `container`.
// The first page is expected to already be server-rendered, so callers
// pass in the initial offset/hasMore instead of triggering a fetch.
function createInfiniteScroller({
  container,
  sentinel,
  loadingEl,
  emptyEl,
  pageSize = 20,
  initialOffset = 0,
  initialHasMore = false,
  buildUrl,
  onEmpty,
  onAppend,
  loginRedirectUrl,
}) {
  let offset = initialOffset;
  let hasMore = initialHasMore;
  let loading = false;
  let requestToken = 0;

  async function fetchPage(reset) {
    // A reset (filter/search/tab changed) always supersedes whatever is
    // in flight -- it must never be silently dropped just because a
    // previous page request hasn't resolved yet. Only plain "load next
    // page" calls respect the loading/hasMore guards.
    if (!reset && (loading || !hasMore)) return;
    const token = ++requestToken;
    if (reset) {
      offset = 0;
      hasMore = true;
      container.innerHTML = "";
      if (emptyEl) emptyEl.hidden = true;
    }
    loading = true;
    if (loadingEl) loadingEl.hidden = false;
    try {
      const res = await fetch(buildUrl(offset, pageSize));
      if (token !== requestToken) return; // superseded by a newer filter/reset
      if (res.status === 401) {
        window.location.href =
          loginRedirectUrl ||
          `/views/auth/?next=${encodeURIComponent(window.location.pathname)}`;
        return;
      }
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const html = await res.text();
      // Reading the body is a second await -- a reset can land in between, and
      // appending here would splice a stale page into the fresh container and
      // push `offset` past rows that were never rendered.
      if (token !== requestToken) return;
      hasMore = res.headers.get("X-Has-More") === "true";
      const wrapper = document.createElement("div");
      wrapper.innerHTML = html;
      const fragmentCount = wrapper.children.length;
      const nodes = Array.from(wrapper.children);
      while (wrapper.firstChild) container.appendChild(wrapper.firstChild);
      renderIcons(container);
      renderDates(container);
      if (typeof onAppend === "function") onAppend(nodes);
      offset += fragmentCount;
      if (offset === 0 && fragmentCount === 0 && typeof onEmpty === "function") {
        onEmpty();
      }
    } catch (err) {
      console.error("Failed to load more items:", err);
      // A reset() empties the container before its request goes out, so a
      // failure here would otherwise leave a blank list with no explanation
      // and no way back -- keep hasMore on so the sentinel can retry.
      if (token === requestToken) {
        hasMore = true;
        showToast("بارگذاری انجام نشد. دوباره تلاش کن.");
      }
    } finally {
      // Only the newest request owns the loading flag. A superseded one
      // clearing it would let the observer start a duplicate page fetch
      // while the newest request is still in flight.
      if (token === requestToken) {
        loading = false;
        if (loadingEl) loadingEl.hidden = true;
      }
    }
  }

  const observer = new IntersectionObserver(
    (entries) => {
      if (entries[0].isIntersecting) fetchPage(false);
    },
    { rootMargin: "200px 0px" }
  );
  observer.observe(sentinel);

  return {
    reset: () => fetchPage(true),
    // Callers that drop a card from the DOM *and* from the server's result
    // set must say so, or `offset` stays one too high and the next page
    // silently skips a row.
    notifyRemoved: (n = 1) => {
      offset = Math.max(0, offset - n);
    },
    disconnect: () => observer.disconnect(),
  };
}
window.createInfiniteScroller = createInfiniteScroller;

// JSON fetch helper for new code (existing hand-rolled fetch+alert() call
// sites are left alone -- see CLAUDE.md). Redirects to login on 401 instead
// of leaving the caller to handle it, and always resolves to
// {ok, status, data} instead of throwing.
async function apiFetch(url, options = {}) {
  const opts = { ...options };
  opts.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
  }

  let res;
  try {
    res = await fetch(url, opts);
  } catch (err) {
    return { ok: false, status: 0, data: { detail: err.message } };
  }

  if (res.status === 401) {
    window.location.href = `/views/auth/?next=${encodeURIComponent(window.location.pathname)}`;
    return { ok: false, status: 401, data: null };
  }

  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  return { ok: res.ok, status: res.status, data };
}
window.apiFetch = apiFetch;

// The first modal/dialog in this codebase (everything else uses native
// confirm()/alert()). A bottom sheet with a focus trap, Escape-to-close,
// and backdrop-click-to-close; never itself uses confirm()/alert().
// `fields`: [{name, label, type, required, maxlength, step, placeholder}].
// `onConfirm(values)` / `onSkip(values)` may return `false` to keep the
// sheet open (e.g. after a failed request); anything else closes it.
function createSheet({
  title,
  fields = [],
  onConfirm,
  onSkip,
  confirmLabel = "تایید",
  skipLabel = "رد کردن",
}) {
  const backdrop = document.createElement("div");
  backdrop.className = "sheet-backdrop";

  const sheet = document.createElement("div");
  sheet.className = "sheet";
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-modal", "true");
  sheet.setAttribute("aria-label", title || "");

  const panel = document.createElement("div");
  panel.className = "sheet-panel glass";

  const head = document.createElement("div");
  head.className = "sheet-head";
  const heading = document.createElement("h3");
  heading.textContent = title || "";
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "icon-btn";
  closeBtn.setAttribute("aria-label", "بستن");
  closeBtn.dataset.icon = "x";
  head.appendChild(heading);
  head.appendChild(closeBtn);

  const form = document.createElement("form");
  form.className = "sheet-body";
  const inputs = {};
  fields.forEach((f) => {
    const wrap = document.createElement("div");
    wrap.className = "field";
    const label = document.createElement("label");
    label.textContent = f.label + (f.required ? " *" : "");
    label.setAttribute("for", `sheet-${f.name}`);
    const fieldInput = document.createElement("div");
    fieldInput.className = "field-input";
    const input = document.createElement("input");
    input.id = `sheet-${f.name}`;
    input.name = f.name;
    input.type = f.type || "text";
    if (f.placeholder) input.placeholder = f.placeholder;
    if (f.maxlength) input.maxLength = f.maxlength;
    if (f.required) input.required = true;
    if (f.step) input.step = f.step;
    fieldInput.appendChild(input);
    wrap.appendChild(label);
    wrap.appendChild(fieldInput);
    form.appendChild(wrap);
    inputs[f.name] = input;
  });

  const actions = document.createElement("div");
  actions.className = "sheet-actions";
  const skipBtn = document.createElement("button");
  skipBtn.type = "button";
  skipBtn.className = "cc-btn ghost";
  skipBtn.textContent = skipLabel;
  const confirmBtn = document.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "cc-btn primary";
  confirmBtn.textContent = confirmLabel;
  actions.appendChild(skipBtn);
  actions.appendChild(confirmBtn);

  panel.appendChild(head);
  panel.appendChild(form);
  panel.appendChild(actions);
  sheet.appendChild(panel);

  document.body.appendChild(backdrop);
  document.body.appendChild(sheet);
  const previousOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";

  const previouslyFocused = document.activeElement;

  function collectValues() {
    const values = {};
    for (const [name, el] of Object.entries(inputs)) values[name] = el.value;
    return values;
  }

  function close() {
    document.removeEventListener("keydown", onKeydown);
    backdrop.removeEventListener("click", close);
    backdrop.remove();
    sheet.remove();
    document.body.style.overflow = previousOverflow;
    if (previouslyFocused && typeof previouslyFocused.focus === "function") {
      previouslyFocused.focus();
    }
  }

  function focusableEls() {
    return Array.from(
      panel.querySelectorAll('button, input, textarea, [href], [tabindex]:not([tabindex="-1"])')
    ).filter((el) => !el.disabled);
  }

  function onKeydown(e) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      return;
    }
    if (e.key !== "Tab") return;
    const focusables = focusableEls();
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  backdrop.addEventListener("click", close);
  closeBtn.addEventListener("click", close);
  document.addEventListener("keydown", onKeydown);

  skipBtn.addEventListener("click", async () => {
    const result = typeof onSkip === "function" ? await onSkip(collectValues()) : true;
    if (result !== false) close();
  });

  confirmBtn.addEventListener("click", async () => {
    if (!form.reportValidity()) return;
    const result = typeof onConfirm === "function" ? await onConfirm(collectValues()) : true;
    if (result !== false) close();
  });

  renderIcons(sheet);
  const firstFocusable = focusableEls()[0];
  if (firstFocusable) firstFocusable.focus();

  return { close };
}
window.createSheet = createSheet;
